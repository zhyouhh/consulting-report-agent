# Current Worklist

最后更新：2026-04-20（Task 3a/3b 落地、codex 插件绕过方案、review 派发纪律）

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

8. **⭐ 阶段推进门禁重构（进行中，3c 下一步）**
- 状态：`Task 1/2/3a/3b/6 + regex 加固已完成，3c 下一步`
- 工作分支：`feat/stage-advance-gates`
- 已落 commit（按时间顺序）：
  - `9f192c0` Task 1 — stage_checkpoints.json storage helpers
  - `b127da2` Task 2 — length target + quality gate helpers
  - `aded34e` Task 2 hardening — 收紧 `_DL_ENTRY_PATTERN` / `_EXPECTED_LENGTH_*_PATTERN`
  - `fd37631` Task 6 — docs(skill) 阶段推进与工具错误规则
  - `59cfc91` **Task 3a** — 重写 `_infer_stage_state`（三条件投影）+ 7 条 RED 测试 + Step 3 skill.py 侧（`_count_words` / `_MARKDOWN_STRIP_PATTERNS` / `_has_effective_report_draft` 加 `min_words` kwarg）。两道闸 ✅。
  - `0c4f85e` **Task 3b** — migration（`_backfill_stage_checkpoints_if_missing` 只回填 `outline_confirmed_at` for ≥S2，写 `__migrated_at` 幂等标记）+ cascade（`_clear_stage_checkpoint_cascade` 保留 marker）+ 2 RED 测试 + `_stage_index("done")` 防御返回 `len(STAGE_ORDER)`。两道闸 ✅（1 Important follow-up，见下）。
- 关联文档：
  - `docs/superpowers/specs/2026-04-17-stage-advance-gates-design.md`（设计稿）
  - `docs/superpowers/plans/2026-04-17-stage-advance-gates.md`（plan）
- 当前测试基线（`0c4f85e` 上）：`unittest discover tests` — **313 passed / 27 failed / 1 skipped**。27 failed 全是 `test_workspace_summary_*`，分布：**17 在 `tests/test_skill_engine.py` + 10 在 `tests/test_workspace_materials.py`**。后者是新发现——plan Step 6 的 line 列表**只覆盖了 test_skill_engine**，没列 test_workspace_materials，3c sweep 时需自行补齐。sweep 套路一致（缺 `_save_stage_checkpoint(project_dir, "outline_confirmed_at")`）。
- 剩余 Task（按依赖）：
  - **Task 3c（下一步）** — 扩 `get_workspace_summary` 增加 `checkpoints` / `length_targets` / `quality_progress` / `flags` / `next_stage_hint` / `stalled_since` / `word_count` / `delivery_mode`，新增 `_current_report_word_count` / `_extract_delivery_mode` / `_last_evidence_write_at` / `_build_quality_progress` / `record_stage_checkpoint`；在 summary 入口 wire 进 `_backfill_stage_checkpoints_if_missing`；批量 sweep 27 个 `test_workspace_summary_*`；全量回归 GREEN。
  - Task 4 — `backend/main.py` 加 `/api/projects/{id}/stage/checkpoint` endpoint（走 `record_stage_checkpoint`）+ `backend/chat.py` 加 `_detect_stage_keyword` + Step 5 的 chat.py refactor（`REPORT_DRAFT_CANDIDATES` 改引用 `skill_engine` 属性）。依赖 Task 3c。
  - Task 5 — `write_file` 自签名拦截 + `system_notice` 注入。只依赖 Task 1（可与 3c / 4 并行）。
  - Task 7 — 前端 `StageAdvanceControl` 组件。依赖 Task 4。**派 sonnet 4.6 high，不派 codex**（用户指示）。
  - Task 8 — smoke test + 桌面端实机回归。
  - Final cross-task code review（sonnet high）。
- **Code review follow-ups**（在 3c 或后续任务顺手吸收，未做成独立 task）：
  - (Important, 3b) `_CASCADE_ORDER`(list) 和 `STAGE_CHECKPOINT_KEYS`(set) 平行结构，未来添新 checkpoint 键时会静默漂移。加一行 class-body assert `set(_CASCADE_ORDER) == STAGE_CHECKPOINT_KEYS` 自校验。
  - (Minor, 3b) `_backfill_stage_checkpoints_if_missing` 的 idempotency（第二次调 no-op）未测，补一个测试。
  - (Minor, 3b) 迁移用 `now()` 而非项目历史时间戳，在方法里一行 why 注释。
  - (Minor, 3b) `test_migration_only_*` 里 `_write_review_checklist` / `_write_delivery_log` 对该方法无影响，可删减 noise。
  - (Minor, 3a) Flag dict 命名混合 `*_ready`（文件就绪）vs `*_confirmed/_started/_passed/_done/_archived`（checkpoint 事件）——分类合理但 dict 里加一行注释点名。
  - (Minor, 3a) `_resolve_length_targets` 内嵌数学（`ceil(expected/1000*1.3)`, `int(expected*0.7)`）缺来源，加注释指 plan section。
  - (Minor, 3a) `fallback_used=True` 已暴露在 `length_targets` 但无消费者——3c 在 `get_workspace_summary` 层提示用户"当前用默认篇幅"。
  - (Minor, 3a) `_build_completed_items` 的 `done` 早退重复了后续循环的 S6-skip 逻辑，可合一。
- **Codex 派发绝对路径**：⚠️ **不要走 `codex:codex-rescue` / `Skill('codex:rescue')` 插件 Agent**——插件 1.0.4 的 companion shim 在 Windows Git Bash 下有 heredoc/stdin 处理 bug，两次派任务都在 bash 层循环、`transcript 0 字节、状态僵尸 "running"`。**正确姿势**：写 prompt 到 `.codex-run/task-<X>-prompt.md`，`codex exec --cd <abs-path> --color never --output-last-message .codex-run/task-<X>-last.txt < .codex-run/task-<X>-prompt.md > .codex-run/task-<X>-full.log 2>&1` 后台跑（Bash `run_in_background: true`）。stdout 重定向到 log 是裸 unix 流，**不像插件 JSONL 会死锁**，随时 `tail -f` 看进度。`~/.codex/config.toml` 已是 `gpt-5.4 / xhigh / danger-full-access / approval_policy=never / profile=auto-max`，CLI 直调不需任何 override。`.codex-run/` 已 gitignore。
- **Review 派发纪律**：spec compliance + code quality review 一律 **sonnet 4.6 high**（或 codex），不用 opus（太贵）。通过 `Agent` tool 的 `model: "sonnet"` 参数 override。
- 前置兼容提醒保留：non-plan-write 关键词库扩充已在 main commit `22e8976`（`NON_PLAN_WRITE_FOLLOW_UP_KEYWORDS` 常量 + `_has_existing_report_draft` helper）。**Task 4 Step 5 落地**时要把 blanket pass 插在**当前**的 `_should_allow_non_plan_write` 结构上，不要把 salvage 改动回退掉。
- Task 2 regex hardening（`backend/skill.py:46-51`）已保留未回退（3a/3b spec-review 确认）。

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
