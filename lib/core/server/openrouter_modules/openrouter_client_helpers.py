"""
OpenRouter client helper mixin for transport construction.
"""

from __future__ import annotations
import configparser
import logging
import time
import urllib.error
import urllib.request
import threading
import httpx
from .._shared import json, logger
from ..debug_log import DebugLog
from .openrouter_error_decoder import decode_error_response
from .openrouter_client_config import _ensure_openrouter_python_sdk


class RawOpenRouterHTTPError(Exception):
    """HTTP error from the raw OpenRouter REST fallback."""

    def __init__(self, status_code: int, message: str, response_data=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data


class OpenRouterClientHelpersMixin:
    """Shared SDK creation helper methods for OpenRouter calls."""

    _httpx_client = None
    _httpx_lock = threading.Lock()
    _sdk_client_cache = {}
    _sdk_client_lock = threading.Lock()

    _REASONING_MIN_COMPLETION_TOKENS = 128
    _REASONING_CAPABLE_MIN_COMPLETION_TOKENS = 64
    _ANTHROPIC_REASONING_MIN_COMPLETION_TOKENS = 1536
    _REASONING_MIN_COMPLETION_BY_EFFORT = {
        "none": 16,
        "minimal": 128,
        "low": 256,
        "medium": 512,
        "high": 1024,
        "xhigh": 2048,
        "max": 4096,
    }

    _SDK_CHAT_KEYS = {
        "messages",
        "http_referer",
        "x_open_router_title",
        "x_open_router_categories",
        "cache_control",
        "debug",
        "frequency_penalty",
        "image_config",
        "logit_bias",
        "logprobs",
        "max_completion_tokens",
        "max_tokens",
        "metadata",
        "modalities",
        "model",
        "models",
        "parallel_tool_calls",
        "plugins",
        "presence_penalty",
        "provider",
        "reasoning",
        "response_format",
        "seed",
        "service_tier",
        "session_id",
        "stop",
        "stream",
        "stream_options",
        "temperature",
        "tool_choice",
        "tools",
        "top_logprobs",
        "top_p",
        "trace",
        "user",
        "retries",
        "server_url",
        "timeout_ms",
        "http_headers",
    }

    _MODEL_PARAMETER_KEYS = {
        "frequency_penalty",
        "logit_bias",
        "logprobs",
        "max_completion_tokens",
        "max_tokens",
        "presence_penalty",
        "reasoning",
        "response_format",
        "seed",
        "stop",
        "temperature",
        "tool_choice",
        "tools",
        "top_logprobs",
        "top_p",
    }

    _RAW_ONLY_CHAT_KEYS = {
        "top_k",
        "top_a",
        "min_p",
        "verbosity",
        "include_reasoning",
        "structured_outputs",
        "repetition_penalty",
        "web_search_options",
        "x_search_filter",
        "reasoning_effort",
    }

    _RAW_CHAT_KEYS = _SDK_CHAT_KEYS | _RAW_ONLY_CHAT_KEYS

    _MODEL_PARAMETER_KEYS = _MODEL_PARAMETER_KEYS | _RAW_ONLY_CHAT_KEYS | {
        "parallel_tool_calls",
    }

    _PARAMETERS_THAT_SHOULD_BE_REQUIRED = {
        "logprobs",
        "top_logprobs",
        "parallel_tool_calls",
        "reasoning",
        "response_format",
        "structured_outputs",
        "tool_choice",
        "tools",
        "verbosity",
        "web_search_options",
    }

    _TRANSPORT_ONLY_KEYS = {
        "http_referer",
        "x_open_router_title",
        "x_open_router_categories",
        "retries",
        "server_url",
        "timeout_ms",
        "http_headers",
    }

    _RAW_RETRY_STATUS_CODES = {408, 429, 500, 502, 503, 524, 529}

    def _sanitize_chat_kwargs(self, req: dict):
        """Normalize a chat request to the current model and installed SDK surface."""
        if not isinstance(req, dict):
            return req

        cleaned = dict(req)

        self._strip_empty_optional_chat_values(cleaned)

        model_data = None
        supported = None
        requested_model = str(cleaned.get("model") or "").strip()
        if requested_model:
            model_data = self._get_openrouter_model_metadata(requested_model)
            if model_data:
                canonical_id = str(model_data.get("id") or "").strip()
                if canonical_id:
                    cleaned["model"] = canonical_id
                supported = set(model_data.get("supported_parameters") or [])

        self._normalize_chat_token_fields(cleaned, supported)
        self._normalize_chat_reasoning(cleaned, supported)
        self._ensure_reasoning_disabled_by_default(cleaned, supported)
        self._ensure_reasoning_capable_token_floor(cleaned, supported)
        self._ensure_reasoning_token_floor(cleaned, supported)
        self._filter_by_supported_model_parameters(cleaned, supported)
        self._apply_prompt_caching_defaults(cleaned, supported)
        self._apply_provider_policy_defaults(cleaned)
        self._ensure_required_provider_parameters(cleaned)
        self._strip_empty_optional_chat_values(cleaned)
        self._strip_unsupported_transport_chat_keys(cleaned)
        return cleaned

    def _get_openrouter_model_metadata(self, model_id: str):
        """Return current OpenRouter metadata for a model id or latest alias."""
        model_id = str(model_id or "").strip()
        if not model_id:
            return None

        try:
            api_key = self.get_api_key()
            if not api_key:
                return None
            models_data, error = self._get_models_cached(api_key)
            if error or not isinstance(models_data, dict):
                return None
        except Exception:
            return None

        resolved_id = self._resolve_openrouter_model_id(model_id, models_data)
        for model in (models_data or {}).get("data", []):
            if isinstance(model, dict) and model.get("id") == resolved_id:
                return model
        return None

    def _strip_empty_optional_chat_values(self, cleaned: dict):
        """Remove optional fields whose empty defaults create provider edge cases."""
        for key in (
            "tools",
            "plugins",
            "models",
            "modalities",
            "stop",
            "metadata",
            "image_config",
            "debug",
            "trace",
            "provider",
            "stream_options",
            "response_format",
        ):
            if key not in cleaned:
                continue
            value = cleaned.get(key)
            if value is None or value == "" or value == [] or value == {}:
                cleaned.pop(key, None)

        if not cleaned.get("tools"):
            cleaned.pop("tool_choice", None)
            cleaned.pop("parallel_tool_calls", None)

    def _normalize_chat_token_fields(self, cleaned: dict, supported: set | None):
        """Use the token limit field advertised by the selected model."""
        has_completion = "max_completion_tokens" in cleaned
        has_legacy = "max_tokens" in cleaned
        if not has_completion and not has_legacy:
            return

        token_value = cleaned.get("max_completion_tokens") if has_completion else cleaned.get("max_tokens")
        if token_value in (None, ""):
            cleaned.pop("max_completion_tokens", None)
            cleaned.pop("max_tokens", None)
            return

        if supported:
            if "max_completion_tokens" in supported:
                cleaned["max_completion_tokens"] = token_value
                cleaned.pop("max_tokens", None)
            elif "max_tokens" in supported:
                cleaned["max_tokens"] = token_value
                cleaned.pop("max_completion_tokens", None)
            else:
                cleaned.pop("max_completion_tokens", None)
                cleaned.pop("max_tokens", None)
            return

        if has_legacy and not has_completion:
            cleaned["max_completion_tokens"] = cleaned.pop("max_tokens")

    def _normalize_chat_reasoning(self, cleaned: dict, supported: set | None):
        """Keep only reasoning fields supported by the installed SDK."""
        model_id = cleaned.get("model")
        legacy_include = cleaned.pop("include_reasoning", None)
        flat_effort = str(cleaned.pop("reasoning_effort", "") or "").strip().lower()
        if "reasoning" not in cleaned and legacy_include is not None:
            if legacy_include is False:
                cleaned["reasoning"] = {"enabled": True, "exclude": True}
            elif legacy_include:
                cleaned["reasoning"] = {"enabled": True}
        if "reasoning" not in cleaned and flat_effort:
            cleaned["reasoning"] = {"effort": flat_effort}

        if "reasoning" not in cleaned:
            return

        if supported is not None:
            if "reasoning" not in supported:
                cleaned.pop("reasoning", None)
                return
        else:
            # Hardened fallback when model catalog is not loaded / model is unrecognized
            if not self._model_supports_reasoning(model_id):
                cleaned.pop("reasoning", None)
                return

        reasoning = cleaned.get("reasoning")
        if reasoning in (None, "", False):
            cleaned.pop("reasoning", None)
            return

        if isinstance(reasoning, dict):
            enabled = bool(reasoning.get("enabled", True))
            if not enabled:
                # Omit reasoning parameter entirely when disabled to prevent "none" effort blunting
                cleaned.pop("reasoning", None)
                return

            normalized = {}
            max_tokens = reasoning.get("max_tokens")
            if max_tokens not in (None, ""):
                try:
                    max_tokens_int = int(max_tokens)
                    if max_tokens_int > 0:
                        normalized["max_tokens"] = max_tokens_int
                except Exception:
                    pass

            effort = str(reasoning.get("effort") or "").strip().lower()
            if effort:
                normalized["effort"] = effort
            if "exclude" in reasoning:
                normalized["exclude"] = bool(reasoning.get("exclude"))
            summary = reasoning.get("summary")
            if summary not in (None, ""):
                normalized["summary"] = summary

            # Normalize defaults if neither parameter is present
            if "max_tokens" not in normalized and "effort" not in normalized:
                normalized["effort"] = "medium"

            # Determine provider capabilities and apply target parameter normalization to prevent API conflicts
            is_max_tokens_provider = self._is_max_tokens_provider(model_id)
            is_effort_provider = self._is_effort_provider(model_id)

            if is_max_tokens_provider:
                # Gemini/Anthropic strictly require max_tokens (integer), effort is invalid
                effort = normalized.pop("effort", None)
                if "max_tokens" not in normalized and effort:
                    mapping = {
                        "minimal": 512,
                        "low": 1024,
                        "medium": 2048,
                        "high": 4096,
                        "xhigh": 8192,
                        "max": 16384
                    }
                    normalized["max_tokens"] = mapping.get(effort, 2048)
            elif is_effort_provider:
                # OpenAI/Grok strictly require effort, max_tokens is invalid
                max_tokens_val = normalized.pop("max_tokens", None)
                if "effort" not in normalized and max_tokens_val is not None:
                    try:
                        tokens = int(max_tokens_val)
                        if tokens < 1000:
                            normalized["effort"] = "low"
                        elif tokens <= 3000:
                            normalized["effort"] = "medium"
                        else:
                            normalized["effort"] = "high"
                    except Exception:
                        normalized["effort"] = "medium"
            else:
                # Non-aligned fallback: if both parameters exist, resolve conflict by preferring max_tokens
                if "max_tokens" in normalized and "effort" in normalized:
                    normalized.pop("effort", None)

            cleaned["reasoning"] = normalized

    # ---------------------------------------------------------------
    # Reasoning policy table (verified against OpenRouter docs and the
    # live /api/v1/models registry as of May 2026):
    #
    #   - Gemini 3 Pro / 3.1 Pro Preview: reasoning MANDATORY. Only
    #     accepts low/medium/high. We send `{enabled: true, exclude: true}`
    #     which OpenRouter normalizes to medium effort and is documented
    #     as universally supported.
    #   - Gemini 3 Flash / 3.5 Flash / 3 Flash-Lite (and previews):
    #     reasoning is optional but they DO NOT accept `effort=none`;
    #     the lowest setting is `minimal`. Sending `effort=none` either
    #     400s or silently blunts the response. We send `effort=minimal`
    #     with `exclude=true` for fast, properly-grounded vision output.
    #   - Gemini 2.5 Pro / 2.0 Pro: reasoning mandatory (≥1 thinking
    #     token). Treat like Gemini 3 Pro.
    #   - Grok 4 (x-ai/grok-4): reasoning is ALWAYS ON and `reasoning_effort`
    #     is rejected. We must OMIT the reasoning field entirely.
    #   - Grok 4 Fast reasoning / Grok 4.20 / Grok 4.3: toggleable via
    #     `enabled`. Default off via `effort=none` works for these.
    #   - Anthropic *-thinking, *-reasoner, *-reasoning, *-pro reasoners:
    #     reasoning mandatory. Send `{enabled: true, exclude: true}`.
    #   - Everything else with `reasoning` in supported_parameters:
    #     reasoning is opt-in and `effort=none` is the safe default.
    # ---------------------------------------------------------------

    # Substrings that mark a model whose reasoning CANNOT be disabled and
    # which accepts `enabled: true` (universally supported per docs).
    _MANDATORY_REASONING_SUBSTRINGS = (
        "gemini-3-pro",          # future google/gemini-3-pro
        "gemini-3.1-pro",        # google/gemini-3.1-pro-preview[-customtools]
        "gemini-3.5-pro",
        "gemini-2.5-pro",        # min thinking budget 128; cannot turn off
        "-thinking",             # anthropic/claude-*-thinking, gpt-5-thinking
        "-reasoner",             # deepseek-reasoner
        "-reasoning",            # generic *-reasoning suffix
        "o1-pro",
        "o3-pro",
        "gpt-5-pro",
        "gpt-5.4-pro",
        "gpt-5.5-pro",
    )

    # Grok 4 (the original) refuses ANY reasoning parameter. Other Grok 4.x
    # variants accept `enabled`. We match the bare `x-ai/grok-4` slug only.
    _GROK_NO_REASONING_PARAM_SUBSTRINGS = (
        "x-ai/grok-4",   # exact match guarded below; also matches grok-4-0709 alias
    )

    # Gemini 3.x Flash family: optional reasoning, lowest setting is
    # `minimal`, `none` is rejected/ignored. (Per ai.google.dev/gemini-api
    # docs and confirmed via Vercel AI Gateway report.)
    _GEMINI_FLASH_NEEDS_MINIMAL_SUBSTRINGS = (
        "gemini-3-flash",
        "gemini-3.1-flash",
        "gemini-3.5-flash",
    )

    @staticmethod
    def _model_id_lower(model_id) -> str:
        if not model_id:
            return ""
        try:
            return str(model_id).strip().lower()
        except Exception:
            return ""

    def _model_requires_reasoning(self, model_id) -> bool:
        m = self._model_id_lower(model_id)
        if not m:
            return False
        return any(tok in m for tok in self._MANDATORY_REASONING_SUBSTRINGS)

    def _model_rejects_reasoning_param(self, model_id) -> bool:
        """True for models that 400 when ANY reasoning param is sent (e.g. bare Grok 4)."""
        m = self._model_id_lower(model_id)
        if not m:
            return False
        # Match `x-ai/grok-4` and its dated alias `x-ai/grok-4-0709`, but
        # NOT `x-ai/grok-4-fast-*`, `x-ai/grok-4.20`, `x-ai/grok-4.3`, etc.
        if m == "x-ai/grok-4" or m.startswith("x-ai/grok-4-0"):
            return True
        return False

    def _model_needs_minimal_reasoning(self, model_id) -> bool:
        m = self._model_id_lower(model_id)
        if not m:
            return False
        return any(tok in m for tok in self._GEMINI_FLASH_NEEDS_MINIMAL_SUBSTRINGS)

    def _model_supports_reasoning(self, model_id) -> bool:
        """True if the model has reasoning capability based on standard keywords/naming."""
        m = self._model_id_lower(model_id)
        if not m:
            return False
        if self._model_requires_reasoning(model_id) or self._model_needs_minimal_reasoning(model_id):
            return True
        reasoning_keywords = (
            "o1", "o3", "o4", "thinking", "reasoner", "reasoning", 
            "gemini-3", "gemini-2.5", "gpt-5", "claude-3.7", "claude-3.8", 
            "claude-3.9", "claude-4", "deepseek-r1", "qwen-2.5-72b-instruct", "r1"
        )
        return any(tok in m for tok in reasoning_keywords)

    def _is_max_tokens_provider(self, model_id: str) -> bool:
        m = self._model_id_lower(model_id)
        if not m:
            return False
        return any(x in m for x in ("google/", "gemini", "alibaba/", "qwen"))

    def _is_effort_provider(self, model_id: str) -> bool:
        m = self._model_id_lower(model_id)
        if not m:
            return False
        return any(x in m for x in ("openai/", "gpt-", "o1", "o3", "o4", "x-ai/", "grok", "anthropic/", "claude"))

    def _ensure_reasoning_disabled_by_default(self, cleaned: dict, supported: set | None):
        """Set per-model-family default reasoning posture for screenshot/chat calls.

        Behavior summary:
          - bare Grok 4         -> remove any reasoning field (provider rejects it)
          - mandatory-reasoning -> `{enabled: true, exclude: true}` (universal)
          - Gemini 3.x Flash    -> `{effort: minimal, exclude: true}` (lowest valid)
          - everything else     -> pop parameter entirely (safely off-by-default)
        """
        model_id = cleaned.get("model")

        # Hard rule: bare Grok 4 refuses ALL reasoning params. Strip it.
        if self._model_rejects_reasoning_param(model_id):
            cleaned.pop("reasoning", None)
            return

        # Respect an explicit caller-supplied reasoning config.
        if "reasoning" in cleaned:
            return

        if supported is not None:
            if "reasoning" not in supported:
                return
        else:
            if not self._model_supports_reasoning(model_id):
                return

        if self._model_requires_reasoning(model_id):
            # Set target parameter (max_tokens or effort) depending on the provider, and exclude reasoning details.
            # Avoid passing "enabled": True which causes 400 errors on Anthropic.
            if self._is_max_tokens_provider(model_id):
                cleaned["reasoning"] = {"max_tokens": 2048, "exclude": True}
            else:
                cleaned["reasoning"] = {"effort": "medium", "exclude": True}
            return

        if self._model_needs_minimal_reasoning(model_id):
            if self._is_max_tokens_provider(model_id):
                cleaned["reasoning"] = {"max_tokens": 512, "exclude": True}
            else:
                cleaned["reasoning"] = {"effort": "minimal", "exclude": True}
            return

        # Pop the reasoning parameter entirely when disabled to avoid blunting or 400 errors from "none" effort
        cleaned.pop("reasoning", None)

    def _ensure_reasoning_token_floor(self, cleaned: dict, supported: set | None):
        """Avoid empty answers when reasoning consumes a tiny completion budget."""
        if "reasoning" not in cleaned:
            return
        reasoning = cleaned.get("reasoning") or {}
        if isinstance(reasoning, dict) and str(reasoning.get("effort") or "").lower() == "none":
            return

        preferred_key = "max_completion_tokens"
        if supported and "max_completion_tokens" not in supported and "max_tokens" in supported:
            preferred_key = "max_tokens"

        token_key = None
        for key in ("max_completion_tokens", "max_tokens"):
            if key in cleaned:
                token_key = key
                break

        min_tokens = self._reasoning_completion_floor(cleaned)

        if not token_key:
            cleaned[preferred_key] = min_tokens
            return

        try:
            current = int(cleaned.get(token_key))
        except Exception:
            return

        if 0 < current < min_tokens:
            cleaned[token_key] = min_tokens

    def _ensure_reasoning_capable_token_floor(self, cleaned: dict, supported: set | None):
        """Avoid blank text when reasoning-capable models spend tiny caps on hidden thinking."""
        if "reasoning" in cleaned:
            return
        if not supported or "reasoning" not in supported:
            return

        token_key = None
        for key in ("max_completion_tokens", "max_tokens"):
            if key in cleaned:
                token_key = key
                break
        if not token_key:
            return

        try:
            current = int(cleaned.get(token_key))
        except Exception:
            return

        if 0 < current < self._REASONING_CAPABLE_MIN_COMPLETION_TOKENS:
            cleaned[token_key] = self._REASONING_CAPABLE_MIN_COMPLETION_TOKENS

    def _reasoning_completion_floor(self, cleaned: dict) -> int:
        """Return a completion cap that leaves room after reasoning budget use."""
        reasoning = cleaned.get("reasoning") or {}
        if not isinstance(reasoning, dict):
            return self._REASONING_MIN_COMPLETION_TOKENS

        model_id = str(cleaned.get("model") or "").lower()
        effort = str(reasoning.get("effort") or "medium").strip().lower()
        min_tokens = self._REASONING_MIN_COMPLETION_BY_EFFORT.get(
            effort,
            self._REASONING_MIN_COMPLETION_TOKENS,
        )

        requested_reasoning_tokens = reasoning.get("max_tokens")
        if requested_reasoning_tokens not in (None, ""):
            try:
                min_tokens = max(min_tokens, int(requested_reasoning_tokens) + 128)
            except Exception:
                pass

        if model_id.startswith("anthropic/"):
            min_tokens = max(min_tokens, self._ANTHROPIC_REASONING_MIN_COMPLETION_TOKENS)
            if effort == "xhigh":
                min_tokens = max(min_tokens, 4096)

        return max(self._REASONING_MIN_COMPLETION_TOKENS, int(min_tokens))

    def _filter_by_supported_model_parameters(self, cleaned: dict, supported: set | None):
        """Drop model-specific params not listed for the selected model."""
        if not supported:
            return

        for key in list(cleaned.keys()):
            if key in self._MODEL_PARAMETER_KEYS and key not in supported:
                cleaned.pop(key, None)

    def _ensure_required_provider_parameters(self, cleaned: dict):
        """Ask OpenRouter to route only to providers that honor strict parameters."""
        needs_required = any(
            key in cleaned and cleaned.get(key) not in (None, "", [], {})
            for key in self._PARAMETERS_THAT_SHOULD_BE_REQUIRED
        )
        if not needs_required:
            return

        provider = cleaned.get("provider")
        if provider is None:
            provider = {}
        if not isinstance(provider, dict):
            return
        provider.setdefault("require_parameters", True)
        cleaned["provider"] = provider

    # ---------------------------------------------------------------
    # Prompt caching policy. Verified against OpenRouter docs (May 2026):
    #   - Anthropic models support top-level `cache_control: {type: ephemeral}`
    #     which automatically caches everything up to the last user message.
    #     Cache reads cost ~0.1x the original input price.
    #   - Gemini 2.5 Pro / Flash / 3.x support implicit caching with no
    #     setup, AND explicit per-block cache_control breakpoints. Implicit
    #     caching kicks in automatically once messages are repeated; no
    #     action needed from us. We DO NOT add explicit breakpoints to
    #     Gemini requests because explicit caching has a 5-minute write TTL
    #     and a per-write fee; implicit is the safer default for screenshot
    #     workflows where the same system prompt is reused frequently.
    #   - OpenAI / DeepSeek also do implicit caching automatically.
    # ---------------------------------------------------------------

    # Minimum estimated system-prompt tokens before caching is worth enabling.
    # Anthropic charges a one-time write cost (~1.25x input price), so caching
    # only pays back after ~2 cache hits. Threshold matches the documented
    # Anthropic ephemeral cache minimum (1024 tokens for Sonnet/Opus).
    _CACHE_MIN_SYSTEM_PROMPT_CHARS = 4000  # ~1000 tokens at 4 chars/token

    _ANTHROPIC_CACHE_MODEL_PREFIXES = (
        "anthropic/claude-opus-4",
        "anthropic/claude-sonnet-4",
        "anthropic/claude-haiku-4",
        "anthropic/claude-opus-3",
        "anthropic/claude-sonnet-3.7",
        "anthropic/claude-3.5",
    )

    def _model_supports_anthropic_caching(self, model_id) -> bool:
        if not model_id:
            return False
        m = self._model_id_lower(model_id)
        return any(m.startswith(p) for p in self._ANTHROPIC_CACHE_MODEL_PREFIXES)

    @staticmethod
    def _estimate_system_prompt_chars(messages) -> int:
        """Sum the character lengths of all system-role message contents."""
        if not isinstance(messages, list):
            return 0
        total = 0
        for msg in messages:
            if not isinstance(msg, dict) or msg.get("role") != "system":
                continue
            content = msg.get("content")
            if isinstance(content, str):
                total += len(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        total += len(part["text"])
        return total

    def _apply_prompt_caching_defaults(self, cleaned: dict, supported):
        """Enable Anthropic ephemeral caching when the system prompt is large.

        Skip if the caller already set `cache_control` (respect explicit choice).
        Skip for non-Anthropic models \u2014 they cache implicitly.
        """
        if not isinstance(cleaned, dict):
            return
        if "cache_control" in cleaned:
            return
        model_id = cleaned.get("model")
        if not self._model_supports_anthropic_caching(model_id):
            return
        sys_chars = self._estimate_system_prompt_chars(cleaned.get("messages"))
        if sys_chars < self._CACHE_MIN_SYSTEM_PROMPT_CHARS:
            return
        cleaned["cache_control"] = {"type": "ephemeral"}

    def _apply_provider_policy_defaults(self, cleaned: dict):
        """Apply conservative provider routing policy unless a request overrides it."""
        policy = self._get_openrouter_provider_policy()
        if not policy:
            return

        provider = cleaned.get("provider")
        if provider is None:
            provider = {}
        if not isinstance(provider, dict):
            return
        for key, value in policy.items():
            provider.setdefault(key, value)
        cleaned["provider"] = provider

    def _get_openrouter_provider_policy(self) -> dict:
        """Read privacy/routing defaults from config.

        Defaults shipped in this build:
          - data_collection = "deny"  : refuse providers that train on data.
                                        Safe default; still permits a wide pool.
          - zdr             = False   : do NOT restrict to Zero Data Retention
                                        providers by default. The previous default
                                        of True caused widespread 503 \"no
                                        available provider meets routing
                                        requirements\" and 429 \"too many
                                        requests\" failures because only a single
                                        ZDR-certified provider per model was
                                        eligible and could rate-limit easily.
                                        Set [OpenRouter] ProviderZDR=true ONLY
                                        when shipping PHI; expect reduced
                                        availability when you do.
          - allow_fallbacks = True    : allow OpenRouter to fall back to another
                                        compliant provider when the preferred
                                        one is down/throttled. Set to false to
                                        hard-pin the primary provider.
          - sort            = unset   : let OpenRouter pick best provider
        """
        policy = {
            "data_collection": "deny",
            "zdr": False,
            "allow_fallbacks": True,
        }
        script_dir = getattr(self, "script_dir", None)
        if not script_dir:
            return policy

        config_path = script_dir / "config" / "settings.ini"
        parser = configparser.ConfigParser()
        try:
            parser.read(config_path, encoding="utf-8-sig")
        except Exception:
            return policy

        data_collection = parser.get(
            "OpenRouter",
            "ProviderDataCollection",
            fallback=policy["data_collection"],
        ).strip().lower()
        if data_collection in ("allow", "deny"):
            policy["data_collection"] = data_collection

        zdr = parser.get("OpenRouter", "ProviderZDR", fallback="false").strip().lower()
        policy["zdr"] = zdr in ("1", "true", "yes", "on")

        allow_fb = parser.get("OpenRouter", "AllowFallbacks", fallback="true").strip().lower()
        policy["allow_fallbacks"] = allow_fb in ("1", "true", "yes", "on")

        sort = parser.get("OpenRouter", "ProviderSort", fallback="").strip().lower()
        if sort in ("price", "throughput", "latency"):
            policy["sort"] = sort

        return policy

    def _requires_raw_openrouter_transport(self, cleaned: dict) -> bool:
        """Return true when the request uses documented params missing from SDK 0.9.1."""
        if not isinstance(cleaned, dict):
            return False
        if any(key in cleaned for key in self._RAW_ONLY_CHAT_KEYS):
            return True
        reasoning = cleaned.get("reasoning")
        if isinstance(reasoning, dict) and any(
            key in reasoning for key in ("max_tokens", "exclude", "enabled")
        ):
            return True
        return False

    def _strip_unsupported_transport_chat_keys(self, cleaned: dict):
        """Remove kwargs unsupported by either the SDK or raw OpenRouter transport."""
        allowed = self._RAW_CHAT_KEYS if self._requires_raw_openrouter_transport(cleaned) else self._SDK_CHAT_KEYS
        for key in list(cleaned.keys()):
            if key not in allowed:
                cleaned.pop(key, None)

    def _raw_openrouter_headers(self, api_key: str, extra_headers=None):
        """Build REST headers equivalent to the SDK's app attribution headers."""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/impressionistllm",
            "X-OpenRouter-Title": "ImpressionistLLM",
        }
        if isinstance(extra_headers, dict):
            for key, value in extra_headers.items():
                if value not in (None, ""):
                    headers[str(key)] = str(value)
        return headers

    def _raw_openrouter_payload_and_headers(self, api_key: str, req: dict):
        """Split SDK-style transport kwargs from the REST request body."""
        payload = dict(req or {})
        extra_headers = payload.pop("http_headers", None)
        if payload.get("http_referer"):
            extra_headers = dict(extra_headers or {})
            extra_headers["HTTP-Referer"] = payload.get("http_referer")
        if payload.get("x_open_router_title"):
            extra_headers = dict(extra_headers or {})
            extra_headers["X-OpenRouter-Title"] = payload.get("x_open_router_title")
        if payload.get("x_open_router_categories"):
            extra_headers = dict(extra_headers or {})
            extra_headers["X-OpenRouter-Categories"] = payload.get("x_open_router_categories")
        for key in self._TRANSPORT_ONLY_KEYS:
            payload.pop(key, None)
        return payload, self._raw_openrouter_headers(api_key, extra_headers)

    def _raise_raw_openrouter_error(self, exc: urllib.error.HTTPError):
        """Normalize raw HTTP errors to the same shape as SDK errors AND emit a
        structured DebugLog event with full decoded provider/guardrail context."""
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        headers = {}
        try:
            if getattr(exc, "headers", None):
                headers = dict(exc.headers.items())
        except Exception:
            headers = {}

        decoded = decode_error_response(
            http_status=exc.code,
            raw_body=raw,
            headers=headers,
            exception=exc,
        )
        try:
            DebugLog.error("openrouter.error", decoded)
        except Exception:
            pass

        response_data = None
        message = decoded.get("error_message") or raw or str(exc)
        if raw:
            try:
                response_data = json.loads(raw)
            except Exception:
                response_data = None
        raise RawOpenRouterHTTPError(exc.code, message, response_data) from exc

    def _raise_httpx_openrouter_error(self, exc: httpx.HTTPStatusError):
        """Decode and raise structured OpenRouter error from httpx response."""
        resp = exc.response
        raw = resp.text
        headers = dict(resp.headers.items())

        decoded = decode_error_response(
            http_status=resp.status_code,
            raw_body=raw,
            headers=headers,
            exception=exc,
        )
        try:
            DebugLog.error("openrouter.error", decoded)
        except Exception:
            pass

        response_data = None
        message = decoded.get("error_message") or raw or str(exc)
        if raw:
            try:
                response_data = json.loads(raw)
            except Exception:
                response_data = None
        raise RawOpenRouterHTTPError(resp.status_code, message, response_data) from exc

    def _raw_retry_delay(self, attempt: int, exc=None) -> float:
        """Small bounded backoff for transient raw REST transport failures."""
        retry_after = ""
        try:
            headers = getattr(exc, "headers", None)
            if headers is None and hasattr(exc, "response"):
                headers = getattr(exc.response, "headers", None)
            retry_after = (headers or {}).get("Retry-After", "")
        except Exception:
            retry_after = ""
        try:
            parsed = float(retry_after)
            if parsed > 0:
                return min(parsed, 5.0)
        except Exception:
            pass
        return min(0.5 * (2**attempt), 3.0)

    def _raw_openrouter_post(self, payload: dict, headers: dict, timeout_ms: int):
        """POST to OpenRouter with retry parity for REST-only parameters.

        Honors Retry-After on 429/503 and emits DebugLog events for each
        request/response/retry attempt. Uses shared httpx.Client for Keep-Alive connection pooling.
        """
        if OpenRouterClientHelpersMixin._httpx_client is None:
            with OpenRouterClientHelpersMixin._httpx_lock:
                if OpenRouterClientHelpersMixin._httpx_client is None:
                    OpenRouterClientHelpersMixin._httpx_client = httpx.Client(
                        timeout=30.0,
                        limits=httpx.Limits(
                            max_keepalive_connections=20,
                            max_connections=50,
                            keepalive_expiry=30.0,
                        ),
                    )

        client = OpenRouterClientHelpersMixin._httpx_client
        attempts = 3
        for attempt in range(attempts):
            try:
                DebugLog.event("openrouter.request", {
                    "transport": "raw",
                    "attempt": attempt + 1,
                    "model": payload.get("model"),
                    "stream": bool(payload.get("stream")),
                    "messages_count": len(payload.get("messages") or []),
                    "timeout_ms": int(timeout_ms),
                })
            except Exception:
                pass

            try:
                read_timeout = max(1.0, timeout_ms / 1000.0)
                resp = client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=httpx.Timeout(read_timeout, connect=30.0),
                )
                resp.raise_for_status()
                resp_text = resp.text
                try:
                    DebugLog.event("openrouter.response", {
                        "transport": "raw",
                        "http_status": resp.status_code,
                        "bytes": len(resp_text),
                        "model": payload.get("model"),
                    })
                except Exception:
                    pass
                return json.loads(resp_text)

            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in self._RAW_RETRY_STATUS_CODES or attempt >= attempts - 1:
                    self._raise_httpx_openrouter_error(exc)
                delay = self._raw_retry_delay(attempt, exc)
                try:
                    DebugLog.event("openrouter.retry", {
                        "transport": "raw",
                        "http_status": exc.response.status_code,
                        "attempt": attempt + 1,
                        "delay_seconds": delay,
                        "model": payload.get("model"),
                    }, level="warning")
                except Exception:
                    pass
                time.sleep(delay)
            except httpx.RequestError as exc:
                if attempt >= attempts - 1:
                    try:
                        DebugLog.exception("openrouter.transport_error", exc,
                                           {"transport": "raw", "model": payload.get("model")})
                    except Exception:
                        pass
                    raise
                time.sleep(self._raw_retry_delay(attempt))
        return {}

    def _send_openrouter_chat(self, api_key: str, req: dict, timeout_ms: int):
        """Send chat via SDK unless current docs require REST-only parameters.

        Includes two safety-net retries (each at most once per request) for
        provider-reported 400s that our static model heuristics missed:

          1. "Reasoning is mandatory ..." -> flip reasoning ON via
             `{enabled: true, exclude: true}` and retry.
          2. "Reasoning cannot be enabled" / "reasoning effort cannot be
             specified" / "unknown parameter: reasoning" -> strip the
             reasoning field entirely and retry.
        """
        try:
            return self._dispatch_openrouter_chat(api_key, req, timeout_ms)
        except Exception as exc:
            if not isinstance(req, dict):
                raise
            err_text = self._extract_exception_text(exc)

            if self._is_mandatory_reasoning_error(err_text):
                logger.warning(
                    "OpenRouter reports reasoning is mandatory for %s \u2014 "
                    "retrying with reasoning enabled (exclude=true)",
                    req.get("model"),
                )
                if self._is_max_tokens_provider(req.get("model")):
                    req["reasoning"] = {"max_tokens": 2048, "exclude": True}
                else:
                    req["reasoning"] = {"effort": "medium", "exclude": True}
                return self._dispatch_openrouter_chat(api_key, req, timeout_ms)

            if self._is_reasoning_param_rejected_error(err_text):
                logger.warning(
                    "OpenRouter reports reasoning param is not accepted for %s \u2014 "
                    "retrying with reasoning field removed",
                    req.get("model"),
                )
                req.pop("reasoning", None)
                return self._dispatch_openrouter_chat(api_key, req, timeout_ms)

            raise

    def _dispatch_openrouter_chat(self, api_key: str, req: dict, timeout_ms: int):
        if not self._requires_raw_openrouter_transport(req):
            try:
                DebugLog.event("openrouter.request", {
                    "transport": "sdk",
                    "model": req.get("model"),
                    "stream": bool(req.get("stream")),
                    "messages_count": len(req.get("messages") or []),
                    "timeout_ms": int(timeout_ms),
                    "provider": req.get("provider"),
                })
            except Exception:
                pass
            try:
                with self._openrouter_sdk(api_key, timeout_ms=timeout_ms) as open_router:
                    resp = open_router.chat.send(**req)
                try:
                    data = resp.model_dump() if hasattr(resp, "model_dump") else resp
                    DebugLog.event("openrouter.response", {
                        "transport": "sdk",
                        "model": req.get("model"),
                        "finish_reason": (((data or {}).get("choices") or [{}])[0] or {}).get("finish_reason"),
                        "has_content": bool((((data or {}).get("choices") or [{}])[0] or {}).get("message", {}).get("content")),
                    })
                except Exception:
                    pass
                return resp
            except Exception as exc:
                # Try to decode any embedded provider error.
                try:
                    body = getattr(exc, "response_data", None) or getattr(exc, "body", None)
                    raw = body if isinstance(body, str) else (json.dumps(body) if body else "")
                    decoded = decode_error_response(
                        http_status=getattr(exc, "status_code", None),
                        raw_body=raw,
                        exception=exc,
                    )
                    DebugLog.error("openrouter.error", decoded)
                except Exception:
                    pass
                raise

        payload, headers = self._raw_openrouter_payload_and_headers(api_key, req)
        return self._raw_openrouter_post(payload, headers, timeout_ms)

    @staticmethod
    def _extract_exception_text(exc) -> str:
        """Collect every readable string off an exception for substring matching."""
        text = ""
        try:
            text = str(exc) or ""
        except Exception:
            text = ""
        for attr in ("response_data", "data", "body", "message", "args"):
            try:
                val = getattr(exc, attr, None)
            except Exception:
                val = None
            if not val:
                continue
            try:
                if isinstance(val, str):
                    text += " " + val
                else:
                    text += " " + json.dumps(val, default=str)
            except Exception:
                pass
        return text.lower()

    @classmethod
    def _is_mandatory_reasoning_error(cls, err_text: str) -> bool:
        """Match the \"Reasoning is mandatory for this endpoint\" 400 family."""
        if not err_text:
            return False
        return (
            "reasoning is mandatory" in err_text
            or "reasoning cannot be disabled" in err_text
            or "thinking is required" in err_text
            or "thinking is mandatory" in err_text
            or "thinking cannot be disabled" in err_text
        )

    @classmethod
    def _is_reasoning_param_rejected_error(cls, err_text: str) -> bool:
        """Match providers that reject the reasoning field entirely (bare Grok 4 etc.)."""
        if not err_text:
            return False
        markers = (
            "reasoning effort cannot be specified",
            "reasoning_effort is not supported",
            "unknown parameter: reasoning",
            "unexpected parameter: reasoning",
            "reasoning is not supported",
            "does not support reasoning",
            "thinking effort cannot be specified",
            "thinking_effort is not supported",
            "unknown parameter: thinking",
            "unexpected parameter: thinking",
            "thinking is not supported",
            "does not support thinking",
        )
        return any(m in err_text for m in markers)

    def _stream_openrouter_chat_raw(self, api_key: str, req: dict, timeout_ms: int):
        """Yield OpenAI-compatible SSE chunks from the raw REST endpoint. Uses shared httpx.Client for Keep-Alive."""
        if OpenRouterClientHelpersMixin._httpx_client is None:
            with OpenRouterClientHelpersMixin._httpx_lock:
                if OpenRouterClientHelpersMixin._httpx_client is None:
                    OpenRouterClientHelpersMixin._httpx_client = httpx.Client(
                        timeout=30.0,
                        limits=httpx.Limits(
                            max_keepalive_connections=20,
                            max_connections=50,
                            keepalive_expiry=30.0,
                        ),
                    )

        client = OpenRouterClientHelpersMixin._httpx_client
        payload, headers = self._raw_openrouter_payload_and_headers(api_key, req)
        payload["stream"] = True

        try:
            read_timeout = max(1.0, timeout_ms / 1000.0)
            with client.stream(
                "POST",
                "https://openrouter.ai/api/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=httpx.Timeout(read_timeout, connect=30.0),
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        yield json.loads(data)
                    except Exception:
                        yield {"__text__": data}
        except httpx.HTTPStatusError as exc:
            self._raise_httpx_openrouter_error(exc)

    def _openrouter_sdk(self, api_key: str, timeout_ms: int):
        """Create and configure an OpenRouter SDK client, cached for Keep-Alive reuse."""
        _ensure_openrouter_python_sdk()
        from openrouter import OpenRouter  # type: ignore
        from openrouter.utils.retries import (  # type: ignore
            BackoffStrategy,
            RetryConfig,
        )

        cache_key = (api_key, timeout_ms)
        with OpenRouterClientHelpersMixin._sdk_client_lock:
            if cache_key not in OpenRouterClientHelpersMixin._sdk_client_cache:
                retry = RetryConfig(
                    strategy="backoff",
                    backoff=BackoffStrategy(
                        initial_interval=200,
                        max_interval=2000,
                        exponent=2.0,
                        max_elapsed_time=8000,
                    ),
                    retry_connection_errors=True,
                )
                client = OpenRouter(
                    api_key=api_key,
                    http_referer="https://github.com/impressionistllm",
                    x_open_router_title="ImpressionistLLM",
                    retry_config=retry,
                    timeout_ms=timeout_ms,
                    debug_logger=logging.getLogger("openrouter"),
                )
                OpenRouterClientHelpersMixin._sdk_client_cache[cache_key] = client
            client = OpenRouterClientHelpersMixin._sdk_client_cache[cache_key]

        class DummyContextOpenRouter:
            def __init__(self, client):
                self.client = client
            def __getattr__(self, name):
                return getattr(self.client, name)
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass

        return DummyContextOpenRouter(client)
