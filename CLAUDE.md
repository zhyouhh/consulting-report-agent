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

正式待办仍以 `docs/current-worklist.md` 为唯一真值源；2026-05-19 的打包态 S0-S7 记录在 `docs/superpowers/handoffs/2026-05-19-stage-conductor-packaged-qa.md`。

已修复并打包验证：

- 打包态 GUI 启动崩溃：`settings.mode` null 不再触发首页 error boundary。
- `_internal\skill\scripts\quality_check.ps1` / `export_draft.ps1` 在 Windows PowerShell 下的源码解析和 stdout 编码问题。
- `export_draft.ps1` 优先使用包内 `pandoc.exe`，`consulting_report.spec` 会把 Pandoc 打入 `_internal`。
- checkpoint endpoint 越级推进 / stage desync / legacy `<stage-ack>` runtime side effect。
- 聊天气泡 Markdown GFM 表格渲染。

仍需接续：

1. managed 真实模型长链路偶发 timeout / 无首包，阶段机本身已用确定性打包态 S0-S7 验收。
2. 打包与前端小债：`favicon.ico` 404、输入框 id/name 可访问性提示、`npm audit` high、Vite chunk warning、PyInstaller conda warning。
3. 图片附件按 `managed_model` 分流已推后到 UI 重构；stage-advance-gates Bug G/H 低优先级复核。

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

## S5 用户触发审查（2026-05-22 两按钮重做 → 2026-06-07 R1+R2 迷你聊天 + 断点续审）

S5 阶段审查由**两个用户主动触发按钮**驱动：

| 入口 | 路径 | 写入者 |
|---|---|---|
| 工作区"独立审查"按钮 | `plan/independent-review.md` | `backend/independent_review.py:IndependentReviewAgent`（独立 LLM 会话，5 维度判断）|
| 工作区"AI 味自查"按钮 | `plan/lint-report.md` | `skill/scripts/quality_check.ps1`（PowerShell 脚本，4 机械维度）|

报告就绪后前端自动起一轮主代理 turn（`ChatRequest.system_trigger` 协议 + `_chat_stream_unlocked` 内 `if system_trigger:` 分支）。

**R2（2026-06-07）改了汇报轮注入方式**：不再"让主代理 `read_file` 自己读报告"，而是**把报告全文作为本轮临时 user/context 数据消息注入**（trust boundary：数据非指令、绝不入 system），且**汇报轮禁工具**（请求层 pop tools + 响应层硬拦截 `_execute_tool`）——主代理必基于注入内容回复，恶意报告无法诱导工具调用 / 阶段推进。`system_triggered` 轮只持久化 assistant（报告全文不落 `conversation.json`）。

**R1（2026-06-07）把独立审查从"闷头读→一次性 write→结束"改造成流式迷你聊天窗口 + 断点续审**：

- **流式会说话 agent**：`IndependentReviewAgent.run()` 从非流式改为流式，content 增量作 `content_delta` SSE 事件推前端渲染；`<think>` 三路径剥离由 `backend/stream_parsing.py:ThinkingStreamParser` 负责（chat.py 主循环与 independent_review 共享 import，解循环导入），**前端永不收到 thinking**。
- **`ReviewSessionStore`（`independent_review.py` 内新增）**：进程内续审存档，两锁（review lock / store guard）+ `run_id` + tombstone（done/errored）+ candidate staging + 锁内原子替换（`os.replace`）+ 校验失败自修 ≤2 次后降级 errored 留 snapshot。candidate 从 messages 重建、不私存。
- **endpoint**：`POST /api/projects/{id}/independent-review/stream {resume,run_id,supplement?}` + `POST .../discard`（**旧 GET stream 已删**）。**worker（agent.run + review lock 释放）在 endpoint 函数体创建、不在 `generate()` 内**——Starlette `StreamingResponse` 用 task group 并发 stream_response + listen_for_disconnect、disconnect 抢先 cancel 时 `generate()` 可能一行未执行；worker 在函数体保证 review lock 必释放（否则该项目审查 409 到重启，codex C5 红队 B3）。completion 仅在 lock 释放后 + 重读 done tombstone 才发 `review-completed`。
- **run-bound 注入**：汇报轮绑定本次 run 的 tombstone，绝不汇报旧报告。`trigger_metadata={run_id, report_mtime_ns}`（**opaque 字符串、全程禁转 Number/int**，避 JS 2^53 失精）端到端透传：前端 `buildChatRequest` → `ChatRequest` → `/api/chat/stream` → chat.py tombstone 校验 + 读报告后 re-stat `mtime_ns` 复校（TOCTOU）。lint 路径无 run_id 维持 generic ready。

**关键约束**（baseline + R1/R2 叠加）：
- `_has_effective_review_reports()` 是 `CHECKPOINT_PREREQ.review_passed_at` 生产门禁；要求两份报告 marker + anchor + substantive body 全部命中
- 主代理 `write_file` / `edit_file` 对 `plan/independent-review.md` / `plan/lint-report.md` **显式拒绝**（独立性硬约束）；这两份报告只能由 IndependentReviewAgent / lint 脚本写入
- `_has_effective_review_checklist()` 函数与 `review-checklist.md` 模板保留向后兼容但**不再被生产路径调用**
- `IndependentReviewAgent.run()` 阈值 `MAX_DRAFT_WORDS_FOR_REVIEW = 100000`，超 100k 字 friendly fail（v0；chunk fallback 在 worklist P3）
- per-project lock（`_INDEPENDENT_REVIEW_LOCKS` / `_LINT_REPORT_LOCKS`）：同项目同时只能跑一次审查 / 一次 lint，409 拒并发
- DeepSeek 兼容 helpers（`_should_send_explicit_tool_choice` / `_extract_reasoning_content_from_message` / `_serialize_assistant_tool_call_message`）在 `independent_review.py` 与 `chat.py` 行为锁定一致（`test_deepseek_compat_helpers_match_chat_helpers`，已扩展到流式 follow-up）；流式改造不得破坏官渠兼容

**前端**：
- `IndependentReviewDrawer.jsx` 已重做为流式 **`ReviewChatWindow`**：前端生成 `run_id`（窗口全程不变）+ content_delta 聚合成连续 assistant 气泡（复用 `components/MarkdownMessage.jsx` 渲染）+ **可拖动 / 有关闭按钮（非仅 ESC）/ 带进度**。状态机 running（输入锁）/ errored（错误**留存不自动关**、解锁 supplement 输入框、「继续审查」带累计上下文从断处续）/ completed（自动关窗**不调 discard**；仅用户主动关才 discard）；409 指数退避有上限（5 次）后给出口；open-effect 守 `isOpen` 上升沿（切项目不误启动错误项目审查，红队 B1）。**无 jsdom**：聚合/状态机/队列抽 `utils/` 纯函数测 + 组件 source-guard。
- `triggerSystemTurn` 主聊天忙时入 **pending 队列**（`utils/pendingTriggerQueue.js`，FIFO 多条 + projectId 隔离），结束补发带原 metadata；发起新审查时剪同类型旧 pending（红队 B2）。`ChatPanel` `forwardRef + useImperativeHandle` 暴露 `triggerSystemTurn` / `dropPendingReviewTriggers`，`App.jsx` wire `chatPanelRef` 给 WorkspacePanel。
- `WorkspacePanel` completion 靠 run-bound 返回的 `{run_id, report_mtime_ns}` 触发（**不查 generic workspace ready**，防旧报告误判），保留 `shouldApplyProjectResponse` 项目切换 guard。
- `StagePanel.jsx` 按钮阶段化：S5 才显两个按钮 + 高亮；S6/S7/done 才显"导出可审草稿"。

**回归测试**：`tests/test_independent_review.py`（流式/staging/自修/CAS/run_id 防护/`os.replace` 失败/thinking 剥离）、`tests/test_lint_report.py`、`tests/test_main_api.py`（POST/resume/discard/lock 全路径/B3 generator-未消费 lock 释放/completion 时序）、`tests/test_chat_runtime.py`（system_trigger 注入/run-bound/`mtime` 大整数 str/主代理拒写）、`tests/test_skill_engine.py`、前端 `reviewChatWindow.test.mjs` + `independentReviewDrawer.source.test.mjs`。

详见 `docs/superpowers/cutover_report_2026-05-22_s5-redesign.md`（baseline）+ `docs/superpowers/cutover_report_2026-06-07_s5-review-mini-chat.md`（R1+R2）。

## 工作区文件栏 + 可编辑预览（R3，2026-06-09）

文件「语义」由 `backend/skill.py` 单一真值源给出，前端只做中文文案 + 渲染。改文件树 / 用户写接口前必读：

- `SkillEngine.FILE_SEMANTICS`（**完整 posix 路径**→group/stage，非 basename——否则 `materials/imported/outline.md` 误判 S1）、`USER_EDITABLE_FILES`（8 文件白名单，默认 deny）、`RETIRED_WORKSPACE_FILES`（不显示）；`is_user_editable` / `get_file_semantics` / `list_workspace_files`。白名单比对用 `_canonical_user_path`（整路径 casefold，**不复用**只处理 plan/*.md 的 `_canonicalize_plan_markdown_path`）。
- `validate_user_write` 是**独立于** `validate_plan_write` 的用户写门禁（白名单制，天然拒审查报告/追踪文件/退役/checkpoint）：穿越→`ValueError`(400)、非白名单→**`UserWriteForbiddenError`**(403)。**用专属异常而非内建 `PermissionError`**——`os.replace` 文件被外部程序占用时也抛 `PermissionError`，端点要把「领域拒写 403」与「OS 写失败 500（可重试提示）」分开（异常顺序：`UserWriteForbiddenError`→`StaleFileError`→`FileNotFoundError`→`ValueError`→`OSError`，`FileNotFoundError` 必排 `OSError` 前）。
- 写接口 `POST /api/projects/{id}/files/{path}` `{content, base_mtime_ns}`：mtime CAS（不匹配 `StaleFileError`→409）+ 同目录 temp + `os.replace` 原子写；`base_mtime_ns` 全程 **opaque str**（pydantic 拒 number→422）。**临界区跑专用 `_USER_WRITE_EXECUTOR`，不是 `run_in_threadpool`**——硬约束：`chat_stream` 是同步 generator、被 anyio 默认池迭代、`with request_lock:`（RLock）owner 是 anyio worker；保存若用默认池可能复用 owner 线程→RLock 重入放行→绕过 CAS。专用池线程绝非 chat worker，`acquire` 真阻塞到 chat 释放。**别改回 `run_in_threadpool`**（`test_main_api.py` 有 source-guard 守）。
- 读接口 `GET /files/{path}` 返回 `{content, mtime_ns, editable}`，**不持锁**（chat_stream 整轮持锁，读进锁会冻预览）：`read_file_with_mtime` 先 stat 再 read。AI 写**可编辑**文件（plan 内容文件 + canonical draft `edit_file`）全经原子 `write_file`（temp+`os.replace`），故无锁读不会读到半截、最坏=保存安全 409（只读追踪文件后端直写，极端下预览瞬时错乱、刷新自愈，不可编辑不入 CAS）。`GET /files` 给结构化 `[{path,group,stage,editable,mtime_ns}]`。
- `get_workspace_summary().flags.review_stale`（D6 advisory）：两份审查报告**有效**（`_has_effective_review_reports`，非 scaffold 模板）且 `draft_mtime > min(report mtimes)` 即标，**不** gate 在 `review_passed_at`；不硬阻 S6/S7。
- 前端：`utils/fileTree.js`（分组/置顶/中文名）、`utils/fileEditState.js`（双模式状态机 + `guardLeave` 返 `allow/confirm/block`）、`FilePreviewPanel.jsx`（forwardRef 暴露 `attemptLeave(action)`/`isEditing()`，脏离开**三按钮「保存/放弃修改/取消」延后动作**弹窗 + Esc=取消 + 进入编辑 `selectionSeqRef` 防竞态）、`WorkspacePanel.jsx`/`App.jsx`（切 tab/切项目/新建项目/收面板 dirty 守卫，ref 链 App→WorkspacePanel→FilePreviewPanel）。**`WorkspacePanel.loadFile` 同步 `setCurrentFile(path)` 再异步 GET 内容**——消除「导航已发起、currentFile 未 commit」窗口（否则进入编辑/保存会锁错文件）；`latestFileRequestRef` 丢弃乱序 content 响应。
- 回归：`tests/test_skill_engine.py`、`tests/test_main_api.py::R3FileApiTests`；前端 `fileTree`/`fileEditState`/`filePreviewPanel.source`/`workspacePanel.source`。详见 `docs/superpowers/cutover_report_2026-06-09_r3-file-tree-editing.md`。

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
- `docs/superpowers/plans/` 与 `docs/superpowers/specs/` — 正式变更的设计和落地计划，新功能改动前先去这里看相关 spec

发现正式待办别在 `debug-backlog.md` 里加新条目，直接加到 `current-worklist.md`。

## 测试与质量约定

- 后端用 `unittest` + `pytest` 发现，一律 mock 外部 HTTP（`curl_cffi_requests`、OpenAI 客户端等）。`tests/test_packaging_docs.py` 锁死了 BUILD.md/WINDOWS_BUILD.md 的关键句子，改文档时注意同步
- 前端测试用 Node 原生 `node:test`，不依赖 vitest/jest；单测聚焦 `utils/` 的纯函数和组件状态逻辑
- `tests/test_packaging_spec.py`、`test_packaging_docs.py`、`test_build_support.py` 是打包侧门禁，改 spec 或 build 脚本必跑

## 语言与文案

项目面向中文同事，UI 文案和文档均为中文。代码/命令/变量名/commit message 用英文。不要在用户可见文案里出现"赋能、抓手、闭环"这类 AI 味词汇，也不要暴露"AI reference""内部推理""系统提示"等后台术语（见 `skill/SKILL.md` 写作约束）。
