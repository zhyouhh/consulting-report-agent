# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## 项目定位

Windows 优先的咨询报告写作桌面客户端。目标用户是不太懂 AI 的同事，交付形态是 `dist\咨询报告助手\` 整个文件夹（不是裸 exe）。当前只承诺 Windows 分发和 `可审草稿` 导出，不承诺 macOS 正式支持和最终排版稿。

## 运行时结构

桌面应用本质上是三层：

1. `app.py` 启动 `backend/main.py` 里的 FastAPI（`127.0.0.1:8080`），线程化跑在后台
2. `PyWebView` 打开内嵌窗口，加载同一 FastAPI 挂载的 `frontend/dist/` 静态 SPA
3. LLM 请求默认走 `managed` 模式（`https://newapi.z0y0h.work/client/v1`，模型 `gemini-3-flash`），由薄中转（见 `managed_proxy/app.py`）注入真实上游 key。用户可切到 `custom` 模式自填 OpenAI 兼容 API

`DesktopBridge`（`app.py`）通过 `register_desktop_bridge()` 把原生文件选择器暴露给 FastAPI，这是"本地 HTTP API 能调用原生 OS 对话框"的唯一通道——Web 模式（`run_web.py`）下这些接口会 503。

## 关键数据边界

**运行时用户数据全部位于** `~/.consulting-report/`（即 `C:\Users\<user>\.consulting-report\`）：

- `config.json` — `Settings` 序列化（排除 `mode/api_key/api_base/model/projects_dir/skill_dir/managed_client_token` 等运行时派生字段）
- `projects/<project_id>/` — 每个项目的完整工作区（对话历史、plan 文件、正文、附件）
- `search_runtime_state.json`、`search_cache.json` — 内置搜索池动态状态与缓存

**构建期私有文件**（`.gitignore` 已忽略，必须本地注入）：

- `managed_client_token.txt` — `/client` 的 client token（**不是**上游 API key）。`build.ps1` 会打包前请求 `/client/v1/models` 预检
- `managed_search_pool.json` — 内置搜索池 provider 凭据，schema 见 `backend/config.py:load_managed_search_pool_config_from_path`。这份文件会**随安装包一起分发**，不是服务端秘密

`backend/config.py:get_base_path()` 在 PyInstaller 打包态下返回 `sys._MEIPASS`，在开发态下返回仓库根，所有相对路径寻址都必须经过它。

## Skill 工作流（S0-S7）

`skill/SKILL.md` 定义的阶段状态机由 `backend/skill.py:SkillEngine` 执行。**几个硬约束**，改动任何阶段/plan 文件逻辑前必须理解：

- `plan/project-overview.md` 是项目元信息唯一真值源
- `plan/stage-gates.md`、`plan/progress.md`、`plan/tasks.md` **由后端自动回写**，模型不能手写，测试/代码里也别假设它们是手工维护
- `plan/project-info.md` 已退役，不要新建、读取或引用
- 禁止创建 `gate-control.md`
- 写 `outline.md` / `research-plan.md` 前必须先 `web_search → fetch_url → 写入 notes.md/references.md`，门禁在 `backend/chat.py`（`NON_PLAN_WRITE_ALLOW_KEYWORDS`、`FILE_UPDATE_VERBS`、证据计数逻辑）

## S4 写正文工具（2026-05-06 redesign）

S4 阶段（大纲已确认）model 通过以下 4 个**专用工具**修改 `content/report_draft_v1.md`，统一在 `backend/chat.py:_execute_tool` 派发：

| 工具 | 用途 | 关键约束 |
|---|---|---|
| `append_report_draft(content)` | 起草 / 续写 / 写下一章 | 首次起草 draft 不存在时跳过 read-before-write check |
| `rewrite_report_section(content)` | 重写章节（user 说"重写第N章"） | `content` 必须 `## ` 开头 + 仅 1 个 h2 + 长度 ≤ `max(3000, 3*snapshot.length)`；后端用 `resolve_section_target` 自己定位章节 snapshot |
| `replace_report_text(old, new)` | 文字替换（"把 X 改成 Y"） | `old` 必须在 draft 中**唯一**出现 |
| `rewrite_report_draft(content)` | 整篇重写（"整篇重写"/"推倒重来"） | `content` 必须 `# ` 开头 + ≥ 1 个 `## ` + 长度 ≤ `max(8000, 2*current.length)` |

每个工具入口 inline 调 6 个 invariant check helpers（stage / outline / mixed-intent / mutation-limit / read-before-write+mtime / fetch_url-pending），全部定义在 `backend/report_writing.py`（pure functions，无 `chat.py` 反向 import）。后端用 preflight 已 resolve 的 snapshot 自己控制 `old_string`——model 完全不复述大段文本，结构性绕开 gemini-3-flash 等小模型的复述能力约束。

**关键约束**：
- 不要对 `content/report_draft_v1.md` 用通用 `edit_file` / `write_file`，legacy gate 已只接受这 4 个语义工具（chat.py:5620 + 6081 satisfaction check 白名单）
- 一轮一改：`turn_context["canonical_draft_mutation"]` 限制每轮 ≤ 1 次 canonical write
- read-before-write：先 `read_file` 才能改（首次起草除外）；mtime 变了要重读

**Turn-end 对账**：`_chat_*_unlocked` no-tool-call 分支检测 `canonical_draft_write_obligation` set + 0 mutation + assistant 文本声称已写 → 注入 corrective user message + retry。

**历史背景**：原 `<draft-action>` tag system + classifier + gate + scope enforcement 整套（含 fix4 v5 amendment）已于 2026-05-06 整体删除（净减 6300+ 行）。详见 `docs/superpowers/specs/2026-05-05-report-tools-redesign-design.md` + `docs/superpowers/cutover_report_2026-05-06_tools-redesign.md`。

## 管理型搜索池

`backend/search_pool.py:SearchRouter` 实现分层路由：`primary` → `secondary` → 可选 `native_fallback`。Provider 适配器在 `backend/search_providers.py`（Tavily/Brave/Exa/Serper），状态存储在 `backend/search_state.py`。`per_turn_searches` / `project_minute_limit` / `global_minute_limit` 是并列门禁，任一触发都会返回 `QUOTA_EXHAUSTED_MESSAGE`。

路由单例在 `ChatHandler` 里（`_SEARCH_ROUTER_SINGLETON`），`managed_search_pool.json` 一旦加载不会热重载，改配置需要重启。

## 常用命令

所有命令在仓库根执行。Windows 开发机需要 Python 3.11/3.12 + Node 20 LTS。

```bash
# 开发环境初始化
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
cd frontend && npm install && cd ..

# 启动桌面应用（开发态）
cd frontend && npm run build && cd ..
python app.py

# 前端热更新开发（配合已跑起来的 FastAPI）
cd frontend && npm run dev   # 3000 端口，代理 /api → 8080

# 后端单元测试
.venv\Scripts\python -m pytest tests/                      # 全部
.venv\Scripts\python -m pytest tests/test_chat_runtime.py  # 单文件
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::ChatRuntimeTests::test_xxx  # 单用例

# 前端测试（Node 原生 test runner，不是 vitest）
cd frontend && node --test tests/chatMaterials.test.mjs
cd frontend && node --test tests/                          # 全部

# Windows 打包（必须先放好 managed_client_token.txt 和 managed_search_pool.json）
build.bat                    # 等价于 powershell -File build.ps1
# 或直接：.venv\Scripts\python -m PyInstaller consulting_report.spec
```

**打包前常被忽略的坑**：PyInstaller 必须用项目 `.venv`，不能在 Anaconda 全局环境里打（会从 1GB+ 膨胀）。`build.ps1` 会强制检查 `.venv` 是否存在。

## 文档与追踪

- `docs/current-worklist.md` — 当前待解决/待验证事项的唯一真值源
- `docs/debug-backlog.md` — 已归档的调试历史，**不再维护**当前待办
- `docs/superpowers/plans/` 与 `docs/superpowers/specs/` — 正式变更的设计和落地计划，新功能改动前先去这里看最近的 spec

发现正式待办别在 `debug-backlog.md` 里加新条目，直接加到 `current-worklist.md`。

## 测试与质量约定

- 后端用 `unittest` + `pytest` 发现，一律 mock 外部 HTTP（`curl_cffi_requests`、OpenAI 客户端等）。`tests/test_packaging_docs.py` 锁死了 BUILD.md/WINDOWS_BUILD.md 的关键句子，改文档时注意同步
- 前端测试用 Node 原生 `node:test`，不依赖 vitest/jest；单测聚焦 `utils/` 的纯函数和组件状态逻辑
- `tests/test_packaging_spec.py`、`test_packaging_docs.py`、`test_build_support.py` 是打包侧门禁，改 spec 或 build 脚本必跑

## 语言与文案

项目面向中文同事，UI 文案和文档均为中文。代码/命令/变量名/commit message 用英文。不要在用户可见文案里出现"赋能、抓手、闭环"这类 AI 味词汇，也不要暴露"AI reference""内部推理""系统提示"等后台术语（见 `skill/SKILL.md` 写作约束）。
