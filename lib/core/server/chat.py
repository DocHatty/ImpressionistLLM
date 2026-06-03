"""
Chat streaming mixin for PromptHandler.
Handles chat session initialization, streaming, and event-based communication.
"""
from __future__ import annotations
import threading
from time import time

from ._shared import json, logger
from .schemas import ChatRequest, format_validation_errors


class ChatMixin:
    """Mixin providing chat streaming functionality."""

    _REQUEST_ALWAYS_ALLOWED_KEYS = {
        "model",
        "messages",
        "stream",
        "max_tokens",
        "max_completion_tokens",
        "temperature",
        "top_p",
        "frequency_penalty",
        "presence_penalty",
        "repetition_penalty",
        "seed",
        "stop",
        "logit_bias",
        "logprobs",
        "top_logprobs",
        "response_format",
        "tools",
        "tool_choice",
        "reasoning",
        # Always allow keys required by advanced OpenRouter features
        "plugins",
        "models",
        "provider",
        "parallel_tool_calls",
    }

    def _validate_chat_request(self, request_data):
        """Validate chat request payload before sending it upstream."""
        try:
            ChatRequest.model_validate(request_data)
            return True, ""
        except Exception as exc:  # noqa: BLE001 - normalize validation libraries/errors
            return False, format_validation_errors(exc)

    def _sanitize_openrouter_request(self, req: dict):
        """Remove prompt-specific params that the SDK/provider is unlikely to accept."""
        if not isinstance(req, dict):
            return req
        return self._sanitize_chat_kwargs(req)

    def _resolve_openrouter_model_id(self, model_id: str, models_data: dict | None):
        """Resolve saved model aliases against OpenRouter's current model list."""
        if not model_id or not isinstance(models_data, dict):
            return model_id

        model_ids = {
            str(model.get("id") or "")
            for model in (models_data or {}).get("data", [])
            if isinstance(model, dict)
        }
        if model_id in model_ids:
            return model_id

        if not model_id.startswith("~"):
            router_alias = "~" + model_id
            if router_alias in model_ids:
                logger.info(
                    "Normalized OpenRouter model alias %s -> %s",
                    model_id,
                    router_alias,
                )
                return router_alias

        return model_id

    def _extract_text_value(self, value):
        """Extract user-visible text from OpenAI/OpenRouter content shapes."""
        if value in (None, ""):
            return ""

        if isinstance(value, str):
            return value

        if isinstance(value, list):
            parts = [self._extract_text_value(item) for item in value]
            return "\n".join(part for part in parts if part.strip())

        if isinstance(value, dict):
            parts = []
            for key in ("text", "content", "output_text", "value", "response"):
                if key in value:
                    piece = self._extract_text_value(value.get(key))
                    if piece.strip():
                        parts.append(piece)
            return "\n".join(parts)

        return str(value)

    def _extract_chat_response_text(self, data):
        """Normalize SDK responses so AHK has a stable top-level response field."""
        if not isinstance(data, dict):
            return ""

        choices = data.get("choices") or []
        choice = choices[0] if isinstance(choices, list) and choices else {}
        if isinstance(choice, dict):
            message = choice.get("message") or choice.get("delta") or {}
            if isinstance(message, dict):
                # Cleanly parse and log reasoning text if present
                reasoning = message.get("reasoning") or message.get("reasoning_details")
                if reasoning:
                    if isinstance(reasoning, dict) and "steps" in reasoning:
                        steps = reasoning.get("steps")
                        if isinstance(steps, list):
                            reasoning_text = "\n".join(self._extract_text_value(s) for s in steps)
                        else:
                            reasoning_text = self._extract_text_value(steps)
                    else:
                        reasoning_text = self._extract_text_value(reasoning)
                    
                    if reasoning_text.strip():
                        logger.info(f"[chat] [reasoning] Extracted thinking tokens:\n{reasoning_text.strip()}")
                        data["reasoning"] = reasoning_text.strip()

                for key in ("content", "output_text", "text", "response"):
                    text = self._extract_text_value(message.get(key))
                    if text.strip():
                        return text

            for key in ("content", "output_text", "text", "response"):
                text = self._extract_text_value(choice.get(key))
                if text.strip():
                    return text

        for key in ("output_text", "text", "content", "response"):
            text = self._extract_text_value(data.get(key))
            if text.strip():
                return text

        return ""

    def _first_chat_choice(self, data):
        """Return the first OpenAI-compatible choice dict if present."""
        if not isinstance(data, dict):
            return {}
        choices = data.get("choices") or []
        if choices and isinstance(choices[0], dict):
            return choices[0]
        return {}

    def _has_chat_response_text(self, data) -> bool:
        """Return true when a chat response has user-visible assistant text."""
        return bool(self._extract_chat_response_text(data).strip())

    def _should_retry_empty_chat_response(self, data) -> bool:
        """OpenRouter documents occasional 200 OK responses with no content."""
        if self._has_chat_response_text(data):
            return False
        if not isinstance(data, dict):
            return False
        choice = self._first_chat_choice(data)
        finish_reason = str(choice.get("finish_reason") or "").lower()
        if finish_reason in ("", "length", "error", "stop"):
            return True
        message = choice.get("message") or {}
        if isinstance(message, dict) and message.get("content") in (None, ""):
            return True
        return False

    def _increase_token_budget_for_retry(self, req: dict):
        """Give a no-content retry enough room when the first response hit length."""
        if not isinstance(req, dict):
            return
        for key in ("max_completion_tokens", "max_tokens"):
            if key not in req:
                continue
            try:
                current = int(req.get(key))
            except Exception:
                return
            req[key] = max(current + 64, min(current * 2, 2048))
            return

    def _strip_sampler_params_for_retry(self, req: dict):
        """Drop sampler params that reasoning-only providers refuse silently.

        Some upstream providers (OpenAI o-series, GPT-5 thinking variants,
        Anthropic *-thinking, DeepSeek reasoner) return 200 OK with an empty
        content field when temperature or top_p are present. Removing them on
        the retry recovers a real answer instead of repeating the same empty
        response.
        """
        if not isinstance(req, dict):
            return
        for key in ("temperature", "top_p", "frequency_penalty", "presence_penalty"):
            req.pop(key, None)

    def _format_upstream_exception(self, exc):
        """Return a readable provider/API error without depending on SDK internals."""
        for attr in ("response_data", "data", "body"):
            try:
                value = getattr(exc, attr, None)
                if value:
                    if isinstance(value, (dict, list)):
                        return json.dumps(value)
                    return str(value)
            except Exception:
                pass

        text = str(exc).strip()
        return text or exc.__class__.__name__

    def _status_for_upstream_exception(self, exc):
        """Map SDK exception class names to HTTP statuses for local clients."""
        status_code = getattr(exc, "status_code", None)
        try:
            if status_code:
                return int(status_code)
        except Exception:
            pass

        name = exc.__class__.__name__.lower()
        if "badrequest" in name or "validation" in name:
            return 400
        if "unauthorized" in name or "authentication" in name:
            return 401
        if "forbidden" in name or "permission" in name:
            return 403
        if "rate" in name:
            return 429
        if "timeout" in name:
            return 504
        return 502

    def _append_chat_stream(self, sid, chunk=None, done=False):
        """Append a chunk to the chat stream or mark it as done."""
        if not sid:
            return

        # Timeout protection - if lock acquisition takes too long, something is wrong
        acquired = self._chat_stream_lock.acquire(timeout=5.0)
        if not acquired:
            logger.error(f"[chat] Failed to acquire stream lock for sid={sid} after 5s timeout")
            return
        
        try:
            self._purge_expired_chat_streams()
            entry = self._chat_stream_store.get(sid) or {
                "chunks": [],
                "done": False,
                "size": 0,
            }

            if chunk:
                chunks = entry["chunks"]
                chunk_s = str(chunk)
                chunks.append(chunk_s)
                entry["size"] = entry.get("size", 0) + len(chunk_s)

                # Bound memory only when total size exceeds the cap (avoids O(n) on every append).
                if entry["size"] > self._chat_stream_max_buffer_chars:
                    kept = []
                    running = 0
                    for c in reversed(chunks):
                        running += len(c)
                        kept.append(c)
                        if running >= self._chat_stream_max_buffer_chars:
                            break
                    entry["chunks"] = list(reversed(kept))
                    entry["size"] = running

            if done:
                entry["done"] = True
            entry["exp"] = time() + self._chat_stream_ttl
            self._chat_stream_store[sid] = entry
            conds = self._chat_stream_conds.get(sid, [])
        finally:
            self._chat_stream_lock.release()

        # Notify all waiting conditions for this sid (outside the lock)
        for cond in conds:
            try:
                with cond:
                    cond.notify_all()
            except Exception as e:
                logger.debug(f"Failed to notify stream waiter for sid={sid}: {e}")

    def _start_openrouter_stream_thread(self, sid: str, request_data: dict):
        """Start (or restart) an OpenRouter streaming run that writes into the chat stream store."""
        if not sid:
            return False, "Missing sid"

        api_key = self.get_api_key()
        if not api_key:
            return False, "Missing OpenRouter API key"

        logger.info(f"[chat] Starting new stream thread for sid={sid}")
        
        # Atomic state transition: stop previous run and join thread before starting new one
        cancel = threading.Event()
        prev_thread = None
        
        with self._chat_run_lock:
            prev = self._chat_runs.get(sid)
            if prev:
                logger.info(f"[chat] Found previous run for sid={sid}, stopping it")
                prev_cancel = prev.get("cancel")
                if prev_cancel:
                    prev_cancel.set()
                prev_thread = prev.get("thread")
            
            # Set new run state atomically
            self._chat_runs[sid] = {
                "cancel": cancel,
                "thread": None,
                "started": time(),
            }
        
        # Join previous thread outside the lock to avoid deadlock
        if prev_thread and prev_thread.is_alive():
            logger.info(f"[chat] Joining previous thread for sid={sid}")
            # Give it 2 seconds to finish gracefully
            prev_thread.join(timeout=2.0)
            if prev_thread.is_alive():
                logger.warning(f"[chat] Previous thread for sid={sid} did not finish in time (likely stuck)")
            else:
                logger.info(f"[chat] Previous thread for sid={sid} joined successfully")

        # Clean up any existing Conditions for this sid so replaced SSE handler threads can exit cleanly.
        # This prevents replaced SSE connections from consuming notifications meant for the new one.
        with self._chat_stream_lock:
            stale_conds = self._chat_stream_conds.pop(sid, [])
            logger.debug(f"[chat] Cleaned up {len(stale_conds)} stale SSE conditions for sid={sid}")

        # Notify stale Conditions so replaced SSE handlers exit before the new stream starts.
        for stale_cond in stale_conds:
            try:
                with stale_cond:
                    stale_cond.notify_all()
            except Exception as e:
                logger.debug(f"[chat] Failed to notify stale stream condition for sid={sid}: {e}")

        # Reset output buffer for this run.
        with self._chat_stream_lock:
            self._chat_stream_store[sid] = {
                "exp": time() + self._chat_stream_ttl,
                "chunks": [],
                "done": False,
                "size": 0,
            }
            logger.info(f"[chat] Reset stream buffer for sid={sid}")

        # Create and start new thread with descriptive name for debugging
        t = threading.Thread(
            target=self._openrouter_stream_worker,
            args=(sid, api_key, request_data, cancel),
            daemon=True,
            name=f"OpenRouterStream-{sid[:8]}"
        )
        with self._chat_run_lock:
            self._chat_runs[sid]["thread"] = t
        t.start()
        logger.info(f"[chat] Started new stream thread for sid={sid}, thread={t.name}")
        return True, None

    def _cancel_openrouter_stream(self, sid: str):
        """Cancel an ongoing chat stream."""
        if not sid:
            return
        with self._chat_run_lock:
            run = self._chat_runs.get(sid)
            if run and run.get("cancel"):
                run["cancel"].set()

    def _purge_expired_chat_inits(self):
        """Remove expired chat initialization data."""
        now = time()
        expired = [
            sid
            for sid, v in self._chat_init_store.items()
            if v.get("exp", 0) < now
        ]
        for sid in expired:
            self._chat_init_store.pop(sid, None)

    def _purge_expired_chat_streams(self):
        """Remove expired chat streams."""
        now = time()
        expired = [
            sid
            for sid, v in self._chat_stream_store.items()
            if v.get("exp", 0) < now
        ]
        for sid in expired:
            self._chat_stream_store.pop(sid, None)
            self._chat_stream_conds.pop(sid, None)

    def run_chat_stream(self, body: str):
        """Start a chat stream (OpenRouter streaming)."""
        data, sid = self._parse_body_and_require_sid(body)
        if data is None:
            return

        req_data = data.get("request")
        if isinstance(req_data, str):
            try:
                req_data = json.loads(req_data or "{}")
            except json.JSONDecodeError:
                self.send_json({"error": "Invalid request JSON"}, 400)
                return

        if not isinstance(req_data, dict):
            self.send_json({"error": "Missing request"}, 400)
            return

        ok, err = self._validate_chat_request(req_data)
        if not ok:
            self.send_json({"error": err}, 400)
            return

        ok, err = self._start_openrouter_stream_thread(sid, req_data)
        if not ok:
            self.send_json({"error": err or "Failed"}, 500)
            return

        self.send_json({"ok": True})

    def complete_chat(self, body):
        """Synchronous chat completion for AHK client."""
        if not self._acquire_heavy_slot():
            return
        try:
            try:
                data = json.loads(body or "{}")
            except json.JSONDecodeError:
                self.send_json({"error": "Invalid JSON format"}, 400)
                return
            
            req = data.get("request")
            if not req or not isinstance(req, dict):
                self.send_json({"error": "Invalid request format"}, 400)
                return

            ok, err = self._validate_chat_request(req)
            if not ok:
                self.send_json({"error": err}, 400)
                return

            api_key = self.get_api_key()
            if not api_key:
                self.send_json({"error": "No API key configured"}, 500)
                return

            # Force non-streaming
            req["stream"] = False
            req = self._sanitize_openrouter_request(req)

            last_data = None
            for attempt in range(2):
                # Increase timeout for large/slow prompts (180s)
                resp = self._send_openrouter_chat(api_key, req, timeout_ms=180_000)
                data = resp.model_dump() if hasattr(resp, "model_dump") else resp
                last_data = data
                if not self._should_retry_empty_chat_response(data) or attempt >= 1:
                    break
                logger.warning(
                    "OpenRouter returned no content; retrying once with adjusted token budget "
                    "and relaxed sampler params"
                )
                self._increase_token_budget_for_retry(req)
                # Many reasoning-capable models (o-series, gpt-5-thinking,
                # anthropic *-thinking, deepseek-reasoner) silently return
                # empty content when temperature/top_p are sent. Strip them
                # on the retry so the request shape matches what these
                # providers actually accept.
                self._strip_sampler_params_for_retry(req)

            data = last_data
            if isinstance(data, dict) and not data.get("response"):
                response_text = self._extract_chat_response_text(data)
                if response_text:
                    data["response"] = response_text
            self.send_json(data if isinstance(data, dict) else {"response": str(data or "")})

        except Exception as e:
            error_text = self._format_upstream_exception(e)
            logger.exception("Complete chat failed")
            self.send_json({"error": error_text}, self._status_for_upstream_exception(e))
        finally:
            self._release_heavy_slot()

    def cancel_chat_stream(self, body: str):
        """Cancel a chat stream."""
        data, sid = self._parse_body_and_require_sid(body)
        if data is None:
            return

        self._cancel_openrouter_stream(sid)
        # Mark done to release any waiting clients quickly.
        self._append_chat_stream(sid, None, done=True)
        self.send_json({"ok": True})

    def set_chat_init(self, body: str):
        """Store initialization data for a chat session."""
        data, sid = self._parse_body_and_require_sid(body)
        if data is None:
            return

        # Bound memory usage defensively (local server, but still)
        raw_size = len(body.encode("utf-8")) if body else 0
        if raw_size > 512_000:
            self.send_json({"error": "Payload too large"}, 413)
            return

        payload = {
            "title": data.get("title") or "",
            "model": data.get("model") or "",
            "system": data.get("system") or "",
            "user": data.get("user") or "",
            "assistant": data.get("assistant") or "",
            "autorun": bool(data.get("autorun", False)),
        }

        with self._chat_init_lock:
            self._purge_expired_chat_inits()
            self._chat_init_store[sid] = {
                "exp": time() + self._chat_init_ttl,
                "data": payload,
            }

        self.send_json({"ok": True})

    def get_chat_init(self):
        """Retrieve initialization data for a chat session."""
        sid = self._require_sid_from_query()
        if sid is None:
            return

        with self._chat_init_lock:
            self._purge_expired_chat_inits()
            entry = self._chat_init_store.pop(sid, None)

        if not entry:
            self.send_json({"error": "Not found"}, 404)
            return

        self.send_json({"ok": True, "data": entry.get("data") or {}})

    def push_chat_stream(self, body: str):
        """Push a chunk to a chat stream (used by external clients)."""
        data, sid = self._parse_body_and_require_sid(body)
        if data is None:
            return

        chunk = data.get("chunk")
        done = bool(data.get("done", False))

        # Use _append_chat_stream to avoid code duplication
        self._append_chat_stream(sid, chunk, done)

        self.send_json({"ok": True})

    def get_chat_stream(self):
        """Get accumulated chunks from a chat stream (polling mode)."""
        sid = self._require_sid_from_query()
        if sid is None:
            return

        with self._chat_stream_lock:
            self._purge_expired_chat_streams()
            entry = self._chat_stream_store.get(sid)
            if not entry:
                self.send_json({"ok": True, "chunks": [], "done": False})
                return
            chunks = entry.get("chunks") or []
            done = entry.get("done", False)
            entry["chunks"] = []
            entry["size"] = 0
            self._chat_stream_store[sid] = entry

        self.send_json({"ok": True, "chunks": chunks, "done": done})

    def get_chat_stream_events(self):
        """Get chat stream using Server-Sent Events (streaming mode)."""
        sid = self._require_sid_from_query()
        if sid is None:
            return

        # Always create a new Condition for this request
        cond = threading.Condition()

        with self._chat_stream_lock:
            self._purge_expired_chat_streams()
            # Initialize list if not present
            if sid not in self._chat_stream_conds:
                self._chat_stream_conds[sid] = []
            # Append our new condition to the list
            self._chat_stream_conds[sid].append(cond)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        last_heartbeat = time()
        done = False
        try:
            while True:
                chunks = []
                done = False
                with self._chat_stream_lock:
                    self._purge_expired_chat_streams()
                    # Check if our Condition is still in the list (not replaced by new run)
                    conds = self._chat_stream_conds.get(sid, [])
                    if cond not in conds:
                        break
                    entry = self._chat_stream_store.get(sid)
                    if entry:
                        chunks = entry.get("chunks") or []
                        done = entry.get("done", False)
                        entry["chunks"] = []
                        entry["size"] = 0
                        self._chat_stream_store[sid] = entry

                if chunks:
                    payload = json.dumps({"chunks": chunks, "done": done})
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()

                now = time()
                if now - last_heartbeat > 10:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_heartbeat = now

                if done:
                    self.wfile.write(b'data: {"done": true}\n\n')
                    self.wfile.flush()
                    break

                with cond:
                    cond.wait(timeout=0.05)
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            # Always clean up our Condition — even on client disconnect — to prevent memory creep.
            with self._chat_stream_lock:
                # Only remove our specific Condition from the list
                conds = self._chat_stream_conds.get(sid, [])
                if cond in conds:
                    conds.remove(cond)
                    # Clean up empty list to save memory
                    if not conds:
                        self._chat_stream_conds.pop(sid, None)
