# Report Draft Write Reliability Design

## Context

The consulting report client currently relies on the model to decide whether a user request should call a file tool. In S4 report writing, users often say short follow-up commands such as "继续写吧". The current runtime exposes `write_file` and `edit_file` with `tool_choice: auto`, so the model may answer in plain text and claim that content has been saved without actually updating `content/report_draft_v1.md`.

Recent test-session evidence in `D:\CodexProject\test\.consulting-report` showed:

- `conversation.json` updated at `2026-04-23 01:11:13`.
- `content/report_draft_v1.md` last changed at `2026-04-23 00:26:27`.
- The assistant claimed "报告全文已存入 `content/report_draft_v1.md`".
- The actual draft still ended at chapter two and had no chapter three, four, or five.

The prior false-write guard detects some assistant claims by scanning the assistant's final text for phrases like "已写入" and file paths. That is only a safety net. It is not the right root-cause fix because the model can always use different wording.

## Goals

1. Make S4 report continuation produce a real file mutation when the user has asked for more report text.
2. Add a task-specific report draft tool so the model does not need to construct fragile `edit_file(old_string, new_string)` calls for ordinary continuation.
3. Keep the canonical draft path as `content/report_draft_v1.md`.
4. Preserve all existing write gates, stage gates, workspace memory behavior, quality-check behavior, and export behavior.
5. Replace assistant-wording-driven false-write detection for S4 continuation with deterministic file-state verification.

## Non-Goals

- Do not redesign the whole S0-S7 state machine.
- Do not introduce a final Markdown deliverable path such as `output/final-report.md`.
- Do not change the UI contract or add frontend controls.
- Do not make S4 automatically advance to S5 just because the draft reaches the word floor.
- Do not remove `write_file` or `edit_file`; they remain general-purpose tools.

## Existing Boundaries To Preserve

The only report draft path is:

```text
content/report_draft_v1.md
```

Existing consumers of that path:

- `SkillEngine._current_report_word_count()`
- `SkillEngine._has_effective_report_draft()`
- `SkillEngine.get_primary_report_path()`
- `/api/projects/{project_id}/quality-check`
- `/api/projects/{project_id}/export-draft`
- S4 checklist text and stage tracking

Existing write gates in `ChatHandler._execute_plan_write()` must still apply:

- Canonical draft path enforcement.
- S0/S1 substantive plan gates.
- Non-plan writing permission gate.
- `web_search` then `fetch_url` gate before writing formal files.
- `stage_checkpoints.json` protection.
- Self-signature and premature review verdict protection.
- `analysis-notes.md` data-log reference validation.
- Conversation-state workspace memory persistence.

## Design

### 1. Add `append_report_draft`

Add a dedicated tool to `ChatHandler._get_tools()`:

```json
{
  "name": "append_report_draft",
  "description": "追加或续写报告正文到唯一草稿路径 content/report_draft_v1.md。用于 S4/S5 中用户要求继续写、补全章节、扩写正文时。不要用于 plan 文件、审查清单、交付记录或最终归档。",
  "parameters": {
    "type": "object",
    "properties": {
      "content": {
        "type": "string",
        "description": "要追加到报告草稿末尾的新正文，必须是完整 Markdown 段落或章节，不要只写摘要。"
      }
    },
    "required": ["content"]
  }
}
```

The tool must not accept a file path. The backend always targets `SkillEngine.REPORT_DRAFT_PATH`.

Execution flow:

1. Validate `content` is a non-empty string with substantive body text. For this tool, substantive means at least 80 non-whitespace characters after removing common Markdown markers (`#`, list bullets, emphasis markers, links/images syntax delimiters, code fences) and whitespace; this is a lightweight regex cleanup, not a full Markdown parser. This avoids accepting one-line status summaries as report content.
2. Read existing `content/report_draft_v1.md` if present; otherwise start from an empty draft.
3. Join existing draft and new content with a clean blank-line boundary.
4. Call `_execute_plan_write()` with:
   - `file_path=SkillEngine.REPORT_DRAFT_PATH`
   - `content=combined_content`
   - `persist_func_name="write_file"`
   - `persist_args={"file_path": SkillEngine.REPORT_DRAFT_PATH, "content": combined_content}`
5. On success, return:
   - `status`
   - `message`
   - `path`
   - `appended_chars`
   - `word_count`
   - `report_word_floor`
   - `report_ready`

Why it must call `_execute_plan_write()`:

- This preserves the existing gate chain.
- This keeps memory persistence identical to `write_file`.
- This ensures UI word count, S4 readiness, quality-check, and export all see the same file.

### 2. Prefer `append_report_draft` In S4 Prompting

Update skill/runtime wording so the model has a simple instruction:

- In S4/S5, when the user asks to continue, expand, or complete report body text, prefer `append_report_draft`.
- Use `write_file` only for creating a fresh full draft or intentionally replacing the whole draft.
- Use `edit_file` only for precise modifications to existing text.
- Never claim that report draft content is saved unless a real file tool returned success in the same turn.

This is not the primary safety mechanism; it reduces tool-use friction.

### 3. Add Deterministic Required-Draft-Write Guard

At turn start, the backend should decide whether this turn objectively requires a report draft mutation.

Create a small per-turn structure such as:

```python
required_writes = {
    "content/report_draft_v1.md": DraftWriteSnapshot(...)
}
```

Use objective inputs and a precise predicate. A required draft write is created only when all of these are true:

1. Current turn is allowed to write non-plan files.
2. Current workspace stage is one of `S4`, `S5`, `S6`, `S7`, or `done`.
3. The user's latest message has report-body write intent for that stage.

For `S4`, report-body write intent is true when either:

- The message explicitly asks for report body work, such as "继续写", "继续写吧", "继续写报告", "接着写", "补全剩余章节", "续写", "扩写正文", "写下一章", "写第三章", "补正文", "完善正文".
- The message is a short follow-up continuation such as "继续", "可以继续", "接着", "往下写", and the most recent assistant turn asked the user to continue writing the draft or said it would complete remaining report chapters after confirmation.

For `S5`, `S6`, `S7`, and `done`, report-body write intent is true only when the user explicitly asks to change report body content, such as "修改正文", "补一段报告", "扩写第三章", "重写结论", "把报告里 X 改成 Y", "继续写报告正文". Short generic follow-ups such as "继续", "开始审查", "继续审查", "导出", "归档", or "下一步" do not require a draft write in these later stages.

Report-body write intent is false for review, quality-check, export, stage-transition, question-answering, or planning requests, even if the draft is below the word floor.

The report draft being below the word floor is not enough by itself to require a write. It only matters after report-body write intent has been established.

For `S0`, `S1`, `S2`, and `S3`, the guard never creates required draft writes. Existing gates should block premature body writing.

Snapshot fields:

- Whether the file existed.
- File hash or exact content hash.
- Word count.
- Last modified time as an auxiliary signal, not the only signal.

At the point where a final assistant text response would be accepted, verify every required path. The required-write guard has exactly one source of truth: the project file snapshot.

- If the file hash changed, accept.
- If the file did not exist and now exists with substantive content, accept.
- If the file did not change, do not save the assistant's false-completion response to `conversation.json`; instead add an internal retry message and continue the existing retry loop.

Feedback to the model should be specific:

```text
用户本轮要求继续撰写报告正文，因此必须真实更新 `content/report_draft_v1.md`。
刚才没有检测到该文件发生变化。请先调用 `append_report_draft` 追加正文；
如果需要整体重写，才使用 `write_file`。不要只口头说明已完成。
```

Limit retries with the existing retry pattern. Reuse `MAX_MISSING_WRITE_RETRIES` or add a clearly named limit such as `MAX_REQUIRED_WRITE_RETRIES`.

### 4. Successful Write Recognition

Update successful-write tracking so these tools are recognized as report-draft write attempts:

- `write_file` success targeting the canonical path.
- `edit_file` success targeting the canonical path.
- `append_report_draft` success.

For `append_report_draft`, `_extract_successful_write_path()` should return `SkillEngine.REPORT_DRAFT_PATH` when the tool result has `status: success`.

This tracking is not the acceptance source for the deterministic required-write guard. Required writes pass only when the before/after file snapshot proves a real mutation. Successful-write tracking remains useful for the existing assistant-text missing-write feedback and for diagnostics.

### 5. Memory And Context

`append_report_draft` must persist the full combined draft into `conversation_state.json` under the same workspace memory source key used by `write_file`:

```text
file:content/report_draft_v1.md
```

This matters because `build_project_context()` does not currently include the draft body directly. The next model turn often relies on conversation-state workspace memory to know what has already been written.

### 6. Stage Compatibility

Expected outcomes:

- S4 with partial draft below word floor: if the user asks to continue report-body writing, append increases word count; stage remains S4 until the report is ready and the user starts review.
- S4 with partial draft below word floor: if the user asks a question, asks for review, or asks for export/status, no required draft write is created merely because the draft is short.
- S4 with ready draft but no `review_started_at`: stage remains S4.
- User asks "开始审查": existing checkpoint flow may set `review_started_at`, but this should not require `append_report_draft`.
- S5 after review started: explicit report-body edits require and accept draft mutation; review actions should not trigger required draft write.
- S6/S7/done: explicit report-body edits require and accept draft mutation; presentation, delivery, export, and archive actions should not trigger required draft write.
- Legacy draft paths remain blocked.

### 7. Testing Requirements

Add tests in `tests/test_chat_runtime.py`:

1. `append_report_draft` creates `content/report_draft_v1.md` through the normal write gate.
2. `append_report_draft` appends to an existing draft with a clean blank-line boundary.
3. `append_report_draft` is blocked before non-plan writing is allowed.
4. `append_report_draft` updates workspace memory as `file:content/report_draft_v1.md`.
5. S4 "继续写吧" with no actual draft mutation triggers retry feedback instead of accepting false completion.
6. S4 "继续写吧" with `append_report_draft` success is accepted.
7. S4 short "继续" after the previous assistant asked whether to continue writing the draft triggers the required-write guard.
8. S5 "开始审查" does not require a draft mutation.
9. S5 explicit body edit request requires and accepts draft mutation.
10. S6/S7 export or archive requests do not require a draft mutation.
11. Legacy draft path blocking remains unchanged.

Add tests in `tests/test_skill_engine.py` only if a helper is introduced in `SkillEngine`; otherwise keep stage assertions in existing tests.

### 8. Packaging And Verification

Before completion:

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py
.venv\Scripts\python -m pytest tests/test_skill_engine.py tests/test_workspace_materials.py tests/test_stage_quality_gates.py
.\build.bat
```

The packaged app must include the updated `skill/SKILL.md` and backend changes in `dist\咨询报告助手\`.

Packaging depends on local private files `managed_client_token.txt` and `managed_search_pool.json`. If either file is missing, report that packaging is blocked after tests pass; do not invent replacements.

## Open Assumptions

- `append_report_draft` is an internal LLM tool only; no frontend button is needed.
- The canonical report draft remains Markdown.
- The user still controls stage advance into S5 through the existing workspace button or explicit stage intent.
