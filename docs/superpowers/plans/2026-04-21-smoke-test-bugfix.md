# Plan: 2026-04-21 Smoke Test Bug Fix

## Context

合并 `feat/stage-advance-gates` 到 main 并本机打出 91 MB 包后，用户 smoke test 暴露 7 个 bug（详见 `docs/current-worklist.md` 第 1a 条）。

本 plan 先修**核心 4 个**（Bug A、B、D、F）+ **前端复制体验**（worklist #8）。Bug C/G/H 作为后续跟进。

## 根因摘要（已诊断闭环）

| Bug | 精准位置 | 根因 |
|---|---|---|
| A | `backend/chat.py:3067-3069` | `_should_allow_non_plan_write` 发现 `outline_confirmed_at` 存在即 `return True`，无后续阶段校验 |
| B | `backend/skill.py:record_stage_checkpoint` | `set` 操作前不校验对应 plan 文件是否有效存在；弱关键词（`backend/chat.py:165`"没问题/好的/OK"）能在 outline.md 不存在时就设 `outline_confirmed_at` |
| D | `skill/SKILL.md:46-49` vs `backend/skill.py:62` | 正则要求 `### [DL-YYYY-NN]` 条目格式，但 skill 文档从未规定该格式；模型自发写表格 → 有效来源永远 0 |
| F | `backend/chat.py:1217-1224` | 反幻觉基础设施（`FILE_UPDATE_VERBS` + `_expected_plan_writes_for_message`）已存在，但白名单硬编码 5 条路径（`report_draft_v1.md` / `content/report.md` / `content/draft.md` / `content/final-report.md` / `output/final-report.md`），**漏了 `content/report_draft_v1.md`**，导致该路径的伪造"已同步"声明逃过检测 |
| 复制 | `frontend/src/components/ChatPanel.jsx` + `FilePreviewPanel.jsx` | 聊天消息气泡 & 文件预览面板的容器上某层阻止了 `user-select`，用户无法框选复制 |

## Agent 分派（三路并行）

### Agent 1 — Bug A + B + F（后端门禁 & 反幻觉）
- 派发方式：裸 `codex exec`（gpt-5.4 xhigh），`.codex-run/task-4-*.log`
- 必须 TDD：先写 failing test，再改代码
- 改动文件：
  - `backend/chat.py`（Bug A 门禁阶段校验 / Bug F 白名单扩展为前缀匹配）
  - `backend/skill.py`（Bug B `record_stage_checkpoint` 前置校验）
  - `tests/test_chat_runtime.py` + `tests/test_skill_engine.py`
- 验收：新增三条单测全绿，现有 397 条不回归

### Agent 2 — Bug D（Skill 契约对齐）
- 派发方式：裸 `codex exec`，`.codex-run/task-5-*.log`
- 改动文件：
  - `skill/SKILL.md`（§S2 明确 `### [DL-YYYY-NN] 事实摘要` 格式 + 完整条目示例含 URL / material:xxx / 访谈标签）
  - `backend/chat.py` 或 `backend/skill.py`（首次写 data-log.md 时通过 `_emit_system_notice_once` 注入一次 DL-id 格式提示；无需 S2 以外触发）
  - `tests/test_skill_engine.py` 加对新格式的计数测试
- 验收：新增测试绿，现有测试不回归

### Agent 3 — 前端复制体验（worklist #8）
- 派发方式：`general-purpose` agent + `model: sonnet`
- 改动文件：
  - `frontend/src/components/ChatPanel.jsx`（消息气泡容器 `user-select: text`）
  - `frontend/src/components/FilePreviewPanel.jsx`（预览内容区 `user-select: text`）
- 保留：消息右上角复制按钮不动
- 约束：只改 CSS / 事件拦截，不引入富文本逻辑
- 验收：`cd frontend && npm run build` 零错；人工描述交互行为预期

## 活性监控

派活后启动一条 30 min cron（`7,37 * * * *`），检查三个 bash background job + `.codex-run/*-full.log` tail，确认 codex 没挂。三个 Agent 全部 completed 后 `CronDelete`。

## 非本 plan 范围

以下 bug 不在本次修复内：
- **Bug C**（S0 质量门槛）— 需要产品设计讨论
- **Bug G**（回退级联清理文件）— 边缘体验
- **Bug H**（S1 next_stage_hint 空）— 边缘体验
- worklist #2-7 老待办

验收通过后跟 Bug E 一并从 worklist 1a 中标记为已解决，其余保留。
