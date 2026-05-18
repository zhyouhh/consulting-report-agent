# Stage Conductor v0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace text/tag-based stage mutation with an explicit `advance_stage` tool plus strict stage write gates, then package and visually verify S0-S7 and assistant Markdown table rendering.

**Architecture:** `stage_checkpoints.json` remains the truth source. `SkillEngine.record_stage_checkpoint()` owns transition validation for both UI and model tools. `ChatHandler` owns the model-facing `advance_stage` tool and write-time gates.

**Tech Stack:** Python 3.11/3.12, FastAPI, OpenAI-compatible tool calls, React, Node native test runner, PyInstaller Windows packaging.

---

## Existing Context

- Worktree: `D:\MyProject\consulting-report-agent\.worktrees\stage-conductor-v0`
- Branch: `codex/stage-conductor-v0`
- Spec: `docs/superpowers/specs/2026-05-19-stage-conductor-v0-design.md`
- Markdown table fix already committed on this branch:
  - `frontend/src/components/ChatPanel.jsx`
  - `frontend/tests/chatPanelMarkdown.test.mjs`

Do not implement on `main`. Do not push unless the user explicitly asks.

## File Map

- `backend/skill.py`: checkpoint keys, predecessor validation, stage inference, write-stage helper candidates.
- `backend/chat.py`: tool schema, `_execute_tool`, `_execute_plan_write`, turn context, conversation sanitize, claim mismatch guard.
- `backend/main.py`: checkpoint endpoint behavior.
- `backend/stage_ack.py`: remove from runtime; delete only after tests are rewritten.
- `skill/SKILL.md`: model workflow instructions; remove stage-ack and instruct `advance_stage`.
- `tests/test_skill_engine.py`: checkpoint predecessor and stage inference tests.
- `tests/test_chat_runtime.py`: tool schema/execution/write gate/conversation tests.
- `tests/test_stage_ack.py`: delete or replace with no-op sanitizer tests.
- `tests/test_packaging_docs.py`: update doc assertions that mention stage-ack.
- `frontend/src/components/ChatPanel.jsx`: already has GFM table rendering; optionally add tag sanitizer only if backend tests reveal legacy leakage.
- `frontend/tests/chatPanelMarkdown.test.mjs`: existing Markdown regression.

---

## Task 1: SkillEngine Checkpoint Transition Validation

**Files:**
- Modify: `backend/skill.py`
- Test: `tests/test_skill_engine.py`
- Test: `tests/test_main_api.py`

- [ ] **Step 1: Write failing tests for full predecessor validation**

Add tests near existing checkpoint tests:

```python
def test_record_stage_checkpoint_rejects_outline_confirmation_without_s0(self):
    project_dir = self._make_project()
    self._write_stage_two_prerequisites(project_dir)

    with self.assertRaisesRegex(ValueError, "需求访谈|S0|s0"):
        self.engine.record_stage_checkpoint("demo", "outline_confirmed_at", "set")

def test_record_stage_checkpoint_rejects_review_start_without_data_and_analysis_quality(self):
    project_dir = self._make_project()
    self._write_stage_two_prerequisites(project_dir)
    self.engine._save_stage_checkpoint(project_dir, "s0_interview_done_at")
    self.engine._save_stage_checkpoint(project_dir, "outline_confirmed_at")
    (project_dir / "content").mkdir(exist_ok=True)
    (project_dir / "content" / "report_draft_v1.md").write_text(
        "# Draft\n\n" + ("有效正文。" * 1200),
        encoding="utf-8",
    )

    with self.assertRaisesRegex(ValueError, "data-log|analysis"):
        self.engine.record_stage_checkpoint("demo", "review_started_at", "set")
```

Add delivery-mode tests:

```python
def test_record_delivery_archived_report_only_requires_review_passed(self):
    ...

def test_record_delivery_archived_presentation_mode_requires_presentation_ready(self):
    ...
```

Update API/table tests in `tests/test_main_api.py`:

```python
def _write_stage_one_prerequisites(project_dir: Path) -> None:
    # Copy the compact helper from ChatRuntimeTests or keep it local in this
    # test module. It should write effective notes.md, references.md,
    # outline.md, and research-plan.md.

def test_checkpoint_tables_key_sets_are_aligned(self):
    from backend.main import _CHECKPOINT_ROUTES
    from backend.skill import SkillEngine

    route_keys = set(_CHECKPOINT_ROUTES.values())
    self.assertEqual(route_keys, SkillEngine.STAGE_CHECKPOINT_KEYS)

def test_checkpoint_endpoint_rejects_review_started_when_predecessors_missing(self):
    # Use a real temporary SkillEngine project, not a mock, so the endpoint
    # exercises record_stage_checkpoint() predecessor validation.
    from backend.skill import SkillEngine
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = Path(__file__).resolve().parents[1] / "skill"
        engine = SkillEngine(Path(tmpdir) / "projects", skill_dir)
        engine.create_project(name="demo", workspace_dir=str(Path(tmpdir) / "ws"))
        project_dir = engine.get_project_path("demo")
        self._write_stage_one_prerequisites(project_dir)
        engine._save_stage_checkpoint(project_dir, "s0_interview_done_at")
        engine._save_stage_checkpoint(project_dir, "outline_confirmed_at")
        (project_dir / "content").mkdir(exist_ok=True)
        (project_dir / "content" / "report_draft_v1.md").write_text(
            "# Draft\n\n" + ("有效正文。" * 1200),
            encoding="utf-8",
        )
        with mock.patch.object(main_module, "skill_engine", engine):
            response = self.client.post("/api/projects/demo/checkpoints/review-started")
    self.assertEqual(response.status_code, 400)
    self.assertIn("data-log", response.json()["detail"])
```

For the second test, create a project with an effective draft but missing
data-log/analysis prerequisites, then call
`/api/projects/{id}/checkpoints/review-started`. Expected: `400`.

- [ ] **Step 2: Run RED tests**

Run:

```powershell
.venv\Scripts\python -m pytest tests/test_skill_engine.py tests/test_main_api.py -q
```

Expected: new tests fail because `record_stage_checkpoint()` only checks direct prereq.

- [ ] **Step 3: Implement transition validation**

In `backend/skill.py`, add helper:

```python
def _validate_stage_checkpoint_transition(self, project_path: Path, key: str) -> None:
    checkpoints = self._load_stage_checkpoints(project_path)
    targets = self._resolve_length_targets(project_path)

    def require(condition: bool, message: str) -> None:
        if not condition:
            raise ValueError(message)

    project_overview_ready = self._is_effective_plan_file(project_path, "project-overview.md")
    notes_ready = self._has_effective_notes(project_path)
    references_ready = self._has_effective_references(project_path)
    outline_ready = self._has_effective_outline(project_path)
    research_plan_ready = self._has_effective_research_plan(project_path)
    data_ready = self._has_enough_data_log_sources(project_path, targets["data_log_min"])
    analysis_ready = self._has_enough_analysis_refs(project_path, targets["analysis_refs_min"])
    report_ready = self._has_effective_report_draft(project_path, min_words=targets["report_word_floor"])

    if key == "s0_interview_done_at":
        require(project_overview_ready, "需要先创建有效 project-overview.md，才能完成需求访谈。")
        return
    if key == "outline_confirmed_at":
        require("s0_interview_done_at" in checkpoints, "需要先完成 S0 需求访谈，才能确认大纲。")
        missing = []
        if not notes_ready:
            missing.append("notes.md")
        if not references_ready:
            missing.append("references.md")
        if not outline_ready:
            missing.append("outline.md")
        if not research_plan_ready:
            missing.append("research-plan.md")
        require(not missing, f"需要先补齐 {', '.join(missing)}，才能确认大纲。")
        return
    if key == "review_started_at":
        require("outline_confirmed_at" in checkpoints, "需要先确认大纲，才能开始审查。")
        require(data_ready, "需要先补齐 data-log.md 的有效来源条目，才能开始审查。")
        require(analysis_ready, "需要先补齐 analysis-notes.md 的 DL 引用，才能开始审查。")
        require(report_ready, "需要先形成达到字数门槛的有效正文，才能开始审查。")
        return
    if key == "review_passed_at":
        require("review_started_at" in checkpoints, "需要先进入质量审查，才能标记审查通过。")
        require(self._has_effective_review_checklist(project_path), "需要先完成有效 review-checklist.md，才能标记审查通过。")
        return
    if key == "presentation_ready_at":
        require("review_passed_at" in checkpoints, "需要先审查通过，才能标记演示准备完成。")
        require(self._delivery_mode_requires_presentation(project_path), "仅报告项目不需要演示准备阶段。")
        require(self._has_effective_presentation_plan(project_path), "需要先完成 presentation-plan.md，才能标记演示准备完成。")
        return
    if key == "delivery_archived_at":
        if self._delivery_mode_requires_presentation(project_path):
            require("presentation_ready_at" in checkpoints, "需要先完成演示准备，才能归档。")
        else:
            require("review_passed_at" in checkpoints, "需要先审查通过，才能归档。")
        require(self._has_effective_delivery_log(project_path), "需要先完成 delivery-log.md，才能归档。")
```

In `record_stage_checkpoint()`, replace `_validate_stage_checkpoint_prereq(project_path, key)` with the new helper only for `action == "set"`. `action == "clear"` must keep the existing idempotent cascading behavior and must not run predecessor validation. Keep `_validate_stage_checkpoint_prereq()` for user-facing notice fallback until chat code is updated.

- [ ] **Step 4: Run GREEN tests**

Run:

```powershell
.venv\Scripts\python -m pytest tests/test_skill_engine.py tests/test_main_api.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/skill.py tests/test_skill_engine.py tests/test_main_api.py
git commit -m "fix(stage): validate checkpoint predecessors"
```

---

## Task 2: Add `advance_stage` Tool and Remove Keyword Checkpoint Side Effects

**Files:**
- Modify: `backend/chat.py`
- Test: `tests/test_chat_runtime.py`

- [ ] **Step 1: Write failing tests**

Add tests around existing tool/runtime tests:

```python
def test_tool_schema_includes_advance_stage(self):
    handler = self._make_handler_with_project()
    tools = handler._build_tools()
    by_name = {tool["function"]["name"]: tool for tool in tools}
    self.assertIn("advance_stage", by_name)
    props = by_name["advance_stage"]["function"]["parameters"]["properties"]
    self.assertIn("checkpoint_key", props)
    self.assertIn("action", props)

def test_advance_stage_sets_outline_checkpoint_and_turn_event(self):
    handler = self._make_handler_with_project()
    self._write_stage_one_prerequisites(self.project_dir)
    handler.skill_engine._save_stage_checkpoint(self.project_dir, "s0_interview_done_at")
    handler._turn_context = handler._new_turn_context(can_write_non_plan=False)

    result = handler._execute_tool(
        self.project_id,
        self._make_tool_call("advance_stage", json.dumps({
            "checkpoint_key": "outline_confirmed_at",
            "reason": "用户确认大纲",
        }, ensure_ascii=False)),
    )

    self.assertEqual(result["status"], "success")
    self.assertIn("outline_confirmed_at", handler.skill_engine._load_stage_checkpoints(self.project_dir))
    self.assertEqual(handler._turn_context["checkpoint_event"], {"action": "set", "key": "outline_confirmed_at"})

def test_build_turn_context_confirm_outline_has_no_checkpoint_side_effect(self):
    handler = self._make_handler_with_project()
    self._write_stage_one_prerequisites(self.project_dir)
    handler.skill_engine._save_stage_checkpoint(self.project_dir, "s0_interview_done_at")

    turn_context = handler._build_turn_context(self.project_id, "确认大纲")

    self.assertNotIn("outline_confirmed_at", handler.skill_engine._load_stage_checkpoints(self.project_dir))
    self.assertIsNone(turn_context["checkpoint_event"])
```

Add S0 soft gate test:

```python
def test_advance_stage_s0_rejects_without_prior_assistant_turn(self):
    ...
```

- [ ] **Step 2: Run RED tests**

Run:

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -q -k "advance_stage or checkpoint_side_effect"
```

Expected: fails because tool does not exist and keywords still mutate pending checkpoint.

- [ ] **Step 3: Add tool schema**

Find `_build_tools()` in `backend/chat.py`. Add `advance_stage` function schema with `checkpoint_key`, `action`, `reason`.

- [ ] **Step 4: Add execution method**

Add:

```python
def _tool_advance_stage(self, project_id: str, checkpoint_key: object, reason: object, action: object = "set") -> dict:
    if checkpoint_key not in self.skill_engine.STAGE_CHECKPOINT_KEYS:
        return {"status": "error", "message": f"未知阶段 checkpoint: {checkpoint_key}"}
    if action not in {"set", "clear"}:
        return {"status": "error", "message": f"未知 action: {action}"}
    if not isinstance(reason, str) or not reason.strip():
        return {"status": "error", "message": "advance_stage 需要 reason"}
    if checkpoint_key == "s0_interview_done_at" and action == "set" and not self._has_prior_s0_assistant_turn(project_id):
        return {"status": "error", "message": "需要先完成一轮 S0 澄清提问，才能完成需求访谈。"}
    try:
        result = self.skill_engine.record_stage_checkpoint(project_id, checkpoint_key, action)
    except ValueError as exc:
        return {"status": "error", "checkpoint_key": checkpoint_key, "message": str(exc)}
    summary = self.skill_engine.get_workspace_summary(project_id)
    self._turn_context["checkpoint_event"] = {"action": action, "key": checkpoint_key}
    return {
        "status": "success",
        "action": action,
        "checkpoint_key": checkpoint_key,
        "stage_code": summary.get("stage_code"),
        "message": f"已记录阶段变更：{checkpoint_key}",
        **result,
    }
```

Wire in `_execute_tool()` for `func_name == "advance_stage"`.

- [ ] **Step 5: Remove keyword checkpoint side effects**

In `backend/chat.py`:

1. Remove `_STRONG_ADVANCE_KEYWORDS`, `_ROLLBACK_KEYWORDS`, `_STAGE_RANK` if no longer referenced.
2. Remove `_detect_stage_keyword()`.
3. In `_build_turn_context()`, delete the block that calls `_detect_stage_keyword()` and sets `pending_stage_keyword`.
4. Remove fallback execution of `pending_stage_keyword` in `_finalize_assistant_turn()`.

Keep `_is_non_plan_write_blocking_message()` and non-plan write intent logic.

Update tests that asserted checkpoint table parity with `_STAGE_RANK`. If
`tests/test_main_api.py::CheckpointTableInvariantTests` or similar tests import
`ChatHandler._STAGE_RANK`, rewrite them to compare `_CHECKPOINT_ROUTES` against
`SkillEngine.STAGE_CHECKPOINT_KEYS`, excluding the S0 set endpoint special case.

- [ ] **Step 6: Run GREEN tests**

Run:

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -q -k "advance_stage or checkpoint_side_effect or weak_affirmation"
```

Expected: all selected tests pass. Update/delete old keyword tests that assert side effects.

- [ ] **Step 7: Commit**

```powershell
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "feat(stage): add advance_stage tool"
```

---

## Task 3: Remove Stage-Ack Runtime and Add Sanitizer

**Files:**
- Modify: `backend/chat.py`
- Delete: `backend/stage_ack.py`
- Modify/Delete: `tests/test_stage_ack.py`
- Modify: `tests/test_chat_runtime.py`

- [ ] **Step 1: Write replacement failing tests**

Add tests:

```python
def test_stage_ack_text_is_stripped_but_does_not_set_checkpoint(self):
    handler = self._make_handler_with_project()
    handler.skill_engine._save_stage_checkpoint(self.project_dir, "s0_interview_done_at")
    self._write_stage_one_prerequisites(self.project_dir)

    persisted = self._finalize_assistant_for_test(
        handler,
        "大纲已确认。\n<stage-ack>outline_confirmed_at</stage-ack>\n",
    )

    self.assertNotIn("<stage-ack", persisted)
    self.assertNotIn("outline_confirmed_at", handler.skill_engine._load_stage_checkpoints(self.project_dir))

def test_load_conversation_strips_legacy_stage_ack(self):
    handler = self._make_handler_with_project()
    self._write_conversation([
        {"role": "assistant", "content": "旧消息\n<stage-ack>outline_confirmed_at</stage-ack>\n"}
    ])

    loaded = handler._load_conversation(self.project_id)

    self.assertNotIn("<stage-ack", loaded[0]["content"])
```

- [ ] **Step 2: Run RED tests**

Run:

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -q -k "stage_ack_text or legacy_stage_ack"
```

Expected: old stage-ack path sets checkpoint or parser tests still expect execution.

- [ ] **Step 3: Add simple strip helper**

In `backend/chat.py`, add module-level regex/helper:

```python
_STAGE_ACK_STRIP_RE = re.compile(
    r'<stage-ack(?:\s+action="(?:set|clear)")?>[a-z_0-9]+</stage-ack>',
    re.IGNORECASE,
)

def _strip_legacy_stage_ack(content: str) -> str:
    if not content or "<stage-ack" not in content.lower():
        return content
    return re.sub(r"\n{3,}", "\n\n", _STAGE_ACK_STRIP_RE.sub("", content)).strip()
```

Use it when:

1. loading assistant conversation messages;
2. finalizing assistant visible content before persistence;
3. sanitizing provider messages if a legacy tag survives.

- [ ] **Step 4: Remove stage-ack execution**

In `_finalize_assistant_turn()`:

1. remove `StageAckParser` import;
2. remove parse/executable event logic;
3. strip legacy stage-ack text only;
4. preserve empty-turn fallback if stripping empties content.

Delete `backend/stage_ack.py` and `tests/test_stage_ack.py`, or leave no runtime references and remove tests from discovery. Prefer deletion.

- [ ] **Step 5: Update old tests**

Delete or rewrite `StageAckFinalizePipelineTests`, `StageAckRegressionTests`, and stream tail stage-ack tests in `tests/test_chat_runtime.py`. Replacement tests should assert no side effect.

Search:

```powershell
rg -n "StageAck|stage-ack|_STAGE_ACK|pending_stage_keyword" backend tests
```

Expected after task: no runtime stage-ack execution references; only sanitizer tests and historical docs may mention it.

- [ ] **Step 6: Run GREEN tests**

Run:

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py tests/test_stage_checkpoints.py -q
```

Expected: pass.

- [ ] **Step 7: Commit**

```powershell
git add backend/chat.py tests/test_chat_runtime.py tests/test_stage_ack.py backend/stage_ack.py
git commit -m "fix(stage): retire stage-ack execution"
```

---

## Task 4: Stage Write Gates

**Files:**
- Modify: `backend/chat.py`
- Test: `tests/test_chat_runtime.py`

- [ ] **Step 1: Write failing tests**

Add tests:

```python
def test_write_file_rejects_data_log_before_outline_checkpoint(self):
    handler = self._make_handler_with_project()
    handler.skill_engine._save_stage_checkpoint(self.project_dir, "s0_interview_done_at")
    self._write_stage_one_prerequisites(self.project_dir)
    handler._turn_context = handler._new_turn_context(can_write_non_plan=True)

    result = handler._execute_tool(self.project_id, self._make_tool_call(
        "write_file",
        json.dumps({"file_path": "plan/data-log.md", "content": "# Data log\n\n### [DL-2026-01] x\n- **URL**: https://example.com"}, ensure_ascii=False),
    ))

    self.assertEqual(result["status"], "error")
    self.assertIn("确认大纲", result["message"])

def test_advance_stage_then_write_data_log_same_turn_allowed(self):
    handler = self._make_handler_with_project()
    self._write_stage_one_prerequisites(self.project_dir)
    handler.skill_engine._save_stage_checkpoint(self.project_dir, "s0_interview_done_at")
    handler._turn_context = handler._new_turn_context(can_write_non_plan=True)

    advance = handler._execute_tool(... advance_stage outline_confirmed_at ...)
    write = handler._execute_tool(... write_file plan/data-log.md ...)

    self.assertEqual(advance["status"], "success")
    self.assertEqual(write["status"], "success")
```

Also add tests:

1. `analysis-notes.md` rejected before data-log threshold;
2. `review-checklist.md` rejected before `review_started_at`;
3. `delivery-log.md` rejected before `review_passed_at` / `presentation_ready_at`.

- [ ] **Step 2: Run RED tests**

Run:

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -q -k "data_log_before_outline or same_turn_allowed or review_checklist or delivery_log"
```

Expected: future-stage writes currently land or fail for wrong reason.

- [ ] **Step 3: Implement gate helper**

In `backend/chat.py`, add:

```python
def _validate_stage_write_allowed(self, project_id: str, normalized_path: str) -> str | None:
    project_path = self.skill_engine.get_project_path(project_id)
    if not project_path:
        return "项目不存在"
    checkpoints = self.skill_engine._load_stage_checkpoints(project_path)
    targets = self.skill_engine._resolve_length_targets(project_path)
    path = normalized_path

    if path in {"plan/project-overview.md", "plan/notes.md", "plan/references.md"}:
        return None
    if path in {"plan/outline.md", "plan/research-plan.md"}:
        if "s0_interview_done_at" not in checkpoints:
            return "需要先完成 S0 需求访谈，再写大纲或研究计划。"
        return None
    if path == "plan/data-log.md":
        if "outline_confirmed_at" not in checkpoints:
            return "需要先确认大纲，才能写入 data-log.md。"
        return None
    if path == "plan/analysis-notes.md":
        if not self.skill_engine._has_enough_data_log_sources(project_path, targets["data_log_min"]):
            return "需要先补齐 data-log.md 的有效来源条目，才能写 analysis-notes.md。"
        return None
    if path in {"plan/review-checklist.md", "plan/review.md"}:
        if "review_started_at" not in checkpoints:
            return "需要先进入质量审查阶段，才能写审查文件。"
        return None
    if path == "plan/presentation-plan.md":
        if "review_passed_at" not in checkpoints:
            return "需要先审查通过，才能写演示方案。"
        if not self.skill_engine._delivery_mode_requires_presentation(project_path):
            return "仅报告项目不需要 presentation-plan.md。"
        return None
    if path == "plan/delivery-log.md":
        if self.skill_engine._delivery_mode_requires_presentation(project_path):
            if "presentation_ready_at" not in checkpoints:
                return "需要先完成演示准备，才能写交付归档记录。"
        elif "review_passed_at" not in checkpoints:
            return "需要先审查通过，才能写交付归档记录。"
        return None
    return None
```

Call it in `_execute_plan_write()` after `normalized_path = self.skill_engine.validate_plan_write(...)` and canonical draft path normalization, before read-before-write and persistence.

- [ ] **Step 4: Ensure canonical draft still uses existing S4 gate**

Do not duplicate report draft logic in this helper. Keep `check_report_writing_stage()` in `append_report_draft` / canonical draft flow.

- [ ] **Step 5: Run GREEN tests**

Run:

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -q -k "data_log_before_outline or same_turn_allowed or analysis_notes or review_checklist or delivery_log"
```

Expected: pass.

- [ ] **Step 6: Commit**

```powershell
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "fix(stage): gate future-stage writes"
```

---

## Task 5: Prompt, Docs, Packaging Tests, and Claim Mismatch Guard

**Files:**
- Modify: `backend/chat.py`
- Modify: `skill/SKILL.md`
- Modify: `tests/test_packaging_docs.py`
- Test: `tests/test_chat_runtime.py`

- [ ] **Step 1: Write failing tests**

Add packaging doc assertions:

```python
self.assertIn("advance_stage", self.skill_md)
self.assertNotIn("<stage-ack>", self.skill_md)
self.assertNotIn("stage-ack 标签规范", self.skill_md)
```

Add claim mismatch test:

```python
def test_claim_stage_advance_without_advance_stage_gets_corrective_notice(self):
    handler = self._make_handler_with_project()
    handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
    handler._turn_context["stage_code_before_turn"] = "S1"
    result = self._finalize_assistant_for_test(handler, "已进入资料采集阶段。")
    notices = handler._turn_context.get("pending_system_notices", [])
    self.assertTrue(any("advance_stage" in n["reason"] or "advance_stage" in n["user_action"] for n in notices))

def test_claim_guard_does_not_fire_when_stage_auto_advances(self):
    handler = self._make_handler_with_project()
    handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
    handler._turn_context["stage_code_before_turn"] = "S2"
    self._write_stage_one_prerequisites(self.project_dir)
    handler.skill_engine._save_stage_checkpoint(self.project_dir, "s0_interview_done_at")
    handler.skill_engine._save_stage_checkpoint(self.project_dir, "outline_confirmed_at")
    (self.project_dir / "plan" / "data-log.md").write_text(
        "\n\n".join(
            f"### [DL-2026-{index:02d}] 事实 {index}\n- **URL**：https://example.com/{index}"
            for index in range(1, 9)
        ),
        encoding="utf-8",
    )
    self._finalize_assistant_for_test(handler, "已进入分析阶段。")
    notices = handler._turn_context.get("pending_system_notices", [])
    self.assertFalse(any(n["category"] == "stage_claim_without_checkpoint" for n in notices))
```

- [ ] **Step 2: Run RED tests**

Run:

```powershell
.venv\Scripts\python -m pytest tests/test_packaging_docs.py tests/test_chat_runtime.py tests/test_main_api.py -q -k "advance_stage or stage_ack or corrective or checkpoint"
```

Expected: fails because SKILL still mentions stage-ack and guard missing.

- [ ] **Step 3: Update `skill/SKILL.md`**

Remove stage-ack instructions and appendix. Replace stage transition language:

```md
用户明确确认推进或回退阶段时，先调用 `advance_stage`。
工具返回 success 后，才能继续写下一阶段文件。
不要用文字声称已推进阶段，除非 `advance_stage` 已成功。
```

S0 section:

```md
用户回答 S0 澄清问题后，或明确说跳过访谈后：
1. 必要时更新 `plan/project-overview.md`
2. 调用 `advance_stage(checkpoint_key="s0_interview_done_at", action="set", reason="...")`
3. 工具成功后进入 S1
```

- [ ] **Step 4: Add claim mismatch guard**

Store the initial stage in `_build_turn_context()`:

```python
summary = self.skill_engine.get_workspace_summary(project_id)
self._turn_context["stage_code_before_turn"] = summary.get("stage_code")
```

Then implement a small helper in `backend/chat.py`:

```python
_STAGE_ADVANCE_CLAIM_RE = re.compile(
    r"(进入资料采集|进入分析|进入报告撰写|进入质量审查|已推进到)",
)

def _maybe_emit_stage_claim_mismatch_notice(self, project_id: str, visible_content: str) -> None:
    if self._turn_context.get("checkpoint_event"):
        return
    if not _STAGE_ADVANCE_CLAIM_RE.search(visible_content or ""):
        return
    before = self._turn_context.get("stage_code_before_turn")
    try:
        current = self.skill_engine.get_workspace_summary(project_id).get("stage_code")
    except ValueError:
        current = before
    if current != before:
        return
    self._emit_system_notice_once(
        category="stage_claim_without_checkpoint",
        path=None,
        reason="助手声称推进阶段，但本轮没有成功调用 advance_stage。",
        user_action="请先调用 advance_stage；如果阶段未推进，请明确说明当前仍停留在原阶段。",
        surface_to_user=False,
    )
```

Call after visible content is stripped and before persistence. The guard must
not fire when S2/S3 legitimately auto-advance because file quality thresholds
changed during the turn.

- [ ] **Step 5: Run GREEN tests**

Run:

```powershell
.venv\Scripts\python -m pytest tests/test_packaging_docs.py tests/test_chat_runtime.py tests/test_main_api.py -q -k "advance_stage or stage_ack or corrective or checkpoint"
```

- [ ] **Step 6: Commit**

```powershell
git add backend/chat.py skill/SKILL.md tests/test_packaging_docs.py tests/test_chat_runtime.py
git commit -m "docs(stage): require advance_stage workflow"
```

---

## Task 6: Cleanup, Regression Sweep, and Build Prep

**Files:**
- Modify as needed: `docs/current-worklist.md`, `AGENTS.md`, `README.md`
- No production code unless a test failure requires it.

- [ ] **Step 1: Search for forbidden runtime references**

Run:

```powershell
rg -n "stage-ack|StageAck|_STRONG_ADVANCE_KEYWORDS|_ROLLBACK_KEYWORDS|pending_stage_keyword|_detect_stage_keyword" backend tests skill frontend docs/current-worklist.md AGENTS.md
```

Expected:

- `backend/` has no stage-ack execution path.
- `skill/SKILL.md` has no stage-ack instruction.
- Historical docs may mention stage-ack, but current worklist/AGENTS should describe advance_stage.

- [ ] **Step 2: Update current docs**

Update:

1. `docs/current-worklist.md`: mark checkpoint API越级 / stage desync item addressed or move to recently solved.
2. `AGENTS.md`: replace DeepSeek/stage-ack workflow note with advance_stage if there is any current guidance that would mislead future agents.

- [ ] **Step 3: Run focused backend tests**

Run:

```powershell
.venv\Scripts\python -m pytest tests/test_skill_engine.py tests/test_chat_runtime.py tests/test_main_api.py tests/test_packaging_docs.py -q
```

- [ ] **Step 4: Run frontend tests**

Run:

```powershell
cd frontend
node --test tests/
```

- [ ] **Step 5: Run frontend build**

Run:

```powershell
cd frontend
npm run build
```

- [ ] **Step 6: Commit docs/test cleanup**

```powershell
git add docs/current-worklist.md AGENTS.md README.md tests frontend backend skill
git commit -m "chore(stage): clean stage conductor references"
```

If no files changed, skip commit.

---

## Task 7: Package and Visual S0-S7 QA

**Files:**
- Build output: `dist/咨询报告助手/`
- QA report: `docs/superpowers/handoffs/2026-05-19-stage-conductor-packaged-qa.md`

- [ ] **Step 1: Run full practical verification**

Run:

```powershell
.venv\Scripts\python -m pytest tests/test_skill_engine.py tests/test_chat_runtime.py tests/test_main_api.py tests/test_packaging_docs.py tests/test_packaging_spec.py tests/test_build_support.py -q
cd frontend
node --test tests/
npm run build
```

- [ ] **Step 2: Build package**

Run from repo root:

```powershell
build.bat
```

Expected:

- `dist\咨询报告助手\咨询报告助手.exe` exists.
- build exits 0.

- [ ] **Step 3: Launch packaged app**

Start:

```powershell
Start-Process -FilePath "dist\咨询报告助手\咨询报告助手.exe" -WindowStyle Hidden
```

Then use Browser/Computer Use to open `http://127.0.0.1:8080`.

- [ ] **Step 4: Visual S0-S7 flow**

Use a deterministic topic, for example:

```text
主题：猪猪侠大战牛魔王研究报告
目标：生成约 3000 字可审草稿
交付形式：仅报告
```

Drive the UI:

1. Create project.
2. Observe S0.
3. Answer S0 questions or say "跳过访谈，直接开始".
4. Confirm S1 outline.
5. Let S2/S3 collect/analyze.
6. Generate S4 draft.
7. Run quality check.
8. Start review, pass review.
9. Archive S7.

Capture screenshots at:

- S0 initial;
- S1 outline confirmation;
- S2/S3 progress;
- S4 draft;
- S5 review;
- S7/done;
- Markdown table rendered in chat.

- [ ] **Step 5: Write QA report**

Create `docs/superpowers/handoffs/2026-05-19-stage-conductor-packaged-qa.md` with:

```md
# Stage Conductor Packaged QA — 2026-05-19

## Package
- Path:
- Build command:
- Build result:

## Scenario
- Topic:
- Delivery mode:
- Model/channel:

## S0-S7 Results
| Stage | Result | Evidence |
|---|---|---|

## Markdown Rendering
- Result:
- Screenshot:

## Bugs / Issues
- ...

## Verdict
- Pass / Blocked
```

- [ ] **Step 6: Commit QA report if package QA succeeds or produces useful findings**

```powershell
git add docs/superpowers/handoffs/2026-05-19-stage-conductor-packaged-qa.md
git commit -m "test(stage): document packaged stage conductor qa"
```

Do not commit `dist/` unless it is tracked by project convention.
