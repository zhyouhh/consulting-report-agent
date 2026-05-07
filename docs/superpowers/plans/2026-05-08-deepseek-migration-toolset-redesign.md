# DeepSeek V4 Pro Migration — Toolset Redesign + Guard Layer Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Source spec:** `docs/superpowers/specs/2026-05-08-deepseek-migration-toolset-redesign-design.md` (APPROVED)

**Goal:** 在 managed channel 由 `gemini-3-flash` → `deepseek-v4-pro` 切换基础上，做一次性的工具集 + guard 控制层简化，并吸收 2026-05-07 E2E 实测的 6 个产品/工程问题。工具数 10 → 7，guard 控制层 ~1024 → ~300 行（70% 缩减）。

**Architecture:** 三段式 commit：Commit 1 加新代码（新旧并存）；Commit 2 删 schema 注册（model 不再可见旧工具但 callable 还在）；Commit 3 删旧 callable + guard 控制层 + grep 残留扫描。`edit_file` / `write_file` 加 path-based dispatcher（仅在 `content/report_draft_v1.md` 路径触发分派），保留 `append_report_draft` 唯一 vertical specialty。所有共享 invariant 仍走 `report_writing.py` 6 个 helper（其中 `check_no_prior_canonical_mutation_in_turn` 改阈值 1→3 + 适配 list；`check_read_before_write_canonical_draft` 加 within-turn self-refresh）。

**Tech Stack:** Python 3.11/3.12 + FastAPI + PyWebView + PyInstaller (backend); React + Tailwind (frontend)；测试用 unittest + pytest + node:test。新增依赖：`pytest-xdist>=3.0`。

---

## File Structure

### 后端

| 文件 | 责任 | 本 plan 改动 |
|---|---|---|
| `backend/chat.py` | FastAPI chat 处理 + 工具 dispatch + 流式 SSE + obligation 检测 | 主战场（删 + 加 + 重构） |
| `backend/report_writing.py` | 6 个 invariant pure-function helpers | 加 2 helper（`detect_user_message_intent` / `resolve_section_anchor`）+ 改 2 helper 阈值/逻辑 + Commit 3 删 `resolve_section_target` |
| `backend/main.py` | FastAPI startup + heal_stale_managed_model 调用 | 不动（Commit 0 已完成） |
| `app.py` | desktop entry + RotatingFileHandler 配置 | 加 `_setup_app_log` |
| `consulting_report.spec` | PyInstaller spec | 加 `version='version_info.txt'` |
| `version_info.txt` (新) | PE32 版本信息块 | 新建 |
| `pytest.ini` (新) | pytest 默认配置 | 新建 |
| `requirements.txt` | python 依赖 | 加 `pytest-xdist>=3.0` |
| `managed_search_pool.json` | 搜索池配置 | `per_turn_searches: 2 → 3` |
| `skill/SKILL.md` | model 系统提示资产 | 改 §S0 + 改 §S4 工具引用 |

### 前端

| 文件 | 责任 | 本 plan 改动 |
|---|---|---|
| `frontend/src/components/ThinkingBlock.jsx` (新) | HTML5 `<details>` 默认折叠的 reasoning UI 组件 | 新建 |
| `frontend/src/components/ChatPanel.jsx` | SSE 解析 + 消息渲染 | 新增 `parsed.type === 'thinking'` 分支 + ThinkingBlock 渲染 |

### 测试

| 文件 | 改动 |
|---|---|
| `tests/test_chat_runtime.py` | 加 dispatcher / S0 gate / mutation_limit / claim retry / parser 测试；Commit 2 删 3 个 ToolTests class |
| `tests/test_report_writing.py` | 加 `DetectUserMessageIntentTests` / `ResolveSectionAnchorTests` |
| `tests/test_app_logging.py` (新) | RotatingFileHandler 配置验证 |
| `tests/test_packaging_spec.py` | 加 `VersionInfoTests` |
| `tests/test_stream_api.py` | 标记 `@pytest.mark.slow`（不动逻辑） |
| `tests/smoke_packaged_app.py` | 标记 `@pytest.mark.slow`（不动逻辑） |
| `frontend/tests/thinkingBlock.test.mjs` (新) | ThinkingBlock 折叠/展开/渲染 |
| `frontend/tests/chatPresentationThinking.test.mjs` (新) | `appendThinkingEventContent` + `splitAssistantMessageBlocks` thinking sentinel 解析 |
| `frontend/tests/chatPanelSseRouting.test.mjs` (新) | SSE thinking 事件累计 + 分块行为 |

---

## Test Fixture Conventions（implementer 必读）

本 plan 中多个 task 的测试用例引用一组**约定的测试 fixture helper**，这些 helper 在现有 `tests/test_chat_runtime.py` 中以类似形式存在或需要小幅扩展。实施时把以下"约定 API"映射到实际 helper：

| 约定 API | 实际位置/构造方式 |
|---|---|
| `_make_handler_with_state(project_id, turn_context, conversation_state=None)` | 既有的 `ChatHandler` 构造 fixture（一般是 `setUp` 里造一个 `ChatHandler` + mock `SkillEngine` + 注入 `turn_context`）。若没有，参考最近 commit `0b8b968` 中 `tests/test_config.py` 的 `HealStaleManagedModelTests` 风格搭一个 minimal builder。 |
| `_make_tool_call(name, arguments)` | 构造一个 `tool_call`-like object，至少有 `function.name` (= name) 和 `function.arguments` (= JSON 字符串)。许多现有 test 已用此 pattern。 |
| `_invoke_finalize(handler, project_id, turn_tool_calls, assistant_text)` | 调 `_finalize_assistant_turn` 的薄 wrapper；传入 mock 的 turn_tool_calls 列表（每项 `function.name` 字段为 tool 名）+ assistant 文本。 |
| `_setup_canonical_draft(project_id, content)` | 在 mock 项目目录下写 `content/report_draft_v1.md` 文件，用于 dispatcher 测试。一般用 `tempfile.TemporaryDirectory` + monkey-patch `SkillEngine.get_project_path`。 |
| `_run_dispatcher(handler, tool_name, args)` | 直接调 `_dispatch_edit_file` / `_dispatch_write_file`，绕过 `_execute_tool` schema 检查；用于精准断言 dispatcher 内部分支。 |

**重要现有契约**（fixed per R1 P1-1, P1-2 — 实施时严格遵守，不要假设）：
- 工具结果错误字段是 **`message`**（不是 `error`）：`{"status": "error", "message": "..."}` 是标准。所有 dispatcher / 工具 reject 都用 `"message"`。测试 assertion 也用 `result["message"]`。
- 前端消息内容 `message.content` 是 **string**（不是 array）。Tool event 通过在 string 中追加 `🔧 调用工具: ...` / `✅ 结果: ...` 单行 sentinel 实现，由 `splitAssistantMessageBlocks(content)` 解析（见 `frontend/src/utils/chatPresentation.js:31, 68`）。本 plan 的 thinking 折叠**必须沿用 string 模型 + sentinel** 模式（在 chatPresentation.js 中扩展 helper），**不要**迁到 array、**不要**新建独立 message-content utils 文件。
- 共享 invariant helpers 真实签名：见 `backend/report_writing.py:105/119/132/148/157/190`，参见 plan Task 14 step 3 的正确串接。
- claim retry 入口：`_maybe_inject_obligation_retry` (`backend/chat.py:5458`)，**不是** `_finalize_assistant_turn`。

**实施时**：先通读 `tests/test_chat_runtime.py` 找到现有测试如何构造 ChatHandler + tool_call。然后给本 plan 中的 fixture API 写一个 `setUp` adapter（约 30 行）放在每个新 test class 顶部。一旦 adapter 通了，**所有 plan 中带 `pass` body 的 test 直接按注释描述 fill in**——assertion 模式都很简单：

```python
# pattern A — pass case
result = handler._dispatch_edit_file(project_id, file_path, old, new, turn_context)
self.assertEqual(result["status"], "success")
self.assertEqual(result["canonical_action"], "<expected>")
self.assertEqual(len(turn_context["canonical_draft_mutations"]), 1)

# pattern B — reject case
result = handler._dispatch_edit_file(...)
self.assertEqual(result["status"], "error")
self.assertIn("<expected keyword>", result["message"])

# pattern C — turn-end finalize claim retry
result = handler._invoke_finalize(...)
# 断言 retry 分支被触发：检查 mock 的 corrective injection 是否被调
mock_inject.assert_called_once()
```

**计入工作量**：每个带 `pass` 的 test body 约 5-10 行 code，一组 test class 约 30 分钟搭 adapter + 30 分钟填 body。本 plan 的整体时间估算（~3 工作日 / 1 implementer）已包含这部分。

## Convention：Per-task test scope

**重要前提**：本 plan 默认每个 task 只跑 task 内 explicit 列出的 test 命令；不跑全套 `pytest tests/`。最终回归在 Commit 1 / 2 / 3 末尾的"Final test pass"task 一次性跑全套。这是 spec §3.9.5 的 "plan-only convention" 落地——**不**改 AGENTS.md / CLAUDE.md 的全局测试约定。

每个 task 的 "Run" 命令是该 task 完成后唯一必跑的检查。如果 task 改了多个文件，"Run" 列出最相关的 test 子集（一般 1-2 个 test class / file）。

---

## Commit 0 — 已落地（spec §3.10 / 已 commit）

**这部分代码已经在 commits `06779b1` / `0b8b968` / `8b3ad16` 中合入。本 plan 后续 task 假设这个基线已存在。** 不要重复实施；如本地仓库不在该基线上，先 `git pull` 拉到最新 main。

| 文件 | 改动 | spec 段 |
|---|---|---|
| `backend/config.py` | `heal_stale_managed_model` + `_default_managed_models_fetch` 函数 | §3.10 |
| `backend/main.py` | 启动时调 `heal_stale_managed_model` + `save_settings` 落盘 | §3.10 |
| `backend/context_policy.py` | `tier_1m_eff_256k = (1_000_000, 256_000)` + `deepseek-v4-pro` exact mapping | §3.7 / §3.10 |
| `frontend/src/utils/connectionMode.js` | fallback `gemini-3-flash` → `deepseek-v4-pro` | §3.10 |
| `frontend/tests/connectionMode.test.mjs` | 4 处 fallback 字符串更新 | §3.10 |
| `tests/test_config.py` | 7 个 `HealStaleManagedModelTests` + 默认模型字符串更新 | §3.10 |
| `tests/test_context_policy.py` | `test_exact_match_for_managed_deepseek_uses_1m_provider_and_256k_effective` | §3.10 |
| `docs/current-worklist.md` | 加 #4「图片附件能力按 managed_model 分流」推后处理 | §2.2 |
| `docs/managed-proxy-deployment.md` / `CLAUDE.md` / `AGENTS.md` | 默认模型名同步 | §3.10 |
| `docs/default-managed-proxy-contract.md` / `README.md` | catch 漏掉的 `gemini-3-flash` 引用 | §3.10 |
| 服务器 managed proxy `MANAGED_PROXY_ALLOWED_MODELS=deepseek-v4-pro` + 容器重建 | 实际执行 | §1.1 |

如需 baseline 验证：

```bash
git log --oneline -5 | grep deepseek-migration
.venv\Scripts\python -m pytest tests/test_config.py::HealStaleManagedModelTests tests/test_context_policy.py -q
```

期望：3 commits 可见 + 8 测试 PASS。

---

## Commit 1 — 新工具入口 + 增强 dispatcher + 首轮 gate + think 折叠

**目标**：加新代码，**不删旧代码**，新旧并存。Commit 1 完成后旧 4 工具仍在，新 dispatcher 也在；model 仍可调旧工具（schema 注册保留）。本阶段不会出 regression（旧路径不变）。

### Task 1: pytest infrastructure（pytest-xdist + slow markers + pytest.ini）

> **TDD exception: config-only**（fixed per R1 P2-2）。本 task 是 pytest 基础设施配置 + slow marker 标注，不引入新代码逻辑，因此不是"写 test → fail → implement → pass"序列；改完后 collection 验证就够。


**Files:**
- Create: `pytest.ini`
- Modify: `requirements.txt`
- Modify: `tests/test_stream_api.py:1-30`（加 `@pytest.mark.slow` 到 class）
- Modify: `tests/smoke_packaged_app.py:1-30`（加 `@pytest.mark.slow` 到 class）

- [ ] **Step 1: 加 pytest-xdist 依赖**

修改 `requirements.txt`（在末尾追加）：

```
pytest-xdist>=3.0
```

- [ ] **Step 2: 创建 `pytest.ini`**

```ini
[pytest]
addopts = -m "not slow" -n auto --dist worksteal
testpaths = tests
markers =
    slow: tests that need real uvicorn / packaged exe / minutes-long setup
```

- [ ] **Step 3: 给 stream_api 测试加 slow marker**

`tests/test_stream_api.py` 顶部 import 后加：

```python
import pytest
```

然后给主 test class 加装饰器：

```python
@pytest.mark.slow
class StreamApiTests(unittest.TestCase):
    ...
```

如果有多个 test class，给每个加。

- [ ] **Step 4: 给 smoke_packaged_app 加 slow marker**

`tests/smoke_packaged_app.py` 同样：import pytest + class 装饰器。

- [ ] **Step 5: 安装并验证**

```bash
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pytest tests/ -q --collect-only 2>&1 | tail -20
```

期望：collection 完成，看不到 `test_stream_api.py` 的 case（slow 默认排除）。

- [ ] **Step 6: 验证 fast 集合并行运行**

```bash
.venv\Scripts\python -m pytest tests/test_config.py tests/test_context_policy.py -q
```

期望：用 `-n auto` 并行跑通，全 pass。

- [ ] **Step 7: 验证 `-m ""` 跑全套（含 slow）能力**

```bash
.venv\Scripts\python -m pytest tests/test_stream_api.py -m "" -q --collect-only
```

期望：能 collect 到 stream_api test。

### Task 2: `app.py` 加 RotatingFileHandler

**Files:**
- Modify: `app.py`
- Test: `tests/test_app_logging.py` (新)

- [ ] **Step 1: 写失败测试**

新建 `tests/test_app_logging.py`：

```python
"""验证 app.log RotatingFileHandler 配置。"""
import logging
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest.mock import patch


class AppLogTests(unittest.TestCase):
    def test_setup_app_log_attaches_rotating_file_handler_to_root_logger(self):
        from app import _setup_app_log

        # 清理 root logger handlers 防止串扰
        root = logging.getLogger()
        original_handlers = list(root.handlers)
        try:
            for h in list(root.handlers):
                if isinstance(h, RotatingFileHandler):
                    root.removeHandler(h)

            with patch("app.Path.home") as mock_home:
                mock_home.return_value = Path("C:/__test_home__")
                with patch("logging.handlers.RotatingFileHandler.__init__",
                           return_value=None) as mock_handler_init:
                    _setup_app_log()
                    self.assertTrue(mock_handler_init.called,
                                    "RotatingFileHandler should be constructed")
                    args, kwargs = mock_handler_init.call_args
                    expected_log = Path("C:/__test_home__/.consulting-report/app.log")
                    self.assertEqual(args[0], expected_log)
                    self.assertEqual(kwargs.get("maxBytes"), 5 * 1024 * 1024)
                    self.assertEqual(kwargs.get("backupCount"), 3)
                    self.assertEqual(kwargs.get("encoding"), "utf-8")
        finally:
            root.handlers = original_handlers
```

- [ ] **Step 2: Run test → fail (函数不存在)**

```bash
.venv\Scripts\python -m pytest tests/test_app_logging.py -q
```

Expected: FAIL `ImportError: cannot import name '_setup_app_log' from 'app'`

- [ ] **Step 3: 在 `app.py` 顶部 import 区块加**

`app.py` 现有 import 段后追加：

```python
import logging
from logging.handlers import RotatingFileHandler


def _setup_app_log():
    """Attach a RotatingFileHandler to root logger so packaged windowed exe
    has visible logs at ~/.consulting-report/app.log.

    Coexists with backend/main.py's logging.basicConfig (root logger may have
    multiple handlers).
    """
    log_dir = Path.home() / ".consulting-report"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Log dir creation failure should never crash app boot
        return
    log_file = log_dir / "app.log"
    handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)


_setup_app_log()
```

注意：`Path` 已 import。如果未 import 加 `from pathlib import Path`。

- [ ] **Step 4: Run test → pass**

```bash
.venv\Scripts\python -m pytest tests/test_app_logging.py -q
```

Expected: PASS

### Task 3: PyInstaller version_info 块

**Files:**
- Create: `version_info.txt`
- Modify: `consulting_report.spec`
- Test: `tests/test_packaging_spec.py`（加新 test class）

- [ ] **Step 1: 写失败测试**

`tests/test_packaging_spec.py` 末尾追加：

```python
class VersionInfoTests(unittest.TestCase):
    def test_version_info_file_exists_at_repo_root(self):
        from pathlib import Path
        repo = Path(__file__).resolve().parents[1]
        self.assertTrue((repo / "version_info.txt").is_file(),
                        "version_info.txt must exist at repo root")

    def test_version_info_contains_required_string_fields(self):
        from pathlib import Path
        repo = Path(__file__).resolve().parents[1]
        text = (repo / "version_info.txt").read_text(encoding="utf-8")
        for field in ("CompanyName", "FileDescription", "FileVersion",
                      "InternalName", "OriginalFilename", "ProductName",
                      "ProductVersion"):
            self.assertIn(field, text, f"version_info.txt missing {field}")
        self.assertIn("咨询报告写作助手", text)

    def test_consulting_report_spec_references_version_info(self):
        from pathlib import Path
        repo = Path(__file__).resolve().parents[1]
        text = (repo / "consulting_report.spec").read_text(encoding="utf-8")
        self.assertIn("version='version_info.txt'", text)
```

- [ ] **Step 2: Run test → fail**

```bash
.venv\Scripts\python -m pytest tests/test_packaging_spec.py::VersionInfoTests -q
```

Expected: 3 FAIL

- [ ] **Step 3: 创建 `version_info.txt`**

```
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(0, 1, 0, 0),
    prodvers=(0, 1, 0, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '080404b0',
        [
          StringStruct('CompanyName', 'ZhYoU'),
          StringStruct('FileDescription', '咨询报告写作助手'),
          StringStruct('FileVersion', '0.1.0'),
          StringStruct('InternalName', 'consulting-report'),
          StringStruct('OriginalFilename', '咨询报告助手.exe'),
          StringStruct('ProductName', '咨询报告助手'),
          StringStruct('ProductVersion', '0.1.0'),
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [0x0804, 1200])])
  ]
)
```

- [ ] **Step 4: 修改 `consulting_report.spec` EXE 段**

把：

```python
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='咨询报告助手',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app_icon.ico',
)
```

改为：

```python
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='咨询报告助手',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app_icon.ico',
    version='version_info.txt',
)
```

- [ ] **Step 5: Run test → pass**

```bash
.venv\Scripts\python -m pytest tests/test_packaging_spec.py::VersionInfoTests -q
```

Expected: 3 PASS

### Task 4: managed_search_pool.json per_turn_searches 2 → 3

> **TDD exception: config-only**（fixed per R1 P2-2）。改 JSON 配置 + 验证现有 search_pool test 不破。

### Scope note: spec §3.8.3 start menu shortcut（fixed per R2 仍未覆盖项）

spec §3.8.3 提及 `build.ps1 --install-shortcut` 可选生成 Start menu 快捷方式。**本 plan 明确将其移出本轮 scope**：

- spec §3.8.3 原文写"可选；默认不开；发版给同事时人工执行"
- 本轮 J 项交付仅为：log file (Task 2) + version_info (Task 3)
- 快捷方式留给后续单独 task，触发时机：cutover 完成 + 同事开始 distribution 时
- 理由：cutover 测试不需要快捷方式；它只影响发版便利性，不影响功能正确性。把它纳入本 plan 会 inflate scope 且没有验收信号

如未来要补，单独 spec/plan 加 `Task 3b: build.ps1 --install-shortcut` 即可，约 30 行 powershell + 1 个 packaging test。



**Files:**
- Modify: `managed_search_pool.json`
- Test: `tests/test_search_pool.py` (existing) — sanity check 不破坏

- [ ] **Step 1: 改配置**

`managed_search_pool.json` 找 `"limits"` 段把 `"per_turn_searches": 2` 改为 `"per_turn_searches": 3`。其他字段不动。

- [ ] **Step 2: 跑配额相关 test 确认无 regression**

```bash
.venv\Scripts\python -m pytest tests/test_search_pool.py -q
```

Expected: PASS（test 一般断言行为 logic 不锁死具体阈值；如果有断言 == 2，需要修改测试预期）。

- [ ] **Step 3: 如有测试断言 == 2，改为 == 3 并 commit 解释**

例如 `tests/test_search_pool.py` 出现 `self.assertEqual(limits.per_turn_searches, 2)` → 改为 `3`。

### Task 5: `report_writing.py` 加 `detect_user_message_intent` helper

**Files:**
- Modify: `backend/report_writing.py`
- Test: `tests/test_report_writing.py`

- [ ] **Step 1: 写失败测试**

`tests/test_report_writing.py` 末尾追加：

```python
class DetectUserMessageIntentTests(unittest.TestCase):
    def test_generative_keywords_match(self):
        from backend.report_writing import detect_user_message_intent
        cases = [
            "帮我起草第一章",
            "续写下一章",
            "写下一段",
            "继续写",
            "帮我写完第二章",
        ]
        for msg in cases:
            self.assertEqual(detect_user_message_intent(msg), "generative",
                             f"expected generative for: {msg}")

    def test_modify_keywords_match(self):
        from backend.report_writing import detect_user_message_intent
        cases = [
            "把'增长'改成'增速'",
            "重写第二章",
            "替换第一段",
            "修改结论部分",
            "删掉最后一节",
            "调整第三章的措辞",
        ]
        for msg in cases:
            self.assertEqual(detect_user_message_intent(msg), "modify",
                             f"expected modify for: {msg}")

    def test_ambiguous_returns_ambiguous(self):
        from backend.report_writing import detect_user_message_intent
        for msg in ["", "看一下背景", "你好", "ok 继续"]:
            self.assertEqual(detect_user_message_intent(msg), "ambiguous",
                             f"expected ambiguous for: {msg}")

    def test_chinese_punctuation_does_not_break(self):
        from backend.report_writing import detect_user_message_intent
        # 中文标点 + 长句
        self.assertEqual(
            detect_user_message_intent("把第二章里的'30%'改成'三成'，谢谢。"),
            "modify",
        )
```

- [ ] **Step 2: Run test → fail (helper 不存在)**

```bash
.venv\Scripts\python -m pytest tests/test_report_writing.py::DetectUserMessageIntentTests -q
```

Expected: FAIL `ImportError`

- [ ] **Step 3: 实现 helper**

`backend/report_writing.py` 末尾追加（在 `detect_canonical_draft_write_obligation` 上方或下方均可）：

```python
_GENERATIVE_PATTERNS = [
    re.compile(r"起草"),
    re.compile(r"续写"),
    re.compile(r"写下一[章节段]"),
    re.compile(r"继续写"),
    re.compile(r"帮我写"),
    re.compile(r"写完(第|下一)"),
]

_MODIFY_PATTERNS = [
    re.compile(r"把.+改(成|为)"),
    re.compile(r"重写第.+[章节]"),
    re.compile(r"替换"),
    re.compile(r"修改"),
    re.compile(r"删[掉除]"),
    re.compile(r"调整"),
]


def detect_user_message_intent(user_message: str) -> str:
    """Lightweight keyword-based intent classifier for canonical draft writes.

    Returns:
        "generative" — 起草 / 续写 / 写下一章 / 继续写 / 帮我写 等新增类语义
        "modify"     — 把.改成 / 重写第N章 / 替换 / 修改 / 删掉 / 调整 等修改类语义
        "ambiguous"  — 其他（不阻拦工具调用，仅 turn-end retry 不触发）

    Generative 比 modify 优先（同时含 "续写第二章并替换..." 罕见，按 generative 处理）。
    """
    if not user_message:
        return "ambiguous"
    for p in _GENERATIVE_PATTERNS:
        if p.search(user_message):
            return "generative"
    for p in _MODIFY_PATTERNS:
        if p.search(user_message):
            return "modify"
    return "ambiguous"
```

- [ ] **Step 4: Run test → pass**

```bash
.venv\Scripts\python -m pytest tests/test_report_writing.py::DetectUserMessageIntentTests -q
```

Expected: 4 PASS

### Task 6: `report_writing.py` 加 `resolve_section_anchor` helper

**Files:**
- Modify: `backend/report_writing.py`
- Test: `tests/test_report_writing.py`

- [ ] **Step 1: 写失败测试**

`tests/test_report_writing.py` 末尾追加：

```python
class ResolveSectionAnchorTests(unittest.TestCase):
    DRAFT = (
        "# 报告标题\n"
        "## 第一章 引言\n"
        "引言正文 1\n"
        "引言正文 2\n"
        "## 第二章 战略选择\n"
        "战略正文 1\n"
        "## 第三章 实施路径\n"
        "实施正文 1\n"
    )

    def test_anchor_first_line_only_match_returns_full_section(self):
        from backend.report_writing import resolve_section_anchor
        # anchor 含旧正文，仅取首行做 label match
        anchor = "## 第二章 战略选择\n旧战略正文（已被模型乱编）"
        snap = resolve_section_anchor(anchor, self.DRAFT)
        self.assertIsNotNone(snap)
        self.assertTrue(snap.startswith("## 第二章 战略选择\n"))
        self.assertIn("战略正文 1", snap)
        self.assertNotIn("## 第三章", snap)
        self.assertNotIn("实施正文", snap)

    def test_single_line_anchor_matches(self):
        from backend.report_writing import resolve_section_anchor
        snap = resolve_section_anchor("## 第二章 战略选择", self.DRAFT)
        self.assertIsNotNone(snap)
        self.assertIn("战略正文 1", snap)

    def test_label_not_in_draft_returns_none(self):
        from backend.report_writing import resolve_section_anchor
        self.assertIsNone(
            resolve_section_anchor("## 第十章 不存在", self.DRAFT)
        )

    def test_duplicate_label_returns_none(self):
        from backend.report_writing import resolve_section_anchor
        draft_dup = self.DRAFT + "## 第二章 战略选择\n重复段\n"
        self.assertIsNone(
            resolve_section_anchor("## 第二章 战略选择", draft_dup)
        )

    def test_anchor_must_start_with_h2(self):
        from backend.report_writing import resolve_section_anchor
        self.assertIsNone(resolve_section_anchor("第二章", self.DRAFT))
        self.assertIsNone(resolve_section_anchor("# 第二章", self.DRAFT))

    def test_last_section_extends_to_eof(self):
        from backend.report_writing import resolve_section_anchor
        snap = resolve_section_anchor("## 第三章 实施路径", self.DRAFT)
        self.assertIsNotNone(snap)
        self.assertIn("实施正文 1", snap)
```

- [ ] **Step 2: Run test → fail**

```bash
.venv\Scripts\python -m pytest tests/test_report_writing.py::ResolveSectionAnchorTests -q
```

Expected: FAIL `ImportError`

- [ ] **Step 3: 实现 helper**

`backend/report_writing.py` 末尾追加：

```python
def resolve_section_anchor(anchor: str, draft: str) -> Optional[str]:
    """从 draft 中按 h2 anchor 精确定位完整章节 snapshot。

    与 legacy `resolve_section_target` 的区别：
      - legacy: 从 user_message 抽 "第N章/节" 前缀做 prefix-match
      - 新版: 直接拿 anchor (`## 章节标题...` prefix) 的**首行**做 h2-label exact match，
              然后展开到下一个同级 `## ` 行之前的全部内容（最后章节展开到 EOF）

    Args:
        anchor: 形如 `## 第二章 战略选择`。**仅取首行**（截到第一个 \\n）做 label 匹配；
                anchor 首行之后的正文被忽略——不参与一致性校验。
                这意味着模型可以传 `## 第二章 战略选择` 单行，也可以传带旧正文版本，两者
                都成功匹配，dispatcher 用 draft 中的实际章节 snapshot 作为 actual_old。
                这是 spec 设计的核心点：消除模型必须复述 1500 字章节原文的失败模式。
        draft: 完整草稿文本

    Returns:
        匹配的完整章节文本（含 `## ...` 行 + 正文，不含下一章 `## ` 行；最后章节含到 EOF）；
        若 label 不存在 / label 出现多次 / anchor 首行不以 `## ` 开头 → 返回 None。
    """
    if not anchor or not draft:
        return None
    first_line = anchor.split("\n", 1)[0].strip()
    if not first_line.startswith("## "):
        return None

    lines = draft.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if line.rstrip("\n").strip() == first_line]
    if len(matches) != 1:
        return None

    start = matches[0]
    end = len(lines)
    for j in range(start + 1, len(lines)):
        stripped = lines[j].rstrip("\n").strip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            end = j
            break
    return "".join(lines[start:end])
```

- [ ] **Step 4: Run test → pass**

```bash
.venv\Scripts\python -m pytest tests/test_report_writing.py::ResolveSectionAnchorTests -q
```

Expected: 6 PASS

### Task 7: `report_writing.py` 改 mutation_limit 1→3 + 适配 list 数据结构

**Files:**
- Modify: `backend/report_writing.py:check_no_prior_canonical_mutation_in_turn`
- Test: `tests/test_report_writing.py`

- [ ] **Step 1: 读现有 helper**

```bash
.venv\Scripts\python -c "import inspect; from backend.report_writing import check_no_prior_canonical_mutation_in_turn; print(inspect.getsource(check_no_prior_canonical_mutation_in_turn))"
```

记录原签名和原使用的 `turn_context` 字段名（旧字段是 `canonical_draft_mutation` 单 dict）。

- [ ] **Step 2: 写新行为测试**

`tests/test_report_writing.py` 末尾追加：

```python
class MutationLimit3Tests(unittest.TestCase):
    def _ctx(self, mutations_count):
        return {
            "canonical_draft_mutations": [
                {"tool": "edit_file", "canonical_action": "text_replace",
                 "target_label": f"m{i}", "old_len": 1, "new_len": 1,
                 "mtime_after": 0.0, "ts": 0.0}
                for i in range(mutations_count)
            ]
        }

    def test_zero_mutations_passes(self):
        from backend.report_writing import check_no_prior_canonical_mutation_in_turn
        self.assertIsNone(check_no_prior_canonical_mutation_in_turn(self._ctx(0)))

    def test_two_mutations_passes(self):
        from backend.report_writing import check_no_prior_canonical_mutation_in_turn
        self.assertIsNone(check_no_prior_canonical_mutation_in_turn(self._ctx(2)))

    def test_three_mutations_blocks_fourth(self):
        from backend.report_writing import check_no_prior_canonical_mutation_in_turn
        msg = check_no_prior_canonical_mutation_in_turn(self._ctx(3))
        self.assertIsNotNone(msg)
        self.assertIn("3", msg)  # mentions limit or count

    def test_error_msg_summarizes_mutations(self):
        from backend.report_writing import check_no_prior_canonical_mutation_in_turn
        msg = check_no_prior_canonical_mutation_in_turn(self._ctx(3))
        self.assertIn("text_replace", msg)
        self.assertIn("m0", msg)
        self.assertIn("m2", msg)

    def test_legacy_field_name_returns_none(self):
        # 老字段名 canonical_draft_mutation 不应被新 helper 误读
        from backend.report_writing import check_no_prior_canonical_mutation_in_turn
        ctx = {"canonical_draft_mutation": {"tool": "rewrite_report_section"}}
        self.assertIsNone(check_no_prior_canonical_mutation_in_turn(ctx))
```

- [ ] **Step 3: Run → fail（旧实现是 limit=1 + 单 dict 字段）**

```bash
.venv\Scripts\python -m pytest tests/test_report_writing.py::MutationLimit3Tests -q
```

Expected: 多个 FAIL

- [ ] **Step 4: 改 helper**

替换 `backend/report_writing.py` 中 `check_no_prior_canonical_mutation_in_turn` 整个函数。**保留函数名** 以便 import 不破，但内部改用新字段：

```python
MAX_CANONICAL_MUTATIONS_PER_TURN = 3


def check_no_prior_canonical_mutation_in_turn(turn_context: Dict[str, Any]) -> Optional[str]:
    """Block when this turn's canonical-draft mutations have hit the per-turn cap.

    与旧实现的差异：
      - 字段名 `canonical_draft_mutation` (dict) → `canonical_draft_mutations` (list)
      - 阈值 1 → MAX_CANONICAL_MUTATIONS_PER_TURN (= 3)
      - 错误消息附带 mutations 列表摘要供模型对照

    新字段 schema 见 spec §3.4.1。
    """
    mutations = turn_context.get("canonical_draft_mutations") or []
    if not isinstance(mutations, list):
        mutations = []
    if len(mutations) < MAX_CANONICAL_MUTATIONS_PER_TURN:
        return None
    summary_lines = []
    for i, m in enumerate(mutations):
        if not isinstance(m, dict):
            continue
        summary_lines.append(
            f"  {i+1}. {m.get('canonical_action', '?')} "
            f"{m.get('target_label', '?')} "
            f"(old={m.get('old_len', 0)} → new={m.get('new_len', 0)})"
        )
    summary = "\n".join(summary_lines) if summary_lines else "  (无可解析摘要)"
    return (
        f"本轮已经成功修改正文草稿 {len(mutations)} 次，达到上限 "
        f"{MAX_CANONICAL_MUTATIONS_PER_TURN}。\n"
        f"已完成的修改：\n{summary}\n"
        f"请等用户回应再做下一次修改。"
    )
```

注意：**不**改 chat.py 中调用此 helper 的位置；本 task 仅改 helper 内部 + 字段名。chat.py 的写盘代码暂时仍写老字段（旧路径），新 dispatcher 用新字段。Commit 3 才统一字段名。

- [ ] **Step 5: Run test → pass**

```bash
.venv\Scripts\python -m pytest tests/test_report_writing.py::MutationLimit3Tests -q
```

Expected: 5 PASS

- [ ] **Step 6: 跑 report_writing 整套确认无 regression**

```bash
.venv\Scripts\python -m pytest tests/test_report_writing.py -q
```

Expected: ALL PASS（旧 test 用老字段断言可能 fail；如有，标记为 deprecated 或调整 fixture 用新字段）。

### Task 8: `report_writing.py` `check_read_before_write_canonical_draft` 加 within-turn self-refresh

**Files:**
- Modify: `backend/report_writing.py:check_read_before_write_canonical_draft`
- Test: `tests/test_report_writing.py`

- [ ] **Step 1: 读现有 helper**

```bash
.venv\Scripts\python -c "import inspect; from backend.report_writing import check_read_before_write_canonical_draft; print(inspect.getsource(check_read_before_write_canonical_draft))"
```

理解原签名（参数名、返回值）。

- [ ] **Step 2: 写新行为测试**

`tests/test_report_writing.py` 末尾追加：

```python
class ReadBeforeWriteSelfRefreshTests(unittest.TestCase):
    """spec §3.4.3: 第一次写入后 mtime 必然变。第二次写入若仍要 read-before-write
    会被自己制造的 stale snapshot 卡住。helper 应识别 last self mutation."""

    def _ctx_with_self_mutation(self, mtime_after):
        return {
            "canonical_draft_mutations": [{
                "tool": "edit_file", "canonical_action": "text_replace",
                "target_label": "m1", "old_len": 1, "new_len": 1,
                "mtime_after": mtime_after, "ts": 0.0,
            }],
            "last_read_mtime": {},  # 故意空：第一次写后没人 read
        }

    def test_skip_when_current_mtime_matches_last_self_mutation(self):
        from backend.report_writing import check_read_before_write_canonical_draft
        ctx = self._ctx_with_self_mutation(mtime_after=12345.0)

        def fake_stat(path):
            class S:
                st_mtime = 12345.0
            return S()

        # 注：实际签名见 helper；此处假设接受 (turn_context, draft_path, *, stat_func=os.stat)
        # 若签名不同，按实际改造测试调用
        result = check_read_before_write_canonical_draft(
            ctx, "content/report_draft_v1.md", stat_func=fake_stat,
        )
        self.assertIsNone(result, "self-mutation 后 mtime 未被外部改动，应跳过 read 要求")

    def test_block_when_someone_else_modified_after_self_mutation(self):
        from backend.report_writing import check_read_before_write_canonical_draft
        ctx = self._ctx_with_self_mutation(mtime_after=12345.0)

        def fake_stat(path):
            class S:
                st_mtime = 99999.0  # 比 last_self_mutation 新 → 外部改动
            return S()

        result = check_read_before_write_canonical_draft(
            ctx, "content/report_draft_v1.md", stat_func=fake_stat,
        )
        self.assertIsNotNone(result)

    def test_first_write_no_mutations_yet_falls_through(self):
        # 没有自写过 → 走原 read-before-write 路径
        from backend.report_writing import check_read_before_write_canonical_draft
        ctx = {"canonical_draft_mutations": [], "last_read_mtime": {}}

        def fake_stat(path):
            class S:
                st_mtime = 5000.0
            return S()

        result = check_read_before_write_canonical_draft(
            ctx, "content/report_draft_v1.md", stat_func=fake_stat,
        )
        # 期望返回非 None（要求先 read）—— 视原 helper 是否支持 first-write 例外
        # 若 first-write 例外存在（draft 不存在），调整测试为 mock os.path.exists False
        self.assertIsNotNone(result)
```

- [ ] **Step 3: Run test → fail**

```bash
.venv\Scripts\python -m pytest tests/test_report_writing.py::ReadBeforeWriteSelfRefreshTests -q
```

Expected: FAIL（self-refresh 逻辑还没加；行为可能不一致）

- [ ] **Step 4: 改 helper（保留原签名 `(turn_context, skill_engine, project_id, *, require_read=...)`，仅在内部加 self-refresh 短路）**

**重要**（fixed per R1 P0-2）：现有 helper 真实签名是
`check_read_before_write_canonical_draft(turn_context, skill_engine, project_id, *, require_read=...)`。
**不要替换签名**为 `(turn_context, draft_path)`，调用契约必须保持兼容（dispatcher 入口和现有 4 个 mutation 路径都按这个签名调）。

实施步骤：

```python
# 1. 先读原 helper 完整源码：
# .venv\Scripts\python -c "import inspect; from backend.report_writing import check_read_before_write_canonical_draft; print(inspect.getsource(check_read_before_write_canonical_draft))"

# 2. 在函数体**最前面**加 self-refresh 短路；保留原参数 + 原后续逻辑不动：

def check_read_before_write_canonical_draft(
    turn_context,
    skill_engine,
    project_id,
    *,
    require_read=True,
    # NEW（仅测试用注入；生产代码不传）：
    stat_func=None,
):
    """spec §3.4.3 within-turn self-refresh：若本轮已自己写过且当前 mtime
    等于 last self mutation mtime，跳过 read 要求（自己写完不必再 read 自己的输出）。

    非 test 调用方继续用原 3-arg 签名 + require_read kwarg；本次新增的
    stat_func 只用于单测注入，生产代码用 os.stat。
    """
    import os
    
    # NEW self-refresh 短路（在原逻辑前）：
    mutations = turn_context.get("canonical_draft_mutations") or []
    if isinstance(mutations, list) and mutations:
        last_self = mutations[-1]
        if isinstance(last_self, dict):
            last_self_mtime = last_self.get("mtime_after")
            if isinstance(last_self_mtime, (int, float)):
                # 用 skill_engine + project_id 解析 draft 真实 path
                draft_path = _resolve_canonical_draft_path(skill_engine, project_id)
                if draft_path is not None:
                    _stat = stat_func if stat_func is not None else os.stat
                    try:
                        current_mtime = _stat(draft_path).st_mtime
                        if current_mtime == last_self_mtime:
                            return None  # within-turn self-refresh skip
                    except OSError:
                        pass  # 文件不存在等 → 走原逻辑
    
    # 原 read-before-write 逻辑（不要重写，保留现有完整 body）
    # ... [现有 require_read / last_read_mtime 比较 / 错误消息] 保留不动 ...
```

**关键约束**：
- **不**替换原 3-arg 签名
- **不**改原 require_read kwarg 行为
- 仅在函数顶部加 short-circuit
- 若原 helper 内部已有 path 解析（grep 现有代码看），复用同一 helper（如 `_resolve_canonical_draft_path` 或类似名），不重复造轮子

- [ ] **Step 5: 更新测试 fixture 签名**

Step 1 写的测试 stub 用了 `(turn_context, "content/report_draft_v1.md", stat_func=fake_stat)` 简化签名；**实施时调整为真实签名**：

```python
def test_skip_when_current_mtime_matches_last_self_mutation(self):
    from backend.report_writing import check_read_before_write_canonical_draft
    handler, project_id = self._setup_handler_with_canonical_draft(...)  # fixture
    ctx = self._ctx_with_self_mutation(mtime_after=12345.0)

    def fake_stat(path):
        class S:
            st_mtime = 12345.0
        return S()

    result = check_read_before_write_canonical_draft(
        ctx, handler.skill_engine, project_id,
        require_read=True,
        stat_func=fake_stat,
    )
    self.assertIsNone(result)
```

- [ ] **Step 5: Run test → pass**

```bash
.venv\Scripts\python -m pytest tests/test_report_writing.py::ReadBeforeWriteSelfRefreshTests -q
```

Expected: PASS（部分；若签名 mismatch 调整测试 fixture 后再 PASS）

- [ ] **Step 6: 跑 report_writing 整套**

```bash
.venv\Scripts\python -m pytest tests/test_report_writing.py -q
```

Expected: ALL PASS（旧 read-before-write 测试不应破坏）

### Task 9: `chat.py` `s0_confirmation_completed` 字段 + state load 兼容 + turn-context inject

**Files:**
- Modify: `backend/chat.py:_empty_conversation_state` (line 746)
- Modify: `backend/chat.py:_load_conversation_state` (line 840)
- Modify: `backend/chat.py:_new_turn_context` (line 5523) — 加 `s0_confirmation_completed` 默认
- Modify: `backend/chat.py` turn-start point — 把 conversation_state 中的 flag 灌进 turn_context

**关键背景**（fixed per R1 P0-1）：spec §6.1 要求老 state 文件**缺字段时缺省 True**（不强制老用户重走 S0），但若我们在 `_empty_conversation_state` 默认 False，merge 后字段永远存在 → 老 state load 时永远走默认路径，不会触发"老用户缺字段=True"的判定。修法：在 `_load_conversation_state` 中**判断 raw payload**（disk JSON）是否含字段，再根据 disk vs memory 分支决定缺省值。

- [ ] **Step 1: 写测试 — 三场景 round-trip**

`tests/test_chat_runtime.py` 末尾追加：

```python
class S0ConversationStateRoundtripTests(unittest.TestCase):
    """spec §3.2.1 + §6.1: 三种 case 的缺省行为
    
    场景 A: 全新项目，无 disk file → False（首次 S0 gate 生效）
    场景 B: 老项目，disk file 不含字段 → True（spec §6.1 老用户兼容）
    场景 C: 老项目，disk file 已含字段 → 按 disk 值
    """

    def test_new_project_no_state_file_defaults_false(self):
        # 用 tmp dir 创建项目目录但不写 conversation_state.json
        # 调 _load_conversation_state → result["s0_confirmation_completed"] is False
        pass

    def test_legacy_state_file_without_field_defaults_true(self):
        # 在项目目录写 conversation_state.json without s0_confirmation_completed key
        # 调 _load_conversation_state → result["s0_confirmation_completed"] is True
        pass

    def test_modern_state_file_with_field_keeps_value(self):
        # disk JSON 含 s0_confirmation_completed=False → load 后保留 False
        pass

    def test_state_save_load_round_trip_preserves_field(self):
        # 创建 state with field=True → save → re-load → field 仍 True
        pass

    def test_turn_context_inherits_flag_from_loaded_state(self):
        # mock state with field=True → call turn-start helper
        # → turn_context["s0_confirmation_completed"] == True
        pass
```

`pass` body 实施按 "Test Fixture Conventions" 章节的 `_make_handler_with_state` adapter 填。

- [ ] **Step 2: Run test → fail**

```bash
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::S0ConversationStateRoundtripTests -q
```

Expected: FAIL

- [ ] **Step 3: 改 `_empty_conversation_state`（默认 False）**

`backend/chat.py` line 746：

```python
def _empty_conversation_state(self) -> Dict:
    return {
        "version": 1,
        "events": [],
        "memory_entries": [],
        "compact_state": None,
        "draft_followup_state": None,
        "s0_confirmation_completed": False,  # NEW per spec §3.2.1; 全新项目缺省 False
    }
```

- [ ] **Step 4: 改 `_load_conversation_state` — 区分 disk-payload 缺字段 vs 全新 state**

读现有 `_load_conversation_state`（line 840）：

```bash
.venv\Scripts\python -c "import inspect; from backend.chat import ChatHandler; print(inspect.getsource(ChatHandler._load_conversation_state))"
```

修改逻辑（**保留现有 isinstance + 字段级 copy 风格**，fixed per R2 new-introduced #2，**不**用 `{**empty_state, **raw}`——会破坏现有 type-normalize 防线，corruption 数据可能写入非预期类型）：

```python
def _load_conversation_state(self, project_id, history=None):
    # ... 现有 lock + path lookup ...
    state_path = self._get_conversation_state_path(project_id)
    empty_state = self._empty_conversation_state()
    
    if not state_path or not state_path.exists():
        # 全新项目：返回 empty_state（s0_confirmation_completed=False, 首轮 gate 生效）
        # ... 现有 legacy compact_state 兼容逻辑保留 ...
        return empty_state
    
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        self._rename_broken_sidecar(state_path)
        return empty_state
    
    if not isinstance(raw, dict):
        # 非 dict（罕见 corruption）当老 state 处理
        return empty_state
    
    # 沿用现有 field-by-field type-normalize（参考 _save_conversation_state_atomically
    # line 884-892 的相同 isinstance 校验风格）：
    state = self._empty_conversation_state()  # 起点 default 全字段
    if isinstance(raw.get("events"), list):
        state["events"] = raw["events"]
    if isinstance(raw.get("memory_entries"), list):
        state["memory_entries"] = raw["memory_entries"]
    state["compact_state"] = self._normalize_compact_state(raw.get("compact_state"))
    if isinstance(raw.get("draft_followup_state"), dict):
        state["draft_followup_state"] = raw["draft_followup_state"]
    
    # NEW per spec §3.2.1 + §6.1 + R2 new-introduced #2:
    # 关键 — disk raw payload 中**没有**该 key → 老 state，缺省 True（不强制重走 S0）
    # 有 key 且 bool → 沿用 disk 值
    # 有 key 但非 bool（corruption）→ 当老 state 处理，缺省 True（保守）
    if "s0_confirmation_completed" in raw:
        s0_value = raw["s0_confirmation_completed"]
        state["s0_confirmation_completed"] = s0_value if isinstance(s0_value, bool) else True
    else:
        state["s0_confirmation_completed"] = True  # legacy default per spec §6.1
    
    return state
```

**关键修法（fixed per R2）**：
- **保留**现有 `isinstance(raw.get(field), expected_type)` 检查风格——与 `_save_conversation_state_atomically` 对称
- 不用 `{**empty_state, **raw}`：merge spread 会绕过 type-normalize，恶意/损坏 disk JSON 可能注入非预期类型字段
- disk-payload `s0_confirmation_completed` key 检测在 merge **之前**做（`"s0_confirmation_completed" in raw`），保证老 state 缺字段走 `else` 分支 → True
- 上面 Task 9 step 3 已让 `_empty_conversation_state` 默认 False，**因此本 step 必须显式覆盖**——否则缺 key 的老 state 会被默认 False 误拦

- [ ] **Step 4b: 改 `_save_conversation_state_atomically`（line 878）加字段到白名单**

**关键**（fixed per R2 P0-1 PARTIAL）：现有 `_save_conversation_state_atomically` 是**显式 field-by-field copy**——line 884-892 只 copy `events` / `memory_entries` / `compact_state` / `draft_followup_state`。如果不加新字段到此白名单，Task 11 写入 `s0_confirmation_completed=True` 后 save 会被悄悄丢掉（merge 起点是 `_empty_conversation_state()`，新字段 always False）。

修法：在 line 884 `if isinstance(payload, dict):` 块末尾追加：

```python
        if isinstance(payload, dict):
            # ... 现有 events / memory_entries / compact_state / draft_followup_state copy ...
            
            # NEW per spec §3.2.1 + R2 P0-1
            s0_flag = payload.get("s0_confirmation_completed")
            if isinstance(s0_flag, bool):
                state["s0_confirmation_completed"] = s0_flag
```

注意：用 `isinstance(..., bool)` 严格类型检查（不接受 truthy/falsy 其他类型，避免 disk corruption 写入 string/int 后误读）。

测试加 case：

```python
def test_save_load_round_trip_persists_s0_field(self):
    """fixed per R2 P0-1: atomic save 白名单包含 s0_confirmation_completed。"""
    # 写一份 state with field=True 调 _save_conversation_state_atomically
    # → re-load 读出来字段 == True
    # → assertEqual reloaded["s0_confirmation_completed"], True
    pass

def test_save_drops_invalid_s0_field_type(self):
    """非 bool 不写入。"""
    # payload["s0_confirmation_completed"] = "yes"  # str, not bool
    # save → load → 字段 == False（来自 _empty_conversation_state default）
    pass
```

- [ ] **Step 5: 改 `_new_turn_context`（line 5523）加默认 `s0_confirmation_completed`**

```python
def _new_turn_context(self, *, can_write_non_plan: bool) -> Dict[str, object]:
    return {
        # ... 现有字段
        "canonical_draft_mutation": None,
        # NEW（spec §3.2）：默认 True；turn-start 实际值由调用方从 conversation_state 灌入
        "s0_confirmation_completed": True,
    }
```

注意默认 True 的理由：避免任何漏 inject 场景误拦合法老 turn。**真正的 first-turn gate 依赖调用方把 disk state 灌进来**。

- [ ] **Step 6: 改 turn-start：把 conversation_state 的 flag 灌进 turn_context**

`backend/chat.py` 找到调 `_new_turn_context(...)` 的位置（grep `self._new_turn_context\|_new_turn_context(`），在那之后立即从 `_load_conversation_state` 取 `s0_confirmation_completed` 灌入：

```python
# 现有：
self._turn_context = self._new_turn_context(can_write_non_plan=...)

# 新增（紧跟其后）：
loaded_state = self._load_conversation_state(project_id, history=...)
self._turn_context["s0_confirmation_completed"] = loaded_state.get(
    "s0_confirmation_completed", True
)
```

如果 `_load_conversation_state` 已在该处被调用，复用其结果而不是重复 load。

- [ ] **Step 7: 改解锁逻辑（Task 11 中 finalize）的落盘 path**

flag flip True 时落盘到 conversation_state.json — 该步骤在 Task 11 step 3。**不要重复实现**；本 task 仅确保 inject 逻辑正确。

- [ ] **Step 8: Run test → pass**

```bash
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::S0ConversationStateRoundtripTests -q
```

Expected: PASS

### Task 10: `chat.py` `_execute_tool` 入口加 first-turn gate

**Files:**
- Modify: `backend/chat.py:_execute_tool` (line 3657)
- Test: `tests/test_chat_runtime.py`

- [ ] **Step 1: 写测试**

`tests/test_chat_runtime.py` 末尾追加：

```python
class S0FirstTurnGateTests(unittest.TestCase):
    """spec §3.2.2 / §3.2.3: first-turn 工具白名单 + reject 行为。"""

    S0_WHITELIST = {"read_file", "read_material_file", "web_search", "fetch_url"}

    def test_whitelist_constant_exists(self):
        from backend.chat import S0_FIRST_TURN_ALLOWED_TOOLS
        self.assertEqual(set(S0_FIRST_TURN_ALLOWED_TOOLS), self.S0_WHITELIST)

    def test_whitelist_tool_passes_when_flag_false(self):
        # write_file / edit_file / append_report_draft 在 S0 first turn 应被 reject
        # web_search / read_file 应通过
        # 实施时用现有 _execute_tool fixture mock 出 turn_context with
        # s0_confirmation_completed=False, 调 _execute_tool with each tool
        pass

    def test_writer_tool_rejected_when_flag_false(self):
        pass

    def test_writer_tool_passes_when_flag_true(self):
        pass

    def test_reject_message_mentions_clarify(self):
        # 拒绝消息文案应含"澄清/确认/补充问题"等关键词
        pass
```

实施 step 4 后再补全 Step 1 中的 pass test 体（参考现有 `_execute_tool` 单测的 fixture 写法）。

- [ ] **Step 2: 在 `chat.py` 顶部 import 区或常量区加白名单**

找一处合适常量区（grep `^[A-Z_]+ = `），加：

```python
S0_FIRST_TURN_ALLOWED_TOOLS = frozenset({
    "read_file",
    "read_material_file",
    "web_search",
    "fetch_url",
})
```

- [ ] **Step 3: 在 `_execute_tool` 入口加 gate**

`_execute_tool` (line 3657) 函数体最前面，在任何工具分发之前：

```python
def _execute_tool(self, project_id: str, tool_call):
    func_name = tool_call.function.name
    turn_context = self._get_or_init_turn_context(project_id)  # 现有获取方式
    # spec §3.2.2 — first-turn S0 gate
    if not turn_context.get("s0_confirmation_completed", True):
        if func_name not in S0_FIRST_TURN_ALLOWED_TOOLS:
            return {
                "status": "error",
                "message": (
                    "首轮项目澄清。请先以纯文本输出 3-5 个针对 seed"
                    "（项目主题/受众/范围/边界）的确认/补充问题，"
                    "等用户回答后再使用其他工具。"
                ),
            }
    # ... 原工具分发逻辑
```

注意：`_get_or_init_turn_context` 是占位伪代码——按实际 chat.py 中获取 turn_context 的真实方式调整（grep `turn_context` 看现有 pattern）。

- [ ] **Step 4: 补全 step 1 的 pass test 体（用 ChatHandler fixture）**

参考已有 `_execute_tool` 测试的 fixture 写法：

```python
def test_writer_tool_rejected_when_flag_false(self):
    handler = self._make_handler_with_state(
        project_id="proj-x",
        turn_context={"s0_confirmation_completed": False},
    )
    result = handler._execute_tool("proj-x", _make_tool_call("edit_file", {"file_path":"x.md","old_string":"a","new_string":"b"}))
    self.assertEqual(result.get("status"), "error")
    self.assertIn("首轮", result.get("message", ""))
```

`_make_handler_with_state` / `_make_tool_call` 按现有 helper 复用。

- [ ] **Step 5: Run test → pass**

```bash
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::S0FirstTurnGateTests -q
```

Expected: PASS

### Task 11: `chat.py` `_finalize_assistant_turn` 加 S0 解锁逻辑

**Files:**
- Modify: `backend/chat.py:_finalize_assistant_turn` (line 5873)
- Test: `tests/test_chat_runtime.py`

- [ ] **Step 1: 写测试**

`tests/test_chat_runtime.py` 末尾追加 `S0FirstTurnUnlockTests`：

```python
class S0FirstTurnUnlockTests(unittest.TestCase):
    """spec §3.2.3: 解锁双条件 = 没调过非白名单工具 AND 文本非空。"""

    def test_unlock_when_only_whitelist_tools_called_and_text_nonempty(self):
        # mock turn_tool_calls = [web_search] + assistant_text = "我先了解一下"
        # 调 finalize → s0_confirmation_completed flips True
        pass

    def test_no_unlock_when_text_empty(self):
        # mock tool_calls=[web_search], assistant_text=""
        # finalize → flag remains False
        pass

    def test_no_unlock_when_writer_tool_attempted(self):
        # 即便 write_file 被 dispatcher reject，仍计入 non_whitelist_called
        pass

    def test_persisted_to_conversation_state_json(self):
        # finalize 后调 _load_conversation_state，flag = True
        pass
```

- [ ] **Step 2: Run test → fail**

```bash
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::S0FirstTurnUnlockTests -q
```

Expected: FAIL

- [ ] **Step 3: 在 `_finalize_assistant_turn` 末段加解锁逻辑**

`_finalize_assistant_turn` 函数体最后（在 conversation_state save 之前）：

```python
# spec §3.2.3 — S0 first-turn unlock
turn_context = self._get_or_init_turn_context(project_id)  # actual access pattern
if not turn_context.get("s0_confirmation_completed", True):
    non_whitelist_called = any(
        getattr(c.function, "name", None) not in S0_FIRST_TURN_ALLOWED_TOOLS
        for c in (turn_tool_calls or [])
    )
    text_nonempty = bool((assistant_message_text or "").strip())
    if (not non_whitelist_called) and text_nonempty:
        turn_context["s0_confirmation_completed"] = True
        # 落盘到 conversation_state.json
        def _flip(state):
            state["s0_confirmation_completed"] = True
            return state
        self._mutate_conversation_state(project_id, _flip)
```

注意：`turn_tool_calls` / `assistant_message_text` 名称按 `_finalize_assistant_turn` 实际签名调整。

- [ ] **Step 4: Run test → pass**

```bash
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::S0FirstTurnUnlockTests -q
```

Expected: PASS

### Task 12: `skill/SKILL.md` 加 §S0 first-turn 硬约束 + system_prompt 透传

> **TDD exception: doc/asset-only**（fixed per R1 P2-2）。SKILL.md 是 prompt 资产，验证落点是渲染后的 system prompt 是否包含新段落 + 现有 skill_engine / skill_assets 测试不破，没有"写 test → fail" 序列。


**Files:**
- Modify: `skill/SKILL.md`
- Modify: `backend/chat.py:_build_system_prompt` (line 5418) — 仅确认 SKILL.md 文本被 prompt 包含；如已通过 SKILL.md 包含则无需改 chat.py

- [ ] **Step 1: 找到 SKILL.md §S0 章节**

```bash
grep -n "^##.*S0" skill/SKILL.md
```

- [ ] **Step 2: 在 §S0 段落末尾追加首轮硬约束块**

参考 spec §3.2.4 文案：

```markdown
### 首轮硬约束

项目第一次响应：

1. 你可以先用 `web_search` / `fetch_url` 搜主题相关内容、用 `read_file` 读 seed 和已上传材料；
2. 然后必须以纯文本输出 3-5 个针对 seed（项目主题/受众/范围/边界）的确认/补充问题；
3. 不允许调用任何写工具（`write_file` / `edit_file` / `append_report_draft`）；
4. 即便用户首条说"直接推进 / 不用每步都问"，第一轮仍要发问 ——
   但格式可以轻：复述你的理解 + 1-2 个真正需要拍板的点。
```

- [ ] **Step 3: 检查 `_build_system_prompt` 是否包含完整 SKILL.md**

```bash
.venv\Scripts\python -c "import inspect; from backend.chat import ChatHandler; print(inspect.getsource(ChatHandler._build_system_prompt))"
```

确认 SKILL.md 全文（含新加段落）会进入 system prompt。如果是 selective 摘录，需要确保新加段落被摘录。

- [ ] **Step 4: 跑 SKILL 渲染相关测试**

```bash
.venv\Scripts\python -m pytest tests/test_skill_engine.py tests/test_skill_assets.py -q
```

Expected: ALL PASS（如有 SKILL.md 内容断言，可能需更新 fixture）

### Task 13: `chat.py` `canonical_obligation` 字段（与旧 `canonical_draft_write_obligation` 并存）

**Files:**
- Modify: `backend/chat.py`（在 turn-start 设置 obligation）
- Test: `tests/test_chat_runtime.py`

- [ ] **Step 1: 写测试**

`tests/test_chat_runtime.py` 末尾追加：

```python
class CanonicalObligationFieldTests(unittest.TestCase):
    """spec §3.5.4: turn-start 写入 canonical_obligation。"""

    def test_generative_message_sets_intent_generative(self):
        # 用户消息含"续写下一章"
        # turn-start 后 turn_context["canonical_obligation"] == {"intent":"generative", "expected_action":"append"}
        pass

    def test_modify_message_sets_intent_modify(self):
        # 用户消息"把 X 改成 Y"
        # turn-start 后 obligation == {"intent":"modify", "expected_action":"any_canonical_write"}
        pass

    def test_ambiguous_message_sets_no_obligation(self):
        # 用户消息"看下背景资料"
        # obligation == {"intent": None, "expected_action": None}
        pass

    def test_legacy_field_still_populated(self):
        # 旧字段 canonical_draft_write_obligation 暂时也被写入（Commit 1 并存）
        pass
```

- [ ] **Step 2: 找到 turn-start 处**

```bash
grep -n "canonical_draft_write_obligation\s*=" backend/chat.py | head
```

定位现有 `detect_canonical_draft_write_obligation` 调用处（应该是 turn-start 用 user_message 调）。

- [ ] **Step 3: 在该位置同时写入 `canonical_obligation`**

```python
# 现有：
old_obl = detect_canonical_draft_write_obligation(user_message)
turn_context["canonical_draft_write_obligation"] = old_obl  # legacy

# 新增（并存，Commit 3 删 legacy）：
from backend.report_writing import detect_user_message_intent
intent = detect_user_message_intent(user_message)
expected_action = {
    "generative": "append",
    "modify": "any_canonical_write",
    "ambiguous": None,
}[intent]
turn_context["canonical_obligation"] = {
    "intent": None if intent == "ambiguous" else intent,
    "expected_action": expected_action,
}
```

- [ ] **Step 4: Run test → pass**

```bash
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::CanonicalObligationFieldTests -q
```

Expected: PASS

### Task 14: `chat.py` `_execute_tool` `edit_file` canonical dispatcher

**Files:**
- Modify: `backend/chat.py:_execute_tool` edit_file 分支 (line 3683)
- Test: `tests/test_chat_runtime.py`

- [ ] **Step 1: 写测试 — `EditFileCanonicalDispatcherTests`**

`tests/test_chat_runtime.py` 末尾追加：

```python
class EditFileCanonicalDispatcherTests(unittest.TestCase):
    """spec §3.1.2: canonical draft 路径分派。"""
    CANONICAL = "content/report_draft_v1.md"

    DRAFT = (
        "# 报告标题\n"
        "## 第一章 引言\n"
        "引言段\n"
        "## 第二章 战略\n"
        "战略段\n"
    )

    def test_section_rewrite_via_h2_anchor(self):
        # old_string="## 第二章 战略" + new_string=完整新章节
        # → canonical_action="section_rewrite"
        # → mutations 列表新增一项
        pass

    def test_full_rewrite_requires_user_keyword(self):
        # old_string="# 报告标题" + user_message 无"整篇/推倒/全文"关键词
        # → reject
        pass

    def test_full_rewrite_with_keyword_passes(self):
        # old_string="# 报告标题" + user_message="整篇重写"
        # → canonical_action="full_rewrite"
        pass

    def test_text_replace_unique_match(self):
        # old_string="引言段" + new_string="新引言"
        # → canonical_action="text_replace"
        pass

    def test_text_replace_non_unique_rejected(self):
        # old_string 在 draft 中出现 2+ 次 → reject
        pass

    def test_section_delete(self):
        # old_string="## 第二章 战略" + new_string=""
        # → canonical_action="section_delete"
        pass

    def test_text_delete(self):
        # old_string="引言段" + new_string=""
        # → canonical_action="text_delete"
        pass

    def test_empty_old_string_rejected_with_append_hint(self):
        # old_string="" → reject + 文案含"append_report_draft"
        pass

    def test_anchor_label_not_in_draft_rejected(self):
        # old_string="## 第十章 不存在" → reject "锚点章节未在 draft 中唯一匹配"
        pass

    def test_single_line_h1_goes_text_replace_not_full_rewrite(self):
        # old_string="# 报告标题" but 不等于 draft 第一行
        # → 按 spec line 153 走 text_replace 分支（防止误判）
        pass

    def test_non_canonical_path_uses_generic_edit(self):
        # file_path="some/other.md" → 走 _generic_edit_file，不进入分派
        pass

    def test_post_hoc_generative_intent_blocks_edit(self):
        # user_message 是 generative ("续写下一章") + 调 edit_file
        # → reject + 文案"用户消息看起来是想新增内容...请用 append_report_draft"
        pass


class EditFileCanonicalInvariantRejectTests(unittest.TestCase):
    """spec §5.1 + R1 P1-4：6 个共享 invariant 各自独立 reject + within-turn self-refresh skip。"""
    CANONICAL = "content/report_draft_v1.md"

    def test_stage_lt_s4_rejected(self):
        # check_report_writing_stage 未通过 → reject + result["message"] 含 "阶段"
        pass

    def test_outline_not_confirmed_rejected(self):
        # check_outline_confirmed 未通过 → reject + 含 "outline" / "大纲"
        pass

    def test_mixed_intent_rejected(self):
        # turn 内已有 secondary action family（如 plan 写入）→ reject
        pass

    def test_mutation_limit_full_rejected(self):
        # turn_context["canonical_draft_mutations"] 已 3 条 → reject + 含 "上限"
        pass

    def test_no_read_before_write_rejected(self):
        # turn_context["last_read_mtime"] 缺 canonical path 且无 self-mutation
        # → reject + 含 "read_file"
        pass

    def test_within_turn_self_refresh_skips_read_check(self):
        # 第一次 dispatcher 写入成功 → mutations[0].mtime_after = T
        # 第二次同 turn 调 dispatcher，无 last_read_mtime 但 stat → T
        # → 不 reject，正常走（self-refresh 短路生效）
        pass

    def test_fetch_url_pending_rejected(self):
        # turn_context["web_search_performed"]=True + "fetch_url_performed"=False
        # → reject + 含 "fetch_url"
        pass


class EditFileGenericRegressionTests(unittest.TestCase):
    """spec §5.1: 非 canonical 路径行为不变（与 dispatcher 不互相影响）。"""

    def test_edit_other_md_uses_generic_no_invariants_run(self):
        # file_path="other/notes.md"
        # → 走 _generic_edit_file，不走 6 invariants
        # 即便 stage<S4 / outline 未确认 / mutations 已满 也不 reject
        pass

    def test_edit_other_md_with_unique_match_succeeds(self):
        # 普通 edit 行为不变
        pass

    def test_edit_other_md_non_unique_old_string_rejected_by_generic(self):
        # 沿用 generic edit_file 的 unique-match 检查（如有）
        pass
```

- [ ] **Step 2: Run → fail**

```bash
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::EditFileCanonicalDispatcherTests tests/test_chat_runtime.py::EditFileCanonicalInvariantRejectTests tests/test_chat_runtime.py::EditFileGenericRegressionTests -q
```

Expected: FAIL（dispatcher 不存在）

- [ ] **Step 3: 实现 dispatcher**

`backend/chat.py` 找 `_execute_tool` 中 `edit_file` 分支（line 3683）。把现有 generic 实现抽成 `_generic_edit_file(...)` 私有方法（保留原签名/行为），新加 `_dispatch_edit_file(...)` 在 `_execute_tool` 中调用：

```python
# in _execute_tool, edit_file 分支：
if func_name == "edit_file":
    args = json.loads(tool_call.function.arguments) if isinstance(...) else ...
    file_path = args.get("file_path")
    old_string = args.get("old_string", "")
    new_string = args.get("new_string", "")
    return self._dispatch_edit_file(project_id, file_path, old_string, new_string, turn_context)


def _dispatch_edit_file(self, project_id, file_path, old_string, new_string, turn_context):
    canonical_path = "content/report_draft_v1.md"
    if file_path != canonical_path:
        return self._generic_edit_file(project_id, file_path, old_string, new_string)

    # canonical draft 分支
    from backend.report_writing import (
        resolve_section_anchor,
        detect_user_message_intent,
        check_report_writing_stage,
        check_outline_confirmed,
        check_no_mixed_intent_in_turn,
        check_no_prior_canonical_mutation_in_turn,
        check_read_before_write_canonical_draft,
        check_no_fetch_url_pending,
    )

    if not old_string or not old_string.strip():
        return {"status": "error", "message":
                "edit_file 需要 old_string 锚点；新增内容请用 append_report_draft"}

    draft_full_path = self._project_canonical_draft_path(project_id)  # 实际 helper
    draft = draft_full_path.read_text(encoding="utf-8") if draft_full_path.exists() else ""

    canonical_action = None
    actual_old = None
    target_label = None

    if old_string.startswith("## "):
        snapshot = resolve_section_anchor(old_string, draft)
        if snapshot is None:
            return {"status": "error", "message": "锚点章节未在 draft 中唯一匹配"}
        canonical_action = "section_rewrite" if new_string else "section_delete"
        actual_old = snapshot
        target_label = old_string.split("\n", 1)[0].strip()
    elif (old_string.startswith("# ")
          and old_string.strip() == draft.split("\n", 1)[0].strip()):
        # 整篇重写：old_string 必须等于 draft 第一行 h1
        user_msg = turn_context.get("user_message_text", "") or ""
        if not any(kw in user_msg for kw in ("整篇", "推倒", "全文重写", "重写整")):
            return {"status": "error", "message":
                    "整篇重写需要用户明确说'整篇/推倒/全文重写'。"
                    "局部修改请用 ## 锚点；新增请用 append_report_draft"}
        canonical_action = "full_rewrite"
        actual_old = draft
        target_label = "<full draft>"
    else:
        # 包括 single-line h1（"# 旧标题" → "# 新标题"）走 text_replace
        if draft.count(old_string) != 1:
            return {"status": "error", "message":
                    "old_string 必须在 draft 中唯一出现"}
        canonical_action = "text_replace" if new_string else "text_delete"
        actual_old = old_string
        target_label = f"{old_string[:30]} → {new_string[:30]}"

    # 共享 invariants（fixed per R1 P0-2: use REAL signatures from
    # backend/report_writing.py:105/119/132/148/157/190）
    user_msg = turn_context.get("user_message_text", "") or ""
    for check, args, kwargs in [
        (check_report_writing_stage, (self.skill_engine, project_id), {}),
        (check_outline_confirmed, (self.skill_engine, project_id), {}),
        (check_no_mixed_intent_in_turn, (self, user_msg), {}),
        (check_no_prior_canonical_mutation_in_turn, (turn_context,), {}),
        (check_no_fetch_url_pending, (turn_context,), {}),
        (check_read_before_write_canonical_draft,
         (turn_context, self.skill_engine, project_id), {"require_read": True}),
    ]:
        err = check(*args, **kwargs)
        if err:
            return {"status": "error", "message": err}  # 注意 "message" 字段（per R1 P1-1）

    # post-hoc reverse intent
    user_msg = turn_context.get("user_message_text", "") or ""
    if detect_user_message_intent(user_msg) == "generative":
        return {"status": "error", "message":
                "用户消息看起来是想新增内容（起草/续写/写下一章），"
                "请用 append_report_draft；edit_file 是改已有内容"}

    # 写盘
    new_draft = draft.replace(actual_old, new_string, 1)
    draft_full_path.write_text(new_draft, encoding="utf-8")
    new_mtime = draft_full_path.stat().st_mtime

    # append mutation
    mutations = turn_context.setdefault("canonical_draft_mutations", [])
    mutations.append({
        "tool": "edit_file",
        "canonical_action": canonical_action,
        "target_label": target_label,
        "old_len": len(actual_old),
        "new_len": len(new_string),
        "mtime_after": new_mtime,
        "ts": time.time(),
    })

    return {
        "status": "success",
        "canonical_action": canonical_action,
        "target_label": target_label,
        "old_len": len(actual_old),
        "new_len": len(new_string),
    }
```

- [ ] **Step 4: 把现有 edit_file 实现抽成 `_generic_edit_file`**

把 line 3683 现有 edit_file body 整体复制到一个新的 `_generic_edit_file(self, project_id, file_path, old_string, new_string)` 方法。原 entry 改为调 `_dispatch_edit_file`。

- [ ] **Step 5: Run test → pass**

```bash
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::EditFileCanonicalDispatcherTests -q
```

Expected: 12 PASS

### Task 15: `chat.py` `_execute_tool` `write_file` canonical 永远拒绝

**Files:**
- Modify: `backend/chat.py:_execute_tool` write_file 分支 (line 3673)
- Test: `tests/test_chat_runtime.py`

- [ ] **Step 1: 写测试**

```python
class WriteFileCanonicalDispatcherTests(unittest.TestCase):
    """spec §3.1.3: canonical draft 路径**永远拒绝** write_file。"""
    CANONICAL = "content/report_draft_v1.md"

    def test_canonical_path_always_rejected_when_draft_exists(self):
        # write_file(file_path=CANONICAL, content="...") + draft 已存在
        # → reject + 文案含 append_report_draft + edit_file
        pass

    def test_canonical_path_rejected_even_when_draft_missing(self):
        # 首次起草也拒绝；统一走 append_report_draft
        pass

    def test_non_canonical_path_uses_generic_write(self):
        # file_path="other/file.md" → 走 _generic_write_file
        pass


class WriteFileGenericRegressionTests(unittest.TestCase):
    """spec §5.1 + R1 P1-4：非 canonical 路径 write 行为不变。"""

    def test_write_other_md_succeeds(self):
        # file_path="plan/notes.md", content="..."
        # → 走 _generic_write_file，正常写入
        pass

    def test_write_other_md_does_not_record_canonical_mutation(self):
        # 写入非 canonical 路径不应触发 _record_successful_canonical_draft_mutation
        # → turn_context["canonical_draft_mutations"] 仍空
        pass
```

- [ ] **Step 2: Run → fail**

```bash
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::WriteFileCanonicalDispatcherTests tests/test_chat_runtime.py::WriteFileGenericRegressionTests -q
```

Expected: FAIL

- [ ] **Step 3: 实现**

`backend/chat.py` write_file 分支（line 3673）：

```python
if func_name == "write_file":
    args = ...
    file_path = args.get("file_path")
    content = args.get("content")
    return self._dispatch_write_file(project_id, file_path, content)


def _dispatch_write_file(self, project_id, file_path, content):
    canonical_path = "content/report_draft_v1.md"
    if file_path == canonical_path:
        return {"status": "error", "message":
                "正文草稿请用 append_report_draft（首次起草 / 续写）"
                "或 edit_file（章节重写 / 文字替换 / 整篇重写）。"
                "write_file 不接受 canonical draft 路径。"}
    return self._generic_write_file(project_id, file_path, content)
```

把现有 write_file body 抽成 `_generic_write_file`（同 Task 14 模式）。

- [ ] **Step 4: Run test → pass**

```bash
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::WriteFileCanonicalDispatcherTests -q
```

Expected: 3 PASS

### Task 16: `chat.py` `append_report_draft` 适配新 mutations list + post-hoc intent

**Files:**
- Modify: `backend/chat.py:append_report_draft` 实现（grep `name == "append_report_draft"`）
- Test: `tests/test_chat_runtime.py`

- [ ] **Step 1: 找现有 append_report_draft 实现位置**

```bash
grep -n "def.*append_report_draft\|name == \"append_report_draft\"" backend/chat.py
```

- [ ] **Step 2: 写测试**

```python
class AppendReportDraftMutationsListTests(unittest.TestCase):
    """spec §3.1.4: append 写入 mutations list（不再用 dict 字段）。"""

    def test_first_draft_appends_first_draft_action(self):
        # draft 不存在 → first_time → canonical_action="first_draft"
        # mutations[0]["canonical_action"] == "first_draft"
        pass

    def test_subsequent_append_uses_append_action(self):
        # draft 存在 → canonical_action="append"
        pass

    def test_post_hoc_modify_intent_blocks_append(self):
        # user_message="把第二章改成 X" → detect_user_message_intent="modify"
        # → reject + 文案"用户消息看起来是想改已有内容"
        pass

    def test_post_hoc_generative_intent_passes_append(self):
        # user_message="续写下一章" → 正常通过
        pass
```

- [ ] **Step 3: Run → fail**

```bash
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::AppendReportDraftMutationsListTests -q
```

- [ ] **Step 4: 改 append_report_draft 实现**

在 invariant check 之后、写盘之前，加 post-hoc intent check：

```python
from backend.report_writing import detect_user_message_intent

user_msg = turn_context.get("user_message_text", "") or ""
if detect_user_message_intent(user_msg) == "modify":
    return {"status": "error", "message":
            "用户消息看起来是想改已有内容（把 X 改成 Y / 重写第N章），"
            "请用 edit_file；append 是新增内容"}
```

写盘成功后改用 mutations list：

```python
canonical_action = "first_draft" if first_time else "append"
target_label = "first chapter" if first_time else "next paragraph/chapter"
new_mtime = draft_full_path.stat().st_mtime
mutations = turn_context.setdefault("canonical_draft_mutations", [])
mutations.append({
    "tool": "append_report_draft",
    "canonical_action": canonical_action,
    "target_label": target_label,
    "old_len": 0 if first_time else len(prev_draft),
    "new_len": len(content),
    "mtime_after": new_mtime,
    "ts": time.time(),
})
```

注意：旧字段 `canonical_draft_mutation`（单 dict）暂时**也保留写入**（Commit 1 并存策略），Commit 3 删除。

- [ ] **Step 5: Run test → pass**

```bash
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::AppendReportDraftMutationsListTests -q
```

### Task 16b: `chat.py` `_record_successful_canonical_draft_mutation` 桥接到新 list（防 Commit 1 旧工具漏计数）

**Files:**
- Modify: `backend/chat.py:_record_successful_canonical_draft_mutation` (line 5715)
- Modify: `backend/chat.py:_new_turn_context` (line 5523) — 加 `canonical_draft_mutations: []`
- Test: `tests/test_chat_runtime.py`

**关键背景**（fixed per R1 P1-3）：Commit 1 旧 3 工具（`rewrite_report_section` / `replace_report_text` / `rewrite_report_draft`）schema 仍注册，model 仍可调。它们走旧 callable，记录到 `canonical_draft_mutation`（单 dict）。如果新 helper `check_no_prior_canonical_mutation_in_turn` 只看 `canonical_draft_mutations`（list），Commit 1 期间旧工具会绕过 mutation_limit。修法：让 `_record_successful_canonical_draft_mutation` **同时写两份**——旧 dict（兼容旧 helper）+ 新 list（新 helper 看）。

- [ ] **Step 1: 写测试**

`tests/test_chat_runtime.py` 末尾追加：

```python
class CanonicalMutationBridgeTests(unittest.TestCase):
    """Commit 1 期间旧工具的 mutation 也要 append 新 list（防 mutation_limit 漏拦）。"""

    def test_record_writes_both_old_dict_and_new_list(self):
        # 调 _record_successful_canonical_draft_mutation 1 次
        # → turn_context["canonical_draft_mutation"] 是 dict
        # → turn_context["canonical_draft_mutations"] 是 list with 1 entry
        pass

    def test_record_three_times_yields_list_len_3(self):
        # 连续调 3 次 → list 长度 3 + 旧 dict 是最后一次的 merged
        pass

    def test_new_list_entry_includes_required_fields(self):
        # 每条 list 项至少含: tool / canonical_action / target_label / old_len /
        # new_len / mtime_after / ts
        pass
```

- [ ] **Step 2: 加 `canonical_draft_mutations: []` 到 `_new_turn_context`（line 5523）**

```python
def _new_turn_context(self, *, can_write_non_plan: bool) -> Dict[str, object]:
    return {
        # ... 现有
        "canonical_draft_mutation": None,
        "canonical_draft_mutations": [],   # NEW per spec §3.4 + R1 P1-3
        "s0_confirmation_completed": True, # NEW per Task 9
    }
```

- [ ] **Step 3: 改 `_record_successful_canonical_draft_mutation`（line 5715）**

读现有实现：

```bash
.venv\Scripts\python -c "import inspect; from backend.chat import ChatHandler; print(inspect.getsource(ChatHandler._record_successful_canonical_draft_mutation))"
```

在原函数 body 末尾追加新 list append（保留原 dict 写入）：

```python
def _record_successful_canonical_draft_mutation(self, ...):
    # ... 原 dict merge / 写入逻辑保留 ...
    self._turn_context["canonical_draft_mutation"] = mutation  # 旧字段（line 5730）
    
    # NEW（spec §3.4 + R1 P1-3）：同步 append 新 list
    new_entry = {
        "tool": mutation.get("tool"),
        "canonical_action": mutation.get("canonical_action"),
        "target_label": mutation.get("target_label", ""),
        "old_len": mutation.get("old_len", 0),
        "new_len": mutation.get("new_len", 0),
        "mtime_after": mutation.get("mtime_after"),
        "ts": mutation.get("ts") or time.time(),
    }
    mutations_list = self._turn_context.setdefault("canonical_draft_mutations", [])
    mutations_list.append(new_entry)
```

`mutation` 字段名按现有 `_record_*` 入参实际名调整（grep 看现有 dict 结构）；缺失字段用 `.get(default)` 兜底。

- [ ] **Step 4: Run test → pass**

```bash
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::CanonicalMutationBridgeTests -q
```

Expected: 3 PASS

- [ ] **Step 5: 同步更新 `_maybe_inject_obligation_retry` 旧分支（Task 21）**

旧分支（Task 21 step 3 中保留的 line 5470-5486 等价代码块）现在仅检查 `canonical_draft_mutation`（旧 dict）。新 list 的检查在新分支已覆盖。Commit 1 期间两个分支都正确——旧 obligation + 旧 dict 走旧分支，新 obligation + 新 list 走新分支，**没有遗漏 + 没有重复 retry**（`obligation_retry_fired` 防重）。本步骤是审计性确认，不需要改代码。

- [ ] **Step 6: 跑 mutation_limit / retry 相关测试整套**

```bash
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -q -k "Mutation or mutation or Retry or obligation"
```

Expected: ALL PASS

### Task 17: `chat.py` `ThinkingStreamParser` 实现

**Files:**
- Modify: `backend/chat.py`（新增 class 在合适位置，如 module-level）
- Test: `tests/test_chat_runtime.py`

- [ ] **Step 1: 写测试**

```python
class ThinkingStreamParserTests(unittest.TestCase):
    """spec §3.3.2: stateful 解析器分离 <think>...</think> 与正常 content。"""

    def test_normal_text_passes_through(self):
        from backend.chat import ThinkingStreamParser
        p = ThinkingStreamParser()
        events = p.feed("Hello world")
        self.assertEqual(events, [{"type": "content", "data": "Hello world"}])

    def test_simple_think_block(self):
        from backend.chat import ThinkingStreamParser
        p = ThinkingStreamParser()
        events = []
        events += p.feed("<think>reasoning</think>actual reply")
        self.assertEqual(events, [
            {"type": "thinking", "data": "reasoning"},
            {"type": "content", "data": "actual reply"},
        ])

    def test_split_across_chunks(self):
        from backend.chat import ThinkingStreamParser
        p = ThinkingStreamParser()
        events = []
        events += p.feed("pre<thi")
        events += p.feed("nk>reaso")
        events += p.feed("ning</think>post")
        # 期望：pre 进 content、reasoning 进 thinking、post 进 content
        # 顺序保持
        kinds = [e["type"] for e in events]
        self.assertEqual(kinds, ["content", "thinking", "thinking", "content"])
        # 累计 data 一致
        content_total = "".join(e["data"] for e in events if e["type"] == "content")
        thinking_total = "".join(e["data"] for e in events if e["type"] == "thinking")
        self.assertEqual(content_total, "prepost")
        self.assertEqual(thinking_total, "reasoning")

    def test_unclosed_think_at_eof_treats_remainder_as_thinking(self):
        from backend.chat import ThinkingStreamParser
        p = ThinkingStreamParser()
        events = p.feed("<think>truncated")
        events += p.flush()
        thinking = "".join(e["data"] for e in events if e["type"] == "thinking")
        self.assertEqual(thinking, "truncated")

    def test_no_think_returns_only_content(self):
        from backend.chat import ThinkingStreamParser
        p = ThinkingStreamParser()
        events = p.feed("Plain reply, no think tags.")
        events += p.flush()
        self.assertTrue(all(e["type"] == "content" for e in events))

    def test_nested_think_treats_inner_open_as_text(self):
        """spec §3.3.5 + R1 P1-4: 嵌套 <think> 取 outermost；
        内层 `<think>` 当作 thinking 内容里的字符串透传。"""
        from backend.chat import ThinkingStreamParser
        p = ThinkingStreamParser()
        events = p.feed("<think>outer <think>inner")  # 第二个 <think> 不切换状态
        events += p.feed(" still thinking</think>actual reply")
        thinking = "".join(e["data"] for e in events if e["type"] == "thinking")
        content = "".join(e["data"] for e in events if e["type"] == "content")
        self.assertIn("inner", thinking)
        self.assertIn("still thinking", thinking)
        self.assertEqual(content, "actual reply")
```

**实施注意**（fixed per R1 P1-4）：parser 的 feed loop 当前在 INSIDE_THINK 状态下找 `</think>`。如果遇到内层 `<think>`，应当作普通 thinking 文本不切换。当前实现（Step 3 中 `target = self.CLOSE if self._inside else self.OPEN`）已经是这个语义——INSIDE 状态下只 search CLOSE，看到 OPEN 也当 text。本测试是验证而非新增逻辑。

- [ ] **Step 2: Run → fail**

```bash
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::ThinkingStreamParserTests -q
```

Expected: FAIL `ImportError`

- [ ] **Step 3: 在 chat.py module-level 加 parser 类**

`backend/chat.py` 顶部 import 区之后（class ChatHandler 之前）：

```python
class ThinkingStreamParser:
    """States: NORMAL → INSIDE_THINK → NORMAL.

    Buffers text across feed() calls so partial `<think>` / `</think>` boundaries
    spanning chunk borders parse correctly. flush() emits any remaining buffered
    text using current state (truncated <think> → emitted as thinking).
    """
    OPEN = "<think>"
    CLOSE = "</think>"

    def __init__(self):
        self._buf = ""
        self._inside = False

    def feed(self, delta: str) -> list[dict]:
        if not delta:
            return []
        self._buf += delta
        events = []
        while self._buf:
            target = self.CLOSE if self._inside else self.OPEN
            other = self.OPEN if self._inside else self.CLOSE
            idx = self._buf.find(target)
            if idx == -1:
                # 检查是否可能是 partial target (e.g. "<thi")
                # 如果 buf 末尾是 target 的 prefix，保留这部分；其余 emit
                safe_emit_end = len(self._buf)
                for k in range(1, len(target)):
                    if self._buf.endswith(target[:k]) or self._buf.endswith(other[:k]):
                        safe_emit_end = len(self._buf) - k
                        break
                if safe_emit_end > 0:
                    chunk = self._buf[:safe_emit_end]
                    if chunk:
                        events.append({
                            "type": "thinking" if self._inside else "content",
                            "data": chunk,
                        })
                    self._buf = self._buf[safe_emit_end:]
                break
            # 找到 target
            pre = self._buf[:idx]
            if pre:
                events.append({
                    "type": "thinking" if self._inside else "content",
                    "data": pre,
                })
            self._buf = self._buf[idx + len(target):]
            self._inside = not self._inside
        return events

    def flush(self) -> list[dict]:
        if not self._buf:
            return []
        events = [{
            "type": "thinking" if self._inside else "content",
            "data": self._buf,
        }]
        self._buf = ""
        return events
```

- [ ] **Step 4: Run test → pass**

```bash
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::ThinkingStreamParserTests -q
```

Expected: 5 PASS

### Task 18: `chat.py` 集成 ThinkingStreamParser 到 stream pipeline

> **TDD exception: integration-only**（fixed per R1 P2-2）。Parser 单元行为已在 Task 17 通过 ThinkingStreamParserTests 覆盖；本 task 只把 parser 接到流式 yield 处。验证靠 Task 17 测试 + 手工 SSE 流。


**Files:**
- Modify: `backend/chat.py` 流式分支（around line 2507 yield content_delta 处）
- Test: `tests/test_stream_api.py` (slow，仅手工验证)

- [ ] **Step 1: 找到流式 yield 处**

```bash
grep -n "yield .*\"type\": \"content\"" backend/chat.py | head
```

应该在 line 2507 附近。这是 model 流式响应的 content delta 出口。

- [ ] **Step 2: 集成 parser**

定位 yield 之前：

```python
# 现有：
if delta_content_str:
    yield {"type": "content", "data": delta_content_str}

# 改为：
if delta_content_str:
    if not hasattr(self, "_thinking_parser") or self._thinking_parser is None:
        self._thinking_parser = ThinkingStreamParser()
    for ev in self._thinking_parser.feed(delta_content_str):
        yield ev
```

并在每轮流结束（finalize 处）调 flush：

```python
# 在 stream 结束、finalize_assistant_turn 调用前：
if hasattr(self, "_thinking_parser") and self._thinking_parser is not None:
    for ev in self._thinking_parser.flush():
        yield ev
    self._thinking_parser = None
```

注意：`_thinking_parser` 单例放 ChatHandler instance attr 可能有并发问题（多 turn 并发）；更安全是放 turn-local context。**实施时**：把 parser 放进 turn_context 或者一个 stream-local 闭包变量。

更安全实现（建议）：

```python
# 在 stream 函数顶部：
parser = ThinkingStreamParser()

# yield 处：
if delta_content_str:
    for ev in parser.feed(delta_content_str):
        yield ev

# stream 结束前：
for ev in parser.flush():
    yield ev
```

- [ ] **Step 3: 跑相关测试（fast 集合）**

```bash
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::ThinkingStreamParserTests -q
```

Expected: PASS（parser 行为不变）

- [ ] **Step 4: 验证手工 SSE 流（可选 slow）**

```bash
.venv\Scripts\python -m pytest tests/test_stream_api.py -m "" -q -k "thinking"
```

如已有 stream_api thinking 测试 → PASS；否则跳过。

### Task 19: 前端 `ThinkingBlock.jsx` 组件 + chatPresentation 扩展

**Files:**
- Create: `frontend/src/components/ThinkingBlock.jsx`
- Modify: `frontend/src/utils/chatPresentation.js`（加 thinking sentinel 处理）
- Test: `frontend/tests/thinkingBlock.test.mjs`
- Test: `frontend/tests/chatPresentationThinking.test.mjs` (新)

**关键背景**（fixed per R1 P1-2）：现有 `message.content` 是**字符串**，不是数组。Tool event 通过 `🔧 调用工具:` / `✅ 结果:` 单行 sentinel 嵌入 content 字符串，由 `splitAssistantMessageBlocks(content)` 切片。**本 plan 不引入数据模型变更**——thinking 折叠用同样的 sentinel 模式，仅用多行包裹 tag（因 thinking 内容是多行）。

**SSE event type 命名**（fixed per R1 P1-2）：spec 例子里写过 `_delta` 后缀的 event 名，但现有协议（`type: "content"` / `"tool"` 等，see `chat.py:2507`）没有 `_delta` 后缀。**本 plan 统一用 `type: "thinking"` 命名**（与 spec §3.3.2 表意一致），以匹配现有协议。本任务的 ThinkingStreamParser（Task 17）已用 `"thinking"`，**不再改动**。

- [ ] **Step 1: 写测试**

新建 `frontend/tests/thinkingBlock.test.mjs`：

```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
// 用 server-side render 或简单 string match 验证 default closed
// 这里用 React DOMServer renderToStaticMarkup
import { renderToStaticMarkup } from 'react-dom/server';
import React from 'react';
import { ThinkingBlock } from '../src/components/ThinkingBlock.jsx';

test('ThinkingBlock renders <details> default closed', () => {
  const html = renderToStaticMarkup(
    React.createElement(ThinkingBlock, { text: 'reasoning content' })
  );
  assert.match(html, /<details[^>]*class="thinking-block"/);
  // 默认无 open 属性
  assert.doesNotMatch(html, /<details[^>]*\sopen/);
  assert.match(html, /<summary>推理过程<\/summary>/);
  assert.match(html, /reasoning content/);
});

test('ThinkingBlock renders empty text gracefully', () => {
  const html = renderToStaticMarkup(
    React.createElement(ThinkingBlock, { text: '' })
  );
  assert.match(html, /<details[^>]*class="thinking-block"/);
});
```

- [ ] **Step 2: Run → fail**

```bash
cd frontend && node --test tests/thinkingBlock.test.mjs
```

Expected: FAIL `Cannot find module ThinkingBlock`

- [ ] **Step 3: 创建组件**

`frontend/src/components/ThinkingBlock.jsx`：

```jsx
import React from 'react'

export function ThinkingBlock({ text }) {
  return (
    <details className="thinking-block">
      <summary>推理过程</summary>
      <div className="thinking-content">{text}</div>
    </details>
  )
}

export default ThinkingBlock
```

- [ ] **Step 4: 加样式**

在 `frontend/src/index.css`（或对应入口 css）加：

```css
.thinking-block {
  margin: 8px 0;
  padding: 6px 10px;
  border-left: 3px solid #cbd5e1;
  background: #f8fafc;
  font-size: 0.85rem;
  color: #475569;
  border-radius: 4px;
}

.thinking-block summary {
  cursor: pointer;
  font-weight: 500;
  user-select: none;
}

.thinking-content {
  margin-top: 6px;
  max-height: 240px;
  overflow-y: auto;
  font-family: ui-monospace, Menlo, Consolas, monospace;
  white-space: pre-wrap;
  word-break: break-word;
}
```

- [ ] **Step 5: Run test → pass**

```bash
cd frontend && node --test tests/thinkingBlock.test.mjs
```

Expected: PASS

### Task 20: 前端 SSE thinking 路由 + sentinel 嵌入 + splitAssistantMessageBlocks 扩展 + ChatPanel 渲染

**Files:**
- Modify: `frontend/src/utils/chatPresentation.js`（加 `appendThinkingEventContent` + `splitAssistantMessageBlocks` 识别 thinking sentinel）
- Modify: `frontend/src/components/ChatPanel.jsx` (line 503 附近 SSE 分支 + render path)
- Test: `frontend/tests/chatPresentationThinking.test.mjs` (新)
- Test: `frontend/tests/chatPanelSseRouting.test.mjs` (新)

**关键设计**（fixed per R1 P1-2 + R2 new-introduced #3）：因 `message.content` 是 string，需要用 sentinel tag 包裹 thinking 内容。选用 **`<thinking-block>...</thinking-block>` 标签**（参考现有 `<stage-ack>` tag 处理模式 in `chatPresentation.js:59-66`）。

- 流式追加：每来一个 thinking delta，把它追加到当前 thinking-block 标签内，而不是新建一个 block（防多 block 拆碎）。
- **Delta escape**（fixed per R2 new-introduced #3）：thinking delta 内容若含字面 `<thinking-block>` / `</thinking-block>` 字符串会破坏切片正则。**最简方案**：frontend `appendThinkingEventContent` 入口仅转义这两个**特定 tag 模式**（不是所有 `<`），用 unicode 数学括号字符 `⟨` / `⟩` 做 sentinel——这两字符在 reasoning 文本里几乎不会自然出现，且不会和 markdown / HTML 冲突。`ThinkingBlock` 组件渲染时做反向还原。Backend stream 协议不改。
- 切片：`splitAssistantMessageBlocks` 用正则提取 `<thinking-block>...</thinking-block>` 段，emit 为 `{type: "thinking", content: ...}` block；其他 content 仍走原 text/tool 切片。
- 渲染：ChatPanel block list 中遇到 `type === "thinking"` 渲染 `<ThinkingBlock>`。组件内对 escaped 字符做反向还原。

- [ ] **Step 1: 写 chatPresentation 测试**

新建 `frontend/tests/chatPresentationThinking.test.mjs`：

```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  appendThinkingEventContent,
  splitAssistantMessageBlocks,
} from '../src/utils/chatPresentation.js';

test('appendThinkingEventContent wraps first delta in thinking-block tag', () => {
  const after = appendThinkingEventContent('Hello', 'reasoning1');
  assert.match(after, /Hello/);
  assert.match(after, /<thinking-block>reasoning1<\/thinking-block>/);
});

test('appendThinkingEventContent merges into existing trailing thinking-block', () => {
  const prev = 'Hello\n<thinking-block>reasoning1</thinking-block>\n';
  const after = appendThinkingEventContent(prev, 'reasoning2');
  // 应合并为一个 block：<thinking-block>reasoning1reasoning2</thinking-block>
  const matches = after.match(/<thinking-block>/g) || [];
  assert.equal(matches.length, 1, 'should merge into single block');
  assert.match(after, /reasoning1reasoning2/);
});

test('appendThinkingEventContent starts new thinking-block after content delta interrupts', () => {
  // prev: thinking-block followed by text → next thinking delta should start new block
  const prev = '<thinking-block>r1</thinking-block>\nactual text\n';
  const after = appendThinkingEventContent(prev, 'r2');
  const matches = after.match(/<thinking-block>/g) || [];
  assert.equal(matches.length, 2);
});

test('splitAssistantMessageBlocks emits thinking blocks separated from text', () => {
  const content = 'pre\n<thinking-block>secret reasoning</thinking-block>\npost';
  const blocks = splitAssistantMessageBlocks(content);
  const types = blocks.map(b => b.type);
  assert.deepEqual(types, ['text', 'thinking', 'text']);
  assert.equal(blocks[0].content, 'pre');
  assert.equal(blocks[1].content, 'secret reasoning');
  assert.equal(blocks[2].content, 'post');
});

test('splitAssistantMessageBlocks tolerates multiline thinking content', () => {
  const content = '<thinking-block>line 1\nline 2\nline 3</thinking-block>';
  const blocks = splitAssistantMessageBlocks(content);
  assert.equal(blocks.length, 1);
  assert.equal(blocks[0].type, 'thinking');
  assert.match(blocks[0].content, /line 1\nline 2\nline 3/);
});
```

- [ ] **Step 2: Run → fail**

```bash
cd frontend && node --test tests/chatPresentationThinking.test.mjs
```

Expected: FAIL `appendThinkingEventContent is not exported`

- [ ] **Step 3: 实现 `appendThinkingEventContent` 在 chatPresentation.js**

在 `frontend/src/utils/chatPresentation.js` 加（含 R2 new-introduced #3 escape）：

```javascript
const TRAILING_THINKING_RE = /<thinking-block>([\s\S]*?)<\/thinking-block>\s*$/;

// fixed per R2 new-introduced #3：仅转义 thinking delta 中的字面 sentinel tag
// 模式（不是所有 `<`）。用 unicode 数学括号 ⟨ / ⟩ (⟨/⟩) 做替身——
// 它们在 reasoning 文本里几乎不会自然出现，且不与 markdown/HTML 冲突。
// ThinkingBlock 组件渲染时调 unescapeThinkingContent 反向还原。
function escapeThinkingDelta(delta) {
  return String(delta)
    .replace(/<thinking-block>/g, "⟨THINKING_OPEN⟩")
    .replace(/<\/thinking-block>/g, "⟨THINKING_CLOSE⟩");
}

export function unescapeThinkingContent(content) {
  return String(content)
    .replace(/⟨THINKING_OPEN⟩/g, "<thinking-block>")
    .replace(/⟨THINKING_CLOSE⟩/g, "</thinking-block>");
}

export function appendThinkingEventContent(prev = "", delta = "") {
  if (!delta) return prev;
  const safeDelta = escapeThinkingDelta(delta);
  // 若 prev 末尾正好是一个 thinking-block 标签，合并
  const m = prev.match(TRAILING_THINKING_RE);
  if (m) {
    const merged = m[1] + safeDelta;
    return prev.slice(0, prev.length - m[0].length) + `<thinking-block>${merged}</thinking-block>\n`;
  }
  // 否则新起一个 block
  const sep = prev && !prev.endsWith("\n") ? "\n" : "";
  return `${prev}${sep}<thinking-block>${safeDelta}</thinking-block>\n`;
}
```

测试加（与 unicode sentinel 一致）：

```javascript
test('appendThinkingEventContent escapes literal thinking-block tag in delta', () => {
  const dirty = 'reasoning <thinking-block>nested</thinking-block> end';
  const after = appendThinkingEventContent('', dirty);
  // 切片不应被嵌套字面 tag 破坏：只该有 1 个真正的 thinking-block 包裹
  const opens = after.match(/<thinking-block>/g) || [];
  assert.equal(opens.length, 1, 'literal nested tag must not start a new block');
  // 且 escaped 内容里有 sentinel
  assert.ok(after.includes('⟨THINKING_OPEN⟩'));
});

test('unescapeThinkingContent reverses escape', () => {
  const escaped = '⟨THINKING_OPEN⟩';
  assert.equal(unescapeThinkingContent(escaped), '<thinking-block>');
});
```

ThinkingBlock 组件渲染端（Task 19 step 3）改为：

```jsx
import { unescapeThinkingContent } from '../utils/chatPresentation.js'

export function ThinkingBlock({ text }) {
  const display = unescapeThinkingContent(text)
  return (
    <details className="thinking-block">
      <summary>推理过程</summary>
      <div className="thinking-content">{display}</div>
    </details>
  )
}
```

- [ ] **Step 4: 改 `splitAssistantMessageBlocks` 识别 thinking-block**

`splitAssistantMessageBlocks` (line 68) 当前按行扫描；改为先用正则提取 thinking-block 段：

```javascript
const THINKING_BLOCK_RE = /<thinking-block>([\s\S]*?)<\/thinking-block>/g;

export function splitAssistantMessageBlocks(content = "") {
  const safeContent = stripStageAckTags(content);
  const blocks = [];
  let cursor = 0;
  // 先按 thinking-block 切大块
  for (const m of safeContent.matchAll(THINKING_BLOCK_RE)) {
    const before = safeContent.slice(cursor, m.index);
    if (before) {
      blocks.push(...splitTextAndTool(before));
    }
    blocks.push({ type: "thinking", content: m[1] });
    cursor = m.index + m[0].length;
  }
  const tail = safeContent.slice(cursor);
  if (tail) {
    blocks.push(...splitTextAndTool(tail));
  }
  return blocks;
}

// 抽出原 splitAssistantMessageBlocks 的 text/tool 行扫描逻辑：
function splitTextAndTool(content) {
  const lines = content.split("\n");
  const out = [];
  let textBuffer = [];
  const flush = () => {
    const merged = textBuffer.join("\n").trim();
    if (merged) out.push({ type: "text", content: merged });
    textBuffer = [];
  };
  for (const line of lines) {
    const isToolLine = line.startsWith("🔧 调用工具:") || line.startsWith("✅ 结果:") || line.startsWith("⚠️ 结果:");
    if (isToolLine) {
      flush();
      out.push({ type: "tool", content: line });
      continue;
    }
    textBuffer.push(line);
  }
  flush();
  return out;
}
```

- [ ] **Step 5: Run chatPresentation test → pass**

```bash
cd frontend && node --test tests/chatPresentationThinking.test.mjs
```

Expected: 5 PASS

- [ ] **Step 6: 写 ChatPanel SSE 路由测试**

新建 `frontend/tests/chatPanelSseRouting.test.mjs`：

```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { appendThinkingEventContent } from '../src/utils/chatPresentation.js';

test('SSE thinking event accumulation produces single trailing thinking-block', () => {
  let content = '';
  // simulate 3 streamed thinking deltas
  content = appendThinkingEventContent(content, 'first ');
  content = appendThinkingEventContent(content, 'second ');
  content = appendThinkingEventContent(content, 'third');
  const matches = content.match(/<thinking-block>/g) || [];
  assert.equal(matches.length, 1);
  assert.match(content, /first second third/);
});

test('SSE thinking after content emits separate block', () => {
  let content = '';
  content = appendThinkingEventContent(content, 'reasoning');
  content += '\nactual reply\n';  // simulating content event appending
  content = appendThinkingEventContent(content, 'more reasoning');
  const matches = content.match(/<thinking-block>/g) || [];
  assert.equal(matches.length, 2);
});
```

- [ ] **Step 7: Run → pass**

```bash
cd frontend && node --test tests/chatPanelSseRouting.test.mjs
```

Expected: 2 PASS

- [ ] **Step 8: 接入 ChatPanel SSE 分支（line 503 附近）**

`frontend/src/components/ChatPanel.jsx` 在现有 SSE 分支（参考 `parsed.type === 'tool'` 的写法）：

```javascript
} else if (parsed.type === 'thinking') {
  setMessages((prev) => prev.map((m) =>
    m.id === assistantId
      ? { ...m, content: appendThinkingEventContent(m.content, parsed.data) }
      : m
  ))
}
```

import `appendThinkingEventContent` 在文件顶（从 `../utils/chatPresentation.js`）。

- [ ] **Step 9: 改 ChatPanel render path**

ChatPanel 的 message renderer（grep 现有 `splitAssistantMessageBlocks` 调用位置）：

```jsx
{splitAssistantMessageBlocks(message.content).map((block, idx) => {
  if (block.type === 'text') return <Markdown key={idx} ... />
  if (block.type === 'tool') return <ToolEvent key={idx} ... />
  if (block.type === 'thinking') return <ThinkingBlock key={idx} text={block.content} />
  return null
})}
```

import `ThinkingBlock` 在文件顶（从 `./ThinkingBlock.jsx`）。

- [ ] **Step 10: 跑 ChatPanel 现有测试确认无 regression**

```bash
cd frontend && node --test tests/
```

Expected: ALL PASS（含 stage-ack / tool 切片等现有 test 不受影响）

### Task 21: `chat.py` `_maybe_inject_obligation_retry` 加并行 `canonical_obligation` 分支

**Files:**
- Modify: `backend/chat.py:_maybe_inject_obligation_retry` (line 5458)
- Test: `tests/test_chat_runtime.py`

**关键背景**（fixed per R1 P0-3）：现有 retry 入口是 `_maybe_inject_obligation_retry` (line 5458)，被 chat loop 在 line 2697 / 2935 调用，**不是** `_finalize_assistant_turn`。本 task 在 Commit 1 阶段**并行新增**新字段分支（不替换旧分支），让两条 path 同时跑——这样旧 4 工具（Commit 1 期间仍存在）也能命中旧 retry，新 dispatcher 命中新 retry。Commit 3（Task 33）才删旧分支。

- [ ] **Step 1: 写测试**

`tests/test_chat_runtime.py` 末尾追加：

```python
class ClaimOnlyRetryWithCanonicalObligationTests(unittest.TestCase):
    """spec §3.5.4: claim retry 用 canonical_obligation.intent + mutations list 长度。"""

    def test_generative_obligation_zero_mutations_with_claim_triggers_retry(self):
        # turn_context: canonical_obligation={"intent":"generative",...}
        # canonical_draft_mutations=[]
        # assistant_text claims "已写完" → _maybe_inject_obligation_retry returns True
        pass

    def test_modify_obligation_zero_mutations_with_claim_triggers_retry(self):
        pass

    def test_generative_obligation_one_mutation_no_retry(self):
        # mutations=[1 entry] → returns False（spec §7.6 acknowledged limitation）
        pass

    def test_no_obligation_no_retry(self):
        # canonical_obligation={"intent": None,...} 且 legacy obligation 也 None → False
        pass

    def test_legacy_obligation_path_still_works(self):
        # Commit 1 并存策略：旧 canonical_draft_write_obligation 仍能触发 retry
        # legacy obligation set + 旧 canonical_draft_mutation None + assistant 声称
        # → returns True
        pass

    def test_obligation_retry_fired_flag_prevents_double(self):
        # turn_context["obligation_retry_fired"] = True → returns False（防重复）
        pass
```

- [ ] **Step 2: Run test → fail**

```bash
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::ClaimOnlyRetryWithCanonicalObligationTests -q
```

Expected: FAIL

- [ ] **Step 3: 改 `_maybe_inject_obligation_retry`（line 5458）**

在 line 5458 函数体内**并行新增**新字段分支。**保留旧分支**（line 5470-5486 旧逻辑不动）。

```python
def _maybe_inject_obligation_retry(
    self, assistant_text: str, current_turn_messages: List[Dict] | None = None,
) -> bool:
    if current_turn_messages is None:
        return False
    if self._turn_context.get("obligation_retry_fired"):
        return False  # 防重复

    from backend.report_writing import assistant_text_claims_modification
    
    # === 新分支优先（spec §3.5.4，Commit 1 加，Commit 3 删除旧分支后这就是唯一分支）===
    # 关键：new_intent 一旦命中（"generative"/"modify"），新分支就**完整接管**这次决策——
    # 无论 retry True 或 False，立即 return，**不允许 fall through 到旧分支**（fixed per
    # R2 new-introduced #1）。否则 Commit 1 期间新 dispatcher 写完已 append 新 list 但旧
    # dict 仍 None，assistant 文本若声称完成会被旧分支错误重 retry。
    new_obligation = self._turn_context.get("canonical_obligation") or {}
    new_intent = new_obligation.get("intent") if isinstance(new_obligation, dict) else None
    if new_intent in ("generative", "modify"):
        mutations_list = self._turn_context.get("canonical_draft_mutations") or []
        if mutations_list:
            return False  # 新 list 已有 mutation → 不 retry
        if not assistant_text_claims_modification(assistant_text):
            return False  # 没声称完成 → 不 retry
        corrective = (
            f"你在回复中声称已修改正文（intent={new_intent}），但本轮没有成功调用任何"
            f"写正文工具。请实际调用对应工具（"
            f"{'append_report_draft' if new_intent == 'generative' else 'edit_file 或 append_report_draft'}）"
            f"完成写入，不要只在文字中声明已完成。"
        )
        self._inject_synthetic_user_correction(corrective, current_turn_messages)
        self._turn_context["obligation_retry_fired"] = True
        return True
    
    # === 旧分支（保留至 Commit 3 删除）：仅当 new_intent 缺失时才走 ===
    # 即旧 4 工具走旧 detect_canonical_draft_write_obligation 写 legacy obligation 但
    # new detect_user_message_intent 返回 "ambiguous" 的边缘 case 才 fall through 到此。
    # 例：用户消息含 "改写" / "重做"——旧 detector 覆盖，新 detector "ambiguous"。
    #
    # 关键防线（fixed per R3 new-introduced，防 Commit 1/2 期间死角）：
    # 旧分支判 retry 前**也要**检查新 list `canonical_draft_mutations` 非空——
    # 因为新 dispatcher 可能已写入新 list 但旧字段 dict 仍 None（Commit 2 之后旧 4 工具
    # schema 已删，model 只能调新 dispatcher，但 turn-start 仍可能写出 legacy obligation
    # 当 new detector 返回 ambiguous 时）。如果只看旧 dict 会误判"未写入" → 错误 retry。
    old_obligation = self._turn_context.get("canonical_draft_write_obligation")
    if old_obligation:
        # 双重检查：旧 dict OR 新 list 任一非空 → 已写入，不 retry
        if self._turn_context.get("canonical_draft_mutation"):
            return False  # 旧字段已有 mutation
        if self._turn_context.get("canonical_draft_mutations"):
            return False  # 新 list 已有 mutation（防 R3 死角）
        if not assistant_text_claims_modification(assistant_text):
            return False
        corrective = (
            "你在回复中声称已修改正文（"
            f"obligation={old_obligation['tool_family']}），但本轮没有成功调用任何写正文工具"
            "（append_report_draft / rewrite_report_section / replace_report_text / "
            "rewrite_report_draft）。请实际调用对应工具完成写入，不要只在文字中声明已完成。"
        )
        self._inject_synthetic_user_correction(corrective, current_turn_messages)
        self._turn_context["obligation_retry_fired"] = True
        return True
    
    return False
```

**关键约束（fixed per R2 new-introduced #1）**：
- 新分支**完整接管**决策——`new_intent in ("generative", "modify")` 命中后任何 path（命中 mutations 不 retry / 没声称不 retry / 真的 retry）都立即 `return`，绝不 fall through 到旧分支
- 旧分支只在 `new_intent` 不在两类 intent 内时跑——即新 detector 返回 `"ambiguous"`、`None` 或字段缺失的情况
- 这个修法在 Commit 1 期间正确：新 dispatcher 用 `canonical_obligation`（intent 必明确）+ 新 list；旧 4 工具走旧 `canonical_draft_write_obligation`，但**因为 turn-start Task 13 同时写两个字段**，旧工具 turn 的 `canonical_obligation.intent` 也会被新 detector 正确分类。意味着：实际上 Commit 1 期间几乎所有 turn 都走新分支。旧分支只是兜底
- chat loop line 2697 / 2935 的两个调用点**不动**

**对应测试加强**（Task 21 step 1 末尾追加）：

```python
def test_new_branch_blocks_old_branch_when_mutations_nonempty(self):
    """fixed per R2 new-introduced #1: 新 list 非空时新分支 return False，
    不应 fall through 触发旧分支 retry。"""
    # turn_context: canonical_obligation={"intent":"generative",...}
    #              canonical_draft_mutations=[{...one entry...}]
    #              canonical_draft_write_obligation={"tool_family":"append"}  # legacy 也设
    #              canonical_draft_mutation=None  # 旧 dict 因为新 dispatcher 不写
    # assistant_text claims "已写完"
    # → 期望 returns False（新分支拦下，旧分支不应触发）
    pass

def test_legacy_only_obligation_without_new_intent_uses_old_branch(self):
    """legacy obligation set but new_intent="ambiguous" → 走旧分支兜底。"""
    # turn_context: canonical_obligation={"intent":None,...}
    #              canonical_draft_write_obligation={"tool_family":"rewrite_section"}
    #              canonical_draft_mutation=None
    #              canonical_draft_mutations=[]
    # assistant_text claims 完成
    # → 期望 returns True（旧分支触发 retry）
    pass

def test_new_list_nonempty_blocks_legacy_retry_under_ambiguous_intent(self):
    """fixed per R3 new-introduced：用户消息 "改写第一章"——
    旧 detector 识别为 obligation 但新 detector 返回 ambiguous。
    新 dispatcher 已写入 canonical_draft_mutations 但旧 dict 仍 None。
    旧分支必须看到新 list 非空 → 不 retry（防 R3 dead zone）。"""
    # turn_context: canonical_obligation={"intent":None,...}  # ambiguous
    #              canonical_draft_write_obligation={"tool_family":"rewrite_section"}
    #              canonical_draft_mutation=None  # 旧 dict 没写（新 dispatcher 不写旧）
    #              canonical_draft_mutations=[{...one entry from new dispatcher...}]
    # assistant_text claims 完成
    # → 期望 returns False（旧分支应识别新 list 非空，不重复 retry）
    pass
```

- [ ] **Step 4: Run test → pass**

```bash
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::ClaimOnlyRetryWithCanonicalObligationTests -q
```

Expected: PASS

- [ ] **Step 5: 跑现有 retry 相关测试确认无 regression**

```bash
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -q -k "obligation or retry or Retry"
```

Expected: ALL PASS（现有旧字段测试不应破坏）

### Task 22: Commit 1 — Final test pass + 提交

**Files:** N/A (整体回归)

- [ ] **Step 1: 跑 fast 集合全套**

```bash
.venv\Scripts\python -m pytest tests/ -q
```

Expected: ALL PASS（默认 -m "not slow"，fast 集合）

- [ ] **Step 2: 跑前端测试**

```bash
cd frontend && node --test tests/
cd ..
```

Expected: ALL PASS

- [ ] **Step 3: 检视改动文件清单**

```bash
git status
git diff --stat
```

Expected: Commit 1 文件清单约：
- `pytest.ini` (new)
- `requirements.txt`
- `version_info.txt` (new)
- `consulting_report.spec`
- `app.py`
- `managed_search_pool.json`
- `backend/chat.py`
- `backend/report_writing.py`
- `frontend/src/components/ThinkingBlock.jsx` (new)
- `frontend/src/components/ChatPanel.jsx`
- `frontend/src/utils/chatPresentation.js` (modified — 加 `appendThinkingEventContent` + 扩展 `splitAssistantMessageBlocks`)
- `frontend/src/index.css`
- `skill/SKILL.md`
- 多个 `tests/test_*.py`
- 多个 `frontend/tests/*.mjs` (new)

- [ ] **Step 4: Commit**

```bash
git add pytest.ini requirements.txt version_info.txt consulting_report.spec app.py \
  managed_search_pool.json \
  backend/chat.py backend/report_writing.py \
  frontend/src/components/ThinkingBlock.jsx frontend/src/components/ChatPanel.jsx \
  frontend/src/utils/chatPresentation.js frontend/src/index.css \
  skill/SKILL.md \
  tests/test_chat_runtime.py tests/test_report_writing.py tests/test_packaging_spec.py \
  tests/test_app_logging.py tests/test_stream_api.py tests/smoke_packaged_app.py \
  frontend/tests/thinkingBlock.test.mjs frontend/tests/chatPresentationThinking.test.mjs frontend/tests/chatPanelSseRouting.test.mjs

git commit -m "$(cat <<'EOF'
feat(deepseek-migration): add canonical-draft dispatcher + S0 gate + think folding (commit 1/3)

Additive changes only; old 4-tool path still functional. Subsequent commits
will cut traffic (Commit 2) and delete callable + guard layer (Commit 3).

- backend/chat.py:
  * S0_FIRST_TURN_ALLOWED_TOOLS + first-turn gate in _execute_tool
  * S0 unlock logic in _finalize_assistant_turn (double-condition: no
    non-whitelist tool + non-empty assistant text)
  * canonical_obligation field (parallel with legacy
    canonical_draft_write_obligation)
  * _dispatch_edit_file canonical draft dispatcher (## anchor section /
    full rewrite / text replace + reverse intent guard)
  * _dispatch_write_file always rejects canonical path
  * append_report_draft writes to canonical_draft_mutations list +
    post-hoc modify-intent reject
  * ThinkingStreamParser + integrated stream split for `thinking` SSE
    event type
  * claim-only retry switched to canonical_obligation.intent + len(mutations)
- backend/report_writing.py:
  * detect_user_message_intent helper
  * resolve_section_anchor helper (h2-label first-line match)
  * MAX_CANONICAL_MUTATIONS_PER_TURN = 3 + list-based mutation tracking
  * within-turn mtime self-refresh in check_read_before_write_canonical_draft
- app.py: _setup_app_log RotatingFileHandler at ~/.consulting-report/app.log
- consulting_report.spec + version_info.txt: PyInstaller version block
- managed_search_pool.json: per_turn_searches 2 → 3
- pytest.ini + requirements.txt: pytest-xdist + slow markers default-skip
- skill/SKILL.md: §S0 first-turn hard constraint (3-5 confirm/补充 questions
  before writer tools)
- frontend ThinkingBlock + ChatPanel SSE routing
- new test classes: EditFileCanonicalDispatcherTests,
  WriteFileCanonicalDispatcherTests, AppendReportDraftMutationsListTests,
  S0FirstTurnGateTests, S0FirstTurnUnlockTests,
  S0ConversationStateRoundtripTests, ThinkingStreamParserTests,
  ClaimOnlyRetryWithCanonicalObligationTests, MutationLimit3Tests,
  ReadBeforeWriteSelfRefreshTests, DetectUserMessageIntentTests,
  ResolveSectionAnchorTests, VersionInfoTests, AppLogTests,
  CanonicalObligationFieldTests, CanonicalMutationBridgeTests,
  EditFileCanonicalInvariantRejectTests, EditFileGenericRegressionTests,
  WriteFileGenericRegressionTests

Spec: docs/superpowers/specs/2026-05-08-deepseek-migration-toolset-redesign-design.md (§3.1-§3.5, §3.8-§3.9)
Plan: docs/superpowers/plans/2026-05-08-deepseek-migration-toolset-redesign.md (Task 1-22)
EOF
)"
```

- [ ] **Step 5: Codex review Commit 1**

依据项目 CLAUDE.md 子代理派活规则：

```bash
mkdir -p .codex-run
cat > .codex-run/task-c1-review-prompt.md <<'EOF'
请 review 当前 HEAD commit（feat(deepseek-migration): add canonical-draft dispatcher
+ S0 gate + think folding (commit 1/3)）的代码质量。

Spec：docs/superpowers/specs/2026-05-08-deepseek-migration-toolset-redesign-design.md
Plan：docs/superpowers/plans/2026-05-08-deepseek-migration-toolset-redesign.md

重点检查：
1. 新代码是否覆盖 spec §3.1.2 / §3.1.3 / §3.1.4 / §3.2 / §3.3 / §3.4 / §3.5.4 全部要点
2. 共享 invariants 是否正确串接到新 dispatcher
3. ThinkingStreamParser 是否处理 partial chunk 边界
4. canonical_obligation 是否与旧 obligation 字段并存（为 Commit 3 删除做准备）
5. mutation list 的 within-turn self-refresh 是否正确（防自己写完撞 read-before-write）
6. 不应该出现的：删除任何旧路径、改动旧工具的 schema 注册（这是 Commit 2 的事）

verdict: APPROVED / NEEDS_CHANGES
EOF

codex exec --cd "$(pwd)" --color never \
  --output-last-message .codex-run/task-c1-review-last.txt \
  < .codex-run/task-c1-review-prompt.md > .codex-run/task-c1-review-full.log 2>&1 &
```

按 codex verdict 处理；若 NEEDS_CHANGES 则修复 + 再 review，直到 APPROVED。**APPROVED 后才进入 Commit 2。**

---

## Commit 2 — 切流量：删 schema 注册（model 不再可见旧工具）

**目标**：让 model 走新路径。删除 3 个旧专用工具的 schema 注册 + dispatch 路由 + SKILL.md 引用文案 + 错误消息。callable 仍保留（不删函数体）。

### Task 23: 删 3 个旧工具的 schema 注册

**Files:**
- Modify: `backend/chat.py` (line 3526, 3550, 3577 附近)
- Test: 现有 `RewriteReportSectionToolTests` / `ReplaceReportTextToolTests` / `RewriteReportDraftToolTests` 全部应跳过

- [ ] **Step 1: 找 schema 注册位置**

```bash
grep -n "\"name\": \"rewrite_report_section\"\|\"name\": \"replace_report_text\"\|\"name\": \"rewrite_report_draft\"" backend/chat.py
```

- [ ] **Step 2: 删除 3 处 tool schema dict（连同上下 description / parameters 完整删）**

每处约 20-30 行。在 schema list 中（一般是 `tools_payload` 或 `SUPPORTED_TOOLS_SCHEMA` 之类的 list）整段删 dict。

- [ ] **Step 3: 验证 schema list 仍合法**

```bash
.venv\Scripts\python -c "from backend.chat import ChatHandler; print('schema load ok')"
```

Expected: 无 import error

- [ ] **Step 4: 跑 chat schema 相关 test**

```bash
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -q -k "schema or Schema or tool_spec or ToolSpec"
```

Expected: PASS（如有 test 断言 schema 列表长度 == 旧值，需更新预期）

### Task 24: 删 `_execute_tool` dispatch 路由

**Files:**
- Modify: `backend/chat.py:_execute_tool` (line 3787, 3791, 3795)

- [ ] **Step 1: 找 dispatch 路由**

```bash
grep -n "func_name == \"rewrite_report_section\"\|func_name == \"replace_report_text\"\|func_name == \"rewrite_report_draft\"" backend/chat.py
```

- [ ] **Step 2: 删 3 个 elif 分支整体（callable 仍保留，仅删 dispatch 入口）**

`_execute_tool` 中的：

```python
elif func_name == "rewrite_report_section":
    return self._tool_rewrite_report_section(...)  # 整个 elif 删
elif func_name == "replace_report_text":
    return self._tool_replace_report_text(...)     # 整个 elif 删
elif func_name == "rewrite_report_draft":
    return self._tool_rewrite_report_draft(...)    # 整个 elif 删
```

- [ ] **Step 3: 跑 _execute_tool 相关 test**

```bash
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -q -k "execute_tool or dispatch"
```

Expected: PASS

### Task 25: 更新 SKILL.md §S4 工具引用文案

**Files:**
- Modify: `skill/SKILL.md`

- [ ] **Step 1: grep 旧工具名引用**

```bash
grep -n "rewrite_report_section\|replace_report_text\|rewrite_report_draft" skill/SKILL.md
```

- [ ] **Step 2: 替换文案**

把原 4-工具 §S4 段落改为：

```markdown
### S4 写正文工具

| 工具 | 用途 |
|---|---|
| `append_report_draft(content)` | 起草 / 续写 / 写下一章 |
| `edit_file(file_path, old_string, new_string)` | 章节重写（`old_string` 用 `## 锚点`）/ 文字替换（`old_string` 在 draft 中唯一）/ 整篇重写（`old_string` 等于 draft 第一行 h1 + 用户明确要求"整篇/推倒/全文重写"）|

约束：
- 不要对 `content/report_draft_v1.md` 用 `write_file`——首次起草请用 `append_report_draft`
- 一轮内 ≤ 3 次 canonical write
- 章节重写时 `old_string` 仅取首行 h2 标题做匹配；后端用 draft 中实际 snapshot 替换
```

- [ ] **Step 3: 验证无残留**

```bash
grep -n "rewrite_report_section\|replace_report_text\|rewrite_report_draft" skill/SKILL.md
```

Expected: 0 命中

- [ ] **Step 4: 跑 skill_assets 测试**

```bash
.venv\Scripts\python -m pytest tests/test_skill_assets.py tests/test_skill_engine.py -q
```

Expected: PASS（如有 fixture 断言 SKILL.md 含旧工具名，需更新）

### Task 26: 标记/删除旧 ToolTests 整 class

**Files:**
- Modify: `tests/test_chat_runtime.py`（删 3 个 class 整体）+ 其他可能的 test 文件

- [ ] **Step 1: 找旧 ToolTests class**

```bash
grep -n "class RewriteReportSectionToolTests\|class ReplaceReportTextToolTests\|class RewriteReportDraftToolTests" tests/test_chat_runtime.py
```

- [ ] **Step 2: 整 class 删除（连方法体）**

每 class 约 50-100 行。完整删除（不要保留 skip stub）。

- [ ] **Step 3: 同步删任何关联 fixture / helper（如 `_make_rewrite_report_section_tool_call` 等）**

```bash
grep -n "rewrite_report_section\|replace_report_text\|rewrite_report_draft" tests/
```

删除 test 内引用；仅保留**新** dispatcher 测试中作为字符串提及的（如 reject 文案断言）。

- [ ] **Step 4: 跑 chat_runtime 整套**

```bash
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -q
```

Expected: ALL PASS（数量减少）

### Task 27: Commit 2 — Final test pass + 提交

- [ ] **Step 1: 全套 fast 测试**

```bash
.venv\Scripts\python -m pytest tests/ -q && cd frontend && node --test tests/ && cd ..
```

Expected: ALL PASS

- [ ] **Step 2: Commit**

```bash
git add backend/chat.py skill/SKILL.md tests/test_chat_runtime.py
git commit -m "$(cat <<'EOF'
feat(deepseek-migration): cut traffic — drop schema registration for 3 legacy tools (commit 2/3)

Model no longer sees rewrite_report_section / replace_report_text /
rewrite_report_draft in its tool list. Their callables remain intact for
Commit 3 deletion. New dispatcher path (Commit 1) handles all canonical
draft writes via edit_file / write_file (rejected) / append_report_draft.

- backend/chat.py: drop 3 legacy tool schema dicts + dispatch elif branches
- skill/SKILL.md §S4: rewrite tool reference table (3 entries → 2 entries:
  append_report_draft + edit_file)
- tests/test_chat_runtime.py: drop RewriteReportSectionToolTests /
  ReplaceReportTextToolTests / RewriteReportDraftToolTests entire classes

Spec: docs/superpowers/specs/2026-05-08-deepseek-migration-toolset-redesign-design.md (§3.5.1)
Plan: docs/superpowers/plans/2026-05-08-deepseek-migration-toolset-redesign.md (Task 23-27)
EOF
)"
```

- [ ] **Step 3: Codex review Commit 2**

```bash
cat > .codex-run/task-c2-review-prompt.md <<'EOF'
请 review HEAD commit（commit 2/3）。

Spec §3.5.1 行 1-3, 9, 10, 11；行 13。
Plan Commit 2 task 23-27。

重点检查：
1. schema 注册仅删 3 个旧工具，未误删其他工具
2. dispatch 路由仅删 3 个旧工具入口；callable 应仍存在
3. SKILL.md 引用清理无残留
4. 旧 ToolTests class 完整删除（不留 skip stub）
5. 现有 test 跑通无 regression

verdict: APPROVED / NEEDS_CHANGES
EOF

codex exec --cd "$(pwd)" --color never \
  --output-last-message .codex-run/task-c2-review-last.txt \
  < .codex-run/task-c2-review-prompt.md > .codex-run/task-c2-review-full.log 2>&1 &
```

按 verdict 处理至 APPROVED 才进入 Commit 3。

---

## Commit 3 — 删旧 callable + guard 控制层 + 残留扫描

**目标**：本次最大删除。所有 gemini 时代的 obligation 检测、家族锁、关键词门禁、3 个旧工具 callable、legacy turn_context 字段都删干净。grep 全仓 0 命中作为 quality gate。

### Task 28: 删 3 个旧工具 callable 实现

**Files:**
- Modify: `backend/chat.py` （3 个 `_tool_*` / `def rewrite_report_*` 私有方法）

- [ ] **Step 1: 找 callable 定义**

```bash
grep -n "def _tool_rewrite_report_section\|def _tool_replace_report_text\|def _tool_rewrite_report_draft\|def rewrite_report_section\|def replace_report_text\|def rewrite_report_draft" backend/chat.py
```

- [ ] **Step 2: 完整删除 3 个函数（连 docstring）**

每个 callable 约 100-150 行。

- [ ] **Step 3: 跑 chat_runtime 验证**

```bash
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -q
```

Expected: ALL PASS

### Task 29: 删 `_guard_canonical_draft_obligation_tool`

**Files:**
- Modify: `backend/chat.py`

- [ ] **Step 1: 找位置**

```bash
grep -n "_guard_canonical_draft_obligation_tool" backend/chat.py
```

- [ ] **Step 2: 删除 function 定义 + 所有调用点**

包括 `_execute_tool` 中可能调 `_guard_canonical_draft_obligation_tool(...)` 的位置整行删。

- [ ] **Step 3: 验证**

```bash
grep -rn "_guard_canonical_draft_obligation_tool" backend/ tests/
```

Expected: 0 命中

### Task 30: 删旧 obligation 检测 + keyword 门禁常量

**Files:**
- Modify: `backend/chat.py`
- Modify: `backend/report_writing.py`

- [ ] **Step 1: 删 `detect_canonical_draft_write_obligation` 旧版本**

`backend/report_writing.py` line ~220 处。完整删函数定义（保留 `detect_user_message_intent` 新 helper）。

```bash
grep -n "def detect_canonical_draft_write_obligation" backend/report_writing.py
```

整段删除。

- [ ] **Step 2: 删 chat.py 中调用**

```bash
grep -n "detect_canonical_draft_write_obligation" backend/chat.py
```

整行删（之前 Commit 1 已加 detect_user_message_intent 并存调用，现在只保留新 helper）。

- [ ] **Step 3: 删 `NON_PLAN_WRITE_ALLOW_KEYWORDS` / `FILE_UPDATE_VERBS`**

```bash
grep -n "NON_PLAN_WRITE_ALLOW_KEYWORDS\|FILE_UPDATE_VERBS" backend/chat.py
```

整段定义删除 + 所有引用点删除。

- [ ] **Step 4: 验证**

```bash
grep -rn "NON_PLAN_WRITE_ALLOW_KEYWORDS\|FILE_UPDATE_VERBS\|detect_canonical_draft_write_obligation" backend/ tests/ skill/
```

Expected: 0 命中

### Task 31: 删 classify / preflight / decision 死代码

**Files:**
- Modify: `backend/chat.py`

- [ ] **Step 1: grep**

```bash
grep -n "_classify_canonical_draft_turn\|_preflight_canonical_draft_check\|_make_canonical_draft_decision\|_empty_canonical_draft_decision" backend/chat.py
```

- [ ] **Step 2: 删除每个函数定义 + 调用点**

注意：spec §3.5.1 中 4 / 5 / 6 行标 "（如有）" — 实际可能 Commit 1 时残留 body / 可能已是空函数。按实际情况删。

- [ ] **Step 3: 验证**

```bash
grep -rn "_classify_canonical_draft_turn\|_preflight_canonical_draft_check\|_make_canonical_draft_decision\|_empty_canonical_draft_decision" backend/ tests/
```

Expected: 0 命中

### Task 32: 删 `_validate_*` pre-write helpers

**Files:**
- Modify: `backend/chat.py`

- [ ] **Step 1: grep**

```bash
grep -n "_validate_append_turn_canonical_draft_write\|_validate_required_report_draft_prewrite" backend/chat.py
```

- [ ] **Step 2: 删除函数定义**

逻辑 Commit 1 已 inline 迁移到 `_dispatch_edit_file` / `_dispatch_write_file` / `append_report_draft` 入口的 invariant 串。这两个 helper 已无引用。

- [ ] **Step 3: 验证**

```bash
grep -rn "_validate_append_turn_canonical_draft_write\|_validate_required_report_draft_prewrite" backend/ tests/
```

Expected: 0 命中

### Task 33: 删 legacy turn_context 字段

**Files:**
- Modify: `backend/chat.py`
- Modify: `backend/report_writing.py`

- [ ] **Step 1: grep legacy 字段**

```bash
grep -n "canonical_draft_decision\|required_write_snapshots\|draft_action_events\|canonical_draft_mutation\b" backend/chat.py backend/report_writing.py
```

注意 `canonical_draft_mutation\b` 是单 dict 旧字段，`canonical_draft_mutations` 是新 list 字段——单词边界 `\b` 防误删。

- [ ] **Step 2: 删除每个赋值点 + 读取点**

包括 turn-init / turn-end / mutation 写盘等所有触及位置。

- [ ] **Step 3: 删 `canonical_draft_write_obligation` legacy field**

```bash
grep -n "canonical_draft_write_obligation" backend/chat.py
```

所有写入与读取 → 删除。代码现在仅依赖 `canonical_obligation`。

- [ ] **Step 4: 验证**

```bash
grep -rn "canonical_draft_decision\|required_write_snapshots\|draft_action_events\|canonical_draft_write_obligation" backend/ tests/
grep -rn "canonical_draft_mutation[^s]" backend/ tests/
```

Expected: 0 命中（除 spec/cutover 文档自身引用外）

### Task 34: 删 `resolve_section_target` legacy

**Files:**
- Modify: `backend/report_writing.py`
- Modify: `backend/chat.py`（删 import + call site）

- [ ] **Step 1: grep**

```bash
grep -rn "resolve_section_target" backend/ tests/
```

- [ ] **Step 2: 删除函数定义（report_writing.py line 19）**

包含 `_SECTION_PREFIX_RE` 模块级正则常量（如仅 resolve_section_target 用），一并删除。

- [ ] **Step 3: 删 chat.py 中 import / call**

- [ ] **Step 4: 验证**

```bash
grep -rn "resolve_section_target\|_SECTION_PREFIX_RE" backend/ tests/
```

Expected: 0 命中（除 spec/cutover 自身）

### Task 35: 删 obligation detector / tool family lock 独立测试文件（如有）

**Files:**
- Possibly delete: `tests/test_obligation_detector.py` / `tests/test_tool_family_lock.py`（如存在）
- Modify: `tests/test_chat_runtime.py` 中 `ToolFamilyLockTests` / `KeywordGateTests` 整 class

- [ ] **Step 1: 找**

```bash
ls tests/test_obligation_detector.py tests/test_tool_family_lock.py 2>/dev/null
grep -n "class ToolFamilyLockTests\|class KeywordGateTests" tests/test_chat_runtime.py
```

- [ ] **Step 2: 删整 class / 整 file**

如有独立 test file → `git rm`；class in test_chat_runtime → 整 class 删除。

- [ ] **Step 3: 跑测试**

```bash
.venv\Scripts\python -m pytest tests/ -q
```

Expected: ALL PASS

### Task 36: Quality gate — grep 全仓残留扫描

**Files:** N/A（验证）

- [ ] **Step 1: 关键残留 grep —— 7 段（fixed per R1 P1-5）**

```bash
echo "===== [1/7] 旧工具名 ====="
grep -rn "rewrite_report_section\|replace_report_text\|rewrite_report_draft" \
  backend/ tests/ skill/ frontend/src/ \
  | grep -v "docs/superpowers/specs/2026-05" \
  | grep -v "docs/superpowers/cutover_report_2026-05-06"

echo "===== [2/7] 旧 guard 函数 ====="
grep -rn "_guard_canonical_draft_obligation_tool\|_classify_canonical_draft_turn\|_preflight_canonical_draft_check\|_make_canonical_draft_decision\|_empty_canonical_draft_decision\|_validate_append_turn_canonical_draft_write\|_validate_required_report_draft_prewrite" backend/ tests/

echo "===== [3/7] 旧 obligation detector（spec §A 第一条） ====="
grep -rn "detect_canonical_draft_write_obligation" backend/ tests/

echo "===== [4/7] 旧 keyword constants ====="
grep -rn "NON_PLAN_WRITE_ALLOW_KEYWORDS\|FILE_UPDATE_VERBS" backend/ tests/

echo "===== [5/7] 旧 turn_context 字段 ====="
grep -rn "canonical_draft_decision\|required_write_snapshots\|draft_action_events\|canonical_draft_write_obligation" backend/ tests/

echo "===== [6/7] 旧 mutation dict 字段（非新 list） ====="
grep -rn "canonical_draft_mutation\b" backend/ tests/

echo "===== [7/7] legacy resolve helper ====="
grep -rn "resolve_section_target\|_SECTION_PREFIX_RE" backend/ tests/
```

Expected: 每段 0 命中。`docs/superpowers/specs/2026-05-08-...md` 自身的 spec 文本（含历史叙述）允许命中——明确从 grep target 路径 exclude。

- [ ] **Step 2: 若有命中，定位并删**

每条命中说明残留代码或测试。修后再跑 step 1，直到全 0。

### Task 37: Commit 3 — Final test pass + 提交

- [ ] **Step 1: 全套测试（fast）**

```bash
.venv\Scripts\python -m pytest tests/ -q && cd frontend && node --test tests/ && cd ..
```

Expected: ALL PASS

- [ ] **Step 2: 全套测试（含 slow，提交前一次）**

```bash
.venv\Scripts\python -m pytest tests/ -m "" -q
```

Expected: ALL PASS

- [ ] **Step 3: 打包 smoke（手工）**

```bash
build.bat
```

期望：dist 产物正常生成；version_info 字段正确（任务管理器中分组按"咨询报告助手" / 文件属性可见 ProductName）。

- [ ] **Step 4: Commit**

```bash
git add backend/chat.py backend/report_writing.py \
  tests/test_chat_runtime.py tests/test_report_writing.py
# 如有删除的独立 test file：git rm tests/test_obligation_detector.py 等

git commit -m "$(cat <<'EOF'
feat(deepseek-migration): delete legacy callable + guard control layer (commit 3/3)

Final commit of the 3-step migration. Removes the gemini-era guard control
layer (~700 lines of net deletion) along with 3 legacy report-tools'
callables, classify/preflight/decision dead code, validate-prewrite helpers,
keyword gate constants, legacy turn_context fields, and resolve_section_target
legacy helper.

Quality gate: grep 0 hits across backend/ tests/ skill/ frontend/src/ for:
- rewrite_report_section / replace_report_text / rewrite_report_draft
- _guard_canonical_draft_obligation_tool / _classify_canonical_draft_turn
  / _preflight_canonical_draft_check / _make_canonical_draft_decision
  / _empty_canonical_draft_decision / _validate_append_turn_canonical_draft_write
  / _validate_required_report_draft_prewrite
- NON_PLAN_WRITE_ALLOW_KEYWORDS / FILE_UPDATE_VERBS
- canonical_draft_decision / required_write_snapshots / draft_action_events
  / canonical_draft_write_obligation / canonical_draft_mutation (single-dict)
- resolve_section_target / _SECTION_PREFIX_RE

Cutover audit per spec §A appendix.

Net delta (with adds from Commits 1-2): ~-210 lines (with tests);
backend code only ~-270 lines. Guard control layer 1024 → ~300 lines.

Spec: docs/superpowers/specs/2026-05-08-deepseek-migration-toolset-redesign-design.md (§3.5, §A)
Plan: docs/superpowers/plans/2026-05-08-deepseek-migration-toolset-redesign.md (Task 28-39)
EOF
)"
```

- [ ] **Step 5: Codex review Commit 3**

```bash
cat > .codex-run/task-c3-review-prompt.md <<'EOF'
请 review HEAD commit（commit 3/3）+ 整个 3-commit chain。

Spec §3.5.1 全部行 + §A 附录 grep 清单。
Plan Commit 3 task 28-37。

重点检查：
1. spec §3.5.1 列出的所有删除项是否已删（逐行核对）
2. Quality gate grep（spec §A）的 7 段命令应全部 0 命中（除 spec/cutover 自身）
3. 6 个共享 invariants 仍存在且被新 dispatcher 调用
4. claim-only retry 机制保留且使用 canonical_obligation
5. 整 3-commit chain 是否符合 spec §4 阶段拆分（Commit 1 加 / Commit 2 切流量 / Commit 3 删）
6. 现有 test ALL PASS

verdict: APPROVED / NEEDS_CHANGES
EOF

codex exec --cd "$(pwd)" --color never \
  --output-last-message .codex-run/task-c3-review-last.txt \
  < .codex-run/task-c3-review-prompt.md > .codex-run/task-c3-review-full.log 2>&1 &
```

按 verdict 处理至 APPROVED。

### Task 38: 写 cutover report

**Files:**
- Create: `docs/superpowers/cutover_report_2026-05-08_deepseek-migration.md`

- [ ] **Step 1: 模板**

```markdown
# Cutover Report — DeepSeek V4 Pro Migration + Toolset Redesign

**Date**: 2026-05-08
**Spec**: docs/superpowers/specs/2026-05-08-deepseek-migration-toolset-redesign-design.md
**Plan**: docs/superpowers/plans/2026-05-08-deepseek-migration-toolset-redesign.md
**Commits**: <填 Commit 0/1/2/3 SHA>

## 净改动

| 模块 | 删除 | 新增 | 净值 |
|---|---|---|---|
| ... | ... | ... | ... |

## 验证

- 全套 fast pytest: PASS（X/X）
- 全套 slow pytest: PASS（含 stream_api / smoke_packaged）
- 前端 node:test: PASS（X/X）
- packaged smoke: 老 config heal + 新 config 起 + first-turn S0 + S4 dispatcher + thinking fold 全验证

## E2E 跑通的 7 个场景（spec §5.5，fixed per R1 P2-4）

1. 全新机器首启（删 ~/.consulting-report/）→ managed_model auto-set deepseek-v4-pro ✅
2. 老 config heal（手工写 managed_model="gemini-3-flash"）→ heal 触发 + log 可见 ✅
3. 首轮项目澄清 S0 gate → 模型必发问 + 不调写工具 → 用户回应解锁 ✅
4. S1 web_search per_turn=3 → 一轮 3 次 OK + 第 4 次 quota_exhausted ✅
5. S4 dispatcher → append_report_draft 起草 + edit_file(## 第二章...) 重写 ✅
6. `<think>` 折叠 UI → 默认 closed + 点击展开可读 reasoning ✅
7. mutation_limit=3 → 一轮 3 次 mutation 通过 + 第 4 次 reject + 错误消息含 mutations 摘要 ✅

## 已知 limitation

- spec §7.6: partial obligation retry 漏检"用户要 N 处 model 改 1 处但口头声称 N 处都改完"。接受。
```

- [ ] **Step 2: 填实际 commit SHA + 测试结果**

```bash
git log --oneline -5
.venv\Scripts\python -m pytest tests/ -q --tb=no | tail -5
```

填入模板。

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/cutover_report_2026-05-08_deepseek-migration.md
git commit -m "docs(deepseek-migration): add cutover report"
```

### Task 39: 更新 worklist + memory

**Files:**
- Modify: `docs/current-worklist.md`
- Modify: `C:/Users/36932/.claude/projects/D--MyProject-CodeProject-consulting-report-agent/memory/project-consulting-report-agent-current-focus.md`

- [ ] **Step 1: 移除 worklist 中已 ship 项**

如 worklist 中之前列了"deepseek-migration spec 实施" → 划掉/标 done。

- [ ] **Step 2: 更新 memory 当前 focus**

current-focus.md 改为下一阶段任务（如 "等使用反馈 + UI 重构准备"），保留 deepseek-migration 的 cutover 链接。

- [ ] **Step 3: Commit**

```bash
git add docs/current-worklist.md
git commit -m "docs(deepseek-migration): update worklist post-cutover"
```

memory 文件**不**进 git（在 `~/.claude/`），单独保存。

---

## E2E Smoke 验证清单（spec §5.5，**7 个独立场景**——与 cutover report 模板对齐，fixed per R2 P2-4）

每个 commit 完成后人工跑一次 packaged exe smoke：

1. **全新机器首启**（删 `~/.consulting-report/`）→ managed_model auto-set 为 deepseek-v4-pro，无 error；启动后 `app.log` 已生成（log file rotate 配置一并验证：写够 5MB 触发 backup → 场景 1 内验）
2. **老配置 heal**（手工写 `config.json` 含 `managed_model: "gemini-3-flash"`）启动 → `app.log` 含 heal 通知 + config 已切换 + `/v1/models` 实际请求成功
3. **首轮项目澄清 S0 gate** → 新建项目 → model 必发问 + 不调写工具 → 用户回应解锁 → 第二轮可调 web_search/edit_file
4. **per-turn searches=3** → S1 阶段 model 一轮 3 次 web_search 全跑通；第 4 次返回 quota_exhausted
5. **S4 dispatcher** → `append_report_draft` 起草第一章 → `edit_file(old_string="## 第二章 战略")` 重写第二章 → mutations list 含 2 项
6. **`<think>` 折叠** → 前端 ThinkingBlock 默认折叠；点击 `<summary>` 展开可见 reasoning + escape 还原后无 sentinel 残留
7. **mutation_limit=3** → 一轮 3 次 mutation 全成功；第 4 次 `edit_file` 被 reject + 错误消息含 mutations list 摘要 + 真实当前字数

任意场景 fail → 创建 worklist item，回头修。

---

## Self-Review Checklist（实施前）

- [ ] Spec §1 背景理解清楚
- [ ] Spec §2 in-scope 9 项全部映射到 task（B/C/D/E/F/G/I/J/K + Commit 3 grep gate）
- [ ] Spec §3.10 已完成项标 Commit 0 ✅
- [ ] Spec §A 附录 grep 命令已嵌入 Task 36
- [ ] 每个 task 列出 explicit "Run" 命令（不让 agent 默认全套）
- [ ] 每个 task 有 Files 清单 + Step 编号 + 完整代码 + 期望结果
- [ ] Codex review loop 已嵌入每个 commit 末尾（task 22 / 27 / 37）
- [ ] cutover report task 已嵌入（task 38）

---

## Plan 完成 — Codex Review

写完后用以下 prompt 让 codex 审查 plan 本身：

```bash
cat > .codex-run/plan-review-prompt.md <<'EOF'
请 review 这份 implementation plan：
docs/superpowers/plans/2026-05-08-deepseek-migration-toolset-redesign.md

它对应的已 APPROVED spec：
docs/superpowers/specs/2026-05-08-deepseek-migration-toolset-redesign-design.md

重点检查：
1. spec §2.1 in-scope 9 项是否每项都至少 1 个 task 覆盖
2. spec §3.10 的 Commit 0 已完成项是否正确标 done
3. 每个 task 的 Files / Steps / Run 是否够具体让 fresh engineer 直接做
4. Test-first 顺序是否每个 task 都遵循（Step 1 写 test → Step 2 fail → Step 3 实现 → Step 4 pass）
5. Commit 1 的"加新代码不删旧代码"原则是否守住（不应在 Commit 1 删任何旧路径）
6. Commit 2 的"切流量"是否仅删 schema 注册 + dispatch 路由 + SKILL.md 引用，不删 callable
7. Commit 3 的 Quality gate grep 是否完整覆盖 spec §A
8. claim-only retry 机制是否正确保留（spec §3.5.4）
9. spec §6.1 用户数据兼容（老配置 / 老 conversation_state）是否在对应 task 中处理
10. spec §7.6 partial obligation retry 已知 limitation 是否正确接受（不需要新 task 修）

verdict: APPROVED / NEEDS_CHANGES（NEEDS_CHANGES 时列出具体问题 + 优先级 P0/P1/P2）
EOF

codex exec --cd "$(pwd)" --color never \
  --output-last-message .codex-run/plan-review-last.txt \
  < .codex-run/plan-review-prompt.md > .codex-run/plan-review-full.log 2>&1 &
```

修复后再次 review，循环直到 APPROVED。

---

## Notes for the implementer

1. **Per-task test only** — 每个 task 末尾的 "Run" 命令是该 task 完成后**唯一**必跑的检查。不要默认 `pytest tests/`。最终回归在每个 commit 末尾的 "Final test pass" task 一次性跑全套。
2. **Codex review at every commit** — Commit 1 / 2 / 3 末尾都嵌入了 codex review loop。**APPROVED 后才进入下一 commit**。NEEDS_CHANGES 直接修，不要堆到下个 commit。
3. **Test-first** — 每个 task 都 follow TDD：写 test → run fail → 写实现 → run pass。即使 spec 看起来很明确，先写 test 帮你 catch 实施偏差。
4. **Pseudocode markers** — Plan 里 `_get_or_init_turn_context(...)` 等是占位伪代码。实施时按 chat.py 实际 turn_context 获取方式调整（grep `turn_context\s*=` 找现有 pattern）。
5. **Commit message convention** — 项目 commit message 用英文。本 plan task 末尾给的 message 模板是建议，可微调。
6. **Worktree 隔离（推荐）** — 由于改动量大且涉及 backend/chat.py 主线程逻辑，建议每个 commit 在独立 git worktree 上做（`superpowers:using-git-worktrees`）。
7. **不动**：spec §1 背景中的服务端 managed proxy 配置 / managed_client_token.txt / managed_search_pool.json 中除 per_turn_searches 外的字段。
