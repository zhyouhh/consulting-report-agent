"""N6 统一材料转换服务。依赖注入、纯边界（不依赖 chat 模块，仿 report_writing.py 架构）。

职责：文档转换（markitdown/LibreOffice）+ 图片转写（注入的 vision/ocr 适配器）+
缓存/tombstone/锁/GC。caller（ChatHandler）注入 settings/client 派生的适配器与 capability。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

CONVERTER_VERSION = "n6-v1"

TEXT_SUFFIXES = {".txt", ".md", ".csv", ".markdown"}

LIBREOFFICE_FORCE_SUFFIXES = {".doc": "docx", ".ppt": "pptx"}  # .xls 不在内：先试 markitdown
SOFFICE_TIMEOUT_SECONDS = 120

# Office Open XML 和 ODF 格式均为 ZIP 容器，magic bytes = b"PK\x03\x04"
ZIP_BASED_SUFFIXES = {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"}
_ZIP_MAGIC = b"PK\x03\x04"


class MaterialConversionError(Exception):
    """转换失败（含 tombstone 命中）。caller 据此返回工具 error，不当成功正文。"""


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

    def _cache_paths(self, content_hash: str) -> tuple[Path, Path]:
        base = self.cache_dir / content_hash
        return base.with_suffix(".md"), base.with_suffix(".error")

    def _atomic_write(self, target: Path, text: str) -> None:
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)

    def _cache_key(self, path: Path, extra: str = "") -> str:
        return self._content_hash(path) + "-" + CONVERTER_VERSION + extra

    def convert_document(self, path: Path) -> str:
        suffix = path.suffix.lower()
        key = self._cache_key(path)
        md_path, err_path = self._cache_paths(key)
        with self._lock_for(key):
            if md_path.exists():
                return md_path.read_text(encoding="utf-8")
            if err_path.exists():
                raise MaterialConversionError(err_path.read_text(encoding="utf-8"))
            try:
                md = self._raw_convert_document(path, suffix)
            except MaterialConversionError as exc:   # 友好原文（老版本/超时…）原样落 tombstone 并 re-raise
                self._atomic_write(err_path, str(exc))
                raise
            except Exception as exc:  # noqa: BLE001 - 其它异常转 tombstone
                reason = f"文档解析失败：{type(exc).__name__}"
                self._atomic_write(err_path, reason)
                raise MaterialConversionError(reason) from exc
            self._atomic_write(md_path, md)
            return md

    def _refs_path(self, key: str) -> Path:
        return self.cache_dir / (key + ".refs")

    def retain(self, key: str, material_id: str) -> None:
        with self._lock_for(key):
            refs = self._read_refs(key); refs.add(material_id)
            self._atomic_write(self._refs_path(key), "\n".join(sorted(refs)))

    def release(self, key: str, material_id: str) -> None:
        with self._lock_for(key):
            refs = self._read_refs(key); refs.discard(material_id)
            if refs:
                self._atomic_write(self._refs_path(key), "\n".join(sorted(refs)))
                return
            for p in (self._refs_path(key), *self._cache_paths(key)):
                if p.exists():
                    p.unlink()

    def _read_refs(self, key: str) -> set[str]:
        p = self._refs_path(key)
        if not p.exists():
            return set()
        return {ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()}

    def _markitdown_convert(self, file_path: Path) -> str:
        # ZIP 容器格式（docx/xlsx/pptx/odt…）校验文件头：markitdown 会把损坏文件当纯文本返回，先挡住
        if file_path.suffix.lower() in ZIP_BASED_SUFFIXES:
            with open(file_path, "rb") as fh:
                magic = fh.read(4)
            if magic != _ZIP_MAGIC:
                raise ValueError(f"文件头校验失败，不是有效的 {file_path.suffix.lower()} 格式（ZIP magic 不匹配）")
        from markitdown import MarkItDown
        # 收面：禁插件、禁远程抓取（仅本地文件转换，§9.3）
        text = (MarkItDown(enable_plugins=False).convert(str(file_path)).text_content or "").strip()
        if not text:
            raise ValueError("empty conversion result")
        return text

    def _raw_convert_document(self, path: Path, suffix: str) -> str:
        if suffix in TEXT_SUFFIXES:
            return path.read_text(encoding="utf-8")
        if suffix in LIBREOFFICE_FORCE_SUFFIXES:                 # .doc/.ppt：必须先 LibreOffice
            converted = self._libreoffice_to_modern(path, LIBREOFFICE_FORCE_SUFFIXES[suffix])
            return self._markitdown_convert(converted)
        if suffix == ".xls":                                     # .xls：markitdown 优先，失败回退 LibreOffice
            try:
                return self._markitdown_convert(path)
            except Exception:
                converted = self._libreoffice_to_modern(path, "xlsx")
                return self._markitdown_convert(converted)
        return self._markitdown_convert(path)                    # docx/pptx/xlsx/pdf/html/csv…

    def _libreoffice_to_modern(self, path: Path, target_ext: str) -> Path:
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if not soffice:
            raise MaterialConversionError("老版本 .doc/.ppt 在当前环境读不了（缺 LibreOffice）")
        outdir = self.cache_dir / "_soffice"
        outdir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                [soffice, "--headless", "--convert-to", target_ext, "--outdir", str(outdir), str(path)],
                timeout=SOFFICE_TIMEOUT_SECONDS, check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
            raise MaterialConversionError("老格式转换失败（LibreOffice 超时或出错），请改存为新版格式重传") from exc
        out = outdir / (path.stem + "." + target_ext)
        if not out.exists():
            raise MaterialConversionError("老格式转换未产出文件，请改存为新版格式重传")
        return out
