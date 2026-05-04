# Handoff — Phase 1 Implementation Complete (reality_test pending)

**Created:** 2026-05-04
**Status:** Phase 1 done (16 commits on `claude/happy-jackson-938bd1`); Phase 2/3 + reality_test smoke pending.

This is the cold-start briefing for the next session. Phase 1 of the `2026-05-04-context-signal-and-intent-tag` plan is fully implemented and packaged. Read this first.

---

## What this work is

Implements **Phase 1** of the spec `docs/superpowers/specs/2026-05-04-context-signal-and-intent-tag-design.md` (5-round APPROVED) + plan `docs/superpowers/plans/2026-05-04-context-signal-and-intent-tag.md` (6-round APPROVED). Targets 5 reality_test bugs under one root cause: **"signal channel layering misalignment" between backend and model**.

Phase 1 covers groups A1 (system_notice surface_to_user layering), A2 (quality_progress feedback), A3 (empty assistant fallback + sanitize), C1 (tool-log HTML comment), and the 7-step orchestrator integrating them. **Phase 2 (B1) and Phase 3 (cutover) are NOT done** — see "Next steps" below.

## Bug status (5 bugs from reality_test)

| Bug | Description | Phase 1 status |
|---|---|---|
| A | 门禁误判 (`_classify_canonical_draft_turn` keyword chain) | **Pending Phase 2** — replaced by `<draft-action>` tag (Tasks 15-23) |
| B | 黄框污染 (read-before-write internal nudge surfaced to user) | ✅ Fixed (A1: `surface_to_user=False` filter) |
| C | 阈值黑盒 (progress.md never showed 5/7 numerics) | ✅ Fixed (A2: `_render_progress_markdown` + `quality_hint` in tool_result) |
| D | 兜底黑洞 (`"（本轮无回复）"` polluted history) | ✅ Fixed (A3: `_finalize_empty_assistant_turn` + 3-layer sanitize) |
| E | 工具历史零记忆 (tool_calls dropped from history) | ✅ Fixed (C1: `<!-- tool-log -->` HTML comment in persisted assistant content) |

## What's done in this branch

**Branch:** `claude/happy-jackson-938bd1`
**Base:** `main` at commit `e31ab78` (2026-05-04 worklist sync, max_iterations=20)
**HEAD:** `e29f120`
**16 commits** (chronological):

```
5143720  docs: add 2026-05-04 context-signal-and-intent-tag spec & plan
70f9b48  feat(notice): add SystemNotice.surface_to_user required field (A1 prep) [Task 1]
da8bc1c  feat(notice): dual dedupe + audit 18 sites + constructor pass-through (A1) [Task 2]
82b9a8f  test(notice): pass surface_to_user in chat endpoint mock (A1 followup) [Task 2 fix1]
763b954  feat(notice): server + frontend filter surface_to_user=false (A1) [Task 3]
b8d7e64  feat(progress): render quality_progress for S2/S3 (A2) [Task 4]
544d7b9  feat(progress): attach quality_hint to tool_result for S2/S3 (A2) [Task 5]
6e3b448  feat(empty-turn): unify fallback into helper, do not persist (A3) [Task 6]
c4a3229  feat(provider-build): coalesce consecutive user messages (A3) [Task 7]
2cc29cd  feat(history): three-layer sanitize legacy fallback (A3) [Task 8]
69a2b5a  feat(tool-log): pair tool_calls with results by id (C1) [Task 9]
c2f02ba  feat(tool-log): append summary block before tail tags (C1) [Task 10]
f5802e4  feat(tool-log): strip helper backend + frontend (C1) [Task 11]
d66269c  feat(tool-log): three-layer sanitize (C1) [Task 12]
5d9255b  refactor(chat): extend _finalize_assistant_turn to 7-step orchestrator (A3+C1) [Task 13]
462657b  fix(chat): orchestrator returns persisted_content (Task 13 followup)
e29f120  test(chat): update assertions for orchestrator persisted_content return (Task 13 followup)
```

## Verification status

- ✅ pytest full suite: **713 passed / 1 skipped / 0 failed** in 21 min (project venv at repo root)
- ✅ Frontend tests: **168 passed / 0 failed** (chatPresentation, toolLogStrip)
- ✅ build.ps1 success: **dist/咨询报告助手/ 91 MB** ready to launch
- ⏸️ **reality_test smoke pending** — exe is built, user has not yet manually loaded reality_test project to verify the 4 fixed bugs do not reproduce

## Next steps

### Step 1 — reality_test smoke (Task 14)

Launch `dist/咨询报告助手/咨询报告助手.exe`, load project at `D:\MyProject\CodeProject\consulting-report-agent\reality_test\`, verify checklist from plan §Task 14 Step 3:

- 黄框：之前每轮触发的"本轮要修改的文件 X 已存在，请先调用 read_file" **不再出现** (Bug B)
- progress.md：S2/S3 阶段 system prompt 包含 `**质量进度**: 5/7 条 有效来源` (Bug C — check backend log)
- 空回复：触发 stream 截断后用户面看到 "（这一轮我没有产出可见回复...）"，下一轮模型 prompt 中**没有**这条文本 (Bug D)
- tool-log：assistant 持久化 content 含 `<!-- tool-log ... -->`；用户 UI 看不到；复制按钮粘贴出来不含 (Bug E)
- 历史 sanitize：reality_test 旧 conversation.json 中 "（本轮无回复）" 不再展示

**Note:** Bug A reproduction (开始写报告吧 → keyword classifier blocking) is EXPECTED to still occur — Phase 2 fixes it.

### Step 2 — Phase 2 (Tasks 15-23): B1 draft-action tag, gradual rollout

Tasks 15-22 introduce `<draft-action>` tag parser + parallel preflight (does NOT delete keyword classifier). Task 23 is the cutover decision point — produces 5-session compare report and **MUST be reviewed by human** before deletion.

Plan locations:
- Task 15-16: new `backend/draft_action.py` module + tail guard prefix
- Task 17-20: parallel preflight (`_preflight_canonical_draft_check`) + event parse + `_gate_canonical_draft_tool_call` + draft_decision_compare event
- Task 21-22: tools/draft_decision_compare_report.py script + SKILL.md §S4 update
- Task 23: 5-session reality_test + human-reviewed cutover artifact

### Step 3 — Phase 3 (Tasks 24-27): cutover + cleanup

After Task 23 cutover approved by user:
- Task 24: delete `_classify_canonical_draft_turn` keyword internals + dead helpers (grep-verified)
- Task 25: downstream reference cleanup + regression
- Task 26: build.ps1 repackage + reality_test e2e
- Task 27: worklist + memory sync

## Execution rules (still in force)

Same as 2026-04-21 handoff. Highlights:

1. **codex dispatch via PowerShell + inline env injection** (`feedback-codex-env-injection-on-stale-shell.md`):
   ```powershell
   $env:CODEX_CCTQ_CODEX_PRO_0_3__API_KEY = [Environment]::GetEnvironmentVariable("CODEX_CCTQ_CODEX_PRO_0_3__API_KEY", "User")
   cmd /c "codex exec --cd `"$pwd`" --color never --output-last-message .codex-run\task-N-last.txt < .codex-run\task-N-prompt.md > .codex-run\task-N-full.log 2>&1"
   ```
   Run with `run_in_background: true`.

2. **`.codex-run/` three files per task:** `task-N-impl-prompt.md` / `task-N-impl-last.txt` / `task-N-impl-full.log`. Reviewer rounds use `task-N-spec-r1-*` and `task-N-quality-r1-*`. Worktree-local, gitignored. Phase 1's full task artifacts are still on disk under `.codex-run/`.

3. **20-min liveness cron:** off-minute, kill at 30 min stale, CronDelete on completion, silent otherwise. Three-line meta cron prompt; do NOT poll every 5 min.

4. **Two-stage review per task:** spec compliance reviewer (codex) → code quality reviewer (codex). REJECT requires fix and re-review. Independent helper / pure additive task can skip quality (controller self-check). chat.py main-path / multi-site / orchestrator changes MUST do both.

5. **DO NOT run `pytest tests/` full suite inside reviewer prompts** — chat_runtime suite is 11k lines and consistently times out codex's tool budget. Reviewers use targeted `tests/test_<module>.py` runs + segmented sanity. Controller does the full sweep at Phase boundaries.

6. **Project venv path:** worktree has no local `.venv`; tests must use `D:\MyProject\CodeProject\consulting-report-agent\.venv\Scripts\python.exe`.

7. **Build private files:** `managed_client_token.txt` and `managed_search_pool.json` are NOT in worktree by default (gitignored, so `git worktree add` does not copy them). Before running `build.ps1`, copy from repo root:
   ```bash
   cp ../../../managed_client_token.txt ../../../managed_search_pool.json .
   ```

## Worktree state at handoff

- **Untracked:** `.serena/`, `.codex-run/`, `dist/`, `frontend/node_modules/`, `.venv/`, `managed_client_token.txt`, `managed_search_pool.json` — all gitignored
- **Worktree path:** `D:\MyProject\CodeProject\consulting-report-agent\.claude\worktrees\happy-jackson-938bd1`
- **Branch:** `claude/happy-jackson-938bd1` (NOT pushed yet — PR creation is the last step of Task 14, deferred until reality_test smoke passes)

## Why Phase 1 took longer than estimated

Lessons for Phase 2 cost estimation:

- **chat_runtime suite size (11k lines, 374-381 tests)** is the dominant time cost. Each spec/quality reviewer that runs full suite incurs 5-15 min and often times out codex tool budget. Phase 2 will hit the same wall.
- **Task 13 (orchestrator integration)** had a 2-round fix loop: spec rejected on `return visible_content` (one-line), then 14 cascading test assertion failures because old tests asserted exact content equality but new behavior persists tool-log into content. Total ~30 min extra. Phase 2 Task 19 (`_gate_canonical_draft_tool_call` main-path) has similar risk profile.
- **Worktree setup overhead:** missing `.venv`, missing private build files — first-time worktree adds 5-10 min friction. Phase 2 fresh session should pre-copy these.

## Reference docs

- Spec: [2026-05-04-context-signal-and-intent-tag-design.md](../specs/2026-05-04-context-signal-and-intent-tag-design.md)
- Plan: [2026-05-04-context-signal-and-intent-tag.md](../plans/2026-05-04-context-signal-and-intent-tag.md)
- Worklist: [docs/current-worklist.md](../../current-worklist.md)
- Prior handoff (S0 interview, fully landed on main): [2026-04-21-s0-impl-handoff.md](2026-04-21-s0-impl-handoff.md)
