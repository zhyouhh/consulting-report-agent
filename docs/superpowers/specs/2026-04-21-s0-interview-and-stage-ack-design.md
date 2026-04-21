# 2026-04-21 — S0 预访谈 + stage-ack 信号机制设计

## Context

二轮 smoke test（见 `docs/current-worklist.md` §1b）暴露两处链条性问题：

1. **Bug 1**：新建项目填完表单即 UI 错误把 S0 checklist 全部打钩（"需求访谈完成 / 范围界定明确 / project-overview.md 创建 / 交付形式确认"）。根因是 `backend/skill.py:1257` `stage_zero_complete = project_overview_ready`——只要表单生成的 `plan/project-overview.md` 通过 `_is_effective_plan_file`，就当成 S0 全过。当前模型在首轮直接写 outline/research-plan，需求访谈根本没发生。

2. **Bug 3**：用户口头"确认"无法推进阶段。根因是 `backend/chat.py` 的弱关键词表 `_WEAK_ADVANCE_BY_STAGE["S1"]` 只认 `["行","可以","同意","没问题","OK","ok","好的","挺好的"]`，没有"确认"。

两 bug 同源：阶段推进信号识别层级错位——后端用正则硬猜 LLM 本来能判的意图，列表永远覆盖不全，加词越多误伤越多（第一性：意图识别应交给回路内的 LLM）。

本 spec 用**一体化方案**解决两事：
- 新增 S0 预访谈门槛 + 新 checkpoint `s0_interview_done_at`
- 6 个 checkpoint 的 set/clear 统一走「LLM 发 XML tag」为主通道，关键词表退化为强短语兜底

## Goals

1. 新建项目后，模型**必发**一轮 3-5 问的打包澄清问答，用户一次性回答（或主动跳过）后方可推进 S1
2. 6 个 stage checkpoint 统一「tag + 按钮 + 强关键词兜底」三路径架构
3. 删除弱关键词表，瘦身强关键词表（补 S0 条目）
4. 不加新前端 UI 元素（"最少打扰"原则）
5. 老项目升级后 `_backfill_stage_checkpoints_if_missing` 自动补 `s0_interview_done_at`，避免卡 S0

## Non-Goals

- 不引入 tool call / function-calling（XML tag 已够，Gemini-3-flash 稳定性更好）
- 不加"撤销 toast"（工作区右侧状态栏已经在刷新）
- 不改新建项目表单字段（worklist #3 并行项，不在本 spec 范围）
- 不设"N 轮问答硬门槛"（一轮必发，不管用户是否"充分"回答）

## Design

### 1. S0 预访谈流程

```
用户填表单 提交
   ↓
后端 create_project 写 plan/project-overview.md seed 版（含表单字段 + 空位）
   ↓
模型第 1 回合（收到前端拼的欢迎消息 + 读 seed）
   ✓ 必须：基于 seed 做一轮 3-5 个追问（打包一条消息），维度按 SKILL.md §S0 建议清单挑
   ✗ 禁止：直接写 plan/outline.md / plan/research-plan.md / plan/data-log.md / plan/analysis-notes.md（四个正式产出文件）
   ↓
用户（一次性回答 · 或说"跳过/不用问了/直接开始"）
   ↓
模型第 2 回合
   · 若有答 → merge 进 plan/project-overview.md（追加澄清段落）
   · 若跳过 → overview 保持 seed
   · 回复尾输出 <stage-ack>s0_interview_done_at</stage-ack>
   ↓
后端扫到 tag → 前置校验（s0 无硬闸，seed 一定在）
         → set s0_interview_done_at
         → 从 content 剥 tag 再返前端
   ↓
UI "需求访谈完成"打钩 → 进 S1
```

**防违规三层**（防模型跳追问直接写 outline）：

1. `skill/SKILL.md §S0` 硬规定"首轮必发追问，禁止写 outline/research-plan/data-log/analysis-notes"
2. `backend/chat.py` S0 阶段拦下上述四个文件（outline / research-plan / data-log / analysis-notes）的 `write_file`，注入 `system_notice`：`"S0 阶段：请先对 seed 做一轮澄清，再写大纲/研究计划/资料清单/分析笔记"`
3. `s0_interview_done_at` 前置校验：无硬闸（project-overview.md 一定存在，再校验无意义）

### 2. stage-ack tag 语法

**Set**：`<stage-ack>KEY</stage-ack>`
**Clear**：`<stage-ack action="clear">KEY</stage-ack>`

**合法 KEY 白名单（6 个）**：
- `s0_interview_done_at`（新）
- `outline_confirmed_at`
- `review_started_at`
- `review_passed_at`
- `presentation_ready_at`
- `delivery_archived_at`

**扫描 & 防注入**：
- 扫描正则：`<stage-ack(?:\s+action="(set|clear)")?>([a-z_0-9]+)</stage-ack>`（正则与 Appendix A 完全一致）
- 只扫**当前回合** assistant 完整输出 content，不追溯历史 assistant 消息
- User 消息里写 tag 当纯文本忽略（`StageAckParser` 只接 assistant role 的输入）
- **可执行 tag 的位置约束（关键防护）**：
  - 必须在回复**尾部**——tag block 出现在"最后一个非空正文段"之后
  - 必须**独立一行**——整行仅包含 `<stage-ack>…</stage-ack>`（前后允许空白字符）
  - 必须在 **Markdown 围栏代码块外**（`` ``` `` / `~~~`）、inline code（反引号对）外、blockquote（行首 `>`）外
  - 不满足任一条件的 tag → 识别但**不执行**，仍从 content 剥离（防 tag 漏出前端），并记录 warning
- 未指定 `action` 默认 `set`
- KEY 不在白名单 → 忽略 + 日志 warning，**不**默认发 `system_notice`（避免模型乱发 tag 时炸聊天区；system_notice 保留给阻塞性的前置校验失败）
- 同回合多 tag：**不做去重**，合法事件按出现顺序逐条执行；重复的 set/clear 由 `record_stage_checkpoint` 幂等语义吞掉。允许 `set X; clear X; set X` 等全排列顺序语义
- 事件模型建议：`StageAckEvent{raw, action, key, start, end, executable, ignored_reason}`，parser 单测能覆盖"识别但不执行"场景

**剥离（含流式策略 — 关键）**：
- 后端识别并处理 tag 后，将 `<stage-ack>…</stage-ack>` 从 content 中剔除，剥干净的 content 再写入 `conversation.json`、`conversation_state.json`，并参与 post-turn compaction
- **前端永远看不到 tag**——包括流式中间态。实现采用 **tail guard** 策略（非固定窗口）：
  - `chat_stream` 维护"未 flush 尾部"。每次 append delta 后扫描尾部：若尾部含 `<stage-ack` 的**任意前缀子串**（`<` / `<s` / `<st` / … / `<stage-ack`），**从该前缀位置起全部字节暂停 flush**；前面的安全字节才 flush 给前端
  - 流关闭时对完整 content 做最终 `parse` + `strip`，然后一次性 flush 剩余**已剥干净**的尾部字节
  - **不使用固定字节窗口**——同回合多 tag 尾部块（如 `set X; clear X; set X` 三个合法 tag + 空行）整体可能超过数百字节，固定窗口会让较早的 `<stage-ack` 前缀滑出窗口被泄漏
- 已持久化的老 conversation.json 如残留未剥 tag（历史数据迁移场景），在 `_load_conversation` 读取路径做 sanitize，保证后续 prompt 不再把真实 tag 喂回模型

**前置校验失败时**：
- `set`：tag 从 content 剥掉，checkpoint 不落戳，发 `system_notice` 告诉模型"阶段文件未就绪，先补齐 {path}"
- `clear`：对合法 key 始终幂等执行（clear 本身无前置校验，`_clear_stage_checkpoint_cascade` 幂等），tag 剥掉

### 3. 关键词表重构

**删**：`_WEAK_ADVANCE_BY_STAGE` 整表删除。弱词误伤高、覆盖永远不全，是 Bug 3 的直接成因。

**留并补**：`_STRONG_ADVANCE_KEYWORDS` 保留现有 5 条 entry，新增 `s0_interview_done_at` 条目：

```python
"s0_interview_done_at": [
    "跳过访谈",
    "不用问了",
    "先写大纲吧",
    "够了开始吧",
    "直接开始",
]
```

挑的短语刻意长且具体，避免"确认""开始"这类高频误伤词。Bug 3 原生的"确认"问题由 tag 主通道解决，strong list 不收纳它。

**留不变**：`_ROLLBACK_KEYWORDS` 原样保留，作为 `<stage-ack action="clear">` 的兜底。

**匹配优先级（新）**：
1. **可执行** tag（通过位置约束）存在 → tag 赢。若 tag 被降级为"识别但不执行"（如在 code fence 内 / 非尾部），语义上相当于无 tag，应让 strong 关键词兜底照常生效
2. 无 tag + strong 关键词命中 + 前置校验通过 → 按关键词推进
3. 无 tag + 无关键词 → 不自动推进，用户走前端按钮

**S0 访谈强关键词的软门槛（防首轮绕过）**：
- `s0_interview_done_at` 的 strong 关键词（"跳过访谈 / 不用问了 / 先写大纲吧 / 够了开始吧 / 直接开始"）**只在项目已有至少一条 assistant S0 预访谈提问后才生效**
- 判据（实现时命名 `has_prior_s0_assistant_turn`）：`conversation.json` 里存在至少一条 `role == "assistant"` 消息（前端拼接的欢迎消息是 user role，不算；tool role 也不算）。spec 不要求严格验证该 assistant 消息是否"的确是 S0 提问"——配合 §6 SKILL.md 硬规定（S0 阶段首轮 assistant 只能发追问），实际首条 assistant 一定是提问
- 用户第一条消息就说"直接开始" → 关键词**不**触发 set；tag 同理——模型首轮误发 `<stage-ack>s0_interview_done_at</stage-ack>` 也被拒（同一门槛判据），剥掉 + system_notice 提醒"S0 阶段请先打包追问"
- 迁移路径例外：backfill 逻辑不受此门槛限制（backfill 走独立代码路径，不经过 strong 关键词或 tag 通道）

### 4. 六 checkpoint × 三路径矩阵

| Checkpoint | 主通道 tag | 前端按钮 | 强关键词兜底 |
|---|---|---|---|
| `s0_interview_done_at`（新） | `<stage-ack>s0_interview_done_at</stage-ack>` | 暂无专用按钮 | "跳过访谈 / 不用问了 / 先写大纲吧 / 够了开始吧 / 直接开始" |
| `outline_confirmed_at` | 同格式 | `StageAdvanceControl` S1「确认大纲，进入资料采集」 | "确认大纲 / 大纲没问题 / 按这个大纲写 / 就这个大纲 / 就按这个版本" |
| `review_started_at` | 同格式 | S4「完成撰写，开始审查」 | "开始审查 / 进入审查 / 可以审查了 / 开始 review" |
| `review_passed_at` | 同格式 | S5「审查通过，准备交付」 | "审查通过 / 审查没问题 / 报告可以交付" |
| `presentation_ready_at` | 同格式 | S6「演示准备完成」 | "演示准备好了 / 演示准备完成 / PPT 完成 / 讲稿完成" |
| `delivery_archived_at` | 同格式 | S7「归档，结束项目」 | "归档结束项目 / 项目交付完成 / 交付归档" |

Clear（回退）方向三路径完全对称：tag `action="clear"` / `RollbackMenu` UI / `_ROLLBACK_KEYWORDS`。

### 5. 前置校验与硬闸

复用现有 `_validate_stage_checkpoint_prereq(project_path, key)`，对 `s0_interview_done_at` 不设**文件级** validator，但有**对话级软门槛**：

| Key | 文件级 validator | 对话级软门槛 |
|---|---|---|
| `s0_interview_done_at` | 无 | set 必须发生在 assistant 已发出一条 S0 预访谈提问后（见 §3）；迁移 backfill 走独立路径，不受此门槛 |
| `outline_confirmed_at` | `_has_effective_outline` | — |
| `review_started_at` | `_has_effective_report_draft` | — |
| `review_passed_at` | `_has_effective_review_checklist` | — |
| `presentation_ready_at` | `_has_effective_presentation_plan` | — |
| `delivery_archived_at` | `_has_effective_delivery_log` | — |

对应 `CHECKPOINT_PREREQ` dict 加 `s0_interview_done_at: None` entry（无文件级闸）；`get_stage_checkpoint_prereq_notice` 对 `None` 返回 `None`。对话级软门槛在 `StageAckParser` / strong 关键词匹配路径里独立实现，不进 `CHECKPOINT_PREREQ` 表。

**s0 不允许通过 checkpoint endpoint 直接 set**（与 §7 `backend/main.py` 行为一致）：checkpoint endpoint 无对话上下文，无法执行 §3 对话级软门槛，因此 `POST /api/projects/{id}/checkpoints/s0-interview-done?action=set` 返回 **400 Bad Request**；`s0_interview_done_at` 的 set 只能走 StageAckParser / strong 关键词软门槛 / schema migration 三条路径。

**clear 语义澄清**：clear 对合法 key 始终幂等成功（无任何前置校验），`_clear_stage_checkpoint_cascade` 会级联清除下游 checkpoint。所以前置校验仅针对 `set`。

### 6. SKILL.md 规则调整

**§S0 项目启动 → 启动门禁 → 前置位置**（大改，位置重要）：

SKILL.md 原「启动门禁」段后紧接插入强制子节。对 Gemini-3-flash 这种小模型，放附录末尾会被忽略——必须前置且用硬格式。建议文案如下（实施时微调，保持强度）：

```md
### S0 预访谈（强制）

当前阶段是 S0 且本项目 `stage_checkpoints.json` 还没有 `s0_interview_done_at` 时：

1. 你的**第一轮回复**只能做一件事：基于 `plan/project-overview.md` 提出 3-5 个打包的澄清问题（一条消息内全发完）。
2. 第一轮**禁止**：
   - 调用 `write_file` 写入 `plan/outline.md`、`plan/research-plan.md`、`plan/data-log.md`、`plan/analysis-notes.md`
   - 输出 `<stage-ack>s0_interview_done_at</stage-ack>`
3. 用户回答问题后，或用户明确说"跳过访谈 / 不用问了 / 直接开始"后，才可以更新 `plan/project-overview.md`；用户跳过就沿用 seed 不改。
4. 完成上述处理后，在回复**最后单独一行**输出：

`<stage-ack>s0_interview_done_at</stage-ack>`

不要解释这个 tag。不要把 tag 放进代码块、列表、引用、正文中间——放错位置会被系统剥掉但不执行。

### S0 追问维度建议清单

从以下 6 条里选 3-5 条，内容按 seed 自由改写：
- 决策场景（这份报告将拿去做什么决定？）
- 读者深度（读者对主题的既有了解？）
- 期望核心发现（最想在报告里看到的 1-2 个洞察）
- 时间/资源约束（除截止日外是否有其他约束）
- 已有假设（心中已经有哪些预判想验证或推翻）
- 关键风险与盲区（最担心报告漏掉什么）
```

**§S1–S7 各节「推进到 Sx」段**（小改）：
- 移除弱关键词表的相关说明（"挺好继续写""这段可以"等）
- 统一表述：「用户明确确认推进时，你在回复**最后单独一行**输出 `<stage-ack>KEY</stage-ack>`；用户点前端按钮或说强关键词（见附录）也能推进」
- rollback 对称：用户明确要回退时输出 `<stage-ack action="clear">KEY</stage-ack>`

**新增附录「stage-ack 标签规范」**：
- 完整 6 个合法 KEY 列表
- set / clear 语法
- 用法规则：
  - 只在用户明确表达推进/回退意图时发
  - 不要每条消息都发
  - 不要发未列出的 KEY
  - tag **必须放在回复最后、单独一行、代码块外**（非尾部/非独立行/代码块内会被识别但不执行）
  - **正文中需要展示 XML 示例时必须使用转义文本**（比如 `\<stage-ack\>...\</stage-ack\>`）；**即使在 code fence / inline code / blockquote 内也不要输出真实 `<stage-ack>` 标签**——真实 tag 不管放哪里都会被 parser 识别并剥离（code fence 不是安全区）
- 附录强关键词短语表（给模型参考用户习惯说法）

### 7. 代码变更清单

**`backend/chat.py`**：
- 新增 `StageAckParser` 类：
  - `parse(content) -> list[StageAckEvent]`：正则匹配（见附录 A）+ 位置判定（尾部/独立行/代码块外）+ 白名单校验；返回按出现顺序排列事件列表，不做去重
  - `StageAckEvent` 字段：`raw, action, key, start, end, executable, ignored_reason`
  - `strip(content) -> str`：无论 executable 与否都剥掉
- 新增**流式 tail guard**：`_chat_stream` 维护未 flush 尾部 buffer。每次 append delta 后扫描尾部是否含 `<stage-ack` 任意前缀子串——含则**暂停**从该前缀位起的字节 flush；不含则 flush 安全字节。流关闭时对最终完整 content 做 `parse + strip`，一次性 flush 剩余已剥干净的尾部。**不用固定字节窗口**（多 tag 尾部块可能超过数百字节，固定窗口会让较早 `<stage-ack` 前缀泄漏）
- 流关闭后（或非流式完整响应后）：
  1. `StageAckParser.parse(full_content)` 收集全部合法事件
  2. 对每个 executable 事件按顺序调 `record_stage_checkpoint(key, action)`，对 S0 事件先过**对话软门槛**（assistant 已发 S0 提问？）
  3. 校验失败 → 发 `system_notice`（只对 set 失败，clear 始终幂等）
  4. `StageAckParser.strip(full_content)` → 剥后写 `conversation.json` / `conversation_state.json` / 参与 compaction
- `_load_conversation` 读取路径加 sanitize：把历史残留的 `<stage-ack>…</stage-ack>` 剥掉再喂回模型（防老 session 污染 prompt）
- 删除 `_WEAK_ADVANCE_BY_STAGE` 字典 + `_detect_stage_keyword` 里的弱词匹配分支；用 `_detect_stage_keyword` 处理强关键词时，S0 走**对话软门槛**
- `_STRONG_ADVANCE_KEYWORDS` 加 `s0_interview_done_at` entry
- S0 阶段 `write_file` 门禁：当 `stage_code == "S0"` 且要写 `plan/outline.md` / `plan/research-plan.md` / `plan/data-log.md` / `plan/analysis-notes.md` 时，返回 error + 注入 `system_notice`（文案："S0 阶段：请先对 seed 做一轮澄清，再写大纲/研究计划/资料清单/分析笔记"）
- **`_should_allow_non_plan_write` 补漏洞**（codex review #6）：当 `stage_code ∈ {"S0", "S1"}` 且 `outline_confirmed_at` 未 set 时，`NON_PLAN_WRITE_ALLOW_KEYWORDS`（含"开始写 / 开始写报告 / 开始正文 / 写正文"等）**不得**打开非 plan 写入。否则用户说"开始写"会同时触发 S0 set + 非 plan 写入授权，绕过 S1 大纲确认。

**`backend/main.py`**：
- `_CHECKPOINT_ROUTES` 新增 `"s0-interview-done": "s0_interview_done_at"`；**该路由仅支持 `action=clear`**（前端 RollbackMenu 调用）
- `POST /api/projects/{id}/checkpoints/s0-interview-done?action=set` **必须返回 `400 Bad Request`**。原因：endpoint 层没有对话上下文，无法执行 §3 的对话级软门槛；`s0_interview_done_at` 的 set 只能走以下三条路径：StageAckParser（流里 tag）/ `_detect_stage_keyword` strong 关键词通道（带对话门槛）/ `_backfill_stage_checkpoints_if_missing` schema migration
- 其他 5 个 checkpoint route 的 set/clear 行为不变
- 对应补 endpoint 测试（`tests/test_main_api.py`）：`s0 clear route reachable returns 200`；**`s0 set route returns 400 Bad Request`**；`clear idempotent when s0 not set returns 200`

**`backend/skill.py`**：
- `STAGE_CHECKPOINT_KEYS` 加 `s0_interview_done_at`
- `_CASCADE_ORDER` 把 `s0_interview_done_at` 放**第一位**（rollback 时级联清空下游）
- `CHECKPOINT_PREREQ` 加 `s0_interview_done_at: None`
- `_infer_stage_state`：`stage_zero_complete` 从 `project_overview_ready` 改成 `"s0_interview_done_at" in checkpoints`
- `_build_completed_items` 的 S0 分支保持现状（只亮 `STAGE_CHECKLIST_ITEMS["S0"][2]` = "project-overview.md 创建"）；当 stage 推进到 S1 后，现有 `STAGE_ORDER[:stage_index]` 主循环会自动把 S0 的 4 项全亮——不用额外改，只因 stage 判据变了，亮起时机自然从 "seed 生成" 推迟到 "访谈完成推 S1"
- `_backfill_stage_checkpoints_if_missing` **升级为 schema 增量迁移**（不再仅在文件不存在时运行）：
  - 文件不存在：创建，按以下规则补 key
  - 文件存在但缺 `s0_interview_done_at`：按 "stage-gates.md 显示 stage ≥ S1" **或** "已有任一下游 checkpoint（outline_confirmed_at 等）" 判据补 s0
  - 对 stage = S0 且只有 project-overview.md 无访谈记录的老项目：**不**自动补 s0（避免误判未访谈项目为已完成访谈）
  - outline_confirmed_at 仍按 "stage ≥ S2" 补（与原有逻辑并存）
  - 迁移函数每次项目加载时都运行一次（幂等，已补过的不重复补）

**`skill/SKILL.md`**：按 §6 改

**前端**（`frontend/src/`）：
- `components/RollbackMenu.jsx`：
  - 保留现有 `ROLLBACK_HIDDEN_STAGES`（当前阶段 S0/S1 时菜单整体不显示，本 spec 不改此行为——S0 阶段不展示 rollback menu）
  - 在 **S2+** 的 RollbackMenu 里新增 `s0_interview_done_at` 条目（标题"回到需求访谈"，confirm 文案"之前的表单信息不会删；回到 S0 继续补充澄清；当前大纲、研究计划、数据日志等下游产出也会被清空"——因为 clear 会级联清下游 checkpoint）
  - 点击调 `POST /api/projects/{id}/checkpoints/s0-interview-done?action=clear`
- `utils/workspaceSummary.js`：`stage_code == "S0"` 时 completed 按后端 `_build_completed_items` 输出展示（S0 阶段只亮"project-overview.md 创建"；访谈完成推进 S1 后 S0 全 4 项由主循环自动亮起）；`flags.s0_interview_done` 字段进对象
- `components/ChatPanel.jsx`：content 渲染前加**二级保险剥 tag**（`<stage-ack>…</stage-ack>` 正则剥）——后端主剥，前端兜底兼容流式中间态偶发泄漏和老 conversation 历史残留
- 无新 UI 组件（不加 S0 前端按钮、不加撤销 toast）

### 8. 测试计划

**`tests/test_chat_runtime.py`**（`StageAckParser` + 集成路径）：
- **Parser 基础**：set / clear / 剥离 / 未知 KEY 忽略 / 多 tag 按序执行（不去重）/ action 缺省默认 set
- **Parser 位置约束**：
  - `tag in fenced code ignored`（code fence 内识别但不执行）
  - `tag in inline code ignored`
  - `tag in blockquote ignored`
  - `non-tail tag ignored`（非尾部 tag 识别但不执行，剥离）
  - `tag trailing whitespace tolerated`（尾部有空行/空白仍算"尾部"）
- **流式 tag 不泄漏**：
  - `stream single tag tail not leaked`：模拟 SSE chunk 按字节切割，验证前端永远收不到 `<stage-ack` 片段
  - `stream multi-tag tail longer than 128 bytes not leaked`：显式回归 Round 2 抓到的"固定窗口不够用"case——构造 `set X; clear X; set X` 等总长度超过 128 字节的尾部 ack block，验证 tail guard 策略下前端仍收不到任何前缀字节
- **剥离契约**：`tag stripped before conversation save`（`conversation.json` 无 tag）；`tag stripped before compaction`（`conversation_state.json` / compact input 无 tag）；`_load_conversation sanitizes legacy tag`（老数据迁移）
- **同回合顺序语义**：`set+clear same key order executes both`；`clear+set same key preserves final set`；`multi key different actions execute in order`
- **注入防御**：`user message tag not parsed`（user role 的 tag 纯文本）
- **未知 KEY**：`unknown key logs warning but emits no system_notice`
- **前置校验失败路径**：`set with missing prereq emits notice, strips tag, no persist`；`clear always succeeds idempotent`
- **S0 对话软门槛**：`s0 strong keyword before any assistant message ignored`；`s0 tag first turn without prior assistant question ignored + notice`；`s0 skip works after assistant asked once`
- **S0 write_file 门禁**：`s0 write plan/outline.md rejected with notice`；`s0 write plan/research-plan.md rejected`；`s0 write plan/data-log.md rejected`；`s0 write plan/analysis-notes.md rejected`；`s0 write plan/notes.md allowed`；`s0 write plan/project-overview.md allowed`（允许 merge 澄清）
- **NON_PLAN_WRITE 漏洞补丁**：`direct start writing keyword does not bypass S0 or S1 without outline_confirmed_at`
- **端到端**：S0 首轮 assistant 必须发追问 → 第 2 轮发 tag → s0 set → stage 推进 S1；中途"跳过" → 软门槛通过 → s0 set

**`tests/test_skill_engine.py`**：
- `s0_interview_done_at` 进 `_infer_stage_state`：未 set → S0；set 后 → S1
- `_backfill_stage_checkpoints_if_missing` schema 增量迁移：
  - `file missing → create + backfill by stage-gates.md`（原语义保留）
  - `file exists missing s0, stage ≥ S1 → s0 backfilled`（本次新增，关键迁移测）
  - `file exists missing s0, stage = S0 → s0 NOT backfilled`（防误补未访谈项目）
  - `file exists has s0 → no-op`（幂等）
  - `file exists with outline_confirmed_at but no s0 → outline_confirmed_at preserved AND s0_interview_done_at backfilled`（4-17 spec 落地后最常见场景：保留已有戳同时补 s0，不是二选一）
- `_CASCADE_ORDER` rollback 到 S0 → 清空下游全部 checkpoint
- `CHECKPOINT_PREREQ[s0_interview_done_at] = None` set 不调 validator；clear 幂等成功
- `_build_completed_items` 在 S0 阶段只亮 `STAGE_CHECKLIST_ITEMS["S0"][2]`；推进到 S1 后前置 S0 全 4 项亮起

**`tests/test_main_api.py`**：
- `/api/projects/{id}/checkpoints/s0-interview-done?action=clear` 路由可达（返 200）
- `/api/projects/{id}/checkpoints/s0-interview-done?action=set` **必须返回 400 Bad Request**（与 §7 `backend/main.py` 行为一致；endpoint 层无对话上下文，不允许直接 set s0）
- `clear` 在 s0 未 set 时幂等返 200

**`frontend/tests/`**：
- `workspaceSummary.test.mjs`：`stage_code == "S0"` 对应完成项与后端输出一致（不再假定 S0 全亮）
- `rollbackMenu.test.mjs`：S2+ 菜单有"回到需求访谈"条目 + 点击调正确 endpoint；S0/S1 阶段菜单整体不渲染
- `chatPresentation.test.mjs`：新增"content 里残留 tag 时，前端二级剥干净渲染"的保护测试

**回归基线**：后端 403 passed → 多条新测试（数值由实施时统计）；前端 140 passed → 多条新测试；`npm run build` 零错。删除 `_WEAK_ADVANCE_BY_STAGE` 会破坏现有弱词断言，需同步更新或删除相关测试（非"无改动基线"）。

## Alternatives Considered

1. **Tool call 机制** — 模型调 `advance_stage(key, evidence)` 工具。更规范可审 evidence，但需改 tool schema + 前端 SSE 多处理一种事件 + Gemini-3-flash 的 function-calling 稳定性风险。XML tag 对小模型更友好。

2. **保留全关键词 + 叠加 tag** — 强+弱+tag 三路触发。信号路径太多，调试噩梦，误伤叠加。已否。

3. **N 轮问答硬门槛** — 比如"assistant 问题 ≥ 3 + user 回答 ≥ 3"。用户纠偏明确否了（见 memory `feedback-minimal-ai-questioning.md`）。

4. **S0 前端"跳过访谈"按钮** — 会再加一层 UI 入口。保持最少打扰原则，先不加；如实战发现用户常卡，Phase 2 再评估。

5. **放宽 `_DL_ENTRY_PATTERN` 等正则（"放宽后端"）** — Bug D 修复时已证伪（task-5 prompt 明确禁止）。本 spec 延续"教模型，不是放宽后端"原则。

## Risks & Mitigations

| 风险 | 缓解 |
|---|---|
| 模型漏发 tag | 强关键词兜底 + 前端按钮兜底 |
| 模型乱发 tag 误推进 | 前置校验硬闸 + tag 位置约束（尾部/独立行/代码块外）+ S0 对话软门槛 |
| 模型在首轮就误发 s0 tag 绕过访谈 | 软门槛拒绝（"assistant 历史无 S0 提问"条件下 tag 不执行）+ system_notice 纠正 |
| **流式 tag 泄漏到前端 UI**（新，codex review 抓到） | `chat_stream` **tail guard**（非固定窗口）：尾部出现 `<stage-ack` 任意前缀子串即暂停 flush 至流关闭后统一 parse+strip；覆盖多 tag 尾部块（可超数百字节）；前端二级剥兜底 |
| **已迁移项目缺 s0 schema**（新） | `_backfill_stage_checkpoints_if_missing` 升级为 schema 增量迁移，每次加载项目幂等运行 |
| Gemini-3-flash 不稳定发 tag | SKILL.md §S0 前置 + 硬格式文案；回归测试覆盖；失败回退到强关键词 |
| 老项目升级卡 S0 | schema 迁移按 stage-gates.md ≥ S1 或已有下游 checkpoint 自动补 s0；stage=S0 无访谈的老项目不补（正确路径是让用户重新走访谈） |
| **NON_PLAN_WRITE_ALLOW_KEYWORDS 绕过 S1 大纲确认**（新，codex review 抓到） | `_should_allow_non_plan_write` 在 S0/S1 阶段且无 `outline_confirmed_at` 时拒绝"开始写/写正文"等通用允许关键词 |
| S0 首轮 write_file 门禁误伤合法场景 | 只拦 outline/research-plan/data-log/analysis-notes 四个正式产出文件；notes/references/project-overview 可正常写；用户问答不受任何影响 |
| conversation.json 历史残留未剥 tag 污染 prompt | `_load_conversation` 读取路径 sanitize；前端 `ChatPanel` 渲染前二级剥 |
| 模型把 tag 放在代码块内（展示示例） | 解析器识别但不执行；SKILL.md 明确要求展示 tag 时用转义文本 |
| 用户填完表单直接关掉，从不跟模型对话 | 无状态影响；下次重开项目，checkpoint 仍未 set，继续停在 S0 |
| 用户连续多次说"跳过访谈" | 第一次（软门槛通过后）set；后续 set 走 `_save_stage_checkpoint` 幂等语义，保留首次时间戳 |
| 多 project 并发切换 | tag 事件处理必须在 `_get_project_request_lock` 内完成（与 `record_stage_checkpoint` 同锁），不能锁外异步 |

## Rollout

**Phase 1（本 spec 范围，有先后）**：

1. **迁移兼容测试先行**：`test_skill_engine.py` 的 `_backfill_stage_checkpoints_if_missing` 增量迁移测试（文件存在但缺 s0 的 5 个 case）必须先通过，再跑其他测试。否则先跑 smoke 可能把已有老项目打回 S0 造成用户体验灾难。
2. 代码改动 + SKILL.md 调整（按 §6 前置子节 + 附录文案）
3. 补 parser / 流式缓冲 / sanitize / 门禁 / routes 的所有新测试
4. 运行完整后端 + 前端测试套件，确认新基线（应 >原 403 后端 / 140 前端，具体数值由实施统计）
5. 重打包 `dist\咨询报告助手\`
6. 三轮 smoke test：
   - 新项目 S0 访谈正常流（模型问 → 用户答 → tag 推进）
   - 用户说"跳过访谈" → 软门槛通过 → 推进
   - 老项目（加载已有 4-17 spec 留下的 `stage_checkpoints.json`）不会被打回 S0

**Phase 2（独立评估，不在本 spec）**：收集真实项目 smoke 数据 → 判断是否加 S0 前端按钮、是否需要进一步瘦身强关键词表、是否需要调整 tail guard 的流式剥离策略

## Open Questions

暂无。所有关键决策点已在 brainstorm 中拍板（参见对话记录 2026-04-21）。

## Appendix A — tag syntax reference

```
<stage-ack>KEY</stage-ack>                  # set
<stage-ack action="set">KEY</stage-ack>     # 等价 set（显式）
<stage-ack action="clear">KEY</stage-ack>   # clear / rollback
```

**扫描正则**：

```python
STAGE_ACK_RE = re.compile(
    r'<stage-ack(?:\s+action="(set|clear)")?>([a-z_0-9]+)</stage-ack>',
    re.IGNORECASE,
)
```

**KEY 白名单**：`STAGE_CHECKPOINT_KEYS ∪ {"s0_interview_done_at"}` —— 实施时 s0 应正式加入 `STAGE_CHECKPOINT_KEYS`，此处只是说明白名单就是该集合。

## Appendix B — 强关键词短语表（给模型 prompt 参考用）

```python
_STRONG_ADVANCE_KEYWORDS = {
    "s0_interview_done_at": [
        "跳过访谈", "不用问了", "先写大纲吧", "够了开始吧", "直接开始",
    ],
    "outline_confirmed_at": [
        "确认大纲", "大纲没问题", "按这个大纲写", "就这个大纲", "就按这个版本",
    ],
    "review_started_at": [
        "开始审查", "进入审查", "可以审查了", "开始 review",
    ],
    "review_passed_at": [
        "审查通过", "审查没问题", "报告可以交付",
    ],
    "presentation_ready_at": [
        "演示准备好了", "演示准备完成", "PPT 完成", "讲稿完成",
    ],
    "delivery_archived_at": [
        "归档结束项目", "项目交付完成", "交付归档",
    ],
}
```

`_ROLLBACK_KEYWORDS` 保留原样，不在此重复。

## References

- `docs/superpowers/specs/2026-04-17-stage-advance-gates-design.md`（前置实施，本 spec 基于其 checkpoint 基础设施）
- `docs/current-worklist.md` §1b（本 spec 要解决的 Bug 1 + Bug 3 描述）
- memory `feedback-minimal-ai-questioning.md`（本 spec 的核心约束来源）
- 2026-04-21 brainstorm 会话（对话记录留存于 Claude Code session）
