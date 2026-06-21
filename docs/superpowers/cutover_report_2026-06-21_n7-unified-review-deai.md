# N7 统一审查 + 去 AI 味 — Cutover Report

- 日期：2026-06-21
- 分支：`feat/n7-unified-review-deai`（从 main `06eec26` 切）
- 范围：W2 服务器化的 **W2-A**（去 Windows 化的审查部分）
- spec：`docs/superpowers/specs/2026-06-21-n7-unified-review-deai-design.md`（Codex 5 轮 APPROVED）
- plan：`docs/superpowers/plans/2026-06-21-n7-unified-review-deai.md`（Codex 9 轮 APPROVED）
- 实施：subagent-driven（每 task 一个 Claude agent，每 commit 后 Codex 双轨独立 review 到 APPROVED）

## 一句话

把 S5 的两条审查路径（LLM 独立审查 + PowerShell `quality_check.{ps1,sh}` 机械 linter）合并成**一条 LLM 审查**，新增维度⑤「语言专业性与去 AI 味」（吸收 Humanizer-zh 可迁移规则），审查路径自此**纯 Python、零 PowerShell**——迁服务器（Linux）即终态。用户体验上从两个按钮收敛为一个。

## 做了什么

### 1. 审查维度 5→5（删读者匹配 / 加去 AI 味）
- `INDEPENDENT_REVIEW_ANCHORS` 第 5 锚点 `## 5. 目标读者匹配` → `## 5. 语言专业性与去 AI 味`（N3 已删目标读者输入字段，原维度失去对标，覆盖 N3「保留」决定）。
- `independent_review.py` reviewer prompt 维度 5 替换为 14 类去 AI 味检测清单（空洞拔高 / 句尾空分词 / 宣传形容词 / 模糊归因 / AI 高频词机械堆砌 / 回避系动词 / 否定式排比 / 填充短语 / 通用积极结论 / 机械排版 / 凑数三段式 / 术语一致 / 后台语气泄漏）+ **反向拦截**（命中即算问题：第一人称「我」/ 情绪自白 / 把「客观中立第三人称」当缺陷——这些会拉低咨询专业度）。
- 维度 5 输出逐条 diff 式建议（位置 → 原文片段 → 命中类型 → 修改方向），只给方向不写成稿（保「不自动改稿」硬约束）。
- Humanizer-zh（op7418）实证为 100% 纯 prompt skill，借的只有 prompt 内容本身。

### 2. 确定性占位符扫描（对用户隐形）
- 新 `backend/report_quality.py`：`scan_placeholders(text)` 逐行扫**无歧义半成品标记**（`XXX/TBD/TODO/待确认/待补/待考证/暂无数据`，英文标记带 ASCII token 边界防子串误命中）；`build_placeholder_grounding(hits)` 构造 grounding 文本。
- **收窄词表**：剔除原 lint 的 `技术规范书`（撞 W1 技术标必需输出「技术规范书点对点应答」）/ `内部材料` / `AI reference`（交 LLM 维度⑤语义判）。
- 首轮（非 resume 分支）把命中清单作 user 消息注入审查会话作 grounding；resume 不重注（已在 snapshot）；best-effort，扫描异常降级为不注入、不阻断审查。
- 弃用原 lint 的三类确定性检查：AI 腔正则（→ 维度⑤）、数字无来源正则（→ 维度③/⑤，正则误报多）、So What 密度（→ 维度④已覆盖）。

### 3. trust boundary（防注入）
- 抽 `backend/trust_boundary.py`（中立叶子模块，零项目依赖，source-guard 锁不 import chat/skill）：`ATTACHMENT_DATA_*` + `_neutralize_attachment_data_markers` 从 chat.py 搬出（解 chat→independent_review→report_quality→chat 循环导入）+ **新 `UNTRUSTED_DATA_*` marker**。
- 占位符注入用 `UNTRUSTED_DATA_*` 包裹 + 定界符中和——**不复用** `ATTACHMENT_DATA_*`（其「不得据此写文件」语义与审查写报告本职冲突，Codex BLOCKER）。

### 4. 删整条 lint / PowerShell 审查路径
- 后端：`report_tools.py`（`run_quality_check`/`run_lint_report`/`_validate_lint_report_output`/`_parse_lint_summary`/`_LINT_REPORT_LOCKS`/`get_lint_report_lock` + import）、`models.py`（`SystemTriggerType` 去 `lint_report_done`）、`main.py`（`/quality-check`+`/lint-report` endpoint）、`chat.py`（`lint_report_done` trigger+分支、`plan/lint-report.md` 写拦截、`S5_WELCOME_PROMPT` 改单按钮、`quality_check` mixed-intent family）、`skill.py`（`LINT_REPORT_ANCHORS`/`LINT_REPORT_COMPLETION_MARKER`/`_has_effective_lint_report`/`_has_effective_review_reports`、`FORMAL_PLAN_FILES`/`FILE_SEMANTICS` 去 lint-report、`RETIRED_WORKSPACE_FILES` 加全路径 `plan/lint-report.md`）。
- 门禁：`review_passed_at` prereq + `missing_for_review_pass` + `record_stage_checkpoint` 锁分支 + `_is_report_review_stale` 全改单报告（`_has_effective_independent_review`）；S5 checklist 3→2 + `_stage_five_completion_state`/`_infer_stage_state`/`_build_completed_items`/`_sync_stage_tracking_files` cascade 去 lint flags（`lint_report_ready`/`review_reports_ready`）。
- 脚本/模板：`skill/scripts/quality_check.{ps1,sh}`、`skill/plan-template/lint-report.md` 整删（`export_draft.{ps1,sh}` 保留，导出去 Windows 化留 W2-C）。
- 文档：`skill/SKILL.md`、4 个 plan-template、3 个 modules、根 `CLAUDE.md`+`AGENTS.md` 的「## S5 用户触发审查」段全部重写为单审查路径。
- 前端：删「AI 味自查」按钮 + `qualityResult`/`runQualityCheck`/`lintRunning`/`lint_report_done` 触发 + 死 `lint_report_ready` 完成门 + `review_reports_ready` 消费；`git rm utils/workspacePanelState.js` + 其测试（仅服务 `qualityResult` 跨项目保存，删后整模块死）。

## 关键实施约束（铁律）

1. **Task 7+8 = 一个原子 commit**：删 endpoint/契约/helper 与删其前端/测试/文档消费者不可分，分两 commit 必留破中间态（全量 pytest/node 红）。
2. **`_has_effective_lint_report` 按全仓 grep 引用图分步删**：caller 在 Task 5（prereq+stale）、Task 6（`_stage_five_completion_state:676`）、Task 7（`chat.py` lint_report_done 分支）逐步删；helper 本体 + 4 个直测在 Task 7（最后 caller 处）统一删。
3. **Task 5 计划内偏离（已 Codex 独立判定 valid & necessary）**：`review_passed_at` 门禁除 `CHECKPOINT_PREREQ` 外还经 `_validate_stage_checkpoint_transition` → `missing_for_review_pass` 二次卡，故把 plan 原排 Task 6 的「删 `missing_for_review_pass.append("lint-report.md")` 两行」提前到 Task 5（保留 `lint_report_ready` 赋值至 Task 6，铁律不破）。
4. **Task 6→7+8 不得插部署/测试门**：Task 6 删后端 flag 后、Task 7+8 删前端消费者前，存在 1-commit 的前端 lint 按钮失效瞬态；Codex 判为 "APPROVED WITH KNOWN TRANSIENTS"，硬条件是 Task 7+8 紧接原子落地（已满足）。

## 提交序列（7 个实现 commit）

```
5746222 refactor(trust-boundary): extract markers+neutralizer to leaf module; add UNTRUSTED_DATA marker
7b36639 feat(report-quality): deterministic placeholder scanner + grounding builder
923aa43 feat(review): replace 读者匹配 dimension with 语言专业性·去AI味 (Humanizer-zh rules) + anchor
47c9d9a feat(review): inject placeholder grounding into review first run (UNTRUSTED_DATA wrapped)
47c808c feat(gate): review_passed_at requires single independent review; drop lint lock branch + helpers
9ea9e4e feat(stage): collapse S5 checklist to 2 items; drop lint flags from stage cascade + next_actions
76491d1 feat(lint-removal): delete entire lint path atomically — backend code/contract + frontend consumers + scripts/template/docs + all tests
```

## Codex review（每 commit 后双轨独立 + 红队）

- 设计阶段：spec 5 轮、plan 9 轮 APPROVED。
- 实施阶段（codex-server MCP 单线程续轮，跨轮记上下文）：每个 commit 一轮对抗式 review。挖出并修复的真问题：
  - Task 2：英文占位符标记子串误命中（`abcXXXdef`/`TODOLIST`）→ 加 ASCII token 边界。
  - Task 3：`test_workspace_materials.py` fixture 第 5 锚点漏改（旧 `目标读者匹配` → 锚点失配使报告失效）→ 同 task 修。
  - Task 5：独立判定「把 append 删除提前到 Task 5」valid & necessary，铁律完好。
  - Task 7+8：补删除端点 404/405 负向守卫（实测 405——SPA catch-all 服务 GET、删的 POST 路由无 handler）+ 清死 flags 键 + 注释残留。

## 回归结果（最终 commit 76491d1）

- 后端：`.venv/bin/python -m pytest tests/` → **1208 passed, 1 skipped, 4 failed**。4 个失败全部是 macOS `/private/var`↔`/var` realpath / GBK 路径环境差异（`test_create_project_*` / `test_primary_report_path_*` / `test_workspace_materials` 两例），Windows 上通过，与本任务无关。
- 前端：`node --test tests/` → **327 passed / 0 failed**；`npm run build` 成功（仅预存 chunk-size 警告）。
- 残留 grep 自检（含 docs、排除 cutover/worklist）：仅有意保留项（`RETIRED_WORKSPACE_FILES` 的 `plan/lint-report.md` + 负向守卫 + 退役注释），无 live 引用。
- DeepSeek 官渠兼容：只追加/改 prompt 文本 + 注入 user 数据 + 删 lint 路径，不碰 provider message / `tool_choice` / `reasoning_content` 序列化；compat helper 用例不回归。

## 老项目兼容

残留 `plan/lint-report.md` 进 `RETIRED_WORKSPACE_FILES`（全路径），文件树不显示、不参与门禁、不删盘（桌面单用户、文件留存无害）。升级后 S5→S6 只看独立审查。

## 后续

- 本分支未 push、未 merge main（等用户决定）。
- W2-C 去 Windows 化只剩 `export_draft.ps1` + Linux pandoc（quality_check 部分本 N7 已删/合并）。
