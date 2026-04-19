# Real Token Accounting And Post-Turn Compaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace misleading estimated token usage with provider-grounded token accounting, lower managed `gemini-3-flash` to a 200k effective context limit, and trigger auto-compaction only after a turn completes based on real usage.

**Architecture:** Keep the existing chat loop structure in `backend/chat.py`, but split the responsibilities mentally into three layers: usage normalization, compact-state persistence, and turn-finalization. Persist post-turn compact summaries into a sidecar file so the next provider conversation can consume them without polluting the user-visible conversation history. Update the frontend to render only provider-real usage fields and explicitly label unavailable fields, while preserving a lightweight compatibility alias for API consumers that still expect `max_tokens`.

**Tech Stack:** Python (`openai`, stdlib json/pathlib, existing FastAPI stack), React/Vite frontend, Python `unittest`, frontend `node:test`, PyInstaller packaging.

---

## File Map

- Modify: `backend/context_policy.py`
  - Change `gemini-3-flash` effective limit from `500_000` to `200_000`.
  - Keep provider limit separate from effective limit.
- Modify: `backend/models.py`
  - Expand `TokenUsage` to the new provider-real shape while keeping `max_tokens` as a compatibility alias.
- Modify: `backend/chat.py`
  - Normalize provider usage for non-streaming and streaming paths.
  - Keep preflight compaction only as a hidden hard guard.
  - Persist post-turn compact summaries to a sidecar file and load them into the next provider conversation while skipping covered history.
  - Trigger post-turn auto-compact synchronously before returning final turn completion.
- Modify: `backend/main.py`
  - Keep API logging compatible with the new `token_usage` shape.
  - Ensure clear-conversation removes both `conversation.json` and the compact-state sidecar.
- Modify: `frontend/src/utils/contextUsage.js`
  - Replace estimated-vs-actual labeling with provider-real field formatting, availability labels, and post-turn compaction status messaging.
- Modify: `frontend/src/components/ChatPanel.jsx`
  - Render the richer usage summary and explicit compaction outcome.
- Modify: `tests/test_context_policy.py`
  - Update policy expectations for managed Gemini.
- Modify: `tests/test_chat_runtime.py`
  - Add backend TDD coverage for usage normalization, streaming usage handling, sidecar persistence, history skipping, and post-turn auto-compact.
- Modify: `tests/test_main_api.py`
  - Update API response expectations and clear-conversation behavior.
- Modify: `tests/test_stream_api.py`
  - Update SSE contract coverage for the new usage payload shape and final-event ordering.
- Modify: `frontend/tests/contextUsage.test.mjs`
  - Add frontend formatter coverage for real/unavailable usage fields and post-turn compaction messaging.
- Optional create only if `backend/chat.py` becomes too hard to keep legible: `backend/token_usage.py`
  - Hold normalization helpers only. Do not split unless the implementation becomes unreasonably tangled.

## Shared Execution Notes

- Use the existing Python interpreter already used in this workspace:
  - `C:\Users\user\AppData\Local\Programs\Python\Python312-codex\python.exe`
- Use `unittest`, not `pytest`, because this environment already has passing `unittest` coverage and `pytest` is not the default harness here.
- Frontend tests already use Node's built-in runner; run them with `node --test frontend/tests/contextUsage.test.mjs`.
- Do not include the spec/plan documents or unrelated untracked docs in the shipping commit unless explicitly needed later.

### Task 1: Lock The New Token Usage Contract

**Files:**
- Modify: `tests/test_context_policy.py`
- Modify: `tests/test_chat_runtime.py`
- Modify: `tests/test_main_api.py`
- Modify: `tests/test_stream_api.py`
- Modify: `frontend/tests/contextUsage.test.mjs`
- Modify: `backend/context_policy.py`
- Modify: `backend/models.py`

- [ ] **Step 1: Write the failing context-policy test for managed Gemini**

```python
def test_exact_match_for_managed_gemini_uses_1m_provider_and_200k_effective(self):
    policy = resolve_context_policy("gemini-3-flash")
    self.assertEqual(policy.provider_context_limit, 1_000_000)
    self.assertEqual(policy.effective_context_limit, 200_000)
```

- [ ] **Step 2: Write failing backend contract tests for provider-real usage normalization**

```python
@mock.patch("backend.chat.OpenAI")
def test_chat_returns_provider_real_usage_fields(self, mock_openai):
    mock_openai.return_value.chat.completions.create.return_value = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=180000,
            completion_tokens=1200,
            total_tokens=181200,
            prompt_tokens_details=SimpleNamespace(cached_tokens=4000),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
        ),
        choices=[SimpleNamespace(message=SimpleNamespace(content="完成", tool_calls=[]))],
    )
    ...
    token_usage = result["token_usage"]
    self.assertEqual(token_usage["usage_source"], "provider")
    self.assertEqual(token_usage["context_used_tokens"], 180000)
    self.assertEqual(token_usage["input_tokens"], 180000)
    self.assertEqual(token_usage["output_tokens"], 1200)
    self.assertEqual(token_usage["cache_read_tokens"], 4000)
    self.assertEqual(token_usage["max_tokens"], 200000)
```

- [ ] **Step 3: Write failing contract tests for incomplete provider usage**

```python
def test_chat_marks_usage_unavailable_without_provider_fields(...):
    mock_openai.return_value.chat.completions.create.return_value = SimpleNamespace(
        usage=None,
        choices=[SimpleNamespace(message=SimpleNamespace(content="完成", tool_calls=[]))],
    )
    ...
    token_usage = result["token_usage"]
    self.assertEqual(token_usage["usage_source"], "unavailable")
    self.assertIsNone(token_usage["context_used_tokens"])
    self.assertIsNone(token_usage["input_tokens"])
```

- [ ] **Step 4: Write failing API and frontend formatter tests**

```python
def test_chat_endpoint_returns_new_token_usage_shape(...):
    handler.chat.return_value = {
        "content": "ok",
        "token_usage": {
            "usage_source": "provider",
            "context_used_tokens": 180000,
            "input_tokens": 180000,
            "output_tokens": 1200,
            "total_tokens": 181200,
            "effective_max_tokens": 200000,
            "provider_max_tokens": 1000000,
            "max_tokens": 200000,
            "preflight_compaction_used": False,
            "post_turn_compaction_status": "not_needed",
            "compressed": False,
        },
    }
```

```js
test("formatContextUsage shows provider-real usage and unavailable detail", () => {
  assert.deepEqual(
    formatContextUsage({
      usage_source: "provider",
      context_used_tokens: 180000,
      effective_max_tokens: 200000,
      input_tokens: 180000,
      output_tokens: 1200,
      post_turn_compaction_status: "completed",
      preflight_compaction_used: false,
    }),
    {
      label: "上下文真实用量",
      detail: "180k / 200k",
      modeTag: "Provider真实统计",
      compressedTag: "已自动整理",
      fields: [...]
    },
  )
})
```

```python
def test_stream_endpoint_emits_provider_usage_shape_incrementally(...):
    yield {"type": "usage", "data": {
        "usage_source": "provider",
        "context_used_tokens": 180000,
        "input_tokens": 180000,
        "output_tokens": 1200,
        "total_tokens": 181200,
        "effective_max_tokens": 200000,
        "provider_max_tokens": 1000000,
        "max_tokens": 200000,
        "preflight_compaction_used": False,
        "post_turn_compaction_status": "completed",
    }}
```

- [ ] **Step 5: Run the focused RED tests**

Run:

```powershell
& 'C:\Users\user\AppData\Local\Programs\Python\Python312-codex\python.exe' -m unittest `
  tests.test_context_policy.ContextPolicyTests.test_exact_match_for_managed_gemini_uses_1m_provider_and_200k_effective `
  tests.test_chat_runtime.ChatRuntimeTests.test_chat_returns_provider_real_usage_fields `
  tests.test_chat_runtime.ChatRuntimeTests.test_chat_marks_usage_unavailable_without_provider_fields `
  tests.test_main_api.WorkspaceApiTests.test_chat_endpoint_returns_new_token_usage_shape `
  tests.test_stream_api.ChatStreamApiTests.test_stream_endpoint_emits_provider_usage_shape_incrementally

node --test --test-name-pattern "^formatContextUsage shows provider-real usage and compaction status$" frontend/tests/contextUsage.test.mjs
```

Expected:
- Python tests fail because the effective limit is still `500000` and the API/SSE token usage shape is too small.
- Frontend formatter test fails because it still speaks in `actual/estimated` terms.

- [ ] **Step 6: Implement the minimal schema and policy changes**

```python
# backend/context_policy.py
TIER_LIMITS = {
    "tier_1m": (1_000_000, 200_000),
    ...
}

# backend/models.py
class TokenUsage(BaseModel):
    usage_source: Literal["provider", "provider_partial", "unavailable"] = "unavailable"
    context_used_tokens: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    max_tokens: int = 128000
    effective_max_tokens: int = 128000
    provider_max_tokens: int = 128000
    preflight_compaction_used: bool = False
    post_turn_compaction_status: Literal["not_needed", "completed", "failed", "skipped_unavailable"] = "not_needed"
    compressed: bool = False
    raw_usage: dict | None = None
```

- [ ] **Step 7: Re-run the focused GREEN tests**

Run the same commands as Step 5.

Expected:
- The contract and policy tests now pass.

### Task 2: Normalize Usage For Non-Streaming And Streaming Chat

**Files:**
- Modify: `backend/chat.py`
- Modify: `tests/test_chat_runtime.py`
- Modify: `tests/test_main_api.py`
- Modify: `tests/test_stream_api.py`

- [ ] **Step 1: Write failing normalization tests for different usage payload shapes**

```python
def test_normalize_usage_prefers_prompt_tokens_for_context_used(...):
    usage = SimpleNamespace(prompt_tokens=180000, completion_tokens=800, total_tokens=180800)
    normalized = handler._normalize_provider_usage(usage, policy, preflight_compaction_used=False)
    self.assertEqual(normalized["context_used_tokens"], 180000)
    self.assertEqual(normalized["usage_source"], "provider")

def test_normalize_usage_falls_back_to_total_tokens_without_guessing(...):
    usage = SimpleNamespace(total_tokens=140000)
    normalized = handler._normalize_provider_usage(usage, policy, preflight_compaction_used=False)
    self.assertEqual(normalized["context_used_tokens"], 140000)
    self.assertEqual(normalized["usage_source"], "provider_partial")

def test_normalize_usage_accepts_input_and_output_token_shapes(...):
    usage = SimpleNamespace(input_tokens=91000, output_tokens=1200, total_tokens=92200)
    normalized = handler._normalize_provider_usage(usage, policy, preflight_compaction_used=False)
    self.assertEqual(normalized["input_tokens"], 91000)
    self.assertEqual(normalized["output_tokens"], 1200)
    self.assertEqual(normalized["context_used_tokens"], 91000)
    self.assertEqual(normalized["usage_source"], "provider")
```

- [ ] **Step 2: Write a failing streaming usage test**

```python
def test_chat_stream_emits_provider_real_usage_payload_when_final_usage_chunk_arrives(...):
    mock_openai.return_value.chat.completions.create.return_value = iter([
        self._make_chunk(content="第一段"),
        self._make_usage_chunk(prompt_tokens=175000, completion_tokens=900, total_tokens=175900),
    ])
    events = list(handler.chat_stream(project["id"], "继续"))
    usage_event = next(event for event in events if event["type"] == "usage")
    self.assertEqual(usage_event["data"]["usage_source"], "provider")
    self.assertEqual(usage_event["data"]["context_used_tokens"], 175000)
```

- [ ] **Step 3: Run the RED slice**

Run:

```powershell
& 'C:\Users\user\AppData\Local\Programs\Python\Python312-codex\python.exe' -m unittest `
  tests.test_chat_runtime.ChatRuntimeTests.test_normalize_usage_prefers_prompt_tokens_for_context_used `
  tests.test_chat_runtime.ChatRuntimeTests.test_normalize_usage_falls_back_to_total_tokens_without_guessing `
  tests.test_chat_runtime.ChatRuntimeTests.test_normalize_usage_accepts_input_and_output_token_shapes `
  tests.test_chat_runtime.ChatRuntimeTests.test_chat_stream_emits_provider_real_usage_payload_when_final_usage_chunk_arrives `
  tests.test_stream_api.ChatStreamApiTests.test_stream_endpoint_emits_provider_usage_shape_incrementally
```

Expected:
- Fails because there is no unified normalization helper and stream usage still falls back to estimated totals.

- [ ] **Step 4: Implement the normalization helpers in `backend/chat.py`**

```python
def _normalize_provider_usage(self, usage, policy, *, preflight_compaction_used: bool, post_turn_compaction_status: str = "not_needed"):
    raw = self._usage_to_dict(usage)
    prompt_tokens = self._first_usage_value(raw, "prompt_tokens", "input_tokens")
    completion_tokens = self._first_usage_value(raw, "completion_tokens", "output_tokens")
    total_tokens = self._first_usage_value(raw, "total_tokens")
    cache_read = self._extract_usage_detail(raw, ...)
    reasoning = self._extract_usage_detail(raw, ...)
    context_used = prompt_tokens if prompt_tokens is not None else total_tokens
    usage_source = "provider" if prompt_tokens is not None else "provider_partial" if total_tokens is not None else "unavailable"
    return {...}
```

- [ ] **Step 5: Make non-streaming `chat()` and streaming `chat_stream()` both use the helper**

Implementation notes:
- Track the usage attached to the final assistant-producing provider call only.
- Do not accumulate tool-loop usage into the main returned `token_usage`.
- For streaming, request provider usage with a best-effort `stream_options={"include_usage": True}` path, but keep a compatibility retry without it if the provider rejects that parameter.

- [ ] **Step 6: Re-run the GREEN slice**

Run the same command as Step 3.

Expected:
- The new normalization tests pass.

### Task 3: Persist Post-Turn Compact State And Use It On The Next Turn

**Files:**
- Modify: `backend/chat.py`
- Modify: `backend/main.py`
- Modify: `tests/test_chat_runtime.py`
- Modify: `tests/test_main_api.py`

- [ ] **Step 1: Write failing persistence tests**

```python
def test_chat_auto_compact_persists_sidecar_and_skips_compacted_history_next_turn(...):
    first_response = ...
    second_response = ...
    # first turn returns prompt_tokens over 90% threshold and causes compact
    # second turn should inject compact summary and omit the compacted prefix
    ...
    compact_state_path = Path(project["project_dir"]) / "conversation_compact_state.json"
    self.assertTrue(compact_state_path.exists())
    payload = json.loads(compact_state_path.read_text(encoding="utf-8"))
    self.assertEqual(payload["source_message_count"], 2)
    self.assertIn("紧凑摘要", payload["summary_text"])
```

```python
def test_chat_discards_compact_sidecar_when_history_becomes_shorter_than_source_count(...):
    ...
    compact_state_path.write_text(json.dumps({
        "summary_text": "旧摘要",
        "source_message_count": 8,
        "last_compacted_at": "2026-04-13T12:00:00",
        "trigger_usage": {"context_used_tokens": 190000},
    }, ensure_ascii=False), encoding="utf-8")
    conversation_path.write_text(json.dumps([{"role": "user", "content": "只剩一条"}], ensure_ascii=False), encoding="utf-8")
    provider_conversation = handler._build_provider_conversation(project["id"])
    self.assertFalse(compact_state_path.exists())
    self.assertNotIn("旧摘要", json.dumps(provider_conversation, ensure_ascii=False))
```

```python
def test_clear_conversation_removes_compact_sidecar(...):
    ...
    self.assertFalse((project_path / "conversation_compact_state.json").exists())
```

- [ ] **Step 2: Write failing post-turn auto-compact status tests**

```python
def test_chat_marks_post_turn_compaction_completed_when_threshold_is_hit(...):
    ...
    self.assertEqual(result["token_usage"]["post_turn_compaction_status"], "completed")

def test_chat_marks_post_turn_compaction_skipped_when_usage_is_unavailable(...):
    ...
    self.assertEqual(result["token_usage"]["post_turn_compaction_status"], "skipped_unavailable")
```

- [ ] **Step 3: Run the RED slice**

Run:

```powershell
& 'C:\Users\user\AppData\Local\Programs\Python\Python312-codex\python.exe' -m unittest `
  tests.test_chat_runtime.ChatRuntimeTests.test_chat_auto_compact_persists_sidecar_and_skips_compacted_history_next_turn `
  tests.test_chat_runtime.ChatRuntimeTests.test_chat_discards_compact_sidecar_when_history_becomes_shorter_than_source_count `
  tests.test_chat_runtime.ChatRuntimeTests.test_chat_marks_post_turn_compaction_completed_when_threshold_is_hit `
  tests.test_chat_runtime.ChatRuntimeTests.test_chat_marks_post_turn_compaction_skipped_when_usage_is_unavailable `
  tests.test_main_api.WorkspaceApiTests.test_clear_conversation_removes_compact_sidecar
```

Expected:
- Fails because no sidecar exists, no history skipping exists, and no post-turn status exists.

- [ ] **Step 4: Implement compact sidecar persistence**

Implementation notes:
- Add helpers in `backend/chat.py` for:
  - `_get_compact_state_path(project_id)`
  - `_load_compact_state(project_id)`
  - `_save_compact_state_atomically(project_id, payload)`
  - `_clear_compact_state(project_id)`
- Store:
  - `summary_text`
  - `source_message_count`
  - `last_compacted_at`
  - `trigger_usage`
- Inject the summary into `_build_provider_conversation()`
- Skip the first `source_message_count` persisted history messages when the sidecar is valid.
- If persisted history is shorter than `source_message_count`, treat the sidecar as invalid, delete it, and fall back to the raw conversation only.

- [ ] **Step 5: Implement post-turn compaction execution**

Implementation notes:
- After the final assistant text is known but before the turn fully finishes:
  - normalize real usage
  - decide if `context_used_tokens / effective_max_tokens >= 0.9`
  - if yes, summarize the compactable prefix and atomically write the sidecar
  - only then send the final SSE `usage` event and `[DONE]`
- Keep preflight compaction as an internal hard guard only; surface it only through `preflight_compaction_used`.

- [ ] **Step 6: Update clear-conversation handling**

```python
@app.delete("/api/projects/{project_id}/conversation")
async def clear_conversation(project_id: str):
    ...
    compact_state = project_path / "conversation_compact_state.json"
    if compact_state.exists():
        compact_state.unlink()
```

- [ ] **Step 7: Re-run the GREEN slice**

Run the same command as Step 3.

Expected:
- Sidecar persistence, history skipping, and post-turn status tests now pass.

### Task 4: Refresh The Frontend Display And Compatibility Layer

**Files:**
- Modify: `frontend/src/utils/contextUsage.js`
- Modify: `frontend/src/components/ChatPanel.jsx`
- Modify: `frontend/tests/contextUsage.test.mjs`
- Modify: `tests/test_main_api.py`
- Modify: `tests/test_stream_api.py`

- [ ] **Step 1: Write failing frontend formatter tests for the new semantics**

```js
test("formatContextUsage shows unavailable fields honestly", () => {
  const usage = formatContextUsage({
    usage_source: "unavailable",
    context_used_tokens: null,
    effective_max_tokens: 200000,
    post_turn_compaction_status: "skipped_unavailable",
    preflight_compaction_used: false,
  })
  assert.equal(usage.modeTag, "Provider未提供")
  assert.equal(usage.detail, "未提供 / 200k")
  assert.equal(usage.compactedStatus, "本轮未获得真实 usage，未触发自动整理")
})
```

- [ ] **Step 2: Write a failing API compatibility test for `max_tokens` alias retention**

```python
def test_chat_endpoint_keeps_max_tokens_alias_for_existing_clients(...):
    ...
    self.assertEqual(response.json()["token_usage"]["max_tokens"], 200000)
    self.assertEqual(response.json()["token_usage"]["effective_max_tokens"], 200000)
```

```python
def test_stream_endpoint_keeps_usage_event_last_before_done(...):
    ...
    self.assertLess(content_event_index, usage_event_index)
    self.assertLess(usage_event_index, done_event_index)
```

- [ ] **Step 3: Run the RED slice**

Run:

```powershell
node --test frontend/tests/contextUsage.test.mjs
& 'C:\Users\user\AppData\Local\Programs\Python\Python312-codex\python.exe' -m unittest `
  tests.test_main_api.WorkspaceApiTests.test_chat_endpoint_keeps_max_tokens_alias_for_existing_clients `
  tests.test_stream_api.ChatStreamApiTests.test_stream_endpoint_keeps_usage_event_last_before_done
```

Expected:
- Frontend tests fail because the formatter still presents `actual/estimated`.
- API compatibility expectations still reference the old shape and values.

- [ ] **Step 4: Implement the formatter and panel updates**

Implementation notes:
- Keep the top progress bar based on `context_used_tokens / effective_max_tokens` only.
- Replace the old `actual/estimated` badge with:
  - `Provider真实统计`
  - `Provider部分提供`
  - `Provider未提供`
- Show compaction outcome text derived from `post_turn_compaction_status`.
- Keep `max_tokens` alias in API output for one compatibility cycle.

- [ ] **Step 5: Re-run the GREEN slice**

Run the same commands as Step 3.

Expected:
- The frontend formatter and API compatibility tests pass.

### Task 5: Full Regression, Fresh Package, And Release Verification

**Files:**
- Modify: `backend/chat.py`
- Modify: `backend/context_policy.py`
- Modify: `backend/models.py`
- Modify: `backend/main.py`
- Modify: `frontend/src/components/ChatPanel.jsx`
- Modify: `frontend/src/utils/contextUsage.js`
- Modify: `tests/test_chat_runtime.py`
- Modify: `tests/test_context_policy.py`
- Modify: `tests/test_main_api.py`
- Modify: `tests/test_stream_api.py`
- Modify: `frontend/tests/contextUsage.test.mjs`
- Modify: `requirements.txt` only if needed by the final implementation

- [ ] **Step 1: Run the full regression suite**

Run:

```powershell
& 'C:\Users\user\AppData\Local\Programs\Python\Python312-codex\python.exe' -m unittest `
  tests.test_chat_runtime `
  tests.test_chat_context `
  tests.test_skill_engine `
  tests.test_context_policy `
  tests.test_main_api `
  tests.test_stream_api

node --test frontend/tests/contextUsage.test.mjs
```

Expected:
- All Python and frontend tests pass with no new failures.

- [ ] **Step 2: Run a source-level smoke test for token accounting and post-turn compaction**

Run:

```powershell
@'
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from backend.chat import ChatHandler
from backend.config import Settings
from backend.skill import SkillEngine

with tempfile.TemporaryDirectory() as tmpdir:
    projects_dir = Path(tmpdir) / "projects"
    engine = SkillEngine(projects_dir, Path("skill"))
    project = engine.create_project(
        name="smoke",
        workspace_dir=tmpdir,
        project_type="strategy-consulting",
        theme="token smoke",
        target_audience="team",
        deadline="2026-04-20",
        expected_length="2000",
    )
    settings = Settings(
        mode="managed",
        managed_base_url="https://newapi.z0y0h.work/client/v1",
        managed_model="gemini-3-flash",
        projects_dir=projects_dir,
    )
    with mock.patch("backend.chat.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.side_effect = [
            SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=195000, completion_tokens=500, total_tokens=195500),
                choices=[SimpleNamespace(message=SimpleNamespace(content="第一轮完成", tool_calls=[]))],
            ),
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="紧凑摘要", tool_calls=[]))],
            ),
        ]
        handler = ChatHandler(settings, engine)
        result = handler.chat(project["id"], "继续")
        compact_state = json.loads((Path(project["project_dir"]) / "conversation_compact_state.json").read_text(encoding="utf-8"))
        print(result["token_usage"]["usage_source"], result["token_usage"]["context_used_tokens"], result["token_usage"]["post_turn_compaction_status"])
        print(compact_state["source_message_count"], bool(compact_state["summary_text"]))
'@ | & 'C:\Users\user\AppData\Local\Programs\Python\Python312-codex\python.exe' -
```

Expected:
- The script prints provider-real usage data and a persisted compact sidecar summary.

- [ ] **Step 3: Remove the old package and build a fresh one**

Run:

```powershell
Copy-Item -LiteralPath "D:\CodexProject\Consult report\consulting-report-agent\dist\咨询报告助手\_internal\managed_client_token.txt" `
  -Destination "D:\CodexProject\Consult report\consulting-report-agent\managed_client_token.txt" -Force
Remove-Item -LiteralPath "D:\CodexProject\Consult report\consulting-report-agent\build" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath "D:\CodexProject\Consult report\consulting-report-agent\dist" -Recurse -Force -ErrorAction SilentlyContinue
& 'C:\Users\user\AppData\Local\Programs\Python\Python312-codex\python.exe' -m pip install -r requirements.txt
& 'C:\Users\user\AppData\Local\Programs\Python\Python312-codex\python.exe' -m PyInstaller consulting_report.spec
Remove-Item -LiteralPath "D:\CodexProject\Consult report\consulting-report-agent\managed_client_token.txt" -Force
```

Expected:
- A fresh `dist\咨询报告助手\` exists and includes `_internal\managed_client_token.txt`.

- [ ] **Step 4: Do the final independent review and then commit**

Run:

```powershell
git -C "D:\CodexProject\Consult report\consulting-report-agent" status --short
```

Checklist:

```text
- token usage display uses provider-real fields only
- gemini-3-flash effective limit is 200k everywhere
- post-turn auto-compact writes and consumes the sidecar correctly
- history-shorter-than-sidecar invalidation removes stale compact state
- clear conversation removes compact state
- old package was replaced by the new build
```

- [ ] **Step 5: Commit and push**

```bash
git add backend/chat.py backend/context_policy.py backend/models.py backend/main.py frontend/src/components/ChatPanel.jsx frontend/src/utils/contextUsage.js frontend/tests/contextUsage.test.mjs tests/test_chat_runtime.py tests/test_context_policy.py tests/test_main_api.py tests/test_stream_api.py
git commit -m "feat: use real token accounting and post-turn compaction"
git push origin main
```
