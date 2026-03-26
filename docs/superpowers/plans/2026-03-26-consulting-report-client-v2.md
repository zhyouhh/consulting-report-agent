# Consulting Report Client V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Windows-first V2 consulting report desktop client with a managed default channel, optional custom API mode, bundled v1.2 skill assets, richer project workspace state, quality checks, and reviewable draft export.

**Architecture:** Keep the existing `FastAPI + React + PyWebView + PyInstaller` desktop shape, but separate concerns cleanly: backend owns settings, project state, and script-backed actions; frontend owns connection-mode UI and the workspace shell; bundled skill assets become the single source of runtime prompts/templates/scripts. The managed proxy itself is an external prerequisite exposed as an OpenAI-compatible endpoint; this repo only documents and integrates against that contract.

**Tech Stack:** Python 3.11+, FastAPI, React 18, axios/fetch, PyWebView, PyInstaller, Python `unittest`, Node 20 built-in test runner

---

## Scope Note

This plan intentionally covers:

1. Desktop client changes in this repository.
2. A small in-repo contract document for the managed proxy endpoint.

This plan intentionally does **not** cover implementing the managed proxy service itself. That service lives outside this repo and should be deployed/configured on your existing server before final end-to-end verification.

Deployment constraint for that external service:

1. Reuse the existing host that already serves `newapi` rather than introducing a new machine.
2. Keep the proxy thin: no database, no queue, no heavyweight background workers.
3. Resource use must stay modest; do not design anything that needs large resident memory, disk staging, or persistent caches.

## File Structure

### Existing files to modify

- `backend/config.py`
  Responsibility: persist managed/custom connection settings and skill bundle location.
- `backend/models.py`
  Responsibility: validate richer project creation payloads and API settings payloads.
- `backend/skill.py`
  Responsibility: create projects from the bundled v1.2 templates, expose workspace metadata, and load richer context files.
- `backend/chat.py`
  Responsibility: honor the new settings model and richer system prompt context.
- `backend/main.py`
  Responsibility: expose settings, project, workspace, quality-check, and export endpoints.
- `skill/SKILL.md`
  Responsibility: provide the bundled v1.2 system prompt and workflow rules.
- `skill/plan-template/project-overview.md`
  Responsibility: seed the V2 project overview template.
- `skill/plan-template/progress.md`
  Responsibility: seed the V2 progress template.
- `skill/plan-template/notes.md`
  Responsibility: seed the V2 notes template.
- `skill/plan-template/stage-gates.md`
  Responsibility: seed the V2 stage gate template.
- `skill/scripts/quality_check.ps1`
  Responsibility: provide the bundled Windows quality-check script.
- `skill/scripts/export_draft.ps1`
  Responsibility: provide the bundled Windows reviewable-draft export script.
- `frontend/src/App.jsx`
  Responsibility: own top-level workspace state and route data into sidebar/chat/workspace panels.
- `frontend/src/components/SettingsModal.jsx`
  Responsibility: render managed/custom connection modes and save the expanded settings payload.
- `frontend/src/components/Sidebar.jsx`
  Responsibility: list projects and open the extracted project-creation modal.
- `frontend/src/components/ChatPanel.jsx`
  Responsibility: show current project stage and current connection mode in the chat header.
- `consulting_report.spec`
  Responsibility: bundle the new runtime skill assets and any helper scripts/libs needed at runtime.
- `BUILD.md`
  Responsibility: explain the updated Windows packaging and first-run behavior.
- `WINDOWS_BUILD.md`
  Responsibility: explain updated packaging, distribution, and managed/default channel behavior.
- `README.md`
  Responsibility: keep product claims aligned with actual V2 behavior.

### New files to create

- `backend/report_tools.py`
  Responsibility: wrap skill quality-check/export scripts behind testable Python functions.
- `tests/test_config.py`
  Responsibility: cover managed/custom settings persistence and defaults.
- `tests/test_skill_engine.py`
  Responsibility: cover project creation, bundled template copy, and workspace metadata extraction.
- `tests/test_report_tools.py`
  Responsibility: cover script invocation wrappers with mocks and failure handling.
- `tests/test_main_api.py`
  Responsibility: cover settings, workspace summary, quality-check, and export endpoints using FastAPI `TestClient`.
- `docs/default-managed-proxy-contract.md`
  Responsibility: pin the OpenAI-compatible managed proxy contract this client expects.
- `skill/modules/writing-core.md`
  Responsibility: bundled V2 general writing rules.
- `skill/modules/common-gotchas.md`
  Responsibility: bundled V2 anti-AI and writing pitfall rules.
- `skill/modules/quality-review.md`
  Responsibility: bundled V2 quality review rules.
- `skill/modules/final-delivery.md`
  Responsibility: bundled V2 draft export rules.
- `skill/modules/consulting-lifecycle.md`
  Responsibility: bundled V2 consulting lifecycle rules.
- `skill/modules/strategy-consulting.md`
  Responsibility: bundled V2 strategy consulting rules.
- `skill/modules/market-research.md`
  Responsibility: bundled V2 market research rules.
- `skill/modules/specialized-research.md`
  Responsibility: bundled V2 specialized research rules.
- `skill/modules/management-system.md`
  Responsibility: bundled V2 management-system rules.
- `skill/modules/implementation-plan.md`
  Responsibility: bundled V2 implementation-plan rules.
- `skill/modules/due-diligence.md`
  Responsibility: bundled V2 due-diligence rules.
- `skill/modules/business-charts.md`
  Responsibility: bundled V2 business chart rules.
- `skill/modules/framework-diagrams.md`
  Responsibility: bundled V2 framework diagram rules.
- `skill/modules/data-analysis.md`
  Responsibility: bundled V2 data analysis rules.
- `skill/modules/recommendation-framework.md`
  Responsibility: bundled V2 recommendation framework rules.
- `skill/modules/templates-collection.md`
  Responsibility: bundled V2 template collection.
- `skill/lib/__init__.py`
  Responsibility: bundled helper package marker.
- `skill/lib/chart_utils.py`
  Responsibility: bundled helper utilities required by selected scripts.
- `frontend/src/components/ProjectCreateModal.jsx`
  Responsibility: collect richer project metadata without bloating `Sidebar.jsx`.
- `frontend/src/components/WorkspacePanel.jsx`
  Responsibility: host the stage/file tabs and action buttons.
- `frontend/src/components/StagePanel.jsx`
  Responsibility: render current stage, checklist summary, next actions, and action buttons.
- `frontend/src/components/FilePreviewPanel.jsx`
  Responsibility: keep file navigation and Markdown rendering focused.
- `frontend/src/utils/connectionMode.js`
  Responsibility: compute user-facing connection labels and help text as pure functions.
- `frontend/src/utils/workspaceSummary.js`
  Responsibility: normalize backend workspace metadata into UI-friendly labels.
- `frontend/tests/connectionMode.test.mjs`
  Responsibility: verify connection-mode helpers with Node’s built-in test runner.
- `frontend/tests/workspaceSummary.test.mjs`
  Responsibility: verify workspace summary helpers with Node’s built-in test runner.

## Task 1: Lock The Managed/Custom Settings Contract

**Files:**
- Create: `docs/default-managed-proxy-contract.md`
- Create: `tests/test_config.py`
- Modify: `backend/config.py`
- Modify: `backend/models.py`

- [ ] **Step 1: Write the failing settings persistence tests**

```python
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.config import Settings, load_settings, save_settings


class SettingsPersistenceTests(unittest.TestCase):
    def test_default_settings_use_managed_mode(self):
        settings = Settings()
        self.assertEqual(settings.mode, "managed")
        self.assertEqual(settings.managed_model, "gemini-3-flash")
        self.assertTrue(settings.managed_base_url)

    def test_save_and_load_round_trip_custom_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            settings = Settings(
                mode="custom",
                managed_base_url="https://managed.example/v1",
                managed_model="gemini-3-flash",
                custom_api_base="https://custom.example/v1",
                custom_api_key="secret",
                custom_model="gpt-4.1-mini",
            )
            with mock.patch("backend.config.get_user_config_dir", return_value=config_dir):
                save_settings(settings)
                loaded = load_settings()
        self.assertEqual(loaded.mode, "custom")
        self.assertEqual(loaded.custom_api_base, "https://custom.example/v1")
        self.assertEqual(loaded.custom_model, "gpt-4.1-mini")
```

- [ ] **Step 2: Run the config tests to verify they fail**

Run: `python -m unittest tests.test_config -v`

Expected: FAIL with missing `mode` / `managed_base_url` / `managed_model` fields on `Settings`.

- [ ] **Step 3: Expand the persisted settings model**

Implement the minimal schema update in `backend/config.py`:

```python
class Settings(BaseSettings):
    mode: str = "managed"

    managed_base_url: str = "https://newapi.z0y0h.work/client/v1"
    managed_model: str = "gemini-3-flash"

    custom_api_key: str = ""
    custom_api_base: str = ""
    custom_model: str = ""

    # Backward-compatible aliases used by existing chat code during migration
    api_key: str = ""
    api_base: str = ""
    model: str = ""
```

And add a normalization helper so old config files still load:

```python
def normalize_settings_payload(data: dict) -> dict:
    if "mode" not in data:
        data["mode"] = "custom" if data.get("api_key") else "managed"
    data.setdefault("managed_base_url", "https://newapi.z0y0h.work/client/v1")
    data.setdefault("managed_model", "gemini-3-flash")
    data.setdefault("custom_api_base", data.get("api_base", ""))
    data.setdefault("custom_api_key", data.get("api_key", ""))
    data.setdefault("custom_model", data.get("model", ""))
    return data
```

- [ ] **Step 4: Update request/response models for the richer settings and project payload**

Add the new settings payload model in `backend/models.py`:

```python
class SettingsUpdate(BaseModel):
    mode: Literal["managed", "custom"]
    managed_base_url: str
    managed_model: str
    custom_api_base: str = ""
    custom_api_key: str = ""
    custom_model: str = ""
```

And extend `ProjectInfo` to match the V2 project-creation modal:

```python
class ProjectInfo(BaseModel):
    name: str = Field(...)
    project_type: str = Field(...)
    target_audience: str = Field(...)
    deadline: str = Field(...)
    expected_length: str = Field(...)
    theme: str = Field(...)
    notes: str = ""
```

- [ ] **Step 5: Write the managed proxy contract document**

Create `docs/default-managed-proxy-contract.md` with a concrete, short contract:

```markdown
# Default Managed Proxy Contract

Base URL: `https://newapi.z0y0h.work/client/v1`

Required endpoints:
- `POST /chat/completions`
- `GET /models` (optional but recommended)

Behavior:
- force upstream model to `gemini-3-flash`
- reject non-whitelisted models
- return OpenAI-compatible JSON
- support bearer auth owned by the proxy, not by the client
```

- [ ] **Step 6: Run the config tests again**

Run: `python -m unittest tests.test_config -v`

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/config.py backend/models.py tests/test_config.py docs/default-managed-proxy-contract.md
git commit -m "feat: add managed and custom connection settings"
```

## Task 2: Bundle The V1.2 Skill Runtime And Project Metadata

**Files:**
- Create: `tests/test_skill_engine.py`
- Create: `skill/modules/writing-core.md`
- Create: `skill/modules/common-gotchas.md`
- Create: `skill/modules/quality-review.md`
- Create: `skill/modules/final-delivery.md`
- Create: `skill/modules/consulting-lifecycle.md`
- Create: `skill/modules/strategy-consulting.md`
- Create: `skill/modules/market-research.md`
- Create: `skill/modules/specialized-research.md`
- Create: `skill/modules/management-system.md`
- Create: `skill/modules/implementation-plan.md`
- Create: `skill/modules/due-diligence.md`
- Create: `skill/modules/business-charts.md`
- Create: `skill/modules/framework-diagrams.md`
- Create: `skill/modules/data-analysis.md`
- Create: `skill/modules/recommendation-framework.md`
- Create: `skill/modules/templates-collection.md`
- Create: `skill/plan-template/project-overview.md`
- Create: `skill/plan-template/progress.md`
- Create: `skill/plan-template/notes.md`
- Create: `skill/plan-template/stage-gates.md`
- Create: `skill/scripts/quality_check.ps1`
- Create: `skill/scripts/export_draft.ps1`
- Create: `skill/lib/__init__.py`
- Create: `skill/lib/chart_utils.py`
- Modify: `skill/SKILL.md`
- Modify: `backend/skill.py`
- Modify: `backend/chat.py`

- [ ] **Step 1: Write the failing project template and workspace-summary tests**

```python
import tempfile
import unittest
from pathlib import Path

from backend.skill import SkillEngine


class SkillEngineTests(unittest.TestCase):
    def test_create_project_copies_v12_templates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            skill_dir = Path(tmpdir) / "skill"
            (skill_dir / "plan-template").mkdir(parents=True)
            (skill_dir / "plan-template" / "project-overview.md").write_text("# 项目概览\n", encoding="utf-8")
            (skill_dir / "plan-template" / "progress.md").write_text("# 进度\n", encoding="utf-8")
            engine = SkillEngine(projects_dir, skill_dir)
            engine.create_project("demo", "strategy-consulting", "主题", "高层决策者", "2026-04-01", "3000字", "备注")
            self.assertTrue((projects_dir / "demo" / "plan" / "project-overview.md").exists())
            self.assertTrue((projects_dir / "demo" / "plan" / "progress.md").exists())

    def test_workspace_summary_reads_stage_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "projects" / "demo" / "plan"
            project_dir.mkdir(parents=True)
            (project_dir / "progress.md").write_text("**阶段**: S4\n**状态**: 进行中\n", encoding="utf-8")
            (project_dir / "stage-gates.md").write_text("### S4 报告撰写 ⬜\n- [x] 报告结构确定\n", encoding="utf-8")
            engine = SkillEngine(Path(tmpdir) / "projects", Path(tmpdir) / "skill")
            summary = engine.get_workspace_summary("demo")
            self.assertEqual(summary["stage_code"], "S4")
            self.assertEqual(summary["status"], "进行中")
```

- [ ] **Step 2: Run the skill engine tests to verify they fail**

Run: `python -m unittest tests.test_skill_engine -v`

Expected: FAIL because `create_project` still expects the old signature and `get_workspace_summary` does not exist.

- [ ] **Step 3: Extend project creation to use the bundled v1.2 templates**

Update `backend/skill.py` so `create_project` accepts the richer payload:

```python
def create_project(
    self,
    name: str,
    project_type: str,
    theme: str,
    target_audience: str,
    deadline: str,
    expected_length: str,
    notes: str = "",
) -> Path:
    ...
```

When writing the initial project files, populate the new template files instead of the legacy `project-info.md` placeholders:

```python
overview = self._fill_template(
    project_path / "plan" / "project-overview.md",
    {
        "[填写项目名称]": name,
        "[战略咨询/市场研究/尽职调查/运营优化]": project_type,
        "[YYYY-MM-DD]": deadline,
    },
)
```

Before wiring Python code to those files, copy the runtime asset subset from the sibling repo into this repo:

```powershell
New-Item -ItemType Directory -Force -Path skill\modules, skill\plan-template, skill\scripts, skill\lib | Out-Null
Copy-Item ..\consulting-report-skill\SKILL.md skill\SKILL.md -Force
Copy-Item ..\consulting-report-skill\plan-template\project-overview.md skill\plan-template\project-overview.md -Force
Copy-Item ..\consulting-report-skill\plan-template\progress.md skill\plan-template\progress.md -Force
Copy-Item ..\consulting-report-skill\plan-template\notes.md skill\plan-template\notes.md -Force
Copy-Item ..\consulting-report-skill\plan-template\stage-gates.md skill\plan-template\stage-gates.md -Force
Copy-Item ..\consulting-report-skill\scripts\quality_check.ps1 skill\scripts\quality_check.ps1 -Force
Copy-Item ..\consulting-report-skill\scripts\export_draft.ps1 skill\scripts\export_draft.ps1 -Force
Copy-Item ..\consulting-report-skill\modules\*.md skill\modules\ -Force
Copy-Item ..\consulting-report-skill\lib\__init__.py skill\lib\__init__.py -Force
Copy-Item ..\consulting-report-skill\lib\chart_utils.py skill\lib\chart_utils.py -Force
```

- [ ] **Step 4: Add workspace summary helpers to `SkillEngine`**

Implement a small summary API in `backend/skill.py`:

```python
def get_workspace_summary(self, project_name: str) -> dict:
    progress_text = self.read_file(project_name, "plan/progress.md")
    stage_gates_text = self.read_file(project_name, "plan/stage-gates.md")
    return {
        "stage_code": self._extract_stage_code(progress_text),
        "status": self._extract_stage_status(progress_text),
        "completed_items": self._extract_checked_items(stage_gates_text),
        "next_actions": self._extract_open_items(stage_gates_text)[:3],
    }
```

- [ ] **Step 5: Expand system prompt context loading**

Update `backend/chat.py` to load the richer context file set:

```python
context_files = [
    ("当前项目概览", "plan/project-overview.md"),
    ("当前项目进度", "plan/progress.md"),
    ("阶段门禁", "plan/stage-gates.md"),
    ("项目备注", "plan/notes.md"),
]
```

Loop through the list and append any existing file content into the system prompt before first chat turn.

- [ ] **Step 6: Run the skill engine tests again**

Run: `python -m unittest tests.test_skill_engine -v`

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/skill.py backend/chat.py tests/test_skill_engine.py
git commit -m "feat: bundle v1.2 skill templates and workspace metadata"
```

## Task 3: Expose Workspace, Quality Check, And Draft Export APIs

**Files:**
- Create: `backend/report_tools.py`
- Create: `tests/test_report_tools.py`
- Create: `tests/test_main_api.py`
- Modify: `backend/main.py`

- [ ] **Step 1: Write failing tests for script wrappers**

```python
import unittest
from unittest import mock

from backend.report_tools import run_quality_check, export_reviewable_draft


class ReportToolsTests(unittest.TestCase):
    @mock.patch("subprocess.run")
    def test_run_quality_check_returns_stdout(self, mock_run):
        mock_run.return_value = mock.Mock(returncode=0, stdout="高风险: 0", stderr="")
        result = run_quality_check("D:/tmp/report.md", "D:/skill/scripts/quality_check.ps1")
        self.assertEqual(result["status"], "ok")
        self.assertIn("高风险: 0", result["output"])

    @mock.patch("subprocess.run")
    def test_export_reviewable_draft_returns_output_path(self, mock_run):
        mock_run.return_value = mock.Mock(returncode=0, stdout="已生成可审草稿: D:/tmp/output/report.docx", stderr="")
        result = export_reviewable_draft("D:/tmp/report.md", "D:/tmp/output", "D:/skill/scripts/export_draft.ps1")
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["output_path"].endswith(".docx"))
```

- [ ] **Step 2: Write failing API tests for the new endpoints**

```python
from fastapi.testclient import TestClient
from backend.main import app


def test_workspace_endpoint_returns_stage_summary():
    client = TestClient(app)
    response = client.get("/api/projects/demo/workspace")
    assert response.status_code == 200
    assert "stage_code" in response.json()


def test_quality_check_endpoint_returns_bucketed_output():
    client = TestClient(app)
    response = client.post("/api/projects/demo/quality-check")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
```

- [ ] **Step 3: Run the wrapper and API tests to verify they fail**

Run: `python -m unittest tests.test_report_tools tests.test_main_api -v`

Expected: FAIL because the wrapper module and endpoints do not exist yet.

- [ ] **Step 4: Implement the report tool wrappers**

Create `backend/report_tools.py` with thin wrappers:

```python
def run_quality_check(file_path: str, script_path: str) -> dict:
    result = subprocess.run(
        ["powershell", "-ExecutionPolicy", "Bypass", "-File", script_path, "-FilePath", file_path],
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "status": "ok" if result.returncode == 0 else "error",
        "output": result.stdout or result.stderr,
    }
```

```python
def export_reviewable_draft(file_path: str, output_dir: str, script_path: str) -> dict:
    result = subprocess.run(
        ["powershell", "-ExecutionPolicy", "Bypass", "-File", script_path, "-InputPath", file_path, "-OutputDir", output_dir],
        capture_output=True,
        text=True,
        check=False,
    )
    output_path = _extract_output_path(result.stdout)
    return {
        "status": "ok" if result.returncode == 0 else "error",
        "output": result.stdout or result.stderr,
        "output_path": output_path,
    }
```

- [ ] **Step 5: Add workspace, quality-check, and export endpoints**

Extend `backend/main.py` with:

```python
@app.get("/api/projects/{project_name}/workspace")
async def get_workspace(project_name: str):
    return skill_engine.get_workspace_summary(project_name)


@app.post("/api/projects/{project_name}/quality-check")
async def quality_check(project_name: str):
    report_path = skill_engine.get_primary_report_path(project_name)
    return run_quality_check(str(report_path), str(skill_engine.get_script_path("quality_check.ps1")))


@app.post("/api/projects/{project_name}/export-draft")
async def export_draft(project_name: str):
    report_path = skill_engine.get_primary_report_path(project_name)
    output_dir = skill_engine.ensure_output_dir(project_name)
    return export_reviewable_draft(str(report_path), str(output_dir), str(skill_engine.get_script_path("export_draft.ps1")))
```

- [ ] **Step 6: Run the wrapper and API tests again**

Run: `python -m unittest tests.test_report_tools tests.test_main_api -v`

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/report_tools.py backend/main.py tests/test_report_tools.py tests/test_main_api.py
git commit -m "feat: add workspace, quality check, and draft export APIs"
```

## Task 4: Build The Dual-Mode Settings UI

**Files:**
- Create: `frontend/src/utils/connectionMode.js`
- Create: `frontend/tests/connectionMode.test.mjs`
- Modify: `frontend/src/components/SettingsModal.jsx`
- Modify: `frontend/src/App.jsx`

- [ ] **Step 1: Write failing pure helper tests for connection mode labels**

```javascript
import test from "node:test";
import assert from "node:assert/strict";
import { describeConnectionMode } from "../src/utils/connectionMode.js";

test("describeConnectionMode returns managed label", () => {
  assert.deepEqual(describeConnectionMode({
    mode: "managed",
    managed_model: "gemini-3-flash",
  }), {
    title: "默认通道",
    subtitle: "推荐，开箱即用 · gemini-3-flash",
  });
});
```

- [ ] **Step 2: Run the helper tests to verify they fail**

Run: `node --test frontend/tests/connectionMode.test.mjs`

Expected: FAIL because `connectionMode.js` does not exist.

- [ ] **Step 3: Implement the connection-mode helper**

Create `frontend/src/utils/connectionMode.js`:

```javascript
export function describeConnectionMode(settings) {
  if (settings.mode === "managed") {
    return {
      title: "默认通道",
      subtitle: `推荐，开箱即用 · ${settings.managed_model}`,
    };
  }
  return {
    title: "自定义 API",
    subtitle: settings.custom_model || "高级配置，自行承担可用性",
  };
}
```

- [ ] **Step 4: Refactor `SettingsModal.jsx` to support both modes**

Reshape the form state:

```javascript
const [form, setForm] = useState({
  mode: "managed",
  managed_base_url: "",
  managed_model: "gemini-3-flash",
  custom_api_base: "",
  custom_api_key: "",
  custom_model: "",
});
```

UI requirements:

1. Two side-by-side mode cards or radio buttons.
2. Managed mode shows the prefilled base URL and model as read-only by default.
3. Custom mode reuses the existing model-list fetch flow.
4. Saving sends the full settings payload to `/api/settings`.

- [ ] **Step 5: Lift settings summary into `App.jsx`**

Add a top-level settings load so child components can show the active connection:

```javascript
const [settings, setSettings] = useState(null);

const loadSettings = async () => {
  const res = await axios.get("/api/settings");
  setSettings(res.data);
};
```

Pass `settings` and `onSettingsSaved={loadSettings}` into child components that need the current connection status.

- [ ] **Step 6: Run the helper tests and frontend build**

Run: `node --test frontend/tests/connectionMode.test.mjs`

Expected: PASS

Run: `cd frontend && npm run build`

Expected: build completes without JSX or type syntax errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/utils/connectionMode.js frontend/tests/connectionMode.test.mjs frontend/src/components/SettingsModal.jsx frontend/src/App.jsx
git commit -m "feat: add managed and custom connection mode UI"
```

## Task 5: Replace The Thin Preview With A Real Workspace Panel

**Files:**
- Create: `frontend/src/components/ProjectCreateModal.jsx`
- Create: `frontend/src/components/WorkspacePanel.jsx`
- Create: `frontend/src/components/StagePanel.jsx`
- Create: `frontend/src/components/FilePreviewPanel.jsx`
- Create: `frontend/src/utils/workspaceSummary.js`
- Create: `frontend/tests/workspaceSummary.test.mjs`
- Modify: `frontend/src/components/Sidebar.jsx`
- Modify: `frontend/src/components/ChatPanel.jsx`
- Modify: `frontend/src/App.jsx`

- [ ] **Step 1: Write failing workspace summary helper tests**

```javascript
import test from "node:test";
import assert from "node:assert/strict";
import { summarizeWorkspace } from "../src/utils/workspaceSummary.js";

test("summarizeWorkspace falls back safely when stage data is missing", () => {
  const summary = summarizeWorkspace({});
  assert.equal(summary.stageLabel, "未开始");
  assert.deepEqual(summary.nextActions, []);
});
```

- [ ] **Step 2: Run the helper tests to verify they fail**

Run: `node --test frontend/tests/workspaceSummary.test.mjs`

Expected: FAIL because `workspaceSummary.js` does not exist.

- [ ] **Step 3: Extract project creation into its own modal**

Move the form out of `Sidebar.jsx` and expand it for V2 fields:

```javascript
const initialForm = {
  name: "",
  project_type: "strategy-consulting",
  theme: "",
  target_audience: "高层决策者",
  deadline: "",
  expected_length: "",
  notes: "",
};
```

`Sidebar.jsx` should only own list rendering and modal open/close state after this split.

- [ ] **Step 4: Build the workspace helper and stage/file components**

Create `frontend/src/utils/workspaceSummary.js`:

```javascript
export function summarizeWorkspace(apiSummary = {}) {
  return {
    stageLabel: apiSummary.stage_code || "未开始",
    statusLabel: apiSummary.status || "待开始",
    completedItems: apiSummary.completed_items || [],
    nextActions: apiSummary.next_actions || [],
  };
}
```

Then create:

1. `StagePanel.jsx` for stage/status/checklist/action buttons
2. `FilePreviewPanel.jsx` for file list + Markdown preview
3. `WorkspacePanel.jsx` to fetch `/api/projects/:name/workspace` and switch between tabs

- [ ] **Step 5: Update `ChatPanel.jsx` to show current stage and connection**

Change the header from a bare project name to a small status row:

```javascript
<div>
  <h2>{project || "请选择或创建项目"}</h2>
  {project && (
    <p className="text-xs text-[#8888a8]">
      {connectionLabel} · 当前阶段 {workspace.stageLabel}
    </p>
  )}
</div>
```

- [ ] **Step 6: Replace `PreviewPanel` usage in `App.jsx`**

Swap the old panel for the new workspace shell:

```javascript
{showPreview && (
  <WorkspacePanel
    project={currentProject}
    settings={settings}
    refreshToken={workspaceRefreshToken}
  />
)}
```

Use a refresh token or callback so sending a message, running quality check, or exporting a draft can trigger workspace refresh cleanly.

- [ ] **Step 7: Run the helper tests and frontend build**

Run: `node --test frontend/tests/workspaceSummary.test.mjs`

Expected: PASS

Run: `cd frontend && npm run build`

Expected: build completes and produces `frontend/dist`.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/ProjectCreateModal.jsx frontend/src/components/WorkspacePanel.jsx frontend/src/components/StagePanel.jsx frontend/src/components/FilePreviewPanel.jsx frontend/src/utils/workspaceSummary.js frontend/tests/workspaceSummary.test.mjs frontend/src/components/Sidebar.jsx frontend/src/components/ChatPanel.jsx frontend/src/App.jsx
git commit -m "feat: add project workspace and richer project creation flow"
```

## Task 6: Wire UI Actions To The New Backend Endpoints

**Files:**
- Modify: `frontend/src/components/WorkspacePanel.jsx`
- Modify: `frontend/src/components/StagePanel.jsx`
- Modify: `frontend/src/components/FilePreviewPanel.jsx`
- Modify: `frontend/src/components/ChatPanel.jsx`

- [ ] **Step 1: Add quality-check and export button handlers**

In `WorkspacePanel.jsx`, add action handlers:

```javascript
const runQualityCheck = async () => {
  const res = await axios.post(`/api/projects/${encodeURIComponent(project)}/quality-check`);
  setQualityResult(res.data);
};

const exportDraft = async () => {
  const res = await axios.post(`/api/projects/${encodeURIComponent(project)}/export-draft`);
  showSuccess(`已导出可审草稿：${res.data.output_path}`);
};
```

- [ ] **Step 2: Render the quality-check result as structured output**

In `StagePanel.jsx`, do not dump raw script output as an unreadable blob. Parse it into grouped sections where possible and at minimum preserve:

1. 高风险
2. 中风险
3. 低风险
4. 原始输出折叠区

- [ ] **Step 3: Refresh file/workspace state after actions**

After a successful quality check or export:

```javascript
await Promise.all([loadWorkspace(), loadFiles()]);
```

After a successful chat completion, call a parent `onProjectMutated()` callback from `ChatPanel.jsx` so the workspace can refresh stage/file state.

- [ ] **Step 4: Run the frontend build**

Run: `cd frontend && npm run build`

Expected: build completes without unresolved imports or props mismatches.

- [ ] **Step 5: Manual smoke test the desktop flow**

Run:

```bash
python app.py
```

Expected manual results:

1. 应用正常打开桌面窗口
2. 设置中可切换“默认通道 / 自定义 API”
3. 新建项目时可填写 V2 字段
4. 聊天头部能看到当前连接模式和阶段
5. 右侧工作区可查看阶段和文件
6. 质量检查按钮返回可读结果
7. 导出按钮返回可审草稿路径或清晰错误

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/WorkspacePanel.jsx frontend/src/components/StagePanel.jsx frontend/src/components/FilePreviewPanel.jsx frontend/src/components/ChatPanel.jsx
git commit -m "feat: wire workspace actions to quality check and export endpoints"
```

## Task 7: Update Packaging And Release Documentation

**Files:**
- Modify: `consulting_report.spec`
- Modify: `BUILD.md`
- Modify: `WINDOWS_BUILD.md`
- Modify: `README.md`

- [ ] **Step 1: Write the failing packaging checklist into the docs diff**

Document the expected runtime bundle contents before editing:

```text
dist/咨询报告助手/
  咨询报告助手.exe
  skill/
  frontend/dist/
  ...
```

The checklist must also call out that the bundled skill now comes from `consulting-report-skill v1.2` assets.

- [ ] **Step 2: Update `consulting_report.spec` to include the runtime asset set**

Keep the bundle minimal but sufficient:

```python
datas=[
    ("skill", "skill"),
    ("frontend/dist", "frontend/dist"),
]
```

If `backend/report_tools.py` relies on extra runtime files from the bundled skill package, include those exact directories as additional `datas` entries rather than copying the entire external repo blindly.

- [ ] **Step 3: Rewrite the build docs to match V2 reality**

`BUILD.md` and `WINDOWS_BUILD.md` must explicitly say:

1. Windows is the only first-phase supported platform.
2. Default mode is managed and works out of the box once the managed proxy is deployed.
3. Custom API remains available as an advanced mode.
4. Export is “可审草稿” rather than final typeset Word/PDF.

`README.md` must stop promising features that are not implemented.

- [ ] **Step 4: Run the final verification commands**

Run:

```bash
python -m unittest tests.test_config tests.test_skill_engine tests.test_report_tools tests.test_main_api -v
```

Expected: PASS

Run:

```bash
node --test frontend/tests/connectionMode.test.mjs frontend/tests/workspaceSummary.test.mjs
```

Expected: PASS

Run:

```bash
cd frontend && npm run build
```

Expected: `vite build` succeeds.

Run:

```bash
pyinstaller consulting_report.spec
```

Expected: build succeeds and writes `dist/咨询报告助手/`.

- [ ] **Step 5: Manual packaged smoke test**

Launch the packaged app:

```bash
dist\\咨询报告助手\\咨询报告助手.exe
```

Expected manual results:

1. 应用启动无白屏
2. 默认通道状态正确显示
3. 项目创建、聊天、工作区、质检、导出均可访问
4. 用户配置保存在 `~/.consulting-report/config.json`

- [ ] **Step 6: Commit**

```bash
git add consulting_report.spec BUILD.md WINDOWS_BUILD.md README.md
git commit -m "docs: update packaging and release docs for client v2"
```

## Final Verification Checklist

- [ ] Managed/custom settings survive app restart
- [ ] Old config files still load without crashing
- [ ] New projects use v1.2 plan templates
- [ ] Chat system prompt includes V2 core plan files
- [ ] Workspace endpoint shows stage/status/next actions
- [ ] Quality check output is readable in the UI
- [ ] Exported output is labeled as reviewable draft
- [ ] Windows package opens without requiring local build steps

## Handoff Notes

1. Do not start implementation until the managed proxy base URL is deployed or at least stubbed.
2. If old projects must remain readable, add a compatibility branch during implementation instead of migrating everything in one shot.
3. Keep automated coverage focused on pure backend logic and pure frontend helpers; avoid dragging in a heavy frontend test framework unless the existing lightweight approach proves insufficient.
4. Managed proxy deployment credentials should stay out of the repository; treat them as out-of-band operator input during execution.
