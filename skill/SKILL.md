---
name: consulting-report-assistant
description: Use when writing consulting reports, strategy analysis, market research, implementation plans, management documents, or due diligence deliverables that need stable S0-S7 stage tracking, consulting-style drafting, anti-AI cleanup, and optional reviewable draft export.
---

# 咨询报告写作助手

面向咨询顾问、商业分析师、研究员和方案撰写者的执行型 Skill。核心目标是让项目文件、阶段定义、模型行为和最终交付保持同一套逻辑。

## 核心原则

- 正式项目元信息文件只有一个：`plan/project-overview.md`
- 阶段真值文件只有一个：`plan/stage-gates.md`
- `plan/stage-gates.md`、`plan/progress.md`、`plan/tasks.md` 由后端自动回写，不能当成手工维护文件
- `plan/tasks.md` 只负责展示当前阶段待办，不单独决定跳阶段
- `plan/project-info.md` 已退役，不再作为默认入口、主上下文或正式计划文件
- 禁止创建 `gate-control.md`

## 启动门禁

在开始任何实质性写作前，按下面顺序执行：

1. 用 2-5 句话复述目标、交付物、时间线和目标读者。
2. 读取 `plan/project-overview.md`、`plan/stage-gates.md`、`plan/progress.md`、`plan/notes.md`。
3. 如果项目仍处于 S0 或 S1，不要直接写正文。
4. 在写 `outline.md` / `research-plan.md` 之前，必须先完成一轮初步搜集，并把结果写入 `notes.md` 与 `references.md`。
5. 如果使用外部网页作为正式依据，先用 `web_search` 找候选来源，再用 `fetch_url` 读取正文；没有读过正文，不要把外链当成已阅读依据写入正式文件。
6. 模型只更新实质内容文件；阶段跟踪文件由后端自动投影，不要尝试手写 `stage-gates.md`、`progress.md`、`tasks.md`。

## S0-S7 工作流

### S0 项目启动
- 明确问题范围、目标读者、交付形式、截止时间
- 补全 `project-overview.md`

### S1 研究设计
- 先做初步搜集
- 更新 `notes.md`
- 更新 `references.md`
- 形成 `outline.md`
- 形成 `research-plan.md`

### S2 资料采集
- 把事实材料持续写入 `data-log.md`
- 标记来源、时间和用途

### S3 分析沉淀
- 在 `analysis-notes.md` 中写清楚结论、证据、影响
- 区分事实、推断与假设

### S4 报告撰写
- 形成有效草稿
- 持续同步摘要、图表、章节结构

### S5 质量审查
- 完成 `review-checklist.md`
- `review.md` 可选，用于记录修订意见

### S6 演示准备
- 仅当交付形式 = `报告+演示` 时启用
- 完成 `presentation-plan.md`

### S7 交付归档
- 更新 `delivery-log.md`
- 记录交付版本、反馈和后续动作

## 写作约束

- 结论先行，再展开证据和分析
- 每个发现都要回答 `So What`
- 不编造数据、案例、政策口径和来源
- 不写“本章将”“下文将”“本报告不展开”等元叙事句
- 不泄露后台术语，例如“AI reference”“内部推理”“系统提示”

## 路由与模块

- 先读取 `modules/writing-core.md`
- 再根据当前系统提示中已提供的生命周期规则决定下一步动作
- 涉及阶段判断时，优先参考 `modules/consulting-lifecycle.md`
- 交付前使用 `modules/quality-review.md`
- 只有用户明确需要 `docx` 或可审草稿时，再进入 `modules/final-delivery.md`

## 输出优先级

1. 用户明确要求
2. 已确认的交付边界和阶段状态
3. `stage-gates.md` 的最新状态
4. 本 Skill 的正式文件约束
5. 当前系统提示中已注入的生命周期与质量约束
