from datetime import datetime
from typing import Literal, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProjectInfo(BaseModel):
    """Project creation payload."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=200)
    workspace_dir: str = Field(..., min_length=1, max_length=500)
    project_type: str = Field(..., min_length=1, max_length=100)
    theme: str = Field(..., min_length=1, max_length=200)
    # 报告面向对象（高层/中层/执行）已从新建表单移除，保留为可选字段仅作向后兼容。
    target_audience: str = Field(default="", max_length=100)
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


SystemTriggerType = Literal["independent_review_done", "lint_report_done"]


class ChatRequest(BaseModel):
    """Chat request payload."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., min_length=1, max_length=100)
    message_text: str = Field(default="", max_length=10000)
    attached_material_ids: List[str] = Field(default_factory=list)
    transient_attachments: List[TransientAttachment] = Field(default_factory=list)
    system_trigger: Optional[SystemTriggerType] = None
    # Trigger metadata (C4): opaque strings carried end-to-end with a system trigger so the
    # main agent can run-bind a review report (C5). report_mtime_ns MUST stay a string — a raw
    # st_mtime_ns (~1.7e18) exceeds JS Number.MAX_SAFE_INTEGER and would be silently rounded on
    # the frontend, making the run-bound check always fail.
    run_id: Optional[str] = Field(default=None, max_length=100)
    report_mtime_ns: Optional[str] = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def validate_message_or_trigger(self):
        if self.system_trigger is None and not self.message_text.strip():
            raise ValueError("message_text must be non-empty when system_trigger is None")
        return self


class TokenUsage(BaseModel):
    """Token usage snapshot."""

    usage_source: Literal["provider", "provider_partial", "unavailable"] = "unavailable"
    context_used_tokens: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    max_tokens: int = 128000
    effective_max_tokens: int = 128000
    provider_max_tokens: int = 128000
    preflight_compaction_used: bool = False
    post_turn_compaction_status: Literal["not_needed", "completed", "failed", "skipped_unavailable"] = "not_needed"
    compressed: bool = False
    raw_usage: dict | None = None

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
        normalized.setdefault("effective_max_tokens", normalized.get("max_tokens", 128000))
        normalized.setdefault("provider_max_tokens", normalized.get("provider_max_tokens", normalized["effective_max_tokens"]))
        return normalized


class SystemNotice(BaseModel):
    category: str
    path: Optional[str] = None
    reason: str
    user_action: str
    surface_to_user: bool


class ChatResponse(BaseModel):
    """Chat response payload."""

    content: str
    files_updated: Optional[List[str]] = None
    token_usage: Optional[TokenUsage] = None
    system_notices: Optional[List[SystemNotice]] = None
