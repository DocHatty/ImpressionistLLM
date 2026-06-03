"""
Decode OpenRouter / upstream-provider error responses into the structured
shape the AHK Debug Console expects.

Reference: https://openrouter.ai/docs/api/reference/errors

Error response shape:
    {
        "error": {
            "code": <int>,
            "message": <str>,
            "metadata": { ... }
        }
    }

We extract:
    - http_status         : the HTTP status code (often == error.code for 4xx)
    - error_code          : the JSON error.code value (str/int)
    - error_message       : human-readable message
    - error_category      : our friendly bucketed label (e.g. "rate_limited",
                            "no_provider", "moderation", "guardrail",
                            "provider_error", "bad_request", "auth", "credits")
    - retry_after_seconds : parsed from Retry-After header (429/503 only)
    - metadata            : full metadata dict (provider_name, raw, reasons,
                            patterns, flagged_input, etc.)
    - hint                : one-line suggested user action

This module never raises \u2014 worst case the unknown fields are returned as is.
"""

from __future__ import annotations

import json
import re
from typing import Any


_CODE_TO_CATEGORY = {
    400: "bad_request",
    401: "auth",
    402: "credits",
    403: "forbidden",
    408: "timeout",
    429: "rate_limited",
    500: "server_error",
    502: "provider_error",
    503: "no_provider",
    524: "timeout",
    529: "overloaded",
}

_HINTS_BY_CATEGORY = {
    "bad_request":
        "Check the request: missing parameter, wrong field type, or model id not recognized by this endpoint.",
    "auth":
        "Your API key is invalid, expired, or disabled. Generate a new one at openrouter.ai/keys.",
    "credits":
        "Your account is out of credits. Add credits at openrouter.ai/credits.",
    "forbidden":
        "The request was blocked. If metadata contains 'patterns' or 'reasons' a guardrail or moderation filter rejected it.",
    "timeout":
        "Upstream timed out. Retry with a longer timeout or a different model.",
    "rate_limited":
        "Rate limited. Honor the Retry-After header and back off.",
    "server_error":
        "Provider returned an internal error. Retry or pick another model.",
    "provider_error":
        "The chosen upstream provider returned an invalid response. Retry or pick another model.",
    "no_provider":
        "No provider available that meets your routing requirements. "
        "If you set ProviderZDR=true or AllowFallbacks=false in [OpenRouter], "
        "relax those and retry.",
    "overloaded":
        "Provider is overloaded. Retry after a short delay.",
    "moderation":
        "Input was flagged by a moderation filter. See metadata.reasons.",
    "guardrail":
        "A configured guardrail blocked the request. See metadata.patterns / metadata.pipeline.",
    "model_not_found":
        "OpenRouter does not recognize this model id. It may be deprecated, "
        "renamed, or not in your account's available list. Refresh the model "
        "list and pick a current slug.",
    "unknown":
        "Unrecognized error. See raw_body for full provider output.",
}


def decode_error_response(
    *,
    http_status: int | None = None,
    raw_body: str | bytes | None = None,
    headers: dict[str, Any] | None = None,
    exception: BaseException | None = None,
) -> dict[str, Any]:
    """Return a normalized error dict suitable for logging and UI display."""

    out: dict[str, Any] = {
        "http_status": int(http_status) if http_status is not None else None,
        "error_code": None,
        "error_message": "",
        "error_category": "unknown",
        "retry_after_seconds": None,
        "metadata": {},
        "raw_body": "",
        "hint": "",
        "provider_name": "",
    }

    if isinstance(raw_body, (bytes, bytearray)):
        try:
            raw_body = raw_body.decode("utf-8", errors="replace")
        except Exception:
            raw_body = ""

    if raw_body:
        out["raw_body"] = raw_body[:8000]
        try:
            parsed = json.loads(raw_body)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            err = parsed.get("error")
            if isinstance(err, dict):
                out["error_code"] = err.get("code")
                out["error_message"] = str(err.get("message") or "")
                md = err.get("metadata")
                if isinstance(md, dict):
                    out["metadata"] = md
                    if "provider_name" in md:
                        out["provider_name"] = str(md.get("provider_name") or "")

    # Retry-After header
    if headers:
        ra = headers.get("Retry-After") or headers.get("retry-after")
        if ra not in (None, ""):
            try:
                secs = float(ra)
                if secs > 0:
                    out["retry_after_seconds"] = secs
            except Exception:
                pass

    # Exception fallback (raw transport errors)
    if exception is not None and not out["error_message"]:
        out["error_message"] = str(exception)
        out["exception_type"] = exception.__class__.__name__

    # Categorize
    out["error_category"] = _classify(out)
    out["hint"] = _HINTS_BY_CATEGORY.get(out["error_category"], _HINTS_BY_CATEGORY["unknown"])

    return out


def _classify(decoded: dict[str, Any]) -> str:
    status = decoded.get("http_status")
    msg = (decoded.get("error_message") or "").lower()
    md = decoded.get("metadata") or {}

    # Specific message patterns first \u2014 these override the status code.
    if _looks_like_model_not_found(msg):
        return "model_not_found"
    if "reasons" in md and "flagged_input" in md:
        return "moderation"
    if "patterns" in md or _looks_like_guardrail(msg):
        return "guardrail"

    if isinstance(status, int) and status in _CODE_TO_CATEGORY:
        return _CODE_TO_CATEGORY[status]

    # Try to infer from JSON error.code if HTTP status missing
    ec = decoded.get("error_code")
    if isinstance(ec, int) and ec in _CODE_TO_CATEGORY:
        return _CODE_TO_CATEGORY[ec]

    if "rate limit" in msg or "too many requests" in msg:
        return "rate_limited"
    if "credit" in msg or "insufficient" in msg:
        return "credits"
    if "unauthor" in msg or "invalid api key" in msg:
        return "auth"
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    if "no available" in msg or "no provider" in msg or "routing requirements" in msg:
        return "no_provider"
    return "unknown"


_MODEL_NOT_FOUND_PATTERNS = (
    re.compile(r"\bmodel(?:\s+or\s+endpoint)?\s+(?:not\s+found|is\s+not\s+available|does\s+not\s+exist)", re.I),
    re.compile(r"\bunknown\s+model\b", re.I),
    re.compile(r"\bmodel\s+'[^']+'\s+(?:not\s+found|does\s+not\s+exist|unknown)", re.I),
)


def _looks_like_model_not_found(msg: str) -> bool:
    if not msg:
        return False
    return any(p.search(msg) for p in _MODEL_NOT_FOUND_PATTERNS)


def _looks_like_guardrail(msg: str) -> bool:
    if not msg:
        return False
    return ("blocked" in msg and ("prompt" in msg or "injection" in msg or "guardrail" in msg))
