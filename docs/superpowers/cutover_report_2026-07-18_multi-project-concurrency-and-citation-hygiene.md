# Cutover：多项目并发、停顿自愈与引用卫生

日期：2026-07-18
状态：已实现、全量回归通过、独立红队 APPROVE、已部署 `kr-web-01`

## 用户结果

- 切换项目不再中止正在生成的回答；回到原项目可继续看到流式进度与完整聊天状态。
- 同一项目仍只允许一条生成流；单标签页最多同时生成 3 个项目，超出的系统轮按 FIFO 等待。
- 后台生成项目在侧栏显示 pulse 状态，不会覆盖当前项目的 workspace、材料或输入框。
- 删除、上传、登出与系统触发在并发下有明确的 token/锁协议，不再靠 React loading 状态猜测。
- 工具参数大小写风格不一致时可在 schema 范围内自愈；模型泄漏 DSML 控制文本时会纠偏、重试并净化历史。
- `[DL-...]` 只作为内部证据编号，不再进入报告正文/附录；即使模型漏控，导出层仍会剥除。
- 研究型报告在 S4 得到更明确的证据综合、行业洞察和非拼贴写作约束。

原计划：`docs/superpowers/plans/2026-07-18-multi-project-concurrency-and-citation-hygiene.md`。

## 实现摘要

### A. 多项目并发与共享状态

- `frontend/src/utils/chatPanelPoolCore.js` 是无 React 依赖的同步真值：members、stream lease、
  FIFO waiter、upload/delete token、busy pid 与清理协议都在这里。
- `frontend/src/components/ChatPanelPool.jsx` 会话内常驻已访问项目；隐藏项目仅改变显示，不卸载
  `ChatPanel`。桌面 `App` 与移动 `MobileShell` 共用同一协议。
- 每个 ChatPanel 用 ref CAS 护住本地 stream/upload；全局 lease 在本地 CAS 清零后精确释放。
- App 对项目列表做 seq+uid 守卫的静默刷新；后台项目回调按 pid 更新，只有活动 pid 能刷新当前 UI。
- Sidebar 登出只发意图；App 先停止接受新工作，再中止全部 stream/upload/pending，最后清会话。
- 后端聊天 SSE 专用 executor 从 8 扩到 16；这是单 worker 内的容量，不代表支持多 worker。
- DELETE 端点使用 request→review 固定锁序、5 秒超时 409、异常透传与 finally 解锁；聊天在创建
  handler 前和拿 request lock 后各复验一次项目存在，已删项目不调 provider、不重建 handler。
- `SkillEngine` registry、`materials.json` 与 material conversion cache refs 使用锁内原子 RMW；
  conversion cache 锁由规范化 `(resolve(cache_dir), key)` 进程级注册表共享。

### B. 参数与 DSML 自愈

- 工具参数先验证 object；参数索引从 `_build_tools()` schema 惰性生成，snake_case 原值优先，
  只在 schema 已声明字段内接受 camelCase→snake_case。
- 直接 `args[...]` 的参数集合与 schema required 有一致性测试，缺参先返回友好工具错误。
- 同步和流式输出都检测真实 DSML 定界结构：首轮泄漏会注入纠偏并重试一次；第二次仍泄漏则
  只给用户安全兜底，不持久化控制文本。
- finalize、message parts、conversation load、provider 历史和 `/conversation` GET 全部走同一净化链；
  无 DSML 的普通文本逐字节不变。
- DeepSeek 的 `tool_choice`、`reasoning_content` 与 null 字段序列化边界未改。

### C. DL 内部引用收口

- `backend/report_quality.py:INTERNAL_CITATION_RE` 是唯一共享正则，支持无年份、斜杠合并、
  逗号/顿号多 token；分隔符只吃水平空白，不能跨换行吞段落。
- `skill/SKILL.md` 在 S4 明确要求把内部编号改写为正式来源表达。
- 独立审查维度⑤与 placeholder grounding 会点名正文/附录中的 DL 痕迹。
- `backend/report_tools.py` 在 raw-openxml 中和前剥除标题与正文 DL 标记，保留段落换行。

### D. 研究写作纪律

- `backend/skill.py:RESEARCH_WRITING_DISCIPLINE` 经现有 methodology block 注入。
- 只命中 analytical/specialized 四类报告的 S4；S1–S3、结构型报告和 technical-bid 不注入。
- type×stage token 矩阵全部保持 methodology block ≤2000 tokens，没有放宽预算。

## 独立红队

最终红队只读审查覆盖 Block A–D 与禁改区，过程中发现并修复三处真实竞态：

1. `useImperativeHandle` 每次 render 生成新对象，callback ref 会 null/rebind，pool cleanup 误删刚入队
   waiter。修复为全生命周期稳定 handle，方法通过 ref delegate 更新。
2. DELETE 失败/409 后 `finishDelete` 同栈恢复时，ChatPanel 仍拿到旧 `deleting=true` prop，待处理
   系统轮永久沉默。修复为 `acquireStreamLease` 是删除准入唯一同步真值。
3. forget 到 React 真正卸载之间，旧 handle 可为已删 pid 新建 ghost lease/upload。修复为 stream/upload
   要求 pid 仍在 pool members；同时保留“未访问项目可从 Sidebar 直接删除”的合法路径。

复核结论：`APPROVE`，无剩余上线阻断。

## 验证证据

- 后端全量：`1857 passed, 1 skipped, 4 deselected, 176 subtests passed`。
- 前端全量：`606 passed`。
- 前端生产构建：通过；发布 bundle `index-DlBZZnSg.js` / `index-DKcAfNcX.css`。
- 红队附加高风险后端集：`1139 passed, 1 skipped, 133 subtests passed`。
- 包装文档测试：`15 passed`。
- `git diff --check`：通过。

## 部署与冒烟

部署目标：`kr-web-01`，systemd `consulting-report.service`，单 worker。

1. 备份 8 个运行时源文件与当前 frontend dist 到
   `/opt/cra-rollback-20260718-105702`。
2. file-push 到独立 staging；本地/远端逐文件 SHA-256 对账。
3. staged Python 文件 `py_compile`；frontend tar 校验后解到 `frontend/dist.new`。
4. 运行时源文件安装到位，`dist → dist.old`、`dist.new → dist` 原子切换并重启 service。
5. 验证：service active、`NRestarts=0`、startup journal 无 traceback、本机和公网 health 200。
6. 公网 shell 引用新 bundle，`Cache-Control: no-cache, must-revalidate`；hash asset 200 + immutable。
7. 未登录 `/api/auth/me`、`/api/projects`、`/api/admin/users` 均为 401。
8. 真实浏览器加载新 bundle，登录页和密码控件正常渲染，console error 为 0。

运维日志已通过 `bin/vps log kr-web-01` 写入私有 VPS 项目。

## 已接受限制

- ChatPanel 池在当前标签页会话内常驻，不做 LRU；只有项目数真实增长后才设计淘汰。
- `MAX_CONCURRENT_CHAT_STREAMS=3` 是单标签页常量；多标签页/多用户共享容量不在本批范围。
- A8 的 Python RLock/锁注册表只覆盖单进程单 worker；增加 worker 前必须换跨进程锁/共享状态。
- 金额日额度仍是软帽，并发 in-flight 不预扣；出现可观测超额后再引入 reserve。
- 本次线上 GUI 冒烟不使用真实账号制造/删除业务数据；鉴权内并发由行为测试、全量回归和红队证明。

## 回滚

- 后端/skill：从 `/opt/cra-rollback-20260718-105702` 恢复对应文件。
- 前端：将服务器 `frontend/dist.old` 原子换回 `frontend/dist`。
- 重启 `consulting-report.service`，复验 health、shell bundle、鉴权与 journal。
