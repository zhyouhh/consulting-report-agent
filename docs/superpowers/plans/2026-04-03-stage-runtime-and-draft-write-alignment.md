# Stage Runtime And Draft Write Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让阶段跟踪、正文写入许可、研究取证路径与前端展示统一到同一套后端事实，消除“卡阶段 / 假落盘 / 文件漂移 / 工具只搜不读”的连锁问题。

**Architecture:** 以 `backend/skill.py` 中单一的 stage runtime 为核心，根据实质性产物文件推导当前阶段、已完成项、下一步和当前阶段任务，并由后端统一回写 `plan/stage-gates.md`、`plan/progress.md`、`plan/tasks.md`。`backend/chat.py` 复用同一套 runtime 和轻量用户意图判断，决定何时允许写正文并拦截“口头宣称已落盘但实际上未写入”的情况；`web_search` 继续负责找候选来源，`fetch_url` 负责在需要引用外部网页时抓取正文，不引入新数据库或重型状态机。

**Tech Stack:** Python 3.12, FastAPI backend, existing unittest suite, React, Node test runner, current PyInstaller packaging flow.

---

## Locked Decisions

1. `plan/stage-gates.md`、`plan/progress.md`、`plan/tasks.md` 视为“阶段跟踪文件”，后续统一由后端回写，不再要求模型自己维护同步。
   - 这三份文件在实现完成后也不应再允许模型通过 `write_file` 直接写入。
2. `plan/project-overview.md`、`plan/notes.md`、`plan/references.md`、`plan/outline.md`、`plan/research-plan.md`、`plan/data-log.md`、`plan/analysis-notes.md`、`plan/review-checklist.md`、`plan/review.md`、`plan/presentation-plan.md`、`plan/delivery-log.md` 仍然是“实质内容文件”，继续由用户/模型写入。
3. `stage-gates.md` 和 `tasks.md` 不删其一，但职责必须拆开：
   - `stage-gates.md` = 全 8 阶段门禁快照与已完成项。
   - `tasks.md` = 当前阶段的可执行事项，只展示当前阶段，不再重复整张八阶段模板。
4. `progress.md` 改为后端生成的运行时摘要，不再承担“自由发挥日志”职责，否则一定继续漂移。
5. `web_search` 只负责发现候选来源；当模型要把外部网页写入 `references.md`、`notes.md`、`outline.md`、`research-plan.md` 时，必须先对选中的链接调用 `fetch_url` 读取正文。
6. `plan/project-overview.md` 是 `交付形式` 的唯一事实源：
   - `仅报告` => 跳过 S6
   - `报告+演示` => 进入 S6
7. S5 完成证据以 `review-checklist.md` 为准，`review.md` 只作为可选审查记录，不再决定是否进入后续阶段。
8. 不新增新的持久化系统；允许使用现有项目文件、现有会话历史和现有工具调用记录做轻量判断。
9. 当前本地工作区已有未提交改动：`backend/skill.py`、`tests/test_skill_engine.py` 中关于编号型 references 识别的修改必须保留，不要覆盖。

## File Ownership Map

- Modify: `D:\CodexProject\Consult report\consulting-report-agent\backend\skill.py`
  负责 stage runtime、阶段推断、阶段跟踪文件渲染、写文件后的同步。
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\backend\chat.py`
  负责正文写入许可、报告类假落盘检测、`web_search` / `fetch_url` 轻量约束。
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\skill\SKILL.md`
  负责告诉模型哪些文件是后端生成、外部来源什么时候必须 `fetch_url`、什么时候不能直接写正文。
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\skill\modules\consulting-lifecycle.md`
  负责把 S0-S7 生命周期语义、门禁和工具顺序写清楚。
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\skill\plan-template\project-overview.md`
  负责保留 `交付形式` 等正式字段，并作为 S6 是否启用的唯一事实源。
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\skill\plan-template\stage-gates.md`
  负责保留全阶段门禁展示骨架，适配后端投影格式。
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\skill\plan-template\progress.md`
  负责保留摘要模板骨架，适配后端投影格式。
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\skill\plan-template\tasks.md`
  负责改成“仅当前阶段任务”的后端投影模板。
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\frontend\src\components\WorkspacePanel.jsx`
  负责默认预览文件与遗留文件展示顺序的正式口径。
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\frontend\src\utils\workspaceFiles.js`
  负责 `project-overview.md` 默认预览和 `project-info.md` 遗留排序规则。
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\frontend\src\utils\workspaceSummary.js`
  负责继续把后端 `/workspace` 摘要原样映射给 UI，不自行再造阶段逻辑。
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_skill_engine.py`
  负责 runtime、阶段投影、阶段文件回写、references 计数和阶段跳转测试。
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_chat_runtime.py`
  负责正文写入许可、假落盘检测、`fetch_url` 使用约束测试。
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_chat_context.py`
 负责 system prompt / lifecycle 指令对齐测试，以及提示词路径对阶段跟踪文件的自愈测试。
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_workspace_materials.py`
  负责不同交付模式、材料存在与否、S6 可选性等回归。
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\frontend\tests\workspaceSummary.test.mjs`
  负责锁定前端只消费后端阶段摘要，不兜底本地阶段推导。
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\frontend\tests\workspaceFiles.test.mjs`
  负责锁定 `project-overview.md` 为默认预览文件，并将 `project-info.md` 退到次级位置。
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\docs\current-worklist.md`
  负责记录这轮“阶段事实统一修复”的最终落地项与风险说明。

## Task 1: 定义单一 Stage Runtime 契约

**Files:**
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\backend\skill.py`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_skill_engine.py`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_chat_context.py`

- [ ] **Step 1: 写失败测试，锁定“阶段事实只能来自实质文件，不来自 stage tracking 文件本身”**

```python
def test_stale_stage_tracking_files_do_not_force_stage_jump(self):
    (project_dir / "plan" / "tasks.md").write_text("# fake\n\n**阶段**: S4\n", encoding="utf-8")
    (project_dir / "plan" / "progress.md").write_text("# fake\n\n**阶段**: S4\n", encoding="utf-8")

    summary = engine.get_workspace_summary("demo")

    self.assertEqual(summary["stage_code"], "S0")

def test_project_context_reads_backend_synced_tasks_snapshot(self):
    engine.get_workspace_summary("demo")
    context = engine.build_project_context("demo")
    self.assertIn("当前阶段任务", context)
    self.assertNotIn("# fake", context)

def test_build_system_prompt_self_heals_stale_stage_tracking_files_without_workspace_summary_call(self):
    (project_dir / "plan" / "stage-gates.md").write_text("**阶段**: S4", encoding="utf-8")
    (project_dir / "plan" / "progress.md").write_text("**阶段**: S4", encoding="utf-8")
    (project_dir / "plan" / "tasks.md").write_text("**阶段**: S4", encoding="utf-8")

    prompt = handler._build_system_prompt(project["id"])

    self.assertNotIn("**阶段**: S4", prompt)
    self.assertIn("S0", prompt)
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `D:\py312-embed\python.exe -m unittest tests.test_skill_engine tests.test_chat_context -v`

Expected: FAIL，因为当前实现仍会把 `tasks.md` 当成已有内容读入上下文，且 `build_project_context()` / `_build_system_prompt()` 路径没有统一 runtime 自愈入口。

- [ ] **Step 3: 写最小实现，新增单一 runtime 构建函数**

在 `backend/skill.py` 中收敛出一个单点入口，类似：

```python
def _build_stage_runtime(self, project_path: Path) -> dict:
    return {
        "stage_code": "S1",
        "status": "进行中",
        "completed_items": [...],
        "next_actions": [...],
        "current_stage_title": "S1 研究设计",
        "current_stage_goal": "...",
        "current_stage_tasks": [...],
        "presentation_required": False,
    }
```

要求：

1. `stage_code`、`completed_items`、`next_actions` 继续基于实质文件推导。
2. `tasks.md`、`progress.md`、`stage-gates.md` 不再作为阶段输入。
3. `build_project_context()` 在读取上下文前必须先走同一套 runtime 同步，确保聊天提示词路径与 `/workspace` 路径一致。
4. 保留现有 `_count_reference_evidence()` 的编号型 references 识别，不得回退。

- [ ] **Step 4: 再跑测试确认通过**

Run: `D:\py312-embed\python.exe -m unittest tests.test_skill_engine tests.test_chat_context -v`

补充要求：
1. S5 完成证据改为 `review-checklist.md`；`review.md` 只作为可选审查记录，不再决定阶段是否完成。
2. `tests.test_workspace_materials` 中与 S5/S7 相关的旧预期要和这条语义在同一轮 TDD 中一起调整，不能拖到最后回归时再兜底。

Expected: PASS，且 runtime 已成为后续所有阶段文件投影的唯一输入。

- [ ] **Step 5: Commit**

由于当前工作区已脏，先不做中间提交；记录完成状态，待最终集成时仅 stage 已审阅的改动。

## Task 2: 让三份阶段跟踪文件全部由后端投影生成

**Files:**
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\backend\skill.py`
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\skill\plan-template\stage-gates.md`
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\skill\plan-template\progress.md`
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\skill\plan-template\tasks.md`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_skill_engine.py`

- [ ] **Step 1: 写失败测试，锁定三个文件的职责边界**

```python
def test_workspace_summary_rewrites_all_stage_tracking_files_from_same_runtime(self):
    engine.get_workspace_summary("demo")

    stage_gates = (project_dir / "plan" / "stage-gates.md").read_text(encoding="utf-8")
    progress = (project_dir / "plan" / "progress.md").read_text(encoding="utf-8")
    tasks = (project_dir / "plan" / "tasks.md").read_text(encoding="utf-8")

    self.assertIn("**阶段**: S0", stage_gates)
    self.assertIn("**阶段**: S0", progress)
    self.assertIn("**阶段**: S0", tasks)

def test_tasks_projection_only_lists_current_stage_actions(self):
    engine.get_workspace_summary("demo")
    tasks = (project_dir / "plan" / "tasks.md").read_text(encoding="utf-8")
    self.assertIn("## 当前阶段待办", tasks)
    self.assertNotIn("### S6 演示准备", tasks)

def test_stage_tracking_files_cannot_be_written_through_write_file(self):
    for path in ("plan/stage-gates.md", "plan/progress.md", "plan/tasks.md"):
        with self.assertRaises(ValueError):
            engine.write_file("demo", path, "# stale")
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `D:\py312-embed\python.exe -m unittest tests.test_skill_engine -v`

Expected: FAIL，因为当前只有 `stage-gates.md` 会在 `get_workspace_summary()` 中被重写，`progress.md` 和 `tasks.md` 仍然是静态模板或模型产物，而且 `write_file` 仍允许覆盖这三份阶段跟踪文件。

- [ ] **Step 3: 写最小实现，新增统一投影渲染器**

在 `backend/skill.py` 中实现：

```python
def _sync_stage_tracking_files(self, project_path: Path, runtime: dict | None = None) -> dict: ...
def _render_stage_gates_markdown(self, runtime: dict) -> str: ...
def _render_progress_markdown(self, runtime: dict) -> str: ...
def _render_tasks_markdown(self, runtime: dict) -> str: ...
```

具体要求：

1. `stage-gates.md` 保留 S0-S7 全量门禁视图。
2. `progress.md` 仅展示当前阶段摘要、完成项、下一步、阻塞，不保留可被模型自由发挥的历史区块。
3. `tasks.md` 仅展示当前阶段目标、待办、进入下一阶段前必须满足的条件，不再重复八阶段全表。
4. `create_project()` 初始化后立即同步三份文件，避免项目刚创建时就是旧模板。
5. `validate_plan_write()` / `write_file()` 对 `plan/stage-gates.md`、`plan/progress.md`、`plan/tasks.md` 直接拒写，并返回“这些文件由后端自动生成”的明确错误。
6. `get_workspace_summary()` 只读 runtime，不再手搓额外阶段语义。

- [ ] **Step 4: 再跑测试确认通过**

Run: `D:\py312-embed\python.exe -m unittest tests.test_skill_engine -v`

Expected: PASS，三个阶段跟踪文件内容对齐到同一 stage runtime。

- [ ] **Step 5: Commit**

由于当前工作区已脏，先不做中间提交；记录完成状态，待最终集成时仅 stage 已审阅的改动。

## Task 3: 在写文件链路里同步阶段跟踪，并修正正文写入许可

**Files:**
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\backend\skill.py`
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\backend\chat.py`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_skill_engine.py`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_chat_runtime.py`

- [ ] **Step 1: 写失败测试，锁定“写完实质文件后，阶段跟踪文件必须自动刷新”**

```python
def test_write_file_refreshes_stage_tracking_after_outline_and_research_plan(self):
    engine.write_file("demo", "plan/notes.md", NOTES)
    engine.write_file("demo", "plan/references.md", REFERENCES)
    engine.write_file("demo", "plan/outline.md", OUTLINE)
    engine.write_file("demo", "plan/research-plan.md", RESEARCH_PLAN)

    tasks = (project_dir / "plan" / "tasks.md").read_text(encoding="utf-8")
    self.assertIn("**阶段**: S2", tasks)
```

- [ ] **Step 2: 写失败测试，锁定“继续吧 / 继续哈”在正确上下文中能续写正文”**

```python
def test_should_allow_non_plan_write_accepts_continue_variants_after_draft_started(self):
    self.assertTrue(handler._should_allow_non_plan_write(project["id"], "继续吧"))
    self.assertTrue(handler._should_allow_non_plan_write(project["id"], "继续哈"))

def test_should_allow_non_plan_write_stays_blocked_before_report_stage(self):
    self.assertFalse(handler._should_allow_non_plan_write(project["id"], "继续吧"))

def test_should_allow_non_plan_write_uses_recent_conversation_history_after_outline_confirmation(self):
    handler._save_conversation(
        project["id"],
        [
            {"role": "user", "content": "大纲没问题，继续写正文吧"},
            {"role": "assistant", "content": "收到，继续完善正文。"},
        ],
    )
    self.assertTrue(handler._should_allow_non_plan_write(project["id"], "继续"))

def test_should_allow_non_plan_write_respects_newer_blocking_instruction(self):
    handler._save_conversation(
        project["id"],
        [
            {"role": "user", "content": "大纲没问题，继续写正文吧"},
            {"role": "assistant", "content": "收到。"},
            {"role": "user", "content": "先别写正文，先补计划"},
        ],
    )
    self.assertFalse(handler._should_allow_non_plan_write(project["id"], "继续"))
```

- [ ] **Step 3: 运行测试并确认失败**

Run: `D:\py312-embed\python.exe -m unittest tests.test_skill_engine tests.test_chat_runtime -v`

Expected: FAIL，因为 `write_file()` 目前不会同步 `progress.md` / `tasks.md` / `stage-gates.md`，且 `_should_allow_non_plan_write()` 既不识别“继续哈”，也不会继承前序已确认的正文续写意图。

- [ ] **Step 4: 写最小实现，打通同步链路和轻量正文许可**

实现要求：

1. `SkillEngine.write_file()` 在写入以下文件后自动调用 `_sync_stage_tracking_files()`：
   - 任意正式实质 `plan/*.md`
   - `report_draft_v1.md`
   - `content/report.md`
   - `content/draft.md`
   - `output/final-report.md`
2. `ChatHandler._should_allow_non_plan_write()` 改为结合以下因素判断：
   - 当前消息中的明确许可词，补上“继续吧”“继续哈”等口语变体。
   - 最近会话里是否已经有正文续写许可且未被新约束覆盖。
   - 是否已经存在报告草稿。
   - 当前 runtime 是否已进入可合理写正文的阶段。
3. 用显式的“最近会话扫描” helper 实现跨轮判断，优先从现有 conversation/history 派生，不额外新建数据库表。

- [ ] **Step 5: 再跑测试确认通过**

Run: `D:\py312-embed\python.exe -m unittest tests.test_skill_engine tests.test_chat_runtime -v`

Expected: PASS，写完实质文件后阶段文件自动刷新，正文续写不再因为口语许可或跨轮上下文丢失而误拦截。

- [ ] **Step 6: Commit**

由于当前工作区已脏，先不做中间提交；记录完成状态，待最终集成时仅 stage 已审阅的改动。

## Task 4: 修正报告类假落盘检测，并补齐 `web_search` / `fetch_url` 约束

**Files:**
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\backend\chat.py`
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\skill\SKILL.md`
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\skill\modules\consulting-lifecycle.md`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_chat_runtime.py`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_chat_context.py`

- [ ] **Step 1: 写失败测试，锁定报告草稿类文件也属于“已宣称必须真的写入”的集合**

```python
def test_expected_writes_include_report_draft_targets_when_assistant_claims_report_saved(self):
    expected = handler._expected_file_writes_for_message("已写入 `report_draft_v1.md` 并完成正文初稿。")
    self.assertIn("report_draft_v1.md", expected)
```

- [ ] **Step 2: 写失败测试，锁定“只 `web_search` 不 `fetch_url`”时不能宣称已阅读外部网页正文**

```python
def test_chat_stream_retries_when_assistant_claims_external_read_without_fetch_url(self):
    ...
    self.assertIn("请先使用 `fetch_url`", retry_prompt)
```

- [ ] **Step 3: 运行测试并确认失败**

Run: `D:\py312-embed\python.exe -m unittest tests.test_chat_runtime tests.test_chat_context -v`

Expected: FAIL，因为当前缺少报告草稿类目标的假落盘追踪，且 `web_search` 返回 snippet 后模型就可能停止，不会被进一步提醒调用 `fetch_url`。

- [ ] **Step 4: 写最小实现，扩展假落盘追踪和外部网页使用约束**

实现要求：

1. 将以下文件纳入 assistant message 的“已宣称写入必须真实落盘”检测：
   - `report_draft_v1.md`
   - `content/report.md`
   - `content/draft.md`
   - `output/final-report.md`
2. 将现有 `_expected_plan_writes_for_message()` / `_get_missing_expected_writes()` 扩展或重命名为更泛化的文件写入检测 helper，例如 `_expected_file_writes_for_message()`。
3. 记录本轮成功工具调用类型；若 assistant 在本轮宣称“已查阅官网/公开网页/外部链接”并更新 `references.md`、`notes.md`、`outline.md`、`research-plan.md`，但本轮没有 `fetch_url`，则像现在的 missing-write retry 一样追加纠偏提示并重试。
4. 在 `skill/SKILL.md` 与 `consulting-lifecycle.md` 明确写死：
   - `web_search` = 找候选来源
   - `fetch_url` = 读候选来源正文
   - 只有读过正文，才能把外链当成已阅读依据写入正式文件
5. 这一步只做“轻量强约束”，不引入新的网页缓存系统。

- [ ] **Step 5: 再跑测试确认通过**

Run: `D:\py312-embed\python.exe -m unittest tests.test_chat_runtime tests.test_chat_context -v`

Expected: PASS，报告正文类文件不再能假装落盘，外部网页使用也不会停留在“只搜不读”。

- [ ] **Step 6: Commit**

由于当前工作区已脏，先不做中间提交；记录完成状态，待最终集成时仅 stage 已审阅的改动。

## Task 5: 清理残留四阶段遗毒，并完成全链路回归

**Files:**
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\backend\skill.py`
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\backend\chat.py`
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\skill\SKILL.md`
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\skill\modules\consulting-lifecycle.md`
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\frontend\src\utils\workspaceSummary.js`
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\frontend\tests\workspaceSummary.test.mjs`
- Modify: `D:\CodexProject\Consult report\consulting-report-agent\docs\current-worklist.md`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_skill_engine.py`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_workspace_materials.py`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_chat_runtime.py`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_chat_context.py`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_main_api.py`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\tests\test_stream_api.py`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\frontend\tests\workspaceSummary.test.mjs`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\frontend\tests\workspaceFiles.test.mjs`
- Test: `D:\CodexProject\Consult report\consulting-report-agent\frontend\tests\workspacePanelState.test.mjs`

- [ ] **Step 1: 先做一次残留扫描，列出所有仍然使用旧四阶段口径的代码或文案**

Run: `Select-String -Path '.\backend\**\*','.\skill\**\*','.\frontend\src\**\*' -Pattern '阶段0|阶段1|阶段2|阶段3|阶段4|项目初始化|大纲设计|分段撰写|整合导出'`

Expected: 只留下与当前八阶段体系兼容的命名；若仍有旧词，先逐个清掉。

- [ ] **Step 2: 写失败测试，锁定前端只消费后端摘要，不自行复刻阶段体系**

```javascript
test("summarizeWorkspace returns backend stage payload without local phase remapping", () => {
  const summary = summarizeWorkspace({ stage_code: "S6", status: "进行中" });
  assert.equal(summary.stageLabel, "S6");
  assert.equal(summary.statusLabel, "进行中");
});
```

- [ ] **Step 3: 更新 worklist，记录本轮收敛后的职责分工**

写入至少以下结论：

```markdown
- `stage-gates.md`、`progress.md`、`tasks.md` 改为后端回写
- 模型不再允许通过 `write_file` 直接改这三份阶段跟踪文件
- `stage-gates.md` = 全阶段门禁，`tasks.md` = 当前阶段任务，二者不再重复
- 正文写入许可改为 runtime + 会话意图联合判断
- `web_search` 只负责找链接，`fetch_url` 负责读链接正文
```

- [ ] **Step 4: 运行后端回归**

Run: `D:\py312-embed\python.exe -m unittest tests.test_skill_engine tests.test_workspace_materials tests.test_chat_runtime tests.test_chat_context tests.test_main_api tests.test_stream_api -v`

Expected: PASS，覆盖阶段推断、材料识别、正文写入、API 摘要输出与提示词约束。

- [ ] **Step 5: 运行前端回归**

Run: `node --test .\tests\workspaceSummary.test.mjs .\tests\workspaceFiles.test.mjs .\tests\workspacePanelState.test.mjs`

Workdir: `D:\CodexProject\Consult report\consulting-report-agent\frontend`

Expected: PASS，前端阶段展示继续只消费后端 `/workspace` 摘要。

- [ ] **Step 6: 运行打包级别的最小 sanity check**

Run: `npm run build`

Workdir: `D:\CodexProject\Consult report\consulting-report-agent\frontend`

Expected: frontend build 成功。

Run: `D:\py312-embed\python.exe -m PyInstaller consulting_report.spec --noconfirm`

Workdir: `D:\CodexProject\Consult report\consulting-report-agent`

Expected: PyInstaller 成功产出新的 `dist\` 构建目录。

- [ ] **Step 7: Commit**

在所有代码评审和打包验证完成后，一次性选择已审阅的目标 hunks / 文件进行最终提交，避免把当前工作区里无关的已有改动误打包进中间提交。

## Review Checklist For This Plan

- [ ] 没有引入新的持久化系统或重型状态机。
- [ ] 三份阶段跟踪文件的“后端回写”边界写清楚了。
- [ ] `stage-gates.md` 与 `tasks.md` 的职责拆分清楚了，不再是重复模板。
- [ ] “继续吧 / 继续哈”类口语许可有明确修复路径。
- [ ] 报告正文类文件已纳入假落盘检测。
- [ ] `web_search` 与 `fetch_url` 的职责分工和轻量约束都写清楚了。
- [ ] 保留并兼容现有 `_count_reference_evidence()` 的编号型来源识别修复。
- [ ] 最终回归覆盖后端、前端和打包 sanity check。
