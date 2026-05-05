"""共享写正文工具的 invariant check + target resolve + text scanner.

Pure functions only. No ChatHandler dependency. Tests in tests/test_report_writing.py.
"""

from __future__ import annotations

import re
from typing import Dict, Optional


# ---- Section target resolve (迁移自 fix4 fix2 的 _preflight_resolve_section_target) ----

_SECTION_PREFIX_RE = re.compile(
    r"第([一二三四五六七八九十百千万0-9]+)(?:章(?!节)|节(?!章)|部分)"
)


def resolve_section_target(
    user_message: str,
    draft_text: str,
    extract_markdown_heading_nodes,
) -> Optional[Dict[str, str]]:
    """user_message 中抽章节数字前缀，prefix-match draft heading.

    返回 {label, snapshot} 当且仅当 user_message 含 '第N章/节/部分' 这类前缀且
    draft heading 中**所有 prefix 都恰好唯一定位到同一个 heading**；否则返回 None。
    任意 prefix 0/多 candidate → fail-fast None（per fix4-fix2 Bug 7）。

    `extract_markdown_heading_nodes` 注入：避免 backend.chat 循环依赖；callers 传入
    `ChatHandler._extract_markdown_heading_nodes`。
    """
    if not user_message or not draft_text:
        return None
    matches = list(_SECTION_PREFIX_RE.finditer(user_message))
    if not matches:
        return None
    heading_nodes = extract_markdown_heading_nodes(draft_text)
    if not heading_nodes:
        return None

    resolved = []
    for prefix_match in matches:
        prefix = prefix_match.group(0)
        candidates = [
            node for node in heading_nodes
            if isinstance(node, dict)
            and isinstance(node.get("label"), str)
            and str(node.get("label")).startswith(prefix)
        ]
        if len(candidates) != 1:
            return None  # fail-fast on any non-unique prefix
        resolved.append(candidates[0])

    unique_keys = set()
    for n in resolved:
        s = n.get("start", -1)
        e = n.get("end", -1)
        if not isinstance(s, (int, float)) or not isinstance(e, (int, float)):
            return None  # malformed heading node — fail-closed
        unique_keys.add((int(s), int(e)))
    if len(unique_keys) != 1:
        return None  # multi-prefix resolving to different headings → ambiguous

    node = resolved[0]
    label = str(node.get("label") or "")
    snapshot = str(node.get("section_snapshot") or "")
    if not label or not snapshot:
        return None
    return {"label": label, "snapshot": snapshot}


# ---- Assistant text claim detection (per spec §3.5 §7.6) ----

_TEXT_CLAIM_RE_1 = re.compile(
    r"(?:已|已经|完成|写完|改完|重写完|替换完|同步|落盘)"
    r"[^。！？!?\n]{0,30}"
    r"(?:正文|草稿|报告|章节|第[一二三四五六七八九十0-9]+章|content/report_draft_v1\.md)"
)
_TEXT_CLAIM_RE_2 = re.compile(
    r"(?:正文|草稿|报告|章节|第[一二三四五六七八九十0-9]+章)"
    r"[^。！？!?\n]{0,30}"
    r"(?:已|已经|完成|写完|改完|重写完|替换完|同步|落盘)"
)


def assistant_text_claims_modification(text: str) -> bool:
    """启发式判定 assistant_message 是否声称已修改正文（vs 仅意图陈述）。

    True 表示文本含"已完成"标志 + 正文相关名词。
    False 表示仅含"我会/我将"等意图陈述 OR 完全不含相关词。

    用法：spec §3.5 turn-end 对账。如果 obligation 存在 + 没 mutation +
    text claim → 触发 retry corrective message。
    """
    has_claim = bool(_TEXT_CLAIM_RE_1.search(text) or _TEXT_CLAIM_RE_2.search(text))
    if has_claim:
        return True
    # 无声称 → 不撒谎
    return False


# ---- Pre-write invariant checks (per spec §3.1) ----

def check_report_writing_stage(
    skill_engine, project_id: str,
) -> Optional[str]:
    """阶段必须 S4-S7 才能写正文。返回 None=ok，str=error message."""
    project_path = skill_engine.get_project_path(project_id)
    if not project_path:
        return "项目不存在"
    stage_state = skill_engine._infer_stage_state(project_path)
    stage_code = stage_state.get("stage_code", "S0")
    if stage_code not in {"S4", "S5", "S6", "S7"}:
        return f"本工具仅在 S4 阶段及之后可用。当前阶段：{stage_code}"
    return None


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


def check_no_mixed_intent_in_turn(
    handler, user_message: str,
) -> Optional[str]:
    """复用现有 _secondary_action_families_in_message 逻辑：本轮 secondary
    action 数 ≤ 1（即只能含一个写正文 family + 至多一个 secondary action）。

    `handler` 注入 ChatHandler 实例避免循环 import。
    """
    secondary = handler._secondary_action_families_in_message(user_message)
    if len(secondary) > 1:
        return (
            "请把多个动作拆成多个回合分别处理（如先重写章节，再单独发起导出/质量检查/查看字数）"
        )
    return None


def check_no_prior_canonical_mutation_in_turn(
    turn_context: Dict,
) -> Optional[str]:
    """一轮一次 canonical mutation 限制（spec §3.6）."""
    if turn_context.get("canonical_draft_mutation"):
        return "本轮已经修改过正文草稿一次，请等用户回应再做下一次修改"
    return None


def check_read_before_write_canonical_draft(
    turn_context: Dict,
    skill_engine,
    project_id: str,
    *,
    require_read: bool = True,
) -> Optional[str]:
    """draft 已存在时本轮必须 read_file 过；mtime 变了要重读。

    `require_read=False` 用于 append_report_draft 首次起草（draft 不存在时跳过）。
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
