"""
OpenRouter client constants and imports shared across mixin modules.
"""

import logging
from ..vendor import _ensure_openrouter_python_sdk

MAX_LLM_CALL_ATTEMPTS = 2
BASE_TIMEOUT_MS = 45_000  # 45 seconds for the first attempt
STREAM_IDLE_TIMEOUT_SEC = 25  # 25 seconds idle timeout for streaming

__all__ = [
    "MAX_LLM_CALL_ATTEMPTS",
    "BASE_TIMEOUT_MS",
    "STREAM_IDLE_TIMEOUT_SEC",
    "_ensure_openrouter_python_sdk",
    "logging",
]
