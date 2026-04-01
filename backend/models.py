from datetime import datetime
from typing import Literal, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProjectInfo(BaseModel):
    """Project creation payload."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=50, pattern=r"^[a-zA-Z0-9_\u4e00-\u9fa5-]+$")
    workspace_dir: str = Field(..., min_length=1, max_length=500)
    project_type: str = Field(..., min_length=1, max_length=100)
    theme: str = Field(..., min_length=1, max_length=200)
    target_audience: str = Field(..., min_length=1, max_length=100)
    deadline: str = Field(..., min_length=1, max_length=50)
    expected_length: str = Field(..., min_length=1, max_length=100)
    notes: str = Field(default="", max_length=2000)
    initial_material_paths: List[str] = Field(default_factory=list)


class Message(BaseModel):
    """Conversation message."""

    role: str
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now())


class TransientAttachment(BaseModel):
    """Transient attachment for the current turn."""

    name: str = Field(..., min_length=1, max_length=255)
    mime_type: str = Field(..., min_length=1, max_length=100)
    data_url: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def _ensure_image_only(self):
        if not self.mime_type.startswith("image/"):
            raise ValueError("transient_attachments only supports image/* payloads")
        return self


class ChatRequest(BaseModel):
    """Chat request payload."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., min_length=1, max_length=100)
    message_text: str = Field(..., min_length=1, max_length=10000)
    attached_material_ids: List[str] = Field(default_factory=list)
    transient_attachments: List[TransientAttachment] = Field(default_factory=list)


class TokenUsage(BaseModel):
    """Token usage snapshot."""

    current_tokens: int = 0
    max_tokens: int = 128000
    effective_max_tokens: int = 128000
    provider_max_tokens: int = 128000
    compressed: bool = False
    usage_mode: Literal["actual", "estimated"] = "estimated"

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_max_tokens(cls, data):
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        legacy_max_tokens = normalized.pop("max_tokens", None)
        if legacy_max_tokens is not None:
            normalized["max_tokens"] = legacy_max_tokens
            normalized.setdefault("effective_max_tokens", legacy_max_tokens)
            normalized.setdefault("provider_max_tokens", legacy_max_tokens)
        else:
            effective_max_tokens = normalized.get("effective_max_tokens")
            if effective_max_tokens is not None:
                normalized.setdefault("max_tokens", effective_max_tokens)
        return normalized


class ChatResponse(BaseModel):
    """Chat response payload."""

    content: str
    files_updated: Optional[List[str]] = None
    token_usage: Optional[TokenUsage] = None
