# 管理办法颗粒度化需求确认 + 流程图布局修复 —— 设计 spec

- 状态：**✅ APPROVED + 无遗留开放问题，可直接交 Fable 单份实施**（Codex gpt-5.5 xhigh 单轨合并审 6 轮：R1 6BLOCKER→R2 3→R3 1→R4 APPROVED→用户改设计[采集移回 S0]→R5 1BLOCKER→R6 APPROVED；§8 已收口全部决定项，用户拍板不拆 plan）
- 日期：2026-07-11
- 触发来源：试用用户「陈燕」（登录名 `测试者1号`）0710 反馈第 2/3/4/6 条，经服务器实测会话与代码核对后收敛。原始反馈另有 1/5/7/8 条，判定为「功能不存在 / 超范围 / 技术不可达 / 无法复现」，**明确不纳入本 spec**（见 §2.5）。
- 范围：两条彼此独立、可并行的工作流，合并在一份 spec 内（用户指定）——
  - **工作流 A（#2/#3/#4）**：把「一刀切的需求确认」升级为「按报告类型感知，并对 management-document 采集颗粒度/条款格式/组织分工」，再按颗粒度注入不同模板。
  - **工作流 B（#6）**：修复 `diagram_render.py` 流程图在「多节点近线性流」下的布局崩坏（节点挤成一条横带、长中文标签叠压不可读）。
- 关联：R5 方法论路由（`build_methodology_block`/`load_type_skeleton`/`TYPE_SKELETON_MAP`/`METHODOLOGY_TONE`，本 spec 复用同一注入缝）、W1 技术标（第 7 类型、`structural` 腔调）、报告图表生成 v2（`diagram_render.py`/`chart_limits.py`/`chart_style.py`，工作流 B 直接改）、`project_created` 自动需求确认 system trigger（2026-07-09，工作流 A 直接改）。
- 非目标声明：本 spec 只交付**设计**，不含实施。实施由 Fable 对照本 spec 接手。**v1 关键数据流已在本次修订中拍死（§4.2），不再留给实施方自行判断。**

---

## 1. 背景：为什么是这四条

### 1.1 一手证据（已在生产服务器核对）

陈燕在 kr-web-01 上真实做了两份 management-document 报告（均给中国南方航空集团）：

| 项目 | 类型 | 关键事实 |
|---|---|---|
| `主数据 管理办法`（proj-2ab8…） | management-document | 上传旧版《主数据管理办法-1203修订版》作参考；会话里**同一句「每条加主题」指令连发 4 次**；正文终稿仍残留操作类内容 |
| `数据质量规则流程图设计及流程说明`（proj-c051…） | management-document | 生成 12 节点流程图 + 010–100 活动说明大表；**流程图渲染成一条不可读的横带** |

四条反馈与证据逐条已验证，是「可复现的踩坑」，非泛泛建议。

### 1.2 一个共同根因（#2/#3/#4）

需求确认目前**对所有报告类型完全一刀切**，代码事实：

- 自动需求确认开场白 `chat.py:SYSTEM_TRIGGER_PROMPTS["project_created"]`（143 行）是**一段写死的静态 dict 常量**；运行时仅 `SYSTEM_TRIGGER_PROMPTS.get(system_trigger)`（2653 行）取用，**拿不到 `project_type`**——所以今天无法按类型分叉。
- `skill.py:build_methodology_block`（≈2870 行）在 `stage not in (S1,S2,S3,S4)` 时**直接返回空**——需求确认阶段（S0）模型**没有任何类型专属指引**。
- 类型感知只在 S1+ 出现，且仅是 `load_type_skeleton(project_type)`（2633 行）注入的**「文档结构骨架」**（management-document 固定映射到 `management-system.md`，`TYPE_SKELETON_MAP` 239 行），是「怎么排版正文」，**不是**「回头向用户采集类型专属参数」。

结论：今天**没有任何机制**在写作前采集 management-document 专属参数——**颗粒度（#4）、条款格式（#3）、组织分工（#2）**。陈燕全程靠中途手动救火。

### 1.3 一个独立的渲染 bug（#6）

`_render_flowchart`（`diagram_render.py:495`）把每层沿**固定 6.4in 宽度**（`MAX_FIGURE_WIDTH_IN`，受 A4 文字区约束不能再宽）横向铺开：`box_w = min(0.19, 0.92/n_layers*0.8)`。陈燕的流程是近线性 11 层链（START→N01→…→END），`n_layers≈11` → `box_w≈0.067`（≈11mm），而节点标签是多行中文（「规则定义与转化\n[领域数据架构师]」）**字号/换行不随框缩小** → 文本溢出、相邻叠压。她在该项目**明说「优先级是流程图的清晰程度」**，结果拿到糊图。

> 注意（Codex R1 纠正的事实）：这**不是**「没有节点上限」——`chart_limits.py` 已有 `MAX_FLOW_NODES=20`/`MAX_FLOW_EDGES=40`。她 12 节点**低于**上限却仍崩，说明**是布局算法问题，不是缺限额**。

---

## 2. 明确的问题陈述

### 2.1 P1（#2）需求确认不区分报告类型
- 现象：`project_created` 静态开场白对 7 类问同一组通用问题；S0 无类型骨架注入。
- 后果：management-document 核心要素（组织架构、职责分工）需求阶段无人问 → 模型凭空猜（陈燕被迫纠正「最高治理机构名称」、手动补「四层架构」）。
- 判定：**真问题**，是 #3/#4 的上位根因。

### 2.2 P2（#3）条款格式被动、且不可一刀切
- 现象：模型初稿默认 `**第X条** 正文`（无条标题）；陈燕要 `第一条【制定目的】…`，连发 4 次才改全（撞每轮 10 次 canonical 改写上限）。
- 关键澄清（用户已拍板）：**绝不能内置任何单一格式**。三种都合法：`第一条 正文`／`第一条 制定目的 正文`／`第一条【制定目的】正文`。取决于该单位/该文件惯例。
- 判定：**真问题**，解法是「采集偏好 / 沿用参考件」，**不是**换一种硬编格式。

### 2.3 P3（#4）management-document 没有「颗粒度」概念
- 现象：只有一个扁平 `management-document` 类型、一套骨架；`management-system.md` 3.3 明确要求「流程具体可执行、时限明确」，示例通篇操作内容。
- 用户诉求：顶层「管理办法」（重组织/职责/原则/框架，操作细节下沉）与「操作细则」（含步骤/时限）是**文档海拔差异**，由用户按每份文件声明，**不是**硬编「管理办法一律不许有操作内容」（那会写坏大量本应可执行的制度）。
- 判定：**真问题**，与现有 `TYPE_SKELETON_MAP`/`load_type_skeleton` 架构严丝合缝。

### 2.4 P4（#6）流程图多节点近线性流布局崩坏
- 现象与根因见 §1.3。判定：**真 bug，已复现**，四条里唯一「用户真受害、能立刻定位修」的。
- 收敛：#6 原文还含「希望自动转表格/Mermaid/时间轴」的泛化愿望——但她 proj2 产出**本就有表格 + 流程图**，「纯文字罗列」不符实。故 #6 收敛为**「把已生成的图渲染对」**；泛化「自动可视化」只作 §7 低优先级 advisory。

### 2.5 明确排除（不在本 spec，Codex R1 认可）
| 反馈 | 排除理由 |
|---|---|
| #1 迭代/优化（确认方向+新旧对比） | **功能不存在**，非 bug。是另一个产品方向（diff/redline），docx→md 已丢原稿精确格式，忠实对照难。列 backlog。 |
| #5 导出格式提前设置 | `export_reviewable_draft` 只产单一 `.docx`，产品**不承诺最终排版稿**，对应不到任何现存选项，超范围。 |
| #7 引用不编造 + 给页码 | 幻觉不可根除（现独立审查已抓到她两处编造）；「给页码」技术不可达——markitdown 转 md 时页码整个丢失。质量体系增强列 backlog，不塞本 spec。 |
| #8 系统卡死 | 服务器她整个会话时段（07-10 14:00–17:00）**零 500/零 provider 超时/零重试**，无崩溃证据。最可能是「长耗时轮 + 无增量进度反馈」的感知延迟，无法复现定位。 |

---

## 3. 目标 / 非目标

### 3.1 目标
- **G1**：需求确认变类型感知——management-document 在需求阶段采集**颗粒度、条款格式、组织分工**。
- **G2**：三参数被 S1–S4 读取并驱动模板：按颗粒度注入不同结构骨架；按条款格式偏好/参考件决定条款样式；组织分工作为已确认上下文注入。
- **G3**：**绝不引入**「管理办法不能有操作内容」这类普适硬规则；操作内容有无由**用户声明的颗粒度**决定。
- **G4**：流程图在「多节点 / 近线性 / 长中文标签」下**可读**（不叠压、不溢出框），超可读上限时**友好失败**而非产糊图。
- **G5（零回归）**：DeepSeek 官渠兼容、多租户隔离、R3 写门禁、计费口径、信任边界、两个 system trigger 语义、其余 6 类报告的需求确认与骨架、无图报告导出、既有 20 种图渲染，全部不受影响。

### 3.2 非目标
- 不做 #1/#5/#7/#8（§2.5）。
- 不给其余 6 类报告做颗粒度化（只 management-document）。
- 不做「模型回读自己生成的图做视觉校验」。
- 不引入 Mermaid/交互图（docx 嵌不进）。
- 不做流程图自动分页/多图拆分（超上限走友好失败，分页列 backlog）。

---

## 4. 工作流 A 设计：类型感知需求确认 + 颗粒度化模板

### 4.1 三个参数

| 参数 | 性质 | 取值（management-document） | 缺省（向后兼容） |
|---|---|---|---|
| `document_granularity` | **确定性模板选择器** | `standard`（混合=现状）／`top_level`（顶层办法）／`detailed`（操作细则） | `standard`（=今天行为，**逐字零回归**） |
| `clause_format` | **确定性写作指令（opt-in）** | `plain`（第一条 正文）／`title_plain`（第一条 制定目的 正文）／`title_bracket`（第一条【制定目的】）／`follow_reference`（沿用参考件样式） | **`None`（未设=不注入任何条款指令→模型按今天行为，逐字零回归）** |
| `org_division` | **上下文（非选择器）** | 自由文本（治理架构 + 各角色职责摘要），或「让助手据主题草拟再确认」 | 空（模型按现状自行组织，不阻塞） |

> **关键（修 Codex R2 BLOCKER 1）**：`clause_format` 默认 `None` 而非 `title_plain`——只有用户**显式设值**才注入确定性条款指令。这样「management-document + 未设 clause_format」与改前**逐字一致**（零回归），同时把「不强制任何格式」这条用户硬约束落进默认行为（opt-in，不改默认样式）。`document_granularity=standard`（默认）走原骨架锚点，同样逐字不变。

### 4.2 参数怎么采集、存哪、谁读（v1 已拍死 —— 三参数全在 S0 需求确认阶段采集）

**核心原则（用户拍板）：三个参数都是「需求确认」的产出，全部在 S0 访谈里采集，落进 `plan/project-overview.md`（需求确认阶段本就产出的元信息真值源）。不进新建项目弹窗、不改 `ProjectInfo`、不改前端建项目表单。**

理由：`project-overview.md` 是 S0 的产出物——它已是「元信息唯一真值源」、**模型可写**（chat.py 阶段写门禁**显式放行 `plan/project-overview.md` 的模型写入**，非笼统「所有 `plan/*.md`」——tracking/非正式 plan 等仍受门禁）、且每轮经 `CORE_CONTEXT_FILES`(skill.py:38) → `build_project_context()`(skill.py:1799) 自动注入 system prompt。把三参数放这里，既贴合「需求确认时确认」的心智，又零前端/零 API schema 改动，还天然解决「S0 口头改了怎么回写」——**用户在对话里改主意，模型下一轮改写该文件的槽即可**，不需要任何单独回写机制。

> **重要（修 Codex R5 BLOCKER，事实校正）**：`project-overview.md` **不在** `USER_EDITABLE_FILES`（skill.py:84），用户**不能**经文件编辑 API 直接改它（`test_skill_engine.py` 断言用户写它 → `UserWriteForbiddenError`）。所以 v1 的采集通道是**「S0 对话 → 模型写槽」单一路径**，不是「用户到文件预览里手改槽」。这不影响设计：用户想调整就在需求访谈里说，模型改写槽。若未来要放开用户手改，需另把该文件加进 `USER_EDITABLE_FILES` + 同步 R3 写门禁/文件树语义/测试（列 §7 backlog，v1 不做）。

- **采集**：`project_created` 开场白（management-document 分支）在需求访谈里追问三参数，用户确认后模型把结果写回 `project-overview.md`：
  - `document_granularity` / `clause_format` → 写进一个**固定结构化槽位**（固定中文标签行，见下 §4.2.1）。
  - `org_division` → 写进一个**自由文本小节**（无需结构化）。
- **读取**：
  - `document_granularity` + `clause_format`（**确定性选择器**）→ 后端新增 `parse_management_doc_params(project_path)`，从 `project-overview.md` 固定槽位**白名单枚举解析**；`build_methodology_block` 调它拿两值。
  - `org_division`（**上下文**）→ 无需解析：随 `project-overview.md` 全文被现有 `build_project_context()` 注入。
- **信任边界（关键，比照 R5，但更简单）**：槽位内容最终**源于不可信的用户对话输入**（经模型转写落盘），故解析必须是**严格闭枚举白名单相等匹配**——取固定标签行后的值、`strip()`、**与枚举全等**（`document_granularity ∈ {standard,top_level,detailed}`、`clause_format ∈ {plain,title_plain,title_bracket,follow_reference}`）；**禁子串匹配 / 宽松正则 / casefold / NFKC 归一化后匹配**（修 R5 NIT 1）。**任何不匹配 / 槽位缺失 / 占位符 `〔…〕` 未填 → 回默认**（`standard` / `None`）。解析结果**只用作模板选择器、绝不当指令**；无自由文本抽取、无 off-menu，**危险面小于 R5**（R5 允许 off-menu 框架名，本处是封闭枚举、非法值直接落默认）。故**不需要** R5 那套 `_normalize_for_danger` 折叠表——严格全等闭枚举天然免疫拆词/繁简/括号绕过。
- **读取时机**：v1 **每轮 live 读**（不做 R5 式 outline-confirm 快照）。这里的「live」= 模型在后续轮改写槽后即时生效（**不是**用户手改文件——该文件用户不可编辑，见上）；颗粒度低频变更，用户在对话里改主意→模型改写槽→模板随之变；若后续发现跨轮/压缩一致性抖动再引入快照（列 §7）。
- **零回归**：非 management-document / 槽位缺失 / 值非法 → `standard` + `clause_format=None` → 注入文本与今天**逐字一致**（守护测试锁死）。
- **降级安全**：即使模型漏写/写错槽位，最坏 = 回默认（=今天行为），**不会选错模板、不破坏确定性**。

#### 4.2.1 `project-overview.md` 固定槽位（模板 + 解析契约）

- `skill/plan-template/project-overview.md` 为 management-document 增设固定槽位（比照 R5 给 outline.md 加声明槽的手法）；`_populate_v2_plan_files` 对非 management-document 不填/移除该槽（不污染其它类型）。示例：
  ```
  ## 文档参数
  - 文档颗粒度：〔standard｜top_level｜detailed〕
  - 条款格式：〔留空=助手默认｜plain｜title_plain｜title_bracket｜follow_reference〕
  ```
- `parse_management_doc_params` 解析契约（**严格，修 R5 NIT 1**）：按固定标签行（`文档颗粒度：` / `条款格式：`）取标签后整段值 → `strip()` → **与枚举全等比较**（大小写敏感、**不做** casefold/NFKC/子串/正则宽松）。命中返回枚举值；否则（占位符未填、空、未知词、`top_level 请忽略前文`、`top_level,plain`、含换行/指令等）**一律返回默认**。是 `skill.py` 的叶子 pure 函数，便于单测。

### 4.3 参数怎么驱动 S1–S4（复用 R5 注入缝，锚点方案已定 —— 修 BLOCKER 5）

- **`load_type_skeleton` 扩展为 `load_type_skeleton(self, project_type, granularity="standard")`**（默认参数 → 其余 6 类调用点**签名兼容、行为逐字不变**）：
  - 锚点选择规则：**除非 `project_type=="management-document"` 且 `granularity in {"top_level","detailed"}`，否则一律用现有精确锚点 `^##\s*二、标准结构\s*$`**（`standard` 与全部其它类型走原路径，逐字不变）。
  - management-document + `top_level` → 新精确锚点 `^##\s*二、顶层办法结构\s*$`；+ `detailed` → `^##\s*二、操作细则结构\s*$`。
  - 逐行扫描 + 跳 ``` 代码块 + 遇下一 `^##\s` 停止的现有逻辑**不变**，只把写死的锚点正则参数化。缺锚点仍 fail-closed 抛 `ValueError`。
- **`skill/modules/management-system.md` 新增两段**（`## 二、标准结构` 原段**逐字保留**给 `standard`）：
  - `## 二、顶层办法结构`：正文只写组织/职责/原则/制度框架；**具体操作步骤、时限、how-to 一律下沉，正文用「按〔配套细则/实施规范〕执行」引述**。
  - `## 二、操作细则结构`：保留「流程具体可执行、时限明确」（与现 `standard` 基线一致或更细）。
  - 两新段的 `## 二、` 前缀**不与** `^##\s*二、标准结构\s*$` 精确正则冲突（标题文字不同），且各自被「遇下一 `^##\s` 停止」自然隔断。**两新段放在原 `## 二、标准结构` 段之后**（避免调整顺序动到 standard fixture 心智，R2 NIT 4）。Codex R2 已核实 `TYPE_SKELETON_MAP` 全部 7 个模块都含 `## 二、标准结构` 锚点，故新签名对其余 6 类零误伤。
- **`build_methodology_block`**：调 `parse_management_doc_params(project_path)`（§4.2.1）拿 `document_granularity`/`clause_format`；前者传入 `load_type_skeleton`，后者传给 `_declare_and_invite_instruction`。
- **条款格式注入必须按 `project_type=="management-document"` 门控，不能按 `tone=="structural"`（修 R2 BLOCKER 1）**：`METHODOLOGY_TONE` 里 **`implementation-plan` 也是 `structural`**（skill.py:252 已核实），若挂在 `structural` 分支会误伤实施方案、破坏「其余 6 类零变化」。落点：`_declare_and_invite_instruction` 内 `if project_type == "management-document" and clause_format is not None:` 才追加条款样式指令；**`clause_format is None`（默认/未设）→ 不追加任何文本 → 逐字零回归**。
  - 指令内容按 `clause_format` 取值给出**明确样式** + 「**不要默认加【】**」。`follow_reference` 且**无可读参考件时回退等价 `title_plain` 的中性提示**（不要求模型沿用不存在的样式，修 R2 NIT 3）。
- **组织分工**：无需新代码——见 §4.2，落进 `project-overview.md` 即被现有核心上下文注入带上。

### 4.4 采集侧落点（S0 访谈 + 模板槽位，**无前端 / 无 ProjectInfo 改动**）
- **`backend/chat.py` S0 开场白 builder**（见 §6 的 `_build_system_trigger_prompt`）：management-document 分支在访谈问题里加「确认文档颗粒度 / 条款格式 / 组织分工」。**关键（修 R5 NIT 3）：`project_created` 首轮仍保持现有「本轮只做提问澄清、不写正文/不写文件」语义——首轮只提问，不诱导 `write_file`**；把结果填进 `project-overview.md` 的「## 文档参数」槽 + 组织分工小节的动作发生在**用户回答之后的常规 S0 轮次**（模型本就在 S0 逐步充实 project-overview.md），不在 kickoff 首轮。**写槽须遵守既有 read-before-write 门禁（chat.py:6784，已有文件先 `read_file` 再写）**——builder 文案不能诱导模型第一步直接 `write_file` 撞门。
- **`skill/plan-template/project-overview.md`**：增设 §4.2.1 的「## 文档参数」槽位（仅 management-document 保留）。
- **`backend/skill.py:_populate_v2_plan_files`**：对非 management-document 不保留该槽（比照现有按类型替换占位的手法）。
- **不改** `backend/models.py:ProjectInfo`、`frontend/src/utils/projectCreatePayload.js`、`frontend/src/components/ProjectCreateModal.jsx`、`backend/main.py` 建项目端点——三参数不走建项目 API，故这些文件**零改动、零回归**。（这也是相对上一版设计的关键简化：把采集从「建项目弹窗结构化字段」移回「S0 需求确认」，前端与建项目契约完全不动。）

---

## 5. 工作流 B 设计：流程图布局修复

### 5.1 根因（已定位 + Codex R1 事实校正）
`_render_flowchart`（`diagram_render.py:495`）：
1. **固定横向铺开**：`x = 0.05 + (0.9/(n_layers-1))*li`，宽度恒为 `MAX_FIGURE_WIDTH_IN=6.4in`（A4 约束，不能再宽）。层多则每列被压扁。
2. **框缩字不缩**：`box_w = min(0.19, 0.92/n_layers*0.8)` 缩到 ~0.067，但 `annotate(fontsize=7.8)`/`_wrap(label,10)` 不随之调 → 文本溢出。
3. **长中文多行标签**：宽度需求远超压扁后列宽 → 叠压。
- **事实校正**：`_canvas()`（61 行）把高度 **clamp 到 `MAX_FIGURE_HEIGHT_IN=7.5in`**（**非无限增长**）；`MAX_FLOW_NODES=20`/`MAX_FLOW_EDGES=40` **已存在**。真 bug = 12 节点（<20）低于上限却因横向压缩不可读——**布局算法问题，非缺限额**。

### 5.2 修复方向
- **B1 方向自适应（主修，判据拍死 —— 修 R2 BLOCKER 3）**：新增朝向启发式——**近线性/层多**时改**纵向（top-to-bottom）**布局：层沿 y 轴自上而下、节点占**整行宽**放标签，充分利用 7.5in 高度预算（一行一节点，横向不再被 6.4in 挤）。近扁平/多分支保留横向。**v1 判据（定死，非开放）**：`n_layers >= 7 且 max_rows <= 2` → 纵向。**此判据必须命中陈燕的 12 节点 ≈11 层 case**（`n_layers≈11>=7`、`max_rows<=2` → 纵向），是回归测试的锚样例。
- **B2 尺寸自洽**：框尺寸/字号/`_wrap` 宽度从「可用单元格尺寸」反推，保证标签放得进框（或框随最长标签在行内撑开）；纵向下每行宽度充裕，标签基本不需极限换行。**纵向分支的画布高度不能沿用横向的 `_canvas(1.6 + 0.72*max_rows)`（近线性 max_rows≤2 只给 ~2.3–3.0in、行会挤）——改为按层数取高：`_canvas(min(MAX_FIGURE_HEIGHT_IN, n_layers * MIN_ROW_HEIGHT_IN / 0.8))`**（`/0.8` 还原 axes 占比），使每层拿到 ≥`MIN_ROW_HEIGHT_IN` 行高；超 7.5in 由 B3 的 `n_layers>12` 先行拦截。
- **B3 高度预算与友好失败（公式拍死）**：纵向可用高度 ≈ `_canvas` 的 axes 高度（`fig 高度 × 0.8`，`fig 高度 ≤ 7.5in`），最小可读行高 `MIN_ROW_HEIGHT_IN`（v1 定 **0.5in**）。**判据**：若 `n_layers > floor(6.0 / 0.5) = 12` → 抛 `ChartRenderError`（人话：节点过多致单图不可读，建议拆分子流程或改 process 模板）。**`MAX_FLOW_NODES` 保持 20 不收紧**（避免挡掉宽而浅的合法图）；纵向高度约束落在**新增独立常量 `FLOW_MAX_VERTICAL_LAYERS = 12`**（`chart_limits.py`），`12 >= 陈燕的 11 层`，不挡真实复现样例。数值全部进 `chart_limits.py` 常量，测试可直接引用。
- **B4（可选，低优先级）**：按角色泳道。增强非修 bug，列 §7。

### 5.3 约束（工作流 B）
- 只改 `diagram_render.py`（连带 `chart_limits.py` 调 flow 上限/加纵向行高常量）。**禁 matplotlib 全局状态机**（只 Agg+OO：`Figure`+`FigureCanvasAgg`），沿用 `chart_style` 工厂——`test_chart_render.py::RendererSourceGuardTests` 连 docstring 都扫，不得触犯。
- 其余 19 种图（所有非 flowchart 渲染器）**源码不改、不回归**。
- 输出仍是不可变 PNG + sidecar；`chart_assets` 落盘/引用扫描/清扫契约不变。
- `render_diagram` 入口签名、错误归一（`ChartRenderError`）、preflight（生成≠插入、复用三道 pure helper、S0 白名单拦截）**不变**。

---

## 6. 数据流与落点（给 Fable 的接线清单，符号名已核对）

| 层 | 文件/函数（已核对真名） | 改动 |
|---|---|---|
| S0 开场白 | `backend/chat.py:SYSTEM_TRIGGER_PROMPTS`（143）→ **改为运行时 builder** `_build_system_trigger_prompt(project_id, system_trigger)`，调用点（2653/2725） | 静态 dict 改运行时组装：management-document 分支追问三参数、指示模型把颗粒度/格式写进「## 文档参数」槽、组织分工写进小节；**保留 keyset 一致性测试 + `project_created` 带工具语义 + 未知 trigger 仍 fail-fast 报错（不查项目）**（修 BLOCKER 4 + R2 NIT 1） |
| 模板槽位 | `skill/plan-template/project-overview.md` + `backend/skill.py:_populate_v2_plan_files`（1051） | management-document 增「## 文档参数」固定槽；非该类型不保留 |
| 参数解析 | `backend/skill.py` 新 pure 函数 `parse_management_doc_params(project_path)` | 从 `project-overview.md` 固定槽**闭枚举白名单**解析两值；非法/缺失→默认(`standard`/`None`) |
| 骨架选择 | `backend/skill.py:load_type_skeleton`（2633，加 `granularity="standard"`）+ `build_methodology_block`（2870，调 `parse_management_doc_params`） | 参数化锚点；其余 6 类默认参数逐字不变 |
| 骨架内容 | `skill/modules/management-system.md` | 保留 `## 二、标准结构`；其后加 `## 二、顶层办法结构`/`## 二、操作细则结构` |
| S1 声明腔调 | `backend/skill.py:_declare_and_invite_instruction`（2816） | **按 `project_type=="management-document"` 门控**（非 tone，`implementation-plan` 同为 structural）；`clause_format is not None` 才注入条款样式（不默认加【】/无参考件回退中性） |
| 组织分工上下文 | `plan/project-overview.md`（已在 `skill.py:CORE_CONTEXT_FILES`:38 → `build_project_context`:1799） | **无需新注入代码**——落进该文件即被现有注入带上 |
| 流程图渲染 | `backend/diagram_render.py:_render_flowchart`（495）/`_flow_layers`（453）/`_canvas`（61） | 方向自适应（`n_layers>=7 且 max_rows<=2`）+ 尺寸自洽 + `n_layers>12` 友好失败 |
| 图限额 | `backend/chart_limits.py`（`MAX_FLOW_NODES`=20 保持/`MAX_FIGURE_HEIGHT_IN`=7.5） | 加 `FLOW_MAX_VERTICAL_LAYERS=12` + `MIN_ROW_HEIGHT_IN=0.5` 常量 |
| **不改** | `backend/models.py:ProjectInfo` / `frontend/src/utils/projectCreatePayload.js` / `frontend/src/components/ProjectCreateModal.jsx` / `backend/main.py` 建项目端点 | 三参数不走建项目 API，前端与建项目契约**零改动** |

---

## 7. Advisory / Backlog（不作硬目标）
- **A-adv1（#6 泛化）**：system prompt/方法论块**软提示**「对比类优先 markdown 表、流程步骤类优先 create_diagram」。advisory、不门禁。
- **A-adv2（泳道流程图 B4）**：flowchart 按角色分泳道。增强非修 bug。
- **A-backlog1（#1）**：迭代/redline 模式（新旧对照）——独立产品方向，另立 spec。
- **A-backlog2（跨轮快照）**：若 live 读「## 文档参数」槽出现跨轮/压缩抖动，改 R5 式 outline-confirm 快照冻结（v1 先 live 读，见 §4.2）。
- **A-backlog5（放开用户手改参数）**：把 `plan/project-overview.md` 加进 `USER_EDITABLE_FILES` + 同步 R3 写门禁/文件树语义/测试，让用户到文件预览里直接改「## 文档参数」槽（v1 只走 S0 对话，见 §4.2 R5 校正）。
- **A-backlog6（#5 导出格式/排版）**：陈燕「填基本信息时预设导出格式」——当前只产单一 docx 可审草稿、不承诺最终排版稿。若未来支持多导出格式或公文排版模板（字体/页边距/公文头），再在建项目或设置里加预选。
- **A-backlog7（#8 长耗时进度反馈）**：陈燕「复杂检索卡死、需重复点击」——无崩溃证据、无法复现（§2.5）；疑似根因＝单轮多工具长耗时 + 无增量进度反馈的感知延迟。可做的是**给长耗时轮加增量进度提示**（缓解「像卡死」），而非追一个不可复现的崩溃。
- **A-backlog3（#7）**：引用防编造的质量体系增强。
- **A-backlog4（B3 后续）**：超节点流程图自动拆分子流程/分页，替代友好失败。

---

## 8. 已定项（无遗留开放问题）

数据流 / 契约 / 阈值已在 §4–§5 全部拍死。剩余决定：

- **颗粒度保留三档** `standard`/`top_level`/`detailed`，`standard` = 零回归默认。
- **不拆 plan（用户拍板）**：工作流 A / B 合一份实施 plan 交 Fable；两者**代码零耦合**（A 动 `chat.py`/`skill.py`/模板，B 动 `diagram_render.py`/`chart_limits.py`），可分别实施/验证，**B（流程图修复）可先落先验**让陈燕先看到效果。
- S0 给用户的颗粒度/条款格式**中文选项文案**由 Fable 实施时拟定（面向不懂技术的顾问、避免困惑），无需在 spec 里固化。

---

## 9. 硬约束与零回归清单（实施必守）
- **DeepSeek 官渠兼容**：工作流 A 只追加 system prompt 文本 / 读写 per-project `project-overview.md`；**不碰** provider message、tool-call 序列化、`reasoning_content`、`tool_choice`、建项目 API。工作流 B 完全不碰 chat 链路。`test_chat_runtime.py` DeepSeek 用例、`compat_helpers_match` 不回归。
- **多租户隔离**：三参数存 per-project `project-overview.md`（本就 per-uid 工作区内），无跨租户共享状态；`parse_management_doc_params` 只读该项目文件。
- **参数解析信任边界**：槽位内容源于不可信用户对话输入（经模型落盘） → `parse_management_doc_params` 必须**严格闭枚举全等匹配**（`document_granularity ∈ {standard,top_level,detailed}`、`clause_format ∈ {plain,title_plain,title_bracket,follow_reference}`；**禁子串/宽松正则/casefold/NFKC**），非法/缺失回默认；结果只作模板选择器、绝不当指令。**闭枚举无 off-menu、无自由文本抽取 → 危险面小于 R5，不需 `_normalize_for_danger` 折叠表**（有对抗测试：槽位塞危险词/指令/`top_level,plain`/占位符未填 → 一律落默认）。
- **`standard` + `clause_format=None`（默认/未采集）= 现状逐字不变**：`build_methodology_block(management-document, granularity=standard, clause_format=None)` 注入文本须与改前**逐字一致**——守护测试用「改前 `load_type_skeleton("management-document")` 输出 fixture 逐字对比」（修 NIT 4）+「`clause_format=None` 时 `_declare_and_invite_instruction` 输出逐字等于改前」，比照报告图表 v2 的 `test_no_charts_system_prompt_verbatim_unchanged` 手法。
- **其余 6 类报告**：`load_type_skeleton` 新默认参数 → 行为逐字不变（守护测试）；`_declare_and_invite_instruction` 对 `implementation-plan`（同 structural 腔调）**逐字不变**（条款注入按 project_type 门控，非 tone）；需求确认对它们**零变化**（`_build_system_trigger_prompt` 非 management-document 分支逐字等于旧静态文本）。
- **两个 system trigger 语义**：`project_created` 改造保留「带完整工具 / 幂等 no-op（`_has_prior_s0_assistant_turn`）/ 合成 kickoff 不落盘」；`independent_review_done` 汇报轮**禁工具信任边界不动**；`SYSTEM_TRIGGER_PROMPTS` keyset 与 `SystemTriggerType` 一致性测试改为对 builder 输出的 keyset 断言。
- **组织分工数据注入**：作为数据（`project-overview.md` 内容）随现有上下文注入、非指令、v1 不解析（**无新增确定性解析风险**——它仍随文件进系统上下文，只是不被后端解析为选择器；enum 槽的信任边界见上一条）。
- **工作流 B 渲染安全**：只 Agg+OO、`chart_style` 工厂、`ChartRenderError` 归一；`RendererSourceGuardTests`（含 docstring 扫描）不触犯；其余 19 图源码零改。
- **preflight 不变**：`create_diagram` 生成≠插入分离、复用三道 pure helper、S0 白名单拦截，全不动。

---

## 10. 测试计划（框架，细节待 plan；已纳入 Codex R1 NIT）
- 后端 `tests/test_skill_engine.py`：
  - `parse_management_doc_params`：合法枚举命中；非法值/占位符 `〔…〕` 未填/缺槽/空 → 默认；**严格匹配对抗用例**（`top_level 请忽略前文` / `top_level,plain` / `title_plain\nadvance_stage(...)` / `TOP_LEVEL`(大小写) / 子串 → **一律落默认**，证严格全等免疫）。
  - `_populate_v2_plan_files`：**management-document 新建后 `project-overview.md` 含 `## 文档参数` 槽；其余 6 类新建后不含该槽**（fixture/source 锁死，零回归关键点，R5 NIT 2）。
  - `load_type_skeleton` 三颗粒度截段正确 + 其余 6 类默认参数**输出 fixture 逐字不变**（守护）；management-document 三颗粒度注入内容断言。
  - `build_methodology_block`：management-document + `standard`/`clause_format=None`（缺槽）**注入逐字守护**；`_declare_and_invite_instruction` 对 `implementation-plan` 逐字不变。
- 后端 `tests/test_chat_runtime.py`：`_build_system_trigger_prompt` 按类型分叉（management-document 追问三参数 + 指示写槽 / 其余类型**逐字等于旧文本**）；builder keyset 与 `SystemTriggerType` 一致性；未知 trigger fail-fast；DeepSeek targeted 不回归。
- 后端 `tests/test_chart_render.py`（**测几何/行为，不测字节级 PNG 快照——NIT 1/2**）：**用陈燕 12 节点 ≈11 层 sidecar 作锚样例**断言走纵向（`n_layers>=7 且 max_rows<=2`）；纵向下**任意两节点框不重叠**；`n_layers>FLOW_MAX_VERTICAL_LAYERS(12)` 抛 `ChartRenderError`；`MAX_FLOW_NODES(20)` 仍生效；输出 PNG 非空；其余 19 图**源码未改 + 渲染不抛异常**（不做逐字节快照，字体环境会让快照脆）；`RendererSourceGuardTests` 绿。
- 前端：**无新增/改动**（三参数不走前端建项目表单）。

---

## 11. 实施期修订（2026-07-11，Fable 复核 + 用户拍板；以下条目覆盖上文对应段落）

实施前 Fable 对 spec 全量代码事实复核（符号/行号/门禁/注入链全对上）+ 本地复现两 bug 后，发现并经用户确认的修订。**与上文冲突处以本节为准**：

### 11.1 工作流 A 修订
- **A1（bug 级）clause_format 注入范围 S1→S1–S4**：§4.3/§6 原落点 `_declare_and_invite_instruction` 只在 `build_methodology_block` 的 `stage=="S1"` 分支被调（skill.py:2895-2899），S4 写条款时指令不在场。改为：条款样式指令做成**独立小块**，management-document 且 `clause_format is not None` 时在 **S1–S4 全阶段**随 methodology block 注入（S1 附在 declare 指令后、S2–S4 附在 adhere 指令后）。测试补 S4 注入断言。
- **A2 枚举加中文别名**：`parse_management_doc_params` 的闭枚举扩为**双语别名闭集**（如 `顶层办法→top_level`、`操作细则→detailed`、`沿用参考件→follow_reference`），仍是**严格全等匹配、无归一化/子串/正则**，信任边界不变。防 deepseek 转写中文访谈答案时写中文值被静默回落默认（恰复刻原始抱怨）。
- **A3 槽位自带填写说明**：trigger_prompt 是 transient system message 只注入 kickoff 那一轮（chat.py:2744），「写槽」指令活不过开场轮。改为模板槽位小节**自带填写说明文字**（后端可信文案，随 `CORE_CONTEXT_FILES` 每轮注入天然持久）；说明与取值行分离，**取值行上除取值本身不得有其它文字**（否则全等解析不中）。
- **A4 三根小刺**：① 槽位「有值但不合法」时（≠缺失/占位符未填），methodology block 注入一条**固定文案** advisory（不回显槽内容）提示模型与用户确认后修正；② 条款样式指令点明「优先于骨架示例中的条款写法」（standard 骨架示例是 `### 第X条 主题` 标题式，会与 `plain` 打架）；③ `follow_reference` 无可读参考件时**不回退 title_plain**，改为指令内置条件句「若项目中无可参考旧文件，先与用户确认采用哪种样式」（模型自知有无参考件，后端不做存在性 heuristic）。
- **A5 事实更正**：§2.3 引 management-system.md「3.3 编写要点」作证据不成立——`load_type_skeleton` 只注入 `## 二` 段，编写要点不进 prompt；真正进 prompt 的操作内容源头是骨架示例里的「第三章 核心业务流程：步骤一/二/三」。结论不变。

### 11.2 工作流 B 修订
- **B1 纵向判据 7→5**：实测 5–6 层横向流标签已全部溢出边框（`_wrap(label,10)` 在 7.8pt 下一行 10 汉字≈1.08in，5 层 box 仅 0.94in、6 层 0.79in）。判据改 **`n_layers >= 5 且 max_rows <= 2` → 纵向**；锚样例（陈燕 12 节点 ≈11 层）仍命中。
- **B2 尺寸自洽双分支都要**：「框尺寸/字号/wrap 宽度从可用单元格反推」**横向分支同样适用**（wrap 字数从 box 宽度动态算），否则修完锚样例、5–6 层之外的横向 case 照样溢出。
- **B3 兜底扩展 + 文案修正**：① 横向分支算出的可读性不达标（如 `n_layers>=5 && max_rows>=3` 的深多分支流，20 节点内可构造）→ 同样友好失败，不产糊图；② 失败文案**删去「改用 process 模板」建议**——`_render_process` 上限 8 步（`_steps_list` cap），>12 层的流程 process 也装不下；只建议「拆分为多张子流程图或合并相邻步骤」。
- **B4 运营注意**：PNG 不可变，存量糊图上线后不自愈，需在项目里重新生成换引用。

### 11.3 新增范围（同批实施，用户 2026-07-11 拍板）
- **C 搜索**：① `managed_search_pool.json` `per_turn_searches` 5→10（部署时改服务器副本+重启）；② **自定义搜索 key/URL**：per-uid Settings 加 `custom_search_provider`（tavily/serper/brave/exa 四选一，决定协议）+ `custom_search_api_key` + `custom_search_api_base`（可选端点覆盖，用于中转/自建兼容服务）。配置了 provider+key → `_web_search` 绕过池子路由与**全部限额门禁**（单轮/分钟/全局；max_iterations 是天然兜底）、不入池子记账。**与模型 API 配置完全独立**（managed 模式也可配，不强制绑定 custom 模型）。URL 校验：**无域名白名单**（用户拍板），但保留 IP 级公网校验（`url_guard` 同款：https + 拒内网/loopback/metadata/CGNAT + 拒 userinfo）——多租户云部署防 SSRF 探内网。key 存 per-uid config.json（同 `custom_api_key` 先例）、GET 掩码回显。
- **D 初次使用引导（终身一次）**：`users` 表加 `onboarded_at`（幂等 ALTER），`/api/auth/me` 透出 `onboarded`，`POST /api/auth/onboarded` 回写（幂等）；前端首次进入时居中卡片式 3-4 步引导（三栏关系/阶段流程/文件查看/审查导出），完成或跳过即回写，换设备不再弹；桌面 local uid 合成 `/me` 返 `onboarded:true` 不弹（web-only 功能）。兑现 0710 反馈响应 F4/F5 承诺的「加强初次使用引导」+ 郭红条的「搜索 API Key 配置」。

## 附录 B：陈燕 0710 反馈 8 点全量处置台账（完整记录，防遗漏）

| # | 原文要点 | 判定 | 处置落点 |
|---|---|---|---|
| 1 | 迭代优化类：①确认优化方向 ②新旧稿内容对比（非只给修订要点） | 功能不存在（她上传旧稿只当参考素材、系统全新生成）；是另一产品方向 | **不做**，记 backlog **A-backlog1**（另立 redline/diff spec）|
| 2 | 管理办法类需确认组织分工 | 真问题（她被迫手动喂四层架构） | **本 spec 工作流 A** — `org_division` 走 S0 对话落 project-overview（§4.2）|
| 3 | 规范条款格式（每条加主题） | 真问题；但**不能硬编格式**（很多办法不要【】、只要「第一条 xxx」） | **本 spec 工作流 A** — `clause_format` opt-in、不默认加【】（§4.1/§4.3）|
| 4 | 管理办法正文不应有操作/解释内容 | 真问题，但**非普适**（颗粒度/海拔差异，很多制度本应可执行） | **本 spec 工作流 A** — `document_granularity` 顶层/细则模板（§4.3）|
| 5 | 导出格式在填基本信息时预设 | 超范围（只产单一 docx、不承诺最终排版稿） | **不做**，记 backlog **A-backlog6** |
| 6 | 对比/流程类纯文字罗列→自动转表/流程图/时间轴 | 部分误报（她 proj2 本就有表+流程图）；真 bug = 流程图排版崩坏 | **本 spec 工作流 B** 修排版（§5）+ advisory **A-adv1**（软提示多用表/图）|
| 7 | 引用上传件偶尔编造 + 拿不到就说不知道并给页码 | 幻觉不可根除（独立审查已抓到她两处编造）；「给页码」技术不可达（md 转换丢页码） | **不做原诉**，记 backlog **A-backlog3**（引用防编造质量增强）|
| 8 | 复杂检索复杂分析加载过长、系统卡死、需重复点击 | 无崩溃证据、无法复现；疑似长耗时轮 + 无增量进度反馈的感知延迟 | **观察**，记 backlog **A-backlog7**（长耗时轮增量进度提示）|

> 本 spec 实施覆盖 #2/#3/#4/#6；#1/#5/#7/#8 已判定不纳入本次实施，理由与将来可做的方向见上表与 §7 backlog——**记录在案、不遗漏**。

## 附录 A：#6 复现证据
陈燕 proj2 sidecar `chart-03a8f66f6f4a.json`：`kind=flowchart`，12 节点近线性链，标签多为「动作\n[角色]」双行中文。渲染产物（已下载核对）：所有节点挤在纵向中部一条横带、文本互相叠压完全不可读。`_flow_layers` 分出 ≈11 层、每层 1–2 节点 → `box_w≈0.067` → 溢出。**12 节点 < `MAX_FLOW_NODES=20`，证明是布局算法问题、非缺限额。** 这是工作流 B 要修的确切形态。
