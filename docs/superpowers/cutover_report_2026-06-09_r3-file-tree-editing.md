# Cutover Report — R3 工作区文件栏重做 + 预览框可编辑

- 日期：2026-06-09
- 分支：`feat/workspace-file-tree-and-editing`（未 push，等用户跨设备同步指令）
- Spec：`docs/superpowers/specs/2026-06-08-workspace-file-tree-and-editing-design.md`
- Plan：`docs/superpowers/plans/2026-06-08-workspace-file-tree-and-editing.md`

## 交付范围（①文件栏 + ②可编辑预览）

- **文件栏**：后端 `list_workspace_files` 给结构化语义（`group/stage/editable/mtime_ns`），前端 `utils/fileTree.js` 分组 + 中文名 + 当前阶段置顶；tracking 组折叠置底；退役文件不显示，`materials/` 跳过。
- **预览框双模式**：8 个白名单文件支持「编辑（raw textarea）→ 保存」；`utils/fileEditState.js` 状态机 + dirty `guardLeave` 覆盖 切文件 / 切 tab / 切项目 / 新建项目 / 刷新关窗 / saving 各离开路径。
- **用户写接口**：`POST /api/projects/{id}/files/{path}` `{content, base_mtime_ns}`——`validate_user_write` 白名单（默认 deny）+ per-project 锁内 mtime CAS + `os.replace` 原子写 + 异常分流 400/403/404/409/422/500。
- **D6 `review_stale` advisory**：两份**有效**审查报告（`_has_effective_review_reports`，非 scaffold 模板）+ `draft_mtime > min(两份报告 mtime)` 即标，**不** gate 在 `review_passed_at`，不硬阻 S6/S7。

## 对 spec 的已核验偏离 / 强化

- §6.1「GET 读在锁内」→ 实测 `chat_stream` 整轮持锁，GET 进锁会冻结预览整轮；改为 **GET 不持锁 + stat-before-read**（最坏=保存时安全 409，绝不静默覆盖），POST 仍持锁。
- **`write_file` 原子化 + canonical draft 直写归一**：`write_file` 改 temp + `os.replace`；canonical draft `edit_file` 原本直接 `draft_path.write_text` 绕过它，改走 `write_file`——所有 AI 写可编辑文件单点原子化、消除 torn read，使 GET 不持锁成立（顺带 crash-safety）。
- §5.4 `review_stale` **gate 在有效报告**，不只判存在——避开 `create_project` scaffold 的 independent-review/lint-report 模板误判。
- §7.2 脏离开确认 **v1 三按钮「保存 / 放弃修改 / 取消」**（应用户要求做进 v1）：延后动作模式——离开动作挂起，「保存」存成功后再离开（撞 409 不离开+给重载入口）、「放弃修改」弃改后离开、「取消」留下；Esc 等同取消。`beforeunload`（整页刷新/关窗）受浏览器原生限制仍二选一。

## Review 闭环：codex 双轨独立（spec 轨 + quality 轨，不合并）

按项目规约（`CodeProject/CLAUDE.md`）走 **spec + quality 双轨独立 review**。质量轨各自独立 session 比合并审更狠——挖出两个合并审漏掉的真 BLOCKER：

**spec 轨**：后端 `SPEC-COMPLIANT`、前端 `SPEC-COMPLIANT`（逐条覆盖、无遗漏、无 over-building）。

**quality 轨（对抗式红队，现实闸门三级标注）**：

1. **后端 BLOCKER — RLock 跨线程互斥失效**：`chat_stream` 是**同步 generator**，Starlette 用 anyio 默认池 `iterate_in_threadpool` 逐 chunk 迭代它，`with request_lock:`（`threading.RLock`）的 owner 是某 anyio worker 线程。用户保存原用 `run_in_threadpool`（同一默认池），可能复用到 owner 线程 → RLock **重入放行** → 保存绕过锁、CAS 形同虚设（正常单用户路径：AI 流式回复中用户编辑正文并保存）。
   **修复**（`8f06c81`，不动 chat_stream）：保存临界区改跑**专用** `ThreadPoolExecutor`（`_USER_WRITE_EXECUTOR`），其线程绝不是 chat 的 anyio worker → 保存线程 ≠ RLock owner → `acquire` 必真正阻塞到 chat 释放，互斥恢复。源码守卫测试锁死不得回退 `run_in_threadpool`。
2. **前端 BLOCKER — 进入编辑的异步竞态**（两轮才真闭合）：点「编辑」A 时异步 GET 取 base，await 期间用户切到 B，结果 A 的内容进了 B 的编辑器、保存打错文件。
   - R1 尝试（`50ab90d`）：`currentFileRef` 比 `currentFile`——被 quality 轨证伪：切文件本身也异步（`loadFile` GET 回来才 commit `currentFile`），pending 导航看不到。
   - R2 尝试（`21ff6ae`）：`selectionSeqRef` epoch——仍有洞：点 B 后 currentFile 未 commit 时再点「编辑」，捕获的是已自增的 seq，之后不再变，仍误入。
   - **根因修复**（`d420dff`）：`WorkspacePanel.loadFile` 把 `setCurrentFile(path)` 提到 GET **之前同步执行**，pending-navigation 窗口从根上消除；epoch 保留作第二道。quality 轨复审 `APPROVED`。

**两轨最终 `APPROVED`**（后端 quality `8f06c81` 后 APPROVED；前端 quality `d420dff` 后 APPROVED）。

**采纳的 NIT 硬化**（`ec42369`）：后端 source-assertion 锁专用池；前端 `latestFileRequestRef` 丢弃乱序 content GET。

**过现实闸门保留不改的 NIT**（单机 / 单用户 / 输入可信 / 失败=重跑）：
- `list_workspace_files` 的 `except OSError: continue` 偏宽——保留：单机真实失败是 Windows 并发删除/占用（delete-pending 抛 PermissionError，窄到 FileNotFoundError 会漏），对自己家目录 ACL 误配近零概率。
- 409 不回传 `current_mtime_ns`——保留：前端撞 409 走重新 GET（`reloadAfterConflict`）拿最新 mtime，不依赖响应体。
- createProject 守卫后 `return true` 早于切换完成、用户取消则项目已建但不切走——保留：项目确已创建，硬等切换完成会让新建弹窗卡在 dirty 决策、体验更差。
- `editDraft` 改回原文仍判 dirty（离开仍弹三按钮）——保留：纯 UI 误报、无数据风险；改需动 `fileEditState` 纯函数语义 + T6 测试。
- **`chat_stream` 仍跨 `yield` 持 thread-owned `RLock`**（既有结构脆性）：本次专用池隔离了用户保存、消除其重入绕过；但若同项目出现并发 sync stream / anyio worker 抢占，generator 仍可能在非 owner worker 上释放锁。**这是既有债、非 R3 引入**，正解是把 chat stream 改 async generator + `asyncio.Lock`——属独立大改，未纳入本次（单机单用户下不构成普通路径 BLOCKER）。

## 测试

- 后端：`tests/test_skill_engine.py`（语义/白名单/`validate_user_write`/`review_stale`）、`tests/test_main_api.py::R3FileApiTests`（GET/POST 全状态码 + 锁串行化 + 持锁期 mtime 变更→409 + 专用池 source-guard + `mtime_ns` str + 拒 number）。R3 相关后端 **281 passed + 18 subtests**，0 fail。
- 前端：`fileTree` / `fileEditState` 纯函数；`filePreviewPanel.source` / `workspacePanel.source` source-guard。全量 **296 pass / 0 fail**。
- 构建冒烟：`npm run build` 成功（仅既有 chunk>500kB 警告）。
- **已知 pre-existing 失败（非 R3）**：`tests/test_lint_report.py::test_lint_report_top_n_truncation`——`quality_check.ps1` 在本地 Windows PowerShell 下不出「仅显示前 30 条」截断标记。R3 未触碰 lint 路径，独立追踪。

## 未做（记后续）

- R3③：图片附件按 model 分流、新建项目表单整理、`project-overview.md` 结构化编辑。
- v2 富文本编辑器；`review_stale` 硬门禁；全局 `allow_origins` 收紧 / 写接口本地 token（D7）。
- `chat_stream` async 化（消除上述既有结构脆性）——独立改造。
- **用户侧 GUI E2E 手测**（非阻塞，待用户）。

## Commit 序列（本分支，base `6f80a07`）

| commit | 内容 |
|---|---|
| `336504a` | T1 文件语义 + 用户可编辑白名单门禁 |
| `f291a14` | T2 结构化 GET + 原子 `write_file` + canonical draft 路由 |
| `427dc18` | T3 POST 用户写（白名单 + mtime CAS + 原子替换）|
| `ece4896` | T4 `review_stale` advisory |
| `e9c3048` | 后端审修复：`UserWriteForbiddenError` 区分领域拒写 vs OS 写失败 |
| `ff7df74` | T5 `fileTree` util |
| `5ff58b2` | T6 `fileEditState` util |
| `a35243a` | T7 `FilePreviewPanel` 重做 |
| `6e0f2f0` | T8 WorkspacePanel/App wiring |
| `50ab90d` | 前端审修复 R1：createProject 守卫 + 进入编辑竞态(v1) + Esc + 409 重载 |
| `21ff6ae` | 前端审修复 R2：selection epoch（竞态二修）|
| `8f06c81` | 后端 quality 修复：保存临界区改专用池（RLock 重入）|
| `d420dff` | 前端 quality 修复：同步提交选择（竞态根因）|
| `ec42369` | quality NIT 硬化：专用池回归守卫 + 丢弃乱序 content GET |
