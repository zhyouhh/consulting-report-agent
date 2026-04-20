# Current Worklist

最后更新：2026-04-17（新增阶段推进门禁重构 / 聊天与预览复制体验）

## 当前未解决 / 待验证

1. 新包实机 smoke test
- 状态：`待验证`
- 目标：在最新 `85 MB` Windows 包里跑一轮完整业务流，确认代码层面的修复已经真实反映到桌面端体验。
- 重点检查：
  - 默认渠道启动与基础聊天
  - 内置搜索池是否正常工作
  - 阶段推进、文件落盘、右侧工作区同步
  - `web_search -> fetch_url -> write_file` 门禁
  - 打包后私有文件是否正确注入

2. 流式输出体感
- 状态：`待验证`
- 来源：原 `debug-backlog` 第 1 条
- 现状：前端正常结束时的强制 flush 已修；默认通道读流超时和友好报错也已修。
- 仍需确认：真实 exe 里是否还会出现“正文不是平滑流出，而是一大段集中冒出来”的体感问题。

3. 新建项目表单与废 UI 整理
- 状态：`待开始`
- 目标：把“填了像没填”的字段、重复输入项和旧流程遗留 UI 一次性清干净。
- 当前方向：
  - 删除真正无效或重复的字段
  - 把“截止日期”改成日期选择器
  - 重新审视“已有材料或备注”和“初始材料”的语义重叠
  - 提高项目类型、主题、目标读者、篇幅等字段在初始化和首轮交互中的利用率

4. 默认渠道文案与默认模型决策
- 状态：`待开始`
- 目标：把“推荐/保证可用”类表述改成更中性的“默认渠道 / 开箱即用”。
- 待定项：
  - 默认模型是否从 `gemini-3-flash` 调整为 `gpt-5.4`
  - 设置页、README、打包文档里的相关表述统一

5. `draw.io skill` 评估
- 状态：`待开始`
- 目标：判断它对咨询报告场景是否真有价值，还是只会增加复杂度。

6. 前端生产包优化
- 状态：`待开始`
- 现状：`vite build` 已通过，但主 JS chunk 仍接近 `1 MB`。
- 目标：在不引入复杂度失控的前提下做基本拆包，降低首屏和构建产物压力。

7. 技术债清理
- 状态：`待开始`
- 当前明确项：
  - `pydantic` deprecation warning 仍存在
  - 需要再看是否有可以从打包里继续排除的非必需依赖

8. **⭐ 阶段推进门禁重构（进行中，跨设备接手）**
- 状态：`Task 1/2/6 + regex 加固已完成，Task 3 待重派`
- 工作分支：`feat/stage-advance-gates`（已 push 到 origin）
- 已落 commit（按时间顺序）：
  - `9f192c0 feat(skill): add stage_checkpoints.json storage helpers` — Task 1
  - `b127da2 feat(skill): add length target + quality gate helpers` — Task 2
  - `aded34e fix(skill): tighten regex for bolded DL headings and parenthetical length commentary` — Task 2 后续 hardening（修复 `_DL_ENTRY_PATTERN` 漏匹配 bolded 头、`_EXPECTED_LENGTH_LINE_PATTERN` 误吃括号注释）
  - `fd37631 docs(skill): document stage advancement gates and tool error handling rules` — Task 6
- 关联文档：
  - `docs/superpowers/specs/2026-04-17-stage-advance-gates-design.md`（设计稿）
  - `docs/superpowers/plans/2026-04-17-stage-advance-gates.md`（分 8 个 Task 的 TDD 落地计划 + 各 Task RED 测试）
- 剩余 Task：
  - **Task 3（待重派）** — 重写 `_infer_stage_state`。⚠️ 已两次派给 codex xhigh 全量做，都在写完 `tests/test_skill_engine.py` 后进程死掉（同一失败点）。**接手时务必拆成 3 个 sub-task 分别派**：(3a) Step 1 RED 测试 + Step 2 `_infer_stage_state` 改写；(3b) Step 3 `_count_words` + Step 4 migration & cascade；(3c) Step 5 `get_workspace_summary` 扩字段 + Step 6 旧测试断言批量更新 + Step 7 全量回归。每段独立 commit。
  - Task 4 — checkpoint endpoints + `_detect_stage_keyword`（依赖 Task 3）
  - Task 5 — `write_file` 自签名拦截 + `system_notice` 注入（只依赖 Task 1）
  - Task 7 — 前端 StageAdvanceControl（依赖 Task 4，派给 sonnet）
  - Task 8 — smoke test + regression sweep
  - Final code review across all 8 tasks
- 前置兼容提醒：non-plan-write 关键词库扩充已在 main commit `22e8976`（`NON_PLAN_WRITE_FOLLOW_UP_KEYWORDS` 常量 + `_has_existing_report_draft` helper）。plan Task 4 Step 5 的代码片段按旧结构写，落地时要把 blanket pass 插在**当前**的 `_should_allow_non_plan_write` 结构上，不要把 salvage 改动回退掉。
- 已完成的 Task 2 regex hardening（`backend/skill.py:46-51`）必须保留：`_DL_ENTRY_PATTERN` 用 `^#{3,4}\s*\*{0,2}\s*\[`，`_EXPECTED_LENGTH_*_PATTERN` 用 `[^\n(（]+` 边界。Task 3 改写时不要回退。
- Codex 死法记录：第一次（v1）跑了 27 min 后死，留下未 commit 的 backend/skill.py + test_skill_engine.py 改动（已 stash，stash 名 `task3-codex-partial-died-at-20:18`，对端可 `git stash drop` 或 `git stash show` 参考）；第二次（v2）跑了 1h09 后死，未 commit 的部分已 `git checkout --` 丢弃。

9. 聊天与文件预览复制体验
- 状态：`待开始`
- 现象：
  - 聊天对话框里的消息正文不可框选复制（只能用消息右上角的复制按钮）
  - 文件内容预览面板完全不可复制，只能看
- 目标：聊天与文件预览都支持原生框选复制；保留现有复制按钮作为显式入口。
- 约束：不引入额外的富文本复杂度，只处理 CSS 层面的 `user-select` 与事件拦截。

## 最近已解决

1. 内置搜索池主链路
- 状态：`已完成`
- 结论：`managed_search_pool.json` 打包注入、运行时状态/缓存、四家 provider 适配器、分层路由、native fallback、chat runtime 接线都已落地。

2. 1.29 GB 异常大包
- 状态：`已完成`
- 根因：之前在 Anaconda 大环境里打包，PyInstaller 把大量无关科学计算/Notebook 依赖一起卷进包。
- 结论：已切到项目 `.venv` 打包，最新包体积约 `85.3 MB`。

3. 打包脚本不稳
- 状态：`已完成`
- 结论：`build.bat` 已改为薄入口，实际逻辑迁到 `build.ps1`；默认走项目 `.venv`，不再依赖脏全局环境。

4. 前端依赖漏洞
- 状态：`已完成`
- 结论：已升级前端依赖，当前 `npm audit` 为 `0 vulnerabilities`。

5. 阶段事实源与工作流对齐
- 状态：`已完成`
- 关联文档：`docs/superpowers/specs/2026-04-01-stage-facts-and-phase-alignment-design.md`
- 结论：`project-info.md` 已退出正式工作流；阶段推断、正式 plan 文件和门禁规则已对齐。

6. Session memory 重构
- 状态：`已完成`
- 关联文档：`docs/superpowers/specs/2026-04-14-session-memory-rearchitecture-design.md`
- 结论：`conversation_state.json`、memory entries、post-turn compaction 和 provider 上下文顺序已完成重构。

## 已取代 / 废弃

1. Web Search 相关性加固（针对 SearXNG 单后端）
- 状态：`已被取代（Superseded）`
- 关联文档：`docs/superpowers/specs/2026-04-15-web-search-relevance-hardening-design.md`（顶部已加 Superseded banner）
- 取代原因：项目走了**管理型搜索池**路线（`managed-search-pool` 已完成，见"最近已解决"第 1 条），四家 provider + 分层路由，从根本上绕过了 SearXNG 召回质量问题。
- 不要再按这份 spec 落地。保留文档是因为它记录的 SearXNG 实测问题可作为未来搜索策略调整的参考。

## 使用约定

- 只在本文件维护“仍需要行动”的事项。
- 已解决但值得保留上下文的内容，放到“最近已解决”。
- 历史调试记录归档到 `docs/debug-backlog.md`，不再作为当前事实源。
