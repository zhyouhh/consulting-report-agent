from pydantic import BaseModel, Field, model_validator
from typing import Optional, List
from datetime import datetime


class ProjectInfo(BaseModel):
    """项目信息"""
    name: str = Field(..., min_length=1, max_length=50, pattern=r'^[a-zA-Z0-9_\u4e00-\u9fa5-]+$')
    project_type: str = Field(..., min_length=1, max_length=100)
    theme: str = Field(..., min_length=1, max_length=200)
    target_audience: str = Field(..., min_length=1, max_length=100)
    deadline: str = Field(..., min_length=1, max_length=50)
    expected_length: str = Field(..., min_length=1, max_length=100)
    notes: str = Field(default="", max_length=2000)

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


class ChatRequest(BaseModel):
    """对话请求"""
    project_name: str = Field(..., min_length=1, max_length=50, pattern=r'^[a-zA-Z0-9_\u4e00-\u9fa5-]+$')
    message: str = Field(..., min_length=1, max_length=10000)


class TokenUsage(BaseModel):
    """Token使用统计"""
    current_tokens: int = 0
    max_tokens: int = 128000
    compressed: bool = False


class ChatResponse(BaseModel):
    """对话响应"""
    content: str
    files_updated: Optional[List[str]] = None
    token_usage: Optional[TokenUsage] = None
