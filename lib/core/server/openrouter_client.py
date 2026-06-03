from ._openrouter_client_core import (
    _OpenRouterClientCore,
    BASE_TIMEOUT_MS,
    MAX_LLM_CALL_ATTEMPTS,
    STREAM_IDLE_TIMEOUT_SEC,
)


class OpenRouterClientMixin(_OpenRouterClientCore):
    """Backward-compatible mixin facade for OpenRouter client methods."""

    pass
