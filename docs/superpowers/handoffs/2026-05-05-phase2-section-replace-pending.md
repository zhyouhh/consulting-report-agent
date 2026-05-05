# Handoff — Phase 2a Done, Section/Replace Fallback Pending Before Cutover

**Created:** 2026-05-05
**Status:** Phase 2 已实施 13 commits (含 Task 19 三轮 fix)，cutover smoke 跑完，发现 section/replace 路径在新通道**比旧通道更差**——必须先修这个，再切主进 Phase 3。

This is the cold-start brief for the next session. Read this first.

---

## TL;DR

Phase 2 (`<draft-action>` tag + gate + compare event 灰度通道) 全部实施 + 双轮 review 完毕，13 commits 已合并到 `main`。Cutover smoke 跑了 4 sessions 暴露 **section/replace 路径架构缺口**：begin/continue 有 keyword fallback 兜底（Bug A 修好），但 section/replace 没 fallback，model 不发 tag 时 gate 死循环 19 次到 max_iterations。**比旧通道（fail fast）UX 更差**。下次会话第一件事修这个，然后才能 Phase 3 切主。

## Phase 2 完整 commit 清单（13 commits, all on `main` now）

```
6112a75 fix(rollout): inject preflight_keyword_intent into canonical_draft_decision (Task 19 fix3)
a89b081 fix(draft-action): move gate to _execute_plan_write before legacy block (Task 19 fix2)
f6ed0e9 docs(skill): add draft-action tag spec to §S4 (B1)              [Task 22]
5a6a5b8 feat(rollout): cutover review report script (Phase 2a)         [Task 21]
ab91fda fix(rollout): silent compare writer preflight to avoid notice leak (Task 20 fix1)
cf445e2 feat(rollout): structured draft_decision_compare + exception events (Phase 2a) [Task 20]
d603042 fix(draft-action): restrict tagless fallback to no-executable-tag turns (Task 19 fix1)
dc2a321 feat(draft-action): tool gate + tagless fallback (B1)         [Task 19]
6e956fb fix(draft-action): use section-label-as-prefix matcher per §4.6 (Task 18 fix1)
1a15b12 feat(draft-action): pre-validation rules per spec §4.6 (B1)    [Task 18]
dda3aef feat(preflight): add _preflight_canonical_draft_check parallel to legacy classifier (B1) [Task 17]
234c0fb feat(stream): extend tail-guard scan for draft-action markers (B1) [Task 16]
8940d70 feat(draft-action): parser module + position rules (B1)        [Task 15]
```

5 个 fix（Task 18 fix1 / Task 19 fix1/fix2/fix3 / Task 20 fix1）每个都是 reviewer catch 出真问题修的。

## Cutover smoke 实测结果

**报告文件**：`docs/superpowers/cutover_report_2026-05-05_fix3.md`（fix3 后跑的 4 sessions）。

| Session | User msg | old.mode | new.mode + kw | fallback | 实际行为 |
|---|---|---|---|---|---|
| A | "开始写报告吧" | no_write (P10) | require + begin | ✅ | append_report_draft fallback pass，draft 写成功 (5882 字节) |
| B | "把第二章重写一下" | no_write (P10) | no_write + None | - | model 调 19 次 edit_file 全程不发 tag → gate block 19 次 → max_iter |
| C | "把'体能'改成'力量'" | no_write | no_write | - | draft 没"体能"字符串 → edit_file tool string check fail（不到 gate） |
| D | "继续写第三章" | no_write (P10) | require + continue | ✅ | append_report_draft fallback pass，draft 写成功 |

**指标对比**：

| 指标 | fix2 数据 | fix3 数据 | 阈值 |
|---|---|---|---|
| 一致率 | 25% | 50% | ≥95% ❌ |
| blocked_missing_tag | 3 | 1 | 0 ❌ |
| fallback_used | 0 | 2 | controlled |
| 异常数 | 0 | 0 | ✓ |

字面阈值不达标，但 2 个不一致都是 NEW BETTER（旧通道误判 begin/continue → 新通道修了 Bug A）。

## 核心问题：section/replace 路径架构缺口

### 现象（来自 reality_test 实测）

User "把第二章重写一下" → model 看完 draft 后**反复调 19 次 edit_file，全程不发 `<draft-action>` tag**，gate 全部 block，死循环到 max_iterations 退出。

### 为什么 model 不发 tag

不是 tag 形式复杂（section tag `<draft-action>section:第二章</draft-action>` 跟 begin tag 复杂度相似）。是 **model 的推理偏好**：当 user 用动词（"重写"/"改"）时，model 倾向直接调 edit_file，不愿先输出"声明 tag"。SKILL.md §S4 (Task 22 才加) 引导力度不够。

### 为什么 spec §4.2 没给 section/replace 加 keyword fallback

设计理由：位置模糊（"重写第二章"重写哪节？），必须显式 tag 定位。

### 为什么旧通道也不 work

旧通道有 `_resolve_section_rewrite_targets`，但它的匹配条件是 `heading.label in user_message` —— 要求 heading 完整 label 是 user message 的子串。draft heading 像 "第二章 跨版本战斗力模拟分析"，user 只说"第二章"，子串条件**几乎从不满足**。所以旧通道实际也大量 fail。

### 旧 vs 新 UX 对比

| 路径 | 旧通道 | 新通道（当前 main） |
|---|---|---|
| section "重写第二章" | mode=no_write → "本轮用户没要求修改正文草稿" → fail fast | gate block × 19 → 死循环到 max_iter | 
| replace "把 X 改 Y" | 同上 | 同上 |

**新通道 UX 比旧通道差**——这是切主进 Phase 3 前必须修的。

## 下次会话要做：fix4 — section/replace keyword fallback

### 目标

让 `_preflight_canonical_draft_check` 也输出 section/replace 的 keyword intent + heading prefix match，让 gate 在 model 不发 tag 时也能 fallback pass。

### 推荐方案（A'：spec §4.2 amendment）

1. 扩展 `_DRAFT_INTENT_PREFLIGHT_KEYWORDS` 加 `section` / `replace` 类（如 `["重写", "把.*改成", "替换"]`）
2. 改 `_preflight_canonical_draft_check`：`preflight_keyword_intent` 字段允许 `section` / `replace` 值
3. 改 `_resolve_section_rewrite_targets` 的匹配规则：从"heading 完整 label 是 user msg 子串" 改成"heading 数字前缀（如 `第N章`）匹配 user msg 的 section_label"
4. `_gate_canonical_draft_tool_call` 的 `edit_file` 分支加 keyword fallback：tag 缺失但 keyword=section/replace 且能定位到 heading → record fallback + pass
5. 加防御测试：注入 `intent_kind="section"` 不能让 gate 通过（仅 `preflight_keyword_intent` 可信）
6. **更新 spec** §4.2：把"section/replace 必须 tag"改成"section/replace 优先 tag, fallback 走 prefix-match keyword"
7. **更新 SKILL.md** §S4：保留 tag 鼓励但说明 fallback 存在

工作量估计：~2-3 小时（实施 + 双轮 review + 重 build + 重跑 cutover）。

### 备选方案（B：保守，不动 spec）

只改 SKILL.md §S4 加强 tag 引导（example dialogue + 强制 wording），看 model 是否学会发 tag。但 reality_test 数据说服力差（4 sessions 0 次发 section tag），效果存疑。

## Phase 3 仍然 pending

修完 section/replace + 重测 cutover 一致率 / blocked count 上去后，**才能**进 Phase 3 (Task 24-27)：
- Task 24: 删旧 `_classify_canonical_draft_turn` + REPORT_BODY_*_KEYWORDS 等常量
- Task 25: 下游 caller 切到 `_preflight_canonical_draft_check`
- Task 26: build.ps1 重打包 + reality_test 端到端
- Task 27: worklist + memory 同步

## Worktree state

- **Branch**: `claude/phase2-draft-action-tag` (HEAD `6112a75`，已合并到 main)
- **Build**: dist/咨询报告助手/ 91MB May 5 14:56（含 fix2/fix3）
- **reality_test conversation_state.json**: events 已清空过 2 次（before-fix2 / before-fix3 backup 在 reality_test/.consulting-report/）
- **Worktree path**: `D:\MyProject\CodeProject\consulting-report-agent\.claude\worktrees\happy-jackson-938bd1`

## Verification baselines

- pytest GateCanonicalDraftToolCallTests: 13 → 17 passed (fix3 加了 4 个新 e2e test)
- Wider sanity (gate / preflight / orchestrator / canonical / compare): 70 passed, 0 failed
- frontend tests: 168 passed
- Cutover smoke (4 sessions reality_test): A/D fallback work, B/C exposed section/replace gap

## Reference docs

- Spec: [docs/superpowers/specs/2026-05-04-context-signal-and-intent-tag-design.md](../specs/2026-05-04-context-signal-and-intent-tag-design.md) — §4.2 是 section/replace 的硬约束源头，fix4 时要 amend
- Plan: [docs/superpowers/plans/2026-05-04-context-signal-and-intent-tag.md](../plans/2026-05-04-context-signal-and-intent-tag.md)
- Cutover Report (fix3 后): [cutover_report_2026-05-05_fix3.md](../cutover_report_2026-05-05_fix3.md)
- Phase 1 Handoff (已 merged): [2026-05-04-phase1-impl-handoff.md](2026-05-04-phase1-impl-handoff.md)

## Execution rules (still in force)

1. codex dispatch via PowerShell + inline env injection (`feedback-codex-env-injection-on-stale-shell.md`)
2. `.codex-run/` three files per task: `task-N-{stage}-prompt.md` / `last.txt` / `full.log`
3. chat.py 主路径改动 必走 spec + quality 双轮 review
4. Reviewer 不跑 `pytest tests/` 全套（chat_runtime 11k 行，超 codex tool budget）
5. Worktree 用 repo root .venv (`D:\MyProject\CodeProject\consulting-report-agent\.venv\Scripts\python.exe`)
6. build.ps1 私有文件：`managed_client_token.txt` / `managed_search_pool.json` 已在 worktree
