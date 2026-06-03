"""
Lightweight in-memory debug ring buffer + Python logging bridge.

This module exists so the AHK Debug Console can subscribe to a single
unified event stream that includes:

    - Python `logger` calls from anywhere in the server (when debug is on)
    - Structured OpenRouter request / response / error events emitted by
      the OpenRouter client mixin
    - HTTP handler exceptions

Usage from other modules:

    from .debug_log import DebugLog
    DebugLog.event("openrouter.request", {...})
    DebugLog.error("openrouter.error", {"code": 503, "message": "..."})

When DebugLog.enabled is False, event() is a near-no-op (a couple of
attribute lookups). When enabled, events are pushed into a bounded
deque and a threading.Condition is notified for SSE waiters.

This module is intentionally process-local: it does not write to disk,
does not touch the network, and the buffer is cleared when the server
restarts. The AHK Debug Console window controls the on/off state via
POST /debug/log/enable | /debug/log/disable.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from collections import deque
from typing import Any


class _DebugLog:
    """Process-wide singleton (use the module-level DebugLog reference)."""

    MAX_EVENTS = 2000           # ring buffer cap
    MAX_FIELD_CHARS = 16000     # per-field redaction cap

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._events: deque = deque(maxlen=self.MAX_EVENTS)
        self._seq = 0
        self._enabled = False
        self._handler_attached = False
        self._handler: logging.Handler | None = None

    # ---- public API ------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        """Turn the sink on AND attach to the Python root logger so every
        log call also flows into the ring buffer."""
        if self._enabled:
            return
        self._enabled = True
        self._attach_logging_bridge()
        self.event("debug.enabled", {"max_events": self.MAX_EVENTS})

    def disable(self) -> None:
        if not self._enabled:
            return
        self.event("debug.disabled", {})
        self._detach_logging_bridge()
        self._enabled = False

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._seq = 0

    def event(self, kind: str, payload: dict[str, Any] | None = None,
              level: str = "info") -> None:
        """Push a structured event. Cheap when disabled."""
        if not self._enabled and not kind.startswith(("debug.", "error.")):
            return
        rec = {
            "kind": kind,
            "level": level,
            "ts": time.time(),
            "data": self._redact(payload or {}),
        }
        with self._cond:
            self._seq += 1
            rec["seq"] = self._seq
            self._events.append(rec)
            self._cond.notify_all()

    def error(self, kind: str, payload: dict[str, Any] | None = None) -> None:
        """Always-on error event \u2014 captured even with the sink disabled,
        so users can flip Debug Console open and see the last 2000 errors."""
        rec = {
            "kind": kind,
            "level": "error",
            "ts": time.time(),
            "data": self._redact(payload or {}),
        }
        with self._cond:
            self._seq += 1
            rec["seq"] = self._seq
            self._events.append(rec)
            self._cond.notify_all()

    def exception(self, kind: str, exc: BaseException,
                  extra: dict[str, Any] | None = None) -> None:
        data = dict(extra or {})
        data["exception_type"] = exc.__class__.__name__
        data["exception_message"] = str(exc)
        try:
            data["traceback"] = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )[: self.MAX_FIELD_CHARS]
        except Exception:
            data["traceback"] = "<unavailable>"
        self.error(kind, data)

    def snapshot(self, since_seq: int = 0, limit: int = 500) -> dict[str, Any]:
        with self._lock:
            head_seq = self._seq
            out: list[dict[str, Any]] = []
            for rec in self._events:
                if rec["seq"] > since_seq:
                    out.append(rec)
                    if len(out) >= limit:
                        break
            return {
                "enabled": self._enabled,
                "head_seq": head_seq,
                "count": len(out),
                "events": out,
            }

    def wait_for(self, since_seq: int, timeout: float = 5.0) -> list[dict[str, Any]]:
        """Block until new events arrive, then return them. Used by SSE."""
        deadline = time.time() + timeout
        with self._cond:
            while True:
                if self._seq > since_seq:
                    out = [r for r in self._events if r["seq"] > since_seq]
                    return out
                remaining = deadline - time.time()
                if remaining <= 0:
                    return []
                self._cond.wait(timeout=remaining)

    # ---- redaction -------------------------------------------------

    _SENSITIVE_KEYS = {
        "authorization", "auth", "api_key", "apikey",
        "bearer", "secret", "token", "password",
    }
    _BEARER_PREFIX = "Bearer "

    def _redact(self, obj: Any) -> Any:
        try:
            if isinstance(obj, dict):
                out = {}
                for k, v in obj.items():
                    if str(k).lower() in self._SENSITIVE_KEYS:
                        out[k] = "<redacted>"
                    elif isinstance(v, str) and v.startswith(self._BEARER_PREFIX):
                        out[k] = "Bearer <redacted>"
                    else:
                        out[k] = self._redact(v)
                return out
            if isinstance(obj, list):
                return [self._redact(x) for x in obj[:200]]
            if isinstance(obj, str) and len(obj) > self.MAX_FIELD_CHARS:
                return obj[: self.MAX_FIELD_CHARS] + f"... <{len(obj) - self.MAX_FIELD_CHARS} more chars>"
            return obj
        except Exception:
            return "<unredactable>"

    # ---- logging bridge --------------------------------------------

    def _attach_logging_bridge(self) -> None:
        if self._handler_attached:
            return
        h = _DebugLoggingHandler(self)
        h.setLevel(logging.DEBUG)
        h.setFormatter(logging.Formatter(
            "%(name)s | %(message)s"
        ))
        root = logging.getLogger()
        root.addHandler(h)
        # Make sure the root logger doesn't suppress DEBUG.
        if root.level == logging.NOTSET or root.level > logging.DEBUG:
            root.setLevel(logging.DEBUG)
        self._handler = h
        self._handler_attached = True

    def _detach_logging_bridge(self) -> None:
        if not self._handler_attached:
            return
        try:
            logging.getLogger().removeHandler(self._handler)
        except Exception:
            pass
        self._handler = None
        self._handler_attached = False


class _DebugLoggingHandler(logging.Handler):
    """Forwards Python `logger` records into the DebugLog ring buffer."""

    def __init__(self, sink: "_DebugLog") -> None:
        super().__init__()
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            kind = "log." + record.levelname.lower()
            payload = {
                "logger": record.name,
                "message": record.getMessage(),
            }
            if record.exc_info:
                payload["traceback"] = self.format(record)
            self._sink.event(kind, payload, level=record.levelname.lower())
        except Exception:
            pass


# Module-level singleton.
DebugLog = _DebugLog()
