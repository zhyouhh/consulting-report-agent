# 2026-05-13 packaged S0-S7 QA handoff

## Scope

Validated the rebuilt `dist\咨询报告助手\` package after the DeepSeek official-channel tool-call fix.

Primary goals:
- Confirm the official `deepseek-v4-pro` channel can complete a tool-call round trip without 400 errors.
- Run the packaged app through the S0-S7 lifecycle.
- Record blockers for the next repair session.

## Result

Backend/API lifecycle can be driven from S0 to `done / 已归档` in the packaged build.

The package is **not ready for user handoff** because the GUI crashes at startup and the packaged quality-check script fails under Windows PowerShell.

## Evidence Summary

Local QA evidence was generated under:

`D:\MyProject\consulting-report-agent\.gstack\qa-reports\s0-s7-package-run-20260513-005421\`

This `.gstack` folder is local QA output and is intentionally not committed.

Key observed state:
- Final stage: `done`
- Final status: `已归档`
- Official DeepSeek tool-call 400: not reproduced after the fix
- Packaged app process was stopped after QA; port `8080` was released

## Fixed In Current Branch

### DeepSeek official-channel tool-call compatibility

Root cause:
- Official DeepSeek reasoner routes reject explicit `tool_choice="auto"` in this setup.
- Thinking/tool-call responses require non-empty `reasoning_content` to be passed back with the assistant tool-call message.
- SDK `model_dump()` can include null-only fields such as `reasoning_content: null`, which official routes reject.

Implemented:
- For DeepSeek models, omit explicit `tool_choice`; rely on the provider default.
- Preserve non-empty `reasoning_content` in stream and non-stream tool-call follow-up messages.
- Serialize assistant tool-call messages with only provider-needed fields; omit null SDK fields.

Regression coverage:
- Stream tool-call follow-up preserves `reasoning_content`.
- Stream/non-stream follow-up omits empty/null `reasoning_content`.
- DeepSeek requests include `tools` but omit explicit `tool_choice`.
- Full `tests/test_chat_runtime.py` and the rest of `tests/` passed during this session.

## Open Bugs From Packaged QA

### P0/P1: Packaged GUI startup crash

Symptom:
- Opening `http://127.0.0.1:8080` from the packaged exe shows `应用出错，请刷新页面`.
- Browser console: `TypeError: Cannot read properties of null (reading 'mode')`.
- `/api/settings` and `/api/projects` returned 200, so this appears to be a frontend state/null-handling crash rather than FastAPI startup failure.

Likely next investigation path:
- Start the packaged exe.
- Open browser devtools on `/`.
- Trace settings load path in `frontend/src`.
- Add a regression test around null/initial settings state before the UI reads `.mode`.

### P1: Packaged `quality_check.ps1` encoding failure

Symptom:
- `POST /api/projects/{id}/quality-check` returns `{"status":"error","output":null}`.
- Running the bundled script directly shows garbled Chinese text and PowerShell parser errors.

Observed command shape:

```powershell
powershell -ExecutionPolicy Bypass -File dist\咨询报告助手\_internal\skill\scripts\quality_check.ps1 -FilePath <report_draft_v1.md>
```

Likely root cause:
- UTF-8 script content is being interpreted by Windows PowerShell with a legacy code page.

Likely next fix:
- Ensure packaged `.ps1` scripts are encoded as UTF-8 with BOM or invoke PowerShell with an encoding-safe approach.
- Add a packaged-script smoke test that runs `quality_check.ps1` directly from `_internal`.

### P2: Checkpoint API accepts out-of-order advancement

Symptom:
- `review-started` checkpoint was accepted while the workspace was still at S2 because earlier quality gates were incomplete.

Current behavior:
- `record_stage_checkpoint()` validates the direct prerequisite file for the target checkpoint.
- It does not enforce that all earlier stage gates have completed.

User impact:
- UI probably reduces the chance of this, but API callers or future automation can create inconsistent state.

Likely next fix:
- Add predecessor-stage validation in `SkillEngine.record_stage_checkpoint()`.
- Add tests for rejecting `review_started_at` before S3/S4 prerequisites are actually complete.

### P2/P3: `export-draft` depends on external `pandoc`

Current behavior:
- `skill/scripts/export_draft.ps1` does `Get-Command pandoc`.
- The package does not bundle `pandoc.exe`.

Decision needed:
- Bundle Pandoc for a heavier but more self-contained Windows package.
- Or replace the export path with a lighter Python-native `.docx` generator, accepting reduced Markdown fidelity.

For this product's target users, requiring them to install Pandoc is probably the wrong UX.

## Recommended Next Session Order

1. Fix packaged GUI startup crash first; users cannot use the product if this remains.
2. Fix packaged PowerShell script encoding and add direct packaged-script smoke coverage.
3. Decide Pandoc bundling vs Python-native export, then fix `export-draft`.
4. Harden checkpoint API ordering after the user-facing blockers are closed.
5. Rebuild package and rerun packaged GUI + S0-S7 QA.
