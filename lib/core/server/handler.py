"""
Main HTTP request handler that orchestrates all server functionality.
Assembles all mixin classes and provides routing and HTTP infrastructure.
"""
import threading
import traceback
import re
from email.utils import formatdate
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from time import time
from urllib.parse import parse_qs, urlparse

from ._shared import (
    json,
    logger,
    _heavy_op_semaphore,
    _heavy_op_lock,
    _API_PROMPT_PREFIX,
    _API_MODEL_DEFAULTS_PREFIX,
    _HEAVY_OP_CONCURRENCY,
    _HEAVY_OP_QUEUE_LIMIT,
    _HEAVY_OP_QUEUE_TIMEOUT_SEC,
)
from . import _shared
from .chat import ChatMixin
from .debug_log import DebugLog
from .image_preprocess import ImagePreprocessMixin
from .models import ModelsMixin
from .openrouter_client import OpenRouterClientMixin
from .prompt_io import PromptIOMixin
from .static_files import StaticFilesMixin


class PromptHandler(
    ChatMixin,
    ImagePreprocessMixin,
    OpenRouterClientMixin,
    PromptIOMixin,
    ModelsMixin,
    StaticFilesMixin,
    SimpleHTTPRequestHandler,
):
    """Main request handler for prompt, model, chat, and static file APIs."""

    # Class-level state
    script_dir = None
    _api_key_cache = None
    _chat_html_cache = None  # Cache chat.html in memory
    _html_bytes_cache = {}  # path -> (mtime, bytes)
    _html_bytes_cache_max_entries = 64
    _chat_init_store = {}  # sid -> {"exp": float, "data": dict}
    _chat_init_lock = threading.Lock()
    _chat_init_ttl = 300  # seconds
    _chat_stream_store = {}  # sid -> {"exp": float, "chunks": list, "done": bool, "size": int}
    _chat_stream_lock = threading.Lock()
    _chat_stream_ttl = 600  # seconds
    _chat_stream_conds = {}  # sid -> [threading.Condition, ...]
    _chat_run_lock = threading.Lock()
    _chat_runs = {}  # sid -> {"cancel": threading.Event, "thread": threading.Thread, "started": float}
    _chat_stream_max_buffer_chars = 2_000_000
    _prompt_list_cache = None  # {"key": tuple, "data": [...]}
    _prompt_list_cache_key = None
    _prompt_parsed_cache = {}  # path -> {"mtime": float, "data": {...}}
    _prompt_parsed_cache_max_entries = 256
    _cache_lock = threading.Lock()
    _prompt_write_lock = threading.Lock()

    def log_message(self, *args):
        """Suppress default HTTPServer request logging; we use explicit logger calls."""
        pass

    # -------------------------------------------------------------------------
    # HTTP Infrastructure
    # -------------------------------------------------------------------------

    def _set_cache_headers(self, max_age: int = 3600):
        """Mark this response as cacheable and set headers."""
        expires = formatdate(time() + max_age, usegmt=True)
        self.send_header("Cache-Control", f"public, max-age={max_age}")
        self.send_header("Pragma", "cache")
        self.send_header("Expires", expires)
        self._cache_headers_set = True

    def end_headers(self):
        """Override to add CORS headers."""
        origin = self.headers.get("Origin", "")
        if origin.startswith(("http://127.0.0.1", "http://localhost")):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

        if not getattr(self, "_cache_headers_set", False):
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        else:
            self._cache_headers_set = False

        super().end_headers()

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        """Handle GET requests."""
        try:
            self._handle_get()
        except (ConnectionError, BrokenPipeError) as e:
            logger.info(f"GET {self.path} client connection closed: {e}")
        except Exception as e:
            err_msg = str(e)
            if "10053" in err_msg or "10054" in err_msg or "Broken pipe" in err_msg:
                logger.info(f"GET {self.path} client connection aborted: {e}")
                return
            logger.error(f"GET {self.path} failed: {e}")
            logger.debug(traceback.format_exc())
            try:
                DebugLog.exception("http.get_failed", e, {"path": self.path})
            except Exception:
                pass
            try:
                self.send_json({"error": str(e)}, 500)
            except Exception:
                pass  # Connection may already be closed

    def do_POST(self):
        """Handle POST requests."""
        try:
            self._handle_post()
        except (ConnectionError, BrokenPipeError) as e:
            logger.info(f"POST {self.path} client connection closed: {e}")
        except Exception as e:
            err_msg = str(e)
            if "10053" in err_msg or "10054" in err_msg or "Broken pipe" in err_msg:
                logger.info(f"POST {self.path} client connection aborted: {e}")
                return
            logger.error(f"POST {self.path} failed: {e}")
            logger.debug(traceback.format_exc())
            try:
                DebugLog.exception("http.post_failed", e, {"path": self.path})
            except Exception:
                pass
            try:
                self.send_json({"error": str(e)}, 500)
            except Exception:
                pass  # Connection may already be closed

    # 25 MB: large enough to accept the max 5 screenshots at full resolution
    # (1400px max edge, JPEG-90 base64 ≈ ~1 MB each plus headroom for the
    # preprocessed contact sheet / panel crops / zooms). The previous 10 MB
    # cap was getting clipped on multi-screenshot routing with PNG inputs.
    _MAX_BODY_BYTES = 25 * 1024 * 1024  # 25 MB

    def read_body(self):
        """Read request body with size limit."""
        length_str = self.headers.get("Content-Length", "0").strip()
        try:
            length = int(length_str)
        except (TypeError, ValueError):
            self.send_error(400, "Invalid Content-Length")
            return None
        if length < 0:
            self.send_error(400, "Invalid Content-Length")
            return None
        if length <= 0:
            return ""
        if length > self._MAX_BODY_BYTES:
            self.send_error(413, "Payload too large")
            return None
        try:
            return self.rfile.read(length).decode("utf-8")
        except UnicodeDecodeError:
            self.send_error(400, "Request body must be valid UTF-8")
            return None

    def send_json(self, data, status=200):
        """Send JSON response."""
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    # -------------------------------------------------------------------------
    # Shared Utilities
    # -------------------------------------------------------------------------

    @staticmethod
    def _prune_lru_cache(cache, max_entries: int):
        """Prune cache to max entries."""
        while len(cache) > max_entries:
            cache.pop(next(iter(cache)), None)

    @staticmethod
    def _extract_sid(data) -> str:
        """Extract session ID from data."""
        if not isinstance(data, dict):
            return ""
        return str(data.get("sid") or "").strip()

    def _validate_sid(self, sid):
        """Validate locally generated chat/session ids."""
        sid = str(sid or "").strip()
        if not sid or len(sid) > 128 or not re.fullmatch(r"[A-Za-z0-9_-]+", sid):
            return False, "Invalid sid"
        return True, None

    def _parse_json_body(self, body: str):
        """Parse JSON body and handle errors."""
        try:
            return json.loads(body or "{}")
        except json.JSONDecodeError:
            self.send_json({"error": "Invalid JSON"}, 400)
            return None

    def _parse_body_and_require_sid(self, body: str):
        """Parse body and require session ID."""
        data = self._parse_json_body(body)
        if data is None:
            return None, None
        sid = self._extract_sid(data)
        if not sid:
            self.send_json({"error": "Missing sid"}, 400)
            return None, None
        ok, message = self._validate_sid(sid)
        if not ok:
            self.send_json({"error": message}, 400)
            return None, None
        return data, sid

    def _require_sid_from_query(self):
        """Extract and require session ID from query string."""
        qs = parse_qs(urlparse(self.path).query or "")
        sid = (qs.get("sid") or [""])[0].strip()
        if not sid:
            self.send_json({"error": "Missing sid"}, 400)
            return None
        ok, message = self._validate_sid(sid)
        if not ok:
            self.send_json({"error": message}, 400)
            return None
        return sid

    def _validate_prompt_name(self, name):
        """Ensure the prompt name is safe for the filesystem (Windows-compatible)."""
        name = str(name or "").strip()
        if not name:
            return False, "Prompt name cannot be empty"
        if len(name) > 120:
            return False, "Prompt name too long"
        if any(ord(ch) < 32 for ch in name):
            return False, "Prompt name cannot contain control characters"
        invalid_chars = r'\\/:*?"<>|'
        if any(ch in name for ch in invalid_chars):
            return False, f"Prompt name cannot contain any of: {invalid_chars}"
        if name.endswith((" ", ".")):
            return False, "Prompt name cannot end with a space or period"
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        stem = name.split(".", 1)[0].upper()
        if stem in reserved:
            return False, "Prompt name cannot use a reserved Windows device name"
        return True, None

    def _get_default_output_settings(self, name):
        """
        Output behavior defaults by prompt.
        Loads from prompts/_meta/output_defaults.json if available,
        with a hardcoded fallback.
        """
        baseline = {"useEditWindow": False, "useChatSession": False}
        if not name:
            return dict(baseline)

        normalized = name.strip().lower()

        # Try to load from the JSON manifest
        try:
            if self.script_dir:
                manifest_path = Path(self.script_dir) / "prompts" / "_meta" / "output_defaults.json"
                if manifest_path.is_file():
                    with open(manifest_path, "r", encoding="utf-8") as f:
                        data = json.loads(f.read())
                        if isinstance(data, dict) and normalized in data:
                            val = data[normalized]
                            if isinstance(val, dict):
                                return {
                                    "useEditWindow": bool(val.get("useEditWindow", False)),
                                    "useChatSession": bool(val.get("useChatSession", False))
                                }
        except Exception as e:
            logger.warning(f"Failed to load output defaults from manifest: {e}")

        # Fallback to hardcoded defaults
        if normalized in ("association", "pirads"):
            return {"useEditWindow": True, "useChatSession": False}
        if normalized in ("differential", "staging", "walkthrough", "whats the deal", "whats-the-deal"):
            return {"useEditWindow": False, "useChatSession": True}

        return dict(baseline)

    def _normalize_output_settings(self, settings):
        """Sanitize output settings while keeping the user's choice."""
        settings = settings or {}
        use_edit = bool(settings.get("useEditWindow"))
        use_chat = bool(settings.get("useChatSession"))

        # Chat session takes priority; ensure only one mode at a time
        if use_chat:
            use_edit = False

        return {"useEditWindow": use_edit, "useChatSession": use_chat}

    def _heavy_status(self):
        """Return local heavy-operation queue state."""
        with _heavy_op_lock:
            return {
                "concurrency": _HEAVY_OP_CONCURRENCY,
                "queue_limit": _HEAVY_OP_QUEUE_LIMIT,
                "queue_timeout_sec": _HEAVY_OP_QUEUE_TIMEOUT_SEC,
                "active": _shared._heavy_op_active,
                "waiting": _shared._heavy_op_waiting,
            }

    def _acquire_heavy_slot(self, timeout_sec: int = _HEAVY_OP_QUEUE_TIMEOUT_SEC):
        """Acquire a heavy-operation slot, waiting briefly instead of failing immediately."""
        queue_full_status = None
        with _heavy_op_lock:
            if _shared._heavy_op_waiting >= _HEAVY_OP_QUEUE_LIMIT:
                queue_full_status = {
                    "concurrency": _HEAVY_OP_CONCURRENCY,
                    "queue_limit": _HEAVY_OP_QUEUE_LIMIT,
                    "queue_timeout_sec": _HEAVY_OP_QUEUE_TIMEOUT_SEC,
                    "active": _shared._heavy_op_active,
                    "waiting": _shared._heavy_op_waiting,
                }
            else:
                _shared._heavy_op_waiting += 1

        if queue_full_status is not None:
            self.send_json(
                {
                    "error": "Server queue is full; please retry shortly",
                    "queue": queue_full_status,
                },
                429,
            )
            return False

        try:
            acquired = _heavy_op_semaphore.acquire(timeout=timeout_sec)
        finally:
            with _heavy_op_lock:
                _shared._heavy_op_waiting = max(0, _shared._heavy_op_waiting - 1)

        if not acquired:
            self.send_json(
                {
                    "error": "Server queue timed out; please retry shortly",
                    "queue": self._heavy_status(),
                },
                429,
            )
            return False

        with _heavy_op_lock:
            _shared._heavy_op_active += 1
        return True

    def _release_heavy_slot(self):
        """Release a previously acquired heavy-operation slot."""
        with _heavy_op_lock:
            _shared._heavy_op_active = max(0, _shared._heavy_op_active - 1)
        _heavy_op_semaphore.release()

    # -------------------------------------------------------------------------
    # Routing
    # -------------------------------------------------------------------------

    def _verify_api_secret(self) -> bool:
        """Verify dynamic subprocess API secret if set in environment.

        Supports both the X-API-Secret header (for fetch requests) and
        the ImpressionistSession cookie (for EventSource/SSE requests that
        cannot customize headers).
        """
        import os
        expected_secret = os.environ.get("IMPRESSIONIST_API_SECRET", "")
        if not expected_secret:
            return True  # Fallback for dev mode

        path = urlparse(self.path).path.rstrip("/")
        if not path.startswith("/api"):
            return True
        if path in ("/api/health", "/api/status", "/health", "/status"):
            return True

        # 1. Check direct X-API-Secret header (preferred for standard APIs)
        received_secret = self.headers.get("X-API-Secret", "").strip()
        if received_secret == expected_secret:
            return True

        # 2. Check for a valid session cookie (essential for EventSource/SSE)
        from http.cookies import SimpleCookie
        cookie_header = self.headers.get("Cookie", "")
        if cookie_header:
            try:
                cookie = SimpleCookie()
                cookie.load(cookie_header)
                if "ImpressionistSession" in cookie:
                    session_cookie_id = cookie["ImpressionistSession"].value
                    if _shared.validate_session(session_cookie_id):
                        return True
            except Exception:
                pass

        logger.warning(f"Unauthorized API request blocked: {self.path} (missing/invalid secret and session)")
        self.send_json({"error": "Unauthorized: Invalid or missing API secret"}, 401)
        return False

    def _handle_get(self):
        """Route GET requests."""
        if not self._verify_api_secret():
            return
        path = urlparse(self.path).path.rstrip("/")

        # Health check
        if path in ("", "/", "/health", "/status"):
            if path in ("/health", "/status"):
                self.send_json(
                    {
                        "status": "ok",
                        "port": self.server.server_port,
                        "script_dir": str(self.script_dir or ""),
                        "queue": self._heavy_status(),
                    }
                )
                return
            # Serve HTML
            self.serve_html()
            return

        # Context Manager
        if path == "/context":
            self.serve_context_manager()
            return

        # Chat UI
        if path == "/chat":
            # Serve cached template with API key injected for instant readiness
            self.serve_chat_html()
            return

        # Debug Console UI
        if path == "/debug":
            self.serve_html_file("debug.html")
            return

        # Serve JavaScript files
        if path.endswith(".js"):
            self.serve_js(path)
            return

        # Serve CSS files
        if path.endswith(".css"):
            self.serve_css(path)
            return

        # API: List prompts
        if path == "/api/prompts":
            self.list_prompts()
            return

        # API: Get single prompt
        if path.startswith(_API_PROMPT_PREFIX):
            name = path[len(_API_PROMPT_PREFIX) :]
            self.get_prompt(name)
            return

        # API: Get models (with full metadata from OpenRouter)
        if path == "/api/models":
            self.get_models()
            return

        # API: Get model defaults (supported parameters from OpenRouter)
        if path.startswith(_API_MODEL_DEFAULTS_PREFIX):
            model_id = path[len(_API_MODEL_DEFAULTS_PREFIX) :]
            self.get_model_defaults(model_id)
            return

        # Chat init payload (avoid large URL params)
        if path == "/api/chat/init":
            self.get_chat_init()
            return

        # Chat stream pull
        if path == "/api/chat/stream":
            self.get_chat_stream()
            return
        if path == "/api/chat/stream/events":
            self.get_chat_stream_events()
            return

        # Debug Console: snapshot + SSE
        if path == "/api/debug/snapshot":
            self.debug_snapshot()
            return
        if path == "/api/debug/events":
            self.debug_events_sse()
            return

        self.send_error(404)

    def _handle_post(self):
        """Route POST requests."""
        if not self._verify_api_secret():
            return
        path = urlparse(self.path).path.rstrip("/")
        body = self.read_body()
        if body is None:
            return

        # Start OpenRouter streaming run (server-side; avoids AHK streaming edge cases)
        if path == "/api/chat/run":
            self.run_chat_stream(body)
            return

        # Generate single-use launch ticket for browser windows
        if path == "/api/tickets/create":
            ticket = _shared.create_ticket()
            self.send_json({"ticket": ticket})
            return

        if path == "/api/llm/complete":
            self.complete_chat(body)
            return

        if path == "/api/screenshot/preprocess":
            self.preprocess_screenshot_images(body)
            return


        # Cancel a running OpenRouter streaming run
        if path in ("/api/chat/cancel", "/api/chat/closed"):
            self.cancel_chat_stream(body)
            return

        # Graceful shutdown endpoint. Called by AHK during tray-Exit before it
        # resorts to taskkill /T /F. Returning 200 here gives the server a chance
        # to flush logs and close upstream OpenRouter sockets cleanly, which
        # avoids "ConnectionResetError" entries in the OpenRouter dashboard and
        # prevents the next launch from binding to a port stuck in TIME_WAIT.
        if path == "/api/shutdown":
            self._handle_shutdown_request()
            return

        # Chat init payload (avoid large URL params)
        if path == "/api/chat/init":
            self.set_chat_init(body)
            return

        # Chat stream push
        if path == "/api/chat/stream":
            self.push_chat_stream(body)
            return

        # Save prompt
        if path == "/api/prompt":
            self.save_prompt(body)
            return

        # Delete prompt
        if path == "/api/prompt/delete":
            self.delete_prompt(body)
            return

        # Debug Console control
        if path == "/api/debug/enable":
            DebugLog.enable()
            self.send_json({"ok": True, "enabled": True})
            return
        if path == "/api/debug/disable":
            DebugLog.disable()
            self.send_json({"ok": True, "enabled": False})
            return
        if path == "/api/debug/clear":
            DebugLog.clear()
            self.send_json({"ok": True})
            return

        self.send_error(404)

    # -------------------------------------------------------------------------
    # Graceful shutdown handler
    # -------------------------------------------------------------------------

    def _handle_shutdown_request(self):
        """POST /api/shutdown: ack the request, then trigger graceful shutdown.

        We must send the response BEFORE calling _graceful_shutdown(), since
        shutdown() blocks until all in-flight requests (including this one)
        finish. Scheduling the actual shutdown on a daemon thread lets this
        handler return cleanly first.
        """
        self.send_json({"ok": True, "shutting_down": True})
        try:
            # Import lazily to avoid a circular dependency at module load time.
            from .. import http_server as _hs  # type: ignore
        except Exception:
            try:
                import http_server as _hs  # type: ignore  # pragma: no cover
            except Exception:
                logger.warning("shutdown: could not import http_server module")
                return

        def _do():
            try:
                _hs._graceful_shutdown(reason="/api/shutdown")
            except Exception as e:
                logger.warning(f"shutdown: graceful shutdown raised: {e}")

        threading.Thread(target=_do, name="ShutdownTrigger", daemon=True).start()

    # -------------------------------------------------------------------------
    # Debug Console handlers
    # -------------------------------------------------------------------------

    def debug_snapshot(self):
        """Return up to N buffered events since a given sequence number.

        Query params:
            since  - optional integer seq (default 0)
            limit  - optional integer (default 500)
        """
        qs = parse_qs(urlparse(self.path).query or "")
        try:
            since = int((qs.get("since") or ["0"])[0])
        except (TypeError, ValueError):
            since = 0
        try:
            limit = int((qs.get("limit") or ["500"])[0])
        except (TypeError, ValueError):
            limit = 500
        limit = max(1, min(limit, 2000))
        self.send_json(DebugLog.snapshot(since_seq=since, limit=limit))

    def debug_events_sse(self):
        """Server-Sent Events stream of debug events. Long-polls DebugLog.

        Query param `since` lets the client resume after a known seq.
        Each event is one SSE message: data: <json>\\n\\n.
        """
        qs = parse_qs(urlparse(self.path).query or "")
        try:
            since = int((qs.get("since") or ["0"])[0])
        except (TypeError, ValueError):
            since = 0

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        # Send the current snapshot first so a freshly opened console is
        # immediately populated with recent events without waiting for new ones.
        try:
            initial = DebugLog.snapshot(since_seq=since, limit=500)
            for ev in initial.get("events", []):
                line = "data: " + json.dumps(ev) + "\n\n"
                self.wfile.write(line.encode("utf-8"))
                since = max(since, ev.get("seq", since))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as e:
            logger.warning(f"[debug] initial snapshot send failed: {e}")
            return

        last_heartbeat = time()
        try:
            while True:
                evs = DebugLog.wait_for(since_seq=since, timeout=5.0)
                for ev in evs:
                    line = "data: " + json.dumps(ev) + "\n\n"
                    self.wfile.write(line.encode("utf-8"))
                    since = max(since, ev.get("seq", since))
                self.wfile.flush()
                now = time()
                if now - last_heartbeat > 10:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_heartbeat = now
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as e:
            logger.debug(f"[debug] SSE loop exited: {e}")
            return
