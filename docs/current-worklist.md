# Current Worklist

最后更新：2026-04-24（正文草稿工具契约与真实落盘防线已合 main；1c 从待诊断转为待现场复测）

## 当前未解决 / 待验证

1. 二轮重打包已完成，主链路已跑完
- 状态：`已走二轮 smoke（暴露新 3 bug，见 1b）`
- 二轮重点验回顾：
  - Bug A/B/D/F 修复在新包里都生效（data-log.md 已按 `### [DL-YYYY-NN]` 格式写；非 plan 写入阶段门禁生效）
  - 聊天气泡 + 文件预览原生框选复制可用
- 二轮新暴露问题见 1b

1a. **[BUG 串] stage-advance-gates 实机链条性失效 — A/B/D/F 已修**
- 状态：`A/B/D/F 已修，C/G/H 待跟进`（2026-04-21 3 路并行 codex + general-purpose 派活，全部合 main）
- 关联 plan：`docs/superpowers/plans/2026-04-21-smoke-test-bugfix.md`
- 测试基线：403 passed / 1 skipped（基线 397 → 403，加 6 条新测试）

**Bug A ✅** — `backend/chat.py` `_should_allow_non_plan_write` 已叠加阶段校验，仅在推断阶段 ≥ S4 时放行非 plan 写入。commit `cb15e4c fix(chat): gate non-plan writes by stage`。

**Bug B ✅** — `backend/skill.py:record_stage_checkpoint` 在 `set` 前校验对应 plan 文件有效存在（outline/report_draft/review_checklist/presentation_plan/delivery_log），缺文件 raise ValueError。commit `7e262cf fix(skill): validate stage checkpoint prerequisites`。

**Bug C ⏸** — 未修。S0 质量门槛缺失：`stage_zero_complete = project_overview_ready`，项目一创建就 S0 完成。需产品侧设计"访谈深度"判据（最少 N 轮真实问答？区分表单生成 vs 访谈补全？），暂挂。

**Bug D ✅** — `skill/SKILL.md` §S2 明确 `### [DL-YYYY-NN]` 格式 + 完整示例，并写明"表格形式不会被识别"；首次写 `plan/data-log.md` 时通过 `_emit_system_notice_once` 注入格式提示。commits `7a50bb3` / `88f10d7` / `4a6a7da`。

**Bug E ✅** — Bug A+D 修好后自消，不再独立追踪。

**Bug F ✅** — `backend/chat.py:_expected_plan_writes_for_message` 白名单从硬编码 5 条路径改成正则匹配 `report_draft_v\d+\.md` 和 `(content|output)/*.md`，`_is_expected_report_write_path` 方法抽出可复用。+28 行测试。commit `1e180cc fix(chat): detect versioned report draft claims`。

**Bug G ⏸** — 未修。回退 checkpoint 后 `content/*.md` 仍存在，状态不自洽。需要级联清理 or UI 标红提示，暂挂。

**Bug H ⏸** — 未修。S1 回退后 UI「下一步建议」显示"暂无"，`next_stage_hint` S1 分支缺。暂挂。

~~**Bug I**~~ — 已排除，黄色警告是当轮新触发。

**派活记录**（作为项目默认工作法参考）：
- 3 路并行：task-4（codex exec, Bug A+B+F）+ task-5（codex exec, Bug D）+ frontend-copy（general-purpose + sonnet, worklist #8）
- 两个 codex 共享 main working tree，Bug F 先手被 task-4 commit，task-5 跑完看到存在不覆盖，零冲突
- 监控从 30 min cron → 5 min cron（监控到 task-5 越界迹象）→ 20 min cron（兜底挂掉），bash 完成靠系统 notification，无需频繁自查

1b. **[二轮 smoke] 新发现三处问题**
- 状态：`待讨论方向 + 待修`（2026-04-21 二次 smoke）
- 测试项目：`D:\MyProject\CodeProject\JustTest\.consulting-report\`
- 已走 systematic-debugging Phase 1，根因定位完毕，未下修

**新 Bug 1（S0 门槛回归，关联旧 1a#Bug C）** — 图5
- 现象：填完新建项目表单 → 右侧「已完成」直接显示 "需求访谈完成 / 范围界定明确 / project-overview.md 创建 / 交付形式确认" 四项全勾，对话一句没说
- 实证：`backend/skill.py:1257` `stage_zero_complete = project_overview_ready`；`STAGE_CHECKLIST_ITEMS["S0"]` 正是这 4 项；表单创建项目时直接写 `plan/project-overview.md` → `_is_effective_plan_file` 立即 True → stage 跳 S1 → `_build_completed_items` 把 S0 全部塞入完成
- 用户澄清（2026-04-21）：原设计意图是「表单 → 模型基于信息（可选加一轮 web_search 了解主题）→ 主动需求访谈 → 写 outline → 再确认」。当前行为跳过了访谈步骤
- 决策点（需讨论）：
  - 访谈「完成」的判据（最少 N 轮真实 Q&A？必答字段清单？模型自判 + 用户点"访谈够了"？）
  - `project-overview.md` 模型还需不需要主动补全/修订？还是表单已经生成的版本即终稿？
  - S0 checklist 4 项怎么映射（哪项来自表单、哪项来自访谈、哪项由模型判定）

**新 Bug 2 ✅（tool 结果气泡吞 assistant 正文）** — 图6
- 现象：`✅ 结果: {...}` 气泡把紧跟的 assistant 正文首段一起吞入同一个气泡
- 根因：`frontend/src/components/ChatPanel.jsx:509` 流式拼接 tool 事件时只在前面加 `\n`、尾部不加；后续 `content` 块直接 append 同一行；`utils/chatPresentation.js:64` `splitAssistantMessageBlocks` 按行识别整行以 `✅ 结果:` 开头为 tool block → 把吞进去的正文也算 tool
- 修法：抽 `appendToolEventContent(prev, toolText)` 纯函数（chatPresentation.js），自动补尾 `\n`；ChatPanel.jsx 调用
- commit：`73b345d fix(chat): preserve text after tool events`；前端测试 139→140 passed，`npm run build` 零错
- 附带：codex 多加了 `frontend/tests/index.js`（为让 `node --test tests/` 做显式目录入口，可保留）

**新 Bug 3（口头"确认"不推进阶段）** — 图8
- 现象：用户回"确认"（响应模型"请回复'确认大纲'或'按此大纲执行'"），`stage_checkpoints.json` 未写入 `outline_confirmed_at`
- 实证：`conversation.json[2]` 用户原话 = `"确认"`；`stage_checkpoints.json` 只有 `__migrated_at`
- 根因：`backend/chat.py` `_STRONG_ADVANCE_KEYWORDS["outline_confirmed_at"]` = `["确认大纲","大纲没问题","按这个大纲写","就这个大纲","就按这个版本"]` — **没单独"确认"**；`_WEAK_ADVANCE_BY_STAGE["S1"]` = `["行","可以","同意","没问题","OK","ok","好的","挺好的"]` — **也没"确认"**。`_detect_stage_keyword` 直接返回 None，没调 `record_stage_checkpoint`
- 反讽：模型自己在回复里引导"请回复'**确认大纲**'或'按此大纲执行'"，用户简写"确认"最自然，关键词表漏了
- 更深问题：整个「后端用 regex 猜对话意图」的方案是否合适？LLM 本身在回路内最懂上下文，层级颠倒
- 决策点（需讨论）：
  - (a) 短期打补丁：`_WEAK_ADVANCE_BY_STAGE["S1"]` 加 `"确认"`，同步改 SKILL.md §S1 白名单
  - (b) 中期重构：让 LLM 在 assistant 尾部输出结构化信号（如 `<stage-ack>outline_confirmed</stage-ack>`），后端校验前置文件后 set checkpoint，剥掉标签再返回前端。五个 checkpoint 通吃
  - (c) 加轻量意图分类（调一次小模型判断）— 成本 + 延迟

1c. **[新发现] 模型行为硬伤 — 主体修复已合 main，待现场复测**
- 状态：`部分已修复，待现场复测`（2026-04-24 codex 已完成正文草稿工具契约、真实落盘校验、先读再写门禁；commit `a7f36d0` 已合 main）
- 测试项目：`D:\CodexProject\test\.consulting-report\`
- 模型约束：`gemini-3-flash`（免费批量渠道限制，无法更换）

**2026-04-24 已落地**：
- `content/report_draft_v1.md` 成为正文草稿唯一规范路径；首次成稿/续写走 `append_report_draft`，修改已有正文走 `read_file + edit_file`，禁止用 `write_file` 直接覆盖正文草稿
- 所有已有文件通用要求同一轮先 `read_file`，再 `write_file` / `edit_file`，降低模型拿旧上下文覆盖新文件的概率
- 正文写入工具回传真实落盘字数进度，`append_report_draft` 事件保留真实 tool name，`draft_followup_state` 改成结构化状态，不再从 assistant 文案反推
- 混合意图（如"写够 5000 字再导出/质量检查/看文件/看字数"）改为本轮只完成正文写入并给下一步提示，后续动作下一轮单独处理
- 章节改写新增范围校验：`edit_file.new_string` 不能把整篇草稿或多个同级章节塞进单章节替换里

**仍需现场确认**：
- data-log 伪条目场景是否已被真实写入校验和先读再写门禁兜住
- 反思循环输出是否仍会在 `gemini-3-flash` 下复现；如复现，再单独做流式循环检测/截断

**问题 1：模型假装落盘，把条目写在聊天正文不调工具**
- 现象：用户催"把来源写进去"，模型回一大段正文里有 `### [DL-2026-03]`、`DL-2026-04`、`DL-2026-05` 完整 Markdown 条目，**但 `plan/data-log.md` 磁盘上没有这几条**
- 证据：`conversation.json[17]` 6613 字 assistant 包含伪造条目；同 turn `~/.consulting-report/debug/payload-latest.json` 里无 `edit_file` / `write_file` 调用
- 现状 UI：`data-log.md` 卡在 5 条（DL-01/02/06/07/08，跳号），有效来源 `5/7 71%` 一直上不去
- 已尝试无效：SKILL.md 有"禁止在工具被挡时贴正文"约束，但不覆盖"工具没被挡、模型自己不调"这种场景

**问题 2：反思循环输出（自言自语）**
- 现象：assistant 无限输出 `(修正: ...) (纠正: ...) (对不起，我需要停止自言自语...)` 循环至 max_tokens 截断，末尾残留 `<stage` 标签
- 根因判断：Gemini 3 Flash 遇到「多层 skill 约束 + 模糊状态」时的反思幻觉故障
- 目前无兜底——污染 conversation.json，下一轮带烂尾消息进 context

**问题 3：`plan/report-draft.md` 反复违规**
- 现象：模型反复尝试写 `plan/report-draft.md`（非法文件名），skill 校验拦截后下一轮继续试
- 正确位置应是 `content/report_draft_v1.md`
- SKILL.md 现有文案没能拦住这个幻觉

**备选修复方向（codex 自行判断）**：
- α：加 `append_file(file_path, content)` 工具 —— 对追加条目场景零门槛，降低模型跳过工具的诱因
- β：SKILL.md 加硬约束「正文里写 `### [DL-XXXX-NN]` 条目而本轮没真调工具 = 严重错误」
- γ：流式层加循环检测 —— 累积 content 时检测 `(修正:` / `(纠正:` 等 pattern 连续 ≥3 次主动打断 stream
- δ：`content/report_draft_v1.md` 正确路径在 SKILL.md 里更显著地提示

**诊断入口**：
- 每次请求的完整 payload 都在 `~/.consulting-report/debug/`（`payload-latest.json` 覆盖写 / `error-*.json` 保留），对照 `conversation.json` 能看出真实 tool 调用行为
- 本轮 claude 侧代码改动已清 400 死循环（见"最近已解决"第 0 条 2026-04-22 那份），剩下的是模型行为/产品交互层

2. 流式输出体感
- 状态：`待验证`
- 来源：原 `debug-backlog` 第 1 条
- 现状：前端正常结束时的强制 flush 已修；默认通道读流超时和友好报错也已修。
- 仍需确认：真实 exe 里是否还会出现"正文不是平滑流出，而是一大段集中冒出来"的体感问题。

3. 新建项目表单与废 UI 整理
- 状态：`待开始`
- 目标：把"填了像没填"的字段、重复输入项和旧流程遗留 UI 一次性清干净。
- 当前方向：
  - 删除真正无效或重复的字段
  - 把"截止日期"改成日期选择器
  - 重新审视"已有材料或备注"和"初始材料"的语义重叠
  - 提高项目类型、主题、目标读者、篇幅等字段在初始化和首轮交互中的利用率
- 关联：Task 7 的 `length_fallback` chip 目前只是非交互提示，因为 `ProjectCreateModal` 没有 edit 模式；如果本项做了"新建项目表单改造 + 加 edit 模式"，可以顺便让 chip 点击打开编辑面板。

4. 默认渠道文案与默认模型决策
- 状态：`待开始`
- 目标：把"推荐/保证可用"类表述改成更中性的"默认渠道 / 开箱即用"。
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

8. ~~聊天与文件预览复制体验~~ — ✅ 已修，commit `341de44`。根因：PyWebView 的 WebView2 在 Win 下对非输入元素默认禁选；通过 `.selectable-content` 工具类（`-webkit-user-select: text` + `*` 子选择器）在 ChatPanel 气泡 + FilePreviewPanel 预览区放开。右上角复制按钮保留。已进"最近已解决"。

## 最近已解决

0. ⭐ **400 死循环根因清理 + edit_file 工具 + debug dump 转正（2026-04-22）**
- 状态：`已完成`（claude 侧自改自测，未派 codex；测试 509 passed / 1 skipped / 0 failed）
- 根因：`newapi → Gemini` OpenAI 流式兼容层偶发把并行 `functionCall` 的 chunk `index` 合并到 0，导致我方累积层把多个 tool_call 的 `name` 和 `arguments` 首尾拼接成 `"write_filewrite_file"` + `"{...}{...}"`，上游拒收 `400 INVALID_ARGUMENT`
- 代码改动全部在 `backend/chat.py`：
  - **Fix A**（畸形 tool_calls 拦截）：`if collected_message["tool_calls"]:` 分支开头校验每个 tool_call 的 `name in known_tool_names` 且 `arguments` 是合法 JSON；任一畸形 → 本轮作废，append `assistant 占位 + user 反馈` 对子做合规隔板（**单独 append user 反馈会造成连续两条 user → Gemini 角色交替校验 400，踩过一次**），`iterations += 1; continue`
  - **Fix B**（当轮空 content 兜底）：流式和非流式两条 `_finalize_assistant_turn` 之后都加 `if not assistant_message.strip(): assistant_message = "（本轮无回复）"`，避免空 parts 的 assistant 进历史
  - **Fix C**（历史回放兜底）：`_to_provider_message` 对 `role=assistant` 且 `content=""` 的老残迹同样兜底，不依赖干净历史
  - **Fix D**（system prompt 约束）：加 `concurrency_rule`「每轮只发一个 tool_call」—— 实测 Gemini 3 Flash 基本无视，但 Fix A 能兜底合并畸形
- 新工具 `edit_file(file_path, old_string, new_string)`：精确字符串替换，要求 `old_string` 唯一存在；`write_file` 和 `edit_file` 共用抽出来的 `_execute_plan_write(project_id, *, file_path, content, persist_func_name, persist_args)` 方法跑完整 gate 链（S0 block / non-plan-write / fetch-url gate / path normalize / signature / data-log-hint / persist）。`skill/SKILL.md` 新增「文件工具选择」章节，明确 data-log.md / analysis-notes.md 追加条目一律 `edit_file`，`write_file` 只用于新建或整体重写
- 配置：`managed_search_pool.json` `per_turn_searches: 2 → 4`（仍受 `project_minute_limit: 10` / `global_minute_limit: 20` 保护）
- debug dump 转正：`_debug_dump_request` 方法从临时调试代码改成持久辅助工具。路径从 `D:/consulting-debug/` 挪到 `~/.consulting-report/debug/`（跨平台 + 和其他用户数据同目录），每次请求写 `payload-latest.json`（覆盖），失败时另存 `error-{UTC}-{label}.json`（保留）。`label` ∈ `{stream, stream-iter, nostream}`，`note` 字段带 `iteration=N`
- 关键证据：`~/.consulting-report/debug/error-20260422T132039Z-stream.json`（最初定位到 `write_filewrite_file` 畸形 payload）、`error-20260422T135150Z-stream.json`（Fix A 早期实现引入的"连续两条 user"回归证据）
- 后续未解决的模型行为问题转交 codex，见"当前未解决"第 1c 条

1. ⭐ **stage-advance-gates smoke-test bugfix（Bug A/B/D/F + 前端复制）**
- 状态：`已完成`（2026-04-21 3 路并行派活，全部合 main）
- 5 个 commit：`cb15e4c` / `7e262cf` / `1e180cc`（task-4 Bug A/B/F）+ `4a6a7da` / `88f10d7` / `7a50bb3`（task-5 Bug D）+ `341de44`（frontend-copy 复制体验）
- 测试：后端 403 passed（397→403，+6 新测试）；前端 139 passed；`npm run build` 零错
- 详情见"当前未解决/待验证"第 1a 条（保留在那里以便追 C/G/H 跟进）
- 下一步：**重打包 `dist\咨询报告助手\` → 用户二轮 smoke test**

1. ⭐ **阶段推进门禁重构（stage-advance-gates，Task 1-8 全闭环）**
- 状态：`已完成`（2026-04-21 分支 `feat/stage-advance-gates` 合 main）
- 关联文档：`docs/superpowers/specs/2026-04-17-stage-advance-gates-design.md`、`docs/superpowers/plans/2026-04-17-stage-advance-gates.md`
- 覆盖：
  - Task 1/2 — stage_checkpoints.json storage + length target + quality gate helpers（含 regex 加固）
  - Task 3a/3b/3c — 重写 `_infer_stage_state`（三条件投影）+ migration cascade + `get_workspace_summary` 扩 `checkpoints` / `length_targets` / `quality_progress` / `flags` / `next_stage_hint` / `stalled_since` / `word_count` / `delivery_mode` / `length_fallback_used`
  - Task 4 — `POST /api/projects/{id}/checkpoints/{name}` endpoint + stage-aware `_detect_stage_keyword`（strong / weak S4 排除 / rollback / negation 抑制 / `非常同意` 不误伤 / tie-break）+ `_should_allow_non_plan_write` blocking-first 优先级 + 两轮 follow-up（`checkpoint_event` 字段 / OK/ok 大小写 spec 同步 / `SkillEngine.record_stage_checkpoint` 解耦 `backend.main` / 4 张 checkpoint 表 invariant test）
  - Task 5 — `write_file` 自签名拦截 + `system_notice` 三段链路（`_emit_system_notice_once` + stream pop drain + `ChatResponse.system_notices`）
  - Task 6 — `skill/SKILL.md` 阶段推进与工具错误规则
  - Task 7 — 前端 `StageAdvanceControl` + `RollbackMenu` + `ConfirmDialog` + `WorkspacePanel` chip + `ChatPanel` `system_notice` 渲染 + `workspaceSummary` 契约映射 + 7 fix round（`flags.outline_ready` 字段名 / length_fallback chip 非交互 / `delivery_mode` 中文字面量 / "调整大纲"触发 prompt / `next_stage_hint` 消费守护 / checkpoint 错误反馈 + `pending` 态 / ConfirmDialog a11y / 隐藏后台阶段码 / `length_targets.report_word_floor` 契约对齐）
  - Task 8 — 新包 91 MB（dist/咨询报告助手/）
  - Final cross-task review — APPROVED（见 `.codex-run/final-rereview-last.txt`）
- 测试基线（合并前）：后端 397 passed / 1 skipped / 0 failed；前端 139 pass / 0 fail；`npm run build` 零错。
- 派发规则（已成为项目默认）：
  - 实施任务（`--write`）→ 裸 `codex exec`（插件不稳定）；前端 `general-purpose` agent 配 `model: sonnet`
  - Review（read-only）→ 裸 `codex exec`（GPT-5.4 xhigh）
  - 裸 exec 模板：`codex exec --cd "..." --color never --output-last-message .codex-run/X-last.txt < .codex-run/X-prompt.md > .codex-run/X-full.log 2>&1`，bash 传 `run_in_background: true`
  - 30 min cron (`7,37 * * * *`) 做活性自查，完成后自动 `CronDelete`

3. 内置搜索池主链路
- 状态：`已完成`
- 结论：`managed_search_pool.json` 打包注入、运行时状态/缓存、四家 provider 适配器、分层路由、native fallback、chat runtime 接线都已落地。

4. 1.29 GB 异常大包
- 状态：`已完成`
- 根因：之前在 Anaconda 大环境里打包，PyInstaller 把大量无关科学计算/Notebook 依赖一起卷进包。
- 结论：已切到项目 `.venv` 打包，最新包体积约 `91 MB`（含 Task 4/7 新增代码）。

5. 打包脚本不稳
- 状态：`已完成`
- 结论：`build.bat` 已改为薄入口，实际逻辑迁到 `build.ps1`；默认走项目 `.venv`，不再依赖脏全局环境。

6. 前端依赖漏洞
- 状态：`已完成`
- 结论：已升级前端依赖，当前 `npm audit` 为 `0 vulnerabilities`。

7. 阶段事实源与工作流对齐
- 状态：`已完成`
- 关联文档：`docs/superpowers/specs/2026-04-01-stage-facts-and-phase-alignment-design.md`
- 结论：`project-info.md` 已退出正式工作流；阶段推断、正式 plan 文件和门禁规则已对齐。

8. Session memory 重构
- 状态：`已完成`
- 关联文档：`docs/superpowers/specs/2026-04-14-session-memory-rearchitecture-design.md`
- 结论：`conversation_state.json`、memory entries、post-turn compaction 和 provider 上下文顺序已完成重构。

## 已取代 / 废弃

1. Web Search 相关性加固（针对 SearXNG 单后端）
- 状态：`已被取代（Superseded）`
- 关联文档：`docs/superpowers/specs/2026-04-15-web-search-relevance-hardening-design.md`（顶部已加 Superseded banner）
- 取代原因：项目走了**管理型搜索池**路线（`managed-search-pool` 已完成，见"最近已解决"第 3 条），四家 provider + 分层路由，从根本上绕过了 SearXNG 召回质量问题。
- 不要再按这份 spec 落地。保留文档是因为它记录的 SearXNG 实测问题可作为未来搜索策略调整的参考。

## 使用约定

- 只在本文件维护"仍需要行动"的事项。
- 已解决但值得保留上下文的内容，放到"最近已解决"。
- 历史调试记录归档到 `docs/debug-backlog.md`，不再作为当前事实源。
