from pathlib import Path


def require_non_empty_bundle_text_file(root: Path, filename: str) -> Path:
    file_path = root / filename
    if not file_path.exists():
        raise FileNotFoundError(
            f"缺少打包必需文件 {filename}。请先在项目根目录放置该文件，再执行打包。"
        )

    if not file_path.read_text(encoding="utf-8").strip():
        raise ValueError(
            f"打包必需文件 {filename} 为空。请写入有效内容后再执行打包。"
        )

    return file_path
