# Batch 3（R4 来源可信度 + R5 方法论路由）实施交接 — 2026-06-11

> **✅ 2026-06-11 收尾：批 3 已全部完成** —— B3-B10 全实施 + codex 双轨/红队逐 task APPROVED（commit `e6b4d8a`→`86b6b24`，HEAD `86b6b24`，skill_engine 210/前端 299/build 绿）。本交接的 B3-B10 待办已落地，**留作实施历史，不再是待办**。cutover：`docs/superpowers/cutover_report_2026-06-10_batch3-source-credibility-and-methodology.md`。

> 上一会话被 harness `[Request interrupted]` 反复腰斩 + agent 幻觉 + 电脑重启搞乱，用户重开会话接手 B3-B10。本文档是**核查过的 ground truth**，不是凭记忆写的。

## TL;DR

- 分支 `feat/batch3-source-credibility-and-methodology`（从含 plan commit 的 main 切出）
- **A1 ✅ B1 ✅ B2 ✅** 已 commit + codex 审过；**B3-B10 待做**
- plan：`docs/superpowers/plans/2026-06-10-batch3-source-credibility-and-methodology-routing.md`（11 task，codex 三轮 APPROVED）——**每个 task 的完整代码 / old_string / 命令都在 plan 里**，照着做即可
- HEAD = `5e26607`，working tree clean，`tests/test_skill_engine.py` 155 passed

## 真实 git 状态（2026-06-11 核查）

```
5e26607 feat(r5): TYPE_SKELETON_MAP + load_type_skeleton + FRAMEWORK_MENU   ← B2
2f47a4d refactor(r5): remove dead get_template() + skill/templates/          ← B1
584abb6 feat(r4): source-credibility three-tier annotation in SKILL.md S2   ← A1
e8e9c3f docs(batch-3): R4+R5 implementation plan — codex 3-round APPROVED    ← plan
```

- working tree：clean
- B3 代码（`parse_and_sanitize_methodology`）：**不存在**（上一会话试图实施，但 Edit 没落地 / 被幻觉淹没）

## 已完成（每个都 git+pytest 自验 + codex 审过）

### A1 = R4（`584abb6`）三档可信度 + 色点 + S2 小结 + 守护测试
- 纯 prompt 改 `skill/SKILL.md` S2 段 + 守护测试 `test_skill_md_datalog_examples_all_recognized_as_valid_sources`
- codex 单轨 **APPROVED**
- **NIT 已修**（过现实闸门采纳）：守护测试加显式断言「示例集必须含行首 `访谈:`/`调研:` 块」，防有人用 URL 块替换致守护空转

### B1 = 删死码（`2f47a4d`）
- 删 `get_template()` + `skill/templates/`（4 文件）+ 新增 `DeadMethodologyTemplateGuardTests`
- codex 单轨 **APPROVED**
- NIT 已拒（过闸门不修）：守护测试非严格 repo-wide，但 `hasattr` 断言已锁方法移除、codex 手验根脚本无残留
- **顺带 pre-clear B10 打包风险**：codex 确认 `consulting_report.spec` 用 `('skill','skill')` 整目录打包，删 templates 不报错

### B2 = 骨架加载（`5e26607`）TYPE_SKELETON_MAP + load_type_skeleton + FRAMEWORK_MENU
- codex **双轨**：spec 轨秒过；**quality 轨独立挖出 1 真 BLOCKER**——`load_type_skeleton` 的 `in_fence` 只翻转、循环结束不校验，unclosed fence 会把下游 `## 三、` 章节吞进骨架（fail-open，违背 fail-closed 设计意图）
- **已修**：循环后 `if in_fence: raise ValueError`（`backend/skill.py:2394`）+ `FileNotFoundError→ValueError`（:2372）+ 2 个 fail-closed 测试 + `METHODOLOGY_TONE` key 集断言
- quality 续审 **APPROVED**，155 passed

## 待做：B3-B10（按顺序，每个一 commit）

| task | 内容 | 派谁（plan 建议） | review |
|---|---|---|---|
| **B3** | `parse_and_sanitize_methodology` 三态净化（**trust boundary 安全核心**） | opus | 双轨 + 红队 |
| B4 | `__methodology_snapshot` 快照持久化 + cascade 条件保留 | opus | 双轨 + 红队 |
| B5 | 确认门方法论声明前置 + legacy 不规退 | opus | 双轨 + 红队 |
| B6 | `build_methodology_block` 装配 + 三腔调 + token 预算 | opus | 双轨 |
| B7 | chat.py 装配接入 + `methodology_declared` flag | opus | 双轨 + 红队 |
| B8 | SKILL.md 路由段改写为系统注入 | sonnet | 单轨 |
| B9 | 前端确认按钮 `methodology_declared` + 禁用理由 | sonnet | 双轨 |
| B10 | 回归 + cutover + worklist/CLAUDE.md 同步 | 主 agent | — |

### B3 接手细节（上一会话已 pre-clear，可直接用）
- **插入点（`5e26607` 行号）**：
  - 3 个常量（`_METHODOLOGY_DECLARATION_RE` / `KNOWN_FRAMEWORK_NAMES` / `_METHODOLOGY_DANGER_SUBSTRINGS`）加在 `FRAMEWORK_MENU` 的 `)` 之后（`backend/skill.py:275`，`REPORT_DRAFT_PATH` 之前）
  - `_canonical_framework_name` + `parse_and_sanitize_methodology` 加在 `load_type_skeleton` 的 `return body` 之后（`backend/skill.py:2399`，`def get_skill_prompt` 之前）
  - 7 个测试加在 `test_framework_menu_lists_core_frameworks` 之后（`SkillEngineTests` 内，`class S0CheckpointInfrastructureTests` 之前）
- **已验证**：`KNOWN_FRAMEWORK_NAMES`（33 项，无空格 casefold）覆盖 B6 三腔调举例的所有框架（SWOT/波特五力/BCG 矩阵 · SMART/RACI/里程碑 · DAMA-DMBOK/ISO 8000/成熟度模型 · 根因分析/对标分析）；`_canonical_framework_name` 去空格归一让「BCG 矩阵」带空格写法也命中
- **安全核心**：已知框架名在危险词检查**之前**放行（不被误杀）；菜单外严格短标签正则 `[A-Za-z0-9一-鿿\-/]{1,20}`（无空格无下划线）；命中工具名/checkpoint/注入词 → `malformed`
- 完整代码在 plan「## Task B3」段，照抄即可

## ⚠️ 上一会话的坑（重开会话必读）

1. **agent 幻觉 ×2**：B2 后台 `SendMessage` resume 报假 SHA `34d99e0` + 文件 binary 损坏；B3 前台 opus 报假 `91eaa05`/162 passed 但代码**根本不存在**。**根因：harness `[Request interrupted]` 在 agent 执行期反复腰斩，agent 的 DONE 文本已生成、但 git commit 没落地。**
2. **核查纪律是唯一防线**：每个 task 必须 `git log/status/diff` + `pytest` 自验，**绝不信 agent 自报的 SHA / 测试数**。两次幻觉都靠这个抓出来。
3. **竞态**：B2 修复时主 agent 在后台 agent 还活时并发 `git checkout` → 撞车（agent 第二次 amend `5e26607` 覆盖才没留永久损害）。**纪律：agent 没真正 settle 前不碰它在改的文件；要中途接管先 `TaskStop`。**
4. **用户明确纠正**：返工派回 agent **是正常做法**，别因一次事故立「禁派 agent」的乱规则——真问题是竞态，不是派 agent 本身。
5. **环境建议**：若新会话 interrupt 干扰仍在，B3-B10 这种 plan 代码确定性高的 task，主 agent 可直接用 Edit 实施（每步 git+pytest 自验），或用**前台 Agent**（阻塞、返回即真完成）——**不要用 `SendMessage` 后台 resume**（非阻塞、通知可被幻觉/腰斩）。

## 实施 SOP（项目 CLAUDE.md）

- review 一律 codex-server MCP（`gpt-5.5` xhigh，sandbox `read-only`，approval `never`）；plan/spec 合并单轨、**代码 commit 双轨独立不合并**；B3/B4/B5/B7 架构/安全敏感定稿前加**对抗式红队**（扛住才算真 approve）
- **每个 NIT/BLOCKER 先过现实闸门**再决定修不修（A1 NIT 修了、B1 NIT 拒了、B2 BLOCKER 修了）
- 禁止重跑 `tests/test_chat_runtime.py` 全量（22min），用 `-k` targeted
- 只改 app 副本 `skill/`，不碰 canonical `consulting-report-skill/`
- `__methodology_snapshot` 绝不进 `STAGE_CHECKPOINT_KEYS`（有 assert 会炸）
- 确认门绝不进 `_stage_one_completion_state`（legacy 规退红线）
- DeepSeek 官渠兼容不回归（B7 注入只追加 system prompt 文本，不碰 provider message/tool_choice/reasoning_content）
- git push 等用户
