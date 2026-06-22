import base64
import binascii
from datetime import datetime
from typing import Literal, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.material_limits import (
    ALLOWED_IMAGE_MIME,
    MAX_TRANSIENT_ATTACHMENTS,
    MAX_TRANSIENT_IMAGE_BYTES,
)


class ProjectInfo(BaseModel):
    """Project creation payload."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=200)
    workspace_dir: Optional[str] = Field(default=None, max_length=500)
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

    # Front-end pending-attachment id, so attachment_transcribed events can reference it.
    id: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=255)
    mime_type: str = Field(..., min_length=1, max_length=100)
    data_url: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def _ensure_allowed_mime(self):
        if self.mime_type not in ALLOWED_IMAGE_MIME:
            raise ValueError(
                f"不支持的图片格式 {self.mime_type!r}，"
                f"允许的格式：{', '.join(sorted(ALLOWED_IMAGE_MIME))}"
            )
        return self


SystemTriggerType = Literal["independent_review_done"]


class ChatRequest(BaseModel):
    """Chat request payload."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., min_length=1, max_length=100)
    message_text: str = Field(default="", max_length=10000)
    # Front-end client message id for a normal user turn (so transcription events can target
    # the right pending message). OPTIONAL: system_trigger turns are not user messages and
    # won't send it.
    client_message_id: Optional[str] = Field(default=None, max_length=100)
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

    @model_validator(mode="after")
    def _validate_transient_attachment_limits(self):
        attachments = self.transient_attachments
        if not attachments:
            return self
        # Count limit
        if len(attachments) > MAX_TRANSIENT_ATTACHMENTS:
            raise ValueError(
                f"图片数量过多：最多 {MAX_TRANSIENT_ATTACHMENTS} 张，当前 {len(attachments)} 张"
            )
        # Per-attachment byte size limit (decoded)
        for att in attachments:
            data_url = att.data_url
            # Require the canonical data URL format: "data:<mime>;base64,<payload>"
            if not data_url.startswith("data:") or ";base64," not in data_url:
                raise ValueError(
                    f"附件 {att.name!r} 的 data_url 格式无效，"
                    "需要 data:<mime>;base64,<payload> 格式"
                )
            # Fix2: the data_url header carries the REAL mime the provider sees — the
            # `mime_type` field alone is not enough, since chat.py forwards data_url verbatim
            # as the image_url. Parse `data:<mime>;base64,` and require <mime> to be both
            # allow-listed AND equal to the declared mime_type field (no smuggling a
            # text/html data_url behind a mime_type="image/png" label).
            header, b64_payload = data_url.split(";base64,", 1)
            data_url_mime = header[len("data:"):]
            if data_url_mime not in ALLOWED_IMAGE_MIME:
                raise ValueError(
                    f"附件 {att.name!r} 的 data_url 声明的格式 {data_url_mime!r} 不被支持，"
                    f"允许的格式：{', '.join(sorted(ALLOWED_IMAGE_MIME))}"
                )
            if data_url_mime != att.mime_type:
                raise ValueError(
                    f"附件 {att.name!r} 的 data_url 格式 {data_url_mime!r} 与声明的 "
                    f"mime_type {att.mime_type!r} 不一致"
                )
            # Fix3: strict base64 decode — validate=False silently drops invalid chars, so a
            # non-canonical payload (e.g. trailing "!!!!") would pass and make byte accounting
            # unreliable. Use validate=True and surface a clean Chinese validation error.
            try:
                decoded = base64.b64decode(b64_payload, validate=True)
            except (binascii.Error, ValueError):
                raise ValueError(
                    f"附件 {att.name!r} 的 data_url 无法解码：base64 内容损坏"
                )
            decoded_bytes = len(decoded)
            if decoded_bytes > MAX_TRANSIENT_IMAGE_BYTES:
                limit_mb = MAX_TRANSIENT_IMAGE_BYTES / (1024 * 1024)
                actual_mb = decoded_bytes / (1024 * 1024)
                raise ValueError(
                    f"附件 {att.name!r} 大小 {actual_mb:.1f} MB 超过单图上限 {limit_mb:.0f} MB，"
                    f"请压缩后重试（字节数：{decoded_bytes}）"
                )
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
