"""
Models cache and model-related API endpoints.
"""
import difflib
import threading
from time import time
from urllib.parse import parse_qs, unquote, urlparse

from ._shared import json, logger, _API_MODEL_DEFAULTS_PREFIX, _is_truthy


class ModelsMixin:
    """Mixin providing models cache and model-related API endpoints."""

    # Class variables for models cache (shared across all instances)
    _models_cache = None
    _models_cache_time = 0
    _models_cache_key = None
    _models_cache_ttl = 600  # seconds
    _models_lock = threading.Lock()

    def _invalidate_models_cache(self):
        ModelsMixin._models_cache = None
        ModelsMixin._models_cache_time = 0
        ModelsMixin._models_cache_key = None

    def _get_models_cached(self, api_key, force_refresh=False):
        """Return cached models if fresh, otherwise fetch and cache."""
        now = time()
        if (
            not force_refresh
            and ModelsMixin._models_cache
            and ModelsMixin._models_cache_key == api_key
            and (now - ModelsMixin._models_cache_time)
            < ModelsMixin._models_cache_ttl
        ):
            return ModelsMixin._models_cache, None

        try:
            with ModelsMixin._models_lock:
                # Double-check cache inside the lock
                now = time()
                if (
                    not force_refresh
                    and ModelsMixin._models_cache
                    and ModelsMixin._models_cache_key == api_key
                    and (now - ModelsMixin._models_cache_time)
                    < ModelsMixin._models_cache_ttl
                ):
                    return ModelsMixin._models_cache, None

                # SDK returns a typed model; normalize it to a plain dict shape.
                with self._openrouter_sdk(api_key, timeout_ms=10_000) as open_router:
                    resp = open_router.models.list()

                data = resp.model_dump() if hasattr(resp, "model_dump") else resp
                ModelsMixin._models_cache = data
                ModelsMixin._models_cache_time = now
                ModelsMixin._models_cache_key = api_key
                return data, None
        except Exception as e:
            # Normalize common vendor errors into a friendlier message.  When the
            # vendored OpenRouter SDK is missing or corrupted, the underlying
            # RuntimeError message may include platform-specific guidance.  To
            # avoid leaking internal paths, return a concise explanation.
            msg = str(e)
            if any(
                phrase in msg.lower()
                for phrase in (
                    "missing vendored dependencies",
                    "corrupted vendor directory",
                    "openrouter sdk marker exists",
                )
            ):
                msg = (
                    "OpenRouter SDK is not available; please install or vendor the SDK to enable model listing"
                )
            return None, msg

    def get_models(self):
        api_key = self.get_api_key()
        if not api_key:
            self.send_json({"error": "No API key"}, 500)
            return

        qs = parse_qs(urlparse(self.path).query or "")
        force_refresh = _is_truthy(qs.get("refresh", ["0"])[0])
        vision_only = _is_truthy(qs.get("vision", ["0"])[0])

        data, error = self._get_models_cached(api_key, force_refresh=force_refresh)
        if error:
            self.send_json({"error": error}, 500)
            return

        models = []
        for m in data.get("data", []):
            arch = m.get("architecture", {}) or {}
            input_modalities = arch.get("input_modalities", []) or []
            output_modalities = arch.get("output_modalities", []) or []
            modality = (arch.get("modality", "") or "").lower()
            is_vision = ("image" in input_modalities) and ("text" in output_modalities)
            if not is_vision and "image" in modality and "text" in modality:
                is_vision = True
            if vision_only and not is_vision:
                continue

            pricing = m.get("pricing", {})
            prompt_price = pricing.get("prompt", "0")
            try:
                prompt_cost = float(prompt_price)
            except Exception:
                prompt_cost = 0

            models.append(
                {
                    "id": m.get("id", ""),
                    "name": m.get("name", m.get("id", "")),
                    "context_length": m.get("context_length", 0),
                    "is_free": prompt_cost == 0,
                    "is_vision": is_vision,
                    "pricing": {
                        "prompt": prompt_price,
                        "completion": pricing.get("completion", "0"),
                    },
                    "description": m.get("description", ""),
                    "supported_parameters": m.get("supported_parameters", []),
                    "architecture": arch,
                    "top_provider": m.get("top_provider", {}),
                }
            )
        self.send_json({"success": True, "models": models})

    def _find_model_data(self, data, model_id):
        """Find a model, accepting current OpenRouter latest aliases."""
        model_id = str(model_id or "").lstrip("\ufeff").strip()
        if not model_id or not isinstance(data, dict):
            return model_id, None

        resolved_id = model_id
        try:
            resolved_id = self._resolve_openrouter_model_id(model_id, data)
        except Exception:
            resolved_id = model_id

        for m in data.get("data", []):
            if isinstance(m, dict) and m.get("id") == resolved_id:
                return resolved_id, m

        if model_id.startswith("~"):
            plain_id = model_id[1:]
            for m in data.get("data", []):
                if isinstance(m, dict) and m.get("id") == plain_id:
                    return plain_id, m

        return resolved_id, None

    def _suggest_model_ids(self, data, model_id, limit=6):
        """Return nearby current model ids for a missing/stale id."""
        if not isinstance(data, dict):
            return []

        model_ids = [
            str(m.get("id") or "")
            for m in data.get("data", [])
            if isinstance(m, dict) and m.get("id")
        ]
        model_id = str(model_id or "").strip()
        if not model_id:
            return []

        suggestions = []
        provider = model_id.split("/", 1)[0].lstrip("~")
        tail = model_id.split("/", 1)[-1].lower()
        tail_prefix = tail.rsplit("-", 1)[0] if "-" in tail else tail

        for candidate in model_ids:
            candidate_plain = candidate.lstrip("~")
            candidate_provider = candidate_plain.split("/", 1)[0]
            candidate_tail = candidate_plain.split("/", 1)[-1].lower()
            if candidate_provider == provider and (
                tail_prefix and tail_prefix in candidate_tail
                or candidate_tail and candidate_tail in tail
            ):
                suggestions.append(candidate)
                if len(suggestions) >= limit:
                    return suggestions

        for candidate in difflib.get_close_matches(model_id, model_ids, n=limit, cutoff=0.45):
            if candidate not in suggestions:
                suggestions.append(candidate)

        return suggestions[:limit]

    def _canonicalize_openrouter_model_id(self, model_id):
        """Return a current OpenRouter model id, or a validation error if known stale."""
        model_id = str(model_id or "").lstrip("\ufeff").strip()
        if not model_id:
            return model_id, "Model ID is required", []

        try:
            api_key = self.get_api_key()
        except Exception:
            api_key = ""
        if not api_key:
            return model_id, None, []

        try:
            data, error = self._get_models_cached(api_key)
        except Exception:
            return model_id, None, []

        if error or not isinstance(data, dict):
            return model_id, None, []

        resolved_id, model_data = self._find_model_data(data, model_id)
        if model_data:
            return resolved_id, None, []

        # Hardened fallback: allow prompt saves for unrecognized or new models (e.g. gpt-5.5)
        logger.info(f"Model '{model_id}' is not in the cached OpenRouter model list, but allowing it as a fallback/new model.")
        return model_id, None, []

    def get_model_defaults(self, model_id):
        """Get model defaults and supported parameters from OpenRouter API."""
        api_key = self.get_api_key()
        if not api_key:
            self.send_json({"error": "No API key"}, 500)
            return

        # URL decode the model_id
        model_id = unquote(model_id)
        # Strip potential BOM or stray whitespace
        model_id = model_id.lstrip("\ufeff").strip()

        data, error = self._get_models_cached(api_key)
        if error:
            self.send_json({"error": error}, 500)
            return

        model_id, model_data = self._find_model_data(data, model_id)

        if not model_data:
            # Hardened fallback: instead of returning 404, synthesize a safe default model_data block.
            # This ensures custom or unrecognized models work flawlessly.
            logger.warning(f"Model '{model_id}' not found in registry cache; using safe fallback parameters.")
            model_data = {
                "id": model_id,
                "name": model_id.split("/")[-1] if "/" in model_id else model_id,
                "description": f"Fallback configuration for unrecognized model '{model_id}'",
                "context_length": 8192,
                "supported_parameters": [
                    "temperature", "top_p", "max_completion_tokens", "max_tokens",
                    "frequency_penalty", "presence_penalty", "repetition_penalty",
                    "stop", "response_format", "tools", "tool_choice", "reasoning"
                ],
                "architecture": {
                    "input_modalities": ["text", "image"],
                    "output_modalities": ["text"]
                },
                "top_provider": {
                    "max_completion_tokens": 4096
                },
                "pricing": {
                    "prompt": "0.0",
                    "completion": "0.0"
                }
            }

        # Extract supported parameters with their defaults
        supported_params = model_data.get("supported_parameters", [])

        # Build parameter info with descriptions and defaults
        parameter_info = self._build_parameter_info(supported_params, model_data)

        self.send_json(
            {
                "success": True,
                "data": {
                    "model_id": model_id,
                    "name": model_data.get("name", model_id),
                    "description": model_data.get("description", ""),
                    "context_length": model_data.get("context_length", 0),
                    "supported_parameters": supported_params,
                    "parameter_info": parameter_info,
                    "architecture": model_data.get("architecture", {}),
                    "top_provider": model_data.get("top_provider", {}),
                    "pricing": model_data.get("pricing", {}),
                },
            }
        )

    def _build_parameter_info(self, supported_params, model_data):
        """Build detailed parameter info with descriptions and defaults."""
        # Standard parameter definitions
        param_definitions = {
            "temperature": {
                "name": "Temperature",
                "description": "Controls randomness. Lower = more focused, higher = more creative",
                "type": "float",
                "min": 0.0,
                "max": 2.0,
                "default": 1.0,
                "step": 0.1,
                "category": "creativity",
            },
            "top_p": {
                "name": "Top P (Nucleus Sampling)",
                "description": "Controls diversity via nucleus sampling. Lower = more focused on likely tokens",
                "type": "float",
                "min": 0.0,
                "max": 1.0,
                "default": 1.0,
                "step": 0.05,
                "category": "creativity",
            },
            "top_k": {
                "name": "Top K",
                "description": "Limits token selection to top K most likely. Lower = more focused",
                "type": "integer",
                "min": 0,
                "max": 100,
                "default": 0,
                "step": 1,
                "category": "creativity",
            },
            "max_completion_tokens": {
                "name": "Max Tokens",
                "description": "Maximum number of tokens to generate in the response",
                "type": "integer",
                "min": 1,
                "max": model_data.get("context_length", 4096),
                "default": 1024,
                "step": 64,
                "category": "output",
            },
            "max_tokens": {
                "name": "Max Tokens",
                "description": "Maximum number of tokens to generate in the response",
                "type": "integer",
                "min": 1,
                "max": model_data.get("context_length", 4096),
                "default": 1024,
                "step": 64,
                "category": "output",
            },
            "frequency_penalty": {
                "name": "Frequency Penalty",
                "description": "Reduces repetition of frequent tokens. Higher = less repetition",
                "type": "float",
                "min": -2.0,
                "max": 2.0,
                "default": 0.0,
                "step": 0.1,
                "category": "repetition",
            },
            "presence_penalty": {
                "name": "Presence Penalty",
                "description": "Encourages new topics. Higher = more topic diversity",
                "type": "float",
                "min": -2.0,
                "max": 2.0,
                "default": 0.0,
                "step": 0.1,
                "category": "repetition",
            },
            "repetition_penalty": {
                "name": "Repetition Penalty",
                "description": "Penalizes repeated tokens. Higher = less repetition (alternative to frequency_penalty)",
                "type": "float",
                "min": 0.0,
                "max": 2.0,
                "default": 1.0,
                "step": 0.05,
                "category": "repetition",
            },
            "seed": {
                "name": "Seed",
                "description": "Random seed for reproducible outputs. Same seed = same output",
                "type": "integer",
                "min": 0,
                "max": 2147483647,
                "default": None,
                "step": 1,
                "category": "reproducibility",
            },
            "stop": {
                "name": "Stop Sequences",
                "description": "Sequences where the model will stop generating",
                "type": "array",
                "default": [],
                "category": "output",
            },
            "min_p": {
                "name": "Min P",
                "description": "Minimum probability threshold for token selection",
                "type": "float",
                "min": 0.0,
                "max": 1.0,
                "default": 0.0,
                "step": 0.01,
                "category": "creativity",
            },
            "top_a": {
                "name": "Top A",
                "description": "Alternative sampling method based on token probability ratios",
                "type": "float",
                "min": 0.0,
                "max": 1.0,
                "default": 0.0,
                "step": 0.01,
                "category": "creativity",
            },
            "logit_bias": {
                "name": "Logit Bias",
                "description": "Adjust likelihood of specific tokens appearing",
                "type": "object",
                "default": {},
                "category": "advanced",
            },
            "logprobs": {
                "name": "Log Probabilities",
                "description": "Return log probabilities of output tokens",
                "type": "boolean",
                "default": False,
                "category": "advanced",
            },
            "top_logprobs": {
                "name": "Top Log Probabilities",
                "description": "Number of most likely tokens to return probabilities for",
                "type": "integer",
                "min": 0,
                "max": 20,
                "default": 0,
                "step": 1,
                "category": "advanced",
            },
            "response_format": {
                "name": "Response Format",
                "description": "Specify output format (e.g., JSON mode)",
                "type": "object",
                "default": None,
                "category": "output",
            },
            "tools": {
                "name": "Tools",
                "description": "Function calling / tool use definitions",
                "type": "array",
                "default": [],
                "category": "advanced",
            },
            "tool_choice": {
                "name": "Tool Choice",
                "description": "Control which tool(s) the model should use",
                "type": "string",
                "default": "auto",
                "category": "advanced",
            },
            "structured_outputs": {
                "name": "Structured Outputs",
                "description": "Require providers that can honor strict JSON schema response_format requests",
                "type": "boolean",
                "default": False,
                "category": "output",
            },
            "parallel_tool_calls": {
                "name": "Parallel Tool Calls",
                "description": "Allow the model to call multiple tools in one response when tools are provided",
                "type": "boolean",
                "default": True,
                "category": "advanced",
            },
            "verbosity": {
                "name": "Verbosity",
                "description": "Constrains response detail for models/providers that support verbosity control",
                "type": "string",
                "options": ["low", "medium", "high", "max"],
                "default": "medium",
                "category": "output",
            },
            "web_search_options": {
                "name": "Web Search Options",
                "description": "Provider-specific web search options for models that advertise native search controls",
                "type": "object",
                "default": None,
                "category": "advanced",
            },
            "reasoning_effort": {
                "name": "Reasoning Effort",
                "description": "Legacy flat reasoning effort. Prefer the unified reasoning controls when available",
                "type": "string",
                "options": ["none", "minimal", "low", "medium", "high", "xhigh"],
                "default": "medium",
                "category": "reasoning",
            },
            "reasoning": {
                "name": "Reasoning & Thinking",
                "description": "Configure OpenRouter reasoning controls including enablement, effort, token budget, and response exclusion",
                "type": "reasoning_config",
                "default": {
                    "enabled": False,
                    "effort": "medium",
                    "exclude": False,
                },
                "category": "reasoning",
            },
        }

        # Build info for supported parameters only
        result = {}
        for param in supported_params:
            if param in ("include_reasoning", "reasoning_effort") and "reasoning" in supported_params:
                continue
            if param in param_definitions:
                result[param] = param_definitions[param].copy()
            else:
                # Unknown parameter - add basic info
                result[param] = {
                    "name": param.replace("_", " ").title(),
                    "description": f"Model-specific parameter: {param}",
                    "type": "unknown",
                    "default": None,
                    "category": "other",
                }

        return result
