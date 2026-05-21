# 2026-05-21 — S5 Independent Review Redesign 设计

> **Version**: v6（response to codex R1-R5 reviews）
> **状态**: R5 提到的唯一 must-fix（Bug 21）+ 3 个改进建议已处理，待 R6 review

## R5 Round 5 Review Response Annex

Codex R5 verdict: **CHANGES_NEEDED**，但 codex 明说"只需要修 Bug 21 的 commit phasing；修完后这份 spec 可以进入 plan 阶段"。

| R5 Item | 严重度 | 处理章节 | 概述 |
|---|---|---|---|
| **Bug 21**（新）— Commit 3 包含 S5 欢迎信调用点 → 前端按钮还没出现时提示用户点不存在按钮 | 高 | §13 Commit 3/4 重写 | S5 welcome **helper 实现**留 Commit 3（dormant），但 `_chat_stream_unlocked()` 的**调用点**移到 Commit 4 atomic cutover（与 backend gate + SKILL + 前端按钮一起激活）|
| **改进建议 #1** — Commit 2 "完全等价"表述不严谨 | 低 | §13 Commit 2 改进 | 加新 stub 后文件 tab 会展示 → 改成"主流程等价；文件 tab 可能看到 pending stub，属低风险" |
| **改进建议 #2** — Commit 4 切换清单显式写"从 FORMAL_PLAN_FILES 移除 review-checklist.md" | 低 | §13 Commit 4 补充 | 已显式列在切换清单（不只在测试 bullet 提到） |
| **改进建议 #3** — IndependentReviewAgent.run() 骨架同步 30k preflight | 低 | §2.3 骨架补 | `_build_client()` 前加 word_count 检查，超 30k emit friendly error |

## R4 Round 4 Review Response Annex

Codex R4 verdict: **CHANGES_NEEDED**。R3 7 项全 FIXED；R1+R2 carry-over 全 HOLDS（R1 Bug 9 REOPENED）；新 3 Bug + 1 Suggestion：

| R4 Item | 严重度 | 处理章节 | 概述 |
|---|---|---|---|
| **Bug 18**（新）— Commit 1 加 FORMAL_PLAN_FILES 不是真 additive | 高 | §13 重写 Commit 重组 | Commit 1 加新文件 → `validate_plan_write` 接受 → 主代理可伪造写入（拒写拦截还没落）。修法：把 `FORMAL_PLAN_FILES` 加新文件 + 主代理拒写拦截放同一个 commit（Commit 2），Commit 1 真 100% dormant |
| **Bug 19**（新）— Commit 3 切 gate 但前端按钮在 Commit 4，非用户可用原子 cutover | 高 | §13 重写 Commit 重组 | 改为 Commit 4 是用户可见 atomic cutover：backend gate (CHECKPOINT_PREREQ) + SKILL 文档 + 前端按钮 + smoke 一次性落；Commit 1-3 全 dormant |
| **Bug 20**（新）— backend 伪代码误用 frontend camelCase `stageCode` | 高 | §2.1 / §3.6 / §6.1 修正 | 真实 backend `get_workspace_summary` 返回 snake_case `stage_code`（skill.py:1214-1223）；`stageCode` 只是前端 `summarizeWorkspace` 的映射（workspaceSummary.js:29-35）。所有 backend Python 伪代码改成 `workspace.get("stage_code")` |
| **R1 Bug 9 REOPENED** — 字段路径仍含 frontend 名 | 中 | §6.1 修正 | `_should_emit_s5_welcome` 引用 `workspace.checkpoints.review_started_at` 是对的，但 `stage_code` vs `stageCode` 这一处错了——见 Bug 20 |
| **Suggestion 10** — DeepSeek helper 行为矩阵测试不要用未绑定 instance method | 低 | §2.4 接受 | 测试创建最小 `ChatHandler` instance（或 mock），而不是 `ChatHandler._should_send_explicit_tool_choice(self, model)`；如果未来 helper 读 `self` 测试形态不会失真 |

## R3 Round 3 Review Response Annex

Codex R3 verdict: **CHANGES_NEEDED**。R2 修复 10/12 FIXED + 2 PARTIAL，新增 4 个必修 + 3 个 Suggestion：

| R3 Item | 严重度 | 处理章节 | 概述 |
|---|---|---|---|
| **Bug 11 + Bug 15**（PARTIAL/新）— chunk fallback 残留冲突 | 中 | §11 / §12 / §R1 Annex 全篇收敛 | 删除所有 chunk fallback 残留：§11 测试 `test_independent_review_agent_chunk_mode` 改 `test_independent_review_agent_word_count_over_30k_emits_error`；§12 风险表"v0 加 H2 chunk fallback" 改 "v0 friendly fail，v1 map-reduce worklist"；§R1 Annex Suggestion 2 标 SUPERSEDED |
| **Bug 13 + Bug 14**（PARTIAL/新）— Commit 拆分仍非原子可落 main | 高 | §13 重写 | 新方案：Commit 1 只做 additive backend + templates + tests（**不切** `CHECKPOINT_PREREQ` + SKILL.md 仍指向 review-checklist）；Commit 3 atomic cutover（endpoints + system_trigger + finalize 分支 + 主代理拒写 + lock + CHECKPOINT_PREREQ 切换 + SKILL/lifecycle/templates 同步）；这样每个 commit 自身可落 main，中间态不破坏 |
| **Bug 16**（新）— `s5_welcome_shown_at` load/save 路径不完整 | 高 | §6.1 补充 | 真实 `_load_conversation_state` 从 `_empty_conversation_state()` 重建白名单字段（chat.py:813-821 / 932-948 / 995-1013）；只改 save 白名单不够；必须同步改 `_empty_conversation_state()` 加 `"s5_welcome_shown_at": None` + `_load_conversation_state()` 复制逻辑（如果 payload 字段是非空 str 则复制） |
| **Bug 17**（新）— system-triggered turn `turn_context` 初始化不完整 | 高 | §5.2 补充 | 真实 turn_context 含 `checkpoint_event` / `stage_code_before_turn` / `s0_confirmation_completed` / `canonical_obligation` 等多字段；新路径必须显式调 `_build_turn_context(project_id, "")` 初始化，再设 `system_triggered=True`、清空 `canonical_obligation`、保证 `checkpoint_event=None`；否则会继承上一轮 stale context |
| **Suggestion 7** — Bug 10 加 edit_file 回归测试 | 低 | §11.1 接受 | 测试清单补 `test_main_agent_cannot_edit_independent_review_md` + `test_main_agent_cannot_edit_lint_report_md` |
| **Suggestion 8** — DeepSeek helper 等价测试用行为矩阵 | 低 | §2.4 接受 | 不用反射 / 字符串比对；改对同一批模型名跑两边 helper 断言返回一致：`deepseek-v4-pro` / `DeepSeek-Reasoner` / `gpt-4.1` / 空字符串 |
| **Suggestion 9** — PyInstaller 已自然打包，spec 不需要改 | 低 | §11.3 确认 | 当前 `consulting_report.spec:15-17` 已把整个 `skill/` 目录打入；新模板自然进 `_internal/skill/plan-template/`；保留存在性 smoke 即可 |

## R2 Round 2 Review Response Annex

Codex R2 verdict: **CHANGES_NEEDED**。10 个必修：6 个 R1 PARTIAL + 4 个新 Bug + 2 个 Suggestion。

| R2 Item | 严重度 | 处理章节 | 概述 |
|---|---|---|---|
| **Bug 1**（PARTIAL）— `_build_completed_items` 真实签名只有 `(stage_code, flags)`，不接 `stage_five_state` | 高 | §4.4 重写 | 改为通过 `flags["independent_review_ready"]` / `flags["lint_report_ready"]` 读；覆盖真实代码两处 S5 逻辑（行 1652 历史 + 行 1678 当前） |
| **Bug 3**（PARTIAL）— `consulting-lifecycle.md` 第 50 行未处理 | 中 | §7.2 补充 | 加第 50 行 "报告-only 项目可从 S5 直接进入 S7" 同步修改（其实这行可保留不动，但 spec 影响清单需说清） |
| **Bug 4**（PARTIAL）— DeepSeek 兼容 helper 实现写反了 | 高 | §2.3 重写 | 真实 `_should_send_explicit_tool_choice` 是 `"deepseek" not in active_model.lower()`（chat.py:443-446）；spec 写的 `"api.openai.com" in base_url` 完全反了。改为 copy 真实逻辑，加测试锁死 |
| **Bug 5**（PARTIAL）— `_finalize_assistant_turn` 真实在 chat.py:6211-6215 无条件 `history.extend([user, assistant])` | 高 | §5.2 重写 | 明确新增 `_finalize_assistant_only_turn` 分支，或在 `_finalize_assistant_turn` 加 `if turn_context.get("system_triggered"): history.extend([assistant])` 分支；保留向后兼容 |
| **Bug 6**（PARTIAL）— `run_quality_check` 真实返回 `stdout or stderr` | 中 | §3.6 修正 | 旧函数返回必须保留 `stdout or stderr` shape（不能改成 `.strip()`），否则 tests/test_report_tools.py:20-25 锁死的 stderr 失败回传会破坏 |
| **Bug 8**（PARTIAL）— `with get_lock()` 阻塞不返回 409；`record_stage_checkpoint` 才是 advance_stage 真正统一入口 | 高 | §3.6 + §4.4 + §13 修正 | lint endpoint 用 `acquire(blocking=False)` 失败即 409；`record_stage_checkpoint` 在 `review_passed_at` 路径加 lock 检查（不是只在 main.py endpoint）；chat tool 调用 advance_stage 也走 record_stage_checkpoint，自动覆盖 |
| **Bug 10**（新）— 主代理 write_file 仍能写新文件，破坏独立 second opinion 可信边界 | **关键** | §8.1 + §5.5 新增 | `chat.py:4775` 附近主代理 write_file 拦截分支显式拒绝 `plan/independent-review.md` 和 `plan/lint-report.md`；只有独立审查代理 / lint 脚本可写 |
| **Bug 11**（新）— §2.5 chunk fallback 无法跨章审查 | 中 | §2.5 重写 | v0 **不做** chunk fallback；超长正文直接 friendly fail "正文超过 30k 字暂不支持自动审查，建议拆分章节单独审查或精简正文" |
| **Bug 12**（新）— `s5_welcome_shown_at` 写入过早会吞掉欢迎信 | 中 | §6.1 修正 | 改为 `_finalize_assistant_turn` 成功后写；turn 失败 / 用户 abort 时不写，下次再发 |
| **Bug 13**（新）— Commit 拆分制造中间态破坏 | 中 | §13 重写 | Commit 1 合并 §7 文档/templates/test lock 同步（FORMAL_PLAN_FILES + S5 gate + SKILL.md + modules + templates 一起落） |
| **Suggestion 5** — 写明 module-level lock 单进程假设 | 低 | §9.3 接受 | 加 note："仅适用于当前单进程桌面部署；未来 uvicorn workers > 1 必须换文件锁/数据库锁" |
| **Suggestion 6** — `tests/smoke_packaged_app.py:40-53` EXPECTED_PLAN_FILES 同步 | 低 | §11.3 接受 | 显式说要把旧 `review-checklist.md` 替换为新两份文件 |

## R1 Round 1 Review Response Annex

Codex R1 verdict: **CHANGES_NEEDED**。9 个 Bug + 4 个 Suggestion + 8 个 Open Questions 处理对照表：

| R1 Item | 处理章节 | 概述 |
|---|---|---|
| Bug 1 — `_has_effective_review_checklist` 仍在生产路径 | §4.4 重写 | 改为状态模型扩展：`_stage_five_completion_state` 加 `independent_review_ready` / `lint_report_ready` / `review_reports_ready`；`CHECKPOINT_PREREQ.review_passed_at` 改用 `_has_effective_review_reports`；同步更新 `STAGE_CHECKLIST_ITEMS["S5"]` / `_build_completed_items` |
| Bug 2 — 新报告文件被 `validate_plan_write` 拒绝 | §8.1 / §8.2 重写 | `FORMAL_PLAN_FILES`：删 `review-checklist.md` + 加 `independent-review.md` + `lint-report.md`；`_initialize_project_structure` 复用现有"遍历 FORMAL_PLAN_FILES 复制模板"机制 |
| Bug 3 — 主提示词带回旧 S5 | §7.2 / §7.3 新增 | 同步修改 `skill/modules/consulting-lifecycle.md` + `plan-template/progress.md` + `plan-template/stage-gates.md` + `plan-template/tasks.md` |
| Bug 4 — IndependentReviewAgent 不兼容 DeepSeek | §2.3 重写 | 删除假 `_create_managed_openai_client` 引用；复用 `ChatHandler.client` 构造模式；显式 DeepSeek 三约束（不发 `tool_choice="auto"` / 保留 `reasoning_content` / 丢 null SDK dump 字段）；复用 `_should_send_explicit_tool_choice` + `_extract_reasoning_content_from_message` 现有 helper |
| Bug 5 — SSE / system_trigger 链路不可执行 | §2.1 / §5.1 / §5.2 / §5.4 重写 | 独立审查改 `fetch POST + ReadableStream`（不用 EventSource）；`ChatRequest.message_text` 改 `Optional` + Pydantic validator；ChatPanel 重构 `sendMessage` 为 `startStream({...})`；后端新增 `_chat_stream_system_triggered` 独立路径不写 user message 历史 |
| Bug 6 — quality-check 向后兼容破坏 | §3.3 / §3.5 / §3.6 重写 | 新增 `run_lint_report()` 函数 + 新脚本参数 `-FilePath` / `-OutputPath` / `-DryRun`；保留 `run_quality_check()` 旧返回 shape 不变；旧 `/quality-check` endpoint 行为不变 |
| Bug 7 — 软门禁口径不一 | §2.2 / §4.1 / §4.2 / §4.3 统一 | 严格 5/5 anchor；两份报告各加 completion marker（`<!-- independent-review:complete -->` / `<!-- lint-report:complete -->`）；system prompt / anchor 判定 / SKILL.md 三处口径统一 |
| Bug 8 — 并发与中断场景缺失 | §9.3 / §9.4 / §9.5 新增 | per-project review/lint running 状态；运行中禁用 S5 工具按钮 + 审查通过入口；endpoint 在开始与结束前校验仍处 S5；`review_passed_at` 在 running 状态下拒绝；SSE 断连策略 |
| Bug 9 — S5 欢迎信判定不可靠 | §6.1 重写 | 接受 `conversation_state.json` 加可选 `s5_welcome_shown_at` 字段（破例 Non-Goals §10）；判定改为 `checkpoints.review_started_at` + 该字段幂等 |
| Suggestion 1 — 加 outline.md / research-plan.md | §2.2 接受 | `outline.md` 必读；`research-plan.md` 存在则读 |
| ~~Suggestion 2 — token 超限给 fallback~~ | **SUPERSEDED by R2 Bug 11** | R2 改为 v0 不做 chunk fallback，超长直接 friendly fail（理由：chunk 无法跨章审查口径一致性 / 关键假设逻辑链）；本条 R1 建议已作废 |
| Suggestion 3 — `--dry-run` | §3.3 接受 | 脚本层支持，不暴露到 UI |
| Suggestion 4 — 打包 smoke 进 §11 | §11.3 接受 | §11 显式列 `tests/smoke_packaged_app.py` 覆盖 `/lint-report` / 旧 `/quality-check` / `/export-draft` |
| Open Q §1 max_iterations | §2.3 选 15 | 固定 5 读 + 1 写 + 1 finalize = 7，留 8 容错 |
| Open Q §2 启动延迟 | §5.4 不拍脑袋 | 等 `review-completed` event + `_has_effective_independent_review` 服务端确认 → 再触发 turn |
| Open Q §3 字段命名 | §5.1 用 `system_trigger` + Literal | OK |
| Open Q §4 4/5 vs 5/5 | §4.1 选 5/5 严格 + completion marker | OK |
| Open Q §5 审查文件清单 | §2.2 加 outline + 可选 research-plan | OK |
| Open Q §6 欢迎信判定 | §6.1 用 `s5_welcome_shown_at` 字段 | OK |
| Open Q §7 `--dry-run` | §3.3 脚本层支持 | OK |
| Open Q §8 drawer 取消 | §9.3 必须支持关闭/断开 + backend 处理断连 | OK |

---

## Context

S5 质量审查阶段当前是项目流程里**最不可信的一环**。

### 当前痛点

实测背景：用户在 piggy 项目跑 S0-S7 全流程时撞到——前面 S0-S4 都顺利，但到 S5 出现「AI 说审完了 / 系统说没审完」的割裂。

具体卡点：

1. **模型自评不可信**：当前 S5 要求模型自己填 `plan/review-checklist.md`，按 5 条 checkbox（事实核对、结论一致、结构逻辑、术语口径、表达清晰）自勾自检。这是"自己审自己"，没有独立 second opinion。
2. **契约割裂**：`backend/skill.py:_has_effective_review_checklist()` 判定需要 `≥3 个 - [x]` 全部勾选；但 DeepSeek V4 Pro 实测中常用表格 + `✅` 这种格式写审查清单，正则识别不到，导致用户看到 UI 提示「需要先补齐 review-checklist.md」与对话里「AI 已完成审查」并存。
3. **UX 暴露过早**：当前"运行质量检查"和"导出可审草稿"两个按钮在 `StagePanel.jsx:179-193` 是**无条件渲染**——所有阶段都显示。用户在 S0/S1/S2 看到"导出"和"质量检查"按钮，不知道什么时候该用。
4. **质量检查 vs 质量审查命名冲突**：当前 `quality_check.ps1` 脚本和 S5 阶段都叫"质量检查/质量审查"。中文里"检查"和"审查"在咨询语境基本同义，用户分不清这两个东西。

### 设计思路

抛弃"模型自评"路线，改成**两个用户主动触发的工具 + 独立审查代理（独立 LLM 上下文）+ 主代理通知机制**。

新流程：

```text
用户进入 S5
  ↓
主代理欢迎信 + UI 按钮亮起（独立审查 / AI 味自查）
  ↓
用户点"独立审查" → drawer 弹出
  ↓
独立审查代理读 data-log / analysis-notes / 正文 / references / outline / (research-plan 可选)
  ↓
按 5 个判断维度审查 → 落 plan/independent-review.md（带 completion marker）
  ↓
drawer 自动关 → 前端验证报告 ready → 自动起一轮主代理 turn
  ↓
主代理 read_file 读报告 → 跟用户讨论
  ↓
用户决定改 → 主代理按 S4 工具规则修改正文
  ↓
（用户点"AI 味自查"按钮，同上流程）
  ↓
用户认为审查通过 → 主代理调 advance_stage(review_passed_at)
  ↓
后端校验两份报告 exist 且非 stub → 推进 S6/S7
```

关键架构选择：**不是主代理调子代理工具，而是按钮触发独立 LLM 会话 + 文件落盘 + 前端事件链**。这避免了 agent-to-agent RPC 协议的复杂性。

## Goals

1. 砍掉模型自评 `review-checklist.md` 机制；改成两个用户主动触发的工具
2. 新增独立审查代理（独立 LLM 上下文 + 独立 system prompt + 独立工具集，禁用主代理工具）
3. 改造 AI 味自查脚本（4 维度，报告按章节排列）
4. 三个按钮（独立审查 / AI 味自查 / 导出可审草稿）按阶段条件显示，未达到阶段不渲染
5. 新增"server 完成 → 前端验证 ready → 自动起主代理 turn"机制，不等用户下一轮交互
6. S5 进入时给用户 UX 提示（主代理欢迎信 + UI 按钮高亮 + StagePanel 下一步建议文字）
7. 软门禁判定：两份报告文件 exist + 5/5 anchor + completion marker = 可推进 S6/S7
8. 旧 `review-checklist.md` 模板与判定逻辑退役迁移，向后兼容老项目

## Non-Goals（硬约束）

用户原话：**"前面 s0 到写完文章的流程基本上都顺了的，所以改代码注意不要改到前面的东西，那些逻辑别又改坏了。"** 这是本 spec 最高优先级硬约束。

1. **S0-S4 任何核心逻辑不动**——包括但不限于：
   - `backend/skill.py:_infer_stage_state()` 中 S0-S4 投影
   - `backend/skill.py` 中 `_stage_one_completion_state` / `_stage_four_completion_state`
   - `backend/chat.py` 中 S0 interview 软门禁、`fetch_url` 前置门禁、`append_report_draft` / `edit_file` canonical draft dispatcher
   - `backend/report_writing.py` 全文
   - `_has_effective_data_log()` / `_has_effective_analysis_notes()` / `_has_effective_report_draft()` 判定
2. **S6/S7 核心流程不动**——只动按钮显示条件和 `_stage_five_completion_state` 返回的 S5 状态字段；`_stage_six_completion_state` / `_stage_seven_completion_state` 通过 stage_five_state 接收新字段，不需要改自身实现
3. **不实现 agent-to-agent 消息协议**——主代理与独立审查代理通过文件 + 前端事件链通信
4. **不重做主聊天 UI**——Drawer 是新增组件；ChatPanel 内部 `sendMessage` 重构为 `startStream`，但主流式逻辑（accumulate / render / abort）不动
5. **不引入新模型 provider**——独立审查代理复用 `ChatHandler.client` 同款 OpenAI client 构造（`api_key=settings.api_key, base_url=settings.api_base`）
6. **不动 `advance_stage` 工具本身**——只在 `CHECKPOINT_PREREQ` 改 review_passed_at 元组中的 helper 函数名
7. **不动其他 PowerShell 脚本**——`export_draft.ps1` 保留现状，只动 `quality_check.ps1`
8. **不实现独立审查代理的多轮迭代/自我修正**——一次跑完落盘，失败让用户重按按钮（max_iterations=15 内）
9. **不实现 conversation history persistence**——独立审查代理无状态，每次重头
10. **`conversation.json` schema 不变**；**`conversation_state.json` 仅新增可选 `s5_welcome_shown_at` 字段（向后兼容，老 schema 不读到该字段时默认未发欢迎信）**
11. **不动 `web_search` / `fetch_url` / `read_file` / `write_file` / `edit_file` / `append_report_draft` / `advance_stage` 工具实现**
12. **不引入新依赖**——前端 Drawer 用现有 React + Tailwind 实现，不引 `react-rnd` 等
13. **不做 Linux / macOS 适配**——`quality_check.ps1` 重构后仍是 PowerShell-only
14. **不删 `_emit_system_notice_once` 调用**——保留现有所有 system notice 链路

## Design Summary

### 模块影响清单

**新增**：

| 文件 | 用途 |
|---|---|
| `backend/independent_review.py` | 独立审查代理实现 |
| `frontend/src/components/IndependentReviewDrawer.jsx` | drawer 组件 |
| `skill/plan-template/independent-review.md` | stub 模板（带 marker 占位） |
| `skill/plan-template/lint-report.md` | stub 模板（带 marker 占位） |
| `tests/test_independent_review.py` | 独立审查代理单元测试 |
| `tests/test_lint_report.py` | 脚本输出测试 |
| `frontend/tests/independentReviewDrawer.test.mjs` | 前端组件测试 |
| `frontend/tests/stagePanelButtons.test.mjs` | 按钮阶段化测试 |

**修改**：

| 文件 | 修改范围 |
|---|---|
| `backend/main.py` | 新增 `GET /api/projects/{id}/independent-review/stream`（SSE）+ `POST /api/projects/{id}/lint-report`；`chat_stream` 处理新增 `system_trigger` 字段 |
| `backend/chat.py` | `system_trigger` 入口 + `_chat_stream_system_triggered` 新方法；S5 进入欢迎信注入逻辑（基于 `s5_welcome_shown_at`） |
| `backend/models.py` | `ChatRequest.message_text` 改 `Optional` + 加 Pydantic validator；加 `system_trigger` 字段 |
| `backend/skill.py` | `FORMAL_PLAN_FILES` 切换；新增 `_has_effective_independent_review` / `_has_effective_lint_report` / `_has_effective_review_reports`；`_stage_five_completion_state` 扩展字段；`CHECKPOINT_PREREQ.review_passed_at` 切换 helper；`STAGE_CHECKLIST_ITEMS["S5"]` 更新；`_build_completed_items` S5 逻辑更新；`get_workspace_summary` 加 flags + next_stage_hint；`_load_conversation_state` / `_save_conversation_state_atomically` 兼容新字段 |
| `backend/report_tools.py` | 新增 `run_lint_report(report_path, output_path, script_path, dry_run=False)`；`run_quality_check()` 旧函数保留 shape 不变 |
| `skill/scripts/quality_check.ps1` | 重构为 4 维度规则集 + `-FilePath` / `-OutputPath` / `-DryRun` 参数；旧 stdout 模式（无 `-OutputPath`）保留以维持向后兼容 |
| `skill/SKILL.md` | S5 段重写 |
| `skill/modules/consulting-lifecycle.md` | 第 20 行 + 第 50 行 S5 描述同步 |
| `skill/plan-template/progress.md` | 第 42 行 S5 表格行同步 |
| `skill/plan-template/stage-gates.md` | 第 40-43 行 S5 段同步 |
| `skill/plan-template/tasks.md` | 第 44-45 行 S5 段同步 |
| `skill/plan-template/review-checklist.md` | 保留模板文件（向后兼容旧测试），但从 `FORMAL_PLAN_FILES` 移除 |
| `frontend/src/components/StagePanel.jsx` | 按钮阶段条件渲染 + 独立审查/AI 味自查新按钮 + S5 高亮逻辑 + StagePanel 下一步建议文字 |
| `frontend/src/components/WorkspacePanel.jsx` | drawer trigger 处理 + axios 调用新 endpoint + per-project running 状态 |
| `frontend/src/components/ChatPanel.jsx` | `sendMessage` 重构为 `startStream({ messageText, systemTrigger, attachedMaterialIds, ... })`；user bubble 渲染条件化 |
| `frontend/src/utils/workspaceSummary.js` | 加 `independentReviewReady` / `lintReportReady` / `reviewReportsReady` 字段 |

**退役**：

| 资产 | 处理 |
|---|---|
| `_has_effective_review_checklist()` | 函数本身保留（向后兼容老 unit test），但不再被 `CHECKPOINT_PREREQ` / `_stage_five_completion_state` / `_build_completed_items` 调用 |
| 旧"运行质量检查"按钮 | UI 删除（`StagePanel.jsx`）；`POST /api/projects/{id}/quality-check` endpoint 保留 + 返回 shape 不变（向后兼容外部脚本） |
| `plan-template/review-checklist.md` | 模板文件保留；从 `FORMAL_PLAN_FILES` 移除；老项目已有的 `plan/review-checklist.md` 不删 |

### 数据流图

```text
[用户点"独立审查"按钮]
  ↓
[前端 StagePanel onClick] disable 按钮 + 设置 reviewRunning state
  ↓
[前端 IndependentReviewDrawer] fetch GET /api/projects/{id}/independent-review/stream (SSE)
  ↓
[backend/main.py: independent_review_stream endpoint]
  ↓ 校验 stageCode == S5 且当前 project 没有正在跑的审查
  ↓ 创建 IndependentReviewAgent + 注入 run_id
[backend/independent_review.py: IndependentReviewAgent.run()]
  ↓ system prompt 注入（5 维度刻死 + completion marker 要求）
  ↓ tool loop: read_file × N → write_file(plan/independent-review.md)
  ↓ 每一步 emit SSE event: progress / content / tool_call / tool_result
  ↓ 完成时验证 marker → emit SSE event: review-completed { path, run_id }
  ↓
[前端 drawer]
  ↓ 显示流式工作过程
  ↓ 收到 review-completed → 自动关 drawer
  ↓ axios.get('/api/projects/{id}/workspace') 验证 flags.independent_review_ready === true
  ↓ 如果 ready：调用 ChatPanel.startStream({ systemTrigger: "independent_review_done" })
  ↓ 如果 not ready：显示错误 "审查报告不完整，请重试"
[ChatPanel.startStream]
  ↓ 不渲染 user bubble（renderUserBubble=false）
  ↓ fetch POST /api/chat/stream { project_id, message_text: "", system_trigger: "independent_review_done" }
[backend/main.py: chat_stream endpoint]
  ↓ ChatRequest validator 接受空 message_text + system_trigger
  ↓ ChatHandler.chat_stream 检测到 system_trigger
  ↓ 进入 _chat_stream_system_triggered 独立路径
  ↓ 不写 user message 到 conversation.json
  ↓ 注入 system message: "独立审查报告已生成（plan/independent-review.md）..."
  ↓ 主代理 turn 正常流程（read_file → 输出回复）
  ↓ assistant message 正常持久化到 conversation.json
  ↓
[前端 ChatPanel] 用户看到主代理 stream，跟普通对话一样
  ↓
[用户决定改] → [主代理 edit_file / append_report_draft 改正文]
  ↓
[用户点"AI 味自查"按钮] → POST /api/projects/{id}/lint-report (同步) → 返回 { path, summary } → 同样的 chat_stream(system_trigger="lint_report_done") 链
  ↓
[用户最终回答"审查通过"] → [主代理 advance_stage(review_passed_at)]
  ↓
[record_stage_checkpoint → _validate_stage_checkpoint_transition → _stage_five_completion_state]
  ↓ review_reports_ready 检查通过 → 写 stage_checkpoints.json → 推进 S6/S7
```

## Detailed Design

### 1. 按钮阶段化显示

#### 1.1 现状

`frontend/src/components/StagePanel.jsx:179-193`:

```jsx
<div className="flex gap-2 mt-4">
  <button onClick={onRunQualityCheck} ...>运行质量检查</button>
  <button onClick={onExportDraft} ...>导出可审草稿</button>
</div>
```

无任何阶段条件。`onRunQualityCheck` 走 `POST /api/projects/{id}/quality-check`，`onExportDraft` 走 `POST /api/projects/{id}/export-draft`。

#### 1.2 新规则

| 按钮 | 显示条件 | 备注 |
|---|---|---|
| **独立审查** | `stageCode === 'S5'` | S5 阶段才出现；未跑过时高亮 |
| **AI 味自查** | `stageCode === 'S5'` | 同上 |
| **导出可审草稿** | `stageCode in ['S6', 'S7', 'done']` AND `summary.flags.report_draft_ready === true` | S5 不能导出（还在审查）|
| ~~运行质量检查~~ | **删除** | 旧按钮，UI 不再出现 |

未达到条件时按钮**不渲染**（不是 disabled）——避免用户疑惑"为什么按了没反应"。

#### 1.3 高亮逻辑

S5 阶段进入时（`stageCode === 'S5'`），按钮未跑过时增加 `btn-highlight-pulse` CSS class（呼吸动画，opacity 0.7 ↔ 1.0 cycle）。已跑过时取消高亮。

判断"已跑过"：

```js
const independentReviewDone = summary.flags?.independent_review_ready === true
const lintReportDone = summary.flags?.lint_report_ready === true
```

这两个 flag 由后端 `_has_effective_independent_review` / `_has_effective_lint_report` 分别检测后填入 workspace summary。

**Running 状态**（防止并发，见 §9.3）：当前端处于 `reviewRunning === true` 或 `lintRunning === true` 时，所有 S5 工具按钮（独立审查 / AI 味自查 / 进入下一阶段）一律 disabled。

#### 1.4 StagePanel 下一步建议文字

`StagePanel` 已有"下一步建议"区域。S5 阶段时按报告完成度展示不同提示：

| 状态 | 文字 |
|---|---|
| S5 且无任何报告 | "请点击上方'独立审查'和'AI 味自查'按钮" |
| S5 且只跑了独立审查 | "还差'AI 味自查'，请点击上方按钮" |
| S5 且只跑了 AI 味自查 | "还差'独立审查'，请点击上方按钮" |
| S5 且两份都跑了 | "等主代理跟你讨论审查结果，确认通过后说'审查通过'" |

这个文字通过 `workspaceSummary.nextActions` 在后端 `get_workspace_summary` 计算（具体逻辑在 §10）。

### 2. 独立审查代理

#### 2.1 Endpoint

`GET /api/projects/{project_id}/independent-review/stream`

> **R1 Bug 5 修正**：原 spec 写 `POST` + `EventSource`，但 EventSource 只能 GET。本节改为 GET SSE endpoint。如果未来需要 POST body 携带配置（如 `force_refresh`），可改为 `fetch POST + ReadableStream` 模式（与 ChatPanel 现有 stream 处理一致），但 v0 用 GET。

**响应**：SSE stream (`text/event-stream`)

**SSE 事件类型**：

| event | data | 用途 |
|---|---|---|
| `progress` | `{"type": "progress", "step": "reading\|thinking\|writing", "detail": "..."}` | drawer 顶部进度提示 |
| `content` | `{"type": "content", "text": "..."}` | drawer 主体流式追加文本 |
| `tool_call` | `{"type": "tool_call", "tool": "read_file", "args": {...}}` | drawer 显示"正在读 X 文件" |
| `tool_result` | `{"type": "tool_result", "tool": "read_file", "status": "success"\|"error", "summary": "..."}` | drawer 显示工具结果 |
| `review-completed` | `{"type": "review-completed", "path": "plan/independent-review.md", "run_id": "..."}` | drawer 据此自动关 + 触发主代理 turn |
| `error` | `{"type": "error", "detail": "..."}` | drawer 显示错误后自动关 |

事件 wire 格式沿用 chat stream 已有约定：

```text
data: {"type": "progress", "step": "reading", "detail": "data-log.md"}\n\n
data: {"type": "tool_call", "tool": "read_file", "args": {"file_path": "plan/data-log.md"}}\n\n
data: {"type": "review-completed", "path": "plan/independent-review.md", "run_id": "abc123"}\n\n
data: [DONE]\n\n
```

**前置校验**（endpoint 入口）：

- 当前 stage 必须 == `S5`（通过 `get_workspace_summary` 读 `stageCode`）；非 S5 返回 `400 {"detail": "独立审查只能在 S5 阶段使用"}`
- 当前 project 没有正在跑的独立审查（per-project running 状态，见 §9.3）；并发返回 `409 {"detail": "上一次独立审查仍在进行中，请等待"}`
- 项目必须存在；不存在返回 `404`

#### 2.2 独立审查代理 System Prompt

写死在 `backend/independent_review.py`，**不暴露给用户编辑**：

```text
你是独立审查代理。你的任务是对咨询报告的草稿做独立、客观的审查，不参与写作、不修改任何文件以外的内容。

## 你将读取的文件

必读：
- plan/data-log.md — 资料与数据登记
- plan/analysis-notes.md — 分析沉淀
- content/report_draft_v1.md — 报告正文草稿
- plan/references.md — 引用清单
- plan/project-overview.md — 项目元信息（含目标读者、交付边界）
- plan/outline.md — 报告大纲（核对结构与正文匹配）

可选（存在则读，不存在跳过）：
- plan/research-plan.md — 研究设计（核对分析路径与原计划匹配）

## 你的审查维度（5 条，缺一不可，**全部 5 个章节必须输出**）

### 1. 结论-证据一致性
每个核心结论是否能追溯到 data-log / analysis-notes 的具体支撑？引用的数据方向是否真的支持结论（不是相关词出现就当支持）？

### 2. 关键假设与逻辑链
问题→分析→结论→建议链条有无跳跃、断层？隐含假设是否被明确暴露给读者？如果某个关键假设不成立，结论会不会垮？

### 3. 数据口径一致性
同一指标（市场规模、增速、份额、政策口径等）在不同章节出现时是否口径统一、数字不打架？

### 4. 建议可执行性
每条建议是否回答了"谁来做、做什么、何时、优先级"？空话建议（"加强 X / 提升 Y / 推动 Z"）必须直接点名。

### 5. 目标读者匹配
术语密度、论证深度、前提假设是否匹配 project-overview 里写明的目标读者？

## 输出格式

写一个 markdown 文件到 plan/independent-review.md。结构严格如下（标题层级、维度顺序、章节命名都不要改）：

# 独立审查报告

**审查时间**：[ISO 8601 当前时间]
**审查代理**：DeepSeek V4 Pro · independent-review
**审查范围**：data-log / analysis-notes / report_draft_v1 / references / outline (+ research-plan)

---

## 1. 结论-证据一致性

### 1.1 [一句话判断]
- **位置**：[报告第几章 / 第几段，越具体越好]
- **原文**：[引用 1-3 句]
- **问题**：[一句话分析]
- **修改方向**：[方向性建议，不要写具体句子]

（每个维度下 1-N 条 issue；如果该维度未发现问题，**仍要保留该维度的章节标题**，并在标题下写"未发现问题"）

## 2. 关键假设与逻辑链
...

## 3. 数据口径一致性
...

## 4. 建议可执行性
...

## 5. 目标读者匹配
...

---

## 总体判断

[partner review 风格 1-2 段：判断报告整体可发可不发；如不可发，哪几个 issue 必须先改]

<!-- independent-review:complete -->

## 完成标记的硬性要求

报告**末尾必须**输出 `<!-- independent-review:complete -->` 这一行 HTML 注释。这是系统识别审查完成的契约。如果你写的报告里没有这行，系统会判定为不完整，用户会被要求重新审查。

5 个维度的 H2 章节标题（`## 1. 结论-证据一致性` 一直到 `## 5. 目标读者匹配`）**必须全部出现**——即使某维度无问题也要写"## X. [维度名]\n\n未发现问题"，不能省略。

## 语气规则

- **直接、有据**，绝不出现"建议考虑""可以探讨""值得思考"这类模糊词
- 每个 issue 必须有原文位置和具体修改方向
- **宁少而精**：5 条维度里没问题的维度写"未发现问题"，不要为了凑数胡编

## 工作流

1. 先 read_file 读上述 6 个必读文件（按你认为合理的顺序）；如果 plan/research-plan.md 存在也读
2. 在脑中按 5 个维度做完整审查
3. 一次性 write_file 把完整报告写到 plan/independent-review.md
4. 报告写完即结束，不做任何其他动作

## 工具集

你只有两个工具：
- read_file(file_path) — 读项目文件
- write_file(file_path, content) — 写文件，但只能写到 plan/independent-review.md，其他路径会被拒绝

其他工具不可用。不要尝试调用 edit_file / append_report_draft / advance_stage / web_search / fetch_url / quality_check。
```

#### 2.3 独立审查代理 Implementation

> **R1 Bug 4 修正**：原 spec 引用 `_create_managed_openai_client` 不存在；现统一复用 `ChatHandler.client` 构造方式 + 复用 `_should_send_explicit_tool_choice` / `_extract_reasoning_content_from_message` 现有 helper。

新模块 `backend/independent_review.py`：

```python
"""Independent review agent — 独立审查代理（S5 阶段工具）。"""

from typing import Iterator
from pathlib import Path
import json
import threading
from datetime import datetime, timezone

import httpx
from openai import OpenAI

from .skill import SkillEngine
from .config import Settings


INDEPENDENT_REVIEW_SYSTEM_PROMPT = """[见 §2.2]"""

# 独立审查代理只能写这一个路径
CANONICAL_REVIEW_PATH = "plan/independent-review.md"

# 完成标记 marker
INDEPENDENT_REVIEW_COMPLETION_MARKER = "<!-- independent-review:complete -->"

# 5 维度 anchor（用于 _has_effective_independent_review 校验）
INDEPENDENT_REVIEW_ANCHORS = [
    "## 1. 结论-证据一致性",
    "## 2. 关键假设与逻辑链",
    "## 3. 数据口径一致性",
    "## 4. 建议可执行性",
    "## 5. 目标读者匹配",
]

# 工具 schema，只暴露 read_file / write_file
INDEPENDENT_REVIEW_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取项目文件内容",
            "parameters": {
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
                "required": ["file_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "写文件，只允许写 plan/independent-review.md",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "content": {"type": "string"}
                },
                "required": ["file_path", "content"]
            }
        }
    }
]


class IndependentReviewAgent:
    """独立审查代理。每个 project 一个 instance，但同时只能 run 一次。"""
    
    MAX_ITERATIONS = 15  # R1 Open Q §1 决议

    def __init__(self, skill_engine: SkillEngine, settings: Settings):
        self.skill_engine = skill_engine
        self.settings = settings

    def _build_client(self) -> OpenAI:
        """复用 ChatHandler 同款 OpenAI client 构造方式"""
        http_client = httpx.Client(timeout=120.0)
        return OpenAI(
            api_key=self.settings.api_key,
            base_url=self.settings.api_base,
            http_client=http_client,
        )

    def _resolve_model(self) -> str:
        if self.settings.mode == "managed":
            return self.settings.managed_model
        return self.settings.model

    def _should_send_explicit_tool_choice(self, active_model: str) -> bool:
        """**与 chat.py:443-446 真实实现严格一致**：
        DeepSeek 官渠 reasoner route 拒绝 tool_choice="auto"，所以名字含 'deepseek' 时不发；
        其他 provider（含 OpenAI / 自定义 GPT-*）显式发 auto。
        实现必须 copy chat.py 完全相同的逻辑，不要 paraphrase；R2 Bug 4 catch 过我们误把逻辑反过来。
        """
        return "deepseek" not in (active_model or "").lower()

    def _extract_reasoning_content_from_message(self, message) -> str:
        """复用主代理 helper：抽 reasoning_content"""
        reasoning = getattr(message, "reasoning_content", None)
        if isinstance(reasoning, str) and reasoning:
            return reasoning
        try:
            dumped = message.model_dump(exclude_none=False)
            reasoning = dumped.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning:
                return reasoning
        except Exception:
            pass
        return ""

    def _serialize_assistant_tool_call_message(self, message) -> dict:
        """复用主代理 helper：序列化 assistant tool_call message，丢 null SDK dump 字段，保留非空 reasoning_content。"""
        msg_dict = {
            "role": "assistant",
            "content": message.content or "",
        }
        if message.tool_calls:
            msg_dict["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in message.tool_calls
            ]
        reasoning = self._extract_reasoning_content_from_message(message)
        if reasoning:
            msg_dict["reasoning_content"] = reasoning
        return msg_dict

    def run(self, project_id: str) -> Iterator[dict]:
        """运行审查；yield SSE event dict（caller 负责 wire 序列化）。"""
        # R5 改进建议 #3 + R2 Bug 11：preflight 检查正文长度，超 30k 字直接 friendly fail
        try:
            report_path = self.skill_engine.get_primary_report_path(project_id)
            report_text = Path(report_path).read_text(encoding="utf-8")
            word_count = self.skill_engine._count_words(report_text)
            if word_count > 30000:
                yield {
                    "type": "error",
                    "detail": f"正文超过 30k 字（当前 {word_count} 字），暂不支持自动审查。建议先精简正文或拆分章节单独审查。"
                }
                return
        except Exception as e:
            yield {"type": "error", "detail": f"读取正文失败：{str(e)}"}
            return

        client = self._build_client()
        model = self._resolve_model()

        messages = [
            {"role": "system", "content": INDEPENDENT_REVIEW_SYSTEM_PROMPT}
        ]

        iteration = 0
        review_written = False

        while iteration < self.MAX_ITERATIONS:
            iteration += 1
            yield {"type": "progress", "step": "thinking", "detail": f"第 {iteration} 轮"}

            request_kwargs = {
                "model": model,
                "messages": messages,
                "tools": INDEPENDENT_REVIEW_TOOLS,
                "stream": False,
            }
            if self._should_send_explicit_tool_choice(model):
                request_kwargs["tool_choice"] = "auto"
            # DeepSeek 官渠：不显式传 tool_choice（已通过上面的判定）

            try:
                response = client.chat.completions.create(**request_kwargs)
            except Exception as e:
                yield {"type": "error", "detail": f"模型调用失败：{str(e)}"}
                return

            choice = response.choices[0]
            msg = choice.message

            # 累积 assistant message（保留 reasoning_content + 丢 null SDK dump 字段）
            messages.append(self._serialize_assistant_tool_call_message(msg))

            if msg.content:
                yield {"type": "content", "text": msg.content}

            if not msg.tool_calls:
                # 没有 tool_call 了，但还没写报告 → 强制结束
                if not review_written:
                    yield {"type": "error", "detail": "审查代理未生成报告，请重试"}
                    return
                # 验证 marker 存在
                if not self._verify_completion_marker(project_id):
                    yield {"type": "error", "detail": "审查报告缺少完成标记，请重试"}
                    return
                yield {"type": "review-completed", "path": CANONICAL_REVIEW_PATH}
                return

            # 处理每个 tool_call
            for tc in msg.tool_calls:
                tool_name = tc.function.name
                try:
                    tool_args = json.loads(tc.function.arguments)
                except Exception:
                    tool_args = {}

                yield {"type": "tool_call", "tool": tool_name, "args": tool_args}

                result = self._execute_tool(project_id, tool_name, tool_args)
                yield {
                    "type": "tool_result",
                    "tool": tool_name,
                    "status": result.get("status", "error"),
                    "summary": result.get("summary", ""),
                }

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })

                if (
                    tool_name == "write_file"
                    and result.get("status") == "success"
                    and tool_args.get("file_path") == CANONICAL_REVIEW_PATH
                ):
                    review_written = True

        yield {"type": "error", "detail": f"审查超时（超过 {self.MAX_ITERATIONS} 轮），请重试"}

    def _execute_tool(self, project_id: str, tool_name: str, args: dict) -> dict:
        """执行 read_file / write_file；write_file 强制 path 白名单。"""
        if tool_name == "read_file":
            try:
                content = self.skill_engine.read_file(project_id, args.get("file_path", ""))
                return {"status": "success", "content": content, "summary": f"读取 {len(content)} 字"}
            except Exception as e:
                return {"status": "error", "detail": str(e), "summary": "读取失败"}

        if tool_name == "write_file":
            file_path = args.get("file_path", "")
            if file_path != CANONICAL_REVIEW_PATH:
                return {
                    "status": "error",
                    "detail": f"独立审查代理只能写 {CANONICAL_REVIEW_PATH}，请求被拒",
                    "summary": "路径不允许",
                }
            try:
                # 通过 SkillEngine.write_file 走标准 plan 写入路径；
                # 由于 §8.1 把 independent-review.md 加进 FORMAL_PLAN_FILES，
                # validate_plan_write 会接受这个路径
                self.skill_engine.write_file(project_id, file_path, args.get("content", ""))
                return {"status": "success", "summary": "审查报告已写入"}
            except Exception as e:
                return {"status": "error", "detail": str(e), "summary": "写入失败"}

        return {"status": "error", "detail": f"未知工具 {tool_name}", "summary": "未知工具"}

    def _verify_completion_marker(self, project_id: str) -> bool:
        """验证报告末尾有 completion marker"""
        try:
            text = self.skill_engine.read_file(project_id, CANONICAL_REVIEW_PATH)
            return INDEPENDENT_REVIEW_COMPLETION_MARKER in text
        except Exception:
            return False


# 并发控制：per-project running 状态
_INDEPENDENT_REVIEW_LOCKS: dict[str, threading.Lock] = {}
_INDEPENDENT_REVIEW_LOCKS_GUARD = threading.Lock()


def get_independent_review_lock(project_id: str) -> threading.Lock:
    with _INDEPENDENT_REVIEW_LOCKS_GUARD:
        if project_id not in _INDEPENDENT_REVIEW_LOCKS:
            _INDEPENDENT_REVIEW_LOCKS[project_id] = threading.Lock()
        return _INDEPENDENT_REVIEW_LOCKS[project_id]
```

#### 2.4 与主代理的隔离

独立审查代理：

- **独立 messages 数组**：不复用主代理的 conversation_state，每次从空 messages + system prompt 重新开始
- **独立 OpenAI client instance**：每次 `run()` 调用 `_build_client()` 创建新实例（避免 streaming state 互相污染）；不复用 `ChatHandler.client`
- **DeepSeek 兼容逻辑——copy + 锁定测试**：`_should_send_explicit_tool_choice` / `_extract_reasoning_content_from_message` / `_serialize_assistant_tool_call_message` 这三个 helper 在 `chat.py` 是 `ChatHandler` instance method（行 443 / 3189 / 类似位置）。**独立审查代理重新实现一份等价版本**——避免循环 import + 避免改 chat.py 影响主代理。实现要点：
  - `_should_send_explicit_tool_choice` 严格 copy chat.py:443-446 逻辑：`return "deepseek" not in active_model.lower()`
  - `_extract_reasoning_content_from_message` 同 chat.py:3189-3206
  - `_serialize_assistant_tool_call_message` 同 chat.py:3214 附近
  - 加 `tests/test_independent_review.py::test_deepseek_compat_helpers_match_chat_helpers`——**用行为矩阵不用源码比对**（R3 Suggestion 8 + R4 Suggestion 10）：对同一批模型名跑两边 helper 断言返回一致：
  ```python
  TEST_MODELS = [
      "deepseek-v4-pro", "DeepSeek-Reasoner", "deepseek-chat",
      "gpt-4.1", "gpt-4o-mini", "claude-sonnet-4-6",
      "managed-custom-model", "",  # 空字符串边界
  ]
  
  # R4 Suggestion 10：必须创建最小 instance，不要 ChatHandler._should_send_explicit_tool_choice(self, model)
  # 后者依赖 helper 不读 self；如果未来 helper 读实例字段，测试形态会失真
  fake_settings = Settings(mode="managed", managed_model="deepseek-v4-pro", ...)
  chat_handler = ChatHandler(skill_engine=fake_skill, settings=fake_settings)
  ir_agent = IndependentReviewAgent(skill_engine=fake_skill, settings=fake_settings)
  
  for model in TEST_MODELS:
      chat_result = chat_handler._should_send_explicit_tool_choice(model)
      ir_result = ir_agent._should_send_explicit_tool_choice(model)
      assert chat_result == ir_result, f"Diverged on {model!r}: chat={chat_result}, ir={ir_result}"
  ```
  这比反射 / 字符串比对更稳——chat.py 未来改实现逻辑时只要行为不变，测试也不会误警。
- **独立工具集**：只有 `read_file` + `write_file`，**不暴露** `advance_stage` / `edit_file` / `append_report_draft` / `web_search` / `fetch_url` / `quality_check`
- **写入路径强制**：`write_file` 拒绝任何非 `plan/independent-review.md` 的路径
- **不触发主代理 stage gate**：独立审查代理调用 `SkillEngine.read_file` / `SkillEngine.write_file`，这些函数本身不调主代理的 turn_context / fetch_url_gate / canonical draft mutation count（这些都是 chat.py 主代理路径里的，与 SkillEngine 基础 IO 解耦）
- **不写 conversation.json**：审查会话不持久化到主对话历史

#### 2.5 Token Budget 与超长处理

> **R2 Bug 11 修正**：v0 **不做** chunk fallback。理由：chunk fallback 无法可靠覆盖跨章审查维度（数据口径一致性、关键假设逻辑链都需要跨章看），降级报告还要保证 5 anchors + marker，复杂度高且效果不可控。v0 用 friendly failure。完整 map-reduce 设计推到 v1。

| 输入 | 估算 |
|---|---|
| data-log.md | 5-15k 字 |
| analysis-notes.md | 10-20k 字 |
| report_draft_v1.md | 20-50k 字 |
| references.md | 1-3k 字 |
| project-overview.md | 0.5-2k 字 |
| outline.md | 1-3k 字 |
| **总输入** | 50-100k 字 ≈ 30-60k tokens |

输出审查报告：600-1000 字 ≈ 500-800 tokens

DeepSeek V4 Pro context window：200k tokens，对绝大多数项目安全。

**超长处理策略**（report_draft 超过 30k 字时）：

- IndependentReviewAgent 在 `_build_client` 前先 `_count_words(report_draft)`
- 如果 > 30k 字（阈值）：**直接 emit error event，不启动 LLM call**：
  ```json
  {"type": "error", "detail": "正文超过 30k 字（当前 X 字），暂不支持自动审查。建议先精简正文或拆分章节单独审查。"}
  ```
- 前端 drawer 显示此错误 3 秒后自动关闭；按钮重新 enabled
- 用户可手动精简正文或选择继续不审查（强行 `advance_stage(review_passed_at)` 会被软门禁拒，但用户可以选择不进 S6 / S7）

**为什么 v0 不分块审查**：

- chunk 之间无共享上下文 → 数据口径一致性维度（同一指标多章不同数字）查不到
- chunk 之间无共享上下文 → 关键假设逻辑链（前提是否在后续章节被推翻）查不到
- 合并 chunk findings 时仍要一次完整 LLM call 做 synthesis，这次 call 仍可能 hit token 限制
- 降级报告必须保证 5 anchors + marker 才能通过软门禁——chunk mode 输出格式难以稳定保证

**v1 规划**（写进 worklist，不在本 spec 实施）：完整 map-reduce — 每章 LLM call 产结构化 findings + 全局 synthesis call 读 outline + project-overview + 跨章指标表 + 全部 finding 产 5 维度报告，并明确标 degraded coverage 警示。

### 3. AI 味自查脚本改造

> **R1 Bug 6 修正**：新增 `run_lint_report()` 函数 + 脚本参数 + 保留 `run_quality_check()` 旧返回 shape。

#### 3.1 现状

`skill/scripts/quality_check.ps1`（183 行）执行 9 条正则检查，输出到 stdout，由 `backend/report_tools.py:run_quality_check` 捕获返回给前端。

格式：每条 finding 一行 `"Level | Title\n  行号: 原文"`，末尾 summary 三个 count。

#### 3.2 新规则集（4 维度）

| 维度 | 触发规则 | 报告标签 |
|---|---|---|
| **1. AI 写作口癖** | 合并：元叙事词（本章/本报告/后文/下文/换言之/综上所述）+ 机械过渡词（首先/其次/最后/此外/另外/接下来 **同章连续出现 ≥3 次**）+ 空洞强调句（值得注意的是/重要的是/必须强调的是）+ 空洞形容词（非常/极其/十分/相当，**negative lookahead 排除数据语境**：`非常\s*显著`、`极其\s*\d+%` 不报） | `AI 腔` |
| **2. 内容完整性** | 占位符（XXX/TBD/TODO/待补/待确认）+ 后台推进表述（技术规范书/内部材料/AI reference）+ **新增** 被动消极承诺词（有待进一步/暂无数据/后续研究/有待考证） | `内容缺失` |
| **3. 数据标注覆盖** | 数字（包括百分比、亿元等量词）后是否跟 source 标注（括号/脚注/`source:`） | `缺标注` |
| **4. 章节级 So What 密度** | 按 H1/H2 章节统计行动词（建议/应当/需要/可以/必须）数量；每章 < 阈值（默认 2）则标记"光分析没结论" | `章节 So What` |

砍掉（不再检查）：

- ❌ **图表编号连续性** — 中文报告编号格式不统一（图1/图一/图表 1），false positive 率高
- ❌ **So What 全文计数** — 数字本身无意义，升级到章节级

#### 3.3 脚本参数与执行模式

> **R1 Suggestion 3 / Open Q §7 接受**：脚本层支持 `-DryRun`，UI 不暴露。

`skill/scripts/quality_check.ps1` 改造：

```powershell
param(
    [Parameter(Mandatory = $true)]
    [string]$FilePath,
    
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "",  # 留空 → 旧模式输出 stdout；非空 → 新模式写 markdown 文件
    
    [Parameter(Mandatory = $false)]
    [switch]$DryRun = $false  # true → 跑全部检查但不写文件，把 markdown 输出到 stdout
)
```

模式判定：

| 调用方式 | 行为 |
|---|---|
| `quality_check.ps1 -FilePath X` | **旧模式**（向后兼容）：stdout 输出 Level/Title/行号格式，end with `[SUMMARY]`；exit 0 |
| `quality_check.ps1 -FilePath X -OutputPath Y` | **新模式**：markdown 写到 Y；stdout 简短状态行；exit 0 |
| `quality_check.ps1 -FilePath X -OutputPath Y -DryRun` | **新模式 dry-run**：markdown 输出到 stdout 不写 Y；exit 0 |

新模式 markdown 输出结尾必带：

```markdown
<!-- lint-report:complete -->
```

#### 3.4 报告格式（markdown 输出）

```markdown
# AI 味自查报告

**自查时间**：[ISO 8601]
**自查脚本**：quality_check.ps1 v2
**检查文件**：content/report_draft_v1.md (X 字)

---

## 按章节排列

### 第一章 [章节标题]
- **行 45** `AI 腔` `本章将分析过去十年的政策演进` → 删除"本章将"
- **行 89** `缺标注` `市场规模达到 5000 亿元` → 数字后未跟来源标注
- **行 112** `内容缺失` `XXX` → 占位符未填

### 第二章 [章节标题]
无命中

### 第三章 [章节标题]
...

---

## 章节 So What 密度

| 章节 | 行动词数 | 提示 |
|---|---|---|
| 第一章 | 1 | 偏少，光分析没结论 |
| 第二章 | 5 | 充分 |
| 第三章 | 0 | **缺失** — 该章未给任何行动建议 |

---

## 总览

- AI 腔：12 处
- 内容缺失：3 处
- 缺标注：7 处
- 章节 So What 偏少：1 章

**预估改完所需时间**：约 8 分钟

<!-- lint-report:complete -->
```

时间估算公式（在脚本里）：

- `AI 腔` × 5 秒
- `内容缺失` × 10 秒
- `缺标注` × 10 秒
- `章节 So What 偏少` × 60 秒
- 总和（秒）/ 60，向上取整

**Top-N 截断 fallback**：如果正文超过 30k 字 + lint 命中 > 100 处，按维度截取 top 30 条（按行号顺序），并在总览段加 `**注**：超长报告，仅显示前 30 条 issue` 提示。

#### 3.5 章节解析

脚本按 markdown H1 / H2 划章节。每条 finding 关联到所在章节：

- 从文件头到第一个 `# ` 或 `## ` 之前：归为"开篇"
- 之间每段 `## XXX` ~ 下一个 `## YYY`：归为该章
- 章节标题取 `## ` 后第一行 trim

#### 3.6 Backend 包装层

`backend/report_tools.py` 新增函数 `run_lint_report`：

```python
def run_lint_report(
    report_path: str,
    output_path: str,
    script_path: str,
    dry_run: bool = False,
) -> dict:
    """新版 lint，写 markdown 文件并返回 {path, summary}"""
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", script_path,
        "-FilePath", report_path,
        "-OutputPath", output_path,
    ]
    if dry_run:
        cmd.append("-DryRun")
    
    proc = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        return {"status": "error", "detail": proc.stderr.strip() or proc.stdout.strip()}
    
    # 从生成的 markdown 解析 summary
    summary = _parse_lint_summary(output_path) if not dry_run else {}
    return {"status": "ok", "path": output_path, "summary": summary}


def _parse_lint_summary(output_path: str) -> dict:
    """读 lint-report.md 末尾的总览段，提取数字"""
    # 实现：grep "## 总览" 后的 - X 行，拿 4 个 count + estimated_minutes
    ...


# 旧函数保留，**返回 shape 严格不变**（R2 Bug 6 catch）
def run_quality_check(file_path: str, script_path: str) -> dict:
    """旧版 quality check，**完全保留 backend/report_tools.py:17-22 真实行为**：
    - 不传 -OutputPath，走脚本的旧 stdout 模式
    - 返回 {"status": ok|error, "output": stdout OR stderr}（关键：保留 stderr fallback）
    - 不能改成 stdout.strip()，否则 tests/test_report_tools.py:20-25 锁死的 stderr 失败回传测试会破坏
    """
    result = _run_powershell(["-File", script_path, "-FilePath", file_path])
    return {
        "status": "ok" if result.returncode == 0 else "error",
        "output": result.stdout or result.stderr,
    }
```

`backend/main.py` 现有 `/quality-check` endpoint **行为不变**（仍调 `run_quality_check`，返回 `{status, output}`）。

新 endpoint：

```python
@app.post("/api/projects/{project_id}/lint-report")
async def lint_report(project_id: str):
    # 前置校验：必须 S5（R4 Bug 20：backend 返回 snake_case stage_code）
    workspace = skill_engine.get_workspace_summary(project_id)
    if workspace.get("stage_code") != "S5":
        raise HTTPException(status_code=400, detail="AI 味自查只能在 S5 阶段使用")
    
    # 并发控制：non-blocking acquire（R2 Bug 8 catch）
    # 旧代码用 `with lock:` 会阻塞等待，不会返回 409
    lock = get_lint_report_lock(project_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="上一次 AI 味自查仍在进行中，请等待")
    try:
        report_path = skill_engine.get_primary_report_path(project_id)
        output_path = str(skill_engine.get_project_path(project_id) / "plan" / "lint-report.md")
        script_path = skill_engine.get_script_path("quality_check.ps1")
        return run_lint_report(report_path, output_path, script_path)
    finally:
        lock.release()
```

`get_lint_report_lock` 模式同 `get_independent_review_lock`，**两者都用 non-blocking acquire**。

独立审查 endpoint 同理：

```python
@app.get("/api/projects/{project_id}/independent-review/stream")
async def independent_review_stream(project_id: str):
    # R4 Bug 20：backend 返回 snake_case stage_code
    workspace = skill_engine.get_workspace_summary(project_id)
    if workspace.get("stage_code") != "S5":
        raise HTTPException(status_code=400, detail="独立审查只能在 S5 阶段使用")
    
    lock = get_independent_review_lock(project_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="上一次独立审查仍在进行中，请等待")
    
    def generate():
        try:
            agent = IndependentReviewAgent(skill_engine, settings)
            for event in agent.run(project_id):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            lock.release()
    
    return StreamingResponse(generate(), media_type="text/event-stream", headers={...})
```

### 4. 报告文件 Schema 与软门禁

> **R1 Bug 1 + Bug 7 修正**：迁移点改为 `_stage_five_completion_state` 状态模型扩展；anchor 判定严格 5/5 + completion marker。

#### 4.1 `plan/independent-review.md` 软门禁

`backend/skill.py` 新增 helper：

```python
INDEPENDENT_REVIEW_ANCHORS = [
    "## 1. 结论-证据一致性",
    "## 2. 关键假设与逻辑链",
    "## 3. 数据口径一致性",
    "## 4. 建议可执行性",
    "## 5. 目标读者匹配",
]
INDEPENDENT_REVIEW_COMPLETION_MARKER = "<!-- independent-review:complete -->"


def _has_effective_independent_review(self, project_path: Path) -> bool:
    text = self._read_plan_file(project_path, "independent-review.md")
    if not text or self._is_template_content(text, "independent-review.md"):
        return False
    # 严格 5/5 anchor
    if not all(a in text for a in INDEPENDENT_REVIEW_ANCHORS):
        return False
    # 必须有 completion marker
    if INDEPENDENT_REVIEW_COMPLETION_MARKER not in text:
        return False
    return self._has_substantive_body(text)
```

#### 4.2 `plan/lint-report.md` 软门禁

```python
LINT_REPORT_ANCHORS = ["## 按章节排列", "## 总览"]
LINT_REPORT_COMPLETION_MARKER = "<!-- lint-report:complete -->"


def _has_effective_lint_report(self, project_path: Path) -> bool:
    text = self._read_plan_file(project_path, "lint-report.md")
    if not text or self._is_template_content(text, "lint-report.md"):
        return False
    if not all(a in text for a in LINT_REPORT_ANCHORS):
        return False
    if LINT_REPORT_COMPLETION_MARKER not in text:
        return False
    return self._has_substantive_body(text)
```

#### 4.3 组合判定

```python
def _has_effective_review_reports(self, project_path: Path) -> bool:
    return (
        self._has_effective_independent_review(project_path)
        and self._has_effective_lint_report(project_path)
    )
```

#### 4.4 状态模型扩展（取代 `_has_effective_review_checklist`）

> **R1 Bug 1 关键修正**：原 spec 误以为 `record_stage_checkpoint` 里直接 `if key == "review_passed_at"`；实际真正路径是 `_stage_five_completion_state()` → `_validate_stage_checkpoint_transition()`。本节按真实路径改。

**改 `_stage_five_completion_state`**（`backend/skill.py:459`）：

```python
def _stage_five_completion_state(
    self,
    project_path: Path,
    checkpoints=None,
    targets=None,
    stage_one_state=None,
    stage_four_state=None,
) -> dict:
    checkpoints = checkpoints if checkpoints is not None else self._load_stage_checkpoints(project_path)
    targets = targets if targets is not None else self._resolve_length_targets(project_path)
    stage_one_state = stage_one_state or self._stage_one_completion_state(project_path, checkpoints)
    stage_four_state = stage_four_state or self._stage_four_completion_state(
        project_path, checkpoints, targets, stage_one_state
    )

    # 新字段：两份审查报告的 ready 状态
    independent_review_ready = self._has_effective_independent_review(project_path)
    lint_report_ready = self._has_effective_lint_report(project_path)
    review_reports_ready = independent_review_ready and lint_report_ready

    review_passed = "review_passed_at" in checkpoints

    missing_for_review_pass = list(stage_four_state["missing_for_stage_four"])
    if not independent_review_ready:
        missing_for_review_pass.append("independent-review.md（请先点'独立审查'按钮）")
    if not lint_report_ready:
        missing_for_review_pass.append("lint-report.md（请先点'AI 味自查'按钮）")

    missing_for_stage_five = list(missing_for_review_pass)
    if not review_passed:
        missing_for_stage_five.append("review_passed_at")

    return {
        # 新字段
        "independent_review_ready": independent_review_ready,
        "lint_report_ready": lint_report_ready,
        "review_reports_ready": review_reports_ready,
        # 保留旧字段（向后兼容，但永远 False，因为不再被任何逻辑读）
        "review_checklist_ready": False,
        # 推进字段
        "review_passed": review_passed,
        "review_pass_prerequisites_complete": not missing_for_review_pass,
        "stage_five_complete": not missing_for_stage_five,
        "missing_for_review_pass": missing_for_review_pass,
        "missing_for_stage_five": missing_for_stage_five,
    }
```

**改 `CHECKPOINT_PREREQ.review_passed_at`**（`backend/skill.py:182`）：

```python
"review_passed_at": (
    "_has_effective_review_reports",
    "plan/independent-review.md, plan/lint-report.md",
    "需要先完成独立审查和 AI 味自查，才能标记审查通过。",
    "请先在 S5 阶段点击上方'独立审查'和'AI 味自查'按钮，再确认审查通过。",
),
```

注意：错误描述里直接引导用户点按钮，不暴露 helper 函数名。

**改 `STAGE_CHECKLIST_ITEMS["S5"]`**（`backend/skill.py:139-143`）：

```python
"S5": [
    "独立审查完成",
    "AI 味自查完成",
    "事实、逻辑与语言质量审查完成",
],
```

**改 `_build_completed_items`**（R2 Bug 1 catch：真实签名是 `(stage_code, flags)`，不接 `stage_five_state`；要覆盖 chat.py:1652 历史 S5 + 1678 当前 S5 两处）：

```python
def _build_completed_items(self, stage_code: str, flags: dict) -> list[str]:
    completed: list[str] = []
    stage_index = self._stage_index(stage_code)
    for stage in self.STAGE_ORDER[:stage_index]:
        if stage == "S6" and not flags["presentation_required"]:
            continue
        if stage == "S5":
            # 改造：历史 S5（已经过完）逻辑改用新 flags
            if flags["independent_review_ready"]:
                completed.append(self.STAGE_CHECKLIST_ITEMS["S5"][0])
            if flags["lint_report_ready"]:
                completed.append(self.STAGE_CHECKLIST_ITEMS["S5"][1])
            completed.append(self.STAGE_CHECKLIST_ITEMS["S5"][2])  # 历史上肯定过了
            continue
        completed.extend(self.STAGE_CHECKLIST_ITEMS[stage])

    # 当前 stage S5 的逻辑：改用新 flags
    if stage_code == "S0":
        if flags["project_overview_ready"]:
            completed.append(self.STAGE_CHECKLIST_ITEMS["S0"][2])
    elif stage_code == "S1":
        # ...（S1-S4 完全不动）
        ...
    elif stage_code == "S5":
        # 旧：if flags["review_checklist_ready"]: completed.append(... S5[0]) ...
        # 新：分别按两份报告就绪状态填三条 checklist 项
        if flags["independent_review_ready"]:
            completed.append(self.STAGE_CHECKLIST_ITEMS["S5"][0])
        if flags["lint_report_ready"]:
            completed.append(self.STAGE_CHECKLIST_ITEMS["S5"][1])
        # 第 3 项"事实/逻辑/语言质量审查完成"——只有两份报告都 ready 时勾选
        if flags["independent_review_ready"] and flags["lint_report_ready"]:
            completed.append(self.STAGE_CHECKLIST_ITEMS["S5"][2])
    elif stage_code in ("S6", "S7"):
        # ...（S6/S7 完全不动）
        ...

    return list(dict.fromkeys(completed))
```

**关键修复点**：

- 真实签名 `(self, stage_code, flags)`，不接 `stage_five_state`——通过 `flags["independent_review_ready"]` 等读
- 两处 S5 逻辑（行 1652 历史 + 1678 当前）都要改
- `flags` dict 由 `_infer_stage_state()` 构造时填入新字段（见下）

**改 `_infer_stage_state()` flags**：在 flags 字典里加：

```python
flags = {
    ...,
    "independent_review_ready": stage_five_state["independent_review_ready"],
    "lint_report_ready": stage_five_state["lint_report_ready"],
    "review_reports_ready": stage_five_state["review_reports_ready"],
    # 旧字段保留 backwards-compat，永远 False
    "review_checklist_ready": False,
}
```

**`_sync_stage_tracking_files()` next_actions**（如该 helper 存在）：S5 时按报告状态给不同 next_action 文字（见 §1.4 表）。

`_validate_stage_checkpoint_transition` **不需要改**——它通过 `stage_five_state["missing_for_review_pass"]` 间接读 `_has_effective_review_reports`，这套机制自动生效。

**`record_stage_checkpoint` 加 lock 检查（R2 Bug 8）**：

`backend/skill.py:1409-1423` 真实 `record_stage_checkpoint()` 是 `advance_stage` 工具与 main.py checkpoint endpoint 的统一入口（chat.py:4319-4324 chat tool 也走这里）。在 `review_passed_at` 路径加 lock 检查：

```python
def record_stage_checkpoint(self, project_id: str, key: str, action: str) -> dict:
    ...
    if key == "review_passed_at" and action == "set":
        # R2 Bug 8：审查或 lint 正在跑时拒绝推进
        from .independent_review import get_independent_review_lock
        from .report_tools import get_lint_report_lock  # 假设把 lock 工厂搬到这里或新 module
        
        review_lock = get_independent_review_lock(project_id)
        if review_lock.locked():
            raise ValueError("独立审查正在进行中，请等待完成后再标记审查通过")
        
        lint_lock = get_lint_report_lock(project_id)
        if lint_lock.locked():
            raise ValueError("AI 味自查正在进行中，请等待完成后再标记审查通过")
    ...
    # 继续 _validate_stage_checkpoint_transition + 写入 stage_checkpoints.json
```

这样所有走 `advance_stage` 工具的路径自动得到 lock 保护，不需要在 main.py endpoint 单独做。

### 5. 主代理 Turn 自动触发机制

> **R1 Bug 5 修正**：`ChatRequest.message_text` 改 Optional + Pydantic validator；ChatPanel 重构 `sendMessage` 为 `startStream`；后端独立 `_chat_stream_system_triggered` 不写 user message。

#### 5.1 ChatRequest 字段扩展

`backend/models.py`：

```python
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator


SystemTriggerType = Literal["independent_review_done", "lint_report_done"]


class ChatRequest(BaseModel):
    project_id: str = Field(..., min_length=1, max_length=100)
    message_text: str = Field(default="", max_length=10000)
    attached_material_ids: list[str] = Field(default_factory=list)
    transient_attachments: list[Attachment] = Field(default_factory=list)
    system_trigger: Optional[SystemTriggerType] = None

    @model_validator(mode="after")
    def validate_message_or_trigger(self):
        if self.system_trigger is None:
            # 普通 turn：必须有 message_text
            if not self.message_text or not self.message_text.strip():
                raise ValueError("message_text must be non-empty when system_trigger is None")
        else:
            # system trigger turn：message_text 可空（应该为空）
            if self.message_text.strip():
                # 不报错，但 backend 会忽略 message_text
                pass
        return self
```

#### 5.2 ChatHandler 行为

`backend/chat.py:ChatHandler.chat_stream()` 入口处理：

```python
def chat_stream(
    self,
    project_id: str,
    message_text: str,
    attached_material_ids: list[str],
    transient_attachments: list[dict],
    system_trigger: Optional[str] = None,  # 新参数
) -> Iterator[dict]:
    ...
    if system_trigger:
        # System-triggered turn: 完全独立路径，不走 user message persistence
        yield from self._chat_stream_system_triggered(project_id, system_trigger)
        return
    # 正常 user message turn — 现有逻辑全部不动
    ...
```

新方法 `_chat_stream_system_triggered`：

```python
def _chat_stream_system_triggered(
    self,
    project_id: str,
    trigger: str,
) -> Iterator[dict]:
    """System-triggered turn: 注入 system message，不写 user message"""
    
    trigger_prompt = SYSTEM_TRIGGER_PROMPTS.get(trigger)
    if not trigger_prompt:
        yield {"type": "error", "data": f"未知 system_trigger: {trigger}"}
        return
    
    # 加载现有 conversation 历史
    conversation = self._load_conversation(project_id)
    
    # 构造 OpenAI messages：现有历史 + 一条 system message（不持久化）
    messages = self._build_provider_messages(project_id, conversation, additional_system=trigger_prompt)
    
    # 跑标准的 tool loop（与 _chat_stream_unlocked 复用同款迭代逻辑）
    # 但 turn 起始处**不**写 user message 到 conversation.json
    # turn 结束时正常持久化 assistant message
    yield from self._run_tool_loop_for_system_turn(project_id, messages, conversation)
```

**R2 Bug 5 修正**：真实 `_finalize_assistant_turn` 在 `backend/chat.py:6211-6215`：

```python
# Step 6: persist this turn.
history.extend([
    current_user_message,
    {"role": "assistant", "content": persisted_content},
])
self._save_conversation(project_id, history)
return persisted_content
```

无条件 `history.extend([user, assistant])`。spec 必须明确改这一段。两个选项（都 OK，spec 选项 A）：

**选项 A（spec 采用）**：`_finalize_assistant_turn` 加 system-triggered 分支

```python
# Step 6: persist this turn.
if self._turn_context.get("system_triggered"):
    # R2 Bug 5：system-triggered turn 不写 user message
    # 因为没有真实 user message，current_user_message 是占位 dict
    history.extend([
        {"role": "assistant", "content": persisted_content},
    ])
else:
    history.extend([
        current_user_message,
        {"role": "assistant", "content": persisted_content},
    ])
self._save_conversation(project_id, history)
return persisted_content
```

**选项 B（备选，不采用）**：新增 `_finalize_assistant_only_turn` 独立函数——但会导致大量代码重复（finalize 路径里还有 `_append_tool_log_to_assistant` 等多步处理），维护成本高。

**`_run_tool_loop_for_system_turn` 实际实现**：

复用 `_chat_stream_unlocked` 的工具调用循环逻辑，但：

1. **不**在 turn 开始时把 user message 推进 conversation（`_chat_stream_unlocked` 当前会做这一步——新方法跳过）
2. **必须显式新建 turn_context（R3 Bug 17 修正）** — 真实 turn_context 含 `checkpoint_event` / `stage_code_before_turn` / `s0_confirmation_completed` / `canonical_obligation` / `user_message_text` 等多字段（chat.py:5892-5901 / 5934-5948 / 6218-6225）。新路径必须调用 `_build_turn_context(project_id, "")` 完整初始化，然后再覆盖如下字段：

   ```python
   def _chat_stream_system_triggered(self, project_id: str, trigger: str) -> Iterator[dict]:
       # R3 Bug 17：必须新建 turn_context，不能继承上一轮 stale context
       self._turn_context = self._build_turn_context(project_id, "")
       self._turn_context["system_triggered"] = True
       self._turn_context["user_message_text"] = ""
       # 显式清空可能 stale 的字段
       self._turn_context["canonical_obligation"] = None
       self._turn_context["checkpoint_event"] = None
       # stage_code_before_turn 保持 _build_turn_context 计算结果（用于 stage-claim mismatch 检测）
       
       trigger_prompt = SYSTEM_TRIGGER_PROMPTS.get(trigger)
       if not trigger_prompt:
           yield {"type": "error", "data": f"未知 system_trigger: {trigger}"}
           return
       ...
       # 加载现有 conversation 历史
       conversation = self._load_conversation(project_id)
       messages = self._build_provider_messages(project_id, conversation, additional_system=trigger_prompt)
       
       # 跑标准 tool loop（与 _chat_stream_unlocked 共享 helper）
       yield from self._run_tool_loop_for_system_turn(project_id, messages, conversation)
   ```

3. turn_context 设 `system_triggered=True` 标志，让 `_finalize_assistant_turn` 走 system-triggered 分支
4. turn_context 的 `user_message_text` 设为空字符串——不会触发 turn-end "claimed-but-not-done" 检测（该检测的 trigger 是 user 强 keyword，与 system trigger 无关）
5. assistant 写入 conversation.json **正常**——assistant 回复仍然持久化作为正常 assistant message

**实施注意**：

- `current_user_message` 在 `_finalize_assistant_turn` 路径上是个 dict variable；选项 A 不需要改 caller 签名，只在 finalize 内部分支
- 测试要锁死：
  - `test_finalize_assistant_turn_skips_user_when_system_triggered`
  - `test_finalize_assistant_turn_keeps_user_for_normal_turn`
  - `test_system_triggered_turn_does_not_inherit_stale_checkpoint_event` — 上一轮 `advance_stage` 后触发 system turn，新 turn_context 中 `checkpoint_event` 为 None（R3 Bug 17）
  - `test_system_triggered_turn_stage_mismatch_uses_current_stage` — system turn 的 stage-claim mismatch 检测仍按当前 stage 工作（R3 Bug 17）

`SYSTEM_TRIGGER_PROMPTS` 字典：

```python
SYSTEM_TRIGGER_PROMPTS = {
    "independent_review_done": (
        "[系统通知] 独立审查报告已生成（plan/independent-review.md）。"
        "请用 read_file 阅读该文件，按 5 个维度向用户报告主要发现"
        "（每个维度 1-2 句话总结），然后询问用户是否要按建议修改正文。"
        "不要把整份报告原文贴进聊天框——用户能从工作区面板直接看到报告文件。"
    ),
    "lint_report_done": (
        "[系统通知] AI 味自查报告已生成（plan/lint-report.md）。"
        "请用 read_file 阅读该文件，按章节向用户报告主要发现"
        "（哪些章节命中较多 / 哪些章节缺 So What），然后询问用户是否要按建议修改正文。"
        "不要把整份报告原文贴进聊天框——用户能从工作区面板直接看到报告文件。"
    ),
}
```

#### 5.3 持久化策略

- **system_trigger 注入的 system message 不持久化** — 只用于本轮 turn 的 OpenAI 调用
- **user message 完全不写** — `conversation.json` 不会出现 system trigger 对应的 user 占位
- **assistant reply 正常持久化** — 写入 conversation.json 作为正常 assistant message
- 下一轮用户提问时，主代理还能看到自己上一轮"读完报告后给的总结"，但看不到"系统注入的提示"——避免暴露后台术语

#### 5.4 前端事件链（fetch + ReadableStream）

> **R1 Bug 5 修正**：删除 EventSource POST 错误；用 fetch + reader 模式；等服务端 ready 确认再触发主代理 turn。

**独立审查路径**：

```jsx
// IndependentReviewDrawer.jsx
const url = `/api/projects/${encodeURIComponent(projectId)}/independent-review/stream`
const response = await fetch(url, { method: 'GET' })
const reader = response.body.getReader()
const decoder = new TextDecoder()
let buffer = ''
while (true) {
  const { done, value } = await reader.read()
  if (done) break
  buffer += decoder.decode(value, { stream: true })
  const lines = buffer.split('\n\n')
  buffer = lines.pop() || ''
  for (const block of lines) {
    if (!block.startsWith('data: ')) continue
    const payload = block.slice(6)
    if (payload === '[DONE]') return
    try {
      const data = JSON.parse(payload)
      if (data.type === 'review-completed') {
        // 不立即触发 turn——先 server confirmed flag
        const ws = await axios.get(`/api/projects/${projectId}/workspace`)
        if (ws.data.flags?.independent_review_ready) {
          onClose()
          onTriggerSystemTurn('independent_review_done')
        } else {
          setError('审查报告未通过服务端校验，请重试')
          setTimeout(onClose, 3000)
        }
        return
      } else if (data.type === 'error') {
        setError(data.detail || '审查失败')
        setTimeout(onClose, 3000)
        return
      } else {
        setEvents(prev => [...prev, data])
      }
    } catch (e) {
      // ignore parse errors
    }
  }
}
```

**AI 味自查路径**（无 drawer，无 SSE）：

```jsx
// WorkspacePanel.jsx 或 StagePanel.jsx
const runLintReport = async () => {
  if (lintRunning) return
  setLintRunning(true)
  try {
    const res = await axios.post(`/api/projects/${projectId}/lint-report`)
    onProjectMutated?.()
    // 等 workspace summary 更新
    const ws = await axios.get(`/api/projects/${projectId}/workspace`)
    if (ws.data.flags?.lint_report_ready) {
      onTriggerSystemTurn('lint_report_done')
    } else {
      showError('AI 味自查报告未通过服务端校验')
    }
  } catch (e) {
    showError('AI 味自查失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    setLintRunning(false)
  }
}
```

**ChatPanel.startStream 重构**：

`sendMessage` 内的 chat stream 逻辑抽出为 `startStream({ messageText, systemTrigger, attachedMaterialIds, transientAttachments, renderUserBubble })`。

```jsx
const startStream = async ({
  messageText,
  systemTrigger,
  attachedMaterialIds,
  transientAttachments,
  renderUserBubble = true,
}) => {
  // 1. 渲染 user bubble（如果 renderUserBubble）
  if (renderUserBubble) {
    setMessages(prev => [...prev, {
      id: `${Date.now()}-u`,
      role: 'user',
      content: messageText,
      attachedMaterialIds,
    }])
  }
  
  // 2. 创建 assistant 占位 + 启动 fetch
  const assistantId = `${Date.now()}-a`
  setMessages(prev => [...prev, { id: assistantId, role: 'assistant', content: '' }])
  
  const response = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      project_id: projectId,
      message_text: messageText || '',
      attached_material_ids: attachedMaterialIds || [],
      transient_attachments: transientAttachments || [],
      system_trigger: systemTrigger || null,
    }),
  })
  // ... 现有 reader / accumulate 逻辑
}

const sendMessage = async () => {
  // ... 现有附件上传逻辑 + 输入校验 ...
  await startStream({
    messageText: trimmedInput,
    systemTrigger: null,
    attachedMaterialIds: requestAttachedMaterialIds,
    transientAttachments: transientAttachmentsPayload,
    renderUserBubble: true,
  })
}

// 暴露给外部组件调用
const triggerSystemTurn = (systemTrigger) => {
  startStream({
    messageText: '',
    systemTrigger,
    renderUserBubble: false,
  })
}
```

`triggerSystemTurn` 通过 prop 或 ref 暴露给 StagePanel / WorkspacePanel 调用。

### 6. S5 进入提醒机制

> **R1 Bug 9 修正**：字段路径 `summary.checkpoints.review_started_at`（不是 `flags`）；用 `conversation_state.s5_welcome_shown_at` 字段做幂等。

#### 6.1 主代理欢迎信

判定"该发欢迎信"：

```python
def _should_emit_s5_welcome(self, project_id: str) -> bool:
    # R4 Bug 20：backend get_workspace_summary 返回 snake_case stage_code
    # （frontend summarizeWorkspace 才把它映射为 stageCode）
    workspace = self.skill_engine.get_workspace_summary(project_id)
    if workspace.get("stage_code") != "S5":
        return False
    if not workspace.get("checkpoints", {}).get("review_started_at"):
        return False
    state = self._load_conversation_state(project_id)
    if state.get("s5_welcome_shown_at"):
        return False
    return True


def _mark_s5_welcome_shown(self, project_id: str) -> None:
    state = self._load_conversation_state(project_id)
    state["s5_welcome_shown_at"] = datetime.now(timezone.utc).isoformat()
    self._save_conversation_state_atomically(project_id, state)
```

**`conversation_state.json` schema 微扩展**（破例 Non-Goals §10，但记入修订）：新增可选字段 `s5_welcome_shown_at: Optional[str]`。

**R3 Bug 16 修正——load/save/empty 三处必须同步改**：

真实 `_load_conversation_state` 不是直接返回 JSON dict，而是从 `_empty_conversation_state()` 重建白名单字段（chat.py:813-821 / 932-948 / 995-1013）。只改 `_save_conversation_state_atomically` 白名单**会让字段在 load 时被丢弃**。

必须三处同步改：

1. **`_empty_conversation_state()`** 加 `"s5_welcome_shown_at": None`：

   ```python
   def _empty_conversation_state(self) -> dict:
       return {
           ...existing fields...,
           "s5_welcome_shown_at": None,  # 新增
       }
   ```

2. **`_load_conversation_state()`** 加复制逻辑（如果 payload 字段是非空 str 则复制）：

   ```python
   def _load_conversation_state(self, project_id: str) -> dict:
       state = self._empty_conversation_state()
       payload = self._read_conversation_state_file(project_id)
       if not payload:
           return state
       ...existing copy logic...
       welcome_shown = payload.get("s5_welcome_shown_at")
       if isinstance(welcome_shown, str) and welcome_shown:
           state["s5_welcome_shown_at"] = welcome_shown
       return state
   ```

3. **`_save_conversation_state_atomically()`** 白名单加该字段，只在非空 str 时保存：

   ```python
   def _save_conversation_state_atomically(self, project_id: str, state: dict) -> None:
       payload = {
           ...existing fields...,
       }
       welcome_shown = state.get("s5_welcome_shown_at")
       if isinstance(welcome_shown, str) and welcome_shown:
           payload["s5_welcome_shown_at"] = welcome_shown
       ...atomic write...
   ```

**测试覆盖**（必加）：

- `test_load_conversation_state_with_no_s5_welcome_field` — 老 state 无字段时 load 返回 `None`，下次进 S5 注入欢迎信
- `test_load_conversation_state_preserves_s5_welcome` — 已写入字段后 reload 仍保留
- `test_save_load_roundtrip_with_s5_welcome` — 写入后 reload + 再写入仍幂等
- `test_save_skips_none_s5_welcome` — None 时不写入文件（避免 schema 噪音）

**注入时机（R2 Bug 12 修正）**：

R2 catch 出原方案有 race condition：如果在第一次 OpenAI 调用前写 `s5_welcome_shown_at`，但 turn 失败 / 用户 abort / 没有产出可见 assistant message，下次进 S5 时字段已存在不会再发欢迎信。

**修正后两阶段**：

1. **turn 起始时**（检测到 S5 + checkpoints.review_started_at 已写 + `s5_welcome_shown_at` 未写）：把欢迎 system message 追加到 messages 数组（不持久化到 conversation.json）；**不要**写 `s5_welcome_shown_at`
2. **turn 成功完成时**（`_finalize_assistant_turn` 写完 assistant message 到 conversation.json 后）：调 `_mark_s5_welcome_shown` 写 `s5_welcome_shown_at`

判定"turn 成功"：

- `_finalize_assistant_turn` 走完且 `persisted_content` 非空（说明 assistant 真的输出了可见内容）
- 而不是 `_chat_stream_unlocked` 进入但中途异常 / abort

实施：

```python
def _chat_stream_unlocked(self, ...):
    ...
    welcome_injected = False
    if self._should_emit_s5_welcome(project_id):
        # 追加欢迎 system message 到 messages（不持久化）
        messages.append({"role": "system", "content": S5_WELCOME_PROMPT})
        welcome_injected = True
    ...
    # 走完 turn 后：
    persisted_content = self._finalize_assistant_turn(...)
    if welcome_injected and persisted_content and persisted_content.strip():
        # 只有真的输出了 assistant message 才标记 welcome 已发
        self._mark_s5_welcome_shown(project_id)
    return persisted_content
```

下一轮 user message 来时 `s5_welcome_shown_at` 已存在，不重复注入。turn 失败 / abort 不写，下次进 S5 会重发欢迎信——这是想要的。

**欢迎 system message 文本**：

```text
[S5 阶段进入提醒]
用户刚进入 S5 质量审查阶段。S5 的玩法跟以前不一样了：

不再要求你自己填写 review-checklist.md。审查由两个用户主动触发的工具完成：
1. 用户点"独立审查"按钮：会派一个独立审查代理读 data-log / analysis-notes / 正文 / references / outline，按 5 个判断类维度审查，落 plan/independent-review.md。
2. 用户点"AI 味自查"按钮：会跑机械化脚本扫正文，按 4 个机械维度查 AI 腔、占位符、数据标注、章节 So What 密度，落 plan/lint-report.md。

请你**在本轮回复**用一句话提醒用户使用上方两个新按钮，简单说明两个按钮的区别。
不要假装审查已完成。
不要自己写 plan/review-checklist.md（已退役）。
```

#### 6.2 UI 高亮

- 按钮高亮：S5 阶段 + 对应报告未生成时按钮加 `btn-highlight-pulse` CSS class
- StagePanel 下一步建议文字：S5 阶段时按报告状态动态显示（见 §1.4）

### 7. SKILL.md S5 段与 modules / templates 同步

> **R1 Bug 3 修正**：除了 SKILL.md，`consulting-lifecycle.md` + 三个 tracking 模板都有旧 S5 文案需要同步。

#### 7.1 `skill/SKILL.md` S5 段重写

替换 `skill/SKILL.md` 行 138-142 现有 S5 段：

```markdown
### S5 质量审查

S5 阶段由两个用户主动触发的工具完成，你不再自己写 review-checklist.md。

**用户操作流**：
1. 用户点上方"独立审查"按钮 → 独立审查代理读 data-log / analysis-notes / 正文 / references / outline，按 5 维度审查，落 `plan/independent-review.md`
2. 用户点上方"AI 味自查"按钮 → 机械脚本扫正文，按 4 维度查 AI 腔/占位符/标注/章节 So What，落 `plan/lint-report.md`

**你的任务**：
- 用户进入 S5 时，**主动提醒用户使用上方两个新按钮**——一句话说清楚两个按钮的区别
- 当系统通知"独立审查报告已生成"时，read_file 读 `plan/independent-review.md`，按维度向用户报告主要发现，询问是否需要修改
- 当系统通知"AI 味自查报告已生成"时，read_file 读 `plan/lint-report.md`，按章节向用户报告，询问是否需要修改
- 用户决定改某条 → 你按 S4 工具规则修改正文（read_file + edit_file / append_report_draft）
- 用户认为审查通过 → 调用 `advance_stage(checkpoint_key="review_passed_at", action="set", reason="...")`

**禁止**：
- 不要自己写 `plan/review-checklist.md`（已退役）
- 不要假装独立审查或 AI 味自查已完成
- 不要在用户没点按钮的情况下尝试推进 S5
- 不要把审查报告内容大段贴进聊天框——报告文件已经在工作区，你只 summarize 关键发现

**推进到 S6/S7**：用户明确确认审查通过时，调用 `advance_stage(checkpoint_key="review_passed_at", action="set", reason="...")`。后端会校验 `plan/independent-review.md` 和 `plan/lint-report.md` 都存在且结构完整。
```

#### 7.2 `skill/modules/consulting-lifecycle.md` 同步

**第 20 行原文**：

```text
| S5 质量审查 | 完成系统复核 | 勾选审查清单、必要时记录修订意见 | `review-checklist.md` `review.md`(可选) |
```

改为：

```text
| S5 质量审查 | 完成独立审查与 AI 味自查 | 用户点"独立审查"+"AI 味自查"按钮，主代理基于两份报告与用户讨论 | `independent-review.md` `lint-report.md` |
```

**第 50 行原文**（R2 Bug 3 catch）：

```text
2. 报告-only 项目可从 S5 直接进入 S7
```

**保留不动**——这一行是流程拓扑描述（"报告-only 项目走 S5 → S7"），与 S5 内部如何审查无关。无论 S5 用旧 review-checklist 还是新独立审查，"报告-only 项目跳过 S6"的逻辑不变。

> R2 Bug 3 PARTIAL 的实际范围：第 20 行必改（已覆盖）；第 50 行不需要改（仅在影响清单中标注"已确认无需修改"，避免后续 reviewer 再质疑）。

#### 7.3 `skill/plan-template/progress.md` 同步

第 42 行原文：

```text
| S5 | 质量审查 | `review-checklist.md` / `review.md` | | |
```

改为：

```text
| S5 | 质量审查 | `independent-review.md` / `lint-report.md` | | |
```

#### 7.4 `skill/plan-template/stage-gates.md` 同步

第 40-43 行原文：

```text
### S5 质量审查 ⬜
- [ ] review-checklist.md 完成
- [ ] review.md 记录修订意见
- [ ] 事实、逻辑与语言质量审查完成
```

改为：

```text
### S5 质量审查 ⬜
- [ ] 独立审查完成（plan/independent-review.md）
- [ ] AI 味自查完成（plan/lint-report.md）
- [ ] 事实、逻辑与语言质量审查完成
```

#### 7.5 `skill/plan-template/tasks.md` 同步

第 44-45 行原文：

```text
### S5 质量审查
- [ ] 完成 `review-checklist.md`
```

改为：

```text
### S5 质量审查
- [ ] 点击工作区"独立审查"按钮（生成 `plan/independent-review.md`）
- [ ] 点击工作区"AI 味自查"按钮（生成 `plan/lint-report.md`）
- [ ] 主代理基于两份报告与用户讨论修改方向
```

#### 7.6 测试锁定

`tests/test_packaging_docs.py` 现有 SKILL.md 验收测试要同步更新——锁定新 S5 段的存在和旧 "完成 review-checklist.md" 短语的消失。同步在 modules / plan-template 各文件上加 assertion。

### 8. 退役迁移与 FORMAL_PLAN_FILES 切换

> **R1 Bug 2 修正**：通过 `FORMAL_PLAN_FILES` 切换，复用现有"_initialize_project_structure 遍历 FORMAL_PLAN_FILES 复制模板"机制。

#### 8.1 `FORMAL_PLAN_FILES` 修改与主代理写入隔离（R2 Bug 10）

`backend/skill.py:22-37`：

```python
FORMAL_PLAN_FILES = {
    "project-overview.md",
    "progress.md",
    "stage-gates.md",
    "notes.md",
    "outline.md",
    "research-plan.md",
    "references.md",
    "tasks.md",
    "review.md",
    "data-log.md",
    "analysis-notes.md",
    # "review-checklist.md",  # 退役：从 FORMAL_PLAN_FILES 移除（模板文件保留以兼容老测试）
    "independent-review.md",   # 新增
    "lint-report.md",          # 新增
    "presentation-plan.md",
    "delivery-log.md",
}
```

**后果**：

1. `_initialize_project_structure(project_dir)`（行 803-815）会自动遍历新的 FORMAL_PLAN_FILES，从 `skill/plan-template/` 复制每个文件——所以**新模板需要存在**（见 §8.2）
2. `_initialize_project_structure` 不再复制 `review-checklist.md` 到新项目（向后兼容老项目）
3. `validate_plan_write()`（行 1046）会接受 `plan/independent-review.md` + `plan/lint-report.md`——这是独立审查代理 / lint 脚本写入的前提

**⚠️ 关键问题（R2 Bug 10）**：把新文件加进 `FORMAL_PLAN_FILES` 后，**主代理的 `write_file` / `edit_file` 也能写这两个文件**——这会破坏"独立 second opinion"的核心可信边界。主代理可以伪造一份"我已经审完了"的报告。

**修复（必须加）**：在 `backend/chat.py` 主代理 write_file 阶段拦截路径（行 4775 附近）**显式拒绝**写入这两个新文件：

```python
# backend/chat.py 主代理 write_file 拦截路径
if normalized_path == "plan/independent-review.md":
    return (
        "`plan/independent-review.md` 只能由独立审查代理生成（用户点'独立审查'按钮）。"
        "你不能直接写这份报告——这是审查独立性的硬约束。"
    )
if normalized_path == "plan/lint-report.md":
    return (
        "`plan/lint-report.md` 只能由 AI 味自查脚本生成（用户点'AI 味自查'按钮）。"
        "你不能直接写这份报告。"
    )
```

拦截位置：插入到 `backend/chat.py:4775` 现有 `if normalized_path in {"plan/review-checklist.md", "plan/review.md"}:` 分支**之后、`plan/presentation-plan.md` 分支之前**。

**独立审查代理 / lint 脚本如何绕过这个拦截**：

- 独立审查代理走 `SkillEngine.write_file`（不经过 chat.py 主代理拦截）；`SkillEngine.write_file` 走 `validate_plan_write` 校验 FORMAL_PLAN_FILES，会接受这两个文件
- lint 脚本走 PowerShell `Out-File`（完全绕过 backend write 路径）
- chat.py 主代理写文件路径有 `_should_allow_non_plan_write` / `_should_block_plan_write_for_stage` 等额外门禁——加 path 拒绝分支只影响主代理，不影响独立审查代理 / lint 脚本

**测试要锁死**：

- `tests/test_chat_runtime.py::test_main_agent_cannot_write_independent_review_md`
- `tests/test_chat_runtime.py::test_main_agent_cannot_write_lint_report_md`
- `tests/test_chat_runtime.py::test_main_agent_cannot_edit_independent_review_md` — `edit_file` 路径同样被拦（R3 Suggestion 7）
- `tests/test_chat_runtime.py::test_main_agent_cannot_edit_lint_report_md` — 同上
- `tests/test_independent_review.py::test_independent_review_agent_can_write_canonical_path`（已计划，验证审查代理仍能写）
- `tests/test_lint_report.py::test_lint_script_writes_lint_report_md`（验证脚本仍能写）

`edit_file` 同样被拦的原因：真实 `write_file` 和 `edit_file` 都会进入 `_execute_plan_write`（chat.py:3898-3905 / 4582-4586），路径校验在共享门禁里（chat.py:4775 附近）——按 spec 在该位置插入拒绝分支后会自动覆盖 edit_file。

#### 8.2 新模板

`skill/plan-template/independent-review.md`：

```markdown
# 独立审查报告

[等待运行 — 请在 S5 阶段点击工作区"独立审查"按钮]

<!-- independent-review:pending -->
```

`skill/plan-template/lint-report.md`：

```markdown
# AI 味自查报告

[等待运行 — 请在 S5 阶段点击工作区"AI 味自查"按钮]

<!-- lint-report:pending -->
```

模板的 marker 是 `:pending`，而不是 `:complete`——确保软门禁判定 `_has_effective_*` 在 stub 状态返回 False（marker 不匹配）。

`_is_template_content` helper 应能识别这两个新 stub（基于"[等待运行]"字面量）。

#### 8.3 老项目兼容

老项目（已有 `plan/review-checklist.md`）首次进入 S5 时：

- 旧文件**不删**（用户数据）
- 软门禁（`_has_effective_review_reports`）不读它——只看新两份报告
- UI 不再展示与 `review-checklist.md` 相关的提示
- 用户首次点"独立审查"按钮后会生成 `plan/independent-review.md`（与旧文件共存）

老项目缺 `plan/independent-review.md` / `plan/lint-report.md` stub 文件的处理：

`_initialize_project_structure` 只在创建项目时调用一次；老项目不会重跑。所以老项目不会有 stub 文件。当用户首次点按钮时，审查代理 / lint 脚本直接生成完整报告——不需要 stub 文件存在。

但 `_is_template_content` 的判定基于 "模板字面量"。如果文件不存在，`_read_plan_file` 返回 `None` 或空字符串，`_has_effective_*` 判定为 False（这是想要的）。所以无需特殊迁移逻辑。

#### 8.4 测试覆盖

`tests/test_skill_assets.py` 同步：

- 验证 plan-template 包含 `independent-review.md` + `lint-report.md`
- 验证 plan-template/review-checklist.md 文件仍存在（向后兼容）
- 验证 `FORMAL_PLAN_FILES` 包含两个新文件，不包含 `review-checklist.md`
- 验证新项目创建后 `plan/` 目录含 `independent-review.md` + `lint-report.md`，**不**含 `review-checklist.md`

### 9. 前端 Drawer 组件

#### 9.1 IndependentReviewDrawer.jsx

```jsx
// frontend/src/components/IndependentReviewDrawer.jsx
import { useState, useEffect, useRef } from 'react'

export default function IndependentReviewDrawer({
  projectId,
  isOpen,
  onClose,
  onCompleted,  // (reportPath) => void; 在服务端确认 ready 后调用
}) {
  const [events, setEvents] = useState([])
  const [error, setError] = useState(null)
  const abortControllerRef = useRef(null)

  useEffect(() => {
    if (!isOpen || !projectId) return
    setEvents([])
    setError(null)

    const controller = new AbortController()
    abortControllerRef.current = controller

    const runStream = async () => {
      try {
        const url = `/api/projects/${encodeURIComponent(projectId)}/independent-review/stream`
        const response = await fetch(url, { method: 'GET', signal: controller.signal })
        if (!response.ok) {
          const detail = await response.json().catch(() => ({ detail: response.statusText }))
          throw new Error(detail.detail || '启动审查失败')
        }
        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const blocks = buffer.split('\n\n')
          buffer = blocks.pop() || ''
          for (const block of blocks) {
            if (!block.startsWith('data: ')) continue
            const payload = block.slice(6)
            if (payload === '[DONE]') return
            try {
              const data = JSON.parse(payload)
              if (data.type === 'review-completed') {
                onCompleted?.(data.path)
                onClose()
                return
              } else if (data.type === 'error') {
                setError(data.detail || '审查失败')
                setTimeout(onClose, 3000)
                return
              } else {
                setEvents(prev => [...prev, data])
              }
            } catch (e) {
              // ignore parse errors
            }
          }
        }
      } catch (e) {
        if (e.name === 'AbortError') return
        setError(e.message || '网络错误')
        setTimeout(onClose, 3000)
      }
    }

    runStream()
    return () => controller.abort()
  }, [isOpen, projectId])

  if (!isOpen) return null

  return (
    <div className="fixed bottom-4 right-4 w-[480px] h-[600px] bg-[#171a31] border border-[#2f3158] rounded-2xl shadow-2xl z-50 flex flex-col">
      <div className="px-4 py-3 border-b border-[#2f3158] flex items-center justify-between">
        <span className="text-sm font-medium text-[#eef1ff]">独立审查代理工作中...</span>
        <span className="text-xs text-[#8f93c9]">{events.length} 步</span>
      </div>
      <div className="flex-1 overflow-y-auto p-4 space-y-2 text-sm text-[#d9dcf5]">
        {events.map((evt, i) => <DrawerEvent key={i} event={evt} />)}
        {error && <div className="text-red-400 mt-4">错误：{error}</div>}
      </div>
    </div>
  )
}

function DrawerEvent({ event }) {
  if (event.type === 'progress') return <div className="text-[#64ffda]">▸ {event.detail}</div>
  if (event.type === 'tool_call') return <div className="text-[#8f93c9]">🔧 {event.tool}</div>
  if (event.type === 'tool_result') return <div className="text-[#8f93c9]">  ✓ {event.summary}</div>
  if (event.type === 'content') return <div className="text-[#d9dcf5]">{event.text}</div>
  return null
}
```

#### 9.2 AI 味自查无 Drawer

脚本快（几秒），无需 drawer。直接 axios.post 同步等响应，期间按钮 spinner，响应回来后自动起主代理 turn。

#### 9.3 并发与中断处理

> **R1 Bug 8 修正**：

**Per-project running 状态**（前端）：

- `WorkspacePanel` 或 `StagePanel` 内部状态：`reviewRunning: bool` / `lintRunning: bool`
- 当 `reviewRunning === true`：禁用 "独立审查" 按钮、禁用 "AI 味自查" 按钮、禁用 `advance_stage(review_passed_at)` 入口
- 同理 `lintRunning`
- 状态在 drawer 关闭 / lint endpoint 返回后清除

**Per-project running 锁**（后端）：

- 见 §2.3 `_INDEPENDENT_REVIEW_LOCKS` 和 §3.6 `_LINT_REPORT_LOCKS`
- 第二个并发请求触发 `409` 错误（"上一次审查仍在进行中"）
- 关键实现细节：endpoint 用 `lock.acquire(blocking=False)`，失败立即 raise HTTPException(409)；不是 `with lock:`（会阻塞等待）

**Suggestion 5：单进程假设**（R2 Suggestion 5）：

module-level dict + threading.Lock 的实现**只适用于当前单进程桌面部署**。当前部署状态：

- `app.py:76` PyWebView 桌面入口启动 uvicorn 单进程
- `backend/main.py:462-463` FastAPI app 实例
- `run_web.py:13-17` web 模式启动 uvicorn 单 worker

未来若改为多 worker uvicorn（如 `--workers 4`）或迁移到 Gunicorn 多进程部署，**module-level lock 失效**——必须换文件锁（`filelock` 库）或数据库锁。这条假设记入 spec，实施时在 `backend/independent_review.py` 顶部加 docstring 注释。

**审查代理 vs `advance_stage(review_passed_at)` 同时发生**：

- `advance_stage` 工具调用现有 `_validate_stage_checkpoint_transition` 不感知 running 状态——这是个潜在风险
- 缓解：`advance_stage` 在 `review_passed_at` 时增加检查 — 如果当前 project 有 lock held（独立审查 / lint 正在跑），直接 reject "审查正在进行中，请等待完成"
- 实现：复用 `get_independent_review_lock(project_id).locked()` 来判断

**SSE 断连**（用户刷新 / 关闭页面 / 网络抖动）：

- 后端：用 FastAPI 的 `request.is_disconnected()` 检测；断连时 `IndependentReviewAgent.run()` 完成当前 OpenAI 调用后退出循环（不在中途强杀）
- 后端：审查代理可能已经 `write_file` 成功（部分进度落盘）——这种情况下 `_has_effective_independent_review` 校验会因缺 completion marker 而返回 False，用户重按按钮覆盖写
- 前端：fetch reader 错误时 drawer 显示"网络断开"3 秒后自动关，按钮重新 enabled，用户可重试
- 没有"恢复中断的审查"机制——v0 简单粗暴，重审一次

#### 9.4 ESC 键 / 用户主动关闭

> **R1 Open Q §8 决议**：drawer 必须能中断 + backend 处理断连。

- Drawer 不显示"关闭按钮"，但响应 ESC 键关闭（监听 `keydown` 事件）
- ESC 关闭 → `AbortController.abort()` 终止 fetch reader → 后端 detect disconnect → 终止审查 LLM call 后退出
- 关闭后前端 `reviewRunning` 状态清除，按钮重新 enabled
- 已经 partial 写入的 `independent-review.md`（无 marker）会被 `_has_effective_independent_review` 判定为 False，下次按按钮重新生成

#### 9.5 防双击

- 按钮 onClick 入口判定 `reviewRunning` / `lintRunning`；为 true 时直接 return（不发请求）
- 后端 `_INDEPENDENT_REVIEW_LOCKS.acquire(blocking=False)`；获取失败 → 409 error

### 10. workspaceSummary 扩展

`backend/skill.py:get_workspace_summary` 返回值增加：

```python
{
  ...,
  "flags": {
    ...,
    "independent_review_ready": stage_five_state["independent_review_ready"],
    "lint_report_ready": stage_five_state["lint_report_ready"],
    "review_reports_ready": stage_five_state["review_reports_ready"],
    # 旧字段保留兼容
    "review_checklist_ready": False,  # 永远 False，新逻辑不读
  },
  "next_stage_hint": <S5 阶段时动态文字，见 §1.4>,
}
```

前端 `frontend/src/utils/workspaceSummary.js`：

```js
export function mapWorkspaceSummary(raw) {
  return {
    ...,
    flags: {
      ...,
      independentReviewReady: raw.flags?.independent_review_ready ?? false,
      lintReportReady: raw.flags?.lint_report_ready ?? false,
      reviewReportsReady: raw.flags?.review_reports_ready ?? false,
    },
  }
}
```

### 11. 测试策略

#### 11.1 后端单元测试

**`tests/test_independent_review.py`（新文件）**：

- `test_independent_review_agent_reads_required_files` — 验证代理 read_file 读 data-log / analysis-notes / report_draft / references / project-overview / outline
- `test_independent_review_agent_reads_optional_research_plan` — research-plan.md 存在则读
- `test_independent_review_agent_writes_only_canonical_path` — write_file 拒绝非 `plan/independent-review.md` 路径
- `test_independent_review_agent_no_other_tools` — tools schema 只有 read_file / write_file
- `test_independent_review_agent_max_iterations_15` — 验证超过 15 轮 emit error
- `test_independent_review_agent_word_count_over_30k_emits_friendly_error` — 验证超过 30k 字直接 emit error event + 不启动 LLM call（R3 Bug 15）
- `test_independent_review_endpoint_sse` — endpoint 返回 SSE content-type
- `test_independent_review_endpoint_emits_completed_event` — 最后 emit `review-completed`
- `test_independent_review_endpoint_requires_s5` — 非 S5 阶段返回 400
- `test_independent_review_endpoint_concurrent_returns_409` — 并发返回 409
- `test_independent_review_no_stage_check` — 审查代理不被 S0 软门禁等阻断
- `test_independent_review_agent_deepseek_compatibility` — 验证 tool_choice 字段、reasoning_content 处理、null SDK dump 字段处理
- `test_independent_review_agent_verifies_completion_marker` — 验证 marker 检测

**`tests/test_lint_report.py`（新文件）**：

- `test_lint_report_4_dimensions` — 输出包含 4 个维度标签
- `test_lint_report_groups_by_section` — 按 H1/H2 章节分组
- `test_lint_report_estimates_time` — 总览段包含预估时间
- `test_lint_report_writes_markdown_file` — 写到 `plan/lint-report.md`
- `test_lint_report_negative_lookahead` — "非常显著（数字）" 不被空洞形容词命中
- `test_lint_report_so_what_per_chapter` — 章节级 So What 计数
- `test_lint_report_completion_marker` — 输出末尾有 `<!-- lint-report:complete -->`
- `test_lint_report_top_n_truncation` — 超长报告截 top 30
- `test_lint_report_dry_run_no_file` — dry-run 模式不写文件，输出到 stdout
- `test_run_lint_report_endpoint_requires_s5` — endpoint 校验 S5
- `test_run_lint_report_endpoint_concurrent_returns_409` — 并发返回 409
- `test_run_quality_check_backwards_compat` — 旧 `run_quality_check` 返回 shape `{status, output}` 不变

**`tests/test_skill_engine.py`（扩展）**：

- `test_has_effective_independent_review_requires_5_anchors` — 严格 5/5
- `test_has_effective_independent_review_requires_marker` — 缺 marker 返回 False
- `test_has_effective_lint_report_requires_anchors_and_marker`
- `test_has_effective_review_reports_combined`
- `test_advance_stage_review_passed_at_uses_new_check` — review_passed_at 用新 helper
- `test_advance_stage_review_passed_at_rejects_missing_independent_review` — 错误消息引导用户点按钮
- `test_advance_stage_review_passed_at_rejects_missing_lint_report`
- `test_advance_stage_review_passed_at_rejects_when_lock_held` — 审查正在跑时拒绝
- `test_formal_plan_files_includes_new_files`
- `test_formal_plan_files_excludes_review_checklist`
- `test_initialize_project_structure_copies_new_templates`
- `test_initialize_project_structure_skips_review_checklist`
- `test_validate_plan_write_accepts_independent_review`
- `test_validate_plan_write_accepts_lint_report`
- `test_stage_five_completion_state_new_fields`
- `test_stage_five_completion_state_old_field_always_false`
- `test_workspace_summary_flags_include_review_reports`
- `test_workspace_summary_next_action_s5_no_reports`
- `test_workspace_summary_next_action_s5_partial`
- `test_workspace_summary_next_action_s5_complete`

**`tests/test_chat_runtime.py`（扩展）**：

- `test_chat_stream_with_system_trigger_skips_user_message` — 验证 user message 不写入 conversation.json
- `test_chat_stream_independent_review_trigger_injects_system` — 注入对应 system prompt
- `test_chat_stream_lint_report_trigger_injects_system`
- `test_chat_stream_invalid_system_trigger_returns_error`
- `test_chat_request_validator_rejects_empty_message_without_trigger`
- `test_chat_request_validator_accepts_empty_message_with_trigger`
- `test_s5_first_entry_welcome_notice_injected` — 首次进入 S5 注入欢迎信
- `test_s5_repeat_entry_no_double_welcome` — `s5_welcome_shown_at` 已存在时不重复
- `test_conversation_state_s5_welcome_shown_at_persisted`

**`tests/test_main_api.py`（扩展）**：

- `test_independent_review_endpoint_requires_s5`
- `test_independent_review_endpoint_returns_sse`
- `test_lint_report_endpoint_requires_s5`
- `test_lint_report_endpoint_returns_summary`
- `test_chat_stream_accepts_system_trigger_field`
- `test_chat_stream_rejects_empty_message_without_trigger`

**`tests/test_packaging_docs.py`（扩展）**：

- 锁定 `skill/SKILL.md` 新 S5 段的关键句子
- 锁定旧 "完成 review-checklist.md" 短语已消失
- 锁定 `skill/modules/consulting-lifecycle.md` 新 S5 描述
- 锁定 `skill/plan-template/stage-gates.md` / `progress.md` / `tasks.md` 新 S5 文案

**`tests/test_skill_assets.py`（扩展）**：

- 见 §8.4 列表

#### 11.2 前端测试

**`frontend/tests/independentReviewDrawer.test.mjs`（新）**：

- `test_drawer_opens_on_prop_true`
- `test_drawer_closes_on_review_completed`
- `test_drawer_displays_progress_events`
- `test_drawer_displays_error_3s_then_close`
- `test_drawer_aborts_fetch_on_unmount`
- `test_drawer_esc_key_closes`

**`frontend/tests/stagePanelButtons.test.mjs`（新或扩展）**：

- `test_independent_review_button_hidden_in_s0_to_s4`
- `test_independent_review_button_visible_in_s5`
- `test_lint_report_button_visible_in_s5`
- `test_export_button_hidden_until_s6`
- `test_export_button_visible_in_s6_s7_done`
- `test_buttons_highlight_in_s5_when_not_ready`
- `test_buttons_no_highlight_when_ready`
- `test_buttons_disabled_when_running`

**`frontend/tests/workspaceSummary.test.mjs`（扩展）**：

- `test_workspace_summary_maps_independent_review_ready`
- `test_workspace_summary_maps_lint_report_ready`
- `test_workspace_summary_maps_review_reports_ready`

**`frontend/tests/chatPanelStartStream.test.mjs`（新）**：

- `test_start_stream_with_system_trigger_no_user_bubble`
- `test_start_stream_normal_renders_user_bubble`
- `test_send_message_delegates_to_start_stream`

#### 11.3 端到端 + 打包态 smoke

> **R1 Suggestion 4 接受**：显式列打包态 smoke。

新建测试项目 `piggy-v2` 走完 S0-S7 流程（手工或脚本）：

1. S0 interview → S1 outline → S2 data-log → S3 analysis → S4 写正文 — **完全不变**
2. S5 进入 → 看到主代理欢迎信 + 两个按钮高亮 + StagePanel "请点击上方按钮" 提示
3. 点"独立审查" → drawer 弹 → 流式工作 → 报告落 → drawer 自动关
4. drawer 关后 → axios.get(workspace) 验证 `flags.independent_review_ready === true` → 主代理 turn 自动开始
5. 主代理 read_file → 输出 partner 风格摘要 → 询问是否修改
6. 用户回答"先改前两条" → 主代理 edit_file 修改正文（按 S4 工具规则）
7. 用户点"AI 味自查" → 报告生成 → 主代理 turn → 按章节报告
8. 用户回答"通过" → 主代理 advance_stage(review_passed_at) → 进 S6（或 S7，取决于交付模式）
9. 整个流程主代理不出现"我已经写了 review-checklist.md"
10. 老项目（有旧 review-checklist.md）走完 S5 不被阻断

**打包态 smoke**（`tests/smoke_packaged_app.py` 扩展，R2 Suggestion 6 catch）：

- `build.bat` 重建 `dist/咨询报告助手/`
- 启动 packaged exe
- API 端点验证：
  - `GET /api/projects/{id}/independent-review/stream`（mock 模式或直接结束）
  - `POST /api/projects/{id}/lint-report`
  - `POST /api/projects/{id}/quality-check`（旧 endpoint 向后兼容）
  - `POST /api/projects/{id}/export-draft`
- 验证 `skill/scripts/quality_check.ps1` 在打包态能找到（`_internal/skill/scripts/`）
- 验证 `skill/plan-template/independent-review.md` + `lint-report.md` 在打包态存在
- 验证新项目创建后 `plan/` 目录正确
- **关键同步**：`tests/smoke_packaged_app.py:40-53` 现有 `EXPECTED_PLAN_FILES` 列表仍包含 `review-checklist.md`——必须同步更新为：
  ```python
  EXPECTED_PLAN_FILES = {
      "project-overview.md",
      "progress.md",
      "stage-gates.md",
      "notes.md",
      "outline.md",
      "research-plan.md",
      "references.md",
      "tasks.md",
      "review.md",
      "data-log.md",
      "analysis-notes.md",
      "independent-review.md",  # 新增
      "lint-report.md",          # 新增
      "presentation-plan.md",
      "delivery-log.md",
      # "review-checklist.md",  # 移除
  }
  ```
  否则改完 FORMAL_PLAN_FILES 后 smoke 测试会先被旧断言卡住

#### 11.4 回归

- 全部现有 backend 测试通过（旧 `_has_effective_review_checklist` 单测可保留作为 backwards-compat 测试，验证函数仍可调用但不再被门禁使用）
- 全部 frontend 测试通过
- DeepSeek migration 相关测试（`tests/test_chat_runtime.py` 的 DeepSeek tool-call follow-up）不被破坏
- Stage conductor v0 相关测试（`advance_stage` / legacy sanitizer）不被破坏
- **S0-S4 流程零变更**：reality_test 项目复测一次 S0-S4 流式
- 打包态 `dist/咨询报告助手/` 启动正常

### 12. 风险与缓解

| 风险 | 严重度 | 缓解 |
|---|---|---|
| 独立审查代理超 token budget | 中 | v0 直接 friendly fail（§2.5）：超过 30k 字直接 emit error event，UI 显示"正文过长，请精简后重试"；v1 worklist 完整 map-reduce 设计（R3 Bug 15） |
| 独立审查代理输出格式不符（不按 5 维度结构） | 中 | system prompt 详细约束 + 严格 5/5 anchor + completion marker；测试覆盖 marker 缺失 / anchor 缺失场景 |
| 软门禁误判（用户跑了审查但门禁说没跑） | 中 | `_has_effective_review_reports` 边界单测：空文件 / 模板 stub / 截断文件 / 缺 anchor / 缺 marker 全覆盖 |
| 旧项目 `review-checklist.md` 残留 | 低 | 不删用户文件；软门禁不读它；UI 不展示旧 checklist 相关提示 |
| 前端自动触发 turn 失败（网络抖动） | 中 | drawer 关闭前调 workspace.refresh 等 server confirmed；失败时主聊天显示错误提示+按钮重 enabled |
| 审查代理调用 OpenAI client 失败（managed 通道 timeout） | 中 | SSE event: error，drawer 显示错误 3 秒后自动关；用户可重按按钮重试 |
| 主代理被骗（用户问"已经审完了吗"，主代理虚假声称完成） | 中 | system prompt 明确"未收到 system_trigger 通知前一律否认审查完成" |
| 旧 `quality-check` endpoint 被遗漏调用 | 低 | endpoint 保留 + 返回 shape 不变；`run_quality_check()` 函数保留 |
| 独立审查代理把 `report_draft_v1.md` 误改 | 高 | write_file 强制 path 白名单 `plan/independent-review.md`；测试覆盖 |
| S5 欢迎信重复触发 | 低 | `s5_welcome_shown_at` 字段持久化（破例 conversation_state schema 加可选字段） |
| `system_trigger` 字段被恶意客户端利用绕过门禁 | 低 | system_trigger 只注入 system message，不绕过 stage gate；恶意 trigger 不会让主代理"假装"审查完成 |
| 后端 chat_stream 在 system_trigger 下注入 user message（破坏前端 UI 显示） | 中 | `_chat_stream_system_triggered` 独立路径；turn_context 加 `system_triggered=True` 让 `_finalize_assistant_turn` 跳过 user message 写入 |
| 旧 review-checklist 单元测试失败 | 低 | 旧测试改名 `test_*_backwards_compat`，验证 `_has_effective_review_checklist` 仍可被调用但不被门禁使用 |
| 并发：用户同时点两个按钮 | 中 | per-project lock + 前端 disabled 双重防御 |
| 并发：审查跑到一半用户 advance_stage | 中 | `advance_stage(review_passed_at)` 在 lock held 时返回 error |
| SSE 断连：用户刷新 | 低 | fetch reader 错误恢复；partial 落盘的报告无 marker 判定为 False；重审一次 |
| DeepSeek tool-call 400（reasoning_content / tool_choice） | 中 | 复用 chat.py 同款 helper：`_should_send_explicit_tool_choice` + `_extract_reasoning_content_from_message` + null SDK dump 字段过滤 |
| `s5_welcome_shown_at` 老项目首次进 S5 多次触发 | 低 | 字段不存在时 `_load_conversation_state` 返回 None，首次进 S5 时设置；之后幂等 |

### 13. 实施分阶段

> **R4 Bug 18 + 19 修正**：v4 方案 Commit 1 加 `FORMAL_PLAN_FILES` 后主代理可伪造写入（拒写拦截还没落）；Commit 3 切 gate 但前端按钮在 Commit 4 → 中间态用户看到"点上方按钮"但 UI 没按钮。
>
> **v5 新方案——把切换风险压到最后一个 commit**：
>
> - **Commit 1**：100% additive backend（不加 FORMAL_PLAN_FILES，不加任何拦截）
> - **Commit 2**：FORMAL_PLAN_FILES 加新文件 + **同时**加主代理拒写拦截 + 审查代理 + lint 脚本——这俩必须同一个 commit（避免 R4 Bug 18 的中间态独立性破洞）
> - **Commit 3**：endpoints + chat_stream + finalize 分支 + record_stage_checkpoint lock（dormant 路径就绪，但 SKILL 还说旧流程 + 前端还没按钮，用户不会触发）
> - **Commit 4**：**用户可见 atomic cutover**——`CHECKPOINT_PREREQ.review_passed_at` 切换 + `STAGE_CHECKLIST_ITEMS["S5"]` + `_build_completed_items` + SKILL / lifecycle / templates 同步 + **前端按钮 / drawer / ChatPanel startStream** + smoke `EXPECTED_PLAN_FILES` 同步——全部一次性落 main
> - **Commit 5**：端到端 + cutover doc

每个 commit 末尾嵌入 codex review loop（spec / quality 双轮），通过 APPROVED 才进下一 commit。

**Commit 0**：spec 与 plan 落 main（本次 spec 通过 R5 review + plan 通过 review 后）

**Commit 1：后端 100% additive（dormant）**

只做**纯增加**——所有新函数、新字段都不被任何门禁调用。**不动** `FORMAL_PLAN_FILES`，不动主代理任何写入路径，不加新模板。

- `backend/models.py` — `ChatRequest.message_text` Optional + validator + `system_trigger` 字段（向后兼容：旧前端传非空 message 不受影响）
- `backend/skill.py`：
  - 新增 `_has_effective_independent_review` / `_has_effective_lint_report` / `_has_effective_review_reports` helper（dormant，不被任何门禁调用）
  - `_stage_five_completion_state` 扩展返回字段（旧字段 `review_checklist_ready` 仍由 `_has_effective_review_checklist` 计算，**新字段** `independent_review_ready` / `lint_report_ready` / `review_reports_ready` 也填，但这些新字段还没被 `CHECKPOINT_PREREQ` / `missing_for_review_pass` 读）
  - `_infer_stage_state` flags 加新字段
  - `get_workspace_summary` 加新 flag 字段
  - `_empty_conversation_state` / `_load_conversation_state` / `_save_conversation_state_atomically` 加 `s5_welcome_shown_at` 字段三处同步（R3 Bug 16）
  - `STAGE_CHECKLIST_ITEMS["S5"]` / `_build_completed_items` / `CHECKPOINT_PREREQ.review_passed_at` / `FORMAL_PLAN_FILES` **完全不动**——留到 Commit 4 atomic cutover
- 单元测试：`test_skill_engine.py` 扩展新 helper 单测 + `_stage_five_completion_state` 新字段单测 + conversation_state load/save roundtrip 单测

**中间态校验**：Commit 1 落 main 后系统行为**完全等价**于切换前——`validate_plan_write` 仍按旧 FORMAL_PLAN_FILES 工作，主代理路径不变，UI 不变。`tests/test_chat_runtime.py` 现有全部测试不变。

**Commit 2：审查代理 + lint 脚本 + 主代理拒写拦截 + FORMAL_PLAN_FILES 扩展（同时 atomic）**

**关键：FORMAL_PLAN_FILES 加新文件**和**主代理拒写拦截**必须同一个 commit（R4 Bug 18 强制约束），否则中间态会破独立性边界。

- `backend/skill.py`：
  - `FORMAL_PLAN_FILES` 加 `independent-review.md` + `lint-report.md`（保留 `review-checklist.md`）
  - 这让 `validate_plan_write` 接受新两文件 + `_initialize_project_structure` 创建新项目时自动复制新 stub 模板
- `backend/chat.py`：
  - 主代理 write_file / edit_file 拦截分支**显式拒绝** `plan/independent-review.md` 和 `plan/lint-report.md`（chat.py:4775 附近 path 拒绝分支，R2 Bug 10）
- `backend/independent_review.py` 新模块（含 DeepSeek 兼容 helper，行为矩阵测试锁死等价）
- `skill/scripts/quality_check.ps1` 重构（4 维度 + `-FilePath` / `-OutputPath` / `-DryRun` 参数 + completion marker）；旧 stdout 模式（无 `-OutputPath`）保留向后兼容
- `backend/report_tools.py` — `run_lint_report` 新增（`run_quality_check` 旧函数 `stdout or stderr` 返回 shape 严格不变）
- `skill/plan-template/independent-review.md` + `lint-report.md` 新增 stub 模板（带 `:pending` marker）
- per-project module-level lock 工厂函数
- 单元测试：`test_independent_review.py` + `test_lint_report.py` + 主代理拒写测试 + 主代理拒 edit 测试（R3 Suggestion 7）

**中间态校验**：Commit 2 落 main 后：
- 新建项目会自动包含两份新 stub 文件（无害——marker 是 `:pending` 不会通过软门禁）
- 主代理理论上不会主动写新文件（SKILL 还说写 review-checklist）；拒写拦截在那里也无害
- endpoints / chat_stream system_trigger / `CHECKPOINT_PREREQ` 切换全没动——主流程不变
- **小副作用**（R5 改进建议 #1）：因为 `/api/projects/{id}/files` 会 rglob 所有 markdown，前端文件 tab 会展示这两个 pending stub。这是**低风险可接受**——用户能看到文件但内容是 `[等待运行 — 请在 S5 阶段点击工作区"独立审查"按钮]`，不会误以为已审完
- 主流程对用户**完全等价**于切换前

**Commit 3：endpoints + chat_stream system_trigger + finalize 分支（dormant，不切 gate，不接 S5 欢迎信主路径）**

- `backend/main.py` — `/independent-review/stream` (GET SSE) + `/lint-report` (POST) endpoints + S5 校验 + non-blocking lock acquire 返回 409
- `backend/chat.py`：
  - `_chat_stream_system_triggered` 独立路径（显式调 `_build_turn_context` 初始化避免 stale context，R3 Bug 17）
  - `_finalize_assistant_turn` 加 `system_triggered` 分支（R2 Bug 5）
  - **S5 欢迎信 helper（`_should_emit_s5_welcome` / `_mark_s5_welcome_shown`）实现**——但**不在 `_chat_stream_unlocked()` 调用**（保持 dormant，避免 R5 Bug 21）
- `backend/skill.py:record_stage_checkpoint` — `review_passed_at` 加 lock 检查（chat tool 走 advance_stage 自动覆盖，R2 Bug 8）
- 单元测试：`test_main_api.py` 新 endpoint 测试 + `test_chat_runtime.py` system_trigger / finalize 分支 / S5 welcome helper 单测（直接测 helper，不测 chat_stream 整合）

**中间态校验**：Commit 3 落 main 后 endpoints **可调**，但因为：
- SKILL.md 仍说写 review-checklist（主代理不会主动调 endpoint）
- 前端按钮还没出现（用户也调不了）
- `_chat_stream_unlocked()` **不调** S5 welcome 注入（dormant helper 不被触发）
- `CHECKPOINT_PREREQ.review_passed_at` 还是旧 `_has_effective_review_checklist`

系统对用户行为仍**完全等价**于切换前。只有手工 POST 才能触发新路径——但目标用户不会这么做。

**Commit 4：用户可见 atomic cutover（backend gate + S5 欢迎信激活 + SKILL + 前端按钮 + smoke）**

**这一个 commit 是用户可见原子切换**——backend gate / S5 欢迎信调用点 / SKILL 文档 / 前端按钮 / smoke 必须同步落 main（R5 Bug 21 修正：S5 welcome 注入调用点从 Commit 3 移到这里，避免提示用户点不存在的按钮）。

Backend 切换：

- `backend/skill.py`：
  - `CHECKPOINT_PREREQ.review_passed_at` helper 函数名切换为 `_has_effective_review_reports`（atomic 切换点）
  - `FORMAL_PLAN_FILES` 同时移除 `review-checklist.md`（**显式列在切换清单**，避免 R5 改进建议 #2 提到的"测试 bullet 才提"问题）
  - `STAGE_CHECKLIST_ITEMS["S5"]` 文案换新
  - `_build_completed_items` S5 两处逻辑改用新 flags（R2 Bug 1）
- `backend/chat.py`：
  - **激活 S5 欢迎信注入调用点**：在 `_chat_stream_unlocked()` 加调用 `_should_emit_s5_welcome` + 注入 welcome system message + turn 完成后 `_mark_s5_welcome_shown`（R5 Bug 21）

Skill 文档同步：

- `skill/SKILL.md` S5 段重写（指向新流程）
- `skill/modules/consulting-lifecycle.md` 行 20 同步（行 50 不动）
- `skill/plan-template/progress.md` / `stage-gates.md` / `tasks.md` 同步
- `tests/test_packaging_docs.py` 锁定新文案
- `tests/test_skill_assets.py` 验证 FORMAL_PLAN_FILES 含新文件 + 不含 review-checklist

前端（与 backend 切换同步，避免 R4 Bug 19）：

- `frontend/src/components/IndependentReviewDrawer.jsx` 新组件
- `frontend/src/components/StagePanel.jsx` 按钮阶段化 + 高亮 + running 状态 + ESC 关闭
- `frontend/src/components/WorkspacePanel.jsx` drawer trigger + axios 调用
- `frontend/src/components/ChatPanel.jsx` `sendMessage` 重构为 `startStream`
- `frontend/src/utils/workspaceSummary.js` 字段映射

打包 smoke 同步：

- `tests/smoke_packaged_app.py:40-53` 的 `EXPECTED_PLAN_FILES` 同步（R2 Suggestion 6）

测试：

- backend：`test_skill_engine.py` 扩展 `CHECKPOINT_PREREQ` 切换后效
- frontend：drawer / stagePanelButtons / workspaceSummary / chatPanelStartStream 全套
- packaging：smoke 新 EXPECTED_PLAN_FILES

**中间态校验**：Commit 4 落 main 后系统**用户可见原子切换到新 S5 流程**：
- 用户进 S5 → 主代理欢迎信介绍新流程 → 看到两个按钮 → 点按钮 → 走完审查
- 老项目（已有 `plan/review-checklist.md`）：旧文件保留不读；新 gate 要求两份新报告
- 新项目：从一开始就走新流程

**Commit 5：端到端 + 回归 + 打包态 smoke + cutover**

- 端到端 piggy-v2 走完 S0-S7
- 打包态 `dist/咨询报告助手/` 重建 + smoke
- `docs/superpowers/cutover_report_2026-05-21_s5-redesign.md`
- 更新 `docs/current-worklist.md`

### 14. 验收标准

**Stage 5 走通**：

1. 进入 S5 → 用户看到主代理欢迎信介绍新流程（一句话）
2. 两个按钮在 S5 显示且高亮
3. 点独立审查 → drawer 弹 → 流式工作 → 报告落 → drawer 自动关
4. drawer 关后服务端确认 ready → 主代理 turn 自动开始
5. 主代理 read_file 读独立审查报告 → 输出 partner 风格摘要 + 询问意见
6. 用户回答"先改前两条" → 主代理 edit_file 修改正文
7. 用户点 AI 味自查 → 同上流程
8. 用户回答"通过" → 主代理 advance_stage(review_passed_at) → 进 S6 或 S7
9. 整个流程主代理不出现"我已经写了 review-checklist.md"
10. 老项目 review-checklist.md 残留不阻断推进

**回归**：

- 全部现有 backend 测试通过（旧 `_has_effective_review_checklist` 单测可保留为 backwards-compat）
- 全部 frontend 测试通过
- DeepSeek migration / stage conductor v0 相关测试不被破坏
- **S0-S4 流程零变更**：reality_test 项目复测一次 S0-S4
- 打包态 dist/咨询报告助手/ 启动正常 + 完整 S0-S7 走通

**契约稳定**：

- `conversation.json` schema 不变
- `conversation_state.json` schema 仅新增可选 `s5_welcome_shown_at`（向后兼容）
- `stage_checkpoints.json` schema 不变
- `ChatRequest.message_text` 改 Optional + validator（向后兼容：现有客户端传非空 message 不受影响）
- `ChatRequest.system_trigger` 新增可选字段，老前端不传仍兼容
- `workspaceSummary` 只加新 flag，老前端忽略仍可工作

---

**End of spec v2**
