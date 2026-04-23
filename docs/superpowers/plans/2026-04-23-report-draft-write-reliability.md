# Report Draft Write Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make S4/S5+ report-body continuation impossible to fake by adding a dedicated `append_report_draft` tool and deterministic file-snapshot verification.

**Architecture:** Keep `content/report_draft_v1.md` as the only report draft path. `append_report_draft` is only a convenience wrapper that builds the next full draft and then calls the existing `_execute_plan_write()` gate chain. Required-write enforcement is based on before/after file snapshots, not assistant wording or tool success alone, and is wired into both streaming and non-streaming chat loops.

**Tech Stack:** Python 3, FastAPI backend, OpenAI-compatible chat tools, `unittest`/`pytest`, existing `SkillEngine` stage machine.

---

## Preconditions

- Work on branch `codex/report-draft-write-reliability`.
- Do not revert unrelated existing changes in the working tree.
- Do not commit inside subagent tasks. The controller will run final verification, build the package, create one final commit, and push as requested by the user.
- The user explicitly requested push for this task. Worker subagents still must not push; only the controller may push after final verification and packaging.
- Use `gpt-5.4` with `xhigh` for all subagents.
- Workers should edit files directly and list changed paths in their final message.

## Files

- Modify: `backend/chat.py`
  - Add `append_report_draft` tool schema and execution.
  - Add lightweight substantive-content helpers.
  - Add report-body intent and required-write snapshot helpers.
  - Wire required-write verification into `chat_stream()` and `chat()`.
  - Keep persistence through `_execute_plan_write()`.
- Modify: `skill/SKILL.md`
  - Document that S4/S5 body continuation should prefer `append_report_draft`.
- Modify: `tests/test_chat_runtime.py`
  - Add tests for the new tool, required-write guard, streaming and non-streaming retry behavior, S4/S5 boundaries, and memory persistence.
- Optional Modify: `tests/test_skill_engine.py`
  - Only if a helper is added to `SkillEngine`. Prefer keeping the implementation in `ChatHandler`.

---

### Task 1: Add `append_report_draft` Tool And Tool-Level Tests

**Files:**
- Modify: `backend/chat.py`
- Modify: `tests/test_chat_runtime.py`
- Modify: `skill/SKILL.md`

- [ ] **Step 1: Write failing tests for `append_report_draft` basics**

Add tests near existing report-draft path tests in `tests/test_chat_runtime.py`.

Required test cases:

```python
def test_append_report_draft_creates_canonical_draft_via_write_gate(self, mock_openai):
    del mock_openai
    handler = self._make_handler_with_project()
    handler._turn_context = handler._new_turn_context(can_write_non_plan=True)

    result = handler._execute_tool(
        self.project_id,
        self._make_tool_call(
            "append_report_draft",
            json.dumps({"content": "## 第三章：IP 强度对比\n\n" + ("正文" * 80)}, ensure_ascii=False),
        ),
    )

    self.assertEqual(result["status"], "success")
    self.assertEqual(result["path"], "content/report_draft_v1.md")
    self.assertTrue((self.project_dir / "content" / "report_draft_v1.md").exists())
```

```python
def test_append_report_draft_appends_with_clean_blank_line_boundary(self, mock_openai):
    del mock_openai
    handler = self._make_handler_with_project()
    handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
    draft_path = self.project_dir / "content" / "report_draft_v1.md"
    draft_path.write_text("# Draft\n\n## 第一章\n\n已有正文\n", encoding="utf-8")

    result = handler._execute_tool(
        self.project_id,
        self._make_tool_call(
            "append_report_draft",
            json.dumps({"content": "## 第二章\n\n" + ("新增正文" * 60)}, ensure_ascii=False),
        ),
    )

    text = draft_path.read_text(encoding="utf-8")
    self.assertEqual(result["status"], "success")
    self.assertIn("已有正文\n\n## 第二章", text)
```

Also add:

- Short content is rejected.
- Tool is blocked when `can_write_non_plan=False`.
- Successful append updates `conversation_state.json` memory entry with `source_key == "file:content/report_draft_v1.md"`.

- [ ] **Step 2: Run the new tests and verify they fail**

Run targeted tests, replacing names with the actual test names if needed:

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::ChatRuntimeTests::test_append_report_draft_creates_canonical_draft_via_write_gate -q
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -k "append_report_draft" -q
```

Expected: fail because the tool is unknown.

- [ ] **Step 3: Implement the tool schema**

In `ChatHandler._get_tools()`, add:

```python
{
    "type": "function",
    "function": {
        "name": "append_report_draft",
        "description": (
            "追加或续写报告正文到唯一草稿路径 content/report_draft_v1.md。"
            "用于 S4/S5 中用户要求继续写、补全章节、扩写正文时。"
            "不要用于 plan 文件、审查清单、交付记录或最终归档。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "要追加到报告草稿末尾的新正文，必须是完整 Markdown 段落或章节，不要只写摘要。",
                },
            },
            "required": ["content"],
        },
    },
}
```

- [ ] **Step 4: Implement tool execution through `_execute_plan_write()`**

Add a branch in `_execute_tool()` before `read_file`:

```python
if func_name == "append_report_draft":
    return self._execute_append_report_draft(project_id, args.get("content", ""))
```

Add `_execute_append_report_draft()` in `ChatHandler`:

- Validate content is string and substantive: after lightweight removal of common Markdown markers (`#`, bullets, emphasis markers, link/image delimiters, code fences) and whitespace, at least 80 characters must remain.
- Read existing draft if present through `skill_engine.read_file()` or direct project file read after canonical path resolution.
- Join with exactly one blank-line boundary.
- Call `_execute_plan_write()` with the canonical path and combined content.
- If `_execute_plan_write()` succeeds, enrich the returned result with `path`, `appended_chars`, `word_count`, `report_word_floor`, and `report_ready`.

The helper must not write directly to disk.

- [ ] **Step 5: Update success tracking and memory exclusion**

Update `_extract_successful_write_path()`:

```python
if func_name == "append_report_draft" and result.get("status") == "success":
    return self.skill_engine.REPORT_DRAFT_PATH
```

Update `_current_turn_successful_tool_source_keys()` so `append_report_draft` success maps to `file:content/report_draft_v1.md`; otherwise the same-turn memory exclusion may duplicate the just-written draft in the prompt.

- [ ] **Step 6: Update skill wording**

In `skill/SKILL.md` under file tools or S4:

- Add that S4/S5 report body continuation should prefer `append_report_draft(content)`.
- Keep `write_file` for whole-draft replacement.
- Keep `edit_file` for precise local edits.
- State that the assistant must not claim saved report content unless a real file tool succeeded in the same turn.

- [ ] **Step 7: Run Task 1 tests**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -k "append_report_draft" -q
```

Expected: all append-report tests pass.

---

### Task 2: Add Required-Draft-Write Intent And Snapshot Helpers

**Files:**
- Modify: `backend/chat.py`
- Modify: `tests/test_chat_runtime.py`

- [ ] **Step 1: Write failing helper tests**

Add tests for pure-ish helper behavior. These can call private helpers directly; the codebase already tests private runtime helpers.

Required cases:

- S4 explicit: `"继续写吧"` requires `content/report_draft_v1.md`.
- S4 chapter explicit: `"写第三章"` requires `content/report_draft_v1.md`.
- S4 contextual short follow-up: previous assistant says `"若无问题，请回复“继续”，我将补全剩余章节"` and user says `"继续"`; requires draft write.
- S4 non-writing question: `"现在字数多少？"` does not require draft write.
- S4 review transition: `"开始审查"` does not require draft write.
- S4 blocked/negative request with `can_write_non_plan=False` does not create required writes even if the text contains `"继续写"`; `_execute_plan_write()` remains the gate if the model tries anyway.
- S5 review transition: `"开始审查"` does not require draft write.
- S5 explicit body edit: `"扩写第三章"` requires draft write.
- S5+ inline edit explicit: `"把报告里 X 改成 Y"` requires draft write.
- S5+ body continuation explicit: `"继续写报告正文"` requires draft write.
- S6/S7 export/archive: `"导出可审草稿"` or `"归档"` does not require draft write.
- S0-S3 never creates required draft writes.

Suggested helper names to test:

```python
handler._build_required_write_snapshots(project_id, user_message)
handler._message_has_report_body_write_intent(project_id, user_message, stage_code)
```

The exact names may change, but keep the behavior covered.

- [ ] **Step 2: Run helper tests and verify they fail**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -k "required_draft_write or report_body_write_intent" -q
```

Expected: fail because helpers do not exist.

- [ ] **Step 3: Implement small intent helpers**

Keep this intentionally small; do not build a broad classifier.

Suggested constants:

```python
REPORT_BODY_EXPLICIT_WRITE_KEYWORDS = (
    "继续写", "继续写吧", "继续写报告", "继续写正文", "接着写",
    "补全剩余章节", "续写", "扩写正文", "写下一章", "补正文", "完善正文",
    "修改正文", "补一段报告", "重写结论",
)
REPORT_BODY_SHORT_CONTINUATION_KEYWORDS = ("继续", "可以继续", "接着", "往下写")
REPORT_BODY_REVIEW_OR_DELIVERY_KEYWORDS = (
    "开始审查", "继续审查", "质量检查", "运行质量检查", "导出", "归档", "交付",
)
```

Rules:

- First check `self._turn_context.get("can_write_non_plan")`; if false, do not create required writes. This prevents an impossible retry loop where the guard demands a draft mutation that the write gate would reject.
- If a review/delivery keyword is present, return false unless an explicit body-write keyword is also present.
- `S4`: explicit body-write keywords count; short continuation counts only if recent assistant history asked user to continue writing or promised remaining report chapters.
- `S5`/`S6`/`S7`/`done`: only explicit body-write keywords count.
- `S0`/`S1`/`S2`/`S3`: always false.

- [ ] **Step 4: Implement snapshot helpers**

Add a lightweight structure. A `dict` is acceptable; a dataclass is also fine if it stays local to `backend/chat.py`.

Snapshot fields:

```python
{
    "path": "content/report_draft_v1.md",
    "exists": bool,
    "sha256": str | None,
    "word_count": int,
    "mtime": float | None,
}
```

Use content hash as the acceptance source. `mtime` is diagnostic only.

Helpers:

- `_snapshot_project_file(project_id, normalized_path) -> dict`
- `_required_write_paths_for_turn(project_id, user_message) -> set[str]`
- `_build_required_write_snapshots(project_id, user_message) -> dict[str, dict]`
- `_required_writes_satisfied(project_id, snapshots) -> tuple[bool, list[str]]`

For report draft substantive existence, use the same lightweight substantive-content helper as Task 1.

- [ ] **Step 5: Run Task 2 tests**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -k "required_draft_write or report_body_write_intent" -q
```

Expected: pass.

---

### Task 3: Wire Required-Write Guard Into Streaming And Non-Streaming Chat Loops

**Files:**
- Modify: `backend/chat.py`
- Modify: `tests/test_chat_runtime.py`

- [ ] **Step 1: Write failing streaming tests**

Add tests that simulate provider responses.

Case A: S4 required write with no tool mutation retries instead of accepting false completion.

Set up project past S3 with partial draft and prior assistant context if needed. Mock the provider stream:

1. First response: assistant text only, e.g. `"报告全文已存入 content/report_draft_v1.md"` with no tool calls.
2. Second response: a real `append_report_draft` tool call.
3. Third response: final confirmation text.

Assert:

- Events include the required-write retry warning.
- `content/report_draft_v1.md` changed.
- Final `conversation.json` does not keep the first false-completion as the accepted assistant turn.

Case B: S4 required write with `append_report_draft` success is accepted without retry.

Case C: S5 `"开始审查"` does not require mutation and can finish with text only.

- [ ] **Step 2: Write failing non-streaming tests**

Use `handler.chat(...)` or `_chat_unlocked(...)` patterns already in the file.

Required cases:

- S4 required write with text-only response gets retried.
- S4 with successful `append_report_draft` passes.

This is mandatory; spec review explicitly called out both loops.

- [ ] **Step 3: Run new loop tests and verify they fail**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -k "required_draft_write and chat_stream" -q
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -k "required_draft_write and non_stream" -q
```

Expected: fail because loop guard is not wired.

- [ ] **Step 4: Add per-turn required-write snapshots**

In both `_chat_stream_unlocked()` and `_chat_unlocked()`:

- After `_build_turn_context(...)`, compute:

```python
required_write_snapshots = self._build_required_write_snapshots(project_id, user_message)
required_write_retries = 0
```

- Store in local loop state, not global state.

- [ ] **Step 5: Verify snapshots before accepting final assistant text**

In the branch where there are no tool calls and the candidate assistant message would be accepted:

1. Run existing self-correction check.
2. Run existing missing-write text guard.
3. Run required-write snapshot verification.
4. If required writes are unsatisfied and retry budget remains:
   - Emit a stream/tool notice in streaming mode.
   - Append the candidate assistant message and a user feedback message to `current_turn_messages`.
   - Continue the loop.
5. If retry budget is exhausted:
   - Return a clear assistant message saying the report draft was not updated and the user should retry, without claiming completion.

Use a helper:

```python
def _build_required_write_feedback(self, missing_paths: list[str]) -> str:
    ...
```

Required feedback should explicitly tell the model to call `append_report_draft` for report continuation.

- [ ] **Step 6: Ensure final persisted assistant message is safe**

The accepted assistant turn must be the last successful response after required writes are satisfied, not the false completion that triggered retry.

Do not write the false-completion candidate into `conversation.json` as the final assistant message.

- [ ] **Step 7: Run Task 3 tests**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -k "required_draft_write" -q
```

Expected: pass.

---

### Task 4: Full Verification, Regression Checks, Packaging Readiness

**Files:**
- Modify only if Task 1-3 left documentation or test gaps.

- [ ] **Step 1: Run targeted runtime tests**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -k "append_report_draft or required_draft_write or legacy_report_draft" -q
```

Expected: pass.

- [ ] **Step 2: Run full chat runtime tests**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py
```

Expected: pass, with existing warnings only.

- [ ] **Step 3: Run stage and workspace regression tests**

```powershell
.venv\Scripts\python -m pytest tests/test_skill_engine.py tests/test_workspace_materials.py tests/test_stage_quality_gates.py
```

Expected: pass.

- [ ] **Step 4: Run diff sanity checks**

```powershell
git diff --check -- backend/chat.py skill/SKILL.md tests/test_chat_runtime.py docs/superpowers/specs/2026-04-23-report-draft-write-reliability-design.md docs/superpowers/plans/2026-04-23-report-draft-write-reliability.md
```

Expected: no whitespace errors. CRLF warnings are acceptable if they already appear in this repo.

- [ ] **Step 5: Package**

If `managed_client_token.txt` and `managed_search_pool.json` exist:

```powershell
if (Test-Path dist\咨询报告助手) { Remove-Item -LiteralPath dist\咨询报告助手 -Recurse -Force }
.\build.bat
```

Expected:

- `dist\咨询报告助手\咨询报告助手.exe` exists.
- Packaged `_internal\skill\SKILL.md` includes `append_report_draft`.

If packaging private files are missing, report packaging blocked after tests pass.

- [ ] **Step 6: Final commit and push**

The controller, not worker subagents, performs this after all reviews and packaging:

```powershell
git status --short
git add backend/chat.py skill/SKILL.md tests/test_chat_runtime.py docs/superpowers/specs/2026-04-23-report-draft-write-reliability-design.md docs/superpowers/plans/2026-04-23-report-draft-write-reliability.md
git commit -m "fix: require real report draft writes"
git push -u origin codex/report-draft-write-reliability
```

This push is allowed because the user explicitly requested it in the current task. Do not generalize this into future automatic pushes.

Before staging, the controller must decide whether earlier uncommitted files from the prior bugfix should be included in the same commit or left out. Do not stage unrelated `docs/current-worklist.md` or untracked `AGENTS.md` unless the user explicitly requests it.
