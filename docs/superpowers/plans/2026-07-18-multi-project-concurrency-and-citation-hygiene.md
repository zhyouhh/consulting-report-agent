# Plan：多项目并发 + 停顿自愈 + DL 内部引用收口 + 研究类写作纪律（试用反馈 0717 批次）

日期：2026-07-18（v11 定稿：Codex 八轮迭代 APPROVED → 对抗红队轮触发 Block A 简化重构（砍 LRU/retain/淘汰，会话内常驻）+ 后端共享态加固（A8）→ 确认轮收口 2 HIGH + 2 MEDIUM + 3 LOW → **终审 SHIP**）
状态：**SHIP，待实施**（codex 执行；实施走 per-task Codex 双轨审惯例）
来源：试用反馈汇总 0717 谭进（TED）三条反馈 + ZhYoU 发现的报告正文 `[DL-XXXX-XX]` 内部标记泄漏问题。

---

## 0. 背景与取证结论（实施前必读，全部已在服务器/代码实锤）

**谭进反馈 1「难以任务并发」**——三个症状、三个精确机制，全部是前端单流架构造成：

1. 「切到项目二后项目一的作业停止」：`frontend/src/components/ChatPanel.jsx` 项目切换 effect（`previousProjectIdRef` 块，约 121–134 行）里 `abortControllerRef.current?.abort()` **主动杀掉在途流**。
2. 「项目一指令带入项目二输入框」：abort → `startStream` 返回失败 → `sendMessage` 里 `restoreInputForRetry`（约 855 行）把项目一**已发送的消息回填输入框**。它只有「发送序号未变 + 输入框为空」两道守卫，**没有项目守卫**——此刻 UI 已是项目二。另外 `input` 是单一 state，未发送草稿切项目也跟着走。
3. 「回滚至上一指令」：后端 `backend/chat.py` 的 `_finalize_assistant_turn`（定义约 7066 行，流式路径调用点约 3221 行）在流式循环**跑完后**才落盘；abort → GeneratorExit → 落盘不执行，整轮不进 `conversation.json`，切回项目一该指令消失。（注：`chat.py:1624` 附近注释讲的是 `_flush_pending_tool_calls` 不能进 finally——finally 里 yield 会 RuntimeError、破坏心跳链路；落盘同理不能塞 finally。**该链路结构不许改**。）

后端 per-project 请求锁（`tenant_project_key`）与 `_CHAT_STREAM_EXECUTOR`（`main.py:951`，8 worker）已支持跨项目并发流，**但红队实锤：per-user 共享态并不并发安全**（registry.json 非原子写 + 无锁 RMW、MaterialConverter 锁碎片），是本计划新增的后端前置任务 A8——**「per-project 锁 ⇒ 后端全并发安全」这个假设是错的，实施顺序上 A8 先于前端并发上线**。

**谭进反馈 2「偶然停顿、没有下一步动作」**——服务器实锤一例（kr-web-01，TED 的「高质量数据集」项目 conversation.json 第 14 条 + journal `Jul 09 10:48`）：DeepSeek 把工具调用参数名写成 camelCase（`filePath`），`_execute_tool` 里 `args["file_path"]` KeyError → 通用 `except Exception` 把裸 `'file_path'` 返回给模型 → 模型随后把工具调用 DSL 当正文吐出，泄漏原始 DSML 标记（真实样本，含闭合标签形态）：

```
<｜｜DSML｜｜tool_calls>
<｜｜DSML｜｜invoke name="read_file">
<｜｜DSML｜｜parameter name="filePath" string="true">plan/outline.md</｜｜DSML｜｜parameter>
</｜｜DSML｜｜invoke>
</｜｜DSML｜｜tool_calls>
```

该轮以乱码收尾、零动作、零提示。现有畸形 tool_calls 自愈（`chat.py:3090` 附近）只覆盖「工具名未知 / 参数 JSON 坏 / 上游合并条目」，参数键名写错与 DSML 泄漏进 content 两种均漏网。**该泄漏已持久化在该用户 conversation.json 里**（B2 须含历史清理）。

**DL 标记泄漏**——`skill/SKILL.md` S3 段强制要求 `analysis-notes.md` 里写 `[DL-...]` 引用（后端数引用数放行 S3→S4，机制本身不动），但 S4 写正文指令**没有一句**说正文不要带。全服务器 25 份草稿 4 份泄漏（71 / 62 / 27 / 6 处），无任何环节拦截，谭进那份 62 处已归档交付。**引用 token 语法真值源**：`backend/skill.py:137-140` 的 `_DL_REFERENCE_GROUP_PATTERN = DL-(?:\d{4}-)?\d+(?:/\d+)*`——**允许无年份 `[DL-001]`** 与斜杠合并 `[DL-2026-01/06]`。

**谭进反馈 3「像资料汇总不像深度研究」**——一半是 DL 标记的台账感（本批治），一半是分析深度/观点密度（进 worklist 专项，见 Block D）。

---

## 1. 目标 / 非目标

**目标**
- A：真·多项目并发（桌面 + 移动）：项目一生成中切到项目二，项目一继续跑、完成落盘；输入框按项目隔离；不再有「切走即杀 + 指令串扰 + 回滚」。含后端共享态加固（A8）与删除端点竞态守卫（A7）。
- B：停顿自愈：工具参数键名 camelCase 容错 + 直接索引参数缺失友好报错；DSML 标记泄漏检测 → self-heal 重发 → 持久化全链净化（**含历史存量清理**）。
- C：DL 内部标记三层收口：S4 prompt 规则（源头）+ 审查 grounding 点名（模型在环清理）+ 导出剥除（确定性兜底）。
- D：研究类报告 S4 写作纪律注入；深度研究质量专项进 worklist。

**非目标**
- 独立审查流（ReviewChatWindow）的跨项目并发/后台化——行为保持现状。
- 后端 turn 落盘时机改造（finally-yield 是 W2-C 心跳链路硬约束）。
- DSML 的流式过程中拦截（token 跨 chunk 边界；本批轮末检测+重发+持久化净化，已流出到屏幕的乱码不回收）。
- 深度研究质量拉升——worklist 专项。
- 多标签页/多用户维度的全局流量控制（cap 只约束单 App 实例，A6 注释如实声明）。
- 计费 in-flight 预扣（日额度并发下是软帽——见 §9 如实定性，B2 spec 本就接受软帽语义）。
- **实例淘汰机制（LRU/retain/hold 计数）——红队 ROI 裁决刻意砍掉**：试用用户每人 1–3 个项目（服务器实数据），会话内全部常驻的内存成本可忽略，而淘汰子系统曾是全部生命周期竞态的来源。若未来单会话项目数真实增长再加（先量数据），记 worklist。

---

## 2. Block A：前端多项目并发（ChatPanel 池化——会话内常驻模型）

### 设计总述

**方案 = 每项目一个 ChatPanel 实例、本次登录会话内访问过即常驻挂载、绝不中途淘汰**（仅两个卸载点：项目删除、登出）。ChatPanel 内部逻辑近零改动——`projectId` prop 对每个实例终身不变，「项目切换」相关代码变成死码删除，输入串扰和回滚**结构性消失**；不存在淘汰 ⇒ 不存在「流/收尾/回填/队列在淘汰边缘丢失」的整类竞态（v3–v7 曾为此堆的 LRU/retain/hold/generation 机制全部不需要）。

**架构分层**：池状态语义——已挂载集合、lease、waiter FIFO、upload/delete token——抽成**无 React 依赖的纯模块** `frontend/src/utils/chatPanelPoolCore.js`，`ChatPanelPool.jsx` 只是薄 React 壳。**并发正确性由纯模块的 node:test 行为测试证明**，source-guard 只锁结构接线。poolCore 是 `useRef` 里的可变对象——**每次影响成员/busy 的 mutation 后，壳层递增 version state（或 core 发 change 通知）触发重渲染**。

核心决策：

1. **池成员 = 本会话访问过的项目集合（首次访问懒挂载）∪ {active}**。移除仅两径：`forgetProject(pid)`（删除成功后）、登出整树卸载。**无 LRU、无 retain 计数、无淘汰时序**。上限即用户项目总数（试用实数据 ≤3/人）；worklist 记「若未来单会话项目数增长，量完真实数据再加淘汰」。
2. **流并发上限 = 池层同步 lease**（`MAX_CONCURRENT_CHAT_STREAMS = 3`，防浏览器 HTTP/1.1 6 连接上限与失控烧额度）。
   - `acquireStreamLease(pid) -> { status: 'started' | 'same_pid' | 'cap_full' | 'deleting', token? }`——结构化结果；**判定优先级 `deleting` > `same_pid` > `cap_full`**（测试锁「已有同 pid lease 后进入 deleting」与「cap 满且 pid deleting」都返回 `deleting`）。同 pid 已有活跃 lease → `same_pid` 拒绝（绝不幂等放行——React `loading` 不是同步互斥）；`releaseStreamLease(token)` 按 token 精确释放。ChatPanel 侧配套同步 CAS `streamInFlightRef`。
   - **admission 同步前置**（在调用 async `startStream` 之前）；`sendUserMessage` 同步 boolean 契约不变；lease token 传入 `startStream`，函数体整体 try/finally 释放（前端普通 async 函数，不在后端 finally-yield 禁区）。
   - **释放顺序铁律：先清 `streamInFlightRef`，再 `releaseStreamLease`（其内部才触发全局 drain）**。
   - fire-and-forget 调用点一律 `void startStream(...).catch(log)`。
   - 用户轮拒绝文案：`cap_full` →「最多同时生成 3 个项目，请等待其中一个完成」+ 输入回填；`deleting` →「项目正在删除」；**两者不得混用**。
3. **拒绝分类：`local_busy` 与 `global_cap_full` 两种拒绝路径不同**：
   - **local_busy**（本实例 loading/uploading/CAS 占用）：用户轮走现状守卫；系统轮入**本地** pending 队列——本实例流收尾 settle timer flush（现状机制，不注册全局 waiter）；uploading 期间被拒的，上传 `finally` 后补一次本地 flush。
   - **global_cap_full**：系统轮入本地队列 + 注册 `enqueuePermitWaiter(pid)`；一次性标记（`project_created` fired / App 侧 marker）**只在 lease 成功后消费**，排队本身用 `autoStartQueuedRef` 同步置位防 StrictMode 双排队。
   - **唤醒（drain 协议）**：`releaseStreamLease` 内 pop waiter FIFO → 调该实例 `flushPendingTriggers()`，同步返回分类，drain 按分类处置：`started` → 停止；`local_busy` → 删 waiter 不重排（本地唤醒源接管，再遇 cap_full 重新注册），继续；`cap_full` → 重排 FIFO 尾部并停止；`deleting` → 删 waiter、不启动、**不 dequeue 本地队列**（保留待恢复），继续；`empty/stale/missing` → 删 waiter 继续。drain 维护本轮 visited 集防自旋；队列侧**先 peek、admission 成功才 dequeue**。分类状态机抽纯 helper（扩展 `utils/pendingTriggerQueue.js` 或新模块）node:test 直测。
4. **autoStart 多值化**：App `autoStartProjectId` 单值改 **`pendingAutoStartProjectIds`（Set，函数式 immutable 更新）**；`onAutoStartConsumed(pid)` 按 pid 删除；清理路径：项目删除、登出、实例初始 GET 发现会话非空。autoStart props 透传全部实例（`ChatPanel.jsx:761` 已有 pid 匹配守卫），**不入 activeOnlyProps**。
5. **后台实例回调带 pid、不改当前视图——双契约适配器**：现状 `App.jsx:371` `handleMaterialsMerged` 同时传给 ChatPanel（473）与 WorkspacePanel（500），且 `appInitGating.source.test.mjs:52` 锁定共用。**不许改 WorkspacePanel 侧既有签名**。并存两组：active 版（签名不变，WorkspacePanel/MobileShell 工作区路径用）`handleActiveMaterialsMerged(materials)` / `handleActiveProjectMutated()`；pool 版（仅 Pool 用）`handlePanelMaterialsMerged(pid, materials)` / `handlePanelProjectMutated(pid)`——经 `pid === currentProjectIdRef.current`（渲染期赋值）守卫，匹配委托 active 版，不匹配只做无害全局动作（额度刷新、静默项目列表刷新）。MobileShell 分别接收路由两组。source-guard 锁**两条契约并存**。`panelProps` 与 `activeOnlyProps`（`project`/`workspace`/`materials`/`injectedPrompt`/`onInjectedPromptConsumed`）互斥，后台实例收 `null`/`[]`。
6. **`loadProjects` 拆分 + 静默刷新守卫**：`initializeApp` 保留顶层 loading gate（行为不变）；新 `refreshProjectsSilently()` 原位更新 `projects`、**绝不触碰顶层 loading**（现 `App.jsx:105` 置 loading=true → 409 行全屏加载页**替换整个业务树**会卸载池杀掉所有流）；创建后（235）/删除后（258）/后台完成回调一律走静默版。静默刷新带 **`projectsRefreshSeqRef` 序号 + 发起时 uid 快照**双守卫（镜像额度刷新 `App.jsx:130` 模式）；创建/删除 2xx 先乐观更新本地列表再静默对账。`appInitGating.source` 增加「初始化之外无顶层 loading 置位路径」断言。
7. **删除 / 登出 / 卸载卫生**：
   - **upload/delete 同步 token 协议**（poolCore 内，先查后删有 TOCTOU）：`beginUpload(pid) -> uploadToken | null`（拒绝 deleting 项目 → 上传入口 showError「项目正在删除」）；`endUpload(uploadToken)`（token 精确）。**多生产者语义（终审提醒）**：ChatPanel 与 WorkspacePanel 是两个上传生产者——poolCore **每个 token 独立登记**，`tryBeginDelete` 须等该 pid **全部** upload token 清零才放行；测试锁「两次 begin、结束一次后删除仍被拒」（防单 token 覆盖）；`tryBeginDelete(pid) -> { status: 'started' | 'uploading' | 'deleting', token? }`（`uploading` → showError「项目正在导入材料，请稍候再删」；`deleting` → 双击重复请求静默/提示，**绝不产生第二个 token 或第二个 DELETE**；`started` 原子标记 deleting）。**deleting 封锁该 pid 一切新工作**：`acquireStreamLease` 返 `deleting`、`beginUpload` 拒绝、waiter/timer 唤醒暂停（本地队列保留）。
   - `finishDelete(deleteToken, {forgotten})`——**exactly-once，放删除流程 `finally`**。**分层**：core 只做 token 校验+状态转移返回 `'forgotten' | 'resume_required' | 'stale'`；Pool wrapper 收 `resume_required` 调该实例 `flushPendingTriggers()` 恰好一次（`stale` 零副作用）。`forgotten`（2xx）→ `forgetProject`；`resume_required`（409/失败/异常）→ deleting 已清、队列恢复。
   - ChatPanel 同步 `uploadInFlightRef`；上传 `finally` 铁律：**先清 ref → `endUpload` → flush 本地队列（判忙用 ref 不用 React state）**。
   - 拆 `abortProjectWork(pid)`（只 abort 流）与 `forgetProject(pid)`（原子清成员/waiter/lease/upload/deleting 记录 → 卸载）。删除流程：`tryBeginDelete` → `try:` `abortProjectWork` → DELETE `finally: finishDelete`。
   - **登出编排上提 App**（现状 `Sidebar.jsx:147` 先 await logout 再回调——等待期上传完成可起幽灵流）：Sidebar（含 MobileShell 转发）只发「登出意图」；App 调 orchestrator `runLogout`（见下）。`sidebar.source.test.mjs` 更新锁新契约。
   - **shutdown/logout orchestrator（无状态、回调注入、单一实现——绝不与 ChatPanel refs 形成双份状态）**，新纯模块三函数：
     - `shouldContinueAfterUpload({mounted, accepting})`——ChatPanel 上传 await 后传 `mountedRef.current`/`acceptingWorkRef.current` 当前值；
     - `runTwoPassShutdown(handles)`——第一遍全实例 `stopAcceptingWork()`（同步、首条语句置 `acceptingWorkRef=false`），第二遍逐实例 `abortActiveStream()` + `cancelActiveUpload()`（abort 在途上传、exactly-once `endUpload`）+ `cancelPendingWork()`；Pool `abortAll()` 直接调用；
     - `runLogout({abortAll, requestLogout, clearSession})`——内部 `try { await requestLogout() } finally { clearSession() }`；`clearSession` = `setAuthUser(null)` + 清 `pendingAutoStartProjectIds`（**POST reject 也无条件退出本地态**，无 `resumeAcceptingWork` 复活路径）；App 入口 `void runLogout(...).catch(log)`。
   - **`sendMessage` 每个上传/图片准备 await 后查 `shouldContinueAfterUpload`**，失败只做 upload token / 队列 / waiter 的幂等清理、禁止进 stream admission（杀「登出后上传 resolve 发幽灵 `/api/chat/stream`」）。
   - **WorkspacePanel 材料 tab 上传同样必须走 `beginUpload(pid)`/`endUpload(token)` 协议**（红队确认轮 HIGH 2：`WorkspacePanel.jsx:296` 直传同一 `/materials/upload`，不接入则「材料 tab 上传中删除」照样撞 rmtree）：pool handle 暴露 `beginUpload`/`endUpload` 给 App → WorkspacePanel props 接入，桌面/移动两壳共用；行为/source-guard/smoke 各加「材料 tab 上传中删除被拒”。
   - **waiter / upload token 身份 = pid（无 generation）**：会话常驻模型下实例只在删除/登出时卸载，无淘汰-重挂窗口；StrictMode 双挂载由「effect cleanup 注销 waiter、二次 setup 重注册」覆盖，`cancelWaiter(pid)` 幂等。**全计划不再有任何 generation 记账**（红队确认轮 LOW 3 定稿）。
   - **unmount 卫生**（删除/登出的卸载，迟到异步不得复活工作）：unmount cleanup（约 217 行）追加 abort controller、取消初始 conversation GET、清自有 timeout、注销 waiter。**初始 GET 隔离不能只靠 `mountedRef`**（StrictMode 旧请求 rejection 可能在二次 setup 后执行）：回调须校验 request token/controller 仍是当前那只 + `axios.isCancel`/AbortError 分支；timer/`maybeAutoStart` 入口 `mountedRef` fail-closed。
8. **ref 单一持有**：pool handle `{ triggerSystemTurn, dropPendingReviewTriggers, sendUserMessage, abortProjectWork, forgetProject, abortAll, flushPendingTriggers(pid), tryBeginDelete(pid), finishDelete(...), beginUpload(pid), endUpload(token) }`（用户三件套路由 active 实例；upload 两件套供 WorkspacePanel 经 App 接入）；ChatPanel 实例 handle 增 `abortActiveStream()` / `flushPendingTriggers()` / `cancelActiveUpload()` / `cancelPendingWork()`（幂等）/ `stopAcceptingWork()`。`dropPendingReviewTriggers` 清空队列后同步注销该实例 waiter。**poolRef 由 App 创建传给 MobileShell**，MobileShell 删内部 `chatPanelRef`（`MobileShell.jsx:22`）。
9. **显隐**：active 实例 wrapper `display: contents`（布局透明，ChatPanel 根 `flex-1 min-w-0 min-h-0` 直接作用于父容器，与现状逐像素一致）；非 active `display: none`；wrapper 零布局类。
10. **无项目态**：`projectId={null}` 实例 key 用 sentinel `'__no_project__'`，永不入成员/lease/waiter/upload/deleting 任何表。

### Task A0：poolCore 纯模块 + orchestrator

文件：新建 `frontend/src/utils/chatPanelPoolCore.js`（+ orchestrator 纯函数，可同文件或独立模块）

- 实现：会话成员集合（visit 挂载 / forget 移除）、lease 注册表（结构化四态、优先级、token 精确、cap 3）、waiter FIFO（分类 drain + visited 集 + `cancelWaiter` 幂等）、upload/delete token 协议、`computeMounted` 去重、change 通知；orchestrator 三函数（§设计 7）。全同步，无 React。
- **行为测试** `frontend/tests/chatPanelPoolCore.test.mjs` 至少覆盖：
  - acquire 四态与优先级（含「同 pid lease 后进入 deleting」「cap 满且 deleting」都返 `deleting`）；同 pid 二次 acquire `same_pid`；token 精确释放互不误伤；先清 CAS 再 release 的自唤醒重试成功；
  - waiter：FIFO 序；分类 drain 五分支处置正确；visited 集防自旋；「D 先 cap-wait → 转入上传 → A release」不自旋、waiter 被删、上传 finally 后补发；peek-成功才 dequeue；
  - upload/delete：`tryBeginDelete` 三态、双击不产生第二 token；`beginUpload` 拒 deleting；`finishDelete` 三态返回、`resume_required` 由 wrapper 补 flush 恰好一次、`stale` 零副作用；
  - `forgetProject` 原子清全部表；sentinel 不入任何表；`cancelPendingWork` 幂等（token/队列/waiter 重复清理无副作用——设计中已无 hold 计数）；
  - orchestrator：`shouldContinueAfterUpload` 拒绝分支；`runLogout` reject 仍调 `clearSession`；`runTwoPassShutdown` 两遍顺序。

### Task A1：ChatPanel 去「项目切换」逻辑 + 接入池协议

文件：`frontend/src/components/ChatPanel.jsx`

1. 删项目切换 effect 中 `previousProjectIdRef` 分支（约 121–134 行）及该 ref；挂载态 conversation 加载保留，GET 回调按 request token/controller 匹配 + `axios.isCancel`/AbortError 分支隔离，hold-free（无 retain 机制）、exactly-once 语义只针对 token 清理。
2. 新 props：admission 接口（结构化四态、同步前置、token 精确、先清 CAS 再 release）、waiter 注册接口、`beginUpload`/`endUpload`、`visible`（上升沿补滚底）。新 refs：`streamInFlightRef`、`uploadInFlightRef`、`uploadAbortControllerRef`、`acceptingWorkRef`、`autoStartQueuedRef`、`mountedRef`。
3. `sendMessage` / `sendUserMessage` / `triggerSystemTurn` / `flushPendingTriggers`：同步 admission 前置；`sendUserMessage` 忙/拒绝同步返回 false；系统轮按 local_busy/cap_full/deleting 分流；上传 `finally` 三步铁律；`sendMessage` 上传 await 后过 `shouldContinueAfterUpload`；fire-and-forget `void ...catch(log)`。
4. `project_created`：排队 `autoStartQueuedRef` 同步置位；fired 标记与 `onAutoStartConsumed(projectId)` 移到 lease 成功后；初始 GET 发现会话非空也调 `onAutoStartConsumed(projectId)`。
5. handle 增 `abortActiveStream()` / `flushPendingTriggers()` / `cancelActiveUpload()` / `cancelPendingWork()` / `stopAcceptingWork()`。
6. unmount cleanup（约 217 行）追加：abort controller、取消初始 GET、清自有 timeout、注销 waiter；timer/`maybeAutoStart` 入口 `mountedRef` fail-closed。
7. **不动**：`isActiveProjectRequest` / `activeProjectIdRef` 及 SSE handler 守卫（固定 projectId 下恒真，保留零风险）；`restoreInputForRetry` 双守卫；根 className（min-h-0 锁测）；parts/流式装配；乐观清空语义。

### Task A2：ChatPanelPool 薄壳

文件：新建 `frontend/src/components/ChatPanelPool.jsx`（forwardRef）

- 持 poolCore（`useRef` 创建）+ version state 桥接重渲染；props：`activeProjectId`、`panelProps`、`activeOnlyProps`、`pendingAutoStartProjectIds`、`onAutoStartConsumed(pid)`、pool 版回调、`onBusyIndicatorChange`（= lease 活跃集，供 Sidebar）。
- 渲染成员按 `computeMounted`；`key={pid}` 同 pid 绝不双实例；sentinel 规则§设计 10；`finishDelete` 的 `resume_required` 在此 wrapper 层补 flush。

### Task A3：App.jsx（桌面壳）接线

文件：`frontend/src/App.jsx`

1. `chatPanelRef` 改指 Pool（调用点语法零改动）；桌面 JSX `<ChatPanel/>`（约 466 行）→ `<ChatPanelPool/>`。
2. `loadProjects` 拆分 + `refreshProjectsSilently`（seq+uid 双守卫 + 乐观更新对账）。
3. 双契约回调适配器 + `currentProjectIdRef`（渲染期赋值）。
4. `pendingAutoStartProjectIds`（Set，函数式更新）+ `onAutoStartConsumed(pid)` + 三条清理路径。
5. `deleteProject`（约 248 行）：`tryBeginDelete` 三态分支 → `started` 时 `try:` `abortProjectWork` → DELETE `finally: finishDelete(token, {forgotten: 是否2xx})`。
6. 登出：Sidebar 发意图 → App `void runLogout({abortAll, requestLogout, clearSession}).catch(log)`（精确形状§设计 7，勿按简写实现）。
7. lease 活跃集传 Sidebar `busyProjectIds`。
8. **WorkspacePanel 上传接入 token 协议**：App 把 pool 的 `beginUpload`/`endUpload` 经 props 传给 WorkspacePanel（材料 tab 上传入口，`WorkspacePanel.jsx:296`），上传前 begin、finally end——与 ChatPanel 同一协议（§设计 7）。
9. **禁改区**：init effect 依赖仍 `[authUser?.uid, authUser?.must_change_password]`；`workspaceProjectId` 守卫链不动。

### Task A4：MobileShell.jsx（移动壳）接线

文件：`frontend/src/components/MobileShell.jsx`

1. 删内部 `chatPanelRef`（22 行）改用 App 传入 poolRef；`<ChatPanel/>`（约 104 行）→ `<ChatPanelPool/>`；池状态 App 单一持有。
2. **双契约回调分别接收并路由**（工作区路径 active 版原签名、Pool 用 pid 版，不许并成一个）；登出意图转发不自 await。
3. 抽屉内 Sidebar 加 `busyProjectIds`。
4. **禁改区**：壳根手势/`touchAction`/`min-h-0` 链/零 transform/常驻挂载语义；mobileShell.source 断言更新保语义不放宽。

### Task A5：Sidebar 生成中指示 + 登出意图化

文件：`frontend/src/components/Sidebar.jsx`

- 项目行标题旁：`busyProjectIds` 含该项目 → `<span className="w-1.5 h-1.5 rounded-full bg-abright animate-pulse" />`（token 类，无 emoji）。副标题逻辑不动。
- 登出按钮改发意图回调（不再自己 `await /api/auth/logout`，编排在 App）。

### Task A6：后端 SSE 执行池扩容

文件：`backend/main.py:951`

- `max_workers=8 → 16`。注释**如实**写：「前端 cap=3 只约束单标签页；多用户/多标签页仍可能逼近 16，届时长流串行化（排队不丢）——全局容量治理是后置项」。不许声称容量问题已解决。

### Task A7：后端删除端点竞态守卫 + chat 锁后复验

文件：`backend/main.py`（`delete_project` 端点，约 1341 行）、`backend/chat.py`

- 现状：删除不取任何锁直接 `rmtree`，与在途 chat 轮 / 独立审查写报告并发时后者读写已删目录。
- 端点改同步 `def`（FastAPI 默认线程池，**acquire/删除/release 全程同一线程**——RLock 跨线程 release 会失败）。**锁序 = request → review，与 chat 现状一致**（chat.py:3466 先持 request、2692 再非阻塞取 review）：

```python
DELETE_PROJECT_LOCK_TIMEOUT_SECONDS = 5.0  # 具名常量，测试可注入缩短

lock = _get_project_request_lock(scope.lock_key)      # chat.py 同一注册表
if not lock.acquire(timeout=DELETE_PROJECT_LOCK_TIMEOUT_SECONDS):
    raise HTTPException(409, detail="项目正在生成内容，已停止的话请稍等几秒再删")
try:
    review_lock = get_independent_review_lock(scope.lock_key)
    if not review_lock.acquire(blocking=False):
        raise HTTPException(409, detail="项目正在进行独立审查，请等审查结束后再删除")
    try:
        scope.engine.delete_project(scope.project_id)
        ...
    finally:
        review_lock.release()
finally:
    lock.release()
```

- 409 的 HTTPException 必须穿透宽泛 `except Exception`（加 `except HTTPException: raise`）。
- **chat 双重复验（红队 MEDIUM×2）**：①`/api/chat/stream` 的 `generate()` 在调 `get_chat_handler`（`main.py:1465`）**之前**先预检项目存在——否则 DELETE 完成后才开始执行的旧 StreamingResponse 会为已删项目**新建并缓存 handler**（永久无用 + 覆盖共享 converter）；②`chat_stream` 拿到 request lock 后、构建 prompt/调 provider **之前**再复验（`get_project_record`/`get_project_path`）——友好报「项目已被删除」终止，不调 provider 不烧额度。测试断言两层：provider 未调用 **且 handler 缓存未新增**。
- **明确不覆盖**：材料端点不持锁——delete-vs-upload 由前端 token 协议兜底，后端加锁记 worklist。
- 测试（`tests/test_main_api.py`）：空闲删除成功；chat 锁占用 → 409（注入短 timeout）；review 锁占用 → 409；汇报轮与 DELETE 交错不死锁；三路径后另一线程可再 acquire 两把锁；409 不被吞成 500；**锁后复验：排队请求在项目被删后不调 provider**。

### Task A8：后端 per-user 共享态并发加固（红队 BLOCKER/HIGH，**先于前端并发上线**）

文件：`backend/skill.py`、`backend/material_conversion.py`

1. **registry.json 并发安全**：`SkillEngine` 加 per-engine `threading.RLock`；`_load_registry` → 改 → `_save_registry` 的**完整 RMW 事务**（`_touch_project`、create、delete、`add_materials`/`remove_material` 触发的 touch）全部持锁；`_save_registry`（skill.py:3121，现直接 `write_text`）改 **temp + `os.replace` 原子写**。并发流/后台上传（Block A 明确引入的场景）不再丢更新、读者不再见半截 JSON。
2. **MaterialConverter 缓存锁全局化**：per-key 锁从 converter 实例属性改**进程级注册表按 `(cache_dir.resolve(), key)` 键**（**必须 `resolve()` 规范化**——相对/绝对路径别名会造出两把锁，红队确认轮 LOW）——per-(uid, project) handler 各自 `set_material_converter` 会造成多个 converter 实例共享同一 cache 目录，实例级锁互不互斥（refcount/`.refs` sidecar 并发窗口）。`ChatHandler` 接线语义不变。
3. 测试（`tests/test_skill_engine.py` / `test_material_conversion.py`）：barrier 并发 `_touch_project` 无丢失更新；并发 create/delete registry 一致；持续读者只见合法 JSON；**converter 互斥测试要用 barrier/仪表化证明临界区 `max_concurrent == 1`**（只查最终 refs 值受合法调度影响、证明不了无重叠），另单测两个不同 material 并发 retain 不丢。
4. **禁改区**：converter 的 DI 纯边界（不 import chat）、`cache_key_from_sha256` 契约、R3 用户写 `_USER_WRITE_EXECUTOR` CAS 链不碰。
5. **部署假设明示**：per-engine RLock 与进程级锁注册表只在**单进程单 worker**（现 systemd 配置）下成立——多 worker 需跨进程锁，超本批范围；A8 代码注释与本 plan 都写明。

### Block A 测试

- **行为测试（主证明）**：`chatPanelPoolCore.test.mjs`（Task A0 清单）+ 纯 helper（trigger admission 分类）+ orchestrator 三函数。
- 结构 source-guard：新增 `chatPanelPool.source.test.mjs`（Pool 接线/wrapper display/sentinel/双契约并存/admission 同步前置/try-finally release/先清 CAS 再 release/上传 finally 三步/void-catch/三处 orchestrator 委托 + `clearSession` 绑定）。
- **既有 source-guard 逐个更新（保语义、验证 Pool→ChatPanel 转发链，不许只放宽正则）**：`stageAdvanceControl.test.mjs:123`（handle 形状 + 新成员）、`autoStartInterview.source.test.mjs:54`（两壳接线走 Pool + Set 化 autoStart + queued/fired 分离）、`fileLinks.source.test.mjs:85`、`mobileShell.source.test.mjs`、`sidebar.source.test.mjs`（登出意图化）、**`workspacePanel.source.test.mjs:94`**（红队 MEDIUM：现锁 `loadProjects(createdProject.id)` 在 dirty-guard `proceed` 内——改为锁「项目选择 + 静默对账仍在 `proceed` 内、`attemptLeave` 之后」的真不变式）、**`independentReviewDrawer.source.test.mjs:183`**（红队确认轮 MEDIUM：精确匹配旧三成员 handle 形状，A1 加五个方法后必红——更新为断言旧三项仍在 + 新增方法完整，不许改模糊匹配）；`appInitGating.source` 增「初始化之外无顶层 loading 置位」+ 静默刷新 seq/uid 守卫断言。
- 应全绿零改动（红了=破坏禁改区，修实现别改测试）：`chatPanelComposerClear.source` / `chatPanelParts.source` / `mobileViewport.source`。
- 手工 smoke（写进 PR）：A 发送 → 切 B → A 继续（圆点亮）→ B 发送 → 切回 A 实时内容 → A 落盘；后台 A 上传完成不污染 B 视图；上传中的 A 删除被拦；删除生成中项目（桌面+移动，409 路径实例不丢、pending trigger 不抢跑）；上传进行中登出无幽灵 stream；登出杀全部流（POST 失败也进登录页）；第 4 项目被 cap 拦 + 排队审查汇报自动补发；快速连开两项目各自 auto-start；移动端全流程。

---

## 3. Block B：停顿自愈（参数容错 + DSML 泄漏检测 + 历史清理）

### Task B1：工具参数键名归一 + 直接索引参数校验

文件：`backend/chat.py`（`_execute_tool`，约 4792 行起）

1. `args = json.loads(...)` 之后：先验顶层是 object（非 dict → 友好 error「参数必须是 JSON 对象」），再 `args = self._normalize_tool_arg_keys(func_name, args)`：camelCase→snake_case 纯算法转换，结果 ∈ 该工具 schema properties 且 snake 版缺失 → 改名；已有 snake 版丢弃 camel 键；未知键保留。
2. **参数名/required 集合从 `_build_tools()` 派生**——handler 实例级惰性缓存（首次用到构建），不在模块 import 期。
3. 必填校验前置——**范围只圈今天会 KeyError 的直接索引参数**：write_file `file_path`/`content`、read_file `file_path`、read_material_file `material_id`、web_search `query`、fetch_url `url`。缺失 → dispatch 前返回 `{"status":"error","message":"缺少必需参数 file_path，请按工具定义补全参数后重新调用"}`。**明确不强校验**：`advance_stage`（红队更正措辞：`_tool_advance_stage` 对 `None` 有**自带的友好校验**（chat.py:4948 起对 checkpoint_key/action 自报错、无 KeyError），前置强校验属重复且会改变现有文案）、`edit_file`（`.get("")` 默认自有文案；**`new_string=""` 是合法删除语义**）、`append_report_draft`/`create_chart`/`create_diagram`（自有校验）。
4. 一致性测试：①直接索引校验集 ⊆ schema `required`；②归一化 properties 集与 `_build_tools()` 一致。
5. 不新增 `except KeyError` 分支。
6. **禁改区**：provider 序列化四件套不碰；`advance_stage` S0 白名单与 checkpoint 语义不动。

### Task B2：DSML 泄漏检测 → self-heal → 持久化全链净化 + 历史存量清理

文件：`backend/chat.py`、`backend/main.py`（GET /conversation 消费方，见第 5 条）

1. 模块级模式（覆盖闭合标签）：
   - 检测：`_DSML_MARKUP_PATTERN = re.compile(r"</?[|｜]{1,2}\s*DSML\s*[|｜]{1,2}")`
   - 净化 `_strip_dsml_markup(text)`——**无命中原样返回输入（逐字节相等），只有命中才重建**；命中时按行删除含标记整行、保留相邻存留行换行边界；全删空返 ""。
2. 轮末检测+重试：两处 turn-end 自愈链（sync 约 3395–3420、stream 约 3180–3210，锚点 = `candidate_message = collected_message["content"]` 之后、self-correction 检查之前）：命中且 `dsml_leak_retries < MAX_DSML_LEAK_RETRIES(=1)` → 计数+1、`[self-heal]` 日志、**append 进 `current_turn_messages` 的 assistant 用净化版**（空则占位「（上一条输出包含工具标记，已作废）」）+ 纠偏 user 消息、`continue`。**不许把原始 DSML append 进 `current_turn_messages`**（它是 `_build_message_parts` 约 7126 行的输入、parts 是前端首选渲染源）。
3. 重试耗尽仍命中 → finalize 前 `assistant_message` 过净化；净化后为空 → 人话兜底「刚才的输出出现了格式错误，本轮已终止，请重新发送一次你的指令。」
4. **防御纵深**：finalize/`_build_message_parts` 对全部入 parts 文本段与 `assistant_message` 统一过净化（无命中=无操作）——覆盖 tool_calls-present 夹带旁路（只净化不重试）。
5. **历史存量清理（红队 HIGH：事发用户的 conversation.json 已带 DSML，且历史 assistant 原文会回传 provider 诱导复发）**：抽**共用净化 helper**（对历史 assistant `content` 与 text `parts` 过 `_strip_dsml_markup`，干净文本 no-op），**两个消费方都必须显式调用**——①`_load_conversation` 白名单重建（覆盖 provider 历史与 parts）；②**`GET /conversation` 端点（`main.py:1389`）——它直接 `json.load` 文件、根本不走 `_load_conversation`**（红队确认轮实锤，只改 load 会漏前端显示）。**不做一次性磁盘迁移**（load/GET 层净化即可，下次 re-save 自然干净）。
6. 测试：mock 首轮返回 §0 真实多行样本 → 断言纠偏+重试；二次仍泄漏 → **读持久化 conversation.json 断言 `content` 与 `parts` 全部**无 DSML/`filePath`/opener/closer 残留；正常正文含「DSML」字样无竖线定界不误伤；**无 DSML 的 content/parts 经净化链逐字节不变**；tool_calls-present 夹带 → finalize 净化；**预置带历史 DSML 的 conversation.json → `_load_conversation` 后 provider 消息净化 + 真实 `TestClient.get(".../conversation")` 响应净化**（两个消费方分别断言，不许只测 handler load）；DeepSeek targeted 用例不回归。

---

## 4. Block C：DL 内部引用标记三层收口

### 共享语法（先定义，两个消费方对齐）

新常量放 `backend/report_quality.py`（叶子层，`report_tools` 可安全 import）：

```python
# 对齐 skill.py:_DL_REFERENCE_GROUP_PATTERN 的引用 token 语法（允许无年份 [DL-001]、斜杠合并 [DL-2026-01/06]），
# 并允许一个方括号里逗号/顿号连写多个 token；含任何非 token 文本（如 [DL-2026-01 型设备]）不命中。
# 分隔符两侧只吃水平空白（[ \t]*）——\s 会跨换行吞段落边界。
INTERNAL_CITATION_RE = re.compile(
    r"\[DL-(?:\d{4}-)?\d+(?:/\d+)*(?:[ \t]*[,，、][ \t]*DL-(?:\d{4}-)?\d+(?:/\d+)*)*\]"
)
```

`report_tools` import 该常量（加跨模块一致性 source-guard）。剥除只吃**水平**前导空白：`re.sub(r"[ \t]?" + INTERNAL_CITATION_RE.pattern, "", text)`——绝不用 `\s?`（吃换行粘段）。

### Task C1：SKILL.md S4 规则（源头）

文件：`skill/SKILL.md`（「### S4 报告撰写」段 bullet 追加）

- 「正文与附录**禁止出现** `[DL-...]` 内部编号标记——那是 `analysis-notes.md` 专用的内部追踪记号，不是给读者看的引用格式」
- 「正文需要交代出处时用文字表述（如『据国家数据局 2024 年 12 月发布的指导意见』），或在文末『参考资料』章节集中列出」

**禁改区**：S2 data-log 条目格式段、S3 引用要求段一个字不动（`_EVIDENCE_MARKERS` 与 `test_skill_md_datalog_examples_all_recognized_as_valid_sources` 锁测）。改后 `test_packaging_docs.py` + `test_skill_engine.py` 全绿。

### Task C2：审查 grounding 点名（模型在环）

文件：`backend/report_quality.py`、`backend/independent_review.py`

1. `_PLACEHOLDER_PATTERN` 增加分支（复用 `INTERNAL_CITATION_RE.pattern`，捕获组结构保持，`scan_placeholders` 零改动覆盖）。
2. `build_placeholder_grounding` 文案加「内部资料编号标记（[DL-...]）」类别。
3. 审查 prompt 维度⑤（`### 5. 语言专业性与去 AI 味`，约 62 行）追加：「正文出现 `[DL-...]` 等内部资料编号是半成品痕迹，必须指出并要求改为正式引用表述或移入参考资料。」
4. 测试口径：`test_no_charts_system_prompt_verbatim_unchanged` 比运行时常量、自动通过——**保留原断言不动**，另加：维度⑤含 DL 规则句、`len(INDEPENDENT_REVIEW_ANCHORS) == 5`、无图不出现 `CHART_REVIEW_ADDENDUM` 语义。
5. **禁改区**：5 维锚点契约、UNTRUSTED_DATA 框定/中和/50 行上限不动。

### Task C3：导出剥除（确定性兜底）

文件：`backend/report_tools.py`

1. 纯函数 `_strip_internal_citation_markers(text)`（共享语法 + 水平空白规则）。
2. `build_export_markdown`（约 454 行）：`title` 与 `body` 先 strip 再走现有链（`_neutralize_raw_openxml` 之前）。
3. **禁改区**：封面 raw openxml 常量、`_neutralize_raw_openxml`、`_sanitize_xml_text`、TOC 固化链不碰。

### Block C 测试

- `tests/test_report_quality.py`：命中——`[DL-2026-01]`、`[DL-001]`、`[DL-2026-01/06]`、`[DL-2026-01、DL-2026-03]`、行内连写；不命中——`[DL-2026-01 型设备]`、`[注]`、`[1]`、跨换行 `[DL-2026-01\n、DL-2026-02]`；grounding 文案含新类别。
- `tests/test_report_tools.py`：strip 变体全覆盖 + 段落边界（剥后换行保留不粘段）+ 集成断言（62 处样式正文 → 零残留；**空格断言收窄到常规单空格样例**，不承诺全局无双空格、不做全局 Markdown 空白重写）+ 既有导出/TOC 全绿 + 跨模块正则一致性 guard。
- `tests/test_independent_review.py`：维度⑤新句、anchors==5、无图 addendum 条件语义。

---

## 5. Block D：研究类写作纪律 + worklist

### Task D1：S4 写作纪律注入（R5 通道）

文件：`backend/skill.py`

1. 新常量 `RESEARCH_WRITING_DISCIPLINE`（~150 token，中文）：论点先行（每章每节第一句是判断不是背景）；证据服务论点（引用事实后必须紧跟解读，禁止连续两段以上纯事实堆叠）；每章有 So-What（决策含义/行动指向）；深度优先于覆盖（三条证据链打穿胜过十条资料罗列）。
2. `build_methodology_block`（约 3027 行）：`stage == "S4"` 且 `METHODOLOGY_TONE[project_type] in {"analytical", "specialized"}`（strategy-consulting / market-research / due-diligence / specialized-research）→ `instr += "\n\n" + RESEARCH_WRITING_DISCIPLINE`。structural / bid 不注入。
3. **token 预算测试改造**：扩成 **type×stage 矩阵**（至少：四个研究类 S4、technical-bid S1、management-document 最长条款组合），全部 ≤2000，**不许调阈值**；超了裁纪律文本。
4. **禁改区**：`__methodology_snapshot`、`parse_and_sanitize_methodology`、declare/adhere 既有文本、条款样式注入链不动。

### Task D2：worklist 条目

文件：`docs/current-worklist.md`

新增：
- 「深度研究质量拉升专项（谭进 0717 反馈 #3 完整解）：S3 分析深度要求、S4 行业洞察 prompt 工程、可能的多轮 deepen 机制——待专项 spec」
- 「材料端点 per-project 加锁（delete-vs-upload 后端侧防护）——本批前端 token 协议兜底」
- 「ChatPanel 池按需淘汰——本批刻意不做（会话内常驻）；若单会话项目数真实增长，先量数据再设计」
- 「计费 in-flight 预扣——日额度并发下软帽（见本 plan §9），量级可接受即不做」

### Block D 测试

- `tests/test_skill_engine.py`：analytical/specialized S4 含纪律、S1–S3 不含、structural/bid 全阶段不含、token 矩阵绿、既有方法论测试全绿。

---

## 6. 全局禁改区（每 task 完成后自查）

1. DeepSeek 官渠兼容：provider message / tool-call / `reasoning_content` / `tool_choice` 序列化零改动（B2 只增删 `{role, content}` 文本条目 + load 层净化文本值，模式同既有 sanitizer）；`test_deepseek_compat_helpers_match_chat_helpers` 绿。
2. finally-yield 心跳链路结构不动（后端 generator；前端 startStream 的 try/finally 是普通 async 函数，不在此禁区）。
3. 多租户：不触碰 `require_project` / `tenant_project_key` 键化；A7 用同一复合键锁；`test_tenant_isolation.py` 绿。
4. 前端既有锁测语义不放宽：min-h-0 / init effect deps / composer clear 双守卫 / parts 装配 / 零 transform / paletteGuard。
5. 计费不变式：`finally: response.close()` / settle-once 不碰。
6. trust boundary：ATTACHMENT_DATA / UNTRUSTED_DATA 框定与中和不碰。
7. executor 隔离：A7 删除临界区跑 FastAPI sync 线程池（同线程 acquire/release），绝不进 `_CHAT_STREAM_EXECUTOR` / `_USER_WRITE_EXECUTOR`；A8 的 engine 锁不得在 chat generator 线程外释放它未持有的锁（普通 RLock 语义自然满足）。

## 7. 回归与验收

- 后端 `python -m pytest tests/` 全绿（基线约 1821）；前端 `node --test tests/` 全绿（基线约 587）+ `npm run build`。
- 真模型 GUI E2E（人工）：Block A smoke 清单 + 研究类报告 S4 无 DL + 导出 docx 检查 + 移动端并发流程。

## 8. 部署（kr-web-01）与回滚

- 后端 file-push：`backend/chat.py`、`backend/skill.py`、`backend/material_conversion.py`、`backend/report_quality.py`、`backend/report_tools.py`、`backend/main.py`、`backend/independent_review.py`、`skill/SKILL.md` + systemd 重启（sha256 核验，认 `systemctl show consulting-report -p WorkingDirectory`）。
- 前端：本地 build → tar → `dist.new` + `--strip-components=1` → 原子 swap（绝不在 frontend/ 直接解包）。
- 回滚点：`/opt/cra-rollback-20260718/` + `frontend/dist.old`。
- 无 DB 迁移、无配置改动、无依赖新增。

## 9. 已知限制（接受项，写进 cutover）

- 后台流继续烧该用户额度——设计使然，日额度兜底；**如实定性：日额度是软帽**（`MeteredManagedClient._reserve` 只读已结算用量、无 in-flight 预扣——B2 spec 既有接受项），并发 3 流最坏超额 ≈ cap 触线时已在途的 2 轮成本（试用规模可接受；in-flight 预扣记 worklist）。
- 会话内实例常驻不淘汰——上限=用户项目总数（试用实数据 ≤3/人）；内存增长有界，淘汰机制刻意不做（worklist 记数据驱动的后置项）。
- DSML 已流出到屏幕的乱码不回收（轮末机制），reload 显示净化版；provider 重试历史看净化版（保真度换持久化清洁，刻意取舍）。
- DL 剥除只在导出层，预览/草稿仍可见（S5 审查负责源头清理）。
- `MAX_CONCURRENT_CHAT_STREAMS=3` 单标签页常量不配置化；多标签页/多用户全局容量后置（A6 注释如实声明）。**红队确认轮 ROI 备注**：用户项目数 ≤3 时 `cap_full`/waiter 路径几乎不可达——保留它是因为该协议已收敛、隔离在纯模块且有行为测试，删除反而要重开一轮设计；若实施中该部分成为负担，可与用户重议降级（同 pid CAS + deleting 门保留、去 cap/waiter）。
- A8 并发安全只覆盖**单进程单 worker** 部署（现状）；多 worker 是明示的不支持项。
- 删除生成中项目：前端先 abort，后端 chat/review 双锁 409 + 锁后复验兜底——极端时序下需数秒后重试；上传中项目删除被前端 token 协议拦（材料端点后端锁记 worklist）。
