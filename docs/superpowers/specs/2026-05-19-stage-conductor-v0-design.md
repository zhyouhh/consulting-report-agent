# 2026-05-19 — Stage Conductor v0 设计

## Context

最近的实机会话暴露了一个阶段错位问题：用户确认大纲后，左侧聊天里模型已经继续写 `plan/data-log.md` 和 `plan/analysis-notes.md`，但右侧 UI 仍停在 S1；之后模型补发阶段信号，UI 又一次性跳到 S4。

根因不是前端刷新问题，而是当前阶段系统是混合机制：

1. `stage_checkpoints.json` 是阶段真值源，`backend/skill.py:_infer_stage_state()` 用 checkpoint + 文件质量推断 S0-S7。
2. 模型推进阶段主要靠回复尾部 `<stage-ack>KEY</stage-ack>`，这个信号在 assistant 回合 finalize 时才执行。
3. `backend/chat.py` 还残留 `_STRONG_ADVANCE_KEYWORDS`，用户短语可设置 pending checkpoint。
4. 文件写入门禁只覆盖 S0 和正文草稿等关键路径，没有按 S1/S2/S3/S4 的正式产物完整收口。

因此同一轮里会出现：

```text
模型先写未来阶段文件
  ↓
assistant 结尾才尝试推进 checkpoint
  ↓
如果 tag 漏发，UI 卡在旧阶段；如果后来补发，UI 会跳阶段
```

这次设计只借鉴 `academic-research-skills` 的一个思想：阶段推进由 conductor 管，不由写作文本管。不引入多 agent。

## Goals

1. 移除 `<stage-ack>` 作为阶段推进机制，不再让模型靠隐藏文本改阶段。
2. 新增正式工具 `advance_stage`，让模型在同一轮内先推进阶段，再写下一阶段文件。
3. 删除聊天侧阶段推进关键词副作用，不再靠 `_STRONG_ADVANCE_KEYWORDS` / rollback keyword 猜用户意图。
4. 给正式产物补阶段写入门禁，防止模型提前写未来阶段文件。
5. 给 `record_stage_checkpoint()` 补完整前序阶段校验，UI/API/模型工具共用同一套规则。
6. 保持 S2/S3 的自动质量推进：`data-log.md` 达标进 S3，`analysis-notes.md` 达标进 S4。
7. 保留旧会话兼容：历史 `<stage-ack>` 被剥离，但不再执行。
8. 同步验证聊天 Markdown 表格渲染修复。

## Non-Goals

1. 不做多 agent 调度。
2. 不重做 UI。
3. 不改 S0-S7 的产品阶段定义。
4. 不引入 LLM classifier 判断用户是否确认。
5. 不新增更多关键词。
6. 不删除 `stage_checkpoints.json`，它仍是阶段真值源。

## Design Summary

新的 checkpoint 变更路径是：

```text
用户自然语言确认
  ↓
模型理解意图
  ↓
模型调用 advance_stage(checkpoint_key, action, reason)
  ↓
后端立即校验完整前序条件并写 stage_checkpoints.json
  ↓
同一轮后续工具调用按最新阶段事实决定是否允许写文件
  ↓
workspace summary / UI 只读后端事实
```

旧路径：

```text
assistant 文本尾部 <stage-ack>...
用户关键词 fallback
```

不再具备阶段副作用。

这里的“阶段副作用”特指 checkpoint mutation。S2 → S3、S3 → S4 仍由
`_infer_stage_state()` 基于 `data-log.md` / `analysis-notes.md` 的质量阈值自动投影，不需要
`advance_stage`。

## Stage Checkpoint Contract

### Checkpoint Keys

沿用现有 6 个 key：

```text
s0_interview_done_at
outline_confirmed_at
review_started_at
review_passed_at
presentation_ready_at
delivery_archived_at
```

### `advance_stage` Tool

新增 OpenAI-compatible tool：

```json
{
  "name": "advance_stage",
  "description": "记录或回退阶段 checkpoint。确认用户明确推进/回退阶段后，先调用本工具，再写下一阶段文件。",
  "parameters": {
    "type": "object",
    "properties": {
      "checkpoint_key": {
        "type": "string",
        "enum": [
          "s0_interview_done_at",
          "outline_confirmed_at",
          "review_started_at",
          "review_passed_at",
          "presentation_ready_at",
          "delivery_archived_at"
        ]
      },
      "action": {
        "type": "string",
        "enum": ["set", "clear"],
        "default": "set"
      },
      "reason": {
        "type": "string",
        "description": "一句话说明为什么推进或回退，用于调试日志和 tool result。"
      }
    },
    "required": ["checkpoint_key", "reason"]
  }
}
```

`action="set"` 时执行完整前序校验。`action="clear"` 时走现有级联清除逻辑，始终幂等。

### S0 Soft Gate

`s0_interview_done_at` 仍有对话级软门槛：

1. 新项目首轮 assistant 必须先问 3-5 个澄清问题。
2. 如果 `conversation.json` 中没有任何 prior assistant message，`advance_stage(s0_interview_done_at)` 返回 error，不落 checkpoint。
3. 用户回答或明确跳过后，下一轮模型才可以调用 `advance_stage(s0_interview_done_at)`。
4. 推荐顺序是：必要时先更新 `plan/project-overview.md` 的澄清内容；再调用
   `advance_stage(s0_interview_done_at)`；工具成功后才写 S1 文件。

这个软门槛只适用于 `advance_stage` 工具 set S0；schema migration 不受影响。

## Full Predecessor Validation

`SkillEngine.record_stage_checkpoint(project_id, key, action)` 改为统一校验完整前序链，而不是只校验目标 checkpoint 的单个文件。

建议新增 pure-ish helper：

```python
def _validate_stage_checkpoint_transition(self, project_path: Path, key: str) -> None:
    ...
```

规则：

| Key | Required Conditions |
|---|---|
| `s0_interview_done_at` | `plan/project-overview.md` effective |
| `outline_confirmed_at` | S0 complete; `notes.md`, `references.md`, `outline.md`, `research-plan.md` effective |
| `review_started_at` | S1 complete; `data-log.md` source threshold met; `analysis-notes.md` DL-reference threshold met; `content/report_draft_v1.md` effective and over word floor |
| `review_passed_at` | `review_started_at` set; `review-checklist.md` effective |
| `presentation_ready_at` | `review_passed_at` set; delivery mode is `报告+演示`; `presentation-plan.md` effective |
| `delivery_archived_at` | if delivery mode is `报告+演示`, `presentation_ready_at` set; otherwise `review_passed_at` set; `delivery-log.md` effective |

This closes the current worklist item where checkpoint API can越级推进.

## Stage Write Gates

`ChatHandler._execute_plan_write()` gets a new gate after path normalization and before write persistence.

Suggested helper:

```python
def _validate_stage_write_allowed(
    self,
    project_id: str,
    normalized_path: str,
    source_tool_name: str,
) -> str | None:
    ...
```

Rules:

| Path | Minimum Requirement |
|---|---|
| `plan/project-overview.md` | allowed in S0+ |
| `plan/notes.md` / `plan/references.md` | allowed in S0+ |
| `plan/outline.md` / `plan/research-plan.md` | require `s0_interview_done_at`; existing evidence gate still applies |
| `plan/data-log.md` | require `outline_confirmed_at` |
| `plan/analysis-notes.md` | require `data-log.md` source threshold met |
| `content/report_draft_v1.md` | keep existing `check_report_writing_stage`: stage must be S4+ |
| `plan/review-checklist.md` / `plan/review.md` | require `review_started_at` |
| `plan/presentation-plan.md` | require `review_passed_at` and delivery mode `报告+演示` |
| `plan/delivery-log.md` | require `review_passed_at` for report-only, or `presentation_ready_at` for report+presentation |

Important consequence:

```text
User says "没问题，继续吧"
  ↓
If model forgets advance_stage(outline_confirmed_at)
  ↓
write_file(plan/data-log.md) returns error
  ↓
No future-stage file lands silently
```

If the model does the right thing:

```text
advance_stage(outline_confirmed_at) succeeds
  ↓
same-turn write_file(plan/data-log.md) is allowed
```

## Removing Stage-Ack

### Backend

1. Remove `backend/stage_ack.py` from runtime use.
2. Delete `_STAGE_ACK_MARKER`, stage-ack tail guard, `_finalize_assistant_turn()` stage-ack execution, and `_apply_stage_ack_event()`.
3. Keep or add simple sanitizer regex only:

```python
STAGE_ACK_STRIP_RE = re.compile(
    r'<stage-ack(?:\s+action="(?:set|clear)")?>[a-z_0-9]+</stage-ack>',
    re.IGNORECASE,
)
```

It strips old tags from:

1. loaded conversation history,
2. assistant visible/persisted content,
3. frontend display fallback.

It never executes checkpoint side effects.

### Tests

Delete or rewrite `tests/test_stage_ack.py` and `StageAck*` test classes in `tests/test_chat_runtime.py`.

Replacement assertions:

1. assistant text containing `<stage-ack>outline_confirmed_at</stage-ack>` does not set checkpoint;
2. tag is stripped from persisted conversation;
3. `advance_stage` is the only model-side stage mutation path.

### Skill Prompt

`skill/SKILL.md` removes the stage-ack appendix and all instructions to output XML tags.

Every stage transition instruction becomes:

```md
用户明确确认推进/回退阶段时，先调用 `advance_stage` 工具。
工具成功后，才能继续写下一阶段文件。
不要用文字声称已推进阶段，除非 `advance_stage` 已成功。
```

## Removing Keyword Side Effects

In `backend/chat.py`:

1. Remove `_STRONG_ADVANCE_KEYWORDS`, `_ROLLBACK_KEYWORDS`, `_STAGE_RANK`, `_detect_stage_keyword()` and `pending_stage_keyword`.
2. `_build_turn_context()` no longer mutates checkpoints from user text.
3. Natural language confirmation is handled by the model choosing `advance_stage`.

This intentionally means plain backend code no longer guesses that `"没问题，继续吧"` means outline confirmation. That judgment belongs to the LLM, but the mutation belongs to the tool.

## UI Checkpoint API

Existing UI endpoint remains:

```text
POST /api/projects/{project_id}/checkpoints/{name}?action=set|clear
```

Changes:

1. It uses the same upgraded `record_stage_checkpoint()` predecessor validation.
2. `s0-interview-done?action=set` remains rejected because endpoint has no conversation context for S0 soft gate.
3. `s0-interview-done?action=clear` remains allowed for advanced rollback.

## Claim Mismatch Guard

Add a lightweight turn-end guard after assistant visible content is finalized:

If assistant visible text claims a stage has advanced but no checkpoint event happened this turn and stage did not change, inject a corrective retry/user notice.

Scope for v0:

1. Detect a small set of strong assistant claims, not user keywords:
   - `进入资料采集`
   - `进入分析`
   - `进入报告撰写`
   - `进入质量审查`
   - `已推进到`
2. If matched and `_turn_context["checkpoint_event"]` is absent, do not set checkpoint automatically.
3. Return a corrective message to the model:

```text
你刚才声称推进阶段，但没有调用 advance_stage 成功。
请先调用 advance_stage，或改口说明当前仍停留在原阶段。
```

This is a guardrail, not a classifier.

## Tool Result Shape

`advance_stage` success:

```json
{
  "status": "success",
  "action": "set",
  "checkpoint_key": "outline_confirmed_at",
  "stage_code": "S2",
  "message": "已记录阶段推进：outline_confirmed_at"
}
```

`advance_stage` failure:

```json
{
  "status": "error",
  "checkpoint_key": "outline_confirmed_at",
  "message": "需要先补齐 notes.md / references.md / outline.md / research-plan.md，才能确认大纲。"
}
```

On success, set:

```python
turn_context["checkpoint_event"] = {
    "action": action,
    "key": checkpoint_key,
}
```

## Frontend Markdown Rendering

The already implemented Markdown fix is part of this branch:

1. `ChatPanel.jsx` imports `remark-gfm`;
2. assistant `ReactMarkdown` receives `remarkPlugins={[remarkGfm]}`;
3. assistant tables get compact dark-theme table components;
4. `frontend/tests/chatPanelMarkdown.test.mjs` locks the behavior.

This is not part of stage conductor, but final packaged QA must verify it visually.

## Testing Plan

### Backend Unit Tests

Add/replace tests in `tests/test_chat_runtime.py`:

1. `advance_stage` tool is present in tool schema.
2. `advance_stage(outline_confirmed_at)` fails without full S1 prerequisites.
3. `advance_stage(outline_confirmed_at)` succeeds with S1 prerequisites and sets `checkpoint_event`.
4. Same-turn `advance_stage(outline_confirmed_at)` then `write_file(plan/data-log.md)` is allowed.
5. `write_file(plan/data-log.md)` before `outline_confirmed_at` is rejected.
6. `write_file(plan/analysis-notes.md)` before data-log threshold is rejected.
7. stage-ack text is stripped but does not set checkpoint.
8. `_build_turn_context("确认大纲")` has no checkpoint side effect.
9. claim mismatch guard retries or emits corrective notice when assistant says stage advanced without tool success.

Add tests in `tests/test_skill_engine.py`:

1. `record_stage_checkpoint(review_started_at)` rejects when outline is confirmed but data-log/analysis/report are incomplete.
2. `record_stage_checkpoint(outline_confirmed_at)` requires S0 and all S1 files.
3. `delivery_archived_at` predecessor differs by delivery mode.
4. clearing `s0_interview_done_at` still cascades downstream checkpoints.

Add/update API tests:

1. UI checkpoint endpoint cannot越级 set `review-started`.
2. `s0-interview-done?action=set` returns 400.
3. `s0-interview-done?action=clear` remains idempotent.

### Frontend Tests

1. Existing `chatPanelMarkdown.test.mjs` passes.
2. Existing `node --test tests/` passes.
3. If a frontend sanitizer is added for old stage-ack residue, add a focused test.

### Integration / Packaged QA

After implementation:

1. Run backend targeted tests.
2. Run backend full tests if time permits before packaging.
3. Run frontend full node tests.
4. Run `npm run build`.
5. Run `build.bat`.
6. Launch packaged `dist\咨询报告助手\咨询报告助手.exe`.
7. Use visual browser/computer automation to run a report from S0 to S7:
   - S0 interview question appears.
   - User answers or skips.
   - model uses `advance_stage`, not `<stage-ack>`.
   - S1 outline confirmation advances to S2.
   - S2/S3 auto-advance via data-log and analysis quality.
   - S4 draft writing works.
   - S5 quality review works.
   - S6 if delivery mode requires presentation, otherwise skipped.
   - S7 archive reaches done.
8. Verify chat Markdown table renders as an HTML table, not raw pipe text.

## Migration / Compatibility

1. Existing projects keep `stage_checkpoints.json`.
2. Historical `<stage-ack>` text in conversations is stripped on read/display.
3. Existing `stage_checkpoints.json` migration logic remains, but no future checkpoint is inferred from stage-ack.
4. Existing UI buttons keep working, now with stricter predecessor validation.

## Risks

| Risk | Mitigation |
|---|---|
| Model forgets to call `advance_stage` | Stage write gates block future files; claim mismatch guard corrects false claims |
| Removing keywords makes natural confirmation less automatic | LLM still understands natural language and must call tool; prompt makes this explicit |
| Stage write gates block legitimate repair edits | Gates apply to formal future-stage writes; current-stage and prior-stage support files remain allowed |
| Tests tied to stage-ack are numerous | Replace with advance_stage tests; do not preserve old behavior |
| Packaged S0-S7 may be slow with real model/search | Use prepared topic and allow enough timeout; capture visual evidence and bug report if blocked |

## Acceptance Criteria

1. No backend code path executes `<stage-ack>` side effects.
2. Model-side stage mutation only happens through `advance_stage`.
3. UI-side stage mutation still happens through checkpoint endpoint with the same predecessor validation.
4. Future-stage files cannot land before their required stage facts.
5. Same-turn `advance_stage` then next-stage write works.
6. Full relevant tests pass.
7. New Windows package is built.
8. Visual packaged S0-S7 QA completes without stage desync.
9. Chat Markdown table renders correctly in packaged UI.
