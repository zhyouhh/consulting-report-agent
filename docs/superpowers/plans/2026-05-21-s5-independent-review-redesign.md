# S5 Independent Review Redesign Implementation Plan

> **Version**: v5 (response to codex R1 + R2 + R3 + R4 reviews)
> **状态**: R4 CHANGES_NEEDED 已处理，待 R5 review

## R4 Round 4 Review Response Annex

Codex R4 verdict: **CHANGES_NEEDED**。R3 7 项中 6 FIXED + 1 PARTIAL，2 个真阻断 + 1 个非阻断 note：

| R4 Item | 严重度 | 处理章节 | 概述 |
|---|---|---|---|
| **Bug 14 PARTIAL → 真问题** — `_finalize_empty_assistant_turn` 修改引入新破坏 | 高 | Task 3.4 Step 2 重写 | 真实函数（chat.py:1059-1076）**本来就不 append empty assistant**——这是 invariant"不持久化 empty assistant 避免污染下轮 prompt"。我 plan 写"`history.append({"role": "assistant", "content": fallback_content})`"是错的。修：只按 `system_triggered` 跳过 `history.append(current_user_message)`，不动 assistant fallback 处理 |
| **R1 Bug 2 carry-over → 真问题** — Task 4.2 S5 welcome 用 `messages.append(...)` | 高 | Task 4.2 重写 | `_chat_stream_unlocked` 里**没有** `messages` 变量——provider conversation 每轮由 `_build_provider_turn_conversation` 构造（chat.py:2501）。修：S5 welcome 也走 Task 3.0 的 `additional_system_messages` 通道，复用 Task 3.3 顶部分支结构（`elif self._should_emit_s5_welcome(project_id):`） |
| **R4 note** — 多个 system messages 兼容 | N/A | 不阻断 | OpenAI / DeepSeek 都支持多 system messages（验证过文档）；关键是不持久化 transient prompt——v5 设计满足 |

---

## R3 Round 3 Review Response Annex

Codex R3 verdict: **CHANGES_NEEDED**。R2 7 项中 4 FIXED + 2 PARTIAL + 1 PARTIAL(typo)，4 个新 Bug + 3 改进建议：

| R3 Item | 严重度 | 处理章节 | 概述 |
|---|---|---|---|
| **Bug 13** — system-trigger prompt 在 tool follow-up 轮次丢失 | 高 | Task 3.3 重写 Step 3 | 真实每轮都重新 append user message + helper 总 append；改为**每轮都传** `additional_system_messages=[trigger_prompt]`（不只 iteration==0）。`include_current_user=False`，`current_user_message` 是 placeholder dict（不进 history） |
| **Bug 14** — `current_user_message=None` 在 stream 尾部 `.get` 崩 | 高 | Task 3.3 + 3.4 修正 | 真实尾部 `current_user_message.get("content")` 两次（chat.py:2820-2831）+ `_finalize_empty_assistant_turn` 也 append `current_user_message`。改为 `current_user_message = {"role": "user", "content": ""}` 占位 + `_finalize_assistant_turn` 内 `system_triggered` 分支跳过 persist |
| **Bug 15** — packaged smoke 不能调 LLM endpoint | 中 | Task 5.2 重写 | smoke "不调 /api/chat 不消耗额度"（smoke:7-9）+ 项目停 S0。改 smoke 只测：非 S5 endpoint 返回 400 + 模板/脚本存在。S5 正常流 / lock / SSE content-type 放 `test_main_api.py` 用 mock agent |
| **Bug 16** — `buildChatRequest` 改造破坏现有前端测试 | 中 | Task 4.5 Step 8 补 | `frontend/tests/chatMaterials.test.mjs:35-75` 锁定 trim + 空字段省略。改写：sendMessage 在 ChatPanel 内 trim；system-trigger 允许空 messageText；可选字段仍只在非空写入 payload |
| **改进建议 1** — plan v2 → v3 typo | 低 | 顶部 note | "以 plan v2 为准" 改 "v4" |
| **改进建议 2** — Task 3.3 残留旧描述 | 低 | Task 3.3 清理 | 删 `_chat_stream_system_triggered` 旧 method / Step 2 旧 SYSTEM_TRIGGER_PROMPTS 引用（新方案是 `_chat_stream_unlocked` 内分支） |
| **改进建议 3** — Task 3.0 加 budget/compression 测试 | 低 | Task 3.0 补 | system-trigger prompt 在长 history 下不被 context fitting 裁掉 |

---

## R2 Round 2 Review Response Annex

Codex R2 verdict: **CHANGES_NEEDED**。R1 11 项 10 FIXED + 1 PARTIAL，新增 4 Bug + 3 Suggestion：

| R2 Item | 严重度 | 处理章节 | 概述 |
|---|---|---|---|
| **Bug 2 PARTIAL → Bug 10 真问题** — `_run_stream_turn` 抽取方案不足落地 | 高 | Task 3.0 + 3.3 重写 | 真实 stream loop 跨 400 行 + 多 yield + 大量闭包变量，Option A 抽 helper 不可行。改方案三：`_chat_stream_unlocked` 内加 `if system_triggered:` 分支 + 用 Task 3.0 扩展的 `_build_provider_turn_conversation` 注入 transient system message。不抽 helper 不复制 loop |
| **Bug 9** — Task 3.0 helper 签名写错 | 中 | Task 3.0 修正 | 真实签名第二参是 `history`（不是 `conversation`）；正确签名 `(project_id, history, current_user_message, current_turn_messages=None, *, exclude_current_turn_memory=False)`。新参数放 keyword-only 区域；测试用真实签名 |
| **Bug 11** — 旧 review-checklist 测试面未清完 | 高 | Task 4.1 / 4.3 Rewrite step 扩展 | 还有 7 处测试硬编码旧契约：`test_chat_runtime.py:5336-5456 / 5901-5924 / 6041-6052` 写入门禁；`test_main_api.py:570-573` API system notice；`test_skill_engine.py:185-189 / 1584-1591 / 1609-1618` helper 推进 S5 |
| **Bug 12** — 前端自动触发主代理 turn 少 workspace ready 二次确认 | 中 | Task 4.4 / 4.5 补 | spec §5.4 要求收到 `review-completed` 后先 GET workspace 确认 `independent_review_ready` / `lint_report_ready` 再 trigger system turn。plan 缺该检查 |
| **改进建议 1** — spec 仍写 EXPECTED_PLAN_FILES | 低 | plan 顶部 note | 加 "以 plan v2 为准"（spec 已 APPROVED 不动）|
| **改进建议 2** — startStream useCallback + 防重入 | 低 | Task 4.5 补 | 显式说 startStream 必须 useCallback，复用 `loading/uploading/abortControllerRef` 防重入 |
| **改进建议 3** — buildChatRequest messageText 默认 "" | 低 | Task 4.5 补 | 避免 system-trigger 调用漏传时 `.trim()` 抛错 |

**注**：spec v6 `EXPECTED_PLAN_FILES` 字面量是历史遗留——以 plan v4 中 `REQUIRED_PLAN_FILES` 为准（与真实 `tests/smoke_packaged_app.py:39` 一致）。

---

## R1 Round 1 Review Response Annex

Codex R1 verdict: **CHANGES_NEEDED**。8 个 Bug + 3 个 Suggestion 处理对照：

| R1 Item | 严重度 | 处理章节 | 概述 |
|---|---|---|---|
| **Bug 1** — `_has_effective_review_checklist` 仍在生产路径 | 高 | Task 4.1 加 Step | Commit 4 显式删 `_stage_five_completion_state:474` / `_infer_stage_state:1564,1624-1626` / `_build_completed_items:1679,1683` 中的旧 helper 调用，旧字段固定 `False`，`review_ready` 改为 `review_reports_ready and review_passed` |
| **Bug 2** — chat loop 插入点不可按 plan 实现 | 高 | 新增 Task 3.0 + 改 Task 3.3 / 4.2 | 真实 helper 是 `_build_provider_turn_conversation`（chat.py:3243），不是 `_build_provider_messages`。先扩展 helper 加 `additional_system_messages` + `include_current_user` 参数，system_trigger 和 welcome 通过统一入口注入 |
| **Bug 3** — 前端 App.jsx wiring 漏 | 高 | Task 4.5 加 App.jsx | ChatPanel/WorkspacePanel 是兄弟组件（App.jsx:230-253），不改父组件无法把 drawer 完成事件接到 ChatPanel.startStream。用 `forwardRef + useImperativeHandle` 暴露 `triggerSystemTurn` |
| **Bug 4** — Commit 4 打破现有测试，plan 没覆盖 | 高 | Task 4.1 / 4.3 加 Rewrite Step | 列出具体要改的：`tests/test_skill_engine.py:292-309 / 442-452 / 1234-1270` + `tests/test_workspace_materials.py:240-245` 硬编码旧 S5 契约 |
| **Bug 5** — 前端组件测试写法不匹配现有测试栈 | 高 | Task 4.4 重写测试 | `frontend/package.json` 没 jsdom / testing-library / react-test-renderer。改为：纯函数测试（SSE parser / button visibility）+ source-level guard（grep AbortController / ESC / review-completed 分支） |
| **Bug 6** — smoke 常量名错 + endpoint smoke 缺失 | 中 | Task 4.6 / 5.2 | `REQUIRED_PLAN_FILES`（不是 `EXPECTED_PLAN_FILES`）；Task 5.2 扩展 smoke 调 `/lint-report` + 保留 `/quality-check` + 模板存在性 |
| **Bug 7** — SSE 断连后端处理漏 | 中 | Task 3.1 补 | endpoint signature 加 `request: Request`，generator 内 `is_disconnected()` 检测；前端 abort 后端 generator 释放 lock |
| **Bug 8** — `ChatHandler` 构造参数顺序错 | 低 | Task 2.3 | 真实是 `ChatHandler(settings, skill_engine)`（chat.py:377）；测试示例改用命名参数 |
| **Suggestion 1** — Commit 4 回滚脚本级策略 | 低 | Risk & Rollback 补 | 写清回滚 Commit 4 后 Commit 2/3 dormant 仍稳；用户数据保留 |
| **Suggestion 2** — Task 1.1 validator 测试不触发真实 stream | 低 | Task 1.1 改测试 | 测 `ChatRequest.model_validate` 或 patch `get_chat_handler` |
| **Suggestion 3** — PowerShell BOM 保留 | 低 | Task 2.4 补 | `tests/test_skill_assets.py:20-44` 锁定 `.ps1` BOM 和 `[Console]::OutputEncoding`，重构脚本时显式保留 |

---

> **For agentic workers:** Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace S5 model-self-evaluation review-checklist mechanism with: (a) user-triggered independent review agent (separate LLM context); (b) refactored AI-味自查 script with 4 dimensions; (c) auto-trigger main-agent turn on report ready; (d) phase-gated UI buttons. Old `review-checklist.md` retires.

**Architecture:** Spec defines 5-commit atomic phasing — Commit 1-3 全 dormant（添加功能但不切换用户路径），Commit 4 = user-visible atomic cutover（backend gate + SKILL 文档 + 前端按钮一次性落 main）。S0-S4 流程零变更，硬约束。

**Tech Stack:** Python 3.11/3.12 + FastAPI + OpenAI SDK + DeepSeek V4 Pro managed channel；React + Tailwind + Node native test runner；PyInstaller Windows packaging。

---

## Existing Context

- **Worktree**: 本地 main 分支（不要 push 除非用户明确要求）
- **Spec**: `docs/superpowers/specs/2026-05-21-s5-independent-review-redesign-design.md`（v6, 1990+ 行，经 R1-R6 6 轮 codex review APPROVED）
- **main HEAD**: `607453c`（截至 2026-05-21 拉取）
- **R6 verdict**: APPROVED——所有 27 个 Bug + 14 个 Suggestion 都已 resolve 进 spec

### 硬约束（用户原话）

> "前面 s0 到写完文章的流程基本上都顺了的，所以改代码注意不要改到前面的东西，那些逻辑别又改坏了。"

S0-S4 的所有 helper / gate / 工具实现**不动**，包括：

- `backend/skill.py:_infer_stage_state()` 中 S0-S4 投影
- `backend/chat.py` 中 S0 interview 软门禁、`fetch_url` 前置门禁、`append_report_draft` / `edit_file` canonical draft dispatcher
- `backend/report_writing.py` 全文
- `_has_effective_data_log()` / `_has_effective_analysis_notes()` / `_has_effective_report_draft()` 判定

### Commit 拆分原则（来自 spec §13）

| Commit | 性质 | 关键约束 |
|---|---|---|
| 1 | 100% additive dormant | 不动 `FORMAL_PLAN_FILES`、不动主代理路径、不加新模板 |
| 2 | 内 atomic | FORMAL_PLAN_FILES 加新文件 + 主代理拒写拦截 **同一 commit**（避免独立性边界破洞）+ 审查代理 + lint 脚本 |
| 3 | dormant | endpoints / system_trigger / finalize 分支就绪，但 S5 welcome 调用点不接（避免提示用户点不存在按钮）|
| 4 | **用户可见 atomic cutover** | CHECKPOINT_PREREQ 切换 + S5 welcome 激活 + SKILL 文档 + 前端按钮 + smoke 同步 |
| 5 | 端到端 + cutover doc | piggy-v2 走 S0-S7 + dist 重建 + cutover report |

---

## File Map

### 新增

- `backend/independent_review.py` — 独立审查代理实现
- `frontend/src/components/IndependentReviewDrawer.jsx` — drawer 组件
- `skill/plan-template/independent-review.md` + `lint-report.md` — stub 模板
- `tests/test_independent_review.py` + `tests/test_lint_report.py`
- `frontend/tests/independentReviewDrawer.test.mjs`
- `frontend/tests/stagePanelButtons.test.mjs` + `frontend/tests/chatPanelStartStream.test.mjs`
- `docs/superpowers/cutover_report_2026-05-21_s5-redesign.md`

### 修改

| 文件 | 修改范围 |
|---|---|
| `backend/main.py` | 新 endpoints + `chat_stream` 接 `system_trigger` |
| `backend/chat.py` | `_chat_stream_system_triggered` + `_finalize_assistant_turn` 分支 + S5 welcome helper/调用 + 主代理拒写 + DeepSeek helper 复用 |
| `backend/models.py` | `ChatRequest` 字段扩展 + Pydantic validator |
| `backend/skill.py` | `FORMAL_PLAN_FILES` + `_has_effective_*` + `_stage_five_completion_state` + `CHECKPOINT_PREREQ` + `STAGE_CHECKLIST_ITEMS` + `_build_completed_items` + `_infer_stage_state` flags + conversation_state 三处 + `record_stage_checkpoint` lock |
| `backend/report_tools.py` | `run_lint_report` 新增 + `run_quality_check` 保留 shape |
| `skill/scripts/quality_check.ps1` | 重构为 4 维度 + 参数化 |
| `skill/SKILL.md` | S5 段重写 |
| `skill/modules/consulting-lifecycle.md` | 行 20 同步 |
| `skill/plan-template/progress.md` / `stage-gates.md` / `tasks.md` | S5 段同步 |
| `frontend/src/components/StagePanel.jsx` | 按钮阶段化 |
| `frontend/src/components/WorkspacePanel.jsx` | drawer trigger |
| `frontend/src/components/ChatPanel.jsx` | `sendMessage` 重构为 `startStream` |
| `frontend/src/utils/workspaceSummary.js` | 新 flags 字段映射 |
| `tests/test_main_api.py` / `test_chat_runtime.py` / `test_skill_engine.py` / `test_packaging_docs.py` / `test_skill_assets.py` / `test_report_tools.py` / `smoke_packaged_app.py` | 同步扩展 |

### 退役（保留兼容）

- `_has_effective_review_checklist()` — 保留但不再被生产路径调用
- `plan-template/review-checklist.md` — 文件保留，但 FORMAL_PLAN_FILES 在 Commit 4 移除
- `POST /api/projects/{id}/quality-check` — endpoint 保留 + 返回 shape 不变（向后兼容）

---

# Commit 1：后端 100% additive dormant 基础设施

不动 FORMAL_PLAN_FILES，不动主代理路径，不加新模板。所有新函数、新字段、新 schema 都不被任何门禁调用。

## Task 1.1: ChatRequest 字段扩展 + Pydantic validator

**Files:**

- Modify: `backend/models.py`
- Test: `tests/test_main_api.py`

**Steps:**

- [ ] **Step 1**: 在 `backend/models.py` `ChatRequest` 改 `message_text` 默认值 + 加 `system_trigger` 字段：

  ```python
  from typing import Literal, Optional
  from pydantic import BaseModel, Field, model_validator
  
  SystemTriggerType = Literal["independent_review_done", "lint_report_done"]
  
  class ChatRequest(BaseModel):
      project_id: str = Field(..., min_length=1, max_length=100)
      message_text: str = Field(default="", max_length=10000)  # 改 Optional
      attached_material_ids: list[str] = Field(default_factory=list)
      transient_attachments: list[Attachment] = Field(default_factory=list)
      system_trigger: Optional[SystemTriggerType] = None  # 新增
      
      @model_validator(mode="after")
      def validate_message_or_trigger(self):
          if self.system_trigger is None:
              if not self.message_text or not self.message_text.strip():
                  raise ValueError("message_text must be non-empty when system_trigger is None")
          return self
  ```

- [ ] **Step 2**: 加测试 `tests/test_main_api.py::test_chat_request_validator_rejects_empty_message_without_trigger`（**R1 Suggestion 2 修正**：直接测 `ChatRequest.model_validate` 避免触发真实 LLM stream）：

  ```python
  from pydantic import ValidationError
  from backend.models import ChatRequest
  
  def test_chat_request_rejects_empty_message_without_trigger(self):
      # 直接测 Pydantic validator，不通过 endpoint（避免 mock 真实 chat handler）
      with self.assertRaises(ValidationError):
          ChatRequest.model_validate({
              "project_id": "demo",
              "message_text": "",
              "system_trigger": None,
          })
  
  def test_chat_request_accepts_empty_message_with_trigger(self):
      req = ChatRequest.model_validate({
          "project_id": "demo",
          "message_text": "",
          "system_trigger": "independent_review_done",
      })
      assert req.system_trigger == "independent_review_done"
      assert req.message_text == ""
  
  def test_chat_request_accepts_non_empty_message_without_trigger(self):
      # 现有正常 turn 不受影响
      req = ChatRequest.model_validate({
          "project_id": "demo",
          "message_text": "hello",
      })
      assert req.system_trigger is None
  ```

**Acceptance Criteria:**

- `pytest tests/test_main_api.py -k "chat_request"` 全过
- 现有 `/api/chat/stream` endpoint 不需要变化——`ChatRequest.model_validate` 自动应用 validator
- 老前端 POST 非空 message + 不传 system_trigger → 仍正常工作

---

## Task 1.2: SkillEngine 新增 `_has_effective_*` helper（dormant）

**Files:**

- Modify: `backend/skill.py`
- Test: `tests/test_skill_engine.py`

**Steps:**

- [ ] **Step 1**: 在 `backend/skill.py` 加常量 + 三个 helper（不被任何门禁调用，留 Commit 4 启用）：

  ```python
  INDEPENDENT_REVIEW_ANCHORS = [
      "## 1. 结论-证据一致性",
      "## 2. 关键假设与逻辑链",
      "## 3. 数据口径一致性",
      "## 4. 建议可执行性",
      "## 5. 目标读者匹配",
  ]
  INDEPENDENT_REVIEW_COMPLETION_MARKER = "<!-- independent-review:complete -->"
  LINT_REPORT_ANCHORS = ["## 按章节排列", "## 总览"]
  LINT_REPORT_COMPLETION_MARKER = "<!-- lint-report:complete -->"
  
  def _has_effective_independent_review(self, project_path: Path) -> bool:
      text = self._read_plan_file(project_path, "independent-review.md")
      if not text or self._is_template_content(text, "independent-review.md"):
          return False
      if not all(a in text for a in INDEPENDENT_REVIEW_ANCHORS):
          return False
      if INDEPENDENT_REVIEW_COMPLETION_MARKER not in text:
          return False
      return self._has_substantive_body(text)
  
  def _has_effective_lint_report(self, project_path: Path) -> bool:
      text = self._read_plan_file(project_path, "lint-report.md")
      if not text or self._is_template_content(text, "lint-report.md"):
          return False
      if not all(a in text for a in LINT_REPORT_ANCHORS):
          return False
      if LINT_REPORT_COMPLETION_MARKER not in text:
          return False
      return self._has_substantive_body(text)
  
  def _has_effective_review_reports(self, project_path: Path) -> bool:
      return (self._has_effective_independent_review(project_path)
              and self._has_effective_lint_report(project_path))
  ```

- [ ] **Step 2**: 加边界单测覆盖 5/5 anchor、marker 缺失、template stub、空 body、截断文件场景：

  ```python
  def test_has_effective_independent_review_rejects_template_stub(self):
      # 写入 [等待运行] 字面量 → False
  
  def test_has_effective_independent_review_requires_all_5_anchors(self):
      # 4/5 anchor → False
  
  def test_has_effective_independent_review_requires_completion_marker(self):
      # 有 5 anchor 但缺 marker → False
  
  def test_has_effective_independent_review_accepts_valid_report(self):
      # 5 anchor + marker + 实质内容 → True
  
  def test_has_effective_lint_report_rejects_template_and_missing_marker(self):
      # 同上
  
  def test_has_effective_review_reports_requires_both(self):
      # 只一份 → False
  ```

**Acceptance Criteria:**

- 6 个新单测全过
- 现有 `_has_effective_review_checklist` 单测**不变**（仍存在但不影响新 helper）
- 系统行为零变化：新 helper 不被任何门禁路径调用，工作流不变

---

## Task 1.3: `_stage_five_completion_state` 字段扩展（dormant）

**Files:**

- Modify: `backend/skill.py:459-492`
- Test: `tests/test_skill_engine.py`

**Steps:**

- [ ] **Step 1**: 改 `_stage_five_completion_state`：保留旧字段 `review_checklist_ready` 由 `_has_effective_review_checklist` 计算，**新字段** `independent_review_ready` / `lint_report_ready` / `review_reports_ready` 也填但不被 `missing_for_review_pass` 读取：

  ```python
  def _stage_five_completion_state(self, project_path, checkpoints=None, ...):
      # ...（保留所有现有逻辑）
      review_checklist_ready = self._has_effective_review_checklist(project_path)  # 旧字段
      independent_review_ready = self._has_effective_independent_review(project_path)  # 新
      lint_report_ready = self._has_effective_lint_report(project_path)  # 新
      review_reports_ready = independent_review_ready and lint_report_ready  # 新
      review_passed = "review_passed_at" in checkpoints
      
      # Commit 1 阶段 missing_for_review_pass 仍按旧逻辑（review-checklist）
      missing_for_review_pass = list(stage_four_state["missing_for_stage_four"])
      if not review_checklist_ready:
          missing_for_review_pass.append("review-checklist.md")
      # ↑ 这里**不改成新 helper**——留 Commit 4 切换
      
      return {
          "review_checklist_ready": review_checklist_ready,
          "independent_review_ready": independent_review_ready,  # 新增
          "lint_report_ready": lint_report_ready,  # 新增
          "review_reports_ready": review_reports_ready,  # 新增
          "review_passed": review_passed,
          "review_pass_prerequisites_complete": not missing_for_review_pass,
          "stage_five_complete": not missing_for_stage_five,
          "missing_for_review_pass": missing_for_review_pass,
          "missing_for_stage_five": missing_for_stage_five,
      }
  ```

- [ ] **Step 2**: 加测试验证新字段填值正确：

  ```python
  def test_stage_five_completion_state_includes_new_fields(self):
      # 项目有 review-checklist.md 但没有 independent-review.md → 
      # review_checklist_ready=True, independent_review_ready=False
  
  def test_stage_five_completion_state_review_reports_ready_requires_both(self):
      # 只有 independent-review.md → review_reports_ready=False
  ```

- [ ] **Step 3**: 同时改 `_infer_stage_state` 的 flags 构造（行 ~1640 附近），加新字段：

  ```python
  flags = {
      # ...existing flags...
      "independent_review_ready": stage_five_state["independent_review_ready"],
      "lint_report_ready": stage_five_state["lint_report_ready"],
      "review_reports_ready": stage_five_state["review_reports_ready"],
      # review_checklist_ready 保留旧值
  }
  ```

**Acceptance Criteria:**

- 现有 S5 相关 stage_five_completion_state 测试不变（旧字段行为不变）
- 新字段测试通过
- `_build_completed_items` 暂不动（用旧字段计算）

---

## Task 1.4: workspaceSummary 加新 flag 字段（dormant）

**Files:**

- Modify: `backend/skill.py:get_workspace_summary`
- Test: `tests/test_main_api.py::WorkspaceApiTests`

**Steps:**

- [ ] **Step 1**: 在 `get_workspace_summary` 返回的 `flags` dict 加新字段：

  ```python
  return {
      ...,
      "flags": {
          ...existing fields...,
          "independent_review_ready": flags["independent_review_ready"],
          "lint_report_ready": flags["lint_report_ready"],
          "review_reports_ready": flags["review_reports_ready"],
          # review_checklist_ready 仍存在
      },
  }
  ```

- [ ] **Step 2**: 加测试：

  ```python
  def test_workspace_summary_includes_new_review_flags(self):
      ws = self.client.get("/api/projects/demo/workspace").json()
      self.assertIn("independent_review_ready", ws["flags"])
      self.assertIn("lint_report_ready", ws["flags"])
      self.assertIn("review_reports_ready", ws["flags"])
  ```

**Acceptance Criteria:**

- 旧字段 `review_checklist_ready` 仍在 flags
- 新字段类型 bool，按 `_stage_five_completion_state` 计算

---

## Task 1.5: `conversation_state.s5_welcome_shown_at` schema 三处同步

**Files:**

- Modify: `backend/chat.py:813-821` (`_empty_conversation_state`)
- Modify: `backend/chat.py:932-948` (`_load_conversation_state`)
- Modify: `backend/chat.py:995-1013` (`_save_conversation_state_atomically`)
- Test: `tests/test_chat_runtime.py`

**Steps:**

- [ ] **Step 1**: 改 `_empty_conversation_state()` 加字段：

  ```python
  def _empty_conversation_state(self) -> dict:
      return {
          ...existing fields...,
          "s5_welcome_shown_at": None,  # 新增
      }
  ```

- [ ] **Step 2**: 改 `_load_conversation_state()` 复制逻辑：

  ```python
  # 在现有白名单复制循环里加：
  welcome_shown = payload.get("s5_welcome_shown_at")
  if isinstance(welcome_shown, str) and welcome_shown:
      state["s5_welcome_shown_at"] = welcome_shown
  ```

- [ ] **Step 3**: 改 `_save_conversation_state_atomically()` 白名单：

  ```python
  welcome_shown = state.get("s5_welcome_shown_at")
  if isinstance(welcome_shown, str) and welcome_shown:
      payload["s5_welcome_shown_at"] = welcome_shown
  ```

- [ ] **Step 4**: 加 4 个测试：

  ```python
  def test_load_conversation_state_without_s5_welcome_field(self):
      # 老 state 无字段 → load 返回 state["s5_welcome_shown_at"] == None
  
  def test_save_load_roundtrip_preserves_s5_welcome(self):
      # 写入 ISO 时间 → reload → 字段保留
  
  def test_save_skips_none_s5_welcome(self):
      # state["s5_welcome_shown_at"] = None → 写入的 payload 不含该字段
  
  def test_save_skips_empty_string_s5_welcome(self):
      # state["s5_welcome_shown_at"] = "" → 不写入
  ```

**Acceptance Criteria:**

- 4 个新测试通过
- 老 conversation_state.json 文件加载不报错
- 字段被写入后 reload 仍存在

---

## Commit 1 Acceptance

提交前完整跑：

```bash
.venv\Scripts\python -m pytest tests/test_skill_engine.py tests/test_chat_runtime.py tests/test_main_api.py -q
```

应**全部通过**——包括所有现有测试（验证零回归）。

跑 `cd frontend && npm run build` 验证前端构建不破。

**中间态校验**：Commit 1 落 main 后：
- 系统行为对用户完全等价于切换前
- `validate_plan_write` 仍按旧 FORMAL_PLAN_FILES 工作
- 主代理路径不变，UI 不变

---

# Commit 2：FORMAL_PLAN_FILES + 主代理拒写拦截 + 审查代理 + lint 脚本（内 atomic）

**关键约束**：FORMAL_PLAN_FILES 加新文件**必须**和主代理拒写拦截在**同一个 commit**——否则中间态主代理可伪造写入新报告（R4 Bug 18）。

## Task 2.1: FORMAL_PLAN_FILES 加新文件 + 新 stub 模板

**Files:**

- Modify: `backend/skill.py:22-37` (FORMAL_PLAN_FILES)
- Create: `skill/plan-template/independent-review.md`
- Create: `skill/plan-template/lint-report.md`
- Test: `tests/test_skill_assets.py`

**Steps:**

- [ ] **Step 1**: 在 `FORMAL_PLAN_FILES` 集合加两个新文件（保留 `review-checklist.md` 不动）：

  ```python
  FORMAL_PLAN_FILES = {
      ...,
      "review-checklist.md",  # 保留——Commit 4 才移除
      "independent-review.md",  # 新增
      "lint-report.md",  # 新增
      ...,
  }
  ```

- [ ] **Step 2**: 创建 `skill/plan-template/independent-review.md`（带 `:pending` marker，确保软门禁判定为 stub）：

  ```markdown
  # 独立审查报告
  
  [等待运行 — 请在 S5 阶段点击工作区"独立审查"按钮]
  
  <!-- independent-review:pending -->
  ```

- [ ] **Step 3**: 创建 `skill/plan-template/lint-report.md`：

  ```markdown
  # AI 味自查报告
  
  [等待运行 — 请在 S5 阶段点击工作区"AI 味自查"按钮]
  
  <!-- lint-report:pending -->
  ```

- [ ] **Step 4**: 加测试：

  ```python
  def test_formal_plan_files_includes_new_files(self):
      from backend.skill import SkillEngine
      self.assertIn("independent-review.md", SkillEngine.FORMAL_PLAN_FILES)
      self.assertIn("lint-report.md", SkillEngine.FORMAL_PLAN_FILES)
  
  def test_formal_plan_files_still_includes_review_checklist_in_commit_2(self):
      # Commit 4 才移除，这里仍包含
      self.assertIn("review-checklist.md", SkillEngine.FORMAL_PLAN_FILES)
  
  def test_initialize_project_creates_new_stubs(self):
      # 新建项目时 plan/ 目录含 independent-review.md + lint-report.md
  
  def test_new_stubs_are_template_content(self):
      # _is_template_content 对新 stub 返回 True
      # _has_effective_independent_review 对新 stub 返回 False
  
  def test_validate_plan_write_accepts_independent_review(self):
      # SkillEngine.write_file("plan/independent-review.md") 不被 validate_plan_write 拒
  ```

**Acceptance Criteria:**

- 新模板存在且内容符合 §8.2
- 新建项目自动获得两份 stub（通过 `_initialize_project_structure` 自动复制 FORMAL_PLAN_FILES 模板）
- 软门禁判定新 stub 为 invalid（marker 是 `:pending` 不是 `:complete`）
- `_is_template_content` 能识别新 stub 字面量

---

## Task 2.2: 主代理 write_file / edit_file 拒写拦截

**Files:**

- Modify: `backend/chat.py:4775` (path 拦截分支)
- Test: `tests/test_chat_runtime.py`

**Steps:**

- [ ] **Step 1**: 在 `chat.py:4775` 现有 `if normalized_path in {"plan/review-checklist.md", "plan/review.md"}:` 分支**之后、`plan/presentation-plan.md` 分支之前**插入：

  ```python
  if normalized_path == "plan/independent-review.md":
      return (
          "`plan/independent-review.md` 只能由独立审查代理生成（用户点'独立审查'按钮）。"
          "你不能直接写这份报告——这是审查独立性的硬约束。"
      )
  if normalized_path == "plan/lint-report.md":
      return (
          "`plan/lint-report.md` 只能由 AI 味自查脚本生成（用户点'AI 味自查'按钮）。"
          "你不能直接写这份报告。"
      )
  ```

- [ ] **Step 2**: 加测试覆盖 write + edit 两个路径（R3 Suggestion 7）：

  ```python
  def test_main_agent_cannot_write_independent_review_md(self):
      # mock model 返回 write_file("plan/independent-review.md", ...) tool call
      # 验证响应包含 "只能由独立审查代理生成" 错误
  
  def test_main_agent_cannot_write_lint_report_md(self):
      # 同上 lint-report
  
  def test_main_agent_cannot_edit_independent_review_md(self):
      # mock 返回 edit_file("plan/independent-review.md", old, new) tool call
      # 验证响应包含拒绝消息（共享 _execute_plan_write → _validate_stage_write_allowed）
  
  def test_main_agent_cannot_edit_lint_report_md(self):
      # 同上
  ```

**Acceptance Criteria:**

- 4 个测试通过
- 拒绝消息引导用户点按钮（不暴露内部函数名）
- 主代理写其他 plan 文件（如 outline.md / data-log.md）不受影响

---

## Task 2.3: independent_review.py 模块（含 DeepSeek 兼容 helpers）

**Files:**

- Create: `backend/independent_review.py`
- Create: `tests/test_independent_review.py`

**Steps:**

- [ ] **Step 1**: 创建模块，含完整 spec §2.3 骨架（含 30k 字 preflight、5 维度 system prompt、tool dispatcher、完成验证）。**关键实现要点**：

  - `INDEPENDENT_REVIEW_SYSTEM_PROMPT` = spec §2.2 完整 prompt 文本（写死）
  - `IndependentReviewAgent.run()` 第一步 word_count > 30000 friendly fail
  - `_build_client()` 复用 `OpenAI(api_key=settings.api_key, base_url=settings.api_base, http_client=httpx.Client(timeout=120.0))`
  - `_should_send_explicit_tool_choice()` **完全 copy** chat.py:443-446：`return "deepseek" not in (active_model or "").lower()`
  - `_extract_reasoning_content_from_message()` 同 chat.py:3189-3206
  - `_serialize_assistant_tool_call_message()` 序列化 assistant msg，保留非空 reasoning_content，丢 null SDK dump 字段
  - tool dispatcher 拒绝非 `plan/independent-review.md` 的 write_file 路径
  - max_iterations = 15
  - 完成时验证 `INDEPENDENT_REVIEW_COMPLETION_MARKER` 存在再 emit `review-completed`

- [ ] **Step 2**: 实现 per-project lock 工厂函数：

  ```python
  _INDEPENDENT_REVIEW_LOCKS: dict[str, threading.Lock] = {}
  _INDEPENDENT_REVIEW_LOCKS_GUARD = threading.Lock()
  
  def get_independent_review_lock(project_id: str) -> threading.Lock:
      with _INDEPENDENT_REVIEW_LOCKS_GUARD:
          if project_id not in _INDEPENDENT_REVIEW_LOCKS:
              _INDEPENDENT_REVIEW_LOCKS[project_id] = threading.Lock()
          return _INDEPENDENT_REVIEW_LOCKS[project_id]
  ```

- [ ] **Step 3**: 加测试（mock OpenAI client）：

  ```python
  def test_run_emits_progress_events(self):
      # mock client 返回 read_file × 3 + write_file 序列
      # 验证 SSE event 类型：progress → tool_call → tool_result → review-completed
  
  def test_run_word_count_over_30k_emits_friendly_error(self):
      # 写超长正文 → run() 第一步 emit error，不调 client
      # 验证 mock client.chat.completions.create 调用次数 == 0
  
  def test_run_rejects_write_to_non_canonical_path(self):
      # mock client 返回 write_file("plan/data-log.md", ...) tool call
      # 验证 tool_result status=error, summary="路径不允许"
  
  def test_run_requires_completion_marker(self):
      # mock client 写报告内容不含 marker → emit error 不 emit review-completed
  
  def test_run_max_iterations_15(self):
      # mock client 一直 tool_call 不结束 → 15 轮后 emit error
  
  def test_deepseek_compat_helpers_match_chat_helpers(self):
      # 行为矩阵（R3 Suggestion 8 + R4 Suggestion 10）：
      TEST_MODELS = [
          "deepseek-v4-pro", "DeepSeek-Reasoner", "deepseek-chat",
          "gpt-4.1", "gpt-4o-mini", "claude-sonnet-4-6",
          "managed-custom-model", "",
      ]
      # R1 Bug 8 修正：ChatHandler 真实签名是 (settings, skill_engine)（chat.py:377），不是反过来
      # 用命名参数避免顺序错
      chat_handler = ChatHandler(settings=settings, skill_engine=skill_engine)
      ir_agent = IndependentReviewAgent(skill_engine=skill_engine, settings=settings)
      for model in TEST_MODELS:
          assert chat_handler._should_send_explicit_tool_choice(model) == ir_agent._should_send_explicit_tool_choice(model)
  ```

**Acceptance Criteria:**

- 6 个核心单测通过
- 模块独立于 chat.py（无循环 import）
- token 超长直接 emit error 不启动 LLM call

---

## Task 2.4: quality_check.ps1 重构 + run_lint_report 新函数

**Files:**

- Modify: `skill/scripts/quality_check.ps1`
- Modify: `backend/report_tools.py`
- Test: `tests/test_lint_report.py`（新文件）
- Test: `tests/test_report_tools.py`（扩展）

**Steps:**

- [ ] **Step 1**: 重构 `quality_check.ps1`（**R1 Suggestion 3**：必须保留现有 UTF-8 BOM + `[Console]::OutputEncoding` 设置，`tests/test_skill_assets.py:20-44` 锁死了这些，否则 Windows 打包态老坑回归）：

  - **保留** UTF-8 with BOM 编码（脚本文件本身）
  - **保留** 现有顶部 `$utf8NoBom = New-Object System.Text.UTF8Encoding $false` + `[Console]::OutputEncoding = $utf8NoBom` + `$OutputEncoding = $utf8NoBom` 三行
  - 加 `-FilePath` / `-OutputPath` / `-DryRun` 参数
  - 4 维度规则（AI 写作口癖合并 / 内容完整性 / 数据标注覆盖 / 章节级 So What 密度）
  - negative lookahead 排除"非常显著（数字）"等数据语境
  - 砍掉图表编号连续性 + So What 全文计数
  - 旧模式（无 `-OutputPath`）：仍 stdout 输出旧格式，向后兼容
  - 新模式：写 markdown 文件到 `-OutputPath`，末尾带 `<!-- lint-report:complete -->`
  - markdown 报告按 H1/H2 章节排列；末尾"预估改完所需时间"
  - `tests/test_skill_assets.py` 现有 BOM / OutputEncoding 测试**不动**——重构后仍应通过

- [ ] **Step 2**: 在 `backend/report_tools.py` 加 `run_lint_report`：

  ```python
  def run_lint_report(report_path: str, output_path: str, script_path: str,
                     dry_run: bool = False) -> dict:
      args = ["-File", script_path, "-FilePath", report_path, "-OutputPath", output_path]
      if dry_run:
          args.append("-DryRun")
      result = _run_powershell(args)
      if result.returncode != 0:
          return {"status": "error", "detail": result.stderr or result.stdout}
      summary = _parse_lint_summary(output_path) if not dry_run else {}
      return {"status": "ok", "path": output_path, "summary": summary}
  
  def _parse_lint_summary(output_path: str) -> dict:
      # 读 lint-report.md "## 总览" 段提取 4 个 count + estimated_minutes
      ...
  ```

  `run_quality_check` 函数**完全不动**（保留 `stdout or stderr` shape）。

- [ ] **Step 3**: 实现 `get_lint_report_lock(project_id)`（同 `get_independent_review_lock` 模式）

- [ ] **Step 4**: 加测试：

  ```python
  # test_lint_report.py
  def test_lint_report_writes_markdown_with_marker(self):
      # 跑脚本 -OutputPath → 文件存在，末尾含 marker
  
  def test_lint_report_4_dimensions(self):
      # 输出含 AI 腔 / 内容缺失 / 缺标注 / 章节 So What 4 个标签
  
  def test_lint_report_groups_by_section(self):
      # 按 H1/H2 分组
  
  def test_lint_report_negative_lookahead(self):
      # "非常显著（5%）" 不被空洞形容词命中
  
  def test_lint_report_dry_run_no_file(self):
      # -DryRun → 文件不存在，stdout 含 markdown
  
  def test_lint_report_top_n_truncation(self):
      # 超 100 处命中 → 截 top 30 + 注
  
  # test_report_tools.py 扩展
  def test_run_quality_check_returns_stdout_or_stderr_backwards_compat(self):
      # 旧函数仍 return {"status", "output": stdout or stderr}
  
  def test_run_lint_report_returns_path_and_summary(self):
      # 新函数 return {"status", "path", "summary"}
  ```

**Acceptance Criteria:**

- 脚本旧模式行为不变（`run_quality_check` 单测全过）
- 脚本新模式生成符合 spec §3.4 格式的 markdown
- 6 个新单测全过

---

## Task 2.5: `record_stage_checkpoint` 加 lock 检查（dormant）

**Files:**

- Modify: `backend/skill.py:1409-1423`（`record_stage_checkpoint`）
- Test: `tests/test_skill_engine.py`

**Steps:**

- [ ] **Step 1**: 在 `record_stage_checkpoint` 顶部加 lock 检查（dormant 因为 Commit 2 还没启用 endpoint）：

  ```python
  def record_stage_checkpoint(self, project_id: str, key: str, action: str) -> dict:
      ...
      if key == "review_passed_at" and action == "set":
          # 审查正在跑时拒绝推进（R2 Bug 8）
          from .independent_review import get_independent_review_lock
          from .report_tools import get_lint_report_lock
          
          review_lock = get_independent_review_lock(project_id)
          if review_lock.locked():
              raise ValueError("独立审查正在进行中，请等待完成后再标记审查通过")
          
          lint_lock = get_lint_report_lock(project_id)
          if lint_lock.locked():
              raise ValueError("AI 味自查正在进行中，请等待完成后再标记审查通过")
      
      # 现有 _validate_stage_checkpoint_transition + 写入逻辑不变
      ...
  ```

- [ ] **Step 2**: 加测试：

  ```python
  def test_record_stage_checkpoint_rejects_review_passed_when_review_lock_held(self):
      lock = get_independent_review_lock("demo")
      lock.acquire()
      try:
          with self.assertRaisesRegex(ValueError, "独立审查正在进行中"):
              self.engine.record_stage_checkpoint("demo", "review_passed_at", "set")
      finally:
          lock.release()
  
  def test_record_stage_checkpoint_rejects_review_passed_when_lint_lock_held(self):
      # 同上
  
  def test_record_stage_checkpoint_succeeds_when_no_lock_held(self):
      # 准备好 S5 前置 + acquire→release lock → 推进成功
  ```

**Acceptance Criteria:**

- 3 个新测试通过
- 现有 `_validate_stage_checkpoint_transition` 行为不变

---

## Commit 2 Acceptance

```bash
.venv\Scripts\python -m pytest tests/ -q
cd frontend && node --test tests/ && npm run build && cd ..
```

应全部通过。

**中间态校验**：Commit 2 落 main 后：
- 新建项目会自动包含两份新 stub 文件（marker `:pending` 不会通过软门禁）
- 主代理被拒写两份新报告（拒写拦截激活）
- endpoints / chat_stream system_trigger / `CHECKPOINT_PREREQ` 切换全没动
- **小副作用**：文件 tab 可能看到 pending stub（低风险，用户能看到文件但内容是占位）
- 主流程对用户**完全等价**于切换前

---

# Commit 3：endpoints + chat_stream + finalize + S5 welcome helper（dormant，不接调用点）

endpoints 可调，但 SKILL 仍说写 review-checklist，前端按钮不出现，`CHECKPOINT_PREREQ.review_passed_at` 还是旧 helper。`_chat_stream_unlocked()` **不调** S5 welcome 注入（避免提示用户点不存在按钮，R5 Bug 21）。

## Task 3.0: `_build_provider_turn_conversation` helper 扩展（先决重构，R1 Bug 2 + R2 Bug 9）

**Files:**

- Modify: `backend/chat.py:3243-...` (`_build_provider_turn_conversation`)
- Test: `tests/test_chat_runtime.py`

**背景**：R1 Bug 2 catch 出原 plan 引用的 `_build_provider_messages` 不存在；真实 helper 是 `_build_provider_turn_conversation`（chat.py:3243），它**总是 append 当前 user message**。要支持 system-triggered turn 跳过 user message，必须先扩展该 helper。

**R2 Bug 9 修正**：真实签名第二参是 `history`（不是 `conversation`），keyword-only 参数标 `*` 分隔。完整真实签名（grep `def _build_provider_turn_conversation` 在 chat.py:3243-3251 附近）：

```python
def _build_provider_turn_conversation(
    self,
    project_id: str,
    history: list[dict],
    current_user_message: dict | None,
    current_turn_messages=None,
    *,
    exclude_current_turn_memory: bool = False,
) -> tuple[list[dict], int]:
```

**Steps:**

- [ ] **Step 1**: 改签名加两个可选 keyword-only 参数：

  ```python
  def _build_provider_turn_conversation(
      self,
      project_id: str,
      history: list[dict],
      current_user_message: dict | None,
      current_turn_messages=None,
      *,
      exclude_current_turn_memory: bool = False,
      additional_system_messages: list[dict] | None = None,  # R1 Bug 2 新增
      include_current_user: bool = True,  # R1 Bug 2 新增
  ) -> tuple[list[dict], int]:
      ...
      # 在现有 build 完 messages 后、return 前：
      if additional_system_messages:
          messages.extend(additional_system_messages)  # 追加 transient system messages（不持久化）
      
      # current_user_message 是否 append 由 include_current_user 控制
      if include_current_user and current_user_message is not None:
          messages.append(current_user_message)
      ...
  ```

  实施时 grep 真实 helper 内 `current_user_message` 使用点——必须改为受 `include_current_user` 控制（不要漏掉副作用如 `current_turn_start_index` 计算）。

- [ ] **Step 2**: 现有所有 caller（chat.py:2501 / 2901 / 3096）都不传新参数 → 用默认值 `additional_system_messages=None, include_current_user=True` → **行为不变**。grep 验证只有 3 个 caller，确认每个都按 positional 调用没传超 default 的字段。

- [ ] **Step 3**: 加测试（用真实参数名 `history`）：

  ```python
  def test_build_provider_turn_conversation_appends_additional_system_messages(self):
      conv, _ = handler._build_provider_turn_conversation(
          project_id="demo",
          history=[{"role": "user", "content": "hi"}],
          current_user_message={"role": "user", "content": "new"},
          additional_system_messages=[{"role": "system", "content": "TRIGGER"}],
      )
      # 验证 TRIGGER 出现在 messages 中
      assert any(m.get("content") == "TRIGGER" for m in conv)
  
  def test_build_provider_turn_conversation_skips_current_user_when_disabled(self):
      conv, _ = handler._build_provider_turn_conversation(
          project_id="demo",
          history=[],
          current_user_message=None,
          include_current_user=False,
      )
      # 验证 conv 中没有 role="user" 的 current 消息
  
  def test_build_provider_turn_conversation_backwards_compatible(self):
      # 不传新参数 → 行为与改造前一致
  
  def test_additional_system_messages_survive_long_history_compression(self):
      """R3 改进建议 3：system-trigger prompt 在长 history 下不被 context fitting 裁掉"""
      # 准备超长 history（达到 context policy budget 边界）
      # 传 additional_system_messages
      # 验证返回 conversation 仍包含 transient system message（不被截断）
      # （需要根据 _resolve_context_policy 实际逻辑写——可能要 mock 一个低 token limit policy）
  ```

**Acceptance Criteria:**

- 现有 chat.py 所有 caller 行为不变（默认参数）
- 4 个新测试通过（含 R3 改进建议 3 的 budget 测试）
- 后续 Task 3.3 / 4.2 在此基础上注入 transient system message

---

## Task 3.1: `/independent-review/stream` endpoint

**Files:**

- Modify: `backend/main.py`
- Test: `tests/test_main_api.py`

**Steps:**

- [ ] **Step 1**: 加 GET SSE endpoint（R1 Bug 7：加 `request: Request` + `is_disconnected()` 检测）：

  ```python
  from fastapi import Request
  
  @app.get("/api/projects/{project_id}/independent-review/stream")
  async def independent_review_stream(project_id: str, request: Request):
      workspace = skill_engine.get_workspace_summary(project_id)
      if workspace.get("stage_code") != "S5":
          raise HTTPException(status_code=400, detail="独立审查只能在 S5 阶段使用")
      
      lock = get_independent_review_lock(project_id)
      if not lock.acquire(blocking=False):
          raise HTTPException(status_code=409, detail="上一次独立审查仍在进行中，请等待")
      
      async def generate():
          try:
              agent = IndependentReviewAgent(skill_engine, settings)
              for event in agent.run(project_id):
                  # R1 Bug 7：每个 event 前检查客户端是否断开
                  if await request.is_disconnected():
                      # 客户端断开，停止后续 LLM 调用，节省 token
                      return
                  yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
              yield "data: [DONE]\n\n"
          finally:
              lock.release()  # 无论是否断开都释放锁
      
      return StreamingResponse(generate(), media_type="text/event-stream", headers={
          "Cache-Control": "no-cache",
          "Connection": "keep-alive",
          "X-Accel-Buffering": "no",
      })
  ```

- [ ] **Step 2**: 加测试：

  ```python
  def test_independent_review_endpoint_requires_s5(self):
      # 项目处于 S2 → 调 endpoint → 400
  
  def test_independent_review_endpoint_returns_sse(self):
      # S5 + mock agent → response content-type 是 text/event-stream
  
  def test_independent_review_endpoint_409_when_concurrent(self):
      # 持锁状态下调 → 409
  
  def test_independent_review_endpoint_releases_lock_on_client_disconnect(self):
      # mock request.is_disconnected() 返回 True
      # 验证 generator 退出，lock 释放，后续 LLM call 不再发生
  ```

**Acceptance Criteria:**

- 3 个新测试通过
- 非 S5 / 并发 / 正常流式三种场景都覆盖

---

## Task 3.2: `/lint-report` endpoint

**Files:**

- Modify: `backend/main.py`
- Test: `tests/test_main_api.py`

**Steps:**

- [ ] **Step 1**: 加 POST endpoint：

  ```python
  @app.post("/api/projects/{project_id}/lint-report")
  async def lint_report(project_id: str):
      workspace = skill_engine.get_workspace_summary(project_id)
      if workspace.get("stage_code") != "S5":
          raise HTTPException(status_code=400, detail="AI 味自查只能在 S5 阶段使用")
      
      lock = get_lint_report_lock(project_id)
      if not lock.acquire(blocking=False):
          raise HTTPException(status_code=409, detail="上一次 AI 味自查仍在进行中，请等待")
      try:
          report_path = skill_engine.get_primary_report_path(project_id)
          output_path = str(skill_engine.get_project_path(project_id) / "plan" / "lint-report.md")
          script_path = skill_engine.get_script_path("quality_check.ps1")
          return run_lint_report(report_path, output_path, script_path)
      finally:
          lock.release()
  ```

- [ ] **Step 2**: 加测试同 Task 3.1（require_s5 / returns_summary / 409_concurrent）

**Acceptance Criteria:**

- 3 个新测试通过
- 旧 `/quality-check` endpoint 保持向后兼容（仍 `{"status", "output": stdout or stderr}`）

---

## Task 3.3: `_chat_stream_system_triggered` 独立路径

**Files:**

- Modify: `backend/chat.py:chat_stream` + 新方法 `_chat_stream_system_triggered`
- Test: `tests/test_chat_runtime.py`

**Steps:**

- [ ] **Step 1**: 在 `ChatHandler.chat_stream` 入口检测 `system_trigger`：

  ```python
  def chat_stream(self, project_id, message_text, attached_material_ids,
                  transient_attachments, system_trigger=None):
      ...
      if system_trigger:
          yield from self._chat_stream_system_triggered(project_id, system_trigger)
          return
      # 正常 user message turn 走原逻辑
      ...
  ```

  同时改 `backend/main.py:chat_stream` endpoint 把 `chat_request.system_trigger` 传进 handler。

- [ ] **Step 2**: ~~实现 `_chat_stream_system_triggered`~~ —— **此 step 已被 Step 3 方案三 v2 取代**（R3 改进建议 2 清理）。新方案不抽独立 method，直接在 `_chat_stream_unlocked` 内加 `if system_trigger:` 分支。SYSTEM_TRIGGER_PROMPTS 字典定义见 Step 3。

- [ ] **Step 3**: **实施方案 — 方案三 v2（R2 Bug 10 + R3 Bug 13/14 修正）**：

  R1 提的 Option A（抽 `_run_stream_turn` helper）被 R2 Bug 10 否决（400 行 generator 抽取风险大）。
  
  R2 方案三初版（iteration==0 注入）被 R3 Bug 13 + 14 否决：
  - Bug 13：后续 tool follow-up 轮次会丢 trigger prompt → 模型忘记任务
  - Bug 14：`current_user_message=None` 会在 stream 尾部 `.get` 崩

  **方案三 v2 — 每轮都注入 + placeholder user message**：

  ```python
  # backend/chat.py 顶部 module-level
  SYSTEM_TRIGGER_PROMPTS = {
      "independent_review_done": "[系统通知] 独立审查报告已生成（plan/independent-review.md）。请用 read_file 阅读，按 5 个维度向用户报告主要发现，然后询问用户是否需要修改正文。不要把整份报告原文贴进聊天框。",
      "lint_report_done": "[系统通知] AI 味自查报告已生成（plan/lint-report.md）。请用 read_file 阅读，按章节向用户报告主要发现，然后询问用户是否需要修改正文。不要把整份报告原文贴进聊天框。",
  }
  
  def _chat_stream_unlocked(self, project_id, message_text, ...,
                            system_trigger: Optional[str] = None):
      # ...现有 setup 逻辑（auth check / project resolve / ...）不变...
      
      # System-triggered 路径分流（R3 Bug 17：避免继承 stale turn_context）
      if system_trigger:
          # 显式重建 turn_context
          self._turn_context = self._build_turn_context(project_id, "")
          self._turn_context["system_triggered"] = True
          self._turn_context["user_message_text"] = ""
          self._turn_context["canonical_obligation"] = None
          self._turn_context["checkpoint_event"] = None
          
          trigger_prompt = SYSTEM_TRIGGER_PROMPTS.get(system_trigger)
          if not trigger_prompt:
              yield {"type": "error", "data": f"未知 system_trigger: {system_trigger}"}
              return
          
          # R3 Bug 14：placeholder dict 而非 None，避免 stream 尾部 .get 崩
          # 该 placeholder 不会进 history（include_current_user=False）+ finalize 跳过 persist
          current_user_message = {"role": "user", "content": ""}
          provider_user_message = current_user_message
          
          # R3 Bug 13：每轮都注入 trigger prompt（不只 iteration==0）
          # 否则 follow-up 轮次 conversation 里没有"按 5 个维度汇报"指令，行为漂
          transient_system_messages = [{"role": "system", "content": trigger_prompt}]
          include_current_user = False  # placeholder 不 append 到 conversation
      else:
          # 现有 user message turn 逻辑（不变）
          transient_system_messages = None
          include_current_user = True
          # current_user_message / provider_user_message 走原逻辑
      
      # ...其他现有 setup（history load / obligation snapshots / etc.）不变...
      
      # 进入 stream loop，**每轮**传 transient_system_messages（R3 Bug 13）：
      while iteration < max_iterations:
          conversation, current_turn_start_index = self._build_provider_turn_conversation(
              project_id=project_id,
              history=history,
              current_user_message=current_user_message,
              current_turn_messages=current_turn_messages,
              additional_system_messages=transient_system_messages,  # 每轮都传，不只 iteration==0
              include_current_user=include_current_user,
          )
          # ...其他 stream loop 逻辑不变...
      
      # ...stream 尾部 .get 调用安全（current_user_message 是占位 dict）...
      # ...finalize 通过 turn_context.system_triggered 跳过 user message persist...
  ```

  **关键差异**（相对 R2 方案三）：
  - **每轮都传** `additional_system_messages=transient_system_messages`，不只 iteration==0（修 Bug 13）
  - **placeholder user message** `{"role": "user", "content": ""}` 而非 None（修 Bug 14）——尾部 `.get` 调用安全
  - `include_current_user=False` 让 placeholder 不进 conversation（不污染历史）
  - `_finalize_assistant_turn` 通过 `turn_context.system_triggered` 标志跳过 persist（Task 3.4 已处理）
  - `_finalize_empty_assistant_turn`（chat.py:1059-1076）也要同步加 system-trigger 分支跳过 user message append——见 Task 3.4 Step 3

- [ ] **Step 4**: `backend/main.py:chat_stream` endpoint 把 `chat_request.system_trigger` 传给 `handler.chat_stream(..., system_trigger=...)`，handler 内部传给 `_chat_stream_unlocked`

- [ ] **Step 5**: 加 follow-up 测试（R3 Bug 13 关键回归保护）：

  ```python
  def test_system_triggered_turn_keeps_trigger_in_follow_up_iterations(self):
      """R3 Bug 13: tool call follow-up 轮次仍带 trigger prompt"""
      # mock model: 第 1 轮 tool_call read_file，第 2 轮 final reply
      # 验证：第 2 轮发往 OpenAI 的 messages 仍含 trigger_prompt
      
  def test_system_triggered_turn_does_not_crash_on_finalize(self):
      """R3 Bug 14: stream 尾部 .get 调用不崩"""
      # mock model: assistant content 非空 / 空两种场景
      # 验证：placeholder current_user_message dict 让 .get("content") 返回 ""
      # 验证：history 中不出现 placeholder user message
  ```

- [ ] **Step 4**: 加测试：

  ```python
  def test_chat_stream_with_system_trigger_skips_user_message(self):
      # 走 system_trigger 路径 → conversation.json 不含 user 占位
  
  def test_chat_stream_independent_review_trigger_injects_correct_prompt(self):
      # 注入的 system message 含 "独立审查报告已生成"
  
  def test_chat_stream_lint_report_trigger_injects_correct_prompt(self):
      # 同上 lint-report
  
  def test_chat_stream_invalid_system_trigger_returns_error(self):
      # system_trigger="unknown" → emit error event
  
  def test_system_triggered_turn_does_not_inherit_stale_checkpoint_event(self):
      # 上一轮 advance_stage 后 turn_context.checkpoint_event 有值
      # 触发 system turn → 新 turn_context.checkpoint_event = None
  ```

**Acceptance Criteria:**

- 5 个测试通过
- 现有 chat_stream 测试（正常 user message turn）全部不变

---

## Task 3.4: `_finalize_assistant_turn` system-triggered 分支

**Files:**

- Modify: `backend/chat.py:6211-6215`
- Test: `tests/test_chat_runtime.py`

**Steps:**

- [ ] **Step 1**: 改 `_finalize_assistant_turn` 行 6211-6215：

  ```python
  # Step 6: persist this turn.
  if self._turn_context.get("system_triggered"):
      # System-triggered turn 不写 user message
      history.extend([
          {"role": "assistant", "content": persisted_content},
      ])
  else:
      history.extend([
          current_user_message,
          {"role": "assistant", "content": persisted_content},
      ])
  self._save_conversation(project_id, history)
  ```

- [ ] **Step 2**: 同步改 `_finalize_empty_assistant_turn`（chat.py:1059-1076，R3 Bug 14 catch 该路径也会 append `current_user_message`）。

  **R4 Bug 14 修正**：真实函数本身就**不 append empty assistant**（这是现有 invariant"不持久化空 assistant 避免污染下轮 prompt"，docstring 行 1067-1071）。我 R3 plan 错误地加了"`history.append(assistant fallback)`"——破坏了 invariant。

  正确改法**只改一行**（行 1073）：

  ```python
  # 原 chat.py:1073：
  #     history.append(current_user_message)
  # 改为：
  if not self._turn_context.get("system_triggered"):
      history.append(current_user_message)
  # 行 1074 self._save_conversation(...) 不变
  # 不动 assistant 处理（真实函数本来就不 append assistant）
  ```

  这样 system-triggered turn 即使 assistant content 空也不会污染 history，且**不破坏现有 invariant**。

- [ ] **Step 3**: 加测试：

  ```python
  def test_finalize_assistant_turn_skips_user_when_system_triggered(self):
      self._turn_context["system_triggered"] = True
      self._finalize_assistant_turn(...)
      # 验证 history 只 append 了 assistant
  
  def test_finalize_assistant_turn_keeps_user_for_normal_turn(self):
      self._turn_context["system_triggered"] = False  # 或不设
      self._finalize_assistant_turn(...)
      # 验证 history append 了 [user, assistant]
  
  def test_finalize_empty_assistant_turn_skips_user_when_system_triggered(self):
      # R3 Bug 14：空 assistant 路径也不能 append placeholder user message
      self._turn_context["system_triggered"] = True
      self._finalize_empty_assistant_turn(...)
      # 验证 history 不含 placeholder user
  ```

**Acceptance Criteria:**

- 2 个测试通过
- 现有 `_finalize_assistant_turn` 测试不变

---

## Task 3.5: S5 welcome helper (dormant)

**Files:**

- Modify: `backend/chat.py`
- Test: `tests/test_chat_runtime.py`

**Steps:**

- [ ] **Step 1**: 加 helper：

  ```python
  S5_WELCOME_PROMPT = """[S5 阶段进入提醒]
  用户刚进入 S5 质量审查阶段。S5 的玩法跟以前不一样了：
  
  不再要求你自己填写 review-checklist.md。审查由两个用户主动触发的工具完成：
  1. 用户点"独立审查"按钮：会派一个独立审查代理读 data-log / analysis-notes / 正文 / references / outline，按 5 个判断类维度审查，落 plan/independent-review.md。
  2. 用户点"AI 味自查"按钮：会跑机械化脚本扫正文，按 4 个机械维度查 AI 腔、占位符、数据标注、章节 So What 密度，落 plan/lint-report.md。
  
  请你**在本轮回复**用一句话提醒用户使用上方两个新按钮，简单说明两个按钮的区别。
  不要假装审查已完成。
  不要自己写 plan/review-checklist.md（已退役）。
  """
  
  def _should_emit_s5_welcome(self, project_id: str) -> bool:
      workspace = self.skill_engine.get_workspace_summary(project_id)
      if workspace.get("stage_code") != "S5":
          return False
      if not workspace.get("checkpoints", {}).get("review_started_at"):
          return False
      state = self._load_conversation_state(project_id)
      if state.get("s5_welcome_shown_at"):
          return False
      return True
  
  def _mark_s5_welcome_shown(self, project_id: str) -> None:
      state = self._load_conversation_state(project_id)
      state["s5_welcome_shown_at"] = datetime.now(timezone.utc).isoformat()
      self._save_conversation_state_atomically(project_id, state)
  ```

  **不在 `_chat_stream_unlocked()` 调用这两个 helper**——留 Commit 4 激活。

- [ ] **Step 2**: 加 helper 单测：

  ```python
  def test_should_emit_s5_welcome_returns_true_when_s5_entered_no_history(self):
      # stage_code=S5 + review_started_at 已写 + s5_welcome_shown_at 为 None → True
  
  def test_should_emit_s5_welcome_returns_false_when_not_s5(self):
      # stage_code=S4 → False
  
  def test_should_emit_s5_welcome_returns_false_when_already_shown(self):
      # s5_welcome_shown_at 有 ISO 时间 → False
  
  def test_mark_s5_welcome_shown_writes_iso_timestamp(self):
      # 调用后 state.s5_welcome_shown_at 是 ISO 8601 字符串
  ```

**Acceptance Criteria:**

- 4 个单测通过
- helper 不被 `_chat_stream_unlocked` 调用——`grep "_should_emit_s5_welcome\|_mark_s5_welcome_shown" backend/chat.py` 仅出现在 helper 定义处和测试中
- 用户实际进 S5 不会触发欢迎信

---

## Commit 3 Acceptance

```bash
.venv\Scripts\python -m pytest tests/ -q
cd frontend && node --test tests/ && npm run build && cd ..
```

应全部通过。手工 POST 测试新 endpoint 工作正常。

**中间态校验**：Commit 3 落 main 后 endpoints 可调，但：
- SKILL.md 仍说写 review-checklist
- 前端按钮还没出现
- `_chat_stream_unlocked()` 不调 S5 welcome 注入
- `CHECKPOINT_PREREQ.review_passed_at` 还是旧 helper

系统对用户行为**完全等价**于切换前。

---

# Commit 4：用户可见 atomic cutover

**这一个 commit 是用户可见原子切换**。backend gate / S5 welcome 调用激活 / SKILL 文档 / 前端按钮 / smoke 必须同步落 main。

## Task 4.1: CHECKPOINT_PREREQ + FORMAL_PLAN_FILES 切换

**Files:**

- Modify: `backend/skill.py:22-37` (FORMAL_PLAN_FILES)
- Modify: `backend/skill.py:182-187` (CHECKPOINT_PREREQ.review_passed_at)
- Modify: `backend/skill.py:139-143` (STAGE_CHECKLIST_ITEMS["S5"])
- Modify: `backend/skill.py:1646-1684` (`_build_completed_items` S5 两处)
- Modify: `backend/skill.py:459-492` (`_stage_five_completion_state` missing_for_review_pass 切换)
- Test: `tests/test_skill_engine.py`

**Steps:**

- [ ] **Step 1**: `FORMAL_PLAN_FILES` 移除 `review-checklist.md`（保留模板文件，仅从集合移除）：

  ```python
  FORMAL_PLAN_FILES = {
      ...,
      # "review-checklist.md",  # 退役
      "independent-review.md",
      "lint-report.md",
      ...,
  }
  ```

- [ ] **Step 1.5（R1 Bug 1 关键修正）**: 显式删除生产路径中的 `_has_effective_review_checklist` 调用 + 固定旧字段：

  ```python
  # backend/skill.py:474 — _stage_five_completion_state 改：
  # 旧：review_checklist_ready = self._has_effective_review_checklist(project_path)
  # 新：review_checklist_ready = False  # 永远 False（向后兼容字段）
  
  # backend/skill.py:478-479 — missing_for_review_pass 已经在前面 Step 4 切换为读 new helper
  
  # backend/skill.py:1564 — _infer_stage_state 中：
  # 旧：review_checklist_ready = stage_five_state["review_checklist_ready"]
  # 新：删除该行（不再需要计算）
  
  # backend/skill.py:1624-1626 — flags 字典中：
  # 旧：
  #   "review_checklist_ready": review_checklist_ready,
  #   "review_ready": review_checklist_ready and review_passed,
  # 新：
  #   "review_checklist_ready": False,  # 永远 False
  #   "review_ready": stage_five_state["review_reports_ready"] and review_passed,
  
  # backend/skill.py:1679, 1683 — _build_completed_items 中：
  # 旧 if flags["review_checklist_ready"]: completed.append(...)
  # 已经在 Step 5 改用 flags["independent_review_ready"] / flags["lint_report_ready"]
  
  # _has_effective_review_checklist() 函数本身**保留**（向后兼容），但 grep 验证它不再被生产路径调用：
  # 应只出现在函数定义处 + backwards-compat 测试中
  ```

- [ ] **Step 2**: 切换 `CHECKPOINT_PREREQ.review_passed_at`：

  ```python
  "review_passed_at": (
      "_has_effective_review_reports",  # 新 helper
      "plan/independent-review.md, plan/lint-report.md",
      "需要先完成独立审查和 AI 味自查，才能标记审查通过。",
      "请先在 S5 阶段点击上方'独立审查'和'AI 味自查'按钮，再确认审查通过。",
  ),
  ```

- [ ] **Step 3**: 更新 `STAGE_CHECKLIST_ITEMS["S5"]`：

  ```python
  "S5": [
      "独立审查完成",
      "AI 味自查完成",
      "事实、逻辑与语言质量审查完成",
  ],
  ```

- [ ] **Step 4**: 改 `_stage_five_completion_state` `missing_for_review_pass`：

  ```python
  missing_for_review_pass = list(stage_four_state["missing_for_stage_four"])
  if not independent_review_ready:
      missing_for_review_pass.append("independent-review.md（请先点'独立审查'按钮）")
  if not lint_report_ready:
      missing_for_review_pass.append("lint-report.md（请先点'AI 味自查'按钮）")
  ```

- [ ] **Step 5**: 改 `_build_completed_items` S5 两处逻辑（行 1652 历史 + 行 1678 当前）按 spec §4.4 给出的代码：

  - 历史 S5：用 `flags["independent_review_ready"]` / `flags["lint_report_ready"]`
  - 当前 S5：同上 + 两份都 ready 时勾选第 3 项

- [ ] **Step 6**: 加测试：

  ```python
  def test_formal_plan_files_no_longer_includes_review_checklist(self):
      self.assertNotIn("review-checklist.md", SkillEngine.FORMAL_PLAN_FILES)
  
  def test_checkpoint_prereq_review_passed_at_uses_new_helper(self):
      prereq = SkillEngine.CHECKPOINT_PREREQ["review_passed_at"]
      self.assertEqual(prereq[0], "_has_effective_review_reports")
  
  def test_advance_stage_review_passed_at_rejects_missing_reports(self):
      # 项目无 independent-review.md / lint-report.md → record_stage_checkpoint 抛 ValueError
      # 错误消息含 "独立审查" / "AI 味自查" 按钮引导文案
  
  def test_advance_stage_review_passed_at_accepts_when_both_reports_ready(self):
      # 写好两份合规报告 → record_stage_checkpoint 成功
  
  def test_build_completed_items_s5_uses_new_flags(self):
      # 现 S5 + independent_review_ready=True, lint_report_ready=False
      # → completed 含 "独立审查完成"，不含 "AI 味自查完成"
  ```

**R1 Bug 4 + R2 Bug 11 修正——Rewrite Existing Regression Tests（Commit 4 不可绕开）**：

现有测试硬编码旧 S5 契约，Commit 4 必须同步改写：

| 测试位置 | 旧期望 | 新期望 / 处理 |
|---|---|---|
| `tests/test_skill_engine.py:292-309` | 新项目 `plan/` 含 `review-checklist.md` | 新项目 `plan/` 含 `independent-review.md` + `lint-report.md`，**不**含 `review-checklist.md` |
| `tests/test_skill_engine.py:442-452` | `stage-gates.md` 含 "review-checklist.md 完成" | 改为新三项："独立审查完成（plan/independent-review.md）" + "AI 味自查完成（plan/lint-report.md）" + "事实、逻辑与语言质量审查完成" |
| `tests/test_skill_engine.py:1234-1270` | S5 next_actions 指向 `review-checklist.md` | 改为指向"独立审查"+"AI 味自查"按钮文案（按 spec §1.4） |
| `tests/test_workspace_materials.py:240-245` | 同上 next_actions 旧期望 | 同上 |
| **R2 Bug 11 新增**：`tests/test_chat_runtime.py:5336-5456` | review-checklist 写入门禁测试（self-signature 等） | 改造为新 `plan/independent-review.md` / `plan/lint-report.md` 主代理拒写测试（Commit 2 Task 2.2 已加了拒写拦截，这里锁定行为） |
| `tests/test_chat_runtime.py:5901-5924` | review-checklist 写入相关 | 同上 |
| `tests/test_chat_runtime.py:6041-6052` | review-checklist 写入相关 | 同上 |
| `tests/test_main_api.py:570-573` | API system notice 引用 review-checklist 路径和原因 | 改为引用新报告路径 + 新 system notice 原因（按 SKILL 新文案） |
| `tests/test_skill_engine.py:185-189` | helper `_write_review_checklist` 推进 S5 | 改名 `_write_independent_review_and_lint_report` 写两份合规报告；或在 fixture 里直接写文件不通过 helper |
| `tests/test_skill_engine.py:1584-1591` | 同上 helper 推进 | 同上 |
| `tests/test_skill_engine.py:1609-1618` | 同上 helper 推进 | 同上 |

- [ ] **Step 7**: 逐文件改写上述 11 处测试 + grep 验证仓库内无残留 `review-checklist.md 完成` / `完成 review-checklist.md` 字面量在测试中（除 backwards-compat 测试明确标注）

- [ ] **Step 7.5**: grep 验证 `_write_review_checklist` 真实定义位置 + 重命名为 `_write_independent_review_and_lint_report`（或保留旧 helper 作为 backwards-compat，新增 wrapper helper）

> **R2 Bug 11 重要 catch**：Commit 4 移除 `review-checklist.md` 后 `validate_plan_write` 会先因非正式 plan 文件拒绝（skill.py:1057-1060），不会再走旧 self-signature 逻辑（skill.py:1096-1110）。所以旧 write-gate 测试会失败或锁定退役语义——必须主动改写或删除。

**Acceptance Criteria:**

- 5 个新测试通过 + 4 个旧测试改写后通过
- **旧** `_has_effective_review_checklist` 单测可能失败——选项：
  - (a) 改成 `@pytest.mark.skip(reason="superseded by _has_effective_review_reports in Commit 4")`
  - (b) 改名为 `test_*_backwards_compat`，仅验证函数仍可被调用
  - 推荐 (b)
- grep 验证 `_has_effective_review_checklist` 只出现在函数定义 + backwards-compat 测试中，不在生产代码路径

---

## Task 4.2: `_chat_stream_unlocked` 激活 S5 welcome 注入

**Files:**

- Modify: `backend/chat.py:_chat_stream_unlocked`
- Test: `tests/test_chat_runtime.py`

**R4 R1-carry-over 修正**：原 plan Step 1 写 `messages.append({"role": "system", "content": S5_WELCOME_PROMPT})`——但 `_chat_stream_unlocked` 内**没有** `messages` 变量。provider conversation 是每轮由 `_build_provider_turn_conversation` 构造（chat.py:2501）。S5 welcome 必须走 Task 3.0 的 `additional_system_messages` 通道，复用 Task 3.3 顶部分支结构。

**Steps:**

- [ ] **Step 1**: 在 `_chat_stream_unlocked` **顶部已有的 system_trigger 分支结构上**，加 `elif` 分支处理 S5 welcome（与 system_trigger 同款机制：每轮注入 transient system message）：

  ```python
  def _chat_stream_unlocked(self, project_id, message_text, ...,
                            system_trigger: Optional[str] = None):
      # ...现有 setup 逻辑（auth check / project resolve / ...）不变...
      
      welcome_injected = False
      
      if system_trigger:
          # Task 3.3 Step 3 已有逻辑：placeholder current_user_message + transient_system_messages
          ...（不变）
      else:
          # 正常 user message turn 逻辑（不变）
          transient_system_messages = None
          include_current_user = True
          # current_user_message / provider_user_message 走原逻辑
          
          # R4 R1-carry-over 修正：S5 welcome 走同款 transient_system_messages 通道
          if self._should_emit_s5_welcome(project_id):
              welcome_injected = True
              transient_system_messages = [{"role": "system", "content": S5_WELCOME_PROMPT}]
              # 不动 current_user_message / include_current_user——这是正常 user turn
              # user message 仍正常持久化；welcome 只是额外 transient system 提示
      
      # ...其他现有 setup（history load / obligation snapshots / etc.）不变...
      
      # Stream loop 每轮传 transient_system_messages（与 Task 3.3 Step 3 同款）：
      while iteration < max_iterations:
          conversation, current_turn_start_index = self._build_provider_turn_conversation(
              project_id=project_id,
              history=history,
              current_user_message=current_user_message,
              current_turn_messages=current_turn_messages,
              additional_system_messages=transient_system_messages,  # welcome 或 trigger 或 None
              include_current_user=include_current_user,
          )
          # ...其他 stream loop 逻辑不变...
      
      # turn 走完后：
      persisted_content = self._finalize_assistant_turn(...)
      if welcome_injected and persisted_content and persisted_content.strip():
          # 只有真的输出了 assistant message 才标记
          self._mark_s5_welcome_shown(project_id)
      return persisted_content
  ```

- [ ] **Step 2**: 加测试：

  ```python
  def test_s5_first_entry_injects_welcome_and_marks_shown(self):
      # 项目进入 S5 + welcome 未发 → 主代理 turn 后注入 welcome + 写 s5_welcome_shown_at
  
  def test_s5_repeat_entry_no_double_welcome(self):
      # s5_welcome_shown_at 已写 → welcome 不重复注入
  
  def test_s5_welcome_not_marked_when_turn_fails(self):
      # mock LLM call 失败 → s5_welcome_shown_at 不写
      # 下次进 S5 仍会重发欢迎信
  
  def test_s5_welcome_not_emitted_in_non_s5_stages(self):
      # stage_code=S4 → 不注入
  ```

**Acceptance Criteria:**

- 4 个测试通过
- 配合 Task 3.5 的 helper 单测形成完整覆盖

---

## Task 4.3: Skill 文档同步

**Files:**

- Modify: `skill/SKILL.md` (S5 段)
- Modify: `skill/modules/consulting-lifecycle.md` (行 20)
- Modify: `skill/plan-template/progress.md` (行 42)
- Modify: `skill/plan-template/stage-gates.md` (行 40-43)
- Modify: `skill/plan-template/tasks.md` (行 44-45)
- Test: `tests/test_packaging_docs.py`

**Steps:**

- [ ] **Step 1**: 改 `skill/SKILL.md` S5 段（行 138-142），用 spec §7.1 完整文本替换

- [ ] **Step 2**: 改 `skill/modules/consulting-lifecycle.md` 第 20 行，按 spec §7.2 替换（行 50 不动）

- [ ] **Step 3**: 改 `skill/plan-template/progress.md` 第 42 行：

  ```text
  | S5 | 质量审查 | `independent-review.md` / `lint-report.md` | | |
  ```

- [ ] **Step 4**: 改 `skill/plan-template/stage-gates.md` 第 40-43 行：

  ```text
  ### S5 质量审查 ⬜
  - [ ] 独立审查完成（plan/independent-review.md）
  - [ ] AI 味自查完成（plan/lint-report.md）
  - [ ] 事实、逻辑与语言质量审查完成
  ```

- [ ] **Step 5**: 改 `skill/plan-template/tasks.md` 第 44-45 行：

  ```text
  ### S5 质量审查
  - [ ] 点击工作区"独立审查"按钮（生成 `plan/independent-review.md`）
  - [ ] 点击工作区"AI 味自查"按钮（生成 `plan/lint-report.md`）
  - [ ] 主代理基于两份报告与用户讨论修改方向
  ```

- [ ] **Step 6**: 更新 `tests/test_packaging_docs.py` 锁定句子：

  ```python
  def test_skill_md_s5_section_uses_new_workflow(self):
      content = (REPO / "skill" / "SKILL.md").read_text(encoding="utf-8")
      self.assertIn("独立审查", content)
      self.assertIn("AI 味自查", content)
      self.assertIn("plan/independent-review.md", content)
      self.assertIn("plan/lint-report.md", content)
      self.assertNotIn("完成 review-checklist.md", content)
  ```

**Acceptance Criteria:**

- 5 个文件按 spec 同步修改
- `tests/test_packaging_docs.py` 全部通过

---

## Task 4.4: 前端按钮阶段化 + IndependentReviewDrawer 组件

**Files:**

- Create: `frontend/src/components/IndependentReviewDrawer.jsx`
- Modify: `frontend/src/components/StagePanel.jsx:179-193`
- Modify: `frontend/src/components/WorkspacePanel.jsx`
- Modify: `frontend/src/utils/workspaceSummary.js`
- Test: `frontend/tests/independentReviewDrawer.test.mjs` + `stagePanelButtons.test.mjs` + `workspaceSummary.test.mjs`

**Steps:**

- [ ] **Step 1**: 创建 `IndependentReviewDrawer.jsx` 按 spec §9.1 完整骨架（fetch + ReadableStream + auto-close + ESC 关闭 + 不显示关闭按钮）

- [ ] **Step 2**: 改 `StagePanel.jsx`：

  - 删除"运行质量检查"按钮
  - 加"独立审查"按钮（仅 S5 显示 + 高亮 + running 时 disabled）
  - 加"AI 味自查"按钮（同上）
  - "导出可审草稿"按钮加阶段条件 `stageCode in ['S6', 'S7', 'done']`
  - StagePanel 下一步建议文字按报告状态动态显示（spec §1.4）

- [ ] **Step 3**: 改 `WorkspacePanel.jsx`：

  - 删除 `runQualityCheck` 调用（保留函数定义供向后兼容如有外部调用）
  - 加 drawer trigger + axios 调用新 endpoint
  - 加 per-project running 状态（reviewRunning / lintRunning）

- [ ] **Step 4**: 改 `workspaceSummary.js` 字段映射：

  ```js
  flags: {
      ...,
      independentReviewReady: raw.flags?.independent_review_ready ?? false,
      lintReportReady: raw.flags?.lint_report_ready ?? false,
      reviewReportsReady: raw.flags?.review_reports_ready ?? false,
  }
  ```

- [ ] **Step 5**: 加测试（**R1 Bug 5 修正：前端测试栈不支持 React 组件运行时测试**——`frontend/package.json` 无 jsdom / testing-library / react-test-renderer；现有 `confirmDialogA11y.test.mjs:4-5` 明确说 node:test 不能 render React。改为两类测试）：

  **类型 A：纯函数测试**（抽 helper 后单独测）：

  ```js
  // independentReviewDrawer.test.mjs — 抽 SSE event parser 为纯函数
  // 在 IndependentReviewDrawer.jsx 内 export 一个 parseDrawerEvent(data) 纯函数
  test('parseDrawerEvent recognizes review-completed event')
  test('parseDrawerEvent recognizes error event')
  test('parseDrawerEvent ignores malformed payload')
  
  // stagePanelButtons.test.mjs — 抽 shouldShowButton(buttonKey, stageCode, flags) 为纯函数
  test('shouldShowButton: independent_review hidden in S0-S4')
  test('shouldShowButton: independent_review visible in S5')
  test('shouldShowButton: lint_report visible in S5')
  test('shouldShowButton: export hidden until S6')
  test('shouldShowButton: export visible in S6/S7/done')
  test('shouldShowButton: buttons should highlight when not ready')
  test('shouldShowButton: buttons disabled when running')
  
  // workspaceSummary.test.mjs（扩展）
  test('maps independent_review_ready / lint_report_ready / review_reports_ready')
  ```

  **类型 B：source-level guard**（grep 锁死关键代码模式）：

  ```js
  // independentReviewDrawer.source.test.mjs — grep 验证关键代码存在
  test('IndependentReviewDrawer.jsx uses AbortController for fetch lifecycle', () => {
      const src = readFileSync(path.join(__dirname, '../src/components/IndependentReviewDrawer.jsx'), 'utf-8')
      assert.ok(/new AbortController\(\)/.test(src), 'must use AbortController')
      assert.ok(/controller\.abort\(\)/.test(src), 'must call abort on cleanup')
  })
  
  test('IndependentReviewDrawer.jsx listens for ESC keydown', () => {
      const src = readFileSync(...)
      assert.ok(/keydown.*Escape|key === ['"]Escape['"]/.test(src), 'must handle ESC')
  })
  
  test('IndependentReviewDrawer.jsx calls onTriggerSystemTurn after review-completed', () => {
      const src = readFileSync(...)
      assert.ok(/review-completed/.test(src))
      assert.ok(/onTriggerSystemTurn|triggerSystemTurn/.test(src))
  })
  ```

  类型 B 锁死实施层面的关键行为模式，不需要 React 运行时。

**说明**：spec 中提到的"reviewer drawer 测试覆盖"用类型 A + 类型 B 组合实现——SSE parser 用纯函数测，UI 行为用 source grep guard。这对齐 `frontend/tests/confirmDialogA11y.test.mjs` 现有测试模式，不引入新依赖。

**Acceptance Criteria:**

- Drawer 组件按 spec §9.1 行为
- 按钮阶段化按 spec §1.2
- 所有前端测试通过

---

## Task 4.5: ChatPanel.sendMessage 重构为 startStream + App.jsx wiring

**Files:**

- Modify: `frontend/src/components/ChatPanel.jsx:411-...`
- Modify: `frontend/src/App.jsx:230-253` (**R1 Bug 3 修正：必须改父组件 wire 起来**)
- Modify: `frontend/src/components/WorkspacePanel.jsx` + `StagePanel.jsx`（接受 `onTriggerSystemTurn` prop）
- Test: `frontend/tests/chatPanelStartStream.test.mjs`

**Steps:**

- [ ] **Step 1**: 抽 `startStream({ messageText, systemTrigger, attachedMaterialIds, transientAttachments, renderUserBubble })` 函数（按 spec §5.4 给出的代码）

- [ ] **Step 2**: `sendMessage` 改为薄 wrapper 调用 `startStream({ ..., systemTrigger: null, renderUserBubble: true })`

- [ ] **Step 3**: ChatPanel 用 `forwardRef + useImperativeHandle` 暴露 `triggerSystemTurn(triggerType)`：

  ```jsx
  // ChatPanel.jsx 顶部加：
  import { forwardRef, useImperativeHandle } from 'react'
  
  const ChatPanel = forwardRef(function ChatPanel(props, ref) {
      // ...existing state and helpers...
      
      const triggerSystemTurn = useCallback((triggerType) => {
          startStream({
              messageText: '',
              systemTrigger: triggerType,
              renderUserBubble: false,
          })
      }, [startStream])
      
      useImperativeHandle(ref, () => ({ triggerSystemTurn }), [triggerSystemTurn])
      
      // ...rest of component...
  })
  ```

- [ ] **Step 4**: 改 `App.jsx:230-253` 把 ChatPanel 接 ref + 把 `onTriggerSystemTurn` callback 传 WorkspacePanel：

  ```jsx
  // App.jsx
  const chatPanelRef = useRef(null)
  
  // ...
  <ChatPanel
      ref={chatPanelRef}
      projectId={currentProjectId}
      // ...其他 props...
  />
  {showWorkspacePanel && (
      <WorkspacePanel
          // ...其他 props...
          onTriggerSystemTurn={(triggerType) => chatPanelRef.current?.triggerSystemTurn(triggerType)}
      />
  )}
  ```

- [ ] **Step 5**: 改 `WorkspacePanel.jsx` / `StagePanel.jsx` 接受并使用 `onTriggerSystemTurn` prop（**R2 Bug 12 修正**：触发 system turn 前先 GET workspace 二次确认 server 已 ready，避免主代理读到未通过软门禁的报告）：

  ```jsx
  // WorkspacePanel.jsx
  const runLintReport = async () => {
      const res = await axios.post(`/api/projects/${projectId}/lint-report`)
      if (res.data.status !== 'ok') return
      onProjectMutated?.()
      
      // R2 Bug 12：触发前先 GET workspace 验证 lintReportReady
      const ws = await axios.get(`/api/projects/${projectId}/workspace`)
      if (ws.data.flags?.lint_report_ready) {
          props.onTriggerSystemTurn?.('lint_report_done')
      } else {
          showError('AI 味自查报告未通过服务端校验，请重试')
      }
  }
  
  // StagePanel.jsx — IndependentReviewDrawer onCompleted 同样模式
  const onIndependentReviewCompleted = async (reportPath) => {
      const ws = await axios.get(`/api/projects/${projectId}/workspace`)
      if (ws.data.flags?.independent_review_ready) {
          props.onTriggerSystemTurn?.('independent_review_done')
      } else {
          showError('独立审查报告未通过服务端校验，请重试')
      }
  }
  ```

- [ ] **Step 6**: 加测试（按 R1 Bug 5 修法：纯函数 + source-level guard，含 R2 Bug 12 workspace ready check 测试）：

  ```js
  // chatPanelStartStream.test.mjs
  // 抽 buildChatStreamRequest({ messageText, systemTrigger, ...}) 为纯函数（如还没有）
  test('buildChatStreamRequest passes system_trigger in body')
  test('buildChatStreamRequest empty messageText when system_trigger set')
  
  // source-level guard
  test('ChatPanel.jsx uses forwardRef and exposes triggerSystemTurn')
  test('App.jsx wires chatPanelRef.triggerSystemTurn into WorkspacePanel via onTriggerSystemTurn prop')
  
  // R2 Bug 12: workspace ready 二次确认
  test('WorkspacePanel.runLintReport awaits workspace GET before onTriggerSystemTurn', () => {
      // grep WorkspacePanel.jsx source 验证 lint 完成后调 axios.get workspace + 检查 lint_report_ready
      const src = readFileSync('frontend/src/components/WorkspacePanel.jsx', 'utf-8')
      assert.ok(/lint_report_ready|lintReportReady/.test(src))
  })
  test('IndependentReviewDrawer onCompleted awaits workspace independent_review_ready')
  ```

- [ ] **Step 7**: `startStream` 必须 `useCallback`（R2 改进建议 2）+ 复用现有防重入语义：

  ```jsx
  const startStream = useCallback(async ({ messageText, systemTrigger, attachedMaterialIds = [], 
                                            transientAttachments = [], renderUserBubble = true }) => {
      // 防重入：复用现有 loading/uploading state（ChatPanel.jsx:71-87）
      if (loading || uploading) return
      // ...
  }, [loading, uploading, projectId, /* 完整依赖列表 */])
  ```

- [ ] **Step 8**: `buildChatRequest` extension（R2 改进建议 3 + R3 Bug 16）：`frontend/src/utils/chatMaterials.js:32-46` 中 messageText 默认 ""，避免 `.trim()` 抛错。**但要同步改写 `frontend/tests/chatMaterials.test.mjs:35-75`**——现有测试锁定 trim + 空字段省略，会被破坏。

  **新设计**（保留向后兼容 + 加 system_trigger 字段）：

  ```js
  // frontend/src/utils/chatMaterials.js
  export function buildChatRequest({ projectId, messageText = '', attachedMaterialIds = [], 
                                      transientAttachments = [], systemTrigger = null }) {
      const payload = {
          project_id: projectId,
      }
      // messageText：保留 trim 行为（向后兼容现有测试），但 system_trigger 时允许空
      const trimmed = typeof messageText === 'string' ? messageText.trim() : ''
      payload.message_text = systemTrigger ? messageText : trimmed
      // attached_material_ids：只在非空时写入（向后兼容）
      if (attachedMaterialIds.length > 0) payload.attached_material_ids = attachedMaterialIds
      // transient_attachments：只在非空时写入
      if (transientAttachments.length > 0) payload.transient_attachments = transientAttachments
      // system_trigger：只在非 null 时写入
      if (systemTrigger) payload.system_trigger = systemTrigger
      return payload
  }
  ```

  **同步改写 `frontend/tests/chatMaterials.test.mjs:35-75`** —— R3 Bug 16：

  ```js
  // 现有测试保留（验证普通 sendMessage trim 行为）
  test('buildChatRequest trims messageText by default')
  test('buildChatRequest omits empty optional fields')
  
  // 新增（R3 Bug 16）
  test('buildChatRequest with systemTrigger allows empty messageText', () => {
      const req = buildChatRequest({ projectId: 'demo', messageText: '', systemTrigger: 'independent_review_done' })
      assert.equal(req.message_text, '')
      assert.equal(req.system_trigger, 'independent_review_done')
  })
  
  test('buildChatRequest omits system_trigger when null', () => {
      const req = buildChatRequest({ projectId: 'demo', messageText: 'hi' })
      assert.equal(Object.prototype.hasOwnProperty.call(req, 'system_trigger'), false)
  })
  ```

**Acceptance Criteria:**

- 6 个测试通过
- 现有 sendMessage 测试不变
- App.jsx wiring 完整——WorkspacePanel 点 lint 按钮 → axios.post lint-report → axios.get workspace → 验证 ready → 调 `onTriggerSystemTurn('lint_report_done')` → 触发 ChatPanel 起新 stream
- ChatPanel 内部 startStream 路径既支持普通 user message（renderUserBubble=true）也支持 system_trigger（renderUserBubble=false）
- `startStream` useCallback 依赖完整不破闭包
- `buildChatRequest` messageText 默认 "" 避免 trim 抛错

---

## Task 4.6: smoke REQUIRED_PLAN_FILES 同步

**Files:**

- Modify: `tests/smoke_packaged_app.py:39-54`

**Steps:**

- [ ] **Step 1**: 改 `REQUIRED_PLAN_FILES`（R1 Bug 6 修正：常量真实名字是 `REQUIRED_PLAN_FILES`，不是 `EXPECTED_PLAN_FILES`）：

  ```python
  REQUIRED_PLAN_FILES = {
      "project-overview.md",
      "progress.md",
      "stage-gates.md",
      "notes.md",
      "outline.md",
      "research-plan.md",
      "references.md",
      "tasks.md",
      "review.md",
      "data-log.md",
      "analysis-notes.md",
      "independent-review.md",  # 新增
      "lint-report.md",  # 新增
      "presentation-plan.md",
      "delivery-log.md",
      # "review-checklist.md",  # 退役
  }
  ```

**Acceptance Criteria:**

- 打包态 smoke 测试用新 `REQUIRED_PLAN_FILES` 通过

---

## Commit 4 Acceptance

```bash
.venv\Scripts\python -m pytest tests/ -q
cd frontend && node --test tests/ && npm run build && cd ..
```

应全部通过。

**用户可见 atomic 切换**——Commit 4 落 main 后：
- 用户进 S5 → 主代理欢迎信介绍新流程
- UI 显示两个新按钮 + 高亮
- 旧"运行质量检查"按钮消失
- "导出可审草稿"按钮 S5 不显示
- `advance_stage(review_passed_at)` 校验两份新报告

---

# Commit 5：端到端 + 打包态 smoke + cutover

## Task 5.1: 端到端 piggy-v2

- [ ] **Step 1**: 新建测试项目 `piggy-v2`
- [ ] **Step 2**: 手工跑 S0-S7 完整流程（按 spec §11.3 步骤 1-10）
- [ ] **Step 3**: 验证：
  - S5 进入 → 欢迎信 + 按钮高亮
  - 点"独立审查" → drawer 弹 → 流式工作 → 报告落 → drawer 关
  - 主代理自动 turn → partner 风格摘要
  - 用户反馈"先改前两条" → 主代理 edit_file 改正文
  - 点"AI 味自查" → 报告生成 → 主代理按章节报告
  - 用户"通过" → 主代理 advance_stage(review_passed_at) → S6/S7
  - 老项目 review-checklist.md 残留不阻断推进

**Acceptance Criteria:**

- 10 项验收全过
- 不出现"我已经写了 review-checklist.md"这种历史用语

---

## Task 5.2: 打包态 dist 重建 + smoke

**R3 Bug 15 修正**：smoke 明确"不调 /api/chat 不消耗 LLM/搜索 API 额度"（`tests/smoke_packaged_app.py:7-9`）+ smoke 项目停在 S0（`:226-232`）。新 endpoint 要求 S5——packaged smoke 不应进 S5 调真实 LLM。

**重新拆分测试覆盖**：
- **packaged smoke**（无 LLM）：测 S0 状态下新 endpoint 返回 400 + 模板/脚本存在 + 旧 `/quality-check` + `/export-draft` 兼容
- **`tests/test_main_api.py`**（mock agent）：测 S5 正常流 + lock + SSE content-type + 409 并发

- [ ] **Step 1**: 跑 `build.bat`

- [ ] **Step 2**: 扩展 `tests/smoke_packaged_app.py` —— **不调用真实 LLM**：
  
  ```python
  # smoke 项目仍停 S0；新 endpoint 调用应得 400（"只能在 S5 阶段使用"）
  
  # 1. POST /api/projects/{id}/lint-report → 期待 400（S5 校验拒）
  resp = post(f"/api/projects/{pid}/lint-report")
  assert resp.status_code == 400 and "S5" in resp.json()["detail"]
  
  # 2. GET /api/projects/{id}/independent-review/stream → 期待 400（S5 校验拒）
  resp = get(f"/api/projects/{pid}/independent-review/stream")
  assert resp.status_code == 400 and "S5" in resp.json()["detail"]
  
  # 3. 保留 POST /api/projects/{id}/quality-check 兼容验证（旧 endpoint 仍工作）
  # 4. 保留 POST /api/projects/{id}/export-draft
  # 5. 验证模板和脚本存在：
  assert (dist / "_internal/skill/plan-template/independent-review.md").exists()
  assert (dist / "_internal/skill/plan-template/lint-report.md").exists()
  assert (dist / "_internal/skill/scripts/quality_check.ps1").read_text().startswith(...)  # 含 -OutputPath
  ```

- [ ] **Step 3**: 在 `tests/test_main_api.py` 用 mock agent 覆盖**真实 endpoint 行为**（不依赖打包 + 不调 LLM）：

  ```python
  def test_independent_review_endpoint_returns_sse_content_type(self):
      # 准备 S5 项目 fixture（写完 outline / data-log / analysis-notes / report_draft）
      # mock IndependentReviewAgent.run() 返回 fake events
      # 验证 response.headers["content-type"].startswith("text/event-stream")
  
  def test_independent_review_endpoint_409_when_lock_held(self):
      # 持锁状态调 endpoint → 409
  
  def test_independent_review_endpoint_releases_lock_on_disconnect(self):
      # mock request.is_disconnected() True → generator 退出 + lock 释放
  
  def test_lint_report_endpoint_returns_summary(self):
      # mock run_lint_report 返回 {status: ok, path, summary}
      # 验证 endpoint 透传 summary
  ```

- [ ] **Step 4**: 跑 `tests/smoke_packaged_app.py` + `tests/test_main_api.py`

- [ ] **Step 5**: 手工启动 `dist/咨询报告助手/咨询报告助手.exe` + 走 piggy-v2 流程（这里才**真的**触发 endpoint + 真实 LLM）

- [ ] **Step 6**: 验证打包内容：
  - `_internal/skill/plan-template/independent-review.md` 存在
  - `_internal/skill/plan-template/lint-report.md` 存在
  - `_internal/skill/scripts/quality_check.ps1` 是 v2 新版（含 `-OutputPath` 参数）

**Acceptance Criteria:**

- dist 重建成功
- packaged smoke 通过（新 endpoint 在 S0 返回 400 + 模板/脚本存在 + 旧 endpoint 兼容）
- `tests/test_main_api.py` 新 mock agent 测试通过（覆盖 SSE / lock / 409 / disconnect）
- 手工 E2E 在打包态走通（Task 5.1 的端到端）

---

## Task 5.3: Cutover doc + worklist update

- [ ] **Step 1**: 创建 `docs/superpowers/cutover_report_2026-05-21_s5-redesign.md`，记录：
  - 实施概述（5 commits）
  - 验证结果（测试 + 端到端 + 打包态 smoke）
  - 已知限制（30k 字 friendly fail / 单进程 lock 假设）
  - 老项目兼容性确认（review-checklist.md 残留不阻断）

- [ ] **Step 2**: 更新 `docs/current-worklist.md`：
  - 把 P1 #2 "S5 review-checklist 格式契约" 标 `已解决`
  - 加 "S5 Independent Review Redesign（2026-05-21）" 入"已解决记录"段
  - 记录 v1 chunk fallback worklist 项（超 30k 字 map-reduce）

**Acceptance Criteria:**

- cutover report 落地
- worklist 更新

---

## 实施分阶段 review loop

按 CodeProject CLAUDE.md 默认工作法：

- 每个 commit 末嵌入 codex spec-compliance + quality review 双轮
- 用 codex exec 派活（裸命令，不走 plugin）
- review 不通过 → 修 → 再 review，直到 APPROVED 才进下一 commit

---

## Risk & Rollback

| 风险 | Rollback 策略 |
|---|---|
| Commit 2 后主代理误写新文件 | 拒写拦截在 Commit 2 同 commit，不会出现 |
| Commit 4 主代理收到欢迎信但前端按钮没出现 | Commit 4 atomic 同步，无中间态 |
| 老项目升级后 S5 卡住 | `_has_effective_review_reports` 返回 False，错误消息引导用户点按钮（不阻断推进，只阻断 advance_stage） |
| DeepSeek 官渠拒 tool_choice | 复用 chat.py `_should_send_explicit_tool_choice` 真实逻辑 + 行为矩阵测试锁死等价 |
| 独立审查代理 token 超限 | v0 friendly fail，UI 显示"正文过长，请精简后重试"|

### Commit-level Rollback Procedure（R1 Suggestion 1）

如果 Commit 4 用户可见 cutover 出问题，必须回滚：

1. **Git revert Commit 4 整体**（不要 cherry-pick 部分文件——backend gate / SKILL / 前端必须同步回滚）
2. 回滚后系统状态：
   - 老 `CHECKPOINT_PREREQ.review_passed_at` 重新指向 `_has_effective_review_checklist`
   - SKILL.md S5 段恢复要求 `review-checklist.md`
   - 前端按钮恢复"运行质量检查" + "导出可审草稿"始终显示
   - **Commit 2 / Commit 3 仍 dormant**——`independent-review.md` / `lint-report.md` 文件保留为用户数据（如 Commit 2-3 之后用户已经跑过审查），但软门禁不再读
3. 回滚验证：
   - 跑 `.venv\Scripts\python -m pytest tests/ -q` 验证 backend 回归（应使用 Commit 4 之前的旧测试断言）
   - 跑 `cd frontend && node --test tests/ && npm run build` 验证前端回归
   - 跑 `tests/smoke_packaged_app.py` 验证 packaged smoke 仍按旧 `REQUIRED_PLAN_FILES` 工作（包含 `review-checklist.md`，不包含两份新报告）
   - 手工 S5 流程验证用户可走回旧 review-checklist 路径

如果只 Commit 5 出问题（cutover doc 写错 / smoke 失败），回滚 Commit 5 即可——Commit 4 用户可见 cutover 已生效不需要动。

---

**End of plan**
