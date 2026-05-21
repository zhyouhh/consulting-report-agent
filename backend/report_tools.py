from pathlib import Path
import re
import subprocess
import threading

from .skill import SkillEngine


LINT_REPORT_COMPLETION_MARKER = SkillEngine.LINT_REPORT_COMPLETION_MARKER
LINT_REPORT_ANCHORS = SkillEngine.LINT_REPORT_ANCHORS


def _run_powershell(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def run_quality_check(file_path: str, script_path: str) -> dict:
    result = _run_powershell(["-File", script_path, "-FilePath", file_path])
    return {
        "status": "ok" if result.returncode == 0 else "error",
        "output": result.stdout or result.stderr,
    }


def run_lint_report(
    report_path: str,
    output_path: str,
    script_path: str,
    dry_run: bool = False,
) -> dict:
    args = ["-File", script_path, "-FilePath", report_path, "-OutputPath", output_path]
    if dry_run:
        args.append("-DryRun")
    result = _run_powershell(args)
    if result.returncode != 0:
        return {"status": "error", "detail": result.stderr or result.stdout}
    if not dry_run and not _validate_lint_report_output(output_path):
        return {"status": "error", "detail": "脚本完成但输出未通过校验"}
    summary = _parse_lint_summary(output_path) if not dry_run else {}
    return {"status": "ok", "path": output_path, "summary": summary}


def _validate_lint_report_output(output_path: str) -> bool:
    path = Path(output_path)
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return False
    if LINT_REPORT_COMPLETION_MARKER not in text:
        return False
    return all(anchor in text for anchor in LINT_REPORT_ANCHORS)


def _parse_lint_summary(output_path: str) -> dict:
    try:
        text = Path(output_path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return {}

    def _extract(pattern: str) -> int:
        match = re.search(pattern, text)
        if not match:
            return 0
        return int(match.group(1))

    return {
        "ai_style": _extract(r"-\s*AI 腔：(\d+)\s*处"),
        "content_gap": _extract(r"-\s*内容缺失：(\d+)\s*处"),
        "citation_gap": _extract(r"-\s*缺标注：(\d+)\s*处"),
        "section_so_what_low": _extract(r"-\s*章节 So What 偏少：(\d+)\s*章"),
        "estimated_minutes": _extract(r"预估改完所需时间\*\*：约\s*(\d+)\s*分钟"),
    }


def export_reviewable_draft(file_path: str, output_dir: str, script_path: str) -> dict:
    result = _run_powershell(["-File", script_path, "-InputPath", file_path, "-OutputDir", output_dir])

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


_LINT_REPORT_LOCKS: dict[str, threading.Lock] = {}
_LINT_REPORT_LOCKS_GUARD = threading.Lock()


def get_lint_report_lock(project_id: str) -> threading.Lock:
    with _LINT_REPORT_LOCKS_GUARD:
        if project_id not in _LINT_REPORT_LOCKS:
            _LINT_REPORT_LOCKS[project_id] = threading.Lock()
        return _LINT_REPORT_LOCKS[project_id]
