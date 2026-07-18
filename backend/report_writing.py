"""共享写正文工具的 invariant check + target resolve + text scanner.

Pure functions only. No ChatHandler dependency. Tests in tests/test_report_writing.py.
"""

from __future__ import annotations

from typing import Dict, Optional


def resolve_section_anchor(anchor: str, draft: str) -> Optional[str]:
    """Resolve an exact h2 anchor to the full section snapshot in draft."""
    if not anchor or not draft:
        return None

    anchor_lines = anchor.splitlines(keepends=True)
    if not anchor_lines:
        return None
    label = anchor_lines[0].rstrip("\r\n")
    if not label.startswith("## "):
        return None
    if not label[3:].strip():
        return None

    lines = draft.splitlines(keepends=True)
    matches = [
        index
        for index, line in enumerate(lines)
        if line.rstrip("\r\n") == label and line.startswith("## ")
    ]
    if len(matches) != 1:
        return None

    start = matches[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    return "".join(lines[start:end])


# ---- Pre-write invariant checks (per spec §3.1) ----

def check_report_writing_stage(
    skill_engine, project_id: str,
) -> Optional[str]:
    """Thin canonical-tool facade over the shared AI/user formal-content state gate."""
    return skill_engine.formal_content_write_block_guidance(project_id)


def check_outline_confirmed(
    skill_engine, project_id: str,
) -> Optional[str]:
    """outline_confirmed_at 必须 set."""
    project_path = skill_engine.get_project_path(project_id)
    if not project_path:
        return "项目不存在"
    checkpoints = skill_engine._load_stage_checkpoints(project_path)
    if "outline_confirmed_at" not in checkpoints:
        return "请先在工作区确认大纲，再发起正文写作"
    return None


MAX_CANONICAL_MUTATIONS_PER_TURN = 10


def check_no_prior_canonical_mutation_in_turn(
    turn_context: Dict,
) -> Optional[str]:
    """Block when this turn's canonical-draft mutations hit the per-turn cap."""
    mutations = turn_context.get("canonical_draft_mutations") or []
    if not isinstance(mutations, list):
        mutations = []
    if len(mutations) < MAX_CANONICAL_MUTATIONS_PER_TURN:
        return None

    summary_lines = []
    for index, mutation in enumerate(mutations):
        if not isinstance(mutation, dict):
            summary_lines.append(
                f"  {index + 1}. 无法解析的 mutation 记录 "
                f"({type(mutation).__name__})"
            )
            continue
        summary_lines.append(
            f"  {index + 1}. {mutation.get('canonical_action', '?')} "
            f"{mutation.get('target_label', '?')} "
            f"(old={mutation.get('old_len', 0)} -> new={mutation.get('new_len', 0)})"
        )
    summary = "\n".join(summary_lines) if summary_lines else "  (无可解析摘要)"
    return (
        f"本轮已经成功修改正文草稿 {len(mutations)} 次，达到上限 "
        f"{MAX_CANONICAL_MUTATIONS_PER_TURN}。\n"
        f"已完成的修改：\n{summary}\n"
        f"请等用户回应再做下一次修改。"
    )


def check_read_before_write_canonical_draft(
    turn_context: Dict,
    skill_engine,
    project_id: str,
    *,
    require_read: bool = True,
    stat_func=None,
) -> Optional[str]:
    """draft 已存在时本轮必须 read_file 过；mtime 变了要重读。

    `require_read=False` 用于 append_report_draft 首次起草（draft 不存在时跳过）。
    同一轮内若刚由本系统写入且文件 mtime 未再变化，则允许继续第二/第三次修改。
    """
    draft_path_normalized = skill_engine.REPORT_DRAFT_PATH
    project_path = skill_engine.get_project_path(project_id)
    if not project_path:
        return "项目不存在"
    actual_path = project_path / draft_path_normalized
    if not actual_path.exists():
        return None  # draft 不存在 → 首次起草场景，无需 read
    if not require_read:
        return None

    mutations = turn_context.get("canonical_draft_mutations") or []
    if isinstance(mutations, list) and mutations:
        last_self = mutations[-1]
        if isinstance(last_self, dict):
            last_self_mtime = last_self.get("mtime_after")
            if isinstance(last_self_mtime, (int, float)):
                try:
                    stat_result = (
                        stat_func(actual_path)
                        if stat_func is not None
                        else actual_path.stat()
                    )
                    current_mtime = stat_result.st_mtime
                except OSError:
                    current_mtime = None
                if (
                    isinstance(current_mtime, (int, float))
                    and abs(current_mtime - last_self_mtime) <= 1e-6
                ):
                    return None

    snapshots = turn_context.get("read_file_snapshots") or {}
    snap_mtime = snapshots.get(draft_path_normalized)
    if snap_mtime is None:
        return "请先 read_file 读取正文，再修改"
    if not isinstance(snap_mtime, (int, float)):
        # 非数值 snapshot → 视作无有效快照，按需重新 read_file。
        return "请先 read_file 读取正文，再修改"
    current_mtime = actual_path.stat().st_mtime
    if abs(current_mtime - snap_mtime) > 1e-6:
        return "草稿在你阅读后被修改，请先重新 read_file 再提交"
    return None


def check_no_fetch_url_pending(
    turn_context: Dict,
) -> Optional[str]:
    """web_search 后必须 fetch_url 才能落盘外部信息。"""
    if turn_context.get("web_search_performed") and not turn_context.get(
        "fetch_url_performed",
    ):
        return "请先 fetch_url 读取候选网页正文，再写正文"
    return None
