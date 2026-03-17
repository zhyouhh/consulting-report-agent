from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class ProjectInfo(BaseModel):
    """项目信息"""
    name: str = Field(..., min_length=1, max_length=50, pattern=r'^[a-zA-Z0-9_\u4e00-\u9fa5-]+$')
    report_type: str = Field(..., pattern=r'^(research-report|system-plan|implementation|regulation)$')
    theme: str = Field(..., min_length=1, max_length=200)
    target_audience: str = Field(..., min_length=1, max_length=100)


class Message(BaseModel):
    """对话消息"""
    role: str  # user | assistant
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)


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
