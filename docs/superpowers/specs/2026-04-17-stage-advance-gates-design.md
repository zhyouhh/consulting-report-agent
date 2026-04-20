> **实施状态**：`设计已定稿，待工程落地`
> **审查状态**：经 9 轮迭代（2 轮 Claude 工程/UX 审查 + 7 轮 codex 独立审查）于 2026-04-17 通过最终版审查。
> **接手指引**：配套落地计划在同目录 `../plans/2026-04-17-stage-advance-gates.md`，按 Rollout Order 顺序开 8 个 task 落地即可。整体追踪在 `docs/current-worklist.md` 第 8 项。

# 阶段推进门禁重构设计稿

## 1. 背景

在最新 85 MB 桌面包上用"猪猪侠大战超人专项研究报告"做了一轮端到端实测，结论是阶段门禁形同虚设：

1. 项目预期 `6000` 字，模型只写了 `1200` 字就宣布"已进入 S7 交付归档阶段"。
2. 整个 S0 → S7 过程一共 `4 分 6 秒`，模型在用户仅回复"继续吧"的情况下一路跑完。
3. `review-checklist.md` 由模型自己打勾、自己签"审查人：咨询报告写作助手"，自己下结论"建议通过"。
4. `delivery-log.md` 的"客户反馈收集"勾上了，但文件内容是 `(待记录)` 占位符。
5. 当用户指出"你只写了 1200 字"后，模型的扩写 2000 字被后端 `_should_allow_non_plan_write` 门禁挡掉了 `content/report.md` 写入，但模型没有告诉用户被挡，而是把扩写内容静默贴在聊天框里，用户以为已经落盘。

根因来自三层：

1. `backend/skill.py:_infer_stage_state` 只按文件存在性推断阶段，S2/S3/S4/S5/S7 的"完成"实际上只是"对应文件非空"。没有用户确认环节，没有内容质量门槛。
2. `backend/chat.py:_should_allow_non_plan_write` 的授权关键词库不认"质问/反馈"类语义（例如 "？？？字数不够" "太短了"），被挡住后也没有让模型把错误告知用户。
3. `review-checklist.md` / `delivery-log.md` 允许模型自填"审查人""客户反馈"字段，后端不做任何内容检查就投影到阶段勾选状态。

与此同时，用户的原始产品意图是：

1. 大纲确认一次之后，后续 S2 / S3 / S4 都可以自由继续推进，不再需要每次确认。
2. S4 是人机协作主战场，一章一章改、反复改，都不应该需要重新授权。
3. S5 / S6 / S7 属于后期关卡，每一步都必须用户显式点头。
4. 搜索免费额度 `5000` 次/月要省着花，但节流不能省到"模型每轮只能搜 2 次、跨阶段之后就不再搜"的程度。

## 2. 目标

本次改造目标是：

1. 把阶段推进从"文件存在性"改成"文件就绪 AND 用户确认戳 AND 质量门槛"三件齐备。
2. 引入持久化 `stage_checkpoints.json`，作为阶段推进的唯一用户确认真值源。
3. 设计一张单按钮上下文感知 UI：右侧工作区面板永远只有一个主按钮，按当前阶段变身文案。
4. 开放大纲确认后 S2 / S3 / S4 的自由写作通道，不再每轮重新授权。
5. 质量门槛按 `expected_length` 缩放，设上限避免逼模型凑数造假。
6. 让模型在被 tool error 挡住时必须在可见回复里说明原因，禁止静默绕过。
7. 拦截 `review-checklist.md` / `delivery-log.md` 里的"自签字段"写入。
8. 保留回退能力，通过双通道实现（UI 菜单 + 对话关键词），避免错点无法恢复。

## 3. 非目标

本次不做：

1. 不放宽 `per_turn_searches`（保持 `2`）—— 真问题不是配额，是模型懒。
2. 不引入 LLM 层面的 stage-routing prompt 改写（例如"让模型自己调用 advance_stage 工具"），保持阶段推断在后端单点计算。
3. 不给每个阶段独立做按钮 / 卡片 / 进度条之类的重 UI，维持单按钮上下文感知。
4. 不做阶段时长 SLA、截止日期告警这类新功能。
5. 不做跨项目的阶段模板复用。
6. 不动搜索池 provider 层逻辑、管理通道薄中转、桌面桥、打包产物格式。

## 4. 已确认决策

1. 阶段推进分三类：**硬关卡**（需用户显式点头）、**质量门槛**（自动推进但必须满足量化条件）、**全自由**（大纲戳覆盖期内的 S4 内部反复改写）。
2. 4 个硬关卡：`S1 → S2`（确认大纲）、`S4 → S5`（开始审查）、`S5 → S6`（仅报告+演示）/`S5 → S7`（仅报告）、`S6 → S7`（演示完成，仅报告+演示）、`S7 → 完成`（归档）。S5 分流规则见 §5.1。
3. 2 个质量门槛：`S2 → S3` 看 `data-log.md` 的来源条目数，`S3 → S4` 看 `analysis-notes.md` 的反向引用数。
4. 1 个大纲通行证：`outline_confirmed_at` 戳存在时，整个 S2 / S3 / S4 内部 `can_write_non_plan = True`；但**用户在本轮说"先别写正文"之类的短期阻断短语时，本轮返回 False，戳保留**（见 §11.4）。
5. UI 按钮采用上下文感知。S4 和 S5 都是**双按钮**——S4：继续扩写 + 开始审查（字数达标时"开始审查"新出现）；S5：审查通过 + 回去再改（永远两个按钮对称，避免"不通过"被折进 `⋯` 菜单）。其他阶段单按钮或不显示。S0 / S2 / S3 不显示主按钮，S2 / S3 用内联计数器代替（见 §9.3）。
6. 回退通过 `⋯` 菜单 + 对话关键词。菜单分一级（阶段敏感的常规回退）和二级（高级回退，含"完全重置大纲确认"）。所有回退只清 checkpoint 戳，不删文件。
7. **回退级联**：清某个戳同时清其后所有戳，避免孤儿戳死循环（见 §5.2）。
8. 质量门槛 N / M 按 `expected_length` 缩放，N 上限 `12`，M 上限 `8`。`expected_length` 无法解析时退化为 `3000` 并在 workspace 响应里返回 `fallback_used=true`，前端显式告知用户当前使用默认值。
9. 模型被 tool error 挡住时的告知策略**双重保障**：§11.1 prompt 约束 + §11.2 后端主动注入 `system_notice` 事件到输出流（不依赖模型配合）。
10. `review-checklist.md` 的"审查人"字段和 `delivery-log.md` 的"客户反馈"/"项目状态"字段做 write_file 层拦截。拦截由对应 checkpoint 戳**自动解除**（戳落下后不再拦截），不留手动豁免机关（见 §12.3）。
11. 关键词识别**阶段敏感**：同一短语（例如"行"/"可以"）根据当前阶段映射到不同 checkpoint。强关键词无歧义通用；弱关键词仅在特定阶段生效（见 §8）。**S4 不使用弱关键词**——S4 是反复改写主战场，用户说"挺好，继续写下一节" 是高频对话模式，不应被误判为"结束撰写进入审查"。S4 进入 S5 只通过强关键词（"开始审查"）或 UI 按钮，不接受弱关键词。
12. 旧项目迁移**只补 `outline_confirmed_at`**，`review_started_at` 及以后全部留空，强制用户重新点击（见 §16.2）。

## 5. 阶段推进规则

统一推进逻辑：

1. 阶段 N → 阶段 N+1 允许推进，当且仅当以下三组条件**全部**满足：
   - **文件就绪**：阶段 N 对应的 plan 文件或 content 文件满足 `_has_effective_*` 检查。
   - **用户确认**（仅硬关卡）：`stage_checkpoints.json` 中对应戳存在。
   - **质量门槛**（仅 S2 → S3、S3 → S4、S4 → S5）：对应量化条件满足。
2. 若任一不满足，`_infer_stage_state` 停在阶段 N，不推进。
3. 回退只清对应戳，不修改文件，允许用户反复推进/回退。

完整规则表：

| 推进动作 | 文件就绪 | 用户确认戳 | 质量门槛 | 类型 |
|---|---|---|---|---|
| S0 → S1 | `project-overview.md` | — | — | 自动 |
| S1 → S2 | `outline.md` + `research-plan.md` | `outline_confirmed_at` | — | **硬关卡** |
| S2 → S3 | `data-log.md` | — | 来源条目 ≥ N | 质量门槛 |
| S3 → S4 | `analysis-notes.md` | — | `[DL-xxx]` 引用 ≥ M | 质量门槛 |
| S4 → S5 | `content/report.md` 等任一 | `review_started_at` | 字数 ≥ `expected_length × 0.7` | **硬关卡** |
| S5 → S6（报告+演示） | `review-checklist.md` | `review_passed_at` | — | **硬关卡** |
| S5 → S7（仅报告） | `review-checklist.md` | `review_passed_at` | — | **硬关卡** |
| S6 → S7（仅报告+演示） | `presentation-plan.md` | `presentation_ready_at` | — | **硬关卡** |
| S7 → done | `delivery-log.md` | `delivery_archived_at` | — | **硬关卡** |

**`done` 是真正的终态**——阶段推断必须投影出 `stage_code = "done"`（或用 `stage_status = "已完成"` 字段配合），UI 按钮消失、进度条 S7 段变为完成状态、workspace `status` 字段从"进行中"变为"已归档"。不能像旧版那样无论 `delivery_archived_at` 是否落下都返回 `S7`——那样"已完成"和"待归档"在 API 上不可区分，最后一道硬关卡等于没做。

### 5.1 S5 分流（报告 vs 报告+演示）

`review_passed_at` 落戳触发的下一步取决于 `plan/project-overview.md` 的"交付形式"字段：

1. 交付形式 = `报告+演示` → 进入 S6，右侧工作区下一个按钮显示"演示准备完成"
2. 交付形式 = `仅报告`（默认） → 跳过 S6 直接进入 S7，下一个按钮显示"归档，结束项目"

`/api/projects/{id}/workspace` 响应里返回 `next_stage_hint` 字段告知前端该显示哪个按钮。前端不应自己从 `delivery_mode` 推断，以免与后端不一致。

### 5.2 回退级联规则

清某个阶段的 checkpoint 戳时，必须**级联清除所有后续阶段戳**，避免出现"文件就绪但中间戳缺失"的孤儿状态。级联关系：

1. 清 `outline_confirmed_at` → 同时清 `review_started_at` / `review_passed_at` / `presentation_ready_at` / `delivery_archived_at`
2. 清 `review_started_at` → 同时清 `review_passed_at` / `presentation_ready_at` / `delivery_archived_at`
3. 清 `review_passed_at` → 同时清 `presentation_ready_at` / `delivery_archived_at`
4. 清 `presentation_ready_at` → 同时清 `delivery_archived_at`
5. 清 `delivery_archived_at` → 只清自己

级联清除不可配置，由 `_clear_stage_checkpoint_cascade` 统一实现；UI 和关键词回退都只调用这一个入口。

## 6. 持久化 checkpoints

### 6.1 存储文件

每个项目一个 `stage_checkpoints.json`，位于项目根目录（与 `conversation.json` / `conversation_state.json` 同级）：

```
<project_dir>/stage_checkpoints.json
```

### 6.2 文件结构

最小字段集：

1. `outline_confirmed_at`
2. `review_started_at`
3. `review_passed_at`
4. `presentation_ready_at`
5. `delivery_archived_at`

每个字段要么不存在，要么是 ISO8601 格式的时间戳字符串。不需要 `version` 字段（单一 schema 即可，未来演进用字段增量）。

### 6.3 写入规则

1. 只通过 5 个幂等 endpoint（`outline-confirmed` / `review-started` / `review-passed` / `presentation-ready` / `delivery-archived`）写入，不允许模型通过 `write_file` 直接写。
2. 重复点击同一个 checkpoint：保留**首次**时间戳，不覆盖。
3. 回退清戳：删除整个 key（**级联**清其后所有戳，见 §5.2），不置 `null`。
4. 写入/清除**必须在 project-level 锁下执行**（复用 `backend/chat.py:60` 的 `_PROJECT_REQUEST_LOCKS`），防止 UI 按钮和关键词并发触发时出现 `load → check → write` 的竞态覆盖。
5. 每次写入/清除成功后触发 `_infer_stage_state` 重算 + `_sync_stage_tracking_files`（回写 `stage-gates.md` / `progress.md` / `tasks.md`），这两步也在同一把锁下完成，保证 `stage_checkpoints.json` 与 stage tracking 文件始终一致。

### 6.4 旧项目迁移

旧项目（无 `stage_checkpoints.json`）按如下规则降级处理：

1. 如果 `stage-gates.md` 里推断当前阶段 ≥ S2，自动落一个 `outline_confirmed_at = "<迁移时的当前时间>"`，保证旧项目不会因为缺戳而被硬回退到 S1。
2. **`review_started_at` / `review_passed_at` / `presentation_ready_at` / `delivery_archived_at` 全部留空**，即便旧项目 `stage-gates.md` 显示处于 S5/S6/S7。旧项目体验是阶段暂时回退到 S4 让用户重新点 UI 按钮，这比默默以为审查已通过安全得多。详细理由见 §16.2。
3. 迁移只做一次，首次读取项目时触发。

## 7. 质量门槛缩放

### 7.1 公式

```python
N = min(12, math.ceil(expected_length / 1000 * 1.3))
M = min(8, math.ceil(expected_length / 1000 * 0.8))
```

### 7.2 映射表

| `expected_length` | N（data-log 来源条目） | M（analysis-notes 引用） |
|---|---|---|
| 1000 字 | 3 | 2 |
| 3000 字 | 4 | 3 |
| 6000 字 | 8 | 5 |
| 10000 字 | 12（上限） | 8（上限） |
| 20000 字 | 12 | 8 |

### 7.3 来源条目判定

`data-log.md` 中一条"有效来源条目"需要满足：

1. 以 `### [DL-xxx]` 开头，`xxx` 为任意可解析的标识符。
2. 条目内至少包含以下之一：
   - 一个 `http://` / `https://` URL
   - 一个 `material:<uuid>` 引用（项目附件材料）
   - 一个明确的访谈记录标识（以 `访谈：` 或 `调研：` 开头的一行文本）

### 7.4 反向引用判定

`analysis-notes.md` 中一条"有效反向引用"需要满足：

1. 文本中出现 `[DL-xxx]` 格式的引用，`xxx` 能在 `data-log.md` 里找到对应条目。
2. 同一个 `[DL-xxx]` 多次出现只算一次。

### 7.5 `expected_length` 解析

1. 从 `plan/project-overview.md` 读取"预期篇幅"字段。真实写入格式由 `backend/skill.py:_populate_v2_plan_files` 生成为 `**预期篇幅**: <值>`（单行，键与值同一行，冒号分隔）。
2. 解析规则：在行内匹配 `预期篇幅` 之后到行尾的所有数字，取最大值作为参照。接受 `6000` / `6000字` / `5000-8000字` / `约 5000 字` 等写法。
3. 无法解析时退化为 `3000`（温和默认值，避免极端门槛阻塞），且在 `/api/projects/{id}/workspace` 返回 `length_targets.fallback_used=true`。前端在 `WorkspacePanel` 顶部渲染一条可点击 chip："预期字数：3000（默认值，点击修改）"，点击后打开项目信息编辑面板（即复用新建项目的表单 UI 进入"编辑"模式，直接改"预期篇幅"字段并保存回 `project-overview.md`）。不要只显示静态灰字——用户根本不知道怎么进"项目信息"。
4. 正则建议采用单行匹配：`r"预期篇幅[^\n]*?[:：]\s*([^\n]+)"`，从捕获组里再 `re.findall(r"\d+", group)` 取最大整数，避免 `[^\n]*?` 跨行歧义。
5. **Heading-form 兜底**：`backend/skill.py:257-258` 存在另一条写入路径会生成 `## 预期篇幅\n<值>\n` 的 Markdown heading 格式（当原模板没有"预期篇幅"占位符时触发）。第 4 条的单行正则不会命中这种格式。解析函数需要追加第二条模式 `r"^##\s*预期篇幅\s*\n\s*([^\n]+)"`（带 `re.MULTILINE` 标志），两条模式**先单行 key-value、后 heading 形式**依次尝试，任一命中即解析。都不命中才走 `fallback_used=True` 的退化路径。

## 8. 关键词识别表

关键词识别按**阶段敏感**设计：同一短语（例如"行"/"可以"）根据当前阶段映射到不同的 checkpoint。避免"行"这类高频短语在任意阶段都触发所有戳。

### 8.1 关键词分组

分两组关键词：**强关键词**（明确意图、跨阶段通用）与**弱关键词**（口语化、阶段内歧义，需阶段敏感过滤）。

**强关键词**（无歧义，阶段敏感也兜底）：

| 目标戳 | 强关键词 |
|---|---|
| `outline_confirmed_at` | "确认大纲" / "大纲没问题" / "按这个大纲写" / "就这个大纲" / "就按这个版本" |
| `review_started_at` | "开始审查" / "进入审查" / "可以审查了" / "开始 review" |
| `review_passed_at` | "审查通过" / "审查没问题" / "报告可以交付" |
| `presentation_ready_at` | "演示准备好了" / "演示准备完成" / "PPT 完成" / "讲稿完成" |
| `delivery_archived_at` | "归档结束项目" / "项目交付完成" / "交付归档" |

**弱关键词**（依赖当前阶段）：

| 当前阶段 | 弱关键词命中后写入 |
|---|---|
| S1 | "行" / "可以" / "同意" / "没问题" / "OK"/"ok" / "好的" / "挺好的" → `outline_confirmed_at` |
| S4 | **不启用弱关键词** —— S4 是反复改写主战场，用户说"挺好继续写下一节" 是高频肯定对话，不应触发"结束撰写进入审查"硬关卡。S4 进入 S5 只通过强关键词（"开始审查"）或 UI 按钮。 |
| S5 | "行" / "可以" / "挺好" / "通过" / "没问题" → `review_passed_at` |
| S6（仅报告+演示） | "行" / "可以" / "OK"/"ok" → `presentation_ready_at` |
| S7 | "行" / "可以" / "归档吧" → `delivery_archived_at` |

弱关键词在自动推进阶段（S0/S2/S3）和 S4 都不生效；只在 S1/S5/S6/S7 这四个硬关卡等待用户点头的阶段激活。

### 8.2 回退关键词

回退关键词**偏口语化**，避开"撤回 / 清除"这类技术词：

| 目标动作 | 关键词 |
|---|---|
| 清 `outline_confirmed_at` | "大纲再改下" / "大纲还要调整" / "回去改大纲" / "先别写了，大纲有问题" |
| 清 `review_started_at` | "还要改报告" / "再改改报告" / "回到写作阶段" / "暂停审查" |
| 清 `review_passed_at` | "重新审查" / "再看看" / "审查没过" |
| 清 `presentation_ready_at` | "演示再改" / "讲稿还要调整" |
| 清 `delivery_archived_at` | "还没归档" / "撤回归档" |

### 8.3 疑问句识别

疑问句不触发任何 checkpoint。判定规则（需同时满足其一）：

1. 句末匹配正则 `r"(吗|么)[?？]?$"`（句末以"吗"/"么"结尾，后可跟可选问号）
2. 句末匹配 `r"[?？]$"`（句末以问号结尾）

**不能**用 `endswith("吗")` 一刀切——"开始写报告嘛"、"就按这个大纲写吗，我再想想" 这类句子其中一部分不是整句末尾，不应误判。正则锚定 `$` 行尾更安全。

### 8.4 命中处理

1. 关键词识别在 `_build_turn_context` 里执行，先于 `_should_allow_non_plan_write`。
2. 识别顺序：疑问句识别 → 回退强关键词 → 推进强关键词 → 回退弱关键词（阶段敏感）→ 推进弱关键词（阶段敏感）。回退优先于推进。
3. 命中推进关键词 → 调 `_save_stage_checkpoint` + 在 turn_context 里标记 `checkpoint_event = {"action": "set", "key": "..."}`，供后续回复模板使用"已记录：进入 S2"这类用户可见提示。
4. 命中回退关键词 → 调 `_clear_stage_checkpoint_cascade`（级联清除，见 §5.2）+ 回复告知"已回退到 S4"。
5. 同一条消息里多个关键词命中：回退胜出；同类（全推进或全回退）里按最靠后阶段胜出（例如"审查通过归档吧"取 `delivery_archived_at`，不是 `review_passed_at`）。实现不能用"命中第一个即 return"的 dict 顺序遍历——必须先收集所有命中再用 `_STAGE_RANK` 选最高阶段的那个 checkpoint。
6. `_detect_stage_keyword` 签名：`def _detect_stage_keyword(self, user_message: str, current_stage: str) -> tuple[str, str] | None`。当前阶段由 `skill_engine._infer_stage_state` 的最近一次结果传入，不在识别函数里重算。

## 9. UI 设计

### 9.1 阶段主按钮（上下文感知）

右侧 `WorkspacePanel` 顶部阶段面板下方永远只有一个主按钮（S4 除外，见 §9.2），按当前阶段变身：

| 阶段 | 按钮显示 | 可点状态 |
|---|---|---|
| S0 | **不显示** | — |
| S1 | "确认大纲，进入资料采集" | `outline.md` 存在且非空时可点 |
| S2 | **不显示** | 后端自动推进（见 §9.3 进度提示） |
| S3 | **不显示** | 后端自动推进（见 §9.3 进度提示） |
| S4 | **双按钮**，见 §9.2 | — |
| S5 | **双按钮**："审查通过，准备交付" + "回去再改" | 两个按钮对称显示，"回去再改" 清 `review_started_at` 并级联，让用户回到 S4 |
| S6 | "演示准备完成" | 仅"报告+演示"时出现 |
| S7 | "归档，结束项目" | 任何时候可点 |

### 9.2 S4 与 S5 的双按钮设计

S4（报告撰写）和 S5（质量审查）都需要两个按钮对称显示，体现"继续 vs 推进"这对等价选择，避免把"反悔 / 不通过"路径折进 `⋯` 菜单导致用户找不到。

#### 9.2.1 S4 双按钮

S4 是人机协作主战场，需要把"继续扩写"和"进入审查"视觉区分开，避免"disabled 按钮"诱导用户把"开始审查"当成"继续扩写"去催模型凑字数。

| 字数状态 | 主按钮（继续写） | 次按钮（进入审查） |
|---|---|---|
| 未达标（字数 < 目标 × 0.7） | "继续扩写"，**总是可点**，点击后在聊天框插入提示"请继续扩写正文" | **不显示** |
| 已达标 | "继续扩写"（保持，颜色变次要） | "完成撰写，开始审查"（主色，新出现） |

按钮下方灰字始终显示"当前 X 字 / 目标 Y 字"作为中性事实，**不作为 disabled 按钮的禁用理由**。

#### 9.2.2 S5 双按钮

S5 阶段用户约一半概率会发现报告还要改，所以"不通过"路径必须和"通过"路径并列，不进 `⋯` 菜单。

| 按钮位置 | 文案 | 行为 |
|---|---|---|
| 主按钮 | "审查通过，准备交付" | POST `/api/projects/{id}/checkpoints/review-passed`，根据 `next_stage_hint` 进 S6 或 S7 |
| 次按钮 | "回去再改" | POST `/api/projects/{id}/checkpoints/review-started?action=clear`，**级联清除** `review_passed_at` / `presentation_ready_at` / `delivery_archived_at`，回到 S4 |

两个按钮永远同时显示，无 disabled 态。"回去再改"点击时弹 §9.5 定义的 S5 回退对话框。

### 9.3 S2 / S3 静默阶段的进度提示

S2 / S3 不显示按钮，但需要让用户知道"现在在哪一步、卡在什么门槛上"：

1. `WorkspacePanel` 顶部阶段标签下方加一行内联计数器：
   - S2：`已收集有效来源 3 / 8 条`（来自 `_has_enough_data_log_sources` 的实时计数）
   - S3：`已完成证据引用 2 / 5 个`（来自 `_has_enough_analysis_refs`）
2. 后端在 `/api/projects/{id}/workspace` 返回里附加 `quality_progress` 字段：
   ```json
   {"stage_code": "S2", "quality_progress": {"label": "有效来源条目", "current": 3, "target": 8}}
   ```
3. 长时间停滞触发提示：如果最近 `30` 分钟没有新的 `data-log.md` / `analysis-notes.md` 写入事件，在阶段面板下方显示灰字中性提示（不暗示"卡住"或系统故障）：S2 显示"需要继续采集资料吗？可以粘贴链接或上传材料。"；S3 显示"需要进一步分析吗？可以让助手基于已有证据再拆一层。"。窗口设 `30` 分钟是为咨询场景常见的"用户思考 / 通话 / 离席"留出余地，避免把锅甩给助手。

### 9.4 回退入口（分级菜单）

主按钮右侧一个小的 `⋯` 图标，**只在 S2 以后的阶段显示**（S0 / S1 不显示，因为没有可回退的戳）。点击展开：

**一级选项**（阶段敏感，默认显示）：

| 当前阶段 | 一级回退选项 |
|---|---|
| S2 / S3 | "调整大纲"（**不清 outline 戳**，只是让助手重新修 `outline.md`；写作通道保持开启） |
| S4 | "回到继续改的状态"（等价于"不做任何事"，因为 S4 本就是自由改，用作心理提示）|
| S5 | **不显示** —— "回去再改" 已作为 S5 次按钮独立展示（§9.2.2），`⋯` 菜单一级留空 |
| S6 | "回到审查阶段"（清 `review_passed_at`，级联清后续） |
| S7 | "回到审查阶段"（清 `review_passed_at`，级联清后续） |

**高级回退**（二级折叠，默认不展开，需要用户主动点"`▸ 更多回退选项`"才显示；折叠控件用左侧三角 disclosure icon + 灰色文字，**不做成按钮样式**，避免诱导用户误点）：

| 选项 | 动作 |
|---|---|
| "完全重置大纲确认" | 清 `outline_confirmed_at` + 级联清所有后续戳；写作通道关闭 |
| "撤回归档" | 清 `delivery_archived_at` |

**设计意图**：一级菜单里**绝不**暴露"清 outline 戳"，因为咨询顾问点"调整大纲"的心智是"调一调"而不是"关掉我的写作通道"。真正需要重置时放二级菜单让用户明确意识到后果。

### 9.5 回退确认对话框

所有回退操作（包括一级的"回到撰写阶段"和二级的"完全重置大纲确认"）都弹确认对话框，**文案必须去技术化**，不出现 "S4"、"S5"、"plan 文件"、"content/report.md"、"checkpoint" 等开发词。

示例：

- 一级"回到撰写阶段继续改"对话框：
  ```
  确认回到撰写阶段继续改报告？
  你写好的正文内容不会被删除，只是重新打开修改通道。
  ```

- 二级"完全重置大纲确认"对话框：
  ```
  确认重置大纲确认？
  你写好的报告正文不会被删除，但暂时无法继续修改，
  直到重新确认新的大纲后才能继续写。
  ```

- S7 "撤回归档"对话框：
  ```
  确认撤回归档？
  所有文件都会保留，只是项目重新回到待归档状态。
  ```

- S5 "回去再改"对话框（S5 次按钮触发）：
  ```
  确认回去继续改报告？
  你写好的正文内容不会被删除，只是重新打开修改通道。
  ```

### 9.6 阶段变化的视觉提示

1. 阶段推进后 `WorkspacePanel` 顶部阶段标签刷新，按钮文案随之变化。
2. 不添加 toast / modal 打断。
3. 阶段面板用一条进度条横向显示阶段走位。"仅报告"项目里进度条**完全不渲染 S6 段**（S0-S5 然后直接到 S7，共 7 段），避免"一个灰的是啥"的困惑；"报告+演示"项目渲染全部 8 段。进度条每段鼠标悬停显示阶段名与完成状态。

## 10. 回退机制

### 10.1 回退的数据影响

回退只影响 `stage_checkpoints.json`，**绝不**：

1. 删除 plan 文件
2. 删除 content 文件
3. 删除 conversation.json / conversation_state.json
4. 修改 materials.json 与附件目录

### 10.2 "调整大纲" vs "重置大纲确认"

这是最容易被误理解的操作，分两个语义：

| 动作 | 是否清 `outline_confirmed_at` | 写作通道 | 适用场景 |
|---|---|---|---|
| **调整大纲** | 否 | 保持开启 | 用户发现大纲有小瑕疵，让助手改 `outline.md`，继续维持 S2-S4 写作 |
| **完全重置大纲确认** | 是（级联清所有后续戳） | 关闭 | 用户对整个研究方向不满意，要回到 S1 重做 |

前者只是在聊天里让助手重写 outline，不触及 checkpoint；后者才涉及后端 state 变更。

### 10.3 回退后的阶段推断（级联规则见 §5.2）

回退只清对应戳 + 级联清所有后续戳，然后立即重跑 `_infer_stage_state`。可能场景：

1. 清 `outline_confirmed_at`（级联清 4 个后续戳） → 阶段回到 `S1`
2. 清 `review_started_at`（级联清 3 个后续戳） → 阶段回到 `S4`
3. 清 `review_passed_at`（级联清 2 个后续戳） → 阶段回到 `S5`
4. 清 `presentation_ready_at`（级联清 `delivery_archived_at`） → 阶段回到 `S6`（仅报告+演示）
5. 清 `delivery_archived_at` → 阶段回到 `S7`

级联清除是防止"孤儿戳 → 文件就绪后自动跳回原阶段"的死循环（见 §16.6 风险）。

### 10.4 回退的幂等性与原子性

1. 同样的回退操作可以重复执行，结果一致。对话关键词和 UI 菜单点击走**同一个** `_clear_stage_checkpoint_cascade` 入口。
2. 级联清除与级联后的 `_sync_stage_tracking_files`（回写 `stage-gates.md` / `progress.md` / `tasks.md`）必须在同一把 project-level 锁下执行，避免两个并发回退请求交错导致 `stage_checkpoints.json` 和 `stage-gates.md` 不一致。
3. 锁复用 `backend/chat.py:60` 现有的 `_PROJECT_REQUEST_LOCKS`（见 §13.3）。

## 11. 模型行为约束

### 11.1 Prompt 级约束：被挡后必须告知

`SKILL.md` 写作约束新增一条：

> 当你调用的工具（`write_file` / `web_search` / `fetch_url`）返回 `status: error` 时，你必须在本轮的可见回复里告诉用户：
>
> 1. 哪个工具调用失败了
> 2. 失败的原因（error message 摘要）
> 3. 用户需要做什么才能让你继续（例如"请点击工作区的'确认大纲'按钮，或说'确认大纲开始写'"）
>
> **严禁**在工具被挡时把本应写入文件的内容直接贴到聊天窗口作为替代输出。

### 11.2 后端兜底：主动注入系统提示到流输出

Prompt 是软约束，模型配合概率不是 100%。原实测问题正是模型被挡后静默绕过——在新方案里必须保证不依赖模型配合也能让用户知道。

**实现方式**：`ChatHandler._execute_tool_call` 在 `write_file` / `fetch_url` 返回 `status: error` 时，除了把 error 返回给模型，**同时**往 `chat_stream` / `chat` 的 assistant 输出流里注入一条用户可见的系统标注消息：

```text
[系统提示] 助手尝试写入 content/report.md 被拦截。
原因：当前轮次还不能开始写正文。
请点击右侧工作区的"确认大纲"按钮，或在对话框里说"确认大纲开始写"。
```

**流输出格式**：
1. 在 `type: content` 的前后加一条 `type: system_notice` 事件，前端 `ChatPanel` 识别并以特殊样式渲染（灰色框 + 图标，明显区别于模型正文）。
2. 一轮对话里最多注入一次（按 `turn_context` 里 `system_notice_emitted` flag 控制），避免同一错误刷屏。
3. 注入点在工具 error 返回后立即触发，无需等模型完成本轮。

这条兜底是**硬保证**：即便模型忽略 §11.1 的 prompt 约束，用户也不会看到静默绕过。

### 11.3 阶段推进的显式提醒

`SKILL.md` 每个阶段描述末尾补一条"推进条件"：

```
### S4 报告撰写

...（原文）

**推进到 S5：** 报告字数 ≥ `expected_length × 0.7`，并且用户明确说"开始审查" / "可以审查了" / 类似短语，或者在右侧点击"完成撰写，开始审查"按钮。未满足时继续在 S4 内部改写。
```

### 11.4 "继续扩写"的短期阻断优先级

用户有时会发出"先别写正文"之类的**短期阻断**语义（见 `backend/chat.py:_is_non_plan_write_blocking_message`）。这种阻断与大纲通行证的优先级规则：

1. 本轮的 `_should_allow_non_plan_write` 检查顺序：
   - 先看最近一条用户消息是否命中 `_is_non_plan_write_blocking_message` → 命中则**本轮**返回 False
   - 再看 `outline_confirmed_at` 是否存在 → 存在则返回 True
   - 再走原有的关键词和历史审计兜底
2. 阻断**不清**`outline_confirmed_at` 戳，只影响本轮。下一轮恢复自由写作。
3. 这保证"先别写正文"是一次性的短期阻断，而不是永久打断写作通道。

## 12. 自签字段拦截

### 12.1 `review-checklist.md` 拦截

拦截前提：`review_passed_at` 戳**尚未**落下。戳已落视为用户已确认，本条不再生效（见 §12.3）。

write_file 落盘前扫描写入内容，命中以下任一规则就拒绝：

1. 出现 `审查人\s*[:：]\s*(咨询报告写作助手|AI|助手|Claude|GPT|模型|ChatGPT|gemini)` 其一（正则忽略空格，覆盖全角半角冒号）。
2. `审查结论[:：]` 或 `建议通过` 由模型单独写入，但 `review_started_at` 戳不存在。

拒绝时返回：

```
review-checklist.md 的"审查人"字段必须由真实用户签字。
请把字段留作 "审查人：[待用户确认]" 交由用户手动填写。
```

### 12.2 `delivery-log.md` 拦截

拦截前提：`delivery_archived_at` 戳**尚未**落下。

write_file 落盘前扫描写入内容，命中以下任一规则就拒绝：

1. `客户反馈` 区块被勾选（`- [x]`）但内容仅为 `(待记录)` / `（待记录）` / `待补充` / `暂无` / `(暂无)` / `（暂无）` 等占位词。正则建议 `r"-\s*\[x\][^\n]*客户反馈[^\n]*[(（]?(待记录|待补充|暂无)[)）]?"`，兼容全角半角括号。
2. `项目状态` / `交付状态` 行出现 `已完成` / `已交付` / `已归档` / `已结束` 等声明。

拒绝后告知模型"归档需要用户显式确认"。

### 12.3 拦截豁免：由 checkpoint 戳自动放行

不另设签字文件或 UI 签字按钮（避免留"死机关"）。直接用对应 checkpoint 戳作为放行信号：

1. `review-checklist.md` 的"审查人"字段检测只在 `review_passed_at` **尚未**落戳时生效。戳落下后视为用户已人工确认，不再拦截后续写入（典型情况：模型补写 `review.md` 或调整 checklist 文案）。
2. `delivery-log.md` 的"已归档/已交付"声明检测只在 `delivery_archived_at` **尚未**落戳时生效。戳落下后允许模型把状态写成"已归档"。
3. 这一规则确保每个拦截都有明确的解除路径（用户在 UI 上点对应按钮 → 戳落下 → 拦截自动解除），不需要额外的豁免机制。

## 13. 模块边界

### 13.1 `backend/skill.py`

职责：

1. `_infer_stage_state` 重写为"文件 + 戳 + 质量门槛"三件齐备推断，且必须投影出 `done` 终态（见 §5 "S7 → done"）。
2. 新增 `_load_stage_checkpoints` / `_save_stage_checkpoint` / `_clear_stage_checkpoint` / `_clear_stage_checkpoint_cascade`（全部下划线前缀，属内部实现）。
3. **公开服务方法** `record_stage_checkpoint(project_id: str, key: str, action: str) -> dict`：供 `backend/main.py` 调用。内部按 `action` 调 `_save_stage_checkpoint` 或 `_clear_stage_checkpoint_cascade`，成功后立刻同步 stage tracking 文件，并在 `_get_project_request_lock(project_id)` 之下原子执行。返回 `{"status": "ok", "key": ..., "timestamp"/"cleared": ...}`。这是 Web 层允许触达的**唯一**入口，禁止 main.py 直接调下划线方法。
4. 新增 `_has_enough_data_log_sources(project_path, min_count)` 和 `_has_enough_analysis_refs(project_path, data_log_path, min_refs)`，以及计数版本 `_count_valid_data_log_sources(project_path) -> int`、`_count_analysis_refs(project_path) -> int`（给 `quality_progress` 用）。
5. 新增 `_resolve_length_targets(project_path)` 解析 `expected_length` 并算 N / M 阈值。
6. 新增 `_last_evidence_write_at(project_path) -> datetime | None` 返回 `data-log.md` / `analysis-notes.md` / `notes.md` / `references.md` 四个文件 mtime 的最大值，用于 S2/S3 停滞提示。
7. `_has_effective_report_draft` 追加字数检查（剥 Markdown 后计字数），候选路径用现有类常量 `REPORT_DRAFT_CANDIDATES`，不再硬编码新列表。
8. 新增旧项目迁移函数 `_backfill_stage_checkpoints_if_missing`（只补 `outline_confirmed_at`，不推断后续戳）。

### 13.2 `backend/chat.py`

职责：

1. `_build_turn_context` 在组装上下文前先识别阶段关键词，写/清对应 checkpoint 戳。全部写入必须在 project-level 锁下执行。
2. `_should_allow_non_plan_write` 增加一条通路：`outline_confirmed_at` 存在时返回 True；但短期阻断消息（`_is_non_plan_write_blocking_message`）先胜出，不清戳（见 §11.4）。
3. `write_file` 工具实现里新增自签字段拦截（调用 `skill_engine.validate_self_signature`）；被拦截时同步调 `_emit_system_notice_once` 插入可见系统提示。
4. `SKILL.md` 注入的提示词补上"被挡必须告知"的约束；但约束作为软保障，硬保障来自 §11.2 的 `system_notice` 注入。
5. **新增 module-level 函数** `_get_project_request_lock(project_id: str) -> threading.RLock`。现有 `ChatHandler._get_project_request_lock`（`backend/chat.py:710` 实例方法）保持不变，但其实现要委托给新的模块级函数（避免重复逻辑）。模块级函数直接操作既有的 `_PROJECT_REQUEST_LOCKS` / `_PROJECT_REQUEST_LOCKS_GUARD` 全局变量，供 `backend/main.py` 的 checkpoint endpoints 导入复用。
6. `chat` / `chat_stream` 主循环里引入 `pending_system_notices` 队列消费点，保证 `_emit_system_notice_once` 注入的提示会以 `type: "system_notice"` 事件真正流出到前端（见 §11.2 与 plan Task 5）。

### 13.3 `backend/main.py`

职责：

1. 新增 5 个 checkpoint endpoint：
   - `POST /api/projects/{id}/checkpoints/outline-confirmed`
   - `POST /api/projects/{id}/checkpoints/review-started`
   - `POST /api/projects/{id}/checkpoints/review-passed`
   - `POST /api/projects/{id}/checkpoints/presentation-ready`
   - `POST /api/projects/{id}/checkpoints/delivery-archived`
2. 每个 endpoint 都支持 `?action=set|clear` 参数（默认 `set`），用于写戳和清戳。
3. 幂等：重复 set 保留首次时间戳，重复 clear 静默通过。
4. **所有 endpoint 只调用 `SkillEngine` 公开服务方法** `record_stage_checkpoint(project_id, key, action)`（见 §13.1）。不允许 main.py 直接调用以下划线开头的私有方法（`_save_stage_checkpoint` / `_clear_stage_checkpoint_cascade` / `_sync_stage_tracking_files`）——这违反模块边界，Web 层不应依赖 engine 内部实现。公开方法内部原子地执行：加锁 → set 或 cascade clear → 回写 stage tracking 文件 → 释放锁。

### 13.4 `frontend/src/components/WorkspacePanel.jsx`

职责：

1. 根据当前阶段渲染单按钮主入口。
2. 按钮右侧的 `⋯` 菜单渲染回退选项。
3. 点击按钮时调对应 endpoint，刷新 workspace 状态。
4. S4 阶段的字数真值以 workspace 响应的 `length_targets.report_word_floor` 与后端字数计数为准；前端只读 workspace 返回的 `flags.report_ready`/`word_count` 决定"开始审查"按钮是否出现（§9.2.1），不在前端重复计字。避免双处字数算法漂移。

### 13.5 `skill/SKILL.md`

职责：

1. 每个阶段描述追加"推进条件"说明。
2. 新增"工具错误处理"章节，定义模型被挡后的必须动作。

## 14. 向后兼容

必须保证：

1. 旧项目（无 `stage_checkpoints.json`）首次加载自动迁移，不回退到 S1。
2. 旧项目 `review-checklist.md` 里已有的 `审查人：咨询报告写作助手` 字段不触发拦截（拦截只作用于新的写入动作，不回扫已有内容）。
3. `web_search` / `fetch_url` / `write_file` 工具签名不变。
4. `/api/workspace` 返回结构在原基础上追加 `checkpoints` 字段，前端按可选字段处理，老前端不受影响。
5. `_sync_stage_tracking_files` 回写 `stage-gates.md` / `progress.md` / `tasks.md` 的格式不变。

## 15. 测试策略

### 15.1 `backend/skill.py` 新增单测

至少覆盖：

1. `_load_stage_checkpoints` 的文件缺失、格式错误、正常读取三种路径
2. `_save_stage_checkpoints` 的幂等写入（重复 set 保留首戳）
3. `_clear_stage_checkpoint` 的清戳行为
4. `_resolve_length_targets` 对各种 `expected_length` 写法的解析
5. `_has_enough_data_log_sources` 对有/无来源的条目识别
6. `_has_enough_analysis_refs` 对 `[DL-xxx]` 反向引用的计数和去重
7. 新 `_infer_stage_state` 在"文件有但戳无"时停在前一阶段
8. 新 `_infer_stage_state` 在"字数不足"时停在 S4
9. 旧项目迁移正确补戳

### 15.2 `backend/chat.py` 新增单测

至少覆盖：

1. 关键词识别："确认大纲" → 写 `outline_confirmed_at`
2. 关键词识别："撤回大纲确认" → 清 `outline_confirmed_at`
3. 回退关键词优先于推进关键词
4. `_should_allow_non_plan_write` 在 `outline_confirmed_at` 存在时返回 True
5. `write_file` 拦截 `审查人：咨询报告写作助手` 并返回明确错误
6. `write_file` 拦截"客户反馈勾选但内容占位"的 delivery-log
7. 模型被挡后的 assistant 消息里包含错误原因（通过 e2e mock 验证）

### 15.3 `backend/main.py` 新增单测

至少覆盖：

1. 5 个 checkpoint endpoint 的 `set` / `clear` 幂等性
2. 不存在的 project_id 返回 `404`
3. 权限边界（checkpoint endpoint 不受 chat rate limit 影响）
4. `/api/workspace` 返回包含 `checkpoints` / `length_targets` / `flags` / `word_count` / `stalled_since` 字段

### 15.4 前端新增单测（Node `node:test`）

至少覆盖：

1. `WorkspacePanel` 在不同阶段渲染不同按钮文案
2. S4 字数未达标时不显示"开始审查"次按钮（非 disabled 状态）
3. S4 字数达标时"开始审查"次按钮出现
4. `⋯` 菜单展开/收起行为（含一级 / 二级折叠切换）
5. 点击回退触发确认对话框
6. `stage_code === "done"` 时主按钮消失、显示"项目已归档"横幅
7. S2/S3 阶段 `stalled_since` 存在时渲染中性停滞提示，文案不含"卡住"/"异常"

### 15.5 回归测试

1. `tests/smoke_packaged_app.py` 追加 checkpoint 相关校验
2. 现有 `test_chat_runtime.py` / `test_skill_engine.py` 对阶段推断的断言需按新规则更新
3. `test_packaging_docs.py` 不受影响

## 16. 风险与规避

### 16.1 关键词识别误触发

风险：

1. 用户说"我还没想好大纲，随便写一个"里的"大纲"可能被误判。
2. 用户说"这份大纲 ok 吗"是疑问句不是确认。

规避：

1. 关键词匹配用**短语级 + 否定前缀抑制**的组合策略：短语采用完整短语子串匹配（例如 `"确认大纲"` 而不是 `"大纲"`），加上紧邻短语前 6 字符内的否定词检测（`不要` / `别` / `没` / `不是` / `不`）。命中否定前缀时整句作废，避免"先不要开始审查" / "不是说审查通过了吗" 误触发。中文没有 word boundary，这是实用下界；不追求完整的语义解析器。
2. 疑问句识别：句末匹配 `(吗|么)[?？]?$` 或以 `?` / `？` 结尾时不触发推进；**不能**用 `endswith("吗")` 因为"开始写报告嘛"会被误判。
3. 同一条消息里多个关键词命中按"回退优先；同类取最高阶段"裁决（详见 §8.4 第 5 条）。
4. UI 按钮作为主通道，关键词只是辅助。

### 16.2 旧项目迁移误推

风险：

1. 迁移脚本把处于 S5 但实际没完成审查的项目误落 `review_passed_at` 戳。
2. `_has_effective_review_checklist` 只看文件存在性，旧项目 review-checklist 是模型自签的，文件存在不代表用户真审过。

规避（**保守迁移策略**）：

1. 迁移**只补 `outline_confirmed_at`**——判据是 `stage-gates.md` 推断的阶段 ≥ S2。依据：旧项目能写到 S2+ 说明用户通过对话含蓄确认过大纲，补这个戳不会造成实际损失（最坏情况是重新写一遍 S2-S4 的通道）。
2. **不补** `review_started_at` / `review_passed_at` / `presentation_ready_at` / `delivery_archived_at`。旧项目即便处于 S5/S6/S7，这些戳也留空，强制用户在 UI 上重新点击"开始审查 / 审查通过 / 演示完成 / 归档"。旧项目体验是"阶段暂时回退到 S4，让你重新点推进按钮"——这比"默默以为审查已通过"安全得多。
3. 迁移幂等：`stage_checkpoints.json` 存在（即便是空对象 `{}`）就不再触发迁移，避免每次加载都跑一次。迁移完成时写入 `{"__migrated_at": "<时间戳>"}` 作为已迁移标记（`__migrated_at` 不在 `STAGE_CHECKPOINT_KEYS` 里，不影响阶段推断）。
4. 迁移触发时机：`SkillEngine.get_workspace_summary` 的最开头，优先于 `_infer_stage_state`。
5. 迁移前**不备份** `stage_checkpoints.json`——新项目本来就没有这个文件，旧项目也不存在既有文件可覆盖，所以备份没有意义（原先 spec 中提到的 `.pre-migration.bak` 是错误设计，本次删除）。

### 16.3 质量门槛过高

风险：

1. 用户真正想要的报告长度很短（例如一页纸），但 N/M 算出来过高挡住推进。

规避：

1. N / M 按 `expected_length` 缩放。
2. 下限也要保证：1000 字报告 N=3 / M=2 已经很低。
3. 提供 UI 回退作为兜底通道。

### 16.4 自签拦截过严

风险：

1. 用户手工修改 `review-checklist.md` 的内容触发拦截。

规避：

1. 拦截只作用于 `write_file` 工具（模型调用），不作用于用户通过 UI/文件系统的直接编辑。
2. 拦截由对应 checkpoint 戳（`review_passed_at` / `delivery_archived_at`）落下时**自动解除**（见 §12.3）。不再设"签字文件"这类手动豁免机关——每个拦截都有明确的解除路径（用户在 UI 点对应按钮）即可。

### 16.5 UI 按钮误点

风险：

1. 用户手滑点错"归档，结束项目"。

规避：

1. 所有硬关卡按钮点击后弹二次确认对话框。
2. 对话框文案明确告知"可随时回退，文件不会被删除"。
3. 回退通过 `⋯` 菜单一键恢复。

## 17. 结论

本次最合适的方案是把阶段推进从"文件存在性投影"升级为"文件 + 戳 + 质量门槛"三件齐备，主轴有三：

1. 一张大纲确认戳 = 整段 S2 / S3 / S4 通行证，解决用户"改报告不要每次都确认"的痛点。
2. 三张终局硬关卡（S4→S5 / S5→S6-S7 / S6-S7→完成）+ UI 单按钮上下文感知，解决"后期阶段必须用户点头"的产品意图。
3. 质量门槛缩放 + 模型被挡必须告知 + 自签字段拦截，解决"模型偷懒 + 自娱自乐 + 静默绕过"的实测病灶。

这个方案最符合当前用户的产品意图，也符合奥卡姆剃刀：只引入一个新的持久化文件 `stage_checkpoints.json`，不重构现有阶段文件投影层，不动搜索池、管理通道、打包产物。
