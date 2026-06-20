# W1 标书（技术标）报告类型：新 project_type + RFP 驱动方法论

- 状态：`✅ APPROVED（codex spec+quality 合并轨 4 轮：R1 4 BLOCKER → R2 2 → R3 1 → R4 APPROVED；3 个收尾 NIT 已折入）。下一步：writing-plans`
- 日期：2026-06-20
- 关联 worklist：W1（标书/技术方案报告类型模板）、接 R5 `build_methodology_block` 方法论路由
- 前置上下文：「轮 1 引擎级特性」之一，与 N6 附件管线独立（各自 spec）；引擎级特性，桌面 + web 都吃到。参考样本在仓库 `bid reference/`（1 份主标 + 3 份副标，真实项目，广西电网数据资源入表，技术投标文件 docx）。

## 1. 背景与目标

**需求**：部门领导要把公司材料 / 招标文件 / 技术规范书上传，吭哧出一份**技术标主体（技术标）**，主要用于**副标**（替别家公司写的陪标，内容与主标相近、字数可少、质量线＝**不出严重错误**）；但质量按主标看齐做更好，**反正就是技术标**。

**真实样本结论（已读 `bid reference/` 4 份 docx 标题大纲）**：主标与副标结构**高度一致**，差别集中在「理论与政策依据」块与各章深度（字数）。印证「更多时候只是字数差别」。技术标有一套**recurring 标准主干**，但**实际结构由本次招标文件/技规的评分点决定**（用户强调：每个项目不同，主干只能作参考）。

**技术标区别于现有 6 类的本质**：其他报告是「自由分析、套固定标准结构」；技术标是**被招标文件/技规/评分标准约束、要逐条响应、被打分**——脊柱是「评分点对标」（技术评分索引表 + 技术规范书点对点应答）。

**现状（实测源码）**：R5 方法论路由已就位但只覆盖 6 类。`backend/skill.py`：
- `TYPE_SKELETON_MAP`（6 type→`skill/modules/*.md`，~243）、`METHODOLOGY_TONE`（3 档 analytical/structural/specialized，~252）。
- `build_methodology_block`（~2695）按 type 注入「骨架 + `FRAMEWORK_MENU` + 阶段指令」到 S1–S4 system prompt；`load_type_skeleton`（~2473）取模块「## 二、标准结构」段；`_render_methodology_block`（~2685）拼装；S1 用 `_declare_and_invite_instruction`（~2642，按腔调让模型在 `plan/outline.md` 顶部写「方法论框架：…」声明行），S2–S4 用 `_adhere_instruction`（沿用确认快照）。
- `project_type` **后端无 enum**（`models.py:14` 自由 `str`），唯一「类型清单」是前端 `ProjectCreateModal.jsx` 下拉（~114）。
- 净化：`parse_and_sanitize_methodology`（~2529）/ `_normalize_for_danger`（~2510）。off-menu 框架名（不在 `KNOWN_FRAMEWORK_NAMES` ~295）只要落 off-menu 白名单 `[A-Za-z0-9一-鿿\-/ 　]` 且不触危险归一化即判 `parsed`——**中文框架名（如「点对点应答」）天然过白名单**。

**目标**：新增第 7 个 `project_type=technical-bid`，接进 R5；其方法论是「**读 RFP→抽评分点→以参考骨架为底按本次评分点增删调序→正文覆盖→后置生成点对点应答+评分索引表（兼覆盖自检）**」；字数复用「预期篇幅」；不出严重错误。

**成功标准**：
- 选「技术标」类型建项目 → S1–S4 system prompt 注入技术标参考骨架 + RFP 驱动指令 + 后置生成指令。
- 模型把上传的招标文件/技规当结构来源（参考骨架不照搬），正文覆盖评分点，最后生成点对点应答 + 评分索引表，并以此自检漏项。
- 副标＝同结构按「预期篇幅」砍字数（主砍理论政策块）；替写公司信息以用户提供为准、不张冠李戴。
- 招标文件读取：引导用户传关键小文件（评分标准/技规）；超大/读取失败时友好停止请用户拆分，不静默截断。
- 不破现有 6 类与 R5 净化/确认门/DeepSeek 兼容。

## 2. 范围

**In scope（v1）**：
- 新 `project_type=technical-bid`：前端下拉 option + `TYPE_SKELETON_MAP` + `METHODOLOGY_TONE` 新腔调 `bid` + 新模块 `skill/modules/technical-bid.md`。
- 技术标**参考骨架**（模块「## 二、标准结构」），框定为「参考、以 RFP 为准、按评分点增删调序」。
- **RFP 驱动 + 后置生成**方法论（**必须写进模块「## 二、标准结构」段内才常驻注入 S1–S4**，见 §3.2 约束；S1 声明腔调适配）。
- 字数复用既有「预期篇幅」字段（不加主/副开关，用户已定 A）。
- 质量护栏（prompt 级）：不编造资质/业绩/政策；替写公司信息以用户材料为准；评分点覆盖自检。
- R5 净化/声明同步核对（确认 bid 框架名走 off-menu 白名单，按需动 `FRAMEWORK_MENU`/`KNOWN_FRAMEWORK_NAMES` 并守 `_normalize_for_danger` 不变式）。

**Out of scope（v1.1 / 归他处）**：
- 评分索引表的**真实页码**与**两表前置排版**（markdown 草稿无页码、两表落草稿末尾、索引指章节；页码与「移到最前」＝导出排版期，本 spec 不做）。
- 自动解析评分标准成结构化评分项表（v1 靠模型读 + 文本组织，不做确定性 parser）。
- 招标文件超大附件的**鲁棒**解析（流式/分块，归 N6）；W1 仅用现有解析器读可承受的小文件 + size 阈值守门（§5），大文件降级。
- 新增图表/自动渲染（维持 R5 现状）。
- 商务标 / 报价 / 资格响应（只做技术标主体）。

## 3. 设计

### 3.1 接入点（最小代码改动）
- 前端 `ProjectCreateModal.jsx` 下拉加 `<option value="technical-bid">技术标（投标）</option>`。
- `TYPE_SKELETON_MAP["technical-bid"] = "technical-bid.md"`。
- `METHODOLOGY_TONE["technical-bid"] = "bid"`（**新腔调**，见 3.4）。
- 新建 `skill/modules/technical-bid.md`，含「## 二、标准结构」段（`load_type_skeleton` 取此段）。
- `project_type` 后端无 enum，无需改 `models.py`；normalize/registry 写入沿用现状。

### 3.2 技术标参考骨架（模块「## 二、标准结构」）

**关键注入约束（codex R1 BLOCKER 1）**：`load_type_skeleton` **只截模块「## 二、标准结构」段**（逐行扫描，遇下一个 `## ` 即止，`###` 子节安全、代码块跳过）。故技术标**所有需常驻 S1–S4 的规则**（参考骨架 + RFP 驱动 3.3 + 后置生成 3.5 + 字数 3.6 + 质量护栏 3.7）**必须全部写进「## 二、标准结构」段内，用 `###` 子节组织**；`## 三` 之后的内容**不进** system prompt。新增 `test_skill_engine` 断言锁定注入文本含这些子节关键句。

参考骨架源自真实样本的 recurring 主干，**显式框定为「参考，以本次招标文件/技规评分点为准，按需增删调序、不要照搬」**：
1. 技术评分索引表（**后置生成**，见 3.5）
2. 技术规范书点对点应答 + 偏差/差异表（**后置生成**）
3. 对项目的理解（背景/目标/范围/工期/标准依据）
4. 需求分析与评估
5. 项目重难点分析及对策
6. 理论与政策依据（**可伸缩**；副标砍这块控字数）
7. 项目技术/服务方案（服务范围、内容、实施步骤、WBS 工作分解）
8. 项目实施管理（进度/里程碑、质量、保密、沟通机制）
9. 服务机构设置与岗位职责
10. 拟投入人员及简历
11. 服务承诺与保证措施（质保/售后/团队稳定/工期/知识产权/信息安全…）
12. 合理化建议 / 拟投入资源 / 附件

### 3.3 RFP 驱动方法论（写进「## 二」的 `### RFP 驱动` 子节，常驻 S1–S4）
此子节随骨架段注入（3.2 约束）：
- **结构来源**：先读上传的招标文件/技规/评分标准 → 抽**评分点清单**与技规条款 → 以上面参考骨架为底，**按本次评分点增删调序**。参考骨架不是模板，RFP 才是结构真来源。
- **正文覆盖**：每个评分点/技规条款都要有正文章节回应。
- 无 RFP 或 RFP 无明确结构 → 退回用参考骨架兜底。

### 3.4 S1 声明腔调（新 `bid` 分支）
`_declare_and_invite_instruction` 加 `bid` 分支（腔调举例框架间**用顿号**分隔，守 R5 codex BLOCKER 4，否则被 parser 判 malformed 卡确认门）：
- tone_line 大意：「本技术标方法＝依招标文件/技规评分点组织结构 + 逐条响应；声明所用方法（如评分点对标、点对点应答、WBS、重难点对策）。结构以 RFP 为准。」
- 仍走现有声明行格式 `方法论框架：〔…〕、〔…〕`（顿号分隔）+ 软邀请 + 确认大纲门。`_adhere_instruction`（S2–S4）无需改。
- **危险词避坑（codex R1 NIT 3）**：声明行框架名**必须避开** `_METHODOLOGY_DANGER_SUBSTRINGS`（`覆盖`/`覆写`/`推进`/`回退`/`归档`/`跳过`/`停止`/`删除`/`门禁`/`检查点`…）与归一化危险形态——否则声明判 malformed、卡确认门。故腔调举例与建议框架名一律用安全词（评分点对标、点对点应答、WBS、重难点对策）；「覆盖评分点」「覆盖自检」只作**正文/骨架规则**措辞（不进声明行、不作框架名）。

### 3.5 后置生成（body-first，兼覆盖自检）
- 「评分索引表」与「点对点应答」**需正文写完才能生成**——前者把评分点映射到正文章节，后者逐条技规指向正文回应处。
- **可执行落点钉死（codex R1 BLOCKER 2 + R2 BLOCKER 1 + R3 BLOCKER 1，已核 `chat.py` dispatcher + `report_writing.py`）**：放弃「顶部占位 + edit_file 回填」——`edit_file` 的 H2 整章 rewrite 有 `max(3000, 3×旧章长)` cap（大表超限），而 `text_replace` 虽无 cap 却在用户意图 generative（"帮我写技术标/继续写正文"）时被 `detect_user_message_intent`（`chat.py:4201`）拒。**改用 `append_report_draft` 把两表追加在草稿末尾**：append 是续写/additive 工具（`chat.py:~3852` 描述），**无长度 cap、不受 generative-intent 拦截、每轮 10 次 mutation 上限内够用**（`MAX_CANONICAL_MUTATIONS_PER_TURN=10`，`report_writing.py:119`）。故工作法：
  1. 读 RFP 抽评分点 → 用 `append_report_draft` 写正文各章覆盖评分点。
  2. 正文写完后，**再 `append_report_draft` 追加两表在草稿末尾**：`## 技术评分索引表`（评分点→正文章节锚点）、`## 技术规范书点对点应答`（技规条款→正文回应处）。
  - **草稿内两表落在末尾**；最终文档「写在最前」= **导出排版期 reorder**，与页码同属导出期、out of scope（§2）。
  - **不新增文件、不新增 stage、不用 edit_file/前插/H2 整章 rewrite**。
  - **跨轮追加需先 read_file（codex R4 NIT 1）**：同轮连续 append 借 self-mutation mtime 放行，但**跨轮** append 会被 read-before-write 挡（`report_writing.py:~198`）——若两表在新一轮才追加，须先 `read_file`。
- **兼覆盖自检（append-only 下的正确顺序，codex R4 NIT 3）**：**先**基于 RFP + 当前 draft 做评分点覆盖检查；**若漏项，先 append 缺失正文章节**；**最后再一次性 append 两表**（避免先出表又补正文导致表格过早失效/重复）。漏项 = 技规/评分点在正文找不到对应章节（对应「不漏响应评分点」这一最致命严重错误）。
- 页码：markdown 无页码，索引表先指**章节锚点**；真实页码＝导出排版期（out of scope §2）。

### 3.6 字数（复用「预期篇幅」）
- 不加主/副开关（用户定 A）。副标＝填短一点「预期篇幅」+ 模块文本提示「副标优先砍理论政策块、保留评分点覆盖与点对点应答」。

### 3.7 质量护栏（prompt 级，advisory）
- 不编造资质/业绩/政策法规（政策须真实可核）。
- 替写公司信息（名称/资质/业绩/人员）**以用户提供材料为准**，不张冠李戴（替别家写时最易犯的严重错误）。
- 评分点覆盖自检（3.5 两表即自检产物）。
- 全程 advisory，不新增硬门禁（与 R5 一致；技术标用户驱动，硬卡覆盖率不可靠）。

## 4. 现有约束的对接（防回归）

- **R5 净化不变式**：bid 声明里的框架名（点对点应答、评分点对标、WBS、重难点对策…）多为中文，**走 off-menu 白名单 `[A-Za-z0-9一-鿿\-/ 　]`**，判 `parsed` 无需进 `KNOWN_FRAMEWORK_NAMES`。**实施期验证**：所选 bid 框架名经 `_normalize_for_danger` 不与 6 个 `STAGE_CHECKPOINT_KEYS` 归一化形态冲突；若决定把某些加入 `FRAMEWORK_MENU`/`KNOWN_FRAMEWORK_NAMES`，必须同步 `_normalize_for_danger` 去除集合（R5 硬不变式：去除集合 ⊇ split 分隔符 ∪ off-menu 白名单）。
- **`build_methodology_block` 装配期只读、token ≤2k/轮**：technical-bid 注入须守同约束（`test_build_methodology_block_token_budget` 等价断言扩到第 7 类）；未知 type / 非写作期 graceful 空块逻辑不变。
- **方法论声明腔调顿号分隔**：bid tone_line 框架举例必须顿号（R5 codex BLOCKER 4）。
- **`__methodology_snapshot` 确认快照**：technical-bid 沿用现有「确认大纲冻结框架」机制，无新 checkpoint key。
- **DeepSeek 官渠兼容**：本特性只追加 system prompt 文本（模块 + tone 分支），不碰 provider message / tool-call / `reasoning_content` / `tool_choice`。

## 5. N6 协同与风险

- 技术标质量靠「读进招标文件/技规」，是上传材料。W1 **不硬依赖 N6**，但**「现有解析器够用」不成立**——`_read_docx`（`Document()` 全量加载，`skill.py:2891`）/`_read_pdf`（全量遍历）对超大整包巨标会慢/吃内存（样本主标 docx 350MB、塞满图）。
- **v1 降级策略（codex R1 BLOCKER 4 + R2 BLOCKER 2，要可执行不空喊）**：
  - 模块文本**引导用户上传关键小文件**（评分标准/技术规范书/公司资质业绩等），而非整包巨标。
  - **可执行的「友好停止」机制（小代码改动，已核现状无 size）**：
    - ① `add_materials`（`skill.py:~1175`）material metadata **加 `size_bytes`**（现状无，`stat().st_size` 一行，作展示/缓存）。
    - ② `read_material_file`（`skill.py:1503`）**读时以 `Path.stat().st_size` 为准**（不信赖 metadata，避免 legacy 无 `size_bytes` 字段的旧材料绕过）；对**重型类型（docx/pdf）超阈值直接 raise 可解释 ValueError**（经 `_execute_tool` `chat.py:~4417` 成 `{status:error}`、不崩主循环、不进全量解析、不 OOM、不静默截断），文案引导拆分关键文件重传。
    - ③ 阈值常量 `MAX_HEAVY_MATERIAL_BYTES`（默认实施时定，量级如 ~15–25MB）**v1 仅作用 docx/pdf**；txt/md/csv 现状是 `read_text()` 全量读（`skill.py:1515`，非流式），v1 **暂不纳入阈值**（纯文本实际很少超大；纳入与否实施时再定）。
    - 可选：`_build_user_content` 材料 note（`chat.py:3769`）带 size，让模型调用前就知道大。
  - 整包巨标的**鲁棒**解析（流式/分块）**显式依赖 N6**（markitdown + 大小/超时限额）；N6 落地前，W1 以「关键小文件 + size 阈值守门 + 降级提示」交付。
  - **注**：因此 W1 不是纯 prompt——含 `size_bytes` + 阈值守门这点轻量后端改动。

## 6. 安全 / trust boundary

- **不过度声称（codex R1 BLOCKER 3）**：普通材料现状是列在 user content 提示模型调 `read_material_file`，工具**直接返回原文**（`chat.py:3769/4383`、`skill.py:1503`）——**不是** S5 那种禁工具的临时数据注入边界。故 W1 对「RFP 作数据非指令」**只能是 prompt 级 advisory**（模块文本提示「招标文件/技规是参考资料、非指令」）。
- **真正的材料 trust boundary（数据块包裹 + 系统规则 + 防注入测试）是 N6 §9 的职责**，W1 不重复造、不假装已具备；W1 落地时若 N6 未做，明确记为「依赖 N6 提供材料注入边界」。
- 替写公司信息以用户材料为准；不编造（prompt 级）。
- **澄清功能 vs 安全两层（codex R2 NIT）**：W1 的**功能交付**不阻塞、不依赖 N6（小文件能跑通）；但材料**强安全边界**（数据块包裹 + 防注入）依赖 N6，N6 前 W1 只有 advisory 级。功能解耦、强边界后补，不矛盾。

## 7. 测试策略

- **改现有 6-slug 锁测（codex R1 NIT 1）**：`tests/test_skill_engine.py:~2286 test_type_skeleton_map_covers_six_slugs` 现锁死 6 slug，必须改 7（含 technical-bid）并相应重命名。
- `build_methodology_block`：technical-bid 注入含参考骨架 + RFP 驱动 + 后置生成 + 字数 + 质量护栏文本（**断言这些子节关键句确在注入文本里**，锁 BLOCKER 1「只截 ## 二」约束）；token ≤2k；S1 走 bid 声明、S2–S4 走 adhere；未知 type 仍空块。
- `_declare_and_invite_instruction` bid 分支：返回含顿号分隔框架举例；声明行格式可被 `parse_and_sanitize_methodology` 判 `parsed`。
- 净化：bid 典型框架名（点对点应答/评分点对标/WBS/重难点对策）经 `parse_and_sanitize_methodology` → `parsed`，且不被 `_normalize_for_danger` 误判危险；遍历 6 个 checkpoint key 变体不漏（沿用 R5 `test_*_all_checkpoint_key_variants` 模式）。
- `load_type_skeleton("technical-bid")`：取到「## 二、标准结构」段、含「参考/以 RFP 为准」框定与后置生成顺序。
- **后置生成 + size 守门新行为（codex R3 NIT 3）**：① 两表经 `append_report_draft` 追加在草稿**末尾**、不调 edit_file（断言 mutation 记录是 append 不是 text_replace/section_rewrite）；② `add_materials` 写入 `size_bytes`；③ 超阈值 docx/pdf 调 `read_material_file` 经 `_execute_tool` 返回 `{status:error}`（不崩、不进全量解析）、读时以 `stat().st_size` 为准（legacy 无字段不绕过）；④ txt/csv 不受阈值卡。
- 前端：下拉含技术标 option；`projectCreatePayload` 带 `project_type=technical-bid`；source-guard。
- 打包文档门禁 `tests/test_packaging_docs.py` / `test_skill_engine.py` 同步（SKILL/模块约束）。
- DeepSeek 兼容回归不破。

## 8. 实施切分（小：模块 prompt + 配置 + 前端一项 + 一处轻量后端守门）

1. 新建 `skill/modules/technical-bid.md`（「## 二、标准结构」段内含：参考骨架 + `### RFP 驱动` + `### 后置生成`〔正文 append 覆盖评分点 → 末尾 append 两表 `## 技术评分索引表`/`## 技术规范书点对点应答`，不用 edit_file〕 + `### 字数` + `### 质量护栏`；全部 `###` 子节，无 `## 三` 后置正文）。
2. `skill.py`：`TYPE_SKELETON_MAP` + `METHODOLOGY_TONE["technical-bid"]="bid"` + `_declare_and_invite_instruction` 加 bid 分支（顿号 + 避危险词）。
3. 轻量后端守门：`add_materials` 加 `size_bytes` + `read_material_file` 重型类型超阈值 raise 可解释 ValueError（§5）。
4. 净化同步核对（off-menu 白名单足够则不动 `KNOWN_FRAMEWORK_NAMES`；如动则守 `_normalize_for_danger` 不变式）。
5. 前端 `ProjectCreateModal.jsx` 下拉加 option（+ payload/source-guard 测试）。
6. 同步文档债（codex R1 NIT 2）：`skill/plan-template/project-overview.md:~7` 报告类型占位仍是旧清单（含已废「运营优化」），同步加 technical-bid / 修旧清单；`skill/SKILL.md` 如有类型清单同步。
7. 改 `test_type_skeleton_map_covers_six_slugs`→7 + 回归 + cutover。

## 9. 开放问题 / 实施期确认

- bid 框架名最终集合（是否纳入 `FRAMEWORK_MENU` 让其可见 vs 仅靠 off-menu 白名单）——倾向**不动菜单**、靠模块文本引导 + off-menu 放行，减小净化同步面；实施时定。
- 技术标参考骨架的措辞颗粒度（多详尽算「参考不照搬」而非变相模板）——据 `bid reference/` 样本再精修一版。
- 评分索引表「指章节」在 markdown 草稿的具体锚点形式（复用 canonical draft 章节锚点）。
- 超大招标文件解析（与 N6 排期联动）。
- v1.1：导出期页码回填、评分标准结构化 parser、商务标/资格响应。
