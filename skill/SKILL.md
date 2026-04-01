---
name: consulting-report-assistant
description: Use when writing consulting reports, strategy analysis, market research, implementation plans, management documents, or due diligence deliverables that need plan tracking, consulting-style drafting, anti-AI cleanup, stable module routing, and optional reviewable draft export.
---

# 咨询报告写作助手

面向咨询顾问、商业分析师、战略规划人员的执行型 Skill。它优先解决四类问题：任务刚接手时信息不全、长文写作过程中上下文容易丢、交付前需要去 AI 味与质量复核，以及共享使用时模块和边界容易跑偏。

## 核心定位

- 以通用咨询报告为主路径，优先服务报告、研究、方案、制度等“正文型交付”
- 默认产物是可持续迭代的项目内文本资产：`plan/*.md`、报告正文、图表脚本、分析框架
- Word/PDF 相关能力属于增强选项，用于形成 `可审草稿`，不替代最终中文排版
- 模块命名与路由口径以 `evals/capability-map.json` 为唯一事实源

## 启动门禁

任何实质性写作开始前，按下面顺序执行：

1. 先对齐任务
- 用 2-5 句话复述目标、交付物、时间线
- 至少确认：项目背景、目标读者、目标篇幅、当前阶段、截止日期

2. 检查或创建 `plan/`
- 若不存在 `plan/`，立即用 `plan-template/` 初始化
- 若已存在，先读取 `project-overview.md`、`progress.md`、`notes.md`
- 先读取项目材料；材料不足时先用 `web_search` / `fetch_url` 补证据，再整理进 `notes.md`、`references.md`
- 先写 `notes.md`、`references.md`，再写 `outline.md` / `research-plan.md`；只用正式 `plan/*.md` 文件，不写 `gate-control.md`

3. 任务入档
- 执行前在 `plan/progress.md` 写入当前任务
- 执行后回写产物路径、已完成内容、下一步动作

4. 无 `plan/` 不进入长任务
- 只有一问一答型小问题可以跳过

## 默认工作流

### 1. 通用主流程

1. 读取 `modules/writing-core.md`，明确通用写作规范
2. 读取 `modules/common-gotchas.md`，先规避高频失误
3. 按 `evals/capability-map.json` 中的模块口径路由到专项模块
4. 输出章节级正文或大纲，不直接把碎片意见堆成答案
5. 用 `modules/quality-review.md` 和 `scripts/quality_check.*` 做交付前检查

### 2. 短周期 / 救火模式

当截止时间极短、任务更偏“先交上去再精修”时，切换到短周期模式：

1. 先锁定交付边界，只确认目标、篇幅、已有材料、截止时间
2. 优先完成目录、核心章节和关键图表，不在首轮追求满配分析
3. 所有未完全核实的信息显式标注待核位置，不得伪造数据和来源
4. 先通过质量脚本清掉元叙事、后台表述、占位符，再决定是否导出 `可审草稿`

## 模块路由

### 核心模块

- `modules/writing-core.md`
  作用：通用咨询写作规范、章节展开方式、去 AI 味原则
- `modules/executive-summary.md`
  作用：从长篇报告中提炼面向高层的执行摘要
- `modules/common-gotchas.md`
  作用：常见踩坑、元叙事表达、自我解释句、后台推进词泄漏
- `modules/quality-review.md`
  作用：结构敏感的质量审查方法和交付前复核
- `modules/final-delivery.md`
  作用：`markdown -> docx 可审草稿 -> pdf 预览/抽查 -> 人工终排` 的增强交付流程

### 专项模块

- `modules/strategy-consulting.md`
- `modules/market-research.md`
- `modules/specialized-research.md`
- `modules/management-system.md`
- `modules/implementation-plan.md`
- `modules/due-diligence.md`

### 工具与支撑模块

- `modules/business-charts.md`
- `modules/framework-diagrams.md`
- `modules/data-analysis.md`
- `modules/recommendation-framework.md`
- `modules/templates-collection.md`
- `modules/consulting-lifecycle.md`

## 写作规则

### 1. 咨询表达

- 结论先行，再展开证据与推导
- 每个发现都回答 `So What`
- 建议必须落到优先级、动作、阶段或量化目标

### 2. 去 AI 味

- 不写“本章将”“下文将”“本报告不展开”等自我解释句
- 不暴露后台推进词，如“技术规范书”“内部材料”“AI reference”等
- 不用机械过渡词和空洞强调句撑篇幅
- 能写成段落就不要拆成过碎的小点

### 3. 数据与事实

- 不编造数据、案例、政策口径
- 数据必须标注来源和时间
- 高风险判断要么可追溯，要么明确标注为研判

## 交付边界

### 默认交付

- 报告正文：`*.md` / 纯文本
- 过程资产：`plan/*.md`
- 图表脚本：`*.py`

### 增强交付

当用户明确需要 `.docx` 或预览版 PDF 时，调用 `modules/final-delivery.md`：

- 使用 `scripts/export_draft.ps1` 或 `scripts/export_draft.sh` 导出 `可审草稿`
- 明确提示这是 reviewable draft，不承诺最终公司模板、页眉页脚、页码、中文字体、图表版式全部到位
- 最终排版仍由人工在 Word 或正式模板中完成

## 输出优先级

规则冲突时，按下列顺序执行：

1. 用户明确要求
2. 已确认的大纲、交付范围、时间边界
3. 本 Skill 的启动门禁和交付边界
4. capability map 约束下的模块路由
5. 专项模块
6. 通用模块

## 版本信息

- 版本：1.2.0
- 创建日期：2026-03-17
- 最近更新：2026-03-26
- 维护目标：可执行、可追踪、可审阅、评测可跑、边界清晰
