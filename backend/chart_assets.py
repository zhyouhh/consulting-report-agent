"""图表资产层（叶子模块，只依赖 stdlib）：落盘 / 引用扫描 / 孤儿清扫。

职责（spec §4.3）：
- PNG + sidecar json 原子写（temp + os.replace，对齐 R3 原子写不变式）
- 草稿引用扫描契约：markdown 图片 / raw <img> / query 串 / URL 编码 / 重复引用
- 孤儿清扫：只删「未被草稿引用 且 mtime 早于 grace 窗口」的资产；绝不在导出路径跑
- 导出前缺图清单（report_tools 的硬门禁数据源）

资产命名：`chart-<uuid12>.png`，chart_id 每次铸新 → PNG 内容不可变 →
预览 URL 无需 cache-bust、可安全长缓存。
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Dict, List, Set
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

ASSETS_SUBDIR = "content/assets"           # 相对项目根；与草稿同级的 assets/ 子目录
DRAFT_RELATIVE_PREFIX = "assets/"          # 草稿内引用的相对前缀
SWEEP_GRACE_SECONDS = 600                  # 刚生成、尚未插入正文的图受 10min 保护
MAX_SIDECAR_BYTES = 64 * 1024

_ASSET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}\.png$")
# markdown 图片：![alt](url "title") / ![alt](<url>)；捕获 url 段
_MD_IMAGE_RE = re.compile(r"!\[[^\]\n]*\]\(\s*<?([^)<>\s]+)>?(?:\s+[^)]*)?\)")
# rehype-raw 允许的裸 <img src="...">
_HTML_IMAGE_RE = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)


def new_chart_id() -> str:
    return f"chart-{uuid.uuid4().hex[:12]}"


def assets_dir(project_path: Path) -> Path:
    return project_path / ASSETS_SUBDIR


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_chart_asset(project_path: Path, chart_id: str, png_bytes: bytes, sidecar: Dict) -> str:
    """PNG + sidecar 成对原子写。返回相对项目根的资产路径（posix）。

    sidecar 超限时降级为只留元信息（数据留痕是 advisory，不因它拒绝出图）。
    """
    directory = assets_dir(project_path)
    directory.mkdir(parents=True, exist_ok=True)
    png_path = directory / f"{chart_id}.png"
    _atomic_write_bytes(png_path, png_bytes)

    payload = json.dumps(sidecar, ensure_ascii=False, indent=2).encode("utf-8")
    if len(payload) > MAX_SIDECAR_BYTES:
        trimmed = {
            key: sidecar.get(key)
            for key in ("kind", "title", "source", "created_at")
            if key in sidecar
        }
        trimmed["note"] = "数据留痕超出大小上限，未完整保存"
        payload = json.dumps(trimmed, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        _atomic_write_bytes(directory / f"{chart_id}.json", payload)
    except OSError:
        logger.warning("[chart-assets] sidecar 写入失败（advisory，忽略）: %s", chart_id)
    return f"{ASSETS_SUBDIR}/{chart_id}.png"


def _extract_asset_name(url: str) -> str | None:
    """把引用 URL 归一成 assets 下的文件名；非本地 assets 引用返回 None。

    契约（spec §4.3 + 测试锁）：剥 query/fragment、URL 解码、容忍 `./`/`content/` 前缀。
    """
    if not url or url.startswith("data:"):
        return None
    parsed = urlparse(url)
    if parsed.scheme or parsed.netloc:  # http(s)/绝对 URL 不算本地资产
        return None
    path = unquote(parsed.path)
    path = path.lstrip("./") if path.startswith("./") else path
    for prefix in (ASSETS_SUBDIR + "/", DRAFT_RELATIVE_PREFIX):
        if path.startswith(prefix):
            name = path[len(prefix):]
            if _ASSET_NAME_RE.match(name):
                return name
            return None
    return None


def scan_chart_references(markdown_text: str) -> Set[str]:
    """扫出草稿引用的全部资产文件名（`<chart_id>.png` 集合，天然去重）。"""
    if not markdown_text:
        return set()
    names: Set[str] = set()
    for pattern in (_MD_IMAGE_RE, _HTML_IMAGE_RE):
        for match in pattern.finditer(markdown_text):
            name = _extract_asset_name(match.group(1))
            if name:
                names.add(name)
    return names


def list_missing_assets(report_path: Path) -> List[str]:
    """导出前硬校验数据源：草稿引用了、盘上却不存在的资产（返回草稿内的引用形态）。"""
    try:
        text = Path(report_path).read_text(encoding="utf-8")
    except OSError:
        return []
    directory = Path(report_path).parent / "assets"
    missing = []
    for name in sorted(scan_chart_references(text)):
        if not (directory / name).is_file():
            missing.append(f"{DRAFT_RELATIVE_PREFIX}{name}")
    return missing


def sweep_orphan_assets(
    project_path: Path,
    draft_text: str | None,
    *,
    grace_seconds: float = SWEEP_GRACE_SECONDS,
    now: float | None = None,
) -> List[str]:
    """删「未被草稿引用 且 早于 grace 窗口」的 png+sidecar。best-effort，绝不抛。

    竞态规约（spec §4.3）：绝不在导出路径调用；grace 窗口保护「刚生成、还没插入」
    的在途图；引用集按调用时草稿内容计算。
    """
    removed: List[str] = []
    try:
        directory = assets_dir(project_path)
        if not directory.is_dir():
            return removed
        referenced = scan_chart_references(draft_text or "")
        current = time.time() if now is None else now
        for png in directory.glob("*.png"):
            name = png.name
            if not _ASSET_NAME_RE.match(name) or name in referenced:
                continue
            try:
                if current - png.stat().st_mtime < grace_seconds:
                    continue
                png.unlink()
                removed.append(name)
                sidecar = directory / (png.stem + ".json")
                if sidecar.is_file():
                    sidecar.unlink()
            except OSError:
                continue
    except Exception:
        logger.warning("[chart-assets] 孤儿清扫失败（best-effort，忽略）", exc_info=True)
    return removed


def load_referenced_sidecars(
    project_path: Path,
    draft_text: str,
    *,
    max_total_chars: int = 12000,
) -> List[Dict]:
    """给 S5 审查的预构建 grounding：草稿引用图的 sidecar 列表（总量截断）。

    返回 [{name, text}]；text 是 sidecar json 原文（不可信数据，调用方负责框定）。
    """
    directory = assets_dir(project_path)
    result: List[Dict] = []
    used = 0
    for name in sorted(scan_chart_references(draft_text)):
        sidecar = directory / (Path(name).stem + ".json")
        if not sidecar.is_file():
            continue
        try:
            text = sidecar.read_text(encoding="utf-8")
        except OSError:
            continue
        if used + len(text) > max_total_chars:
            result.append({"name": name, "text": "（超出注入预算，数据留痕略）"})
            continue
        used += len(text)
        result.append({"name": name, "text": text})
    return result
