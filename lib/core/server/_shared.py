"""
Shared constants, utilities, and the JSON adapter used across all server modules.
"""
import sys as _sys
from pathlib import Path as _BootPath

# ---------------------------------------------------------------------------
# Vendor path: ensure vendored packages (orjson, openrouter, etc.) are importable
# ---------------------------------------------------------------------------
# Only add vendor_site_packages if we are not running inside a virtual environment (e.g. on Windows)
if _sys.prefix == _sys.base_prefix:
    _vendor_dir = str((_BootPath(__file__).resolve().parent.parent / "vendor_site_packages"))
    if _vendor_dir not in _sys.path:
        _sys.path.insert(0, _vendor_dir)

# ---------------------------------------------------------------------------
# JSON: use orjson (Rust/SIMD, 4-25x faster) with stdlib fallback
# ---------------------------------------------------------------------------
try:
    import orjson as _orjson

    class json:
        """Thin adapter so the rest of the file keeps using json.loads / json.dumps."""

        JSONDecodeError = _orjson.JSONDecodeError

        @staticmethod
        def loads(s, **kw):
            if isinstance(s, str):
                s = s.encode("utf-8")
            try:
                return _orjson.loads(s)
            except Exception:
                import json as _stdlib_json
                return _stdlib_json.loads(s, **kw)

        @staticmethod
        def dumps(obj, *, indent=None, default=None, **kw):
            opts = _orjson.OPT_NON_STR_KEYS | _orjson.OPT_SERIALIZE_NUMPY
            if indent:
                opts |= _orjson.OPT_INDENT_2
            try:
                return _orjson.dumps(obj, option=opts, default=default).decode("utf-8")
            except Exception:
                import json as _stdlib_json
                return _stdlib_json.dumps(obj, indent=indent, default=default, **kw)

except ImportError:
    import json  # type: ignore[no-redef]  # stdlib fallback

import logging
import threading

# Configure logging
_LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"

# Prompt file section delimiters
_DELIM_EXAMPLES = "---EXAMPLES---"
_DELIM_EXAMPLE = "---EXAMPLE---"
_DELIM_OUTPUT = "---OUTPUT---"
_API_PROMPT_PREFIX = "/api/prompt/"
_API_MODEL_DEFAULTS_PREFIX = "/api/model-defaults/"

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format=_LOG_FORMAT,
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# Heavy operation semaphore for rate limiting expensive operations
_HEAVY_OP_CONCURRENCY = 2
_HEAVY_OP_QUEUE_LIMIT = 12
_HEAVY_OP_QUEUE_TIMEOUT_SEC = 90
_heavy_op_semaphore = threading.BoundedSemaphore(_HEAVY_OP_CONCURRENCY)
_heavy_op_lock = threading.Lock()
_heavy_op_active = 0
_heavy_op_waiting = 0


def _is_truthy(value: str) -> bool:
    """Check if a string value represents a truthy value."""
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Security: Thread-safe, self-pruning Single-Use Launch Tickets & Sessions
# ---------------------------------------------------------------------------
_tickets_lock = threading.Lock()
_valid_tickets = {}  # ticket_id -> expiry_timestamp

_sessions_lock = threading.Lock()
_valid_sessions = {}  # session_id -> expiry_timestamp


def create_ticket(ttl: float = 15.0) -> str:
    """Generate a cryptographically secure short-lived single-use ticket."""
    import secrets
    import time

    ticket_id = secrets.token_hex(16)
    with _tickets_lock:
        _valid_tickets[ticket_id] = time.time() + ttl
    return ticket_id


def consume_ticket(ticket_id: str) -> bool:
    """Validate, immediately consume (delete) a ticket, and prune expired ones."""
    if not ticket_id:
        return False
    import time

    now = time.time()
    with _tickets_lock:
        # Self-prune expired tickets to prevent leaks/memory growth
        expired = [k for k, v in _valid_tickets.items() if v < now]
        for k in expired:
            _valid_tickets.pop(k, None)

        if ticket_id in _valid_tickets:
            expiry = _valid_tickets.pop(ticket_id)
            if expiry >= now:
                return True
    return False


def create_session(ttl: float = 86400.0) -> str:
    """Create a new authenticated session ID for browser pages with a default 24h lifetime."""
    import secrets
    import time

    session_id = secrets.token_hex(16)
    with _sessions_lock:
        _valid_sessions[session_id] = time.time() + ttl
    return session_id


def validate_session(session_id: str, ttl_extend: float = 86400.0) -> bool:
    """Verify if a session ID is currently active and valid, prune expired ones, and extend lease."""
    if not session_id:
        return False
    import time

    now = time.time()
    with _sessions_lock:
        # Self-prune expired sessions to prevent leaks/memory growth
        expired = [k for k, v in _valid_sessions.items() if v < now]
        for k in expired:
            _valid_sessions.pop(k, None)

        if session_id in _valid_sessions:
            expiry = _valid_sessions[session_id]
            if expiry >= now:
                # Extend the lease of the valid active session
                _valid_sessions[session_id] = now + ttl_extend
                return True
    return False

