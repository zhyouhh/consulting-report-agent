# Session Memory Rearchitecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a lightweight structured session-memory layer so successful reads and writes survive across turns, while keeping the existing visible chat history and API surface stable.

**Architecture:** Keep `backend/chat.py` as the center of gravity. Add one new sidecar state file, record only high-value successful tool events plus deduplicated memory entries, and rebuild provider-context assembly into five explicit layers: `system prompt -> compact summary -> recent memory -> recent visible messages -> current turn`. Upgrade the existing compaction and budget-fit paths so they preserve that layer order instead of flattening everything back into plain messages.

**Tech Stack:** Python (`json`, `pathlib`, existing `openai` / FastAPI stack), existing `unittest` suite, no new database or third-party memory system.

---

## File Map

- Modify: `backend/chat.py`
  - Replace the standalone compact-state helpers around `chat.py:476-559` with unified conversation-state helpers.
  - Persist successful `read_material_file` / `read_file` / `fetch_url` / `write_file` events and upserted memory entries from `_execute_tool()` and the turn-finalization path.
  - Rebuild `_build_provider_conversation()` and `_fit_conversation_to_budget()` so the five-layer order remains stable even when trimming to fit.
  - Extend post-turn compaction to summarize visible history plus memory coverage and save coverage counts into the new sidecar.
- Modify: `backend/main.py`
  - Update `/api/projects/{project_id}/conversation` clear logic so it removes `conversation.json`, `conversation_state.json`, and the legacy `conversation_compact_state.json`.
- Modify: `tests/test_chat_runtime.py`
  - Add RED/GREEN coverage for sidecar lifecycle, event persistence, memory upsert behavior, compact drift recovery, five-layer provider assembly, layered budget trimming, and stream/non-stream parity.
- Modify: `tests/test_main_api.py`
  - Cover clearing both the new and legacy sidecar files.
- Modify: `tests/test_stream_api.py`
  - Keep stream endpoint coverage aligned with any stream-path state persistence or ordering assertions added in runtime tests.
- Optional modify only if absolutely needed for readability: `tests/test_chat_context.py`
  - Add one focused assertion for provider-context assembly if `tests/test_chat_runtime.py` becomes too crowded.

## Shared Execution Notes

- Use the existing Python interpreter already used in this workspace:
  - `C:\Users\user\AppData\Local\Programs\Python\Python312-codex\python.exe`
- Use `unittest`, not `pytest`.
- Keep the implementation scoped to this subsystem. Do not drag in frontend work, search quality work, or正文门禁修复.
- Do not stage unrelated untracked docs that are already sitting in the worktree.
- Prefer small helper functions inside `backend/chat.py`; do not introduce a new memory framework.

### Task 1: Unify Conversation Sidecar Lifecycle

**Files:**
- Modify: `backend/chat.py`
- Modify: `backend/main.py`
- Test: `tests/test_chat_runtime.py`
- Test: `tests/test_main_api.py`

- [ ] **Step 1: Write the failing sidecar lifecycle tests**

```python
@mock.patch("backend.chat.OpenAI")
def test_load_conversation_state_returns_empty_state_when_file_is_missing(self, mock_openai):
    del mock_openai
    handler = ChatHandler(self._make_settings(projects_dir=projects_dir), engine)
    state = handler._load_conversation_state(project["id"], history=[])
    self.assertEqual(
        state,
        {
            "version": 1,
            "events": [],
            "memory_entries": [],
            "compact_state": None,
        },
    )

@mock.patch("backend.chat.OpenAI")
def test_load_conversation_state_migrates_legacy_compact_sidecar(self, mock_openai):
    del mock_openai
    legacy_path = project_path / "conversation_compact_state.json"
    legacy_path.write_text(
        json.dumps({"summary_text": "old summary", "source_message_count": 3}, ensure_ascii=False),
        encoding="utf-8",
    )
    state = handler._load_conversation_state(project["id"], history=[{"role": "user", "content": "x"}] * 3)
    self.assertEqual(state["compact_state"]["summary_text"], "old summary")
    self.assertFalse(legacy_path.exists())

@mock.patch("backend.chat.OpenAI")
def test_load_conversation_state_discards_drifted_compact_state(self, mock_openai):
    del mock_openai
    handler._save_conversation_state_atomically(
        project["id"],
        {
            "version": 1,
            "events": [],
            "memory_entries": [{"id": "mem-1", "category": "workspace", "source_key": "write:plan/outline.md", "source_event_ids": [], "text": "outline ready", "created_at": "2026-04-14T10:00:00"}],
            "compact_state": {
                "summary_text": "summary",
                "covered_visible_message_count": 9,
                "covered_memory_entry_count": 5,
                "last_compacted_at": "2026-04-14T10:00:00",
            },
        },
    )
    state = handler._load_conversation_state(project["id"], history=[{"role": "user", "content": "only one"}])
    self.assertIsNone(state["compact_state"])
    self.assertEqual(len(state["memory_entries"]), 1)

@mock.patch("backend.chat.OpenAI")
def test_load_conversation_state_renames_broken_json_and_recovers_empty_state(self, mock_openai):
    del mock_openai
    state_path = project_path / "conversation_state.json"
    state_path.write_text("{broken", encoding="utf-8")
    state = handler._load_conversation_state(project["id"], history=[])
    broken_backups = list(project_path.glob("conversation_state.json.broken-*"))
    self.assertEqual(state, {"version": 1, "events": [], "memory_entries": [], "compact_state": None})
    self.assertEqual(len(broken_backups), 1)
    self.assertFalse(state_path.exists())
```

```python
@mock.patch("backend.main.skill_engine.get_project_path")
def test_clear_conversation_removes_new_and_legacy_sidecars(self, mock_get_project_path):
    project_path = Path(tmpdir)
    (project_path / "conversation.json").write_text("[]", encoding="utf-8")
    (project_path / "conversation_state.json").write_text("{}", encoding="utf-8")
    (project_path / "conversation_compact_state.json").write_text("{}", encoding="utf-8")
    mock_get_project_path.return_value = project_path

    response = self.client.delete("/api/projects/proj-demo/conversation")

    self.assertEqual(response.status_code, 200)
    self.assertFalse((project_path / "conversation.json").exists())
    self.assertFalse((project_path / "conversation_state.json").exists())
    self.assertFalse((project_path / "conversation_compact_state.json").exists())
```

- [ ] **Step 2: Run the focused RED tests**

Run:

```powershell
& 'C:\Users\user\AppData\Local\Programs\Python\Python312-codex\python.exe' -m unittest `
  tests.test_chat_runtime.ChatRuntimeTests.test_load_conversation_state_returns_empty_state_when_file_is_missing `
  tests.test_chat_runtime.ChatRuntimeTests.test_load_conversation_state_migrates_legacy_compact_sidecar `
  tests.test_chat_runtime.ChatRuntimeTests.test_load_conversation_state_discards_drifted_compact_state `
  tests.test_chat_runtime.ChatRuntimeTests.test_load_conversation_state_renames_broken_json_and_recovers_empty_state `
  tests.test_main_api.WorkspaceApiTests.test_clear_conversation_removes_new_and_legacy_sidecars
```

Expected:
- Fail because `conversation_state.json` helpers do not exist yet.
- Fail because the API only removes `conversation.json` and the old compact sidecar.
- Fail because broken sidecar recovery is not implemented yet.

- [ ] **Step 3: Implement the minimal unified sidecar helpers**

```python
def _empty_conversation_state(self) -> Dict:
    return {
        "version": 1,
        "events": [],
        "memory_entries": [],
        "compact_state": None,
    }

def _get_conversation_state_path(self, project_id: str):
    project_path = self.skill_engine.get_project_path(project_id)
    if not project_path:
        return None
    return project_path / "conversation_state.json"

def _load_conversation_state(self, project_id: str, history: List[Dict] | None = None) -> Dict:
    # load new state if present
    # if missing, optionally migrate legacy conversation_compact_state.json
    # if JSON is broken, rename to conversation_state.json.broken-<timestamp> and return empty state
    # if compact coverage exceeds history or memory length, drop only compact_state

def _save_conversation_state_atomically(self, project_id: str, payload: Dict):
    # write temp file, then replace

def _clear_conversation_state_files(self, project_id: str):
    # remove conversation_state.json and legacy compact sidecar if they exist
```

- [ ] **Step 4: Wire the API cleanup path**

```python
# backend/main.py
@app.delete("/api/projects/{project_id}/conversation")
async def clear_conversation(project_id: str):
    ...
    conv_file.unlink(...)
    state_file = project_path / "conversation_state.json"
    if state_file.exists():
        state_file.unlink()
    legacy_compact = project_path / "conversation_compact_state.json"
    if legacy_compact.exists():
        legacy_compact.unlink()
    return {"status": "ok"}
```

- [ ] **Step 5: Run the focused GREEN tests**

Run the same command as Step 2.

Expected:
- All lifecycle tests pass.

### Task 2: Persist Successful Tool Events And Upsert Memory Entries

**Files:**
- Modify: `backend/chat.py`
- Test: `tests/test_chat_runtime.py`

- [ ] **Step 1: Write the failing event/memory persistence tests**

```python
@mock.patch("backend.chat.OpenAI")
def test_execute_read_material_file_persists_evidence_event_and_memory(self, mock_openai):
    del mock_openai
    material = engine.add_materials(project["id"], [str(material_path)])[0]
    result = handler._execute_tool(
        project["id"],
        self._make_tool_call("read_material_file", json.dumps({"material_id": material["id"]}, ensure_ascii=False)),
    )
    state = handler._load_conversation_state(project["id"], history=[])
    self.assertEqual(result["status"], "success")
    self.assertEqual(state["events"][0]["kind"], "read_material")
    self.assertEqual(state["memory_entries"][0]["category"], "evidence")
    self.assertEqual(state["memory_entries"][0]["source_key"], f"material:{material['id']}")

@mock.patch("backend.chat.OpenAI")
def test_write_file_upserts_workspace_memory_for_same_path(self, mock_openai):
    del mock_openai
    handler._execute_tool(project["id"], self._make_tool_call("write_file", json.dumps({"file_path": "plan/outline.md", "content": "# v1"}, ensure_ascii=False)))
    handler._execute_tool(project["id"], self._make_tool_call("write_file", json.dumps({"file_path": "plan/outline.md", "content": "# v2"}, ensure_ascii=False)))
    state = handler._load_conversation_state(project["id"], history=[])
    workspace_memories = [item for item in state["memory_entries"] if item["source_key"] == "write:plan/outline.md"]
    self.assertEqual(len(workspace_memories), 1)
    self.assertIn("plan/outline.md", workspace_memories[0]["text"])

@mock.patch("backend.chat.OpenAI")
def test_fetch_url_failure_does_not_persist_long_term_memory(self, mock_openai):
    del mock_openai
    handler._fetch_url = mock.Mock(return_value={"status": "error", "message": "403"})
    handler._execute_tool(project["id"], self._make_tool_call("fetch_url", json.dumps({"url": "https://example.com"}, ensure_ascii=False)))
    state = handler._load_conversation_state(project["id"], history=[])
    self.assertEqual(state["events"], [])
    self.assertEqual(state["memory_entries"], [])
```

- [ ] **Step 2: Run the focused RED tests**

Run:

```powershell
& 'C:\Users\user\AppData\Local\Programs\Python\Python312-codex\python.exe' -m unittest `
  tests.test_chat_runtime.ChatRuntimeTests.test_execute_read_material_file_persists_evidence_event_and_memory `
  tests.test_chat_runtime.ChatRuntimeTests.test_write_file_upserts_workspace_memory_for_same_path `
  tests.test_chat_runtime.ChatRuntimeTests.test_fetch_url_failure_does_not_persist_long_term_memory
```

Expected:
- Fail because `_execute_tool()` currently returns results but never writes event/memory state.

- [ ] **Step 3: Implement event builders and memory upsert**

```python
def _append_successful_tool_state(self, project_id: str, func_name: str, args: Dict, result: Dict):
    state = self._load_conversation_state(project_id, history=self._load_conversation(project_id))
    event = self._build_memory_event(project_id, func_name, args, result)
    if not event:
        return
    state["events"].append(event)
    memory_entry = self._build_memory_entry(func_name, event, result)
    if memory_entry:
        self._upsert_memory_entry(state["memory_entries"], memory_entry)
    self._save_conversation_state_atomically(project_id, state)

def _upsert_memory_entry(self, entries: List[Dict], entry: Dict):
    for index, existing in enumerate(entries):
        if existing.get("category") == entry["category"] and existing.get("source_key") == entry["source_key"]:
            entries[index] = entry
            return
    entries.append(entry)
```

Rules to encode:
- `read_material_file` and successful `fetch_url` map to `category="evidence"`.
- `read_file` and `write_file` map to `category="workspace"`.
- `write_file` uses `source_key="write:<normalized_path>"`.
- Failed tools do not persist state.

- [ ] **Step 4: Re-run the focused GREEN tests**

Run the same command as Step 2.

Expected:
- The new sidecar fills with high-value successful tool facts only.

### Task 3: Rebuild Provider Context Into Five Explicit Layers

**Files:**
- Modify: `backend/chat.py`
- Test: `tests/test_chat_runtime.py`
- Optional Test: `tests/test_chat_context.py`

- [ ] **Step 1: Write the failing provider-assembly tests**

```python
@mock.patch("backend.chat.OpenAI")
def test_build_provider_conversation_orders_compact_memory_visible_history_and_current_turn(self, mock_openai):
    del mock_openai
    handler._save_conversation(project["id"], [
        {"role": "user", "content": "old user"},
        {"role": "assistant", "content": "old assistant"},
        {"role": "user", "content": "recent user"},
        {"role": "assistant", "content": "recent assistant"},
    ])
    handler._save_conversation_state_atomically(
        project["id"],
        {
            "version": 1,
            "events": [],
            "memory_entries": [
                {
                    "id": "mem-1",
                    "created_at": "2026-04-14T10:00:00",
                    "category": "evidence",
                    "source_key": "material:mat-1",
                    "source_event_ids": ["evt-1"],
                    "text": "行业资料确认了三条增长驱动因素",
                }
            ],
            "compact_state": {
                "summary_text": "covered summary",
                "covered_visible_message_count": 2,
                "covered_memory_entry_count": 0,
                "last_compacted_at": "2026-04-14T10:05:00",
            },
        },
    )

    conversation = handler._build_provider_conversation(
        project["id"],
        handler._load_conversation(project["id"]),
        handler._build_persisted_user_message("current turn"),
    )

    self.assertEqual(conversation[0]["role"], "system")
    self.assertIn("[对话摘要]", conversation[1]["content"])
    self.assertIn("[工作记忆]", conversation[2]["content"])
    self.assertEqual(conversation[3]["content"], "recent user")
    self.assertEqual(conversation[4]["content"], "recent assistant")
    self.assertEqual(conversation[-1]["content"], "current turn")
```

```python
@mock.patch("backend.chat.OpenAI")
def test_fit_budget_preserves_five_layer_order_when_trimming(self, mock_openai):
    del mock_openai
    handler._estimate_tokens = mock.Mock(side_effect=[999999, 999999, 1200])
    conversation = handler._build_provider_conversation(...)
    fitted, _, _, _ = handler._fit_conversation_to_budget(conversation)
    self.assertEqual(fitted[0]["role"], "system")
    self.assertIn("[工作记忆]", fitted[2]["content"])
    self.assertEqual(fitted[-1]["content"], "current turn")

@mock.patch("backend.chat.OpenAI")
def test_fit_budget_trims_recent_visible_messages_before_memory_block(self, mock_openai):
    del mock_openai
    handler._estimate_tokens = mock.Mock(side_effect=[999999, 999999, 999999, 1500])
    conversation = handler._build_provider_conversation(...)
    fitted, _, _, _ = handler._fit_conversation_to_budget(conversation)
    assistant_blocks = [msg["content"] for msg in fitted if msg["role"] == "assistant"]
    self.assertTrue(any("[工作记忆]" in content for content in assistant_blocks))
    self.assertFalse(any(content == "old assistant to trim first" for content in assistant_blocks))
```

- [ ] **Step 2: Run the focused RED tests**

Run:

```powershell
& 'C:\Users\user\AppData\Local\Programs\Python\Python312-codex\python.exe' -m unittest `
  tests.test_chat_runtime.ChatRuntimeTests.test_build_provider_conversation_orders_compact_memory_visible_history_and_current_turn `
  tests.test_chat_runtime.ChatRuntimeTests.test_fit_budget_preserves_five_layer_order_when_trimming `
  tests.test_chat_runtime.ChatRuntimeTests.test_fit_budget_trims_recent_visible_messages_before_memory_block
```

Expected:
- Fail because `_build_provider_conversation()` currently injects only `system + compact summary + flattened history + current turn`.
- Fail because `_fit_conversation_to_budget()` still delegates to `_compress_conversation()` which flattens everything back into generic recent messages.
- Fail because trimming priority is not yet locked to “visible messages before memory block”.

- [ ] **Step 3: Implement layer builders and layered trimming**

```python
def _build_provider_segments(self, project_id: str, history: List[Dict], current_user_message: Dict) -> Dict[str, List[Dict]]:
    state = self._load_conversation_state(project_id, history=history)
    compact_state = state.get("compact_state")
    memory_entries = self._select_recent_memory_entries(state)
    visible_history = self._select_recent_visible_history(history, compact_state)
    return {
        "system": [{"role": "system", "content": self._build_system_prompt(project_id)}],
        "compact_summary": self._build_compact_summary_segment(compact_state),
        "recent_memory": self._build_memory_segment(memory_entries),
        "recent_visible_messages": [self._to_provider_message(project_id, item, include_images=False) for item in visible_history if ...],
        "current_turn": [self._to_provider_message(project_id, current_user_message, include_images=True)],
    }

def _build_memory_segment(self, memory_entries: List[Dict]) -> List[Dict]:
    if not memory_entries:
        return []
    lines = ["[工作记忆]"]
    for item in memory_entries:
        lines.append(f"- [{item['category']}] {item['text']}")
    return [{"role": "assistant", "content": "\n".join(lines)}]
```

```python
def _fit_segments_to_budget(self, segments: Dict[str, List[Dict]]) -> tuple[List[Dict], int, bool, ResolvedContextPolicy]:
    # flatten in fixed order only after trimming decisions are made
    # trim recent visible messages first
    # then trim recent memory
    # only then fall back to summary-heavy compression
```

- [ ] **Step 4: Re-run the focused GREEN tests**

Run the same command as Step 2.

Expected:
- Provider context now always respects:
  - `system prompt`
  - `compact summary`
  - `recent memory`
  - `recent visible messages`
  - `current turn`
- Budget trimming removes the right layer first instead of collapsing the order.

### Task 4: Extend Post-Turn Compaction And Keep Stream/Non-Stream In Sync

**Files:**
- Modify: `backend/chat.py`
- Test: `tests/test_chat_runtime.py`
- Test: `tests/test_stream_api.py`

- [ ] **Step 1: Write the failing compaction and parity tests**

```python
@mock.patch("backend.chat.OpenAI")
def test_chat_auto_compact_covers_visible_messages_and_memory_entries(self, mock_openai):
    ...
    token_usage = {
        "usage_source": "provider",
        "context_used_tokens": 195000,
        "effective_max_tokens": 200000,
    }
    state = handler._load_conversation_state(project["id"], history=handler._load_conversation(project["id"]))
    self.assertEqual(state["compact_state"]["covered_visible_message_count"], 4)
    self.assertEqual(state["compact_state"]["covered_memory_entry_count"], 2)

@mock.patch("backend.chat.OpenAI")
def test_chat_stream_persists_read_memory_for_next_turn(self, mock_openai):
    mock_openai.return_value.chat.completions.create.side_effect = [
        iter([
            self._make_chunk(
                tool_calls=[self._make_stream_tool_call_chunk(0, id="call_1", name="read_file", arguments='{"file_path":"plan/outline.md"}')]
            ),
            self._make_chunk(tool_calls=[self._make_stream_tool_call_chunk(0, arguments="")]),
            self._make_usage_chunk(prompt_tokens=1500, completion_tokens=60, total_tokens=1560),
        ]),
        iter([
            self._make_chunk(content="续写"),
            self._make_usage_chunk(prompt_tokens=1600, completion_tokens=80, total_tokens=1680),
        ]),
    ]
    list(handler.chat_stream(project["id"], "先读 outline"))
    conversation = handler._build_provider_conversation(
        project["id"],
        handler._load_conversation(project["id"]),
        handler._build_persisted_user_message("继续"),
    )
    self.assertTrue(any("[工作记忆]" in msg.get("content", "") for msg in conversation if msg["role"] == "assistant"))
```

- [ ] **Step 2: Run the focused RED tests**

Run:

```powershell
& 'C:\Users\user\AppData\Local\Programs\Python\Python312-codex\python.exe' -m unittest `
  tests.test_chat_runtime.ChatRuntimeTests.test_chat_auto_compact_covers_visible_messages_and_memory_entries `
  tests.test_chat_runtime.ChatRuntimeTests.test_chat_stream_persists_read_memory_for_next_turn
```

Expected:
- Fail because post-turn compaction only tracks visible-message coverage.
- Fail because stream and non-stream paths do not both rebuild context from sidecar memory.

- [ ] **Step 3: Implement compaction coverage and stream parity**

```python
def _build_compaction_summary_messages(self, project_id: str, history: List[Dict], state: Dict) -> List[Dict]:
    messages = []
    for message in history:
        provider_message = self._to_provider_message(project_id, message, include_images=False)
        if provider_message:
            messages.append(provider_message)
    memory_segment = self._build_memory_segment(self._select_recent_memory_entries(state, include_all=True))
    messages.extend(memory_segment)
    return messages

def _finalize_post_turn_compaction(self, project_id: str, history: List[Dict], token_usage: Dict) -> Dict:
    state = self._load_conversation_state(project_id, history=history)
    ...
    state["compact_state"] = {
        "summary_text": summary_text,
        "covered_visible_message_count": len(history),
        "covered_memory_entry_count": len(state["memory_entries"]),
        "last_compacted_at": now,
    }
    self._save_conversation_state_atomically(project_id, state)
```

Also ensure:
- `chat()` and `chat_stream()` both call the same provider-context assembly helper.
- `chat()` and `chat_stream()` both save visible conversation first, then finalize compaction from the same sidecar-aware state.

- [ ] **Step 4: Re-run the focused GREEN tests**

Run the same command as Step 2.

Expected:
- Both entry points now share the same memory-aware continuation model.

### Task 5: Keep The Sidecar Itself Small After Compaction

**Files:**
- Modify: `backend/chat.py`
- Test: `tests/test_chat_runtime.py`

- [ ] **Step 1: Write the failing sidecar-size-control tests**

```python
@mock.patch("backend.chat.OpenAI")
def test_finalize_post_turn_compaction_drops_covered_memory_entries_and_slims_old_events(self, mock_openai):
    del mock_openai
    handler._save_conversation_state_atomically(
        project["id"],
        {
            "version": 1,
            "events": [
                {
                    "id": "evt-1",
                    "kind": "read_material",
                    "created_at": "2026-04-14T10:00:00",
                    "source_ref": {"material_id": "mat-1", "file_path": None, "url": None},
                    "title": "访谈纪要",
                    "summary": "old summary",
                    "excerpt": "old excerpt",
                    "metadata": {"truncated": False},
                }
            ],
            "memory_entries": [
                {
                    "id": "mem-1",
                    "created_at": "2026-04-14T10:00:00",
                    "category": "evidence",
                    "source_key": "material:mat-1",
                    "source_event_ids": ["evt-1"],
                    "text": "old memory",
                }
            ],
            "compact_state": None,
        },
    )
    handler._summarize_messages = mock.Mock(return_value="summary")
    usage = {"usage_source": "provider", "context_used_tokens": 195000, "effective_max_tokens": 200000}
    handler._finalize_post_turn_compaction(project["id"], handler._load_conversation(project["id"]), usage)
    state = handler._load_conversation_state(project["id"], history=handler._load_conversation(project["id"]))
    self.assertEqual(state["memory_entries"], [])
    self.assertEqual(
        state["events"][0],
        {
            "id": "evt-1",
            "kind": "read_material",
            "created_at": "2026-04-14T10:00:00",
            "source_ref": {"material_id": "mat-1", "file_path": None, "url": None},
            "title": "访谈纪要",
        },
    )

@mock.patch("backend.chat.OpenAI")
def test_finalize_post_turn_compaction_trims_old_excerpts_when_sidecar_is_still_too_large(self, mock_openai):
    del mock_openai
    handler._sidecar_needs_excerpt_trim = mock.Mock(return_value=True)
    ...
    handler._finalize_post_turn_compaction(project["id"], handler._load_conversation(project["id"]), usage)
    state = handler._load_conversation_state(project["id"], history=handler._load_conversation(project["id"]))
    self.assertNotIn("excerpt", state["events"][0])
```

- [ ] **Step 2: Run the focused RED tests**

Run:

```powershell
& 'C:\Users\user\AppData\Local\Programs\Python\Python312-codex\python.exe' -m unittest `
  tests.test_chat_runtime.ChatRuntimeTests.test_finalize_post_turn_compaction_drops_covered_memory_entries_and_slims_old_events `
  tests.test_chat_runtime.ChatRuntimeTests.test_finalize_post_turn_compaction_trims_old_excerpts_when_sidecar_is_still_too_large
```

Expected:
- Fail because compaction currently records coverage counts but does not slim old sidecar payloads.
- Fail because there is no final excerpt-trimming fallback yet.

- [ ] **Step 3: Implement post-compaction sidecar slimming**

```python
def _shrink_compacted_state(self, state: Dict) -> Dict:
    compact = state.get("compact_state") or {}
    covered_memory = compact.get("covered_memory_entry_count", 0)
    covered_entries = state["memory_entries"][:covered_memory]
    covered_event_ids = {
        event_id
        for entry in covered_entries
        for event_id in entry.get("source_event_ids", [])
    }
    state["memory_entries"] = state["memory_entries"][covered_memory:]
    slimmed_events = []
    for event in state.get("events", []):
        if event.get("id") in covered_event_ids:
            slimmed_events.append(
                {
                    "id": event["id"],
                    "kind": event["kind"],
                    "created_at": event["created_at"],
                    "source_ref": event["source_ref"],
                    "title": event.get("title"),
                }
            )
        else:
            slimmed_events.append(event)
    if self._sidecar_needs_excerpt_trim(slimmed_events, state["memory_entries"]):
        for event in slimmed_events:
            event.pop("excerpt", None)
    state["events"] = slimmed_events
    return state
```

- [ ] **Step 4: Re-run the focused GREEN tests**

Run the same command as Step 2.

Expected:
- Covered memory is dropped after compaction.
- Older covered events retain only source pointers and titles.
- If the sidecar is still too large after slimming covered events, old excerpts are removed next.

### Task 6: Run The Full Regression Slice And Optional Worklist Sync

**Files:**
- Modify: `docs/current-worklist.md`
- Test: `tests/test_chat_runtime.py`
- Test: `tests/test_main_api.py`
- Test: `tests/test_stream_api.py`
- Test: `tests/test_chat_context.py`

- [ ] **Step 1: Run the regression slice**

Run:

```powershell
& 'C:\Users\user\AppData\Local\Programs\Python\Python312-codex\python.exe' -m unittest `
  tests.test_chat_context `
  tests.test_chat_runtime `
  tests.test_main_api `
  tests.test_stream_api
```

Expected:
- The targeted session-memory regression slice passes end-to-end.

- [ ] **Step 2: Sanity-check no unrelated packaging or API tests were broken**

Run:

```powershell
& 'C:\Users\user\AppData\Local\Programs\Python\Python312-codex\python.exe' -m unittest `
  tests.test_context_policy `
  tests.test_models
```

Expected:
- Still PASS.

- [ ] **Step 3: Optionally update the worklist item after the code is actually done**

```markdown
## 新增待排查问题（2026-04-14）

1. 材料读取结果没有形成稳定工作记忆
- 状态：`已完成`
- 设计稿：`docs/superpowers/specs/2026-04-14-session-memory-rearchitecture-design.md`
- 计划：`docs/superpowers/plans/2026-04-14-session-memory-rearchitecture.md`
- 结论：新增 `conversation_state.json` 作为轻量 sidecar，成功工具读取会沉淀为结构化记忆，并按 `system -> compact summary -> recent memory -> recent visible messages -> current turn` 回注上下文。
```

- [ ] **Step 4: Commit the scoped implementation**

```bash
git add backend/chat.py backend/main.py tests/test_chat_runtime.py tests/test_main_api.py tests/test_stream_api.py
git commit -m "feat: add structured session memory sidecar"
```

If you also updated the worklist or `tests/test_chat_context.py`, stage those explicitly in a follow-up step instead of relying on the default `git add`.
