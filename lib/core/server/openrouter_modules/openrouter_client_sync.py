"""
Synchronous OpenRouter call mixin (JSON and text completions).
"""

from .._shared import json, logger
from .openrouter_client_config import MAX_LLM_CALL_ATTEMPTS, BASE_TIMEOUT_MS


class OpenRouterClientSyncMixin:
    """Synchronous call helpers for completion endpoints."""

    # Telemetry counters
    _call_counters = {
        "attempts": 0,
        "successes": 0,
        "failures": 0,
        "retries_by_reason": {},
    }

    def _call_llm_core(
        self,
        model,
        messages,
        response_format=None,
        max_completion_tokens=2000,
        max_tokens=None,
        temperature=0.3,
        timeout_ms: int = BASE_TIMEOUT_MS,
        **extra_kwargs,
    ):
        """Unified core LLM dispatch path with retry policy and telemetry."""
        api_key = self.get_api_key()
        if not api_key:
            return None, "No API key configured"

        if max_tokens is not None:
            max_completion_tokens = max_tokens

        req_dict = {
            "model": model,
            "messages": messages,
            "temperature": float(temperature),
            "max_completion_tokens": int(max_completion_tokens),
            "stream": False,
        }
        if response_format is not None:
            req_dict["response_format"] = response_format

        for k, v in extra_kwargs.items():
            if v is not None:
                req_dict[k] = v

        req = self._sanitize_chat_kwargs(req_dict)

        last_error = None
        for attempt in range(MAX_LLM_CALL_ATTEMPTS):
            attempt_timeout = int(timeout_ms * (attempt + 1))
            self._call_counters["attempts"] += 1
            try:
                resp = self._send_openrouter_chat(api_key, req, timeout_ms=attempt_timeout)
                data = resp.model_dump() if hasattr(resp, "model_dump") else resp
                content = (
                    (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
                )
                
                # Treat empty response as a transient error to retry
                if not content:
                    last_error = "No content in response"
                    self._call_counters["retries_by_reason"]["empty_response"] = \
                        self._call_counters["retries_by_reason"].get("empty_response", 0) + 1
                    if attempt < MAX_LLM_CALL_ATTEMPTS - 1:
                        logger.warning(
                            f"[_call_llm_core] Empty content in attempt {attempt+1}/{MAX_LLM_CALL_ATTEMPTS}; retrying"
                        )
                        continue
                    else:
                        self._call_counters["failures"] += 1
                        return None, last_error
                
                self._call_counters["successes"] += 1
                return data, None

            except Exception as e:
                last_error = str(e)
                err_lower = last_error.lower()
                
                # Classify retry-worthy errors (timeouts, connection issues, rate limits)
                should_retry = any(
                    term in err_lower
                    for term in (
                        "timeout",
                        "timed out",
                        "temporarily unavailable",
                        "connection",
                        "connect",
                        "reset by peer",
                        "rate_limited",
                        "too many requests",
                    )
                )
                
                reason = "transient_error" if should_retry else "fatal_error"
                self._call_counters["retries_by_reason"][reason] = \
                    self._call_counters["retries_by_reason"].get(reason, 0) + 1
                
                if attempt < MAX_LLM_CALL_ATTEMPTS - 1 and should_retry:
                    logger.warning(
                        f"[_call_llm_core] Attempt {attempt+1}/{MAX_LLM_CALL_ATTEMPTS} failed: {last_error}; retrying with timeout {attempt_timeout} ms"
                    )
                    continue
                
                self._call_counters["failures"] += 1
                return None, last_error

        self._call_counters["failures"] += 1
        return None, last_error or "Unknown error"

    def call_llm(
        self,
        model,
        messages,
        max_completion_tokens=2000,
        max_tokens=None,
        temperature=0.3,
        **extra_kwargs,
    ):
        """Make a call to the OpenRouter API and return a plain text response."""
        data, err = self._call_llm_core(
            model=model,
            messages=messages,
            max_completion_tokens=max_completion_tokens,
            max_tokens=max_tokens,
            temperature=temperature,
            **extra_kwargs,
        )
        if err:
            return None, err
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return content or "", None

    def call_llm_json(
        self,
        model,
        messages,
        *,
        schema_name,
        schema,
        max_completion_tokens=2000,
        max_tokens=None,
        temperature=0.2,
        **extra_kwargs,
    ):
        """Call OpenRouter and require a structured JSON response via response_format."""
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": str(schema_name or "response"),
                "strict": True,
                "schema": schema or {},
            },
        }
        data, err = self._call_llm_core(
            model=model,
            messages=messages,
            response_format=response_format,
            max_completion_tokens=max_completion_tokens,
            max_tokens=max_tokens,
            temperature=temperature,
            **extra_kwargs,
        )
        if err:
            return None, err
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        try:
            return json.loads(content), None
        except json.JSONDecodeError as e:
            return None, f"Invalid JSON response: {e}"
