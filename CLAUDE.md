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
- `parse_and_sanitize_methodology` 是 trust boundary 净化（outline 用户可编辑）：净化结果作**数据**注入、绝不当指令。**不变式**：`_normalize_for_danger` 去除集合必须 ⊇ `parse` 的 split 分隔符（`、,，`）∪ off-menu 白名单 `[A-Za-z0-9一-鿿\-/ 　]` 允许的非字母数字字符——改 off-menu 白名单或 split 分隔符须同步（防工具名/checkpoint 的空格/连字符/顿号变体绕过）。归一化危险词组覆盖全部 6 个 `STAGE_CHECKPOINT_KEYS`（`test_*_all_checkpoint_key_variants` 遍历防漏）。
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
- `MeteredManagedClient(raw,uid,model_pricing)`：镜像 `.chat.completions.create`，调用前 `_reserve`（`used>=cap`→`QuotaExceededError`；缺失计数≥3→`ModelPausedError`），调用后/流末 `_settle`（`add_usage` 原子累加 + 成功清缺失计数）。**流式 `_metered_stream` 在 `finally` 恰好结算一次**（自然结束/provider 异常/GeneratorExit 一律 fail-closed），`finally` 内 settle 包独立内层 finally（settle 抛错不遮蔽在途异常、底层流必 `close()`，`sys.exc_info()` 判在途异常）。`__getattr__` 透传非 `.chat.completions.create` 调用面（如 `.responses`）。
- `wrap_client_for_billing(raw,uid,settings)`：managed→`MeteredManagedClient`；custom→裸 client。
- `today_shanghai()` UTC+8 日界；`_miss_counter` 进程级 per-(uid,model,**day**)（day 入键 → 暂停次日自动清零）。

**accounts.py**：`usage_daily(uid,day,cost_micro_yuan,cache_hit/miss_tokens,output_tokens, PK(uid,day))` 原子 `ON CONFLICT DO UPDATE` 累加；`get_usage_today`/`add_usage`/`get_effective_daily_cap_micro`（user override `users.daily_cost_micro_yuan` → 全局 `app_config.global_daily_cap_micro_yuan` → 默认 `DEFAULT_GLOBAL_DAILY_CAP_MICRO_YUAN`=5_000_000）/`set_user_daily_cap_micro`。

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

## 管理型搜索池

`backend/search_pool.py:SearchRouter` 实现分层路由：`primary` → `secondary` → 可选 `native_fallback`。Provider 适配器在 `backend/search_providers.py`（Tavily/Brave/Exa/Serper），状态存储在 `backend/search_state.py`。`per_turn_searches` / `project_minute_limit` / `global_minute_limit` 是并列门禁，任一触发都会返回 `QUOTA_EXHAUSTED_MESSAGE`。

**多 key 轮询**：每个 provider 支持配多个 key（`managed_search_pool.json` 里 `api_keys: [...]` 列表；旧 `api_key` 单值仍兼容，`config.py:ManagedSearchProviderConfig.__post_init__` 互相回填）。`BaseSearchProvider._next_api_key()` 每次 search **线程安全轮转**取一个 key 传给 `_request_payload(query, api_key)`，把负载摊到多账号；`daily_soft_limit` 应按 key 数缩放才有实际余量。改 key/限额后**要重启**（路由单例不热重载）。

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
3. **4 个测试在 mac 上失败属环境差异、非真 bug**：`test_skill_engine.py` / `test_workspace_materials.py` 里涉及 `tempfile` 路径比对的用例，因 macOS `/var`→`/private/var` symlink、临时路径未解析 vs 已解析不相等而失败，**Windows 上通过**。要 mac 全绿需把这些用例的临时路径断言改走 `os.path.realpath`/`.resolve()`（独立小活，见 worklist N-section）。

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
