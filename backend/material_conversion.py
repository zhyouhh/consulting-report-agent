"""N6 统一材料转换服务。依赖注入、纯边界（不依赖 chat 模块，仿 report_writing.py 架构）。

职责：文档转换（markitdown/LibreOffice）+ 图片转写（注入的 vision/ocr 适配器）+
缓存/tombstone/锁/GC。caller（ChatHandler）注入 settings/client 派生的适配器与 capability。
"""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path
from typing import Callable

CONVERTER_VERSION = "n6-v1"


class MaterialConverter:
    def __init__(
        self,
        *,
        cache_dir: Path,
        vision_adapter: Callable[[str, str], str],   # (data_url, mime) -> 转写文本
        ocr_adapter: Callable[[Path], str],           # (image_path) -> 文字
        capability_resolver: Callable[[], bool],      # () -> 主模型是否多模态
        image_cache_namespace: str = "default",       # = 视觉模型 id + prompt 版本 + OCR 版本（spec §6 缓存键）
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._vision_adapter = vision_adapter
        self._ocr_adapter = ocr_adapter
        self._capability_resolver = capability_resolver
        self._image_cache_namespace = image_cache_namespace
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _content_hash(self, path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    def _lock_for(self, key: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock
