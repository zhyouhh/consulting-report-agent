# 工作区文件栏重做 + 预览框可编辑（R3）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把工作区文件栏从「裸英文文件名平铺」改成「中文分组 + 当前阶段置顶」，并让 8 个用户内容文件支持「预览↔编辑→保存」，后端补一条带白名单门禁 + mtime CAS + 原子写的用户写接口。

**Architecture:** 文件「语义」（属哪组、哪阶段、能否编辑）由后端 `SkillEngine` 单一真值源给出，前端只做中文文案映射与渲染。权限是安全边界，**只认后端** `validate_user_write` 白名单（默认 deny），前端 `editable` 仅控显隐。写接口持与聊天同一把 per-project 锁做 mtime CAS + 原子 `os.replace`，防 AI 写入与用户保存互相覆盖。

**Tech Stack:** 后端 FastAPI + Python `unittest`/pytest；前端 React + Node 原生 `node:test`（无 jsdom，纯函数单测 + 组件 source-guard）。

---

## 关键背景（实施者必读，零上下文假设）

这是一个 **Windows 优先的单机桌面应用**：FastAPI 绑 `127.0.0.1:8080`，PyWebView 加载前端 SPA。运行时用户数据在 `~/.consulting-report/projects/<id>/`。本计划的设计源是已 codex APPROVED 的 spec：`docs/superpowers/specs/2026-06-08-workspace-file-tree-and-editing-design.md`（实施中如遇本计划未覆盖的细节，以该 spec 为准）。

### 已核验的代码锚点（行号为本计划撰写时的真值，实施时以符号为准）

| 符号 | 位置 | 说明 |
|---|---|---|
| `SkillEngine.FORMAL_PLAN_FILES` | `backend/skill.py:22` | 15 个正式 plan 文件名集合（basename） |
| `SkillEngine.REPORT_DRAFT_PATH` | `backend/skill.py:167` | `"content/report_draft_v1.md"` |
| `SkillEngine.read_file` | `backend/skill.py:1019` | `get_project_path → _resolve_project_path → read_text`，缺失抛 `ValueError` |
| `SkillEngine.normalize_file_path` | `backend/skill.py:1042` | 归一为 canonical posix 相对路径；穿越路径经 `_resolve_project_path` 抛 `ValueError` |
| `SkillEngine.validate_plan_write` | `backend/skill.py:1064` | **LLM 专用**写门禁（带 evidence gate）；**不可**复用为用户写门禁 |
| `SkillEngine.get_workspace_summary` | `backend/skill.py:1206` | 返回 dict，`flags` 字段在 1245 行 |
| `SkillEngine._is_backend_owned_stage_tracking_file` | `backend/skill.py:1773` | stage-gates/progress/tasks |
| `SkillEngine._canonicalize_plan_markdown_path` | `backend/skill.py:1780` | **只对 plan/\*.md 小写，content/ 不动** → 用户写不能复用它 |
| `SkillEngine._to_posix` | `backend/skill.py:2221` | Path/str → posix str |
| `SkillEngine._resolve_project_path` | `backend/skill.py:2226` | `(base/file).resolve()`，逃逸抛 `ValueError("非法的文件路径")` |
| `_get_project_request_lock` | `backend/chat.py:212`（模块级）/ `ChatHandler._get_project_request_lock` 1995 | per-project `threading.RLock` |
| `chat_stream` 持锁范围 | `backend/chat.py:3217` | **`with request_lock:` 包裹整轮流式** —— 锁可能被持有 30s+ |
| GET `/files` | `backend/main.py:296` | 当前裸返回 `{"files": [path...]}`，只跳过 `project-info.md` |
| GET `/files/{path}` | `backend/main.py:312` | 当前只返回 `{"content"}` |
| 端点内取锁范式 | `backend/main.py:652-654`（`clear_conversation`） | `handler = get_chat_handler(pid); lock = handler._get_project_request_lock(pid); with lock:` |
| `allow_origins=["*"]` | `backend/main.py:57` | 既有全局现状（D7：R3 不收紧） |

### ⚠️ 对 spec §6.1 的一处已核验偏离（GET 不持锁）

spec §6.1 写「GET `/files/{path}` 读取在 per-project 锁内做」。但已核验 `chat_stream`（`chat.py:3217`）**整轮持有该锁**，若 GET 也进锁，AI 流式回复期间用户**连预览文件都会卡住整轮**——违反「用户触碰到的每一层必须丝滑」。

**本计划的处理（更优且仍正确）：**
- **AI 写入先做原子化（前置硬基础，Task 2）**：当前 `SkillEngine.write_file`（`skill.py:1040`）用 `write_text` 非原子写；所有 AI 可写正式文件（plan 文件 + 正文 `append_report_draft`/`edit_file`）都经 `_execute_plan_write` → `self.skill_engine.write_file(...)`（`chat.py:4854`）落盘。Task 2 把 `write_file` 改成「同目录 temp + `os.replace`」原子写——单点改动覆盖全部 AI 写路径，**彻底消除"读到写入中间态（torn read）"**。这是下面 GET 不持锁能成立的前提（codex R1 BLOCKER 2）。
- **GET 不持锁**，但 `read_file_with_mtime` **先 `stat` 再 `read`**：返回的 `base_mtime_ns` 永远不晚于返回的字节。若 AI 写入恰好插入读取间隙，最坏结果是用户保存时拿到一个**安全的 409**（提示重新加载），**绝不会**静默覆盖 AI 的更新；**可编辑文件**经原子 `write_file` 写入，无锁读不会读到半截（只读追踪文件 stage-gates/progress/tasks 仍后端直写，极端并发下预览可能瞬时错乱、刷新自愈——但它们不可编辑、不入保存/CAS，无数据风险）。预览全程响应。
- **POST 必须持锁**（CAS 原子性是它存在的意义），并用 `run_in_threadpool` 包裹临界区——锁阻塞落在线程池线程上，不阻塞事件循环。保存撞上 AI 整轮持锁时会排队等待，这是 spec §10 已接受的取舍。

> 此偏离 + 原子写已经过 plan codex review（R1）核验，标注为「intentional, verified」；实施者无需再决策，按本计划做即可。spec §6.1/§6.2 已相应更新。

### 测试夹具范式（照抄）

- **`tests/test_skill_engine.py`**：`engine = SkillEngine(projects_dir, repo_skill_dir)`；`project = engine.create_project(payload)`；`project_dir = Path(project["project_dir"])`。已有 helper `_make_project()` 返回 `project_dir` 且设 `self.engine`。
- **`tests/test_main_api.py`**：`self.client = TestClient(main_module.app)`；真实文件 IO 用 `mock.patch.object(main_module, "skill_engine", engine)`（engine 指向 tempdir，见现有 `main.py:215-237` 范式）。
- **前端 utils**：`import test from "node:test"; import assert from "node:assert/strict";`，直接 import `../src/utils/X.js` 断言纯函数。
- **前端组件**：无 jsdom，写 `*.source.test.mjs`，`readFileSync` 组件源码后 `assert.match` / `assert.doesNotMatch` 关键 wiring（范式见 `frontend/tests/independentReviewDrawer.source.test.mjs`）。

---

## 文件结构（创建 / 修改清单）

**后端：**
- 修改 `backend/skill.py` — 新增 `StaleFileError`（模块级）、`FILE_SEMANTICS` / `USER_EDITABLE_FILES` / `RETIRED_WORKSPACE_FILES`（类常量）、`_canonical_user_path` / `is_user_editable` / `get_file_semantics` / `list_workspace_files` / `read_file_with_mtime` / `validate_user_write` / `user_write_file` / `_is_report_review_stale`；`get_workspace_summary` 加 `review_stale` flag；顶部加 `import os` / `import tempfile`。
- 修改 `backend/main.py` — GET `/files` 改调 `list_workspace_files`；GET `/files/{path}` 扩 `{content, mtime_ns, editable}`；新增 POST `/files/{path}`；加 `run_in_threadpool` import、`StaleFileError` import、`UserFileWrite` 模型。

**前端：**
- 新建 `frontend/src/utils/fileTree.js` — 纯函数：分组、当前阶段置顶、path→中文名。
- 新建 `frontend/src/utils/fileEditState.js` — 纯函数：预览↔编辑状态机 + `guardLeave` 决策。
- 重写 `frontend/src/components/FilePreviewPanel.jsx` — 分组文件树 + 双模式 + dirty 守卫 + 暴露 `confirmDiscardIfDirty`。
- 修改 `frontend/src/components/WorkspacePanel.jsx` — 结构化 files、`handleSaveFile` / `reloadFile`、tab 切换守卫、`forwardRef` 暴露 `confirmDiscardIfDirty`、传 `review_stale`。
- 修改 `frontend/src/App.jsx` — `workspacePanelRef` + `handleSelectProject` 切项目前守卫。

**测试：**
- 修改 `tests/test_skill_engine.py`、`tests/test_main_api.py`。
- 新建 `frontend/tests/fileTree.test.mjs`、`frontend/tests/fileEditState.test.mjs`、`frontend/tests/filePreviewPanel.source.test.mjs`；修改 `frontend/tests/workspacePanel.source.test.mjs`（无则新建）。

**文档（Task 9）：**
- 新建 `docs/superpowers/cutover_report_<YYYY-MM-DD>_r3-file-tree-editing.md`。
- 修改 `docs/current-worklist.md`、`consulting-report-agent/CLAUDE.md`。

---

## Task 1: 后端 — 文件语义常量 + 用户可编辑白名单门禁

**Files:**
- Modify: `backend/skill.py`（类常量区 `:38` 之后；方法可放在 `validate_plan_write` 附近 `:1085` 之后）
- Test: `tests/test_skill_engine.py`

纯判定逻辑，零文件 IO 之外副作用。白名单 = 默认 deny，是 POST 写接口与 GET `editable` 字段的**唯一真值源**。

- [ ] **Step 1: 写失败测试**

加到 `tests/test_skill_engine.py` 的 `SkillEngineTests` 类内（`_make_project()` 已存在，返回 `project_dir` 且设 `self.engine`）：

```python
def test_is_user_editable_whitelist_matrix(self):
    self._make_project()
    engine = self.engine
    # 8 个白名单文件可编辑
    for path in [
        "content/report_draft_v1.md", "plan/outline.md", "plan/research-plan.md",
        "plan/notes.md", "plan/references.md", "plan/data-log.md",
        "plan/analysis-notes.md", "plan/presentation-plan.md",
    ]:
        self.assertTrue(engine.is_user_editable(path), f"{path} 应可编辑")
    # 只读 / 退役 / 未知
    for path in [
        "plan/project-overview.md", "plan/independent-review.md", "plan/lint-report.md",
        "plan/delivery-log.md", "plan/stage-gates.md", "plan/progress.md",
        "plan/tasks.md", "plan/review.md", "plan/project-info.md",
        "plan/review-checklist.md", "plan/something-unknown.md", "stage_checkpoints.json",
    ]:
        self.assertFalse(engine.is_user_editable(path), f"{path} 应只读")

def test_is_user_editable_casefolds_full_path(self):
    # Windows 大小写不敏感：大写变体（含 content/）必须仍判为可编辑（白名单整路径 casefold）
    self._make_project()
    self.assertTrue(self.engine.is_user_editable("content/Report_Draft_V1.MD"))
    self.assertTrue(self.engine.is_user_editable("PLAN/OUTLINE.MD"))

def test_get_file_semantics_known_and_unknown(self):
    self._make_project()
    engine = self.engine
    self.assertEqual(engine.get_file_semantics("plan/data-log.md"),
                     {"group": "research", "stage": "S2", "editable": True})
    self.assertEqual(engine.get_file_semantics("plan/independent-review.md"),
                     {"group": "review", "stage": "S5", "editable": False})
    self.assertEqual(engine.get_file_semantics("content/report_draft_v1.md"),
                     {"group": "draft", "stage": "S4", "editable": True})
    # 未知 .md → other/None/False
    self.assertEqual(engine.get_file_semantics("notes/random.md"),
                     {"group": "other", "stage": None, "editable": False})

def test_validate_user_write_allow_deny_traversal(self):
    self._make_project()
    engine = self.engine
    pid = engine.list_projects()[0]["id"]
    # allow：返回白名单 canonical（第一参数是 project_ref，会解析真实项目）
    self.assertEqual(engine.validate_user_write(pid, "plan/outline.md"), "plan/outline.md")
    self.assertEqual(engine.validate_user_write(pid, "content/report_draft_v1.md"),
                     "content/report_draft_v1.md")
    # deny：非白名单 → PermissionError（审查报告 / 后端追踪 / 退役 / checkpoint / 未知）
    for path in [
        "plan/independent-review.md", "plan/lint-report.md",
        "plan/stage-gates.md", "plan/progress.md", "plan/tasks.md",
        "plan/delivery-log.md", "plan/review.md",
        "plan/project-overview.md", "plan/project-info.md",
        "stage_checkpoints.json", "plan/whatever-unknown.md",
    ]:
        with self.assertRaises(PermissionError, msg=f"{path} 应拒写"):
            engine.validate_user_write(pid, path)
    # 路径穿越 → ValueError
    with self.assertRaises(ValueError):
        engine.validate_user_write(pid, "../../../etc/passwd")
```

- [ ] **Step 2: 跑测试确认 fail**

Run: `.venv\Scripts\python -m pytest tests/test_skill_engine.py -k "user_editable or file_semantics or validate_user_write" -v`
Expected: FAIL（`AttributeError: 'SkillEngine' object has no attribute 'is_user_editable'`）

- [ ] **Step 3: 实现常量 + 方法**

在 `backend/skill.py` 的 `FORMAL_PLAN_FILES` 块（`:38`）之后加类常量：

```python
    # R3: 文件语义单一真值源。键为「完整相对 posix 路径」（非 basename）——否则
    # materials/imported/outline.md 会被误判为 S1 正式大纲。stage 是文件级属性（用于置顶）。
    FILE_SEMANTICS = {
        "plan/project-overview.md": {"group": "overview", "stage": "S0"},
        "plan/notes.md": {"group": "research", "stage": "S1"},
        "plan/references.md": {"group": "research", "stage": "S1"},
        "plan/data-log.md": {"group": "research", "stage": "S2"},
        "plan/outline.md": {"group": "analysis", "stage": "S1"},
        "plan/research-plan.md": {"group": "analysis", "stage": "S1"},
        "plan/analysis-notes.md": {"group": "analysis", "stage": "S3"},
        "content/report_draft_v1.md": {"group": "draft", "stage": "S4"},
        "plan/independent-review.md": {"group": "review", "stage": "S5"},
        "plan/lint-report.md": {"group": "review", "stage": "S5"},
        "plan/presentation-plan.md": {"group": "delivery", "stage": "S6"},
        "plan/delivery-log.md": {"group": "delivery", "stage": "S7"},
        "plan/stage-gates.md": {"group": "tracking", "stage": None},
        "plan/progress.md": {"group": "tracking", "stage": None},
        "plan/tasks.md": {"group": "tracking", "stage": None},
        "plan/review.md": {"group": "other", "stage": None},
    }

    # R3: 用户可手动编辑白名单（canonical = casefold 后的完整 posix 相对路径）。默认 deny——
    # 任何不在此集合的文件（后端自动维护 / 审查报告 / 退役 / checkpoint）都只读。
    USER_EDITABLE_FILES = {
        "content/report_draft_v1.md",
        "plan/outline.md",
        "plan/research-plan.md",
        "plan/notes.md",
        "plan/references.md",
        "plan/data-log.md",
        "plan/analysis-notes.md",
        "plan/presentation-plan.md",
    }

    # R3: GET /files 跳过的退役文件（不显示）。
    RETIRED_WORKSPACE_FILES = {
        "plan/project-info.md",
        "plan/review-checklist.md",
    }
```

在 `validate_plan_write`（`:1085` 之后）附近加方法：

```python
    def _canonical_user_path(self, normalized_path: str) -> str:
        # 注意：不复用 _canonicalize_plan_markdown_path（它只 lower plan/*.md，content/ 不动）。
        # 这里对整条 posix 相对路径统一 casefold——Windows 文件系统大小写不敏感，
        # content/Report_Draft_V1.MD 必须与 content/report_draft_v1.md 判为同一文件。
        return self._to_posix(normalized_path).lstrip("/").casefold()

    def is_user_editable(self, normalized_path: str) -> bool:
        return self._canonical_user_path(normalized_path) in self.USER_EDITABLE_FILES

    def get_file_semantics(self, normalized_path: str) -> dict:
        """Map a normalized relative path to {group, stage, editable}.
        Unknown .md → group='other', stage=None, editable=False."""
        canonical = self._canonical_user_path(normalized_path)
        semantics = self.FILE_SEMANTICS.get(canonical, {"group": "other", "stage": None})
        return {
            "group": semantics["group"],
            "stage": semantics["stage"],
            "editable": canonical in self.USER_EDITABLE_FILES,
        }

    def validate_user_write(self, project_ref: str, file_path: str) -> str:
        """R3: independent whitelist gate for USER (HTTP) writes — NOT validate_plan_write
        (that carries the LLM-only pre-outline evidence gate and does not itself deny
        independent-review/lint-report; those live in the chat tool layer the HTTP endpoint
        never reaches). Whitelist = default-deny.
        Path traversal → ValueError (endpoint 400). Not whitelisted → PermissionError (403).
        Returns the whitelist canonical path so the write target is stable across casing."""
        normalized = self.normalize_file_path(project_ref, file_path)  # 穿越路径在此抛 ValueError
        canonical = self._canonical_user_path(normalized)
        if canonical not in self.USER_EDITABLE_FILES:
            raise PermissionError(f"`{normalized}` 不可由用户手动编辑")
        return canonical
```

> `FILE_SEMANTICS` 的键全为小写 ASCII，故用 `_canonical_user_path`（casefold）查表与白名单判定保持一致——大小写变体的 group/stage/editable 三者同步命中。

- [ ] **Step 4: 跑测试确认 pass**

Run: `.venv\Scripts\python -m pytest tests/test_skill_engine.py -k "user_editable or file_semantics or validate_user_write" -v`
Expected: PASS（5 个用例）

- [ ] **Step 5: Commit**

```bash
git add backend/skill.py tests/test_skill_engine.py
git commit -m "feat(r3): file semantics + user-editable whitelist gate (skill engine)"
```

---

## Task 2: 后端 — 结构化文件列表 + 读取带 mtime

**Files:**
- Modify: `backend/skill.py`（顶部 `import os`/`tempfile`；`write_file` 改原子；加 `list_workspace_files`、`read_file_with_mtime`）
- Modify: `backend/chat.py`（canonical draft `edit_file` 直写改走原子 `write_file`，`:4237-4238`）
- Modify: `backend/main.py`（GET `/files` 改造、GET `/files/{path}` 扩展）
- Test: `tests/test_skill_engine.py`（list 语义 + write_file 原子 + canonical draft 不直写）、`tests/test_main_api.py`（端点）

- [ ] **Step 1: 写失败测试（engine 层 list 语义）**

加到 `tests/test_skill_engine.py`：

```python
def test_list_workspace_files_semantics_and_skips(self):
    project_dir = self._make_project()
    engine = self.engine
    pid = engine.list_projects()[0]["id"]
    # 准备文件：正文 + 一份退役 + 一个 materials 同名干扰
    (project_dir / "content").mkdir(parents=True, exist_ok=True)
    (project_dir / "content" / "report_draft_v1.md").write_text("正文", encoding="utf-8")
    (project_dir / "plan" / "project-info.md").write_text("退役", encoding="utf-8")
    (project_dir / "materials" / "imported").mkdir(parents=True, exist_ok=True)
    (project_dir / "materials" / "imported" / "outline.md").write_text("材料里的同名文件", encoding="utf-8")

    files = engine.list_workspace_files(pid)
    by_path = {f["path"]: f for f in files}

    # 退役 / materials 跳过
    self.assertNotIn("plan/project-info.md", by_path)
    self.assertNotIn("materials/imported/outline.md", by_path)

    # 正文：draft/S4/可编辑/mtime 是 str
    draft = by_path["content/report_draft_v1.md"]
    self.assertEqual(draft["group"], "draft")
    self.assertEqual(draft["stage"], "S4")
    self.assertTrue(draft["editable"])
    self.assertIsInstance(draft["mtime_ns"], str)

    # 重点阶段映射（create_project 已 scaffold 这些 plan 文件）
    self.assertEqual(by_path["plan/outline.md"]["stage"], "S1")
    self.assertEqual(by_path["plan/data-log.md"]["stage"], "S2")
    self.assertEqual(by_path["plan/analysis-notes.md"]["stage"], "S3")
    self.assertEqual(by_path["plan/presentation-plan.md"]["stage"], "S6")
    self.assertEqual(by_path["plan/delivery-log.md"]["stage"], "S7")
    # 审查报告只读
    self.assertFalse(by_path["plan/independent-review.md"]["editable"])
    self.assertEqual(by_path["plan/independent-review.md"]["group"], "review")
    # 后端自动维护文件只读
    self.assertFalse(by_path["plan/stage-gates.md"]["editable"])
    self.assertEqual(by_path["plan/stage-gates.md"]["group"], "tracking")

def test_read_file_with_mtime_returns_str_mtime(self):
    project_dir = self._make_project()
    engine = self.engine
    pid = engine.list_projects()[0]["id"]
    (project_dir / "content").mkdir(parents=True, exist_ok=True)
    (project_dir / "content" / "report_draft_v1.md").write_text("正文内容", encoding="utf-8")
    data = engine.read_file_with_mtime(pid, "content/report_draft_v1.md")
    self.assertEqual(data["content"], "正文内容")
    self.assertIsInstance(data["mtime_ns"], str)
    self.assertTrue(data["mtime_ns"].isdigit())

def test_write_file_atomic_writes_content_no_temp_residue(self):
    # BLOCKER 2 回归守卫：write_file 改原子（temp + os.replace）后仍正确写入、且成功路径不留 .tmp。
    # （torn read 本身竞态难确定性测试；此处守 happy-path 行为 + 清理。）
    project_dir = self._make_project()
    engine = self.engine
    pid = engine.list_projects()[0]["id"]
    engine.write_file(pid, "plan/notes.md", "原子写入的内容")
    self.assertEqual((project_dir / "plan" / "notes.md").read_text(encoding="utf-8"),
                     "原子写入的内容")
    self.assertEqual(list((project_dir / "plan").glob("*.tmp")), [])

def test_canonical_draft_edit_no_direct_write_text_in_chat(self):
    # R2 BLOCKER：canonical draft edit_file 不得再绕过原子 write_file 直接 draft_path.write_text。
    # 源码守卫——fail-first（改前 chat.py:4238 仍有该直写），3b-2 路由到 write_file 后转绿。
    chat_src = (Path(__file__).resolve().parents[1] / "backend" / "chat.py").read_text(encoding="utf-8")
    self.assertNotIn("draft_path.write_text(", chat_src)
```

> `create_project` 会 `for template_name in sorted(self.FORMAL_PLAN_FILES)` 拷贝模板（`skill.py:833`），故 `plan/outline.md`、`plan/data-log.md` 等在测试里已存在。若某文件无模板未被 scaffold，断言前先手动 `write_text` 占位。

- [ ] **Step 2: 跑测试确认 fail**

Run: `.venv\Scripts\python -m pytest tests/test_skill_engine.py -k "list_workspace_files or read_file_with_mtime or write_file_atomic or canonical_draft" -v`
Expected：`list/read` 用例 FAIL（`AttributeError: ... 'list_workspace_files'`）；`canonical_draft` 用例 FAIL（chat.py 仍有 `draft_path.write_text`）；`write_file_atomic` 现状即过（仅回归守卫，原子改造后须仍过）。

- [ ] **Step 3: 实现 engine 方法（含原子 write_file）**

3a. `backend/skill.py` 顶部 import 区（`:1-9`，当前**没有** `os`/`tempfile`）补：

```python
import os
import tempfile
```

3b. **把 `write_file`（`:1031`）改成原子写**（BLOCKER 2：所有 AI 写正式文件都经 `_execute_plan_write → self.skill_engine.write_file`（`chat.py:4854`）落盘，单点原子化即覆盖全部写路径、消除 torn read）。用 `Path.write_text` 写 temp 再 `os.replace`，**保持原 newline 行为不变**（不要换成 `open(..., newline="")`，会改 Windows 换行）：

```python
    def write_file(self, project_ref: str, file_path: str, content: str):
        """鍐欏叆椤圭洰鏂囦欢（原子：同目录 temp + os.replace，避免并发读到写入中间态）"""
        project_path = self.get_project_path(project_ref)
        if not project_path:
            raise ValueError(f"项目 {project_ref} 不存在")

        normalized_path = self.validate_plan_write(project_ref, file_path)
        full_path = self._resolve_project_path(project_path.resolve(), normalized_path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_name = tempfile.mkstemp(dir=str(full_path.parent), suffix=".tmp")
        os.close(tmp_fd)
        try:
            Path(tmp_name).write_text(content, encoding="utf-8")  # 与原 write_text 同 newline 行为
            os.replace(tmp_name, full_path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
```

> `Path` 已在 `skill.py:2` import；`os.close(tmp_fd)` 后用 `Path.write_text` 自己开句柄写，再 `os.replace` 原子替换。崩溃时不留半截目标文件（顺带白赚 crash-safety）。

3b-2. **把 canonical draft `edit_file` 的直写改走原子 `write_file`**（R2 BLOCKER：`append_report_draft` 已经 `_execute_plan_write → write_file`，但正文 `edit_file`（章节重写/整篇重写/文字替换）分支在 `chat.py:4237-4238` **直接 `draft_path.write_text(...)`**，绕过原子写——这是最高频被并发预览的文件，必须一起原子化，否则 GET 不持锁仍有 torn read）。把 `chat.py:4236-4238`：

```python
        new_draft = draft_text.replace(actual_old, new_string, 1)
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(new_draft, encoding="utf-8")
        mtime_after = draft_path.stat().st_mtime
```

改为：

```python
        new_draft = draft_text.replace(actual_old, new_string, 1)
        # 走原子 write_file（content/ 路径在 validate_plan_write 内提前 return、无 gating），消除 torn read
        self.skill_engine.write_file(project_id, self.skill_engine.REPORT_DRAFT_PATH, new_draft)
        mtime_after = draft_path.stat().st_mtime
```

> `write_file(project_id, REPORT_DRAFT_PATH, new_draft)` 内部 `validate_plan_write` 对 `content/report_draft_v1.md` 不是 plan markdown → 立即 return（无证据门禁），随后做 `mkdir` + 原子 `os.replace`。落到与原 `draft_path` 同一目标，行为等价但原子。这是 chat.py 唯一一处 canonical draft 直写（`append` 路径已走 write_file）。

3c. 加在 `read_file`（`:1019`）附近：

```python
    def list_workspace_files(self, project_ref: str) -> list[dict]:
        """R3: structured workspace file list for the front-end file tree.
        Skips retired files and everything under materials/. Each .md → {path, group,
        stage, editable, mtime_ns}. mtime_ns is a str (opaque — never coerce to Number;
        JS loses precision past 2^53)."""
        project_path = self.get_project_path(project_ref)
        if not project_path:
            raise ValueError(f"项目 {project_ref} 不存在")

        files = []
        for md_file in project_path.rglob("*.md"):
            rel_path = self._to_posix(md_file.relative_to(project_path)).lstrip("/")
            if rel_path in self.RETIRED_WORKSPACE_FILES:
                continue
            if rel_path.startswith("materials/"):
                continue
            semantics = self.get_file_semantics(rel_path)
            files.append({
                "path": rel_path,
                "group": semantics["group"],
                "stage": semantics["stage"],
                "editable": semantics["editable"],
                "mtime_ns": str(md_file.stat().st_mtime_ns),
            })
        return files

    def read_file_with_mtime(self, project_ref: str, file_path: str) -> dict:
        """R3: content + mtime_ns for the edit base. NO lock here (the per-project request
        lock is held by chat_stream for a full turn — locking reads would freeze preview).
        stat BEFORE read so the returned base_mtime is never NEWER than the bytes returned:
        if an AI write interleaves, the worst case is a safe 409 on save (user reloads),
        never a silent overwrite. The AI writes EDITABLE files only via the atomic write_file
        (os.replace), so a no-lock read of an editable file never sees a half-written one.
        (Read-only tracking files are still direct-written; a rare torn preview self-heals on
        reload — they are never editable nor in the save/CAS path.)"""
        project_path = self.get_project_path(project_ref)
        if not project_path:
            raise ValueError(f"项目 {project_ref} 不存在")
        full_path = self._resolve_project_path(project_path, file_path)
        if not full_path.exists():
            raise ValueError(f"文件 {file_path} 不存在")
        mtime_ns = str(full_path.stat().st_mtime_ns)
        content = full_path.read_text(encoding="utf-8")
        return {"content": content, "mtime_ns": mtime_ns}
```

- [ ] **Step 4: 跑 engine 测试确认 pass**

Run: `.venv\Scripts\python -m pytest tests/test_skill_engine.py -k "list_workspace_files or read_file_with_mtime or write_file_atomic or canonical_draft" -v`
Expected: PASS（含 `canonical_draft` 源码守卫——需先完成 Step 3b-2 的 chat.py 改动）

> 顺带回归 write_file 既有用例（确认原子改造没破坏既有写入行为）：`.venv\Scripts\python -m pytest tests/test_skill_engine.py -k "write_file or plan_write" -v` 全过。

- [ ] **Step 5: 写端点失败测试**

加到 `tests/test_main_api.py`（新建一个 `R3FileApiTests(unittest.TestCase)`，复用真实 engine + tempdir 范式）：

```python
class R3FileApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main_module.app)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        from backend.skill import SkillEngine
        repo_skill_dir = Path(__file__).resolve().parents[1] / "skill"
        self.engine = SkillEngine(Path(self._tmp.name) / "projects", repo_skill_dir)
        project = self.engine.create_project({
            "name": "demo", "workspace_dir": str(Path(self._tmp.name) / "ws"),
            "project_type": "strategy-consulting", "theme": "t",
            "target_audience": "a", "deadline": "2026-04-01",
            "expected_length": "3000 words", "notes": "n",
        })
        self.pid = project["id"]
        self.project_dir = Path(project["project_dir"])
        (self.project_dir / "content").mkdir(parents=True, exist_ok=True)
        (self.project_dir / "content" / "report_draft_v1.md").write_text("初稿", encoding="utf-8")
        self._patch = mock.patch.object(main_module, "skill_engine", self.engine)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_list_files_returns_structured_array(self):
        r = self.client.get(f"/api/projects/{self.pid}/files")
        self.assertEqual(r.status_code, 200)
        files = r.json()["files"]
        self.assertTrue(all({"path", "group", "stage", "editable", "mtime_ns"} <= set(f) for f in files))
        draft = next(f for f in files if f["path"] == "content/report_draft_v1.md")
        self.assertTrue(draft["editable"])
        self.assertIsInstance(draft["mtime_ns"], str)

    def test_read_file_returns_content_mtime_editable(self):
        r = self.client.get(f"/api/projects/{self.pid}/files/content/report_draft_v1.md")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["content"], "初稿")
        self.assertTrue(body["editable"])
        self.assertIsInstance(body["mtime_ns"], str)

    def test_read_readonly_file_editable_false(self):
        (self.project_dir / "plan" / "independent-review.md").write_text("审查", encoding="utf-8")
        r = self.client.get(f"/api/projects/{self.pid}/files/plan/independent-review.md")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["editable"])
```

- [ ] **Step 6: 跑端点测试确认 fail**

Run: `.venv\Scripts\python -m pytest tests/test_main_api.py::R3FileApiTests -v`
Expected: FAIL（GET `/files/{path}` 还没返回 `editable`）

- [ ] **Step 7: 改 GET 端点**

`backend/main.py`：把 GET `/files`（`:296-309`）整段替换为：

```python
@app.get("/api/projects/{project_id}/files")
async def list_files(project_id: str):
    try:
        return {"files": skill_engine.list_workspace_files(project_id)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
```

把 GET `/files/{path}`（`:312-318`）整段替换为：

```python
@app.get("/api/projects/{project_id}/files/{file_path:path}")
async def read_file(project_id: str, file_path: str):
    try:
        normalized = skill_engine.normalize_file_path(project_id, file_path)
        data = skill_engine.read_file_with_mtime(project_id, file_path)
        data["editable"] = skill_engine.is_user_editable(normalized)
        return data
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
```

- [ ] **Step 8: 跑端点测试确认 pass**

Run: `.venv\Scripts\python -m pytest tests/test_main_api.py::R3FileApiTests -v`
Expected: PASS（3 个用例）

- [ ] **Step 9: Commit**

```bash
git add backend/skill.py backend/main.py tests/test_skill_engine.py tests/test_main_api.py
git commit -m "feat(r3): structured GET /files + read-with-mtime endpoint"
```

---

## Task 3: 后端 — 用户写接口（白名单 + mtime CAS + 原子写）

**Files:**
- Modify: `backend/skill.py`（顶部 `import os` / `import tempfile`；模块级 `StaleFileError`；`user_write_file`）
- Modify: `backend/main.py`（`run_in_threadpool` / `StaleFileError` import；`UserFileWrite` 模型；POST `/files/{path}`）
- Test: `tests/test_main_api.py`（`R3FileApiTests`）

- [ ] **Step 1: 写失败测试（POST 全路径 + 锁内竞争）**

追加到 `R3FileApiTests`：

```python
    def _mtime(self, rel):
        return str((self.project_dir / rel).stat().st_mtime_ns)

    def test_post_write_success_returns_new_mtime(self):
        base = self._mtime("content/report_draft_v1.md")
        r = self.client.post(
            f"/api/projects/{self.pid}/files/content/report_draft_v1.md",
            json={"content": "改过的正文", "base_mtime_ns": base},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")
        self.assertIsInstance(r.json()["mtime_ns"], str)
        self.assertEqual(
            (self.project_dir / "content" / "report_draft_v1.md").read_text(encoding="utf-8"),
            "改过的正文",
        )

    def test_post_write_denied_readonly_403(self):
        (self.project_dir / "plan" / "independent-review.md").write_text("审查", encoding="utf-8")
        base = self._mtime("plan/independent-review.md")
        r = self.client.post(
            f"/api/projects/{self.pid}/files/plan/independent-review.md",
            json={"content": "试图篡改", "base_mtime_ns": base},
        )
        self.assertEqual(r.status_code, 403)

    def test_post_write_traversal_400(self):
        r = self.client.post(
            f"/api/projects/{self.pid}/files/../../../evil.md",
            json={"content": "x", "base_mtime_ns": "1"},
        )
        self.assertIn(r.status_code, (400, 404))  # 路径穿越被 _resolve_project_path 挡

    def test_post_write_missing_file_404(self):
        # outline.md 在白名单但删除后不存在 → 404（用户只能改已存在文件，不新建）
        outline = self.project_dir / "plan" / "outline.md"
        if outline.exists():
            outline.unlink()
        r = self.client.post(
            f"/api/projects/{self.pid}/files/plan/outline.md",
            json={"content": "x", "base_mtime_ns": "1"},
        )
        self.assertEqual(r.status_code, 404)

    def test_post_write_stale_mtime_409(self):
        r = self.client.post(
            f"/api/projects/{self.pid}/files/content/report_draft_v1.md",
            json={"content": "x", "base_mtime_ns": "999999999999999999"},
        )
        self.assertEqual(r.status_code, 409)

    def test_post_write_rejects_numeric_base_mtime(self):
        base_int = int(self._mtime("content/report_draft_v1.md"))
        r = self.client.post(
            f"/api/projects/{self.pid}/files/content/report_draft_v1.md",
            json={"content": "x", "base_mtime_ns": base_int},  # number, 非 str
        )
        self.assertEqual(r.status_code, 422)  # pydantic str 字段拒绝 int

    def test_post_write_missing_project_404(self):
        r = self.client.post(
            "/api/projects/no-such-project/files/content/report_draft_v1.md",
            json={"content": "x", "base_mtime_ns": "1"},
        )
        self.assertEqual(r.status_code, 404)

    def test_post_write_serialized_under_request_lock(self):
        # 持有与聊天同一把 per-project 锁时，POST 必须阻塞到锁释放（CAS 串行化、不丢写）。
        import backend.chat as chat_mod
        import threading as _t
        lock = chat_mod._get_project_request_lock(self.pid)
        base = self._mtime("content/report_draft_v1.md")
        done = {"status": None}

        def _save():
            r = self.client.post(
                f"/api/projects/{self.pid}/files/content/report_draft_v1.md",
                json={"content": "锁释放后才落盘", "base_mtime_ns": base},
            )
            done["status"] = r.status_code

        lock.acquire()
        try:
            t = _t.Thread(target=_save)
            t.start()
            t.join(timeout=1.0)
            # 锁未释放：请求应仍在等待，未完成
            self.assertIsNone(done["status"], "POST 不应在锁被持有时完成")
        finally:
            lock.release()
        t.join(timeout=5.0)
        self.assertEqual(done["status"], 200)
        self.assertEqual(
            (self.project_dir / "content" / "report_draft_v1.md").read_text(encoding="utf-8"),
            "锁释放后才落盘",
        )
```

- [ ] **Step 2: 跑测试确认 fail**

Run: `.venv\Scripts\python -m pytest tests/test_main_api.py::R3FileApiTests -k post_write -v`
Expected: FAIL（POST 端点不存在 → 405/404）

- [ ] **Step 3: 实现 engine 原子写 + 异常类型**

`backend/skill.py`（`import os` / `import tempfile` 已在 Task 2 Step 3a 添加）。在 imports 之后、`class SkillEngine` 之前加模块级异常：

```python
class StaleFileError(Exception):
    """Raised when a user write's base_mtime_ns no longer matches the file on disk (an AI
    write or another save landed in between). Carries the current mtime_ns (str) for 409."""

    def __init__(self, current_mtime_ns: str):
        super().__init__("文件已被更新")
        self.current_mtime_ns = current_mtime_ns
```

加 engine 方法（`user_write_file`，放在 `read_file_with_mtime` 之后）：

```python
    def user_write_file(self, project_ref: str, file_path: str, content: str,
                        base_mtime_ns: str) -> str:
        """R3: atomic user write with mtime CAS. Caller MUST hold the per-project request
        lock (shared with chat writes) so the stat→replace window is not racing an AI write.
        Returns new mtime_ns (str). Raises:
          - ValueError('非法的文件路径') on traversal       → endpoint 400
          - PermissionError on non-whitelisted file         → endpoint 403
          - FileNotFoundError on missing file               → endpoint 404
          - StaleFileError on mtime mismatch                → endpoint 409
        """
        project_path = self.get_project_path(project_ref)
        if not project_path:
            raise ValueError(f"项目 {project_ref} 不存在")
        canonical = self.validate_user_write(project_ref, file_path)  # PermissionError / ValueError
        full_path = self._resolve_project_path(project_path.resolve(), canonical)
        if not full_path.exists():
            raise FileNotFoundError(f"文件 {canonical} 不存在")
        current_mtime_ns = str(full_path.stat().st_mtime_ns)
        if current_mtime_ns != base_mtime_ns:
            raise StaleFileError(current_mtime_ns)
        # 原子写：同目录 temp + os.replace（与 write_file 同款，newline 行为一致）
        tmp_fd, tmp_name = tempfile.mkstemp(dir=str(full_path.parent), suffix=".tmp")
        os.close(tmp_fd)
        try:
            Path(tmp_name).write_text(content, encoding="utf-8")
            os.replace(tmp_name, full_path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return str(full_path.stat().st_mtime_ns)
```

- [ ] **Step 4: 实现 POST 端点**

`backend/main.py` 顶部补 import：

```python
from fastapi.concurrency import run_in_threadpool
```

把 `from .skill import SkillEngine` 改为：

```python
from .skill import SkillEngine, StaleFileError
```

在 GET `/files/{path}` 端点之后加模型 + POST 端点：

```python
class UserFileWrite(BaseModel):
    content: str
    base_mtime_ns: str  # opaque string; pydantic rejects a raw JSON number → 422


@app.post("/api/projects/{project_id}/files/{file_path:path}")
async def write_user_file(project_id: str, file_path: str, payload: UserFileWrite):
    # 项目不存在前置判 404（避免靠脆弱字符串匹配区分 404/400）
    if not skill_engine.get_project_path(project_id):
        raise HTTPException(status_code=404, detail="项目不存在")

    handler = get_chat_handler(project_id)
    request_lock = handler._get_project_request_lock(project_id)

    def _write_under_lock():
        # 全段持与聊天同一把锁：CAS(stat) → os.replace 必须对 AI 写入原子互斥。
        # run_in_threadpool 包裹，锁阻塞落在线程池线程、不阻塞事件循环。
        with request_lock:
            new_mtime = skill_engine.user_write_file(
                project_id, file_path, payload.content, payload.base_mtime_ns
            )
            return {"status": "ok", "mtime_ns": new_mtime}

    try:
        return await run_in_threadpool(_write_under_lock)
    except PermissionError:
        raise HTTPException(status_code=403, detail="该文件不可编辑")
    except StaleFileError:
        raise HTTPException(
            status_code=409,
            detail="文件已被更新（可能是 AI 刚写过），请重新加载后再编辑",
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="文件不存在")
    except ValueError:
        # 剩余 ValueError = 路径穿越（非法的文件路径）
        raise HTTPException(status_code=400, detail="非法的文件路径")
```

> **异常兜底（R2 NIT 3）**：`os.replace` 在 Windows 若目标被占用 / 只读会抛 `OSError`——它不在上面四类 `except` 内，会冒泡为 FastAPI 默认 **500**（用户侧表现为保存失败提示）。这是可接受兜底，不需为它单设分支；实现时知悉「原子写非任何情况下都成功」即可。

- [ ] **Step 5: 跑测试确认 pass**

Run: `.venv\Scripts\python -m pytest tests/test_main_api.py::R3FileApiTests -v`
Expected: PASS（全部，含锁串行化用例）

- [ ] **Step 6: Commit**

```bash
git add backend/skill.py backend/main.py tests/test_main_api.py
git commit -m "feat(r3): POST /files user write (whitelist + mtime CAS + atomic replace)"
```

---

## Task 4: 后端 — 正文改动后 review_stale advisory

**Files:**
- Modify: `backend/skill.py`（`_is_report_review_stale`；`get_workspace_summary` flag）
- Test: `tests/test_skill_engine.py`、`tests/test_main_api.py`

D6：两份审查报告都存在 **且** `draft_mtime_ns > min(两份报告 mtime)` 即 stale。**不 gate 在 `review_passed_at`**——覆盖「报告已生成、用户改了正文、但还没点审查通过」的窗口。

- [ ] **Step 1: 写失败测试**

加到 `tests/test_skill_engine.py`：

> 复用 test_skill_engine 已有 fixture `_write_independent_review(project_dir)` / `_write_lint_report(project_dir)`（`tests/test_skill_engine.py:254/279`，写带 anchors + completion marker + body 的**有效**报告）。`_is_report_review_stale` gate 在 `_has_effective_review_reports`（BLOCKER 1）——光有 scaffold 的模板不算。

```python
def _set_mtime_ns(self, path, ns):
    os.utime(path, ns=(ns, ns))

def test_review_stale_true_when_draft_newer_than_oldest_report(self):
    project_dir = self._make_project()
    engine = self.engine
    (project_dir / "content").mkdir(parents=True, exist_ok=True)
    draft = project_dir / "content" / "report_draft_v1.md"
    draft.write_text("正文", encoding="utf-8")
    self._write_independent_review(project_dir)   # 有效报告
    self._write_lint_report(project_dir)
    self._set_mtime_ns(project_dir / "plan" / "independent-review.md", 1_000)
    self._set_mtime_ns(project_dir / "plan" / "lint-report.md", 1_500)
    self._set_mtime_ns(draft, 2_000)              # 比两份都新
    self.assertTrue(engine._is_report_review_stale(project_dir))

def test_review_stale_true_when_draft_between_two_reports(self):
    # NIT 2：spec 判定是 draft > min(report mtimes)，不要求比两份都新。
    project_dir = self._make_project()
    engine = self.engine
    (project_dir / "content").mkdir(parents=True, exist_ok=True)
    draft = project_dir / "content" / "report_draft_v1.md"
    draft.write_text("正文", encoding="utf-8")
    self._write_independent_review(project_dir)
    self._write_lint_report(project_dir)
    self._set_mtime_ns(project_dir / "plan" / "independent-review.md", 1_000)
    self._set_mtime_ns(draft, 1_500)              # 介于两份之间
    self._set_mtime_ns(project_dir / "plan" / "lint-report.md", 2_000)
    self.assertTrue(engine._is_report_review_stale(project_dir))

def test_review_stale_false_when_draft_older_than_both(self):
    project_dir = self._make_project()
    engine = self.engine
    (project_dir / "content").mkdir(parents=True, exist_ok=True)
    draft = project_dir / "content" / "report_draft_v1.md"
    draft.write_text("正文", encoding="utf-8")
    self._write_independent_review(project_dir)
    self._write_lint_report(project_dir)
    self._set_mtime_ns(draft, 500)
    self._set_mtime_ns(project_dir / "plan" / "independent-review.md", 1_000)
    self._set_mtime_ns(project_dir / "plan" / "lint-report.md", 1_500)
    self.assertFalse(engine._is_report_review_stale(project_dir))

def test_review_stale_false_when_reports_are_only_templates(self):
    # BLOCKER 1：create_project scaffold 了 independent-review.md / lint-report.md 模板；
    # 仅模板（非有效报告）+ draft 更新 不得置 stale。
    project_dir = self._make_project()
    engine = self.engine
    (project_dir / "content").mkdir(parents=True, exist_ok=True)
    draft = project_dir / "content" / "report_draft_v1.md"
    draft.write_text("正文", encoding="utf-8")
    # 不写有效报告——保留 create_project 拷贝的模板原样
    self._set_mtime_ns(project_dir / "plan" / "independent-review.md", 1_000)
    self._set_mtime_ns(project_dir / "plan" / "lint-report.md", 1_000)
    self._set_mtime_ns(draft, 2_000)
    self.assertFalse(engine._is_report_review_stale(project_dir))

def test_review_stale_false_when_only_one_effective_report(self):
    project_dir = self._make_project()
    engine = self.engine
    (project_dir / "content").mkdir(parents=True, exist_ok=True)
    draft = project_dir / "content" / "report_draft_v1.md"
    draft.write_text("正文", encoding="utf-8")
    self._write_independent_review(project_dir)   # 仅一份有效，lint 仍是模板
    self._set_mtime_ns(project_dir / "plan" / "independent-review.md", 1_000)
    self._set_mtime_ns(draft, 2_000)
    self.assertFalse(engine._is_report_review_stale(project_dir))

def test_workspace_summary_exposes_review_stale_flag(self):
    self._make_project()
    engine = self.engine
    pid = engine.list_projects()[0]["id"]
    summary = engine.get_workspace_summary(pid)
    self.assertIn("review_stale", summary["flags"])
```

- [ ] **Step 2: 跑测试确认 fail**

Run: `.venv\Scripts\python -m pytest tests/test_skill_engine.py -k review_stale -v`
Expected: FAIL（`AttributeError: ... '_is_report_review_stale'`）

- [ ] **Step 3: 实现**

`backend/skill.py`，加在 `_has_effective_review_reports`（`:2031`）附近：

```python
    def _is_report_review_stale(self, project_path: Path) -> bool:
        """R3 D6 advisory: both review reports are EFFECTIVE (substantive, not the scaffolded
        template — BLOCKER 1) AND the draft is newer than the OLDER report. NOT gated on
        review_passed_at — covers the window where reports exist, the draft was edited, but the
        user hasn't clicked 审查通过 yet (review_passed_at unset; record_stage_checkpoint only
        checks report structure, not whether they cover the current draft)."""
        draft_path = project_path / self.REPORT_DRAFT_PATH
        if not draft_path.exists():
            return False
        # 仅模板（新建项目 scaffold 的 independent-review.md / lint-report.md）不算——
        # 必须两份都是有效报告，复用生产门禁 _has_effective_review_reports。
        if not self._has_effective_review_reports(project_path):
            return False
        ir_path = project_path / "plan" / "independent-review.md"
        lint_path = project_path / "plan" / "lint-report.md"
        draft_mtime = draft_path.stat().st_mtime_ns
        oldest_report_mtime = min(ir_path.stat().st_mtime_ns, lint_path.stat().st_mtime_ns)
        return draft_mtime > oldest_report_mtime
```

`get_workspace_summary`（`:1245`）把：

```python
            "flags": stage_state.get("flags", {}),
```

改为：

```python
            "flags": {
                **stage_state.get("flags", {}),
                "review_stale": self._is_report_review_stale(project_path),
            },
```

- [ ] **Step 4: 端点层断言（test_main_api）**

加到 `R3FileApiTests`：

```python
    def _write_effective_reports(self):
        # review_stale gate 在 _has_effective_review_reports，需写带 anchors + marker + body 的有效报告
        eng = self.engine
        ir_lines = ["# Independent review", ""]
        for anchor in eng.INDEPENDENT_REVIEW_ANCHORS:
            ir_lines += [anchor, "审查结论: 已完成实质复核。", "证据说明: 对照正文与资料核验。", ""]
        ir_lines.append(eng.INDEPENDENT_REVIEW_COMPLETION_MARKER)
        (self.project_dir / "plan" / "independent-review.md").write_text(
            "\n".join(ir_lines).strip() + "\n", encoding="utf-8")
        lint_lines = [
            "# AI 味自查", "", "## 总览", "结论: 已完成全文表达检查。", "预计修改时间: 30 分钟。",
            "", "## 按章节排列", "- 执行摘要: 删除空泛形容词。", "- 建议章节: 改为可执行动作。",
            eng.LINT_REPORT_COMPLETION_MARKER,
        ]
        (self.project_dir / "plan" / "lint-report.md").write_text(
            "\n".join(lint_lines).strip() + "\n", encoding="utf-8")

    def test_workspace_review_stale_after_draft_edit(self):
        self._write_effective_reports()
        ir = self.project_dir / "plan" / "independent-review.md"
        lint = self.project_dir / "plan" / "lint-report.md"
        draft = self.project_dir / "content" / "report_draft_v1.md"
        os.utime(ir, ns=(1000, 1000))
        os.utime(lint, ns=(1500, 1500))
        os.utime(draft, ns=(2000, 2000))
        r = self.client.get(f"/api/projects/{self.pid}/workspace")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["flags"]["review_stale"])
```

- [ ] **Step 5: 跑全部确认 pass**

Run: `.venv\Scripts\python -m pytest tests/test_skill_engine.py -k review_stale tests/test_main_api.py::R3FileApiTests -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/skill.py tests/test_skill_engine.py tests/test_main_api.py
git commit -m "feat(r3): review_stale advisory flag in workspace summary (D6)"
```

---

## Task 5: 前端 util — fileTree（分组 + 当前阶段置顶 + 中文名）

**Files:**
- Create: `frontend/src/utils/fileTree.js`
- Test: `frontend/tests/fileTree.test.mjs`

纯函数，无 React 依赖。

- [ ] **Step 1: 写失败测试**

`frontend/tests/fileTree.test.mjs`：

```javascript
import test from "node:test";
import assert from "node:assert/strict";

import { buildFileTree, displayName, GROUP_ORDER } from "../src/utils/fileTree.js";

const files = [
  { path: "plan/project-overview.md", group: "overview", stage: "S0", editable: false, mtime_ns: "1" },
  { path: "plan/notes.md", group: "research", stage: "S1", editable: true, mtime_ns: "2" },
  { path: "plan/data-log.md", group: "research", stage: "S2", editable: true, mtime_ns: "3" },
  { path: "content/report_draft_v1.md", group: "draft", stage: "S4", editable: true, mtime_ns: "4" },
  { path: "plan/presentation-plan.md", group: "delivery", stage: "S6", editable: true, mtime_ns: "5" },
  { path: "plan/stage-gates.md", group: "tracking", stage: null, editable: false, mtime_ns: "6" },
  { path: "weird/unknown.md", group: "other", stage: null, editable: false, mtime_ns: "7" },
];

test("displayName maps known path to Chinese, falls back to basename", () => {
  assert.equal(displayName("content/report_draft_v1.md"), "报告正文");
  assert.equal(displayName("plan/data-log.md"), "资料采集记录");
  assert.equal(displayName("weird/unknown.md"), "unknown");
});

test("buildFileTree groups and orders groups with tracking last", () => {
  const tree = buildFileTree(files, "S2");
  const groupKeys = tree.map((g) => g.group);
  // tracking 在最后
  assert.equal(groupKeys[groupKeys.length - 1], "tracking");
  // 顺序遵循 GROUP_ORDER 子序
  const idx = groupKeys.map((k) => GROUP_ORDER.indexOf(k));
  assert.deepEqual(idx, [...idx].sort((a, b) => a - b));
});

test("buildFileTree sorts current-stage file to the top of its group", () => {
  // S2 → data-log 应排在 research 组顶部（在 notes/S1 之前）
  const tree = buildFileTree(files, "S2");
  const research = tree.find((g) => g.group === "research");
  assert.equal(research.files[0].path, "plan/data-log.md");
  assert.equal(research.files[0].isCurrentStage, true);
  assert.equal(research.hasCurrentStage, true);
});

test("buildFileTree marks S6 presentation-plan current when stage=S6", () => {
  const tree = buildFileTree(files, "S6");
  const delivery = tree.find((g) => g.group === "delivery");
  assert.equal(delivery.files[0].path, "plan/presentation-plan.md");
  assert.equal(delivery.files[0].isCurrentStage, true);
});

test("buildFileTree attaches label + tracking defaultCollapsed", () => {
  const tree = buildFileTree(files, "S2");
  const tracking = tree.find((g) => g.group === "tracking");
  assert.equal(tracking.defaultCollapsed, true);
  assert.equal(tracking.files[0].label, "阶段门禁（系统）");
});

test("buildFileTree routes unknown group to other", () => {
  const tree = buildFileTree([{ path: "x/y.md", group: "nonsense", stage: null }], null);
  assert.equal(tree[0].group, "other");
});
```

- [ ] **Step 2: 跑测试确认 fail**

Run: `cd frontend && node --test tests/fileTree.test.mjs`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 fileTree.js**

`frontend/src/utils/fileTree.js`：

```javascript
export const GROUP_ORDER = [
  "overview", "research", "analysis", "draft", "review", "delivery", "other", "tracking",
];

export const GROUP_LABELS = {
  overview: "项目概览",
  research: "研究与素材",
  analysis: "大纲与分析",
  draft: "报告正文",
  review: "审查报告",
  delivery: "演示与交付",
  tracking: "阶段追踪·系统",
  other: "其他",
};

export const FILE_DISPLAY_NAMES = {
  "plan/project-overview.md": "项目概览",
  "plan/notes.md": "研究笔记",
  "plan/references.md": "资料来源",
  "plan/data-log.md": "资料采集记录",
  "plan/outline.md": "报告大纲",
  "plan/research-plan.md": "研究方案",
  "plan/analysis-notes.md": "分析记录",
  "content/report_draft_v1.md": "报告正文",
  "plan/independent-review.md": "独立审查报告",
  "plan/lint-report.md": "AI 味自查报告",
  "plan/presentation-plan.md": "演示计划",
  "plan/delivery-log.md": "交付记录",
  "plan/stage-gates.md": "阶段门禁（系统）",
  "plan/progress.md": "项目进度（系统）",
  "plan/tasks.md": "阶段任务（系统）",
  "plan/review.md": "审查记录",
};

export function displayName(path) {
  if (FILE_DISPLAY_NAMES[path]) return FILE_DISPLAY_NAMES[path];
  const base = String(path).split("/").pop() || String(path);
  return base.replace(/\.md$/i, "");
}

export function buildFileTree(files = [], currentStage = null) {
  const byGroup = new Map();
  for (const file of files) {
    const group = file.group && GROUP_LABELS[file.group] ? file.group : "other";
    if (!byGroup.has(group)) byGroup.set(group, []);
    byGroup.get(group).push({
      ...file,
      group,
      label: displayName(file.path),
      isCurrentStage: currentStage != null && file.stage === currentStage,
    });
  }
  const groups = [];
  for (const group of GROUP_ORDER) {
    const groupFiles = byGroup.get(group);
    if (!groupFiles || groupFiles.length === 0) continue;
    groupFiles.sort((a, b) => {
      if (a.isCurrentStage !== b.isCurrentStage) return a.isCurrentStage ? -1 : 1;
      return a.path.localeCompare(b.path);
    });
    groups.push({
      group,
      label: GROUP_LABELS[group],
      files: groupFiles,
      hasCurrentStage: groupFiles.some((f) => f.isCurrentStage),
      defaultCollapsed: group === "tracking",
    });
  }
  return groups;
}
```

- [ ] **Step 4: 跑测试确认 pass**

Run: `cd frontend && node --test tests/fileTree.test.mjs`
Expected: PASS（6 个用例）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/utils/fileTree.js frontend/tests/fileTree.test.mjs
git commit -m "feat(r3): fileTree util (group + current-stage pin + Chinese labels)"
```

---

## Task 6: 前端 util — fileEditState（预览↔编辑状态机 + guardLeave）

**Files:**
- Create: `frontend/src/utils/fileEditState.js`
- Test: `frontend/tests/fileEditState.test.mjs`

纯 reducer 风格函数。`guardLeave` 把「能否离开当前编辑」抽成可测决策：`'allow'` / `'confirm'` / `'block'`。

- [ ] **Step 1: 写失败测试**

`frontend/tests/fileEditState.test.mjs`：

```javascript
import test from "node:test";
import assert from "node:assert/strict";

import {
  initialEditState, enterEdit, editDraft, cancelEdit, startSaving,
  saveSucceeded, saveFailed, reloadAfterConflict, guardLeave,
} from "../src/utils/fileEditState.js";

test("initial state is clean preview", () => {
  const s = initialEditState();
  assert.equal(s.mode, "preview");
  assert.equal(s.dirty, false);
  assert.equal(s.saving, false);
});

test("enterEdit loads content + base mtime in edit mode, clean", () => {
  const s = enterEdit(initialEditState(), { content: "正文", mtimeNs: "123" });
  assert.equal(s.mode, "edit");
  assert.equal(s.draft, "正文");
  assert.equal(s.baseMtimeNs, "123");
  assert.equal(s.dirty, false);
});

test("editDraft marks dirty and updates draft", () => {
  let s = enterEdit(initialEditState(), { content: "a", mtimeNs: "1" });
  s = editDraft(s, "a 改");
  assert.equal(s.draft, "a 改");
  assert.equal(s.dirty, true);
});

test("editDraft is a no-op outside edit mode", () => {
  const s = editDraft(initialEditState(), "x");
  assert.equal(s.mode, "preview");
});

test("cancelEdit returns clean preview", () => {
  let s = enterEdit(initialEditState(), { content: "a", mtimeNs: "1" });
  s = editDraft(s, "a 改");
  s = cancelEdit(s);
  assert.equal(s.mode, "preview");
  assert.equal(s.dirty, false);
});

test("save lifecycle: startSaving → saveSucceeded → clean preview", () => {
  let s = enterEdit(initialEditState(), { content: "a", mtimeNs: "1" });
  s = editDraft(s, "a 改");
  s = startSaving(s);
  assert.equal(s.saving, true);
  s = saveSucceeded(s, { mtimeNs: "2" });
  assert.equal(s.mode, "preview");
  assert.equal(s.dirty, false);
  assert.equal(s.saving, false);
});

test("saveFailed stays in edit, keeps draft + dirty, clears saving", () => {
  let s = enterEdit(initialEditState(), { content: "a", mtimeNs: "1" });
  s = editDraft(s, "a 改");
  s = startSaving(s);
  s = saveFailed(s);
  assert.equal(s.mode, "edit");
  assert.equal(s.draft, "a 改");
  assert.equal(s.dirty, true);
  assert.equal(s.saving, false);
});

test("reloadAfterConflict discards local edits, fresh base, clean edit", () => {
  let s = enterEdit(initialEditState(), { content: "a", mtimeNs: "1" });
  s = editDraft(s, "本地改动");
  s = reloadAfterConflict(s, { content: "服务端最新", mtimeNs: "9" });
  assert.equal(s.mode, "edit");
  assert.equal(s.draft, "服务端最新");
  assert.equal(s.baseMtimeNs, "9");
  assert.equal(s.dirty, false);
});

test("guardLeave: allow when preview/clean, confirm when dirty, block when saving", () => {
  assert.equal(guardLeave(initialEditState()), "allow");
  let s = enterEdit(initialEditState(), { content: "a", mtimeNs: "1" });
  assert.equal(guardLeave(s), "allow"); // edit 但未改
  s = editDraft(s, "改");
  assert.equal(guardLeave(s), "confirm");
  s = startSaving(s);
  assert.equal(guardLeave(s), "block");
});
```

- [ ] **Step 2: 跑测试确认 fail**

Run: `cd frontend && node --test tests/fileEditState.test.mjs`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 fileEditState.js**

`frontend/src/utils/fileEditState.js`：

```javascript
export function initialEditState() {
  return { mode: "preview", draft: "", baseMtimeNs: null, saving: false, dirty: false };
}

export function enterEdit(state, { content, mtimeNs }) {
  return { mode: "edit", draft: content, baseMtimeNs: mtimeNs, saving: false, dirty: false };
}

export function editDraft(state, nextDraft) {
  if (state.mode !== "edit") return state;
  return { ...state, draft: nextDraft, dirty: true };
}

export function cancelEdit() {
  return initialEditState();
}

export function startSaving(state) {
  if (state.mode !== "edit") return state;
  return { ...state, saving: true };
}

export function saveSucceeded() {
  return initialEditState();
}

export function saveFailed(state) {
  return { ...state, saving: false };
}

export function reloadAfterConflict(state, { content, mtimeNs }) {
  return { mode: "edit", draft: content, baseMtimeNs: mtimeNs, saving: false, dirty: false };
}

// guardLeave: decide what happens when the user tries to leave the current edit context.
//   'allow'   — no edit, or edit with no unsaved changes
//   'confirm' — dirty: caller should confirm discard before leaving
//   'block'   — a save is in flight: caller must refuse to leave
export function guardLeave(state) {
  if (state.saving) return "block";
  if (state.mode === "edit" && state.dirty) return "confirm";
  return "allow";
}
```

- [ ] **Step 4: 跑测试确认 pass**

Run: `cd frontend && node --test tests/fileEditState.test.mjs`
Expected: PASS（9 个用例）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/utils/fileEditState.js frontend/tests/fileEditState.test.mjs
git commit -m "feat(r3): fileEditState util (preview/edit state machine + guardLeave)"
```

---

## Task 7: 前端 — FilePreviewPanel 重做（文件树 + 双模式 + dirty 守卫）

**Files:**
- Rewrite: `frontend/src/components/FilePreviewPanel.jsx`
- Test: `frontend/tests/filePreviewPanel.source.test.mjs`

无 jsdom → source-guard 测试。组件 `forwardRef`，对外暴露 `confirmDiscardIfDirty`（供 WorkspacePanel/App 切 tab/切项目前调用）。

- [ ] **Step 1: 写 source-guard 失败测试**

`frontend/tests/filePreviewPanel.source.test.mjs`：

```javascript
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = () => readFileSync(path.join(__dirname, "../src/components/FilePreviewPanel.jsx"), "utf-8");

test("imports fileTree + fileEditState utils", () => {
  const s = src();
  assert.match(s, /from ['"]\.\.\/utils\/fileTree['"]/);
  assert.match(s, /buildFileTree/);
  assert.match(s, /from ['"]\.\.\/utils\/fileEditState['"]/);
  assert.match(s, /guardLeave/);
});

test("renders grouped tree via buildFileTree", () => {
  assert.match(src(), /buildFileTree\(files,\s*currentStage\)/);
});

test("edit button only for editable current file and only in preview", () => {
  const s = src();
  assert.match(s, /currentEditable/);
  assert.match(s, /!inEdit && currentEditable/);
});

test("edit mode renders a raw textarea bound to edit.draft", () => {
  const s = src();
  assert.match(s, /<textarea/);
  assert.match(s, /value=\{edit\.draft\}/);
  assert.match(s, /editDraft\(prev, e\.target\.value\)/);
});

test("save posts draft with base_mtime_ns and handles 409 reload", () => {
  const s = src();
  assert.match(s, /onSaveFile\(currentFile, snapshot\.draft, snapshot\.baseMtimeNs\)/);
  assert.match(s, /result\?\.conflict/);
  assert.match(s, /reloadAfterConflict/);
});

test("leave paths go through guardLeave + window.confirm; saving blocks", () => {
  const s = src();
  assert.match(s, /guardLeave\(editRef\.current\)/);
  assert.match(s, /window\.confirm/);
  assert.match(s, /正在保存/);
});

test("exposes confirmDiscardIfDirty + isEditing on the imperative handle", () => {
  const s = src();
  assert.match(s, /useImperativeHandle/);
  assert.match(s, /confirmDiscardIfDirty/);
  // isEditing lets WorkspacePanel.loadFiles skip content reload mid-edit (BLOCKER 3)
  assert.match(s, /isEditing:/);
});

test("edit textarea binds edit.draft, not the content prop (refresh can't clobber edits)", () => {
  const s = src();
  // 编辑态用独立 edit.draft 状态；refreshToken 刷新 content prop 不会覆盖正在编辑的内容。
  assert.match(s, /value=\{edit\.draft\}/);
});

test("registers best-effort beforeunload while dirty/saving", () => {
  const s = src();
  assert.match(s, /beforeunload/);
});

test("switching file is guarded before discarding edits", () => {
  const s = src();
  assert.match(s, /handleSelectFile/);
  assert.match(s, /if \(!confirmLeave\(\)\) return/);
});

test("shows review_stale advisory on the draft", () => {
  const s = src();
  assert.match(s, /reviewStale/);
  assert.match(s, /建议重新审查/);
});
```

- [ ] **Step 2: 跑测试确认 fail**

Run: `cd frontend && node --test tests/filePreviewPanel.source.test.mjs`
Expected: FAIL（旧组件无这些 wiring）

- [ ] **Step 3: 重写 FilePreviewPanel.jsx**

整文件替换为：

```jsx
import React, {
  forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState,
} from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import rehypeHighlight from 'rehype-highlight'
import rehypeRaw from 'rehype-raw'
import 'highlight.js/styles/github-dark.css'
import 'katex/dist/katex.min.css'
import { buildFileTree } from '../utils/fileTree'
import {
  initialEditState, enterEdit, editDraft, startSaving,
  saveSucceeded, saveFailed, reloadAfterConflict, guardLeave,
} from '../utils/fileEditState'
import { showError } from '../utils/toast'

const markdownComponents = {
  code: ({ inline, className, children, ...props }) => (
    inline ? (
      <code className="px-1.5 py-0.5 bg-[#1a1a2e] text-[#64ffda] rounded text-sm font-mono" {...props}>
        {children}
      </code>
    ) : (
      <code className={className} {...props}>{children}</code>
    )
  ),
  table: ({ children }) => (
    <div className="overflow-x-auto my-4">
      <table className="min-w-full border-collapse border border-[#2a2a4a]">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border border-[#2a2a4a] bg-[#1a1a2e] px-4 py-2 text-left text-[#64ffda] font-semibold">{children}</th>
  ),
  td: ({ children }) => (
    <td className="border border-[#2a2a4a] px-4 py-2 text-[#e2e2f0]">{children}</td>
  ),
  img: ({ src, alt }) => (
    <img src={src} alt={alt} className="max-w-full h-auto rounded-lg shadow-lg my-4" />
  ),
  a: ({ href, children }) => (
    <a href={href} className="text-[#64ffda] hover:text-[#52e0c2] underline" target="_blank" rel="noopener noreferrer">{children}</a>
  ),
  blockquote: ({ children }) => (
    <blockquote className="border-l-4 border-[#64ffda] pl-4 py-2 my-4 bg-[#1a1a2e] text-[#c8c8e0] italic">{children}</blockquote>
  ),
  h1: ({ children }) => <h1 className="text-3xl font-bold text-[#e2e2f0] mt-6 mb-4 pb-2 border-b border-[#2a2a4a]">{children}</h1>,
  h2: ({ children }) => <h2 className="text-2xl font-bold text-[#e2e2f0] mt-5 mb-3">{children}</h2>,
  h3: ({ children }) => <h3 className="text-xl font-semibold text-[#e2e2f0] mt-4 mb-2">{children}</h3>,
  p: ({ children }) => <p className="text-[#c8c8e0] leading-7 mb-4">{children}</p>,
  ul: ({ children }) => <ul className="list-disc list-inside text-[#c8c8e0] mb-4 space-y-2">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal list-inside text-[#c8c8e0] mb-4 space-y-2">{children}</ol>,
}

const DRAFT_PATH = 'content/report_draft_v1.md'

const FilePreviewPanel = forwardRef(function FilePreviewPanel({
  files = [],
  currentFile,
  content,
  currentStage = null,
  reviewStale = false,
  onSelectFile,
  onSaveFile,
  onReloadFile,
}, ref) {
  const [edit, setEdit] = useState(initialEditState)
  const [collapsed, setCollapsed] = useState({})
  const editRef = useRef(edit)
  editRef.current = edit

  const currentEditable = useMemo(
    () => Boolean(files.find((f) => f.path === currentFile)?.editable),
    [files, currentFile],
  )
  const groups = useMemo(() => buildFileTree(files, currentStage), [files, currentStage])
  const inEdit = edit.mode === 'edit'
  const isDraft = currentFile === DRAFT_PATH

  // 统一离开守卫：返回 true 表示可安全离开（清空编辑态由调用方处理）。
  const confirmLeave = useCallback(() => {
    const decision = guardLeave(editRef.current)
    if (decision === 'allow') return true
    if (decision === 'block') {
      showError('正在保存，请稍候')
      return false
    }
    // v1 二选一确认（spec §7.2 v1：放弃/取消；三按钮「保存/放弃/取消」留 v2）。
    // 文案点明「取消＝留下」，保证不丢工作：用户可返回继续编辑或先点保存。
    return window.confirm('当前文件有未保存的修改。确定放弃修改并离开？\n（点「取消」可返回继续编辑，或先点「保存」）')
  }, [])

  useImperativeHandle(ref, () => ({
    // WorkspacePanel/App 切 tab / 切项目前调用；false 中止切换。
    confirmDiscardIfDirty: () => {
      const ok = confirmLeave()
      if (ok) setEdit(initialEditState())
      return ok
    },
    // WorkspacePanel.loadFiles 用：编辑态下 refreshToken 刷新只更新文件列表元数据、不重载当前文件 content。
    isEditing: () => editRef.current.mode === 'edit',
  }), [confirmLeave])

  // best-effort：整页刷新 / PyWebView 关窗时，dirty 或 saving 则拦截。
  useEffect(() => {
    const onBeforeUnload = (e) => {
      if (guardLeave(editRef.current) !== 'allow') {
        e.preventDefault()
        e.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [])

  // 切换预览文件本身是一条离开路径：先守卫再丢弃编辑态。
  const handleSelectFile = useCallback((path) => {
    if (path === currentFile) return
    if (!confirmLeave()) return
    setEdit(initialEditState())
    onSelectFile?.(path)
  }, [currentFile, confirmLeave, onSelectFile])

  const handleEnterEdit = useCallback(async () => {
    try {
      const fresh = await onReloadFile(currentFile) // 重新取最新 {content, mtimeNs} 作 base
      setEdit((prev) => enterEdit(prev, { content: fresh.content, mtimeNs: fresh.mtimeNs }))
    } catch (error) {
      showError('无法进入编辑：' + (error?.message || '读取失败'))
    }
  }, [currentFile, onReloadFile])

  const handleCancel = useCallback(() => {
    if (!confirmLeave()) return
    setEdit(initialEditState())
  }, [confirmLeave])

  const handleSave = useCallback(async () => {
    const snapshot = editRef.current
    if (snapshot.mode !== 'edit' || snapshot.saving) return
    setEdit((prev) => startSaving(prev))
    const result = await onSaveFile(currentFile, snapshot.draft, snapshot.baseMtimeNs)
    if (result?.ok) {
      setEdit((prev) => saveSucceeded(prev, { mtimeNs: result.mtimeNs }))
      return
    }
    if (result?.conflict) {
      setEdit((prev) => saveFailed(prev))
      const reload = window.confirm('文件已被更新（可能是 AI 刚写过），加载最新内容？本地修改将丢弃。')
      if (reload) {
        try {
          const fresh = await onReloadFile(currentFile)
          setEdit((prev) => reloadAfterConflict(prev, { content: fresh.content, mtimeNs: fresh.mtimeNs }))
        } catch (error) {
          showError('重新加载失败：' + (error?.message || ''))
        }
      }
      return
    }
    setEdit((prev) => saveFailed(prev))
    showError('保存失败：' + (result?.error || '请重试'))
  }, [currentFile, onSaveFile, onReloadFile])

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* 文件树（分组 + 当前阶段置顶 + 中文名） */}
      <div className="border-b border-[#2a2a4a] max-h-64 overflow-y-auto text-sm">
        {groups.map((group) => {
          const isCollapsed = collapsed[group.group] ?? group.defaultCollapsed
          return (
            <div key={group.group}>
              <div
                onClick={() => setCollapsed((c) => ({ ...c, [group.group]: !isCollapsed }))}
                className="px-3 py-1.5 cursor-pointer text-xs text-[#8f93c9] tracking-wide hover:bg-[#1c1c38] flex items-center justify-between"
              >
                <span>{group.label}</span>
                <span>{isCollapsed ? '▸' : '▾'}</span>
              </div>
              {!isCollapsed && group.files.map((file) => (
                <div
                  key={file.path}
                  onClick={() => handleSelectFile(file.path)}
                  className={`px-4 py-2 cursor-pointer text-sm flex items-center gap-2 ${
                    currentFile === file.path ? 'bg-[#1e1e4a] text-blue-400' : 'hover:bg-[#222244] text-[#c8c8e0]'
                  } ${file.isCurrentStage ? 'border-l-2 border-[#64ffda]' : 'border-l-2 border-transparent'}`}
                >
                  <span className="truncate flex-1">{file.label}</span>
                  {file.isCurrentStage && <span className="text-[10px] text-[#64ffda]">当前</span>}
                  {!file.editable && <span className="text-[10px] text-[#6a6a8a]">只读</span>}
                </div>
              ))}
            </div>
          )
        })}
      </div>

      {/* review_stale advisory（仅正文页显示） */}
      {isDraft && reviewStale && (
        <div className="px-4 py-2 text-xs text-[#c8a060] bg-[#2a1e10] border-b border-[#5a3a10]" role="note">
          正文已改动，建议重新审查（独立审查 / AI 味自查报告可能已过期）。
        </div>
      )}

      {/* 工具栏 */}
      <div className="px-4 py-2 border-b border-[#2a2a4a] flex items-center gap-2 min-h-[2.75rem]">
        {!inEdit && currentEditable && (
          <button onClick={handleEnterEdit} className="px-3 py-1 rounded text-xs bg-[#28366b] text-white">编辑</button>
        )}
        {inEdit && (
          <>
            <button onClick={handleSave} disabled={edit.saving} className="px-3 py-1 rounded text-xs bg-[#2f7d52] text-white disabled:opacity-50">
              {edit.saving ? '保存中…' : '保存'}
            </button>
            <button onClick={handleCancel} disabled={edit.saving} className="px-3 py-1 rounded text-xs bg-[#15162d] text-[#8f93c9] disabled:opacity-50">
              取消
            </button>
          </>
        )}
      </div>

      {/* 正文区：编辑态 textarea / 预览态 markdown */}
      <div className="flex-1 overflow-y-auto p-6 bg-[#0d0d1a]">
        {inEdit ? (
          <textarea
            value={edit.draft}
            onChange={(e) => setEdit((prev) => editDraft(prev, e.target.value))}
            className="w-full h-full min-h-[20rem] bg-[#0d0d1a] text-[#e2e2f0] font-mono text-sm leading-6 outline-none resize-none"
            spellCheck={false}
          />
        ) : (
          <div className="markdown-body max-w-none selectable-content">
            <ReactMarkdown
              remarkPlugins={[remarkGfm, remarkMath]}
              rehypePlugins={[rehypeKatex, rehypeHighlight, rehypeRaw]}
              components={markdownComponents}
            >
              {content}
            </ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  )
})

export default FilePreviewPanel
```

- [ ] **Step 4: 跑测试确认 pass**

Run: `cd frontend && node --test tests/filePreviewPanel.source.test.mjs`
Expected: PASS（全部用例）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/FilePreviewPanel.jsx frontend/tests/filePreviewPanel.source.test.mjs
git commit -m "feat(r3): FilePreviewPanel grouped tree + preview/edit dual mode + dirty guard"
```

---

## Task 8: 前端 — WorkspacePanel 保存/重载/守卫 wiring + App 切项目守卫

**Files:**
- Modify: `frontend/src/components/WorkspacePanel.jsx`
- Modify: `frontend/src/App.jsx`
- Test: `frontend/tests/workspacePanel.source.test.mjs`（新建）

- [ ] **Step 1: 写 source-guard 失败测试**

`frontend/tests/workspacePanel.source.test.mjs`：

```javascript
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const wsSrc = () => readFileSync(path.join(__dirname, "../src/components/WorkspacePanel.jsx"), "utf-8");
const appSrc = () => readFileSync(path.join(__dirname, "../src/App.jsx"), "utf-8");

test("WorkspacePanel passes structured files straight through (no name/path remap)", () => {
  const s = wsSrc();
  assert.match(s, /setFiles\(res\.data\.files\)/);
  // 旧的 path.split('/').pop().replace('.md','') 映射已删除
  assert.doesNotMatch(s, /\.split\(['"]\/['"]\)\.pop\(\)\.replace/);
});

test("WorkspacePanel has handleSaveFile posting content + base_mtime_ns", () => {
  const s = wsSrc();
  assert.match(s, /const handleSaveFile/);
  assert.match(s, /base_mtime_ns:\s*baseMtimeNs/);
  assert.match(s, /status\s*===?\s*409|status === 409/);
  assert.match(s, /conflict:\s*true/);
  // R2 BLOCKER：成功后立即 setContent（不依赖被 isEditing early-return 跳过的 loadFiles 刷新）
  assert.match(s, /setContent\(nextContent\)/);
});

test("WorkspacePanel has reloadFile re-GETting content + mtime", () => {
  const s = wsSrc();
  assert.match(s, /const reloadFile/);
  assert.match(s, /mtimeNs:\s*res\.data\.mtime_ns/);
});

test("WorkspacePanel reloadFile guards stale project responses (NIT 3)", () => {
  const s = wsSrc();
  assert.match(s, /const reloadFile/);
  assert.match(s, /project switched/);
});

test("WorkspacePanel loadFiles skips content reload while editing (BLOCKER 3)", () => {
  const s = wsSrc();
  assert.match(s, /filePreviewRef\.current\?\.isEditing\?\.\(\)/);
});

test("WorkspacePanel guards tab switch via filePreviewRef.confirmDiscardIfDirty", () => {
  const s = wsSrc();
  assert.match(s, /filePreviewRef/);
  assert.match(s, /confirmDiscardIfDirty/);
  assert.match(s, /const handleTabClick/);
});

test("WorkspacePanel is forwardRef exposing confirmDiscardIfDirty", () => {
  const s = wsSrc();
  assert.match(s, /forwardRef/);
  assert.match(s, /useImperativeHandle/);
});

test("WorkspacePanel passes review_stale + currentStage to FilePreviewPanel", () => {
  const s = wsSrc();
  assert.match(s, /reviewStale=\{Boolean\(workspace\?\.flags\?\.review_stale\)\}/);
  assert.match(s, /currentStage=\{workspace\?\.stage_code\}/);
  assert.match(s, /onSaveFile=\{handleSaveFile\}/);
  assert.match(s, /onReloadFile=\{reloadFile\}/);
});

test("App guards project switch via workspacePanelRef before switching", () => {
  const s = appSrc();
  assert.match(s, /workspacePanelRef/);
  assert.match(s, /confirmDiscardIfDirty/);
  assert.match(s, /ref=\{workspacePanelRef\}/);
});

test("App guards workspace-panel toggle (hide unmounts editor) before hiding (R2 BLOCKER)", () => {
  const s = appSrc();
  assert.match(s, /handleToggleWorkspacePanel/);
  assert.match(s, /onToggleWorkspacePanel=\{handleToggleWorkspacePanel\}/);
  // 守卫只在「当前显示且要隐藏」时拦截
  assert.match(s, /showWorkspacePanel && !\(workspacePanelRef\.current\?\.confirmDiscardIfDirty/);
});
```

- [ ] **Step 2: 跑测试确认 fail**

Run: `cd frontend && node --test tests/workspacePanel.source.test.mjs`
Expected: FAIL

- [ ] **Step 3: 改 WorkspacePanel.jsx**

3a. 顶部 import 改为 `forwardRef`/`useImperativeHandle` 可用，并去掉不再用的 `orderPreviewFiles`：

把第 1 行与第 9 行：

```jsx
import React, { useCallback, useEffect, useRef, useState } from 'react'
...
import { getDefaultPreviewFile, orderPreviewFiles } from '../utils/workspaceFiles'
```

改为：

```jsx
import React, { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
...
import { getDefaultPreviewFile } from '../utils/workspaceFiles'
```

3b. 函数签名 `export default function WorkspacePanel({...}) {` 改成 `forwardRef`：

把：

```jsx
export default function WorkspacePanel({
  projectId,
  project,
  workspace,
  materials,
  refreshToken,
  onMaterialDeleted,
  onProjectMutated,
  onCheckpointSet,
  onInsertPrompt,
  onTriggerSystemTurn,
  onDropPendingReviewTriggers,
}) {
  const [activeTab, setActiveTab] = useState('stage')
```

改为：

```jsx
const WorkspacePanel = forwardRef(function WorkspacePanel({
  projectId,
  project,
  workspace,
  materials,
  refreshToken,
  onMaterialDeleted,
  onProjectMutated,
  onCheckpointSet,
  onInsertPrompt,
  onTriggerSystemTurn,
  onDropPendingReviewTriggers,
}, ref) {
  const [activeTab, setActiveTab] = useState('stage')
  const filePreviewRef = useRef(null)

  useImperativeHandle(ref, () => ({
    // App 切项目前调用；委派给 FilePreviewPanel 的 dirty 守卫。false → 中止切项目。
    confirmDiscardIfDirty: () => filePreviewRef.current?.confirmDiscardIfDirty?.() ?? true,
  }), [])

  const handleTabClick = useCallback((next) => {
    // 离开「文件」tab 是一条离开路径：dirty 时先守卫。
    if (activeTab === 'files' && next !== 'files') {
      if (!(filePreviewRef.current?.confirmDiscardIfDirty?.() ?? true)) return
    }
    setActiveTab(next)
  }, [activeTab])
```

3c. `loadFiles` 内把 `{name, path}` 映射改为直接用结构化数组，并**在编辑态下跳过 content 重载**（BLOCKER 3：refreshToken 刷新只更新文件列表元数据，不覆盖正在编辑的文件）。把（含其后的 `if (nextDefault)` 块）：

```jsx
      const orderedPaths = orderPreviewFiles(res.data.files)
      const fileList = orderedPaths.map(path => ({
        name: path.split('/').pop().replace('.md', ''),
        path,
      }))
      setFiles(fileList)

      const nextDefault = fileList.find(file => file.path === currentFile)?.path
        || getDefaultPreviewFile(orderedPaths)

      if (nextDefault) {
        await loadFile(nextDefault, requestProject)
      } else {
        setContent('')
      }
```

改为：

```jsx
      const fileList = res.data.files // 结构化：{path, group, stage, editable, mtime_ns}
      setFiles(fileList)

      // BLOCKER 3：编辑态下只刷新上面的文件列表元数据，绝不重载当前文件 content
      //（否则覆盖编辑器底下的 preview，且 currentFile 变更会与编辑态 desync）。
      if (filePreviewRef.current?.isEditing?.()) {
        return
      }

      const paths = fileList.map(file => file.path)
      const nextDefault = paths.includes(currentFile)
        ? currentFile
        : getDefaultPreviewFile(paths)

      if (nextDefault) {
        await loadFile(nextDefault, requestProject)
      } else {
        setContent('')
      }
```

3d. 在 `exportDraft` 之前（或任意 callback 区）加 `handleSaveFile` + `reloadFile`：

```jsx
  const handleSaveFile = useCallback(async (filePath, nextContent, baseMtimeNs) => {
    const requestProject = projectId
    if (!requestProject) return { ok: false, error: '无项目' }
    try {
      const res = await axios.post(
        `/api/projects/${encodeURIComponent(requestProject)}/files/${filePath}`,
        { content: nextContent, base_mtime_ns: baseMtimeNs },
      )
      if (shouldApplyProjectResponse({
        requestProject,
        activeProject: activeProjectRef.current,
      })) {
        // R2 BLOCKER：成功后立即把预览 content 设为刚保存的内容——不能依赖 loadFiles 刷新，
        // 因为保存瞬间 FilePreviewPanel 仍在编辑态，loadFiles 的 isEditing early-return 会跳过 content 重载，
        // 导致回预览态后显示旧正文。
        setContent(nextContent)
        onProjectMutated?.() // 触发 workspace 刷新（review_stale 可能翻转）
      }
      return { ok: true, mtimeNs: res.data.mtime_ns }
    } catch (error) {
      if (error.response?.status === 409) return { ok: false, conflict: true }
      return { ok: false, error: error.response?.data?.detail || error.message }
    }
  }, [projectId, onProjectMutated])

  const reloadFile = useCallback(async (filePath) => {
    const requestProject = projectId
    const res = await axios.get(
      `/api/projects/${encodeURIComponent(requestProject)}/files/${filePath}`,
    )
    // NIT 3：点「编辑」后立刻切项目时，旧项目 GET 不得回填到新项目面板。
    if (!shouldApplyProjectResponse({
      requestProject,
      activeProject: activeProjectRef.current,
    })) {
      throw new Error('project switched') // FilePreviewPanel catch → 不进入编辑态
    }
    return { content: res.data.content, mtimeNs: res.data.mtime_ns }
  }, [projectId])
```

3e. 三个 tab 按钮的 `onClick={() => setActiveTab('...')}` 改成 `onClick={() => handleTabClick('...')}`（共 3 处：`'stage'` / `'files'` / `'materials'`）。

3f. `FilePreviewPanel` 用法（当前 `:297-302`）替换为带 ref + 新 props：

```jsx
        <FilePreviewPanel
          ref={filePreviewRef}
          files={files}
          currentFile={currentFile}
          content={content}
          currentStage={workspace?.stage_code}
          reviewStale={Boolean(workspace?.flags?.review_stale)}
          onSelectFile={loadFile}
          onSaveFile={handleSaveFile}
          onReloadFile={reloadFile}
        />
```

3g. 文件末尾把：

```jsx
}
```

（`WorkspacePanel` 函数体结束的 `}`）改为：

```jsx
})

export default WorkspacePanel
```

> 注意：原文件结尾是 `}`（函数声明）。改 `forwardRef` 后必须以 `})` 闭合并在其后 `export default WorkspacePanel`。删除原 `export default function` 写法已在 3b 完成。

- [ ] **Step 4: 改 App.jsx 切项目守卫**

`frontend/src/App.jsx`：

4a. 在 `const chatPanelRef = useRef(null)`（`:39`）之后加：

```jsx
  const workspacePanelRef = useRef(null)
```

4b. `handleSelectProject`（`:178`）加守卫：

```jsx
  const handleSelectProject = (project) => {
    if (isSameProjectSelection(currentProjectId, project?.id || null)) {
      return
    }
    if (!(workspacePanelRef.current?.confirmDiscardIfDirty?.() ?? true)) {
      return // 当前文件有未保存修改且用户取消离开 → 不切项目
    }
    setWorkspace(null)
    setMaterials([])
    setCurrentProjectId(project?.id || null)
    setCurrentProject(project || null)
  }
```

4c. `<WorkspacePanel`（`:245`）加 `ref`：

```jsx
          <WorkspacePanel
            ref={workspacePanelRef}
            projectId={currentProjectId}
```

4d. **隐藏工作区面板也是一条离开路径**（R2 BLOCKER：toggle 隐藏会 unmount `WorkspacePanel/FilePreviewPanel`，丢编辑态）。加守卫并改 `ChatPanel` 的 `onToggleWorkspacePanel`。在 `handleSelectProject` 附近加：

```jsx
  const handleToggleWorkspacePanel = () => {
    // 当前显示 + 编辑态脏 + 用户取消 → 不隐藏（隐藏会 unmount 编辑器丢改动）
    if (showWorkspacePanel && !(workspacePanelRef.current?.confirmDiscardIfDirty?.() ?? true)) {
      return
    }
    setShowWorkspacePanel(!showWorkspacePanel)
  }
```

把 `ChatPanel` 的（`:240`）：

```jsx
          onToggleWorkspacePanel={() => setShowWorkspacePanel(!showWorkspacePanel)}
```

改为：

```jsx
          onToggleWorkspacePanel={handleToggleWorkspacePanel}
```

- [ ] **Step 5: 跑 source-guard + 全前端测试确认 pass**

Run: `cd frontend && node --test tests/workspacePanel.source.test.mjs`
Expected: PASS（全部用例）

Run（回归既有 source-guard，确认没破坏 batch-1 断言）: `cd frontend && node --test tests/independentReviewDrawer.source.test.mjs`
Expected: PASS

> 若 `independentReviewDrawer.source.test.mjs` 里 `WorkspacePanel` 相关断言因 forwardRef 改写而失配，按其断言文本微调（它们查的是 `onIndependentReviewCompleted`/`runLintReport`/`runIndependentReview` 区块，本计划未动这些函数，预期不受影响）。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/WorkspacePanel.jsx frontend/src/App.jsx frontend/tests/workspacePanel.source.test.mjs
git commit -m "feat(r3): WorkspacePanel save/reload + tab/project dirty guards wiring"
```

---

## Task 9: 回归 + cutover report + 文档同步

**Files:**
- Create: `docs/superpowers/cutover_report_<YYYY-MM-DD>_r3-file-tree-editing.md`
- Modify: `docs/current-worklist.md`
- Modify: `consulting-report-agent/CLAUDE.md`

- [ ] **Step 1: 后端定向回归**

Run:
```bash
.venv\Scripts\python -m pytest tests/test_skill_engine.py tests/test_main_api.py tests/test_independent_review.py tests/test_lint_report.py -v
```
Expected: PASS（全绿）。

> **不要**跑全量 `tests/test_chat_runtime.py`（约 22 分钟/趟，且本计划未动 chat 核心路径——POST/files 只复用 `_get_project_request_lock` accessor，未改流式逻辑）。如对锁交互有疑，单点跑 `-k request_lock` 即可。

- [ ] **Step 2: 前端全量回归**

Run:
```bash
cd frontend && node --test tests/
```
Expected: PASS（含新增 `fileTree` / `fileEditState` / `filePreviewPanel.source` / `workspacePanel.source`）。

- [ ] **Step 3: 构建冒烟（确认无语法/打包回归）**

Run:
```bash
cd frontend && npm run build
```
Expected: 构建成功，无新 error（既有 chunk warning 属已知小债，可忽略）。

- [ ] **Step 4: 写 cutover report**

新建 `docs/superpowers/cutover_report_<今天日期>_r3-file-tree-editing.md`，内容覆盖：

```markdown
# Cutover Report — R3 工作区文件栏重做 + 预览框可编辑

- 日期：<YYYY-MM-DD>
- Spec：docs/superpowers/specs/2026-06-08-workspace-file-tree-and-editing-design.md
- Plan：docs/superpowers/plans/2026-06-08-workspace-file-tree-and-editing.md

## 交付范围（①+②）
- 文件栏：后端 `list_workspace_files` 给结构化语义（group/stage/editable/mtime_ns），前端 `fileTree` 分组 + 中文名 + 当前阶段置顶；tracking 折叠置底。
- 预览框双模式：8 个白名单文件支持「编辑（raw textarea）→ 保存」；`fileEditState` 状态机 + dirty `guardLeave` 覆盖 切文件/切 tab/切项目/刷新关窗/saving 五条离开路径。
- 用户写接口：`POST /files/{path}`（`validate_user_write` 白名单 + per-project 锁内 mtime CAS + `os.replace` 原子写 + 异常分流 400/403/404/409/422）。
- D6：`review_stale` advisory flag（两份**有效**报告 + draft 比旧报告新即标，不 gate 在 review_passed_at）。

## 对 spec 的已核验偏离 / 强化（plan codex R1）
- §6.1「GET 读在锁内」→ 实测 `chat_stream` 整轮持锁，GET 进锁会冻结预览整轮；改为 **GET 不持锁 + stat-before-read**（最坏=保存时安全 409），POST 仍持锁。
- **`write_file` 原子化 + canonical draft 直写归一**：`write_file` 改 temp + `os.replace`；canonical draft `edit_file`（`chat.py:4238`）原本直接 `draft_path.write_text` 绕过它，改为走 `write_file`——所有 AI 写路径单点原子化、消除 torn read，使 GET 不持锁成立（顺带 crash-safety）。
- §5.4 review_stale **gate 在有效报告**（`_has_effective_review_reports`），不只判存在——避开 create_project scaffold 的 independent-review/lint-report 模板误判。
- §7.2 脏离开确认 **v1 降级为二选一**（放弃/取消，文案点明取消＝留下不丢工作）；三按钮「保存/放弃/取消」留 v2。

## 测试
- 后端：test_skill_engine（语义/白名单/validate_user_write/review_stale）、test_main_api（R3FileApiTests：GET/POST 全状态码 + 锁串行化 + mtime_ns str + 拒 number）。
- 前端：fileTree / fileEditState 纯函数；filePreviewPanel / workspacePanel source-guard。

## 未做（记后续）
- R3③：图片附件按 model 分流、新建项目表单整理、project-overview.md 结构化编辑。
- v2 富文本编辑器；review_stale 硬门禁；全局 allow_origins 收紧 / 写接口本地 token（D7）。
- 用户侧 GUI E2E 手测（非阻塞）。
```

- [ ] **Step 5: 更新 worklist**

`docs/current-worklist.md`：把 R3 条目状态从「spec 定稿待 plan」改为「已实施待 review/E2E」，顶部「最后更新」补一句 R3 实施完成 + 指向 cutover report。

- [ ] **Step 6: 更新 CLAUDE.md 路由/架构**

`consulting-report-agent/CLAUDE.md`：在合适位置（如「关键数据边界」或新增「R3 工作区文件编辑」小节）补：

```markdown
## S3-R3 工作区文件编辑（2026-06-08）

文件「语义」由 `backend/skill.py` 单一真值源给出，前端只做中文文案 + 渲染：

- `SkillEngine.FILE_SEMANTICS`（完整 posix 路径 → group/stage）、`USER_EDITABLE_FILES`（8 文件白名单，默认 deny）、`is_user_editable` / `get_file_semantics` / `list_workspace_files`。
- `validate_user_write` 是**独立于** `validate_plan_write` 的用户写门禁（白名单制，天然拒审查报告/追踪文件/退役/checkpoint）；穿越→ValueError(400)、非白名单→PermissionError(403)。
- 写接口 `POST /api/projects/{id}/files/{path}` `{content, base_mtime_ns}`：全段持 `_get_project_request_lock`（与聊天同锁）→ mtime CAS（不匹配 `StaleFileError`→409）→ 同目录 temp + `os.replace` 原子写；`base_mtime_ns` 全程 opaque str（pydantic 拒 number→422）。
- 读接口 `GET /files/{path}` 返回 `{content, mtime_ns, editable}`，**不持锁**（chat_stream 整轮持锁，读进锁会冻结预览）：`read_file_with_mtime` 先 stat 再 read。AI 写**可编辑**正式文件（plan 内容文件 + canonical draft `edit_file`）全部经原子 `write_file`（temp + `os.replace`），故无锁读可编辑文件不会读到半截、最坏=保存安全 409（只读追踪文件后端直写，极端下预览瞬时错乱、刷新自愈，不可编辑不入 CAS）。
- `get_workspace_summary().flags.review_stale`（D6 advisory）：两份审查报告**有效**（`_has_effective_review_reports`，非 scaffold 模板）且 `draft_mtime > min(report mtimes)` 即标，**不** gate 在 `review_passed_at`；不硬阻 S6/S7。
- 前端：`utils/fileTree.js`（分组/置顶/中文名）、`utils/fileEditState.js`（双模式状态机 + guardLeave）、`FilePreviewPanel.jsx`（forwardRef 暴露 `confirmDiscardIfDirty`）、`WorkspacePanel.jsx`/`App.jsx`（切 tab/切项目 dirty 守卫）。
- 回归：`tests/test_skill_engine.py`、`tests/test_main_api.py::R3FileApiTests`；前端 `fileTree`/`fileEditState`/`filePreviewPanel.source`/`workspacePanel.source`。
```

- [ ] **Step 7: Commit**

```bash
git add docs/superpowers/cutover_report_*_r3-file-tree-editing.md docs/current-worklist.md CLAUDE.md
git commit -m "docs(r3): cutover report + worklist + CLAUDE.md sync (file tree & editing)"
```

---

## 实施后：codex 三轨 review

按项目规约（`CodeProject/CLAUDE.md`「子代理派活规则」）：每 task commit 后或全部完成后，派 **codex（codex-server MCP，`sandbox:"read-only"`）走 spec + quality 双轨独立 review**，外加一轮对抗式红队（首轮 prompt 别喂对策），"审→修→再审"直到 `APPROVED`。重点盯：

1. `validate_user_write` 白名单是否真的拒了所有非白名单文件（尤其 independent-review/lint-report/stage-gates/checkpoint）。
2. POST 锁 + CAS 是否对「AI 写 vs 用户保存」真互斥（red-team：构造并发）。
3. GET 不持锁的 stat-before-read 取舍是否引入静默覆盖（应只产生安全 409）。
4. 前端 dirty `guardLeave` 是否覆盖 spec §7.2 全部离开路径。
5. `mtime_ns` 是否全程 opaque str（前后端均不转 Number）。

红队发现先过「项目现实闸门」（单机/单用户/输入可信）再采纳，避免过度设计。

---

## Self-Review（撰写者已核对）

- **codex plan review R2 已闭环**（对抗式复审 3 BLOCKER + 3 doc-NIT 全采纳）：① canonical draft `edit_file` 直写（`chat.py:4238`）改走原子 `write_file`——这是 R1 原子化漏掉的最高频文件，补后 GET 不持锁才真成立（Task 2 + 源码守卫测试）；② `handleSaveFile` 成功后 `setContent(nextContent)`，修「loadFiles 编辑态 early-return 跳过 content 刷新 → 回预览显示旧正文」竞态（Task 8）；③ 工作区面板 toggle 隐藏会 unmount 编辑器丢改动 → App `handleToggleWorkspacePanel` 加 dirty 守卫（Task 8）；doc-NIT：spec §4/§8.1 + cutover/CLAUDE 措辞同步「有效报告 / GET 不持锁」、`os.replace` 失败走 500 兜底已注明。
- **codex plan review R1 已闭环**（4 BLOCKER + 3 NIT 全采纳，均经现实闸门 + 事实核验）：① review_stale gate 在 `_has_effective_review_reports`（避开 scaffold 模板误判，Task 4）；② `write_file` 原子化（temp + os.replace，单点覆盖全部 AI 写路径，使 GET 不持锁的 torn-read 论证成立，Task 2）；③ `loadFiles` 编辑态跳过 content 重载 + `isEditing()`（Task 7/8）；④ 脏离开确认 v1 二选一文案点明「取消＝留下」+ spec §7.2 降级（3 按钮留 v2）；NIT：deny 矩阵扩全（Task 1）、draft 介于两报告之间用例（Task 4）、`reloadFile` stale 响应守卫（Task 8）。
- **Spec 覆盖**：§5.1/5.2 权限边界→Task 1/3；§5.3 语义表→Task 1（FILE_SEMANTICS 全 16 行）；§5.4/D6 review_stale→Task 4；§6.1 GET 改造→Task 2（GET 不持锁的偏离已显式记录）；§6.2 POST→Task 3；§7.1 文件树→Task 5/7；§7.2 双模式+guardLeave 全离开路径→Task 6/7/8；§8 测试矩阵→各 Task 测试步；§9 threat model→白名单限面（Task 1/3）。
- **无占位符**：所有 step 含真实代码/命令/预期输出。
- **类型/签名一致**：`mtime_ns` 全程 str；`validate_user_write` 返回 canonical；`guardLeave` 返回 `'allow'|'confirm'|'block'` 在 util 与组件一致；`confirmDiscardIfDirty` 在 FilePreviewPanel→WorkspacePanel→App 三层同名贯通；`onSaveFile`/`onReloadFile` 入参签名前后端一致。
- **顺序**：后端先于前端、只读（Task 1/2）先于可写（Task 3）、纯函数（5/6）先于组件（7/8），每步独立可测可 commit。
```
