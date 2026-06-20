# Cutover Report — W1 技术标（technical-bid）报告类型

**日期**：实施 2026-06-21（spec/plan 2026-06-20）
**分支**：`feat/w1-technical-bid-type`（从含 N6 的 `main` 切，8 commits，**未 push/merge**）
**spec**：`docs/superpowers/specs/2026-06-20-w1-technical-bid-type-design.md`
**plan**：`docs/superpowers/plans/2026-06-20-w1-technical-bid-type.md`

## 一句话

新增第 7 个 `project_type=technical-bid`（技术标/投标），接进既有 R5 方法论路由：按评分点驱动注入「RFP 驱动 + 参考骨架 + 后置两表 + 字数/质量护栏」到 S1–S4 system prompt，前端下拉露出「技术标（投标）」。UI 报告类型中文名定为「**技术标**」，内部 slug `technical-bid`。

## 范围裁剪：10 task → 8 task（开工前预检拍板）

plan 写于 N6 合并前，原含 Task 5（`add_materials` 加 `size_bytes`）+ Task 6（`read_material_file` 超阈值守门），作为「N6 落地前的降级守门」。**开工前核实 main 现状：N6 已超额实现这两块**——`backend/skill.py` 已写 `size_bytes`/`content_sha256`，size 守门走 `backend/material_limits.py` 的 `MAX_HEAVY_MATERIAL_BYTES`（25MB）+ `is_heavy_suffix`（覆盖 docx/doc/pdf/pptx/ppt/xlsx/xls，比 plan 设想的「docx/pdf only 20MB」更广、常量集中化）。故 **Task 5/6 删除**，不重做、不引入 `MAX_HEAVY_MATERIAL_BYTES` 类常量。spec §5 的 size 守门以 N6 实现为准。强安全边界（材料 trust boundary）本就依赖 N6。

实际实施 = plan 的 Task 1/2/3/4/7/8/9 + Task 10 回归。

## 实施内容

| 项 | 文件 | 说明 |
|---|---|---|
| per-type 框架菜单 seam | `backend/skill.py:_framework_menu_for_type` | bid 返 `""`、其余返 `FRAMEWORK_MENU`；`build_methodology_block` 改用 seam |
| 注册类型 | `backend/skill.py` `TYPE_SKELETON_MAP`/`METHODOLOGY_TONE` | `technical-bid → technical-bid.md`、`technical-bid → bid` 腔调 |
| 技术标模块 | `skill/modules/technical-bid.md`（新建） | `## 二、标准结构` 段含全部常驻注入规则；`## 一`/`## 三` 不注入 |
| bid 声明腔调 | `backend/skill.py:_declare_and_invite_instruction` | 新 `if tone == "bid"` 分支，顿号分隔举例、避危险归一化词 |
| 后置两表 append 落点锁 | `tests/test_chat_runtime.py`（test-only） | 锁 spec §3.5：两表用 `append_report_draft` 追加末尾、不用 `edit_file`、记 `canonical_action=append` |
| 前端下拉 | `frontend/src/components/ProjectCreateModal.jsx` | `<option value="technical-bid">技术标（投标）</option>` |
| 概览占位同步 | `skill/plan-template/project-overview.md` + `backend/skill.py:_populate_v2_plan_files` | 报告类型占位清单修旧（删「运营优化」、补全 7 类）+ 替换 key 逐字节同步 |

## 关键决策

- **bid 不注入通用 `FRAMEWORK_MENU`**（偏离 spec §3.1 原「保留菜单」，用户已拍板）：技术标按评分点驱动、逐条响应，不靠「挑分析框架」；且通用菜单叠加会爆 token≤2k 预算。**实测**：注入菜单时 worst-case 2128 > 2000；跳过后 technical-bid 注入块 **1694 token ≤ 2000**（plan 预测 1679，余量 306）。其余 6 类 1122–1365 不变。
- **后置两表用 `append`、不用 `edit_file`**：技术评分索引表 + 技术规范书点对点应答须正文写完再生成，一律 `append_report_draft` 追加在草稿末尾（避撞 generative-intent 拦截/mutation cap）；「写最前 + 页码」交导出排版期。
- **参考骨架是参考、非模板**：据 `bid reference/`（1 主标 + 3 副标真实 docx，广西电网数据资源入表）校准——理论政策依据升格独立块前移、重难点两段式（分析+对策）、实施管理五件套、人员附佐证清单；本次结构以招标文件/评分点为真来源，模块强制「拟好结构先讲给用户确认/调整再展开正文」。
- **bid 框架名走 off-menu 白名单、不进 `KNOWN_FRAMEWORK_NAMES`**：评分点对标/点对点应答/WBS/重难点对策经 `_normalize_for_danger` 零误杀；R5 净化不变式（去除集合 ⊇ split 分隔符 ∪ off-menu 白名单）未动，注入 checkpoint/工具名变体的 bid 声明仍判 malformed。

## 审查

按项目规矩 **Codex 双轨独立 review（gpt-5.5 xhigh，spec + quality 不合并）+ 整 branch 综合审**：

- **方法论簇（Task 1-4）**：spec 轨 `SPEC-COMPLIANT`；quality 对抗轨 `APPROVED`（6 个攻击面——`load_type_skeleton` 代码围栏不误截、净化器 token 清洗前先验原文、bid 分支不破 if/elif 链、不碰 provider 序列化、token 测试真遍历每 slug 非假绿——全判干净）。
- **Task 7/8/9**：spec 轨 `SPEC-COMPLIANT`（占位逐字节一致已核）；quality 对抗轨挖到 **1 BLOCKER**——Task 7 两表落点锁测「半假绿」（只断言「以两表末行结尾」、没断言旧稿保留在前，回归成「替换整篇」也会假绿）。**已修**（commit `10b6ece`）：强内容断言 `assertEqual(draft, old_draft.rstrip()+"\n\n"+two_tables.strip())` + `mutation["old_len"]/["new_len"]`（实测确认字段存在）；复审 `APPROVED`。
- **整 branch 综合审**：`APPROVED`，零跨 commit 整合裂缝（端到端 前端→payload→create_project→registry/overview 通；打包含整个 skill 目录、新模块自动入包无需改 spec）。

## 回归

- 后端 `pytest tests/ -q`：**1204 passed / 4 failed / 13 skipped**。4 failed = macOS `/private/var` vs `/var` symlink realpath 环境差异（`test_skill_engine.py` 2 个 + `test_workspace_materials.py` 2 个），**逐个看断言实证为 pre-existing、与 W1 无关、Windows 通过**（见 CLAUDE.md「macOS 上做开发」§3）。W1 零新增失败。
- 前端 `node --test tests/`：**331 passed**（含 2 个新技术标用例）。
- DeepSeek 兼容定向 `-k "deepseek or tool_call or reasoning"`：**10 passed**（本特性只追加 system prompt 文本，不碰 provider message/tool-call/`reasoning_content`/`tool_choice`）。
- token 预算 + 七类 + bid：**5 passed**（worst-case 注入块实测 1694 ≤ 2000）。

## 仍待办（非阻塞）

- **GUI E2E（真实验收，只能人工跑）**：起一个 technical-bid 项目、真模型走 S1–S4，看大纲首行 bid 方法论声明、参考骨架→RFP 驱动的结构讨论、后置两表 append 落点。单测验不了「技术标写得好不好」——这才是 W1 真正的验收。plan 亦标「模块内容质量从未被验证」。
- push/merge 等用户发话。
