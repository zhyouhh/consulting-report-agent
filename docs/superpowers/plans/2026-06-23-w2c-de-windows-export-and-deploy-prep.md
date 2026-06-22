# W2-C 去 Windows 化导出 + web 下载 + 部署前置代码 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让「导出可审草稿」在 Linux/mac 上工作（删 PowerShell、Python 直调 pandoc、原子发布、web 用户真能下载 docx），并补齐部署前置代码（入口 env 化 + 反代真实 IP + SSE 心跳），最后收口 N6 F2 死解析器——使 W2-C 部署后同事能端到端写作+导出。

**Architecture:** Part A 把 `report_tools.export_reviewable_draft` 改为纯 Python 调 pandoc（解析守卫 frozen/Windows、temp+os.replace 原子发布），导出端点改非阻塞线程池执行 + 新增 `GET .../export-draft/download` 流式回 docx；前端 `exportDraft` 改按 status 判成败 + 触发浏览器下载。入口 `run_web.py` host/port env 化 + uvicorn `proxy_headers`。SSE 防 CF 断流：审查流周期心跳、聊天流首字节心跳。Part B 删 N6 遗留的 4 个 legacy 解析器并给无-converter 测试注入假 converter。**部署 runbook（spec §5 / Part C）不在本 plan，交互式执行。**

**Tech Stack:** Python 3.12 / FastAPI / Starlette `StreamingResponse` / pandoc CLI / pytest(unittest) / React + Node 原生 `node:test`。

**spec（真值源）：** `docs/superpowers/specs/2026-06-23-w2c-deploy-and-de-windows-design.md`（Codex 5 轮 APPROVED）。每 commit 后按项目纪律走 Codex spec/quality 双轨独立 review。

**全局约束（每个 task 都遵守）：**
- DeepSeek 官渠兼容：本 plan **不碰** `chat.py`/`independent_review.py` 的 provider message / tool-call / `reasoning_content` / `tool_choice` 序列化。心跳只在 `main.py` SSE 帧层注入。回归须保 `tests/test_chat_runtime.py` DeepSeek 用例 + `compat_helpers_match` 绿。
- 导出读草稿 **不取** per-project request lock（spec §3.6：`chat_stream` 整轮持 RLock，取锁会冻结导出；R3 原子写保证锁外读无 torn read）。
- mac realpath 4 个已知失败用例非本 plan 回归（Windows 绿）。
- 后端测试：`.venv/bin/python -m pytest`；前端：`cd frontend && node --test tests/<file>.test.mjs`。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `backend/report_tools.py` | pandoc 解析 + 原子发布导出（无 PowerShell） | 重写 |
| `backend/main.py` | 导出端点非阻塞化 + 下载端点 + SSE 心跳 | 修改 |
| `run_web.py` | host/port env 化 + uvicorn proxy_headers | 修改 |
| `backend/skill.py` | 删 4 个 legacy 解析器 + `_converter_read_document` 无 converter 改报错；删 `get_script_path`（导出唯一消费者） | 修改 |
| `skill/scripts/export_draft.ps1` / `export_draft.sh` | 退役 | 删除 |
| `skill/modules/final-delivery.md` / `quality-review.md` | 去脚本引用、改应用导出操作描述 | 修改 |
| `frontend/src/components/WorkspacePanel.jsx` | `exportDraft` 按 status 判成败 + 触发下载 | 修改 |
| `tests/test_report_tools.py` | 改 mock pandoc subprocess + 解析守卫 + 原子发布 + 不阻塞 loop | 重写 |
| `tests/test_main_api.py` | 导出端点签名 + 下载端点（属主/非属主/未生成/穿越/FileResponse 非 catch-all） | 修改 |
| `tests/test_skill_assets.py` | 删 PowerShell 专属用例 + 加 source-guard（skill/ 无 `scripts/export_draft`） | 修改 |
| `tests/test_skill_engine.py` / `tests/test_workspace_materials.py` | F2：无-converter 读取改注入假 converter | 修改 |
| `frontend/tests/*.test.mjs` | exportDraft status/下载 + SSE 心跳容忍 | 修改/新增 |

---

## Part A — 去 Windows 化导出 + web 下载 + 原子发布 + 入口 + 心跳

### Task 1: `report_tools.py` 重写——pandoc 解析守卫 + 原子发布

**Files:**
- Rewrite: `backend/report_tools.py`
- Test: `tests/test_report_tools.py`

- [ ] **Step 1: 写失败测试**（替换 `tests/test_report_tools.py` 全文）

```python
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from backend import report_tools


class ResolvePandocTests(unittest.TestCase):
    @mock.patch("backend.report_tools.shutil.which", return_value="/usr/bin/pandoc")
    @mock.patch("backend.report_tools.sys")
    def test_non_windows_non_frozen_uses_system_pandoc_even_if_root_exe_exists(self, m_sys, m_which):
        # 守卫：Linux/mac 非打包态，即便仓库根有 pandoc.exe 也绝不试它。
        m_sys.platform = "linux"
        del m_sys.frozen  # getattr(sys, "frozen", False) → False
        self.assertEqual(report_tools._resolve_pandoc(), "/usr/bin/pandoc")
        m_which.assert_called_once_with("pandoc")

    @mock.patch("backend.report_tools.shutil.which", return_value=None)
    @mock.patch("backend.report_tools.get_base_path")
    @mock.patch("backend.report_tools.sys")
    def test_windows_prefers_bundled_exe(self, m_sys, m_base, m_which, tmp=None):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "pandoc.exe").write_text("x")
            m_sys.platform = "win32"
            del m_sys.frozen
            m_base.return_value = Path(d)
            self.assertEqual(report_tools._resolve_pandoc(), str(Path(d) / "pandoc.exe"))

    @mock.patch("backend.report_tools.shutil.which", return_value=None)
    @mock.patch("backend.report_tools.sys")
    def test_no_pandoc_returns_none(self, m_sys, m_which):
        m_sys.platform = "linux"
        del m_sys.frozen
        self.assertIsNone(report_tools._resolve_pandoc())


class ExportReviewableDraftTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name) / "output"
        self.report = Path(self._tmp.name) / "report_draft_v1.md"
        self.report.write_text("# t", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    @mock.patch("backend.report_tools._resolve_pandoc", return_value="/usr/bin/pandoc")
    @mock.patch("backend.report_tools.subprocess.run")
    def test_atomic_publish_replaces_final_only_on_success(self, m_run, m_pandoc):
        # pandoc 写 temp.docx（mock：真把内容写进 -o 路径），成功后 os.replace 到终名。
        def fake_run(cmd, **kw):
            Path(cmd[cmd.index("-o") + 1]).write_text("docx-bytes")
            return mock.Mock(returncode=0, stdout="", stderr="")
        m_run.side_effect = fake_run
        res = report_tools.export_reviewable_draft(str(self.report), str(self.out))
        self.assertEqual(res["status"], "ok")
        final = self.out / "report_draft_v1.docx"
        self.assertEqual(res["output_path"], str(final))
        self.assertEqual(res["filename"], "report_draft_v1.docx")
        self.assertTrue(final.exists())
        # 无残留 temp
        self.assertEqual([p.name for p in self.out.glob("*.docx")], ["report_draft_v1.docx"])

    @mock.patch("backend.report_tools._resolve_pandoc", return_value="/usr/bin/pandoc")
    @mock.patch("backend.report_tools.subprocess.run")
    def test_pandoc_failure_keeps_old_final_and_cleans_temp(self, m_run, m_pandoc):
        final = self.out / "report_draft_v1.docx"
        self.out.mkdir(parents=True, exist_ok=True)
        final.write_text("OLD")
        m_run.return_value = mock.Mock(returncode=1, stdout="", stderr="boom")
        res = report_tools.export_reviewable_draft(str(self.report), str(self.out))
        self.assertEqual(res["status"], "error")
        self.assertIn("boom", res["output"])
        self.assertEqual(final.read_text(), "OLD")  # 旧文件不动
        self.assertEqual([p.name for p in self.out.glob("*.docx")], ["report_draft_v1.docx"])  # temp 清掉

    @mock.patch("backend.report_tools._resolve_pandoc", return_value=None)
    def test_no_pandoc_friendly_error(self, m_pandoc):
        res = report_tools.export_reviewable_draft(str(self.report), str(self.out))
        self.assertEqual(res["status"], "error")
        self.assertIn("pandoc", res["output"])
```

- [ ] **Step 2: 跑测试看它失败**

Run: `.venv/bin/python -m pytest tests/test_report_tools.py -v`
Expected: FAIL（`_resolve_pandoc` 不存在 / 签名不符）

- [ ] **Step 3: 重写 `backend/report_tools.py` 全文**

```python
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import get_base_path


def _resolve_pandoc() -> str | None:
    """解析 pandoc 可执行：仅打包态/Windows 才优先包内 pandoc.exe（防 Linux 误 exec
    Windows 二进制——WINDOWS_BUILD.md 要求维护者把 pandoc.exe 放仓库根，而 get_base_path()
    开发/服务器态=仓库根），否则走系统 pandoc。"""
    if getattr(sys, "frozen", False) or sys.platform == "win32":
        base = get_base_path()
        for candidate in (base / "pandoc.exe", base / "pandoc" / "pandoc.exe"):
            if candidate.is_file():
                return str(candidate)
    system = shutil.which("pandoc")
    return system or None


def export_reviewable_draft(report_path: str, output_dir: str) -> dict:
    """把报告 markdown 用 pandoc 导出为可审 docx。原子发布：pandoc 写同目录唯一 temp.docx
    → 成功 os.replace 到终名；任一失败保留旧终名 + 清 temp。全程锁外（依赖 R3 原子写不变式）。"""
    pandoc = _resolve_pandoc()
    if not pandoc:
        return {
            "status": "error",
            "output": "未找到 pandoc：请在服务器安装 pandoc（Linux：apt install pandoc），或重装完整的桌面安装包。",
            "output_path": "",
            "filename": "",
        }

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    final_path = out_dir / (Path(report_path).stem + ".docx")

    fd, tmp_name = tempfile.mkstemp(dir=str(out_dir), suffix=".docx")
    os.close(fd)  # Windows 文件占用：pandoc 才能写该路径
    tmp_path = Path(tmp_name)
    try:
        result = subprocess.run(
            [pandoc, report_path, "-o", str(tmp_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            tmp_path.unlink(missing_ok=True)
            return {
                "status": "error",
                "output": result.stderr or result.stdout or "pandoc 导出失败，未生成可审草稿。",
                "output_path": "",
                "filename": "",
            }
        os.replace(tmp_path, final_path)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        return {
            "status": "error",
            "output": f"导出失败：{exc}",
            "output_path": "",
            "filename": "",
        }

    return {
        "status": "ok",
        "output": f"已生成可审草稿: {final_path}\n说明: 当前产物用于预审和传阅，不替代最终中文排版。",
        "output_path": str(final_path),
        "filename": final_path.name,
    }
```

- [ ] **Step 4: 跑测试看它通过**

Run: `.venv/bin/python -m pytest tests/test_report_tools.py -v`
Expected: PASS（全部）

- [ ] **Step 5: Commit**

```bash
git add backend/report_tools.py tests/test_report_tools.py
git commit -m "feat(w2c): pandoc export in pure Python — resolver guard + atomic publish"
```

---

### Task 2: 导出端点去 script + 非阻塞线程池执行

**Files:**
- Modify: `backend/main.py`（导出端点 `~1104-1112`）
- Test: `tests/test_main_api.py`（`test_export_draft_endpoint_returns_output_path` `~1487`）

- [ ] **Step 1: 改测试为新签名**（替换 `tests/test_main_api.py` 的 `test_export_draft_endpoint_returns_output_path`）

```python
    @mock.patch("backend.main.export_reviewable_draft")
    def test_export_draft_endpoint_returns_output_path(self, mock_export_draft):
        self.engine.get_primary_report_path.return_value = "/tmp/report_draft_v1.md"
        self.engine.ensure_output_dir.return_value = "/tmp/output"
        mock_export_draft.return_value = {
            "status": "ok",
            "output": "已生成可审草稿: /tmp/output/report_draft_v1.docx",
            "output_path": "/tmp/output/report_draft_v1.docx",
            "filename": "report_draft_v1.docx",
        }

        response = self.client.post("/api/projects/demo/export-draft")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["output_path"], "/tmp/output/report_draft_v1.docx")
        self.assertEqual(response.json()["filename"], "report_draft_v1.docx")
        # 新签名：不再传 script_path
        mock_export_draft.assert_called_once_with(
            "/tmp/report_draft_v1.md",
            "/tmp/output",
        )
```

- [ ] **Step 2: 跑测试看它失败**

Run: `.venv/bin/python -m pytest tests/test_main_api.py -k export_draft_endpoint -v`
Expected: FAIL（旧端点仍调 `get_script_path` + 3 参）

- [ ] **Step 3: 改导出端点**（`backend/main.py`，把 `async def export_draft` 改为同步 `def`，FastAPI 自动丢线程池跑——避免阻塞事件循环 + 掐住异步流心跳；不取 request lock）

```python
@app.post("/api/projects/{project_id}/export-draft")
def export_draft(scope: ProjectScope = Depends(require_project)):
    # 同步 def 路由：FastAPI 在线程池执行，pandoc 阻塞子进程不卡事件循环（spec §3.2）。
    # 导出不取 per-project request lock（spec §3.6：chat_stream 整轮持 RLock，R3 原子写保证锁外读安全）。
    try:
        report_path = scope.engine.get_primary_report_path(scope.project_id)
        output_dir = scope.engine.ensure_output_dir(scope.project_id)
        return export_reviewable_draft(report_path, output_dir)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
```

- [ ] **Step 4: 跑测试看它通过**

Run: `.venv/bin/python -m pytest tests/test_main_api.py -k export_draft_endpoint -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/main.py tests/test_main_api.py
git commit -m "feat(w2c): export endpoint drops script_path, runs off event loop (sync route)"
```

---

### Task 3: web 下载端点 `GET .../export-draft/download`

**Files:**
- Modify: `backend/main.py`（紧邻 `POST .../export-draft` 注册；新增 import `from fastapi.responses import FileResponse`）
- Test: `tests/test_main_api.py`（新增 `ExportDownloadTests` 或并入既有导出测试类）

- [ ] **Step 1: 写失败测试**（在 `tests/test_main_api.py` 新增，沿用既有 `require_project`/engine mock 夹具风格——参照同文件其它端点测试如何 mock `self.engine` 与 `get_project_record`）

```python
    def test_export_download_serves_deterministic_docx_for_owner(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "output"
            out.mkdir()
            (out / "report_draft_v1.docx").write_bytes(b"PKdocxbytes")
            self.engine.ensure_output_dir.return_value = str(out)
            resp = self.client.get("/api/projects/demo/export-draft/download")
            self.assertEqual(resp.status_code, 200)
            self.assertIn("attachment", resp.headers["content-disposition"])
            self.assertEqual(resp.content, b"PKdocxbytes")

    def test_export_download_404_when_not_generated(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "output"
            out.mkdir()
            self.engine.ensure_output_dir.return_value = str(out)
            resp = self.client.get("/api/projects/demo/export-draft/download")
            self.assertEqual(resp.status_code, 404)
```

> 跨租户/非属主 404 由 `require_project`（canonical id + per-uid engine registry）天然保证——参照同文件 `CrossTenantApiTests` 模式补一条非属主 GET → 404。

- [ ] **Step 2: 跑测试看它失败**

Run: `.venv/bin/python -m pytest tests/test_main_api.py -k export_download -v`
Expected: FAIL（路由不存在 → 404 但 content-disposition 缺失 / 第一个用例 200 拿不到）

- [ ] **Step 3: 加下载端点**（`backend/main.py`，紧邻 `POST .../export-draft`；文件名确定 `report_draft_v1.docx`，路径穿越守卫）

```python
from fastapi.responses import FileResponse  # 顶部 import 区，与现有 StreamingResponse 并列

_EXPORT_DOWNLOAD_FILENAME = "report_draft_v1.docx"

@app.get("/api/projects/{project_id}/export-draft/download")
def export_draft_download(scope: ProjectScope = Depends(require_project)):
    # 只服务确定文件名（不接受客户端任意 filename）；解析后校验仍在该项目 output 目录内。
    output_dir = Path(scope.engine.ensure_output_dir(scope.project_id)).resolve()
    target = (output_dir / _EXPORT_DOWNLOAD_FILENAME).resolve()
    if output_dir not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="尚未生成可审草稿，请先导出。")
    return FileResponse(
        path=str(target),
        filename=_EXPORT_DOWNLOAD_FILENAME,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
```

> `Path.resolve()` + `output_dir in target.parents` 是穿越守卫；`FileResponse(filename=...)` 自动设 `Content-Disposition: attachment`。

- [ ] **Step 4: 跑测试看它通过**

Run: `.venv/bin/python -m pytest tests/test_main_api.py -k export_download -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/main.py tests/test_main_api.py
git commit -m "feat(w2c): GET export-draft/download streams docx to browser (deterministic name, traversal guard)"
```

---

### Task 4: 前端 `exportDraft` 按 status 判成败 + 触发下载

**Files:**
- Modify: `frontend/src/components/WorkspacePanel.jsx`（`exportDraft` `~226-235`）
- Test: `frontend/tests/workspacePanel.source.test.mjs`（source-guard；无 jsdom）

- [ ] **Step 1: 写/补 source-guard 测试**（在既有 `workspacePanel.source.test.mjs` 加断言）

```javascript
import { test } from 'node:test'
import assert from 'node:assert'
import { readFileSync } from 'node:fs'

const src = readFileSync(new URL('../src/components/WorkspacePanel.jsx', import.meta.url), 'utf8')

test('exportDraft 按 status 判成败，不把任意 200 当成功', () => {
  const fn = src.slice(src.indexOf('const exportDraft'), src.indexOf('const exportDraft') + 700)
  assert.match(fn, /status\s*!==\s*['"]ok['"]|res\.data\.status/, 'exportDraft 必须检查 res.data.status')
})

test('exportDraft 成功后触发浏览器下载 export-draft/download', () => {
  const fn = src.slice(src.indexOf('const exportDraft'), src.indexOf('const exportDraft') + 700)
  assert.match(fn, /export-draft\/download/, 'exportDraft 必须触发下载端点')
})
```

- [ ] **Step 2: 跑测试看它失败**

Run: `cd frontend && node --test tests/workspacePanel.source.test.mjs`
Expected: FAIL

- [ ] **Step 3: 改 `exportDraft`**（`frontend/src/components/WorkspacePanel.jsx`）

```javascript
  const exportDraft = async () => {
    if (!projectId) return
    try {
      const res = await axios.post(`/api/projects/${encodeURIComponent(projectId)}/export-draft`)
      if (res.data?.status !== 'ok') {
        showError('导出失败: ' + (res.data?.output || '未知错误'))
        return
      }
      // 触发浏览器下载（带 cookie 凭据；同源 anchor 即可）
      const a = document.createElement('a')
      a.href = `/api/projects/${encodeURIComponent(projectId)}/export-draft/download`
      a.rel = 'noopener'
      document.body.appendChild(a)
      a.click()
      a.remove()
      showSuccess('已导出可审草稿，正在下载…')
      onProjectMutated?.()
    } catch (error) {
      showError('导出失败: ' + (error.response?.data?.detail || error.message))
    }
  }
```

- [ ] **Step 4: 跑测试 + build**

Run: `cd frontend && node --test tests/workspacePanel.source.test.mjs && npm run build`
Expected: PASS + build 绿

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/WorkspacePanel.jsx frontend/tests/workspacePanel.source.test.mjs
git commit -m "feat(w2c): exportDraft checks status + triggers browser download"
```

---

### Task 5: 删导出脚本 + skill 模块去引用 + source-guard

**Files:**
- Delete: `skill/scripts/export_draft.ps1`、`skill/scripts/export_draft.sh`
- Modify: `skill/modules/final-delivery.md`（`:34,40`）、`skill/modules/quality-review.md`（`:136`）
- Modify: `backend/skill.py`（删 `get_script_path` `~1802-1806`）
- Modify: `tests/test_skill_assets.py`

- [ ] **Step 1: 改 `tests/test_skill_assets.py`**——删 3 个 PowerShell 专属用例 + 加 source-guard

```python
# test_runtime_skill_assets_include_referenced_cross_platform_files：删掉 required_files 里的
#   root / "skill" / "scripts" / "export_draft.sh"，保留 capability-map.json + quality_check 负向断言。
# 整体删除：test_windows_powershell_scripts_use_utf8_bom、test_windows_powershell_scripts_force_utf8_stdout、
#   test_export_draft_ps1_prefers_bundled_pandoc_before_system_path（~135）。
# 新增 source-guard：
    def test_skill_docs_no_longer_reference_export_scripts(self):
        root = Path(__file__).resolve().parents[1]
        skill_dir = root / "skill"
        offenders = []
        for p in skill_dir.rglob("*.md"):
            if "scripts/export_draft" in p.read_text(encoding="utf-8"):
                offenders.append(str(p.relative_to(root)))
        self.assertEqual(offenders, [], f"skill 文档仍引用退役导出脚本: {offenders}")
        self.assertFalse((skill_dir / "scripts" / "export_draft.ps1").exists())
        self.assertFalse((skill_dir / "scripts" / "export_draft.sh").exists())
```

- [ ] **Step 2: 跑测试看它失败**

Run: `.venv/bin/python -m pytest tests/test_skill_assets.py -v`
Expected: FAIL（脚本仍在 + 模块文档仍含 `scripts/export_draft`）

- [ ] **Step 3: 删脚本 + 改模块文档 + 删 `get_script_path`**

```bash
git rm skill/scripts/export_draft.ps1 skill/scripts/export_draft.sh
```

`skill/modules/final-delivery.md`：把 `:34` 的 `powershell ... export_draft.ps1` 与 `:40` 的 `bash scripts/export_draft.sh` 命令块替换为：

```markdown
在工作区点击「导出可审草稿」按钮，系统会用 pandoc 生成 `output/report_draft_v1.docx` 并在浏览器下载。当前产物用于预审和传阅，不替代最终中文排版。
```

`skill/modules/quality-review.md:136`「如需导出 `docx` 预审版本，可结合 `modules/final-delivery.md` 使用导出脚本。」→「如需导出 `docx` 预审版本，在工作区点击『导出可审草稿』按钮（见 `modules/final-delivery.md`）。」

`backend/skill.py`：删除 `get_script_path`（`~1802-1806`，导出唯一消费者已在 Task 2 去掉）。

- [ ] **Step 4: 跑测试看它通过**

Run: `.venv/bin/python -m pytest tests/test_skill_assets.py -v && grep -rn "get_script_path" backend/ tests/`
Expected: PASS + grep 无残留（除非有未知消费者，有则一并处理）

- [ ] **Step 5: Commit**

```bash
git add -A skill/ backend/skill.py tests/test_skill_assets.py
git commit -m "feat(w2c): retire export_draft scripts + de-reference skill docs + drop get_script_path"
```

---

### Task 6: `run_web.py` host/port env 化 + uvicorn proxy_headers

**Files:**
- Modify: `run_web.py`
- Test: `tests/test_run_web.py`（新建，source-guard——`run_web` 跑真 uvicorn 不便单测行为）

- [ ] **Step 1: 写失败测试**（`tests/test_run_web.py`）

```python
import unittest
from pathlib import Path


class RunWebConfigTests(unittest.TestCase):
    def setUp(self):
        self.src = (Path(__file__).resolve().parents[1] / "run_web.py").read_text(encoding="utf-8")

    def test_host_port_from_env(self):
        self.assertIn("CRA_BIND_HOST", self.src)
        self.assertIn("CRA_BIND_PORT", self.src)

    def test_uvicorn_trusts_proxy_headers(self):
        self.assertIn("proxy_headers=True", self.src)
        self.assertIn('forwarded_allow_ips="127.0.0.1"', self.src)

    def test_no_stale_hardcoded_external_ip(self):
        self.assertNotIn("57.129.103.127", self.src)
```

- [ ] **Step 2: 跑测试看它失败**

Run: `.venv/bin/python -m pytest tests/test_run_web.py -v`
Expected: FAIL

- [ ] **Step 3: 改 `run_web.py`**——host/port 读 env、删过期 IP 打印、uvicorn 加 proxy_headers

```python
#!/usr/bin/env python3
"""Web模式启动脚本 - 可从外部访问（反代在前）"""
import os

from backend.main import app, assert_safe_startup
import uvicorn

if __name__ == "__main__":
    host = (os.environ.get("CRA_BIND_HOST") or "127.0.0.1").strip()
    port = int((os.environ.get("CRA_BIND_PORT") or "8888").strip())
    print(f"\n🚀 启动 Web 服务... 监听 {host}:{port}（反代/HTTPS 在前）\n")

    app.state.auth_required = True
    app.state.cookie_secure = True   # web 默认部署在 https 之后；本地 http 调试可设 CRA_COOKIE_INSECURE
    if (os.environ.get("CRA_COOKIE_INSECURE") or "").strip():
        app.state.cookie_secure = False
    if not (os.environ.get("CRA_ALLOWED_ORIGIN") or "").strip():
        if app.state.cookie_secure:
            print("⚠️ 未设 CRA_ALLOWED_ORIGIN（生产默认 cookie_secure 态）："
                  "CSRF 不信任 loopback，所有写请求（POST/PUT/PATCH/DELETE）会被 403 拒绝（fail-closed）。"
                  "请设为你的站点 origin（如 https://consulting.z0y0h.work）后重启。")
        else:
            print("⚠️ 未设 CRA_ALLOWED_ORIGIN（本地 CRA_COOKIE_INSECURE 调试态）："
                  "仅 loopback 来源的写请求会被 CSRF 放行；远程部署须设站点 origin 并去掉 CRA_COOKIE_INSECURE。")
    assert_safe_startup(True, host)
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        proxy_headers=True,            # 信任 nginx 注入的 X-Forwarded-For（配合 §5.7 real_ip）
        forwarded_allow_ips="127.0.0.1",
    )
```

- [ ] **Step 4: 跑测试看它通过**

Run: `.venv/bin/python -m pytest tests/test_run_web.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add run_web.py tests/test_run_web.py
git commit -m "feat(w2c): run_web host/port from env + uvicorn proxy_headers for real client IP"
```

---

### Task 7: SSE 防 CF 断流心跳

**Files:**
- Modify: `backend/main.py`（审查流 generate `~1049-1056`、聊天流 generate `~1213-1235`）
- Test: `frontend/tests/sseEvents.test.mjs`（容忍）+ `tests/test_main_api.py`（审查流心跳）

设计（spec §3.7）：审查流有长空闲 queue-wait → 周期心跳；聊天流 sync generator 风险高 → **只在 generate() 开头立即发一行心跳**（正中「无首包」P1 失败模式：首字节即达 CF；中段空闲由 nginx `proxy_read_timeout 600s` + 已有 `X-Accel-Buffering:no` 兜，**不重构 sync generator、不碰 chat.py、不碰锁**）。前端两消费者已天然忽略非 `data:` 行（`extractSseDataPayload` 返 null / `startsWith('data: ')`），只加容忍锁。

- [ ] **Step 1: 写前端容忍测试**（`frontend/tests/sseEvents.test.mjs` 增）

```javascript
import { test } from 'node:test'
import assert from 'node:assert'
import { extractSseDataPayload } from '../src/utils/chatPresentation.js'

test(': keepalive 心跳注释行被忽略（返回 null）', () => {
  assert.strictEqual(extractSseDataPayload(': keepalive'), null)
  assert.strictEqual(extractSseDataPayload(':keepalive'), null)
})
```

- [ ] **Step 2: 写后端审查流心跳测试**（`tests/test_main_api.py`，沿用既有 review stream 测试夹具——构造一个延迟首事件的 worker，断言流里出现 `: keepalive`）

```python
    def test_review_stream_emits_heartbeat_during_idle(self):
        # 构造：worker 长时间不产事件 → generate 的 0.1s timeout 循环应周期发 ": keepalive"。
        # 用很小的心跳间隔（patch HEARTBEAT_INTERVAL_SECONDS）+ 模拟 worker 空转 → 断言收到心跳。
        # 具体夹具参照本文件其它 review-stream 流式断言；关键断言：
        #   self.assertIn(": keepalive", collected_text)
        ...
```

> 实现者按本文件既有 review-stream 测试写法补全；核心断言＝空闲期流中出现 `: keepalive`。

- [ ] **Step 3: 跑测试看它失败**

Run: `cd frontend && node --test tests/sseEvents.test.mjs`（容忍测试此刻应已 PASS——纯既有函数行为，作回归锁）
Run: `.venv/bin/python -m pytest tests/test_main_api.py -k review_stream_emits_heartbeat -v`
Expected: 后端 FAIL（无心跳）

- [ ] **Step 4: 实现心跳**

`backend/main.py` 顶部常量：
```python
SSE_HEARTBEAT_INTERVAL_SECONDS = 20.0
```

审查流 `generate()` 的 `while True` 循环（`~1049-1056`）改为带空闲计时心跳：
```python
        import time
        last_emit = time.monotonic()
        try:
            while True:
                if await request.is_disconnected():
                    cancel_event.set()
                    break
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    if time.monotonic() - last_emit >= SSE_HEARTBEAT_INTERVAL_SECONDS:
                        last_emit = time.monotonic()
                        yield ": keepalive\n\n"
                    continue
                if event is None:
                    break
                last_emit = time.monotonic()
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
```

聊天流 `generate()`（`~1213`）首行立即发心跳（覆盖「无首包」）：
```python
    def generate():
        yield ": keepalive\n\n"   # 首字节立即达 CF，避开无首包期 edge 超时（spec §3.7）
        try:
            handler = get_chat_handler(scope.uid, scope.project_id)
            ...
```

- [ ] **Step 5: 跑测试看它通过 + DeepSeek 回归**

Run: `.venv/bin/python -m pytest tests/test_main_api.py -k review_stream -v`
Run: `.venv/bin/python -m pytest tests/test_chat_runtime.py -k "deepseek or compat or stream" -v`
Expected: PASS（心跳生效 + DeepSeek 兼容不破）

- [ ] **Step 6: Commit**

```bash
git add backend/main.py frontend/tests/sseEvents.test.mjs tests/test_main_api.py
git commit -m "feat(w2c): SSE heartbeat — review stream periodic + chat stream first-byte (anti CF idle drop)"
```

---

## Part B — N6 F2 收口（删 legacy 解析器）

### Task 8: 删 4 个 legacy 解析器 + 无-converter 测试注入假 converter

**Files:**
- Modify: `backend/skill.py`（删 `_legacy_read_document` `~1676`、`_read_docx` `~3055`、`_read_xlsx` `~3067`、`_read_pdf` `~3083`；`_converter_read_document` `~1652` 无 converter 改报错）
- Modify: `tests/test_skill_engine.py`（`~59,129,160` read_material_file 注入假 converter）、`tests/test_workspace_materials.py`（读 `.txt` 素材处）

- [ ] **Step 1: 写失败测试**——断言无 converter 时 `_converter_read_document` 报错（不再静默回退）

`tests/test_skill_engine.py` 新增：
```python
    def test_read_document_without_converter_raises_not_legacy(self):
        # F2：删 legacy 回退后，无 converter 必须明确报错（不再有 _legacy_read_document）。
        engine = SkillEngine(self.config_projects_dir, self.repo_skill_dir)
        self.assertFalse(hasattr(engine, "_legacy_read_document"))
        with self.assertRaises(ValueError):
            engine._converter_read_document("nonexistent", Path("/tmp/x.txt"))
```

- [ ] **Step 2: 跑测试看它失败**

Run: `.venv/bin/python -m pytest tests/test_skill_engine.py -k without_converter -v`
Expected: FAIL（`_legacy_read_document` 仍存在 / 不抛错）

- [ ] **Step 3: 删 4 个函数 + 改 `_converter_read_document`**

`backend/skill.py` 删除 `_legacy_read_document`、`_read_docx`、`_read_xlsx`、`_read_pdf` 整体。`_converter_read_document` 改：
```python
    def _converter_read_document(self, project_ref, material_path):
        if getattr(self, "_material_converter", None) is None:
            raise ValueError("当前环境无法读取该材料（缺少文档转换组件）")
        from backend.material_conversion import MaterialConversionError
        try:
            return self._material_converter.convert_document(material_path)
        except MaterialConversionError as exc:
            raise ValueError(str(exc)) from exc
```

- [ ] **Step 4: 给命中无-converter 读取的测试注入假 converter**

在 `tests/test_skill_engine.py` 顶部加共享假 converter（覆盖 read 路径用到的面：`convert_document` 读文本、`retain`/`release` no-op、`cache_key_from_sha256`、`image_cache_extra`）：
```python
class _FakeConverter:
    image_cache_extra = ""
    def cache_key_from_sha256(self, sha): return sha
    def convert_document(self, path): return Path(path).read_text(encoding="utf-8")
    def retain(self, *a, **k): pass
    def release(self, *a, **k): pass
```
在 `~59,129,160` 调 `read_material_file` 前注入 `engine._material_converter = _FakeConverter()`。`tests/test_workspace_materials.py` 读 `.txt` 素材的用例同样注入。

> 运行全套发现遗漏的无-converter read 点（假 converter 缺某方法报 AttributeError）即按需补该方法。

- [ ] **Step 5: 跑全套相关测试**

Run: `.venv/bin/python -m pytest tests/test_skill_engine.py tests/test_workspace_materials.py tests/test_chat_runtime.py -k "material or read or converter" -v`
Run: `grep -rn "_legacy_read_document\|_read_docx\|_read_xlsx\|_read_pdf" backend/`
Expected: PASS + grep 后端无残留

- [ ] **Step 6: Commit**

```bash
git add backend/skill.py tests/test_skill_engine.py tests/test_workspace_materials.py
git commit -m "feat(w2c): close N6 F2 — delete legacy parsers, no-converter read raises, tests inject fake converter"
```

---

## 收尾：全量回归

- [ ] **Step 1: 后端全量**

Run: `.venv/bin/python -m pytest tests/`
Expected: 全绿（mac realpath 4 例已知差异除外；Windows 绿）

- [ ] **Step 2: 前端全量 + build**

Run: `cd frontend && node --test tests/ && npm run build`
Expected: 全绿 + build 通过

- [ ] **Step 3: DeepSeek 官渠兼容专项**

Run: `.venv/bin/python -m pytest tests/test_chat_runtime.py -k "deepseek or compat" -v`
Expected: PASS（本 plan 未碰 provider 序列化，应零回归）

- [ ] **Step 4: 收尾 commit（如有 doc 同步）**

```bash
git add -A
git commit -m "chore(w2c): part A+B regression green"
```

---

## Self-Review 覆盖核对（spec → task）

| spec 段 | task |
|---|---|
| §3.2 pandoc 解析守卫 + 原子发布 + 不阻塞 loop | Task 1（解析/发布）+ Task 2（不阻塞 loop） |
| §3.3 删脚本/端点改接/删 get_script_path/模块文档 | Task 2 + Task 5 |
| §3.4 测试同步 + source-guard | Task 1/2/5 |
| §3.6 web 下载契约（确定文件名/穿越守卫/路由插点/status 判定/锁外读） | Task 3 + Task 4 |
| §3.7 SSE 心跳（审查周期 + 聊天首字节 + 前端容忍） | Task 7 |
| §4 N6 F2 删 legacy + 无-converter 测试 | Task 8 |
| §5.4 入口 env 化 + uvicorn proxy_headers | Task 6 |
| §5.1/§5.5–§5.9 装机/env/systemd/nginx/CF/ufw | **Part C runbook，不在本 plan**（spec §5 交互式执行） |
