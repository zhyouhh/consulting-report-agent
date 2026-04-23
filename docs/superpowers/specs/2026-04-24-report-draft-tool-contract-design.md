# Report Draft Tool Contract Design

## Context

The current report-writing runtime still lets the model treat `content/report_draft_v1.md` too much like an ordinary file. We already fixed one class of false completion by adding `append_report_draft` and stronger write verification, but the latest live session in `D:\CodexProject\test\.consulting-report` exposed a second failure mode:

- The user first let the model write about 1000 words.
- A continuation turn appended the draft to roughly 3000 words.
- The user then said the report was still below the 5000-word target and the chapters were too thin.
- The model later claimed the draft had reached about 5200 words, but the real file on disk still measured about 3085 words by the backend's own counter.

Investigation showed two root causes:

1. The continuation guard still depended on explicit write-intent phrases such as "继续写" or "扩写正文". The user's message "目标5000字喔？而且每章现在都太单薄了" was semantically a request to keep drafting, but the current intent detector returned false.
2. Once that guard did not activate, the model was free to do a mixture of append-like and overwrite-like actions on the canonical draft path, then report a stale higher word count from an earlier step.

This is not a wording problem. It is a resource-contract problem. The canonical report draft should behave like a special document with tighter rules than generic plan files.

## Goals

1. Make report drafting rely on deterministic file contracts instead of large continuation keyword lists.
2. Keep the tool surface minimal: do not add more draft-specific tools unless strictly necessary.
3. Treat `content/report_draft_v1.md` as a path-level special resource while leaving other files on the existing generic mutation tools.
4. Add one small global safety rule: existing files must be read before generic overwrite/replace mutations.
5. Make report-draft writes return authoritative post-write progress data so the model does not invent or reuse stale counts.
6. Preserve the real user-facing semantics of each tool in logs and diagnostics.

## Non-Goals

- Do not redesign the full S0-S7 state machine.
- Do not add a new `rewrite_report_draft` tool in this iteration.
- Do not make every file write return word-count metadata.
- Do not change frontend controls or add new UI steps.
- Do not remove `write_file` or `edit_file` from the general tool surface for non-draft files.

## Existing Constraints To Preserve

- The only canonical report draft path remains:

```text
content/report_draft_v1.md
```

- Existing stage, export, quality-check, and readiness logic in `SkillEngine` still reads that same path.
- The write-gate chain in `ChatHandler._execute_plan_write()` must remain the single path for real disk writes.
- Stage files and plan files keep their current write model.

## Design Summary

The design is intentionally small:

0. **Turn definition:** `user turn` means one incoming frontend user message handled by a single `ChatHandler.chat()` or `ChatHandler.chat_stream()` invocation, including all internal tool-call loops, retries, and self-correction attempts inside that invocation.
0.1 **Assistant-turn definition:** `assistant turn` means the single final assistant message from one `user turn` that is actually persisted into conversation history. Internal retries, tool-call loops, and self-correction output inside the same `user turn` do not count as separate assistant turns.
1. **Global rule:** if a target file already exists, generic `write_file` and `edit_file` must be preceded by `read_file` for that same file earlier in the same user turn.
2. **Report-draft exception:** `content/report_draft_v1.md` may not be written via generic `write_file`.
3. **Draft-writing modes:**
   - Create or continue report body: `append_report_draft`
   - Modify existing report body text: `edit_file`
4. **Turn discipline for the draft path:** the canonical draft may be successfully mutated at most once per user turn, regardless of whether the tool is `append_report_draft` or `edit_file`.
5. **Progress reporting:** when a write targets the canonical draft, the tool response includes small authoritative progress metadata read from the final on-disk file.
6. **Logging:** keep the actual tool name (`append_report_draft`, `edit_file`, etc.) in conversation-state events instead of flattening everything to `write_file`.
7. **Structured follow-up state:** when the assistant finishes a turn with an under-target draft and asks whether to continue expanding it, that state is persisted as structured metadata instead of being inferred later from assistant free text.

## Alternatives Considered

### Option A: Expand continuation keyword lists

Pros:
- Smallest code diff.

Cons:
- Fundamentally brittle.
- Can never cover all user phrasings such as "字数不够", "太单薄", or "再展开一点".
- Keeps the system dependent on magic wording instead of document state.

Decision: reject.

### Option B: Add more specialized tools (`rewrite_report_draft`, etc.)

Pros:
- More explicit semantics for each action.

Cons:
- Expands the tool surface and increases model decision burden.
- Not needed yet because the existing trio plus one append helper is enough if contracts are clear.

Decision: reject for this iteration.

### Option C: Keep the current tools and tighten path-specific contracts

Pros:
- Minimal surface area.
- Matches the user's preference for simpler, Codex/Claude-style tool models.
- Solves the real failure mode at the resource boundary.

Cons:
- Requires careful tool descriptions and runtime feedback so weaker models still choose correctly.

Decision: choose this option.

## Detailed Design

### 1. Global Existing-File Read-Before-Write Contract

For any generic mutation that targets an already existing project file:

- The backend checks whether `read_file(target_path)` succeeded earlier in the same user turn.
- If not, reject the mutation with a concrete instruction telling the model to read the file first.

This rule applies to:

- `write_file`
- `edit_file`

This rule does **not** apply when the target file does not yet exist.
It also does **not** apply to `append_report_draft`, because that tool already reads the latest canonical draft on the server side before producing the final combined write.

Rationale:

- The backend can already read the file, but the model still needs the latest contents in prompt context.
- This is a small general safety contract for generic overwrite/replace tools.
- `append_report_draft` stays exempt so the model does not have to stuff the full draft back into context merely to continue writing.

Example rejection message:

```text
本轮要修改的文件 `plan/data-log.md` 已存在。请先调用 `read_file` 读取最新内容，再执行写入或替换。
```

### 2. Canonical Draft Path Is A Special Resource

Only the canonical path is special:

```text
content/report_draft_v1.md
```

For this path only:

- Generic `write_file(file_path="content/report_draft_v1.md", ...)` is rejected.
- `append_report_draft(content)` is the only allowed way to create the initial draft file.
- `append_report_draft(content)` is also the allowed way to continue writing new sections at the end.
- `edit_file(file_path="content/report_draft_v1.md", ...)` is the only allowed way to modify existing draft text.
- If the user explicitly asks to rewrite the whole draft (`整篇重写`, `全文重写`, `推倒重写`, `全部改写`) **and the canonical draft already exists**, the legal path is still `edit_file`: first `read_file(content/report_draft_v1.md)`, then replace the full current draft (`old_string = current full draft`, `new_string = rewritten full draft`). `write_file` remains forbidden for the canonical draft.
- If the user asks for whole-draft rewrite before any canonical draft exists, reject with a fixed instruction such as `当前还没有正文草稿，请先用 append_report_draft 起草第一版。`

All other files retain the existing generic semantics of `write_file` and `edit_file`.

This is a path-level rule, not a stage-wide ban. The model may still use `write_file` on other files in S4/S5, such as plan or review artifacts, as allowed by existing gates.

### 3. `append_report_draft` Semantics

Clarify the tool contract:

- If `content/report_draft_v1.md` does not exist, `append_report_draft` creates it.
- If the file exists, `append_report_draft` appends cleanly to the end with one blank-line boundary.
- It is not a replacement or patching tool.

The implementation can remain the same high-level flow:

1. If the draft exists, read it.
2. Join the existing text and the appended content.
3. Pass the final combined content through `_execute_plan_write()`.

The important change is **documentation and enforcement**, not a brand-new code path.

Tool description should explicitly say:

```text
用于报告正文成稿：若 `content/report_draft_v1.md` 不存在则创建，若已存在则追加到末尾。
不要用于局部替换或精确修改已有段落。
```

### 4. One Successful Draft Mutation Per User Turn

For the canonical draft only:

- A user turn may contain many `read_file` calls.
- A user turn may contain **at most one successful draft mutation**.
- The single successful mutation may be either `append_report_draft` or `edit_file`.
- After one successful mutation to `content/report_draft_v1.md`, reject any later canonical-draft mutation in the same turn.

Rationale:

- This is much easier for the default model to follow than a multi-mode state machine.
- It directly blocks the live failure pattern: append first, then overwrite or patch the same draft again before the turn ends.
- Any second mutation can happen in the next user turn with fresh context.

Example rejection message:

```text
本轮已经成功追加了报告正文。请基于当前落盘结果直接向用户汇报，不要继续修改 `content/report_draft_v1.md`。
```

### 5. Replace Open-Ended Intent Heuristics With A Closed Draft Decision Contract

For `content/report_draft_v1.md`, this decision contract is authoritative and is evaluated **before** the generic non-plan-write gate.

This contract only replaces the old keyword-based draft-authorization check. It does **not** bypass existing stage gates. If the current stage is not one of `S4`, `S5`, `S6`, `S7`, or `done`, then any draft-mutation result must downgrade to `Reject with existing stage-gate instruction`.

- If the result is `Require real draft mutation`, the turn is write-authorized for the canonical draft path only. Other non-plan files still follow the existing gate unchanged.
- If the result is `No required draft mutation`, any `append_report_draft`, `edit_file`, or `write_file` targeting `content/report_draft_v1.md` must be rejected for this turn. Only read-only, review, export, archive, or other non-draft actions may proceed.
- If the result is `Reject with fixed instruction`, the turn must return that fixed user-facing instruction immediately for the canonical draft path, without falling through to later priorities.

The previous report-body keyword allowlist is therefore no longer the precondition for canonical draft mutation.
One helper result must become the single source of truth for canonical-draft turns and must drive all three behaviors together:

1. whether a real canonical-draft mutation is required in this user turn
2. whether canonical-draft mutation tools are allowed or rejected in this user turn
3. whether the canonical-draft path bypasses the old non-plan-write keyword gate for this user turn

The old keyword-based non-plan-write gate remains authoritative only for non-draft files.

`draft_followup_state` may only be written by turn-finalization code based on structured backend flags. The canonical-draft classification helper only reads and interprets it for Priority 8. Implementations must not infer Priority 8 from assistant free text.

Use the following closed priority order:

Before applying the table below:

1. If a message contains more than one whitelisted secondary-action family plus any canonical-draft mutation intent, reject immediately with the split-turn instruction before any canonical-draft mutation.
2. If a message contains exactly one whitelisted secondary-action family plus any canonical-draft mutation intent, it must be routed to Priority 5A or 5B first. In that case, Priorities 1-4 and 9 do not match directly for that message.

Secondary-action families are normalized as user-facing follow-up intents, not same-turn executable chat tools. Within Priority 5 mixed-intent handling, `quality_check`, `export`, `inspect_file`, and `inspect_word_count` must never execute in the same chat turn:

- `quality_check`: `质量检查`, `运行质量检查`
- `export`: `导出`
- `inspect_file`: `看看文件`
- `inspect_word_count`: `现在多少字`, `字数多少`, `当前多少字`, `看看字数`, `看看现在多少字`

| Priority | Exact match rule | Result |
| --- | --- | --- |
| 1 | Message matches the existing inline replacement parser (`把报告里 X 改成 Y`) | Require real draft mutation; required tool family = `edit_file` |
| 2 | Message requests rewriting or revising an existing section whose heading or section label is already present in the current draft snapshot, using one of: `重写`, `改写`, `改强`, `改得更强`, `重做` | Require real draft mutation; required tool family = `edit_file` |
| 3 | Message explicitly asks to rewrite the whole draft using one of: `整篇重写`, `全文重写`, `推倒重写`, `全部改写`, and `content/report_draft_v1.md` already exists | Require real draft mutation; required tool family = `edit_file` over the full current draft |
| 4 | `current draft snapshot` does not exist, the stage is one of `S4`, `S5`, `S6`, `S7`, `done`, and the message explicitly asks to start or draft the first report body using one of: `开始写正文`, `开始写报告正文`, `起草正文`, `按大纲写初稿`, `先写第一版`, `继续写正文`, `继续写报告正文` | Require real draft mutation; required tool family = `append_report_draft` |
| 5A | Mixed-intent message: the same message contains one normalized secondary-action family and also contains a conditional/target-length expansion intent such as `扩到`, `补到`, `写到 N 字`, `不够就继续写`, `不够再扩写` | First evaluate the requested threshold against the current on-disk draft snapshot. If already satisfied, perform no canonical-draft mutation and respond with the one secondary-action guidance directly. If not yet satisfied, allow one canonical-draft mutation, then re-evaluate the same threshold against authoritative post-write `report_progress.current_count` and `effective_turn_target_count` when present. Only if the threshold is now satisfied may the assistant respond that the one secondary action is now ready for the user to trigger or request in the next turn |
| 5B | Mixed-intent message: the same message contains one normalized secondary-action family and also contains an explicit draft-mutation intent from Priority 1, 2, 3, 4, or 9, but does not introduce a conditional or numeric threshold | Require real draft mutation first; after exactly one successful canonical-draft mutation, the assistant may emit user-facing follow-up guidance for the one normalized secondary action, but must not try to execute a separate quality-check or export endpoint inside the same chat turn |
| 6 | Message contains one of: `开始审查`, `继续审查`, `质量检查`, `运行质量检查`, `导出`, `归档`, `交付`, `先别写了`, `不要写正文`, `先不写正文`, `暂停写作` | No required draft mutation |
| 7 | Message contains one of: `现在多少字`, `字数多少`, `当前多少字`, `现在写到哪了`, `写到哪了`, `看看文件`, `看看字数` and does not also match Priority 1-5 | No required draft mutation |
| 8 | Stage is `S4`, the current on-disk draft is still below `followup_threshold_count`, `draft_followup_state.reported_under_target == true` or `draft_followup_state.asked_continue_expand == true`, the current message contains an insufficiency-or-expansion signal such as `不够`, `不足`, `太单薄`, `再展开`, `再补一点`, `再扩写`, and Priority 1-7 did not match | Require real draft mutation; required tool family = `append_report_draft` |
| 9 | The message explicitly asks to continue or expand report body text using one of: `继续写报告正文`, `继续写正文`, `扩写正文`, `补全章节`, `写下一章`, `补正文`, regardless of whether the stage is `S4`, `S5`, `S6`, `S7`, or `done` | Require real draft mutation; required tool family = `append_report_draft` |
| 10 | Anything else | No required draft mutation |

Notes:

- The priority order is part of the contract.
- `current draft snapshot` means the latest on-disk contents of `content/report_draft_v1.md` read by backend decision logic at classification time. Workspace memory, prompt context, tool logs, and prior assistant summaries do not count.
- `followup_threshold_count` means: use `draft_followup_state.continuation_threshold_count` when present; otherwise use `SkillEngine._resolve_length_targets(project_path)["expected_length"]`.
- If an edit-only intent (Priority 1, 2, or 3) matches but `current draft snapshot` does not exist, the result is `Reject with fixed instruction`: `当前还没有正文草稿，请先用 append_report_draft 起草第一版。`
- If Priority 2 cannot prove that the named section already exists in the current draft snapshot, Priority 2 does not match and the message falls through to later priorities.
- If Priority 3 cannot prove that `content/report_draft_v1.md` already exists, Priority 3 does not match and becomes the same fixed reject result above.
- If Priority 4 cannot prove that `current draft snapshot` does not exist, Priority 4 does not match.
- Priority 5A and 5B never treat `交付`, `归档`, or pause-style phrases as normalized secondary actions.
- Priority 5A's conditional/target-length expansion path matches only when `current draft snapshot` exists. If `current draft snapshot` does not exist, Priority 5A does not match; the request must either match Priority 4 or return the fixed instruction `当前还没有正文草稿，请先用 append_report_draft 起草第一版。`
- If a Priority 5A message contains an explicit numeric target `N`, compare `current_count` with `N`; only `current_count < N` counts as needing more writing. If the message does not contain an explicit numeric target and only says relative phrases such as `不够就继续写` or `不够再扩写`, compare against `SkillEngine._resolve_length_targets(project_path)["expected_length"]` instead.
- For Priority 5A, if the same message explicitly requests more than one secondary action, reject the entire turn before any canonical-draft mutation and instruct the user to split those actions into separate turns.
- If a Priority 5A turn still remains below the requested threshold after the single allowed canonical-draft mutation, do not execute the secondary action. Return a user-facing message that the draft is still below the requested threshold and needs another turn.
- For Priority 5B, if the same message explicitly requests more than one secondary action, reject the entire turn before any canonical-draft mutation and instruct the user to split those actions into separate turns.
- Priority 5B does not perform threshold re-evaluation. After exactly one successful canonical-draft mutation, it may emit only follow-up guidance for the one normalized secondary action in the same turn.
- Priority 8 is the deliberate state-driven fallback for implicit continuation feedback in S4. It requires under-target draft state against `followup_threshold_count`, structured `draft_followup_state`, and a current-message insufficiency-or-expansion signal, so unrelated S4 requests do not get forced into append mode. If the current message contains a distinct non-expansion action request unrelated to extending the draft, Priority 8 does not match.
- `draft_followup_state` is structured persisted metadata from the immediately previous assistant turn in this thread, with at least: `reported_under_target: bool`, `asked_continue_expand: bool`, `current_count: int`, `target_word_count: int`. Implementations must not infer this state from assistant free text.
- For Priority 8, `reported_under_target` and `asked_continue_expand` come from persisted `draft_followup_state`, but the under-target check itself must be recomputed at classification time from the current on-disk canonical draft via `SkillEngine._count_words()` and `followup_threshold_count`. Persisted `current_count` and `target_word_count` are diagnostic only and must not be used as the authoritative decision input.
- This is why a message like `目标5000字喔？而且每章现在都太单薄了` must fail to match Priorities 1-7, fall into Priority 8, and require `append_report_draft` when the previous assistant turn persisted `draft_followup_state.reported_under_target == true` or `asked_continue_expand == true`.
- Priority 9 handles direct explicit continuation commands and therefore does not depend on previous assistant context.
- The decision contract is path-specific to the canonical draft. It does not change generic plan-file behavior.

### 6. Structured Draft Follow-Up State Lifecycle

Persist only the latest follow-up object as top-level `draft_followup_state` in `conversation_state.json`.

- `_empty_conversation_state()` must default `draft_followup_state` to `null`.
- `_load_conversation_state()` and `_save_conversation_state_atomically()` must preserve this top-level field.
- A missing field is treated exactly as `null`.
- Priority 8 reads only this top-level `draft_followup_state`. It never scans `conversation.json`, assistant free text history, compacted summaries, or workspace memory entries.

`draft_followup_state` must be rewritten at the end of every assistant turn.

`draft_followup_state` must be written from structured backend turn-finalization flags, not by parsing the persisted assistant text. The rendered assistant message must be generated from the same flags.

- Define `final_followup_threshold_count` per assistant turn as:
  - the current turn's active Priority 5A threshold when the final user-visible assistant message invites continuation toward that threshold
  - otherwise `SkillEngine._resolve_length_targets(project_path)["expected_length"]`

- If the canonical draft exists and `current_count < final_followup_threshold_count` at the end of that assistant turn, and the assistant-turn finalization flags indicate that the assistant reported the under-target state and/or explicitly invited the user to continue expanding the draft in the user-visible final message, persist an object:

```json
{
  "reported_under_target": true,
  "asked_continue_expand": true_or_false,
  "current_count": 3085,
  "target_word_count": 5000,
  "continuation_threshold_count": 7000_or_null
}
```

- `reported_under_target` and `asked_continue_expand` are independent booleans.
- `reported_under_target = true` only when the assistant-turn finalization flags indicate that the final user-visible assistant message explicitly states that the draft is still below the currently relevant threshold.
- `asked_continue_expand = true` only when those same finalization flags indicate that the final user-visible assistant message explicitly invites the user to continue or expand the draft.
- `continuation_threshold_count` is optional. When the immediately previous turn ended under a higher temporary Priority 5A threshold and invited continuation toward that threshold, persist that threshold here; otherwise store `null`.
- If the canonical draft meets `final_followup_threshold_count`, `draft_followup_state` must be written as `null`.
- Otherwise, persist `draft_followup_state` whenever the assistant-turn finalization flags indicate that the final user-visible assistant message reported under-target state or explicitly invited continuation; set `reported_under_target` and `asked_continue_expand` independently.
- Write `null` only when neither signal is present.
- If the assistant turn is no longer the immediately previous assistant turn, the old object no longer authorizes Priority 8.
- Priority 8 reads only the `draft_followup_state` from the immediately previous assistant turn in the same thread. It must not look further back.
- If a non-writing assistant turn occurs between the under-target turn and the current user message, the old `draft_followup_state` no longer authorizes Priority 8.

### 7. Lightweight Report Progress Return

Only canonical draft writes should return extra progress metadata.

Do **not** add `word_count` or target metadata to every file write in the system.

For successful writes affecting `content/report_draft_v1.md`, return:

```json
{
  "status": "success",
  "message": "已写入 content/report_draft_v1.md；当前 3085/5000 字，仍需继续补全。",
  "path": "content/report_draft_v1.md",
  "report_progress": {
    "current_count": 3085,
    "target_word_count": 5000,
    "meets_target": false
  },
  "effective_turn_target_count": 7000,
  "effective_turn_target_met": false
}
```

Requirements:

- `current_count` must be computed with the existing `SkillEngine._count_words()` logic on the final on-disk file, not from model-estimated or pre-write content.
- `target_word_count` must come from `SkillEngine._resolve_length_targets(project_path)["expected_length"]`; if parsing falls back, it inherits the existing `3000` fallback through that helper.
- `message` should already encode the same information in simple language, because weaker models may ignore nested fields.
- `report_progress` is only about draft-length progress toward the report target. It is **not** the same as review readiness or stage advancement.
- `report_word_floor` continues to be used only for review readiness and export/quality checks. It is not the denominator for `report_progress`.
- `effective_turn_target_count` is returned only for Priority 5 turns where the current user message introduced an explicit or derived temporary threshold higher than the project default target.
- `report_progress.meets_target` is defined strictly as `current_count >= report_progress.target_word_count`.
- `effective_turn_target_met` is returned only when `effective_turn_target_count` is present, and is defined strictly as `current_count >= effective_turn_target_count`.
- When `effective_turn_target_count` is present and is higher than `report_progress.target_word_count`, this turn's user-visible message and Priority 5A secondary-action decision must use `effective_turn_target_count` as the threshold for "still not enough yet". `report_progress.target_word_count` continues to represent the project default target.

This progress payload should be added to:

- `append_report_draft`
- `edit_file` when it mutates the canonical draft

Only successful `append_report_draft` and canonical-draft `edit_file` responses expose `report_progress`. Shared internal helpers may reuse the same response-building function, but no user-callable successful `write_file` path to `content/report_draft_v1.md` remains.

### 8. Preserve Real Tool Names In Conversation State

When persisting successful tool results to `conversation_state.json`:

- Keep `tool_name = "append_report_draft"` when that tool succeeded.
- If useful for diagnostics, add an auxiliary field such as `persisted_via = "write_file"` rather than flattening the event.

This change is diagnostic, but important:

- It makes live-session reconstruction much easier.
- It prevents confusing timelines where append-like behavior appears as repeated generic writes.

Memory source keys can still normalize to:

```text
file:content/report_draft_v1.md
```

That normalization is helpful for prompt context and should remain.

### 9. Prompting Strategy For Weak Models

The model should not be forced to infer a large policy tree.

Use the same short rule set in three places:

1. **Tool descriptions** in `_get_tools()` — primary truth
2. **Turn-specific system prompt** in `_build_system_prompt()` — runtime reminder
3. **Skill docs** in `skill/SKILL.md` — high-level workflow guidance

The wording should stay nearly identical. Recommended compact rule block:

```text
正文草稿规则：
- 已有文件先 read_file 再用 write_file / edit_file 修改
- 正文首次成稿或续写 -> append_report_draft
- 正文局部修改 -> edit_file
- 不要对 content/report_draft_v1.md 使用 write_file
- 本轮正文草稿成功改过一次后，不要再继续修改
```

This is intentionally short. The backend, not the prompt, should remain the final enforcer.

## Testing Requirements

Add or update tests in `tests/test_chat_runtime.py` to cover:

1. `append_report_draft` creates the draft when the file does not exist.
2. Existing canonical draft may still be appended without a prior same-turn `read_file`, because `append_report_draft` is explicitly exempt from the generic read-before-write rule.
3. Existing generic files must be read earlier in the same turn before `write_file` or `edit_file` succeeds.
4. Generic `write_file` to `content/report_draft_v1.md` is rejected.
5. `edit_file` can modify the canonical draft after same-turn read.
6. After one successful canonical-draft mutation, a second canonical-draft mutation in the same turn is rejected.
7. After a successful `append_report_draft`, a second append in the same turn is rejected.
8. After a successful `append_report_draft`, an `edit_file` to the canonical draft in the same turn is rejected.
9. Canonical-draft write responses include authoritative `report_progress`.
10. Conversation-state events preserve the real successful tool name for append.
11. `append_report_draft` refreshes the workspace memory entry under `file:content/report_draft_v1.md` with the final on-disk draft content.
12. The live failure phrasing equivalent to "目标5000字喔？而且每章现在都太单薄了" is treated as continued-draft context in S4 when the draft remains below target.
13. Explicit whole-draft rewrite requests use `edit_file` over the full current draft after `read_file`, not `write_file`.
14. Mixed-intent requests such as `先扩到 5000 字再导出` and `看看现在多少字，不够就继续写` are treated as write-first turns for the canonical draft, but any secondary action remains guidance-only in that chat turn and must not execute as a separate runtime action.
15. Whole-draft rewrite requests before the first draft exists are rejected with the fixed instruction to create the first draft via `append_report_draft`.
16. In `S4`/`S5`/`S6`/`S7`/`done`, explicit first-draft requests authorize `append_report_draft` even when no draft file exists.
17. Priority 8 reads only structured `draft_followup_state`, not previous assistant wording.

## Packaging And Verification

Before completion:

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py
.venv\Scripts\python -m pytest tests/test_skill_engine.py tests/test_workspace_materials.py tests/test_stage_quality_gates.py
.\build.bat
```

The package in `dist\咨询报告助手\` must include:

- updated backend runtime
- updated `skill/SKILL.md`
- unchanged canonical draft/export/readiness behavior for downstream consumers

## Assumptions

- `append_report_draft` remains an internal LLM tool only; no frontend control is added.
- The report draft stays as Markdown in `content/report_draft_v1.md`.
- This iteration optimizes for robustness with the current default model (`gemini-3-flash`), so runtime feedback should prefer short imperative guidance over abstract explanations.
