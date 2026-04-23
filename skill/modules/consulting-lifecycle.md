# 咨询项目生命周期模块

## 总原则

- 阶段真值文件只有一个：`plan/stage-gates.md`
- `plan/progress.md`、`plan/tasks.md` 都是后端生成的阶段跟踪快照，不由模型手写
- 在 `outline.md` / `research-plan.md` 之前，必须先完成一轮初步搜集，并把结果写入 `notes.md` 与 `references.md`
- 禁止创建 `gate-control.md`，禁止把 `project-info.md` 当成正式主信息文件
- 外部网页证据流程固定为：`web_search` 找候选来源，`fetch_url` 读候选来源正文

## S0-S7 阶段定义

| 阶段 | 目标 | 关键动作 | 关键文件 |
|------|------|----------|----------|
| S0 项目启动 | 明确范围与交付边界 | 澄清需求、定义目标、确认交付形式 | `project-overview.md` |
| S1 研究设计 | 形成研究边界与执行方案 | 初步搜集、沉淀备注、列出来源、确定大纲和研究计划 | `notes.md` `references.md` `outline.md` `research-plan.md` |
| S2 资料采集 | 沉淀事实材料 | 访谈、网页抓取、附件阅读、事实摘录 | `data-log.md` |
| S3 分析沉淀 | 提炼洞察与判断 | 归纳发现、区分事实与假设、形成主线 | `analysis-notes.md` |
| S4 报告撰写 | 形成有效报告草稿 | 撰写正文、补充图表、同步摘要 | `content/report_draft_v1.md` |
| S5 质量审查 | 完成系统复核 | 勾选审查清单、必要时记录修订意见 | `review-checklist.md` `review.md`(可选) |
| S6 演示准备 | 为汇报场景准备材料 | 仅当交付形式 = `报告+演示` 时启用，准备 PPT / 讲稿 / Q&A | `presentation-plan.md` |
| S7 交付归档 | 记录交付与后续动作 | 更新交付记录、沉淀反馈和归档 | `delivery-log.md` |

## 执行要求

### S0 项目启动
1. 补全 `project-overview.md`
2. 明确目标读者、交付形式、篇幅、截止时间
3. 不要手写 `progress.md` / `tasks.md` / `stage-gates.md`

### S1 研究设计
1. 先做一轮必要的初步搜集
2. 把边界、假设、关键问题写入 `notes.md`
3. 把至少 2 个有效来源写入 `references.md`
4. 如果来源来自外部网页，必须先 `fetch_url`
5. 然后再产出 `outline.md` 与 `research-plan.md`

### S2 资料采集
1. 所有有效事实进入 `data-log.md`
2. 标注来源、时间、事实点和可能用途
3. 发现缺口时回到搜集动作补证

### S3 分析沉淀
1. 在 `analysis-notes.md` 中写清楚结论、证据、影响
2. 不把猜测伪装成事实
3. 保证分析主线能支撑后续报告结构

### S4-S7
1. 报告、审查、演示、交付均以前一阶段证据到位为前提
2. 报告-only 项目可从 S5 直接进入 S7
3. 报告+演示项目不能跳过 S6
