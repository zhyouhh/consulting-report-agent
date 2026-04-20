# Current Worklist

最后更新：2026-04-21（Task 3c/5 全落地、插件 Windows sandbox bug 定位、review 改走裸 exec）

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

8. **⭐ 阶段推进门禁重构（进行中，Task 4 下一步）**
- 状态：`Task 1/2/3a/3b/3c/5/6 + regex 加固 + 全部 review follow-up 已落，Task 4 下一步`
- 工作分支：`feat/stage-advance-gates`
- 已落 commit（按时间顺序）：
  - `9f192c0` Task 1 — stage_checkpoints.json storage helpers
  - `b127da2` Task 2 — length target + quality gate helpers
  - `aded34e` Task 2 hardening — 收紧 `_DL_ENTRY_PATTERN` / `_EXPECTED_LENGTH_*_PATTERN`
  - `fd37631` Task 6 — docs(skill) 阶段推进与工具错误规则
  - `59cfc91` **Task 3a** — 重写 `_infer_stage_state`（三条件投影）+ 7 RED 测试 + `_count_words` / `_MARKDOWN_STRIP_PATTERNS` / `_has_effective_report_draft(min_words=...)`
  - `0c4f85e` **Task 3b** — migration `_backfill_stage_checkpoints_if_missing` + cascade + `_stage_index("done")` 防御 + 2 RED 测试
  - `0b50d74` **Task 3c** — 扩 `get_workspace_summary`（checkpoints / length_targets / quality_progress / flags / next_stage_hint / stalled_since / word_count / delivery_mode / length_fallback_used）+ `record_stage_checkpoint` + 9 RED + sweep 27 旧测试 + 吸收全部 8 条 review follow-up
  - `1c4af41` Task 3c hygiene — 注释 `record_stage_checkpoint` 走 `backend.main.get_chat_handler` 取共享锁的原因 + test 名改 roundtrip
  - `e1cee53` **Task 5** — `write_file` 自签名拦截 + `system_notice` 三段链路（`_emit_system_notice_once` + stream 端 pop drain + 非流端注入 `ChatResponse.system_notices`）+ 16 RED 测试
  - `8e7c590` Task 5 hygiene — `validate_self_signature` / `is_protected_stage_checkpoints_path` 加 docstring + `_DELIVERY_PLACEHOLDER_INLINE` 注释 `客户反馈→反馈` 拓宽原因
- 关联文档：`docs/superpowers/specs/2026-04-17-stage-advance-gates-design.md`（设计稿）+ `docs/superpowers/plans/2026-04-17-stage-advance-gates.md`（plan）
- 当前测试基线（`8e7c590` 上）：**369 passed / 0 failed / 1 skipped**（命令 `.venv\Scripts\python -m unittest discover tests`，整套约 5 min）
- **剩余 Task（按依赖）：**
  - **Task 4（下一步）** — `backend/main.py` 加 `POST /api/projects/{id}/stage/checkpoint` endpoint（薄壳，走 `skill_engine.record_stage_checkpoint(project_id, key, action)`）+ `backend/chat.py` 加 `_detect_stage_keyword`（用户对话里命中 `outline_confirmed_at` 等关键词时，建议触发 endpoint）+ chat.py refactor：把 `REPORT_DRAFT_CANDIDATES`（chat.py 里的本地副本）改引用 `self.skill_engine.REPORT_DRAFT_CANDIDATES`，让 SkillEngine 是单一真值源。**plan 位置**：`docs/superpowers/plans/2026-04-17-stage-advance-gates.md` Task 4 章节（grep `^### Task 4`）。**关键约束**：Step 5 落地时不能回退 `_should_allow_non_plan_write` 里的关键词扩充（commit `22e8976`），把 blanket pass 接到现有结构上。
  - Task 7 — 前端 `StageAdvanceControl` 组件。依赖 Task 4 endpoint 上线。**派 sonnet 4.6 high，不派 codex**（用户原始指令、未变）。需要消费 `get_workspace_summary` 新返回的 `next_stage_hint` / `quality_progress` / `flags` / `stalled_since` / `delivery_mode`，渲染下一阶段按钮 + 质量进度条 + 卡阻提示。同时把 Task 5 的 `system_notice` stream 事件接入 `ChatPanel.jsx` 渲染（这块是 Task 5 切出去的小尾巴）。
  - Task 8 — smoke test + 桌面端实机回归。`.venv\Scripts\python -m PyInstaller consulting_report.spec` 打包，跑一轮完整 S0→S7，确认 checkpoint UI 和文件门禁联动正常。
  - Final cross-task code review — 跨 8 个 task 整体看接口一致性、死代码、测试覆盖、SKILL.md 与实现一致性。
- **派发规则（重要，已变）：**
  - **实施任务（`--write`）** → codex 插件可用：`Agent(subagent_type='codex:codex-rescue', ...)` 跑得通（Task 3c 34min、Task 5 ~55min 都成功）。共享 runtime 帮省冷启动 + 提供 job ID 可查 (`codex-companion.mjs status --all --json`)。**注意**：Task 5 末尾插件 status 上报可能僵在 "running"，但 commit 实际已落——判活看 git log + 日志 mtime，别只信 status。
  - **Review 任务（read-only）** → 必须**裸 `codex exec`**，**不能走插件**。原因：插件 `codex-companion.mjs:411,488` 硬编码 `sandbox: "read-only"` for review，Windows 上触发 `CreateProcessAsUserW failed: 5`（普通账户没 `SE_ASSIGNPRIMARYTOKEN_NAME` 特权）。裸 exec 走 `~/.codex/config.toml` 里的 `danger-full-access` 不降权所以能跑。
  - **Review 模型选择** → codex（GPT-5.4 xhigh）裸 exec，不派 sonnet（成本考虑，用户 04-21 明确要求；记忆见 `feedback_review_dispatch.md`）
- **裸 codex exec 模板（review / 任何 read-only 任务）：**
  ```bash
  codex exec --cd "D:\CodexProject\Consult report\consulting-report-agent" \
    --color never --output-last-message .codex-run/task-X-last.txt \
    < .codex-run/task-X-prompt.md > .codex-run/task-X-full.log 2>&1
  ```
  Bash 工具传 `run_in_background: true`。`~/.codex/config.toml` 已配 `gpt-5.4 / xhigh / danger-full-access / approval_policy=never / profile=auto-max`，CLI 直调零 override。`.codex-run/` 已 gitignore。
- **Review 流程模板（两道闸，可参考最近一轮 Task 5）：**
  1. 写 spec compliance review prompt 到 `.codex-run/task-X-spec-review-prompt.md`，裸 exec 派 codex
  2. 等结果（每 10-20 min `ScheduleWakeup` 一次自查）
  3. ✅ 后写 code quality review prompt 到 `.codex-run/task-X-quality-review-prompt.md`，裸 exec 派 codex
  4. 🟡 APPROVED WITH COMMENTS 时，把 Important 修复打成新的 follow-up prompt，再裸 exec 派一次 codex 做修复（仍是 `--write` 类，但裸 exec 一样能 commit）
- **Task 5 切出去的前端尾巴：** `system_notice` stream event 的渲染（`frontend/src/components/ChatPanel.jsx`）合并到 Task 7 一起做，由 sonnet 处理。事件结构见 `backend/chat.py:1367-1372`：`{type, category, path, reason, user_action}`。
- **历史已知坑（保留）：**
  - regex 加固（commit `aded34e`、`backend/skill.py:54-77`）不可回退，多轮 review 已确认
  - non-plan-write 关键词库扩充（commit `22e8976`、`NON_PLAN_WRITE_FOLLOW_UP_KEYWORDS` 常量 + `_has_existing_report_draft` helper）不可回退，**Task 4 Step 5 落地时**把 blanket pass 接到当前结构上，别 salvage 走

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
