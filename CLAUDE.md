# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

Windows 优先的咨询报告写作桌面客户端。目标用户是不太懂 AI 的同事，交付形态是 `dist\咨询报告助手\` 整个文件夹（不是裸 exe）。当前只承诺 Windows 分发和 `可审草稿` 导出，不承诺 macOS 正式支持和最终排版稿。

## 运行时结构

桌面应用本质上是三层：

1. `app.py` 启动 `backend/main.py` 里的 FastAPI（`127.0.0.1:8080`），线程化跑在后台
2. `PyWebView` 打开内嵌窗口，加载同一 FastAPI 挂载的 `frontend/dist/` 静态 SPA
3. LLM 请求默认走 `managed` 模式（`https://newapi.z0y0h.work/client/v1`，模型 `deepseek-v4-pro`），由薄中转（见 `managed_proxy/app.py`）注入真实上游 key。用户可切到 `custom` 模式自填 OpenAI 兼容 API

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

## DeepSeek 官渠兼容

默认 managed 模型是 `deepseek-v4-pro`。这条官渠和 OpenAI 兼容层有几条硬约束，改 `backend/chat.py` 的 provider message / tool-call 逻辑时必须保留：

- 带 `tools` 的 DeepSeek 请求不要显式发送 `tool_choice="auto"`，让 provider 走默认工具选择；官渠 reasoner route 会拒绝这个字段
- assistant tool-call follow-up 要回传非空 `reasoning_content`，否则 thinking/tool-call 链路可能被上游拒绝
- 不要把 SDK `model_dump()` 里的 null 字段原样塞回历史消息；`reasoning_content: null`、`audio: null` 这类字段会触发官渠 400
- 回归测试集中在 `tests/test_chat_runtime.py` 的 DeepSeek/tool-call follow-up 用例

## 打包态 QA 接续（2026-05-19）

正式待办仍以 `docs/current-worklist.md` 为唯一真值源；最近一次打包态 S0-S7 记录在 `docs/superpowers/handoffs/2026-05-19-stage-conductor-packaged-qa.md`。

已修复并打包验证：

- 打包态 GUI 启动崩溃：`settings.mode` null 不再触发首页 error boundary。
- `_internal\skill\scripts\quality_check.ps1` / `export_draft.ps1` 在 Windows PowerShell 下的源码解析和 stdout 编码问题。
- checkpoint endpoint 越级推进 / stage desync / legacy `<stage-ack>` runtime side effect。
- 聊天气泡 Markdown GFM 表格渲染。

仍需接续：

1. `export_draft.ps1` 仍依赖系统 `pandoc`；本机打包态导出通过，但 `dist\咨询报告助手\` 尚未自带 `pandoc.exe`。
2. managed 真实模型长链路偶发 timeout / 无首包，阶段机本身已用确定性打包态 S0-S7 验收。
3. 打包与前端小债：`favicon.ico` 404、输入框 id/name 可访问性提示、`npm audit` high、Vite chunk warning、PyInstaller conda warning。

## Skill 工作流（S0-S7）

`skill/SKILL.md` 定义的阶段状态机由 `backend/skill.py:SkillEngine` 执行。**几个硬约束**，改动任何阶段/plan 文件逻辑前必须理解：

- `plan/project-overview.md` 是项目元信息唯一真值源
- `plan/stage-gates.md`、`plan/progress.md`、`plan/tasks.md` **由后端自动回写**，模型不能手写，测试/代码里也别假设它们是手工维护
- `plan/project-info.md` 已退役，不要新建、读取或引用
- 禁止创建 `gate-control.md`
- 阶段推进 / 回退的模型侧唯一入口是 `advance_stage(checkpoint_key="...", action="set|clear", reason="...")`；不要恢复 `StageAckParser`、`<stage-ack>` 执行语义、强关键词 checkpoint fallback，也不要让模型直接写 `stage_checkpoints.json`
- `SkillEngine.record_stage_checkpoint()` 是 checkpoint 真正写入口，`set` 前必须校验前序阶段、实质文件和质量门禁；API endpoint 和 `advance_stage` 都应委派它
- `backend/chat.py` 和前端仍保留 legacy `<stage-ack>` sanitizer / tail guard，只用于剥离历史残留；命中 sanitizer 不得产生 checkpoint side effect
- 阶段回归测试集中在 `tests/test_skill_engine.py`（transition validation）、`tests/test_chat_runtime.py`（`advance_stage`、legacy sanitizer、写入门禁）、`tests/test_main_api.py`（checkpoint endpoint）和 `tests/test_packaging_docs.py`（SKILL 文档约束）
- 写 `outline.md` / `research-plan.md` 前必须先 `web_search → fetch_url → 写入 notes.md/references.md`，门禁在 `backend/chat.py`（`_should_require_fetch_url_before_write`、证据计数与质量门禁逻辑）

## S4 写正文工具（2026-05-09 DeepSeek migration）

S4 阶段（大纲已确认）报告正文唯一规范路径是 `content/report_draft_v1.md`。DeepSeek migration 后正文工具集从 4 个专用工具收敛为 **1 个生成工具 + 通用 edit dispatcher**：

| 工具 | 用途 | 关键约束 |
|---|---|---|
| `append_report_draft(content)` | 起草 / 续写 / 写下一章 | 首次起草 draft 不存在时跳过 read-before-write check |
| `edit_file(file_path, old_string, new_string)` | 修改已有正文 | `file_path` 为 `content/report_draft_v1.md` 时走 canonical draft dispatcher；先 `read_file`，`old_string` 必须是章节锚点 / H1 整篇锚点 / 唯一文本 |

`edit_file` 对 canonical draft 的分派规则：

- `old_string` 以 `## ` 开头：`resolve_section_anchor()` 只用首行 h2 label 定位整章 snapshot，可用于章节重写 / 删除。
- `old_string` 等于 draft 第一行 H1 且用户明确说"整篇/全文/推倒重来"：整篇重写。
- 其他情况：`old_string` 必须在 draft 中唯一出现，用于文字替换 / 删除。
- `write_file` **不接受** `content/report_draft_v1.md`；首次起草或续写必须用 `append_report_draft`。

正文写入入口 inline 调 6 个 invariant check helpers（stage / outline / mixed-intent / mutation-limit / read-before-write+mtime / fetch_url-pending），全部定义在 `backend/report_writing.py`（pure functions，无 `chat.py` 反向 import）。

**关键约束**：
- 旧专用工具 `rewrite_report_section` / `replace_report_text` / `rewrite_report_draft` 已删除；不要新建、注册或引用。
- `canonical_draft_mutations` 是 list；每轮最多 3 次 canonical draft mutation，超限错误必须带 mutations 摘要和真实进度。
- read-before-write：先 `read_file` 才能改（首次起草除外）；mtime 变了要重读

**Turn-end 对账**：`_chat_*_unlocked` no-tool-call 分支检测 `canonical_obligation` set + `canonical_draft_mutations` 为空 + assistant 文本声称已写 → 注入 corrective user message + retry。只兜底"完全没写却声称写了"，不解决 partial obligation retry。

**历史背景**：原 `<draft-action>` tag system + classifier + gate + scope enforcement 整套（含 fix4 v5 amendment）已于 2026-05-06 删除；4 专用工具中的 3 个旧工具与 gemini 时代 obligation / family-lock 控制层已于 2026-05-09 DeepSeek migration 删除。详见 `docs/superpowers/cutover_report_2026-05-08_deepseek-migration.md`。

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
