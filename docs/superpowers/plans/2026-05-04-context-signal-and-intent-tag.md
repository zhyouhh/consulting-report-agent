# Context Signal & Intent Tag Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复后端与模型之间的"信号通道"分层错位（5 个 reality_test 暴露的 Bug A-E）：A1 system_notice 用户/模型分层，A2 progress.md 阈值回喂，A3 空 assistant 兜底分层 + 合并相邻 user，B1 `<draft-action>` tag 替换关键词遍历主路径同时保留 preflight 粗粒度门禁，C1 工具历史可见化（HTML 注释格式 + 三层 sanitize）。

**Architecture:**
- A1：`SystemNotice` 加 `surface_to_user` **必填字段**（无默认）；`_emit_system_notice_once` dedupe 拆 `user_notice_emitted` / `internal_notice_emitted` 双 flag；服务端 SSE / 非流式响应只发 `surface_to_user=True`
- A2：`_render_progress_markdown` 接收 `stage_state` 渲染 `**质量进度**: 5/7 条 有效来源`；`_persist_successful_tool_result` 对 plan/data-log.md / plan/analysis-notes.md 追加 `quality_hint` JSON 字段
- A3：抽 `_finalize_empty_assistant_turn` helper 统一三处持久化点；`_coalesce_consecutive_user_messages` 在 provider build 合并相邻 user（防 Gemini 角色交替 400）；三层 sanitize（GET /conversation + 前端 history loader + provider build）清理历史污染
- B1：保留 preflight `_classify_canonical_draft_turn`（rename 为 `_preflight_canonical_draft_check`）做粗粒度门禁，新增 `preflight_keyword_intent: "begin"|"continue"|None` 字段；新建 `backend/draft_action.py` 解析 `<draft-action>` 4 种 intent；`_gate_canonical_draft_tool_call` 强制 tag 校验，仅 `append_report_draft` 允许 tagless fallback
- C1：HTML 注释格式 `<!-- tool-log ... -->` 追加在 assistant message stripped 版本末尾；三层 sanitize（GET /conversation + 前端 render + 前端 copy 按钮）；`_pair_tool_calls_with_results` 严格按 `tool_call_id` 配对忽略 retry 隔板
- 编排：扩展 `_finalize_assistant_turn` 为统一编排器（7 步顺序：parse 两类 tag → 执行 stage-ack 副作用 → 执行 draft-action 副作用 → strip → 判空 → A3 或 append tool-log → 持久化）

**Tech Stack:** Python 3.11/3.12（`unittest` + `pytest`，`re`，`dataclasses`），Node 20 LTS（`node:test`），React 18（functional components），FastAPI，OpenAI client streaming，PyWebView + Vite。Windows 优先（PowerShell 文件操作；测试用 `.venv\Scripts\python`）。

---

## Source of Truth

Design spec: **`docs/superpowers/specs/2026-05-04-context-signal-and-intent-tag-design.md`**（APPROVED Round 5）。

Every task cites the spec sections it implements. **If a plan step conflicts with the spec, spec wins.**

---

## File Map

**New files:**
- `backend/draft_action.py` — `DraftActionEvent` dataclass + `DraftActionParser` class (parse/parse_raw/strip + position judge for simple tag + replace block)
- `tools/draft_decision_compare_report.py` — Phase 2a cutover artifact 报表脚本
- `tests/test_draft_action.py` — parser unit tests
- `tests/test_tool_log.py` — `_pair_tool_calls_with_results` + `_append_tool_log_to_assistant` + `_insert_before_tail_tags` + comment regex
- `tests/test_draft_decision_compare_report.py` — 脚本 smoke test

**Modified (backend):**
- `backend/models.py` — `SystemNotice` 加 `surface_to_user: bool` **必填字段**（无 default）
- `backend/chat.py` — 大量改造：
  - 顶部常量：新增 `_DRAFT_INTENT_PREFLIGHT_KEYWORDS`（极短列表，begin / continue 分组）；`USER_VISIBLE_FALLBACK` 常量；`LEGACY_EMPTY_ASSISTANT_FALLBACKS` 常量；`TOOL_LOG_COMMENT_RE` 常量
  - `_emit_system_notice_once` 改 dedupe 双 flag + `surface_to_user` 必填参数
  - 18 处 `_emit_system_notice_once` call site 全部加 `surface_to_user=...`（按 spec Appendix C）
  - `_persist_successful_tool_result` 对 plan/data-log.md / plan/analysis-notes.md 追加 `quality_hint`
  - `_to_provider_message` 加 sanitize 跳过历史 fallback assistant
  - `_build_provider_turn_conversation` 调 `_coalesce_consecutive_user_messages`
  - `_new_turn_context` 字段：拆 `user_notice_emitted` / `internal_notice_emitted`；新增 `tool_log_pairs`、`empty_assistant_diagnostic`
  - `_classify_canonical_draft_turn` rename 为 `_preflight_canonical_draft_check`，简化为粗粒度三问 + 输出 `preflight_keyword_intent`
  - 新增 `_finalize_empty_assistant_turn(project_id, history, current_user_message, *, diagnostic)` helper
  - 新增 `_record_empty_assistant_event(project_id, diagnostic)`
  - 新增 `_coalesce_consecutive_user_messages(conversation)`（含 `_normalize_content` 防御）
  - 新增 `_pair_tool_calls_with_results(current_turn_messages)`
  - 新增 `_append_tool_log_to_assistant(content, current_turn_messages)`
  - 新增 `_insert_before_tail_tags(content, block)`（复用 stage_ack `_tail_anchor` 思路扩展）
  - 新增 `strip_tool_log_comments(content)` helper（`TOOL_LOG_COMMENT_RE` 兼容 unclosed）
  - 新增 `_gate_canonical_draft_tool_call(project_id, tool_name, tool_args, decision, tags)`（v5 签名）+ 集成到工具放行路径
  - 新增 `_record_tagless_fallback_event(project_id, fallback_tool, fallback_intent)`
  - 新增 `_record_draft_decision_compare_event(...)` + `_record_draft_decision_exception_event(...)`
  - `_finalize_assistant_turn` 扩展为统一编排器（spec §5.6 7 步顺序）
  - 流式 tail guard（`_chat_stream_unlocked` / `_chat_unlocked`）扫描表加入 `<draft-action`、`<draft-action-replace` 前缀
  - `_chat_unlocked` / `_chat_stream_unlocked` / early finalize 三处空 assistant 兜底改用 `_finalize_empty_assistant_turn`
  - **删除（Phase 2 Step 2b）**：`_classify_canonical_draft_turn` 内部 begin/continue/section/replace 细分逻辑；`REPORT_BODY_FIRST_DRAFT_KEYWORDS` / `REPORT_BODY_EXPLICIT_CONTINUATION_KEYWORDS` / `REPORT_BODY_WHOLE_REWRITE_KEYWORDS` 常量；`REPORT_BODY_CHAPTER_WRITE_RE` / `REPORT_BODY_INLINE_EDIT_RE` 正则；`_regex_has_clean_report_body_intent` / `_has_explicit_report_body_write_intent` dead helper
- `backend/skill.py` — `_render_progress_markdown` 接收 `stage_state` 可选参数；`_sync_stage_tracking_files` 调用时传 `stage_state`
- `backend/main.py` — `GET /api/projects/{id}/conversation` 返回前 sanitize 历史 fallback + tool-log 注释

**Modified (frontend):**
- `frontend/src/components/ChatPanel.jsx` — `system_notice` 渲染按 `surface_to_user` 过滤；`messages` 加载时调用 sanitize；`copyMessage` 调用 `stripToolLogComments`；render 前调用 `stripToolLogComments`
- `frontend/src/utils/chatPresentation.js`（如不存在创建）— `LEGACY_EMPTY_ASSISTANT_FALLBACKS` 集合 + `sanitizeAssistantMessage` 函数
- `frontend/src/utils/toolLogStrip.mjs`（新建）— `TOOL_LOG_COMMENT_RE` + `stripToolLogComments` 函数

**Modified (docs/skill):**
- `skill/SKILL.md` — §S4 报告撰写节加"draft-action 标签规范"子节 + 附录"draft-action 标签规范"（结构对齐现有 stage-ack 附录）
- `tests/test_packaging_docs.py` — lock 新 SKILL.md 关键句

**Modified (tests):**
- `tests/test_chat_runtime.py` — A1 dedupe + audit、A3 helper + coalesce + sanitize、B1 preflight + draft-action 端到端、C1 pairing + insert + sanitize（共 ~75 条新增测试，详见各 task）
- `tests/test_skill_engine.py` — A2 progress.md 渲染 13 条
- `frontend/tests/chatPresentation.test.mjs` — A1 surface filter + A3 legacy sanitize + C1 tool-log strip + copy strip
- `frontend/tests/toolLogStrip.test.mjs`（新建）— 4 种 comment 边界 case

---

## Task Order & Dependencies

按 spec Rollout 拆两个 PR + 一次重打包：

```
Phase 1（一个 PR，预计 2-3 天）
├── Task 1   A1 SystemNotice surface_to_user 必填字段
├── Task 2   A1 _emit_system_notice_once dual dedupe + audit 18 call sites
├── Task 3   A1 服务端 + 前端 surface_to_user 过滤
├── Task 4   A2 _render_progress_markdown 渲染 quality_progress
├── Task 5   A2 tool_result quality_hint 追加
├── Task 6   A3 _finalize_empty_assistant_turn helper + 三处持久化改造
├── Task 7   A3 _coalesce_consecutive_user_messages 合并相邻 user
├── Task 8   A3 三层 sanitize 历史污染（GET API + 前端 + provider）
├── Task 9   C1 _pair_tool_calls_with_results 配对算法
├── Task 10  C1 _append_tool_log_to_assistant + _insert_before_tail_tags + format
├── Task 11  C1 strip_tool_log_comments backend + 前端 helper
├── Task 12  C1 三层 sanitize（GET API + 前端 render + 前端 copy）
├── Task 13  编排器整合 _finalize_assistant_turn（7 步顺序）+ Phase 1 集成测试
└── Task 14  Phase 1 reality_test smoke + commit + PR

Phase 2 Step 2a（独立 PR，新旧并行，预计 3-4 天）
├── Task 15  backend/draft_action.py 模块（DraftActionEvent + Parser）
├── Task 16  chat.py 流式 tail guard 加 draft-action 前缀
├── Task 17  preflight rename + 简化 + preflight_keyword_intent 字段
├── Task 18  draft-action event 解析 + §4.6 前置校验 + turn_context 写入
├── Task 19  _gate_canonical_draft_tool_call + tagless fallback + record events
├── Task 20  draft_decision_compare event 写入（v5 schema）+ exception event
├── Task 21  tools/draft_decision_compare_report.py 脚本 + smoke test
├── Task 22  SKILL.md §S4 + 附录加 draft-action 规范
└── Task 23  Phase 2a reality_test 跑 5 会话 + cutover artifact + 人工 review

Phase 2 Step 2b（紧接 2a，预计 1 天）
├── Task 24  确认切主条件 + 删除清单（grep 验证 + 删 _classify 细分 + 常量 + dead helper）
└── Task 25  下游引用调整 + 回归测试

Phase 3（重打包 + smoke，预计 0.5-1 天）
├── Task 26  build.ps1 重打包 + reality_test 端到端
└── Task 27  worklist + memory 同步
```

依赖关系：
- Task 1 → 2 → 3（A1 必填字段先建，再改 dedupe，再改前端）
- Task 6 → 7 → 8（A3 helper 先建，再合并算法，再 sanitize）
- Task 9 → 10 → 11 → 12（C1 自底向上）
- Task 13 必须在 1-12 全部完成后做（编排器整合所有 helper）
- Task 14 是 Phase 1 闭关
- Task 15-22 内部基本独立可并行（除 18 依赖 15、19 依赖 18、20 依赖 17/19）
- Task 23 依赖 15-22 全完
- Task 24-25 依赖 23 review 通过
- Task 26-27 依赖 25

**单 task 顺序执行推荐**——所有 task 走 TDD（先写失败测试 → 验证失败 → 实现 → 验证通过 → commit）。

---

## Common Test Setup（v2 修订）

测试新增/修改 `tests/test_chat_runtime.py` 时，**`ChatRuntimeTests` 真实结构**（[tests/test_chat_runtime.py:23-144](../../tests/test_chat_runtime.py:23)）：

- `class ChatRuntimeTests(unittest.TestCase)` 基类只 `setUp` patch `curl_cffi_requests`，**不**自动创建 handler
- 各测试方法显式调 `handler = self._make_handler_with_project()` 创建 handler + 项目；调用后 `self.project_id` / `self.project_dir` 才被设置
- 所有新测试类继承 `ChatRuntimeTests`，每个测试方法**首行**调用 `handler = self._make_handler_with_project()`
- `handler.skill_engine` 是真实 `SkillEngine` 实例（projects_dir 是 tmpdir）
- 没有 `_FakeOpenAIClient`——mock 上游 LLM 用 `mock.patch("backend.chat.OpenAI")`

**示例**：

```python
class FooTests(ChatRuntimeTests):
    def test_bar(self):
        handler = self._make_handler_with_project()  # 必须在首条**非 import** 代码之前调用
        # self.project_id / self.project_dir 现在可用
        # ... 测试逻辑

    def test_baz(self):
        # method 内 import 是合法的 Python 风格，可以在 handler= 之前
        from backend.chat import USER_VISIBLE_FALLBACK
        handler = self._make_handler_with_project()
        self.assertIsInstance(USER_VISIBLE_FALLBACK, str)
```

**关键不变量**：每个 `ChatRuntimeTests` 子类的 test method **必须**调用 `self._make_handler_with_project()` 至少一次，且**调用必须发生在任何引用 `handler.xxx` 或 `self.project_id` / `self.project_dir` 的代码之前**。method 内允许 `from ... import` 在 handler= 之前——`import` 不需要 handler。

新建测试文件（`test_draft_action.py` / `test_tool_log.py` / `test_draft_decision_compare_report.py`）独立 setUp，不依赖 `ChatRuntimeTests` 基类。

---

## Critical Real-Code References（v2 必读）

落地时高频踩坑的真实结构（codex round-1 plan review 列出）：

| 真实结构 | plan 引用方式 |
|---|---|
| `GET /api/projects/{id}/conversation` 返回 `{"messages": [...]}`（[backend/main.py:368-378](../../backend/main.py:368)） | 任何 sanitize 必须保留 `{"messages": ...}` 包装 |
| `main.py` 不存在 `chat_handler` 全局变量；用 `get_chat_handler(project_id)` 工厂（[backend/main.py:49-63](../../backend/main.py:49)） | 所有 `chat_handler.*` 错误用法改 `get_chat_handler(project_id)` |
| `append_report_draft` 工具 schema 只有 `content` 参数（[backend/chat.py:4187-4202](../../backend/chat.py:4187)）；执行入口 `_execute_append_report_draft(project_id, args.get("content", ""))`（[chat.py:4327](../../backend/chat.py:4327)） | 所有 mock tool_args 不能传 `file_path`；摘要里 `path` 从 `result["path"]` 取（[chat.py:4443-4445](../../backend/chat.py:4443)） |
| `stream_split_safe_tail()` 是模块级 helper + `_STAGE_ACK_MARKER` 常量（[chat.py:61-90, 70](../../backend/chat.py:61)） | tail guard 加 draft-action 必须改这个模块级 helper + 加新 marker 常量，**不是**改 `_chat_stream_unlocked` |
| `SystemNotice(...)` constructor 在 [chat.py:3687-3692, 6361-6366](../../backend/chat.py:3687) 用（不只是 `_emit_system_notice_once`） | A1 Task 1/2 必须把这两处也加 `surface_to_user` |
| `_classify_canonical_draft_turn` 内部 stage 解析是 inline 写的（[chat.py:1649-1657](../../backend/chat.py:1649)） | 没有 `_infer_stage_code` helper；preflight 实施时 inline stage 解析或抽 helper |
| `copyMessage(content)` 单参数（[ChatPanel.jsx:282-287](../../frontend/src/components/ChatPanel.jsx:282)）；按钮调用 `copyMessage(msg.content)`（[ChatPanel.jsx:728](../../frontend/src/components/ChatPanel.jsx:728)） | 不要改 `copyMessage` 签名；在 handler 内部 strip |
| 渲染入口是 `splitAssistantMessageBlocks(msg.content)`（[ChatPanel.jsx:691, 13](../../frontend/src/components/ChatPanel.jsx:691)） | tool-log strip 必须在 split 之前调，或在 split 内部调 |
| 现有 `_finalize_assistant_turn` 只处理 stage-ack（[chat.py:6372-6414](../../backend/chat.py:6372)）；3 个 caller 在 [chat.py:3417, 3675, 6349](../../backend/chat.py:3417) | Task 13 编排器扩展时三 caller 都要更新 |

---

### Task 1: A1 — `SystemNotice` 加 `surface_to_user` 必填字段

**Spec:** §1.1

**Files:**
- Modify: `backend/models.py` (around the `SystemNotice` class)
- Test: `tests/test_chat_runtime.py` (append new test class)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_chat_runtime.py`:

```python
class SystemNoticeFieldTests(unittest.TestCase):
    def test_surface_to_user_is_required_no_default(self):
        from backend.models import SystemNotice
        # 不传 surface_to_user 必须抛 ValidationError / TypeError
        with self.assertRaises(Exception):
            SystemNotice(category="test", reason="r", user_action="a")

    def test_surface_to_user_true_accepted(self):
        from backend.models import SystemNotice
        notice = SystemNotice(
            category="test", reason="r", user_action="a", surface_to_user=True,
        )
        self.assertTrue(notice.surface_to_user)

    def test_surface_to_user_false_accepted(self):
        from backend.models import SystemNotice
        notice = SystemNotice(
            category="test", reason="r", user_action="a", surface_to_user=False,
        )
        self.assertFalse(notice.surface_to_user)
```

- [ ] **Step 2: Run test to verify it fails**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::SystemNoticeFieldTests -v
```

Expected: 3 fails — `SystemNotice` 还没加字段，第一个测试现有调用方都不传所以反而通过；后两个 `unexpected keyword argument` 报错。

- [ ] **Step 3: Implement — 加字段**

Modify `backend/models.py` `SystemNotice` 类（pydantic BaseModel）：

```python
class SystemNotice(BaseModel):
    category: str
    path: str | None = None
    reason: str
    user_action: str
    surface_to_user: bool   # 必填，无默认值（强制 audit 全部 call site）
```

- [ ] **Step 4: Run test to verify pass**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::SystemNoticeFieldTests -v
```

Expected: 3 pass。

- [ ] **Step 5: Run full backend test suite to gauge breakage**

```powershell
.venv\Scripts\python -m pytest tests/ -v 2>&1 | Select-String -Pattern "FAIL|ERROR" | Select-Object -First 20
```

Expected: 一堆 fail——所有现有 `SystemNotice(...)` 调用 + `_emit_system_notice_once` 路径都缺字段。这正是预期，下个 task 一一补上。

- [ ] **Step 6: Commit Task 1（仅 model 改动）**

```powershell
git add backend/models.py tests/test_chat_runtime.py
git commit -m "feat(notice): add SystemNotice.surface_to_user required field (A1 prep)"
```

注：commit 后 backend test suite 仍会 fail 至 Task 2 完成——这是 spec 设计的中间态（必填字段强制下游 audit），可接受。Task 2 commit 完成后恢复绿。

---

### Task 2: A1 — `_emit_system_notice_once` dual dedupe + 18 call sites audit

**Spec:** §1.2 + §1.4 + Appendix C

**Files:**
- Modify: `backend/chat.py` (lines around 6455-6474 for the helper; 18 call sites at 4379, 4389, 4524, 4538, 4551, 4560, 4577, 4591, 4604, 4619, 4634, 4649, 4662, 4688, 6030, 6405, 6422, 6443; line 5926 for `_new_turn_context`)
- Test: `tests/test_chat_runtime.py` (append new test class)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_chat_runtime.py`:

```python
class SystemNoticeDualDedupeTests(ChatRuntimeTests):
    def test_user_and_internal_can_coexist_same_turn(self):
        handler = self._make_handler_with_project()
        # 先发 internal notice，再发 user notice，两条都要 in queue
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        handler._emit_system_notice_once(
            category="write_blocked", path=None,
            reason="internal hint", user_action="model fix",
            surface_to_user=False,
        )
        handler._emit_system_notice_once(
            category="non_plan_write_blocked", path=None,
            reason="user must confirm", user_action="please click",
            surface_to_user=True,
        )
        notices = handler._turn_context["pending_system_notices"]
        self.assertEqual(len(notices), 2)
        self.assertEqual(notices[0]["surface_to_user"], False)
        self.assertEqual(notices[1]["surface_to_user"], True)

    def test_internal_notice_does_not_block_user_notice(self):
        handler = self._make_handler_with_project()
        # round-2 codex finding 验证
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        handler._emit_system_notice_once(
            category="write_blocked", reason="r1", user_action="a1",
            surface_to_user=False,
        )
        handler._emit_system_notice_once(
            category="s0_write_blocked", reason="r2", user_action="a2",
            surface_to_user=True,
        )
        notices = handler._turn_context["pending_system_notices"]
        self.assertEqual(len(notices), 2)

    def test_user_notice_does_not_block_internal_notice(self):
        handler = self._make_handler_with_project()
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        handler._emit_system_notice_once(
            category="s0_write_blocked", reason="r", user_action="a",
            surface_to_user=True,
        )
        handler._emit_system_notice_once(
            category="write_blocked", reason="r2", user_action="a2",
            surface_to_user=False,
        )
        notices = handler._turn_context["pending_system_notices"]
        self.assertEqual(len(notices), 2)

    def test_same_class_internal_still_deduped(self):
        handler = self._make_handler_with_project()
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        for _ in range(3):
            handler._emit_system_notice_once(
                category="write_blocked", reason="r", user_action="a",
                surface_to_user=False,
            )
        notices = handler._turn_context["pending_system_notices"]
        self.assertEqual(len(notices), 1)

    def test_same_class_user_still_deduped(self):
        handler = self._make_handler_with_project()
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        for _ in range(3):
            handler._emit_system_notice_once(
                category="s0_write_blocked", reason="r", user_action="a",
                surface_to_user=True,
            )
        notices = handler._turn_context["pending_system_notices"]
        self.assertEqual(len(notices), 1)

    def test_surface_to_user_required_param(self):
        handler = self._make_handler_with_project()
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        with self.assertRaises(TypeError):
            handler._emit_system_notice_once(
                category="x", reason="r", user_action="a",
            )
```

- [ ] **Step 2: Run tests to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::SystemNoticeDualDedupeTests -v
```

Expected: 全 fail。

- [ ] **Step 3: Implement — 改 `_emit_system_notice_once` 双 dedupe**

Replace `backend/chat.py` `_emit_system_notice_once` 方法（约 6455-6474 行）：

```python
def _emit_system_notice_once(
    self,
    *,
    category: str,
    path: str | None = None,
    reason: str,
    user_action: str,
    surface_to_user: bool,  # 必填
) -> None:
    flag_key = "user_notice_emitted" if surface_to_user else "internal_notice_emitted"
    if self._turn_context.get(flag_key):
        return
    notice = {
        "type": "system_notice",
        "category": category,
        "path": path,
        "reason": reason,
        "user_action": user_action,
        "surface_to_user": surface_to_user,
    }
    self._turn_context[flag_key] = True
    queue = self._turn_context.setdefault("pending_system_notices", [])
    queue.append(notice)
```

`_new_turn_context`（约 5926 行）改字段：

```python
def _new_turn_context(self, *, can_write_non_plan: bool) -> Dict[str, object]:
    return {
        "can_write_non_plan": can_write_non_plan,
        "generic_non_plan_write_allowed": can_write_non_plan,
        "web_search_disabled": False,
        "web_search_performed": False,
        "fetch_url_performed": False,
        "web_search_count": 0,
        # v5: 拆 dedupe 双 flag
        "user_notice_emitted": False,
        "internal_notice_emitted": False,
        "pending_system_notices": [],
        "required_write_snapshots": {},
        "canonical_draft_decision": None,
        "draft_followup_flags": None,
        "read_file_paths": set(),
        "canonical_draft_mutation": None,
        "checkpoint_event": None,
        "pending_stage_keyword": None,
    }
```

- [ ] **Step 3.5: Fix `SystemNotice(...)` direct constructors（v2 新增）**

除了 18 个 `_emit_system_notice_once` 调用，**[chat.py:3687-3692, 6361-6366](../../backend/chat.py:3687) 还有两处直接构造 `SystemNotice(category=..., path=..., reason=..., user_action=...)`**——`pending_system_notices` 字段已经包含 `surface_to_user`（Task 2 改 dedupe 时写入），构造时透传即可：

```python
# 旧（约 3687-3692 / 6361-6366）
system_notices = [
    SystemNotice(
        category=notice["category"],
        path=notice.get("path"),
        reason=notice["reason"],
        user_action=notice["user_action"],
    )
    for notice in self._turn_context.get("pending_system_notices", [])
]

# 新
system_notices = [
    SystemNotice(
        category=notice["category"],
        path=notice.get("path"),
        reason=notice["reason"],
        user_action=notice["user_action"],
        surface_to_user=notice["surface_to_user"],  # v5 必填
    )
    for notice in self._turn_context.get("pending_system_notices", [])
]
```

- [ ] **Step 4: Implement — 18 call sites 全部加 `surface_to_user=...`**

按 spec Appendix C 表精确改 18 处。每处 `_emit_system_notice_once(...)` 调用增加最后一个参数：

| chat.py 行 | category | surface_to_user |
|---|---|---|
| 4379 | `write_blocked` | `False` |
| 4389 | `write_blocked` | `False` |
| 4524 | `report_draft_path_blocked` | `True` |
| 4538 | `s0_write_blocked` | `True` |
| 4551 | `non_plan_write_blocked` | `True` |
| 4560 | `fetch_url_gate_blocked` | `False` |
| 4577 | `report_draft_destructive_write_blocked` | `True` |
| 4591 | `report_draft_destructive_write_blocked` | `True` |
| 4604 | `write_blocked` | `False` |
| 4619 | `report_draft_destructive_write_blocked` | `True` |
| 4634 | `checkpoint_forge_blocked` | `True` |
| 4649 | `write_blocked` | `True` ← 注意特例（self_signature 校验失败要给用户看） |
| 4662 | `analysis_refs_missing` | `False` |
| 4688 | `data_log_format_hint` | `False` |
| 6030 | `checkpoint_prereq_missing` | `True` |
| 6405 | `stage_keyword_prereq_missing` | `True` |
| 6422 | `s0_tag_soft_gate` | `True` |
| 6443 | `stage_ack_prereq_missing` | `True` |

每处类似改造（以 4604 为例）：

```python
# 旧
self._emit_system_notice_once(
    category="write_blocked",
    path=normalized_path,
    reason=read_before_write_error,
    user_action="请先读取目标文件最新内容，再重新提交写入。",
)
# 新（追加最后一行）
self._emit_system_notice_once(
    category="write_blocked",
    path=normalized_path,
    reason=read_before_write_error,
    user_action="请先读取目标文件最新内容，再重新提交写入。",
    surface_to_user=False,
)
```

落地后跑 grep 验证：

```powershell
Select-String -Path backend\chat.py -Pattern "_emit_system_notice_once" | Measure-Object
# 应该看到 18 个调用 + 1 个 def，共 19 行（仅声明 + 调用）
```

- [ ] **Step 5: Run new tests to verify pass**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::SystemNoticeDualDedupeTests -v
```

Expected: 6 pass。

- [ ] **Step 6: Run full backend suite to verify Task 1 fallout fixed**

```powershell
.venv\Scripts\python -m pytest tests/ -v 2>&1 | Select-String -Pattern "FAILED|ERROR" | Select-Object -First 20
```

Expected: 几乎所有原有测试通过（之前因 `surface_to_user` 缺失导致的 fail 全消）；如有零星 mock fixture fail，对症修。

- [ ] **Step 7: Commit Task 2**

```powershell
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "feat(notice): dual dedupe + audit 18 sites + constructor pass-through (A1)"
```

---

### Task 3: A1 — 服务端 + 前端 `surface_to_user` 过滤

**Spec:** §1.3

**Files:**
- Modify: `backend/main.py` (around line 258-262 for non-stream response; SSE notice yielder lookup)
- Modify: `backend/chat.py` (`_chat_stream_unlocked` / `_chat_unlocked` notice yield 路径)
- Modify: `frontend/src/components/ChatPanel.jsx` (around line 518-534 for SSE notice handler; line 676 for render)
- Test: `tests/test_chat_runtime.py`、`frontend/tests/chatPresentation.test.mjs`

- [ ] **Step 1: Write backend test**

Append to `tests/test_chat_runtime.py`:

```python
class SystemNoticeServerSideFilterTests(ChatRuntimeTests):
    def test_internal_notice_not_in_sse_yield(self):
        """_chat_stream_unlocked 在准备 yield 前应过滤 surface_to_user=False 的 notice。
        实施时把 yield notice 的代码集中到 helper（如下面 Step 3 的 _yield_user_visible_notices），
        测试调该 helper 验证过滤。"""
        handler = self._make_handler_with_project()
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        handler._turn_context["pending_system_notices"] = [
            {"type": "system_notice", "category": "x_user", "reason": "r1",
             "user_action": "a1", "surface_to_user": True, "path": None},
            {"type": "system_notice", "category": "x_internal", "reason": "r2",
             "user_action": "a2", "surface_to_user": False, "path": None},
        ]
        yielded = list(handler._yield_user_visible_notices())
        self.assertEqual(len(yielded), 1)
        self.assertEqual(yielded[0]["category"], "x_user")

    def test_internal_notice_logged_when_filtered(self):
        """隐藏 notice 即使不 yield，也要写后端日志（便于调试）。"""
        import logging
        handler = self._make_handler_with_project()
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        handler._turn_context["pending_system_notices"] = [
            {"type": "system_notice", "category": "x_internal", "reason": "internal_r",
             "user_action": "a", "surface_to_user": False, "path": None},
        ]
        with self.assertLogs("backend.chat", level="INFO") as caplog:
            list(handler._yield_user_visible_notices())
        self.assertTrue(any("internal-notice" in m for m in caplog.output))

    def test_non_stream_response_filters_internal_notices(self):
        """SystemNotice list 进 ChatResponse 前过滤。"""
        handler = self._make_handler_with_project()
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        handler._turn_context["pending_system_notices"] = [
            {"type": "system_notice", "category": "x_user", "reason": "r1",
             "user_action": "a1", "surface_to_user": True, "path": None},
            {"type": "system_notice", "category": "x_internal", "reason": "r2",
             "user_action": "a2", "surface_to_user": False, "path": None},
        ]
        # 直接调试用 helper 提取 user-visible SystemNotice list
        notices = handler._collect_user_visible_system_notices()
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].category, "x_user")
```

- [ ] **Step 2: Run to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::SystemNoticeServerSideFilterTests -v
```

Expected: fail（`_flush_pending_system_notices` 还没区分）。

- [ ] **Step 3: Implement — backend filter**

加两个 helper 到 `backend/chat.py` `ChatHandler` 类（替代散在 `_chat_unlocked` / `_chat_stream_unlocked` 里的 yield 散点）：

```python
def _yield_user_visible_notices(self):
    """Generator: 仅 yield surface_to_user=True 的 notice；隐藏 notice 写日志。"""
    for notice in self._turn_context.get("pending_system_notices", []):
        if not notice.get("surface_to_user"):
            logging.info(
                "[internal-notice] %s | reason=%s",
                notice.get("category"), notice.get("reason"),
            )
            continue
        yield notice

def _collect_user_visible_system_notices(self):
    """非流式路径：返回 SystemNotice 列表（已过滤）。"""
    return [
        SystemNotice(
            category=n["category"], path=n.get("path"),
            reason=n["reason"], user_action=n["user_action"],
            surface_to_user=True,
        )
        for n in self._turn_context.get("pending_system_notices", [])
        if n.get("surface_to_user")
    ]
```

替换 `_chat_unlocked` / `_chat_stream_unlocked` / `_finalize_early_assistant_message` 中现有的 `for notice in ... yield {"type": "system_notice", ...}` 循环为：

```python
for notice in self._yield_user_visible_notices():
    yield {
        "type": "system_notice",
        "category": notice["category"],
        "path": notice.get("path"),
        "reason": notice["reason"],
        "user_action": notice["user_action"],
        # surface_to_user 不需透传——前端能 yield 到的都是 True
    }
```

替换两处 `SystemNotice(...)` 直接构造（[chat.py:3687-3692, 6361-6366](../../backend/chat.py:3687)）：

```python
system_notices = self._collect_user_visible_system_notices()
```

`backend/main.py` 非流式 chat 响应入口已经 yield 同样过滤后的 SystemNotice，无需额外改动。

- [ ] **Step 4: Write frontend test**

Append to `frontend/tests/chatPresentation.test.mjs`（新建 if absent）：

```javascript
import { test } from 'node:test'
import assert from 'node:assert/strict'

test('system_notice with surface_to_user=false is not in render list', () => {
  // 模拟 messages 数组同时含 surface=true 和 surface=false 两条 system_notice
  // ChatPanel 的 message filter 应只渲染 surface=true 的
  const messages = [
    { id: '1', role: 'system_notice', surface_to_user: true, reason: 'show' },
    { id: '2', role: 'system_notice', surface_to_user: false, reason: 'hide' },
  ]
  const visible = messages.filter(m => m.role !== 'system_notice' || m.surface_to_user !== false)
  assert.equal(visible.length, 1)
  assert.equal(visible[0].id, '1')
})
```

- [ ] **Step 5: Implement frontend filter**

**前提**：SSE handler 现在不发 surface_to_user=False 的 notice（已在 backend filter）；前端无需再判断。但**冗余的兜底过滤仍要加**——历史 conversation 可能有遗留 `system_notice` 消息或调试场景下 backend 关闭过滤时依然安全。

`frontend/src/components/ChatPanel.jsx` 第 676 附近渲染 `system_notice` 的 if 分支前加过滤：

```jsx
if (msg.role === 'system_notice') {
  if (msg.surface_to_user === false) return null  // 前端兜底（backend 已过滤）
  return (
    <div key={msg.id} className="...">
      ...
    </div>
  )
}
```

同时 SSE handler 注入 `system_notice` 消息进 messages array 时**透传 `surface_to_user` 字段**（[ChatPanel.jsx:518-534](../../frontend/src/components/ChatPanel.jsx:518) 附近）：

```jsx
// SSE 'system_notice' 事件 handler（约 524-533）
if (event === 'system_notice') {
  const data = JSON.parse(payload)
  setMessages(prev => [...prev, {
    id: ...,
    role: 'system_notice',
    category: data.category,
    reason: data.reason,
    user_action: data.user_action,
    surface_to_user: data.surface_to_user !== false,  // 默认 True 兜底
  }])
}
```

- [ ] **Step 6: Run all tests**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::SystemNoticeServerSideFilterTests -v
cd frontend; node --test tests/chatPresentation.test.mjs; cd ..
```

Expected: pass。

- [ ] **Step 7: Commit**

```powershell
git add backend/main.py backend/chat.py frontend/src/components/ChatPanel.jsx frontend/tests/chatPresentation.test.mjs tests/test_chat_runtime.py
git commit -m "feat(notice): server + frontend filter surface_to_user=false (A1)"
```

---

### Task 4: A2 — `_render_progress_markdown` 渲染 quality_progress

**Spec:** §2.1

**Files:**
- Modify: `backend/skill.py` (lines 1240-1263 `_render_progress_markdown`; line 1104 `_sync_stage_tracking_files` 调用点)
- Test: `tests/test_skill_engine.py` (append new test class)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_skill_engine.py`:

```python
class ProgressMarkdownQualityProgressTests(unittest.TestCase):
    def _engine(self, tmp):
        from pathlib import Path
        return SkillEngine(Path(tmp) / "p", Path(tmp) / "s")

    def test_s2_renders_quality_progress_when_target_gt_zero(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp)
            md = engine._render_progress_markdown(
                stage_code="S2", status="进行中",
                next_actions=["sample"], completed_items=[],
                stage_state={
                    "stage_code": "S2",
                    "quality_progress": {
                        "label": "条 有效来源", "current": 5, "target": 7,
                    },
                },
            )
            self.assertIn("**质量进度**: 5/7 条 有效来源", md)

    def test_s3_renders_analysis_ref_count(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp)
            md = engine._render_progress_markdown(
                stage_code="S3", status="进行中",
                next_actions=[], completed_items=[],
                stage_state={
                    "stage_code": "S3",
                    "quality_progress": {
                        "label": "项 分析引用", "current": 3, "target": 4,
                    },
                },
            )
            self.assertIn("**质量进度**: 3/4 项 分析引用", md)

    def test_s0_does_not_render_quality_progress(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp)
            md = engine._render_progress_markdown(
                stage_code="S0", status="进行中",
                next_actions=[], completed_items=[],
                stage_state={"stage_code": "S0", "quality_progress": None},
            )
            self.assertNotIn("**质量进度**", md)

    def test_s4_does_not_render_quality_progress(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp)
            md = engine._render_progress_markdown(
                stage_code="S4", status="进行中",
                next_actions=[], completed_items=[],
                stage_state={"stage_code": "S4", "quality_progress": None},
            )
            self.assertNotIn("**质量进度**", md)

    def test_target_zero_suppresses_render(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp)
            md = engine._render_progress_markdown(
                stage_code="S2", status="进行中",
                next_actions=[], completed_items=[],
                stage_state={
                    "stage_code": "S2",
                    "quality_progress": {
                        "label": "条 有效来源", "current": 0, "target": 0,
                    },
                },
            )
            self.assertNotIn("**质量进度**", md)

    def test_stage_state_none_falls_back_to_old_behavior(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp)
            md = engine._render_progress_markdown(
                stage_code="S2", status="进行中",
                next_actions=[], completed_items=[],
                stage_state=None,
            )
            self.assertNotIn("**质量进度**", md)
            self.assertIn("**阶段**: S2", md)

    def test_quality_progress_field_absent_no_render(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp)
            md = engine._render_progress_markdown(
                stage_code="S2", status="进行中",
                next_actions=[], completed_items=[],
                stage_state={"stage_code": "S2"},  # 无 quality_progress key
            )
            self.assertNotIn("**质量进度**", md)
```

- [ ] **Step 2: Run to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_skill_engine.py::ProgressMarkdownQualityProgressTests -v
```

Expected: TypeError fail（签名还没 stage_state 参数）+ 渲染断言 fail。

- [ ] **Step 3: Implement — 改签名 + 加渲染**

Modify `backend/skill.py` `_render_progress_markdown`（约 1240-1263 行）：

```python
def _render_progress_markdown(
    self,
    stage_code: str,
    status: str,
    next_actions: list[str],
    completed_items: list[str],
    *,
    stage_state: dict | None = None,
) -> str:
    current_task = next_actions[0] if next_actions else "当前阶段任务已完成，等待推进下一阶段。"
    completed_summary = " / ".join(completed_items[-3:]) if completed_items else "-"
    next_summary = " / ".join(next_actions[:3]) if next_actions else "-"
    lines = [
        "# 项目进度追踪",
        "",
        "## 当前状态",
        f"**阶段**: {stage_code}",
        f"**状态**: {status}",
        f"**当前任务**: {current_task}",
    ]
    # v5: S2/S3 阶段渲染 quality_progress 行
    if stage_state and stage_code in {"S2", "S3"}:
        qp = stage_state.get("quality_progress")
        if qp and isinstance(qp.get("target"), int) and qp["target"] > 0:
            label = qp.get("label", "")
            current = qp.get("current", 0)
            target = qp["target"]
            lines.append(f"**质量进度**: {current}/{target} {label}")
    lines.extend([
        f"**更新日期**: {datetime.now().strftime('%Y-%m-%d')}",
        "",
        "## 执行摘要",
        f"- 已完成: {completed_summary}",
        f"- 下一步: {next_summary}",
    ])
    return "\n".join(lines).strip() + "\n"
```

`_sync_stage_tracking_files`（约 1104 行）调用点改：

```python
progress_text = self._render_progress_markdown(
    stage_code, status, next_actions, completed_items,
    stage_state=stage_state,
)
```

- [ ] **Step 4: Run tests to verify pass**

```powershell
.venv\Scripts\python -m pytest tests/test_skill_engine.py::ProgressMarkdownQualityProgressTests -v
```

Expected: 7 pass。

- [ ] **Step 5: Run full skill engine suite**

```powershell
.venv\Scripts\python -m pytest tests/test_skill_engine.py -v
```

Expected: all pass（兼容缺省）。

- [ ] **Step 6: Commit**

```powershell
git add backend/skill.py tests/test_skill_engine.py
git commit -m "feat(progress): render quality_progress for S2/S3 (A2)"
```

---

### Task 5: A2 — `_persist_successful_tool_result` 追加 `quality_hint`

**Spec:** §2.2

**Files:**
- Modify: `backend/chat.py` (`_persist_successful_tool_result` around line 1092-1153 OR a new wrapper that runs after it)
- Test: `tests/test_chat_runtime.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_chat_runtime.py`:

```python
class ToolResultQualityHintTests(ChatRuntimeTests):
    def _seed_data_log(self, project_dir, n_entries):
        """Helper: 在 project_dir 下创建 plan/data-log.md 含 n 条 [DL-...] 条目"""
        lines = ["# Data log\n"]
        for i in range(n_entries):
            lines.extend([
                f"\n### [DL-2026-{i+1:02d}] entry {i+1}",
                f"- **来源**: source-{i+1}",
                f"- **时间**: 2026-05-04",
                f"- **URL**: https://example.com/{i+1}",
                f"- **用途**: test",
                "",
            ])
        (project_dir / "plan" / "data-log.md").write_text("\n".join(lines), encoding="utf-8")

    def _seed_outline_for_data_log_min_7(self, project_dir):
        """触发 data_log_min=7（5000 字 → ceil(5000/1000*1.3)=7）"""
        # _resolve_length_targets 从 project-overview.md 读 expected_length
        # _make_handler_with_project 默认 expected_length="3000 words"，需要覆写
        overview = project_dir / "plan" / "project-overview.md"
        text = overview.read_text(encoding="utf-8")
        text = text.replace("3000 words", "5000 字").replace("3000", "5000")
        overview.write_text(text, encoding="utf-8")

    def test_write_data_log_appends_quality_hint_when_s2(self):
        handler = self._make_handler_with_project()
        self._seed_outline_for_data_log_min_7(self.project_dir)
        self._seed_data_log(self.project_dir, 5)
        # mock stage_state 返回 S2（绕过完整 stage 推断）
        with mock.patch.object(handler.skill_engine, "_infer_stage_state", return_value={
            "stage_code": "S2",
            "quality_progress": {"label": "条 有效来源", "current": 5, "target": 7},
        }):
            result = {"status": "success", "path": "plan/data-log.md"}
            handler._maybe_attach_quality_hint(
                self.project_id,
                tool_name="write_file",
                tool_args={"file_path": "plan/data-log.md"},
                result=result,
            )
        self.assertIn("quality_hint", result)
        self.assertIn("5/7", result["quality_hint"])
        self.assertIn("有效来源", result["quality_hint"])

    def test_write_other_plan_file_no_quality_hint(self):
        handler = self._make_handler_with_project()
        result = {"status": "success", "path": "plan/notes.md"}
        handler._maybe_attach_quality_hint(
            self.project_id,
            tool_name="write_file",
            tool_args={"file_path": "plan/notes.md"},
            result=result,
        )
        self.assertNotIn("quality_hint", result)

    def test_write_content_draft_no_quality_hint(self):
        # append_report_draft 真实 schema 没有 file_path，但 _maybe_attach_quality_hint
        # 接收 tool_args dict 形式，写 content/* 的合法工具是 edit_file/write_file（虽然被禁止）
        # 这个测试验证：即使 file_path 指向 content/ 也不附加（因为 path 不在 QUALITY_HINT_TARGET_FILES）
        handler = self._make_handler_with_project()
        result = {"status": "success"}
        handler._maybe_attach_quality_hint(
            self.project_id,
            tool_name="edit_file",
            tool_args={"file_path": "content/report_draft_v1.md"},
            result=result,
        )
        self.assertNotIn("quality_hint", result)

    def test_quality_hint_absent_when_target_zero(self):
        handler = self._make_handler_with_project()
        with mock.patch.object(handler.skill_engine, "_infer_stage_state", return_value={
            "stage_code": "S2",
            "quality_progress": {"label": "条", "current": 0, "target": 0},
        }):
            result = {"status": "success"}
            handler._maybe_attach_quality_hint(
                self.project_id, tool_name="write_file",
                tool_args={"file_path": "plan/data-log.md"}, result=result,
            )
        self.assertNotIn("quality_hint", result)

    def test_quality_hint_absent_when_stage_not_s2_s3(self):
        handler = self._make_handler_with_project()
        with mock.patch.object(handler.skill_engine, "_infer_stage_state", return_value={
            "stage_code": "S4",
            "quality_progress": None,
        }):
            result = {"status": "success"}
            handler._maybe_attach_quality_hint(
                self.project_id, tool_name="write_file",
                tool_args={"file_path": "plan/data-log.md"}, result=result,
            )
        self.assertNotIn("quality_hint", result)

    def test_edit_data_log_also_appends_quality_hint(self):
        handler = self._make_handler_with_project()
        self._seed_outline_for_data_log_min_7(self.project_dir)
        self._seed_data_log(self.project_dir, 5)
        with mock.patch.object(handler.skill_engine, "_infer_stage_state", return_value={
            "stage_code": "S2",
            "quality_progress": {"label": "条 有效来源", "current": 5, "target": 7},
        }):
            result = {"status": "success"}
            handler._maybe_attach_quality_hint(
                self.project_id, tool_name="edit_file",
                tool_args={"file_path": "plan/data-log.md"}, result=result,
            )
        self.assertIn("quality_hint", result)

    def test_write_analysis_notes_appends_when_s3(self):
        handler = self._make_handler_with_project()
        with mock.patch.object(handler.skill_engine, "_infer_stage_state", return_value={
            "stage_code": "S3",
            "quality_progress": {"label": "项 分析引用", "current": 3, "target": 4},
        }):
            result = {"status": "success"}
            handler._maybe_attach_quality_hint(
                self.project_id, tool_name="write_file",
                tool_args={"file_path": "plan/analysis-notes.md"}, result=result,
            )
        self.assertIn("quality_hint", result)
        self.assertIn("3/4", result["quality_hint"])
```

- [ ] **Step 2: Run to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::ToolResultQualityHintTests -v
```

Expected: AttributeError（`_maybe_attach_quality_hint` 不存在）。

- [ ] **Step 3: Implement helper + integrate into write/edit_file 路径**

Add to `backend/chat.py` (somewhere near `_persist_successful_tool_result`):

```python
QUALITY_HINT_TARGET_FILES = frozenset({"plan/data-log.md", "plan/analysis-notes.md"})
QUALITY_HINT_STAGES = frozenset({"S2", "S3"})

def _maybe_attach_quality_hint(
    self,
    project_id: str,
    *,
    tool_name: str,
    tool_args: dict,
    result: dict,
) -> None:
    if tool_name not in {"write_file", "edit_file"}:
        return
    if result.get("status") != "success":
        return
    file_path = tool_args.get("file_path") or ""
    normalized = file_path.replace("\\", "/").lstrip("./")
    if normalized not in self.QUALITY_HINT_TARGET_FILES:
        return
    project_path = self.skill_engine.get_project_path(project_id)
    if project_path is None:
        return
    stage_state = self.skill_engine._infer_stage_state(project_path)
    stage_code = stage_state.get("stage_code")
    if stage_code not in self.QUALITY_HINT_STAGES:
        return
    qp = stage_state.get("quality_progress")
    if not qp or not isinstance(qp.get("target"), int) or qp["target"] <= 0:
        return
    label = qp.get("label", "")
    current = qp.get("current", 0)
    target = qp["target"]
    if current >= target:
        result["quality_hint"] = f"已达标：{current}/{target} {label}"
    else:
        delta = target - current
        next_stage = "S3" if stage_code == "S2" else "S4"
        result["quality_hint"] = (
            f"当前 {current}/{target} {label}，还差 {delta} 条满足 {stage_code} 进 {next_stage} 门槛"
        )
```

集成到现有写工具路径——在 `write_file` / `edit_file` 成功路径上调用，紧跟 `_persist_successful_tool_result`：

```python
# 在 write_file / edit_file 成功 return 之前
self._persist_successful_tool_result(project_id, func_name, args, result, extra)
self._maybe_attach_quality_hint(
    project_id, tool_name=func_name, tool_args=args, result=result,
)
return result
```

- [ ] **Step 4: Run tests to verify pass**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::ToolResultQualityHintTests -v
```

Expected: 7 pass。

- [ ] **Step 5: Commit**

```powershell
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "feat(progress): attach quality_hint to tool_result for S2/S3 (A2)"
```

---

### Task 6: A3 — `_finalize_empty_assistant_turn` helper + 三处持久化改造

**Spec:** §3.1, §3.4

**Files:**
- Modify: `backend/chat.py` (3 sites: ~3423-3442 streaming, ~3681-3700 non-streaming, ~6342-6369 early finalize)
- Test: `tests/test_chat_runtime.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_chat_runtime.py`:

```python
class EmptyAssistantFallbackTests(ChatRuntimeTests):
    def test_finalize_empty_assistant_does_not_persist_assistant(self):
        handler = self._make_handler_with_project()
        history = []
        current_user = {"role": "user", "content": "test", "attached_material_ids": []}
        fallback = handler._finalize_empty_assistant_turn(
            self.project_id, history, current_user,
            diagnostic="stream_truncated",
        )
        # history 末尾应只有 user，不含空 assistant
        self.assertEqual(len(history), 1)
        self.assertEqual(history[-1]["role"], "user")

    def test_finalize_empty_assistant_returns_user_visible_fallback(self):
        handler = self._make_handler_with_project()
        history = []
        current_user = {"role": "user", "content": "test", "attached_material_ids": []}
        fallback = handler._finalize_empty_assistant_turn(
            self.project_id, history, current_user,
            diagnostic="stream_truncated",
        )
        self.assertIn("没有产出可见回复", fallback)
        self.assertIn("换个说法再发", fallback)

    def test_finalize_empty_assistant_records_event(self):
        handler = self._make_handler_with_project()
        from backend.chat import USER_VISIBLE_FALLBACK
        history = []
        current_user = {"role": "user", "content": "test", "attached_material_ids": []}
        handler._finalize_empty_assistant_turn(
            self.project_id, history, current_user,
            diagnostic="tool_only_no_text",
        )
        # 检查 conversation_state.json 的 events 数组里有一条 empty_assistant
        state = handler._load_conversation_state(self.project_id, history)
        events = state.get("events", [])
        empty_events = [e for e in events if e.get("type") == "empty_assistant"]
        self.assertGreaterEqual(len(empty_events), 1)
        self.assertEqual(empty_events[-1]["diagnostic"], "tool_only_no_text")

    def test_user_visible_fallback_constant_exists(self):
        from backend.chat import USER_VISIBLE_FALLBACK
        self.assertIsInstance(USER_VISIBLE_FALLBACK, str)
        self.assertIn("没有产出可见回复", USER_VISIBLE_FALLBACK)
```

- [ ] **Step 2: Run to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::EmptyAssistantFallbackTests -v
```

Expected: ImportError + AttributeError。

- [ ] **Step 3: Implement helpers**

Add to `backend/chat.py` 顶部常量区（约 60 行附近）：

```python
USER_VISIBLE_FALLBACK = (
    "（这一轮我没有产出可见回复，可能是处理过程中断了。"
    "请把刚才的需求换个说法再发一次。）"
)

LEGACY_EMPTY_ASSISTANT_FALLBACKS = frozenset({
    "（本轮无回复）",
    USER_VISIBLE_FALLBACK,
})
```

Add helper methods to `ChatHandler` class:

```python
def _record_empty_assistant_event(self, project_id: str, diagnostic: str) -> None:
    timestamp = datetime.now().isoformat(timespec="seconds")
    def mutate(state: Dict):
        state.setdefault("events", []).append({
            "type": "empty_assistant",
            "diagnostic": diagnostic,
            "recorded_at": timestamp,
        })
        return state
    self._mutate_conversation_state(project_id, mutate)

def _finalize_empty_assistant_turn(
    self,
    project_id: str,
    history: List[Dict],
    current_user_message: Dict,
    *,
    diagnostic: str = "stream_truncated",
) -> str:
    """统一空 assistant 兜底处理。
    1. 不持久化空 assistant（避免污染下轮 prompt）
    2. user message 持久化（否则下轮少一条）
    3. conversation_state 记录 empty_assistant 事件
    Returns: USER_VISIBLE_FALLBACK 字符串（caller yield 给前端）
    """
    history.append(current_user_message)
    self._save_conversation(project_id, history)
    self._record_empty_assistant_event(project_id, diagnostic)
    return USER_VISIBLE_FALLBACK
```

注：`_mutate_conversation_state` 是现有 helper（搜 `_save_conversation_state` 找类似方法），用于原子改 state json。如不存在，参照 `_save_conversation` 实现一个简化版。

- [ ] **Step 4: Refactor 3 persistence sites（v2 按场景分别给代码）**

三处兜底语义不同（generator vs 普通函数 vs early finalize 已经在 generator 内）。**分别**改造：

**Site A — `_chat_stream_unlocked`（generator，约 3423-3442）**：

```python
# 旧
if not assistant_message.strip():
    assistant_message = "（本轮无回复）"
# 后续 history.extend / save_conversation / yield content / yield usage

# 新
if not assistant_message.strip():
    fallback_text = self._finalize_empty_assistant_turn(
        project_id, history, current_user_message,
        diagnostic="stream_truncated",
    )
    yield {"type": "content", "data": fallback_text}
    yield {"type": "usage", "data": token_usage}
    return
```

**Site B — `_chat_unlocked`（普通函数，约 3681-3700）**：

普通函数没有 `yield`——直接 return dict：

```python
# 旧
if not assistant_message.strip():
    assistant_message = "（本轮无回复）"
# history.extend / save / return {"content": assistant_message, "token_usage": ...}

# 新
if not assistant_message.strip():
    fallback_text = self._finalize_empty_assistant_turn(
        project_id, history, current_user_message,
        diagnostic="non_stream_empty",
    )
    return {"content": fallback_text, "token_usage": token_usage}
```

**Site C — `_finalize_early_assistant_message`（普通函数，约 6342-6369）**：

```python
# 旧
if not assistant_message.strip():
    assistant_message = "（本轮无回复）"
# history.extend / save / return (assistant_message, token_usage, system_notices)

# 新
if not assistant_message.strip():
    fallback_text = self._finalize_empty_assistant_turn(
        project_id, history, current_user_message,
        diagnostic="early_finalize_empty",
    )
    # 调用方依然要 token_usage / system_notices
    token_usage = self._finalize_post_turn_compaction(project_id, history, None)
    system_notices = self._collect_user_visible_system_notices()
    return fallback_text, token_usage, system_notices
```

- [ ] **Step 5: Run tests to verify pass**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::EmptyAssistantFallbackTests -v
```

Expected: 4 pass。

- [ ] **Step 6: Run full backend suite to catch regressions**

```powershell
.venv\Scripts\python -m pytest tests/ -v 2>&1 | Select-String -Pattern "FAIL"
```

Expected: 现有"本轮无回复"相关测试可能 fail——更新这些测试期望为新 USER_VISIBLE_FALLBACK 文案。

- [ ] **Step 7: Commit**

```powershell
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "feat(empty-turn): unify fallback into helper, do not persist (A3)"
```

---

### Task 7: A3 — `_coalesce_consecutive_user_messages` 合并相邻 user

**Spec:** §3.2

**Files:**
- Modify: `backend/chat.py` (new helper + invoke from `_build_provider_turn_conversation` ~3777-3801)
- Test: `tests/test_chat_runtime.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_chat_runtime.py`:

```python
class CoalesceConsecutiveUserTests(ChatRuntimeTests):
    def test_two_str_user_messages_merged(self):
        handler = self._make_handler_with_project()
        conv = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "first"},
            {"role": "user", "content": "second"},
        ]
        result = handler._coalesce_consecutive_user_messages(conv)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[1]["role"], "user")
        self.assertEqual(result[1]["content"], "first\n\nsecond")

    def test_str_plus_multipart_merged_to_array(self):
        handler = self._make_handler_with_project()
        conv = [
            {"role": "user", "content": "text"},
            {"role": "user", "content": [
                {"type": "text", "text": "hi"},
                {"type": "image_url", "image_url": {"url": "data:..."}},
            ]},
        ]
        result = handler._coalesce_consecutive_user_messages(conv)
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0]["content"], list)
        # 顺序：先 text(原 str)、再 multipart 的 text、再 image
        self.assertEqual(result[0]["content"][0], {"type": "text", "text": "text"})
        self.assertEqual(result[0]["content"][1], {"type": "text", "text": "hi"})

    def test_two_multipart_arrays_merged(self):
        handler = self._make_handler_with_project()
        conv = [
            {"role": "user", "content": [
                {"type": "text", "text": "a"},
            ]},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:..."}},
            ]},
        ]
        result = handler._coalesce_consecutive_user_messages(conv)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]["content"]), 2)

    def test_does_not_modify_original_history(self):
        handler = self._make_handler_with_project()
        original_msg = {"role": "user", "content": "first"}
        conv = [original_msg, {"role": "user", "content": "second"}]
        handler._coalesce_consecutive_user_messages(conv)
        # 原 message 不应被改
        self.assertEqual(original_msg["content"], "first")

    def test_alternating_user_assistant_no_merge(self):
        handler = self._make_handler_with_project()
        conv = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
        ]
        result = handler._coalesce_consecutive_user_messages(conv)
        self.assertEqual(len(result), 3)

    def test_none_content_normalized_to_empty_string(self):
        handler = self._make_handler_with_project()
        conv = [
            {"role": "user", "content": None},
            {"role": "user", "content": "after"},
        ]
        result = handler._coalesce_consecutive_user_messages(conv)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["content"], "after")  # "" + "\n\n" + "after" → "after"

    def test_invoked_in_build_provider_turn_conversation(self):
        handler = self._make_handler_with_project()
        # 端到端：history 含连续两条 user role，build 后 user 已合并
        history = [
            {"role": "user", "content": "first", "attached_material_ids": []},
            {"role": "user", "content": "second", "attached_material_ids": []},
        ]
        current = {"role": "user", "content": "current", "attached_material_ids": []}
        conv, _ = handler._build_provider_turn_conversation(
            self.project_id, history, current,
        )
        # 前面 history 两条 user + current user → 三条 user 合并成一条
        user_msgs = [m for m in conv if m.get("role") == "user"]
        self.assertEqual(len(user_msgs), 1)
```

- [ ] **Step 2: Run to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::CoalesceConsecutiveUserTests -v
```

Expected: AttributeError。

- [ ] **Step 3: Implement helper**

Add to `backend/chat.py`:

```python
def _coalesce_consecutive_user_messages(self, conversation: List[Dict]) -> List[Dict]:
    """合并相邻的 user role 消息，防 Gemini 角色交替 400。"""
    def _normalize_content(c) -> str | list:
        if c is None:
            return ""
        if isinstance(c, str) or isinstance(c, list):
            return c
        return str(c)

    coalesced: List[Dict] = []
    for msg in conversation:
        if (
            coalesced
            and msg.get("role") == "user"
            and coalesced[-1].get("role") == "user"
        ):
            prev = coalesced[-1]
            prev_content = _normalize_content(prev.get("content"))
            new_content = _normalize_content(msg.get("content"))
            if isinstance(prev_content, str) and isinstance(new_content, str):
                joined = prev_content + "\n\n" + new_content
                prev["content"] = joined.strip("\n") if (prev_content or new_content) else ""
            else:
                prev_parts = prev_content if isinstance(prev_content, list) else (
                    [{"type": "text", "text": prev_content}] if prev_content else []
                )
                new_parts = new_content if isinstance(new_content, list) else (
                    [{"type": "text", "text": new_content}] if new_content else []
                )
                prev["content"] = prev_parts + new_parts
        else:
            coalesced.append(dict(msg))
    return coalesced
```

集成到 `_build_provider_turn_conversation`（约 3777-3801）。**关键：合并后必须重新计算 `current_turn_start_index`**，否则下游 `_fit_conversation_to_budget`（[chat.py:3123-3135, 3518-3530](../../backend/chat.py:3123)）会用过期 index 切错位置。

```python
def _build_provider_turn_conversation(self, ...):
    # ... 已有逻辑 ...
    conversation.extend(history_messages)
    current_user_provider_msg = self._to_provider_message(...)
    conversation.append(current_user_provider_msg)
    current_turn_start_index = len(conversation) - 1  # 旧 index（指 current user）
    if current_turn_messages:
        conversation.extend(current_turn_messages)
    
    # v5: 合并相邻 user role（必须在 budget fit 之前）
    coalesced = self._coalesce_consecutive_user_messages(conversation)
    
    # v2 修订：合并可能改变 current_turn_start_index——重新定位
    # current user message 现在被合并到了"最后一个 user role 块"里
    # 找到新数组里最后一个 user role 的位置（current turn 起点）
    new_current_turn_start_index = next(
        (i for i in range(len(coalesced) - 1, -1, -1) if coalesced[i].get("role") == "user"),
        len(coalesced),
    )
    return coalesced, new_current_turn_start_index
```

测试补充：

```python
def test_coalesce_recomputes_current_turn_start_index(self):
    """history 末尾是 user，current 也是 user → 合并后 index 必须指向新合并的 user 位置"""
    handler = self._make_handler_with_project()
    history = [
        {"role": "user", "content": "previous", "attached_material_ids": []},
    ]
    current = {"role": "user", "content": "current", "attached_material_ids": []}
    conv, idx = handler._build_provider_turn_conversation(
        self.project_id, history, current,
    )
    # 合并后只有 system + 1 个 user → idx 应指向那个合并 user
    user_msgs = [m for m in conv if m.get("role") == "user"]
    self.assertEqual(len(user_msgs), 1)
    self.assertEqual(conv[idx].get("role"), "user")
    self.assertIn("current", conv[idx]["content"])
```

- [ ] **Step 4: Run tests to verify pass**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::CoalesceConsecutiveUserTests -v
```

Expected: 7 pass。

- [ ] **Step 5: Commit**

```powershell
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "feat(provider-build): coalesce consecutive user messages (A3)"
```

---

### Task 8: A3 — 三层 sanitize 历史污染

**Spec:** §3.3

**Files:**
- Modify: `backend/main.py` (~368-377 GET /conversation handler)
- Modify: `backend/chat.py` (`_to_provider_message` ~4072-4097)
- Modify: `frontend/src/utils/chatPresentation.js`（新建 if absent）+ `frontend/src/components/ChatPanel.jsx` 加载入口
- Test: `tests/test_chat_runtime.py`、`tests/test_main_api.py`、`frontend/tests/chatPresentation.test.mjs`

- [ ] **Step 1: Write backend tests**

Append to `tests/test_chat_runtime.py`:

```python
class HistorySanitizeTests(ChatRuntimeTests):
    def test_legacy_fallback_skipped_in_provider_message(self):
        handler = self._make_handler_with_project()
        msg = {"role": "assistant", "content": "（本轮无回复）"}
        result = handler._to_provider_message(self.project_id, msg, include_images=False)
        self.assertIsNone(result)

    def test_user_visible_fallback_skipped_in_provider_message(self):
        from backend.chat import USER_VISIBLE_FALLBACK
        handler = self._make_handler_with_project()
        msg = {"role": "assistant", "content": USER_VISIBLE_FALLBACK}
        result = handler._to_provider_message(self.project_id, msg, include_images=False)
        self.assertIsNone(result)

    def test_normal_assistant_passes_through(self):
        handler = self._make_handler_with_project()
        msg = {"role": "assistant", "content": "normal reply"}
        result = handler._to_provider_message(self.project_id, msg, include_images=False)
        self.assertEqual(result["content"], "normal reply")

    def test_user_role_with_legacy_text_not_sanitized(self):
        handler = self._make_handler_with_project()
        msg = {"role": "user", "content": "（本轮无回复）"}
        result = handler._to_provider_message(self.project_id, msg, include_images=False)
        self.assertEqual(result["content"], "（本轮无回复）")
```

Append to `tests/test_main_api.py`（**真实 API 返回 `{"messages": [...]}`，不是 list**；fixture 风格参考现有 `S0CheckpointEndpointTests` 用 `mock.patch.object(main_module, "skill_engine", autospec=True)`）：

```python
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import backend.main as main_module
from backend.chat import LEGACY_EMPTY_ASSISTANT_FALLBACKS, USER_VISIBLE_FALLBACK


class GetConversationSanitizeTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main_module.app)
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.project_path = Path(self.tmpdir.name) / "demo-project"
        self.project_path.mkdir(parents=True, exist_ok=True)
        # mock skill_engine.get_project_path 返回临时目录（v4: 保存 mock 引用便于测试切换）
        self.patcher = mock.patch.object(
            main_module.skill_engine, "get_project_path",
            return_value=self.project_path,
        )
        self.mock_get_project_path = self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def _write_conversation(self, messages):
        (self.project_path / "conversation.json").write_text(
            json.dumps(messages, ensure_ascii=False), encoding="utf-8",
        )

    def test_get_conversation_returns_messages_dict(self):
        """真实 API 返回 {"messages": [...]} 包装；sanitize 后仍是 dict 形式"""
        self._write_conversation([
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ])
        resp = self.client.get("/api/projects/demo/conversation")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("messages", data)
        self.assertEqual(len(data["messages"]), 2)

    def test_get_conversation_filters_legacy_fallback_assistants(self):
        self._write_conversation([
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "（本轮无回复）"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": USER_VISIBLE_FALLBACK},
            {"role": "user", "content": "q3"},
            {"role": "assistant", "content": "real reply"},
        ])
        resp = self.client.get("/api/projects/demo/conversation")
        data = resp.json()
        # 两条 fallback assistant 应被过滤；剩 4 条
        self.assertEqual(len(data["messages"]), 4)
        # user role 完全不动
        contents = [m["content"] for m in data["messages"]]
        self.assertIn("q1", contents)
        self.assertIn("real reply", contents)
        self.assertNotIn("（本轮无回复）", contents)
        self.assertNotIn(USER_VISIBLE_FALLBACK, contents)

    def test_get_conversation_strips_tool_log_comments_from_assistants(self):
        """assistant content 含 <!-- tool-log ... --> 注释 → API 返回不含"""
        self._write_conversation([
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "Real reply.\n<!-- tool-log\n- web_search ✓\n-->"},
        ])
        resp = self.client.get("/api/projects/demo/conversation")
        data = resp.json()
        assistant_msg = next(m for m in data["messages"] if m["role"] == "assistant")
        self.assertNotIn("<!-- tool-log", assistant_msg["content"])
        self.assertIn("Real reply", assistant_msg["content"])

    def test_get_conversation_user_role_unchanged_even_with_tool_log_text(self):
        """user 自己写的内容含 <!-- tool-log → 不动（防误吞用户输入）"""
        self._write_conversation([
            {"role": "user", "content": "see <!-- tool-log\n--> in my message"},
        ])
        resp = self.client.get("/api/projects/demo/conversation")
        data = resp.json()
        self.assertIn("<!-- tool-log", data["messages"][0]["content"])

    def test_get_conversation_404_when_project_missing(self):
        """直接改 setUp 已有 mock 的 return_value（v4 修订 — 不动 patcher 生命周期，更稳定）"""
        # mock 是 self.patcher.start() 的产物，可直接 setattr 切换返回值
        # 注：self.patcher 已在 setUp patch 了 main_module.skill_engine.get_project_path；
        # 这里直接拿 self.patcher.start() 返回的 MagicMock 重写 return_value
        # （setUp 里 self.patcher.start() 隐式存在；如未保存可改 setUp 加 self.mock_get = self.patcher.start()）
        self.mock_get_project_path.return_value = None
        resp = self.client.get("/api/projects/missing/conversation")
        self.assertEqual(resp.status_code, 404)
```

**额外修改 setUp**（v4 把 patcher 返回的 MagicMock 保存为属性，便于测试用例切换返回值）：

```python
def setUp(self):
    # ... 已有逻辑
    self.patcher = mock.patch.object(
        main_module.skill_engine, "get_project_path",
        return_value=self.project_path,
    )
    self.mock_get_project_path = self.patcher.start()  # v4: 保存 mock 引用
    self.addCleanup(self.patcher.stop)
```

注：`test_get_conversation_strips_tool_log_comments_from_assistants` 测试在 Task 12 落地后才会通过（Task 8 只引入历史 fallback sanitize；tool-log strip 在 Task 12）。Task 8 落地时此测试会 fail——属于预期，Task 12 一并通过即可。

- [ ] **Step 2: Run to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::HistorySanitizeTests -v
```

Expected: fail（`_to_provider_message` 还没 sanitize）。

- [ ] **Step 3: Implement provider build sanitize**

Modify `backend/chat.py` `_to_provider_message`（约 4072 行）顶部加 sanitize（**`LEGACY_EMPTY_ASSISTANT_FALLBACKS` 是模块级常量**——Task 6 已建在 `backend.chat` 模块顶部）：

```python
def _to_provider_message(self, project_id: str, message: Dict, include_images: bool) -> Dict | None:
    role = message.get("role")
    if role not in {"user", "assistant"}:
        return None
    if role == "assistant":
        content = message.get("content", "") or ""
        # v5: sanitize 历史 fallback 污染——这种 assistant 不喂回模型
        if content.strip() in LEGACY_EMPTY_ASSISTANT_FALLBACKS:
            return None
        if not content.strip():
            content = "（本轮无回复）"  # 老逻辑兜底（不应发生但留保险）
        return {"role": "assistant", "content": content}
    # ... 后续 user role 路径不变
```

- [ ] **Step 4: Implement GET /conversation sanitize**

**真实 API 返回 `{"messages": [...]}` 包装；用工厂 `get_chat_handler` 不是全局变量。**

Modify `backend/main.py` `GET /api/projects/{id}/conversation` handler（[backend/main.py:368-378](../../backend/main.py:368)）：

```python
from backend.chat import LEGACY_EMPTY_ASSISTANT_FALLBACKS

@app.get("/api/projects/{project_id}/conversation")
async def get_conversation(project_id: str):
    project_path = skill_engine.get_project_path(project_id)
    if not project_path:
        raise HTTPException(status_code=404, detail="项目不存在")
    conv_file = project_path / "conversation.json"
    if not conv_file.exists():
        return {"messages": []}
    with open(conv_file, "r", encoding="utf-8") as f:
        messages = json.load(f)
    # v5: sanitize 历史 fallback assistant
    sanitized = [
        m for m in messages
        if not (
            m.get("role") == "assistant"
            and (m.get("content") or "").strip() in LEGACY_EMPTY_ASSISTANT_FALLBACKS
        )
    ]
    return {"messages": sanitized}  # 保留 dict wrapper 不破坏前端契约
```

`LEGACY_EMPTY_ASSISTANT_FALLBACKS` 已在 `backend.chat` 模块顶部（Task 6 写入）。

- [ ] **Step 5: Implement frontend sanitize**

Create `frontend/src/utils/chatPresentation.js`（如不存在）：

```javascript
export const LEGACY_EMPTY_ASSISTANT_FALLBACKS = new Set([
  '（本轮无回复）',
  '（这一轮我没有产出可见回复，可能是处理过程中断了。请把刚才的需求换个说法再发一次。）',
])

export function sanitizeAssistantMessage(message) {
  if (message.role !== 'assistant') return message
  const trimmed = (message.content || '').trim()
  if (LEGACY_EMPTY_ASSISTANT_FALLBACKS.has(trimmed)) return null
  return message
}
```

Modify `frontend/src/components/ChatPanel.jsx` history loader（约 121-132 行 `setMessages` 调用）。**真实 API 返回 `{"messages": [...]}`，loader 用 `res.data.messages`**：

```jsx
import { sanitizeAssistantMessage } from '../utils/chatPresentation'

// 现有：fetchConversation 后从 res.data.messages 取 messages
const fetched = res.data.messages || []
const sanitized = fetched
  .map(sanitizeAssistantMessage)
  .filter(m => m !== null)
setMessages(sanitized.map((m, i) => ({ ...m, id: m.id || `msg-${Date.now()}-${i}` })))
```

Append to `frontend/tests/chatPresentation.test.mjs`:

```javascript
test('sanitizeAssistantMessage drops legacy fallback assistant', () => {
  const msg = { role: 'assistant', content: '（本轮无回复）' }
  assert.equal(sanitizeAssistantMessage(msg), null)
})

test('sanitizeAssistantMessage drops user_visible_fallback assistant', () => {
  const msg = {
    role: 'assistant',
    content: '（这一轮我没有产出可见回复，可能是处理过程中断了。请把刚才的需求换个说法再发一次。）',
  }
  assert.equal(sanitizeAssistantMessage(msg), null)
})

test('sanitizeAssistantMessage keeps user role with same text', () => {
  const msg = { role: 'user', content: '（本轮无回复）' }
  assert.deepEqual(sanitizeAssistantMessage(msg), msg)
})

test('sanitizeAssistantMessage keeps normal assistant', () => {
  const msg = { role: 'assistant', content: 'real reply' }
  assert.deepEqual(sanitizeAssistantMessage(msg), msg)
})
```

- [ ] **Step 6: Run all tests**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::HistorySanitizeTests -v
.venv\Scripts\python -m pytest tests/test_main_api.py -v
cd frontend; node --test tests/chatPresentation.test.mjs; cd ..
```

Expected: all pass。

- [ ] **Step 7: Commit**

```powershell
git add backend/chat.py backend/main.py frontend/src/utils/chatPresentation.js frontend/src/components/ChatPanel.jsx tests/test_chat_runtime.py tests/test_main_api.py frontend/tests/chatPresentation.test.mjs
git commit -m "feat(history): three-layer sanitize legacy fallback (A3)"
```

---

### Task 9: C1 — `_pair_tool_calls_with_results` 配对算法

**Spec:** §5.2

**Files:**
- Create: `tests/test_tool_log.py`
- Modify: `backend/chat.py` (add helper near `_persist_successful_tool_result`)

- [ ] **Step 1: Write failing tests**

Create `tests/test_tool_log.py`:

```python
import json
import unittest
from backend.chat import ChatHandler

class PairToolCallsWithResultsTests(unittest.TestCase):
    def setUp(self):
        # 简化构造（不需要完整 fixture，只测算法）
        self.handler = ChatHandler.__new__(ChatHandler)

    def test_basic_pair_one_call_one_result(self):
        msgs = [
            {"role": "assistant", "tool_calls": [
                {"id": "call_1", "function": {"name": "web_search", "arguments": '{"q":"x"}'}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": '{"status":"success","results":[]}'},
        ]
        pairs = self.handler._pair_tool_calls_with_results(msgs)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].name, "web_search")
        self.assertEqual(pairs[0].result["status"], "success")

    def test_skip_text_only_assistant(self):
        msgs = [
            {"role": "assistant", "content": "thinking..."},  # 无 tool_calls
            {"role": "assistant", "tool_calls": [
                {"id": "call_1", "function": {"name": "x", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": '{}'},
        ]
        pairs = self.handler._pair_tool_calls_with_results(msgs)
        self.assertEqual(len(pairs), 1)

    def test_skip_retry_user_barrier(self):
        # 模拟 chat.py:3267-3281 的 malformed retry 隔板
        msgs = [
            {"role": "assistant", "content": "（上条工具调用被上游合并...）"},
            {"role": "user", "content": "刚才的 tool_calls 格式异常..."},
            {"role": "assistant", "tool_calls": [
                {"id": "call_1", "function": {"name": "x", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": '{}'},
        ]
        pairs = self.handler._pair_tool_calls_with_results(msgs)
        self.assertEqual(len(pairs), 1)

    def test_skip_tool_with_no_matching_id(self):
        msgs = [
            {"role": "tool", "tool_call_id": "orphan", "content": '{}'},
            {"role": "assistant", "tool_calls": [
                {"id": "call_1", "function": {"name": "x", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": '{}'},
        ]
        pairs = self.handler._pair_tool_calls_with_results(msgs)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].name, "x")

    def test_handle_malformed_json_tool_result(self):
        msgs = [
            {"role": "assistant", "tool_calls": [
                {"id": "call_1", "function": {"name": "x", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "not-json"},
        ]
        pairs = self.handler._pair_tool_calls_with_results(msgs)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].result["status"], "error")
        self.assertIn("raw", pairs[0].result)

    def test_empty_messages_returns_empty(self):
        pairs = self.handler._pair_tool_calls_with_results([])
        self.assertEqual(pairs, [])

    def test_multi_calls_in_one_assistant_paired_individually(self):
        msgs = [
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "function": {"name": "a", "arguments": "{}"}},
                {"id": "c2", "function": {"name": "b", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"r":1}'},
            {"role": "tool", "tool_call_id": "c2", "content": '{"r":2}'},
        ]
        pairs = self.handler._pair_tool_calls_with_results(msgs)
        self.assertEqual(len(pairs), 2)
        self.assertEqual(pairs[0].name, "a")
        self.assertEqual(pairs[1].name, "b")
```

- [ ] **Step 2: Run to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_tool_log.py -v
```

Expected: AttributeError。

- [ ] **Step 3: Implement helper**

Add to `backend/chat.py`:

```python
from dataclasses import dataclass

@dataclass
class ToolPair:
    name: str
    args: str
    result: dict

def _pair_tool_calls_with_results(self, current_turn_messages: List[Dict]) -> List[ToolPair]:
    """
    严格按 tool_call_id 配对。跳过纯文本 assistant、retry user 隔板、orphan tool 消息。
    """
    pending_calls: dict[str, dict] = {}
    pairs: List[ToolPair] = []
    for msg in current_turn_messages:
        if msg.get("role") == "assistant":
            for tc in (msg.get("tool_calls") or []):
                tc_id = tc.get("id")
                if tc_id:
                    pending_calls[tc_id] = {
                        "name": tc.get("function", {}).get("name"),
                        "args": tc.get("function", {}).get("arguments") or "",
                    }
        elif msg.get("role") == "tool":
            tc_id = msg.get("tool_call_id")
            meta = pending_calls.pop(tc_id, None) if tc_id else None
            if meta is None:
                continue
            try:
                result = json.loads(msg.get("content") or "{}")
                if not isinstance(result, dict):
                    result = {"status": "error", "raw": str(result)}
            except json.JSONDecodeError:
                result = {"status": "error", "raw": msg.get("content")}
            pairs.append(ToolPair(name=meta["name"], args=meta["args"], result=result))
    return pairs
```

- [ ] **Step 4: Run tests to verify pass**

```powershell
.venv\Scripts\python -m pytest tests/test_tool_log.py -v
```

Expected: 7 pass。

- [ ] **Step 5: Commit**

```powershell
git add backend/chat.py tests/test_tool_log.py
git commit -m "feat(tool-log): pair tool_calls with results by id (C1)"
```

---

### Task 10: C1 — `_append_tool_log_to_assistant` + `_insert_before_tail_tags` + format

**Spec:** §5.1, §5.3, Appendix B

**Files:**
- Modify: `backend/chat.py` (new helpers)
- Test: `tests/test_tool_log.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_tool_log.py`:

```python
class AppendToolLogTests(unittest.TestCase):
    def setUp(self):
        self.handler = ChatHandler.__new__(ChatHandler)

    def test_format_success_with_short_args(self):
        msgs = [
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "function": {"name": "web_search", "arguments": '{"query":"x"}'}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"status":"success","results":[1,2,3]}'},
        ]
        result = self.handler._append_tool_log_to_assistant("Hello world.", msgs)
        self.assertIn("<!-- tool-log", result)
        self.assertIn("web_search", result)
        self.assertIn("✓", result)
        self.assertIn("-->", result)
        self.assertTrue(result.startswith("Hello world."))

    def test_format_error_with_brief(self):
        msgs = [
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "function": {"name": "write_file", "arguments": '{"file_path":"plan/x.md"}'}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"status":"error","message":"some error"}'},
        ]
        result = self.handler._append_tool_log_to_assistant("Reply.", msgs)
        self.assertIn("✗", result)

    def test_append_report_draft_path_from_result(self):
        """v2 修订（codex round-1 plan review）：append_report_draft 真实 schema 只有 content；
        路径在 result["path"]，不在 args 里。tool-log 必须能正确拼出 path。"""
        msgs = [
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "function": {"name": "append_report_draft",
                 "arguments": '{"content":"new section text..."}'}},
            ]},
            {"role": "tool", "tool_call_id": "c1",
             "content": '{"status":"success","path":"content/report_draft_v1.md"}'},
        ]
        result = self.handler._append_tool_log_to_assistant("Reply.", msgs)
        self.assertIn("append_report_draft", result)
        self.assertIn("content/report_draft_v1.md", result)  # path 来自 result
        self.assertNotIn("new section text", result)  # content 不能泄漏

    def test_max_iterations_tool_log_full_chain(self):
        """spec §5.5 — 撞 max_iterations=20 时 tool-log 应附加全部 20 条调用记录。"""
        msgs = []
        for i in range(20):
            msgs.append({"role": "assistant", "tool_calls": [
                {"id": f"c{i}", "function": {"name": "web_search",
                 "arguments": f'{{"query":"q{i}"}}'}},
            ]})
            msgs.append({"role": "tool", "tool_call_id": f"c{i}",
                         "content": '{"status":"success","results":[]}'})
        # 加上 max_iter 兜底文案
        result = self.handler._append_tool_log_to_assistant(
            "抱歉，工具调用轮次过多，已停止本轮，请缩小检索范围或改成分步提问。", msgs,
        )
        # 应附加 20 行
        log_lines = [l for l in result.split("\n") if l.startswith("- web_search(")]
        self.assertEqual(len(log_lines), 20)

    def test_no_pairs_no_log_appended(self):
        result = self.handler._append_tool_log_to_assistant("Reply.", [])
        self.assertEqual(result, "Reply.")

    def test_truncate_long_args(self):
        long_arg = "a" * 200
        msgs = [
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "function": {"name": "web_search", "arguments": json.dumps({"query": long_arg})}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"status":"success"}'},
        ]
        result = self.handler._append_tool_log_to_assistant("Reply.", msgs)
        # 单行不应超过 120 字符（Appendix B 规则）
        for line in result.split("\n"):
            self.assertLessEqual(len(line), 120)


class InsertBeforeTailTagsTests(unittest.TestCase):
    def setUp(self):
        self.handler = ChatHandler.__new__(ChatHandler)

    def test_no_tail_tags_appends_at_end(self):
        result = self.handler._insert_before_tail_tags("body text", "BLOCK")
        self.assertTrue(result.endswith("BLOCK"))

    def test_inserts_before_stage_ack_tail(self):
        content = "body\n\n<stage-ack>outline_confirmed_at</stage-ack>"
        result = self.handler._insert_before_tail_tags(content, "INJ")
        # INJ 必须在 stage-ack 之前
        inj_pos = result.find("INJ")
        ack_pos = result.find("<stage-ack")
        self.assertLess(inj_pos, ack_pos)

    def test_inserts_before_draft_action_tail(self):
        content = "body\n\n<draft-action>begin</draft-action>"
        result = self.handler._insert_before_tail_tags(content, "INJ")
        inj_pos = result.find("INJ")
        tag_pos = result.find("<draft-action")
        self.assertLess(inj_pos, tag_pos)

    def test_inserts_before_draft_action_replace_block(self):
        content = "body\n\n<draft-action-replace>\n  <old>x</old>\n  <new>y</new>\n</draft-action-replace>"
        result = self.handler._insert_before_tail_tags(content, "INJ")
        inj_pos = result.find("INJ")
        tag_pos = result.find("<draft-action-replace")
        self.assertLess(inj_pos, tag_pos)

    def test_inserts_before_mixed_stage_ack_and_draft_action(self):
        content = "body\n\n<draft-action>begin</draft-action>\n<stage-ack>outline_confirmed_at</stage-ack>"
        result = self.handler._insert_before_tail_tags(content, "INJ")
        inj_pos = result.find("INJ")
        for tag in ("<draft-action", "<stage-ack"):
            self.assertLess(inj_pos, result.find(tag))

    def test_trailing_whitespace_preserved(self):
        content = "body\n\n<stage-ack>outline_confirmed_at</stage-ack>\n\n"
        result = self.handler._insert_before_tail_tags(content, "INJ")
        # 不破坏尾部 whitespace
        self.assertIn("INJ", result)
```

- [ ] **Step 2: Run to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_tool_log.py::AppendToolLogTests tests/test_tool_log.py::InsertBeforeTailTagsTests -v
```

Expected: AttributeError。

- [ ] **Step 3: Implement helpers**

Add to `backend/chat.py`:

```python
def _format_tool_pair_line(self, pair: ToolPair) -> str:
    """格式：- TOOL_NAME(SHORT_ARGS) ✓ SUMMARY 或 ✗ ERROR_BRIEF。每行硬上限 120 字符。
    
    重要（v2 修订）：append_report_draft 的真实 schema 只有 content 参数（无 file_path）；
    但执行结果会在 result 里加 path（chat.py:4443-4445）。所以路径优先从 result["path"] 取。
    """
    try:
        args_dict = json.loads(pair.args) if isinstance(pair.args, str) else (pair.args or {})
    except json.JSONDecodeError:
        args_dict = {}
    
    # SHORT_ARGS：取首个主参数
    short_args = ""
    if args_dict:
        # 对 append_report_draft 不显示 content 全文（太长），用 "..."
        if pair.name == "append_report_draft":
            short_args = "..."
        else:
            first_key = next(iter(args_dict))
            first_val = str(args_dict[first_key])
            if len(first_val) > 40:
                first_val = first_val[:37] + "..."
            short_args = f"'{first_val}'" if first_key in {"query", "url"} else first_val
    
    is_success = pair.result.get("status") == "success"
    symbol = "✓" if is_success else "✗"
    
    if is_success:
        if pair.name == "web_search":
            n = len(pair.result.get("results") or [])
            summary = f"{n} results"
        elif pair.name == "fetch_url":
            kb = round(len(pair.result.get("content") or "") / 1024, 1)
            summary = f"{kb} KB"
        elif pair.name in {"write_file", "edit_file"}:
            # write/edit 有 args 里 file_path
            summary = args_dict.get("file_path", "")
            qh = pair.result.get("quality_hint")
            if qh:
                summary = f"{summary} | {qh[:40]}"
        elif pair.name == "append_report_draft":
            # append_report_draft 路径在 result 里
            summary = pair.result.get("path", "content/report_draft_v1.md")
        else:
            summary = ""
    else:
        msg = pair.result.get("message") or pair.result.get("error") or "unknown"
        summary = msg[:60]
    
    line = f"- {pair.name}({short_args}) {symbol} {summary}".rstrip()
    if len(line) > 120:
        line = line[:117] + "..."
    return line

def _append_tool_log_to_assistant(
    self,
    content: str,
    current_turn_messages: List[Dict],
) -> str:
    pairs = self._pair_tool_calls_with_results(current_turn_messages)
    if not pairs:
        return content
    lines = [self._format_tool_pair_line(p) for p in pairs]
    block = "<!-- tool-log\n" + "\n".join(lines) + "\n-->"
    return self._insert_before_tail_tags(content, block)

# 复用 stage_ack 的 _tail_anchor 思路，扩展扫描表
TAIL_TAG_SCAN_RE = re.compile(
    r'<stage-ack(?:\s+action="(?:set|clear)")?>[a-z_0-9]+</stage-ack>'
    r'|<draft-action>[^<]+</draft-action>'
    r'|<draft-action-replace>[\s\S]*?</draft-action-replace>',
    re.IGNORECASE,
)

def _insert_before_tail_tags(self, content: str, block: str) -> str:
    """在尾部所有 stage-ack / draft-action / draft-action-replace tag block 之前插入。"""
    # 找出所有 tag span
    spans = [(m.start(), m.end()) for m in self.TAIL_TAG_SCAN_RE.finditer(content)]
    if not spans:
        # 无 tail tag → 直接 append
        sep = "\n\n" if content and not content.endswith("\n") else ""
        return content + sep + block
    
    # 找尾部连续 tag block 的起始位置（_tail_anchor 思路）
    last_pos = -1
    i = 0
    while i < len(content):
        in_tag = False
        for s, e in spans:
            if s <= i < e:
                i = e
                in_tag = True
                break
        if in_tag:
            continue
        if not content[i].isspace():
            last_pos = i
        i += 1
    tail_anchor = last_pos + 1  # 一过最后一个非 tag 非 whitespace 字符
    
    before = content[:tail_anchor].rstrip()
    after = content[tail_anchor:]
    sep = "\n\n" if before else ""
    return before + sep + block + "\n" + after.lstrip("\n")
```

- [ ] **Step 4: Run tests to verify pass**

```powershell
.venv\Scripts\python -m pytest tests/test_tool_log.py -v
```

Expected: all pass。

- [ ] **Step 5: Commit**

```powershell
git add backend/chat.py tests/test_tool_log.py
git commit -m "feat(tool-log): append summary block before tail tags (C1)"
```

---

### Task 11: C1 — `strip_tool_log_comments` backend + 前端 helper

**Spec:** §5.4

**Files:**
- Modify: `backend/chat.py` (add module-level constant + function)
- Create: `frontend/src/utils/toolLogStrip.mjs`
- Create: `frontend/tests/toolLogStrip.test.mjs`
- Test: `tests/test_tool_log.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_tool_log.py`:

```python
class StripToolLogCommentsTests(unittest.TestCase):
    def test_strips_well_formed_single_line(self):
        from backend.chat import strip_tool_log_comments
        s = "Reply.\n<!-- tool-log\n- web_search ✓\n-->"
        result = strip_tool_log_comments(s)
        self.assertEqual(result, "Reply.")

    def test_strips_multi_line(self):
        from backend.chat import strip_tool_log_comments
        s = "Reply.\n<!-- tool-log\n- a ✓\n- b ✗ err\n-->"
        result = strip_tool_log_comments(s)
        self.assertEqual(result, "Reply.")

    def test_handles_unclosed_truncated_stream(self):
        from backend.chat import strip_tool_log_comments
        s = "Reply.\n<!-- tool-log\n- partial ✓"
        result = strip_tool_log_comments(s)
        # 备用分支吞到末尾
        self.assertEqual(result, "Reply.")

    def test_handles_nested_dash_dash(self):
        from backend.chat import strip_tool_log_comments
        s = "Reply.\n<!-- tool-log\n- some -- tool ✓\n-->"
        result = strip_tool_log_comments(s)
        self.assertEqual(result, "Reply.")

    def test_no_tool_log_comment_unchanged(self):
        from backend.chat import strip_tool_log_comments
        s = "Reply with no comment.\n<!-- regular html comment -->"
        result = strip_tool_log_comments(s)
        # 只剥 tool-log，普通注释不动
        self.assertEqual(result, s)
```

Create `frontend/tests/toolLogStrip.test.mjs`:

```javascript
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { stripToolLogComments } from '../src/utils/toolLogStrip.mjs'

test('strips well-formed comment', () => {
  const s = 'Reply.\n<!-- tool-log\n- web_search ✓\n-->'
  assert.equal(stripToolLogComments(s), 'Reply.')
})

test('strips multi-line', () => {
  const s = 'Reply.\n<!-- tool-log\n- a ✓\n- b ✗ err\n-->'
  assert.equal(stripToolLogComments(s), 'Reply.')
})

test('handles unclosed truncated stream', () => {
  const s = 'Reply.\n<!-- tool-log\n- partial ✓'
  assert.equal(stripToolLogComments(s), 'Reply.')
})

test('handles nested -- inside comment', () => {
  const s = 'Reply.\n<!-- tool-log\n- some -- tool ✓\n-->'
  assert.equal(stripToolLogComments(s), 'Reply.')
})

test('preserves non-tool-log comments', () => {
  const s = 'Reply.\n<!-- regular html comment -->'
  assert.equal(stripToolLogComments(s), s)
})
```

- [ ] **Step 2: Run to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_tool_log.py::StripToolLogCommentsTests -v
cd frontend; node --test tests/toolLogStrip.test.mjs; cd ..
```

Expected: ImportError。

- [ ] **Step 3: Implement backend**

Add to `backend/chat.py` 模块级（顶部）：

```python
TOOL_LOG_COMMENT_RE = re.compile(
    r'<!--\s*tool-log'
    r'(?:[\s\S]*?-->|[\s\S]*$)',
    re.IGNORECASE,
)

def strip_tool_log_comments(content: str) -> str:
    return TOOL_LOG_COMMENT_RE.sub("", content).rstrip()
```

- [ ] **Step 4: Implement frontend**

Create `frontend/src/utils/toolLogStrip.mjs`:

```javascript
const TOOL_LOG_COMMENT_RE = /<!--\s*tool-log(?:[\s\S]*?-->|[\s\S]*$)/gi

export function stripToolLogComments(content) {
  if (!content) return content
  return content.replace(TOOL_LOG_COMMENT_RE, '').trimEnd()
}
```

- [ ] **Step 5: Run all tests**

```powershell
.venv\Scripts\python -m pytest tests/test_tool_log.py::StripToolLogCommentsTests -v
cd frontend; node --test tests/toolLogStrip.test.mjs; cd ..
```

Expected: all pass。

- [ ] **Step 6: Commit**

```powershell
git add backend/chat.py frontend/src/utils/toolLogStrip.mjs tests/test_tool_log.py frontend/tests/toolLogStrip.test.mjs
git commit -m "feat(tool-log): strip helper backend + frontend (C1)"
```

---

### Task 12: C1 — 三层 sanitize（GET API + 前端 render + 前端 copy）

**Spec:** §5.4 (table)

**Files:**
- Modify: `backend/main.py` (`GET /api/projects/{id}/conversation`)
- Modify: `frontend/src/components/ChatPanel.jsx` (`copyMessage` ~282-287; render ~718-720)
- Test: `tests/test_main_api.py`、`frontend/tests/chatPresentation.test.mjs`

- [ ] **Step 1: Write failing tests**

**注**：Task 8 的 `GetConversationSanitizeTests` 已经包含 `test_get_conversation_strips_tool_log_comments_from_assistants` 测试（覆盖本节修改）。Task 12 不需新建独立测试类——Task 8 的测试在 Task 12 实施完成后才会通过（Task 8 落地时只有 fallback sanitize；tool-log strip 在 Task 12 落地时打开）。本 task 仅需保证已有测试 pass。

Append to `frontend/tests/chatPresentation.test.mjs`:

```javascript
import { stripToolLogComments } from '../src/utils/toolLogStrip.mjs'

test('copy button output strips tool-log', () => {
  const original = 'Reply.\n<!-- tool-log\n- x ✓\n-->'
  const copyText = stripToolLogComments(original)
  assert.equal(copyText, 'Reply.')
})

test('render input has tool-log stripped before markdown', () => {
  const original = 'Reply\n<!-- tool-log\n- x ✓\n-->\n# Title'
  const rendered = stripToolLogComments(original)
  assert.equal(rendered.includes('<!-- tool-log'), false)
})
```

- [ ] **Step 2: Run to verify fail**

```powershell
cd frontend; node --test tests/chatPresentation.test.mjs; cd ..
```

Expected: 已 import 但 ChatPanel 未集成。

- [ ] **Step 3: Modify GET /conversation**

Update `backend/main.py` GET handler——在 Task 8 加 fallback sanitize 之上**追加 tool-log strip**，**保持 `{"messages": [...]}` 包装**：

```python
from backend.chat import LEGACY_EMPTY_ASSISTANT_FALLBACKS, strip_tool_log_comments

@app.get("/api/projects/{project_id}/conversation")
async def get_conversation(project_id: str):
    project_path = skill_engine.get_project_path(project_id)
    if not project_path:
        raise HTTPException(status_code=404, detail="项目不存在")
    conv_file = project_path / "conversation.json"
    if not conv_file.exists():
        return {"messages": []}
    with open(conv_file, "r", encoding="utf-8") as f:
        messages = json.load(f)
    
    sanitized = []
    for m in messages:
        if m.get("role") == "assistant":
            raw = m.get("content") or ""
            if raw.strip() in LEGACY_EMPTY_ASSISTANT_FALLBACKS:
                continue
            # v5: 剥 tool-log 注释（前端不该看到）
            stripped = strip_tool_log_comments(raw)
            sanitized.append({**m, "content": stripped})
        else:
            sanitized.append(m)
    return {"messages": sanitized}
```

- [ ] **Step 4: Modify frontend ChatPanel**

**真实代码**：`copyMessage(content)` 单参数（[ChatPanel.jsx:282](../../frontend/src/components/ChatPanel.jsx:282)）；按钮调 `copyMessage(msg.content)`（[ChatPanel.jsx:728](../../frontend/src/components/ChatPanel.jsx:728)）；渲染入口 `splitAssistantMessageBlocks(msg.content)`（[ChatPanel.jsx:691](../../frontend/src/components/ChatPanel.jsx:691)）。

**保持 `copyMessage` 签名不变**（避免 caller 也要改）；在函数内部 strip：

```jsx
import { stripToolLogComments } from '../utils/toolLogStrip.mjs'

// copyMessage（约 282-287）
const copyMessage = async (content) => {
  const cleanText = stripToolLogComments(content || '')
  await navigator.clipboard.writeText(cleanText)
}

// render 路径（约 691）—— 在 splitAssistantMessageBlocks 之前 strip
// 旧
const blocks = msg.role === 'assistant'
  ? splitAssistantMessageBlocks(msg.content)
  : [{ type: 'text', content: msg.content }]
// 新
const cleanContent = msg.role === 'assistant'
  ? stripToolLogComments(msg.content || '')
  : msg.content
const blocks = msg.role === 'assistant'
  ? splitAssistantMessageBlocks(cleanContent)
  : [{ type: 'text', content: cleanContent }]
```

- [ ] **Step 5: Run all tests**

```powershell
.venv\Scripts\python -m pytest tests/test_main_api.py -v
cd frontend; node --test tests/; cd ..
```

Expected: all pass。

- [ ] **Step 6: Commit**

```powershell
git add backend/main.py frontend/src/components/ChatPanel.jsx tests/test_main_api.py frontend/tests/chatPresentation.test.mjs
git commit -m "feat(tool-log): three-layer sanitize (C1)"
```

---

### Task 13: 编排器整合 `_finalize_assistant_turn`（7 步顺序）

**Spec:** §5.6

**Files:**
- Modify: `backend/chat.py` (`_finalize_assistant_turn` ~6372-6414)
- Test: `tests/test_chat_runtime.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_chat_runtime.py`:

```python
class AssistantTurnOrchestratorTests(ChatRuntimeTests):
    def test_only_stage_ack_turn_records_checkpoint_then_a3(self):
        """assistant 只回 <stage-ack>outline_confirmed_at</stage-ack> →
        checkpoint 落戳 + 走 A3 不持久化空文本"""
        handler = self._make_handler_with_project()
        # 先准备 outline 让 stage-ack 前置校验通过
        self._write_stage_one_prerequisites(self.project_dir)
        history = []
        current_user = {"role": "user", "content": "确认大纲", "attached_material_ids": []}
        assistant_msg = "<stage-ack>outline_confirmed_at</stage-ack>"
        result = handler._finalize_assistant_turn(
            self.project_id, history, current_user, assistant_msg, [],
            user_message="确认大纲",
        )
        # checkpoint 落戳
        ckpt = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertIn("outline_confirmed_at", ckpt)
        # history 末尾是 user，不持久化空 assistant
        self.assertEqual(history[-1]["role"], "user")
        # 返回值是 USER_VISIBLE_FALLBACK
        from backend.chat import USER_VISIBLE_FALLBACK
        self.assertEqual(result, USER_VISIBLE_FALLBACK)

    def test_stage_ack_executed_before_empty_check(self):
        """精细：把 stage-ack apply 改 mock 抛异常，验证空判断在 apply 之后才发生"""
        handler = self._make_handler_with_project()
        self._write_stage_one_prerequisites(self.project_dir)
        history = []
        current_user = {"role": "user", "content": "确认", "attached_material_ids": []}
        assistant_msg = "<stage-ack>outline_confirmed_at</stage-ack>"
        with mock.patch.object(handler, "_apply_stage_ack_event") as mock_apply:
            handler._finalize_assistant_turn(
                self.project_id, history, current_user, assistant_msg, [],
                user_message="确认",
            )
            # apply 必须被调用至少一次（如果先判空跳过则不会调）
            self.assertTrue(mock_apply.called)

    def test_normal_turn_persists_with_tool_log(self):
        """assistant 有正文 + 工具调用 → 持久化 content 含 tool-log 注释"""
        handler = self._make_handler_with_project()
        history = []
        current_user = {"role": "user", "content": "搜一下", "attached_material_ids": []}
        assistant_msg = "好的，已搜到结果。"
        current_turn_messages = [
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "function": {"name": "web_search",
                 "arguments": '{"query":"猪猪侠"}'}},
            ]},
            {"role": "tool", "tool_call_id": "c1",
             "content": '{"status":"success","results":[1,2]}'},
        ]
        handler._finalize_assistant_turn(
            self.project_id, history, current_user, assistant_msg, current_turn_messages,
            user_message="搜一下",
        )
        # history 末尾 assistant.content 含 tool-log 注释 + 原正文
        self.assertEqual(history[-1]["role"], "assistant")
        self.assertIn("好的，已搜到结果。", history[-1]["content"])
        self.assertIn("<!-- tool-log", history[-1]["content"])
        self.assertIn("web_search", history[-1]["content"])

    def test_tool_only_turn_walks_a3_no_tool_log_persisted(self):
        """assistant 没有可见正文（如只 tool_calls 然后流被截断）→ 走 A3，不持久化"""
        handler = self._make_handler_with_project()
        history = []
        current_user = {"role": "user", "content": "test", "attached_material_ids": []}
        assistant_msg = ""  # 空文本
        current_turn_messages = [
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "function": {"name": "web_search", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"status":"success"}'},
        ]
        result = handler._finalize_assistant_turn(
            self.project_id, history, current_user, assistant_msg, current_turn_messages,
            user_message="test",
        )
        from backend.chat import USER_VISIBLE_FALLBACK
        self.assertEqual(result, USER_VISIBLE_FALLBACK)
        # history 末尾是 user，不含 assistant
        self.assertEqual(history[-1]["role"], "user")
```

- [ ] **Step 2: Run to verify fail**

Expected: 现有 `_finalize_assistant_turn` 还没扩展到 7 步顺序。

- [ ] **Step 3: Refactor `_finalize_assistant_turn`**

按 spec §5.6 7 步重构。当前 `_finalize_assistant_turn`（约 6372-6414）只处理 stage-ack；扩展为：

```python
def _finalize_assistant_turn(
    self,
    project_id: str,
    history: List[Dict],
    current_user_message: Dict,
    assistant_message: str,
    current_turn_messages: List[Dict],
    *,
    user_message: str = "",
) -> str:
    """
    统一编排器（spec §5.6 7 步顺序）：
    1. parse 控制 tag（stage-ack + draft-action）
    2. 执行 stage-ack 副作用
    3. 执行 draft-action 副作用
    4. strip 控制 tag → visible_content
    5. 判空 → 空走 A3
    6. 不空 → append tool-log
    7. 持久化
    """
    from backend.stage_ack import StageAckParser
    # Phase 2 后才有 draft_action
    try:
        from backend.draft_action import DraftActionParser
        draft_parser = DraftActionParser()
    except ImportError:
        draft_parser = None
    
    stage_parser = StageAckParser()
    
    # Step 1: parse
    stage_events = stage_parser.parse(assistant_message)
    draft_events = draft_parser.parse(assistant_message) if draft_parser else []
    
    # Step 2: stage-ack 副作用
    for event in stage_events:
        if event.executable:
            self._apply_stage_ack_event(project_id, event)
    
    # Step 3: draft-action 副作用（Phase 2 才有）
    # 注意：与 Task 18 一致——必须先 _validate_draft_action_event 再 apply
    for event in draft_events:
        validated = self._validate_draft_action_event(project_id, event)
        if validated.executable:
            self._apply_draft_action_event(project_id, validated)
    
    # Step 4: strip
    visible_content = stage_parser.strip(assistant_message)
    if draft_parser:
        visible_content = draft_parser.strip(visible_content)
    visible_content = visible_content.strip()
    
    # Step 5: 判空 → A3
    if not visible_content:
        return self._finalize_empty_assistant_turn(
            project_id, history, current_user_message,
            diagnostic="tag_strip_emptied" if (stage_events or draft_events) else "stream_truncated",
        )
    
    # Step 6: append tool-log
    persisted_content = visible_content
    if current_turn_messages:
        persisted_content = self._append_tool_log_to_assistant(
            persisted_content, current_turn_messages,
        )
    
    # Step 7: 持久化
    history.extend([
        current_user_message,
        {"role": "assistant", "content": persisted_content},
    ])
    self._save_conversation(project_id, history)
    return persisted_content  # caller 用于 return value（最终展示给用户的文本，不含 tool-log——因为前端会 strip）
```

注：`_apply_stage_ack_event` 是现有方法（搜 stage_ack 处理代码移出来）；`_apply_draft_action_event` 是 Phase 2 才加的（Task 18）。Phase 1 阶段 `draft_parser` 总是 None，draft_events 总是 []。

- [ ] **Step 4: Update 3 callers**

三处调用 `_finalize_assistant_turn` 的真实位置（codex round-1 plan review 已 grep 确认）：[chat.py:3417](../../backend/chat.py:3417)（`_chat_stream_unlocked`）、[chat.py:3675](../../backend/chat.py:3675)（`_chat_unlocked`）、[chat.py:6349](../../backend/chat.py:6349)（`_finalize_early_assistant_message`）。

每处按新签名调用：

```python
# 旧签名
self._finalize_assistant_turn(project_id, history, current_user_message, assistant_message, ...)

# 新签名（v2 编排器）
result = self._finalize_assistant_turn(
    project_id, history, current_user_message, assistant_message, current_turn_messages,
    user_message=user_message_text,  # 现有参数保持
)
```

caller 处理返回值：
- generator path（3417）：result 是用户面文本（USER_VISIBLE_FALLBACK 或 persisted_content）；调用方决定是否 yield
- 普通函数 path（3675, 6349）：result 同上；用作 return value 的 `content` 字段

- [ ] **Step 5: Run tests**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::AssistantTurnOrchestratorTests -v
.venv\Scripts\python -m pytest tests/ 2>&1 | Select-String -Pattern "FAIL"
```

Expected: 编排器测试 pass，全套测试无回归。

- [ ] **Step 6: Commit**

```powershell
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "refactor(chat): extend _finalize_assistant_turn to 7-step orchestrator (A3+C1)"
```

---

### Task 14: Phase 1 reality_test smoke + commit + PR

**Spec:** §Rollout Phase 1

- [ ] **Step 1: 重打包**

```powershell
.\build.bat
```

或 `powershell -File build.ps1`。预计输出 `dist\咨询报告助手\`。

- [ ] **Step 2: 启动 reality_test**

打开 `dist\咨询报告助手\咨询报告助手.exe`，加载 reality_test 项目（`D:\MyProject\CodeProject\consulting-report-agent\reality_test\`）。

- [ ] **Step 3: 验证清单**

依次确认：
- 黄框：之前每轮触发的 "本轮要修改的文件 X 已存在，请先调用 read_file" **不再出现**
- progress.md：S2/S3 阶段 system prompt 包含 `**质量进度**: 5/7 条 有效来源`（可在 backend log 看完整 prompt 验证）
- 空回复：触发 stream 截断后用户面看到 "（这一轮我没有产出可见回复...）"，下一轮模型 prompt 中**没有**这条文本
- tool-log：assistant 持久化 content 含 `<!-- tool-log ... -->`；用户 UI 看不到；复制按钮粘贴出来不含
- 历史 sanitize：reality_test 旧 conversation.json 中 "（本轮无回复）" 不再展示

- [ ] **Step 4: Commit packaging changes if any**

如果 build 产物变化导致 spec / 文档需更新，commit。否则跳过。

- [ ] **Step 5: PR Phase 1**

PowerShell here-string（`@'...'@` 单引号字面量；闭合 `'@` 必须列首无缩进）：

```powershell
git push -u origin claude/happy-jackson-938bd1

$body = @'
## Summary
- A1：SystemNotice 加 surface_to_user 必填字段 + dedupe 拆双 + 服务端过滤
- A2：progress.md 渲染 quality_progress + tool_result 追加 quality_hint
- A3：抽 _finalize_empty_assistant_turn helper + _coalesce_consecutive_user_messages + 三层 sanitize
- C1：HTML 注释 tool-log + 配对算法 + insert 算法 + 三层 sanitize
- 编排器：_finalize_assistant_turn 扩展为 7 步顺序

实施 2026-05-04 spec Phase 1（[design](docs/superpowers/specs/2026-05-04-context-signal-and-intent-tag-design.md)）。

## Test plan
- [x] 后端新增测试 pass
- [x] 前端 chatPresentation / toolLogStrip 测试 pass
- [x] 全套回归测试 pass
- [x] reality_test 跑一遍确认验收清单（见 PR 描述）
'@

gh pr create --title "feat(spec-2026-05-04): Phase 1 — A1+A2+A3+C1 context signal layer fixes" --body $body
```

---

### Task 15: B1 — `backend/draft_action.py` 模块（DraftActionEvent + Parser）

**Spec:** §4.3, §4.5, §4.4

**Files:**
- Create: `backend/draft_action.py`
- Create: `tests/test_draft_action.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_draft_action.py`:

```python
import unittest
from backend.draft_action import DraftActionParser, DraftActionEvent

class DraftActionParserBasicTests(unittest.TestCase):
    def setUp(self):
        self.parser = DraftActionParser()

    def test_parse_begin(self):
        events = self.parser.parse("Reply\n<draft-action>begin</draft-action>")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].intent, "begin")
        self.assertTrue(events[0].executable)

    def test_parse_continue(self):
        events = self.parser.parse("Reply\n<draft-action>continue</draft-action>")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].intent, "continue")

    def test_parse_section_with_label(self):
        events = self.parser.parse("Reply\n<draft-action>section:第二章 战力演化</draft-action>")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].intent, "section")
        self.assertEqual(events[0].section_label, "第二章 战力演化")

    def test_parse_replace_nested(self):
        content = "Reply\n<draft-action-replace>\n  <old>原文</old>\n  <new>新文</new>\n</draft-action-replace>"
        events = self.parser.parse(content)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].intent, "replace")
        self.assertEqual(events[0].old_text, "原文")
        self.assertEqual(events[0].new_text, "新文")

    def test_strip_simple_tag(self):
        content = "Reply\n<draft-action>begin</draft-action>"
        result = self.parser.strip(content)
        self.assertEqual(result.strip(), "Reply")

    def test_strip_replace_block(self):
        content = "Reply\n<draft-action-replace>\n<old>x</old>\n<new>y</new>\n</draft-action-replace>"
        result = self.parser.strip(content)
        self.assertEqual(result.strip(), "Reply")

    def test_unknown_intent_ignored(self):
        events = self.parser.parse("Reply\n<draft-action>unknown</draft-action>")
        # 不识别（正则不匹配）
        self.assertEqual(len(events), 0)

    def test_section_label_too_long_ignored(self):
        long = "x" * 100
        events = self.parser.parse(f"Reply\n<draft-action>section:{long}</draft-action>")
        # 80 字符上限
        self.assertEqual(len(events), 0)


class DraftActionParserPositionTests(unittest.TestCase):
    def setUp(self):
        self.parser = DraftActionParser()

    def test_in_fenced_code_ignored_but_stripped(self):
        content = "Reply\n```\n<draft-action>begin</draft-action>\n```"
        events = self.parser.parse(content)
        self.assertTrue(all(not e.executable for e in events))
        self.assertEqual(events[0].ignored_reason, "in_fenced_code")
        # strip 仍然剥
        result = self.parser.strip(content)
        self.assertNotIn("<draft-action", result)

    def test_in_inline_code_ignored(self):
        content = "Reply `<draft-action>begin</draft-action>`"
        events = self.parser.parse(content)
        self.assertTrue(all(not e.executable for e in events))

    def test_in_blockquote_ignored(self):
        content = "Reply\n> <draft-action>begin</draft-action>"
        events = self.parser.parse(content)
        self.assertTrue(all(not e.executable for e in events))

    def test_non_tail_ignored(self):
        content = "<draft-action>begin</draft-action>\nMore text after."
        events = self.parser.parse(content)
        self.assertTrue(all(not e.executable for e in events))
        self.assertEqual(events[0].ignored_reason, "not_tail")

    def test_non_independent_line_ignored(self):
        content = "Reply <draft-action>begin</draft-action>"
        events = self.parser.parse(content)
        self.assertTrue(all(not e.executable for e in events))

    def test_tail_with_trailing_whitespace_ok(self):
        content = "Reply\n<draft-action>begin</draft-action>\n\n   "
        events = self.parser.parse(content)
        self.assertTrue(events[0].executable)
```

- [ ] **Step 2: Run to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_draft_action.py -v
```

Expected: ImportError。

- [ ] **Step 3: Implement `backend/draft_action.py`**

参考 `backend/stage_ack.py` 结构：

```python
"""XML tag parser for draft action signals (begin / continue / section / replace).

Mirrors backend.stage_ack pattern. Parsed events drive _gate_canonical_draft_tool_call
in chat.py. See spec docs/superpowers/specs/2026-05-04-context-signal-and-intent-tag-design.md §4.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


@dataclass
class DraftActionEvent:
    raw: str
    intent: Literal["begin", "continue", "section", "replace"]
    section_label: str | None = None
    old_text: str | None = None
    new_text: str | None = None
    start: int = 0
    end: int = 0
    executable: bool = True
    ignored_reason: str | None = None


SIMPLE_PATTERN = re.compile(
    r'<draft-action>(begin|continue|section:[^<\n]{1,80})</draft-action>',
    re.IGNORECASE,
)

REPLACE_PATTERN = re.compile(
    r'<draft-action-replace>\s*'
    r'<old>([\s\S]{1,1000}?)</old>\s*'
    r'<new>([\s\S]{1,1000}?)</new>\s*'
    r'</draft-action-replace>',
    re.IGNORECASE,
)


class DraftActionParser:
    STRIP_PATTERN = re.compile(
        r'<draft-action>[^<\n]+</draft-action>'
        r'|<draft-action-replace>[\s\S]*?</draft-action-replace>',
        re.IGNORECASE,
    )

    FENCED_RE = re.compile(r"^( {0,3})(```|~~~)", re.MULTILINE)

    def parse_raw(self, content: str) -> list[DraftActionEvent]:
        events: list[DraftActionEvent] = []
        if not content:
            return events
        # simple tags
        for m in SIMPLE_PATTERN.finditer(content):
            payload = m.group(1)
            if payload.startswith("section:"):
                intent = "section"
                section_label = payload[len("section:"):].strip()
            else:
                intent = payload  # begin / continue
                section_label = None
            events.append(DraftActionEvent(
                raw=m.group(0),
                intent=intent,
                section_label=section_label,
                start=m.start(), end=m.end(),
            ))
        # replace blocks
        for m in REPLACE_PATTERN.finditer(content):
            events.append(DraftActionEvent(
                raw=m.group(0),
                intent="replace",
                old_text=m.group(1),
                new_text=m.group(2),
                start=m.start(), end=m.end(),
            ))
        events.sort(key=lambda e: e.start)
        return events

    def parse(self, content: str) -> list[DraftActionEvent]:
        events = self.parse_raw(content)
        if not events:
            return []
        fenced_spans = self._fenced_spans(content)
        tail_anchor = self._tail_anchor(content, events)
        for event in events:
            reason = self._classify_position(content, event, fenced_spans, tail_anchor)
            if reason is not None:
                event.executable = False
                event.ignored_reason = reason
        return events

    def strip(self, content: str) -> str:
        if not content or "draft-action" not in content.lower():
            return content
        result = self.STRIP_PATTERN.sub("", content)
        return re.sub(r"\n{3,}", "\n\n", result)

    def _classify_position(self, content, event, fenced_spans, tail_anchor):
        for s, e in fenced_spans:
            if s <= event.start < e:
                return "in_fenced_code"
        line_start = content.rfind("\n", 0, event.start) + 1
        # 对 multi-line replace block，end 也要看 line_end
        line_end_nl = content.find("\n", event.start)
        line_end = line_end_nl if line_end_nl != -1 else len(content)
        before = content[line_start:event.start]
        # event.end 跨多行（replace）特殊处理：取 event.end 之后的行
        after_end_nl = content.find("\n", event.end)
        after_end = after_end_nl if after_end_nl != -1 else len(content)
        after = content[event.end:after_end]

        if re.match(r"^\s*>", before):
            return "in_blockquote"
        if before.count("`") % 2 == 1:
            return "in_inline_code"
        if before.strip() or after.strip():
            return "not_independent_line"
        if event.start < tail_anchor:
            return "not_tail"
        return None

    def _fenced_spans(self, content):
        spans = []
        open_start = None
        open_fence = None
        for m in self.FENCED_RE.finditer(content):
            fence = m.group(2)
            if open_start is None:
                open_start = m.start()
                open_fence = fence
            elif fence == open_fence:
                line_end_nl = content.find("\n", m.end())
                close_end = line_end_nl + 1 if line_end_nl != -1 else len(content)
                spans.append((open_start, close_end))
                open_start = None
                open_fence = None
        if open_start is not None:
            spans.append((open_start, len(content)))
        return spans

    def _tail_anchor(self, content, events):
        tag_spans = [(e.start, e.end) for e in events]
        last_pos = -1
        i = 0
        while i < len(content):
            in_tag = False
            for ts, te in tag_spans:
                if ts <= i < te:
                    i = te
                    in_tag = True
                    break
            if in_tag:
                continue
            if not content[i].isspace():
                last_pos = i
            i += 1
        return last_pos + 1
```

- [ ] **Step 4: Run tests to verify pass**

```powershell
.venv\Scripts\python -m pytest tests/test_draft_action.py -v
```

Expected: 14+ pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/draft_action.py tests/test_draft_action.py
git commit -m "feat(draft-action): parser module + position rules (B1)"
```

---

### Task 16: B1 — chat.py 流式 tail guard 加 draft-action 前缀

**Spec:** §4.4

**真实结构（v2 修订）**：tail guard 核心是模块级 `stream_split_safe_tail()` 函数 + `_STAGE_ACK_MARKER = "<stage-ack"` 常量（[chat.py:61-90](../../backend/chat.py:61)）。`_chat_stream_unlocked` 只调用它（[chat.py:3196-3201](../../backend/chat.py:3196)）。所以**真正要改的是 `stream_split_safe_tail` + 加 marker**。

**Files:**
- Modify: `backend/chat.py` 模块级 (`_STAGE_ACK_MARKER` 附近 + `stream_split_safe_tail` 函数)
- Test: `tests/test_chat_runtime.py`（独立测试 module-level helper）

- [ ] **Step 1: Write failing tests**

Append to `tests/test_chat_runtime.py`:

```python
class StreamSplitSafeTailDraftActionTests(unittest.TestCase):
    """模块级 helper 独立测试，不需要 ChatHandler。"""

    def test_draft_action_simple_marker_held(self):
        from backend.chat import stream_split_safe_tail
        # buffer 中段就含 "<draft-action" → 从此位置起全部 hold
        emit, hold = stream_split_safe_tail("Hello <draft-action>begin</draft-action>")
        self.assertEqual(emit, "Hello ")
        self.assertEqual(hold, "<draft-action>begin</draft-action>")

    def test_draft_action_replace_marker_held(self):
        from backend.chat import stream_split_safe_tail
        emit, hold = stream_split_safe_tail("Reply <draft-action-replace>")
        self.assertEqual(emit, "Reply ")
        self.assertEqual(hold, "<draft-action-replace>")

    def test_draft_action_partial_prefix_at_tail_held(self):
        from backend.chat import stream_split_safe_tail
        # 末尾恰好是某 marker 的前缀（如 "<draft-act"）→ hold 该尾段
        emit, hold = stream_split_safe_tail("Ok content <draft-act")
        self.assertEqual(emit, "Ok content ")
        self.assertEqual(hold, "<draft-act")

    def test_stage_ack_marker_still_held(self):
        # 回归：stage-ack marker 行为不变
        from backend.chat import stream_split_safe_tail
        emit, hold = stream_split_safe_tail("Hi <stage-ack>x</stage-ack>")
        self.assertEqual(emit, "Hi ")
        self.assertIn("<stage-ack", hold)

    def test_no_marker_emit_all(self):
        from backend.chat import stream_split_safe_tail
        emit, hold = stream_split_safe_tail("plain text no markers here")
        self.assertEqual(emit, "plain text no markers here")
        self.assertEqual(hold, "")

    def test_earliest_marker_anchors_hold(self):
        from backend.chat import stream_split_safe_tail
        # 同时含 stage-ack 和 draft-action，靠前的赢
        emit, hold = stream_split_safe_tail("Hi <draft-action>x</draft-action> <stage-ack>y</stage-ack>")
        self.assertEqual(emit, "Hi ")
        self.assertTrue(hold.startswith("<draft-action"))
```

- [ ] **Step 2: Run to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::StreamSplitSafeTailDraftActionTests -v
```

- [ ] **Step 3: Implement — extend marker list + helper**

Modify `backend/chat.py` module level (around line 61):

```python
_STAGE_ACK_MARKER = "<stage-ack"
_DRAFT_ACTION_MARKER = "<draft-action"  # 同时覆盖 <draft-action-replace
_TAIL_GUARD_MARKERS = (_STAGE_ACK_MARKER, _DRAFT_ACTION_MARKER)


def stream_split_safe_tail(buffer: str) -> tuple[str, str]:
    """Split buffer into (safe_to_emit_now, held_until_stream_close).
    
    v2: now scans for both <stage-ack and <draft-action markers; earliest one wins.
    """
    if not buffer:
        return "", ""
    
    # Rule 1: 找最早的 marker 出现位置
    earliest_idx = -1
    for marker in _TAIL_GUARD_MARKERS:
        idx = buffer.lower().find(marker)
        if idx != -1 and (earliest_idx == -1 or idx < earliest_idx):
            earliest_idx = idx
    if earliest_idx != -1:
        return buffer[:earliest_idx], buffer[earliest_idx:]
    
    # Rule 2: 末尾是某 marker 的前缀（"<draft-act" 等）
    for marker in _TAIL_GUARD_MARKERS:
        marker_len = len(marker)
        max_overlap = min(marker_len - 1, len(buffer))
        for k in range(max_overlap, 0, -1):
            if marker.startswith(buffer[-k:].lower()):
                return buffer[:-k], buffer[-k:]
    
    # Rule 3: 全部安全
    return buffer, ""
```

- [ ] **Step 4: Run tests pass**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::StreamSplitSafeTailDraftActionTests -v
.venv\Scripts\python -m pytest tests/ -v 2>&1 | Select-String -Pattern "stream_split"
```

第二条命令验证现有 stage-ack tail guard 测试无回归。

- [ ] **Step 5: Commit**

```powershell
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "feat(stream): extend tail-guard scan for draft-action markers (B1)"
```

---

### Task 17: B1 — 新增 `_preflight_canonical_draft_check`（Phase 2a 不删旧）

**Spec:** §4.2, §4.7, §Rollout Step 2a

**v2 关键修订（codex round-1 plan review P0）**：spec §Rollout Step 2a 明确要求"保留 `_classify_canonical_draft_turn` 完整旧逻辑同时跑，只做对照组日志"。Task 17 v1 直接 rename + simplify 是错的——会让 Task 20 的 compare 灰度无法运行。

**v2 策略**：
- Phase 2a：**新增** `_preflight_canonical_draft_check` 作为独立函数（与旧 `_classify_canonical_draft_turn` 并存）
- 旧函数完全不动，所有现有 caller 仍调旧函数 + 决策仍由旧函数主导
- 新函数仅供 Task 20 compare 写入用
- Phase 2b（Task 24）才删旧 + final rename + simplify

**Files:**
- Modify: `backend/chat.py` (顶部加 `_DRAFT_INTENT_PREFLIGHT_KEYWORDS` 常量)
- Modify: `backend/chat.py` (新增 `_preflight_canonical_draft_check` 方法，**不动** `_classify_canonical_draft_turn`)
- Modify: `backend/chat.py` (`_make_canonical_draft_decision` / `_empty_canonical_draft_decision` 加 `preflight_keyword_intent` 字段，default `None`，向后兼容旧函数)
- Test: `tests/test_chat_runtime.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_chat_runtime.py`:

```python
class PreflightCheckTests(ChatRuntimeTests):
    def test_preflight_keyword_intent_begin_for_start_writing(self):
        handler = self._make_handler_with_project()
        decision = handler._preflight_canonical_draft_check(
            self.project_id, "开始写报告吧", stage_code="S4",
        )
        self.assertEqual(decision["preflight_keyword_intent"], "begin")

    def test_preflight_keyword_intent_continue_for_continue_writing(self):
        handler = self._make_handler_with_project()
        decision = handler._preflight_canonical_draft_check(
            self.project_id, "继续写", stage_code="S4",
        )
        self.assertEqual(decision["preflight_keyword_intent"], "continue")

    def test_preflight_keyword_intent_none_for_unrelated(self):
        handler = self._make_handler_with_project()
        decision = handler._preflight_canonical_draft_check(
            self.project_id, "你好", stage_code="S4",
        )
        self.assertIsNone(decision["preflight_keyword_intent"])

    def test_preflight_keyword_intent_never_section_or_replace(self):
        handler = self._make_handler_with_project()
        # spec §4.2 硬约束
        for msg in ["重写第二章", "把 X 改成 Y", "section:foo", "replace this"]:
            decision = handler._preflight_canonical_draft_check(
                self.project_id, msg, stage_code="S4",
            )
            self.assertNotIn(
                decision["preflight_keyword_intent"], {"section", "replace"},
            )

    def test_preflight_s0_with_draft_intent_rejects(self):
        handler = self._make_handler_with_project()
        decision = handler._preflight_canonical_draft_check(
            self.project_id, "开始写报告吧", stage_code="S0",
        )
        self.assertEqual(decision["mode"], "reject")
        # surface_to_user system_notice 应被发出
        notices = handler._turn_context.get("pending_system_notices", [])
        user_notices = [n for n in notices if n.get("surface_to_user")]
        self.assertTrue(any("S0" in (n.get("reason") or "") or "大纲" in (n.get("reason") or "") for n in user_notices))

    def test_preflight_no_decisions_no_keyword_no_change(self):
        # "你好" 在 S4 → no_write
        handler = self._make_handler_with_project()
        decision = handler._preflight_canonical_draft_check(
            self.project_id, "你好", stage_code="S4",
        )
        self.assertEqual(decision["mode"], "no_write")

    def test_preflight_begin_wins_over_continue_when_both_match(self):
        """v2 显式：begin/continue 双命中时，按 dict 顺序 begin 在前，begin 赢"""
        handler = self._make_handler_with_project()
        decision = handler._preflight_canonical_draft_check(
            self.project_id, "开始写报告，然后继续写", stage_code="S4",
        )
        self.assertEqual(decision["preflight_keyword_intent"], "begin")
```

**注**：上面前面的几个测试方法也要把 `handler.` 改为 `handler = self._make_handler_with_project()` + `handler.` 风格（v2 修正 ChatRuntimeTests 真实结构）。

- [ ] **Step 2: Run to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::PreflightCheckTests -v
```

Expected: AttributeError（method 还没 rename）。

- [ ] **Step 3: Implement — 加常量 + 新增方法 + 加字段（不删旧）**

Add to `backend/chat.py` 顶部常量区:

```python
# Spec §4.7 — 极短关键词列表，分 begin/continue 两类，仅供 preflight 粗粒度判定
_DRAFT_INTENT_PREFLIGHT_KEYWORDS = {
    "begin": (
        "开始写报告",
        "开始写正文",
        "开始起草",
        "起草报告",
        "写第一版",
    ),
    "continue": (
        "继续写",
        "继续写报告",
        "继续写正文",
        "接着写",
        "写下一章",
        "写下一段",
    ),
}
```

**新增** `_preflight_canonical_draft_check` 方法（**完全独立**于 `_classify_canonical_draft_turn`，旧方法不动）：

```python
def _preflight_canonical_draft_check(
    self,
    project_id: str,
    user_message: str,
    *,
    stage_code: str | None = None,
) -> dict[str, object]:
    """
    新通道：粗粒度门禁（spec §4.2）。
    1. 用户消息是否含正文意图（_DRAFT_INTENT_PREFLIGHT_KEYWORDS 命中）？
    2. 含意图时 stage / outline 是否允许？
    3. mixed-intent 拆轮？
    
    返回 _make_canonical_draft_decision dict，preflight_keyword_intent 字段填值。
    不再做 begin/continue/section/replace 的细分（细分由 draft-action tag 决定）。
    
    Phase 2a 期间：仅供 Task 20 compare 使用，不参与实际决策。
    Phase 2b 切主时：替代 _classify_canonical_draft_turn 成为唯一 preflight。
    """
    # stage 推断：复用现有 inline 逻辑（chat.py:1649-1657 同款）
    normalized_stage = (stage_code or "").strip()
    if not normalized_stage:
        project_path = self.skill_engine.get_project_path(project_id)
        if project_path:
            normalized_stage = (
                self.skill_engine._infer_stage_state(project_path).get("stage_code") or "S0"
            )
        else:
            normalized_stage = "S0"
    
    text = (user_message or "").strip()
    if not text:
        return self._empty_canonical_draft_decision(stage_code=normalized_stage)
    
    # Step 1: preflight_keyword_intent 检测（dict 顺序定义优先级：begin 在前）
    preflight_intent = None
    for intent, kws in _DRAFT_INTENT_PREFLIGHT_KEYWORDS.items():
        if self._phrase_hits(text, list(kws)):
            preflight_intent = intent  # "begin" 或 "continue"
            break  # 首匹配赢——begin 比 continue 优先
    
    # Step 2: 含意图但 stage 不对 → reject
    if preflight_intent is not None:
        if normalized_stage not in self.NON_PLAN_WRITE_ALLOWED_STAGE_CODES:
            self._emit_system_notice_once(
                category="non_plan_write_blocked",
                path=None,
                reason="S0/S1 阶段不能写正文，请先确认大纲再启动正文",
                user_action="请先在工作区确认大纲，再发起正文请求",
                surface_to_user=True,
            )
            return self._make_canonical_draft_decision(
                stage_code=normalized_stage,
                mode="reject",
                priority="P_PREFLIGHT_STAGE",
                fixed_message=self.CANONICAL_DRAFT_STAGE_GATE_MESSAGE,
                preflight_keyword_intent=preflight_intent,
            )
    
    # Step 3: mixed-intent 拆轮（保留现有 helper）
    secondary = self._secondary_action_families_in_message(text)
    if len(secondary) > 1 and preflight_intent is not None:
        return self._make_canonical_draft_decision(
            stage_code=normalized_stage,
            mode="reject",
            priority="P_PREFLIGHT_MULTI",
            fixed_message=self.CANONICAL_DRAFT_SPLIT_TURN_MESSAGE,
            preflight_keyword_intent=preflight_intent,
        )
    
    return self._make_canonical_draft_decision(
        stage_code=normalized_stage,
        mode="no_write" if preflight_intent is None else "require",
        priority="P_PREFLIGHT_OK",
        preflight_keyword_intent=preflight_intent,
    )
```

`_make_canonical_draft_decision` (1606 行) 加 `preflight_keyword_intent` 参数 + dict key（默认 `None`，旧 caller 不传也不报错）。

`_empty_canonical_draft_decision` (1589 行) 加 `"preflight_keyword_intent": None` 默认值。

**不要改任何现有 `_classify_canonical_draft_turn` caller**——Phase 2a 期间所有写入决策仍走旧逻辑。Task 20 单独并行调新方法做 compare。Task 24（Phase 2b）才切换 caller + 删旧。

**关于 begin/continue 双命中优先级（v2 显式）**：`_DRAFT_INTENT_PREFLIGHT_KEYWORDS` 定义顺序为 `("begin", "continue")`，Python 3.7+ dict 保序，循环中首匹配赢。如果某用户消息同时含两类关键词（如 "继续写报告，开始下一章"），`begin` 由于在前会赢——这符合直觉（用户表达"继续 + 开始"时倾向于"开始一个新单元"）。已加测试覆盖。

- [ ] **Step 4: Run tests pass**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::PreflightCheckTests -v
```

Expected: 6 pass。

- [ ] **Step 5: Commit**

```powershell
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "refactor(preflight): rename + simplify + add preflight_keyword_intent (B1)"
```

---

### Task 18: B1 — draft-action event 解析 + §4.6 前置校验 + turn_context 写入

**Spec:** §4.6

**Files:**
- Modify: `backend/chat.py` (new helpers + integrate into `_finalize_assistant_turn`)
- Test: `tests/test_chat_runtime.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_chat_runtime.py`:

```python
from backend.draft_action import DraftActionEvent

class DraftActionPreCheckTests(ChatRuntimeTests):
    def _seed_outline_confirmed(self, handler):
        """Helper: 让 outline_confirmed_at checkpoint 已 set + stage 推到 S4，
        否则 _validate_draft_action_event 会先在 stage_too_early/outline_not_confirmed
        分支拒绝，测不到 no_draft/section/replace 校验。"""
        # 用现有 _write_stage_one_prerequisites 准备 outline + research-plan
        self._write_stage_one_prerequisites(self.project_dir)
        # mock _infer_stage_state 返回 S4 + outline_confirmed_at 已 set
        # 简化：直接落 checkpoints + stage S4 推断逻辑会自然认到
        from datetime import datetime
        ckpt_path = self.project_dir / "stage_checkpoints.json"
        ckpt_path.write_text(
            json.dumps({"outline_confirmed_at": datetime.now().isoformat(timespec="seconds")}),
            encoding="utf-8",
        )

    def _seed_draft(self, content: str):
        """Helper: 写 content/report_draft_v1.md"""
        draft_path = self.project_dir / "content" / "report_draft_v1.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(content, encoding="utf-8")

    def test_section_intent_no_draft_returns_no_draft_message(self):
        handler = self._make_handler_with_project()
        self._seed_outline_confirmed(handler)
        # 故意不调 _seed_draft → draft 不存在
        event = DraftActionEvent(
            raw="...", intent="section", section_label="第二章",
        )
        result = handler._validate_draft_action_event(self.project_id, event)
        self.assertFalse(result.executable)
        self.assertEqual(result.ignored_reason, "no_draft")

    def test_replace_intent_no_draft_returns_no_draft_message(self):
        handler = self._make_handler_with_project()
        self._seed_outline_confirmed(handler)
        event = DraftActionEvent(
            raw="...", intent="replace", old_text="x", new_text="y",
        )
        result = handler._validate_draft_action_event(self.project_id, event)
        self.assertFalse(result.executable)
        self.assertEqual(result.ignored_reason, "no_draft")

    def test_continue_intent_no_draft_auto_degrade_to_begin(self):
        handler = self._make_handler_with_project()
        self._seed_outline_confirmed(handler)
        event = DraftActionEvent(raw="...", intent="continue")
        result = handler._validate_draft_action_event(self.project_id, event)
        self.assertTrue(result.executable)
        self.assertEqual(result.intent, "begin")  # 降级

    def test_section_label_unique_match(self):
        handler = self._make_handler_with_project()
        self._seed_outline_confirmed(handler)
        self._seed_draft(
            "# 报告\n\n## 第一章 序言\n背景内容\n\n## 第二章 战力演化\n演化分析\n"
        )
        event = DraftActionEvent(
            raw="...", intent="section", section_label="第二章 战力演化",
        )
        result = handler._validate_draft_action_event(self.project_id, event)
        self.assertTrue(result.executable)

    def test_section_label_partial_match_ambiguous(self):
        handler = self._make_handler_with_project()
        self._seed_outline_confirmed(handler)
        self._seed_draft(
            "# 报告\n\n## 第二章 战力演化\n正文\n\n## 第二章附录\n附录内容\n"
        )
        event = DraftActionEvent(
            raw="...", intent="section", section_label="第二章",
        )
        result = handler._validate_draft_action_event(self.project_id, event)
        self.assertFalse(result.executable)
        self.assertEqual(result.ignored_reason, "section_ambiguous")

    def test_replace_old_text_not_unique_rejects(self):
        handler = self._make_handler_with_project()
        self._seed_outline_confirmed(handler)
        self._seed_draft("X 出现一次。\n然后 X 又出现一次。\n")  # X 出现两次
        event = DraftActionEvent(
            raw="...", intent="replace", old_text="X", new_text="Y",
        )
        result = handler._validate_draft_action_event(self.project_id, event)
        self.assertFalse(result.executable)
        self.assertEqual(result.ignored_reason, "replace_target_invalid")
```

- [ ] **Step 2-4: Implement `_validate_draft_action_event` + integrate into orchestrator**

Add to `backend/chat.py`:

```python
def _validate_draft_action_event(
    self,
    project_id: str,
    event: DraftActionEvent,
) -> DraftActionEvent:
    """spec §4.6 前置校验。返回事件本身（可能被改 executable=False / 自动降级）"""
    # v2 新增：preflight_blocked / stage_too_early / outline_not_confirmed 校验
    decision = self._turn_context.get("canonical_draft_decision") or {}
    if decision.get("mode") == "reject":
        event.executable = False
        event.ignored_reason = "preflight_blocked"
        # 不重复发 notice（preflight 已发）
        return event
    
    # stage_too_early
    project_path = self.skill_engine.get_project_path(project_id)
    if project_path is None:
        event.executable = False
        event.ignored_reason = "no_project"
        return event
    stage_state = self.skill_engine._infer_stage_state(project_path)
    stage_code = stage_state.get("stage_code") or "S0"
    if stage_code not in self.NON_PLAN_WRITE_ALLOWED_STAGE_CODES:
        event.executable = False
        event.ignored_reason = "stage_too_early"
        self._emit_system_notice_once(
            category="non_plan_write_blocked", path=None,
            reason="S0/S1 阶段不能写正文，请先确认大纲",
            user_action="请先在工作区确认大纲", surface_to_user=True,
        )
        return event
    
    # outline_not_confirmed
    checkpoints = self.skill_engine._load_stage_checkpoints(project_path)
    if "outline_confirmed_at" not in checkpoints:
        event.executable = False
        event.ignored_reason = "outline_not_confirmed"
        self._emit_system_notice_once(
            category="non_plan_write_blocked", path=None,
            reason="大纲尚未确认，不能写正文",
            user_action="请先在工作区确认大纲", surface_to_user=True,
        )
        return event
    
    # 文件存在性
    draft_path = project_path / self.skill_engine.REPORT_DRAFT_PATH
    draft_exists = draft_path.exists() and draft_path.read_text(encoding="utf-8").strip()
    
    if event.intent == "begin":
        return event  # always pass

    if event.intent == "continue":
        if not draft_exists:
            event.intent = "begin"  # auto-degrade（不 fail，可继续）
        return event
    
    if event.intent == "section":
        if not draft_exists:
            event.executable = False
            event.ignored_reason = "no_draft"
            self._emit_system_notice_once(
                category="non_plan_write_blocked", path=None,
                reason=self.CANONICAL_DRAFT_NO_DRAFT_MESSAGE,
                user_action="请先发 <draft-action>begin</draft-action> 起草，再来重写章节",
                surface_to_user=True,
            )
            return event
        # section label 匹配（复用 _resolve_section_rewrite_targets 但改 query 来源）
        draft_text = draft_path.read_text(encoding="utf-8")
        match_result = self._resolve_section_rewrite_targets(
            event.section_label, draft_text,
        )
        if match_result.get("ambiguous"):
            event.executable = False
            event.ignored_reason = "section_ambiguous"
            self._emit_system_notice_once(
                category="non_plan_write_blocked", path=None,
                reason=f"章节 '{event.section_label}' 不唯一，请用完整 heading 定位",
                user_action="参考 read_file content/report_draft_v1.md 看完整章节标题",
                surface_to_user=True,
            )
            return event
        if not (match_result.get("nodes")):
            event.executable = False
            event.ignored_reason = "section_not_found"
            self._emit_system_notice_once(
                category="non_plan_write_blocked", path=None,
                reason=f"找不到章节 '{event.section_label}'，请先 read_file 核对章节标题",
                user_action="先 read_file content/report_draft_v1.md 确认 heading",
                surface_to_user=True,
            )
            return event
        return event
    
    if event.intent == "replace":
        if not draft_exists:
            event.executable = False
            event.ignored_reason = "no_draft"
            self._emit_system_notice_once(
                category="non_plan_write_blocked", path=None,
                reason=self.CANONICAL_DRAFT_NO_DRAFT_MESSAGE,
                user_action="请先发 <draft-action>begin</draft-action> 起草",
                surface_to_user=True,
            )
            return event
        draft_text = draft_path.read_text(encoding="utf-8")
        if event.old_text not in draft_text:
            event.executable = False
            event.ignored_reason = "replace_target_invalid"
            self._emit_system_notice_once(
                category="non_plan_write_blocked", path=None,
                reason="替换源文本未找到，请用完整唯一片段",
                user_action="先 read_file 找到准确文本片段",
                surface_to_user=True,
            )
            return event
        if draft_text.count(event.old_text) > 1:
            event.executable = False
            event.ignored_reason = "replace_target_invalid"
            self._emit_system_notice_once(
                category="non_plan_write_blocked", path=None,
                reason="替换源文本不唯一，请加上下文使其唯一",
                user_action="扩大 OLD 片段使其唯一", surface_to_user=True,
            )
            return event
        return event
    
    return event


def _apply_draft_action_event(
    self,
    project_id: str,
    event: DraftActionEvent,
) -> None:
    """通过校验的 event 追加到 turn_context.draft_action_events 列表（v2 命名统一）"""
    if not event.executable:
        return
    events_list = self._turn_context.setdefault("draft_action_events", [])
    events_list.append(event)
```

**v2 命名约定**：用 `turn_context["draft_action_events"]`（list of event 对象），**不是** `draft_action_decision`。Task 19 gate 也读 `events`（已在 Task 19 v2 同步修订）。`_new_turn_context` 加初始空 list 字段：

```python
def _new_turn_context(self, *, can_write_non_plan: bool) -> Dict[str, object]:
    return {
        # ... 已有字段
        "draft_action_events": [],  # v2 新增
    }
```

集成到 `_finalize_assistant_turn` (Task 13 编排器)：在 Step 3 "执行 draft-action 副作用" 时调用 `_validate_draft_action_event` 然后 `_apply_draft_action_event`。

- [ ] **Step 5: Run tests + commit**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::DraftActionPreCheckTests -v
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "feat(draft-action): pre-validation rules per spec §4.6 (B1)"
```

---

### Task 19: B1 — `_gate_canonical_draft_tool_call` + tagless fallback + record events

**Spec:** §4.8

**Files:**
- Modify: `backend/chat.py` (new gate + caller integration)
- Test: `tests/test_chat_runtime.py`

- [ ] **Step 1: Write failing tests**

Append:

```python
class GateCanonicalDraftToolCallTests(ChatRuntimeTests):
    """注意：append_report_draft 真实 schema 只有 content（chat.py:4187-4202）；
    write_file/edit_file 写 content/* 才有 file_path。"""

    def test_append_report_draft_with_begin_tag_passes(self):
        from backend.draft_action import DraftActionEvent
        handler = self._make_handler_with_project()
        tags = [DraftActionEvent(raw="...", intent="begin", executable=True)]
        decision = {"preflight_keyword_intent": None}
        result = handler._gate_canonical_draft_tool_call(
            self.project_id, "append_report_draft",
            {"content": "new section"},  # 真实 schema 只有 content
            decision, tags,
        )
        self.assertIsNone(result)  # pass

    def test_append_report_draft_with_keyword_fallback_passes(self):
        handler = self._make_handler_with_project()
        decision = {"preflight_keyword_intent": "begin"}
        result = handler._gate_canonical_draft_tool_call(
            self.project_id, "append_report_draft",
            {"content": "new section"},
            decision, [],
        )
        self.assertIsNone(result)

    def test_edit_file_no_tag_blocked_for_canonical_draft_path(self):
        handler = self._make_handler_with_project()
        decision = {"preflight_keyword_intent": "begin"}  # 即使 keyword 命中也不放行 edit_file
        result = handler._gate_canonical_draft_tool_call(
            self.project_id, "edit_file",
            {"file_path": "content/report_draft_v1.md", "old_string": "x", "new_string": "y"},
            decision, [],
        )
        self.assertIsNotNone(result)  # block

    def test_edit_file_with_section_tag_passes(self):
        from backend.draft_action import DraftActionEvent
        handler = self._make_handler_with_project()
        tags = [DraftActionEvent(raw="...", intent="section", section_label="x", executable=True)]
        decision = {"preflight_keyword_intent": None}
        result = handler._gate_canonical_draft_tool_call(
            self.project_id, "edit_file",
            {"file_path": "content/report_draft_v1.md", "old_string": "x", "new_string": "y"},
            decision, tags,
        )
        self.assertIsNone(result)

    def test_edit_file_with_replace_tag_passes(self):
        from backend.draft_action import DraftActionEvent
        handler = self._make_handler_with_project()
        tags = [DraftActionEvent(raw="...", intent="replace", old_text="x", new_text="y", executable=True)]
        decision = {"preflight_keyword_intent": None}
        result = handler._gate_canonical_draft_tool_call(
            self.project_id, "edit_file",
            {"file_path": "content/report_draft_v1.md", "old_string": "x", "new_string": "y"},
            decision, tags,
        )
        self.assertIsNone(result)

    def test_fallback_signal_only_from_preflight_keyword_intent(self):
        """关键防御测试：偷偷塞 intent_kind="section" 不能让 gate 放行 edit_file"""
        handler = self._make_handler_with_project()
        decision = {
            "preflight_keyword_intent": None,
            "intent_kind": "section",  # 偷塞
            "expected_tool_family": "edit_file",
        }
        result = handler._gate_canonical_draft_tool_call(
            self.project_id, "edit_file",
            {"file_path": "content/report_draft_v1.md", "old_string": "x", "new_string": "y"},
            decision, [],
        )
        self.assertIsNotNone(result)  # 必须 block

    def test_non_executable_tag_does_not_pass(self):
        from backend.draft_action import DraftActionEvent
        handler = self._make_handler_with_project()
        tags = [DraftActionEvent(raw="...", intent="section", section_label="x",
                                  executable=False, ignored_reason="no_draft")]
        decision = {"preflight_keyword_intent": None}
        result = handler._gate_canonical_draft_tool_call(
            self.project_id, "edit_file",
            {"file_path": "content/report_draft_v1.md", "old_string": "x", "new_string": "y"},
            decision, tags,
        )
        self.assertIsNotNone(result)

    def test_non_canonical_path_passes_unchecked(self):
        """写其他路径不归 gate 管"""
        handler = self._make_handler_with_project()
        result = handler._gate_canonical_draft_tool_call(
            self.project_id, "write_file",
            {"file_path": "plan/notes.md", "content": "..."},
            {}, [],
        )
        self.assertIsNone(result)

    def test_record_tagless_fallback_event_writes_state(self):
        handler = self._make_handler_with_project()
        decision = {"preflight_keyword_intent": "begin"}
        handler._gate_canonical_draft_tool_call(
            self.project_id, "append_report_draft",
            {"content": "x"},
            decision, [],
        )
        state = handler._load_conversation_state(self.project_id, [])
        events = [e for e in state.get("events", []) if e.get("type") == "tagless_draft_fallback"]
        self.assertGreaterEqual(len(events), 1)

    def test_append_report_draft_no_file_path_still_gated(self):
        """v3 关键回归测试：append_report_draft 真实 schema 没 file_path，
        但 gate 不能因此绕过——它按工具名识别 canonical draft 目标"""
        handler = self._make_handler_with_project()
        # 没 file_path、没 tag、没 keyword_intent → 必须 block
        result = handler._gate_canonical_draft_tool_call(
            self.project_id, "append_report_draft",
            {"content": "any text"},  # 真实 schema 只有 content
            {"preflight_keyword_intent": None},
            [],
        )
        self.assertIsNotNone(result)  # 必须 block，不能因为缺 file_path 就 pass
```

- [ ] **Step 2: Run to verify fail**

- [ ] **Step 3: Implement gate + record helper**

Add to `backend/chat.py`:

```python
CANONICAL_DRAFT_REQUIRES_EXPLICIT_TAG_MESSAGE = (
    "请先在回复中发 <draft-action> tag 声明本轮正文动作类型（begin/continue/section/replace），"
    "再调用写正文工具。"
)

def _gate_canonical_draft_tool_call(
    self,
    project_id: str,
    tool_name: str,
    tool_args: dict,
    decision: dict,
    tags: list,  # list[DraftActionEvent]
) -> str | None:
    """spec §4.8 — 工具放行 gate。返回 None=pass / str=block reason。
    
    入口判定（v3 修订 — codex round-2 plan review P1）：
    - append_report_draft：真实 schema 只有 content，无 file_path。
      但本工具按定义就是写 canonical draft，所以工具名一致即纳入 gate。
    - write_file / edit_file：通用工具，必须看 file_path 是否指向 canonical draft。
    """
    is_canonical_target = False
    if tool_name == "append_report_draft":
        # append_report_draft 工具 by definition 写 canonical draft（chat.py:4187-4202）
        is_canonical_target = True
    elif tool_name in {"write_file", "edit_file"}:
        target_path = (tool_args.get("file_path") or "").replace("\\", "/")
        is_canonical_target = self._is_canonical_report_draft_path(target_path)
    if not is_canonical_target:
        return None
    
    # 仅依赖 preflight_keyword_intent 字段（v4/v5 强制）
    keyword_intent = decision.get("preflight_keyword_intent")
    if keyword_intent not in {"begin", "continue", None}:
        # 防御：如果实施有 bug 让其他值进来，强制 block
        return self.CANONICAL_DRAFT_REQUIRES_EXPLICIT_TAG_MESSAGE
    
    tag_intents = {t.intent for t in tags if getattr(t, "executable", False)}
    
    if tool_name == "append_report_draft":
        if tag_intents & {"begin", "continue"}:
            return None
        if keyword_intent in {"begin", "continue"}:
            self._record_tagless_fallback_event(
                project_id, fallback_tool="append_report_draft",
                fallback_intent=keyword_intent,
            )
            logging.warning("draft_write without explicit tag, fallback path used")
            return None
        return self.CANONICAL_DRAFT_REQUIRES_EXPLICIT_TAG_MESSAGE
    
    if tool_name == "edit_file":
        if tag_intents & {"section", "replace"}:
            return None
        return self.CANONICAL_DRAFT_REQUIRES_EXPLICIT_TAG_MESSAGE
    
    return None


def _record_tagless_fallback_event(
    self,
    project_id: str,
    *,
    fallback_tool: str,
    fallback_intent: str,
) -> None:
    timestamp = datetime.now().isoformat(timespec="seconds")
    def mutate(state: Dict):
        state.setdefault("events", []).append({
            "type": "tagless_draft_fallback",
            "fallback_tool": fallback_tool,
            "fallback_intent": fallback_intent,
            "recorded_at": timestamp,
        })
        return state
    self._mutate_conversation_state(project_id, mutate)
```

**集成 gate（v2 单一插入点）**：

避免 codex round-1 指出的"`write_file` / `edit_file` 都走 `_execute_plan_write`，`append_report_draft` 又内部再调 `_execute_plan_write`"导致双重拦截或漏拦截，把 gate 集成到 **`_validate_required_report_draft_prewrite`** 单一入口（[chat.py:4715-4744](../../backend/chat.py:4715)，所有 canonical draft 写都汇合到这）。

在 `_validate_required_report_draft_prewrite` 早期（在现有 destructive_write_error 校验之前）加：

```python
def _validate_required_report_draft_prewrite(
    self, project_id, normalized_path, content, *,
    source_tool_name, source_tool_args, ...
):
    # v2 新增：先过 gate
    if self._is_canonical_report_draft_path(normalized_path):
        gate_block = self._gate_canonical_draft_tool_call(
            project_id, source_tool_name, source_tool_args or {},
            decision=self._turn_context.get("canonical_draft_decision") or {},
            tags=self._turn_context.get("draft_action_events") or [],
        )
        if gate_block:
            self._emit_system_notice_once(
                category="non_plan_write_blocked",
                path=normalized_path, reason=gate_block,
                user_action="请按 SKILL.md 附录的 draft-action 标签规范操作",
                surface_to_user=True,
            )
            return gate_block  # caller 检测 truthy 返回 → reject
    # ... 现有 destructive_write_error 校验逻辑保留
```

**注**：`source_tool_name` 在 `_validate_required_report_draft_prewrite` 已经是参数；`source_tool_args` 也现成。`_execute_append_report_draft` 内部最终也走这条路径——不需要在 `_execute_append_report_draft` 单独再加 gate，避免双重检查。

- [ ] **Step 4: Run tests pass + commit**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::GateCanonicalDraftToolCallTests -v
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "feat(draft-action): tool gate + tagless fallback (B1)"
```

---

### Task 20: B1 — `draft_decision_compare` event 写入 + exception event（Phase 2a 新旧并行）

**Spec:** §Rollout Step 2a (schema)

**v2 关键修订（codex round-1 plan review P0）**：Phase 2a 期间**新旧函数都活着**——旧 `_classify_canonical_draft_turn` 主导决策（保留所有现有 caller），新 `_preflight_canonical_draft_check`（Task 17 新增）仅供本 task 调用做 compare。

**Files:**
- Modify: `backend/chat.py` (new helpers + integrate into 入口)
- Test: `tests/test_chat_runtime.py`

- [ ] **Step 1: Write failing tests**

```python
class DraftDecisionCompareEventTests(ChatRuntimeTests):
    def test_compare_event_written_per_turn(self):
        """跑一个常规 turn，conversation_state 应含一条 draft_decision_compare 事件。"""
        handler = self._make_handler_with_project()
        # 触发 _record_draft_decision_compare_event 直接调用（不走完整 turn）
        handler._record_draft_decision_compare_event(
            self.project_id,
            turn_id="t1", user_message="开始写报告吧",
            old_decision={"mode": "no_write", "priority": "P10"},
            new_decision={"mode": "require", "priority": "P_PREFLIGHT_OK",
                          "preflight_keyword_intent": "begin"},
            tags=[],
            fallback_used=False, fallback_tool=None, fallback_intent=None,
            blocked_missing_tag=False, blocked_tool=None,
            new_channel_exception=None,
        )
        state = handler._load_conversation_state(self.project_id, [])
        events = [e for e in state.get("events", []) if e.get("type") == "draft_decision_compare"]
        self.assertEqual(len(events), 1)
        e = events[-1]
        for key in ("turn_id", "user_message_hash", "old_decision", "new_decision",
                    "agreement", "divergence_reason", "tag_present", "fallback_used",
                    "fallback_tool", "fallback_intent", "blocked_missing_tag",
                    "blocked_tool", "new_channel_exception", "recorded_at"):
            self.assertIn(key, e)

    def test_compare_agreement_correctly_computed(self):
        handler = self._make_handler_with_project()
        handler._record_draft_decision_compare_event(
            self.project_id, turn_id="t1", user_message="x",
            old_decision={"mode": "no_write"},
            new_decision={"mode": "no_write"},
            tags=[], fallback_used=False, fallback_tool=None, fallback_intent=None,
            blocked_missing_tag=False, blocked_tool=None, new_channel_exception=None,
        )
        state = handler._load_conversation_state(self.project_id, [])
        e = state["events"][-1]
        self.assertTrue(e["agreement"])
        self.assertIsNone(e["divergence_reason"])

    def test_compare_disagreement_records_divergence(self):
        handler = self._make_handler_with_project()
        handler._record_draft_decision_compare_event(
            self.project_id, turn_id="t1", user_message="x",
            old_decision={"mode": "no_write"},
            new_decision={"mode": "require"},
            tags=[], fallback_used=False, fallback_tool=None, fallback_intent=None,
            blocked_missing_tag=False, blocked_tool=None, new_channel_exception=None,
        )
        state = handler._load_conversation_state(self.project_id, [])
        e = state["events"][-1]
        self.assertFalse(e["agreement"])
        self.assertIn("no_write", e["divergence_reason"])

    def test_exception_event_written_when_new_channel_crashes(self):
        handler = self._make_handler_with_project()
        handler._record_draft_decision_exception_event(
            self.project_id, turn_id="t2", stage="preflight",
            exception_class="ValueError", exception_message="test",
        )
        state = handler._load_conversation_state(self.project_id, [])
        events = [e for e in state["events"] if e.get("type") == "draft_decision_exception"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["stage"], "preflight")
        self.assertEqual(events[0]["exception_class"], "ValueError")

    def test_compare_includes_tag_present_per_intent(self):
        from backend.draft_action import DraftActionEvent
        handler = self._make_handler_with_project()
        tags = [
            DraftActionEvent(raw="...", intent="begin", executable=True),
            DraftActionEvent(raw="...", intent="section", executable=False),
        ]
        handler._record_draft_decision_compare_event(
            self.project_id, turn_id="t3", user_message="x",
            old_decision={"mode": "require"}, new_decision={"mode": "require"},
            tags=tags,
            fallback_used=False, fallback_tool=None, fallback_intent=None,
            blocked_missing_tag=False, blocked_tool=None, new_channel_exception=None,
        )
        state = handler._load_conversation_state(self.project_id, [])
        tp = state["events"][-1]["tag_present"]
        self.assertTrue(tp["begin"])
        self.assertFalse(tp["section"])  # executable=False 不算
```

- [ ] **Step 2-4: Implement event writer + integrate**

Add to `backend/chat.py`:

```python
def _record_draft_decision_compare_event(
    self,
    project_id: str,
    *,
    turn_id: str,
    user_message: str,
    old_decision: dict,
    new_decision: dict,
    tags: list,
    fallback_used: bool,
    fallback_tool: str | None,
    fallback_intent: str | None,
    blocked_missing_tag: bool,
    blocked_tool: str | None,
    new_channel_exception: dict | None,
) -> None:
    import hashlib
    user_hash = hashlib.sha1(user_message.encode("utf-8")).hexdigest()
    agreement = old_decision.get("mode") == new_decision.get("mode")
    timestamp = datetime.now().isoformat(timespec="seconds")
    tag_present = {
        "begin": any(t.intent == "begin" and t.executable for t in tags),
        "continue": any(t.intent == "continue" and t.executable for t in tags),
        "section": any(t.intent == "section" and t.executable for t in tags),
        "replace": any(t.intent == "replace" and t.executable for t in tags),
    }
    
    def mutate(state: Dict):
        state.setdefault("events", []).append({
            "type": "draft_decision_compare",
            "turn_id": turn_id,
            "user_message_hash": user_hash,
            "old_decision": old_decision,
            "new_decision": new_decision,
            "agreement": agreement,
            "divergence_reason": None if agreement else f"old.mode={old_decision.get('mode')}, new.mode={new_decision.get('mode')}",
            "tag_present": tag_present,
            "fallback_used": fallback_used,
            "fallback_tool": fallback_tool,
            "fallback_intent": fallback_intent,
            "blocked_missing_tag": blocked_missing_tag,
            "blocked_tool": blocked_tool,
            "new_channel_exception": new_channel_exception,
            "recorded_at": timestamp,
        })
        return state
    self._mutate_conversation_state(project_id, mutate)


def _record_draft_decision_exception_event(
    self,
    project_id: str,
    *,
    turn_id: str | None,
    stage: str,  # "preflight" / "parser" / "gate" / "side_effect"
    exception_class: str,
    exception_message: str,
) -> None:
    timestamp = datetime.now().isoformat(timespec="seconds")
    def mutate(state: Dict):
        state.setdefault("events", []).append({
            "type": "draft_decision_exception",
            "turn_id": turn_id,
            "stage": stage,
            "exception_class": exception_class,
            "exception_message": exception_message[:500],
            "recorded_at": timestamp,
        })
        return state
    self._mutate_conversation_state(project_id, mutate)
```

**集成（v3 修订 — codex round-2 plan review P1 闭环）**：

时序约束：
- 旧 `_classify_canonical_draft_turn` 在 [chat.py:6042-6048](../../backend/chat.py:6042) 由 `_build_turn_context` 调用并写入 `_turn_context["canonical_draft_decision"]`
- `_gate_canonical_draft_tool_call` 在 turn 内每次写 canonical draft 时调用，触发时写 `tagless_draft_fallback` event 到 `conversation_state.json`
- `_finalize_assistant_turn` 在 turn 结束时执行
- compare writer 必须**在 turn 完全结束后**调，才能读到完整的 fallback / blocked 状态

**接入点（v3 修订 — codex round-3 plan review P1）**：必须覆盖**全部** `_build_turn_context` → `_finalize_assistant_turn` 路径，否则 immediate reject / immediate guidance 早退 turn 的 compare 数据会被系统性漏记。

实际有 **3 个** caller chain（全部落 `_run_phase2a_compare_writer(project_id, user_message)`）：

| caller chain | 文件:行 | 触发场景 | compare writer 接入位置 |
|---|---|---|---|
| `_chat_stream_unlocked` (generator) | [chat.py:3417](../../backend/chat.py:3417) | 正常流式 turn | `_finalize_assistant_turn` 之后、最后 yield 之前 |
| `_chat_unlocked` (普通函数) | [chat.py:3675](../../backend/chat.py:3675) | 正常非流式 turn | `_finalize_assistant_turn` 之后、return 之前 |
| `_finalize_early_assistant_message` (普通函数) | [chat.py:6342-6370](../../backend/chat.py:6342) | immediate reject / immediate guidance 早退（如 stage gate 拒绝、preflight reject）；上游 `_chat_stream_unlocked:3053-3077` 和 `_chat_unlocked:3468-3483` 调用 | `_finalize_assistant_turn` 之后、return 之前 |

**特别强调第 3 条**：早退 turn 同样跑了 `_build_turn_context` → 同样调用了旧 `_classify_canonical_draft_turn` → `_turn_context["canonical_draft_decision"]` 已经填好。如果不在这条路径补 caller，cutover artifact 会漏掉**最该观察的拒绝路径**（preflight reject 是新旧通道决策最容易分歧的场景）。

实施：每条 caller chain 在 `_finalize_assistant_turn(...)` 调用紧后追加：

```python
try:
    self._run_phase2a_compare_writer(project_id, user_message_text)
except Exception:
    pass  # silent: compare writer must not affect real turn
```

`user_message_text` 来源因 caller 而异（v5 修订 — codex round-4 P1）：

| caller | `user_message_text` 来源 |
|---|---|
| `_chat_stream_unlocked` | 已有入参 `user_message` 局部变量；`user_message_text = user_message` |
| `_chat_unlocked` | 已有入参 `user_message` 局部变量；同上 |
| `_finalize_early_assistant_message` | 真实签名是 `(project_id, history, current_user_message, assistant_message)`（[chat.py:6342](../../backend/chat.py:6342)），**没有** `user_message` 形参。**v6 修订**：必须抽出真正的文本部分，不能 `str(list)`——因为 `_run_phase2a_compare_writer` 会把 `user_message_text` 传给 `_preflight_canonical_draft_check` 算 `new_decision`，list repr 会污染 preflight 决策结果。用下面 `_extract_user_message_text` helper 派生 |

**`_extract_user_message_text` helper（v6 新增）**——加到 `backend/chat.py`，让所有需要从 user message dict 拿 plain text 的地方共用：

```python
def _extract_user_message_text(self, message: dict | None) -> str:
    """从 persisted user message 拿 LLM 看到的文本。
    multipart content array 会抽出所有 type='text' 部分拼接，跳过 image_url 等。
    """
    if not message:
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
        return "\n\n".join(parts)
    return ""
```

`_finalize_early_assistant_message` 接入：

```python
user_message_text = self._extract_user_message_text(current_user_message)
try:
    self._run_phase2a_compare_writer(project_id, user_message_text)
except Exception:
    pass
```

测试覆盖（加到 `tests/test_chat_runtime.py`）：

```python
class ExtractUserMessageTextTests(ChatRuntimeTests):
    def test_str_content_returns_as_is(self):
        handler = self._make_handler_with_project()
        self.assertEqual(handler._extract_user_message_text({"content": "plain"}), "plain")

    def test_multipart_extracts_text_parts_only(self):
        handler = self._make_handler_with_project()
        msg = {"content": [
            {"type": "text", "text": "first"},
            {"type": "image_url", "image_url": {"url": "data:..."}},
            {"type": "text", "text": "second"},
        ]}
        result = handler._extract_user_message_text(msg)
        self.assertEqual(result, "first\n\nsecond")

    def test_none_message_returns_empty(self):
        handler = self._make_handler_with_project()
        self.assertEqual(handler._extract_user_message_text(None), "")

    def test_image_only_multipart_returns_empty(self):
        handler = self._make_handler_with_project()
        msg = {"content": [{"type": "image_url", "image_url": {"url": "..."}}]}
        self.assertEqual(handler._extract_user_message_text(msg), "")
```

**信号源（明确）**：
- `old_decision` 来自 `_turn_context["canonical_draft_decision"]`（已被旧 `_classify_canonical_draft_turn` 在 turn 开始时写入）
- `new_decision` 实时调 `_preflight_canonical_draft_check` 算
- `fallback_used` / `fallback_tool` / `fallback_intent` 从 `conversation_state.json` 的本 turn `tagless_draft_fallback` event 反查（Task 19 `_record_tagless_fallback_event` 已写入）
- `blocked_missing_tag` / `blocked_tool` 同理，从本 turn `draft_gate_block` event 反查（Task 19 `_gate_canonical_draft_tool_call` block 时新增 `_record_draft_gate_block_event` 写入）
- `tags` 从 `_turn_context["draft_action_events"]` 取（Task 18 `_apply_draft_action_event` 写入）

**Task 19 加 `_record_draft_gate_block_event`（v3 补）**：

```python
# 在 _gate_canonical_draft_tool_call 的 block 路径加：
def _gate_canonical_draft_tool_call(self, ...):
    # ... 上面 begin/continue/section/replace 校验逻辑
    # 任意 block 路径（return non-None block_reason 之前）：
    if block_reason:
        self._record_draft_gate_block_event(
            project_id, tool_name=tool_name, reason=block_reason,
        )
        return block_reason
    return None

def _record_draft_gate_block_event(
    self, project_id: str, *, tool_name: str, reason: str,
) -> None:
    timestamp = datetime.now().isoformat(timespec="seconds")
    def mutate(state: Dict):
        state.setdefault("events", []).append({
            "type": "draft_gate_block",
            "tool_name": tool_name,
            "reason": reason[:200],
            "recorded_at": timestamp,
        })
        return state
    self._mutate_conversation_state(project_id, mutate)
```

**Task 20 compare writer（v3 完整版）**：

```python
import uuid

def _run_phase2a_compare_writer(self, project_id: str, user_message: str):
    """Phase 2a 灰度：在 turn 完全结束后调用。
    所有 fallback / block 状态都从 conversation_state event 反查（本 turn 内事件）。
    所有 exception silent — 永远不让 compare writer 影响真实 turn。"""
    turn_id = str(uuid.uuid4())
    old_decision = self._turn_context.get("canonical_draft_decision") or {}
    
    new_decision = {}
    new_channel_exception = None
    
    try:
        new_decision = self._preflight_canonical_draft_check(project_id, user_message)
    except Exception as e:
        new_channel_exception = {"stage": "preflight", "message": str(e)[:200]}
        try:
            self._record_draft_decision_exception_event(
                project_id, turn_id=turn_id, stage="preflight",
                exception_class=type(e).__name__, exception_message=str(e),
            )
        except Exception:
            pass  # silent: compare writer must not affect turn (intentional swallow)
    
    # 反查本 turn 的 fallback / block 事件
    # 用一个简单 turn-window 标记：compare writer 只数本 turn 内的 event
    # 简化：调用前记录 events 长度，事件就是这之后追加的
    fallback_used = False
    fallback_tool = None
    fallback_intent = None
    blocked_missing_tag = False
    blocked_tool = None
    
    try:
        state = self._load_conversation_state(project_id, [])
        # 反查本 turn 内事件——按 recorded_at 时间戳取最近的（简化）
        # 更稳妥：在 turn 开始记 baseline_event_count，结束反查 baseline 之后追加的
        baseline = self._turn_context.get("compare_baseline_event_count", 0)
        new_events = (state.get("events") or [])[baseline:]
        for ev in new_events:
            if ev.get("type") == "tagless_draft_fallback":
                fallback_used = True
                fallback_tool = ev.get("fallback_tool")
                fallback_intent = ev.get("fallback_intent")
            elif ev.get("type") == "draft_gate_block":
                blocked_missing_tag = True
                blocked_tool = ev.get("tool_name")
    except Exception:
        pass  # silent: state load failure must not affect turn
    
    tags = self._turn_context.get("draft_action_events") or []
    
    try:
        self._record_draft_decision_compare_event(
            project_id, turn_id=turn_id, user_message=user_message,
            old_decision=old_decision, new_decision=new_decision,
            tags=tags,
            fallback_used=fallback_used, fallback_tool=fallback_tool, fallback_intent=fallback_intent,
            blocked_missing_tag=blocked_missing_tag, blocked_tool=blocked_tool,
            new_channel_exception=new_channel_exception,
        )
    except Exception:
        pass  # silent: compare event write failure must not affect turn
```

**Turn baseline 追踪（v3 补）**：在 `_new_turn_context` 加一行 `"compare_baseline_event_count": 0`，并在 `_build_turn_context` 末尾（旧 `_classify_canonical_draft_turn` 调用之后）记下当前 event count：

```python
def _build_turn_context(self, project_id, user_message):
    # ... 现有逻辑（包括 _classify_canonical_draft_turn）
    # v3 新增：记 baseline 供 compare writer 反查
    state = self._load_conversation_state(project_id, [])
    self._turn_context["compare_baseline_event_count"] = len(state.get("events") or [])
    return self._turn_context
```

**Phase 2b（Task 24）切主时**：删除 `_run_phase2a_compare_writer` / `_record_draft_gate_block_event` / baseline 追踪 / caller。`_record_draft_decision_compare_event` schema 保留供历史查询。

- [ ] **Step 5: Commit**

```powershell
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "feat(rollout): structured draft_decision_compare + exception events (Phase 2a)"
```

---

### Task 21: B1 — `tools/draft_decision_compare_report.py` 脚本 + smoke test

**Spec:** §Rollout Step 2a (item 6)

**Files:**
- Create: `tools/draft_decision_compare_report.py`
- Create: `tests/test_draft_decision_compare_report.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_draft_decision_compare_report.py`:

```python
import json
import unittest
import tempfile
from pathlib import Path
from tools.draft_decision_compare_report import generate_report

class CompareReportSmokeTests(unittest.TestCase):
    def test_minimal_fixture_outputs_markdown_with_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state = {
                "events": [
                    {"type": "draft_decision_compare",
                     "turn_id": "t1", "user_message_hash": "abc",
                     "old_decision": {"mode": "no_write"},
                     "new_decision": {"mode": "no_write", "preflight_keyword_intent": None},
                     "agreement": True, "divergence_reason": None,
                     "tag_present": {"begin": False, "continue": False, "section": False, "replace": False},
                     "fallback_used": False, "fallback_tool": None, "fallback_intent": None,
                     "blocked_missing_tag": False, "blocked_tool": None,
                     "new_channel_exception": None,
                     "recorded_at": "2026-05-04T00:00:00"},
                    # 一致 case，1 条
                    # 不一致 case
                    {"type": "draft_decision_compare", "turn_id": "t2",
                     "user_message_hash": "def",
                     "old_decision": {"mode": "no_write"},
                     "new_decision": {"mode": "require"},
                     "agreement": False, "divergence_reason": "old.mode=no_write, new.mode=require",
                     "tag_present": {"begin": True, "continue": False, "section": False, "replace": False},
                     "fallback_used": False, "fallback_tool": None, "fallback_intent": None,
                     "blocked_missing_tag": False, "blocked_tool": None,
                     "new_channel_exception": None,
                     "recorded_at": "2026-05-04T00:01:00"},
                    # missing-tag case
                    {"type": "draft_decision_compare", "turn_id": "t3",
                     "user_message_hash": "ghi",
                     "old_decision": {"mode": "require"},
                     "new_decision": {"mode": "require"},
                     "agreement": True, "divergence_reason": None,
                     "tag_present": {"begin": False, "continue": False, "section": False, "replace": False},
                     "fallback_used": False, "fallback_tool": None, "fallback_intent": None,
                     "blocked_missing_tag": True, "blocked_tool": "edit_file",
                     "new_channel_exception": None,
                     "recorded_at": "2026-05-04T00:02:00"},
                    # exception case
                    {"type": "draft_decision_exception", "turn_id": "t4",
                     "stage": "preflight",
                     "exception_class": "ValueError",
                     "exception_message": "test",
                     "recorded_at": "2026-05-04T00:03:00"},
                ]
            }
            state_path.write_text(json.dumps(state), encoding="utf-8")
            
            md = generate_report([state_path])
            
            # 五个 cutover 指标都能从 md 直接读出
            self.assertIn("一致率", md)
            self.assertIn("67%", md)  # 2/3 = 66.67%，{:.0f} 四舍五入到 67%
            self.assertIn("不一致 case", md)
            self.assertIn("blocked_missing_tag", md)
            self.assertIn("异常数", md)
            # 1 missing-tag + 1 exception
            self.assertEqual(md.count("✗"), 2)  # 至少 missing-tag 和 exception 行有 ✗
```

- [ ] **Step 2: Run to verify fail**

- [ ] **Step 3: Create tools/ directory if missing**

```powershell
if (-not (Test-Path tools)) { New-Item -ItemType Directory -Path tools | Out-Null }
if (-not (Test-Path tools\__init__.py)) { New-Item -ItemType File -Path tools\__init__.py | Out-Null }
```

`__init__.py` 让 `from tools.draft_decision_compare_report import generate_report` 测试导入可用。

- [ ] **Step 4: Implement script**

Create `tools/draft_decision_compare_report.py`:

```python
"""Generate Phase 2a cutover review markdown from conversation_state.json files.

Usage: python tools/draft_decision_compare_report.py state1.json state2.json ...
"""
import json
import sys
from pathlib import Path


def load_events(state_paths: list[Path]) -> list[dict]:
    events = []
    for p in state_paths:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            events.extend(data.get("events", []))
        except Exception as e:
            print(f"[warn] {p}: {e}", file=sys.stderr)
    return events


def generate_report(state_paths: list[Path]) -> str:
    events = load_events(state_paths)
    compare_events = [e for e in events if e.get("type") == "draft_decision_compare"]
    exception_events = [e for e in events if e.get("type") == "draft_decision_exception"]
    
    total = len(compare_events)
    if total == 0:
        return "# Cutover Review\n\n**没有 compare 事件**——是否启用了 Phase 2a 并行？"
    
    agreed = sum(1 for e in compare_events if e.get("agreement"))
    disagreed = total - agreed
    fallback_count = sum(1 for e in compare_events if e.get("fallback_used"))
    blocked_missing = sum(1 for e in compare_events if e.get("blocked_missing_tag"))
    inline_exceptions = sum(1 for e in compare_events if e.get("new_channel_exception"))
    standalone_exceptions = len(exception_events)
    total_exceptions = inline_exceptions + standalone_exceptions
    
    agreement_rate = (agreed / total) * 100 if total else 0
    
    lines = [
        "# Phase 2a Cutover Review",
        "",
        f"**总轮数**: {total}",
        f"**决策一致率**: {agreement_rate:.0f}% ({agreed}/{total})",
        "",
        "## Cutover Metrics",
        "",
        f"| 指标 | 值 | 阈值 | 通过？ |",
        f"|---|---|---|---|",
        f"| 一致率 | {agreement_rate:.0f}% | ≥ 95% | {'✓' if agreement_rate >= 95 else '✗'} |",
        f"| 不一致 case | {disagreed} | 全部需人工标注 | (人工 review) |",
        f"| blocked_missing_tag turn | {blocked_missing} | 0 | {'✓' if blocked_missing == 0 else '✗'} |",
        f"| 受控 fallback case (append_report_draft) | {fallback_count} | (受控范畴，不计入 missing) | - |",
        f"| 异常数（new_channel_exception + draft_decision_exception） | {total_exceptions} | 0 | {'✓' if total_exceptions == 0 else '✗'} |",
        "",
        "## 不一致 case 详情",
        "",
    ]
    for e in compare_events:
        if not e.get("agreement"):
            lines.append(f"- turn_id={e.get('turn_id')} | hash={e.get('user_message_hash')[:8]} | old={e.get('old_decision', {}).get('mode')} → new={e.get('new_decision', {}).get('mode')} | reason={e.get('divergence_reason')}")
    
    lines.extend([
        "",
        "## Exception 详情",
        "",
    ])
    for e in compare_events:
        if e.get("new_channel_exception"):
            ex = e["new_channel_exception"]
            lines.append(f"- inline | turn={e.get('turn_id')} | stage={ex.get('stage')} | {ex.get('message')[:80]}")
    for e in exception_events:
        lines.append(f"- standalone | turn={e.get('turn_id')} | stage={e.get('stage')} | {e.get('exception_class')}: {e.get('exception_message')[:80]}")
    
    return "\n".join(lines)


if __name__ == "__main__":
    paths = [Path(a) for a in sys.argv[1:]]
    if not paths:
        print("Usage: python tools/draft_decision_compare_report.py state1.json [state2.json ...]")
        sys.exit(1)
    print(generate_report(paths))
```

- [ ] **Step 5: Run tests + commit**

```powershell
.venv\Scripts\python -m pytest tests/test_draft_decision_compare_report.py -v
git add tools/__init__.py tools/draft_decision_compare_report.py tests/test_draft_decision_compare_report.py
git commit -m "feat(rollout): cutover review report script (Phase 2a)"
```

---

### Task 22: B1 — SKILL.md §S4 + 附录加 draft-action 规范

**Spec:** §4.11

**Files:**
- Modify: `skill/SKILL.md`
- Modify: `tests/test_packaging_docs.py` (lock new key sentences)

- [ ] **Step 1: 读现有 SKILL.md 找插入点**

```powershell
Select-String -Path skill\SKILL.md -Pattern "S4 报告撰写" -Context 0,5
```

- [ ] **Step 2: 插入 §S4 子节**

在 `skill/SKILL.md` 现有 `### S4 报告撰写` 节末尾追加：

```md
### S4 正文写作标签（draft-action）

当用户表达想要起草/续写/修改正文时（如"开始写报告""继续写""把第二章重写""把 X 改成 Y"），
你在调用 `append_report_draft` / `edit_file` **之前**必须先在回复中输出对应 draft-action tag：

| 用户意图 | 你发的 tag |
|---|---|
| 想看正文初稿 / "开始写报告" / "起草" | `<draft-action>begin</draft-action>` |
| 想继续 / 续写 / 写下一段或下一章 | `<draft-action>continue</draft-action>` |
| 想重写某一节（如"第二章重写"） | `<draft-action>section:第二章 战力演化</draft-action>`（用完整 heading 定位） |
| 想替换具体文字（如"把 X 改成 Y"） | `<draft-action-replace><old>X</old><new>Y</new></draft-action-replace>` |

tag 必须独立一行、在回复尾部、代码块外。系统检测到合法 tag 后才会放行写正文工具。
不发 tag 直接调写正文工具会被拒绝。

模型不需要遍历用户中文表达——只要能从用户消息中识别出"用户希望我做正文动作"，就发对应 tag；
不确定时不发 tag，先问用户澄清。

如果只调工具不输出文本（少见情况），系统会按本轮 preflight 推断的意图兜底放行
（仅 `append_report_draft` begin/continue 类，不放行 `edit_file`）。
```

- [ ] **Step 3: 插入附录**

在 `skill/SKILL.md` 末尾（在 stage-ack 附录后）追加：

```md
## 附录：draft-action 标签规范

阶段进入 S4 后，发起正文动作前的控制信号。

**Simple 形式**（intent ∈ {begin, continue, section}）：

​```
<draft-action>begin</draft-action>
<draft-action>continue</draft-action>
<draft-action>section:第二章 战力演化</draft-action>
​```

**Replace 形式**（嵌套 XML 子节点）：

​```
<draft-action-replace>
  <old>原文片段</old>
  <new>新文本</new>
</draft-action-replace>
​```

**KEY 取值**：
- `begin` — 模型即将首次调用 `append_report_draft` 创建草稿
- `continue` — 模型即将调用 `append_report_draft` 在现有草稿末尾追加（draft 不存在自动降级为 begin）
- `section:LABEL` — 模型即将调用 `edit_file` 重写指定章节（LABEL 必须能在 draft 中唯一找到 heading）
- `replace` — 模型即将调用 `edit_file` 做精确替换（OLD 必须在 draft 中唯一存在）

**位置 / 剥离规则**：完全沿用 stage-ack 附录的同款约束（必须在回复尾部、独立一行、代码块外）。replace 多行 block 要求"起始行独立 + 终止行独立"。
```

注：上面 ```` ``` ```` 在实际写入时是真实的 markdown fence，不是转义。

- [ ] **Step 3: Update test_packaging_docs.py**

锁定新增关键句（如 `"<draft-action>begin</draft-action>"`、`"draft-action 标签规范"`），防止打包后文案被改。

- [ ] **Step 4: Run packaging docs test + commit**

```powershell
.venv\Scripts\python -m pytest tests/test_packaging_docs.py -v
git add skill/SKILL.md tests/test_packaging_docs.py
git commit -m "docs(skill): add draft-action tag spec to §S4 (B1)"
```

---

### Task 23: Phase 2a reality_test 跑 5 会话 + cutover artifact + 人工 review

**Spec:** §Rollout Step 2a 切主条件

- [ ] **Step 1: 重打包 + 启动**

```powershell
.\build.bat
```

- [ ] **Step 2: 跑 5 个真实会话**

按 spec §Rollout Step 2a 第 7 项触发场景：
- 会话 A：用户"开始写报告吧"（begin）
- 会话 B：用户"继续写第二章"（section）
- 会话 C：用户"把 X 改成 Y"（replace）
- 会话 D：用户"继续写"（continue）
- 会话 E：S0/S1 阶段用户"开始写报告吧"（误触发拒绝路径）

每个会话跑到 S4 阶段产出一些动作。

- [ ] **Step 3: 收集 conversation_state.json + 跑 report 脚本（产出到目标位置）**

直接把脚本输出到 `docs/superpowers/` 下，避免后续移动：

```powershell
$today = Get-Date -Format "yyyy-MM-dd"
$report_path = "docs/superpowers/cutover_report_$today.md"
python tools/draft_decision_compare_report.py reality_test/.consulting-report/conversation_state.json > $report_path
```

- [ ] **Step 4: 人工 review 报告文件**

打开 `docs/superpowers/cutover_report_<today>.md`。确认五条 cutover 指标全过：
- 一致率 ≥ 95%
- 不一致 case 标注 "new better" / "tie"，0 个 "old better"
- blocked_missing_tag = 0
- 受控 fallback case 都是 `append_report_draft`
- 异常数 = 0

如有不一致 case 但都是 "new better" → 通过；否则停下来 debug。

- [ ] **Step 5: Commit cutover_report 到 docs/superpowers/**

```powershell
git add docs/superpowers/cutover_report_*.md
git commit -m "docs(rollout): Phase 2a cutover artifact pass review (5 sessions, agreement >= 95%)"
```

---

### Task 24: Phase 2 Step 2b — 删除清单（grep 验证 + 删 _classify 细分 + 常量 + dead helper）

**Spec:** §4.10 删除范围

**Files:**
- Modify: `backend/chat.py`

- [ ] **Step 1: grep verify（v2 加 REPORT_BODY_REPLACE_TEXT_INTENT_RE）**

```powershell
$constants = @(
  "REPORT_BODY_FIRST_DRAFT_KEYWORDS",
  "REPORT_BODY_EXPLICIT_CONTINUATION_KEYWORDS",
  "REPORT_BODY_WHOLE_REWRITE_KEYWORDS",
  "REPORT_BODY_CHAPTER_WRITE_RE",
  "REPORT_BODY_INLINE_EDIT_RE",
  "REPORT_BODY_REPLACE_TEXT_INTENT_RE"  # v2 补
)
foreach ($c in $constants) {
  Write-Host "=== $c ==="
  Select-String -Path backend\chat.py -Pattern $c
}
$helpers = @(
  "_regex_has_clean_report_body_intent",
  "_has_explicit_report_body_write_intent",
  "_parse_report_body_replacement_intent"  # v2 补——使用 REPORT_BODY_REPLACE_TEXT_INTENT_RE
)
foreach ($h in $helpers) {
  Write-Host "=== $h ==="
  Select-String -Path backend\chat.py -Pattern $h
}
$old_classifier = "_classify_canonical_draft_turn"
Write-Host "=== $old_classifier ==="
Select-String -Path backend\chat.py -Pattern $old_classifier
```

确认：
- 所有常量/helper 引用都在删除范围内
- `_classify_canonical_draft_turn` 的 caller 也要列出（Task 17 v2 没改任何 caller）

- [ ] **Step 2: 切换 caller — 把 `_classify_canonical_draft_turn` 替换为 `_preflight_canonical_draft_check`**

`grep -n "_classify_canonical_draft_turn(" backend/chat.py` 找到所有 caller（包括 `_should_allow_non_plan_write` 等），逐一改：

```python
# 旧
decision = self._classify_canonical_draft_turn(project_id, user_message)

# 新
decision = self._preflight_canonical_draft_check(project_id, user_message)
```

注意：`_preflight_canonical_draft_check` 输出的 dict 字段名集合**是** `_classify_canonical_draft_turn` 字段集合的子集（preflight 不再产 begin/continue/section/replace 细分字段，但格式兼容）。下游 `.get(...)` 路径自动得到 `None`，行为退化为"无细分"——按本 spec 设计，细分由 draft-action tag 决定，下游不该再读 `intent_kind` 等字段。

如下游有读 `decision["intent_kind"]` 等字段做精细分支的 caller（如 [chat.py:2556-2584, 4751-4820](../../backend/chat.py:2556) 提到的 prewrite validator），把这些精细分支也清理：当 `intent_kind` 为 None 时按"通用 canonical draft 写"处理（不区分 section/replace/begin/continue）。Task 19 的 `_gate_canonical_draft_tool_call` 已经接管了细分约束。

- [ ] **Step 3: Delete constants + helpers + 旧函数 + Phase 2a compare writer**

按 spec §4.10 删：
- 常量：`REPORT_BODY_FIRST_DRAFT_KEYWORDS`、`REPORT_BODY_EXPLICIT_CONTINUATION_KEYWORDS`、`REPORT_BODY_WHOLE_REWRITE_KEYWORDS`、`REPORT_BODY_CHAPTER_WRITE_RE`、`REPORT_BODY_INLINE_EDIT_RE`、**`REPORT_BODY_REPLACE_TEXT_INTENT_RE`**（v2 补）
- helper：`_regex_has_clean_report_body_intent`、`_has_explicit_report_body_write_intent`、**`_parse_report_body_replacement_intent`**（v2 补，使用 REPLACE_TEXT_INTENT_RE 的）
- 旧函数：`_classify_canonical_draft_turn` 整个删除
- Phase 2a 灰度代码（v5 补全清单 / v6 修订 spec 边界）：
  - `_run_phase2a_compare_writer` 函数本体
  - 三处 `_run_phase2a_compare_writer(...)` caller（[chat.py:3417](../../backend/chat.py:3417) `_chat_stream_unlocked`、[chat.py:3675](../../backend/chat.py:3675) `_chat_unlocked`、[chat.py:6342](../../backend/chat.py:6342) `_finalize_early_assistant_message`）
  - `_record_draft_gate_block_event` 函数（仅被 compare writer 反查 `draft_gate_block` event 消费）+ `_gate_canonical_draft_tool_call` 内部对它的调用
  - `compare_baseline_event_count` 字段从 `_new_turn_context` 删除；`_build_turn_context` 末尾"记 baseline" 那段代码删除

**保留（v6 修订 — spec §956-962 明确要求）**：
- `_record_tagless_fallback_event` 函数本体 + `_gate_canonical_draft_tool_call` 内对它的调用 — spec 明确说"独立事件保留供细粒度调试/未来分析"，cutover 指标不再依赖它，但事件本身仍要写。删除会破坏 spec 保留的 fallback 观测能力

保留：
- `NON_PLAN_WRITE_ALLOW_KEYWORDS` / `NON_PLAN_WRITE_FOLLOW_UP_KEYWORDS`
- `REPORT_BODY_SECTION_REWRITE_KEYWORDS`
- `_make_canonical_draft_decision` / `_apply_stage_gate_to_canonical_draft_decision` / `_empty_canonical_draft_decision`
- `CANONICAL_DRAFT_*_MESSAGE` 系列常量
- `_record_draft_decision_compare_event` / `_record_draft_decision_exception_event`（schema 保留供历史查询；不再有新事件写入）

- [ ] **Step 4: Run full suite**

```powershell
.venv\Scripts\python -m pytest tests/ -v 2>&1 | Select-String -Pattern "FAIL"
```

Expected: 全 pass。如有 fail，往往是测试还在 import/调旧函数——一并改。

- [ ] **Step 5: Commit**

```powershell
git add backend/chat.py
git commit -m "refactor(preflight): switch caller + remove old classifier + dead helpers (Phase 2b)"
```

---

### Task 25: 下游引用调整 + 回归测试

- [ ] **Step 1: grep 残留引用（v2 补全 7 项）**

```powershell
$symbols = @(
  "REPORT_BODY_FIRST_DRAFT_KEYWORDS",
  "REPORT_BODY_EXPLICIT_CONTINUATION_KEYWORDS",
  "REPORT_BODY_WHOLE_REWRITE_KEYWORDS",
  "REPORT_BODY_CHAPTER_WRITE_RE",
  "REPORT_BODY_INLINE_EDIT_RE",
  "REPORT_BODY_REPLACE_TEXT_INTENT_RE",
  "_regex_has_clean_report_body_intent",
  "_has_explicit_report_body_write_intent",
  "_parse_report_body_replacement_intent",
  "_classify_canonical_draft_turn",
  "_run_phase2a_compare_writer",
  "_record_draft_gate_block_event",        # v5 补
  "compare_baseline_event_count"           # v5 补：作为 dict key 出现，grep 也能命中
  # 注：_record_tagless_fallback_event 不在清零清单——spec §956-962 要求保留作为独立观测通道，不属于 Phase 2a 灰度
)
foreach ($c in $symbols) {
  Write-Host "=== $c ==="
  Select-String -Path backend\chat.py,tests\* -Pattern $c -Recurse
}
```

Expected: 全部无匹配。

- [ ] **Step 2: 跑全套测试**

```powershell
.venv\Scripts\python -m pytest tests/ -v
cd frontend; node --test tests/; cd ..
```

Expected: all pass。

- [ ] **Step 3: Commit if any cleanup**

---

### Task 26: build.ps1 重打包 + reality_test 端到端

**Spec:** §Rollout Phase 3

- [ ] **Step 1: 重打包**

```powershell
.\build.bat
```

- [ ] **Step 2: 启动 + 走完一条新的 reality_test 项目（S0 → S7）**

新建一个测试项目（不用 reality_test 主项目，干净起手），走完 S0 访谈 → S1 大纲 → S2 资料采集 → S3 分析 → S4 写正文（用 begin/continue/section/replace 各试一次）→ S5 审查 → S6 演示 → S7 交付。

观察：
- 黄框只在用户该决策时出现
- progress.md 在 S2/S3 显示质量进度
- draft-action tag 工作（用户 4 种表达都正确触发）
- 工具失败时模型如实告诉用户（不撒谎）

- [ ] **Step 3: Commit packaging artifacts (if any)**

---

### Task 27: worklist + memory 同步

- [ ] **Step 1: 更新 worklist**

`docs/current-worklist.md` 标记本 spec 已交付，列出 Bug A-E 全部 RESOLVED。

- [ ] **Step 2: 更新 memory**

`C:\Users\36932\.claude\projects\D--MyProject-CodeProject-consulting-report-agent\memory\` 更新当前 focus（spec/plan 已交付）。

- [ ] **Step 3: Commit**

```powershell
git add docs/current-worklist.md
git commit -m "docs: mark 2026-05-04 context-signal-and-intent-tag spec as shipped"
```

---

## Self-Review Checklist

实施完成后逐条 check：

- [ ] **Spec coverage:** A1-C1 + Rollout + cutover artifact 全部有对应 task
- [ ] **B1 v5 关键约束**: `preflight_keyword_intent` 字段在 Task 17 加 + Task 19 强制使用 + Task 19 防御测试
- [ ] **A3 v5 关键约束**: `_coalesce_consecutive_user_messages` 处理 None / multipart / 防御 copy 在 Task 7 全部测试
- [ ] **C1 v5 关键约束**: 三层 sanitize 全覆盖（GET API + 前端 render + 前端 copy），见 Task 12
- [ ] **顺序约束**: A3/C1/stage-ack 7 步顺序在 Task 13 编排器明确实施
- [ ] **删除安全**: Task 24 grep 全部待删常量/helper 确认无外部 caller
- [ ] **所有 task 测试块都有完整可执行代码**（非 `pass` / `# ...` 占位）。codex round-1 plan review 已 catch 多处占位，v2 全部填实
- [ ] **测试数（量级估算）**: A1: ~12, A2: ~13, A3: ~17, B1: ~40+, C1: ~25（含 spec §5.5 max_iterations + append_report_draft path-from-result）, frontend: ~6
- [ ] **回归无破坏**: 每个 task 末尾 `pytest tests/` 全过

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-04-context-signal-and-intent-tag.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — 派 fresh subagent 每个 task，task 之间 review，快速迭代

**2. Inline Execution** — 在当前会话用 executing-plans，批量执行 + checkpoint

实施前应先派 codex 审 plan 至 APPROVED（按 CLAUDE.md review loop）。
