# Plan: 拆除关键词意图门槛 + 草稿版本快照（2026-07-18）

> 状态：**APPROVED**（v7；v5 经 Codex gpt-5.6-sol high 五轮审，累计修入 11 BLOCKER +
> 19 NIT，终轮含全文对抗式红队复查；v6 在开工前补齐归档态写入语义与 version id
> 契约；v7 按实施红队补齐用户手动编辑正文的同源阶段门与 UI 只读语义，2026-07-18）。基线 commit `51bfacc`
> （行号引用以此为准，实施时以符号名为锚点）。
> 实施者：Codex（后续独立会话）。本 plan 自包含，不依赖对话上下文。

## 0. 背景：真实事故还原

生产项目「猪猪侠与喜羊羊管理制度」（admin 账号）的后台会话记录显示，用户在
「把释义章节融进正文条款」这一个诉求上连续撞墙 4 次后放弃：

| 轮次 | 用户消息 | 实际发生 |
|------|---------|---------|
| 1 | 「融进去吧？双层好像很奇怪。」 | 模型理解正确，调 `edit_file` 整篇重写 → 被关键词门拦：「整篇重写需要明确用户说『整篇重写/全文重写/全部改写/推倒重来』」。模型要求用户念咒。 |
| 2 | 「整篇重写吧」 | 关键词可过，但模型这轮零工具调用，直接谎报「替换完成」（被拦后的幻觉汇报）。 |
| 3 | 「要。」（回答模型自己问的「要我现在执行吗？」） | 再拦——确认词不含关键词，门只看当轮消息，对话上下文不算数。 |
| 4 | 「全文重写。」（一字不差念咒） | 关键词门已过，挂在第二道锚点形状检查：模型把 1 万字草稿凭上下文回贴进 `old_string`，与磁盘全文差几个字节，落进 `is_structural_h1_range` 分支被拒。**该分支报错文案却仍指向关键词**（「整篇重写请明确说…」），模型如实转述「关键词过期，请再说一次」→ 死循环，对话终止。 |

另有同款：早前轮次用户说「推推推！快速！」被 non-plan write 门拦（当时大纲未确认），
门的报错文案是「请先确认大纲或明确说『继续写正文』」——模型据此要求用户
「请说一句『开始写正文』」。

三个正交问题：
1. **关键词门把「用户授权」建模为「当轮消息含咒语」**——自然确认（要。/嗯/融进去吧）
   永远过不了，模型被迫教用户念咒。
2. **锚点形状检查报错误导**——挂在形状检查却报关键词文案，把模型和用户带进死循环。
3. **模型被拦后的幻觉汇报**——门槛越多，「被拦怕了」之后不调工具直接谎报的触发面越大。

这些门存在的唯一实质理由：整篇重写是毁灭性操作（直接替换全稿），而草稿
**没有任何备份**。本方案的核心：**用可逆性换安全**——落盘前自动快照 + 恢复工具，
然后把关键词意图判定全部拆掉（代码门 + 运行时指令一起拆），意图判断交还给拥有
完整对话上下文的模型。

## 1. 目标 / 非目标

**目标**
- G1 用户任何自然语言表达（含短确认「要。」「嗯」）都能完成正文修改/整篇重写，
  全链路（代码门、报错文案、system prompt、SKILL.md）不再要求用户复述任何特定词语。
- G2 正文任何覆盖写入可逆：已有正文写前自动快照，模型可用工具恢复任意近期版本；
  首次成稿因没有写前版本，不生成空快照。
- G3 所有拦截报错文案对模型可执行、不误导：报错只描述「工具怎么用对」，
  绝不出现「需要用户明确说 XX」。

**非目标（禁改区）**
- 不重建任何形式的用户消息意图分类/义务武装（2026-07-09 已删过一套，教训在案）。
- 不加前端历史版本 UI（恢复走对话工具；后续可选增强）。
- 不动阶段状态机：stage checkpoints、`advance_stage`、S0 访谈门、evidence gate、
  fetch_url 门、`stage_checkpoints.json` 写保护全部保留。
- 不恢复三个旧 rewrite 专用工具；`append_report_draft` / `edit_file` 分工不变。
- 不改 S5 独立审查、导出链路、DeepSeek provider 序列化。

## 2. 现状机制清单（拆除对象定位）

### A. 意图正则机器（`backend/report_writing.py` L194–L325）
`_GENERATIVE_PATTERNS`、`_SECTION_MENTION_PATTERN`、`_FULL_REWRITE_PATTERNS`、
`_MODIFY_PATTERNS`、`_DRAFT_ACTION_PATTERN`、否定剥除全套
（`_NEGATION_*`、`_NEGATED_ACTION_PATTERNS`、`_remove_negated_action_clauses`）、
`detect_user_message_intent`、`user_message_requests_full_rewrite`。共 25 个
`re.compile`，约 130 行。

消费点（`backend/chat.py`）：
- L4839：`edit_file` 整篇重写咒语门（`user_message_requests_full_rewrite`）。
- L4891：`edit_file` 拒「generative 意图」（让模型改用 append）。
- L5323：`append_report_draft` 拒「modify 意图」（让模型改用 edit_file）。
- L52–53：import。

### B. 混合意图拦截 + 死代码
- `check_no_mixed_intent_in_turn`（`report_writing.py` L73–86）→
  调 `chat.py` `_secondary_action_families_in_message`（L2528，关键词族：
  「导出」/`REPORT_BODY_INSPECT_FILE_KEYWORDS`/`REPORT_BODY_INSPECT_WORD_COUNT_KEYWORDS`，
  常量在 L480–487）。调用点：`chat.py` L4875（edit_file）、L5300（append）。
- `_message_matches_priority7_inspect`（`chat.py` L2540）：全仓 grep 无调用者，死代码。
- `_phrase_hits`（L6999）：上述两处删除后归零调用，一并删。

### C. non-plan write 门的关键词部分（`backend/chat.py`）
- `_should_allow_generic_non_plan_write`（L7430 起）：先走状态分支
  （`outline_confirmed_at` 已设 且 stage ∈ `NON_PLAN_WRITE_ALLOWED_STAGE_CODES`
  → 无条件放行，这部分是好的状态机），**然后**落到关键词路径：
  `direct_write_keywords`（「开始写/写第一章/开始写正文…」18 个短语）、
  `_looks_like_follow_up_non_plan_request` + `_recent_history_allows_non_plan_write`
  （回扫历史找授权/阻止关键词）、`NON_PLAN_WRITE_FOLLOW_UP_KEYWORDS`（L491）。
- `_is_non_plan_write_approval_message` / `_is_non_plan_write_blocking_message`
  （L7519 起，授权/阻止关键词表）。
- `CANONICAL_DRAFT_STAGE_GATE_MESSAGE`（L488）：「…或明确说“继续写正文”」——念咒文案。
- 执行点：`_non_plan_write_block_reason`（L7570），仅被 `_tool_write_file` 路径消费
  （L5532）；`append/edit` 两个正文工具不经过它（各自有 inline 检查）。
- facade：`_should_allow_non_plan_write`（L7425）。

### D. `edit_file` 整篇重写锚点判定（`chat.py` L4811–4868）
三形态：`## ` 章节锚点 / 首行 H1 单行锚点 / `old_string` 与全文逐字节（或 strip）相等。
`is_structural_h1_range` 分支（以 `# ` 开头、内含 `\n## `、但不等于全文）报错文案
指向用户念咒——事故第 4 轮死循环根因。

### E. 工具描述（`chat.py` `_build_tools` L4410 起）
- `edit_file` description 未文档化「整篇重写 = old_string 只放首行 `# 标题`」约定
  （模型只能靠报错试错发现）。
- `append_report_draft` description 引用混合意图规则（「混合意图里的导出…只给下一步提示」）。

### F. 运行时指令面（Codex 首轮 BLOCKER 1 补充——只拆代码门止不住念咒）
- `skill/SKILL.md` L183：`edit_file` 行写明「整篇重写…+ 用户明确要求"整篇/推倒/
  全文重写"」——经 `get_skill_prompt()` 注入 system prompt，模型会照办念咒。
- 同文件「一轮内 ≤ 3 次 canonical write」与代码 `MAX_CANONICAL_MUTATIONS_PER_TURN = 10`
  不一致（陈旧文案，顺带对齐）。
- `chat.py` `_build_system_prompt`（L6910 起）：
  - `turn_rule` 阻塞分支：「在用户明确确认大纲或**明确要求继续正文**前，禁止写正文…」
    ——改纯状态门后这成为错误指引（状态门不再被任何「明确要求」解锁）。
  - `draft_rule_block` 末行：「mixed-intent 的导出/质量检查/看文件/看字数只给下一步
    提示，不要同轮执行」——D2 删代码门后需同步删除。
- `_tool_write_file` 的 system notice `user_action`（L5539）：「请先让用户确认大纲或
  **明确要求继续正文**后…」——同款念咒指引。

### 保留不动的好门（实施时的禁改锚点）
- `check_read_before_write_canonical_draft`（防盲写 + mtime 一致性）。
- `check_no_prior_canonical_mutation_in_turn`（`MAX_CANONICAL_MUTATIONS_PER_TURN = 10`）。
- `check_report_writing_stage`（S4+）、`check_outline_confirmed`。
- `check_no_fetch_url_pending`、S0 访谈门（`s0_write_blocked`）、evidence gate。
- `_build_canonical_draft_write_file_block_message`（`write_file` 禁写正文、重定向到
  append/edit）。
- full_rewrite / section_rewrite 的 `new_string` 形状检查与长度 cap（L4900–4924）。

## 3. 设计

### D1 删除意图正则机器（子系统 A）
- 删 `report_writing.py` L194–L325 全部正则与
  `detect_user_message_intent` / `user_message_requests_full_rewrite` /
  `_remove_negated_action_clauses`。
- 删 `chat.py` 三个消费块（L4839 咒语门、L4891、L5323）及 import。
- 效果：整篇重写不再需要用户当轮消息含关键词；append↔edit 工具选择完全交给模型，
  由工具描述给指导（见 D6），错用时的兜底是快照（D4）。

### D2 删除混合意图拦截与死代码（子系统 B）
- 删 `check_no_mixed_intent_in_turn`（含 `report_writing.py` 内定义、`chat.py`
  L4875/L5300 两个调用、imports）。
- 删 `_secondary_action_families_in_message`、`_message_matches_priority7_inspect`、
  `REPORT_BODY_INSPECT_FILE_KEYWORDS`、`REPORT_BODY_INSPECT_WORD_COUNT_KEYWORDS`、
  `_phrase_hits` 及其专属常量 `_NEGATION_RE`、`_NEGATION_WINDOW_CHARS`
  （chat.py L509–510，仅 `_phrase_hits` L7006–7007 使用；删前 grep 确认无其余
  消费点）。**误伤警戒**：`_STAGE_ADVANCE_CLAIM_NEGATION_RE`（L300）属阶段推进
  声明子系统，与本批无关，禁止删除。
- 同步删运行时指令面的对应句（子系统 F）：`append_report_draft` description 混合
  意图句、`draft_rule_block` 的 mixed-intent 行。

### D3 non-plan write 门改纯状态（子系统 C + F）
- `_should_allow_generic_non_plan_write` 重写为纯状态判定，同时把
  `NON_PLAN_WRITE_ALLOWED_STAGE_CODES` 收窄为 `{S4, S5, S6, S7}`：
  `outline_confirmed_at` ∈ checkpoints **且** stage ∈ `{S4, S5, S6, S7}`
  → True，否则 False。`done` 是已归档只读态，必须先撤销 `delivery_archived_at` 回到 S7
  才能修改，避免正文变化后仍显示“已归档”。不再读 `user_message`（参数保留与否由实施者定，改签名则同步
  L7071 调用点与 facade L7425 及其测试）。
- 删 `direct_write_keywords`、`NON_PLAN_WRITE_FOLLOW_UP_KEYWORDS`、
  `_looks_like_follow_up_non_plan_request`、`_recent_history_allows_non_plan_write`、
  `_is_non_plan_write_approval_message`、`_is_non_plan_write_blocking_message`、
  `_has_existing_report_draft`（仅 follow-up 路径 L7486 使用，随之归零调用）。
  同步更新陈旧注释：`_build_turn_context` 内「意图分类仅保留工具路由与全篇重写
  解锁两个窄消费方」段（2026-07-09 注释，本批后不再成立）与 facade
  `_should_allow_non_plan_write` 的说明注释。
- 文案三处同步重写（全部去念咒化，改为**按真实状态动态指引**——Codex 二轮
  BLOCKER 3 + 三轮 BLOCKER 1：确认大纲不必然进入 S4；且「大纲未确认」还细分
  S0（访谈未完成、大纲可能不存在）与 S1（大纲还没生成），统一指向「去确认大纲」
  会要求用户确认一个不存在的文件，同样是状态死路）：
  - 新增单一 **state-based helper**（落在 `SkillEngine`，`ChatHandler` 保留
    `_non_plan_write_block_guidance(project_id)` 兼容 facade，
    `report_writing.check_report_writing_stage()` 改为同一 helper 的薄 facade），
    按 stage + checkpoint 四路分发，**同时是权限与文案的单一判定源**：
    helper 返回 `None` 当且仅当放行（outline checkpoint 存在且 stage ∈ S4–S7），
    `_should_allow_generic_non_plan_write` 直接派生为 `helper(...) is None`——
    避免权限函数与文案函数在异常状态（如 stage 推到 S4 但 checkpoint 缺失）下
    一拒一放。三处文案面全部共用它：
    a) S0（访谈未完成）→「请先完成需求访谈/当前澄清，再进入后续阶段。」
    b) S1（大纲未形成或未确认）→「请先形成大纲；大纲就绪后在右侧工作区确认。」
    c) S2/S3（大纲已确认但未到 S4）→「正文与正式内容仅在 S4 及之后可写。请先
    完成当前研究/分析阶段的要求，推进到 S4 后再写。」
    d) S4–S7 → 放行（无文案）。
    e) done →「项目已归档。需要修改正文时，请先撤销交付归档，回到 S7 后再修改。」
    文案只描述真实下一步动作，不含任何反向声明句（如「不要要求用户复述…」
    ——那是实施纪律，进 CLAUDE.md，不进用户/模型可见文案）。具体措辞实施可
    微调，禁改语义：每个状态只指向该状态下真实可执行的下一步。
  - 五个消费面：`CANONICAL_DRAFT_STAGE_GATE_MESSAGE`（常量退役，改调 helper）、
    `_build_system_prompt` `turn_rule` 阻塞分支（删「或明确要求继续正文」）、
    `_tool_write_file` non-plan notice `user_action`（L5539）、HTTP `user_write_file`
    阶段拒绝、workspace list/read 的 `editable`。用户手动编辑正文不得绕过状态门；
    S0–S3/done 返回只读，done 的 403 明确提示先撤销归档回 S7。
  - 测试矩阵覆盖 S0 / S1 / S2 / S3 / S4 / done 六态，逐态断言文案。
- `_build_system_prompt` 补一条通用写入纪律（语义纪律，非关键词门）：
  「用户明确要求暂停、先不要写入或先讨论时，本轮不要调用任何写工具。」——补偿
  「先别写正文」阻止词表的删除。
- 行为变化（接受）：
  - S2/S3 通过「开始写/写第一章」等短语提前解锁 generic 非 plan 写入的路径消失。
    正文两工具本就被 `check_report_writing_stage` 限 S4+，此路径实际只影响极少数
    非草稿 content/ 写入，属可接受收敛（且更贴合 CLAUDE.md 的 S4 正文边界）。
  - 「先别写正文」类阻止词不再硬拦，由上面的系统提示纪律句 + 模型语义遵守。

### D4 草稿版本快照（新安全网）
- 新增 `SkillEngine._snapshot_report_draft(project_path) -> Path | None`
  （返回新建快照路径；draft 不存在时返回 None）：
  - 若 `REPORT_DRAFT_PATH` 存在：`read_bytes` 全文 → 原子写入
    `content/.draft_history/report_draft_v1.<UTC 时间戳 YYYYMMDDTHHMMSSffffff>.md`
    （同目录 temp + `os.replace`、`write_bytes`；同名冲突加**固定宽度补零序号**
    后缀（如 `-01`），prune/列表排序按解析后的 `(timestamp, sequence)` 元组而非
    裸字符串（防 `.10` 排到 `.9` 之前）；文件名无冒号，Windows 兼容）。
    **字节级复制**，不经 `read_text/write_text`，换行与编码逐字节保真
    （恢复语义 = 字节相等）。
  - 快照失败（OSError 等）**fail-closed**：向上抛出，阻止本次主写入——「可逆」是
    本方案卖出的不变式，不能静默降级。
- **写入事务时序（Codex 一轮 BLOCKER 5 + 二轮 BLOCKER 2）**：
  1. 快照创建（fail-closed：失败抛出、主写不发生）；
  2. 主文件 `os.replace`；
  3. **主写失败** → best-effort 删除第 1 步新建的那份快照（用返回的 Path），
     然后重新抛出**原始异常**；回滚删除自身失败只记日志，绝不覆盖/吞掉主写异常。
     效果：正常回滚下失败写入不留痕、不堆积重复快照、不洗真历史；回滚清理
     持续失败时允许临时超限（每次至多多一份），由后续成功写入的 prune 收敛；
  4. **主写成功** → prune 按文件名倒序保留最近 **40** 份。prune 是 best-effort：
     失败只记日志，不得把已成功的主写伪装成失败。禁止在主写之前 prune。
- 挂载点（choke points，覆盖所有写入者）：
  - `SkillEngine.write_file`：normalized 路径与 `REPORT_DRAFT_PATH` 经 casefold
    规范比较（复用项目既有 `_canonical_user_path` 语义，Windows 大小写不敏感）。
    **命中后必须先把写入目标归一：`normalized_path = self.REPORT_DRAFT_PATH`，
    再解析 `full_path`、快照、写入、返回与持久化 metadata**（Codex 四轮
    BLOCKER：只识别不归一时，macOS/Linux 大小写敏感文件系统上 mixed-case 入参
    会「为 canonical 草稿建快照、却把内容写进第二个文件」——canonical 草稿没更新
    还报成功）。目标已存在时 replace 前先快照。覆盖 append/edit 工具与 restore 自身。
  - `SkillEngine.user_write_file`：同条件同处理，且**必须先完成既有路径白名单、
    存在性与 `base_mtime_ns` CAS 校验，全部通过后才创建快照**（Codex 三轮
    NIT 1）——stale 409 不得产生快照。覆盖 workspace 用户手动保存。
  - 首次成稿（文件不存在）不产生快照。
- 泄漏防护：
  - `list_workspace_files`（`rglob("*.md")` 会扫到）跳过 `content/.draft_history/`
    前缀，测试锁住。
  - 导出/审查/字数统计只读固定路径（`REPORT_DRAFT_PATH` + 引用 assets）。已 grep
    确认 `backend/` 无其它 content/ 目录级扫描（仅 `list_workspace_files` 一处
    rglob）；实施时复查一遍。
- 容量：**目标**保留 40 份（prune 成功后数量有界；prune 失败允许暂时超限，
  由后续成功写入的 prune 重试收敛）。单份大小随草稿增长，典型 ~50KB、合计
  ~2MB 量级，无字节级预算——接受。

### D5 新工具 `restore_report_draft`
- schema（`_build_tools`）：
  ```json
  {"name": "restore_report_draft",
   "parameters": {"type": "object",
     "properties": {"version_id": {"type": "string",
       "description": "要恢复的历史版本 id（不带参数调用可先获取版本列表）"}},
     "required": []}}
  ```
- **结果协议（Codex BLOCKER 2）**：前后端只认 `status == "success"`（SSE 映射、
  持久化 tool event、pill 渲染均如此，`chat.py` L1647/L1699/L1754）。因此：
  - 无 `version_id` → `{"status": "success", "action": "list", "mutated": false,
    "versions": [{id, utc_ts, word_count, first_line}, ...]}`，不落盘、不计 mutation。
    **坏版本隔离（Codex 三轮 BLOCKER 2 + 四轮 NIT 3）**：列表为每个版本算
    `word_count`/`first_line` 时逐文件隔离 **UnicodeDecodeError 与 OSError**
    （文件消失、权限变化等）——异常版本仍返回安全元数据
    `{id, utc_ts, readable: false}`（不含正文/标题/字数），列表整体必须成功；
    `first_line` 截断至 120 字符（防超长无换行正文把 40 份列表撑爆工具结果）；
    选中 `readable:false` 的版本恢复时按 D5 既有规则主写前友好拒绝。
    一个损坏/失踪的快照绝不能拖垮整套安全网。
  - 有 `version_id` → 校验通过后恢复 → `{"status": "success", "action": "restore",
    "mutated": true, "path": REPORT_DRAFT_PATH, ...}`。
  - 禁用任何非 success/error 的 status 值。
- **version id 契约**：快照文件名为
  `report_draft_v1.<version_id>.md`；`version_id` 精确定义为 UTC 时间戳 token
  `YYYYMMDDTHHMMSSffffff`，碰撞时带固定宽度序号 `-NN`（例如
  `20260718T163015123456-01`），不含文件名前缀与 `.md`。列表按解析后的
  `(timestamp, sequence)` **从新到旧**返回，`utc_ts` 是相应 UTC 时间的 ISO-8601
  字符串。枚举、prune、list、restore 共用一个文件名解析 helper，禁止各自实现正则。
- **version_id 安全（Codex BLOCKER 4 + 二轮 NIT 3）**：工具参数是不可信输入。
  禁止用 `history_dir / f"{version_id}.md"` 拼路径；必须先枚举 `.draft_history`
  内合法快照文件构建 `{version_id: resolved_path}` 映射，仅精确 key 命中才恢复；
  未命中给友好错 + 可用列表。枚举只接受**匹配快照文件名规范的普通文件**：
  拒绝 symlink，且 resolved path 必须仍位于 `.draft_history` 目录内。
  测试 `../`、绝对路径、反斜杠、多余扩展名、未知 id、symlink。
- **写入账本全接入（Codex BLOCKER 3）**——restore 必须被系统当成一次真实的
  canonical draft 写入，共四条链路：
  1. `canonical_draft_mutations`：完整 entry
     `{tool: "restore_report_draft", path: REPORT_DRAFT_PATH,
       canonical_action: "restore", target_label: version_id,
       old_len, new_len, mtime_after, ts}`（字段齐全，mutation-cap 摘要与审计可读）。
  2. `_extract_successful_write_event`（L2152 起，现只认 append/write/edit）：
     增识 `restore_report_draft` 且 `mutated == true` → canonical draft write event，
     否则 turn-end 对账会判「没有真实写入」并要求模型重调工具（可能反复恢复）。
  3. `_current_turn_successful_tool_source_keys`（L3877 起）：同样增识 restore，
     保证当前轮 workspace memory 去重成立。
  4. `_build_tool_persistence_metadata`（L1382 起）：restore 成功后以草稿文件
     memory 语义持久化**恢复后的全文**（复用 `write_file` 的 metadata 分支或等价
     实现），否则 conversation memory 里仍是恢复前正文，压缩后会把旧版本重新注入。
- 门槛：`check_report_writing_stage`（S4–S7；`done` 按 D3 保持只读）+
  `check_no_prior_canonical_mutation_in_turn`
  （计入每轮 10 次上限）。**不要求** read-before-write（恢复即全量替换，其意义就是
  救回；恢复后 mtime 变化由 mutation entry 的 `mtime_after` 覆盖同轮续改场景）。
  不叠 fetch-url gate（S4 已隐含大纲 checkpoint，恢复不引入外部信息）。
- **恢复写入走 bytes 通道（Codex 二轮 BLOCKER 1，硬性规定，无条件分支）**：
  现有 `SkillEngine.write_file` 用 `Path.write_text`，Windows 换行转换会破坏
  CRLF/混合换行草稿的逐字节恢复（macOS 测试过、Windows 回归锁不住）。因此
  restore 必须走字节写入：`write_file(..., content: str | bytes)`（bytes 分支
  `write_bytes`），或私有 `_write_file_bytes`——两种实现都必须复用同一套
  路径校验、写前快照、原子 temp+`os.replace`、写后 prune（不得旁路 choke point）。
  restore 取快照 `read_bytes` 原样写入；恢复后的 memory 持久化与字数统计对该
  字节串做**严格 UTF-8 decode**，decode 失败在主写发生前友好拒绝
  （不落盘、不计 mutation）。
- **错误通道统一（Codex NIT 3）**：`chat.py` L5127 附近的写工具异常处理只为
  `write_file`/`edit_file` 发 write-blocked notice。将四个 canonical mutation 工具
  （append / edit / write / restore）统一纳入：返回净化后的可行动错误，不裸露
  底层 OSError 文案。
- description 写明：仅在用户要求恢复/撤销正文改动时使用。
- 前端：pill 对未知工具名走通用渲染，验证 `restore_report_draft` 显示不破版；
  可选增强（NIT 7，非必须）：在 `workspaceFileLinks.js` 为 restore 特判映射到
  正文文件路径，让用户恢复后可直接点开正文。
- 工具参数遵守既有约定：先校验 object、camelCase→snake_case 容错、直接索引前给
  友好错误。

### D6 `edit_file` 锚点报错与工具/SKILL 描述重写
- 锚点判定三形态保留不变（`## ` 章节 / 首行 H1 单行 / 全文 strip 相等容错）。
- `is_structural_h1_range` 分支报错文案改为（面向模型、可直接执行）：
  「old_string 覆盖了从 # 标题开始的多章节范围，无法安全匹配。整篇重写：把
  old_string 只设为草稿第一行 `# 标题`（单独一行），new_string 放完整新稿。
  局部修改：用 `## 章节标题` 锚点或唯一文字片段。」
- 全文唯一性失败（`count != 1`）报错同步补一句「可先 read_file 获取当前精确原文」。
- `_build_tools` 中 `edit_file` description 补充整篇重写锚点约定；`append_report_draft`
  description 改为纯指导（首稿/续写用 append，改已有内容用 edit_file——guidance，
  不再有 gate 兜着）。
- `skill/SKILL.md` 同步（子系统 F）：
  - `edit_file` 行删去「+ 用户明确要求"整篇/推倒/全文重写"」，改为
    「整篇重写（`old_string` 只放 draft 第一行 h1）」。
  - 「一轮内 ≤ 3 次 canonical write」改为与代码一致（≤ 10，或直接删数字、
    引用后端上限为准）。
  - 补一行 restore 工具的使用说明（仅用户要求恢复时用）。
- 全链路检查：删除/改写后的**所有 gate 返回文案、工具 description、
  `_build_system_prompt` 产出、`skill/SKILL.md`** 中不得出现念咒句式。
  **禁用句式唯一清单**（D6 与测试计划共用此清单，单一真值，Codex 二轮 NIT 2）：
  「明确说」「请说一句」「明确要求继续正文」「再说一次」「复述」。
  文案自身也不得包含反向声明（如「不要要求用户复述…」）——实施纪律进
  CLAUDE.md，不进运行时文案。收窄为对上述四个产出面的精确清单断言，
  不做 backend/ 全量宽泛 grep（避免误伤「不要逐字复述报告」等合法指令——
  该类指令若存在于上述四个面内，改写为不含「复述」的等价表达）。

### D7 文档
- 项目 `CLAUDE.md`：
  - 更新「S4 正文唯一文件」段：补快照与 restore 不变式（choke point 在
    `SkillEngine.write_file`/`user_write_file`、快照 fail-closed、prune 在主写
    成功后 best-effort、40 份轮转、`.draft_history` 不进 workspace 列表、
    restore 必须接入全部四条写入账本）。
  - 新增硬约束：禁止重建基于用户消息关键词的意图分类/授权门；gate 文案、
    system prompt、SKILL.md 不得要求用户复述特定词语。
- `docs/architecture.md` 对应章节同步（正文写作工具、快照机制、restore 账本接入）。
- `docs/current-worklist.md` 收口本批次。

## 4. 测试计划

- **删改**：`tests/test_report_writing.py`（意图分类器用例 ~28 处引用）、
  `tests/test_chat_runtime.py`（83 处涉及 整篇重写/开始写正文/can_write_non_plan/
  mixed_intent 的场景夹具）、`tests/test_chat_context.py`（涉及处）。原则：删除
  「关键词才放行」类断言，改写为「无关键词也放行」+「状态门仍拦」。
- **新增（快照）**：首写不快照；二写快照且快照字节等于写前版本（bytes 比较）；
  41 份触发 prune 至 40；快照 OSError 时主写入失败（fail-closed）；
  **主写失败回滚两连**（预置 40 份 + mock 主写 `os.replace` 失败）：
  a) 正常回滚——新建快照被删除，历史集合与写前完全一致；
  b) 回滚删除也失败（mock unlink 抛错）——所有旧历史仍在，至多多一份新快照，
  且上抛的是主写原始异常；`user_write_file` 路径同样快照，且 **stale
  `base_mtime_ns`（409）时不产生快照**；**casefold 命中即归一写入目标**
  （`content/Report_Draft_V1.MD` 变体入参 → 断言 canonical 小写文件内容已更新、
  mixed-case 第二文件不存在、返回路径为 canonical path）；prune 失败不影响主写
  成功返回；碰撞后缀测试 **mock 时间戳生成器返回完全相同的
  `YYYYMMDDTHHMMSSffffff`**（文件名含微秒，不冻结时间不会触发后缀）构造 ≥10 份，
  断言 `-01`…`-10` 补零与 `(timestamp, sequence)` 排序；
  `list_workspace_files` 不含 `.draft_history`。
- **新增（restore）**：无参返回 `status:"success"` + `action:"list"`（断言 SSE/
  pill 映射为成功）；**列表坏版本隔离**（一个非法 UTF-8 快照 + 两个正常快照 →
  列表成功、坏版本 `readable:false`、正常版本仍可恢复）；恢复成功且恢复前当前版
  已入历史；恢复后草稿字节等于快照，且**断言走 bytes 写入分支**（spy/guard：
  restore 调用 bytes writer、全程未调用 `Path.write_text`——CRLF 字节等值测试在
  macOS 上锁不住错误的 text 分支）；
  坏 version_id 友好错含可用列表；路径穿越六件套（`../` / 绝对路径 / 反斜杠 /
  多余扩展名 / 未知 id / symlink）全拒；快照字节非法 UTF-8 时主写前友好拒绝
  （不落盘、不计 mutation）；S0–S3 拒；计入 mutation cap；恢复后同轮可继续 edit；
  **turn-end 对账**：流式与非流式下 restore 成功 + 模型宣称已恢复 → 不触发
  missing-write 重调；conversation memory 持久化的是恢复后全文（压缩注入场景）。
- **改写（edit_file）**：无任何关键词的用户消息下 full_rewrite 放行（首行 H1 锚点）；
  structural range 新文案断言；append 与 edit 不再因意图互拦。
- **改写（non-plan 门）**：state-based helper 允许/拒绝矩阵**六态全覆盖**
  （S0 访谈 / S1 大纲未就绪 / S2 / S3 / S4–S7 / done），逐态断言对应文案：S0 指向完成
  访谈、S1 指向形成并确认大纲、S2/S3 指向推进阶段（不得指向已完成的大纲确认）、
  S4–S7 放行、done 指向先撤销交付归档；三个消费面（gate 常量、turn_rule、
  notice user_action、HTTP 手动保存、workspace list/read `editable`）共用 helper 的一致性
  断言；API 矩阵覆盖 S0/S1/S2/S3/S4/S5/S6/S7/done，确保界面编辑入口与后端拒写同步。
- **文案卫生（收窄版）**：对以下产出面断言 D6 唯一禁用清单零命中：全部 gate 返回
  文案常量、`_build_tools` descriptions、`_build_system_prompt` 组装结果、
  `skill/SKILL.md` 内容。清单与 D6 同源（「明确说」「请说一句」
  「明确要求继续正文」「再说一次」「复述」），不做 backend/ 全量宽泛 grep。
- **回归**：`.venv/bin/python -m pytest -q tests/` 全绿；
  `cd frontend && node --test tests/ && npm run build`；工具 schema 变更（新增 restore）
  → 跑 DeepSeek targeted tests。

**场景重放验收**（对照事故四连拦，写成集成测试）：
1. 用户消息「融进去吧？双层好像很奇怪。」→ edit_file 首行 H1 锚点整篇重写：放行。
2. 用户消息「要。」→ 同上：放行。
3. 用户消息「全文重写。」+ old_string 整篇回贴（与磁盘差字节）→ 拒，但报错为
   新锚点指引文案（不含「明确说」）。
4. 整篇重写后 `restore_report_draft` 恢复上一版：内容字节级等于重写前。

## 5. 风险与缓解

| 风险 | 缓解 |
|------|------|
| R1 模型自主整篇重写误伤（无门后） | 快照+restore 把代价从「毁稿」降为一句话恢复；read-before-write、每轮 10 次 cap、`new_string` 形状/长度 cap 全保留 |
| R2 「先别写正文」不再硬拦 | system prompt 通用写入纪律句（D3）+ 模型语义遵守；实测仍回归再加强提示，不回代码门 |
| R3 测试改动面大（test_chat_runtime 83 处） | 按 T1–T4 分任务推进，每步全量绿再进下一步 |
| R4 `.draft_history` 泄漏进 UI/导出 | 列表过滤 + 导出固定路径确认，测试锁住 |
| R5 快照/恢复的失败路径 | 快照 fail-closed、prune best-effort 且后置；四个 canonical 写工具统一 write-blocked notice 与净化报错（NIT 3） |
| R6 restore 绕过账本造成 turn-end 重调/记忆回灌 | D5 四条账本链路全接入 + 流式/非流式对账测试锁住 |

## 6. 实施顺序（4 个 task，每个自带测试先行）

- **T1** D4 快照 + D5 restore（先建安全网；含账本四链路接入与错误通道统一，
  行为纯新增 + 两处既有函数增识 restore，零删除）。
- **T2** D1 + D2 + D6（删意图正则/混合意图/死代码，改锚点报错、工具描述与
  SKILL.md，含文案卫生测试）。
- **T3** D3（non-plan 门改纯状态 + 三处文案重写 + system prompt 纪律句）。
- **T4** D7 文档 + 全量回归 + 场景重放验收。

依赖：T2/T3 依赖 T1（安全网先行）；T2 与 T3 无相互依赖但建议串行（同文件
`chat.py` 冲突面大）。
