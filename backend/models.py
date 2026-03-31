from pydantic import BaseModel, Field, model_validator
from typing import Literal, Optional, List
from datetime import datetime


class ProjectInfo(BaseModel):
    """项目信息"""
    name: str = Field(..., min_length=1, max_length=50, pattern=r'^[a-zA-Z0-9_\u4e00-\u9fa5-]+$')
    workspace_dir: str = Field(..., min_length=1, max_length=500)
    project_type: str = Field(..., min_length=1, max_length=100)
    theme: str = Field(..., min_length=1, max_length=200)
    target_audience: str = Field(..., min_length=1, max_length=100)
    deadline: str = Field(..., min_length=1, max_length=50)
    expected_length: str = Field(..., min_length=1, max_length=100)
    notes: str = Field(default="", max_length=2000)
    initial_material_paths: List[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_report_type(cls, data):
        if isinstance(data, dict) and "project_type" not in data and "report_type" in data:
            data = dict(data)
            data["project_type"] = data["report_type"]
        return data

    @property
    def report_type(self) -> str:
        """兼容旧代码里的 report_type 访问。"""
        return self.project_type


class Message(BaseModel):
    """对话消息"""
    role: str  # user | assistant
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now())


class TransientAttachment(BaseModel):
    """仅服务当前轮次的临时附件。当前只支持图片。"""
    name: str = Field(..., min_length=1, max_length=255)
    mime_type: str = Field(..., min_length=1, max_length=100)
    data_url: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def _ensure_image_only(self):
        if not self.mime_type.startswith("image/"):
            raise ValueError("transient_attachments 只允许图片类型")
        return self


class ChatRequest(BaseModel):
    """对话请求"""
    project_id: str = Field(..., min_length=1, max_length=100)
    message_text: str = Field(..., min_length=1, max_length=10000)
    attached_material_ids: List[str] = Field(default_factory=list)
    transient_attachments: List[TransientAttachment] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_chat_fields(cls, data):
        if isinstance(data, dict):
            normalized = dict(data)
            if "project_id" not in normalized and "project_name" in normalized:
                normalized["project_id"] = normalized["project_name"]
            if "message_text" not in normalized and "message" in normalized:
                normalized["message_text"] = normalized["message"]
            normalized.setdefault("attached_material_ids", [])
            normalized.setdefault("transient_attachments", [])
            return normalized
        return data

    @property
    def project_name(self) -> str:
        return self.project_id

    @property
    def message(self) -> str:
        return self.message_text


class TokenUsage(BaseModel):
    """Token使用统计"""
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
    """对话响应"""
    content: str
    files_updated: Optional[List[str]] = None
    token_usage: Optional[TokenUsage] = None
