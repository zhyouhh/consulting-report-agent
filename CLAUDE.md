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

**⚠️ W2-B/B1 后数据根改为 `data_root()`（`backend/config.py`，`CRA_DATA_ROOT` 环境变量，缺省 = `~/.consulting-report/`）下的 per-uid 分层**（详见下方「## W2-B 多租户基座」段）：

- `<data-root>/app.db` — SQLite 账号库（users/sessions/app_config），见 `backend/accounts.py`
- `<data-root>/users/<uid>/config.json` — **per-uid** `Settings`（排除 `mode/api_key/api_base/model/projects_dir/skill_dir/managed_client_token` 等运行时派生字段）
- `<data-root>/users/<uid>/projects/<project_id>/` — 每个项目的完整工作区（对话历史、plan 文件、正文、附件）。**桌面态 uid 硬绑 `"local"`** → 桌面项目现位于 `~/.consulting-report/users/local/projects/`（**B1 行为变更**：老桌面用户既有 `~/.consulting-report/projects/` 需迁移；桌面已去重点化）
- `<data-root>/search_runtime_state.json`、`search_cache.json` — 内置搜索池动态状态与缓存（**不分 uid**；隔离靠复合键 `tenant_project_key(uid,cid)` 进 cache/quota key，见下方 W2-B 段）

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
- ~~`_internal\skill\scripts\export_draft.ps1` 在 Windows PowerShell 下的源码解析和 stdout 编码问题。~~（**W2-C 2026-06-23 已整条退役 PowerShell 导出脚本，导出改纯 Python 调 pandoc——见下方「## W2-C」段**）
- `consulting_report.spec` 会把 Pandoc 打入 `_internal`；导出在打包/Windows 态优先用包内 `pandoc.exe`（现由后端 `report_tools._resolve_pandoc()` 直接解析调用，不再经 PowerShell 脚本）。
- checkpoint endpoint 越级推进 / stage desync / legacy `<stage-ack>` runtime side effect。
- 聊天气泡 Markdown GFM 表格渲染。

仍需接续：

1. ~~managed 真实模型长链路偶发 timeout / 无首包~~（**✅ 2026-07-06 正面解**：`backend/provider_retry.py` 瞬态自动重试——create 3 次尝试 + 无可见输出断流重发，见「## 试用反馈两修复 + 模型调用重试 + /admin 独立管理页」段）。
2. 打包与前端小债：~~`favicon.ico` 404~~（✅ web 端 2026-07-01 已结清，复用桌面 `app_icon.ico`，见下方「## 中右分栏拖动」部署流程段）、输入框 id/name 可访问性提示、`npm audit` high、Vite chunk warning、PyInstaller conda warning。
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
- `canonical_draft_mutations` 是 list；每轮最多 `MAX_CANONICAL_MUTATIONS_PER_TURN`（现 10）次 canonical draft mutation，超限错误必须带 mutations 摘要和真实进度。
- read-before-write：先 `read_file` 才能改（首次起草除外）；mtime 变了要重读

**Turn-end 对账**：`_chat_*_unlocked` no-tool-call 分支检测 `canonical_obligation` set + `canonical_draft_mutations` 为空 + assistant 文本声称已写 → 注入 corrective user message + retry。只兜底"完全没写却声称写了"，不解决 partial obligation retry。

**历史背景**：原 `<draft-action>` tag system + classifier + gate + scope enforcement 整套（含 fix4 v5 amendment）已于 2026-05-06 删除；4 专用工具中的 3 个旧工具与 gemini 时代 obligation / family-lock 控制层已于 2026-05-09 DeepSeek migration 删除。详见 `docs/superpowers/cutover_report_2026-05-08_deepseek-migration.md`。

## S5 用户触发审查（2026-05-22 重做 → 2026-06-07 R1+R2 迷你聊天 + 断点续审 → N7 统一为单一独立审查）

S5 阶段审查由**唯一一个用户主动触发按钮**驱动（N7：原"AI 味自查"机械脚本路径整条删除，去 AI 味并入独立审查维度⑤）：

| 入口 | 路径 | 写入者 |
|---|---|---|
| 工作区"独立审查"按钮 | `plan/independent-review.md` | `backend/independent_review.py:IndependentReviewAgent`（独立 LLM 会话，5 维度判断，含「语言专业性·去 AI 味」）|

报告就绪后前端自动起一轮主代理 turn（`ChatRequest.system_trigger` 协议 + `_chat_stream_unlocked` 内 `if system_trigger:` 分支；现仅 `independent_review_done` 一种 trigger）。

**去 AI 味（N7 Humanizer-zh）**：独立审查维度⑤=「语言专业性·去 AI 味」，规则在 review prompt 内置。辅以**确定性占位符扫描** `backend/report_quality.py:scan_placeholders` / `build_placeholder_grounding`（穷举正文半成品标记），首轮把命中清单作 grounding 注入审查会话（`UNTRUSTED_DATA` 包裹、定界符中和、50 行上限）。trust boundary 标记与中和器抽到 leaf 模块 `backend/trust_boundary.py`（`UNTRUSTED_DATA_OPEN/CLOSE` + neutralizer），`report_quality` 与 `independent_review` 共享 import。

**R2（2026-06-07）汇报轮注入方式**：不"让主代理 `read_file` 自己读报告"，而是**把报告全文作为本轮临时 user/context 数据消息注入**（trust boundary：数据非指令、绝不入 system），且**汇报轮禁工具**（请求层 pop tools + 响应层硬拦截 `_execute_tool`）——主代理必基于注入内容回复，恶意报告无法诱导工具调用 / 阶段推进。`system_triggered` 轮只持久化 assistant（报告全文不落 `conversation.json`）。

**R1（2026-06-07）独立审查=流式迷你聊天窗口 + 断点续审**：

- **流式会说话 agent**：`IndependentReviewAgent.run()` 流式，content 增量作 `content_delta` SSE 事件推前端渲染；`<think>` 三路径剥离由 `backend/stream_parsing.py:ThinkingStreamParser` 负责（chat.py 主循环与 independent_review 共享 import，解循环导入），**前端永不收到 thinking**。
- **`ReviewSessionStore`（`independent_review.py` 内）**：进程内续审存档，两锁（review lock / store guard）+ `run_id` + tombstone（done/errored）+ candidate staging + 锁内原子替换（`os.replace`）+ 校验失败自修 ≤2 次后降级 errored 留 snapshot。candidate 从 messages 重建、不私存。
- **endpoint**：`POST /api/projects/{id}/independent-review/stream {resume,run_id,supplement?}` + `POST .../discard`（**旧 GET stream 已删**）。**worker（agent.run + review lock 释放）在 endpoint 函数体创建、不在 `generate()` 内**——Starlette `StreamingResponse` 用 task group 并发 stream_response + listen_for_disconnect、disconnect 抢先 cancel 时 `generate()` 可能一行未执行；worker 在函数体保证 review lock 必释放（否则该项目审查 409 到重启，codex C5 红队 B3）。completion 仅在 lock 释放后 + 重读 done tombstone 才发 `review-completed`。
- **run-bound 注入**：汇报轮绑定本次 run 的 tombstone，绝不汇报旧报告。`trigger_metadata={run_id, report_mtime_ns}`（**opaque 字符串、全程禁转 Number/int**，避 JS 2^53 失精）端到端透传：前端 `buildChatRequest` → `ChatRequest` → `/api/chat/stream` → chat.py tombstone 校验 + 读报告后 re-stat `mtime_ns` 复校（TOCTOU）。

**关键约束**（baseline + R1/R2 + N7）：
- `_has_effective_independent_review()` 是 `CHECKPOINT_PREREQ.review_passed_at` 生产门禁；要求 marker + anchor + substantive body 全部命中（单份独立审查报告）
- 主代理 `write_file` / `edit_file` 对 `plan/independent-review.md` **显式拒绝**（独立性硬约束）；这份报告只能由 IndependentReviewAgent 写入
- `_has_effective_review_checklist()` 函数与 `review-checklist.md` 模板保留向后兼容但**不再被生产路径调用**
- `IndependentReviewAgent.run()` 阈值 `MAX_DRAFT_WORDS_FOR_REVIEW = 100000`，超 100k 字 friendly fail（v0；chunk fallback 在 worklist P3）
- per-project lock（`_INDEPENDENT_REVIEW_LOCKS`）：同项目同时只能跑一次审查，409 拒并发
- DeepSeek 兼容 helpers（`_should_send_explicit_tool_choice` / `_extract_reasoning_content_from_message` / `_serialize_assistant_tool_call_message`）在 `independent_review.py` 与 `chat.py` 行为锁定一致（`test_deepseek_compat_helpers_match_chat_helpers`，已扩展到流式 follow-up）；流式改造不得破坏官渠兼容

**前端**：
- `IndependentReviewDrawer.jsx` 已重做为流式 **`ReviewChatWindow`**：前端生成 `run_id`（窗口全程不变）+ content_delta 聚合成连续 assistant 气泡（复用 `components/MarkdownMessage.jsx` 渲染）+ **可拖动 / 有关闭按钮（非仅 ESC）/ 带进度**。状态机 running（输入锁）/ errored（错误**留存不自动关**、解锁 supplement 输入框、「继续审查」带累计上下文从断处续）/ completed（自动关窗**不调 discard**；仅用户主动关才 discard）；409 指数退避有上限（5 次）后给出口；open-effect 守 `isOpen` 上升沿（切项目不误启动错误项目审查，红队 B1）。**无 jsdom**：聚合/状态机/队列抽 `utils/` 纯函数测 + 组件 source-guard。
- `triggerSystemTurn` 主聊天忙时入 **pending 队列**（`utils/pendingTriggerQueue.js`，FIFO 多条 + projectId 隔离），结束补发带原 metadata；发起新审查时剪同类型旧 pending（红队 B2）。`ChatPanel` `forwardRef + useImperativeHandle` 暴露 `triggerSystemTurn` / `dropPendingReviewTriggers`，`App.jsx` wire `chatPanelRef` 给 WorkspacePanel。
- `WorkspacePanel` completion 靠 run-bound 返回的 `{run_id, report_mtime_ns}` 触发（**不查 generic workspace ready**，防旧报告误判），保留 `shouldApplyProjectResponse` 项目切换 guard。
- `StagePanel.jsx` 按钮阶段化：S5 才显"独立审查"按钮（唯一审查按钮）+ 高亮；S6/S7/done 才显"导出可审草稿"。

**回归测试**：`tests/test_independent_review.py`（流式/staging/自修/CAS/run_id 防护/`os.replace` 失败/thinking 剥离/去 AI 味维度/占位符 grounding）、`tests/test_report_quality.py`（占位符扫描）、`tests/test_main_api.py`（POST/resume/discard/lock 全路径/B3 generator-未消费 lock 释放/completion 时序）、`tests/test_chat_runtime.py`（system_trigger 注入/run-bound/`mtime` 大整数 str/主代理拒写）、`tests/test_skill_engine.py`、前端 `reviewChatWindow.test.mjs` + `independentReviewDrawer.source.test.mjs`。

详见 `docs/superpowers/cutover_report_2026-05-22_s5-redesign.md`（baseline）+ `docs/superpowers/cutover_report_2026-06-07_s5-review-mini-chat.md`（R1+R2）。N7 统一审查 + 去 AI 味见 `docs/current-worklist.md` 与 N7 cutover。

## 工作区文件栏 + 可编辑预览（R3，2026-06-09）

文件「语义」由 `backend/skill.py` 单一真值源给出，前端只做中文文案 + 渲染。改文件树 / 用户写接口前必读：

- `SkillEngine.FILE_SEMANTICS`（**完整 posix 路径**→group/stage，非 basename——否则 `materials/imported/outline.md` 误判 S1）、`USER_EDITABLE_FILES`（8 文件白名单，默认 deny）、`RETIRED_WORKSPACE_FILES`（不显示）；`is_user_editable` / `get_file_semantics` / `list_workspace_files`。白名单比对用 `_canonical_user_path`（整路径 casefold，**不复用**只处理 plan/*.md 的 `_canonicalize_plan_markdown_path`）。
- `validate_user_write` 是**独立于** `validate_plan_write` 的用户写门禁（白名单制，天然拒审查报告/追踪文件/退役/checkpoint）：穿越→`ValueError`(400)、非白名单→**`UserWriteForbiddenError`**(403)。**用专属异常而非内建 `PermissionError`**——`os.replace` 文件被外部程序占用时也抛 `PermissionError`，端点要把「领域拒写 403」与「OS 写失败 500（可重试提示）」分开（异常顺序：`UserWriteForbiddenError`→`StaleFileError`→`FileNotFoundError`→`ValueError`→`OSError`，`FileNotFoundError` 必排 `OSError` 前）。
- 写接口 `POST /api/projects/{id}/files/{path}` `{content, base_mtime_ns}`：mtime CAS（不匹配 `StaleFileError`→409）+ 同目录 temp + `os.replace` 原子写；`base_mtime_ns` 全程 **opaque str**（pydantic 拒 number→422）。**临界区跑专用 `_USER_WRITE_EXECUTOR`，不是 `run_in_threadpool`**——硬约束：`chat_stream` 是同步 generator、被 anyio 默认池迭代、`with request_lock:`（RLock）owner 是 anyio worker；保存若用默认池可能复用 owner 线程→RLock 重入放行→绕过 CAS。专用池线程绝非 chat worker，`acquire` 真阻塞到 chat 释放。**别改回 `run_in_threadpool`**（`test_main_api.py` 有 source-guard 守）。
- 读接口 `GET /files/{path}` 返回 `{content, mtime_ns, editable}`，**不持锁**（chat_stream 整轮持锁，读进锁会冻预览）：`read_file_with_mtime` 先 stat 再 read。AI 写**可编辑**文件（plan 内容文件 + canonical draft `edit_file`）全经原子 `write_file`（temp+`os.replace`），故无锁读不会读到半截、最坏=保存安全 409（只读追踪文件后端直写，极端下预览瞬时错乱、刷新自愈，不可编辑不入 CAS）。`GET /files` 给结构化 `[{path,group,stage,editable,mtime_ns}]`。
- `get_workspace_summary().flags.review_stale`（D6 advisory）：独立审查报告**有效**（`_has_effective_independent_review`，非 scaffold 模板）且 `draft_mtime > report_mtime` 即标，**不** gate 在 `review_passed_at`；不硬阻 S6/S7。
- 前端：`utils/fileTree.js`（分组/置顶/中文名；**2026-06-19 N4：当前阶段所在分组整组置顶**，splice+unshift、其余保持 GROUP_ORDER）、`utils/fileEditState.js`（双模式状态机 + `guardLeave` 返 `allow/confirm/block`）、`FilePreviewPanel.jsx`（forwardRef 暴露 `attemptLeave(action)`/`isEditing()`，脏离开**三按钮「保存/放弃修改/取消」延后动作**弹窗 + Esc=取消 + 进入编辑 `selectionSeqRef` 防竞态）、`WorkspacePanel.jsx`/`App.jsx`（切 tab/切项目/新建项目/收面板 dirty 守卫，ref 链 App→WorkspacePanel→FilePreviewPanel）。**`WorkspacePanel.loadFile` 同步 `setCurrentFile(path)` 再异步 GET 内容**——消除「导航已发起、currentFile 未 commit」窗口（否则进入编辑/保存会锁错文件）；`latestFileRequestRef` 丢弃乱序 content 响应。
- **N4（2026-06-19）文件树/预览上下分栏**：`FilePreviewPanel` 文件树高度由 `treePct` state 驱动（默认**三七分** 30/70，去掉旧固定 `max-h-64`）+ 可拖动分隔条（`startTreeResize`，window 级监听 + `resizeCleanupRef` + 卸载 `useEffect` 兜底清理防泄漏）；拖动数学抽 `utils/filePanelLayout.js`（`clampTreePct`/`computeTreePct` 纯函数，无 jsdom 单测 + source-guard）。
- 回归：`tests/test_skill_engine.py`、`tests/test_main_api.py::R3FileApiTests`；前端 `fileTree`/`fileEditState`/`filePreviewPanel.source`/`workspacePanel.source`。详见 `docs/superpowers/cutover_report_2026-06-09_r3-file-tree-editing.md`。

## 来源可信度标注（R4，2026-06-11）

`skill/SKILL.md` S2 段内置三档来源可信度（🟢高/🟡中高/⚪其他，**按机构性质非域名**——data-log 来源含 material/访谈/调研，一半无域名），模型在 `data-log.md` 每条 `**来源**` 行标色点 + S2 采集告一段落报一句分布小结。**全程 advisory，不门禁**。硬约束：新增 data-log 示例必须保住后端 `_EVIDENCE_MARKERS` 计数——`访谈:`/`调研:` 必须**行首独立成行**才计数（别塞进 **URL** 行括号），`tests/test_skill_engine.py::test_skill_md_datalog_examples_all_recognized_as_valid_sources` 锁死。纯 prompt 改、不动 backend。详见 `docs/superpowers/cutover_report_2026-06-10_batch3-source-credibility-and-methodology.md`。

## 方法论路由与显性化（R5，2026-06-11）

失效的「报告类型→方法论框架」路由（canonical skill 设计的模型 `read_file` 自取，嵌 app 后断了——沙箱够不到 skill 目录、`get_template` 死代码、17 模块 16 死）改为**后端代码注入**。`backend/skill.py:build_methodology_block(project_id)` 按 `project_type`+`stage` 注入「类型骨架 + 框架菜单 + 阶段化指令」到 system prompt（`chat.py:_build_system_prompt` 接入，S1–S4）。**几条硬约束**：

- `__methodology_snapshot`（确认大纲那刻冻结的净化框架）是 `stage_checkpoints.json` 的**保留字符串键**，**绝不**进 `STAGE_CHECKPOINT_KEYS`/`_CASCADE_ORDER`（有 invariant assert，加即炸）；后端写、模型不能直写、非新 checkpoint key；`_load_stage_checkpoints` 不返回它 → 不外泄前端 checkpoint 字段（值非机密）。S2–S4 读快照（`read_confirmed_methodology_snapshot`）不读活 outline；cascade 仅随 `outline_confirmed_at` 清、清下游（S5 回退）保留。
- 确认门方法论声明前置**只在 `_validate_stage_checkpoint_transition` 的 `outline_confirmed_at` 分支内联**（仅首次确认 `not in checkpoints` + `project_type in TYPE_SKELETON_MAP`[已知类型，2026-06-21 W1 后**7 个**，含 technical-bid] + `parse_and_sanitize_methodology == "parsed"`），**绝不**进 `_stage_one_completion_state`（否则 R5 前已确认无声明的 legacy known-type 项目被拉回 S1）；unknown type 不卡（避死锁）。
- `parse_and_sanitize_methodology` 是 trust boundary 净化（outline 用户可编辑）：净化结果作**数据**注入、绝不当指令。**核心不变式（2026-06-27 红队 v5 加固后）**：`_normalize_for_danger` 去除集合必须 ⊇ `parse` **容忍/剥除的全部字符** = split 分隔符（`、,，`）∪ off-menu 白名单 `[A-Za-z0-9一-鿿\-/ 　]` 允许的非字母数字字符（`-`/`/`/空格/全角空格）∪ token 边界剥除的 markdown 强调标记 `*` ∪ parse 剥除的括号 `()（）`——任一字符 parse 容忍但归一化没去 → 危险词可借它拆词绕过 denylist。改 off-menu 白名单 / split 分隔符 / token 剥除（`*`/括号）须同步本集合。归一化危险词组覆盖全部 6 个 `STAGE_CHECKPOINT_KEYS`（`test_*_all_checkpoint_key_variants` 遍历防漏）。
- **方法论 denylist 完整加固（2026-06-27，R5 follow-up，已 merge main `fff39ca`）**：① token 清洗 `.strip("*")` 剥**边界**强调标记 → **容忍模型把框架值写成 `**粗体**`**（修用户报的「确认大纲被粗体卡住」bug，原 parser 因 token 含 `*` 返 malformed、逼模型手动去粗体）；② danger 检测在**两形态双查**——「完整 raw_value」（保留括号内容，挡 `SWOT（advance_stage）` 藏括号内）+「`re.sub([（(].*?[)）])` 剥括号跨度后」（镜像 parse 剥括号，挡 `write(x)file`/`stage(x)-ack` 借填充括号拆词）；③ `_METHODOLOGY_ST_FOLD` 繁→简折叠表（仅控制词 18 个繁体字，`_normalize_for_danger` 现为 classmethod 应用折叠）闭合**简繁混写**（`歸档`/`无視`/`設为`，NFKC 不做简繁转换、off-menu 放行任意 CJK、枚举挡不完）——**闭合整类、零依赖、不靠枚举**；折叠只覆盖控制词字符故合法繁体框架名（`價值鏈` 不在表内）不受影响、零误杀。Codex spec+quality 双轨 + 4 轮对抗红队收敛 APPROVED（红队挖出并闭合既有括号/简繁绕过——非本次 bold 引入）。已知限制：嵌套括号注释 `SWOT（优势（内部）分析）` malformed（fails-closed 安全，单层括号注释支持）；其它 Unicode 同形字 out-of-scope（数据框定 + 后端阶段校验是真防护）。回归 `tests/test_skill_engine.py`（`test_parse_methodology_bold_marker_on_values_parsed` / `*_asterisk_cannot_evade_danger` / `*_paren_and_chinese_split_cannot_evade_danger` / `*_mixed_simplified_traditional_cannot_evade_danger`）。
- **方法论声明位置（2026-06-21 修，W1 真模型 E2E 暴露）**：`parse_and_sanitize_methodology` 只扫**首个 `## ` 之前**的 head（H1 不算；防正文里「方法论框架：」误解析，红队 v2——**不可放宽 break 级**，否则破 `test_parse_methodology_ignores_declaration_below_body` 的 H2-章节语义、弱化「正文里声明不算」保护）。故 `skill/plan-template/outline.md` 内置声明槽位（`**方法论框架**：` 行在 `## 确认状态` 之前＝唯一会被扫的顶部区）+ `_declare_and_invite_instruction` 指令点明「第一行、在 `## 确认状态` 之前」——否则模型（deepseek 实测）镜像模板把声明写到 `## 确认状态` 之下 → 扫不到 → 确认大纲门对全 7 类硬卡。
- `build_methodology_block` 装配期**只读**（不写文件）；unknown type / 非写作期（S0、S5+）graceful 空块、**不抛进 chat 链路**；token ≤2k/轮（tiktoken 实测断言）。
- DeepSeek 官渠兼容：方法论注入只给 system prompt **追加文本**，不碰 provider message / tool-call / `reasoning_content` / `tool_choice`；`chat_runtime` DeepSeek 用例不回归。
- 前端 `methodology_declared` flag（`_infer_stage_state` flags）驱动 S1 确认按钮 + 禁用理由，后端未透则向后兼容不阻塞（`?? true`）。
- 全程只改 app 副本 `skill/`，不碰 canonical `consulting-report-skill/`。删了死码 `get_template()` + `skill/templates/`。
- **follow-up**（非阻塞，桌面单用户低优先级，记 `docs/current-worklist.md`）：checkpoint 写事务化（record set 两阶段写 `outline_confirmed_at`+snapshot → 一次原子 raw 写，消除 crash 半提交，危害仅退 missing 兜底）、backfill 窄粒度锁/CAS。
- 回归：`tests/test_skill_engine.py`（净化/快照/确认门/装配/flag）、`tests/test_chat_runtime.py`（装配 + DeepSeek targeted）、`tests/test_packaging_docs.py`、前端 `workspaceSummary`/`stageAdvanceControl`。详见 `docs/superpowers/cutover_report_2026-06-10_batch3-source-credibility-and-methodology.md`。

## N6 附件管线（2026-06-21 实施完成 + 已 merge main `95949ab` + push origin + F4 上线 jp-app-01）

上传素材统一转 markdown/文本再喂模型，结清 #4（图片上传拦截）。改材料读取 / 附件 / 防注入 / 薄网关前必读。

**转换服务** `backend/material_conversion.py:MaterialConverter`（**DI 纯边界，绝不 import chat**——source-guard 测试锁死，连 docstring 都不能含 `import chat` 子串；不反向依赖 SkillEngine/project，只暴露纯函数 `cache_key_from_sha256` + 只读属性 `image_cache_extra` 供 SkillEngine 算 key）：
- 文档：txt 直读；docx/pptx/xlsx/pdf/csv/html 走 markitdown（`enable_plugins=False`）；老 .doc/.ppt→LibreOffice headless 转现代格式再 markitdown，.xls markitdown(xlrd) 优先失败回退 LibreOffice。markitdown 0.1.6 对损坏 docx 不报错 → 加了 ZIP-magic(`PK\x03\x04`)文件头校验。
- 图片：`transcribe_image`（持久带缓存）/`transcribe_image_data_url`（transient 不入持久缓存）→ vision adapter→OCR adapter→`MaterialConversionError`。
- 缓存：内容 hash key + tombstone(.error) + 引用计数 GC(.refs sidecar，shared-hash 安全) + 原子写。**`convert_document` 先快照源文件再 hash+解析**（关 live workspace 文件 TOCTOU 缓存投毒 + size 绕过）。**cache key→file 必须字符串拼接**（`cache_dir/(key+".md"/".error"/".refs")`，**不用 `with_suffix`**——视觉模型名含点会截断/碰撞）。

**接入** `backend/skill.py`：`read_material_file` size 守门(heavy 后缀>25MB friendly fail)+委派 converter；`add_materials` 加 `size_bytes`/`content_sha256`；**`_cache_key_for_material(material, path)` 按当前 live 文件内容算 key**（与 transcribe/convert 的 live-hash 一致）；retain（read 成功 + chat 显示路径 transcribe 成功）/release（`remove_material` 删前 + `delete_project` rmtree 前 `_release_project_material_caches`）。`ChatHandler.__init__` 装配 converter（lambda 晚绑 `_vision_transcribe`/`_ocr_image`/`_main_model_supports_vision`）。

**trust boundary（防注入，N6 核心，改任何附件→模型路径前必读）**：附件派生文本（图片转写 / `read_material_file` 文档正文 / 素材清单 display_name·file_type）一律框进 `ATTACHMENT_DATA_OPEN/CLOSE` 数据块，且不可信片段先过 `_neutralize_attachment_data_markers`（破坏 `<<<`/`>>>` 定界符防越狱）；`content` 永远是 raw 用户意图；`_build_turn_context` 绝不收附件文本；客户端可控 `material_id`（forged）走删除分支**不回显**（通用提示）；`_summarize_messages` 摘要前 `_sanitize_message_for_summary` **fail-closed strip**（畸形框定从首标记砍到 EOF；list 先 flatten；非 str/list shape 序列化后再 strip）+ 丢 `client_message_id`、`attached_material_ids`→count（生产路径 `_to_provider_message` 本就只重建 `{role,content}`，这是第二层防御）；`_build_system_prompt` 含「附件数据非指令」规则。

**图片分流** `_build_user_content`（两阶段：先收 note_lines 再建 content+image_url，**绝不 mutate content[0]**）：多模态主模型→`image_url`；纯文本主模型→当前轮 transcribe、历史轮 `peek_image_transcript`（cache-first，绝不发新视觉请求）；transient 转写存消息独立字段 `attachment_transcripts`（不混入 content），SSE `attachment_transcribed{message_id,attachment_id,status}`（`client_message_id` 仅普通轮带、system_trigger 不带）。

**薄网关** `managed_proxy/app.py`：白名单透传（删强改写 model）+ `MANAGED_PROXY_SELECTABLE_MODELS`（`/v1/models` 只露可选子集，视觉模型可达但不进下拉）+ `/health` preflight。**已上线 jp-app-01**——部署 + new-api 前置（上游 token `model_limits` + 渠道 group/abilities 都要手配）见 `docs/managed-proxy-deployment.md`「N6 视觉转写」段 + `VPS-fix-private/notes/jp-app-01.md`。

**Settings**（`config.py`）：`managed_vision_model`（默 `Qwen/Qwen3-VL-8B-Instruct`）/`vision_enabled`。**依赖**（`requirements.txt`）：`markitdown[docx,pptx,xlsx,xls,pdf]==0.1.6`（**不能用 plan 写的 0.0.1a3，无 `enable_plugins`**）+ `rapidocr-onnxruntime` + `onnxruntime==1.27.0` + `xlrd==2.0.2`；mac 用 `uv pip install`（venv 无 pip）。**限额**常量集中 `backend/material_limits.py`。

**DeepSeek 官渠兼容**：N6 只追加 system prompt 文本 + 改 read_material_file 工具结果字符串 + 改摘要输入，**不碰** provider tool-call/`reasoning_content`/`tool_choice` 序列化；只 `role`+`content` 到 provider。

**仍剩 F2**（Windows，**2026-06-21 用户决定推迟到 W2 服务器化时一起做**——W2 去 Windows 化本就要改解析层）：`build.bat` 打包 smoke 逐格式验 → 过后删 skill.py feature-flag 期保留的 `_legacy_read_document`/`_read_docx`/`_read_xlsx`/`_read_pdf`。

**回归**：`tests/test_material_conversion.py`、`test_managed_proxy.py`、`test_chat_runtime.py`（vision/OCR/transcripts/意图隔离/防注入+compaction 对抗/DeepSeek）、`test_skill_engine.py`（size/refcount/delete release）、`test_models.py`/`test_main_api.py`（限额/状态 API）、前端 `sseEvents`/`chatMaterials`/`modelCapabilities`。详见 `docs/superpowers/cutover_report_2026-06-20_n6-attachment-pipeline.md`。

## W1 技术标（technical-bid）报告类型（2026-06-21 实施 + 真模型 GUI E2E 通过；已 merge main 本地 `9e9a869`）

第 7 个 `project_type=technical-bid`（技术标/投标，UI 中文名「技术标」），接 R5 方法论路由。改类型注册 / 方法论装配 / outline 模板前必读：

- `TYPE_SKELETON_MAP` 加 `technical-bid → technical-bid.md`、`METHODOLOGY_TONE` 加 `technical-bid → bid`（新腔调）；两 dict 的 slug 集必须一致（`build_methodology_block` 用 `TONE.get` fallback，漂移会静默错腔调）。
- 新模块 `skill/modules/technical-bid.md`：常驻注入规则全在 `## 二、标准结构` 段（`load_type_skeleton` 只截「## 二」、遇下一 `## ` 即止，代码块内 `## ` 安全）；`## 一`/`## 三` 不注入。改模块勿把规则挪出「## 二」。
- **bid 不注入通用 `FRAMEWORK_MENU`**（按评分点驱动、不挑分析框架，且菜单叠加爆 token≤2k）：`_framework_menu_for_type(project_type)` seam——bid 返 `""`，其余 6 类返 `FRAMEWORK_MENU`；`build_methodology_block` 用 seam 而非硬编 `self.FRAMEWORK_MENU`。worst-case 注入块（technical-bid）实测 ~1712 ≤ 2000。
- bid 声明腔调（`_declare_and_invite_instruction` 的 `if tone == "bid"`）：举例框架名一律安全词（评分点对标、点对点应答、WBS、重难点对策），避 `_METHODOLOGY_DANGER_SUBSTRINGS`（覆盖/推进/检查点…）；走 off-menu 白名单、不进 `KNOWN_FRAMEWORK_NAMES`。
- 后置两表（技术评分索引表 + 技术规范书点对点应答）：正文写完用 `append_report_draft` **追加在草稿末尾**，不用 `edit_file`（撞 generative-intent 拦截/cap）、不前插；「写最前 + 页码」交导出排版期。落点锁测在 `test_chat_runtime.py`（强内容断言：旧稿保留 + 两表在后，非只验末行）。
- 材料 size 守门由 N6 接管（plan 原 Task 5/6 删——N6 §5 已覆盖 `size_bytes` + `material_limits.MAX_HEAVY_MATERIAL_BYTES`），W1 不重做。
- 前端 `ProjectCreateModal.jsx` 下拉加「技术标（投标）」→ `technical-bid`；`skill/plan-template/project-overview.md` 报告类型占位与 `_populate_v2_plan_files` 替换 key **必须逐字一致**（否则新建项目占位换不掉）。
- 回归：`tests/test_skill_engine.py`（七类/seam/注入内容/bid 腔调/净化守护/声明槽位）、`test_chat_runtime.py`（两表 append 落点锁）、前端 `projectCreateModal`。详见 `docs/superpowers/cutover_report_2026-06-20_w1-technical-bid.md`。

## W2-B 多租户基座（B1，2026-06-22 实施完成 + merge main `c62cd4d` + push origin；分支 `feat/w2b-multi-tenant-core` 保留）

把单用户引擎改成「登录后每用户工作区完全隔离」的多租户 Web 基座。**改鉴权 / 数据路径 / 进程内锁/store/搜索键 / 项目创建前必读。** 详尽交付与红队修复见 `docs/superpowers/cutover_report_2026-06-22_w2b-b1.md`；spec `docs/superpowers/specs/2026-06-21-w2b-multi-tenant-core-design.md`、plan `docs/superpowers/plans/2026-06-21-w2b-b1-tenant-base-auth.md`。

**新增叶子模块**：
- `backend/tenant.py`（**只依赖 config，绝不 import chat/skill/main**）：`data_root()` 下的 per-uid 路径助手 + `tenant_project_key(uid, project_id)`（**唯一中央复合键**，无损可逆转义 `\`/`:` 再以 `::` 连接，杜绝跨租户碰撞，**任何处禁止手拼**）+ `_safe_path_component`（uid 作路径段前拒 `/`/`\`/`..`/`.`/`:`/空——含 Windows 盘符；uuid-hex 与 "local" 通过）。
- `backend/accounts.py`（**只依赖 tenant**）：SQLite 账号层（argon2 密码、sha256 session token、`app_config` 邀请码）。`_db()` 上下文管理器**提交+关闭**（别退回 `with _connect()`）；公共 `get_user_by_*` **剥 password_hash**（内部 `_get_user_row` 才带）；`create_session` 原子 `INSERT...SELECT...WHERE disabled=0` fail-closed 拒停用/不存在用户；`set_user_disabled` 停用与撤销会话**同一事务**。

**main.py 接线（硬约束）**：
- per-uid 工厂 `get_skill_engine(uid)` / `get_chat_handler(uid, project_id)`（缓存键 `(uid, project_id)` 元组）；`SkillEngine`/`ChatHandler` 持 `self.uid`（`ChatHandler.__init__` 断言 `skill_engine.uid==uid`，防分叉）。
- 鉴权依赖 `get_current_uid`（`app.state.auth_required` falsy → "local"；否则 httpOnly cookie `cra_session` → `accounts.get_session_uid` → 401）/ `get_current_admin`（403）。
- **统一归属卡点** `require_project(project_id, uid=Depends(get_current_uid)) -> ProjectScope{uid, project_id(canonical rec["id"]), engine, project_record, lock_key}`：canonicalize id-or-name → `rec["id"]`，查不到即 **404**（非属主用户自己的引擎 registry 无该项目 → 天然隔离，按 id 和按名称都不泄漏）。**所有 `{project_id}` 端点必经 `require_project`**，用 `scope.engine.M(scope.project_id, ...)`，不得用原始路径 project_id 寻址引擎/锁/store。
- `/api/auth/{register,login,logout,me,change-password}`：邀请门（`secrets.compare_digest`）、httpOnly cookie（`samesite=lax`、`secure` 取 `app.state.cookie_secure`）、**logout 幂等**（不依赖有效会话，总清 cookie）、改密保当前会话撤其它、桌面合成 local `/me`。register/login `@limiter.limit("10/minute")`。
- web 创建项目**服务端分配工作区**（`user_projects_dir(uid)/<uuid hex>`）、按 `model_fields_set` **拒收客户端** `workspace_dir`/`initial_material_paths`（400）；桌面态客户端路径照旧。

**复合键不变式（隔离核心）**：进程内 per-project 状态全按 `tenant_project_key(uid, project_id)` 键化——`chat.py` 请求锁 `_get_project_request_lock`(模块级 registry 接已合成键)/会话锁 `_get_conversation_state_lock`、`skill.py:record_stage_checkpoint`（请求锁 + 审查锁检查）、`independent_review.py` 审查锁 `get_independent_review_lock` + `_REVIEW_SESSION_STORE`、搜索 cache + project-minute 配额（`chat.py` 的 `router.search(project_id=tenant_project_key(self.uid, ...))`；**global 配额仍全局共享**）。
- **审查侧 `store_key` vs `project_id` 分离（红队 CRITICAL，改 IndependentReviewAgent 前必读）**：`IndependentReviewAgent.run(project_id, ..., store_key=None)` 用 **canonical `project_id`** 做文件/引擎访问（`get_primary_report_path`/`get_project_path`/`_execute_tool`），用 **`store_key`（=端点 `scope.lock_key` 复合键，默认回落 project_id 向后兼容）** 做所有 `store.*`（`set_errored`/`atomic_commit_report`）；`_commit_verified_candidate` 同。端点 worker 传 `store_key=review_key`、`agent.run(review_project_id)`。混用会让端点 claim（复合）与 agent commit（canonical）键不一致 → 审查保存失败、tombstone 写错键、chat 读不到。

**桌面（`app.py`）/ web（`run_web.py`）入口 + 启动安全门** `assert_safe_startup(auth_required, host)`：桌面 `auth_required=False` + `cookie_secure=False` + 强制 loopback host；web `auth_required=True` + **强制 `CRA_INVITE_CODE`**（否则拒启动）。`app.state.auth_required` 模块级缺省 True（直连 `uvicorn backend.main:app` 默认安全 web 态）。

**环境变量**：`CRA_DATA_ROOT`（数据根）；`CRA_INVITE_CODE`（**web 必设**，含 mac 本地 `run_web.py`——例 `CRA_INVITE_CODE=devcode .venv/bin/python run_web.py`；env 已设则 `set_config` upsert 每次启动权威，未设则随机码 fail-closed 锁死注册）；`CRA_BOOTSTRAP_ADMIN_USERNAME`+`CRA_BOOTSTRAP_ADMIN_PASSWORD`（首启建管理员、`must_change_password=True`、幂等）。

**B1 明确未做（已由 B2/B3 落地——下列均为 B1 当时的边界，现已实现，见「## W2-B/B2」「## W2-B/B3」段）**：custom 模式 B1 时 managed-forced（per-uid settings 只隔离**存储**，custom 激活+SSRF 归 B3——**B3 已激活：`mode` 持久化 + `url_guard` 白名单 SSRF**）；中央计费 + per-user ¥/天配额（B2 已落地）；admin 面板、CSRF/SSRF/CORS 硬化（B1 时 `allow_origins=["*"]` + SameSite=Lax 基线 → **B3 已收紧到 allowlist + Origin 中间件**）、`must_change_password` 路由级强制、per-username 限流（**B3 已落地**）。

**回归**：`tests/test_tenant.py`、`test_accounts.py`、`test_auth_api.py`（含 `AuthApiTestBase`：reload(main) + mock heal + 单例 reset）、`test_tenant_isolation.py`（复合键/搜索隔离/`CrossTenantApiTests` 跨租户 404）、`test_settings_api.py`、`test_project_create_api.py`；既有端点测试已迁移到租户作用域（`auth_required=False` → uid="local" + `get_project_record` mock + 复合 store 键）。**写端点测试**：`AuthApiTestBase` 起隔离 `CRA_DATA_ROOT`；非鉴权端点测试设 `app.state.auth_required=False` 跑 local。

## W2-B/B2 中央计费 + per-user 配额（2026-06-22 实施完成 + merge main `c2916b1` + push origin；分支 `feat/w2b-b2-billing` 保留）

所有 managed LLM/视觉调用经单一 `MeteredManagedClient` 出口计费。改计费 / usage 解析 / 调用点客户端构造 / 配额门禁前必读。详尽交付与红队修复见 `docs/superpowers/cutover_report_2026-06-22_w2b-b2.md`；plan `docs/superpowers/plans/2026-06-22-w2b-b2-central-billing-quota.md`。

**新增叶子模块 `backend/metering.py`**（**只依赖 accounts/config/context_policy，绝不 import chat/skill/main/independent_review**）：
- `price_micro_yuan(model,hit,miss,completion,pricing)` 三档计价（token×元每百万token=微元，`round`；未知模型 `FALLBACK_MODEL_PRICING`）。
- `extract_billing_usage(usage)` → `BillingUsage|None`。读 deepseek `prompt_cache_hit/miss_tokens`+`completion_tokens`（miss 缺失回退 `prompt-hit`）。**fail-closed 契约（红队 4 轮锁死）**：单值经 `_coerce_token`——None（字段缺省）→0；present-but-malformed（非 int/float、bool、inf/nan、负、> `_MAX_PLAUSIBLE_TOKENS`=1e9）一律 raise→返回 None。**绝不静默归零**（归零会假记 0 费用 + 复位缺失计数、绕过暂停保护）。
- `MeteredManagedClient(raw,uid,model_pricing)`：镜像 `.chat.completions.create`，调用前 `_reserve`（`used>=cap`→`QuotaExceededError`；缺失计数≥3→`ModelPausedError`），调用后/流末 `_settle`（`add_usage` 原子累加 + 成功清缺失计数）。**流式 `_metered_stream` 在 `finally` 恰好结算一次**（自然结束/provider 异常/GeneratorExit 一律 fail-closed），`finally` 内 settle 包独立内层 finally（settle 抛错不遮蔽在途异常、底层流必 `close()`，`sys.exc_info()` 判在途异常）。`__getattr__` 透传非 `.chat.completions.create` 调用面（如 `.responses`）。**⚠️ fail-closed 结算的金额与去向 2026-07-06 已改**（256k 封顶 → 请求感知估算 + `failclosed_tokens` 独立列 + GeneratorExit 不计暂停），见下方「## fail-closed 计费修复 + admin 用量趋势折线图」段——settle-once/不抛/不静默归零等不变式**未变**。
- `wrap_client_for_billing(raw,uid,settings)`：managed→`MeteredManagedClient`；custom→裸 client。
- `today_shanghai()` UTC+8 日界；`_miss_counter` 进程级 per-(uid,model,**day**)（day 入键 → 暂停次日自动清零）。

**accounts.py**：`usage_daily(uid,day,cost_micro_yuan,cache_hit/miss_tokens,output_tokens,failclosed_tokens, PK(uid,day))` 原子 `ON CONFLICT DO UPDATE` 累加（`failclosed_tokens` 2026-07-06 加列，`init_db` 幂等 ALTER 迁移老库）；`get_usage_today`/`add_usage`/`get_effective_daily_cap_micro`（user override `users.daily_cost_micro_yuan` → 全局 `app_config.global_daily_cap_micro_yuan` → 默认 `DEFAULT_GLOBAL_DAILY_CAP_MICRO_YUAN`=5_000_000）/`set_user_daily_cap_micro`。

**接线硬约束**：
- chat.py / independent_review.py：managed 模式 `self.client = metering.wrap_client_for_billing(...)`，5 个调用点（主流式/sync/压缩/视觉/审查）调用语法零改动自动计费。**metering 引用必须模块限定**（`from . import metering` + `metering.QuotaExceededError`，**不用** `from .metering import` 拷贝名）——`importlib.reload(metering)` 在同模块对象内重建异常类、拷贝名变陈旧与 wrapper 实抛的活类 isinstance 失配（仅测试态 reload 触发，但保持模块限定使套件顺序无关）。
- **被动 include_usage**：wrapper 不注入，调用点自报、**仅 managed**（`self.settings.mode=="managed"` 才发 `stream_options={"include_usage":True}`）；chat.py 既有 `_should_retry_stream_without_usage` 回退保留。
- **消费侧 `finally: response.close()`**：chat.py 主流式 + independent_review.py 审查流的 `for chunk in response:` 后必 `finally: response.close()`（同步触发 wrapper settle，防提前 break/return 把结算延到 GC 致下次 reserve 在结算前发生）；close 失败 `logger.warning` 不 silent pass。
- 配额异常处理：chat.py 主流式/sync + independent_review.py 在通用 `except` **之前**截 `(QuotaExceededError, ModelPausedError)` → 友好提示。**`ChatResponse.system_notices` 是 `List[SystemNotice]` 对象、非 `List[str]`** → 配额友好返回 `system_notices=None`、提示放 `content`。
- main.py：`/api/chat` 非流式预检（`require_project` 后、`get_chat_handler` **前**，仅 `mode=="managed"` 才碰 metering/accounts，`used>=cap`→友好 200 不建 handler）；`/api/auth/me` 两分支（local 合成 + 真实用户）都加 `today_cost_yuan`/`daily_cap_yuan`；review 端点构造 agent 传 `uid=scope.uid`。
- **DeepSeek 官渠兼容**：B2 只加 reserve/settle + 被动 usage，**不碰** provider message/tool-call/`reasoning_content`/`tool_choice` 序列化。

**前端**：`utils/quotaFormat.js`（`formatYuan`/`quotaLabel`/`quotaRatio`，全 `Number.isFinite` 归一、`quotaRatio` 恒 [0,1] 不返 NaN）；`Sidebar.jsx` 账号块额度行——外层守卫 `authUser && (uid!=='local' || typeof daily_cap_yuan==='number')`，**登出按钮**仍只对非-local（避困登录页），**额度行对 local 也显示**（local 经 managed 计费、受默认 ¥5/天 cap）。

**已知限制 / 待决策**：软帽非原子 reserve（spec §11，并发略超一轮容忍）；从未消费的流不结算；**`_settle` 失败 best-effort**（任何 settle/DB 写失败一律 `logger.warning` 记日志、绝不抛给调用方、漏计一次——计费是成本护栏非支付系统，不因记账抖动崩用户操作）；`.responses` 透传不计费（managed 不走，B3 custom 处理）；单进程 `_miss_counter`；custom 计费：B2 时 managed-forced 故生产不可达，**B3 已激活 custom（走裸 client 不计费——用户自带 key，见「## W2-B/B3」段）**；**⚠️ 桌面 local 受 ¥5/天默认 cap、会被 reserve 拦**（若不想桌面同事被限需单独配置豁免——属配置/产品决策）。

**回归**：`tests/test_metering.py`（计价/usage fail-closed/reserve/settle/暂停/工厂/source-guard）、`test_accounts.py::UsageDailyTests`、`test_chat_runtime.py::B2BillingWiringTests`+`B2BillingSettleTests`（含真 reserve 集成 + reload 回归守卫 + 压缩/视觉真 settle）、`test_independent_review.py::B2ReviewBillingTests`、`test_main_api.py::B2ChatQuotaTests`、`test_auth_api.py::MeCostFieldsTests`、前端 `quotaFormat`/`sidebarQuota.source`。**B2 测试夹具**：reserve/settle 在 managed chat/review 单测里会真跑——base setUp 须隔离 `CRA_DATA_ROOT`+`init_db`+把两道闸门设不触发（巨大 cap + `MAX_CONSECUTIVE_USAGE_MISS`），pause/quota 真行为由 `test_metering.py` 独立覆盖。

## W2-B/B3 admin 面板 + 安全硬化 + custom 激活（2026-06-23 实施完成 + merge main `450acba`（--no-ff）+ push origin；分支 `feat/w2b-b3-admin-security-hardening` 保留）

给多租户 Web 基座补 admin 面板 + CSRF/CORS/SSRF 硬化 + throttle-first 登录限流 + `must_change_password` 路由级强制 + **custom 真激活**。改鉴权 / SSRF 护栏 / settings 持久化 / admin 端点 / 登录限流前必读。详尽交付与红队叙事见 `docs/superpowers/cutover_report_2026-06-23_w2b-b3.md`；plan `docs/superpowers/plans/2026-06-22-w2b-b3-admin-security-hardening.md`。

**新增叶子模块 `backend/url_guard.py`**（**只依赖 httpx + stdlib，绝不 import chat/skill/main/config/accounts**——叶子铁律）：
- `assert_public_ip`：拒私网/loopback/link-local/multicast/reserved/unspecified/CGNAT(100.64/10)/metadata。chat.py `_ensure_public_ip` 委派它（IP 判定单一真值源；`SsrfBlockedError` 继承 `ValueError`，调用方 catch 不变）。
- **三层域名白名单 = 安全边界**：`builtin_allowed_hosts()`（managed 上游 + openai/deepseek/moonshot/智谱/通义）∪ `env_allowed_hosts()`（`CRA_CUSTOM_API_ALLOWED_HOSTS` bootstrap）∪ `_RUNTIME_ALLOWED_HOSTS`（app_config 运行时项，**admin 面板可增删、无需重启**，启动 `set_runtime_allowed_hosts` 注入）。**诚实定性**：白名单是「只有 admin 批准的主机能被连接」这一边界，**不声称通用防 DNS rebinding**——`_GuardedHTTPTransport` 未在连接层 pin IP、对「白名单内域名解析后到连接前翻转私网」仍有 TOCTOU；pinned-IP-with-SNI 为后置增强（spec §8.3 R3-NIT3 允许白名单为 B3 终态）。
- `validate_custom_api_base`：https + 白名单主机 + 解析公网（`assert_resolves_public`）+ 拒 userinfo（防 httpx 注入 `Authorization: Basic` 覆盖用户 Bearer key）+ 拒坏端口。`build_guarded_http_client`（`trust_env=False` 忽略代理 / `follow_redirects=False` 防重定向私网 / `_GuardedHTTPTransport` 每请求重校验）供 OpenAI SDK 用；chat.py / independent_review.py / `/api/models/list` 三处 client 统一走它。`is_valid_hostname`（纯主机名，TLD label ≤63）供 admin 白名单输入校验。

**custom 真激活（config.py）**：`normalize_settings_payload` 非 legacy honor `mode`（`config_version < DESKTOP_CONFIG_VERSION` 的 legacy 仍强制 managed 迁移安全）；**`mode` 现持久化**（从 `save_settings` 的剔除清单移除——之前被当运行时派生字段剔掉，导致 custom 选择存盘即丢、活不过一个请求，custom 从未端到端生效）；`managed_base_url` 服务端只读（normalize 强制回 `DEFAULT_MANAGED_BASE_URL` + 不持久化；`SettingsUpdate.managed_base_url` 改 Optional-ignore）。保存 custom 时端点用 `validate_custom_api_base` 即时校验（400）。

**CSRF / CORS / cookie（main.py）**：`csrf_origin_guard` 中间件——web 态（`app.state.auth_required`）对 POST/PUT/PATCH/DELETE 校验 Origin（缺失退 Referer）∈ allowlist，不匹配 403；桌面 loopback（`auth_required=False`）跳过；**生产（auth + cookie_secure）不信任 loopback 源**（CSRF 层运行时 `allowed_origins(include_loopback=not is_production)` 收紧，CORS 维持 import 期 `list(allowed_origins())` 快照——两者刻意不同步）。CORS 从 `allow_origins=["*"]` 收紧到 allowlist（loopback ∪ `CRA_ALLOWED_ORIGIN`）；web `cookie_secure=True`（`CRA_COOKIE_INSECURE` 本地 http 调试豁免）。**夹具铁律**：CSRF 上线后 web 态测试 TestClient 须带默认 Origin；缺-Origin 用例用 fresh `TestClient`（httpx 合并默认头不删）。

**登录限流 throttle-first（用户拍板，main.py）**：`_reserve_login_attempt` reserve-before-verify（login 处理体最前、验密之前调）、单锁原子（prune-this-key + 判上限 + append）、精确 username key（对齐大小写敏感账号查找，**不 casefold**）、有界 store（`deque(maxlen=_LOGIN_MAX_FAILS)` + `_MAX_TRACKED_LOGIN_KEYS=4096` + 增量 prune/evict）。**桶满直接 429 不验密 = 真封顶撞库**（verify-first 会架空撞库防护——密码仍被验、猜对即登入）；取舍 = 被攻击时该用户 ≤5min 临时锁定（自动恢复，用户选「撞库防护优先」）。成功登录 `_clear_login_fails`。per-IP slowapi `10/minute` 仍在（端点测试须 `m.limiter.enabled=False` 才隔离出 username 维度）。

**admin（main.py + accounts.py）**：8 个 `/api/admin/*`（GET users[带今日花费/cap]/invite-code/allowed-hosts[builtin/env/extra 三类]；POST users/{uid}/password·cap·disabled、invite-code/rotate、allowed-hosts[即时刷新 `set_runtime_allowed_hosts`]）全 `Depends(get_current_admin)`。`accounts.admin_set_user_disabled` **单个 `BEGIN IMMEDIATE` 写事务原子守卫**（消除并发互禁 TOCTOU、活跃 admin 绝不归零；禁最后一个活跃 admin → `LastAdminError` → 端点 400）；`admin_reset_password`（改 hash + `must_change_password=1` + 撤销全部会话同一事务）；`list_all_users`（剥 password_hash）；`rotate_invite_code` / `get/set_custom_api_extra_hosts`（app_config）。cap 用 `Decimal` 解析（字符串入参，`AdminCapBody: str|None`）+ 限长限幅（¥1e6 上限 + `is_finite` 拒 NaN/Inf）→ 400 不 500；allowed-hosts 用 `is_valid_hostname` 校验（拒 scheme/port/path/通配符/空白）。

**must_change_password 强制（main.py）**：`require_password_current`（web 态 `must_change_password` → 403、桌面短路）；**三层覆盖**——`require_project` 默认依赖（所有 path-param `{project_id}` 端点）+ `get_current_admin` 入参（所有 `/api/admin/*`）+ **显式 8 端点**（settings GET/POST、models/list、projects GET/POST、chat、chat/stream、桌面桥 select-workspace-folder/files——body-project 路由「先 `get_current_uid` 再手动调 `require_project`」改默认依赖覆盖不到，必须显式串）。豁免集精确 {me, change-password, logout, health}（否则死锁）。

**DeepSeek 官渠兼容**：B3 全程只加 system prompt 文本 / Starlette 中间件 / 依赖 / httpx 传输层（注入 `http_client`），**不碰** provider message/tool-call/`reasoning_content`/`tool_choice` 序列化；`chat_runtime` DeepSeek + `compat_helpers_match` 不回归。

**已知限制**：DNS rebinding TOCTOU 未彻底防（白名单=安全边界、非连接层 pin IP）；`_LOGIN_FAILS` + `_RUNTIME_ALLOWED_HOSTS` 单进程（多 worker 需共享/广播）；账户锁定 DoS（≤5min，用户接受）；FIFO eviction best-effort（>4096 不同用户名 flood 可复位桶）；custom_api_key 明文存 per-uid `config.json`（既有设计，custom 现激活使其生效）；软帽非原子（B2 沿用）。

**回归**：`tests/test_url_guard.py`(17) / `test_csrf.py`(9) / `test_admin_api.py`(16) / `test_accounts.py`(admin 函数) / `test_auth_api.py`(throttle + must_change) / `test_settings_api.py`(custom/offlist/managed_base 只读) / `test_config.py`(custom mode 持久化跨 reload + legacy 仍 managed) / `test_models.py`(offlist) / `test_tenant_isolation.py` + `test_main_api.py::CrossTenantApiTests` + 前端 `adminApi`/`adminPanel.source`/`forcePasswordChange.source`/`settingsModal.source`/`chatPanelCredentials.source`/`appInitGating.source`。

## W2-C 去 Windows 化导出 + web 下载 + 部署前置（Part A+B，2026-06-23 实施完成 + 本地全绿 + Codex 4-cluster 双轨审 APPROVED + 整分支自审 SHIP-READY；分支 `feat/w2c-de-windows-export`，**待 merge + Part C 部署交互式执行**）

把导出做成跨平台（Linux/mac/Windows）+ web 用户真能下载 docx，并补齐部署前置代码。改导出 / SSE 流 / web 入口前必读。spec `docs/superpowers/specs/2026-06-23-w2c-deploy-and-de-windows-design.md`、plan `docs/superpowers/plans/2026-06-23-w2c-de-windows-export-and-deploy-prep.md`。

**导出（去 Windows 化，`backend/report_tools.py` 全 Python，无 PowerShell）**：
- `_resolve_pandoc()` 平台守卫：**仅 `sys.frozen` 或 `win32` 才优先包内 `pandoc.exe`**（防 Linux 误 exec 仓库根的 Windows 二进制——`get_base_path()` 开发/服务器态=仓库根），否则 `shutil.which("pandoc")`。**不可放宽**成「非 Windows 也试 .exe」（`test_report_tools.py` source 锁）。
- `export_reviewable_draft(report_path, output_dir)`（**2 参，去掉旧 `script_path`**）原子发布：`mkstemp` 在 output 目录建唯一 temp.docx → pandoc 写 temp → **`os.replace` 到终名**；pandoc 失败/`OSError` 清 temp + 保留旧终名。**全程锁外**（依赖 R3 原子写不变式）。
- 端点 `POST .../export-draft` 改**同步 `def`**（FastAPI 线程池跑、不阻塞事件循环掐 SSE 心跳）、**不取 request lock**。`get_script_path` 已从 `skill.py` 删除（导出唯一消费者）。
- **web 下载契约**：新 `GET .../export-draft/download` `FileResponse`——**确定文件名 `report_draft_v1.docx`**（不接受客户端 filename）+ `Path.resolve()` + `output_dir not in target.parents` 穿越/symlink-file 守卫 + `require_project` 属主隔离（跨租户 404）。前端 `WorkspacePanel.exportDraft` 按 `status!=='ok'` 判失败 showError+return、成功创建同源 `<a>` `.click()` 触发浏览器下载（带 cookie）。

**SSE 防 CF ~100s 切空闲流（`backend/main.py`，两条流都周期心跳）**：
- 审查流（async generator）：`event_queue` timeout 路径按 `SSE_HEARTBEAT_INTERVAL_SECONDS`(20s) 计时发 `: keepalive`。
- 聊天流（sync generator 阻塞在 `handler.chat_stream` 内）：`_sse_with_heartbeat(generate)` **线程+队列多路复用**包装（`generate` 作工厂传入、不是 `generate()`）——专用 `_CHAT_STREAM_EXECUTOR`(8 worker) 跑 pump，主循环空闲>interval 发心跳。**硬约束**：心跳只在 HTTP SSE 帧层注入，**不碰 chat.py 的 provider/tool-call/`reasoning_content`/`tool_choice`/DeepSeek 逻辑、不碰 request lock**；**不加 leading 心跳**（快路径输出与现状字节一致=零回归）。
- **锁释放正确性（必须保）**：pump `finally` 里 `gen.close()` → GeneratorExit 抵达 generator 当前 yield → chat 的 `with request_lock` finally 释放锁。pump **先判 stop_event 再 `next(gen)`**；in-loop 与 DONE 两处 `loop.call_soon_threadsafe` 都 try/except `RuntimeError`（loop 关竞态不漏 `gen.close()`）；pump 异常经 `_log_pump_exception` 记日志（不静默吞）。`gen.close()` 无法中断**已在途**的 provider 调用——锁释放发生在「当前阶段返回之后」，非瞬时（与 Starlette 原生断连一致）。
- **跨任务不变式（保住 R3 用户写 CAS）**：聊天 generator 现跑在 `_CHAT_STREAM_EXECUTOR`，与用户写的 `_USER_WRITE_EXECUTOR` 仍是**不同专用池** → 「用户写 `acquire` 靠 RLock 真阻塞到 chat 释放」的 CAS 防绕过性质不变（甚至更隔离）。

**web 入口（`run_web.py`）**：host/port 读 `CRA_BIND_HOST`(默 127.0.0.1)/`CRA_BIND_PORT`(默 8888)；uvicorn `proxy_headers=True` + `forwarded_allow_ips` 读 `CRA_FORWARDED_ALLOW_IPS`(默 127.0.0.1，nginx 走 ::1/bridge 时须设)；`cookie_secure=True`（`CRA_COOKIE_INSECURE` 本地调试豁免）；**未设 `CRA_ALLOWED_ORIGIN` 会告警**（生产 cookie_secure 态 CSRF fail-closed 403 所有写）。

**N6 F2 收口**：`skill.py` 删 4 个 legacy 解析器（`_legacy_read_document`/`_read_docx`/`_read_xlsx`/`_read_pdf`）；`_converter_read_document` 无 converter → `raise ValueError`（不再静默回退）、converter-present 委派 `convert_document` 并映射 `MaterialConversionError`→`ValueError`（`_execute_tool` 据此回 `{status:error}`）。生产无裸 SkillEngine 路径（`ChatHandler.__init__` 必先 wire converter）。

**Trial accepted-risk / 后置硬化（已与 Codex 议定，非 bug）**：① `_sse_with_heartbeat` eager-drain + 无界队列——慢/半开客户端会缓存整轮输出 + 烧发起者自己配额（有 token cap 上界、单 worker、按用户计费），**锁释放不变差（更早）**；后置硬化=背压保持版（一次一个 in-flight `next()`、await-timeout 心跳、yield 门控下次提交）+ 前端 EOF-without-`[DONE]`=interrupted。② `_CHAT_STREAM_EXECUTOR` 8 worker = 单 worker 上 >8 并发长流会串行化（trial 用户量不触及，记此）。③ `FileResponse` stat→open 与并发 `os.replace` 的 TOCTOU 可能 Content-Length 错配（UI 顺序流不触发、自愈重下；后置硬化=pin fd 流式）。④ symlinked output 目录绕守卫需服务器 FS 访问前提（web 用户不可达）。

**Part C 部署 runbook（✅ 2026-06-23 已部署上线 `https://consulting.z0y0h.work`，8 步 smoke 全过——详见 `docs/current-worklist.md` 顶部 + memory `w2c-deploy-status`）**：kr-web-01（腾讯云首尔，与 jp-app-01 分机）反代+CF（`consulting.z0y0h.work`，**实落=源站自签 15y + CF Page Rule 给该子域单设 SSL=full**[zone 其它子域仍 strict]+橙云；MCP token 签不了 Origin CA 故未用 Origin Cert）+ nginx[SSE 关 buffering + `set_real_ip_from` CF 段 `real_ip_header CF-Connecting-IP`] + systemd **单 worker**（B2/B3 进程内状态）+ env[`CRA_DATA_ROOT=/var/lib/consulting-report`/`CRA_INVITE_CODE`/`CRA_ALLOWED_ORIGIN`/bootstrap admin] + 装 pandoc+libreoffice。**风险**：kr-web-01 是渠道商代购但**在用户自有腾讯云账号内**（非他人账号，2026-06-23 用户更正）——仍是试用机，转正经生产换实例时轮换 `managed_client_token`+搜索池凭据+邀请码+admin 密码。连机经 `VPS-fix-private/.run-remote.py kr-web-01`（root key-auth，`43.131.242.15:2233`）。

**回归**：`tests/test_report_tools.py`(7：resolver 守卫含 frozen 分支/原子发布断言 temp -o 路径) / `test_run_web.py`(source-guard) / `test_main_api.py`(导出端点 sync def 守卫/下载属主·未生成·symlink越界·跨租户404/两流心跳/`_sse_with_heartbeat` 断连 finally) / `test_skill_engine.py`(无 converter raise + `MaterialConversionError`→`ValueError`) / `test_skill_assets.py`(脚本退役 source-guard) + 前端 `workspacePanelExport.source`/`sseHeartbeat`。DeepSeek 兼容 + 跨租户隔离不回归。

## 前端额度实时化 + SPA 缓存头（2026-06-23，Part C 上线后小修）

两笔 Part C 后小修，Codex 审 APPROVED，commit 本地 main（`c30b903` 额度 / `d552579` 缓存，**未 push**），已部署 kr-web-01 并实地验证。

**额度实时化**（`frontend/src/App.jsx` / `Sidebar.jsx` / `api.js`）：sidebar 今日额度原只登录拉一次 `/me`→用掉额度后陈旧（与 admin 面板对不上）。现 `handleProjectMutated`（ChatPanel + WorkspacePanel 共用回调）每轮结束 + `window` focus 调 `refreshAuthQuota` 刷 `/me` 的 cost 字段。**硬约束**：

- `refreshAuthQuota` 三重守卫缺一不可：`quotaRefreshSeqRef` 序号（只让最后发起的回包落地，防同 uid 并发 `/me` 乱序覆盖回旧额度）+ `r.data?.uid === prev.uid`（防在途 `/me` 跨用户串号）+ `axios.get(..., {skipUnauthedHandler:true})`（背景轮询不触发全局 401 登出——`api.js` 拦截器据 `error.config?.skipUnauthedHandler` 跳过 `onUnauthed`，否则旧用户在途 `/me` 返 401 会误踢已登录的新用户）。
- **init effect 依赖必须是 `[authUser?.uid, authUser?.must_change_password]`，绝不能退回整个 `[authUser]`**——否则额度刷新每轮造新 `authUser` 引用 → 重跑 `initializeApp` → `loadProjects` 置 `loading=true` → 命中 `if(loading)` 早返回 → 整树卸载重挂 → 黑屏闪 + ChatPanel 内存里的消息/工具调用记录全丢。`frontend/tests/appInitGating.source.test.mjs` 锁死。
- 进度条由 `quotaRatio` 驱动；`overCap`（`cap<=0` 含 admin 设 0 封禁，或 `used>=cap`）渲染红 100%。回归 `sidebarQuota`/`appInitGating`/`apiUnauthed` source-guard。

**SPA 缓存头**（`backend/main.py:_SPAStaticFiles`）：`StaticFiles` 默认只发 ETag/Last-Modified、**不发 Cache-Control** → 浏览器对 index.html 启发式缓存 → 部署原子 swap（`mv dist dist.old && mv dist.new dist`）删旧 hash bundle → 陈旧 shell 指向已删 bundle 返 404 → React 没挂载 → 空 `#root` 满屏深色空白页（控制台静默 404、UI 无报错）。**硬约束**：

- SPA shell（根目录请求 / 显式 `*.html`）→ `no-cache, must-revalidate`；`assets/*`（带内容 hash）→ `immutable`。**别退回裸 `StaticFiles`**（重现空白页 bug）。
- 缓存头**按规范化路径判定**（`_cache_control_for`），不仅看 content-type——因为条件请求命中时 `NotModifiedResponse`(304) 不带 content-type，只按 content-type 判会漏掉 304、旧缓存学不到 no-cache。`tests/test_static_cache_headers.py`(6，含 304 revalidation 用例) 锁死。
- nginx 给 `.js` 资源另发 `expires`（盖掉 immutable 成 `max-age=14400`），无害（资源带 hash）；nginx gzip 会剥 ETag，shell revalidation 走 Last-Modified（`If-Modified-Since`→304 仍带 no-cache，已实地验证）。
- **⚠️ 归因更正**：上面这条 SPA 缓存修复是真实的潜在隐患修复，但 **不是**用户当时报的「登录后空白页」真因——真因是下面的「登录页 422 白屏」。当时误判成缓存，被无痕窗口仍白屏否证、nginx 日志的 422 锁定真因。

**登录页 422 白屏修复**（`frontend/src/components/Login.jsx` / `utils/authError.js` / `components/ErrorBoundary.jsx` / `main.jsx`，commit `3bff742`）：短用户名(<3)/短密码(<6) → 后端 `LoginPayload`/`RegisterPayload` 的 Pydantic `min_length` 校验失败返 **422**，其 `detail` 是**数组** `[{loc,msg,type}]`（非字符串）；旧 `Login.jsx` `setErr(detail)` → 渲染 `{err}` 触发 React **"Objects are not valid as a React child"** → 登录页（App 早返回分支、**不在** App 内层 ErrorBoundary 里）整树卸载 → 空 `#root` 满屏深色白屏（UI 无报错）。**硬约束**：

- 任何把后端错误 `detail` 显示给用户的地方，**必须经 `normalizeAuthError`（或同类归一）** 把 string/数组/对象都转成字符串——**绝不把后端 `detail` 直接塞进 React 子节点**（422 是数组、会白屏）。
- 整个 `<App/>` 由 `main.jsx` 的 `ErrorBoundary` 包裹（共享组件，从 App.jsx 抽出）——App 的早返回分支（Login/ForcePasswordChange/loading）**不在** App 内层 ErrorBoundary 里，没这层外包则它们渲染崩溃=整树白屏。**别移除外层 ErrorBoundary**。
- `Login.jsx` 提交前客户端校验长度（对齐后端 `min_length`：用户名≥3、密码≥6）+ 提交 **trim 后的用户名**（密码不 trim）。`frontend/tests/authError.test.mjs` + `loginErrorHandling.source.test.mjs` 锁死。
- **已知 follow-up（非阻塞，已记 worklist）**：`IndependentReviewDrawer.jsx` 有同类「把 detail 直接进渲染态」写法——当前不可达（审查端点手解析返回字符串 detail）且已被新全局 ErrorBoundary 兜底；彻底治理可抽共享 `normalizeApiErrorDetail`。

## 中右分栏拖动 + 输入框乐观清空（2026-06-25）

两个纯前端 UX 修复（后端/DeepSeek/信任边界/租户隔离零改动）。改主布局或聊天发送逻辑前必读。

**输入框乐观清空**（`ChatPanel.jsx`）：原 `setInput('')` 在 `startStream` 末尾（流式整轮结束才清），消息发出后滞留输入框直到回答完。现 `sendMessage` 点发送即 `setInput('')`（chatbox 风格），`startStream` 成功分支**不再** `setInput`。失败/上传失败/中止经 `restoreInputForRetry()` **双重守卫**回填原文，缺一不可：① `sendSeqRef.current !== sendSeq`（其间发起了更新的发送）则不回填——防旧的被中止发送盖回已被下一条发送清空的输入框；② `setInput(prev => prev === '' ? trimmedInput : prev)`——仅输入框仍空才回填，防点「停止」提前解锁输入框后覆盖用户新打的字。**别改回无守卫的 `setInput(trimmedInput)`**（codex 红队两轮挖出的 abort race）。锁测 `frontend/tests/chatPanelComposerClear.source.test.mjs`。

**中右分栏可拖动**（`App.jsx` + `WorkspacePanel.jsx` + `utils/workspaceResize.js`）：`WorkspacePanel` 根 div 从写死 `w-[28rem]` 改为接 `width` prop（`style={{ width }}` + `flex-shrink-0`）；`App.jsx` 持 `workspaceWidth` state + 竖向拖动条（`cursor-col-resize`，`role="separator"`，沿用 `FilePreviewPanel` 上下拖动的 window 监听 + cleanup ref 模式）+ localStorage(`cra:workspaceWidth`) 持久化。宽度数学抽纯函数 `workspaceResize.js`（`clampWorkspaceWidth`/`computeWorkspaceWidth`/`parseStoredWorkspaceWidth`）。**关键不变式（codex 双轨 + 2 轮红队的 5 BLOCKER）**：
- 容器 ref 必须挂在**排除固定宽 Sidebar 的内层 wrapper**（`<div ref={setContainerRef} className="flex flex-1 min-w-0">` 内含 ChatPanel + 手柄 + WorkspacePanel）。clamp 按这个可调区域预留 `MIN_CHAT_WIDTH`——**绝不**把整窗宽（含 Sidebar）算进去，否则聊天区被挤到 ~100px。
- 存储宽度经 **callback ref `setContainerRef`** 在容器挂载（登录后）时按真实 `getBoundingClientRect().width` **重夹一次**（防存的宽超出当前窗口、启动就挤没聊天区）；window `resize` 另有 effect 持续重夹。
- `clampWorkspaceWidth` 容器窄于 `MIN_CHAT_WIDTH` 时 floor 0（不返回负宽）；`parseStoredWorkspaceWidth` 显式判 null/""（`Number(null)===0` 会被误夹到 MIN）。
- 中间列 `ChatPanel`(flex-1) + 其输入框/上下文用量框靠 flexbox 自动重排，**无需手动同步宽度**。
- 回归：`frontend/tests/workspaceResize.test.mjs`（纯函数）+ `workspaceResize.source.test.mjs`（接线/容器排除 Sidebar/callback-ref 重夹）。

**部署（前端 only 的通用流程）**：`dist` gitignore、服务器不 build → 本地 `npm run build` → tar → `VPS-fix-private/.push-file.py kr-web-01` 推 → 服务器**在真实运行目录 `/opt/consulting-report-agent/frontend/`**（⚠️ 认 `systemctl show consulting-report -p WorkingDirectory`，**别用 `find /opt -name dist` 撞到的残留目录 `/opt/consulting-report/`——曾把 favicon 部署整轮打偏、假性 200 真 404**）解到 `dist.new` + `chmod -R a+rX` + 原子 `mv dist dist.old && mv dist.new dist`，**无须重启 systemd**（`_SPAStaticFiles` 按请求读盘、SPA shell no-cache 用户免硬刷），`dist.old` 留回滚。本次 bundle `index-D9CspGtr.js`。

## 前端 UX 翻新：海军蓝双主题设计系统（2026-06-26 实施完成 + 每批及整分支 Codex 双轨审全 APPROVED + merge main `d794ae0`（--no-ff）+ 部署 kr-web-01；分支 `feat/frontend-redesign` 保留）

把 web 前端从「深紫黑 `#0f0f23` + 薄荷绿 `#64ffda` + emoji」翻新为「海军蓝 `#1B2A4A` accent + 浅/深双主题 + 线性 SVG 图标 + 自托管字体」。**纯前端、零后端改动、业务逻辑只换皮、功能零退化**。改前端配色 / 主题 / 图标 / 字体前必读。spec `docs/superpowers/specs/2026-06-25-frontend-redesign-design.md`、plan `docs/superpowers/plans/2026-06-25-frontend-redesign.md`（含「## 实施期范围增补」）。

**Token 单一真值源（别绕开）**：`frontend/src/index.css` 的 `:root`（浅）/`.dark`（深）定义**通道形式** CSS 变量 `--x: R G B`（如 `--accent: 27 42 74`）；`frontend/tailwind.config.js` 用 `c(v)=\`rgb(var(${v}) / <alpha-value>)\`` 映射成语义类（`bg-* text-* border-*` 等，全集见 config `colors`：bg/chat/ws/card/card2/field/border/col/hair/track/text/t2/t3/accent/abright/asoft·b·t/sel/userbub/stepdone/dotfuture/scrim/success/warn/error）+ radius(chip/tag/ibtn/btn/card/win) + shadow(card/popover/float) + fontSize(2xs/11/xs/12/13/sm/15/base/lg/xl)。**`<alpha-value>` 形式是硬约束**——让 Tailwind 透明度修饰符（`bg-accent/90`）生效；新增 token 必须走 `c()`、不写死 rgb。

**主题切换**：`<html>.dark` class（`darkMode:'class'`）+ localStorage `cra:theme` + `index.html` head **同步 bootstrap** 防 FOUC（只 `==='dark'` 加 `.dark`、catch 里也 remove 兜底默认浅）。`utils/theme.js`（`getInitialTheme/applyTheme/toggleTheme`），`App.jsx` theme state + **独立** effect `applyTheme`（**别并进 init effect**——会每轮重挂黑屏，同既有 `[uid,must_change_password]` 雷区）。**深色一律靠 token 自动切，源码里禁用 `dark:` 前缀，唯一例外 `dark:bg-scrim/N`（弹窗遮罩）**。

**自托管字体**：`src/assets/fonts/*.woff2`（Hanken Grotesk 400/500/600/700 + IBM Plex Mono 400/500，走 Vite hash 管线），`index.css` `@font-face` 用 `./assets/fonts/` 相对路径；中文走系统栈（PingFang SC / Microsoft YaHei）。线性 SVG 图标全在 `src/components/icons.jsx`（`currentColor` 自动随主题）。

**护栏（迁移完整性 = 这些测试，改前端必须保绿）**：`frontend/tests/` 下 `paletteGuard`（**ALLOW_PENDING 已空**=全量迁移；扫到裸 hex / `bg-[#..]` 任意色 / emoji 即失败——**新组件别引入**）、`tokenContract`（每 token 经 `c()` + `<alpha-value>`）、`darkClassGuard`（只许 `dark:bg-scrim/N`）、`themeBootstrap`、`theme.test`。前端 **414 测试**（Node `node:test`）。

**用户批准的范围增补**（非纯换皮，已记 plan）：① 左侧栏可收起/展开（`App.jsx` `showSidebar` + `cra:showSidebar` 持久化 + 聊天 header 内开关、与右侧工作区开关镜像图标 `IconSidebar`↔`IconPanelRight`）；② 材料 tab 直接上传（`WorkspacePanel` 复用聊天回形针同一 `/materials/upload`；忙态按 projectId 作用域 + project-switch/unmount/StrictMode 守卫）。

**复审挖出并修的真坑（改这些组件当心回归）**：材料 tab 原显示**绝对 workspace_dir 路径**——desktop 无害但 **web 部署会泄露 VPS 文件路径，已删**（产品/安全决策，覆盖早期「功能全保」复审结论）；上传忙态守卫的 `mountedRef` **必须 setup 置 true + cleanup 置 false**（只 cleanup 会被 StrictMode 重放永久 false → 上传静默失效）；`activeProjectRef` **渲染期赋值**（被动 useEffect 留切项目窗口）；StagePanel stepper 用**本地 stage 列表索引**算填充（用全局 STAGE_ORDER 会让 report-only S7 算出 116% 溢出）；S2/S3 进度条左标签是「有效来源/证据引用」**不是「正文字数」**；任何把后端 `detail` 显示给用户的地方走 `utils/authError.js:normalizeApiErrorDetail`（422 是数组、直接渲染会崩）。

**未做（follow-up，已记 `docs/current-worklist.md`）**：工具调用卡片重设计 + 去 emoji —— **plan 定稿 Codex APPROVED（4 轮对抗红队）** `docs/superpowers/plans/2026-06-26-tool-call-pill-redesign.md`（v4，9 task subagent-driven，commit `7b94ea7` 本地 main 未 push，**待新会话实施**）。最终方案：后端正常 call/result 发结构化 `tool_call`/`tool_result`（带 id，审查也发 id）；**reload 走 `conversation.json` assistant 的 `tool_events` 并列字段**（`_build_tool_events` 写 + `_load_conversation` 显式保留 + `GET /conversation` 直返；`_to_provider_message`:4036 只回 `{role,content}` 不泄漏 provider）——**不嵌 HTML 注释**（避 `-->`/`]` 截断）、**不动 `_format_tool_pair_line`/tool-log 注释 / provider 序列化**；前端**无文本解析、无 emoji 字符**，`reduceToolEvent`→`msg.toolEvents`→共享 `ToolCallPill`（单行 + 摘要 click-to-expand）成组渲染正文上方（`ToolCallList`），主聊天与审查统一；诊断 `type:"tool"` 保持现状（legacy 分支不删）；`_sanitize_message_for_summary` pop tool_events。非目标：老对话 reload 不显 pill（无字段→[]、无回归）。

## redesign 三处原型差距 follow-up（2026-06-26 实施 + Codex 双轨+对抗红队全 APPROVED + 前后端部署 kr-web-01；分支 `feat/frontend-redesign-followups` commit `8e14eab`，待 merge main + push）

翻新后用户对照原型 `design_handoff_frontend_redesign/` 发现 3 处没改完，本批补齐。改这三处前必读：

- **侧栏副标题「报告类型 · 阶段名」**（`Sidebar.jsx`）：阶段名走单一真值源 `utils/workspaceSummary.js:getStageName`（别在 Sidebar 硬编中文）。`const stageCode = (isActive && currentStageCode) || project.stage_code`——活动项目用实时 workspace stage、其余用 list 的 stage_code、都缺只显示类型。**后端 `skill.py:list_projects` 加 `stage_code`**（与 `get_workspace_summary` 同源 `_infer_stage_state`[只读链路]；advisory：单项目目录损坏 `try/except` 降级 None、绝不让列表端点 500；前端仅 init/新建/删除拉 `/api/projects`、无逐轮轮询，per-project 推断成本可接受）。`App.jsx` 传 `currentStageCode={workspaceProjectId === currentProjectId ? workspace?.stage_code : undefined}`——**`workspaceProjectId` 守卫不可去**（切项目时 workspace 短暂仍持旧项目数据，无守卫旧阶段会瞬时覆盖新项目副标题，codex 红队真 bug）；所有 `setWorkspace(null)` 处同步 `setWorkspaceProjectId(null)` 保不变量。
- **用户管理表格**（`AdminPanel.jsx`；**2026-07-06 弹窗已删、整表迁入 `/admin` 独立页 `AdminPage.jsx`，下述约束原样带走**）：`<table>`→5 列 grid `grid-cols-[1.6fr_1fr_1fr_1fr_1.3fr]`，表头 `bg-card2`/`text-11`/`font-semibold`，状态色标（正常 `text-success`/已禁用 `text-warn`，对应原型 `--ok #34A853`/`--warn #B7791F`），操作列 `text-right`，今日/额度 `font-mono tabular-nums`。**额度列保留可编辑 `input`+`onBlur setCap`**（用户硬要求；原型那列是只读文本，不可改回只读）。grid 补 ARIA `role=table/row/columnheader/cell` + input `aria-label`。
- **新建报告弹窗**（`ProjectCreateModal.jsx`）：补标题「新建报告」+ 副标题「填写基本信息，助手会据此开始准备阶段。」+ 四字段全 `<label htmlFor>`+控件 `id`（原缺标题/副标题/前两项 label）。

回归：`tests/test_skill_engine.py`（list_projects stage_code + 损坏降级）、前端 `sidebar.source`（副标题 wiring + admin grid/色标/可编辑额度）+ `appInitGating.source`（`workspaceProjectId` 守卫）；前端 418 测试 + build 绿（后端 2 个 mac realpath 失败属环境差异）。**部署**：前端 dist（bundle `index-C7_xlMbU.js`）+ 后端 file-push `backend/skill.py` + 重启 systemd（kr-web-01 **首次后端 redeploy**——前几次都是 frontend-only dist swap）。详见 `docs/current-worklist.md` + memory [[frontend-redesign-status]] / [[w2c-deploy-status]]。

## 工具调用卡片 pill + 时间线穿插（2026-06-27 实施完成 + 每 task Codex 双轨审 + 整分支红队 SHIP + merge main `fff39ca`）

工具调用从「两 pill/工具的 emoji 日志」重做为「结构化 pill + 文本/工具按时间线穿插」。改工具渲染 / SSE 工具事件 / conversation 持久化前必读。spec/plan：`docs/superpowers/plans/2026-06-26-tool-call-pill-redesign.md`（pill）+ `docs/superpowers/plans/2026-06-27-tool-call-interleaved-parts.md`（穿插）。

**① pill 数据流（结构化 SSE + 持久化 sibling）**：
- 后端正常工具 call/result 发结构化 SSE `tool_call`/`tool_result`（带 `id` 配对，**独立审查也发 id**）；arg/summary 派生 `_sse_tool_arg`/`_sse_tool_summary`（pill 与持久化共用）。
- 持久化 `tool_events` 并列字段进 `conversation.json`（`_build_tool_events` 写 / `_load_conversation` 白名单重建**显式保留 + 净化**[pending→终态防永久转圈] / `GET /conversation` 端点净化返回）。**白名单重建必须显式保留，否则下轮 re-save 抹掉**（sibling 字段铁律）。
- 诊断类 `type:"tool"` 文本事件保持现状（legacy 分支不删）。

**② 时间线穿插（有序 parts · per-event 镜像）**：assistant 一轮建模成有序片段 `parts = [{type:"text",text} | {type:"tool",id,tool,arg,status,summary}]`，**工具调用切分文本段**，文本/工具按到达顺序交错（取代旧「工具堆顶、文本堆底」）。
- **后端 `_build_message_parts(current_turn_messages, assistant_message)`**：单遍 pending→pop 配对（**与 `_pair_tool_calls_with_results` 同语义**：按 `tool_call_id`、缺 id 跳过、orphan result 跳过、重复 id deque FIFO；未应答 call 保留 `status="error"` pill）；末轮文本段用**干净 `visible_content`（不含 tool-log）非 `persisted_content`**；跳已知合成隔板 `_SYNTHETIC_BARRIER_NOTES`（逐字对齐注入处，守护测试防漂移）。`_finalize_assistant_turn` 旁加 `parts` 持久化（gate 在 `current_turn_messages`，content/tool_events/补尾零改动）、`_load_conversation` 保留+净化（`_sanitize_part_scalar`：scalar-only、pending→success、空白 text 段丢、全空不留 `parts:[]`）、`_sanitize_message_for_summary` pop parts、`GET /conversation` 端点复用 `_sanitize_part_scalar` 净化。
- **前端纯函数 `utils/messageParts.js`**（不可变）：`mutateCurrentTextPart(parts,fn)`（末段 text 续接否则新建）/ `applyToolEventToParts(parts,event)`（按 id；tool_call 早发 pending→full-arg 原地更新、**tool 名非空覆盖 `||`、arg `??`**）/ `closePendingToolParts` / `appendErrorPart` / `partsToText`。
- **`ChatPanel.jsx` 每个写 `msg.content` 的 SSE handler 旁建 `msg.parts`**（**content 装配逐字零改动、只旁加 `, parts:...`**）：两 flush 点（`flushStreamingQueueImmediately` 用 `pending` / timer slice 用 `emitted`，**tool_call 前 flush 必同步建 parts 否则工具插文本前**）+ thinking（`mutateCurrentTextPart(p, t=>appendThinkingEventContent(t,delta))` 并块非 append）+ 诊断 `appendToolEventContent` + tool_call/result（`applyToolEventToParts(m.parts, parsed)`，parsed 字段直配）+ error/network/abort（`closePendingToolParts(appendErrorPart(m.parts, displayText), pendingSummary)`，displayText 用真实文案 `错误: ${data}`/`API调用失败: ${msg}`/abort 仅无可见文本才 `已停止生成`）。**有意差异**：error/network 路径 content 替换成纯错误（保 provider/compaction 旧语义），parts 追加错误段保留已流式叙述（parts 是渲染主源、时间线更忠实）。
- **渲染**：抽 `components/assistantTextRender.jsx:renderAssistantText(text)`（= 原内联 `stripToolLogComments`→`splitAssistantMessageBlocks`→逐 `block.content`：thinking→`ThinkingBlock`、text→`ReactMarkdown remarkGfm assistantMarkdownComponents`，**用 `block.content` 非 `.text`**）；`components/MessageParts.jsx` 按序 text→`renderAssistantText(p.text)` / tool→`ToolCallPill event={p}`（key 索引前缀防重复 id 碰撞）；ChatPanel 渲染分支 `msg.parts?.length ? <MessageParts/> : (<><ToolCallList/>{renderAssistantText(msg.content)}</>)`（**不双渲染工具**；老消息无 parts 回退现状）。复制走 `partsToText(parts)` 经现有 `getCopyableAssistantMessageText` strip。

**硬约束（跨 ① ②）**：`parts`/`tool_events` **绝不进 provider message**（`_to_provider_message` 只回 `{role,content}` 天然丢之）、不碰 `reasoning_content`/`tool_choice`/tool-call 序列化/`persisted_content`/`already_emitted_len`/补尾切片/DeepSeek 官渠兼容；`_sanitize_message_for_summary`（压缩信任边界）pop 两字段；`parts` 纯文本渲染无 `dangerouslySetInnerHTML`；多租户 `GET /conversation` 经 `require_project`。**已知边界**：reload 不还原 live-only thinking/诊断（不持久化=现状）；老对话（改动前）无字段→回退现状不显 pill（无回归）。

**回归**：后端 `tests/test_chat_runtime.py`（`_build_message_parts`/persist/load/summary-pop/per-event）、`tests/test_main_api.py`（`GET /conversation` parts 净化/scalar-only/pending→终态）；前端 `messageParts.test.mjs` / `chatPanelParts.source` / `assistantTextRender.source` / `messageParts.render.source` / `chatPanelSseRouting` / `toolCallPill.source` / `toolEvents.test`。后端 1488 / 前端 459 / build / DeepSeek / 禁改区 全绿。详见 memory [[tool-call-pill-status]] + `docs/current-worklist.md`。

## 聊天框材料区精简（2026-06-29，纯前端 + 已部署 kr-web-01 bundle `index-BpQ_ae3i.js`）

用户反馈"上传后输入框上方常驻铺列全部材料"占地方、像冗余。本批把材料管理收敛到右侧「材料」标签，输入框上方平时只剩输入框本身。**纯前端、后端协议零改动、DeepSeek/信任边界/租户隔离零改动。** Codex 双轨复审（初审 + NIT 修复终核）APPROVED 无 BLOCKER。改聊天框附件/材料 UI 前必读：

- **删掉 `ChatPanel.jsx` 输入框上方 block2（已勾选材料）+ block3（全部项目材料铺列 + 手动挂载 toggle）**。`待发送附件`（pendingAttachments，新拖入未发送）保留。
- **解析状态（已解析/未解析/失败）迁到右侧「材料」标签**（`WorkspacePanel.jsx` 每行 + `conversionStatusChip` 三 tone），是材料解析状态**唯一**前端显示处。`conversionStatusChip` import 从 ChatPanel 移到 WorkspacePanel。
- **为什么砍"手动把旧材料/旧图片重新挂这一轮"**（验证后的取舍，非纯删功能）：① `read_material_file` 读成功后正文按 `material:{id}` 存进 `conversation_state.json` 工作记忆、每轮注回（见「## 管理型搜索池」上方 N6 + worklist 顶「工作记忆旁路」实证）；② 默认 managed 模型 `deepseek-v4-pro` 是纯文本（不在 `MULTIMODAL_MODEL_MARKERS`），图片走转写、读过同样留；③ 模型每轮已从 `build_project_context` 的「## 可用项目材料」拿到全部材料 id。故"重新挂"对默认配置零增量。**后端 `_build_user_content` 的 image_url/transcript 能力与其测试（`test_chat_runtime.py:203/212`）保留不动**，只是前端不再给入口（custom + 真多模态模型想重看原图像素是边角，重新上传即可）。
- **`selectedMaterialIds` 不变量**（删 UI 后唯一写者是上传自动挂载）：①写入只剩 `sendMessage` 上传文档后的 `mergeMaterialIds(selectedMaterialIds, uploadedMaterials)`（无手动 UI）；② **turn-end 成功/失败都清空** `setSelectedMaterialIds([])`（合并原成功/失败两分支），准备期 catch 也清——材料选择已无可见 UI，留着会变成"看不见的已挂材料"悄悄带到下一条无关消息（codex 两轮点名）；③ **待发送附件队列仍"失败保留/成功清空"**（`if (!streamFailed) clearPendingAttachmentQueue()`，图片靠 pending 经 transient 重发，语义同改前）。
- `toggleMaterialSelection`（`utils/chatMaterials.js`）现为**无生产引用的死 util**，有意保留（带测试纯函数、零成本、后续若加"引用材料"选择器可复用）。`materials` prop 仍被 ChatPanel 历史气泡用于反查材料名，**不可删**。
- 回归：`chatPanelAttachmentStatus.source`（改为守"ChatPanel 不再常驻材料列表 `doesNotMatch conversionStatusChip/toggleMaterialSelection/selectedMaterials.map`" + "上传自动挂载不变量 `mergeMaterialIds`/`attachedMaterialIds`"）、`workspacePanel.source`（解析状态 chip 迁入守护）、`chatPanelComposerClear.source`（turn-end 清理块锚点改 `if (isActiveProjectRequest(requestProjectId)) {` 之后的 `if (renderUserBubble)`，断言含 `setSelectedMaterialIds([])` 不含 `setInput`）。前端 460 测试 + build 全绿。

## 移动端适配（drawer 壳，2026-06-30 实施完成 + merge main `011ce2b` + 滑动手势 follow-up merge `a0f88e9` + 部署 kr-web-01 bundle `index-HsuR1V2M.js`；分支 `feat/mobile-web-adaptation` / `fix/mobile-drawer-swipe-close` 保留）

给 web 前端加移动端抽屉壳：**触摸设备（`pointer: coarse`）启用、鼠标设备永远走原桌面三栏、桌面零变化**。纯前端、零后端/DeepSeek/信任边界/租户隔离改动。11 TDD task，每 task Codex spec+quality 双轨独立审到 APPROVED + 整分支红队终审 SHIP-READY + 真实浏览器设备模拟 smoke 过。改移动端布局 / 设备判定 / 抽屉前必读。spec `docs/superpowers/specs/2026-06-30-mobile-web-adaptation-design.md`、plan `docs/superpowers/plans/2026-06-30-mobile-web-adaptation.md`。

- **触发按设备非按宽度**：`utils/deviceMode.js:isCoarsePointer()`（`matchMedia('(pointer: coarse)')`，缺失/抛错 fallback 桌面）；App `const [isMobile] = useState(() => isCoarsePointer())` **首屏锁定、无 matchMedia 监听**——桌面缩窗/分屏永不变成移动壳，也避免运行时切壳卸载子树丢状态。**别加 resize/宽度逻辑、别退回带 listener 的写法。**
- **方案 = 抽屉壳**：`components/MobileShell.jsx` 复用 `ChatPanel`/`Sidebar`/`WorkspacePanel` 只换外壳：聊天占满 + 复用 ChatPanel 自带 60px 顶栏（`onToggleSidebar`/`onToggleWorkspacePanel` 接抽屉、**不新增顶栏**）+ 左抽屉 Sidebar / 右抽屉 WorkspacePanel。App `isMobile ? <MobileShell/> : (原桌面三栏 JSX 逐字不动)`；AdminPanel 上提为两壳兄弟（fixed overlay，移出移动抽屉子树；**2026-07-06 弹窗整个退役——admin 改 `/admin` 独立页新标签打开，App 无 showAdmin/AdminPanel**）。**`App.jsx` init effect 依赖仍是 `[authUser?.uid, authUser?.must_change_password]`，本特性没碰 effect——别退回 `[authUser]`（黑屏重挂雷区）。**
- **硬约束①：抽屉滑动禁 CSS 变换**（`transform`/`translate`/`scale`/`filter`/`blur`/`perspective`）——它们生成 containing block 会破坏内部 `fixed` 弹窗（审查窗）。用 off-canvas `left/right`（`-110%`）+ `visibility` 滑动；审查窗（`IndependentReviewDrawer` isMobile 时）`createPortal(windowEl, document.body)` 脱离抽屉子树满屏。`mobileShell.source.test.mjs` 有 className-only 扫描 guard 钉死「无变换类」；smoke 现场坐实壳子树 `anyTransformInShell: []`。
- **硬约束②：抽屉常驻挂载**（关闭不卸载，只 off-canvas + `visibility:hidden`）——保进行中的材料上传 / 审查 stream 存活。锁测用「wrapper `>` 后必须直接跟 `<Sidebar>`/`<WorkspacePanel>`」的**正向邻接断言**（防 `{cond && <Panel/>}` 条件卸载，含多行 `&& (` 与三元）。右抽屉 wrapper 须显式 `w-[min(28rem,calc(100vw-48px))] h-full flex` 给 `width="100%"` 基准——**48px 是刻意留的 scrim 缝隙**（原 `w-[min(100vw,28rem)]` 手机上取 100vw＝满屏、盖住 scrim+顶栏按钮 → 工作区关不掉只能刷新，2026-06-30 真机暴露后修；**别回满屏**）。
- **抽屉开关 = 顶栏按钮 + 点 scrim + 滑动手势**（任一）。滑动判定纯函数 `utils/drawerSwipe.js:resolveSwipeAction(dx,dy,anyDrawerOpen,threshold=60)`（**`Number.isFinite` fail-closed** + 主轴 `ax>ay` 严格；开着抽屉任一明确水平滑→`close`，没开→右滑 `openLeft`/左滑 `openRight`）；`MobileShell` 根 div 绑 `onTouchStart/onTouchEnd/onTouchCancel`——**`touchstart` 用 `changedTouches[0]` 记本次新增手指 + 按 `identifier` 配对、`touchend` 找不到同指就 `return`（不 fallback `list[0]`，防多指起终点错配）**；`touchstart` 落在 `input/textarea/select/button/a/[contenteditable]/.overflow-x-auto` 上**不接管手势**（防选字/横滚/点按误触发）；**不 `preventDefault`**（保纵向滚动）、**不做 follow-finger / 零 transform**（只改 drawer state，靠现有 off-canvas transition 动）。Codex 双轨 3 轮（fail-closed / target-filter / 多指错配）APPROVED。已知边界：贴屏幕**最边缘**起手会被手机浏览器抢去当后退/前进 → 开抽屉从中间滑最灵、按钮兜底。回归 `tests/drawerSwipe.test.mjs` + `mobileShell.source.test.mjs`。
- **硬约束③：动作后关右抽屉**——审查汇报（`handleTriggerSystemTurn` 调 `chatPanelRef.current?.triggerSystemTurn` 后 closeAll）、`onInsertPrompt`（继续扩写/回退）、设置保存（`onSettingsSaved` 包装 closeAll）都 closeAll，否则动作在抽屉背后像没反应。**`onToggleTheme` 刻意裸透传不 closeAll**（就地切主题、不打断）；**删除项目刻意不 closeAll**（删完顺手挑下一个）；create **成功才** closeAll。审查汇报 ref 链在 MobileShell 复刻（`chatPanelRef`）。
- **硬约束④：移动端文件预览只读**——`FilePreviewPanel` isMobile 时 `handleEnterEdit` 首行早返回 + 编辑按钮/拖动分隔条 `!isMobile` 门控（`useCallback` deps 含 isMobile）。AdminPage（`/admin` 独立页，原 AdminPanel）额度列**仍可编辑**（与上不同，别误改成只读）。
- **硬约束⑤：移动 viewport**——根 `100dvh` + composer `safe-area-inset-bottom` + `min-h-0`（软键盘）。`MobileShell.jsx` 注释**禁 emoji/符号**（`paletteGuard` 扫 `.jsx` emoji 区，曾因 ☰/▣ 自炸）；scrim 是唯一 `dark:bg-scrim/N` 例外。
- **Auth/Modal 窄屏**：Login/ForcePasswordChange/ProjectCreateModal/SettingsModal/AdminPanel/Sidebar 删除确认都从写死宽度改 `w-[min(Npx,calc(100vw-32px))]`（桌面 ≥N+32px 取固定值＝零变化）；ProjectCreate/Settings 双列 `grid-cols-1 min-[480px]:grid-cols-2`；用户表外层 `overflow-x-auto` + 内层 **`min-w-[600px]`**（2026-07-06 起该表在 `/admin` 独立页 `AdminPage.jsx`，约束不变；原「600 < 680 弹窗内容区」的桌面横滚考量随弹窗退役，min-w 仍是窄屏横滚基准）。
- 测试全是 source-guard + 纯函数（**无 jsdom**）：`tests/deviceMode.test.mjs`、`mobileShell.source.test.mjs`、`mobileAuthModals.source.test.mjs` + `appInitGating`/`workspacePanel`/`filePreviewPanel`/`independentReviewDrawer` 追加。前端 488/488。强 guard 铁律：source-guard 须自验「改坏门控→FAIL」（本批多次挖出 `[\s\S]*?` 跨标签/跨结构假阳性，已全改成 tag-bounded / 结构锚定）。**已知未自动覆盖**（smoke 时人工 DOM/CSS 核）：窄视口 modal 收缩、审查窗 portal 真触发、真触屏 pointer 判定。
- 部署：frontend-only dist swap → kr-web-01（见 [[w2c-deploy-status]]）。详见 memory [[mobile-web-adaptation-status]]。

## 试用反馈两修复 + 模型调用重试 + /admin 独立管理页（2026-07-06）

四件套（commit `2bfa2ec` 本地 main；Codex 单轮终审 + 对抗红队 APPROVED；**已部署 kr-web-01**：dist swap bundle `index-D1efA8fM.js` + file-push 5 个后端文件 + systemd 重启，公网 smoke 8/8 过；回滚点 = 服务器 `/opt/cra-rollback-20260706/` + `frontend/dist.old`；✅ 已 push origin 且服务器已 `git reset --hard origin/main` realign 到 `7c7e4a4`、运行文件 sha 与提交一致）（反馈①阶段按钮代发自愈 / 反馈②内部提示进后台 / provider 瞬态重试 / admin 独立页面），改阶段按钮 / system_notice / provider 调用错误路径 / admin 面板前必读：

- **S1/S7 阶段按钮 = 代发消息走主模型**（反馈①）：`ChatPanel` imperative handle 暴露 `sendUserMessage(text)`（忙时返回 false，**不静默排队**），App/MobileShell 以 `onSendPrompt` 传链 WorkspacePanel→StagePanel→`StageAdvanceControl.sendConfirmMessage`。S1/S7 **不再直连** `POST /checkpoints/*`（无模型在环撞门禁即 400 死路）；**S4/S5 保持直连**（内容阈值 / 独立审查报告，代发救不了）；S6 演示功能未做不动。移动端代发成功后 closeAll 关右抽屉。锁测 `stageAdvanceControl.test.mjs` + `independentReviewDrawer.source.test.mjs`（handle 形状）。
- **内部提示全走后台日志**（反馈②）：write-gate 类 `system_notice` 一律 `surface_to_user=False`（`_yield_user_visible_notices` 自动打 `[internal-notice]` 日志）——**新增门禁 notice 别再开 True**，用户对「请调用 advance_stage」无从操作；模型自我修正旁白（畸形 tool_calls/自我循环/声称写入未写/汇报轮禁工具）**不 yield `type:"tool"`**、打 `[self-heal]` 日志；`type:"error"` 硬错误保留给用户。重试耗尽兜底文案（`_build_required_write_failure_message`）是用户可见文案，**写人话、禁工具名/路径**。前端橙框渲染机制保留（未来真需用户动手的通知可用）。
- **provider 瞬态重试**：叶子模块 `backend/provider_retry.py`（**只依赖 stdlib**，chat/independent_review 共享；分类 = 无状态码网络错误全瞬态 + `TRANSIENT_STATUS_CODES`，4xx 确定性错误不重试；退避 2/4/8s 封顶）。chat.py 流式与非流式 create 各 3 次尝试（重构为显式 while + 计数——**旧 `for retry in range(2)` 有「usage 参数回退在末次尝试触发→复用陈旧 response」潜伏 bug，别改回**）；**流中断重发仅当 `iteration_visible_output=False`**（正文/思考/工具 pill 任一已 yield 即不重发，防气泡内容重复），per-turn 预算 `STREAM_MAX_RETRIES`；重试状态行用 `type:"tool"` 透给用户（「连接不稳定，正在自动重试…」——这是反馈②后该通道唯一的后端产出）。计费不变式：`continue` 前必经 `finally: response.close()` 恰好结算一次。Quota/Paused 异常在分类器之前截获、绝不重试。回归 `tests/test_provider_retry.py` + `ProviderRetryStreamTests`。**测试注意**：mock create 失败路径须 `mock.patch("backend.chat.time.sleep")` + 每次 create 给**新**的坏流（复用已耗尽生成器会被当空流正常收尾）。
- **/admin 独立管理页**：`main.jsx` 按 `pathname` 正则 `/^\/admin\/?$/` 分流渲染 `AdminPage`（不引路由库）；后端 `_SPAStaticFiles._SPA_FALLBACK_ROUTES` **白名单**回退（404 → 直接回 `index.html` 文件——**别用 `"."` 目录形态**，会触发 StaticFiles 307 目录重定向多一跳；**别把白名单改成「所有 404 都回退」**，assets 404 可见性是 no-cache 修复链前提）。新端点 `GET /api/admin/usage?days=30`（`accounts.get_usage_history(since_day)`——accounts 叶子层**不 import metering**，since 由 main.py 算好传入；行粒度 (uid,day) + username join）。`AdminPage` 鉴权自理（`/api/auth/me` + `skipUnauthedHandler`，未登录/非 admin/需改密三拦截态都给「返回主页」出口）；纯 div/token 柱状图（**无图表库**）；聚合逻辑在 `utils/adminUsage.js` 纯函数（node:test 直测）；**用户表额度列仍可编辑**（用户硬要求）。侧栏盾牌按钮 `window.open('/admin','_blank','noopener')` 新标签开（保主应用 ChatPanel 内存态）；`AdminPanel.jsx` 弹窗已删。错误 detail 一律 `normalizeAuthError` 归一。回归：`AdminUsageHistoryTests` / `SpaFallbackRouteTests` / `adminUsage.test.mjs` / `adminPage.source.test.mjs`。
- 顺手结清：mac `/var→/private/var` 4 个已知测试失败（断言两侧 `resolve()`，Windows 恒等）——mac 后端 1554 全绿，CLAUDE.md「macOS 注意点 3」的例外已不存在。

## fail-closed 计费修复 + admin 用量趋势折线图（2026-07-06 晚，Codex 单轮审 APPROVED + 已部署 kr-web-01）

用户报「07-06 面板缓存命中率特别低（37-60%）」，排查结论：**面板没算错、真实缓存健康（~65-72%），是 fail-closed 计费在污染数据**。改 metering fail-closed / usage_daily schema / admin 图表前必读。

**根因（三方对账实证：CRA usage_daily vs new-api logs vs managed-proxy 请求数）**：流在 usage 块到达前中断（用户点停止 / 手机切后台断 SSE / 瞬态断流）→ `_settle` 按 deepseek-v4-pro **256k 上下文上限全额记 cache_miss**（¥0.768/次）。07-06 实测 233 个请求两侧全对上，但 CRA 多记 ~190 万幽灵 miss = **7 次中断 ≈ ¥5.6 = 当日账单 42%**；幽灵 miss 同时把命中率从真实 64.6%（new-api 侧）拖到面板 48.7%。07-01 起累计幽灵账约 ¥10。

**修复（backend/metering.py + accounts.py + main.py，硬约束）**：
- **请求感知估算**：fail-closed 结算改按本次 create kwargs 估 token 上界——`estimate_request_tokens_upper_bound`（messages+tools；字符三档：CJK 1/字、ASCII 0.5/字、其余（emoji 等）2/字；×1.15 margin + 2000 base；多模态/异常形态返 None→回落模型 ceiling；估算恒 `min(est, ceiling)` **绝不比旧封顶更贵**）。**已流出的 completion**（`chunk_completion_chars` 逐 chunk 累计 delta content/reasoning_content/tool_calls 字符；非流式 `response_completion_chars`）按 1 token/字符 ×1.15 计**输出价**补上——只按 prompt 估算会漏「短 prompt + 长输出后断流」（Codex BLOCKER）。**诚实定性**：近似上界而非任意 Unicode 严格上界（base64 密度实测 ~0.7/字符略超 0.575 估算）——滥用者中断「逃掉」的差价远小于其自身配额消耗，经济上无利可图；严格上界=旧 256k 封顶=对正常用户 42% 幽灵账，取舍偏典型用户。
- **`failclosed_tokens` 独立列**：fail-closed 账 `add_usage(cost, 0, 0, 0, failclosed=billed)`，**绝不进 cache_miss**（进 miss 就是本次命中率污染事故本身）；`usage_daily` 加列 + `init_db` PRAGMA 检查幂等 ALTER 迁移；`/api/admin/usage` rows 透出该字段，前端图表 tooltip「含中断估算 N tokens」+ 明细表消耗列橙色 `*`。
- **GeneratorExit 不计暂停**：`_metered_stream` finally 里 `sys.exc_info()` 判 GeneratorExit（消费方关流=用户停止/断连/重试关旧流）→ `bump_pause=False`——否则手机切后台 3 次就把该用户当日模型 `ModelPausedError` 锁死；provider 真异常 / 自然结束无 usage 仍计数（暂停保护的本意=provider 不报 usage 的计费盲飞）。计费本身照常（无逃单后门）。
- fail-closed 结算现打 `[metering] fail-closed settle` warning 日志（本次事故零日志、全靠对账定位——观测性补上）。
- settle-once / settle 不抛 / `extract_billing_usage` 不静默归零 / `finally: response.close()` 等既有不变式**全部未动**；历史已污染数据（07-01~06 约 ¥10 + 对应 miss）**不回填**（per-user 无法从 usage_daily 反推、new-api 侧无用户维度，已放弃 surgery）。
- **回归**：`tests/test_metering.py::FailClosedEstimateTests`（估算器/emoji 档/clamp/failclosed 列/GeneratorExit 不暂停/provider 异常仍暂停/流中断补计输出）、`test_accounts.py`（failclosed 累加 + 老库迁移幂等）。

**admin 用量趋势折线图（前端，任务同批）**：柱状图 → 平滑多序列 SVG 折线（`components/UsageTrendChart.jsx` + 纯函数层 `utils/usageChart.js`——Fritsch–Carlson 单调三次插值 `smoothPathD`，**平滑但不过冲**：零值日不画负假谷）。硬约束：
- 序列 = 输入(hit+miss)/缓存命中/输出 走左轴 tokens、消耗走右轴 ¥ 虚线；`axisMax()` 全零侧返 0 → 只标基线 0 不渲染假刻度；hover/点击（`onPointerMove`+`onClick`）吸附最近日 → 竖线 + 数值卡（全序列值 + 命中率 + 活跃用户 + failclosed 提示）。
- **图表颜色类三份写死**（stroke-/bg-/fill-）在 `SERIES` 常量——Tailwind JIT 按源码字面量扫描，**禁运行时拼类名**（`replace('bg-','fill-')` 会让类静默缺失，source-guard 锁死）。
- **用户 × 时间范围双筛选联动**：`usageFilter`（明细卡的用户 select）+ `usageRange`（趋势卡的 7/30/90 日 select）同时驱动趋势图与明细表；**概览 4 卡固定全局近 30 日**不随筛选漂移（`aggregateByDay(usage?.rows, days.slice(-30))`，source-guard 锁）。fetch `days=90` 一次、范围切换纯前端切片不重请求。
- 宽度经 ResizeObserver 实测像素（不用 viewBox 拉伸防文字变形）；`filterUsageRows(rows, uid, sinceDay)` 第三参时间窗；`barRatio`/`niceTicks` 已删（勿引用）。
- **回归**：`frontend/tests/usageChart.test.mjs`（插值不过冲/轴/坐标/序列构建）、`adminUsage.test.mjs`、`adminPage.source.test.mjs`。

**部署（2026-07-06 晚，第五笔）**：dist swap bundle `index-Rwor1vmc.js` + file-push 3 后端文件（metering/accounts/main）+ 重启 → 启动时 `init_db` 自动迁移 DB（已验列存在）；smoke：公网 health/新 bundle/`/admin` 200/usage 端点 401 门禁/journal 干净。**回滚点 `/opt/cra-rollback-20260706b/`（3 旧文件 + app.db.bak）**——注意回滚代码须同时回滚 DB 或容忍多余列（老 add_usage 6 参 INSERT 对多列表安全）。

## admin 搜索池额度监控（2026-07-07 实施 + Codex 3 轮审 APPROVED + 部署 kr-web-01）

搜索池（serper/brave/tavily/exa）的额度/用量监控进 `/admin` 页。改搜索记账 / provider 适配器 / 额度报告 / 搜索池配置前必读。commits `1c463aa`→`34e6352` + docs `262f325`。

- **新叶子模块 `backend/search_quota.py`**（只依赖 accounts/metering/config + requests，**绝不 import chat/skill/main**）：记账 + tavily 实时拉取 + 报告装配。
- **key 身份 = sha256 指纹**（`key_fingerprint`，前 12 hex，非机密、跨配置重排/换 key 稳定）：`accounts.search_usage_daily(provider, key_id, day)` + 快照 app_config 键 + 报告 join 全按指纹；**绝不用列表下标当持久身份**（重排 key 会把旧账记到新 key 头上，Codex BLOCKER）。`init_db` 含 `key_index`→`key_id` 幂等迁移（旧行 `legacy-index:{n}` 保留进历史、指纹不匹配天然不入估算）。
- **记账绝不阻塞搜索**：`SearchRouter` 注入的 `usage_recorder` 在生产接 `enqueue_search_usage`（有界队列 512 + daemon worker 落库、满即丢+日志）——**别改回同步写**（SQLite busy 最长等 5s 会卡 provider 调用）。`record_search_usage` 同步版留给 worker/测试；`wait_for_usage_idle` 测试用。成功/失败都记（errors 列），冷却跳过/缓存命中不记。
- **数据源三档（报告 `source` 字段，前端标签）**：tavily=`live`（`GET /usage` 逐 key、5min TTL 缓存、失败不缓存；**plan_usage/plan_limit 是账号级字段**，按 (plan,usage,limit) 元组去重防同账号多 key 翻倍，**仅 used>0 触发**——月初全零元组无区分度，部署实测 3 账号被误折成 1000/1000；**⚠️ 2026-07-08 实测 `/usage` 端到端滞后 ~45-55min 且批量周期性 flush（非实时表，官方宣传 "real-time" 不准；`tvly-dev-` 开发版 key 照常计数）→ UI 标签「实时」改「官方额度」、卡片可见口径提示指向本地实时「今日 N 次」；官方额度数与本地记账故意解耦、剩余不随每次搜索即时变化＝数据源特性非 bug、勿改回「实时」标签**）；brave=`observed`（`X-RateLimit-*` 响应头月度段快照；**观测在状态码判断之前**，429 恰带 remaining=0，快照挂 `SearchProviderError.quota_snapshot` 走错误记账透传；月度段 0=unlimited 视为无信号）；serper/exa=`estimated`（serper 按响应体 `credits` 真值、exa 按 calls×`est_cost_per_call`；monthly 按本月至今、one_time 按全时段+`baseline_used`；**只按当前配置 key 指纹归集**，退役 key 不拖累）。
- **key 原文零回显**：报告 key 标签 = `#N · 指纹前6位`——**连 key 尾 4 位都不许出现在 API 响应**（`test_report_never_echoes_api_keys_or_their_tails` 锁死）。
- **配置 `quota` 块整体可选**（`config.py:ManagedSearchQuotaConfig`：model=monthly/one_time、unit=credits/usd/requests、per_key_quota、baseline_used、est_cost_per_call）——缺省=未声明（source=none 只显调用统计），**向后兼容随桌面包分发的存量配置**。纯展示/估算用，**不参与限流门禁**（`daily_soft_limit`/`minute_limit` 仍是未执行的摆设字段，本批刻意不接——解决「看不清」非「超了没拦」）。改 `managed_search_pool.json` 需重启（路由单例不热重载）。**2026-07-07 配置重排**：primary=[tavily,brave]（月度重置不用白不用）、secondary=[serper,exa]（一次性库存做兜底）、权重 3/1/3/2、exa 第 4 把 key 入池。
- **端点** `GET /api/admin/search-quota`（`get_current_admin` 门禁、同步 def 走线程池、`?refresh=true` 强刷 tavily 缓存）：缺配置 `configured=false` / 坏配置 `configured=false + error`（两者必须区分）。
- **前端** `SearchPoolQuota.jsx` + `utils/searchQuota.js`（纯函数 node:test 直测）：**独立 effect 取数、不进 reload 的 `Promise.all`**（tavily 慢/挂不拖累核心管理数据，source-guard 锁）；序列颜色类三份写死字面量（JIT 铁律）；估算卡必须带口径提示（「不含其它部署消耗」）；`SOURCE_META.live` 标签＝「官方额度」（非「实时」）+ hint 点破 ~1h 滞后并指向本地实时「今日 N 次」，`estimated`/`live` 两类卡片都可见渲染 `meta.hint`（`searchQuota.test.mjs` 诚实性守护 `/滞后|延迟|非实时|即时/` 挡退回宣称实时）。
- **回归**：`tests/test_search_quota.py`（指纹/队列/tavily 缓存与去重/报告装配/估算窗口/key 零回显）、`test_search_providers.py`（key 归属/serper credits/brave 快照含 429）、`test_search_pool.py`（recorder 注入语义）、`test_accounts.py`（表+迁移）、`test_admin_api.py`、前端 `searchQuota.test.mjs`/`searchPoolQuota.source.test.mjs`。
- **部署（第六笔，2026-07-07）**：7 后端文件 + 配置 + dist swap（bundle `index-D2bYJHJ7.js`）+ 重启；回滚点 `/opt/cra-rollback-20260707/`（含 app.db.bak）。**已知余项**：serper/exa 记账启用前的历史消耗未计（可填 `baseline_used` 校准，不填=剩余偏乐观）；brave 快照等首次真实 brave 搜索才出现。

## opencode SSE 规范化 sidecar（2026-07-03 上线 jp-app-01）

`opencode_proxy/`（镜像 `managed_proxy/` 约定：`create_app(settings)` 工厂 + `NormalizerSettings` dataclass + 非 root Dockerfile）是 new-api ↔ opencode.ai/zen 之间的薄反向代理。修 opencode 2026-07-01→02 起的**非标准流式格式**（把 `usage` 挂在 finish 正文块上，而非 OpenAI 规范的末尾 `choices:[]` 空块 + `[DONE]` 后多发私有块）——该格式让 new-api 抓不到流式 usage → 回退本地估 token（`local_count_tokens`）→ cache=0 → 下游 CRA 按最贵未命中档计费（deepseek-v4-pro miss 3.0 vs hit 0.025 元/百万，差 120×）。opencode 本身物理确有缓存且 usage 字段完整，纯流式格式回归；new-api/薄网关无 bug。改 sidecar / 计费链前必读。部署/回滚见 `docs/opencode-normalizer-deployment.md`。

**拓扑**：CRA → 薄网关 `managed_proxy`（纯字节透传、不碰 usage）→ new-api（渠道路由 + 计费/日志）→ **本 sidecar** → opencode。缓存字段全程透传到 CRA（2026-07-03 门禁 + 薄网关全链实测：8/8 响应带 `prompt_cache_hit_tokens>0`，含走渠道 61 的）。

**硬约束**（`opencode_proxy/normalizer.py` + `app.py`）：
- **必须自建字节级 SSE 组帧**（`_SseEventFramer`，app 走 `upstream.aiter_bytes()`）：只按 `\r`/`\n`/`\r\n` 切行、空行分事件——**绝不用 httpx/requests 的 `iter_lines`**（它按 `str.splitlines()` 会在 ` `/``/`\v`/`\f` 等 Unicode 行边界字符处切断正文 JSON → 正常回复被误判截断、丢 usage；实测 httpx 也如此）。输出帧 `ensure_ascii=True`——不把行边界字符传给下游 new-api。
- **计费 fail-closed**（严格贴合 `backend/metering.py:extract_billing_usage` 语义，防少计费）：usage 候选只认**终态块**（`choices==[]`，或非空 list 且每个 choice 带 finish_reason；`choices` 缺失/非 list 的裸 `{"usage":…}` 与非终态快照不认）；最后一个终态 usage 胜出，且**候选之后出现任何非私有业务事件即清候选**；发出前过 `_usage_is_billable`（prompt/completion 为整数非负 ≤1e9；**miss 存在则 hit 必在且 `hit+miss==prompt`**；嵌套 `prompt_tokens_details.cached_tokens` 须一致）→ `_canonical_usage` **规范化重建**（只留校验过的字段，杜绝未校验 cache 别名穿透）；截断（未见 `[DONE]`）/ 畸形事件 / 非法 UTF-8 / 单事件超 8MiB → **不发 usage、不发 `[DONE]`**。收到 `[DONE]` 当场发 usage+DONE 并停读关上游（不等 EOF）。
- 只丢**明确识别**的 opencode 私有块（含 `cost` 且键 ⊆ `{choices,cost,normalizedUsage,x-opencode-type}`）；`{"error":…}` / 未知对象**透传**（不吞错误）。app **不鉴权**（仅内部、绝不公网暴露，opencode key 由 new-api 每请求经 Authorization 透传、不落盘）、`trust_env=False`、不跟随重定向、拒 `..` 路径段、非流式与 4xx/5xx（含 SSE 错误体）逐字透传。
- **部署态接线**：new-api 渠道 61【商业】Opencode GO base_url→`http://opencode-sse-normalizer:18732`（`newapi_default` 网络容器名）、group→`default,ds`（加回 CRA 的 ds 组，克隆 20 行 ds abilities）。**回滚**=base_url 改回 `https://opencode.ai/zen/go` + group 去 ds + 删 `ds|61` abilities + 重启 new-api（或还原 `one-api.db.bak-ocnorm-*`）。改 new-api 渠道配置需重启（无 admin API 会话，走 DB 直改 + 重启，重启前停容器避 WAL 竞争）。
- Codex 双轨（spec+quality，gpt-5.5 xhigh）审 **5 轮 APPROVED**（红队挖出并修一串真 bug：` ` 切断、候选非最终、cache 组合漏计、别名穿透、事件缓冲误判）；回归 `tests/test_opencode_normalizer.py`（42 用例，事件上限可注入以秒级跑）。DeepSeek 官渠兼容不涉及（sidecar 不 import backend、不碰 provider 序列化）。

## 管理型搜索池

`backend/search_pool.py:SearchRouter` 实现分层路由：`primary` → `secondary` → 可选 `native_fallback`。Provider 适配器在 `backend/search_providers.py`（Tavily/Brave/Exa/Serper），状态存储在 `backend/search_state.py`。`per_turn_searches` / `project_minute_limit` / `global_minute_limit` 是并列门禁，任一触发都会返回 `QUOTA_EXHAUSTED_MESSAGE`。

**多 key 轮询**：每个 provider 支持配多个 key（`managed_search_pool.json` 里 `api_keys: [...]` 列表；旧 `api_key` 单值仍兼容，`config.py:ManagedSearchProviderConfig.__post_init__` 互相回填）。`BaseSearchProvider._next_api_key()` 每次 search **线程安全轮转**取一个 key 传给 `_request_payload(query, api_key)`，把负载摊到多账号。⚠️ per-provider `minute_limit`/`daily_soft_limit` 是**从未被执行的摆设字段**（只解析不消费，别当成生效门禁）；额度可见性与 per-key 用量记账见上方「## admin 搜索池额度监控」段。改 key/限额后**要重启**（路由单例不热重载）。

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

### macOS 上做开发（web 模式，无需打包）

桌面端只承诺 Windows 分发，但**日常开发可以在 macOS 上跑**——走 web 模式（`run_web.py`），不碰 PyWebView / 原生文件桥 / PyInstaller，全程跨平台。需要 Python 3.11/3.12 + Node 20 LTS。

> ⚠️ **系统 Python 太新（≥3.13）装不上依赖**（`curl_cffi`/`pydantic` 等无对应 wheel）。mac 上最省事用 `uv` 拉一个托管 3.12 建 venv，别用系统 `python3`（可能是 3.14）：

```bash
# 开发环境初始化（uv 托管 Python 3.12，避开系统 3.14）
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.txt
cd frontend && npm install && npm run build && cd ..   # 必须 build，否则 SPA 404

# 启动 web 模式（浏览器访问，不开原生窗口）
.venv/bin/python run_web.py            # → http://localhost:8888

# 测试（powershell 相关用例会 skipIf 自动跳过，不报错）
.venv/bin/python -m pytest tests/
```

**三个 macOS 上需要注意的点**：

1. **私有文件不在 git 里，要从 Windows 机拷过去**：`managed_client_token.txt`、`managed_search_pool.json`（`.gitignore` 忽略）放仓库根，否则 managed 模式认证不了 / 内置搜索不工作。临时方案：设置里切 `custom` 模式自填 OpenAI 兼容 key，无需这两个文件也能跑通对话与写作。
2. **S5「导出可审草稿」需本机装 pandoc**（W2-C 已去 Windows 化）：导出改纯 Python 调 pandoc（`report_tools._resolve_pandoc()`：打包/Windows 态优先包内 `pandoc.exe`，否则走系统 `pandoc`）。mac 开发态须 `brew install pandoc`，否则导出返回友好错误「未找到 pandoc」。原 `export_draft.ps1` 硬编码 `powershell` 的问题已不存在（脚本已删）。见下方「## W2-C」段。
3. ~~**4 个测试在 mac 上失败属环境差异**~~（**✅ 2026-07-06 已结清**：`test_skill_engine.py` / `test_workspace_materials.py` 的 `tempfile` 路径断言两侧统一 `.resolve()`，macOS `/var`→`/private/var` symlink 不再误报，Windows 恒等无影响——mac 后端现应全绿）。

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
