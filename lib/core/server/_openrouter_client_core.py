"""
OpenRouter client mixin for PromptHandler.
Modularized for maintainability.
"""

import configparser
import os

from ._shared import json, logger
from .openrouter_modules.openrouter_client_config import (
    MAX_LLM_CALL_ATTEMPTS,
    BASE_TIMEOUT_MS,
    STREAM_IDLE_TIMEOUT_SEC,
)
from .openrouter_modules.openrouter_client_helpers import OpenRouterClientHelpersMixin
from .openrouter_modules.openrouter_client_sync import OpenRouterClientSyncMixin
from .openrouter_modules.openrouter_client_stream import OpenRouterClientStreamMixin


class _OpenRouterClientCore(
    OpenRouterClientHelpersMixin,
    OpenRouterClientSyncMixin,
    OpenRouterClientStreamMixin,
):
    """Mixin providing OpenRouter SDK integration and LLM call methods."""

    _API_KEY_PLACEHOLDERS = {"", "YOUR_API_KEY_HERE", "your_api_key_here"}

    def get_api_key(self):
        """Load the OpenRouter API key from env or config/settings.ini."""
        env_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if env_key and env_key not in self._API_KEY_PLACEHOLDERS:
            return env_key

        script_dir = getattr(self, "script_dir", None)
        if not script_dir:
            return ""

        config_path = script_dir / "config" / "settings.ini"
        try:
            mtime = config_path.stat().st_mtime
        except OSError:
            logger.warning("API key config file not found: %s", config_path)
            return ""

        cache = getattr(self, "_api_key_cache", None)
        if cache and cache.get("mtime") == mtime:
            return cache.get("value", "")

        parser = configparser.ConfigParser()
        try:
            parser.read(config_path, encoding="utf-8-sig")
            api_key = parser.get("API", "APIKey", fallback="").strip()
        except Exception:
            logger.exception("Failed to read API key config")
            api_key = ""

        if api_key in self._API_KEY_PLACEHOLDERS:
            api_key = ""

        self._api_key_cache = {"mtime": mtime, "value": api_key}
        return api_key
