# Cutover Report — S5 Independent Review Redesign (2026-05-22)

**Status:** 自动化收尾已完成；打包重建与 GUI 端到端验收按计划留给用户手工执行。

**依据文档：**

- Spec：[docs/superpowers/specs/2026-05-21-s5-independent-review-redesign-design.md](specs/2026-05-21-s5-independent-review-redesign-design.md)（v6 APPROVED）
- Plan：[docs/superpowers/plans/2026-05-21-s5-independent-review-redesign.md](plans/2026-05-21-s5-independent-review-redesign.md)（v5 APPROVED）

## 实施概述

S5 从旧的模型自评 `review-checklist.md` 改为两个用户主动触发的审查入口：

- 「独立审查」：独立 LLM 会话审 5 个判断维度，输出 `plan/independent-review.md`。
- 「AI 味自查」：PowerShell 脚本审 4 个机械维度，输出 `plan/lint-report.md`。
- 两份报告就绪后，主代理只负责读报告、与用户讨论修改、在用户确认后推进 `review_passed_at`。

Commit 链：

| Phase | Commits | Scope |
|---|---|---|
| Commit 1 | `f8287a3 feat(s5-redesign): add dormant infrastructure for S5 independent review`<br>`2f9f9b9 fix(s0): treat whitespace-only assistant reply as empty`<br>`69caf1c docs: sync worklist after S5 redesign spec+plan APPROVED` | dormant infrastructure：S5 flags/helper/conversation state；前置 S0 空白回复修复；worklist 同步 |
| Commit 2 | `d034faf feat(s5-redesign): add independent review isolation primitives`<br>`18f95b7 fix(s5-redesign): align quality_check.ps1 rules with spec §3.1-3.4`<br>`ea57827 fix(s5-redesign): address quality review R1 important issues` | independent review isolation primitives、lint 脚本、主代理拒写审查报告 |
| Commit 3 | `e93fc80 feat(s5-redesign): wire endpoints + chat_stream system_trigger branch (dormant)`<br>`d59b65a fix(s5-redesign): address quality review R1 issues (async sse + endpoint error shape)`<br>`0123caf fix(s5-redesign): support cooperative cancel in IndependentReviewAgent.run()` | endpoints、system_trigger、SSE/lock/cancel |
| Commit 4 | `98a7e0a feat(s5-redesign): atomic cutover to independent review workflow`<br>`6acf7c5 fix(s5-redesign): address commit 4 quality review (project guard + error UX)` | 用户可见 atomic cutover：backend gate、SKILL、前端按钮、smoke 文件列表 |
| Commit 5 | `6bd8982 docs(s5-redesign): land cutover report and smoke coverage`<br>`0b7212e docs(s5-redesign): improve cutover report and smoke clarity`<br>本 commit 同属 Commit 5，hash 见 git log（cutover doc 自引用最终 hash 是循环） | packaged smoke 扩展、endpoint 覆盖补差、cutover doc、worklist 更新 |

## 验证结果

前四个 implementation commit 已按计划通过 spec-compliance + quality double review，并在进入下一 commit 前关闭 reviewer findings。

Commit 5 本次自动化验证范围：

- `tests/smoke_packaged_app.py` 扩展为：S0 项目调用新 endpoint 返回 400 且 detail 含 `S5`；旧 `/quality-check` 与 `/export-draft` 继续验证；打包资产检查新增 `independent-review.md`、`lint-report.md`、`quality_check.ps1 -OutputPath`。
- `tests/test_main_api.py` 已覆盖：SSE content-type、独立审查持锁 409、断连释放 lock、lint summary 透传。
- 本次不跑 `build.bat`，不声称新 `dist\咨询报告助手\` 已重建。
- 本次不跑 piggy-v2 GUI 手工 S0-S7，不声称真实桌面端 E2E 已完成。

## 老项目兼容性确认

- 旧项目里的 `plan/review-checklist.md` 作为用户数据保留，不主动删除。
- `skill/plan-template/review-checklist.md` 保留为历史模板资产，但不再进入新项目正式 plan 文件集合。
- `_has_effective_review_checklist` 保留为 backwards-compat helper；生产推进路径已退出旧 helper，`review_passed_at` 改为校验 `independent-review.md` + `lint-report.md`。
- 老项目升级后 S0-S4 不受影响；若卡在 S5 推进，错误消息会引导用户点击「独立审查」和「AI 味自查」两个新按钮，不阻断推进到 S5 之前的流程。

## 已知限制

1. **30k 字 friendly fail 是 v0 策略**：正文过长时独立审查直接给友好错误，不做 chunk fallback；后续 v1 可做 map-reduce 重审。
2. **单进程 lock 假设**：当前 per-project lock 覆盖单进程桌面应用；多进程并发未覆盖。
3. **打包态内容依赖重建**：本次只更新 smoke 断言；实际 `_internal/skill/...` 需要用户运行 `build.bat` 后再验证。

## Rollback Procedure

如果 Commit 4 用户可见 cutover 出问题：

逻辑 Commit 与物理 git commits 的对应关系：

| 逻辑 Commit | 物理 git commits |
|---|---|
| Commit 1 | `f8287a3` base + `2f9f9b9` S0 prebugfix + `69caf1c` worklist |
| Commit 2 | `d034faf` base + `18f95b7` spec fix + `ea57827` quality fix |
| Commit 3 | `e93fc80` base + `d59b65a` R1 fix + `0123caf` R2 fix |
| Commit 4 | `98a7e0a` base + `6acf7c5` R1 fix |
| Commit 5 | `6bd8982` docs/smoke |

1. 整体 revert **逻辑 Commit 4**，不要 cherry-pick 部分文件；backend gate、SKILL、前端按钮必须同步回滚。逻辑 Commit 4 包含 `98a7e0a` base 和 `6acf7c5` fix，推荐反序 revert：
   ```bash
   git revert 6acf7c5  # Commit 4 fix
   git revert 98a7e0a  # Commit 4 base
   ```
   或者用范围一次性选择同一段物理 commits：
   ```bash
   git revert 98a7e0a^..6acf7c5
   ```
   若需回滚 Commit 2/3 等更早逻辑 commit，也按上表把对应物理 commits 从新到旧反序 revert。
2. 回滚后系统恢复旧路径：
   - `CHECKPOINT_PREREQ.review_passed_at` 重新指向 `_has_effective_review_checklist`。
   - SKILL.md S5 段恢复要求 `review-checklist.md`。
   - 前端恢复旧质量检查/导出入口。
   - Commit 2/3 的新文件和 endpoint 回到 dormant 状态；已生成的 `independent-review.md` / `lint-report.md` 作为用户数据保留。
3. 回滚验证：
   - 后端：`.venv\Scripts\python -m pytest tests/ -q`
   - 前端：`cd frontend && node --test tests/ && npm run build`
   - 打包态：重建后跑 `tests\smoke_packaged_app.py`
   - 手工：确认 S5 可走回旧 `review-checklist.md` 路径

如果只 Commit 5 出问题，revert Commit 5 即可；Commit 4 用户可见 cutover 不需要回滚。

## Pending Manual Acceptance

- Task 5.1：新建 `piggy-v2`，手工走 S0-S7 GUI 流程。
- Task 5.2 Step 1：运行 `build.bat` 重建 `dist\咨询报告助手\`。
- Task 5.2 Step 5：启动 `dist\咨询报告助手\咨询报告助手.exe` 走真实打包态 E2E。
- Task 5.2 Step 6：验证 `_internal/skill/plan-template/independent-review.md`、`lint-report.md`、`_internal/skill/scripts/quality_check.ps1 -OutputPath`。
