"""
Vendor path bootstrap and OpenRouter SDK setup.
"""
import logging
import os
import sys
import configparser
import tempfile
from pathlib import Path

from ._shared import _is_truthy, _LOG_FORMAT

# Global flag for logger configuration
_openrouter_sdk_logger_configured = False


def _ensure_openrouter_python_sdk():
    """
    Ensure the OpenRouter SDK is available in the Python environment.
    """
    try:
        import openrouter  # noqa: F401
        return
    except ImportError:
        logger = logging.getLogger(__name__)
        logger.error("OpenRouter SDK not found in the active virtual environment.")
        raise RuntimeError(
            "Missing dependencies. Make sure requirements-vendor.txt has been successfully "
            "installed in the virtual environment."
        )


def _expand_env_vars(value: str) -> str:
    """Expand standard environment variables and standard directory placeholders."""
    value = (value or "").strip()
    temp_dir = tempfile.gettempdir()
    value = value.replace("%TEMP%", temp_dir).replace("%TMP%", temp_dir)
    return os.path.expandvars(value)


def _resolve_openrouter_log_dir(script_dir: Path) -> Path:
    """Use the configured runtime log directory, falling back to the local logs folder."""
    config_path = script_dir / "config" / "settings.ini"
    configured = ""

    try:
        parser = configparser.ConfigParser()
        parser.read(config_path, encoding="utf-8-sig")
        configured = parser.get("Logging", "LogDirectory", fallback="")
        if not configured:
            configured = parser.get("Logging", "LogDir", fallback="")
    except Exception:
        configured = ""

    if configured:
        candidate = Path(_expand_env_vars(configured))
        if not candidate.is_absolute():
            candidate = script_dir / candidate
        return candidate

    return script_dir / "logs"


def _configure_openrouter_sdk_logger(script_dir: Path):
    """Configure the OpenRouter SDK logger with file and optional console output."""
    global _openrouter_sdk_logger_configured
    if _openrouter_sdk_logger_configured:
        return

    debug_env = _is_truthy(os.getenv("OPENROUTER_DEBUG", ""))

    try:
        from logging.handlers import RotatingFileHandler
        
        log_dir = _resolve_openrouter_log_dir(script_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        
        # Use RotatingFileHandler with 5MB max size and 3 backups
        fh = RotatingFileHandler(
            log_dir / "openrouter_sdk.log",
            maxBytes=5 * 1024 * 1024,  # 5MB
            backupCount=3,
            encoding="utf-8"
        )
        fh.setLevel(logging.DEBUG if debug_env else logging.INFO)
        fh.setFormatter(logging.Formatter(_LOG_FORMAT))
        logging.getLogger("openrouter").addHandler(fh)
        logging.getLogger("openrouter").setLevel(
            logging.DEBUG if debug_env else logging.INFO
        )

        if debug_env:
            sh = logging.StreamHandler()
            sh.setLevel(logging.DEBUG)
            sh.setFormatter(logging.Formatter(_LOG_FORMAT))
            logging.getLogger("openrouter").addHandler(sh)
    except Exception:
        # Best-effort only.
        pass

    _openrouter_sdk_logger_configured = True
