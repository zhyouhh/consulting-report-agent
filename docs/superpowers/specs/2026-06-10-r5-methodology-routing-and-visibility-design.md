# 方法论路由与显性化（R5）设计

- 日期：2026-06-10
- 状态：**APPROVED**（codex 迭代审 R1→R4 APPROVED + 对抗式红队 R1→R3 APPROVED；累计修 14 迭代 BLOCKER + 红队 2 个 [正常可触发] 真 BLOCKER；剩 2 [既有债] 实现要点 + 1 [仅恶意构造] 已纳入）。待用户过目 → writing-plans
- 关联：`docs/current-worklist.md` 领导评审反馈整改簇 **R5**（批 3）
- 范围决策：把 canonical skill 的「报告类型→方法论」路由**接回 app（改 read_file 自取为代码注入）** + 大纲**显性化声明** + S1**软确认/可换**；模块瘦身合并、图表自动渲染、数据治理细节全文注入、UI 方法论条**后置**
- 前置：批 1（R1+R2，S5 审查迷你聊天）`f111f0e`、批 2（R3，文件栏+可编辑）`53c52fd` 已 merge 进 main

---

## 1. 背景与问题

### 1.1 领导诉求

报奖 demo 后领导评审提出：「没让用户选方法论（BCG / 金字塔 / SWOT 等）」，希望报告**按类型用对分析框架**、且方法论**可见**。

### 1.2 现状实锤（本 spec 的事实基线，均已读源码核实；codex R1 已逐条复核）

这套 app 的 `skill/` 是隔壁可移植 Skill 项目 `D:\MyProject\CodeProject\consulting-report-skill` 的**嵌入副本**。canonical skill **本来设计了完整路由**（`docs/module-routing.md` 的「类型→模块组合」表 + `evals/capability-map.json` 的 `task_types`/`trigger_phrases`），机制是**模型主动 `read_file` 按类型读对应模块**。但嵌进 app 后这条路由**断了**：

1. **只注入一个模块**：`get_skill_prompt()`（`skill.py:2315-2324`）每轮只拼 `SKILL.md` 全文 + `modules/consulting-lifecycle.md` 全文，**无任何按报告类型的分支**。全后端加载 `modules/` 的代码仅此一处（grep `"modules"` 整个 `backend` 只命中 `skill.py:2320`）。
2. **类型模块全是死副本**：6 个类型模块（`strategy-consulting.md` 等）+ 其余通用/工具模块，代码从不注入。子代理实测 16/17 模块与 canonical **字节级相同**（仅 `consulting-lifecycle.md` 被 app 改写并硬注入），说明它们从未被 app 侧改过、也从未被加载。
3. **`read_file` 够不到 skill 目录**：`read_file`（`skill.py:1079-1088`）走 `_resolve_project_path(project_path, ...)`（校验锁在 project root 内，`skill.py:2439-2443`），**锁死在项目工作区**。SKILL.md「路由与模块」段（app 副本 `skill/SKILL.md:203-209`）那句"先读取 `modules/writing-core.md`"对模型**不可执行**——该相对路径会解析到 `<workspace>/.consulting-report/modules/...`（不存在），不是 app 的 `skill/modules/`。
4. **路由桩是死代码**：`get_template(project_type)`（`skill.py:2326-2331`）查 `templates/{project_type}.md`，但全后端 + 测试**零调用**；且 `templates/` 仅 4 个文件、文件名与 6 个 `project_type` slug 对不上。
5. **真实运行佐证**：用户 2026-06-10 跑通的真实报告（`reality_test/3`，implementation-plan 类型）对话记录里，模型碰过的文件路径**全是 `plan/*` 和 `content/report_draft_v1.md`，零 `modules/`**——这份用户评价"很完美"的报告，全程没用上任何类型方法论模块。
6. **模型工具 8 个、无执行器**：`chat.py:3815-3946` 共 8 个工具——`write_file`(3815)/`edit_file`(3833)/`append_report_draft`(3852)/`advance_stage`(3873)/`read_file`(3904)/`read_material_file`(3918)/`web_search`(3932)/`fetch_url`(3946)。S0 首轮允许工具集（`chat.py:102-107`）亦含 `read_material_file`。**无任何通用代码执行器**；`report_tools.py:13-21` 的 `subprocess` 不暴露给模型。

### 1.3 问题陈述

方法论现在**既不显示给用户、也不由代码喂给模型**。领导那句"没让用户选方法论"的真问题不是"选不选"，而是**根本没有按报告类型把方法论用上**。报告质量目前来自 `SKILL.md` 写作规范 + `consulting-lifecycle.md` + 模型自身咨询常识，而非那 16 个模块。R5 要把这条路由以 app 沙箱能跑的方式**接回来**，并让方法论在产物里**看得见**。

---

## 2. 目标与非目标

### 2.1 In Scope

- **G1 路由接回（代码注入）**：后端按 `project_type` 把对应「报告结构骨架」注入 system prompt（S1–S4），替代失效的「模型自取」。
- **G2 共享框架菜单**：一张轻量「分析框架菜单」（框架名 + 一句话，横向对所有类型可用），常驻注入，让模型按报告**实际需要**挑框架，不被类型锁死。
- **G3 显性化**：模型在 `plan/outline.md` 声明本报告采用的方法论框架（**可解析的可见声明行**，§4.2）；声明话术**按类型分腔调**（§7.3）。
- **G4 S1 软确认/可换**：声明同时顺口邀请用户反馈；用户可在**现有「确认大纲」环节**通过聊天换方法论，模型重挑并重写大纲声明。**不新增按钮/设置/模型工具**。
- **G5 已选方法论持久化**：换过的框架要跨轮 + 跨历史压缩稳定（§4.2）——这是 R1 评审挖出的硬契约。
- **G6 模块处置（注入侧）**：明确 17 模块哪些注入、哪些因 app 已做成阶段而不注入、哪些保留为菜单引用。
- **G7 死桩清理**：修 `management-document`(slug) vs `management-system.md`(文件名) 不一致；删 `get_template()` + `templates/` 死代码（repo-wide source guard）。

### 2.2 Out of Scope（明确不做 / 后置）

- **数据治理细节全文注入**：`specialized-research.md` 的 DAMA-DMBOK / ISO 8000 评分卡细节（`:104-:348` 整段、约 1.9k token）**v1 不注入**——非数据子题会注错方法论、且威胁 token 预算（codex R1 BLOCKER 5）。v1 仅菜单一行，细节留 v2 待 A/B（§12）。
- **图表自动渲染 / 代码执行**：模型现有 **8 个**工具（§1.2.6）**无代码执行能力**。复杂 Plotly 图维持"写 `.py` 脚本当交付物"现状（§9）。app 自动渲染图属单独大决定（需沙箱 + Python 运行时），不进 R5。
- **模块瘦身合并**：`writing-core`≈`common-gotchas`、`templates-collection`≈类型骨架、`framework-diagrams`≈框架菜单可视化版——确有冗余（§8.4），但合并是**内容编辑工作**（属作者域），R5 只做"注入侧选择"，不动模块正文合并。
- **`data-analysis` / `business-charts` 全模块注入**：R5 不把这两个模块全文注入（避免 scope creep，codex R1 NIT）；它们以**菜单一行**体现 + 简单视觉本就即时可用（§8.4）。
- **UI 方法论展示条**：S1 大纲里的文字声明 + 可编辑预览（R3 已可见 `outline.md`）即满足"可见"；前端单独的"当前方法论"chip 后置。
- **`evals` 内容质量评测 / canonical 回灌 / 放宽 read_file**：见 §12、§3 D2。

---

## 3. 关键设计决策（决策记录）

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| D1 | 路由载体 | **代码注入（push），不靠模型 read_file（pull）** | app 沙箱 read_file 够不到 skill 目录（§1.2.3）；真实运行实测模型不会主动取模块。代码注入确定性强 |
| D2 | 是否给模型"读 skill 模块"的工具/放宽 read_file | **不做** | 放宽会扩大文件访问面、且仍靠模型自觉（已实测不可靠）；push 一个机制同时覆盖"自动"和"用户可换"，无需新工具（§6.4） |
| D3 | 结构 vs 框架 | **拆开：结构按类型、框架共享** | 框架（SWOT/BCG/金字塔）是通用工具不绑类型（专项研究也能用 SWOT）；真正因类型而异的是报告骨架（§6.2） |
| D4 | 框架菜单形态 | **菜单（名+一句话），非菜谱（全文）** | 模型训练里早会做 SWOT/五力/BCG，菜单只需提示"有哪些可选"。token 实测（§4.3）而非估计 |
| D5 | 注入时机 | **按阶段闸：S1–S4 注入，S4 后撤；且 S1 与 S2–S4 指令不同** | 方法论只在规划/写作期有用；S1 声明+邀请，S2–S4 沿用已选不再重邀请（D11） |
| D6 | 用户选方法论的形态 | **默认按类型自动路由 + S1 聊天软覆盖** | 合"不让用户思考"；又保留 canonical"放那给用户选着用"。覆盖=模型重挑，骑现有"确认大纲"门，零新机制（§7） |
| D7 | 确认是硬门还是软邀请 | **软邀请（不回=默认接受）** | 跟现有"确认大纲"一致；硬卡违反交互原则。[[feedback-minimal-ai-questioning]] |
| D8 | 方法论可换的时间窗 | **锚在 S1「确认大纲」前最便宜；过后不鼓励** | 框架塑造骨架，S1 前换=重写大纲（廉价）；正文写一半再换=重排结构（昂贵），走现有回退路径 |
| D9 | 显性化话术 | **按类型三腔调（分析型/文体方案型/专项研究）** | 不是 6 类都有招牌框架：制度/方案的"方法论"是结构纪律（§7.3） |
| D10 | 路由映射来源 | **以 canonical `module-routing.md` + `capability-map.json` 为设计参考、落地为硬编码常量**（app 运行时不读 capability-map）| port 作者已设计的映射口径避免重造；但不引入运行时读 json 的依赖（codex R2 NIT）|
| D11 | 装配点解析链 | **`build_methodology_block(project_id)` 内解析 type+stage** | 装配点 `chat.py:5962` 只有 `project_id`（codex R1 BLOCKER 2）；type 取项目记录、stage 取 `_infer_stage_state()`（§4.1） |
| D12 | 已选方法论持久化真值源 | **确认时快照**（`stage_checkpoints.json` 保留键 `__methodology_snapshot`）；S2–S4 读快照 | 活 outline 用户可编辑 → 确认后静默换 + legacy 规退（红队 BLOCKER 1+2）；outline 声明仅确认前输入 + 确认后展示（§4.2）|
| D13 | v1 注入粒度 | **仅类型骨架 + 菜单；细节全文留 v2** | 数据治理细节全文非数据子题会注错、且爆 token（codex R1 BLOCKER 5）；细节价值未经 A/B 验证（§12） |
| D14 | 图表能力 | **简单视觉（markdown 表/矩阵）即时渲染；复杂 Plotly 维持脚本交付** | 模型无执行器；非技术同事不会跑 `.py`（§9） |
| D15 | 方法论声明纳入确认大纲前置 | **是**（卡确认大纲 transition，仅新确认 + 6-slug）| 不强制则 G3 静默失效；挂 `_validate_stage_checkpoint_transition`、**不进 `_stage_one_completion_state`**（避 legacy 规退，红队 BLOCKER 1）（§7.5）|
| D16 | outline 声明回注的信任边界 | **净化为数据、绝不当指令入 system** | outline 用户可编辑，原样回注 = prompt 注入；沿用 R2(S5) trust boundary（§4.2，codex R2 BLOCKER 3）|
| D17 | 未知 project_type | **graceful 空块 + log，不抛** | `models.py:14` 不校验枚举，API/旧项目可能非 6-slug；抛进 chat 链路会崩（§4.1，codex R2 BLOCKER 5）|

---

## 4. 架构与数据流

**核心原则：方法论的「该用哪套」由后端按 `project_type`+`stage` 决定并注入 system prompt；模型在注入内容里挑框架、声明、按用户反馈重挑；已选框架在**确认大纲那刻快照**为真值（outline 声明仅确认前输入 + 确认后展示），后端 S2–S4 读快照以跨轮稳定。前端近零改（§7.5）。**

注入装配（每轮 system prompt 组装，现状 `chat.py:5962-5997 _build_system_prompt` 调 `skill.py:get_skill_prompt()` + `build_project_context()`）：

```
system prompt =
    get_skill_prompt()                       # SKILL.md + consulting-lifecycle.md（现状，保留）
  + build_methodology_block(project_id)      # 【新增】§4.1
  + 轮次约束 + build_project_context()         # 现状，保留
```

### 4.1 `build_methodology_block(project_id)` —— 新增装配函数（`skill.py`）

签名只取 `project_id`（装配点只有它，codex R1 BLOCKER 2）；内部解析 `project_type`（项目记录，`skill.py:856`/`958`）与 `stage`（`_infer_stage_state()`，`skill.py:1778`）：

```
def build_methodology_block(project_id) -> str:
    project_type = get_project_type(project_id)        # 项目记录字段（skill.py:856/958）
    project_path = get_project_path(project_id)
    # _infer_stage_state 返回 dict，取 stage_code（skill.py:1876-1883；codex R2 BLOCKER 1）
    stage = _infer_stage_state(project_path)["stage_code"]
    if stage not in {S1, S2, S3, S4}:                  # 仅写作期注入（D5）
        return ""
    # 未知 project_type（API/旧项目；models.py:14 不校验枚举）→ graceful 空块，绝不抛进 chat 链路（codex R2 BLOCKER 5）
    if project_type not in TYPE_SKELETON_MAP:
        log.info("unknown project_type %s, skip methodology block", project_type)
        return ""
    skeleton = load_type_skeleton(project_type)        # §6.1，已知 type 缺锚点才 fail-closed
    menu = FRAMEWORK_MENU                               # §6.3 常驻轻量菜单
    if stage == S1:
        instr = DECLARE_AND_INVITE_INSTRUCTION         # S1：写声明 + 软邀请（§7.2）
    else:                                              # S2–S4
        state, selected = read_confirmed_methodology_snapshot(project_path)  # §4.2 读确认时快照（非活 outline，红队 BLOCKER 2）
        instr = adhere_instruction(state, selected)    # parsed/missing/malformed 分治（codex R2 BLOCKER 4）
    return join([skeleton, menu, instr])
```

> v1 **不**做"数据治理细节按需注全文"（D13；codex R1 BLOCKER 5）——菜单一行 + 模型自身知识足够。装配增量靠实测（§4.3）。
>
> **两种失败模式分清**：未知 `project_type`（运行时数据，API/旧项目）→ graceful 空块 + log，**不抛**；已知 type 的模块"标准结构"锚点缺失（代码/资产回归）→ `load_type_skeleton` **fail-closed 抛错**（§11 测试守），让 dev 立刻发现。

### 4.2 已选方法论的持久化契约（codex R1 BLOCKER 3；红队 BLOCKER 1+2 重定为"确认时快照"）

**核实**：`CORE_CONTEXT_FILES`（`skill.py:34-39`）/ `build_project_context()`（`skill.py:1446-1470`）每轮不含 `outline.md`；且 `outline.md` 用户可编辑（`skill.py:82-84`），`user_write_file` 存盘**不清 `outline_confirmed_at`、不级联回退**（`skill.py:1163-1194`）。故**不能**把"活 outline 声明行"当每轮真值源——两个洞：① 确认后用户改那行 → 下轮静默换方法论，与 §7.4"过 S1 换走回退"矛盾（红队 BLOCKER 2）；② 把声明 parsed 塞进 `_stage_one_completion_state` 会让 R5 前已确认、无声明行的 legacy 项目被判 stage-one 未完成 → `_infer_stage_state` 拉回 S1（`skill.py:467-482`→`1814-1829`，红队 BLOCKER 1）。

**契约（确认时快照，非活读）**：
- **写**：确认大纲那刻（`outline_confirmed_at` set）后端解析+净化声明，存为 `stage_checkpoints.json` 的**保留字符串键** `__methodology_snapshot`（值=净化框架的 string，**非 dict**——`_load_stage_checkpoints`（`skill.py:276-281`）只留 string 值，故**必须把 `__methodology_snapshot` 加进 load 的保留键集**，与 migration marker 同等）。**绝不**做成 `outline_confirmed_at: {timestamp, metadata}`（会被 string 过滤丢→确认失效卡 S1，红队 R2 BLOCKER）。后端写、非模型写，合 §5.2。
- **cascade 保留**（红队 R2 BLOCKER）：`_clear_stage_checkpoint_cascade`（`skill.py:363-381`）重建文件时**必须保留 `__methodology_snapshot`**，仅当清 `outline_confirmed_at` 本身才删；清 `review_started_at`/`review_passed_at`/下游（如 S5"回去改"）时**保留**——否则用户 S5 回退会丢快照、S4 退回 default。
- **实现要点（红队 R3 [既有债]）**：放进**独立保留键集**（如 `PRESERVED_STAGE_CHECKPOINT_STRING_KEYS = {MIGRATION_MARKER_KEY, "__methodology_snapshot"}`），**绝不加进 `STAGE_CHECKPOINT_KEYS`**（它必须 == `_CASCADE_ORDER`，`skill.py:100-118`，加了 invariant assert 会炸）；优先只在 cascade preserve、不必经 `_load_stage_checkpoints` 返回（避免经 `get_workspace_summary` 把内部字段暴露给前端）。
- **读**：S2–S4 装配 `read_confirmed_methodology_snapshot(project_path)` 读**快照**（不读活 outline 行），跨轮/跨压缩稳定、与 chat 历史及用户后续编辑无关。
- **改**：过 S1 后换框架走 §7.4 回退/重确认（重确认时重新快照）；用户在预览里改 outline 声明行**不改快照**（advisory；与 R3 D4「编辑 outline ≠ 重确认、不耦合状态机」一致，UI 提示后置）。

**解析 + 净化**（确认快照时执行一次）：
- 固定格式可见行（行首 `方法论框架：` / `**方法论框架**：`，顿号/逗号分隔），既给人看又可解析，不用隐藏 marker。
- **净化白名单**（trust boundary，codex R2/R3）：① 命中 `FRAMEWORK_MENU` 已知框架名 → **精确匹配**放行；② 菜单外 → **严格短标签**（中英文/数字/连字符，**剥括号内容**，长度 ≤~20 字、条数上限），命中工具名/stage·checkpoint/危险词 → 整条 `malformed` 拒。注入以"用户已选框架（数据，按字面处理）：…"。单用户可信输入下这主要防"误把指令当框架"而非真对手（红队 BLOCKER 4 = [仅恶意构造]，按现实闸门轻收）。可选更硬（v1 不强制）：菜单外只收名词式短标签（须以 分析/模型/矩阵/法/框架/原则 等结尾）。
- **三态**：`parsed` → 注"已选：〔净化框架〕，正文须沿用"；`missing`（legacy 无快照/未写声明）→ 回类型默认建议 + log，不冒充已选；`malformed` → 不静默回默认，注"未解析到有效声明，勿擅自换框架"。

### 4.3 token 预算（codex R1 NIT）

不写未量化估计。落地为**测试断言 / 开发脚本输出**（codex R2 NIT，§11）——用现有 `tiktoken` 路径对 6 类骨架+菜单实测注入块 token，**断言 ≤2k/轮**（§5 第 6 条）；超则削菜单或骨架。

---

## 5. 禁改区 / 不破坏（硬约束）

R5 只**新增装配**，不得破坏以下既有契约：

1. **DeepSeek 官渠兼容**（`CLAUDE.md` 锁）：注入只给 system prompt **追加文本**，不碰 provider message / tool-call / `reasoning_content` / `tool_choice` 逻辑。`tests/test_chat_runtime.py` DeepSeek 用例不得回归。
2. **阶段状态机**：方法论"换"走**现有大纲修订 + `outline_confirmed_at`**，**不**新增 checkpoint、不让模型直接写 `stage_checkpoints.json`、不恢复任何 `<stage-ack>` 语义。阶段推进唯一入口仍是 `advance_stage`。方法论快照存为 `stage_checkpoints.json` **保留字符串键 `__methodology_snapshot`**（后端写、非模型直写、非新 checkpoint key）；cascade 仅随 `outline_confirmed_at` 清除、其它 checkpoint 清除时**保留**（§4.2）。合规。
3. **S5 审查独立性**：注入方法论不影响 `independent-review.md` / `lint-report.md` 写入者约束。
4. **正文写入契约**（S4）：注入不改 `append_report_draft` / canonical draft `edit_file` 的 6 道 invariant check。
5. **`consulting-lifecycle.md` 现状注入**（`skill.py:2320`）保留，不动。
6. **token 预算**：S1–S4 注入增量目标 ≤ 2k token/轮（§4.3 实测），不显著挤占上下文。
7. **装配期只读**：`read_confirmed_methodology_snapshot` 是**只读**，装配期不写任何文件；方法论快照的**写**只发生在确认大纲（`record_stage_checkpoint` 内），不在 system prompt 装配链路。
8. **outline 回注信任边界**：从用户可编辑的 `outline.md`（`skill.py:82-84`）解析的内容回注 system prompt 时**必须净化为数据**（§4.2），不得当系统指令——与 R2（S5）报告注入同一 trust boundary。

---

## 6. 路由与框架菜单（详设计）

### 6.1 报告结构骨架（按类型，6 套）

每个类型模块的「## 二、标准结构」段即该类型骨架（已读 6 个模块核实）。`load_type_skeleton(project_type)` 取对应模块该段；**缺锚点 fail-closed**（抛错而非静默空串，§11 有测试）：

| project_type (slug) | 模块文件 | 骨架要点 | 性质（→§7.3 腔调）|
|---|---|---|---|
| `strategy-consulting` | strategy-consulting.md | 外部(PEST/五力)→内部(SWOT/价值链)→定位→举措→路线图 | 分析型 |
| `market-research` | market-research.md | 市场概况(规模/细分)→竞争格局→客户洞察→趋势机会 | 分析型 |
| `specialized-research` | specialized-research.md | 背景→方法→现状→根因→方案（通用骨架）| 分析型·条件 |
| `management-document` | **management-system.md**（slug→文件映射，§8.3）| 章-条-款-项 公文体 | 文体型 |
| `implementation-plan` | implementation-plan.md | 背景→步骤(准备/实施/验收)→保障→进度→风险→监督 | 方案型 |
| `due-diligence` | due-diligence.md | 业务→财务→运营→法律→风险→估值→红旗 | 分析型 |

### 6.2 框架不绑类型（设计依据）

框架是**共享分析工具**，模块当年按类型归档造成"战略专属"错觉。实例：SWOT 可用于专项研究评估流程/技术；金字塔原理是通用写作结构（已在 `SKILL.md` 写作约束「结论先行」）；波特五力同时出现在 strategy/market/due-diligence 三模块。故框架走**横向共享菜单**，类型只决定**默认强调哪几个**（§6.3"主用类型"列），模型最终按报告实际问题挑、也可挑菜单外框架（用自身知识）。

### 6.3 共享框架菜单（`FRAMEWORK_MENU` 常量，v1）

轻量清单，每条＝框架名 + 一句话用途，常驻 S1–S4 注入。**v1 全部仅菜单一行**（含数据治理项；细节全文 v2，D13）。实际 token 实测（§4.3）。

| 框架 | 一句话 | 主用类型 |
|---|---|---|
| SWOT | 内外部优劣势/机会/威胁 | 广谱 |
| PEST | 政治/经济/社会/技术宏观环境 | 广谱·战略 |
| 波特五力 | 行业竞争强度五维 | 战略/市场/尽调 |
| 价值链 | 主要+支持活动定位优势环节 | 战略 |
| 金字塔原理/MECE | 结论先行、不重不漏分组 | 广谱（已在 SKILL.md）|
| 对标分析 | 选可比对象横向比 | 广谱 |
| 根因分析 | 问题溯源不停表面 | 专项研究 |
| 成熟度模型 | 五级阶梯定位现状/目标 | 评估类 |
| BCG/GE 矩阵 | 业务组合定位 | 战略 |
| 安索夫矩阵 | 增长路径四象限 | 战略 |
| TAM-SAM-SOM | 市场规模自上而下 | 市场 |
| CR4/HHI | 市场集中度 | 市场 |
| SMART | 目标设定五要素 | 实施方案 |
| RACI | 责任分配四角色 | 实施方案 |
| 甘特/里程碑 | 进度与关键节点 | 实施方案 |
| 财务尽调三维 | 收入真实性/成本/资产质量 | 尽调 |
| 红旗识别 | 异常/诉讼/关联交易 | 尽调 |
| 影响-可行矩阵 | 建议优先级排序 | 广谱·建议 |
| DAMA-DMBOK / ISO 8000 | 数据治理组织/质量/成熟度 | 数据专项（仅一行；细节 v2）|

### 6.4 为何 push 一个机制即可覆盖"自动+可换"

框架选择本就是**模型在注入菜单里挑**的动作。用户"换"= 给模型一句聊天反馈让它重挑、改 `outline.md` 声明行 → **确认大纲时快照**（§4.2）；S2–S4 读快照回注，**不需要新设置/按钮/工具**。想用菜单外框架直接说，模型用自身知识照做（菜单是默认建议非笼子）。

---

## 7. 显性化 + S1 软确认/可换

### 7.1 声明位置与格式

模型在 `plan/outline.md` 顶部写一行**可见且可解析**的方法论声明（R3 已让 `outline.md` 可预览/编辑）。格式固定（供 §4.2 后端解析）：

```
方法论框架：SWOT、波特五力、BCG 矩阵
```
（或加粗 `**方法论框架**：…`；解析按行首关键词 + 逗号/顿号分隔。）

### 7.2 阶段化注入指令（codex R1 BLOCKER 4）

- **S1（`DECLARE_AND_INVITE_INSTRUCTION`）**：出/改大纲时，写上述声明行 + 聊天里软邀请：
  > 本报告将采用 **〔所选框架〕** 分析框架。若你希望换用其他方法论，告诉我即可；否则我们按这个继续，你随时可在工作区点"确认大纲"。
- **S2–S4（`adhere_instruction(selected)`）**：注入"本报告已选方法论：〔从**确认快照**取的 selected〕，正文须沿用，不要重新征求或反复改大纲方法论"。**不重复软邀请、不诱导重选**。

### 7.3 三种腔调（D9）

按 §6.1「性质」列分腔：

- **分析型**（战略咨询/市场研究/尽职调查）：`本报告采用 SWOT / 波特五力 / BCG …`。
- **文体/方案型**（管理制度/实施方案）：方法论=结构纪律。`本制度采用「章-条-款-项」规范结构` / `本方案采用 SMART 目标 + RACI 分工 + 里程碑计划`。
- **专项研究**：按子题目——数据治理题→`采用 DAMA-DMBOK + ISO 8000 数据质量评分 + 成熟度模型`；非数据题→落通用研究方法（根因/对标），别硬套招牌框架。

声明指令须把"按本报告类型选合适腔调"写清，避免对制度/方案型硬贴"采用 SWOT"。

### 7.4 换的时间窗（D8）

"问+换"锚在 S1**确认大纲前**（重写大纲声明即可，廉价；确认那刻快照冻结，§4.2）。过 S1 后用户若要换框架，模型应提示"这意味着重排已成稿结构、可能返工"，并走现有「调整大纲 / `advance_stage` 回退」路径重确认（重确认重新快照）。**用户在预览里直接改 outline 声明行不改快照、不自动生效**（advisory）——避免确认后静默换方法论（红队 BLOCKER 2）。

### 7.5 声明纳入确认大纲前置（codex R2 BLOCKER 2 / R3 BLOCKER 1 / 红队 BLOCKER 1+3）

现有确认大纲只校验"有效大纲"（`_has_effective_outline` 数章节，`skill.py:2117-2122`；`outline_confirmed_at` 只查 `missing_prerequisites`，`skill.py:641-644`），**不要求方法论声明行**。模型漏写、用户照样确认进 S2，G3"可见"就静默落空。

修（**只卡新确认 transition，不进持久完成态、不规退 legacy**）：
- 声明前置只作用在**确认大纲 transition 校验**（`_validate_stage_checkpoint_transition` for `outline_confirmed_at`，`skill.py:1633-1657`），**仅当 `outline_confirmed_at` 尚未 set**（新项目首次确认）+ **仅已知 6-slug**（`project_type ∈ TYPE_SKELETON_MAP`，与 §4.1 同源 helper `_get_project_type_for_path`，避漂移）。
- **绝不**把声明塞进 `_stage_one_completion_state` 持久完成态——否则 R5 前已确认、无声明行的 legacy 已知-type 项目会被判 stage-one 未完成、被 `_infer_stage_state` 拉回 S1（`skill.py:467-482`→`1814-1829`，红队 BLOCKER 1）。已确认项目永不重判，靠 §4.2 `missing` 兜底。
- 未知 type 既不注入也不门禁，正常确认（避死锁，R3 BLOCKER 1）。
- 用户提示"大纲缺方法论声明行，请补一行"（与 `CHECKPOINT_PREREQ` `skill.py:239-245` 文案并列）。

**前端近零改（红队 BLOCKER 3）**：S1 确认按钮现只看 `flags.outline_ready`（`frontend/src/utils/workspaceSummary.js:79-87`）→ 漏声明仍可点、点了后端拒、toast。改：`get_workspace_summary` 暴露 `methodology_declared` flag（仅 known+未确认时有意义），确认按钮 enable/禁用理由带上它——不再"前端零改"，是**近零改**（一 flag + 文案）。`STAGE_CHECKLIST_ITEMS["S1"][2]`"分析框架确定"的**显示**也镜像 declaration-parsed（display-only，不驱动阶段回归）。

模型 S1 同一步写大纲+声明，正常流程天然满足；新项目确认后已快照、进 S2 注快照，§4.2 `missing` 仅兜 legacy / 未知 type。

---

## 8. 模块处置（17 个）

### 8.1 注入（R5 接入）
- 6 个**类型骨架**（§6.1）——按 `project_type` 路由。
- **框架菜单**（§6.3）——常驻，仅菜单一行。

### 8.2 不注入（app 已做成阶段/机制）
- `consulting-lifecycle.md` → 已是阶段机且已硬注入（保留现状）。
- `quality-review.md` → 已做成 S5 双按钮审查。
- `final-delivery.md` → 已做成 S7 导出（`export_draft.ps1`）。
- `writing-core` / `common-gotchas` 核心（结论先行/去 AI 味）→ 已在 `SKILL.md` 写作约束。

### 8.3 死桩清理（G7）
- `management-document`(slug) ↔ `management-system.md`(文件)：`load_type_skeleton` 用 slug→文件映射统一（前端 slug 在 `frontend/src/components/ProjectCreateModal.jsx:115-120`，模块文件名 `skill/modules/management-system.md`，映射必要且可行）。倾向加映射，不重命名文件（避免动 canonical 副本名）。
- `get_template()`（`skill.py:2326`）+ `templates/`（4 文件，名不符且零调用）：**删**。R5 走 modules"标准结构"段，不依赖 templates。删除测试用 **repo-wide source guard**（不止 backend，codex R1 NIT）。

### 8.4 保留为菜单引用（不全文注入，不删模块）
- `data-analysis.md`：描述统计/趋势/相关性方法 → **菜单一行**体现；R5 不全文注入（避 scope creep，codex R1 NIT）。产物（文字/表格结论）本就可用。
- `business-charts.md`：简单视觉（markdown 表 / SWOT 四象限 / BCG 四格）模型直接写、即时渲染可用（§9）；复杂 Plotly 脚本属 §9 后置。菜单一行提示"简单图用 markdown 表"。
- `recommendation-framework.md`（影响-可行矩阵）、`framework-diagrams.md`、`templates-collection.md`：以框架菜单条目体现，模块正文合并后置（§2.2）。

---

## 9. 图表现实（为何复杂图后置）

模型 8 个工具无代码执行（§1.2.6、`chat.py:3815-3946` 核实）。故：
- **简单视觉**：表格、框架矩阵（SWOT 2×2、BCG 4 格）用 markdown 表——模型直接写进正文，app 渲染（GFM 表格渲染已支持）/`可审草稿` pandoc 导出可见。**R5 内即可用，零新工具**。
- **复杂统计图**（瀑布/桑基/热力/Plotly）：模型只能 `write_file` 出 `.py` 脚本，app 不跑、非技术同事也不会跑——**当前等于没"用起来"**。维持原 skill"图表脚本 `*.py`"交付现状，不在 R5 解。
- **自动渲染**（把图渲进报告）：需代码执行/沙箱/Python 运行时，单独大决定，明确 Out of Scope。

`report_tools.py:13-21` 的 `subprocess`（`_run_powershell`）仅供 app 跑 lint/导出脚本，模型不可调，非通用执行器。

---

## 10. 涉及文件（实施面）

- `backend/skill.py`：新增 `build_methodology_block(project_id)` / `load_type_skeleton` / `parse_and_sanitize_methodology` / `read_confirmed_methodology_snapshot` / `FRAMEWORK_MENU` / `TYPE_SKELETON_MAP` + slug→文件映射 + `_get_project_type_for_path`（门禁/注入同源）；解析 stage 复用 `_infer_stage_state(...)["stage_code"]`；**确认大纲 transition**（`_validate_stage_checkpoint_transition`，仅新确认 + 6-slug）校验声明 + `record_stage_checkpoint` 写快照入 metadata；`get_workspace_summary` 透 `methodology_declared` flag；删 `get_template`。（`log` 用模块级 logger——codex R3 NIT。）
- `backend/chat.py`：`_build_system_prompt`（`:5962-5997`）装配处接 `build_methodology_block(project_id)`；S1/S2–S4 阶段提示分别带"声明+软邀请"/"沿用已选"指令。
- `skill/SKILL.md`（app 副本）：把失效的「路由与模块」段（`:203-209`）改为"方法论由系统按类型注入"说明 + S1 大纲声明/软邀请约束；不再让模型 read_file 取模块。
- `skill/templates/`：删（§8.3，repo-wide guard）。
- `tests/`：见 §11。
- 前端：**近零改（红队 BLOCKER 3）**：新增 `methodology_declared` flag 透出 + S1 确认按钮 enable/禁用理由带声明前置（`workspaceSummary.js` + 确认按钮组件）；声明本身走 outline.md（R3 已可见/可编辑），换走现有聊天+确认大纲。

---

## 11. 测试

- `tests/test_skill_engine.py`：
  - `build_methodology_block(project_id)` 按 `project_type` 出对应骨架、按 `stage` 闸（S1–S4 有、S0/S5+ 返空）；S1 出"声明+邀请"指令、S2–S4 出"沿用已选"指令（不含邀请）。
  - `parse_and_sanitize_methodology` 三态：能解析→`parsed`；无声明/legacy 无快照→`missing`；非法标签→`malformed`（**不静默回默认**）。`read_confirmed_methodology_snapshot` 读 metadata 快照、不读活 outline。均不抛。
  - **legacy 不规退**（红队 BLOCKER 1）：造已有 `outline_confirmed_at`、outline 无声明行的 known-type 项目 → `_infer_stage_state` **不**被拉回 S1、阶段稳定。
  - **确认门只卡新+known**（红队 BLOCKER 1/3）：known+未确认+缺声明=确认 transition 拒；known+已确认=不重卡；unknown=不卡。
  - **确认后改 outline 不动快照**（红队 BLOCKER 2）：确认后改声明行 → S2–S4 注入仍是快照旧值。
  - `load_type_skeleton`：6 模块"标准结构"锚点存在性快照测试；锚点缺失 **fail-closed**（抛错）。
  - slug→文件映射（management-document→management-system.md）。
  - `get_template`/`templates/` 已删：**repo-wide** source-guard（不止 backend）。
- `tests/test_chat_runtime.py`：system prompt 含方法论块（S1–S4）、不含（其它阶段）；S2–S4 注入来自**确认快照**（造带快照的项目验回注）；净化白名单（恶意声明→malformed 拒）；**DeepSeek 兼容用例不回归**（§5.1）；注入不触发工具/阶段副作用。（遵守 [[feedback-skip-full-chat-runtime]]：不重跑全量，spot-check。）
- `tests/test_packaging_docs.py`：若 SKILL.md 锁定句变更需同步。
- 三腔调：用例校验注入指令对 6 个 slug 给出正确腔调（分析型/文体方案型/专项研究）。
- **确认大纲前置**（codex R2 BLOCKER 2 / 红队）：known+未确认+缺声明 → **确认 transition 校验拒**（surface 补声明）；有声明 → 放行。**不经 `_stage_one_completion_state`**（避 legacy 规退）。
- **净化白名单**（codex R2 BLOCKER 3 / R3 BLOCKER 2）：含工具名/stage·checkpoint/危险词的恶意 outline 声明行 → 判 `malformed`（拒，**不剥**）；合法菜单外标签 → 严格字符白名单放行、以"数据"形式注入；精确匹配的已知框架名放行。
- **三态**（codex R2 BLOCKER 4）：parsed / missing / malformed 各注入正确指令（malformed 不静默回默认）。
- **未知 type**（codex R2 BLOCKER 5）：非 6-slug 的 `project_type` → `build_methodology_block` 返空、**不抛**。
- **前置矩阵**（codex R4 NIT）：known+缺声明=阻止确认大纲；unknown+缺声明=**不**阻止；known+malformed=阻止且不回默认。
- **token 预算**（codex R2 NIT）：6 类骨架+菜单的注入块 token 用 `tiktoken` 实测、**断言 ≤2k**（测试或 `tools/` 脚本输出，非口头）。
- source-guard 避开 `__pycache__` / `.pyc`（codex R2 NIT），避免旧 `.pyc` 命中污染。

---

## 12. 验证（诚实声明）

模块的"内容质量价值"**从未被验证**——连 canonical 的 evals 也只验路由意图格式（`expected_modules` + 禁词 + 行为），`run_evals.py` 是 schema 校验、不跑真 LLM 打分；且用户那份"很完美"的报告全程没用模块。故：

- R5 落地后**强烈建议**补一轮真·A/B：同一选题「裸跑（现状）vs 注入对应骨架+菜单」，比正文方法论运用质量。
- 差异显著 → 路由有价值，继续优化（含考虑 v2 数据治理细节注入）；几乎无差 → R5 可缩为"仅显性化声明 + 删死模块"，省注入 token。
- A/B 是**独立验证步骤**，不阻塞 R5 实施（先接回路由才有得比）。

---

## 13. 开放问题（待 review / 实施细化）

1. `get_project_type(project_id)` 取值的确切字段/函数（registry record vs project-overview 解析）——实施时定，倾向 registry record（`skill.py:856`/`958`）。
2. S2–S4 的 `adhere_instruction` 是否也提醒"如需大改方法论请回 S1"——倾向简短提醒即可。
3. 框架菜单 19 条是否精简（去 GE/安索夫等低频）？v1 留全，token 实测后定。
4. `data-analysis` 菜单条目放"广谱"还是仅"专项/市场"——实施时定。

---

## 14. 与历史整改的关系

- 批 1（R1+R2）、批 2（R3）已闭环并 merge。R5 是批 3 两条之一（另一条 R4 来源标注，独立轻量，数据支持"纯标注+规则档位"，另起）。
- R5 不碰 S5 审查链路、不碰文件树/写接口（R3 域），与前两批正交。
