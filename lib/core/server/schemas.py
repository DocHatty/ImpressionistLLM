"""Validation schemas for local server request payloads."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError, ValidationInfo
from pydantic import field_validator, model_validator
from pydantic.config import ConfigDict


def _has_non_empty_content(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())

    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str) and text.strip():
            return True

        image_url = value.get("image_url")
        if isinstance(image_url, str) and image_url.strip():
            return True
        if isinstance(image_url, dict) and str(image_url.get("url") or "").strip():
            return True

        return any(_has_non_empty_content(value.get(key)) for key in ("content", "value"))

    if isinstance(value, list):
        return any(_has_non_empty_content(item) for item in value)

    return value is not None


class ChatMessage(BaseModel):
    """Single OpenAI/OpenRouter chat message."""

    model_config = ConfigDict(extra="allow")

    role: str
    content: Any = None

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        role = str(value or "").strip()
        if role not in {"system", "user", "assistant", "tool", "developer"}:
            raise ValueError("role must be one of system, user, assistant, tool, developer")
        return role

    @model_validator(mode="after")
    def validate_content(self) -> "ChatMessage":
        if not _has_non_empty_content(self.content):
            raise ValueError("message must contain at least one non-empty text or image content part")
        return self


class ChatRequest(BaseModel):
    """OpenRouter chat request shape accepted by the local server."""

    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[ChatMessage]

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        model = str(value or "").strip()
        if not model:
            raise ValueError("model must be non-empty")
        return model

    @field_validator("messages")
    @classmethod
    def validate_messages(cls, value: list[ChatMessage], info: ValidationInfo) -> list[ChatMessage]:
        if not value:
            raise ValueError("messages must contain at least one message")
        return value


def format_validation_errors(exc: Exception) -> str:
    """Format pydantic validation errors into readable diagnostics."""
    if not isinstance(exc, ValidationError):
        return str(exc)

    messages: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ()))
        msg = err.get("msg", "invalid value")
        if loc:
            messages.append(f"{loc}: {msg}")
        else:
            messages.append(str(msg))

    return "; ".join(messages) if messages else str(exc)
