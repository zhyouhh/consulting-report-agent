"""N6 附件管线限额常量 + 纯校验（无外部依赖、可单测）。"""
from __future__ import annotations

# 重型类型（docx/pdf 等需全量加载）单文件字节上限；超限 read 直接 friendly fail。
MAX_HEAVY_MATERIAL_BYTES = 25 * 1024 * 1024  # 25MB
HEAVY_MATERIAL_SUFFIXES = {".docx", ".doc", ".pdf", ".pptx", ".ppt", ".xlsx", ".xls"}

# transient 图片限额
MAX_TRANSIENT_ATTACHMENTS = 6
MAX_TRANSIENT_IMAGE_BYTES = 8 * 1024 * 1024  # 单图解码后 8MB
ALLOWED_IMAGE_MIME = {"image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp"}

# 视觉转写
VISION_MAX_TOKENS = 1500
MAX_TRANSCRIPT_CHARS = 8000  # 转写文本持久化上限，超出截断


def is_heavy_suffix(suffix: str) -> bool:
    return suffix.lower() in HEAVY_MATERIAL_SUFFIXES


def truncate_transcript(text: str) -> tuple[str, bool]:
    """返回 (文本, 是否截断)。"""
    if len(text) <= MAX_TRANSCRIPT_CHARS:
        return text, False
    return text[:MAX_TRANSCRIPT_CHARS], True
