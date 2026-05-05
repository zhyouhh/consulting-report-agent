# Handoff — Phase 2a Fully Done, Phase 3 Ready

> **⚠️ SUPERSEDED 2026-05-05 深夜**: 本 handoff 写完后又做了一轮 brainstorm + spec/plan 双轮 review，**Phase 3 计划被 redesign 取代**——不再"删 legacy classifier"，而是用 4 个专用工具替换 fix4 v5 整套 tag/gate/scope-enforcement 机制。下一步 cold-start 应该读 [`2026-05-05-tools-redesign-ready-to-implement.md`](2026-05-05-tools-redesign-ready-to-implement.md) 而非本文件。本文件保留作为 fix4 完成时的 snapshot。

**Created:** 2026-05-05 evening (replaces `2026-05-05-phase2-section-replace-pending.md` which is now history)
**Status:** Phase 2a 灰度通道 + fix4 三轮 (impl + fix1 + fix2) + 双轮 review APPROVED + cutover smoke 验证。Phase 3 (Tasks 24-27, 删 legacy classifier + 切主) **可以开始规划**。

This is the cold-start brief for the next session. Read this first.

---

## TL;DR

Phase 2a 包含两阶段：
1. **fix3-completion 阶段** (commits dda3aef → 0a6be28，13 commits 已在 main)：`<draft-action>` tag + gate + compare event + Task 19 fix3 inject preflight_keyword_intent
2. **fix4-completion 阶段** (commits ec0b327 → 07a8269，3 commits 已在 main)：spec §4.12 v5 amendment — section/replace keyword fallback (preflight Step 1.5 + gate edit_file fallback + cached decision + mode promotion)

合计 **16 commits 全在 origin/main HEAD `07a8269`**，41/41 PreflightCheck + Gate pytest pass，cutover smoke A/B 验证 fix4 设计正确。

下一步：**Phase 3 删 legacy classifier**。Phase 3 不阻塞，但**新发现 fix5 candidate（model new_string narrowing）需要独立 track**，跟 Phase 3 无依赖关系。

## fix4 完整 commit 清单（3 commits, all on `main` now）

```
07a8269 fix(rollout): close fix4 round 2 safety holes (Bugs 7-8 partial multi-prefix + snapshot inject)
70ec0ba fix(rollout): address fix4 round 1 rejections (Bugs 1-5 + test tightening)
ec0b327 feat(rollout): section/replace keyword fallback (spec §4.12 v5 fix4)
```

总计 4 文件：`backend/chat.py` (+212 -19) + `tests/test_chat_runtime.py` (+301 -7) + `docs/superpowers/specs/2026-05-04-context-signal-and-intent-tag-design.md` (+30) + `skill/SKILL.md` (+5 -3 incl. fallback note + removed redundant tool-only paragraph)。

## fix4 设计要点（spec §4.12 v5 amendment）

### 1. 取值集合扩展
`preflight_keyword_intent` 从 `{"begin", "continue", None}` → `{"begin", "continue", "section", "replace", None}`。

### 2. 严格安全契约
preflight 输出 `"section"` / `"replace"` 时**必须已 resolve 出唯一目标**——否则保持 `None`，gate 仍然 block（fail-fast，UX ≥ 旧通道）。

### 3. Target resolve 规则
- **replace**：复用 `_parse_report_body_replacement_intent`（已有正则 `REPORT_BODY_REPLACE_TEXT_INTENT_RE`）；要求 draft 存在且 `draft_text.count(old_text) == 1`
- **section**：在 user_message 中抽**章节数字前缀**（`r"第([一二三四五六七八九十百千万0-9]+)(?:章(?!节)|节(?!章)|部分)"` — fix1 添加 negative-lookahead 防 `第二章节` overmatch），用 `label.startswith(prefix)` 在 draft heading nodes 中找候选
- **多 prefix 安全**（fix2 Bug 7）：user_message 含多个章节前缀，**任意一个 unresolved → fail-fast None**；所有 prefix 都 unique resolve 到**同一个** heading 才返回该 heading

### 4. Gate edit_file 分支放行
当 `tag_intents` 为空且 `preflight_keyword_intent ∈ {"section", "replace"}` 时，记录 `tagless_draft_fallback` 事件后放行；其他情况按 v4 §4.8（必须 tag 或 begin/continue keyword）

### 5. 安全契约端到端 (fix2 Bug 8 关键)
- **Bug 8a**：`_required_write_paths_for_turn` + `_build_required_write_snapshots` 优先读 `self._turn_context["canonical_draft_decision"]`（含 fix3 inject 的 fix4 enforcement fields），fallback to 直接 classify 仅在 turn_context 未 populated 时
- **Bug 8b**：`_build_turn_context` 的 fix3 inject 扩展：当 silent preflight 返回 `mode="require"` + `silent_intent ∈ {section, replace}` 且 legacy `mode == "no_write"` 时，**promote** legacy decision 的 mode → `"require"` + priority。**绝不 override** legacy `mode == "reject"`（stage gate / mixed-intent split 仍 authoritative）
- 同时 inject 复制 `rewrite_target_label` / `rewrite_target_snapshot` / `old_text` / `new_text` / `expected_tool_family` / `required_edit_scope` / `intent_kind` 七个字段（仅当 legacy 没 set 时填）

## Cutover smoke 实测结果

**报告文件**：`docs/superpowers/cutover_report_2026-05-05_fix4.md`

| Session | User msg | fallback fired | gate-block | scope-rejected | Verdict |
|---|---|---|---|---|---|
| A | "开始写报告吧" | 1 (intent=begin) | 0 | 0 | ✅ regression OK; draft 2549→3677 |
| B | "把第二章重写一下" | 14 (intent=section) | 0 | 14 | ⚠️ fix4 设计正确（fallback fired），但 model 未缩窄 new_string |
| C | "把'X'改成'Y'" | 0 | 0 | 0 | ❌ no data — model hung 8 min 在 reasoning，未 emit tool_call |
| D | "继续写第三章" | (skipped) | - | - | covered by A regression |

**对比 fix3 cutover**（May 5 14:56）：

| 指标 | fix3 | fix4 | Δ |
|---|---|---|---|
| Section 路径 gate-block 数 | 19 (dead-loop) | 0 | -19 ✅ |
| Section 路径 fallback fired 数 | 0 | 14 | +14 ✅ |
| Begin/continue regression | works | works | unchanged ✅ |
| `_build_required_write_snapshots` populated for tagless section | No | **Yes** (cached decision) | new ✅ |
| Scope enforcement active in fallback path | n/a | **Active** | new ✅ |

## 关键发现 — fix5 candidate (model-behavior issue)

Session B 14 次 fallback fired 之后，模型的 `edit_file.new_string` 反复覆盖多个章节，被 `_validate_required_report_draft_prewrite` scope enforcement 拒绝，error message 已经具体到目标 heading（"本轮要求改写 第二章 跨版本战斗力模拟分析"），但 gemini-3-flash 仍未学会缩窄 new_string 到目标章节。

**性质**：纯 model 行为问题，fix4 后端逻辑全部正确（preflight resolve + inject + cached decision + scope enforcement 全工作）。**与 Phase 3 无依赖关系**：legacy classifier 删了之后，这个 model-narrowing 问题依然存在。

**候选缓解（fix5）**：
1. SKILL.md §S4 加更强引导（fallback 时 new_string 仅限目标章节）
2. System prompt 加 scope 语义说明
3. 后端自动 narrow new_string 到 rewrite_target_snapshot ∩ 模型提交内容（重）
4. 给 model 多举几个 example dialogue（model 行为更可控）

工作量：~1-2 小时（路线 1 / 2，纯文档/SKILL 改动）。**不阻塞 Phase 3**。

## Phase 3 ready

Phase 3 (Task 24-27 per `2026-05-04-context-signal-and-intent-tag.md`)：

- **Task 24**: 删 `_classify_canonical_draft_turn` body + REPORT_BODY_*_KEYWORDS / REPORT_BODY_*_RE 等常量（保留 `REPORT_BODY_REPLACE_TEXT_INTENT_RE` 因为 fix4 复用，但其他可删）
- **Task 25**: 下游 caller 切到 `_preflight_canonical_draft_check`（删 fix3 inject 即可，因为不再有"双通道"，preflight 直接产 master decision）
- **Task 26**: build.ps1 重打包 + reality_test 端到端 smoke
- **Task 27**: worklist + memory 同步

工作量估计：~3-4 小时（含双轮 review）。

**注意 Phase 3 实施前先评估**：
- fix3 inject (`_build_turn_context` lines 6868-area) 在 Phase 3 后是否完全删掉？还是保留作为安全网？建议：删掉 inject、让 `_build_turn_context` 直接调 `_preflight_canonical_draft_check`，但要确认所有下游对 decision 的依赖都对齐到新通道
- 部分 helper（`_required_write_paths_for_turn` / `_build_required_write_snapshots`）是否仍读 `turn_context["canonical_draft_decision"]`？答：是，但 Phase 3 后这个 decision 直接来自 preflight 而非 legacy + inject

## Worktree state

- **Branch**: `claude/phase2-draft-action-tag` (HEAD `07a8269`，与 main 同点)
- **main HEAD**: `07a8269`，已 push origin/main
- **Build**: dist/咨询报告助手/ rebuilt May 5 18:24, 含 fix4 三轮全套
- **reality_test conversation_state.json**: events 14 (cutover smoke session B 残留 section fallback events)
- **reality_test conversation.json**: ⚠️ 不存在（可能在 cutover smoke 期间被某操作清掉了；backup `conversation.json.before-fix4-20260505-182813` 26881 bytes 仍在）
- **reality_test 三个 conversation_state.json backup**: before-fix2-/-fix3-/-fix4-* 都在
- **Worktree path**: `D:\MyProject\CodeProject\consulting-report-agent\.claude\worktrees\happy-jackson-938bd1`
- **Active app**: 咨询报告助手.exe 仍 running（PID 48620 + 56720 + others）— 可继续测，或退出后重建项目状态

## Verification baselines

- pytest PreflightCheckTests + GateCanonicalDraftToolCallTests: 41 PASS (16 + 25)
- Wider sanity (gate / preflight / orchestrator / canonical / compare / extract / required_write): 87 PASS, 0 fail
- frontend tests: not rerun this iteration; baseline 168 from previous Phase 2a
- Cutover smoke (sessions A + B): A pass / B fix4-design-pass + model-narrowing-blocked

## Reference docs

- Spec: `docs/superpowers/specs/2026-05-04-context-signal-and-intent-tag-design.md` (§4.12 v5 amendment in place)
- Plan: `docs/superpowers/plans/2026-05-04-context-signal-and-intent-tag.md`
- Cutover Report fix4: `docs/superpowers/cutover_report_2026-05-05_fix4.md`
- Cutover Report fix3 (历史): `docs/superpowers/cutover_report_2026-05-05_fix3.md`
- Cutover Report fix2 (历史): `docs/superpowers/cutover_report_2026-05-05.md`
- Phase 1 Handoff (历史): `2026-05-04-phase1-impl-handoff.md`
- Phase 2 fix4-pending Handoff (历史，已 superseded): `2026-05-05-phase2-section-replace-pending.md`

## Execution rules (still in force)

1. codex dispatch via PowerShell + inline env injection (`feedback-codex-env-injection-on-stale-shell.md`)
2. `.codex-run/` 文件 convention：`task-N-{stage}-prompt.md` / `last.txt` / `full.log`（fix4 用 `task-fix4-{impl,spec-rN,quality-rN,fix1,fix2}-*` 命名）
3. chat.py 主路径改动 必走 spec + quality 双轮 review
4. Reviewer 不跑 `pytest tests/` 全套（chat_runtime 11k 行，超 codex tool budget）
5. Worktree 用 repo root .venv (`D:\MyProject\CodeProject\consulting-report-agent\.venv\Scripts\python.exe`)
6. build.ps1 私有文件：`managed_client_token.txt` / `managed_search_pool.json` 已在 worktree

## Lessons learned (this iteration)

1. **多轮 review 是必要的**：fix4 v1 表面正确（41 tests pass），但 spec r1 catch `改为` keyword 缺失 + regex overmatch + ambiguous test 名不副实；quality r1 catch silent preflight 字段没 propagate 到 turn_context（破安全契约）。fix1 后 r2 又 catch partial multi-prefix safety hole + snapshot builder 用 legacy classify。fix2 后 r3 全部 APPROVED。如果跳过 review 直接 cutover，会有 silent safety 漏洞。
2. **codex 实施 + 双轮 review 时间成本**：~10-15 min impl + 5-10 min reviewer × 2 + 10 min fix1 impl + 10 min reviewer × 2 + 10 min fix2 impl + 10 min reviewer × 2 = ~80-100 min total。比直接 trial-and-error 快得多。
3. **Cutover smoke 暴露 model-behavior issue**：fix4 后端正确不代表 model 真的能正确使用。这种 case 不能在 unit test 抓到，必须 e2e 跑。Session B 是有价值的 finding（fix5 candidate）。
4. **Computer-use cutover smoke 有限制**：Session C model hung 8 min 没 emit tool_call，无法判断是 model 真的卡住还是后端 hang。需要更可观测的 monitor 工具，或更短的 retry 循环。
5. **"清空对话" 按钮副作用**：会清 events.json 而不只是 conversation history（实测：Session A events 在 Session B 开跑时全部消失，说明"清空对话" 清了整个 conversation_state.json 内的 events 字段，跟 backup 期待不一致）。下次 cutover smoke 之间不要点这个按钮，让 events 累积；分析时按时间戳分割。

## Don't do (still in force)

- ✅ Phase 3 可以开始（Task 24-27）
- 不要再做 fix5 之前去 Phase 3 — fix5 不阻塞，可并行
- 不要把 model-narrowing 问题归咎到 fix4 上（fix4 后端正确）
- 不要破坏 Phase 2a "灰度并行" 约束（即使 Phase 3 删 legacy classifier，也不要把 mode/priority overide 风险带进 Phase 3 的新 preflight master）
- 不要 push 全套 cutover decision 给 user 决定 — fix4 的 cutover 已足够，下次会话直接进 Phase 3 准备工作
