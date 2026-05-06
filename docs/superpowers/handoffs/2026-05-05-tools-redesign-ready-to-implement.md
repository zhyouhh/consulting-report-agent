# Handoff — Tools Redesign Spec/Plan APPROVED, Ready to Implement Task 1-6

> **⚠️ SUPERSEDED 2026-05-06**: 实施已完成。下一步看 [`2026-05-06-tools-redesign-impl-done-smoke-pending.md`](2026-05-06-tools-redesign-impl-done-smoke-pending.md)（cutover smoke 5 sessions pending + merge pending）。本文件保留作为历史 context。

**Created:** 2026-05-05 evening (replaces `2026-05-05-phase2a-fully-done-phase3-ready.md` which is now history)
**Status:** Spec + plan 已通过 codex 双轮 review，待实施 Task 1-6（按 spec §6.4 + plan 6 commit 顺序）。

This is the cold-start brief for the next session. Read this first.

---

## TL;DR

fix4 cutover smoke 暴露 model 不擅长精确复述 1500 字 old_string —— 这不是 SKILL.md 引导能修的，是 model 能力硬约束。决策：**结构性消除 model 控制 old_string 的需求**——把"重写章节"做成**专用工具** `rewrite_report_section(content)`，backend 自己用 preflight 已 resolve 的 `rewrite_target_snapshot` 当 old_string。

scope 扩展：4 个专用工具（`append_report_draft` 重构 + 新增 `rewrite_report_section` / `replace_report_text` / `rewrite_report_draft`）替代 fix4 v5 的 `<draft-action>` tag + gate fallback + scope enforcement 整套机制。`<stage-ack>` tag 系统不动。

净简化：删 ~1300 行后端代码 + cleanup 一批 dead 测试代码。

## 文档

| Doc | 路径 | Status |
|---|---|---|
| Spec | `docs/superpowers/specs/2026-05-05-report-tools-redesign-design.md` | 4 轮 codex review APPROVED_WITH_NOTES（HEAD `7f0d207`） |
| Plan | `docs/superpowers/plans/2026-05-05-report-tools-redesign.md` | 2 轮 codex review APPROVED（HEAD `1030d7b`，2203 行 6 大 Task） |
| Old spec | `docs/superpowers/specs/2026-05-04-context-signal-and-intent-tag-design.md` §4.3-§4.12 | **superseded**（实施 Task 5.4 Step 0 时加 markdown banner，目前还没标） |
| Old plan | `docs/superpowers/plans/2026-05-04-context-signal-and-intent-tag.md` Phase 3 (Tasks 24-27) | **被 redesign plan 取代**（不再独立做 Phase 3） |

## 实施 Task 拆分（plan 6-commit 顺序）

| Task | 内容 | 预估 | 风险 |
|---|---|---|---|
| 1 | `backend/report_writing.py` helpers + `tests/test_report_writing.py` | 30 min | 0 — 纯加法 |
| 2 | turn_context 新字段 (`user_message_text` / `canonical_draft_write_obligation` / `read_file_snapshots`) + `_detect_canonical_draft_write_obligation` + `read_file` mtime hook | 30 min | 0 — 纯加法 |
| 3 | 4 个工具实现（`_tool_rewrite_report_section` / `_tool_replace_report_text` / `_tool_rewrite_report_draft` 新增 + `append_report_draft` 重构）+ `_get_tools` schema + `_execute_tool` dispatch + 4 ToolTests + `_chat_*_unlocked` no-tool-call retry | 2-3 hr | 中 — 4 工具有共享模板但参数不同 |
| 4 | SKILL.md §S4 重写为 4-tool 表格 + 删附录 + 改 chat.py:5289/7459/7521 user_action wording | 30 min | 0 — 文档改动 |
| 5 | 删旧 code（`<draft-action>` 系列 + classifier + gate + 各 record helpers + `_validate_append_turn_canonical_draft_write` + 大量常量）+ 删测试（DraftActionParser / GateCanonicalDraftToolCall / DraftDecisionCompareEvent / DraftActionPreCheck）+ 标记 spec §4.3-§4.12 superseded + 修剪 PreflightCheckTests / StreamSplitSafeTailDraftActionTests | 1 hr | 中 — 大量删除，前 4 commit 已替代功能 |
| 6 | dist rebuild + verify size ±5% + cutover smoke 5 sessions（A begin / B section / C replace / D continue / E whole rewrite）+ cutover_report + worklist/memory/handoff 更新 | 1 hr | 中 — 实测 model 选对工具的概率 |

总预估 ~5-6 小时（含 codex dispatch + double review per task）。

PR boundary：commit 5/6 之间天然 split——前 5 是 "B-1"（加工具但不删旧；可独立 ship 验证 model 学会用新工具），commit 5+ 是 "B-2"（删旧 + 文档更新）。**plan 选 single-PR 一次走完**。

## 关键设计要点（spec 总结）

### 1. 取值集合扩展 → 4 工具
`append_report_draft(content)` (重构) + `rewrite_report_section(content)` (新) + `replace_report_text(old, new)` (新) + `rewrite_report_draft(content)` (新)。每个工具入口 inline check 全套 invariant。

### 2. 系统侧三层保护
- **Write-obligation detector**: turn-start `_detect_canonical_draft_write_obligation` 用粗粒度 keyword 标 turn_context；turn-end 在 `_chat_stream_unlocked` / `_chat_unlocked` no-tool-call 分支检测 obligation + 0 mutation + assistant 文本声称已写 → 注入 corrective user message + retry
- **One mutation per turn**: 共享 helper `check_no_prior_canonical_mutation_in_turn` + 工具成功写盘后 set `turn_context["canonical_draft_mutation"]`
- **Read-mtime tracking**: `read_file` 完成时记录 canonical draft mtime 到 `turn_context["read_file_snapshots"]`；写工具入口 `check_read_before_write_canonical_draft` 检查 mtime 未变

### 3. 内容范围限制（防 model 过宽 content）
- `rewrite_report_section.content` 必须以 `## ` 开头 + 仅 1 个 `## ` heading + 长度 ≤ `max(3000, 3 * target_snapshot.length)`
- `rewrite_report_draft.content` 必须以 `# ` 开头 + ≥ 1 个 `## ` heading + 长度 ≤ `max(8000, 2 * current_draft.length)`

### 4. 删除范围
backend：`backend/draft_action.py` 整 module / `_classify_canonical_draft_turn` / `_preflight_canonical_draft_check` / `_gate_canonical_draft_tool_call` / `_record_*` 4 helpers / `_validate_append_turn_canonical_draft_write` / `_run_phase2a_compare_writer` / `REPORT_BODY_*_KEYWORDS` 系列大部分 / `_DRAFT_ACTION_MARKER` 等常量 / `_finalize_assistant_turn` 中 draft-action 步骤。

测试：`tests/test_draft_action.py` 整文件 / `GateCanonicalDraftToolCallTests` / `DraftActionPreCheckTests` / `DraftDecisionCompareEventTests` / `DraftActionParserTests` / `PreflightCheckTests` 大部分 / `StreamSplitSafeTailDraftActionTests` 修剪。

工具脚本：`tools/draft_decision_compare_report.py`。

## 实施 Open Question（plan 阶段已 punt 给实施）

1. ✅ Single plan vs split — plan 选 single, commit 5/6 PR boundary
2. ⚠️ `_inject_synthetic_user_correction` 实施位置：grep 现有 retry 路径如 fix3 inject 触发的 retry 看 pattern；plan 提议用 `_maybe_inject_obligation_retry` 聚合 helper 让两条 chat path 各调一次，便于单测
3. ⚠️ `_do_append_report_draft` 抽离粒度：视当前 `append_report_draft` 函数复杂度决定；如果较小可直接 inline 到新 `_tool_append_report_draft`

## TDD ordering note

plan 的 sub-step 顺序是**教学顺序**（Step 2 给完整实现 + Step 3 给完整 test 代码）。**executor 跑时**应反序：
1. 先看 Step 3 拷贝 test 代码到 `tests/`
2. Run pytest verify 测试 fail（因为 implementation 还没贴）
3. 再看 Step 2 拷贝 impl 代码到 `backend/`
4. Run pytest verify 测试 pass
5. Step 5 commit

如果严格按 plan step order 跑（impl 先 test 后），仍能验证正确性，只是失去 TDD "test fail 先证明 test 在测真东西" 价值。Plan reviewer r2 接受这个 pedagogical 顺序。

## 实施前必须 grep 确认的引用

per spec §B6/B7/B8：

```bash
# REPORT_BODY_*_KEYWORDS 各常量真实引用
grep -rn "REPORT_BODY_FIRST_DRAFT_KEYWORDS\|REPORT_BODY_EXPLICIT_CONTINUATION_KEYWORDS\|REPORT_BODY_WHOLE_REWRITE_KEYWORDS\|REPORT_BODY_SECTION_REWRITE_KEYWORDS\|REPORT_BODY_REPLACE_TEXT_INTENT_RE\|REPORT_BODY_INLINE_EDIT_RE" backend/

# required_write_snapshots 全 callers
grep -rn "required_write_snapshots\|_required_writes_satisfied\|_build_required_write_snapshots" backend/ tests/

# _validate_required_report_draft_prewrite chat.py:7156 第二调用点
grep -n "_validate_required_report_draft_prewrite" backend/chat.py
```

实施 Task 5.2 时按 grep 结果调整 deletion 边界。

## Worktree state

- **Branch**: `claude/phase2-draft-action-tag` (HEAD `1030d7b`，与 main 同点)
- **main HEAD local**: `1030d7b`（plan v1 + v2 commits，**未 push**）
- **origin/main**: `7f0d207`（spec v4-clean，已 push）
- **Build**: dist/咨询报告助手/ 91 MB May 5 18:24（含 fix4 三轮全套；redesign 实施完后 Task 6.1 重 build）
- **reality_test**: 14 events (fix4 cutover Session B section fallback 残留)；conversation.json **不存在**（cutover 期间被某操作清掉），backup `conversation.json.before-fix4-20260505-182813` 26881 bytes 仍在
- **Active app**: 咨询报告助手.exe 之前还 running，可继续测，或退出后重建 project state
- **Worktree path**: `D:\MyProject\CodeProject\consulting-report-agent\.claude\worktrees\happy-jackson-938bd1`

## Verification baselines

- pytest (current): 87 PASS, 0 fail (含 fix4 三轮测试)
- frontend tests: 168/168 pass
- 实施完成后期望（spec §8 验收 + plan Task 6 acceptance）：
  - chat_runtime suite 0 fail (整删除 class 不再存在)
  - 4 工具 ToolTests 各 7-11 case 全 PASS
  - WriteObligationRetryTests 4 case + StageAckRegressionTests 6 case + ReadBeforeWriteSnapshotTests 2 case + CanonicalMutationLimitTests case 全 PASS
  - frontend 168/168 不变
  - dist 大小 ±5% (~86-96 MB)
  - cutover smoke 5 sessions ≥ 4 个 model 选对工具 + 写盘成功

## Reference docs

- Spec: `docs/superpowers/specs/2026-05-05-report-tools-redesign-design.md`
- Plan: `docs/superpowers/plans/2026-05-05-report-tools-redesign.md`
- Old spec (history, partly superseded): `docs/superpowers/specs/2026-05-04-context-signal-and-intent-tag-design.md`
- Old handoff (history): `docs/superpowers/handoffs/2026-05-05-phase2a-fully-done-phase3-ready.md`
- fix4 cutover report (history): `docs/superpowers/cutover_report_2026-05-05_fix4.md`

## Execution rules (still in force)

1. codex dispatch via PowerShell + inline env injection (`feedback-codex-env-injection-on-stale-shell.md`)
2. `.codex-run/` 文件 convention：`task-N-{stage}-prompt.md` / `last.txt` / `full.log`
3. chat.py 主路径改动 必走 spec + quality 双轮 review
4. Reviewer 不跑 `pytest tests/` 全套（chat_runtime 11k 行，超 codex tool budget）
5. Worktree 用 repo root .venv (`D:\MyProject\CodeProject\consulting-report-agent\.venv\Scripts\python.exe`)
6. build.ps1 私有文件：`managed_client_token.txt` / `managed_search_pool.json` 已在 worktree
7. **git push 等用户 explicit 指令**（per ~/.claude/CLAUDE.md "git push 仅用于跨设备同步，不要自动执行"）

## Don't do (still in force)

- 不要再做 fix5 SKILL.md narrowing — 被 redesign 结构性修了，工具内 backend 自己控制 old_string 不需要 model 缩窄
- 不要再独立做 Phase 3 (Task 24-27 删 legacy classifier) — 本 redesign Task 5 一并删
- 不要破坏 stage-ack 系统：`_TAIL_GUARD_MARKERS` / `TAIL_TAG_SCAN_RE` / `stream_split_safe_tail` / `backend/stage_ack.py` 不动；Task 5.2 删 draft-action 时小心区分
- 不要 push 到 origin/main 未经用户 explicit 指令

## Lessons (from spec/plan review iterations)

- **多轮 review 必要**：spec r1-r4 + plan r1-r2 各自 catch 真问题（spec r1 4 结构性 gap / spec r2 retry 位置错 / spec r3 cross-ref stale / plan r1 placeholder + push 命令冲突）。一次性写完不审就 ship 几乎肯定 break。
- **Plan 阶段 catch 文档约束冲突**（push 不能自动跑）— `~/.claude/CLAUDE.md` 全局规则在 plan 时容易忘
- **Reviewer 给具体 fix 命令**（如 §B9 grep 6 处 `<draft-action>` 字符串、reviewer §13 给的 regex 模板）— 比抽象意见有用，下次 prompt 模板可学
- **承认 fix4 工作大部分会被废弃**（工具入口替代 tag-based gate）— 但 fix4 的 evidence (cutover Session B 14 次失败具体到 old_string 错) 是 redesign 决策的根本输入
