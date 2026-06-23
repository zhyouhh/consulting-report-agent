import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import get_base_path


def _resolve_pandoc() -> str | None:
    """解析 pandoc 可执行：仅打包态/Windows 才优先包内 pandoc.exe（防 Linux 误 exec
    Windows 二进制——WINDOWS_BUILD.md 要求维护者把 pandoc.exe 放仓库根，而 get_base_path()
    开发/服务器态=仓库根），否则走系统 pandoc。"""
    if getattr(sys, "frozen", False) or sys.platform == "win32":
        base = get_base_path()
        for candidate in (base / "pandoc.exe", base / "pandoc" / "pandoc.exe"):
            if candidate.is_file():
                return str(candidate)
    system = shutil.which("pandoc")
    return system or None


def export_reviewable_draft(report_path: str, output_dir: str) -> dict:
    """把报告 markdown 用 pandoc 导出为可审 docx。原子发布：pandoc 写同目录唯一 temp.docx
    → 成功 os.replace 到终名；任一失败保留旧终名 + 清 temp。全程锁外（依赖 R3 原子写不变式）。"""
    pandoc = _resolve_pandoc()
    if not pandoc:
        return {
            "status": "error",
            "output": "未找到 pandoc：请在服务器安装 pandoc（Linux：apt install pandoc），或重装完整的桌面安装包。",
            "output_path": "",
            "filename": "",
        }

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    final_path = out_dir / (Path(report_path).stem + ".docx")

    fd, tmp_name = tempfile.mkstemp(dir=str(out_dir), suffix=".docx")
    os.close(fd)  # Windows 文件占用：pandoc 才能写该路径
    tmp_path = Path(tmp_name)
    try:
        result = subprocess.run(
            [pandoc, report_path, "-o", str(tmp_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            tmp_path.unlink(missing_ok=True)
            return {
                "status": "error",
                "output": result.stderr or result.stdout or "pandoc 导出失败，未生成可审草稿。",
                "output_path": "",
                "filename": "",
            }
        os.replace(tmp_path, final_path)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        return {
            "status": "error",
            "output": f"导出失败：{exc}",
            "output_path": "",
            "filename": "",
        }

    return {
        "status": "ok",
        "output": f"已生成可审草稿: {final_path}\n说明: 当前产物用于预审和传阅，不替代最终中文排版。",
        "output_path": str(final_path),
        "filename": final_path.name,
    }
