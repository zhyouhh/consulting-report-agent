from pathlib import Path
import re
import subprocess


def run_quality_check(file_path: str, script_path: str) -> dict:
    result = subprocess.run(
        ["powershell", "-ExecutionPolicy", "Bypass", "-File", script_path, "-FilePath", file_path],
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "status": "ok" if result.returncode == 0 else "error",
        "output": result.stdout or result.stderr,
    }


def export_reviewable_draft(file_path: str, output_dir: str, script_path: str) -> dict:
    result = subprocess.run(
        ["powershell", "-ExecutionPolicy", "Bypass", "-File", script_path, "-InputPath", file_path, "-OutputDir", output_dir],
        capture_output=True,
        text=True,
        check=False,
    )

    return {
        "status": "ok" if result.returncode == 0 else "error",
        "output": result.stdout or result.stderr,
        "output_path": _extract_output_path(result.stdout),
    }


def _extract_output_path(stdout: str) -> str:
    if not stdout:
        return ""

    match = re.search(r"已生成可审草稿:\s*(.+)", stdout)
    if not match:
        return ""

    return str(Path(match.group(1).strip()))
