# Report Draft Tool Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make canonical report drafting deterministic by turning `content/report_draft_v1.md` into a path-level special resource with explicit create/append/edit contracts, structured continuation state, and mixed-intent handling that never fakes execution.

**Architecture:** Keep the tool surface small: retain `append_report_draft`, `edit_file`, and generic `write_file`, but make canonical-draft behavior path-specific inside `ChatHandler`. Centralize the decision logic in one classification helper that owns canonical-draft authorization, required-write behavior, mixed-intent routing, and reads a structured `draft_followup_state` from `conversation_state.json` while turn-finalization code alone writes that state.

**Tech Stack:** Python 3, FastAPI backend, existing `ChatHandler`/`SkillEngine`, `pytest` + `unittest`, packaged Windows desktop app.

---

## Preconditions

- Branch: `codex/report-draft-write-reliability`
- Spec: `docs/superpowers/specs/2026-04-24-report-draft-tool-contract-design.md`
- Do not revert unrelated local changes (`docs/current-worklist.md`, untracked `AGENTS.md`, or any user work).
- All subagents use `gpt-5.4` with `xhigh`.
- Final controller owns the packaging, merge-to-main, final commit, and push.

## Files

- Modify: `backend/chat.py`
  - canonical draft classification helper
  - mixed-intent routing and reject paths
  - generic existing-file read-before-write gate
  - canonical draft path-specific `write_file` block
  - one-mutation-per-turn enforcement
  - structured `draft_followup_state` read/write
  - canonical draft progress payload
  - real tool-name persistence in `conversation_state.json`
- Modify: `skill/SKILL.md`
  - reduce tool usage instructions to the final contract
  - align wording with guidance-only mixed-intent behavior
- Modify: `tests/test_chat_runtime.py`
  - add/adjust behavior matrix for helper classification, mutation gating, mixed-intent, state lifecycle, progress payload, and memory persistence
- Optional Modify: `tests/test_skill_engine.py`
  - only if a helper is moved into `SkillEngine` (prefer not to)

---

### Task 1: Implement Canonical Draft Decision Contract Helpers

**Files:**
- Modify: `backend/chat.py`
- Modify: `tests/test_chat_runtime.py`

- [ ] **Step 1: Add failing classification-helper tests**

Add tests for a small helper surface in `tests/test_chat_runtime.py`. Keep the implementation in `ChatHandler`.

Required coverage:

- no draft + `开始写正文` in `S4` -> canonical mutation authorized, tool family `append_report_draft`
- edit-only request with no draft -> fixed reject instruction
- whole-draft rewrite with existing canonical draft -> full-file `edit_file` path
- whole-draft rewrite with no canonical draft -> fixed reject instruction `当前还没有正文草稿，请先用 append_report_draft 起草第一版。`
- `目标5000字喔？而且每章现在都太单薄了` after an under-target follow-up state -> implicit append path
- same message with unrelated non-expansion action -> does not match implicit append
- explicit continuation (`继续写正文`, `扩写正文`) -> canonical mutation authorized regardless of prior assistant wording
- mixed-intent pre-routing:
  - `先扩到 5000 字再导出`
  - `看看现在多少字，不够就继续写`
  - `把执行摘要改强一点后导出`
  - multi-secondary-action message -> immediate reject before mutation

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -k "draft_tool_contract or mixed_intent or followup_state" -q
```

Expected: failures because the new classification helpers do not exist yet.

- [ ] **Step 3: Add one canonical-draft classification helper and result shape**

In `backend/chat.py`, add a single helper that returns a structured result for canonical-draft turns only. Keep it as the sole source of truth for:

- whether canonical draft mutation is required
- whether canonical draft mutation is rejected
- which tool family is expected
- whether the turn is Priority `5A`, `5B`, `8`, etc.
- whether follow-up guidance is needed

Suggested shape:

```python
{
    "mode": "require" | "no_write" | "reject",
    "expected_tool_family": "append_report_draft" | "edit_file" | None,
    "fixed_message": str | None,
    "mixed_intent_secondary_family": "quality_check" | "export" | "inspect_file" | "inspect_word_count" | None,
    "effective_turn_target_count": int | None,
    "priority": "P1" | "P2" | ...,
}
```

This helper must replace the old canonical-draft-specific decision points rather than sit beside them. Route canonical-draft decisions through:

- `_build_turn_context`
- `_should_allow_non_plan_write`
- `_should_block_non_plan_write`
- `_required_write_paths_for_turn`
- `_build_required_write_snapshots`
- the required-write retry feedback path

- [ ] **Step 4: Add secondary-action-family normalization and pre-routing**

Implement a helper that maps the whitelisted phrases to:

- `quality_check`
- `export`
- `inspect_file`
- `inspect_word_count`

Then add the Priority 5 precheck:

- if more than one secondary-action family plus canonical mutation intent -> reject before mutation
- if exactly one family plus canonical mutation intent -> route to 5A/5B before 1-4/9

- [ ] **Step 5: Add threshold helpers**

Keep these rules explicit in code:

- project default target = `SkillEngine._resolve_length_targets(project_path)["expected_length"]`
- follow-up threshold = `draft_followup_state.continuation_threshold_count` when present, else project default target
- explicit numeric target in current user message affects only current Priority 5A logic

- [ ] **Step 6: Run Task 1 tests**

Run:

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -k "draft_tool_contract or mixed_intent or followup_state" -q
```

Expected: classification-helper and routing tests pass.

- [ ] **Step 6.1: Add gate-order and retry integration tests**

Cover:

- `S4` implicit continuation without legacy keywords still authorizes canonical draft mutation
- same phrasing in `S3` still degrades to the existing stage-gate rejection
- required canonical write skipped -> retry path fires instead of accepting false completion

- [ ] **Step 7: Commit**

```bash
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "feat: add canonical draft decision contract"
```

---

### Task 2: Enforce Canonical Draft Mutation Rules In Tool Execution

**Files:**
- Modify: `backend/chat.py`
- Modify: `tests/test_chat_runtime.py`

- [ ] **Step 1: Add failing execution tests**

Add tests for:

- generic `write_file(content/report_draft_v1.md, ...)` is rejected
- existing generic file requires same-turn `read_file` before `write_file` or `edit_file`
- canonical draft `append_report_draft` is exempt from same-turn `read_file`
- canonical draft `edit_file` still requires same-turn `read_file`
- after one successful canonical-draft mutation in a user turn, a second draft mutation is rejected
- whole-draft rewrite execution:
  - existing draft -> only legal path is `read_file(content/report_draft_v1.md)` followed by `edit_file(old_string=current_full_draft, new_string=rewritten_full_draft)`
  - no draft -> fixed reject string
- 5A already-met case:
  - draft already >= requested threshold
  - no mutation occurs
  - only guidance is emitted
- 5A unmet case:
  - one append happens
  - if still below threshold, no follow-up action execution, only guidance
- 5A met-after-one-append case:
  - one append happens
  - threshold becomes met from authoritative post-write progress
  - no same-turn secondary execution
  - response says the secondary action can be requested next turn
- all four secondary families are guidance-only:
  - `导出`
  - `质量检查`
  - `看看文件`
  - `看看现在多少字`

- [ ] **Step 2: Run those tests and verify failure**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -k "canonical_draft_mutation or read_before_write or threshold_recheck" -q
```

- [ ] **Step 3: Add path-level `write_file` block for canonical draft**

Inside `_execute_plan_write()`:

- reject generic `write_file` to `content/report_draft_v1.md`
- keep generic `write_file` behavior unchanged for other paths

- [ ] **Step 4: Add generic existing-file read-before-write guard**

Enforce same-turn `read_file` only for:

- generic `write_file`
- generic `edit_file`

Do not apply that guard to `append_report_draft`.

- [ ] **Step 5: Add one-successful-mutation-per-user-turn enforcement**

Track canonical draft mutation success in `self._turn_context`.

After the first successful canonical-draft mutation:

- reject any further canonical-draft `append_report_draft`
- reject any further canonical-draft `edit_file`
- keep non-draft file operations independent

- [ ] **Step 6: Wire mixed-intent handling to guidance-only behavior**

After a Priority 5A/5B turn:

- do not execute export, quality-check, inspect-file, or inspect-word-count runtimes inside the same chat turn
- emit follow-up guidance only
- keep any actual export/quality-check action for later UI or later turn

- [ ] **Step 7: Run Task 2 tests**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -k "canonical_draft_mutation or read_before_write or threshold_recheck" -q
```

Also run:

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -k "whole_draft_rewrite or mixed_intent_secondary or implicit_continuation_retry" -q
```

- [ ] **Step 8: Commit**

```bash
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "feat: enforce canonical draft mutation rules"
```

---

### Task 3: Persist Structured Follow-Up State And Accurate Progress Payload

**Files:**
- Modify: `backend/chat.py`
- Modify: `tests/test_chat_runtime.py`

- [ ] **Step 1: Add failing persistence tests**

Cover:

- `conversation_state.json` top-level `draft_followup_state` defaults to `null`
- missing field is treated as `null`
- after under-target assistant turn that asks to continue, state is persisted
- after target is met, state becomes `null`
- after intervening non-writing assistant turn, old state no longer authorizes Priority 8
- when a Priority 5A threshold is higher than project default, state persists `continuation_threshold_count`
- when default target is met but continuation threshold is not, state remains valid for Priority 8
- `draft_followup_state` is stored only as top-level field in `conversation_state.json`

- [ ] **Step 2: Add failing progress-response tests**

Cover:

- canonical draft writes return:
  - `report_progress.current_count`
  - `report_progress.target_word_count`
  - `report_progress.meets_target`
  - `effective_turn_target_count` when relevant
  - `effective_turn_target_met` when relevant
- `meets_target` reflects project default target only
- `effective_turn_target_met` reflects the temporary Priority 5A threshold only

- [ ] **Step 3: Persist `draft_followup_state` only from turn-finalization flags**

Add a small turn-finalization structure in `backend/chat.py` and write `draft_followup_state` only from it at the end of the final assistant turn that is persisted into history.

Do not parse assistant message text to infer it.

Pin the ownership explicitly:

- turn-finalization code alone writes `draft_followup_state`
- classification helpers only read it
- missing field is treated as `null`

- [ ] **Step 4: Preserve the field in conversation state**

Update:

- `_empty_conversation_state()`
- `_load_conversation_state()`
- `_save_conversation_state_atomically()`

so top-level `draft_followup_state` survives normal load/save/compaction flows.

- [ ] **Step 5: Return accurate canonical draft progress**

For canonical-draft writes only, compute from final on-disk content:

- `report_progress.current_count`
- `report_progress.target_word_count`
- `report_progress.meets_target`
- `effective_turn_target_count`
- `effective_turn_target_met`

Keep the human-facing `message` aligned with those values.

Task 2 threshold re-check logic must call this same final on-disk progress helper instead of recomputing from stale pre-write content.

- [ ] **Step 6: Run Task 3 tests**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -k "draft_followup_state or report_progress or effective_turn_target" -q
```

- [ ] **Step 7: Commit**

```bash
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "feat: persist draft follow-up state and progress"
```

---

### Task 4: Preserve Real Tool Names And Align Model Instructions

**Files:**
- Modify: `backend/chat.py`
- Modify: `skill/SKILL.md`
- Modify: `tests/test_chat_runtime.py`

- [ ] **Step 1: Add failing tests for event persistence and prompt alignment**

Cover:

- successful `append_report_draft` keeps event `tool_name = "append_report_draft"`
- memory source key still refreshes `file:content/report_draft_v1.md`
- mixed-intent turns only yield guidance, not same-turn secondary execution, across all four secondary families

- [ ] **Step 2: Update tool descriptions and system prompt**

In `_get_tools()` and `_build_system_prompt()`:

- keep the rule block short
- say canonical draft create/continue = `append_report_draft`
- say canonical draft edit = `edit_file`
- say canonical draft generic `write_file` forbidden
- say mixed-intent follow-up actions are guidance-only

- [ ] **Step 3: Update `skill/SKILL.md`**

Keep the file-tool section aligned with the runtime contract:

- generic existing-file `read_file` before `write/edit`
- canonical draft special rules
- guidance-only mixed-intent follow-ups

- [ ] **Step 4: Run Task 4 tests**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -k "tool_name or memory_entry or guidance_only" -q
```

- [ ] **Step 5: Commit**

```bash
git add backend/chat.py skill/SKILL.md tests/test_chat_runtime.py
git commit -m "feat: align draft tool prompts and persistence"
```

---

### Task 5: Final Verification And Packaging

**Files:**
- Modify: none expected, unless verification exposes a real bug

- [ ] **Step 1: Run focused chat runtime suite**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -q
```

- [ ] **Step 2: Run regression suite for adjacent stage/workspace behavior**

```powershell
.venv\Scripts\python -m pytest tests/test_skill_engine.py tests/test_workspace_materials.py tests/test_stage_quality_gates.py -q
```

- [ ] **Step 3: Run whitespace / conflict checks**

```powershell
git diff --check
```

- [ ] **Step 4: Build package**

```powershell
.\build.bat
```

- [ ] **Step 5: Verify packaged output**

Check:

- `dist\咨询报告助手\咨询报告助手.exe` exists
- packaged `skill/SKILL.md` contains the final canonical-draft contract wording

- [ ] **Step 6: Commit**

```bash
git add backend/chat.py skill/SKILL.md tests/test_chat_runtime.py tests/test_skill_engine.py tests/test_workspace_materials.py tests/test_stage_quality_gates.py docs/superpowers/specs/2026-04-24-report-draft-tool-contract-design.md docs/superpowers/plans/2026-04-24-report-draft-tool-contract.md
git commit -m "fix: harden canonical report draft tool contracts"
```

---

## Plan Review Checklist

The plan reviewer should confirm:

- One helper owns canonical draft classification
- `draft_followup_state` is written only at turn finalization
- canonical draft `write_file` stays forbidden
- mixed-intent follow-ups are guidance-only, not same-turn endpoint execution
- effective-turn threshold logic is fully covered by tests
- no requirement depends on free-text parsing of previous assistant wording
