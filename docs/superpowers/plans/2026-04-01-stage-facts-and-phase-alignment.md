# Stage Facts And Phase Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify project metadata files, formal `plan/` files, eight-stage phase inference, and pre-outline evidence gates so the UI, templates, and backend all follow the same workflow.

**Architecture:** The implementation will establish one formal plan-file registry in the backend, use it to drive template initialization, direct `SkillEngine.write_file()` validation, and ordered `S0 -> S7` stage inference. The frontend workspace file tab will stop defaulting to legacy `project-info.md`, while templates and skill instructions will be updated so the model is nudged toward the same file set and evidence gates that the backend enforces.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, React, existing unittest suite, existing Node test runner.

---

### Task 1: Align Formal Plan Templates

**Files:**
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\skill\plan-template\project-overview.md`
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\skill\plan-template\stage-gates.md`
- Create: `D:\CodexProject\Consult report\consulting-report-agent\skill\plan-template\data-log.md`
- Create: `D:\CodexProject\Consult report\consulting-report-agent\skill\plan-template\analysis-notes.md`
- Create: `D:\CodexProject\Consult report\consulting-report-agent\skill\plan-template\review-checklist.md`
- Create: `D:\CodexProject\Consult report\consulting-report-agent\skill\plan-template\presentation-plan.md`
- Create: `D:\CodexProject\Consult report\consulting-report-agent\skill\plan-template\delivery-log.md`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_skill_engine.py`

- [ ] **Step 1: Write the failing tests**

Add or extend tests that assert:

```python
def test_create_project_initializes_formal_v2_plan_templates():
    expected_files = {
        "project-overview.md",
        "progress.md",
        "stage-gates.md",
        "notes.md",
        "outline.md",
        "research-plan.md",
        "references.md",
        "tasks.md",
        "review.md",
        "data-log.md",
        "analysis-notes.md",
        "review-checklist.md",
        "presentation-plan.md",
        "delivery-log.md",
    }
    assert expected_files.issubset(created_file_names)
    assert "project-info.md" not in created_file_names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `D:\py312-embed\python.exe -m unittest tests.test_skill_engine -v`

Expected: FAIL because `project-info.md` is still initialized and new template files do not exist.

- [ ] **Step 3: Write minimal implementation**

Implement the formal template set and explicitly expand `project-overview.md` so it preserves the useful metadata fields formerly scattered across `project-info.md`, including:

```text
报告类型
报告主题
项目背景
目标读者
预期篇幅
交付时间
特殊要求
交付形式（默认：仅报告）
成功标准
```

Update `stage-gates.md` so every stage points at a real formal file or a real non-plan draft artifact, and so `S6` is clearly marked as conditional on `交付形式 = 报告+演示`.

- [ ] **Step 4: Run test to verify it passes**

Run: `D:\py312-embed\python.exe -m unittest tests.test_skill_engine -v`

Expected: PASS for template initialization and template-alignment assertions.

- [ ] **Step 5: Commit**

```bash
git add skill/plan-template/project-overview.md skill/plan-template/stage-gates.md skill/plan-template/data-log.md skill/plan-template/analysis-notes.md skill/plan-template/review-checklist.md skill/plan-template/presentation-plan.md skill/plan-template/delivery-log.md tests/test_skill_engine.py
git commit -m "feat: align formal plan templates with stage model"
```

### Task 2: Centralize Formal Plan File Registry And Legacy Handling

**Files:**
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\backend\skill.py`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_skill_engine.py`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_chat_context.py`

- [ ] **Step 1: Write the failing tests**

Add tests that lock these behaviors:

```python
def test_build_project_context_excludes_legacy_project_info():
    assert "当前项目概览" in context
    assert "当前项目信息" not in context

def test_create_project_skips_legacy_project_info_template():
    assert not (project_dir / "plan" / "project-info.md").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `D:\py312-embed\python.exe -m unittest tests.test_skill_engine tests.test_chat_context -v`

Expected: FAIL because legacy `project-info.md` is still copied from the template directory or still treated as a first-class file.

- [ ] **Step 3: Write minimal implementation**

In `backend/skill.py`, add one single backend registry that all later tasks reuse:

```python
CORE_CONTEXT_FILES = [
    ("当前项目概览", "plan/project-overview.md"),
    ("当前项目进度", "plan/progress.md"),
    ("阶段门禁", "plan/stage-gates.md"),
    ("项目备注", "plan/notes.md"),
]

FORMAL_PLAN_FILES = {
    "project-overview.md",
    "progress.md",
    "stage-gates.md",
    "notes.md",
    "outline.md",
    "research-plan.md",
    "references.md",
    "tasks.md",
    "review.md",
    "data-log.md",
    "analysis-notes.md",
    "review-checklist.md",
    "presentation-plan.md",
    "delivery-log.md",
}
```

Use this same `FORMAL_PLAN_FILES` registry inside `_initialize_project_structure()` so only formal plan templates are copied for new projects, and later reuse it for:

1. formal plan write validation
2. stage inference
3. legacy file filtering

- [ ] **Step 4: Run test to verify it passes**

Run: `D:\py312-embed\python.exe -m unittest tests.test_skill_engine tests.test_chat_context -v`

Expected: PASS, and no newly created project contains `plan/project-info.md`.

- [ ] **Step 5: Commit**

```bash
git add backend/skill.py tests/test_skill_engine.py tests/test_chat_context.py
git commit -m "refactor: centralize formal plan file registry"
```

### Task 3: Implement Ordered S0-S7 Phase Inference

**Files:**
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\backend\skill.py`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_skill_engine.py`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_workspace_materials.py`

- [ ] **Step 1: Write the failing tests**

Add tests covering the full ordered phase progression:

```python
def test_outline_without_research_plan_keeps_stage_at_s1():
    assert summary["stage_code"] == "S1"

def test_notes_and_references_without_two_sources_keep_stage_at_s1():
    assert summary["stage_code"] == "S1"

def test_outline_and_research_plan_with_evidence_advance_stage_to_s2():
    assert summary["stage_code"] == "S2"

def test_data_log_advances_stage_to_s3():
    assert summary["stage_code"] == "S3"

def test_review_checklist_advances_stage_to_s5():
    assert summary["stage_code"] == "S5"

def test_presentation_stage_is_skipped_for_report_only_projects():
    assert summary["stage_code"] == "S7"
    assert "presentation-plan.md 完成" not in summary["next_actions"]

def test_presentation_stage_is_required_for_report_and_presentation_projects():
    assert summary["stage_code"] == "S6"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `D:\py312-embed\python.exe -m unittest tests.test_skill_engine tests.test_workspace_materials -v`

Expected: FAIL because `_infer_stage_progress()` still only returns `S0`, `S1`, or `S4`.

- [ ] **Step 3: Write minimal implementation**

Replace the coarse stage inference with ordered completion checks:

```python
def _infer_stage_progress(self, project_path: Path) -> tuple[str, list[str]]:
    completed = []
    if self._is_effective_plan_file(project_path, "project-overview.md"):
        completed.append("project-overview.md 创建")
    if self._evidence_gate_satisfied(project_path):
        completed.extend(["notes.md 完成", "references.md 完成"])
    if self._is_effective_plan_file(project_path, "outline.md"):
        completed.append("outline.md 完成")
    if self._is_effective_plan_file(project_path, "research-plan.md"):
        completed.append("research-plan.md 完成")
    ...
```

Introduce helpers for:

- effective-content detection against template baselines
- references/source-count validation against the spec's minimum evidence threshold
- delivery-mode parsing from `project-overview.md`
- optional `S6` skip behavior for `仅报告`
- stage-gates checkbox/backfill generation so `stage_code`, `completed_items`, `next_actions`, and file contents all stay aligned

- [ ] **Step 4: Run test to verify it passes**

Run: `D:\py312-embed\python.exe -m unittest tests.test_skill_engine tests.test_workspace_materials -v`

Expected: PASS for ordered `S0 -> S7` inference and `S6` optional behavior.

- [ ] **Step 5: Commit**

```bash
git add backend/skill.py tests/test_skill_engine.py tests/test_workspace_materials.py
git commit -m "feat: implement ordered stage inference"
```

### Task 4: Add Pre-Outline Evidence Gate And Formal Plan Whitelist

**Files:**
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\backend\skill.py`
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\backend\chat.py`
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\skill\SKILL.md`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_chat_runtime.py`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_skill_engine.py`

- [ ] **Step 1: Write the failing tests**

Add tests that pin both hard back-end gates:

```python
def test_skill_engine_write_file_rejects_unregistered_plan_file():
    with self.assertRaises(ValueError):
        engine.write_file(project_id, "plan/gate-control.md", "...")

def test_handler_write_file_rejects_unregistered_plan_file():
    result = handler._execute_tool(project_id, tool_call_for("plan/gate-control.md"))
    assert result["status"] == "error"

def test_outline_write_requires_evidence_notes_and_references():
    result = handler._execute_tool(project_id, tool_call_for("plan/outline.md"))
    assert result["status"] == "error"
    assert "notes.md" in result["message"]
    assert "references.md" in result["message"]
    assert "至少 2 个依据" in result["message"]

def test_research_plan_write_requires_evidence_notes_and_references():
    result = handler._execute_tool(project_id, tool_call_for("plan/research-plan.md"))
    assert result["status"] == "error"
    assert "notes.md" in result["message"]
    assert "references.md" in result["message"]
    assert "至少 2 个依据" in result["message"]
```

Also add a positive case:

```python
def test_outline_write_allowed_after_evidence_gate_is_satisfied():
    assert result["status"] == "success"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `D:\py312-embed\python.exe -m unittest tests.test_chat_runtime tests.test_skill_engine -v`

Expected: FAIL because any `plan/*.md` file can still be written and `outline.md` does not require pre-outline evidence.

- [ ] **Step 3: Write minimal implementation**

Move the authoritative file gate into `backend/skill.py`, not just the chat layer:

```python
def is_formal_plan_file(self, file_path: str) -> bool: ...
def evidence_gate_satisfied(self, project_ref: str) -> bool: ...
def validate_plan_write(self, project_ref: str, file_path: str) -> None: ...
```

`validate_plan_write()` should:

1. reject unregistered `plan/*.md` targets
2. reject `outline.md` / `research-plan.md` before the evidence threshold is satisfied
3. produce a message that mentions `notes.md`, `references.md`, and the minimum `2`-source rule

Then make both `SkillEngine.write_file()` and `backend/chat.py` use it:

```python
self.skill_engine.validate_plan_write(project_id, normalized)
```

Update `skill/SKILL.md` so the model is explicitly instructed to:

1. inspect materials or fetch web sources first
2. write `notes.md` and `references.md`
3. only then draft `outline.md` / `research-plan.md`
4. never invent unofficial `plan/*.md` files

- [ ] **Step 4: Run test to verify it passes**

Run: `D:\py312-embed\python.exe -m unittest tests.test_chat_runtime tests.test_skill_engine -v`

Expected: PASS for both whitelist rejection and evidence-gate enforcement.

- [ ] **Step 5: Commit**

```bash
git add backend/chat.py backend/skill.py skill/SKILL.md tests/test_chat_runtime.py tests/test_skill_engine.py
git commit -m "feat: gate outline writes on evidence collection"
```

### Task 5: Fix Preview Default File And Legacy File Presentation

**Files:**
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\frontend\src\components\WorkspacePanel.jsx`
- Create: `D:\CodexProject\Consult report\consulting-report-agent\frontend\src\utils\workspaceFiles.js`
- Create: `D:\CodexProject\Consult report\consulting-report-agent\frontend\tests\workspaceFiles.test.mjs`

- [ ] **Step 1: Write the failing test**

Create a small pure helper test for the live workspace file tab, not the unused legacy preview surface:

```javascript
test("getDefaultPreviewFile prefers project-overview over legacy project-info", () => {
  assert.equal(
    getDefaultPreviewFile([
      "plan/project-info.md",
      "plan/project-overview.md",
      "plan/stage-gates.md",
    ]),
    "plan/project-overview.md",
  );
});
```

Also add:

```javascript
test("orderPreviewFiles pushes legacy project-info to the end", () => {
  assert.deepEqual(orderPreviewFiles([...]), [...expected]);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test .\\tests\\workspaceFiles.test.mjs`

Expected: FAIL because the helper does not exist and `WorkspacePanel.jsx` still implements its own inline file-default logic.

- [ ] **Step 3: Write minimal implementation**

Extract file-selection logic:

```javascript
export function getDefaultPreviewFile(paths = []) {
  if (paths.includes("plan/project-overview.md")) return "plan/project-overview.md";
  if (paths.includes("plan/project-info.md")) return "plan/project-info.md";
  return paths[0] || "";
}
```

Also add ordering/filtering helpers so legacy `project-info.md` can be pushed to the end or omitted for new projects.

Then update `WorkspacePanel.jsx` to use the helper instead of its current inline default-selection logic.

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test .\\tests\\workspaceFiles.test.mjs`

Expected: PASS, and the live workspace file tab will default to `project-overview.md`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/WorkspacePanel.jsx frontend/src/utils/workspaceFiles.js frontend/tests/workspaceFiles.test.mjs
git commit -m "fix: default workspace file tab to project overview"
```

### Task 6: Full Regression Pass And Packaging Sanity Check

**Files:**
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\docs\current-worklist.md`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_chat_runtime.py`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_chat_context.py`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_skill_engine.py`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_workspace_materials.py`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_main_api.py`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_stream_api.py`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\frontend\tests\workspaceFiles.test.mjs`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\frontend\tests\workspaceSummary.test.mjs`

- [ ] **Step 1: Update the worklist entry**

Record that:

```markdown
- `project-info.md` retired from formal workflow
- `gate-control.md` blocked by backend whitelist
- pre-outline evidence threshold requires valid `notes.md + references.md`
- ordered `S0 -> S7` inference implemented
- `S6` optional for report-only projects
- workspace file tab defaults to `project-overview.md`
```

- [ ] **Step 2: Run the backend verification suite**

Run:

```bash
D:\py312-embed\python.exe -m unittest tests.test_skill_engine tests.test_workspace_materials tests.test_chat_runtime tests.test_main_api tests.test_stream_api -v
```

Expected: PASS across stage inference, write gates, chat flow, and API surface.

Expanded final command:

```bash
D:\py312-embed\python.exe -m unittest tests.test_skill_engine tests.test_workspace_materials tests.test_chat_runtime tests.test_chat_context tests.test_main_api tests.test_stream_api -v
```

- [ ] **Step 3: Run the frontend verification suite**

Run:

```bash
node --test .\tests\chatPresentation.test.mjs .\tests\connectionMode.test.mjs .\tests\contextUsage.test.mjs .\tests\projectSelection.test.mjs .\tests\workspaceSummary.test.mjs .\tests\workspaceFiles.test.mjs
```

Expected: PASS, including the new preview-default assertions and the `/workspace -> stage summary` UI mapping.

- [ ] **Step 4: Run a packaging sanity check**

Run:

```bash
npm run build
D:\py312-embed\python.exe -m PyInstaller consulting_report.spec --noconfirm
```

Expected: successful frontend build and PyInstaller output in `dist/`.

- [ ] **Step 5: Commit**

```bash
git add docs/current-worklist.md
git commit -m "docs: record aligned stage workflow rollout"
```
