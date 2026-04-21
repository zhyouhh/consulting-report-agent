# Handoff — S0 Interview + stage-ack Implementation (ready to execute)

**Created:** 2026-04-21
**Status:** Plan APPROVED, awaiting implementation in a fresh Claude Code session.

This document is a **cold-start briefing** for the next session. Read it first, then start executing the plan task-by-task.

---

## What this work is

Implements the **S0 pre-interview gate** and a unified **XML tag-based stage-ack signaling mechanism** across all 6 stage checkpoints. This replaces the brittle weak-keyword detection that caused two production bugs in the second-round smoke test:

- **Bug 1 (图5):** Filling the new-project form immediately flipped the S0 checklist to "all complete" even though no interview had happened — the `project_overview_ready` judge was wrong.
- **Bug 3 (图8):** User typed "确认" (not "确认大纲") and the weak-keyword table missed it, so the stage never advanced.

Design decision: **let the LLM emit `<stage-ack>KEY</stage-ack>` tags at the end of a reply**, with hard gates (prerequisite files, S0 soft gate, position judge) on the backend. Strong keywords keep working as fallback; weak-keyword table is deleted.

## What's already done (in this branch, on `main`)

| Commit | Content |
|---|---|
| `73b345d` | Bug 2 (tool bubble swallows text) — frontend `appendToolEventContent` helper |
| `80e74a2` | Add the APPROVED design spec (5-round codex review) |
| `ccc83ab` | Update worklist + archive smoke-test bugfix plan |
| `c284565` | Add the APPROVED implementation plan (3-round codex review) |

## The two source-of-truth documents

| Role | Path | Reviewed |
|---|---|---|
| **Design spec** (decisions + rationale) | `docs/superpowers/specs/2026-04-21-s0-interview-and-stage-ack-design.md` | 5 rounds, APPROVED |
| **Implementation plan** (what to code, step by step) | `docs/superpowers/plans/2026-04-21-s0-interview-and-stage-ack-impl.md` | 3 rounds, APPROVED |

**Rule:** when the plan and code disagree, trust the plan. When the plan and spec disagree, update the plan — spec wins.

## Plan structure (quick overview)

19 tasks, lettered A through S, strictly ordered. The order is not alphabetical-arbitrary — it satisfies "migration-first" (A–C), then parser (D–F), then chat-runtime wiring (H–N), frontend (O–Q), SKILL.md (R), integration (S).

Each task has 5 steps: failing test → verify fail → minimal impl → verify pass → commit. Steps are 2–5 minutes each.

**Critical architecture:** Task M changes `_build_turn_context` to *defer* strong-keyword `set` until `_finalize_assistant_turn` runs — because the assistant's executable tag hasn't arrived yet when `_build_turn_context` fires. The deferred keyword sits in `turn_context["pending_stage_keyword"]`; finalize either (a) executes tags and discards pending, or (b) runs pending as fallback. `clear` keywords still execute immediately.

## Execution rules (agreed with the user)

Ratified over this session and `C:\Users\36932\.claude\projects\D--MyProject-CodeProject-consulting-report-agent\memory\`:

1. **Task dispatch:** for each task A through S, run one **bare `codex exec`** (not the `codex:rescue` plugin — the plugin's shim has a heredoc bug on Windows Git Bash; memory `feedback-codex-dispatch-workaround.md`).

   Template (Windows/Git Bash):
   ```
   codex exec --cd "D:/MyProject/CodeProject/consulting-report-agent" \
     --color never \
     --output-last-message .codex-run/task-N-last.txt \
     < .codex-run/task-N-prompt.md \
     > .codex-run/task-N-full.log 2>&1
   ```
   Run with `run_in_background: true` in the Bash tool.

2. **`.codex-run/` convention (memory `reference-codex-run-convention.md`):** three files per task:
   - `task-N-prompt.md` (what you hand to codex — the plan's Task N section plus context)
   - `task-N-last.txt` (codex's final message)
   - `task-N-full.log` (full stdout)
   `.codex-run/` is gitignored — nothing you put there ships.

3. **20-min liveness cron:** after dispatching each bare codex job, `CronCreate` a recurring liveness check every 20 minutes at an off-minute (e.g. `7,27,47 * * * *`). The cron prompt should (a) kill + restart if `.codex-run/task-N-full.log` mtime has not advanced in 30+ min with no verdict line, (b) `CronDelete` and summarize on completion.

4. **Post-task review loop:** after each implementation task finishes, **dispatch another bare codex** to review that task's commit + diff against the plan's Step 1–4 acceptance criteria. Same 20-min cron rule. If the review says `NEEDS REVISION`, fix and re-review. Only move on to the next lettered task after the current one is reviewed APPROVED.

   This mirrors the review loop that produced the APPROVED spec (5 rounds) and APPROVED plan (3 rounds).

5. **Front-end exception:** if a task is purely frontend CSS / UI event handling (unlikely in this plan — Tasks O/P/Q are logic-in-utils, fine for codex), `Agent(subagent_type='general-purpose', model='sonnet')` is acceptable. **Do not use opus** for review — memory `feedback-review-dispatch-sonnet.md` says opus review is too expensive.

6. **Testing baseline:** before starting Task A, run once to confirm the baseline:
   ```
   .venv\Scripts\python -m pytest tests/ -q       # expect 403 passed / 1 skipped
   cd frontend && node --test tests/               # expect 140 passed / 0 failed
   cd frontend && npm run build                    # expect 0 errors
   ```
   Keep these numbers in each codex prompt so the bot knows its target.

7. **Never skip `git push`:** the user pushes manually. Your job stops at `git commit`.

8. **Windows paths:** all file ops via PowerShell idioms (`Remove-Item -LiteralPath`, `Test-Path -LiteralPath`). No `rm -rf`, no `cat | head`. Bash tool runs Git Bash so `git` / Unix shell syntax works, but any cross-platform helper should prefer Windows-native.

## Key context the new session needs

- **User profile (memory `user_role.md` if present):** Consulting background, not a programmer but interested in code. Prefers terse, structured replies. Ships Windows desktop app for colleagues who aren't AI-savvy.
- **User feedback memories worth re-reading first thing:**
  - `feedback-codex-dispatch-workaround.md` — why bare exec, not plugin
  - `feedback-review-dispatch-sonnet.md` — sonnet for reviews, not opus
  - `feedback-minimal-ai-questioning.md` — ≤1 round of AI-initiated clarifying questions; trust the user to volunteer info
  - `reference-codex-run-convention.md` — the 3-file-per-task convention
- **Project CLAUDE.md (`consulting-report-agent/CLAUDE.md`):** hard constraints on skill workflow, file boundaries, managed search pool, packaging steps.
- **Workspace CLAUDE.md (`../CLAUDE.md`, i.e. `D:\MyProject\CodeProject\CLAUDE.md`):** updated this session to reflect the bare-codex dispatch rule + `.codex-run/` convention + 20-min cron.

## Quality bar (what "done" looks like)

After Task S completes:

- Backend: `403 + N` passed (where N = total new tests the plan added; plan estimates 80+)
- Frontend: `140 + M` passed (~10 new tests)
- `frontend && npm run build` 0 errors
- `build.bat` produces `dist\咨询报告助手\` ~91 MB
- Three-round smoke test passes:
  1. New project → model asks 3-5 clarifying questions → user answers → tag advances to S1
  2. New project → user types "跳过访谈" after model's first round → tag advances to S1
  3. Pre-created legacy project at stage S2 (file has `outline_confirmed_at` but no `s0_interview_done_at`) → load → NOT pushed back to S0; `stage_checkpoints.json` silently backfilled

## Gotchas the plan already bakes in

- **`_find` not `_rfind`** for the stream tail guard (rfind hits the `<` in `</stage-ack>` and leaks the opening tag — Round 2 review caught this).
- **`yield notice` not `yield {"type":"system_notice","data":notice}`** — `_emit_system_notice_once` already returns an SSE-ready dict with `type` at the top; wrapping it would render empty in the frontend.
- **S0 write_file gate** fires inside `_execute_tool`'s `write_file` branch BEFORE `_should_block_non_plan_write`.
- **Parser unknown key:** event emitted with `executable=False, ignored_reason="unknown_key"`, NOT dropped. Finalize still strips it but doesn't call `record_stage_checkpoint`.
- **`CHECKPOINT_PREREQ["s0_interview_done_at"] = None`** — no file-level validator; S0 soft gate lives in `_finalize_assistant_turn` / `_detect_stage_keyword`, not in `_validate_stage_checkpoint_prereq`.

## Unrelated pending items (don't mix in)

These are on `docs/current-worklist.md` but **out of scope** for this plan:

- Bug C (S0 quality gate) — superseded by this plan
- Bug G (rollback state cascade cleanup) — deferred
- Bug H (S1 next_stage_hint empty) — deferred
- Worklist #2-7 (stream feel, form cleanup, channel copy, draw.io eval, chunk size, tech debt)

Don't spontaneously fix these during implementation of this plan. File a separate plan if they become blockers.

## First move in the new session

```
# 1. Sanity-check baseline tests still pass on current main
.venv\Scripts\python -m pytest tests/ -q
cd frontend && node --test tests/ && cd ..

# 2. Read the plan's Task A section
#    docs/superpowers/plans/2026-04-21-s0-interview-and-stage-ack-impl.md — search for "### Task A:"

# 3. Draft .codex-run/task-A-prompt.md (self-contained: copy Task A section + include baseline numbers + remind bare-exec rules)
# 4. Bash run_in_background: codex exec (Windows template above)
# 5. CronCreate for 20-min liveness check
# 6. Wait for verdict → on completion, review its commit → approve or revise
# 7. Move to Task B
```

Godspeed.
