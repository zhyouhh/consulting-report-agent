# Current Worklist

最后更新：2026-05-21（S5 Independent Review Redesign 的 spec + plan 经 codex 11 轮 review 全部 APPROVED 并落 commit `6e5c2fc`；待派 codex 按 5-commit 拆分实施。stage conductor v0 + 打包态 GUI 启动 / S0-S7 确定性阶段推进 / Markdown 表格 / 质量检查 / 包内 Pandoc 导出仍稳定。真实 managed 模型长链路 timeout 用户已切官渠暂不处理。）

## 当前未解决 / 待验证

1. **P1：managed 真实模型长链路 timeout / 无首包**
- 状态：`暂不处理（用户已切官渠绕过）`
- 现象：2026-05-19 实测中，真实模型 S0 首轮和一次 `advance_stage` 可工作，但后续请求出现上游 timeout / 长时间无首包；确定性打包态 S0-S7 阶段机已通过
- 用户决策（2026-05-21）：暂时切换到 DeepSeek 官渠绕过，本条不影响 S5 redesign 推进
- 后续可选：区分网关/渠道问题与应用重试 UX 问题，必要时增加 no-first-byte 观测日志和用户可理解的恢复提示

2. **P1：S5 Independent Review Redesign（替换原 review-checklist 格式契约）**
- 状态：`spec + plan APPROVED，等实施`
- 关联文档：
  - Spec [docs/superpowers/specs/2026-05-21-s5-independent-review-redesign-design.md](superpowers/specs/2026-05-21-s5-independent-review-redesign-design.md)（v6，6 轮 codex review APPROVED）
  - Plan [docs/superpowers/plans/2026-05-21-s5-independent-review-redesign.md](superpowers/plans/2026-05-21-s5-independent-review-redesign.md)（v5，5 轮 codex review APPROVED）
  - 同落 commit `6e5c2fc docs(s5-redesign): land spec + plan...`
- 设计要点：砍掉模型自评 `review-checklist.md`；改成两个用户主动触发按钮——「独立审查」派独立 LLM 会话审 5 个判断维度，落 `plan/independent-review.md`；「AI 味自查」跑 PowerShell 脚本扫 4 个机械维度，落 `plan/lint-report.md`。两份报告生成后前端自动起主代理 turn，主代理读报告 + 跟用户讨论。
- 实施分 5 commit（每个 commit 嵌入 codex spec-compliance + quality 双轮 review）：
  1. Commit 1：后端 100% additive dormant（新 helper / 字段 / schema 扩展，不接生产路径）
  2. Commit 2：FORMAL_PLAN_FILES 加新文件 + 主代理拒写拦截 + 独立审查代理 + lint 脚本（内 atomic，避免独立性破洞）
  3. Commit 3：endpoints + chat_stream system_trigger 分支 + S5 welcome helper（dormant 不接调用点）
  4. Commit 4：用户可见 atomic cutover（backend gate + SKILL + 前端按钮 + smoke 一次性落）
  5. Commit 5：端到端 piggy-v2 + 打包态 smoke + cutover doc
- 硬约束：S0-S4 流程零变更
- 高风险点：
  - Commit 3 `_chat_stream_unlocked` 加 `system_trigger` 分支（来回 4 轮才稳定，实施时需严格按 plan Task 3.3 Step 3 方案三 v2 写）
  - Commit 4 atomic cutover（11+ 文件一次切换）

3. **P2：打包 / 前端小债**
- 状态：`待清理`
- 当前明确项：`favicon.ico` 404、输入框缺少 `id` 或 `name` 的可访问性提示、`npm audit` high、Vite chunk warning、PyInstaller conda warning

4. **图片附件能力按 managed_model 分流**（与 DeepSeek Migration 同期发现，已推后）
- 状态：`已推后到 UI 重构一并处理`（spec §2.2 Out of Scope）
- 现状：`frontend/src/utils/modelCapabilities.js` 的 `supportsImageAttachments` 对 `mode==="managed"` 一律 return true（gemini-3-flash 时代多模态行为）
- 问题：DeepSeek V4 Pro 是 text-only reasoning 模型，前端不拦图片附件 → 用户传图后请求会被上游 400 拒（postMessage、上传按钮、拖拽都不会有 UX 提示）
- 修复方向：把 managed 分支按 `settings.managed_model` 二次判断，复用 `MULTIMODAL_MODEL_MARKERS`；或维护 managed-mode 多模态白名单
- 关联文件：`frontend/src/utils/modelCapabilities.js`、`frontend/tests/modelCapabilities.test.mjs`、各上传/粘贴入口组件
- 触发条件：UI 重构立项时一起做（设计稿在 `docs/design_UI.pdf`）

5. **UI 重构**
- 状态：`待立项`
- 设计稿：`docs/design_UI.pdf`（用户用 Claude design 做的 3 套初步设计稿）
- 触发条件：当前打包 GUI 已恢复可打开；后续如立项 UI 重构，先单独定范围，不要把渠道稳定性和打包小债混进大重构里

6. **stage-advance-gates Bug G/H 低优先级待复核**
- 状态：`低优先级待复核`
- Bug G：回退 checkpoint 后 `content/*.md` 仍存在，状态可能不自洽；复核时决定级联清理还是 UI 标红提示。
- Bug H：S1 回退后 UI「下一步建议」显示"暂无"，`next_stage_hint` S1 分支缺；复核时补齐提示或确认新版流程已绕开。

7. **新建项目表单与废 UI 整理**（待 UI 重构时并入/评估）
- 状态：`待 UI 重构时评估`
- 目标：清理"填了像没填"的字段、重复输入项和旧流程遗留 UI，包括截止日期控件、材料/备注语义重叠、项目类型/主题/目标读者/篇幅字段利用率。
- 关联：Task 7 的 `length_fallback` chip 目前只是非交互提示；如做项目表单 edit 模式，可顺便让 chip 点击打开编辑面板。

8. **`draw.io skill` 评估**
- 状态：`待评估`
- 目标：判断它对咨询报告场景是否真有价值，还是只会增加复杂度。

9. **前端生产包优化**
- 状态：`待优化`
- 现状：`vite build` 已通过，但主 JS chunk 仍接近 `1 MB`。
- 目标：在不引入复杂度失控的前提下做基本拆包，降低首屏和构建产物压力。

10. **技术债清理**
- 状态：`待清理`
- 当前明确项：`pydantic` deprecation warning、打包依赖排除空间。

## 已解决记录

0f. **Packaged QA 前四个阻断/一致性问题修复（2026-05-13）**
- 状态：`已修复并重打包验证`
- 修复：
  - GUI 首屏崩溃：`supportsImageAttachments(settings)` 兼容启动期 `settings === null`，消除 `Cannot read properties of null (reading 'mode')`
  - `quality_check.ps1`：Windows PowerShell 脚本改为 UTF-8 with BOM，并补直接执行 smoke
  - `export-draft`：用户决策为随 Windows 包带 Pandoc；`consulting_report.spec` 将 `pandoc.exe` 打入 `_internal`，导出脚本优先使用包内 Pandoc
  - checkpoint API 越级推进：`record_stage_checkpoint()` set 下游 checkpoint 时校验前序 checkpoint 链，报告+演示模式下归档仍要求 `presentation_ready_at`
- 验证：
  - frontend `node --test tests\`: 184 passed
  - frontend `npm run build`: passed（仍有既有主 chunk 过大 warning）
  - backend `.venv\Scripts\python.exe -m pytest tests -q -n 8`: 852 passed / 1 skipped / 20 warnings / 22 subtests passed
  - PyInstaller 重建 `dist\咨询报告助手\` 成功；包内 `pandoc.EXE` 存在，当前包体积约 307 MB
  - packaged smoke：exe 启动、`/api/health`、项目脚手架、`quality-check`、`export-draft` 全通过
  - 浏览器打开 `http://127.0.0.1:8080/` 首屏正常渲染，不再显示「应用出错」

0f. **stage conductor v0 阶段推进清理（2026-05-19）**
- 状态：`已解决；打包态确定性 S0-S7 与 Markdown 表格渲染已验证`
- 覆盖问题：checkpoint API 越级推进、写作阶段与 checkpoint desync、legacy `<stage-ack>` 被误当作运行时推进信号、`settings.mode=null` GUI 启动崩溃、Windows PowerShell 脚本源码/输出编码、聊天 Markdown 表格原样显示。
- 当前规则：阶段推进 / 回退只能通过 `advance_stage(checkpoint_key, action, reason)`，并由 `SkillEngine.record_stage_checkpoint()` 统一校验前序阶段、实质文件和质量门禁；`POST /api/projects/{id}/checkpoints/{name}` 也委派同一服务，不能绕过前序阶段。
- 已关闭的旧路径：用户强关键词不再触发 checkpoint side effect；legacy `<stage-ack>` 只作为历史残留做后端 / 前端剥离，不再设置 checkpoint。若畸形 legacy tag 未被 sanitizer 命中，残留风险只剩可见文本污染，不再是阶段推进风险。
- 回归入口：`tests/test_skill_engine.py` 覆盖 transition validation；`tests/test_chat_runtime.py` 覆盖 `advance_stage`、强关键词无副作用、legacy tag 无 checkpoint；`tests/test_main_api.py` 覆盖 checkpoint endpoint；`tests/test_packaging_docs.py` 锁定 `skill/SKILL.md` 不再含 stage-ack 指令。
- 打包态记录：[2026-05-19 stage conductor packaged QA](superpowers/handoffs/2026-05-19-stage-conductor-packaged-qa.md)

0e. **DeepSeek 官渠 tool-call 400 根治 + 打包态后端 S0-S7 QA（2026-05-13）**
- 状态：`代码已修复；打包态后端生命周期已跑到 done；当时遗留的 GUI 启动崩溃已在 2026-05-19 关闭`
- 根因：
  - DeepSeek 官渠 reasoner route 会拒绝显式 `tool_choice="auto"`
  - thinking/tool-call follow-up 需要把非空 `reasoning_content` 随 assistant tool-call message 回传
  - OpenAI SDK `model_dump()` 可能携带 `reasoning_content: null` / `audio: null` 等字段，官渠会拒
- 修复：
  - DeepSeek 模型请求保留 `tools`，但不显式传 `tool_choice`
  - stream / non-stream tool-call follow-up 保留非空 `reasoning_content`
  - assistant tool-call message 改为只序列化 provider 需要的字段，丢掉 null SDK dump 字段
- 验证：
  - targeted regressions 通过
  - `tests/test_chat_runtime.py -q -n 8`: 430 passed / 1 skipped / 20 warnings / 17 subtests passed
  - `tests -q -n 8 --ignore=tests/test_chat_runtime.py`: 409 passed / 8 warnings / 5 subtests passed
  - source canary：真实 `deepseek-v4-pro` 完成 `read_file → tool result → final reply`，0 error
  - packaged canary：`dist\咨询报告助手\` 完成同一 tool-call round trip，0 error
  - packaged S0-S7 API lifecycle：最终 `done / 已归档`
- Handoff：[2026-05-13 packaged S0-S7 QA handoff](superpowers/handoffs/2026-05-13-packaged-s0-s7-qa.md)

0d. **DeepSeek Migration Commit 1-3 + cutover report 完成（2026-05-09）**
- 状态：`已完成；packaged QA 后续已转入 0e，并在 2026-05-19 完成 stage conductor 打包态复测`
- Commit chain：`69730c7 Add migration toolset foundation` → `118f383 Cut traffic from legacy report draft tools` → `9a59955 Delete legacy report draft control layer`
- Cutover report：[docs/superpowers/cutover_report_2026-05-08_deepseek-migration.md](superpowers/cutover_report_2026-05-08_deepseek-migration.md)（commit `a5f1cd1 docs(deepseek-migration): add cutover report`）
- 验证：backend fast `834 passed, 1 skipped, 3 deselected, 13 warnings, 22 subtests passed`；backend including slow `837 passed, 1 skipped, 13 warnings, 22 subtests passed`；frontend node tests `183 passed`；Windows build `build.bat` 成功并重建 `dist\咨询报告助手\`；legacy grep gates 7 类 clean
- 注意：原 Task 38 packaged UI/chat manual E2E 已在 2026-05-13 复测；当时 GUI 阻断已在 2026-05-19 关闭

0c. **DeepSeek Migration Commit 0 + spec + plan 全 APPROVED（2026-05-07~08）**
- 状态：`Commit 0 已 ship 3 commits + spec 3 轮 codex review APPROVED + plan 4 轮 codex review APPROVED`
- Spec：`docs/superpowers/specs/2026-05-08-deepseek-migration-toolset-redesign-design.md`
- Plan：`docs/superpowers/plans/2026-05-08-deepseek-migration-toolset-redesign.md`（HEAD `d7afadb`）
- Commit 0 已 ship：
  - 服务器 managed proxy: `MANAGED_PROXY_ALLOWED_MODELS=deepseek-v4-pro` + 容器重建（2026-05-07）
  - `06779b1` rename default managed model gemini-3-flash → deepseek-v4-pro（含 AGENTS.md / CLAUDE.md / docs/managed-proxy-deployment.md / SettingsModal.jsx / managed_proxy/app.py 5 处）
  - `0b8b968` heal stale managed_model on startup + tier_1m_eff_256k tier mapping + connectionMode fallback + 7 个 HealStaleManagedModelTests + 1 个 context_policy test + APPROVED design spec + UI 设计稿
  - `8b3ad16` catch the last two gemini-3-flash refs in README + proxy contract
- 默认模型名同步覆盖了原 worklist item #3"默认渠道文案与默认模型决策"，整体合入 DeepSeek Migration
- 2026-05-08 E2E 实测：DeepSeek V4 Pro 9 次工具调用 schema 100% 正确，模型行为本身没问题；6 个产品/工程问题（`<think>` 标签泄露 / S0 门槛被动 / per-turn search 配额过严 / packaged stderr 吞没 / version_info 空 / 老用户 config heal）作为本轮 plan 的 in-scope items 一次性处理
- 后续实施已完成，见 0d 与 cutover report；packaged UI/chat 手工验收在 2026-05-13 与 2026-05-19 分阶段完成

0b. **S4 mutation-limit 二次写入真实字数提示（2026-05-07）**
- 状态：`已修复并打包验证`
- 问题：模型同一轮第二次调用 `append_report_draft` 被“一轮一改”guard 拦截后，旧错误只说“本轮已经修改过”，没有带真实字数；后续 assistant free text 可能自行估算并误称已达标。
- 修复：4 个 canonical draft 工具在 mutation-limit error 上统一回传 `report_progress`，错误文案包含“当前真实字数：x/y”和是否仍需补全，明确要求模型下一轮继续，不得声称达标。
- 验证：4 个 targeted regression passed；semantic draft tools + obligation guard 扩展回归 `82 passed, 1 warning`；`build.bat` 重打包成功；新 exe 通过 mock OpenAI `/api/chat/stream` smoke（`read_file → append 成功 → 第二次 append 被 guard 拦截`，stream 与完整 tool payload 均含真实进度）。

0a. **流式输出体感 + open issues 关闭（2026-05-06）**
- 状态：`已关闭`
- 流式体感：前端 flush 修复 + 读流超时友好报错已生效，用户确认当前体感正常
- Streaming retry timing / detector regex 扩展：打包态 5-session smoke 未触发 false positive，旧 app 残留进程不影响功能，整体关闭
- Push to origin/main：已完成

0. **Tools redesign 实施完成并通过打包态 6.3 smoke（2026-05-06）**
- 状态：`Tasks 1-6.2 全部 codex spec+quality 双轮 review APPROVED；2026-05-06 补 obligation tool-family guard；根目录 dist 重建；打包态 Task 6.3 五轮 smoke 全绿；本地 main 已 push origin`
- 实施 commits（17 commits this implementation phase，全在 local branch `claude/phase2-draft-action-tag`）：
  - **Task 1**（4 commits）：`9d183df` `b80413c` `9e54d88` `9cd071d` — `backend/report_writing.py` + 41 helper tests
  - **Task 2**（4 commits）：`292bf6f` `68eb8a2` `2717760` `43b6c68` — turn_context fields + obligation detector + read_file mtime hook
  - **Task 3**（7 commits 含 fix1）：`0c0f387` `c75ff0d` `0404f67` `1644620` `400e433` `dd5a322` + fix1 `5d88e2b` — 4 tools impl + 51 ToolTests + retry hook + Critical fix (legacy gate accepts semantic edit tools)
  - **Task 4**（2 commits）：`fa3088c` `3f28957` — SKILL.md §S4 重写 + chat.py user_action wording
  - **Task 5**（5 commits 含 fix1）：`911a9d2` `8bd0abc` `bac9112` `4ab5010` + fix1 `c53b5f3` — the big delete (-6594 lines) + legacy tag regression + wire append dispatch (Task 3 deferral 关闭) + canonical_draft_mutation merge fix
  - **Task 6.1+6.2**（1 commit）：`d482235` — dist rebuild 86.09MB / 3.16 min + tool-selection benchmark schema sanity
- Net diff：17 files changed, **+4844 / -6535 = -1691 lines net**
- Test acceptance：
  - `pytest tests/test_chat_runtime.py`: **360 passed, 1 skipped, 0 failed** in 1481s（之前 36 pre-existing fails 全部在 Task 5 删除的 deprecated test classes 里，自然消失）
  - `tests/test_report_writing.py`: 41/41
  - `tests/test_tool_selection_benchmark.py`: 4/4
  - 2026-05-06 post-smoke focused regression：`96 passed, 1 warning`（canonical draft obligation + 4 semantic tools + report_writing + benchmark）
  - frontend `node --test tests/`: 168/168 unchanged
- Review iterations: Task 3 + Task 5 各走 2 轮 quality review (r1 With fixes → fix1 → r2 Yes)；其他 task 一轮 APPROVED_WITH_NOTES
- Build：根目录 `dist/咨询报告助手/` 重建成功（`咨询报告助手.exe` 14,069,486 bytes，2026-05-06 22:00）
- Packaged smoke 6.3（evidence: `reality_test/smoke_backups/6-3-packaged-20260506-221248/summary.json`）：
  - A "开始写报告吧" → `append_report_draft` ✅ draft appended
  - B "把第二章重写一下" → `rewrite_report_section` ✅ only 第二章 changed
  - C "把'团队防御蓝领'改成'团队防御核心'" → `replace_report_text` ✅ unique phrase replaced
  - D "继续写第三章" → wrong semantic tools blocked, then `append_report_draft` ✅
  - E "整篇重写，按 outline 用更精炼的语言重写正文" → generic `write_file` / `edit_file` blocked, then `rewrite_report_draft` ✅
- 详见 [cutover report](superpowers/cutover_report_2026-05-06_tools-redesign.md)

0a. **Tools redesign spec + plan review 通过（2026-05-05 深夜，已 superseded by 0 entry above）**
- 状态：`spec + plan 全套通过 codex 双轮 review`（HEAD `1030d7b` plan v2）
- spec stage：4 commits 4 轮 review（d5bb758 → 5cb5f6b → a936bfb → 2c355c8 → 7f0d207），最终 APPROVED_WITH_NOTES
- plan stage：2 commits 2 轮 review（1226a67 → 1030d7b），最终 APPROVED
- 本会话整体输出：spec 788 行 + plan 2203 行 + 5 个 reviewer prompts

1. **Phase 2a fix4 完整集合 — section/replace keyword fallback 实施 + 双轮 review APPROVED + cutover smoke 验证（2026-05-05 17:00-19:00，已被 redesign 取代）**
- 状态：`已合 main + 已 push origin`（main HEAD `07a8269`）
- 16 commits total this Phase 2a 集合（fix4 三轮叠加在 13 commits 之上）：
  - `ec0b327 feat(rollout): section/replace keyword fallback (spec §4.12 v5 fix4)` — Path A 实施：spec §4.12 amendment + chat.py preflight Step 1.5 + gate edit_file fallback + SKILL.md §S4 fallback note + 11 tests
  - `70ec0ba fix(rollout): address fix4 round 1 rejections (Bugs 1-5 + test tightening)` — fix1：(1) `改为` 关键词补齐, (2) `_SECTION_PREFIX_RE` negative-lookahead 防 `第二章节` overmatch, (3) `_preflight_resolve_section_target` 多 prefix dedup, (4) zero_candidates / multi_candidate test 拆分, (5) 防御 test 取值集合放宽至 5 元集
  - `07a8269 fix(rollout): close fix4 round 2 safety holes (Bugs 7-8 partial multi-prefix + snapshot inject)` — fix2：(7) 任意 prefix unresolved → fail-fast None, (8) `_required_write_paths_for_turn` + `_build_required_write_snapshots` 优先读 cached `turn_context["canonical_draft_decision"]`，inject 同时 promote `mode="no_write"→"require"` 让 snapshot/scope 路径生效
- 双轮 review (spec + quality codex reviewer)：r1 REJECTED Bugs 1-5 → fix1; r2 REJECTED Bugs 7-8 → fix2; **r3 BOTH APPROVED**
- 测试：41/41 PreflightCheck + Gate pytest pass; wider sanity 87/0
- Cutover smoke 4 sessions plan reduced to 2 actionable runs (A begin + B section)：
  - Session A "开始写报告吧" ✅ begin fallback fired（regression 保护，fix3 同款行为）, draft 2549→3677 字
  - Session B "把第二章重写一下" ✅ section fallback fired 14 次 + scope enforcement active（vs fix3 19 次 gate-block dead-loop）— **fix4 设计层面工作正确**；模型未能缩窄 new_string 是独立 model-behavior 问题（见 0a）
  - Session C "把'X'改成'Y'" — 模型在 reasoning 阶段 hung 8 min 没 emit tool_call，无 events 数据；inconclusive
  - Session D continue — skipped（A 同类 regression 已覆盖）
- 详见 [cutover report](superpowers/cutover_report_2026-05-05_fix4.md) + [handoff doc (final)](superpowers/handoffs/2026-05-05-phase2a-fully-done-phase3-ready.md)

1. 二轮重打包已完成，主链路已跑完
- 状态：`已走二轮 smoke（暴露新 3 bug，见 1b；后续已全部修复）`
- 二轮重点验回顾：
  - Bug A/B/D/F 修复在新包里都生效（data-log.md 已按 `### [DL-YYYY-NN]` 格式写；非 plan 写入阶段门禁生效）
  - 聊天气泡 + 文件预览原生框选复制可用
- 二轮新暴露问题见 1b（已修）

1a. **[BUG 串] stage-advance-gates 实机链条性失效 — A/B/C/D/F 已修，G/H 移入当前待办**
- 状态：`A/B/C/D/F 已修；G/H 已移入上方 stage-advance-gates Bug G/H 待复核项`（2026-04-21 3 路并行 codex + general-purpose 派活，全部合 main；C 后续被 S0 interview 实施覆盖，详见 1d）
- 关联 plan：`docs/superpowers/plans/2026-04-21-smoke-test-bugfix.md`
- 测试基线：403 passed / 1 skipped（基线 397 → 403，加 6 条新测试）

**Bug A ✅** — `backend/chat.py` `_should_allow_non_plan_write` 已叠加阶段校验，仅在推断阶段 ≥ S4 时放行非 plan 写入。commit `cb15e4c fix(chat): gate non-plan writes by stage`。

**Bug B ✅** — `backend/skill.py:record_stage_checkpoint` 在 `set` 前校验对应 plan 文件有效存在（outline/report_draft/review_checklist/presentation_plan/delivery_log），缺文件 raise ValueError。commit `7e262cf fix(skill): validate stage checkpoint prerequisites`。

**Bug C ✅** — 已先由 S0 interview + legacy stage signal 实施覆盖（spec/plan APPROVED 后 19 个 task 全套合 main），后由 2026-05-19 stage conductor v0 收敛为 `advance_stage`。`stage_zero_complete` 不再依赖 `project_overview_ready`；当前必须由 `advance_stage(checkpoint_key="s0_interview_done_at", action="set", reason="...")` 成功落 checkpoint 才推进。详见 1d 与 0f。

**Bug D ✅** — `skill/SKILL.md` §S2 明确 `### [DL-YYYY-NN]` 格式 + 完整示例，并写明"表格形式不会被识别"；首次写 `plan/data-log.md` 时通过 `_emit_system_notice_once` 注入格式提示。commits `7a50bb3` / `88f10d7` / `4a6a7da`。

**Bug E ✅** — Bug A+D 修好后自消，不再独立追踪。

**Bug F ✅** — `backend/chat.py:_expected_plan_writes_for_message` 白名单从硬编码 5 条路径改成正则匹配 `report_draft_v\d+\.md` 和 `(content|output)/*.md`，`_is_expected_report_write_path` 方法抽出可复用。+28 行测试。commit `1e180cc fix(chat): detect versioned report draft claims`。

**Bug G ↗** — 回退 checkpoint 后 `content/*.md` 仍存在，状态不自洽；已移入当前 stage-advance-gates Bug G/H 待复核项。

**Bug H ↗** — S1 回退后 UI「下一步建议」显示"暂无"，`next_stage_hint` S1 分支缺；已移入当前 stage-advance-gates Bug G/H 待复核项。

~~**Bug I**~~ — 已排除，黄色警告是当轮新触发。

**派活记录**（作为项目默认工作法参考）：
- 3 路并行：task-4（codex exec, Bug A+B+F）+ task-5（codex exec, Bug D）+ frontend-copy（general-purpose + sonnet, worklist #8）
- 两个 codex 共享 main working tree，Bug F 先手被 task-4 commit，task-5 跑完看到存在不覆盖，零冲突
- 监控从 30 min cron → 5 min cron（监控到 task-5 越界迹象）→ 20 min cron（兜底挂掉），bash 完成靠系统 notification，无需频繁自查

1b. **[二轮 smoke] 新发现三处问题 — 全部已修**
- 状态：`三处全修，已合 main`（2026-04-21 二次 smoke 发现，2026-04-21~04-24 修复）
- 测试项目：`D:\MyProject\CodeProject\JustTest\.consulting-report\`

**新 Bug 1 ✅（S0 门槛回归，关联旧 1a#Bug C）** — 图5
- 原现象：填完新建项目表单 → 右侧「已完成」直接四项全勾，对话一句没说
- 修法：S0 interview 全套 19 个 task 实施完毕（spec/plan APPROVED 后），`stage_zero_complete` 改成必须落 `s0_interview_done_at` checkpoint 才推进。2026-05-19 后当前落点是 `advance_stage(checkpoint_key="s0_interview_done_at", action="set", reason="...")`；legacy tag 只做 sanitizer。`backend/skill.py` 不再用 `stage_zero_complete = project_overview_ready` 短路。详见 1d 与 0f。
- 关键 commits：`3817c43` / `aca1350` / `916f135` / `0ab565c` / `8f63570`（当时更新 S0 访谈规则，当前已被 `advance_stage` 口径取代）

**新 Bug 2 ✅（tool 结果气泡吞 assistant 正文）** — 图6
- 现象：`✅ 结果: {...}` 气泡把紧跟的 assistant 正文首段一起吞入同一个气泡
- 根因：`frontend/src/components/ChatPanel.jsx:509` 流式拼接 tool 事件时只在前面加 `\n`、尾部不加；后续 `content` 块直接 append 同一行；`utils/chatPresentation.js:64` `splitAssistantMessageBlocks` 按行识别整行以 `✅ 结果:` 开头为 tool block → 把吞进去的正文也算 tool
- 修法：抽 `appendToolEventContent(prev, toolText)` 纯函数（chatPresentation.js），自动补尾 `\n`；ChatPanel.jsx 调用
- commit：`73b345d fix(chat): preserve text after tool events`；前端测试 139→140 passed，`npm run build` 零错
- 附带：codex 多加了 `frontend/tests/index.js`（为让 `node --test tests/` 做显式目录入口，可保留）

**新 Bug 3 ✅（口头"确认"不推进阶段）** — 图8
- 原现象：用户回"确认"（响应模型"请回复'确认大纲'或'按此大纲执行'"），`stage_checkpoints.json` 未写入 `outline_confirmed_at`
- 修法：当时选了决策点 (b) 中期重构，删除 `_WEAK_ADVANCE_BY_STAGE` 弱关键词表，并短期引入 legacy stage tag 解析来替代口头强关键词 fallback。2026-05-19 stage conductor v0 已进一步收敛：模型必须调用 `advance_stage`，legacy tag 只剥离、不落 checkpoint。详见 1d 与 0f。

1c. **[归档] Gemini-era 模型行为硬伤 — 主体修复已合 main，复测路径已被 DeepSeek Migration 取代**
- 状态：`核心兜底全部落地；Gemini reality_test 复测路径已停止推进`
- 测试项目：`D:\MyProject\CodeProject\consulting-report-agent\reality_test\.consulting-report\`（替代旧的 `D:\CodexProject\test\`）
- 模型约束：`gemini-3-flash`（免费批量渠道限制，无法更换）
- 归档说明：不再按 Gemini reality_test 复测路径推进；当前验收以 DeepSeek packaged UI/chat E2E 为准。

**2026-04-24 已落地（α/β/γ/δ 全套）**：
- `content/report_draft_v1.md` 成为正文草稿唯一规范路径；首次成稿/续写走 `append_report_draft`，修改已有正文走 `read_file + edit_file`，禁止用 `write_file` 直接覆盖正文草稿（**δ + 问题 3 修法**）
- 所有已有文件通用要求同一轮先 `read_file`，再 `write_file` / `edit_file`，降低模型拿旧上下文覆盖新文件的概率
- 正文写入工具回传真实落盘字数进度，`append_report_draft` 事件保留真实 tool name，`draft_followup_state` 改成结构化状态，不再从 assistant 文案反推（**β + 问题 1 修法**）
- 混合意图（如"写够 5000 字再导出/质量检查/看文件/看字数"）改为本轮只完成正文写入并给下一步提示，后续动作下一轮单独处理
- 章节改写新增范围校验：`edit_file.new_string` 不能把整篇草稿或多个同级章节塞进单章节替换里
- **反思循环兜底**（**γ 修法，commit `6883bfa fix: require real report draft writes`**）：流式层加 `SELF_CORRECTION_LOOP_MARKERS = ("（修正", "(修正", "（纠正", "(纠正", "停止自言自语")` 累积检测，命中 ≥3 次实时 break；完整 candidate_message 也再检一次；命中后 `MAX_SELF_CORRECTION_RETRIES=1` 给一次重试机会，feedback 让模型停止反思继续真实动作。代码位置 `backend/chat.py:171/1543/3202/3346`

**2026-05-04 reality_test 进展**：
- reality_test 项目走完 S0 interview 后，第一轮收尾撞 `max_iterations=10` 上限，模型刚 fetch_url 第 1 个百科就被截断，references.md 还是空模板
- 系统化调查：单轮内做了 6 次成功 tool 调用 + 1 次失败 write（fetch_url 前置门禁挡的），assistant 输出**零** SELF_CORRECTION_LOOP_MARKERS 命中——撞顶不是病理性循环，是真实工作密度
- 根因：当前架构（先读后写 + fetch_url 前置 + Gemini 3 Flash 串行 tool call）下，单轮"完成 S0 收尾 + 补全 plan + 抓 1-2 条引用"实际需要 11-13 轮，10 不够
- 修复：`max_iterations` 默认值 10 → 20（commit `ec976b8 fix(chat): raise stream max_iterations from 10 to 20`），`_chat_stream_unlocked` + `chat_stream` 两处。非流式 `chat()` 仍 5（仅测试用）。test_chat_runtime 342 passed / 1 skipped 零回归
- 当时重打包已完成（2026-05-04，dist 104 MB / exe 14 MB）；后续不再以 Gemini reality_test 作为当前验收路径。

1d. **[已完成 / 已被 0f supersede] S0 interview + legacy stage signal 19 个 task 全套实施**
- 状态：`全部合 main；阶段推进运行时已在 2026-05-19 被 stage conductor v0 收敛到 advance_stage`
- 关联文档：`docs/superpowers/specs/2026-04-21-s0-interview-and-stage-ack-design.md` / `docs/superpowers/plans/2026-04-21-s0-interview-and-stage-ack-impl.md` / `docs/superpowers/handoffs/2026-04-21-s0-impl-handoff.md`
- 覆盖范围：
  - **S0 硬门禁**（解 1a Bug C / 1b Bug 1）：`stage_zero_complete` 不再依赖 `project_overview_ready`，必须 `s0_interview_done_at` checkpoint 才推进。`backend/skill.py` 新增 `s0_interview_done_at` infra（commit `3817c43`）+ gating（`aca1350`）；`backend/chat.py` 加 S0 软门禁阻挡 LLM 在访谈未完成时直接写 outline / report-draft（commits `0ab565c` / `216f5f1` / `167e10f`）。当前推进方式是 `advance_stage(checkpoint_key="s0_interview_done_at", action="set", reason="...")`。
  - **legacy stage signal**（解 1b Bug 3 的历史实现）：删除整张 `_WEAK_ADVANCE_BY_STAGE` 弱关键词表（`916f135`），短期引入 assistant 尾部控制 tag 解析、流式 tail guard、历史消息 sanitize 和兜底 strip。2026-05-19 后这些 tag 只作为历史残留清理对象，不再触发 checkpoint side effect。
  - **路由 + 配套**：新增 `POST /api/projects/{id}/checkpoints/s0-interview-done`（`504801f`，`action=set` 直接 400）；`workspaceSummary` 暴露 `s0InterviewDone` flag（`31dc7cf`）；`SKILL.md` 当前写明 S0 强制访谈与 `advance_stage` 规则；S2+ 增加"重置 S0"高级回退选项（`2332822`）
  - **migration**：增量 schema 迁移（`cf26609`），legacy 项目不会被新判据推回 S0
- 测试基线：spec 5 轮 / plan 3 轮 codex review；实施期 19 个 task 各 commit 跑 review
- 结论：1a Bug C ✅ / 1b Bug 1 ✅ / 1b Bug 3 ✅ 全部由本块覆盖，无需独立追踪

8. ~~聊天与文件预览复制体验~~ — ✅ 已修，commit `341de44`。根因：PyWebView 的 WebView2 在 Win 下对非输入元素默认禁选；通过 `.selectable-content` 工具类（`-webkit-user-select: text` + `*` 子选择器）在 ChatPanel 气泡 + FilePreviewPanel 预览区放开。右上角复制按钮保留。已进"已解决记录"。

## 历史已解决

0. ⭐ **context-signal-and-intent-tag Phase 2a 实施完成（2026-05-05，13 commits 已合 main）**
- 状态：`Phase 2a 13/13 task done + 5 fix（reviewer catch 真问题）；后续 fix4 / cutover / 删除旧链路已被 Tools redesign 覆盖`
- 关联文档：
  - spec [2026-05-04-context-signal-and-intent-tag-design.md](superpowers/specs/2026-05-04-context-signal-and-intent-tag-design.md)（5 轮 APPROVED）
  - plan [2026-05-04-context-signal-and-intent-tag.md](superpowers/plans/2026-05-04-context-signal-and-intent-tag.md)（6 轮 APPROVED）
  - handoff [2026-05-05-phase2-section-replace-pending.md](superpowers/handoffs/2026-05-05-phase2-section-replace-pending.md)（下次 session cold-start brief）
  - cutover artifact [cutover_report_2026-05-05_fix3.md](superpowers/cutover_report_2026-05-05_fix3.md)
- Phase 2a 实施 task：
  - Task 15-22：13 commits（parser module / tail-guard / preflight 并行 / validate-apply / gate / compare event / report 脚本 / SKILL §S4）
  - Task 19 fix1/2/3 + Task 18 fix1 + Task 20 fix1：5 个 fix 都修了 reviewer catch 的真问题
- 测试基线：GateCanonicalDraftToolCallTests 17/17 + 70 wider sanity 0 failed
- 关键 commits：`8940d70` parser → `234c0fb` tail-guard → `dda3aef` preflight → `1a15b12+6e956fb` validate → `dc2a321+d603042` gate → `cf445e2+ab91fda` compare event → `5a6a5b8` script → `f6ed0e9` SKILL → `a89b081` fix2 → `6112a75` fix3
- Cutover smoke 实测：begin/continue Bug A 修复（fallback work），section/replace 暴露架构缺口（见 0a）
- **归档说明**：fix4、cutover 重测、旧链路删除均已在 2026-05-06 Tools redesign 中完成或取代；本块不再发起后续任务。

1. ⭐ **context-signal-and-intent-tag Phase 1 实施完成（2026-05-04，16 commits 在 `claude/happy-jackson-938bd1`）**
- 状态：`Phase 1 13/13 task done；后续验证和 Phase 2/3 路线已被 Tools redesign / DeepSeek Migration 覆盖`
- 关联文档：
  - spec `docs/superpowers/specs/2026-05-04-context-signal-and-intent-tag-design.md`（5 轮 review APPROVED）
  - plan `docs/superpowers/plans/2026-05-04-context-signal-and-intent-tag.md`（6 轮 review APPROVED）
  - handoff `docs/superpowers/handoffs/2026-05-04-phase1-impl-handoff.md`（cold-start 下个 session 用）
- 5 reality_test bug 状态：
  - **Bug A**（门禁误判）↗ 后由 Phase 2 `<draft-action>` tag 路线处理，最终被 Tools redesign 取代
  - **Bug B**（黄框污染）✅ A1 修：`SystemNotice.surface_to_user` 必填 + `_emit_system_notice_once` 双 dedupe + 服务端过滤
  - **Bug C**（阈值黑盒）✅ A2 修：`_render_progress_markdown` 渲染 `**质量进度**: 5/7 条 有效来源` + tool_result 追加 `quality_hint`
  - **Bug D**（兜底黑洞）✅ A3 修：`_finalize_empty_assistant_turn` helper（永不持久化空 assistant）+ `_coalesce_consecutive_user_messages` + 三层 sanitize（provider build / GET /conversation / 前端）
  - **Bug E**（工具历史零记忆）✅ C1 修：`<!-- tool-log -->` HTML 注释嵌入 assistant content（模型看，前端 strip）
- 编排器：`_finalize_assistant_turn` 重构成 7 步顺序（Task 13），3 个 caller（stream / non-stream / early-finalize）统一调
- 测试基线：pytest 713 passed / 1 skipped / 0 failed（21 min）；frontend 168 passed；dist/咨询报告助手/ 91 MB
- 派活节奏（实施统计参考）：
  - 13 task × ~30-45 min/task ≈ 6-7 小时（含 spec/quality 两阶段 review）
  - 全程 codex exec gpt-5.4 xhigh + PowerShell tool inline env 注入 + 20 min 静默 cron
  - Task 13 编排器整合是最贵的——3 commit（实施 + return value fix1 + 14 旧测试断言修 fix2）
  - chat_runtime suite 11k 行是 pytest 全套主时间瓶颈，reviewer prompt 必须 narrow scope
- **归档说明**：reality_test、Phase 2/3、cutover compare、重打包与文档同步路线已被后续 Tools redesign / DeepSeek Migration 完成或取代；本块只保留历史背景。

1. ⭐ **400 死循环根因清理 + edit_file 工具 + debug dump 转正（2026-04-22）**
- 状态：`已完成`（claude 侧自改自测，未派 codex；测试 509 passed / 1 skipped / 0 failed）
- 根因：`newapi → Gemini` OpenAI 流式兼容层偶发把并行 `functionCall` 的 chunk `index` 合并到 0，导致我方累积层把多个 tool_call 的 `name` 和 `arguments` 首尾拼接成 `"write_filewrite_file"` + `"{...}{...}"`，上游拒收 `400 INVALID_ARGUMENT`
- 代码改动全部在 `backend/chat.py`：
  - **Fix A**（畸形 tool_calls 拦截）：`if collected_message["tool_calls"]:` 分支开头校验每个 tool_call 的 `name in known_tool_names` 且 `arguments` 是合法 JSON；任一畸形 → 本轮作废，append `assistant 占位 + user 反馈` 对子做合规隔板（**单独 append user 反馈会造成连续两条 user → Gemini 角色交替校验 400，踩过一次**），`iterations += 1; continue`
  - **Fix B**（当轮空 content 兜底）：流式和非流式两条 `_finalize_assistant_turn` 之后都加 `if not assistant_message.strip(): assistant_message = "（本轮无回复）"`，避免空 parts 的 assistant 进历史
  - **Fix C**（历史回放兜底）：`_to_provider_message` 对 `role=assistant` 且 `content=""` 的老残迹同样兜底，不依赖干净历史
  - **Fix D**（system prompt 约束）：加 `concurrency_rule`「每轮只发一个 tool_call」—— 实测 Gemini 3 Flash 基本无视，但 Fix A 能兜底合并畸形
- 新工具 `edit_file(file_path, old_string, new_string)`：精确字符串替换，要求 `old_string` 唯一存在；`write_file` 和 `edit_file` 共用抽出来的 `_execute_plan_write(project_id, *, file_path, content, persist_func_name, persist_args)` 方法跑完整 gate 链（S0 block / non-plan-write / fetch-url gate / path normalize / signature / data-log-hint / persist）。`skill/SKILL.md` 新增「文件工具选择」章节，明确 data-log.md / analysis-notes.md 追加条目一律 `edit_file`，`write_file` 只用于新建或整体重写
- 配置：`managed_search_pool.json` `per_turn_searches: 2 → 4`（仍受 `project_minute_limit: 10` / `global_minute_limit: 20` 保护）
- debug dump 转正：`_debug_dump_request` 方法从临时调试代码改成持久辅助工具。路径从 `D:/consulting-debug/` 挪到 `~/.consulting-report/debug/`（跨平台 + 和其他用户数据同目录），每次请求写 `payload-latest.json`（覆盖），失败时另存 `error-{UTC}-{label}.json`（保留）。`label` ∈ `{stream, stream-iter, nostream}`，`note` 字段带 `iteration=N`
- 关键证据：`~/.consulting-report/debug/error-20260422T132039Z-stream.json`（最初定位到 `write_filewrite_file` 畸形 payload）、`error-20260422T135150Z-stream.json`（Fix A 早期实现引入的"连续两条 user"回归证据）
- 后续模型行为问题曾转入 Gemini-era 修复链路；该路径现已归档，当前验收以 DeepSeek packaged UI/chat E2E 为准。

1. ⭐ **stage-advance-gates smoke-test bugfix（Bug A/B/D/F + 前端复制）**
- 状态：`已完成`（2026-04-21 3 路并行派活，全部合 main）
- 5 个 commit：`cb15e4c` / `7e262cf` / `1e180cc`（task-4 Bug A/B/F）+ `4a6a7da` / `88f10d7` / `7a50bb3`（task-5 Bug D）+ `341de44`（frontend-copy 复制体验）
- 测试：后端 403 passed（397→403，+6 新测试）；前端 139 passed；`npm run build` 零错
- 详情见最近已解决 1a；G/H 已移入当前 stage-advance-gates Bug G/H 待复核项。
- 归档说明：二轮 smoke 与重打包后续已完成；新暴露问题已归入 1b / 1d / 当前 stage-advance-gates Bug G/H 待复核项，不再从本历史块发起 smoke。

1. ⭐ **阶段推进门禁重构（stage-advance-gates，Task 1-8 全闭环）**
- 状态：`已完成`（2026-04-21 分支 `feat/stage-advance-gates` 合 main）
- 关联文档：`docs/superpowers/specs/2026-04-17-stage-advance-gates-design.md`、`docs/superpowers/plans/2026-04-17-stage-advance-gates.md`
- 覆盖：
  - Task 1/2 — stage_checkpoints.json storage + length target + quality gate helpers（含 regex 加固）
  - Task 3a/3b/3c — 重写 `_infer_stage_state`（三条件投影）+ migration cascade + `get_workspace_summary` 扩 `checkpoints` / `length_targets` / `quality_progress` / `flags` / `next_stage_hint` / `stalled_since` / `word_count` / `delivery_mode` / `length_fallback_used`
  - Task 4 — `POST /api/projects/{id}/checkpoints/{name}` endpoint + legacy keyword checkpoint detector（strong / weak S4 排除 / rollback / negation 抑制 / `非常同意` 不误伤 / tie-break；2026-05-19 已由 `advance_stage` 取代）+ `_should_allow_non_plan_write` blocking-first 优先级 + 两轮 follow-up（`checkpoint_event` 字段 / OK/ok 大小写 spec 同步 / `SkillEngine.record_stage_checkpoint` 解耦 `backend.main` / 4 张 checkpoint 表 invariant test）
  - Task 5 — `write_file` 自签名拦截 + `system_notice` 三段链路（`_emit_system_notice_once` + stream pop drain + `ChatResponse.system_notices`）
  - Task 6 — `skill/SKILL.md` 阶段推进与工具错误规则
  - Task 7 — 前端 `StageAdvanceControl` + `RollbackMenu` + `ConfirmDialog` + `WorkspacePanel` chip + `ChatPanel` `system_notice` 渲染 + `workspaceSummary` 契约映射 + 7 fix round（`flags.outline_ready` 字段名 / length_fallback chip 非交互 / `delivery_mode` 中文字面量 / "调整大纲"触发 prompt / `next_stage_hint` 消费守护 / checkpoint 错误反馈 + `pending` 态 / ConfirmDialog a11y / 隐藏后台阶段码 / `length_targets.report_word_floor` 契约对齐）
  - Task 8 — 新包 91 MB（dist/咨询报告助手/）
  - Final cross-task review — APPROVED（见 `.codex-run/final-rereview-last.txt`）
- 测试基线（合并前）：后端 397 passed / 1 skipped / 0 failed；前端 139 pass / 0 fail；`npm run build` 零错。
- 派发规则（已成为项目默认）：
  - 实施任务（`--write`）→ 裸 `codex exec`（插件不稳定）；前端 `general-purpose` agent 配 `model: sonnet`
  - Review（read-only）→ 裸 `codex exec`（GPT-5.4 xhigh）
  - 裸 exec 模板：`codex exec --cd "..." --color never --output-last-message .codex-run/X-last.txt < .codex-run/X-prompt.md > .codex-run/X-full.log 2>&1`，bash 传 `run_in_background: true`
  - 30 min cron (`7,37 * * * *`) 做活性自查，完成后自动 `CronDelete`

3. 内置搜索池主链路
- 状态：`已完成`
- 结论：`managed_search_pool.json` 打包注入、运行时状态/缓存、四家 provider 适配器、分层路由、native fallback、chat runtime 接线都已落地。

4. 1.29 GB 异常大包
- 状态：`已完成`
- 根因：之前在 Anaconda 大环境里打包，PyInstaller 把大量无关科学计算/Notebook 依赖一起卷进包。
- 结论：已切到项目 `.venv` 打包，最新包体积约 `91 MB`（含 Task 4/7 新增代码）。

5. 打包脚本不稳
- 状态：`已完成`
- 结论：`build.bat` 已改为薄入口，实际逻辑迁到 `build.ps1`；默认走项目 `.venv`，不再依赖脏全局环境。

6. 前端依赖漏洞
- 状态：`已完成`
- 结论：已升级前端依赖，当前 `npm audit` 为 `0 vulnerabilities`。

7. 阶段事实源与工作流对齐
- 状态：`已完成`
- 关联文档：`docs/superpowers/specs/2026-04-01-stage-facts-and-phase-alignment-design.md`
- 结论：`project-info.md` 已退出正式工作流；阶段推断、正式 plan 文件和门禁规则已对齐。

8. Session memory 重构
- 状态：`已完成`
- 关联文档：`docs/superpowers/specs/2026-04-14-session-memory-rearchitecture-design.md`
- 结论：`conversation_state.json`、memory entries、post-turn compaction 和 provider 上下文顺序已完成重构。

## 已取代 / 废弃

1. Web Search 相关性加固（针对 SearXNG 单后端）
- 状态：`已被取代（Superseded）`
- 关联文档：`docs/superpowers/specs/2026-04-15-web-search-relevance-hardening-design.md`（顶部已加 Superseded banner）
- 取代原因：项目走了**管理型搜索池**路线（`managed-search-pool` 已完成，见"已解决记录"第 3 条），四家 provider + 分层路由，从根本上绕过了 SearXNG 召回质量问题。
- 不要再按这份 spec 落地。保留文档是因为它记录的 SearXNG 实测问题可作为未来搜索策略调整的参考。

## 使用约定

- 只在本文件维护"仍需要行动"的事项。
- 已解决但值得保留上下文的内容，放到"已解决记录"。
- 历史调试记录归档到 `docs/debug-backlog.md`，不再作为当前事实源。
