import json
import os
from pathlib import Path
import shutil

import requests

from backend.config import load_managed_search_pool_config_from_path


def resolve_bundle_pandoc(root: Path, filename: str = "pandoc.exe") -> Path:
    explicit_path = os.environ.get("CONSULTING_REPORT_PANDOC_EXE", "").strip()
    candidates: list[Path] = []
    if explicit_path:
        candidates.append(Path(explicit_path))

    repo_path = Path(filename)
    if not repo_path.is_absolute():
        repo_path = root / repo_path
    candidates.append(repo_path)

    system_pandoc = shutil.which("pandoc")
    if system_pandoc:
        candidates.append(Path(system_pandoc))

    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.expanduser()
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_file():
            return candidate

    raise FileNotFoundError(
        "缺少打包必需文件 pandoc.exe。请在项目根目录放置 pandoc.exe，"
        "或设置 CONSULTING_REPORT_PANDOC_EXE 指向 pandoc.exe，"
        "或在构建机安装 Pandoc 并确保 pandoc 在 PATH 中。"
    )


def require_non_empty_bundle_text_file(root: Path, filename: str) -> Path:
    file_path = Path(filename)
    if not file_path.is_absolute():
        file_path = root / file_path
    if not file_path.exists():
        raise FileNotFoundError(
            f"缺少打包必需文件 {filename}。请先在项目根目录放置该文件，再执行打包。"
        )

    if not file_path.read_text(encoding="utf-8").strip():
        raise ValueError(
            f"打包必需文件 {filename} 为空。请写入有效内容后再执行打包。"
        )

    return file_path


def validate_bundle_managed_client_token(
    root: Path,
    filename: str,
    models_url: str,
    *,
    timeout_seconds: int = 20,
) -> Path:
    token_path = require_non_empty_bundle_text_file(root, filename)
    token = token_path.read_text(encoding="utf-8").strip().lstrip("\ufeff")

    try:
        response = requests.get(
            models_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout_seconds,
        )
    except requests.RequestException as exc:
        raise ValueError(
            f"无法验证 {filename} 是否可用于默认通道：{exc}"
        ) from exc

    if response.status_code != 200:
        detail = response.text.strip()
        if len(detail) > 200:
            detail = detail[:200] + "..."
        raise ValueError(
            f"{filename} 未通过默认通道校验（状态码 {response.status_code}）：{detail}\n"
            "This file must contain the managed client token for /client, not the upstream API key."
        )

    return token_path


def validate_bundle_managed_search_pool(root: Path, filename: str) -> Path:
    pool_path = require_non_empty_bundle_text_file(root, filename)
    load_managed_search_pool_config_from_path(pool_path)
    return pool_path
