"""
Streaming OpenRouter call mixin with incremental token processing.
"""

import re
import threading
import traceback
from time import time

from .._shared import json, logger
from .openrouter_client_config import STREAM_IDLE_TIMEOUT_SEC


class OpenRouterClientStreamMixin:
    """Streaming OpenRouter call helper."""

    def _openrouter_stream_worker(
        self, sid: str, api_key: str, request_data: dict, cancel: threading.Event
    ):
        """Stream tokens using the official OpenRouter Python SDK into the sid stream store."""
        logger.info(f"[stream_worker] Starting stream for sid={sid}")
        buf = ""
        last_flush = time()
        stream_start_time = time()
        chunk_count = 0
        timeout_seconds = 120  # absolute maximum duration for a stream

        # Try to load stream protocol prefixes dynamically from manifest
        reasoning_prefix = "__REASONING__:"
        content_prefix = "__CONTENT__:"
        try:
            from pathlib import Path
            protocol_path = Path(__file__).parent.parent / "stream_protocol.json"
            if protocol_path.is_file():
                with open(protocol_path, "r", encoding="utf-8") as f:
                    proto = json.loads(f.read())
                    reasoning_prefix = proto.get("reasoning_prefix", reasoning_prefix)
                    content_prefix = proto.get("content_prefix", content_prefix)
        except Exception as e:
            logger.warning(f"Failed to load stream protocol: {e}")

        # If stream remains idle for this long, abort to avoid infinite waits.
        idle_timeout = STREAM_IDLE_TIMEOUT_SEC
        last_token_time = time()

        def flush(force: bool = False):
            nonlocal buf, last_flush
            if not buf:
                return
            now = time()
            if force or (now - last_flush) >= 0.04 or len(buf) >= 2048:
                logger.debug(f"[stream_worker] Flushing {len(buf)} chars for sid={sid}")
                self._append_chat_stream(sid, buf, done=False)
                buf = ""
                last_flush = now

        def event_to_dict(ev):
            """Normalize stream chunks using SDK helpers/typed models where possible."""
            if ev is None:
                return None

            for attr in ("model_dump", "dict", "to_dict"):
                fn = getattr(ev, attr, None)
                if callable(fn):
                    try:
                        return fn()
                    except Exception:
                        pass

            if isinstance(ev, dict):
                return ev

            if isinstance(ev, (bytes, bytearray)):
                try:
                    ev = ev.decode("utf-8", errors="replace")
                except Exception:
                    return None

            if isinstance(ev, str):
                s = ev.strip()
                if not s:
                    return None
                if s == "[DONE]":
                    return {"__done__": True}
                if s.startswith("data:"):
                    s = s[5:].strip()
                try:
                    return json.loads(s)
                except Exception:
                    return {"__text__": s}

            try:
                return {"choices": getattr(ev, "choices")}
            except Exception:
                return None

        def _flatten_reasoning_value(value):
            """Extract clean reasoning text for preview buffers."""
            if value is None:
                return ""
            if isinstance(value, str):
                return value
            if isinstance(value, bool):
                return ""
            if isinstance(value, (int, float)):
                return ""

            if isinstance(value, list):
                parts = []
                for item in value:
                    piece = _flatten_reasoning_value(item).strip()
                    if piece and piece not in parts:
                        parts.append(piece)
                return "\n".join(parts)

            if isinstance(value, dict):
                preferred_keys = (
                    "reasoning",
                    "text",
                    "summary",
                    "content",
                    "description",
                    "title",
                    "output_text",
                    "text_content",
                )
                ignored_keys = {
                    "type", "index", "id", "status", "signature", "format",
                    "token", "tokens", "token_count", "completion_tokens",
                    "prompt_tokens", "reasoning_tokens", "logprob", "logprobs",
                    "probability", "confidence", "offset", "start", "end",
                    "duration_ms", "latency", "finish_reason", "stop_reason",
                    "annotations", "encrypted_content",
                }
                parts = []
                for key in preferred_keys:
                    if key in value:
                        piece = _flatten_reasoning_value(value.get(key)).strip()
                        if piece and piece not in parts:
                            parts.append(piece)
                if parts:
                    return "\n".join(parts)
                for key, item in value.items():
                    if key in preferred_keys or key in ignored_keys:
                        continue
                    piece = _flatten_reasoning_value(item).strip()
                    if piece and piece not in parts:
                        parts.append(piece)
                return "\n".join(parts)
            return ""

        def _sanitize_reasoning_text(value: str) -> str:
            value = str(value or "")
            if not value:
                return ""
            value = value.replace("**", "")
            value = re.sub(r"gAAAAA[0-9A-Za-z_\-=]{80,}", "", value)
            value = re.sub(r"(?<![A-Za-z0-9])[A-Za-z0-9_\-=]{180,}(?![A-Za-z0-9])", "", value)
            value = re.sub(r"[ \t]{2,}", " ", value)
            return value

        def _merge_incremental_text(existing: str, incoming: str) -> str:
            existing = str(existing or "")
            incoming = _sanitize_reasoning_text(incoming)
            if not incoming:
                return ""
            if not existing:
                return incoming
            if incoming == existing:
                return ""
            if len(incoming) <= len(existing) and existing.endswith(incoming):
                return ""
            max_overlap = min(len(existing), len(incoming))
            for overlap in range(max_overlap, 0, -1):
                if existing[-overlap:] == incoming[:overlap]:
                    return incoming[overlap:]
            return incoming

        def _append_unique_part(parts, part_kind, delta):
            delta = str(delta or "")
            if not delta:
                return
            existing = ""
            for k, v in parts:
                if k == part_kind:
                    existing += str(v or "")
            merged = _merge_incremental_text(existing, delta)
            if merged:
                parts.append((part_kind, merged))

        def extract_stream_parts(d):
            """Extract streamed reasoning/content chunks from the stream payload."""
            parts = []
            if not d or d.get("__done__"):
                return parts
            try:
                choice0 = (d.get("choices") or [{}])[0] or {}
                delta_obj = choice0.get("delta") or {}
                delta_reasoning = delta_obj.get("reasoning")
                if delta_reasoning:
                    _append_unique_part(parts, "reasoning", _sanitize_reasoning_text(str(delta_reasoning)))
                delta_reasoning_details = delta_obj.get("reasoning_details")
                if delta_reasoning_details:
                    piece = _flatten_reasoning_value(delta_reasoning_details).strip()
                    if piece:
                        _append_unique_part(parts, "reasoning", piece)
                delta_content = delta_obj.get("content")
                if delta_content:
                    _append_unique_part(parts, "content", str(delta_content))

                msg = choice0.get("message") or {}
                msg_reasoning = msg.get("reasoning")
                if msg_reasoning:
                    _append_unique_part(parts, "reasoning", _sanitize_reasoning_text(str(msg_reasoning)))
                msg_reasoning_details = msg.get("reasoning_details")
                if msg_reasoning_details:
                    piece = _flatten_reasoning_value(msg_reasoning_details).strip()
                    if piece:
                        _append_unique_part(parts, "reasoning", piece)
                msg_content = msg.get("content")
                if msg_content:
                    _append_unique_part(parts, "content", str(msg_content))
            except Exception:
                pass
            return parts

        def extract_stream_error(d):
            """Extract OpenRouter/OpenAI-compatible stream errors from a chunk."""
            if not d or d.get("__done__"):
                return ""

            try:
                err = d.get("error") or {}
                if isinstance(err, dict):
                    code = err.get("code")
                    msg = err.get("message")
                    if msg:
                        code_prefix = f"{code}: " if code else ""
                        return f"[stream error: {code_prefix}{msg}]"
            except Exception:
                pass

            try:
                choice0 = (d.get("choices") or [{}])[0] or {}
                finish_reason = choice0.get("finish_reason")
                if finish_reason == "error":
                    return "[stream error: upstream provider terminated stream with finish_reason=error]"
            except Exception:
                pass

            return ""

        try:
            raw_req_data = dict(request_data or {})
            stream_mode = str(raw_req_data.pop("_impressionist_stream_mode", "") or "").strip().lower()
            req_data = self._sanitize_chat_kwargs(raw_req_data)
            req_data["stream"] = True
            if "max_tokens" not in req_data and "max_completion_tokens" not in req_data:
                req_data["max_tokens"] = 2000
            req_data = self._sanitize_chat_kwargs(req_data)

            use_raw_transport = self._requires_raw_openrouter_transport(req_data)
            logger.info(
                f"[stream_worker] Calling OpenRouter {'REST' if use_raw_transport else 'SDK'} for sid={sid}"
            )
            if use_raw_transport:
                res = self._stream_openrouter_chat_raw(api_key, req_data, timeout_ms=60_000)
                close_stream = None
            else:
                open_router = self._openrouter_sdk(api_key, timeout_ms=60_000)
                close_stream = open_router
                res = open_router.__enter__().chat.send(**req_data)

            try:
                logger.debug(f"[stream_worker] Starting iteration for sid={sid}")
                for ev in res:
                    if cancel.is_set():
                        logger.info(f"[stream_worker] Cancelled sid={sid}")
                        break

                    if time() - stream_start_time > timeout_seconds:
                        logger.error(
                            f"[stream_worker] Timeout after {timeout_seconds}s for sid={sid}"
                        )
                        break

                    if time() - last_token_time > idle_timeout:
                        logger.error(
                            f"[stream_worker] Idle for {idle_timeout}s without tokens for sid={sid}"
                        )
                        break

                    d = event_to_dict(ev)
                    stream_error = extract_stream_error(d)
                    if stream_error:
                        logger.warning(
                            f"[stream_worker] Stream error chunk for sid={sid}: {stream_error}"
                        )
                        self._append_chat_stream(sid, stream_error, done=False)
                        break

                    parts = extract_stream_parts(d)
                    if parts:
                        last_token_time = time()
                    for part_kind, delta in parts:
                        if not delta:
                            continue
                        chunk_count += 1
                        logger.debug(
                            f"[stream_worker] Chunk {chunk_count} ({len(delta)} chars, kind={part_kind}) for sid={sid}"
                        )
                        if stream_mode == "typed":
                            prefix = reasoning_prefix if part_kind == "reasoning" else content_prefix
                            self._append_chat_stream(sid, prefix + delta, done=False)
                            last_flush = time()
                        else:
                            if part_kind != "content":
                                continue
                            buf += delta
                            flush(False)
            finally:
                if close_stream is not None:
                    close_stream.__exit__(None, None, None)

            if cancel.is_set():
                logger.info(f"[stream_worker] Clearing buffer for cancelled sid={sid}")
                buf = ""

            flush(True)
            logger.info(f"[stream_worker] Stream complete for sid={sid}, {chunk_count} chunks")
            self._append_chat_stream(sid, None, done=True)
        except Exception as e:
            logger.error(
                f"[stream_worker] OpenRouter SDK stream failed sid={sid}: {e}\n{traceback.format_exc()}"
            )
            if not cancel.is_set():
                self._append_chat_stream(sid, f"[stream error: {e}]", done=False)
                self._append_chat_stream(sid, None, done=True)
        finally:
            logger.info(f"[stream_worker] Cleaning up sid={sid}")
            with self._chat_run_lock:
                run = self._chat_runs.get(sid)
                if run and run.get("cancel") is cancel:
                    self._chat_runs.pop(sid, None)
            logger.info(f"[stream_worker] Finished sid={sid}")
