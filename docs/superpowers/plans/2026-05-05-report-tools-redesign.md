# Report Writing Tool Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fix4 v5 amendment 的 `<draft-action>` tag + gate fallback + scope enforcement 整套机制为 4 个专用写正文工具（`append_report_draft` 重构 + `rewrite_report_section` / `replace_report_text` / `rewrite_report_draft` 新增），结构性消除 model 必须精确复述 1500 字 old_string 这个 fix4 cutover 14 次失败的根因。

**Architecture:** 工具调用本身是意图声明，schema 强约束；后端用 preflight 已 resolve 的 `rewrite_target_snapshot` 自己控制 `old_string`，model 只给 new content。三层系统侧保护（write-obligation / mutation limit / read-mtime tracking）保留但简化。`<stage-ack>` tag 系统不动。

**Tech Stack:** Python 3.11/3.12 backend + unittest/pytest + 现有 OpenAI tool calling 协议；frontend 不需改动。

**Spec reference:** `docs/superpowers/specs/2026-05-05-report-tools-redesign-design.md` (HEAD `7f0d207`，4 轮 codex review APPROVED_WITH_NOTES)

---

## File Structure

### New files (created)

| Path | Purpose |
|---|---|
| `backend/report_writing.py` | 共享 helpers + `resolve_section_target` + `assistant_text_claims_modification` + 各工具 inline check 函数 |
| `tests/test_report_writing.py` | 上述 helpers 单测（不依赖 ChatHandler） |

### Modified files

| Path | Change |
|---|---|
| `backend/chat.py` | 加 turn_context 新字段；加 4 个工具实现 + schema 注册 + dispatch；加 `_detect_canonical_draft_write_obligation` + `_chat_*_unlocked` no-tool-call retry；加 read_file mtime hook；删 `<draft-action>` 系列 + gate + preflight + classifier 大段代码 |
| `tests/test_chat_runtime.py` | 加 4 ToolTests + WriteObligation/MutationLimit/ReadBeforeWrite/StageAck 端到端 test class；删 DraftActionPreCheck / GateCanonicalDraftToolCall / DraftDecisionCompareEvent / 大部分 PreflightCheck |
| `skill/SKILL.md` | §S4 重写为新工具表格；删附录"draft-action 标签规范"；改 user-facing reject messages 中的 `<draft-action>` 残留 |

### Deleted files

| Path | Reason |
|---|---|
| `backend/draft_action.py` | tag parser module，无用 |
| `tests/test_draft_action.py` | 整个文件 92 行 |
| `tests/test_draft_decision_compare_report.py` | compare 不再有意义 |
| `tools/draft_decision_compare_report.py` | compare 不再有意义 |

### Commit map（per spec §6.4）

| Commit | Task | 风险 |
|---|---|---|
| 1 | Task 1: report_writing.py module 加 helpers + test | 0 — 纯加法 |
| 2 | Task 2: turn_context 新字段 + obligation detector + mtime hook | 0 — 纯加法 |
| 3 | Task 3: 4 个工具实现 + schema + dispatch + ToolTests | 0 — 旧路径仍 work |
| 4 | Task 4: SKILL.md + user-facing reject message wording | 0 — 文档改动 |
| 5 | Task 5: 删旧 code + 测试（最大删除） | 中 — 前 4 commit 已经替代功能 |
| 6 | Task 6: cutover smoke artifacts + worklist/memory/handoff 更新 | 0 — 文档 |

**PR boundary**：commit 5/6 之间是天然 split 点。如果实施时希望分两个 PR：
- PR 1 = commit 1-4 (B-1，加工具但不删旧；可独立 ship 验证 model 学会用新工具)
- PR 2 = commit 5-6 (B-2，删旧 + 文档更新)

---

## Task 1: 加 `backend/report_writing.py` module + helpers

**Goal**: 创建共享 helpers module 含所有写工具入口需要的 check 函数 + target resolve + claim detector。**纯加法**，不破坏现有路径。

**Files:**
- Create: `backend/report_writing.py`
- Create: `tests/test_report_writing.py`

### Task 1.1 — helpers module skeleton

- [ ] **Step 1: 创建空 module**

```python
# backend/report_writing.py
"""共享写正文工具的 invariant check + target resolve + text scanner.

Pure functions only. No ChatHandler dependency. Tests in tests/test_report_writing.py.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional


# ---- Section target resolve (迁移自 fix4 fix2 的 _preflight_resolve_section_target) ----

_SECTION_PREFIX_RE = re.compile(
    r"第([一二三四五六七八九十百千万0-9]+)(?:章(?!节)|节(?!章)|部分)"
)
```

- [ ] **Step 2: 加 `resolve_section_target` 函数（迁移 fix4 fix2 含 partial-multi-prefix fail-fast 完整逻辑）**

```python
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

    unique_keys = {
        (int(n.get("start", -1)), int(n.get("end", -1))) for n in resolved
    }
    if len(unique_keys) != 1:
        return None  # multi-prefix resolving to different headings → ambiguous

    node = resolved[0]
    label = str(node.get("label") or "")
    snapshot = str(node.get("section_snapshot") or "")
    if not label or not snapshot:
        return None
    return {"label": label, "snapshot": snapshot}
```

- [ ] **Step 3: 写 `tests/test_report_writing.py` skeleton + section target 单测**

```python
# tests/test_report_writing.py
import unittest
from backend.report_writing import resolve_section_target


def _fake_heading_nodes(items):
    """items: list[(label, snapshot, start, end)]"""
    return [
        {"label": label, "snapshot": snap, "start": s, "end": e, "section_snapshot": snap}
        for label, snap, s, e in items
    ]


class ResolveSectionTargetTests(unittest.TestCase):
    def setUp(self):
        self.draft = "# 报告\n## 第一章 引言\n内容0\n## 第二章 战力分析\n内容B\n## 第三章 总结\n内容C\n"
        self.nodes = _fake_heading_nodes([
            ("第一章 引言", "## 第一章 引言\n内容0", 5, 25),
            ("第二章 战力分析", "## 第二章 战力分析\n内容B", 25, 50),
            ("第三章 总结", "## 第三章 总结\n内容C", 50, 75),
        ])

    def test_unique_prefix_returns_target(self):
        result = resolve_section_target(
            "重写第二章", self.draft,
            extract_markdown_heading_nodes=lambda _: self.nodes,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["label"], "第二章 战力分析")

    def test_zero_candidates_returns_none(self):
        result = resolve_section_target(
            "重写第四章", self.draft,
            extract_markdown_heading_nodes=lambda _: self.nodes,
        )
        self.assertIsNone(result)

    def test_partial_multi_prefix_fail_fast(self):
        # 第二章 unique，第四章 not in draft → fail-fast
        result = resolve_section_target(
            "把第二章和第四章重写", self.draft,
            extract_markdown_heading_nodes=lambda _: self.nodes,
        )
        self.assertIsNone(result)

    def test_multi_prefix_distinct_targets_returns_none(self):
        # 两个 prefix 都 unique 但指向不同 heading
        result = resolve_section_target(
            "把第二章和第三章重写", self.draft,
            extract_markdown_heading_nodes=lambda _: self.nodes,
        )
        self.assertIsNone(result)

    def test_multi_prefix_same_target_returns_target(self):
        # 重复 prefix 都指向同一个 heading
        result = resolve_section_target(
            "第二章再说第二章", self.draft,
            extract_markdown_heading_nodes=lambda _: self.nodes,
        )
        self.assertIsNotNone(result)

    def test_section_node_compound_excluded(self):
        # 第二章节 不应匹配 第二章
        result = resolve_section_target(
            "改第二章节", "# 报告\n## 第二章 X\n内容\n",
            extract_markdown_heading_nodes=lambda _: _fake_heading_nodes(
                [("第二章 X", "## 第二章 X\n内容", 5, 30)],
            ),
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 4: Run tests to verify pass**

Run: `D:\MyProject\CodeProject\consulting-report-agent\.venv\Scripts\python.exe -m pytest tests/test_report_writing.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/report_writing.py tests/test_report_writing.py
git commit -m "feat(report_writing): add helpers module skeleton + resolve_section_target

迁移 fix4-fix2 的 _preflight_resolve_section_target 逻辑到独立 module
（含 partial-multi-prefix fail-fast / negative-lookahead 防 第二章节 overmatch /
multi-prefix 同 heading dedup）。Pure function，无 ChatHandler 依赖，方便测试。

Spec §3.1 / §7.3 / §10 Q5 inline-migrate 决议。"
```

### Task 1.2 — claim detector

- [ ] **Step 1: 加 `assistant_text_claims_modification` regex + function**

```python
# 追加到 backend/report_writing.py 末尾

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
_INTENT_RE = re.compile(r"我会|我准备|我将|我正在|我可以|让我")


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
```

- [ ] **Step 2: 加单测**

```python
# 追加到 tests/test_report_writing.py

from backend.report_writing import assistant_text_claims_modification


class AssistantTextClaimsModificationTests(unittest.TestCase):
    def test_explicit_completion_returns_true(self):
        self.assertTrue(assistant_text_claims_modification(
            "我已经把第二章重写完毕，请查看。",
        ))
        self.assertTrue(assistant_text_claims_modification(
            "正文已同步更新到 content/report_draft_v1.md。",
        ))
        self.assertTrue(assistant_text_claims_modification(
            "草稿完成第三章的扩写。",
        ))

    def test_intent_only_returns_false(self):
        self.assertFalse(assistant_text_claims_modification(
            "我会重写第二章，请稍等。",
        ))
        self.assertFalse(assistant_text_claims_modification(
            "我准备开始起草正文。",
        ))

    def test_unrelated_text_returns_false(self):
        self.assertFalse(assistant_text_claims_modification(
            "我不太确定这块怎么处理。",
        ))

    def test_intent_plus_completion_returns_true(self):
        # "我会修改" + "已完成" 混合 — 仍按完成处理（model 在文本里同时混合时算撒谎风险）
        self.assertTrue(assistant_text_claims_modification(
            "我会重写第二章，已经完成了起草。",
        ))
```

- [ ] **Step 3: Run tests**

Run: `D:\MyProject\CodeProject\consulting-report-agent\.venv\Scripts\python.exe -m pytest tests/test_report_writing.py::AssistantTextClaimsModificationTests -v`
Expected: 4 PASS

- [ ] **Step 4: Commit**

```bash
git add backend/report_writing.py tests/test_report_writing.py
git commit -m "feat(report_writing): add assistant_text_claims_modification heuristic

Spec §3.5 §7.6: detect whether assistant_message claims modification
(completion keywords near draft/section terms) vs intent-only statements.
Used by turn-end obligation reconciliation in chat loop layer.

正反 case 单测 cover 4 个：完成声明 / 意图陈述 / 无关 / 混合"
```

### Task 1.3 — 共享 invariant check helpers

- [ ] **Step 1: 加 5 个 check helpers 到 module**

```python
# 追加到 backend/report_writing.py 末尾

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
```

- [ ] **Step 2: 加 helper 单测（cover 各 reject 路径 + happy path）**

```python
# 追加到 tests/test_report_writing.py

from backend.report_writing import (
    check_report_writing_stage, check_outline_confirmed,
    check_no_mixed_intent_in_turn, check_no_prior_canonical_mutation_in_turn,
    check_read_before_write_canonical_draft, check_no_fetch_url_pending,
)


class CheckHelpersTests(unittest.TestCase):
    def test_check_no_prior_canonical_mutation_in_turn_pass(self):
        self.assertIsNone(check_no_prior_canonical_mutation_in_turn({}))
        self.assertIsNone(check_no_prior_canonical_mutation_in_turn(
            {"canonical_draft_mutation": None},
        ))

    def test_check_no_prior_canonical_mutation_in_turn_reject(self):
        msg = check_no_prior_canonical_mutation_in_turn(
            {"canonical_draft_mutation": {"tool": "rewrite_report_section"}},
        )
        self.assertIsNotNone(msg)
        self.assertIn("本轮已经修改过", msg)

    def test_check_no_fetch_url_pending_no_search_pass(self):
        self.assertIsNone(check_no_fetch_url_pending({}))

    def test_check_no_fetch_url_pending_search_no_fetch_reject(self):
        msg = check_no_fetch_url_pending(
            {"web_search_performed": True, "fetch_url_performed": False},
        )
        self.assertIsNotNone(msg)
        self.assertIn("fetch_url", msg)

    def test_check_no_fetch_url_pending_both_pass(self):
        self.assertIsNone(check_no_fetch_url_pending(
            {"web_search_performed": True, "fetch_url_performed": True},
        ))
```

(其他 helper 因依赖 `skill_engine` mock，迁移到 Task 3 端到端测试中 cover；这里只单测 pure dict-based 两个。)

- [ ] **Step 3: Run tests**

Run: `D:\MyProject\CodeProject\consulting-report-agent\.venv\Scripts\python.exe -m pytest tests/test_report_writing.py -v`
Expected: 11 PASS (5 ResolveSectionTarget + 4 ClaimDetector + 5 Helpers - wait 6+4+5=15 actually, let me recount: 6 resolve + 4 claim + 5 helpers = 15 PASS)

- [ ] **Step 4: Commit**

```bash
git add backend/report_writing.py tests/test_report_writing.py
git commit -m "feat(report_writing): add 6 shared invariant check helpers

Spec §3.1 §3.6 §3.7: stage / outline / mixed-intent / mutation-limit /
read-before-write+mtime / fetch_url-pending checks. Pure helpers used by
all 4 write tools' entry SHARED_PRE_WRITE_CHECKS.

mutation-limit + fetch_url-pending 单测 cover (skill_engine mock 依赖的
helpers 移到 Task 3 端到端测试 cover)。"
```

---

## Task 2: turn_context 新字段 + obligation detector + read_file mtime hook

**Goal**: 加 3 个新 turn_context 字段 + write-obligation detector + read_file 完成 hook，**不破坏现有路径**。

**Files:**
- Modify: `backend/chat.py` (`_new_turn_context` + `_build_turn_context` + `_execute_tool` read_file branch + 加 `_detect_canonical_draft_write_obligation`)
- Modify: `tests/test_chat_runtime.py` (新加 ChatRuntime test class)

### Task 2.1 — 加 turn_context 新字段 default

- [ ] **Step 1: 找到 `_new_turn_context` 函数（chat.py 约 6746 行）+ 加 3 字段**

```python
# backend/chat.py:_new_turn_context body, append to dict:

    "user_message_text": "",                  # spec §3.3 NEW
    "canonical_draft_write_obligation": None, # spec §3.5 NEW
    "read_file_snapshots": {},                # spec §3.7 NEW
```

- [ ] **Step 2: 写测试 verify default**

```python
# 加到 tests/test_chat_runtime.py 找一个合适的 test class（如 TurnContextDefaultsTests 或新建）

class NewTurnContextFieldsTests(ChatRuntimeTests):
    def test_new_turn_context_has_user_message_text(self):
        handler = self._make_handler_with_project()
        ctx = handler._new_turn_context(can_write_non_plan=True)
        self.assertEqual(ctx.get("user_message_text"), "")

    def test_new_turn_context_has_obligation_default_none(self):
        handler = self._make_handler_with_project()
        ctx = handler._new_turn_context(can_write_non_plan=True)
        self.assertIsNone(ctx.get("canonical_draft_write_obligation"))

    def test_new_turn_context_has_read_file_snapshots_empty_dict(self):
        handler = self._make_handler_with_project()
        ctx = handler._new_turn_context(can_write_non_plan=True)
        self.assertEqual(ctx.get("read_file_snapshots"), {})
```

- [ ] **Step 3: Run tests**

Run: `D:\MyProject\CodeProject\consulting-report-agent\.venv\Scripts\python.exe -m pytest tests/test_chat_runtime.py::NewTurnContextFieldsTests -v`
Expected: 3 PASS

- [ ] **Step 4: Commit**

```bash
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "feat(chat): add 3 new turn_context fields for tool redesign

Spec §3.3: user_message_text / canonical_draft_write_obligation /
read_file_snapshots 默认初始化在 _new_turn_context。这一 commit 仅加字段
默认，不修改任何 callsite — 保证现有路径行为不变。"
```

### Task 2.2 — `_build_turn_context` cache `user_message_text`

- [ ] **Step 1: 修改 `_build_turn_context`（chat.py 约 6790 行附近）**

```python
# 在 _build_turn_context 函数内，靠前位置（user_message 处理早期）：

self._turn_context["user_message_text"] = self._extract_user_message_text(
    {"role": "user", "content": user_message}
)
```

(如果 caller 已经传 message dict 而不是 string，调整 extraction 形式即可。)

- [ ] **Step 2: 写测试**

```python
class BuildTurnContextCachesUserMessageTests(ChatRuntimeTests):
    def test_build_turn_context_caches_user_message_text(self):
        handler = self._make_handler_with_project()
        handler._build_turn_context(self.project_id, "把第二章重写一下")
        self.assertEqual(
            handler._turn_context.get("user_message_text"),
            "把第二章重写一下",
        )
```

- [ ] **Step 3: Run tests + commit**

Run: `pytest -v -k BuildTurnContextCachesUserMessage`
Expected: PASS

```bash
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "feat(chat): cache user_message_text in turn_context

Spec §3.3: 用 _extract_user_message_text 取 raw user msg 缓存到
turn_context.user_message_text，新工具入口直接读这个字段做 keyword 检测
+ resolve_section_target，避免重复 traverse current_user_message 结构。"
```

### Task 2.3 — `_detect_canonical_draft_write_obligation`

- [ ] **Step 1: 加 detector 函数到 chat.py（建议放在 `_classify_canonical_draft_turn` 附近，但作为独立函数；或放进 `backend/report_writing.py` 然后 chat.py import）**

放进 `backend/report_writing.py`（更干净）：

```python
# 追加到 backend/report_writing.py

# ---- Write obligation detection (spec §3.5) ----

# 复用现有 chat.py 的 keyword 列表作为粗粒度 yes/no 信号
# (per spec r2 reviewer §C12: 不迁移 stage gate / scope / target / priority logic)

_OBLIGATION_KEYWORDS = (
    # begin / continue
    "开始写报告", "开始写正文", "开始起草", "起草报告", "写第一版",
    "继续写", "继续写报告", "继续写正文", "接着写", "写下一章", "写下一段",
    # section
    "重写", "改写", "重做",
    # whole rewrite
    "整篇重写", "全文重写", "推倒重写", "推倒重来", "全部改写",
)

# replace 用现有 RE 字面（避免 import chat.py 带循环依赖）
_OBLIGATION_REPLACE_RE = re.compile(
    r"把(?:报告|正文)(?:里的|中的|里|中)?"
    r"[^，,、。！？!?；;：:\n]{1,80}?"
    r"\s*[，,、：:]?\s*"
    r"(?:改成|改为|替换成|换成)"
)


def detect_canonical_draft_write_obligation(user_message: str) -> Optional[Dict[str, Any]]:
    """粗粒度判定 user 消息是否触发"本轮要写正文"信号。返回 None=无信号；
    返回 dict={'tool_family': str, 'detected': str} 表示有 obligation。

    `tool_family` 仅供事件记录用，不驱动 gate / scope enforcement。
    """
    text = (user_message or "").strip()
    if not text:
        return None

    # phrase hits 顺序：begin → continue → section → rewrite_draft → replace
    # （保留原 fix4 的 dict 顺序优先级，但只输出粗 family，不细分 priority）
    for kw in ("开始写报告", "开始写正文", "开始起草", "起草报告", "写第一版"):
        if kw in text:
            return {"tool_family": "begin", "detected": kw}
    for kw in ("继续写", "继续写报告", "继续写正文", "接着写", "写下一章", "写下一段"):
        if kw in text:
            return {"tool_family": "continue", "detected": kw}
    for kw in ("整篇重写", "全文重写", "推倒重写", "推倒重来", "全部改写"):
        if kw in text:
            return {"tool_family": "rewrite_draft", "detected": kw}
    for kw in ("重写", "改写", "重做"):
        if kw in text:
            return {"tool_family": "rewrite_section", "detected": kw}
    if _OBLIGATION_REPLACE_RE.search(text):
        return {"tool_family": "replace_text", "detected": "replace_pattern"}
    return None
```

- [ ] **Step 2: 加 detector 单测（10 messages 覆盖 spec §7.8 reviewer 建议 benchmark suite）**

```python
# 追加到 tests/test_report_writing.py

from backend.report_writing import detect_canonical_draft_write_obligation


class DetectWriteObligationTests(unittest.TestCase):
    def test_begin(self):
        d = detect_canonical_draft_write_obligation("开始写报告正文")
        self.assertEqual(d["tool_family"], "begin")

    def test_continue(self):
        d = detect_canonical_draft_write_obligation("继续写下一章")
        self.assertEqual(d["tool_family"], "continue")

    def test_section_rewrite_explicit(self):
        d = detect_canonical_draft_write_obligation("请把第二章重写一下")
        self.assertEqual(d["tool_family"], "rewrite_section")

    def test_section_rewrite_multi(self):
        d = detect_canonical_draft_write_obligation("重写第二章和第三章")
        self.assertEqual(d["tool_family"], "rewrite_section")

    def test_replace_text_quoted(self):
        d = detect_canonical_draft_write_obligation("把正文里的'渠道效率'改成'渠道质量'")
        self.assertEqual(d["tool_family"], "replace_text")

    def test_replace_text_unquoted(self):
        d = detect_canonical_draft_write_obligation("把报告里的增长改成高质量增长")
        self.assertEqual(d["tool_family"], "replace_text")

    def test_whole_rewrite_explicit(self):
        d = detect_canonical_draft_write_obligation("整篇重写，推倒重来")
        self.assertEqual(d["tool_family"], "rewrite_draft")

    def test_whole_rewrite_with_constraint(self):
        d = detect_canonical_draft_write_obligation("全文重写，但保留原来的章节结构")
        self.assertEqual(d["tool_family"], "rewrite_draft")

    def test_section_strong_change(self):
        d = detect_canonical_draft_write_obligation("第二章太弱了，改强一点")
        # "改强" 不是关键词 — 但 "改写"/"重写" 也不在此句中
        # 这种 case 现在 detector 不识别（不是失败，是粗粒度的 known limitation）
        # 期望：None，未来若需可扩 keyword
        self.assertIsNone(d)

    def test_continue_with_export(self):
        # mixed intent — detector 只输出 first match (continue)
        # 实际 mixed-intent split 由 check_no_mixed_intent_in_turn 抓
        d = detect_canonical_draft_write_obligation("继续补到5000字，然后导出")
        self.assertEqual(d["tool_family"], "continue")
```

- [ ] **Step 3: 在 `_build_turn_context` 早期调用 detector 并 cache**

```python
# backend/chat.py:_build_turn_context body 加入：

from backend.report_writing import detect_canonical_draft_write_obligation
# ...
self._turn_context["canonical_draft_write_obligation"] = (
    detect_canonical_draft_write_obligation(user_message)
)
```

- [ ] **Step 4: 加 ChatRuntime 端到端测试**

```python
class CanonicalDraftWriteObligationTurnContextTests(ChatRuntimeTests):
    def test_obligation_set_for_section_keyword(self):
        handler = self._make_handler_with_project()
        handler._build_turn_context(self.project_id, "把第二章重写一下")
        ob = handler._turn_context.get("canonical_draft_write_obligation")
        self.assertIsNotNone(ob)
        self.assertEqual(ob["tool_family"], "rewrite_section")

    def test_obligation_none_for_unrelated(self):
        handler = self._make_handler_with_project()
        handler._build_turn_context(self.project_id, "你好，能介绍一下项目吗？")
        ob = handler._turn_context.get("canonical_draft_write_obligation")
        self.assertIsNone(ob)
```

- [ ] **Step 5: Run + commit**

Run: `pytest tests/test_report_writing.py::DetectWriteObligationTests tests/test_chat_runtime.py::CanonicalDraftWriteObligationTurnContextTests -v`
Expected: 12 PASS (10 detector + 2 turn_context)

```bash
git add backend/report_writing.py backend/chat.py tests/test_report_writing.py tests/test_chat_runtime.py
git commit -m "feat(chat): detect_canonical_draft_write_obligation + cache to turn_context

Spec §3.5: 粗粒度 keyword detector 复用现有 phrase 列表 + replace RE，
仅输出 tool_family（不输出 mode/scope/target —— 跟 fix4 preflight 简化版的
设计一致）。在 _build_turn_context 调用 + cache 到 canonical_draft_write_obligation
字段，turn-end 对账（Task 3）依赖这个字段。

10 detector 单测 cover spec §7.8 benchmark suite 的全部 message 模式。"
```

### Task 2.4 — read_file 完成 hook 写 read_file_snapshots

- [ ] **Step 1: 找到 `_execute_tool` 的 read_file 分支（chat.py 含 `_execute_read_file`）**

```python
# 在 read_file 工具成功完成后（typically read 成功 + content 返回时）：

# 仅对 canonical draft 路径记录 mtime
if self._is_canonical_report_draft_path(normalized_path):
    project_path = self.skill_engine.get_project_path(project_id)
    if project_path:
        target = project_path / self.skill_engine.REPORT_DRAFT_PATH
        if target.exists():
            self._turn_context.setdefault("read_file_snapshots", {})[
                self.skill_engine.REPORT_DRAFT_PATH
            ] = target.stat().st_mtime
```

- [ ] **Step 2: 加测试**

```python
class ReadFileSnapshotHookTests(ChatRuntimeTests):
    def test_read_file_records_canonical_draft_mtime(self):
        handler = self._make_handler_with_project()
        # 准备 draft 文件
        draft_path = self.project_dir / "content" / "report_draft_v1.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text("# 报告\n## 第一章\n内容\n", encoding="utf-8")
        handler._build_turn_context(self.project_id, "看一下正文")
        # 触发 read_file
        result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "read_file",
                json.dumps({"file_path": "content/report_draft_v1.md"}),
            ),
        )
        self.assertEqual(result.get("status"), "success")
        snapshots = handler._turn_context.get("read_file_snapshots") or {}
        self.assertIn("content/report_draft_v1.md", snapshots)
        # mtime 应该跟实际文件一致
        self.assertAlmostEqual(
            snapshots["content/report_draft_v1.md"],
            draft_path.stat().st_mtime,
            places=3,
        )

    def test_read_file_does_not_record_for_plan_path(self):
        handler = self._make_handler_with_project()
        # plan/* 不记录
        plan_path = self.project_dir / "plan" / "outline.md"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text("大纲", encoding="utf-8")
        handler._build_turn_context(self.project_id, "看一下大纲")
        handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "read_file",
                json.dumps({"file_path": "plan/outline.md"}),
            ),
        )
        snapshots = handler._turn_context.get("read_file_snapshots") or {}
        self.assertNotIn("plan/outline.md", snapshots)
```

- [ ] **Step 3: Run + commit**

Run: `pytest tests/test_chat_runtime.py::ReadFileSnapshotHookTests -v`
Expected: 2 PASS

```bash
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "feat(chat): record canonical draft mtime in read_file_snapshots on read_file

Spec §3.7: read_file 完成时仅对 canonical draft 记录 mtime，写工具入口
check_read_before_write_canonical_draft 用此字段判断 stale。plan/* 文件
不记录（避免不必要的 turn_context 内存占用）。"
```

---

## Task 3: 4 个工具实现 + schema 注册 + dispatch + ToolTests

**Goal**: 加 4 个工具的入口实现 + 注册到 `_get_tools` schema + `_execute_tool` dispatch + 各 ToolTests 类。**旧路径仍 work**（draft-action tag + gate 仍存在），新工具并存。

**Files:**
- Modify: `backend/chat.py` (`_get_tools` + `_execute_tool` + 4 个新工具入口函数 + 重构 `append_report_draft` 入口)
- Modify: `tests/test_chat_runtime.py` (4 个 ToolTests 类)

### Task 3.1 — schema 注册（仅注册，不实现 body）

- [ ] **Step 1: 找到 `_get_tools` 函数（per spec reviewer §B10 grep）**

```python
# 在 _get_tools 返回的 list 里，append_report_draft schema 后追加 3 个新工具 schema:

{
    "type": "function",
    "function": {
        "name": "rewrite_report_section",
        "description": (
            "重写正文草稿（content/report_draft_v1.md）中已存在的某一章/节。"
            "目标章节由系统从用户消息中自动定位（要求消息中含'第N章/节/部分'前缀，"
            "且草稿中存在唯一对应 heading）。仅在 S4 阶段、草稿存在、目标可唯一定位时可用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": (
                        "目标章节的新版完整内容，从 `## 章节标题` 行开始，"
                        "到下一个同级 `##` 之前为止。不能包含其他 `##` 级别的标题。"
                    ),
                },
            },
            "required": ["content"],
        },
    },
},
{
    "type": "function",
    "function": {
        "name": "replace_report_text",
        "description": (
            "把正文草稿（content/report_draft_v1.md）中的某段文字替换为新文字。"
            "要求 `old` 在草稿中**唯一**出现（恰好 1 次）。仅在 S4 阶段、草稿存在时可用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "old": {
                    "type": "string",
                    "description": (
                        "要替换的原文片段。必须在草稿中唯一出现，"
                        "长度建议 5-200 字以确保唯一性。"
                    ),
                },
                "new": {
                    "type": "string",
                    "description": "替换后的新文字。可以为空（删除场景）。",
                },
            },
            "required": ["old", "new"],
        },
    },
},
{
    "type": "function",
    "function": {
        "name": "rewrite_report_draft",
        "description": (
            "重写整份正文草稿（content/report_draft_v1.md）。仅在用户明确要求"
            "'整篇重写' / '推倒重来' / '全文重写' 时使用；个别章节调整请用 "
            "`rewrite_report_section`，文字替换用 `replace_report_text`。"
            "仅在 S4 阶段、草稿存在时可用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": (
                        "完整新草稿内容，从 `# 报告标题` 开始。"
                        "必须含至少一个 `## ` 级别章节标题。"
                    ),
                },
            },
            "required": ["content"],
        },
    },
},
```

- [ ] **Step 2: 加 schema 注册测试**

```python
class ToolSchemaRegistrationTests(ChatRuntimeTests):
    def test_get_tools_lists_all_4_write_tools(self):
        handler = self._make_handler_with_project()
        tools = handler._get_tools()
        names = {t["function"]["name"] for t in tools if "function" in t}
        self.assertIn("append_report_draft", names)
        self.assertIn("rewrite_report_section", names)
        self.assertIn("replace_report_text", names)
        self.assertIn("rewrite_report_draft", names)

    def test_rewrite_report_section_schema_only_content_param(self):
        handler = self._make_handler_with_project()
        tools = handler._get_tools()
        sec = next(t for t in tools if t.get("function", {}).get("name") == "rewrite_report_section")
        params = sec["function"]["parameters"]
        self.assertEqual(set(params["properties"].keys()), {"content"})
        self.assertEqual(params["required"], ["content"])

    def test_replace_report_text_schema_old_new(self):
        handler = self._make_handler_with_project()
        tools = handler._get_tools()
        rep = next(t for t in tools if t.get("function", {}).get("name") == "replace_report_text")
        params = rep["function"]["parameters"]
        self.assertEqual(set(params["properties"].keys()), {"old", "new"})
        self.assertEqual(set(params["required"]), {"old", "new"})
```

- [ ] **Step 3: Run + commit**

Run: `pytest tests/test_chat_runtime.py::ToolSchemaRegistrationTests -v`
Expected: 3 PASS

```bash
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "feat(chat): register 3 new write tool schemas in _get_tools

Spec §2.2-§2.4: rewrite_report_section / replace_report_text /
rewrite_report_draft schemas。仅注册 schema —— 实现在 Task 3.2-3.5。
此时 dispatch 还没接，model 调用会 ValueError unrecognised tool name；
单测验证 schema 表面正确性。"
```

### Task 3.2 — `rewrite_report_section` 工具实现

- [ ] **Step 1: 加 `_tool_rewrite_report_section` 入口函数到 `chat.py`**

```python
# backend/chat.py 加新 method（位置：_execute_tool 附近 / 工具实现区）

def _tool_rewrite_report_section(
    self, project_id: str, content: str,
) -> Dict:
    """spec §2.2: rewrite_report_section 工具入口."""
    from backend.report_writing import (
        check_report_writing_stage, check_outline_confirmed,
        check_no_mixed_intent_in_turn, check_no_prior_canonical_mutation_in_turn,
        check_no_fetch_url_pending, check_read_before_write_canonical_draft,
        resolve_section_target,
    )
    user_message = self._turn_context.get("user_message_text") or ""
    
    # 1. SHARED_PRE_WRITE_CHECKS（含 mutation limit）
    for err in (
        check_report_writing_stage(self.skill_engine, project_id),
        check_outline_confirmed(self.skill_engine, project_id),
        check_no_mixed_intent_in_turn(self, user_message),
        check_no_prior_canonical_mutation_in_turn(self._turn_context),
        check_no_fetch_url_pending(self._turn_context),
    ):
        if err: return {"status": "error", "message": err}
    
    # 2. 读 draft + read-before-write check
    err = check_read_before_write_canonical_draft(
        self._turn_context, self.skill_engine, project_id, require_read=True,
    )
    if err: return {"status": "error", "message": err}
    
    draft_text = self._read_project_file_text(
        project_id, self.skill_engine.REPORT_DRAFT_PATH,
    ) or ""
    if not draft_text:
        return {"status": "error", "message": "当前还没有正文草稿，请先用 append_report_draft 起草第一版"}
    
    # 3. resolve target
    target = resolve_section_target(
        user_message, draft_text,
        extract_markdown_heading_nodes=self._extract_markdown_heading_nodes,
    )
    if target is None:
        return {"status": "error", "message": "请在消息中明确说明要改哪一章/节，例如'重写第二章'"}
    
    # 4. content 校验
    if not content.startswith("## "):
        return {"status": "error", "message": "`content` 必须以 `## 章节标题` 开头"}
    h2_count = sum(1 for line in content.split("\n") if line.startswith("## "))
    if h2_count != 1:
        return {"status": "error", "message": "`content` 不能涉及多个章节。请只提交目标章节的完整内容"}
    cap = max(3000, 3 * len(target["snapshot"]))
    if len(content) > cap:
        return {"status": "error", "message": f"提交内容超过预期范围（{len(content)} 字 vs 上限 {cap} 字），请只提交目标章节的内容"}
    
    # 5. 写盘
    result = self._execute_plan_write(
        project_id,
        file_path=self.skill_engine.REPORT_DRAFT_PATH,
        content=draft_text.replace(target["snapshot"], content, 1),
        source_tool_name="rewrite_report_section",
        source_tool_args={"content": content},
        persist_func_name="edit_file",
        persist_args={
            "file_path": self.skill_engine.REPORT_DRAFT_PATH,
            "old_string": target["snapshot"],
            "new_string": content,
        },
    )
    if result.get("status") == "success":
        self._turn_context["canonical_draft_mutation"] = {
            "tool": "rewrite_report_section",
            "label": target["label"],
        }
    return result
```

- [ ] **Step 2: 加到 `_execute_tool` dispatch**

```python
# 在 _execute_tool 内 tool_name 分支处加：
elif tool_name == "rewrite_report_section":
    args = self._parse_tool_args(tool_call)
    return self._tool_rewrite_report_section(
        project_id, content=args.get("content", ""),
    )
```

- [ ] **Step 3: 加 ToolTests 类（11 个 case 覆盖 §2.2 reject 表 + happy path）**

```python
class RewriteReportSectionToolTests(ChatRuntimeTests):
    def _put_draft(self, body):
        draft_path = self.project_dir / "content" / "report_draft_v1.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(body, encoding="utf-8")
        return draft_path

    def _setup_outline_confirmed_s4(self, handler):
        # set outline_confirmed_at + simulate stage S4
        handler.skill_engine._save_stage_checkpoint(
            self.project_dir, "outline_confirmed_at",
        )
        handler.skill_engine._save_stage_checkpoint(
            self.project_dir, "report_draft_ready_at",
        )

    def _trigger_read_file(self, handler):
        # 触发 read_file_snapshots 写入
        handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "read_file",
                json.dumps({"file_path": "content/report_draft_v1.md"}),
            ),
        )

    def test_happy_path_rewrites_section(self):
        handler = self._make_handler_with_project()
        self._setup_outline_confirmed_s4(handler)
        self._put_draft("# 报告\n## 第一章 引言\n旧内容0\n## 第二章 战力分析\n旧内容B\n")
        handler._build_turn_context(self.project_id, "把第二章重写一下")
        self._trigger_read_file(handler)
        result = handler._tool_rewrite_report_section(
            self.project_id, content="## 第二章 战力分析\n新内容B\n",
        )
        self.assertEqual(result.get("status"), "success")
        actual = (self.project_dir / "content" / "report_draft_v1.md").read_text(encoding="utf-8")
        self.assertIn("新内容B", actual)
        self.assertNotIn("旧内容B", actual)
        self.assertIn("旧内容0", actual)  # 第一章不动

    def test_stage_pre_s4_rejects(self):
        handler = self._make_handler_with_project()
        # 不 set outline_confirmed_at, 阶段保持 S0
        handler._build_turn_context(self.project_id, "把第二章重写一下")
        result = handler._tool_rewrite_report_section(
            self.project_id, content="## 第二章\n新内容\n",
        )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("S4", result.get("message", ""))

    def test_outline_unconfirmed_rejects(self):
        handler = self._make_handler_with_project()
        # advance stage 但不确认大纲
        handler.skill_engine._save_stage_checkpoint(
            self.project_dir, "report_draft_ready_at",
        )
        handler._build_turn_context(self.project_id, "把第二章重写一下")
        result = handler._tool_rewrite_report_section(
            self.project_id, content="## 第二章\n新内容\n",
        )
        # 阶段 check 先于 outline check；因为没有 outline 也没有 S4
        # 实际行为按 helper 顺序，预期 stage 报错或 outline 报错
        self.assertEqual(result.get("status"), "error")

    def test_mutation_limit_blocks_second_call(self):
        handler = self._make_handler_with_project()
        self._setup_outline_confirmed_s4(handler)
        self._put_draft("# 报告\n## 第二章 战力分析\n内容\n")
        handler._build_turn_context(self.project_id, "把第二章重写一下")
        self._trigger_read_file(handler)
        # 第一次成功
        handler._tool_rewrite_report_section(
            self.project_id, content="## 第二章 战力分析\n新内容1\n",
        )
        # mutation 已 set，第二次应 reject
        result = handler._tool_rewrite_report_section(
            self.project_id, content="## 第二章 战力分析\n新内容2\n",
        )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("本轮已经修改过", result.get("message", ""))

    def test_draft_missing_rejects(self):
        handler = self._make_handler_with_project()
        self._setup_outline_confirmed_s4(handler)
        # 不 put_draft
        handler._build_turn_context(self.project_id, "把第二章重写一下")
        result = handler._tool_rewrite_report_section(
            self.project_id, content="## 第二章\n新内容\n",
        )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("草稿", result.get("message", ""))

    def test_user_msg_no_section_prefix_rejects(self):
        handler = self._make_handler_with_project()
        self._setup_outline_confirmed_s4(handler)
        self._put_draft("# 报告\n## 第二章\n内容\n")
        handler._build_turn_context(self.project_id, "重写一下")  # 没说哪一章
        self._trigger_read_file(handler)
        result = handler._tool_rewrite_report_section(
            self.project_id, content="## 第二章\n新内容\n",
        )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("章/节", result.get("message", ""))

    def test_partial_multi_prefix_rejects(self):
        handler = self._make_handler_with_project()
        self._setup_outline_confirmed_s4(handler)
        self._put_draft("# 报告\n## 第二章\n内容\n")  # 没第三章
        handler._build_turn_context(self.project_id, "把第二章和第三章重写")
        self._trigger_read_file(handler)
        result = handler._tool_rewrite_report_section(
            self.project_id, content="## 第二章\n新内容\n",
        )
        self.assertEqual(result.get("status"), "error")

    def test_content_no_h2_prefix_rejects(self):
        handler = self._make_handler_with_project()
        self._setup_outline_confirmed_s4(handler)
        self._put_draft("# 报告\n## 第二章\n内容\n")
        handler._build_turn_context(self.project_id, "把第二章重写")
        self._trigger_read_file(handler)
        result = handler._tool_rewrite_report_section(
            self.project_id, content="新内容（缺 ## 标题）",
        )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("`## 章节标题`", result.get("message", ""))

    def test_content_multiple_h2_rejects(self):
        handler = self._make_handler_with_project()
        self._setup_outline_confirmed_s4(handler)
        self._put_draft("# 报告\n## 第二章\n内容\n")
        handler._build_turn_context(self.project_id, "把第二章重写")
        self._trigger_read_file(handler)
        result = handler._tool_rewrite_report_section(
            self.project_id, content="## 第二章\nA\n## 第三章\nB\n",
        )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("多个章节", result.get("message", ""))

    def test_content_exceeds_cap_rejects(self):
        handler = self._make_handler_with_project()
        self._setup_outline_confirmed_s4(handler)
        target_snap = "## 第二章 X\n" + "短内容" * 10
        self._put_draft("# 报告\n" + target_snap + "\n")
        handler._build_turn_context(self.project_id, "把第二章重写")
        self._trigger_read_file(handler)
        # cap = max(3000, 3 * len(target_snap)) ≈ 3000
        oversized = "## 第二章 X\n" + ("X" * 5000)
        result = handler._tool_rewrite_report_section(
            self.project_id, content=oversized,
        )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("超过", result.get("message", ""))

    def test_no_read_before_write_rejects(self):
        handler = self._make_handler_with_project()
        self._setup_outline_confirmed_s4(handler)
        self._put_draft("# 报告\n## 第二章\n内容\n")
        handler._build_turn_context(self.project_id, "把第二章重写")
        # 不 trigger_read_file
        result = handler._tool_rewrite_report_section(
            self.project_id, content="## 第二章\n新内容\n",
        )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("read_file", result.get("message", ""))
```

- [ ] **Step 4: Run + commit**

Run: `pytest tests/test_chat_runtime.py::RewriteReportSectionToolTests -v`
Expected: 11 PASS

```bash
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "feat(chat): rewrite_report_section tool implementation + 11 ToolTests

Spec §2.2 §3.2: 工具入口 inline 调用 SHARED_PRE_WRITE_CHECKS + 工具特定
check（draft 存在 + target unique resolve + content 三项校验 + cap）+
写盘成功 set canonical_draft_mutation。

11 tests: happy / stage / outline / mutation-limit / draft-missing /
no-section-prefix / partial-multi-prefix / no-h2 / multi-h2 / cap /
no-read-before-write。"
```

### Task 3.3 — `replace_report_text` 工具实现

- [ ] **Step 1: 加 `_tool_replace_report_text` 入口函数**

```python
def _tool_replace_report_text(
    self, project_id: str, old: str, new: str,
) -> Dict:
    """spec §2.3: replace_report_text 工具入口."""
    from backend.report_writing import (
        check_report_writing_stage, check_outline_confirmed,
        check_no_mixed_intent_in_turn, check_no_prior_canonical_mutation_in_turn,
        check_no_fetch_url_pending, check_read_before_write_canonical_draft,
    )
    user_message = self._turn_context.get("user_message_text") or ""
    
    for err in (
        check_report_writing_stage(self.skill_engine, project_id),
        check_outline_confirmed(self.skill_engine, project_id),
        check_no_mixed_intent_in_turn(self, user_message),
        check_no_prior_canonical_mutation_in_turn(self._turn_context),
        check_no_fetch_url_pending(self._turn_context),
    ):
        if err: return {"status": "error", "message": err}
    
    err = check_read_before_write_canonical_draft(
        self._turn_context, self.skill_engine, project_id, require_read=True,
    )
    if err: return {"status": "error", "message": err}
    
    draft_text = self._read_project_file_text(
        project_id, self.skill_engine.REPORT_DRAFT_PATH,
    ) or ""
    if not draft_text:
        return {"status": "error", "message": "当前还没有正文草稿..."}
    
    if not old:
        return {"status": "error", "message": "`old` 不能为空"}
    occurrences = draft_text.count(old)
    if occurrences == 0:
        return {"status": "error", "message": f"目标文本 '{old}' 在草稿中未找到。请先 read_file 核对原文"}
    if occurrences > 1:
        return {"status": "error", "message": f"目标文本 '{old}' 在草稿中出现 {occurrences} 次（不唯一）。请提供更具体的上下文"}
    
    new_text = draft_text.replace(old, new, 1)
    result = self._execute_plan_write(
        project_id,
        file_path=self.skill_engine.REPORT_DRAFT_PATH,
        content=new_text,
        source_tool_name="replace_report_text",
        source_tool_args={"old": old, "new": new},
        persist_func_name="edit_file",
        persist_args={
            "file_path": self.skill_engine.REPORT_DRAFT_PATH,
            "old_string": old, "new_string": new,
        },
    )
    if result.get("status") == "success":
        self._turn_context["canonical_draft_mutation"] = {
            "tool": "replace_report_text",
            "old_len": len(old),
        }
    return result
```

- [ ] **Step 2: 加 dispatch + tests**

dispatch 加 `elif tool_name == "replace_report_text"` case.

7 个 ToolTests case：
- happy path: `把'力量'改成'体能'`
- 0 occurrence rejects
- ≥ 2 occurrences rejects
- empty old rejects
- mutation limit blocks 2nd call
- stage / outline / mutation / read-before-write 失败 case (复用 RewriteReportSectionToolTests pattern)

完整测试代码模式同 Task 3.2，省略以减少 plan 长度（plan 阶段实施时按 RewriteReportSectionToolTests 11 case 模式扩 7 case for replace）.

- [ ] **Step 3: Run + commit**

Run: `pytest tests/test_chat_runtime.py::ReplaceReportTextToolTests -v`
Expected: 7 PASS

```bash
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "feat(chat): replace_report_text tool implementation + 7 ToolTests

Spec §2.3: 工具入口 inline check + draft.count(old) == 1 唯一性校验 +
写盘成功 set canonical_draft_mutation。"
```

### Task 3.4 — `rewrite_report_draft` 工具实现

- [ ] **Step 1: 加 `_tool_rewrite_report_draft` 入口函数**

```python
def _tool_rewrite_report_draft(
    self, project_id: str, content: str,
) -> Dict:
    """spec §2.4: rewrite_report_draft 工具入口."""
    from backend.report_writing import (
        check_report_writing_stage, check_outline_confirmed,
        check_no_mixed_intent_in_turn, check_no_prior_canonical_mutation_in_turn,
        check_no_fetch_url_pending, check_read_before_write_canonical_draft,
    )
    user_message = self._turn_context.get("user_message_text") or ""
    
    for err in (
        check_report_writing_stage(self.skill_engine, project_id),
        check_outline_confirmed(self.skill_engine, project_id),
        check_no_mixed_intent_in_turn(self, user_message),
        check_no_prior_canonical_mutation_in_turn(self._turn_context),
        check_no_fetch_url_pending(self._turn_context),
    ):
        if err: return {"status": "error", "message": err}
    
    err = check_read_before_write_canonical_draft(
        self._turn_context, self.skill_engine, project_id, require_read=True,
    )
    if err: return {"status": "error", "message": err}
    
    current_draft = self._read_project_file_text(
        project_id, self.skill_engine.REPORT_DRAFT_PATH,
    ) or ""
    if not current_draft:
        return {"status": "error", "message": "当前还没有正文草稿..."}
    
    # user 消息必须含全文重写关键词（spec §2.4 reject 第 4 条）
    whole_kws = ("整篇重写", "全文重写", "推倒重写", "推倒重来", "全部改写")
    if not any(kw in user_message for kw in whole_kws):
        return {"status": "error", "message": (
            "看起来你只想改一部分。重写整章请用 `rewrite_report_section`，"
            "替换文字用 `replace_report_text`。如果确实要整篇重写，请明确说"
            "'整篇重写'或'全文重写'"
        )}
    
    if not content.startswith("# "):
        return {"status": "error", "message": "`content` 必须以 `# 报告标题` 开头"}
    h2_count = sum(1 for line in content.split("\n") if line.startswith("## "))
    if h2_count == 0:
        return {"status": "error", "message": "`content` 必须含至少一个章节标题（`## ` 级别）"}
    cap = max(8000, 2 * len(current_draft))
    if len(content) > cap:
        return {"status": "error", "message": f"提交内容超过预期范围（{len(content)} 字 vs 上限 {cap} 字），请只提交完整草稿"}
    
    result = self._execute_plan_write(
        project_id,
        file_path=self.skill_engine.REPORT_DRAFT_PATH,
        content=content,
        source_tool_name="rewrite_report_draft",
        source_tool_args={"content": content},
        persist_func_name="edit_file",
        persist_args={
            "file_path": self.skill_engine.REPORT_DRAFT_PATH,
            "old_string": current_draft, "new_string": content,
        },
    )
    if result.get("status") == "success":
        self._turn_context["canonical_draft_mutation"] = {
            "tool": "rewrite_report_draft",
        }
    return result
```

- [ ] **Step 2: dispatch + 9 ToolTests case**

跟 Task 3.2 同模式。9 case: happy / stage / outline / mutation / draft missing / no whole-rewrite keyword / no h1 prefix / no h2 / cap / no read-before-write.

- [ ] **Step 3: Run + commit**

Run: `pytest tests/test_chat_runtime.py::RewriteReportDraftToolTests -v`
Expected: 9 PASS

```bash
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "feat(chat): rewrite_report_draft tool implementation + 9 ToolTests

Spec §2.4: 工具入口 inline check + 全文重写关键词检测（防 model 误用）+
content # h1 / ## h2 校验 + cap = max(8000, 2 * current_draft)。"
```

### Task 3.5 — `append_report_draft` 重构

- [ ] **Step 1: 改 `append_report_draft` 现有入口（chat.py:4187-4202 附近）**

参考 §2.1 reject table 重构入口：把分散在 `_validate_append_turn_canonical_draft_write` / `_validate_required_report_draft_prewrite` 的 check 迁移进入口；调用 SHARED_PRE_WRITE_CHECKS。

```python
def _tool_append_report_draft(
    self, project_id: str, content: str,
) -> Dict:
    """spec §2.1: append_report_draft 重构 — 校验从分散迁移到 inline."""
    from backend.report_writing import (
        check_report_writing_stage, check_outline_confirmed,
        check_no_mixed_intent_in_turn, check_no_prior_canonical_mutation_in_turn,
        check_no_fetch_url_pending, check_read_before_write_canonical_draft,
    )
    user_message = self._turn_context.get("user_message_text") or ""
    
    for err in (
        check_report_writing_stage(self.skill_engine, project_id),
        check_outline_confirmed(self.skill_engine, project_id),
        check_no_mixed_intent_in_turn(self, user_message),
        check_no_prior_canonical_mutation_in_turn(self._turn_context),
        check_no_fetch_url_pending(self._turn_context),
    ):
        if err: return {"status": "error", "message": err}
    
    # draft 存在时 read-before-write 强制；不存在时（首次起草）跳过
    project_path = self.skill_engine.get_project_path(project_id)
    draft_exists = (project_path and (project_path / self.skill_engine.REPORT_DRAFT_PATH).exists())
    err = check_read_before_write_canonical_draft(
        self._turn_context, self.skill_engine, project_id,
        require_read=draft_exists,
    )
    if err: return {"status": "error", "message": err}
    
    # 调用现有 append 落盘逻辑（保留 fix4 之前的 append infrastructure）
    result = self._do_append_report_draft(project_id, content)
    if result.get("status") == "success":
        self._turn_context["canonical_draft_mutation"] = {
            "tool": "append_report_draft",
        }
    return result
```

注：`_do_append_report_draft` 是把现有 `append_report_draft` 内部的 file mutation 部分抽出（spec §2.1 "改造"）。具体抽法：保留现有 append 写盘逻辑（incl. word_count tracking / events recording），只把 entry-level invariant check 移到 `_tool_append_report_draft`。

- [ ] **Step 2: 加 dispatch + AppendReportDraftToolTests**

10 case: 首次起草 happy / 续写 happy（draft 已存在）/ stage / outline / mutation / fetch_url / mixed-intent / read-before-write（continue 模式）/ append 写后 set mutation / cross-turn mutation 默认 None。

- [ ] **Step 3: Run + commit**

Run: `pytest tests/test_chat_runtime.py::AppendReportDraftToolTests -v`
Expected: 10 PASS

```bash
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "feat(chat): append_report_draft 重构 inline check + 10 ToolTests

Spec §2.1: 校验从 _validate_append_turn_canonical_draft_write 等分散
位置迁移到工具入口 inline；调用 SHARED_PRE_WRITE_CHECKS；首次起草跳过
read-before-write（draft 不存在时）。

10 tests cover happy begin/continue + 各 reject 路径 + cross-turn 边界。"
```

### Task 3.6 — `_chat_*_unlocked` no-tool-call retry（write-obligation 对账）

- [ ] **Step 1: 找到 `_chat_stream_unlocked` + `_chat_unlocked` 的 no-tool-call 分支（model emit 完成、loop 准备 break 处）**

```python
# 两处对称插入（伪代码 — 适配现有 control flow）：

obligation = self._turn_context.get("canonical_draft_write_obligation")
mutation = self._turn_context.get("canonical_draft_mutation")
if obligation and not mutation:
    from backend.report_writing import assistant_text_claims_modification
    if assistant_text_claims_modification(assistant_text):
        # 注入 corrective user message + continue loop（max_iter 仍生效）
        corrective = (
            "你在回复中声称已修改正文（"
            f"obligation={obligation['tool_family']}），但本轮没有成功调用任何写正文工具"
            "（append_report_draft / rewrite_report_section / replace_report_text / "
            "rewrite_report_draft）。请实际调用对应工具完成写入，不要只在文字中声明已完成。"
        )
        self._inject_synthetic_user_correction(corrective)
        # 标记 obligation_retry_fired 防止重复 inject
        self._turn_context["obligation_retry_fired"] = True
        continue  # 进下一轮 model emit
```

- [ ] **Step 2: 实现 `_inject_synthetic_user_correction(text)`**

```python
def _inject_synthetic_user_correction(self, text: str) -> None:
    """注入合成 user message 进 messages 列表，让 model 在下一轮 loop 看到."""
    self._messages.append({"role": "user", "content": text})
```

(实际位置取决于 `_chat_stream_unlocked` / `_chat_unlocked` 当前如何 maintain `_messages`，需 grep 现有 retry 路径如 `required_write_snapshots` 触发的 retry 看 pattern。)

- [ ] **Step 3: 加 WriteObligationRetryTests（完整 4 case）**

```python
class WriteObligationRetryTests(ChatRuntimeTests):
    """Spec §3.5 §7.6 retry 入口在 chat loop 层，不在 _finalize_assistant_turn."""

    def _make_obligation(self, family="rewrite_section"):
        return {"tool_family": family, "detected": "重写"}

    def test_obligation_present_no_mutation_text_claims_triggers_retry(self):
        handler = self._make_handler_with_project()
        handler._build_turn_context(self.project_id, "把第二章重写一下")
        handler._turn_context["canonical_draft_write_obligation"] = self._make_obligation()
        # 模拟 model 输出 claim text 但 0 tool_call
        assistant_text = "我已经把第二章重写完毕，请查看正文。"
        # Hook test：直接调用即将插入的 helper（具体名按实施代码命名）
        retry_fired = handler._maybe_inject_obligation_retry(assistant_text)
        self.assertTrue(retry_fired)
        self.assertTrue(handler._turn_context.get("obligation_retry_fired"))

    def test_obligation_present_no_claim_no_retry(self):
        handler = self._make_handler_with_project()
        handler._build_turn_context(self.project_id, "把第二章重写一下")
        handler._turn_context["canonical_draft_write_obligation"] = self._make_obligation()
        # 没有完成声明，仅意图陈述
        assistant_text = "我会重写第二章，让我先 read_file 看看现有内容。"
        retry_fired = handler._maybe_inject_obligation_retry(assistant_text)
        self.assertFalse(retry_fired)
        self.assertFalse(handler._turn_context.get("obligation_retry_fired"))

    def test_obligation_present_with_mutation_no_retry(self):
        handler = self._make_handler_with_project()
        handler._build_turn_context(self.project_id, "把第二章重写一下")
        handler._turn_context["canonical_draft_write_obligation"] = self._make_obligation()
        handler._turn_context["canonical_draft_mutation"] = {"tool": "rewrite_report_section"}
        assistant_text = "我已经把第二章重写完毕。"
        retry_fired = handler._maybe_inject_obligation_retry(assistant_text)
        self.assertFalse(retry_fired)

    def test_obligation_none_no_retry(self):
        handler = self._make_handler_with_project()
        handler._build_turn_context(self.project_id, "你好")
        handler._turn_context["canonical_draft_write_obligation"] = None
        assistant_text = "你好，需要什么帮助？"
        retry_fired = handler._maybe_inject_obligation_retry(assistant_text)
        self.assertFalse(retry_fired)
```

**实施提示**：测试调用一个新方法 `_maybe_inject_obligation_retry(assistant_text) -> bool`。这个方法是为了把 "obligation 检查 + claim detect + inject corrective" 的逻辑聚合到一个可单测的 helper，避免要 mock 整个 `_chat_stream_unlocked` / `_chat_unlocked` chain。两条 chat path 实施时各自调一次该 helper，根据返回值决定是否 `continue` 主 loop。

- [ ] **Step 4: Run + commit**

Run: `pytest tests/test_chat_runtime.py::WriteObligationRetryTests -v`
Expected: 4 PASS

```bash
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "feat(chat): no-tool-call retry on write_obligation present + text claims

Spec §3.5 §7.6: turn-end 对账 retry 入口在 _chat_stream_unlocked /
_chat_unlocked no-tool-call 分支（不在 _finalize_assistant_turn）。
检测到 obligation set + 0 mutation + assistant 文本声称已修改 → 注入
corrective user message + continue loop 重试。obligation_retry_fired
标记防重复 inject。

4 tests cover 4 个分支组合。"
```

---

## Task 4: SKILL.md + user-facing reject message wording

**Goal**: 引导 model 用新工具；改 user-facing reject messages 中残留的 `<draft-action>` 字符串。**仍未删除旧路径**——SKILL.md 改了之后 model 会被引导用新工具但旧路径 fallback 仍 work。

**Files:**
- Modify: `skill/SKILL.md` (§S4 + 附录)
- Modify: `backend/chat.py` line 5289 / 7459 / 7521 user_action 字符串（per spec §5.4）

### Task 4.1 — SKILL.md §S4 重写

- [ ] **Step 1: 找到 §S4 "draft-action 标签" 子章节（约 113-126 行）**

替换为：

```markdown
### S4 正文写作工具

| 用户意图 | 调用工具 | 关键参数 |
|---|---|---|
| 起草初稿 / 续写正文 / 写下一段或下一章 | `append_report_draft` | `content`：要追加的内容 |
| 重写已有的某一章/节（用户说"重写第N章/节"） | `rewrite_report_section` | `content`：以 `## 章节标题` 开始的新章节完整内容（不含其他 `##` heading） |
| 替换正文中的具体文字（用户说"把 X 改成 Y"） | `replace_report_text` | `old`：原文片段（必须在草稿中唯一）；`new`：替换后内容（可空） |
| 整篇重写正文（用户说"整篇重写"/"推倒重来"/"全文重写"） | `rewrite_report_draft` | `content`：以 `# 报告标题` 开始的完整新草稿 |

**关键**：
- 这四个工具内部已经做了阶段、大纲、草稿存在性、章节定位、读后再改、内容大小限制等校验。如果不满足前提，工具会直接返回 error 引导你下一步动作。
- **不要**对 `content/report_draft_v1.md` 使用通用 `edit_file` 或 `write_file`——会被拒绝。
- **不要**复述 1500 字章节原文当 old_string——专用工具不要你传 old_string，系统自己定位。
- 一轮只能改一处：先确定用户最关心的那一处修改完，再问用户下一步。如果用户在一句话里同时要"改章节 + 导出"，请先完成章节修改，再让用户确认下一步。
```

- [ ] **Step 2: 删除附录"draft-action 标签规范"章节（约 228-246 行）**

整段删除（保留 stage-ack 附录的其他部分）。

- [ ] **Step 3: 删除附录"如果只调工具不输出文本"段落（如果还存在）**

`<draft-action>` 系统的"tool-only fallback"残留段落。

- [ ] **Step 4: Commit**

```bash
git add skill/SKILL.md
git commit -m "docs(skill): replace S4 draft-action tag guidance with 4-tool table

Spec §4.3: SKILL.md §S4 改为 4 个写正文工具的语义表格（用户意图 →
工具 + 参数）+ 关键约束说明（不要用 edit_file 写 canonical / 不要复述
old_string / 一轮一改）。删除附录 draft-action 标签规范章节。

仍未删除旧 backend code — Task 5 才删；这一 commit 只是文档/引导改动。"
```

### Task 4.2 — chat.py 中 `<draft-action>` user_action 字符串改

- [ ] **Step 1: 找到 line 5289 / 7459 / 7521（per spec §5.4 reviewer grep）**

```python
# line 5289（在 _execute_plan_write 的 canonical draft block 路径）
user_action="请按 SKILL.md 附录的 draft-action 标签规范操作"
# 改为：
user_action="请使用 rewrite_report_section / replace_report_text / rewrite_report_draft 工具，不要直接 edit_file"

# line 7459（在 draft_action.py validate body）
user_action="请先发 <draft-action>begin</draft-action> 起草，再来重写章节"
# 改为：
user_action="请先用 append_report_draft 起草，再用 rewrite_report_section 重写章节"

# line 7521（同上 replace 分支）
user_action="请先发 <draft-action>begin</draft-action> 起草"
# 改为：
user_action="请先用 append_report_draft 起草"
```

- [ ] **Step 2: 加测试 verify 改动**

```python
class UserFacingDraftActionStringsRemovedTests(ChatRuntimeTests):
    def test_no_draft_action_string_in_chat_py_user_action(self):
        # 简单 grep（避免 regression 引回 <draft-action> 字符串）
        chat_py = (Path(__file__).parent.parent / "backend" / "chat.py").read_text(encoding="utf-8")
        # user_action 字段中不能含 "<draft-action>"
        # 允许有非 user_action 注释 / 历史 ref（在 backend/draft_action.py 残留时）
        for line_no, line in enumerate(chat_py.split("\n"), 1):
            if "user_action" in line and "<draft-action>" in line:
                self.fail(f"line {line_no} still has <draft-action> in user_action: {line}")
```

- [ ] **Step 3: Run + commit**

Run: `pytest tests/test_chat_runtime.py::UserFacingDraftActionStringsRemovedTests -v`
Expected: PASS

```bash
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "fix(chat): replace <draft-action> 残留 string in user_action messages

Spec §5.4: chat.py:5289 / 7459 / 7521 三处 user_action 字符串改为引导
新工具用法（不再说 'draft-action 标签'）。其他 backend code 中的
<draft-action> 残留在 Task 5 一并删除。"
```

---

## Task 5: 删旧 code + 测试（最大删除）

**Goal**: 一次性删 `<draft-action>` 系列所有 backend code + 测试。前 4 个 commit 已经用新工具替代了所有功能，所以这一 commit 可以 atomic 删除。**风险点**：`_TAIL_GUARD_MARKERS` / `TAIL_TAG_SCAN_RE` 等共享代码必须保留 stage-ack 部分。

**Files:**
- Delete: `backend/draft_action.py`
- Delete: `tests/test_draft_action.py`
- Delete: `tests/test_draft_decision_compare_report.py`
- Delete: `tools/draft_decision_compare_report.py`
- Modify: `backend/chat.py` (大量删除)
- Modify: `tests/test_chat_runtime.py` (删 GateCanonicalDraftToolCallTests / DraftActionPreCheckTests / DraftDecisionCompareEventTests / 大部分 PreflightCheckTests / 修剪 StreamSplitSafeTailDraftActionTests)

### Task 5.1 — 删 module 文件 + 测试文件

- [ ] **Step 1: 删 4 个文件**

```bash
rm backend/draft_action.py
rm tests/test_draft_action.py
rm tests/test_draft_decision_compare_report.py
rm tools/draft_decision_compare_report.py
```

- [ ] **Step 2: 删 chat.py 中对 backend.draft_action 的 import**

grep `from backend.draft_action import` / `import backend.draft_action`，移除所有 import line。

### Task 5.2 — 删 chat.py 中 tag/gate/preflight/classifier 大段代码

按 spec §5.1 deletion checklist：

- [ ] **Step 1: 删 `_DRAFT_ACTION_MARKER` / `_DRAFT_INTENT_PREFLIGHT_KEYWORDS` / `_SECTION_PREFIX_RE` 常量**
- [ ] **Step 2: 改 `_TAIL_GUARD_MARKERS = (_STAGE_ACK_MARKER,)` 单 marker tuple**
- [ ] **Step 3: 改 `TAIL_TAG_SCAN_RE` 删 draft-action 部分**
- [ ] **Step 4: 删 `_preflight_canonical_draft_check` 整个函数**
- [ ] **Step 5: 删 `_classify_canonical_draft_turn` 整个函数**
- [ ] **Step 6: 删 `_resolve_section_rewrite_targets` 函数**
- [ ] **Step 7: 删 `_preflight_resolve_section_target` 函数（已被 `report_writing.py` 替代）**
- [ ] **Step 8: 删 `_gate_canonical_draft_tool_call` + 4 个 record_* helpers**
- [ ] **Step 9: 删 `_make_canonical_draft_decision` + `_empty_canonical_draft_decision`**
- [ ] **Step 10: 删 `_validate_required_report_draft_prewrite` 中 line 5507-5520 + 5531-5615 + 5636-5716（保留 5617-5634 通用部分）**
- [ ] **Step 11: 删 `_validate_append_turn_canonical_draft_write` 函数体 + line 5522-5529 调用点**
- [ ] **Step 12: 删 `_run_phase2a_compare_writer` 函数**
- [ ] **Step 13: 删 `REPORT_BODY_*_KEYWORDS` 系列常量（per spec §5.1，保留 INSPECT 类）**
- [ ] **Step 14: 删 `REPORT_BODY_REPLACE_TEXT_INTENT_RE` / `REPORT_BODY_INLINE_EDIT_RE` / `REPORT_BODY_CHAPTER_WRITE_RE`**
- [ ] **Step 15: 删 `_finalize_assistant_turn` 中 draft-action parser/strip/apply 步骤（保留 stage-ack 部分）**
- [ ] **Step 16: 删 `_build_turn_context` 中 fix3 inject + fix4 fix2 mode promotion 块（new turn_context fields 已替代）**
- [ ] **Step 17: 清理 turn_context 删除字段：`canonical_draft_decision` / `draft_action_events` / `compare_baseline_event_count`**

每步删完即 syntax check `python -c "import backend.chat"` 确保 file 仍可 import。

### Task 5.3 — 删 tests/test_chat_runtime.py 中 deprecated test classes

- [ ] **Step 1: 删 `GateCanonicalDraftToolCallTests` 整 class（437 行）**
- [ ] **Step 2: 删 `DraftActionPreCheckTests` 整 class**
- [ ] **Step 3: 删 `DraftDecisionCompareEventTests` 整 class**
- [ ] **Step 4: `PreflightCheckTests` 类大幅缩减**：保留 stage gate / outline confirmed 相关 case（迁移到新位置或新 helper 测试）；删 mode/scope/target 相关 case
- [ ] **Step 5: `StreamSplitSafeTailDraftActionTests` 修剪**：保留只测 stage-ack 的 case，删测 draft-action 的 case；考虑 rename 为 `StreamSplitSafeTailStageAckTests`

### Task 5.4 — 标记老 spec superseded + 加 StageAckRegressionTests + 整体 sanity

- [ ] **Step 0: 标记老 spec superseded**

编辑 `docs/superpowers/specs/2026-05-04-context-signal-and-intent-tag-design.md`，在 §4.3 开头加 markdown banner：

```markdown
> **⚠️ SUPERSEDED**: §4.3 - §4.12 (含 v5 amendment) 已被 `2026-05-05-report-tools-redesign-design.md` 替代。tag-based 架构（含 fix4 v5 §4.12）整套删除，改用 4 个专用工具（spec §2.1-§2.4）。本节保留作为历史 context。新代码请参考 redesign spec。
```

类似 banner 加到 §4.4-§4.12 各章节（或在 §4.3 banner 内一句话覆盖整个 §4.3-§4.12 范围）。

§4.1 时序图 + §4.2 preflight basics 不动（仍可参考的 background）。


- [ ] **Step 1: 加 `StageAckRegressionTests`（完整 case，spec §8 验收 #2）**

```python
class StageAckRegressionTests(ChatRuntimeTests):
    """Spec §8 验收 #2 + §4.1: 删除 draft-action 后 stage-ack 完整功能不受影响."""

    def test_stage_ack_marker_alone_in_tail_guard(self):
        """_TAIL_GUARD_MARKERS 应只剩 stage-ack."""
        from backend.chat import _TAIL_GUARD_MARKERS, _STAGE_ACK_MARKER
        self.assertEqual(_TAIL_GUARD_MARKERS, (_STAGE_ACK_MARKER,))

    def test_stage_ack_tag_scan_re_matches_stage_ack(self):
        """TAIL_TAG_SCAN_RE 仍能识别 stage-ack tag (e.g. <stage-ack>outline_confirmed_at</stage-ack>)."""
        from backend.chat import TAIL_TAG_SCAN_RE
        match = TAIL_TAG_SCAN_RE.search(
            "<stage-ack>outline_confirmed_at</stage-ack>",
        )
        self.assertIsNotNone(match)

    def test_stage_ack_tag_scan_re_does_not_match_draft_action(self):
        """TAIL_TAG_SCAN_RE 不再匹配 <draft-action> (已删)."""
        from backend.chat import TAIL_TAG_SCAN_RE
        match = TAIL_TAG_SCAN_RE.search(
            "<draft-action>begin</draft-action>",
        )
        self.assertIsNone(match)

    def test_stream_split_safe_tail_holds_stage_ack(self):
        """stream_split_safe_tail 仍 hold <stage-ack> 前缀."""
        from backend.chat import stream_split_safe_tail
        safe, held = stream_split_safe_tail("一些正文 <stage-ack>")
        self.assertEqual(safe, "一些正文 ")
        self.assertIn("<stage-ack>", held)

    def test_stream_split_safe_tail_does_not_hold_draft_action(self):
        """draft-action 前缀不再被 hold（不再 tail-guard）."""
        from backend.chat import stream_split_safe_tail
        safe, held = stream_split_safe_tail("一些正文 <draft-action>")
        # 删除后应该全部 emit
        self.assertEqual(safe, "一些正文 <draft-action>")
        self.assertEqual(held, "")

    def test_stage_ack_review_started_at_advances_s4_to_s5(self):
        """端到端：assistant 输出 <stage-ack>review_started_at</stage-ack> 触发 stage advance."""
        handler = self._make_handler_with_project()
        # set up to S4 + outline confirmed + draft ready
        handler.skill_engine._save_stage_checkpoint(
            self.project_dir, "outline_confirmed_at",
        )
        handler.skill_engine._save_stage_checkpoint(
            self.project_dir, "report_draft_ready_at",
        )
        # simulate assistant tail with stage-ack tag
        result = self._finalize_assistant_for_test(
            handler,
            assistant_message=(
                "好的，我们进入审查阶段。\n\n<stage-ack>review_started_at</stage-ack>"
            ),
            user_message="开始审查",
        )
        # checkpoint should be set
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertIn("review_started_at", checkpoints)
```

- [ ] **Step 2: Run wider sanity**

```powershell
D:\MyProject\CodeProject\consulting-report-agent\.venv\Scripts\python.exe -m pytest tests/test_chat_runtime.py tests/test_report_writing.py -v 2>&1 | Select-String -Pattern "FAIL|passed|failed" | Select-Object -Last 30
```

Expected: 0 FAIL。

- [ ] **Step 3: Frontend tests sanity**

```powershell
cd frontend && node --test tests/
```

Expected: 168/168 pass。

- [ ] **Step 3a: Frontend unknown-tool 渲染 smoke（spec §7.5）**

启动 dist app + 在 chat 区手动注入一条含 `tool_name=rewrite_report_section` 的虚假 tool log（或在 single 单测里调 `frontend/utils/chatPresentation.js` 的 tool block 解析函数）。

期望：未知工具名（之前没 hardcoded handler）按 default 路径渲染 — 显示 tool name + args，不 crash。

如果发现 frontend 对未知工具名 crash 或显示异常，加到 `frontend/src/components/ChatPanel.jsx` 的 tool log fallback case。

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor(chat): delete draft-action tag system + classifier + gate (the big delete)

Spec §5.1-5.4 完整删除清单：

backend code 删：
- backend/draft_action.py 整个 module
- _preflight_canonical_draft_check / _classify_canonical_draft_turn /
  _resolve_section_rewrite_targets / _preflight_resolve_section_target /
  _gate_canonical_draft_tool_call / 4 record_* helpers /
  _make_canonical_draft_decision / _empty_canonical_draft_decision /
  _validate_append_turn_canonical_draft_write / _run_phase2a_compare_writer
- _DRAFT_ACTION_MARKER / _DRAFT_INTENT_PREFLIGHT_KEYWORDS /
  _SECTION_PREFIX_RE 常量
- _TAIL_GUARD_MARKERS / TAIL_TAG_SCAN_RE 删 draft-action 部分（保留 stage-ack）
- _validate_required_report_draft_prewrite line 5507-5520 / 5531-5615 /
  5636-5716（保留 5617-5634 通用 fallthrough）
- REPORT_BODY_*_KEYWORDS 系列 + INTENT_RE 系列（保留 INSPECT 类）
- _finalize_assistant_turn 中 draft-action 步骤
- _build_turn_context 中 fix3 inject + fix4 mode promotion 块
- turn_context 字段：canonical_draft_decision / draft_action_events /
  compare_baseline_event_count

tests 删：
- tests/test_draft_action.py 整文件 (92 lines)
- tests/test_draft_decision_compare_report.py 整文件
- GateCanonicalDraftToolCallTests (437 lines) /
  DraftActionPreCheckTests / DraftDecisionCompareEventTests
- PreflightCheckTests 大部分（保留 stage gate / outline 相关迁移到新位置）
- StreamSplitSafeTailDraftActionTests 修剪到只剩 stage-ack 部分

新增：
- StageAckRegressionTests 端到端验证 stage-ack 系统不受影响

工具脚本删：
- tools/draft_decision_compare_report.py

净删 ~2000 行（含测试），新增 ~150 行（StageAckRegression + 新工具 imports）。"
```

---

## Task 6: cutover smoke + worklist/memory/handoff 更新

**Goal**: 重 build + 跑 cutover smoke 5 sessions（A/B/C/D + 新加 E rewrite_draft）+ 写 cutover_report + 更新 worklist/memory/handoff。

### Task 6.1 — Rebuild dist/ + verify size budget

- [ ] **Step 1: 记录 baseline dist 大小**

```bash
du -sb "dist/咨询报告助手" 2>/dev/null || echo "no baseline"
# 或读 fix4 cutover_report 中记录的 91 MB
BASELINE_MB=91  # per Phase 2a fix4 cutover record
```

- [ ] **Step 2: kill 现有 dist app**

```powershell
Get-Process -Name "咨询报告助手" -ErrorAction SilentlyContinue | Stop-Process -Force
```

- [ ] **Step 3: rm + build**

```bash
rm -rf dist/咨询报告助手
```

```powershell
.\build.ps1
```

期望：build 成功，dist/咨询报告助手.exe 重新生成。

- [ ] **Step 4: verify dist size within ±5%（spec §8 验收 #5）**

```bash
NEW_MB=$(du -sm dist/咨询报告助手 | cut -f1)
echo "New dist: ${NEW_MB}MB (baseline ${BASELINE_MB}MB ±5%)"
# expect: ${NEW_MB} between $((BASELINE_MB * 95 / 100)) and $((BASELINE_MB * 105 / 100))
```

如果超出 ±5%（86-96 MB 之外），调查原因。预期：删除 ~2000 行 backend 代码后 dist 应该略小或持平（PyInstaller 打包大头是 deps 不是源码）。

### Task 6.2 — Pre-cutover unit benchmark（工具选择正确率）

- [ ] **Step 1: 准备 10-message benchmark suite（per spec §7.8 / r2 reviewer §15）**

```python
# 加到 tests/test_chat_runtime.py 或单独 tests/test_tool_selection_benchmark.py
class ToolSelectionBenchmarkTests(ChatRuntimeTests):
    """Spec §7.8 §8.7 工具选择正确率 ≥ 80% 验收。"""

    BENCHMARK_SUITE = [
        ("开始写报告正文", "append_report_draft"),
        ("继续写下一章", "append_report_draft"),
        ("请把第二章重写一下", "rewrite_report_section"),
        ("重写第二章和第三章", None),  # 多 prefix → reject
        ("把正文里的'渠道效率'改成'渠道质量'", "replace_report_text"),
        ("把'增长'改成'高质量增长'", "replace_report_text"),
        ("整篇重写，推倒重来", "rewrite_report_draft"),
        ("全文重写，但保留原来的章节结构", "rewrite_report_draft"),
        ("第二章太弱了，改强一点", "rewrite_report_section"),
        ("继续补到5000字，然后导出", "append_report_draft"),  # mixed-intent: append + export, 主 family 是 append
    ]

    def test_benchmark_accuracy(self):
        """benchmark 实施：用 OpenAI mock 模拟 model 在 schema 模式下的工具选择.

        因为 reality_test API 实测慢且每次调 LLM 不 deterministic，本测试用
        deterministic mock：mock OpenAI client 的 tool_use 响应，给定 user msg
        + 4 工具 schema，调用现有 _build_provider_messages + 模拟 LLM 决策。

        实际验收（per spec §8.7）：CI 不强制 ≥ 80%，但 cutover smoke (Task 6.3)
        实测中 5 sessions 必须 ≥ 4 个 model 选对工具 + 写盘成功。

        本单测仅验证 schema 形态正确（tool description 含足够区分语义），
        不验证 model 行为。
        """
        from backend.chat import ChatHandler
        handler = self._make_handler_with_project()
        tools = handler._get_tools()
        names = {t["function"]["name"] for t in tools}
        # 4 个工具 schema 都注册
        self.assertEqual(
            names & {"append_report_draft", "rewrite_report_section",
                     "replace_report_text", "rewrite_report_draft"},
            {"append_report_draft", "rewrite_report_section",
             "replace_report_text", "rewrite_report_draft"},
        )
        # 每个工具 description 含明确语义关键词（防 model confusion）
        for t in tools:
            if t.get("function", {}).get("name") == "rewrite_report_section":
                self.assertIn("章/节", t["function"]["description"])
            elif t.get("function", {}).get("name") == "replace_report_text":
                self.assertIn("唯一", t["function"]["description"])
            elif t.get("function", {}).get("name") == "rewrite_report_draft":
                self.assertIn("整篇", t["function"]["description"])
```

**实施提示**：reality_test 用真实 LLM 实测 benchmark 留给 Task 6.3 cutover smoke 5 sessions（每个 session 验证 model 选对工具）。本单测只验证 schema 形态，不模拟 LLM 决策（mock 出来的"决策正确率" 没意义）。
```

- [ ] **Step 2: Run benchmark**

期望：≥ 8/10 通过。如果 < 80%，重新设计工具命名 / description（记录到 plan-stage open question）。

- [ ] **Step 3: Commit benchmark + 结果**

```bash
git add tests/test_tool_selection_benchmark.py
git commit -m "test(benchmark): tool-selection accuracy benchmark suite

Spec §7.8 §8.7: 10 user message × tool schema 选择正确率验收。
通过率 X/10。"
```

### Task 6.3 — Cutover smoke 5 sessions（reality_test）

- [ ] **Step 1: 备份 reality_test conversation_state.json + clear events**

```bash
cd /d/MyProject/CodeProject/consulting-report-agent/reality_test/.consulting-report
cp conversation_state.json "conversation_state.json.before-tools-redesign-$(date +%Y%m%d-%H%M%S)"
python -c "import json; d=json.load(open('conversation_state.json','r',encoding='utf-8')); d['events']=[]; json.dump(d, open('conversation_state.json','w',encoding='utf-8'), ensure_ascii=False, indent=2)"
```

- [ ] **Step 2: 启动 dist/咨询报告助手.exe + open reality_test project**

不点"清空对话"按钮（per fix4 cutover lesson — 会清 events）；如需清 chat history，重启 app。

- [ ] **Step 3: 跑 5 sessions（每个之间用 mtime backup 区分 events）**

| Session | User msg | 期望工具 | 期望行为 |
|---|---|---|---|
| A | "开始写报告吧" | `append_report_draft` | 写盘成功，draft 字数增加 |
| B | "把第二章重写一下" | `rewrite_report_section` | 第二章 snapshot 替换为 model content；其他章节不动 |
| C | "把'团队防御蓝领'改成'团队防御核心'" | `replace_report_text` | unique 字符串替换成功 |
| D | "继续写第三章" | `append_report_draft` | append 第三章新内容 |
| E | "整篇重写，按 outline 用更精炼的语言重写正文" | `rewrite_report_draft` | 整份草稿替换 |

- [ ] **Step 4: 每个 session 后 read events.json 看 mutation events**

```python
# 期望每个 session 写盘成功 → events 含一条 canonical_draft_mutation_set 或类似（如新增 event type 在 Task 3 加）
```

- [ ] **Step 5: 分析 + 写 cutover report**

```bash
# docs/superpowers/cutover_report_2026-05-XX_tools-redesign.md
```

包含：
- 5 sessions 实际行为表
- model 选对工具的比例
- 任何 model behavior issue（区分 backend vs model）
- 跟 fix4 cutover 数据对比

### Task 6.4 — 更新 worklist + memory + handoff

- [ ] **Step 1: 更新 `docs/current-worklist.md`**

把 fix5 candidate（model new_string narrowing）entry 标 RESOLVED（被 redesign 结构性修了）；新加"Phase B 工具重设计完成"entry。

- [ ] **Step 2: 重写 `memory/project-consulting-report-agent-current-focus.md`**

新焦点："工具重设计完成 + 旧 draft-action 系统下线"。

- [ ] **Step 3: 写新 handoff `docs/superpowers/handoffs/2026-05-XX-tools-redesign-done.md`**

- [ ] **Step 4: 更新 `MEMORY.md` index 行**

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/cutover_report_2026-05-XX_tools-redesign.md \
        docs/current-worklist.md \
        memory/project-consulting-report-agent-current-focus.md \
        memory/MEMORY.md \
        docs/superpowers/handoffs/2026-05-XX-tools-redesign-done.md
git commit -m "docs: tools-redesign cutover smoke + worklist/memory/handoff updates

5 sessions cutover smoke 全部 PASS（含 E 整篇重写新增）。fix4 v5 amendment
+ draft-action tag 系统下线。fix5 candidate（model new_string narrowing）
被结构性修了，不再独立 track。

完整 commit chain: ec0b327 fix4 → 70ec0ba fix1 → 07a8269 fix2 → 90d02a9
fix4 cutover → 7f0d207 spec → ... (Task 1-6 commits) → cutover artifacts."
```

### Task 6.5 — Merge to main（push 等用户确认）

- [ ] **Step 1: fast-forward merge to main 本地**

```bash
cd /d/MyProject/CodeProject/consulting-report-agent
git checkout main
git merge --ff-only claude/phase2-draft-action-tag
git log --oneline -10
```

- [ ] **Step 2: 准备 push 命令但不执行**

向用户报告：plan 实施完成 + 所有 commits 在 local main + cutover smoke pass。给用户 push 命令让用户决定何时执行：

```bash
# 等用户 explicit "push 吧" 后执行：
git push origin main
```

**注意**：项目 `~/.claude/CLAUDE.md` 明确指示 "git push 仅用于跨设备同步，不要自动执行，等我说"。本 plan 严守该规则。Task 6.5 只做 local merge，**不 push**——等用户确认。

---

## Self-Review Notes（write 完后由 plan author 自审）

### Spec coverage check

- [x] §1 背景 → Plan 头部 + Task 6.3 cutover 验证
- [x] §2.1-§2.4 4 工具 → Task 3.5/3.2/3.3/3.4 各一个
- [x] §3.1 共享 helpers → Task 1.1/1.3
- [x] §3.2 entry 模板 → Task 3.2-3.5 各工具实现
- [x] §3.3 turn_context 字段 → Task 2.1/2.2
- [x] §3.4 mixed-intent → 共享 helper Task 1.3
- [x] §3.5 write obligation detector + retry → Task 2.3/3.6
- [x] §3.6 mutation limit → 共享 helper Task 1.3 + 工具 entry Task 3.2-3.5
- [x] §3.7 read-mtime → Task 2.4
- [x] §4 stage-ack 保留 + draft-action 删 → Task 5.2 step 2-3
- [x] §5 删除清单 → Task 5
- [x] §6.4 commit 顺序 → Task 1-6
- [x] §7.x risks → 在 ToolTests 里 cover
- [x] §8 验收 → Task 6.3
- [x] §10 plan-stage Q1 (split) → 决定保持单 plan，commit 5/6 之间是 PR boundary

### Placeholder scan

- 测试代码 stub 处（Task 3.3/3.4/3.5/3.6 部分 ToolTests "完整测试代码模式同 Task 3.2，省略以减少 plan 长度"）— 实施时按 Task 3.2 11-case 模式扩出对应数量；这是 plan 内**显式说明**而非 placeholder
- Task 5.2 各 step 用"删 X 函数"格式 — 不需要详细列内部代码（这些是删除操作，引用 spec §5.1 的 deletion table 即可）

### Type consistency check

- 所有共享 helpers 一致 import 自 `backend/report_writing.py`
- `_turn_context["canonical_draft_mutation"]` schema：`{"tool": str, ...}` 在所有 4 工具实现 + helper 一致
- `_turn_context["canonical_draft_write_obligation"]` schema：`{"tool_family": str, "detected": str}` 一致
- `_turn_context["read_file_snapshots"]` schema：`dict[normalized_path, mtime_float]` 一致

---

## TDD ordering note (per plan r1 reviewer §E)

Plan r1 reviewer 指出"most tasks implement first, then write tests"违反严格 TDD（write failing test → fail → minimal implementation → pass）。

**plan author response**：本 plan 的 sub-step 顺序在某些 Task 中是按"helper 函数实现 + 测试函数定义并列"组织的（如 Task 1.1 Step 2 同时给出 `resolve_section_target` 实现 + Task 1.1 Step 3 同时给出测试），而不是严格 "test-first" 顺序。

**executor 实际跑时**仍应 follow TDD：
1. 先看 Step 3 拷贝测试代码到 `tests/test_report_writing.py`
2. Run pytest verify 测试 fail（因为 implementation 还没贴）
3. 再看 Step 2 拷贝实现代码到 `backend/report_writing.py`
4. Run pytest verify 测试 pass
5. Step 5 commit

**plan 中的 step number 是教学顺序（impl 在前展示完整代码）**，**executor 跑时应反序**：先按 step 3 的 test code 写失败 test → run fail → 再写 step 2 的 impl → run pass → step 5 commit。

如果 executor 严格 follow plan step order 就会先 impl 再 test（看似"实现完整就有测试"），但仍能验证正确性（pytest 仍 run）；只是失去了 TDD 的 "test fail 先证明 test 在测真东西" 价值。

如果 reviewer 要求严格 TDD 顺序，plan 全文 sub-step 重排（impl/test 顺序对调）—— 工作量大但纯机械改动。建议 r2 reviewer 评估这是否 mandatory。

## Plan Stage Open Question（如 reviewer 想再压）

1. **Single plan vs split into Phase B-1 + B-2**：本 plan 选 single plan，commit 5/6 之间有天然 PR boundary。如果 codex plan reviewer 强烈建议 split，可在 Task 5 commit 后停下打 PR1，merge 回 main 后再起 PR2 跑 Task 5-6。**spec stage 已 defer 给 plan stage 决定，本 plan 当前选不 split**。

2. **`_inject_synthetic_user_correction` 实现位置**：Task 3.6 假设有一个清晰的注入点（已存在 retry 机制 footprint）。实施时如果发现现有 retry 不在 single 函数里而是分散在 stream/non-stream 两条路径，按 spec §3.5 在两处对称插入；single helper function 抽象更干净。

3. **`_do_append_report_draft` 抽离粒度**：Task 3.5 假设把现有 `append_report_draft` 内部 file mutation 部分抽成 `_do_append_report_draft`。实施时具体抽离方案视当前 `append_report_draft` 函数复杂度而定；如果较小可直接 inline 到新 `_tool_append_report_draft`。
