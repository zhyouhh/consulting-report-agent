# Batch 3 Cutover Report — R4 来源可信度标注 + R5 方法论路由接回与显性化（2026-06-11）

> 领导评审反馈整改簇「批 3」落地。subagent-driven-development + codex 双轨/红队**逐 task** 审到 APPROVED。
> 分支 `feat/batch3-source-credibility-and-methodology`，从含 plan commit 的 main 切出。
> spec：`specs/2026-06-10-r4-source-credibility-annotation-design.md`（R4）+ `specs/2026-06-10-r5-methodology-routing-and-visibility-design.md`（R5）。
> plan：`plans/2026-06-10-batch3-source-credibility-and-methodology-routing.md`（11 task，codex 三轮 APPROVED）。

## 1. 概览

| | 范围 | 形态 | review |
|---|---|---|---|
| **R4** | 给每条资料一个内置三档可信度信号 | 纯 prompt 改 `skill/SKILL.md` S2 段 + 1 守护测试 | codex 单轨 APPROVED |
| **R5** | 把失效的「报告类型→方法论框架」路由以 app 沙箱能跑的方式接回后端注入 + 大纲显性化声明 + S1 软确认/可换 | 后端（skill.py/chat.py）+ 前端近零改 + SKILL.md + 删死码 | 逐 task codex 双轨/红队 APPROVED |

**现状根因（R5）**：方法论路由在 canonical skill 里本设计过（模型 `read_file` 自取 modules），但嵌进 app 后断了——`get_skill_prompt` 无类型分支、`read_file` 锁工作区够不到 skill 目录、`get_template` 死代码，17 模块 16 个从不加载。R5 改为**代码注入（push）**。

## 2. 提交序列（A1 + B1–B10）

| task | commit | 内容 | review |
|---|---|---|---|
| A1 (R4) | `584abb6` | 三档(🟢高/🟡中高/⚪其他·按机构性质) + data-log 色点 + S2 分布小结(advisory) + marker 守护测试 | 单轨 APPROVED |
| B1 | `2f47a4d` | 删死码 `get_template()` + `skill/templates/`（4 文件） + repo-wide source-guard | 单轨 APPROVED |
| B2 | `5e26607` | `TYPE_SKELETON_MAP` + `load_type_skeleton`（提「## 二、标准结构」+ 跳代码块 + 缺锚点 fail-closed） + `FRAMEWORK_MENU` | 双轨（quality 挖 unclosed-fence fail-open → 修） |
| B3 | `e6b4d8a` | `parse_and_sanitize_methodology` 三态净化（parsed/missing/malformed）— **trust boundary 安全核心** | 双轨 + 红队 **5 轮** APPROVED |
| B4 | `681a085` | `__methodology_snapshot` 快照持久化（确认时冻结、非 checkpoint key、cascade 条件保留） | 双轨 + 红队 3 轮 APPROVED |
| B5 | `996137b` | 确认门方法论声明前置（仅首次确认 + known 6-slug，**不进持久完成态**，legacy 不规退） | spec/quality(xhigh) + 红队(high) APPROVED |
| B6 | `facdb44` | `build_methodology_block` 装配（stage 闸 S1–S4 + unknown graceful 空块 + 三腔调 + token≤2k） | 双轨 APPROVED |
| B7 | `e0b4ae8` | `chat.py:_build_system_prompt` 接入 + `methodology_declared` flag + S1 完成项镜像 | 双轨 + 红队 APPROVED |
| B8 | `09a01f5` | SKILL.md「路由与模块」段（失效 read_file modules）改为「系统按类型注入」说明 + S1 声明 bullet | 单轨 APPROVED |
| B9 | `b3467a5` | 前端 S1 确认按钮 gate `methodology_declared` + `s1ConfirmDisabledReason`（向后兼容 `?? true`） | 双轨 APPROVED |
| B10 | (本 commit) | 回归 + cutover + worklist/CLAUDE.md 同步 + B8/B9 NIT 守护测试加固 | — |

## 3. 红队挖的关键 BLOCKER（审实现的价值，全过现实闸门）

plan 是**文档级** codex 三轮 APPROVED，但实现级 trust boundary/并发洞由红队**审实现 + 对抗攻击**挖出：

- **B3（5 轮）**：① 危险词在剥括号/截断**之后**才查（违反 spec §11「不剥」）→ 改对完整 raw_value 先行检测；② 声明跨行 + 非顶部解析 → `[^\S\n]` 不吃换行 + 限顶部首个 `##` 前；③ off-menu ≤20 中文太宽可塞自然语言越权指令 → 扩中文阶段操控词 + 容空格修「麦肯锡 7S」误杀；④ **分隔符绕过**（`advance stage`/`write file`/`advance-stage`/`write、file` 用空格/连字符/顿号/逗号替工具名下划线）→ 归一化检测（NFKC+casefold+去 `[\s\-_/.·、,，]`）；⑤ 漏 `s0_interview_done_at` checkpoint + 零宽 Cf 字符 → 补全 + 删 Cf + 动态守护测试遍历 `STAGE_CHECKPOINT_KEYS` 防再漏。**不变式**：归一化去除集合 ⊇ split 分隔符 ∪ off-menu 白名单非字母数字字符。
- **B4**：① `_get_project_type_for_path` 坏 registry 记录抛 KeyError + 两阶段写半提交永久跳过 → `.get` 防 KeyError + 用「当前快照状态」判定取代 was_confirmed（失败可自愈补写）；② 我 v2 引入的**固定 temp 名竞态** → `tempfile.mkstemp` 唯一 temp；③ backfill 无锁覆盖 snapshot → 写前重读 PRESERVED 纵深防御（确认大纲那刻 backfill `changed=False`，实际窗口窄）。
- **B5**：crash-consistency（崩溃在两次 raw 写之间留「已确认无快照」）→ **降级 follow-up**：残留 = 与 legacy 项目一致的 spec §4.2 missing 兜底（非越权/损坏/规退）+ B4 自愈覆盖「用户没动 outline」+ 触发极罕见。红队 high 修正了「无永久丢失」论断（crash + 用户改坏声明会丢），但同意危害仅 missing 兜底、非 BLOCKER。
- **B7**：DeepSeek 兼容 / 注入 trust / flag 死锁 / 阶段回归 / S0 注入 **5 攻击面全未打穿**——`_build_system_prompt` 只追加文本、`tool_choice`/`reasoning_content` 路径未动、注入内容全经上游净化、flag fail-open、完成项 display-only。

## 4. 测试基线（2026-06-11 全回归）

| 套件 | 结果 |
|---|---|
| `tests/test_skill_engine.py` | **210 passed**（baseline 155 + R4/R5 新增 55） |
| `tests/test_packaging_docs.py` | **14 passed** |
| `tests/test_chat_runtime.py`（targeted：装配 + DeepSeek 兼容 + tool_call，遵守 [[feedback-skip-full-chat-runtime]] 不跑全量） | **12 passed**（DeepSeek `tool_choice`/`reasoning_content`/null 剥离用例不回归） |
| 前端 `node --test tests/`（全量） | **299 pass** |
| 前端 `npm run build` | **成功**（既有 chunk warning 非新增） |
| 打包侧 | `consulting_report.spec` 无 `skill/templates` 残留引用；`skill/templates/` 已删（B1） |

## 5. Follow-up（非阻塞，已记 `docs/current-worklist.md` R5 条目）

合并为「stage_checkpoints 写事务性强化」，桌面单用户低优先级：
1. **checkpoint 写事务化**：`record_stage_checkpoint` set 的 `outline_confirmed_at` + `__methodology_snapshot` 两阶段写改一次原子 raw 写（消除 crash 半提交，危害仅退 missing 兜底、非 BLOCKER）。
2. **backfill 窄粒度锁/CAS**：`_backfill_stage_checkpoints_if_missing` 无锁与 record 并发理论 TOCTOU（pre-existing；已加写前重读 PRESERVED 纵深防御；不用 `request_lock` 避卡 summary 高频路径）。
3. backfill PRESERVED 合并「有则覆盖」不 pop（latest 已删 snapshot 时不复活，仅损坏 raw 边界）。

## 6. 仍需用户手工（非阻塞）

- **R4 人工验收**（spec R4 §8 矩阵）：真实 S2 验 7 类来源色点正确（政府🟢/权威媒体🟡/企业官网⚪不误报/material⚪/访谈⚪行首计数/调研⚪行首计数/个人博客⚪+风险括注）+ S2 小结先分布后点名 + 色点编辑态/预览态可读。
- **R5 GUI E2E**（spec R5 §11，8 项）：① S1 模型在 outline 顶部写「方法论框架：…」+ 软邀请；② 确认按钮缺声明禁用+提示、补上可点；③ 确认后 S2 正文用对已选框架；④ S1 聊天「换成 BCG」→ 改声明重确认→快照更新；⑤ management-document 腔调是「章-条-款-项」非 SWOT；⑥ legacy 项目（R5 前确认无声明）不被拉回 S1；⑦ unknown type 不被门卡死；⑧ 复杂图维持脚本交付现状。
- **重打干净包**验证打包态（`.venv` PyInstaller，非 Anaconda）。
- **A/B 验证建议**（spec R5 §12，独立非阻塞）：同选题「裸跑 vs 注入骨架+菜单」比正文方法论质量；几乎无差则缩为「仅声明 + 删死模块」。

## 7. 关键约束（维护者必读）

- `__methodology_snapshot` **绝不**进 `STAGE_CHECKPOINT_KEYS`/`_CASCADE_ORDER`（有 invariant assert，加即炸）；后端写、模型不能直写、非新 checkpoint key；`_load_stage_checkpoints` 不返回它 → 天然不外泄前端 checkpoint 字段（值非机密，模型 B6 本就收到）。
- 确认门方法论前置**只在 transition 分支内联**（仅首次确认 + known 6-slug），**绝不**进 `_stage_one_completion_state`（否则 legacy 已确认无声明项目被拉回 S1）。
- `build_methodology_block` 装配期**只读**；unknown type graceful 空块不抛进 chat 链路。
- B3 净化的**不变式**：`_normalize_for_danger` 去除集合必须 ⊇ `parse` 的 split 分隔符（`、,，`）∪ off-menu 白名单 `[A-Za-z0-9一-鿿\-/ 　]` 允许的非字母数字字符；改 off-menu 白名单或 split 分隔符时必须同步。
- DeepSeek 官渠兼容：方法论注入只给 system prompt **追加文本**，不碰 provider message / tool-call / `reasoning_content` / `tool_choice`。
- 全程只改 app 副本 `consulting-report-agent/skill/`，不碰 canonical `consulting-report-skill/`。
