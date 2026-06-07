# Cutover Report — S5 审查迷你聊天窗口 + 断点续审 + 触发轮注入（R1 + R2，2026-06-07）

**Status:** 自动化收尾完成（后端 253 + run-bound 31 + 前端 253 全绿）；打包重建与真实 GUI+LLM 端到端验收按计划留给用户手工执行。

**依据文档：**

- Spec：[docs/superpowers/specs/2026-06-06-s5-review-mini-chat-and-resume-design.md](specs/2026-06-06-s5-review-mini-chat-and-resume-design.md)（v16，codex 三轨 spec/quality/红队 8 轮 APPROVED）
- Plan：[docs/superpowers/plans/2026-06-07-s5-review-mini-chat-and-resume.md](plans/2026-06-07-s5-review-mini-chat-and-resume.md)（v5，codex 5 轮 APPROVED）
- Baseline：[cutover_report_2026-05-22_s5-redesign.md](cutover_report_2026-05-22_s5-redesign.md)（S5 两按钮重做，本次是其上的增量改造）

## 实施概述

这是「领导评审反馈整改簇 R1–R5」的**批 1**（R1 + R2 合一份 plan）。解决 demo 现场领导亲见的两个硬伤：

- **R1（审查体感像死机 + 断连活全丢）**：把 S5「独立审查」从"闷头读→一次性 write→结束"的子代理，改造成**会说话的流式迷你聊天窗口 + 断点续审**。审查 agent 实时推 `content_delta`，前端渲染文字 + 工具卡片；网络一抖/断连不再 3 秒自动关窗丢活，而是停在可续的 errored 态，用户在断处补充指令、让 agent 带累计上下文从断处续审。
- **R2（AI 味自查主代理答非所问）**：触发那轮不再只给"请 read_file 读报告"的指令（读不读靠模型自觉），改为**直接把报告全文作为 user/context 数据注入**那一轮上下文，主代理必基于注入内容回复。R1 触发注入同理。

**架构**：6-commit atomic 渐进。C1 = R2 独立可 ship（不依赖 R1）；C2–C4 = R1 后端 dormant（流式 agent → 续审存档 store → POST endpoint，各自可单测但不切用户路径）；C5 = 用户可见 cutover（前端 ReviewChatWindow + run-bound 注入增强）；C6 = 回归矩阵 + 本报告。

## Commit 链

| Phase | Commits | Scope |
|---|---|---|
| **C1** R2 触发注入 | `0ec2e13` 报告作 user-data 注入 + 汇报轮禁工具 + ready fail-fast<br>`7f8b9d4` 响应层硬拦截 no-tools 轮 + 防御 elif/read_file fallback<br>`276b7c8` 抑制 no-tools 轮"准备调用工具"旁白 | 触发轮报告全文注入（数据非指令、不入 system）；汇报轮请求层 pop tools + 响应层硬拦截；注入前 ready fail-fast |
| **C2** 流式会说话 agent | `ddba13f` 抽 `ThinkingStreamParser`→`stream_parsing.py`；审查 agent 会说话 + 流式 `run()` + think 剥离<br>`4e20a9b` 缺 id 畸形 tool_call 走合规隔板；空 tool_calls 序列化对齐 chat | 审查 LLM 非流式→流式，content 增量作 SSE 推前端；`<think>` 三路径剥离（前端永不见）；解 chat↔independent_review 循环导入 |
| **C3** 续审存档 store | `7fea285` `ReviewSessionStore`（两锁/run_id/tombstone）+ candidate staging + 原子替换 + 自修≤2 + resume_snapshot<br>`abed413` 关 5 个 store 状态机 blocker<br>`448b265` run() 入口缺 store/run_id fail-fast | 进程内续审存档：candidate 锁外 staging、guard 内原子替换 + tombstone；自修失败 CAS 降级 errored 留 snapshot 可 resume；从 messages 重建 candidate（不私存） |
| **C4** POST endpoint | `2fc16b4` POST stream + resume/discard + run-bound tombstone dispatch + lock-release-all-paths + 结构化 timeout<br>`456373b` 统一 completion（done+worker 共享 disconnect guard + tombstone 重读） | `POST .../stream {resume,run_id,supplement?}`；resume 短 blocking 等锁后重读 store；completion 仅在 lock 释放后 + 重读 done tombstone 才发 review-completed |
| **C5** 前端 cutover + run-bound | `67158f8` ReviewChatWindow mini-chat + resume UI + run-bound 注入 + pending 队列<br>`b2063c6` 双轨 review：SSE EOF 可续 + supplement 输入 + run-bound 锁外 yield<br>`1360c3e` 红队 B1+B2：切项目孤儿 + stale pending<br>`d9fe6c9` 红队 B3：pre-stream disconnect lock 泄漏 | 用户可见 atomic cutover（见下方 C5 review 详情） |
| **C6** 回归 + cutover doc | 本 commit（cutover doc 自引用 hash 见 git log）+ worklist/memory 更新 | spec §6 测试矩阵全覆盖核对（零缺口）；本报告 |

## 关键设计要点

### 续审时序四件套（最大复杂度来源）

断点续审的正确性靠四件套协同：① 流式缩短 worker 退出窗口；② `run_id` 防复活（旧 run 的迟到 commit 被 run_id 不匹配拒绝）；③ resume 短 blocking 等锁 + 前端 409 指数退避（上限 5 次后给"重新发起/关闭"出口）；④ discard 触达 cancel_event。叠加 C4 结构化 timeout。协作 cancel 固有延迟：provider 无首包时 worker 仍可能持 lock 到 timeout。

### Trust boundary

报告作 **user/context 数据消息**注入（非 system 指令）；汇报轮**禁工具**（请求层 pop tools + 响应层硬拦截 `_execute_tool`），恶意/异常报告无法诱导工具调用或阶段推进；`system_triggered` 轮**只持久化 assistant**，报告全文不落 `conversation.json`、不写空 user。

### run-bound 注入（防误汇报旧报告）

`trigger_metadata={run_id, report_mtime_ns}`（opaque 字符串，**全程禁转 Number/int**）端到端透传：前端 `buildChatRequest` → `ChatRequest` → `/api/chat/stream` → `chat.py` tombstone 校验。校验持 review lock：`get_done_mtime(run_id)` 匹配 + 读报告后 **re-stat `mtime_ns` 复校**（TOCTOU 防校验与读取间被新 run 的 `os.replace` 替换）。lint 路径无 run_id 维持 generic ready。

### DeepSeek 官渠兼容（流式改造后仍守住）

- 带 tools 不显式发 `tool_choice`（reasoner route 拒）；
- tool-call follow-up 回传**非空** `reasoning_content`；
- 不把 SDK `model_dump()` 的 null 字段（`reasoning_content: null`/`audio: null`）塞回历史；
- 三 helper（`_should_send_explicit_tool_choice` / `_extract_reasoning_content_from_message` / `_serialize_assistant_tool_call_message`）在 `independent_review.py` 与 `chat.py` 行为锁定一致（`test_deepseek_compat_helpers_match_chat_helpers`，扩展到流式 follow-up）。

## C5 三轨 codex review（含红队挖出 3 个真 BLOCKER）

C5 是用户可见 cutover，过 spec / quality / 对抗式红队三轨独立 review，**非诱导式秒过**——每轨独立挖到真问题，全部修复后复审 APPROVED：

| 来源 | 发现 | 严重度 | 修复 |
|---|---|---|---|
| spec + quality（双轨）| SSE 流 EOF/[DONE]-无结果被当成功 → 窗口卡 running 无续审入口（R1 核心失败场景）| BLOCKER | `consumeStream` 跟踪 `sawError`/`reachedDone`；`[DONE]` 不再短路；非正常完成派发可续 error |
| spec | errored 缺 supplement 输入框（plan Task 5.2 要求"断处输入"）| 应修 | errored 面板加 `<textarea>` → `handleResume` 传 supplement |
| quality | run-bound 错误在持 review lock 的 try 内 yield → partial-consume 断连卡锁 | 应修 | 锁内只算 `run_bound_error`，`finally` release 后锁外 yield |
| 红队 **B1** | 切项目时 WorkspacePanel 异步关窗，那一帧 ReviewChatWindow 收到新 projectId+isOpen=true → 对**错误项目**误发起审查 + 孤儿会话 | BLOCKER | open-effect 加 `openedRef` 守 isOpen 上升沿；projectId 变化不重启 |
| 红队 **B2** | pending 清理放在 enqueue 时（太晚）：R1 成功入队→用户发起 R2 覆盖 tombstone→R1 flush 被 run-bound 拒，成功审查报成 error | BLOCKER | 发起新 run 时（`runIndependentReview`）即 `dropPendingTriggersByType` 剪同类型旧 pending |
| 红队 **B3** | **Starlette 源码级**：`StreamingResponse.__call__` 用 task group 并发跑 stream_response + listen_for_disconnect，disconnect 先完成会 cancel group；client 已断开时 `generate()` 可能一行未执行 → worker（在 generate 内创建）不启动 → review lock **永久泄漏** → 项目审查 409 到进程重启。真实触发：点审查后立刻关窗/切项目/刷新 | BLOCKER | worker 创建（event_queue+run_worker+create_task）从 `generate()` 内移到 **endpoint 函数体**；worker 在 endpoint await 时即启动，`finally` 释放 lock 不再依赖 generate 被消费 |

红队同时核实确认：lint endpoint（普通 JSON，lock 同栈 finally 释放）+ `/api/chat/stream`（请求锁在 generator 消费时才 acquire）**无 B3 同类问题**。

## 验证结果

- 后端 `tests/test_main_api.py + test_independent_review.py + test_skill_engine.py`：**253 passed**（含 B3 回归 `test_review_lock_released_even_if_response_generator_never_consumed`）
- 后端 `tests/test_chat_runtime.py -k "system_trigger or run_bound or trigger_metadata or mtime"`：**31 passed**（含 partial-consume lock 回归）
- 前端 `node --test frontend/tests/`：**253 pass**（含 EOF 兜底 / supplement / B1 上升沿 / B2 dropPendingTriggersByType source+pure 测试）
- `vite build`：通过（仅既有主 chunk >500kB warning，预存债）
- spec §6 测试矩阵：逐节核对**全覆盖、零缺口**；codex R1 必补三类（os.replace 失败留 errored / staged write resume 重建 candidate / mtime 大整数不失精）全部已有对应测试。
- **本次不跑 `build.bat`**，不声称新 `dist\咨询报告助手\` 已重建。
- **本次不跑真实 GUI+LLM 手工 E2E**（见下方手工验收待办）。

## 已知 park / 限制

1. **pending 队列跨项目丢弃是设计口径**（非 bug，红队确认 park 合理）：审查跑完若主聊天忙 + 用户切走项目，回到该项目不自动弹汇报轮——但报告已**持久化到磁盘**不丢，用户可手动让主代理读。per-project 持久化 pending + "切回项目提示有已完成审查报告" 属 R3 工作区重构范畴。
2. **B3 trade-off**（红队确认可接受）：worker 在函数体启动后若 generate 从不被消费（B3 race），cancel_event 不被 set，worker 跑完整轮 agent.run 才释放 lock（**有限期持有，非永久泄漏**）；主动关闭路径前端还会发 `/discard` set cancel。为低概率裸断连加 disconnect watcher 不划算，park。
3. **>100k 字 friendly fail 是 v0 策略**（沿用 baseline）：超 100k 字独立审查给友好错误，不做 chunk fallback（worklist P3）。
4. **单进程 lock 假设**：per-project lock 覆盖单进程桌面应用，多进程并发未覆盖。
5. **过时报告更广问题 park**：本期靠 run-bound 防"误汇报旧报告"；"门禁放行过时报告"（report mtime vs 正文 mtime）不做。

## 手工验收待办（用户执行）

1. **真实 S5 项目 GUI E2E**（plan Task 5.4 Step 3，自动化代替不了）：点独立审查 → 看到流式旁白文字 → 模拟断连（断网/杀后端）→ 错误留存不自动关 → 输入补充 → 继续审查从断处续 → 成功自动关窗 → 主代理基于注入汇报发现。验窗口可拖/可关/带进度。
2. **`build.bat` 重建** `dist\咨询报告助手\` 并打包态复验（managed 真实模型长链路偶发 timeout 是已知项 P1，与本次无关）。
3. 验"点审查后立刻切项目/关窗"不再卡死该项目审查（B3 修复的真实场景）。

## Rollback Procedure

整个 R1+R2 在 feat 分支 `feat/s5-review-mini-chat-and-resume`，未 push、未合 main。回滚 = 不合并该分支即可；main 仍是 baseline S5（2026-05-22 两按钮重做）。若已合并需回滚：`git revert` C1–C6 区间（C1 R2 注入与 C2–C5 R1 可分别回滚——C1 独立可 ship，R1 整体回滚需 C2–C5 一起 revert，因前端 cutover 切了用户路径）。
