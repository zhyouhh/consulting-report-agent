"""共享写正文工具的 invariant check + target resolve + text scanner.

Pure functions only. No ChatHandler dependency. Tests in tests/test_report_writing.py.
"""

from __future__ import annotations

import re
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


# ---- User message intent detection (spec §3.5) ----

_GENERATIVE_PATTERNS = [
    re.compile(r"起草"),
    re.compile(r"续写"),
    re.compile(r"开始写.{0,8}(?:报告|正文)"),
    re.compile(r"开始起草"),
    re.compile(r"写下一[章节段]"),
    re.compile(r"继续写"),
    re.compile(r"帮我写(?!得)"),
    re.compile(r"写完(第|下一)"),
]

_SECTION_MENTION_PATTERN = r"第[一二三四五六七八九十百千万0-9]+(?:章|节|部分)"

_FULL_REWRITE_PATTERNS = [
    re.compile(r"(?:全文|整篇|全部).{0,12}(?:重写|改写)"),
    re.compile(r"(?:重写|改写).{0,12}(?:全文|整篇|全部|这份报告|报告正文|正文)"),
    re.compile(r"推倒(?:重来|重写)"),
]

_MODIFY_PATTERNS = [
    re.compile(r"把.+改(成|为)"),
    re.compile(
        rf"(?:{_SECTION_MENTION_PATTERN}.{{0,40}}(?:重写|改写|重做)|"
        rf"(?:重写|改写|重做).{{0,20}}{_SECTION_MENTION_PATTERN})"
    ),
    *_FULL_REWRITE_PATTERNS,
    re.compile(
        rf"{_SECTION_MENTION_PATTERN}.{{0,40}}"
        r"(?:改强|补强|加强|改好|改弱)"
    ),
    re.compile(r"替换"),
    re.compile(r"修改"),
    re.compile(r"删[掉除]"),
    re.compile(r"调整"),
    re.compile(r"(?:润色|优化)"),
    re.compile(r"(?:请|帮我).{0,20}写得.{0,20}(?:顺|好|自然|清楚|清晰|简洁|通顺|顺畅)"),
    re.compile(
        r"写得.{0,20}(?:"
        r"更(?:顺|好|自然|清楚|清晰|简洁|通顺|顺畅)|"
        r"(?:顺|好|自然|清楚|清晰|简洁|通顺|顺畅).{0,6}(?:一点|一些|些|一下)"
        r")"
    ),
]

_DRAFT_ACTION_PATTERN = (
    r"(?:继续写|写下一[章节段]|写完(?:第|下一)?[章节段]?|帮我写(?!得)|"
    r"起草|续写|修改|调整|润色|优化|替换|推倒重来|推倒重写|重写|改写|"
    r"重做|改强|补强|加强|删[掉除]?|改)"
)

_NEGATION_CORE_PATTERN = r"(?:不用|不要|不必|不需要|无需|别|不是|不想|并非)"
_NEGATION_PREFIX_PATTERN = rf"(?:先{_NEGATION_CORE_PATTERN}|先不|不再|{_NEGATION_CORE_PATTERN})"
_NEGATION_SUFFIX_PATTERN = rf"(?:就)?{_NEGATION_PREFIX_PATTERN}"
_SAME_CLAUSE_TEXT = r"[^，,。！？!?；;\n]{0,8}"
_NEGATION_TO_ACTION_TEXT = r"[^，,。！？!?；;\n]{0,80}"
_REPLACE_VERB_PATTERN = r"(?:改成|改为|替换成|替换为|换成)"
_REPLACEMENT_CLAUSE_TEXT = r"[^，,。！？!?；;\n]{1,80}"

_NEGATED_ACTION_PATTERNS = [
    re.compile(
        rf"{_NEGATION_PREFIX_PATTERN}"
        rf"(?:再)?"
        rf"把{_REPLACEMENT_CLAUSE_TEXT}?{_REPLACE_VERB_PATTERN}"
        rf"{_REPLACEMENT_CLAUSE_TEXT}"
    ),
    re.compile(
        rf"把{_REPLACEMENT_CLAUSE_TEXT}?{_REPLACE_VERB_PATTERN}"
        rf"{_REPLACEMENT_CLAUSE_TEXT}?"
        rf"{_NEGATION_SUFFIX_PATTERN}了?"
    ),
    re.compile(
        rf"{_NEGATION_PREFIX_PATTERN}"
        rf"{_NEGATION_TO_ACTION_TEXT}"
        rf"{_DRAFT_ACTION_PATTERN}"
    ),
    re.compile(
        rf"{_DRAFT_ACTION_PATTERN}"
        rf"{_SAME_CLAUSE_TEXT}"
        rf"{_NEGATION_SUFFIX_PATTERN}了?"
    ),
]


def _remove_negated_action_clauses(user_message: str) -> tuple[str, bool]:
    negated_remaining = user_message
    negated_found = False
    for p in _NEGATED_ACTION_PATTERNS:
        negated_remaining, count = p.subn("", negated_remaining)
        negated_found = negated_found or count > 0
    return negated_remaining, negated_found


def detect_user_message_intent(user_message: str) -> str:
    """Lightweight keyword-based intent classifier for canonical draft writes.

    Returns:
        "generative" — 起草 / 续写 / 写下一章 / 继续写 / 帮我写 等新增类语义
        "modify"     — 把.改成 / 重写第N章 / 替换 / 修改 / 删掉 / 调整 等修改类语义
        "ambiguous"  — 其他（不阻拦工具调用，仅 turn-end retry 不触发）

    Generative 比 modify 优先（同时含 "续写第二章并替换..." 罕见，按 generative 处理）。
    """
    if not user_message:
        return "ambiguous"

    negated_remaining, negated_found = _remove_negated_action_clauses(user_message)
    if negated_found:
        for p in _GENERATIVE_PATTERNS:
            if p.search(negated_remaining):
                return "generative"
        for p in _MODIFY_PATTERNS:
            if p.search(negated_remaining):
                return "modify"
        return "ambiguous"

    for p in _GENERATIVE_PATTERNS:
        if p.search(user_message):
            return "generative"
    for p in _MODIFY_PATTERNS:
        if p.search(user_message):
            return "modify"
    return "ambiguous"


def user_message_requests_full_rewrite(user_message: str) -> bool:
    """Return whether the user explicitly requested a full draft rewrite."""
    if not user_message:
        return False
    remaining, _ = _remove_negated_action_clauses(user_message)
    if not any(p.search(remaining) for p in _FULL_REWRITE_PATTERNS):
        return False
    return detect_user_message_intent(user_message) == "modify"
