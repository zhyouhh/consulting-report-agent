# Stage Advance Gates Implementation Plan

> **Status**: Ready for implementation. Design approved after 9 review rounds (2 Claude eng/UX + 7 codex xhigh) on 2026-04-17. No open blockers.
> **Scope**: Rebuild stage advancement from file-existence projection to "files ready + checkpoint stamps + quality gates", with 4 hard gates, 2 quality gates, 1 outline pass-through stamp, and a real `done` terminal state.
> **Design doc**: `../specs/2026-04-17-stage-advance-gates-design.md`.
> **Worklist entry**: `docs/current-worklist.md` item 8 (next priority).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild stage advancement so each transition requires file readiness, an explicit user-confirmation checkpoint (for hard gates), and a quality threshold (for S2→S3 and S3→S4). Outline confirmation is a single durable token that unlocks the entire S2-S4 writing corridor. Hard gates (S1→S2, S4→S5, S5→S6-S7, S6-S7→done) require explicit user acknowledgment via UI button or conversation keyword. When a tool call is blocked, the model must surface the error to the user instead of silently dumping content into chat.

**Architecture:** Introduce a per-project `stage_checkpoints.json` as the single source of truth for user confirmations. `backend/skill.py:_infer_stage_state` switches from file-existence projection to "files AND checkpoints AND quality gates". `backend/chat.py` learns to recognize stage keywords and (de)mark checkpoints in `_build_turn_context`; `_should_allow_non_plan_write` grants a blanket pass when `outline_confirmed_at` is set. `backend/main.py` gains idempotent checkpoint endpoints. Frontend `WorkspacePanel` renders one context-aware primary button plus a rollback menu. `skill/SKILL.md` gains explicit advancement conditions and a "must-surface-errors" clause. Self-signature patterns in `review-checklist.md` / `delivery-log.md` are blocked at the `write_file` layer.

**Tech Stack:** Python (pydantic, stdlib `json`/`pathlib`/`re`/`datetime`), FastAPI endpoints, React + Tailwind for WorkspacePanel, `pytest`/`unittest.mock` for backend, `node:test` for frontend.

---

## File Map

- Create: `tests/test_stage_checkpoints.py` — unit coverage for checkpoint storage helpers, migration, and idempotency.
- Create: `tests/test_stage_quality_gates.py` — unit coverage for `_resolve_length_targets`, `_has_enough_data_log_sources`, `_has_enough_analysis_refs`.
- Modify: `backend/skill.py`
  - Add `_load_stage_checkpoints` / `_save_stage_checkpoint` / `_clear_stage_checkpoint` / `_clear_stage_checkpoint_cascade` (all private).
  - **Add public service method** `record_stage_checkpoint(project_id: str, key: str, action: str) -> dict`. This is the sole Web-layer entry point for checkpoint mutations—`backend/main.py` must call this instead of reaching into private underscored methods. Method acquires the project lock, dispatches to save/cascade-clear, calls `_sync_stage_tracking_files`, then returns `{"status": "ok", "key": ..., "timestamp"|"cleared": ...}`.
  - **Add path guard helper** `is_protected_stage_checkpoints_path(normalized_path: str) -> bool`. `ChatHandler._execute_tool_call` calls this before any `write_file` hits disk (Task 5 Step 3). Per spec §6.3, the model must never be able to forge stage stamps via `write_file`—only the 5 checkpoint endpoints (via `record_stage_checkpoint`) may mutate `stage_checkpoints.json`.
  - Add `_last_evidence_write_at(project_path) -> datetime | None` returning the max mtime among `plan/notes.md` / `plan/references.md` / `plan/data-log.md` / `plan/analysis-notes.md` (used for S2/S3 stall detection).
  - Add `_resolve_length_targets` / `_has_enough_data_log_sources` / `_has_enough_analysis_refs` / `_count_quality_progress`.
  - Rewrite `_infer_stage_state` to combine file readiness + checkpoints + quality thresholds, return `quality_progress` and `next_stage_hint`.
  - Add `_backfill_stage_checkpoints_if_missing` migration helper (only backfills `outline_confirmed_at`).
  - Extend `_has_effective_report_draft` with a word-count floor that strips Markdown before counting.
  - Add `validate_self_signature` for write-file interceptor; auto-disable interception when corresponding checkpoint timestamp already exists.
- Modify: `backend/chat.py`
  - Add **module-level** `_get_project_request_lock(project_id: str) -> threading.RLock` operating on the existing `_PROJECT_REQUEST_LOCKS` / `_PROJECT_REQUEST_LOCKS_GUARD` globals. Refactor the current `ChatHandler._get_project_request_lock` instance method to delegate to this module-level function (so `backend/main.py` and any future helpers can import it directly).
  - Add `_detect_stage_keyword(user_message, current_stage)` with stage-aware weak keywords (**S4 disabled**; weak keywords only fire in S1/S5/S6/S7) and anchored-end question detection; wire into `_build_turn_context` so it mutates checkpoints (via `_clear_stage_checkpoint_cascade` for rollbacks) under the project lock before context assembly.
  - Update `_should_allow_non_plan_write` priority: `_is_non_plan_write_blocking_message` short-circuits False for this turn (without clearing stamps), then `outline_confirmed_at` presence returns True, then existing fallbacks.
  - Intercept `write_file` calls that try to write self-signed `review-checklist.md` / `delivery-log.md` content; skip interception when the matching checkpoint stamp is already set.
  - Inject `system_notice` event into both streaming and non-streaming chat outputs when `write_file` / `fetch_url` returns `status: error`. Inject only once per turn (tracked via `_turn_context["system_notice_emitted"]`). Specific wiring points:
    - **`chat_stream`** (`_chat_stream_unlocked` at `backend/chat.py:1202`): after each tool execution that returns `status: error`, drain `_turn_context["pending_system_notices"]` and yield each as `{"type": "system_notice", ...}` immediately before the next tool-call iteration or final content chunk.
    - **`chat`** (`_chat_unlocked` at `backend/chat.py:1396`): before returning `{"content": ..., "token_usage": ...}`, if `pending_system_notices` is non-empty, include them in the response as a new `system_notices` field.
- Modify: `backend/models.py`
  - Add optional `system_notices: Optional[List[SystemNotice]] = None` to `ChatResponse` and define the `SystemNotice` pydantic model with fields `category: str`, `path: Optional[str]`, `reason: str`, `user_action: str`.
- Modify: `backend/main.py`
  - Add five idempotent endpoints under `/api/projects/{project_id}/checkpoints/{name}` with `?action=set|clear` param; all writes acquire the project-level lock from `backend/chat.py:_PROJECT_REQUEST_LOCKS`.
  - Extend `/api/projects/{project_id}/workspace` response with `checkpoints`, `length_targets` (`data_log_min` / `analysis_refs_min` / `report_word_floor` / `expected_length` / `fallback_used`), `quality_progress` (`label` / `current` / `target`), and `next_stage_hint` (`"S6"` or `"S7"` after S5 is set; `null` otherwise).
- Modify: `skill/SKILL.md`
  - Append advancement condition to each stage description.
  - Add a "工具错误处理" section describing the must-surface-errors contract.
- Create: `frontend/src/components/StageAdvanceControl.jsx` — S4 dual buttons (continue-writing + start-review) and other stages' single button, plus rollback menu with tiered options (primary "adjust outline" stays in a conversation-only path that does NOT clear checkpoints; secondary "full reset" clears cascade).
- Create: `frontend/src/utils/stageAdvanceConfig.mjs` — per-stage button config including dual-button mode for S4, tiered rollback options, and de-technicalized confirmation dialog copy.
- Modify: `frontend/src/components/WorkspacePanel.jsx` — mount `StageAdvanceControl`; render `quality_progress` inline counter for S2/S3; show fallback-used hint when `length_targets.fallback_used=true`.
- Modify: `frontend/src/components/ChatPanel.jsx` — render new `system_notice` stream event as a distinct gray-bordered info card between assistant messages.
- Create: `frontend/tests/stageAdvanceControl.test.mjs` — coverage for per-stage button rendering, S4 dual-button transitions, disabled state, rollback menu tiers, dialog copy.
- Create: `frontend/tests/systemNoticeRendering.test.mjs` — verify ChatPanel identifies `system_notice` events and renders the distinctive card.
- Modify: `tests/smoke_packaged_app.py` — extend the smoke run to exercise a checkpoint endpoint and verify stage advancement requires the checkpoint.
- Modify: `tests/test_chat_runtime.py` — update stage-projection assertions to match new rules.
- Modify: `tests/test_skill_engine.py` — update stage-projection assertions.
- Modify: `tests/test_main_api.py` — add checkpoint endpoint coverage.

Implementation note: the quality thresholds scale with `expected_length` but cap at N=12 / M=8; do not widen beyond these caps even if a user sets `expected_length=50000`. Caps protect against "凑数式" sourcing.

Implementation note: stage keyword detection is deliberately short-phrase exact-match to avoid misfires on questioning sentences ("这份大纲 ok 吗"). Rollback keywords take precedence over advance keywords when both appear in the same user message.

Implementation note: the self-signature interceptor must only guard **new writes**; existing files that already contain self-signed fields are not rewritten. This matches the "no surprise deletes" principle and preserves old-project contents.

---

### Task 1: Introduce Stage Checkpoints Storage with RED Tests

**Files:**
- Create: `tests/test_stage_checkpoints.py`
- Modify: `backend/skill.py`

- [ ] **Step 1: Add failing tests for checkpoint storage helpers**

```python
import json
from pathlib import Path

from backend.skill import SkillEngine


def test_load_stage_checkpoints_returns_empty_when_file_missing(tmp_path):
    engine = SkillEngine(projects_dir=tmp_path, skill_dir=Path("skill"))
    project_dir = tmp_path / "proj-x"
    project_dir.mkdir()
    assert engine._load_stage_checkpoints(project_dir) == {}


def test_save_stage_checkpoint_is_idempotent_on_repeat_set(tmp_path):
    engine = SkillEngine(projects_dir=tmp_path, skill_dir=Path("skill"))
    project_dir = tmp_path / "proj-x"
    project_dir.mkdir()
    first = engine._save_stage_checkpoint(project_dir, "outline_confirmed_at")
    second = engine._save_stage_checkpoint(project_dir, "outline_confirmed_at")
    assert first == second  # first timestamp preserved


def test_clear_stage_checkpoint_removes_key(tmp_path):
    engine = SkillEngine(projects_dir=tmp_path, skill_dir=Path("skill"))
    project_dir = tmp_path / "proj-x"
    project_dir.mkdir()
    engine._save_stage_checkpoint(project_dir, "review_started_at")
    engine._clear_stage_checkpoint(project_dir, "review_started_at")
    assert "review_started_at" not in engine._load_stage_checkpoints(project_dir)
```

- [ ] **Step 2: Implement storage helpers in `backend/skill.py`**

Add constants and methods near the top of `SkillEngine`:

```python
STAGE_CHECKPOINTS_FILENAME = "stage_checkpoints.json"
STAGE_CHECKPOINT_KEYS = {
    "outline_confirmed_at",
    "review_started_at",
    "review_passed_at",
    "presentation_ready_at",
    "delivery_archived_at",
}

def _stage_checkpoints_path(self, project_path: Path) -> Path:
    return project_path / self.STAGE_CHECKPOINTS_FILENAME

def _load_stage_checkpoints(self, project_path: Path) -> dict[str, str]:
    path = self._stage_checkpoints_path(project_path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {k: v for k, v in data.items() if k in self.STAGE_CHECKPOINT_KEYS and isinstance(v, str)}

def _read_raw_stage_checkpoints(self, project_path: Path) -> dict:
    """Read the raw checkpoint JSON preserving any non-STAGE_CHECKPOINT_KEYS entries
    (such as the `__migrated_at` marker). Used by writers that must not drop markers."""
    path = self._stage_checkpoints_path(project_path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data

def _write_raw_stage_checkpoints(self, project_path: Path, data: dict) -> None:
    self._stage_checkpoints_path(project_path).write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

def _save_stage_checkpoint(self, project_path: Path, key: str) -> str:
    if key not in self.STAGE_CHECKPOINT_KEYS:
        raise ValueError(f"unknown checkpoint key: {key}")
    raw = self._read_raw_stage_checkpoints(project_path)
    if key in raw and isinstance(raw[key], str):
        return raw[key]
    timestamp = datetime.now().isoformat(timespec="seconds")
    raw[key] = timestamp
    self._write_raw_stage_checkpoints(project_path, raw)
    return timestamp

def _clear_stage_checkpoint(self, project_path: Path, key: str) -> None:
    if key not in self.STAGE_CHECKPOINT_KEYS:
        raise ValueError(f"unknown checkpoint key: {key}")
    raw = self._read_raw_stage_checkpoints(project_path)
    if key not in raw:
        return
    del raw[key]
    self._write_raw_stage_checkpoints(project_path, raw)
```

- [ ] **Step 3: Add RED test for `__migrated_at` marker preservation across save/clear**

Critical invariant: both `_save_stage_checkpoint` and `_clear_stage_checkpoint` must preserve any non-STAGE_CHECKPOINT_KEYS field (such as the migration marker). Add:

```python
def test_save_checkpoint_preserves_migration_marker(tmp_path):
    engine = SkillEngine(projects_dir=tmp_path, skill_dir=Path("skill"))
    project_dir = tmp_path / "proj-x"
    project_dir.mkdir()
    # Seed a raw checkpoints file containing only the marker
    (project_dir / "stage_checkpoints.json").write_text(
        json.dumps({"__migrated_at": "2026-04-17T12:00:00"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    engine._save_stage_checkpoint(project_dir, "outline_confirmed_at")
    raw = json.loads((project_dir / "stage_checkpoints.json").read_text(encoding="utf-8"))
    assert raw.get("__migrated_at") == "2026-04-17T12:00:00"
    assert "outline_confirmed_at" in raw


def test_clear_checkpoint_preserves_migration_marker(tmp_path):
    engine = SkillEngine(projects_dir=tmp_path, skill_dir=Path("skill"))
    project_dir = tmp_path / "proj-x"
    project_dir.mkdir()
    (project_dir / "stage_checkpoints.json").write_text(
        json.dumps(
            {"__migrated_at": "2026-04-17T12:00:00", "outline_confirmed_at": "2026-04-17T12:01:00"},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    engine._clear_stage_checkpoint(project_dir, "outline_confirmed_at")
    raw = json.loads((project_dir / "stage_checkpoints.json").read_text(encoding="utf-8"))
    assert raw.get("__migrated_at") == "2026-04-17T12:00:00"
    assert "outline_confirmed_at" not in raw
```

Apply the same pattern later to `_clear_stage_checkpoint_cascade` (already preserves marker per Task 3 Step 4) and add parallel RED test.

- [ ] **Step 4: Run tests, verify GREEN**

```
.venv\Scripts\python -m unittest tests.test_stage_checkpoints -v
```

All five tests must pass.

---

### Task 2: Add Quality Gate Helpers with RED Tests

**Files:**
- Create: `tests/test_stage_quality_gates.py`
- Modify: `backend/skill.py`

- [ ] **Step 1: Add failing tests for length target resolution**

Fixtures must use the **real** project-overview format produced by `backend/skill.py:_populate_v2_plan_files`, which writes `**预期篇幅**: <value>` on a single line with colon separator. Do NOT use `"预期篇幅\n6000"` (newline between key and value)—that format is never written in production and would make the regex look cross-line when it shouldn't need to.

```python
def _make_project_with_overview(tmp_path, length_line):
    """Create a project dir with plan/project-overview.md containing the given '预期篇幅' line."""
    project_dir = tmp_path / "proj"
    (project_dir / "plan").mkdir(parents=True)
    (project_dir / "plan" / "project-overview.md").write_text(
        f"# 项目概览\n\n**预期篇幅**: {length_line}\n",
        encoding="utf-8",
    )
    return project_dir


def test_resolve_length_targets_parses_plain_integer(tmp_path):
    engine = SkillEngine(projects_dir=tmp_path, skill_dir=Path("skill"))
    project_dir = _make_project_with_overview(tmp_path, "6000字")
    targets = engine._resolve_length_targets(project_dir)
    assert targets["expected_length"] == 6000
    assert targets["data_log_min"] == 8
    assert targets["analysis_refs_min"] == 5
    assert targets["report_word_floor"] == 4200
    assert targets["fallback_used"] is False


def test_resolve_length_targets_parses_range_takes_max(tmp_path):
    engine = SkillEngine(projects_dir=tmp_path, skill_dir=Path("skill"))
    project_dir = _make_project_with_overview(tmp_path, "5000-8000字")
    targets = engine._resolve_length_targets(project_dir)
    assert targets["expected_length"] == 8000


def test_resolve_length_targets_caps_at_upper_bound(tmp_path):
    engine = SkillEngine(projects_dir=tmp_path, skill_dir=Path("skill"))
    project_dir = _make_project_with_overview(tmp_path, "50000字")
    targets = engine._resolve_length_targets(project_dir)
    assert targets["data_log_min"] == 12
    assert targets["analysis_refs_min"] == 8


def test_resolve_length_targets_defaults_when_unparseable(tmp_path):
    engine = SkillEngine(projects_dir=tmp_path, skill_dir=Path("skill"))
    project_dir = _make_project_with_overview(tmp_path, "待定")
    targets = engine._resolve_length_targets(project_dir)
    assert targets["expected_length"] == 3000
    assert targets["fallback_used"] is True


def test_has_enough_data_log_sources_counts_only_entries_with_evidence(tmp_path):
    # DL-001 has URL, DL-002 has material id, DL-003 has nothing
    content = """
### [DL-001] 条目一
- 来源：https://example.com/article

### [DL-002] 条目二
- 来源：material:abc-123

### [DL-003] 条目三
- 来源：我自己的印象
"""
    project_dir = _make_project_with_file(tmp_path, "plan/data-log.md", content)
    engine = SkillEngine(projects_dir=tmp_path, skill_dir=Path("skill"))
    assert engine._has_enough_data_log_sources(project_dir, min_count=2) is True
    assert engine._has_enough_data_log_sources(project_dir, min_count=3) is False


def test_has_enough_analysis_refs_deduplicates_and_requires_dl_match(tmp_path):
    data_log = "### [DL-001] a\n### [DL-002] b\n### [DL-003] c"
    analysis = "参见 [DL-001]，再看 [DL-001]，还有 [DL-002]，但 [DL-999] 不存在"
    project_dir = _make_project(tmp_path)
    (project_dir / "plan" / "data-log.md").write_text(data_log, encoding="utf-8")
    (project_dir / "plan" / "analysis-notes.md").write_text(analysis, encoding="utf-8")
    engine = SkillEngine(projects_dir=tmp_path, skill_dir=Path("skill"))
    assert engine._has_enough_analysis_refs(project_dir, min_refs=2) is True
    assert engine._has_enough_analysis_refs(project_dir, min_refs=3) is False
```

- [ ] **Step 2: Implement `_resolve_length_targets` in `backend/skill.py`**

Two regex patterns, tried in order. Primary: the real inline format `**预期篇幅**: 6000字`. Secondary: the heading form `## 预期篇幅\n<value>` produced by `backend/skill.py:257-258` when the original template lacks the placeholder.

```python
_EXPECTED_LENGTH_LINE_PATTERN = re.compile(r"预期篇幅[^\n]*?[:：]\s*([^\n]+)")
_EXPECTED_LENGTH_HEADING_PATTERN = re.compile(
    r"^##\s*预期篇幅\s*\n\s*([^\n]+)", re.MULTILINE
)

def _resolve_length_targets(self, project_path: Path) -> dict:
    overview_path = project_path / "plan" / "project-overview.md"
    expected = 3000
    fallback_used = True
    if overview_path.exists():
        text = overview_path.read_text(encoding="utf-8")
        for pattern in (self._EXPECTED_LENGTH_LINE_PATTERN, self._EXPECTED_LENGTH_HEADING_PATTERN):
            match = pattern.search(text)
            if not match:
                continue
            nums = re.findall(r"\d+", match.group(1))
            if nums:
                expected = max(int(n) for n in nums)
                fallback_used = False
                break
    data_log_min = min(12, math.ceil(expected / 1000 * 1.3))
    analysis_refs_min = min(8, math.ceil(expected / 1000 * 0.8))
    return {
        "expected_length": expected,
        "data_log_min": max(3, data_log_min),
        "analysis_refs_min": max(2, analysis_refs_min),
        "report_word_floor": int(expected * 0.7),
        "fallback_used": fallback_used,
    }
```

Add RED test for heading fallback:

```python
def test_resolve_length_targets_parses_heading_form(tmp_path):
    engine = SkillEngine(projects_dir=tmp_path, skill_dir=Path("skill"))
    project_dir = tmp_path / "proj"
    (project_dir / "plan").mkdir(parents=True)
    (project_dir / "plan" / "project-overview.md").write_text(
        "# 项目概览\n\n## 预期篇幅\n5000字\n",
        encoding="utf-8",
    )
    targets = engine._resolve_length_targets(project_dir)
    assert targets["expected_length"] == 5000
    assert targets["fallback_used"] is False
```

- [ ] **Step 3: Implement data-log and analysis-refs counters**

```python
_DL_ENTRY_PATTERN = re.compile(r"^###\s*\[(DL-[^\]]+)\]", re.MULTILINE)
_DL_REFERENCE_PATTERN = re.compile(r"\[(DL-[^\]]+)\]")
_EVIDENCE_MARKERS = (
    re.compile(r"https?://"),
    re.compile(r"material:[a-zA-Z0-9\-]+"),
    re.compile(r"^(访谈|调研)[:：]", re.MULTILINE),
)

def _has_enough_data_log_sources(self, project_path: Path, min_count: int) -> bool:
    data_log = project_path / "plan" / "data-log.md"
    if not data_log.exists():
        return False
    text = data_log.read_text(encoding="utf-8")
    entries = list(self._DL_ENTRY_PATTERN.finditer(text))
    if len(entries) < min_count:
        return False
    # Each entry body runs from its match to the next entry header (or EOF).
    valid = 0
    for idx, match in enumerate(entries):
        start = match.end()
        end = entries[idx + 1].start() if idx + 1 < len(entries) else len(text)
        body = text[start:end]
        if any(pattern.search(body) for pattern in self._EVIDENCE_MARKERS):
            valid += 1
    return valid >= min_count

def _has_enough_analysis_refs(self, project_path: Path, min_refs: int) -> bool:
    analysis = project_path / "plan" / "analysis-notes.md"
    data_log = project_path / "plan" / "data-log.md"
    if not analysis.exists() or not data_log.exists():
        return False
    dl_ids = {m.group(1) for m in self._DL_ENTRY_PATTERN.finditer(data_log.read_text(encoding="utf-8"))}
    refs = {m.group(1) for m in self._DL_REFERENCE_PATTERN.finditer(analysis.read_text(encoding="utf-8"))}
    return len(refs & dl_ids) >= min_refs
```

- [ ] **Step 4: Run quality-gate tests, verify GREEN**

```
.venv\Scripts\python -m unittest tests.test_stage_quality_gates -v
```

---

### Task 3: Rewrite `_infer_stage_state` Using Checkpoints + Quality Gates

**Files:**
- Modify: `backend/skill.py`
- Modify: `tests/test_skill_engine.py`
- Modify: `tests/test_chat_runtime.py`

- [ ] **Step 1: Add RED tests for the new projection rules**

In `tests/test_skill_engine.py`:

```python
def test_infer_stage_holds_at_s1_without_outline_checkpoint(self):
    # Create project with all S1 files present but no outline_confirmed_at
    project_dir = self._make_project_with_all_s1_files()
    state = self.engine._infer_stage_state(project_dir)
    self.assertEqual(state["stage_code"], "S1")

def test_infer_stage_advances_to_s2_once_outline_checkpoint_set(self):
    project_dir = self._make_project_with_all_s1_files()
    self.engine._save_stage_checkpoint(project_dir, "outline_confirmed_at")
    state = self.engine._infer_stage_state(project_dir)
    self.assertEqual(state["stage_code"], "S2")

def test_infer_stage_holds_at_s3_when_analysis_refs_insufficient(self):
    project_dir = self._make_project_past_outline_confirm()
    self._write_data_log_with_n_sources(project_dir, n=8)
    self._write_analysis_with_refs(project_dir, ref_count=1)  # below M=5
    state = self.engine._infer_stage_state(project_dir)
    self.assertEqual(state["stage_code"], "S3")

def test_infer_stage_holds_at_s4_when_word_count_below_floor(self):
    project_dir = self._make_project_past_s3()
    self._write_report(project_dir, word_count=1200)  # floor = 4200 for 6000 target
    state = self.engine._infer_stage_state(project_dir)
    self.assertEqual(state["stage_code"], "S4")

def test_infer_stage_holds_at_s5_without_review_passed_checkpoint(self):
    project_dir = self._make_project_past_s4()
    self.engine._save_stage_checkpoint(project_dir, "review_started_at")
    # review-checklist exists, but no review_passed_at
    state = self.engine._infer_stage_state(project_dir)
    self.assertEqual(state["stage_code"], "S5")

def test_infer_stage_returns_done_after_delivery_archived(self):
    project_dir = self._make_project_past_s5()
    for key in ("review_passed_at", "delivery_archived_at"):
        self.engine._save_stage_checkpoint(project_dir, key)
    self._write_delivery_log(project_dir)
    state = self.engine._infer_stage_state(project_dir)
    self.assertEqual(state["stage_code"], "done")
    self.assertEqual(state["stage_status"], "已归档")

def test_infer_stage_stays_at_s7_when_archived_stamp_missing(self):
    project_dir = self._make_project_past_s5()
    self.engine._save_stage_checkpoint(project_dir, "review_passed_at")
    # delivery-log ready but no delivery_archived_at
    self._write_delivery_log(project_dir)
    state = self.engine._infer_stage_state(project_dir)
    self.assertEqual(state["stage_code"], "S7")
    self.assertEqual(state["stage_status"], "进行中")
```

- [ ] **Step 2: Implement new `_infer_stage_state`**

Replace the existing function with the three-condition projection. Preserve the existing flags dict so `_build_completed_items` / `_build_skipped_items` keep working; add new flags `outline_confirmed`, `review_started`, `review_passed`, `presentation_ready`, `delivery_archived`.

```python
def _infer_stage_state(self, project_path: Path) -> dict:
    targets = self._resolve_length_targets(project_path)
    checkpoints = self._load_stage_checkpoints(project_path)

    project_overview_ready = self._is_effective_plan_file(project_path, "project-overview.md")
    notes_ready = self._has_effective_notes(project_path)
    references_ready = self._has_effective_references(project_path)
    outline_ready = self._has_effective_outline(project_path)
    research_plan_ready = self._has_effective_research_plan(project_path)

    data_log_quality_ok = self._has_enough_data_log_sources(project_path, targets["data_log_min"])
    analysis_quality_ok = self._has_enough_analysis_refs(project_path, targets["analysis_refs_min"])

    report_ready = self._has_effective_report_draft(project_path, min_words=targets["report_word_floor"])
    review_checklist_ready = self._has_effective_review_checklist(project_path)
    presentation_ready = self._has_effective_presentation_plan(project_path)
    delivery_ready = self._has_effective_delivery_log(project_path)
    presentation_required = self._delivery_mode_requires_presentation(project_path)

    outline_confirmed = "outline_confirmed_at" in checkpoints
    review_started = "review_started_at" in checkpoints
    review_passed = "review_passed_at" in checkpoints
    presentation_done = "presentation_ready_at" in checkpoints
    delivery_archived = "delivery_archived_at" in checkpoints

    stage_zero_complete = project_overview_ready
    stage_one_complete = (
        stage_zero_complete
        and notes_ready and references_ready and outline_ready and research_plan_ready
        and outline_confirmed
    )
    stage_two_complete = stage_one_complete and data_log_quality_ok
    stage_three_complete = stage_two_complete and analysis_quality_ok
    stage_four_complete = stage_three_complete and report_ready and review_started
    stage_five_complete = stage_four_complete and review_checklist_ready and review_passed
    stage_six_complete = stage_five_complete and (
        (presentation_ready and presentation_done) if presentation_required else True
    )
    stage_seven_complete = stage_six_complete and delivery_ready and delivery_archived

    if not stage_zero_complete:
        stage_code = "S0"
        stage_status = "进行中"
    elif not stage_one_complete:
        stage_code = "S1"
        stage_status = "进行中"
    elif not stage_two_complete:
        stage_code = "S2"
        stage_status = "进行中"
    elif not stage_three_complete:
        stage_code = "S3"
        stage_status = "进行中"
    elif not stage_four_complete:
        stage_code = "S4"
        stage_status = "进行中"
    elif not stage_five_complete:
        stage_code = "S5"
        stage_status = "进行中"
    elif presentation_required and not stage_six_complete:
        stage_code = "S6"
        stage_status = "进行中"
    elif not stage_seven_complete:
        stage_code = "S7"
        stage_status = "进行中"
    else:
        # Final gate passed (delivery_archived_at set + delivery-log.md ready)
        stage_code = "done"
        stage_status = "已归档"

    flags = {
        "project_overview_ready": project_overview_ready,
        "notes_ready": notes_ready,
        "references_ready": references_ready,
        "outline_ready": outline_ready,
        "research_plan_ready": research_plan_ready,
        "data_log_ready": data_log_quality_ok,
        "analysis_ready": analysis_quality_ok,
        "report_ready": report_ready,
        "review_checklist_ready": review_checklist_ready,
        "review_notes_ready": self._has_effective_review_notes(project_path),
        "review_ready": review_checklist_ready and review_passed,
        "presentation_ready": presentation_ready,
        "delivery_ready": delivery_ready and delivery_archived,
        "presentation_required": presentation_required,
        "outline_confirmed": outline_confirmed,
        "review_started": review_started,
        "review_passed": review_passed,
        "presentation_done": presentation_done,
        "delivery_archived": delivery_archived,
    }
    return {
        "stage_code": stage_code,
        "stage_status": stage_status,  # "进行中" or "已归档"
        "completed_items": self._build_completed_items(stage_code, flags),
        "skipped_items": self._build_skipped_items(stage_code, flags),
        "checkpoints": checkpoints,
        "length_targets": targets,
        "flags": flags,  # exposed so get_workspace_summary / frontend can read report_ready, outline_ready, etc.
    }
```

- [ ] **Step 3: Extend `_has_effective_report_draft` with `min_words` kwarg (single source of truth)**

**Important: do NOT duplicate the candidate list.** `backend/skill.py:96` already defines `REPORT_DRAFT_CANDIDATES` as a class constant. Reuse it. Also update `backend/chat.py:2862-2868` in Task 4 Step 5 to reference `self.skill_engine.REPORT_DRAFT_CANDIDATES` instead of its own hardcoded list, so the constant becomes the single source of truth across the codebase.

```python
def _has_effective_report_draft(self, project_path: Path, min_words: int = 0) -> bool:
    for candidate in self.REPORT_DRAFT_CANDIDATES:
        report_path = project_path / candidate
        if not report_path.exists():
            continue
        content = report_path.read_text(encoding="utf-8").strip()
        if not content or self._is_template_stub(content):
            continue
        if min_words and self._count_words(content) < min_words:
            continue
        return True
    return False

_MARKDOWN_STRIP_PATTERNS = [
    (re.compile(r"```[\s\S]*?```"), ""),            # fenced code blocks
    (re.compile(r"`[^`]*`"), ""),                    # inline code
    (re.compile(r"!\[[^\]]*\]\([^)]*\)"), ""),       # images
    (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),   # link text only
    (re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE), ""),  # heading markers
    (re.compile(r"^\s*[-*+]\s+", re.MULTILINE), ""),       # list bullets
    (re.compile(r"^\s*\d+\.\s+", re.MULTILINE), ""),       # ordered list markers
    (re.compile(r"\*\*([^*]+)\*\*"), r"\1"),               # bold
    (re.compile(r"\*([^*]+)\*"), r"\1"),                   # italic
    (re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE), ""),     # table rows
]

def _count_words(self, content: str) -> int:
    text = content
    for pattern, repl in self._MARKDOWN_STRIP_PATTERNS:
        text = pattern.sub(repl, text)
    stripped = re.sub(r"[\s\u3000]+", "", text)
    return len(stripped)
```

`_count_words` first strips Markdown structure (code blocks, link targets, heading/list/bold/italic markers, table rows) so that a report with lots of formatting isn't mis-counted. Remaining non-whitespace characters are a reasonable proxy for "字数" with mixed CJK/English content.

- [ ] **Step 4: Add conservative migration helper `_backfill_stage_checkpoints_if_missing`**

Per spec §16.2, migration **only backfills `outline_confirmed_at`**. `review_started_at` / `review_passed_at` / `presentation_ready_at` / `delivery_archived_at` are never auto-set—users must re-click the corresponding UI button. The worst case is an old S5/S6/S7 project temporarily shows as S4, which is safer than "默认通过审查".

Idempotency marker: once migrated, write `{"__migrated_at": "<iso>"}` into the checkpoints file so re-loads don't trigger migration again. `__migrated_at` is not in `STAGE_CHECKPOINT_KEYS` and is filtered out by `_load_stage_checkpoints`.

```python
MIGRATION_MARKER_KEY = "__migrated_at"

def _backfill_stage_checkpoints_if_missing(self, project_path: Path) -> None:
    checkpoints_path = self._stage_checkpoints_path(project_path)
    if checkpoints_path.exists():
        return
    stage_gates_path = project_path / "plan" / "stage-gates.md"
    migrated = {self.MIGRATION_MARKER_KEY: datetime.now().isoformat(timespec="seconds")}
    if stage_gates_path.exists():
        stage_text = stage_gates_path.read_text(encoding="utf-8")
        current_stage = self._extract_stage_code(stage_text)
        if current_stage and self._stage_index(current_stage) >= self._stage_index("S2"):
            migrated["outline_confirmed_at"] = datetime.now().isoformat(timespec="seconds")
    checkpoints_path.write_text(
        json.dumps(migrated, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

def _load_stage_checkpoints(self, project_path: Path) -> dict[str, str]:
    path = self._stage_checkpoints_path(project_path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    # Filter out migration marker and unknown keys
    return {k: v for k, v in data.items() if k in self.STAGE_CHECKPOINT_KEYS and isinstance(v, str)}
```

Wire this into `get_workspace_summary` (at the start) so it runs before `_infer_stage_state`. Add a RED test:

```python
def test_migration_only_backfills_outline_even_for_old_s7_projects(self):
    project_dir = self._make_project()
    self._write_stage_gates_at_stage(project_dir, "S7")
    self._write_report_draft(project_dir, words=5000)
    self._write_review_checklist(project_dir)
    self._write_delivery_log(project_dir)
    self.engine._backfill_stage_checkpoints_if_missing(project_dir)
    checkpoints = self.engine._load_stage_checkpoints(project_dir)
    self.assertIn("outline_confirmed_at", checkpoints)
    self.assertNotIn("review_started_at", checkpoints)
    self.assertNotIn("review_passed_at", checkpoints)
    self.assertNotIn("delivery_archived_at", checkpoints)
```

Additionally implement `_clear_stage_checkpoint_cascade` (spec §5.2):

```python
_CASCADE_ORDER = [
    "outline_confirmed_at",
    "review_started_at",
    "review_passed_at",
    "presentation_ready_at",
    "delivery_archived_at",
]

def _clear_stage_checkpoint_cascade(self, project_path: Path, key: str) -> None:
    if key not in self._CASCADE_ORDER:
        raise ValueError(f"unknown cascade key: {key}")
    start = self._CASCADE_ORDER.index(key)
    checkpoints = self._load_stage_checkpoints(project_path)
    changed = False
    for cascade_key in self._CASCADE_ORDER[start:]:
        if cascade_key in checkpoints:
            del checkpoints[cascade_key]
            changed = True
    if changed:
        # Preserve migration marker
        raw = {}
        path = self._stage_checkpoints_path(project_path)
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                raw = {}
        marker = raw.get(self.MIGRATION_MARKER_KEY)
        payload = dict(checkpoints)
        if marker:
            payload[self.MIGRATION_MARKER_KEY] = marker
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
```

Add RED test for cascade:

```python
def test_clear_cascade_clears_all_subsequent_checkpoints(self):
    project_dir = self._make_project()
    for key in ("outline_confirmed_at", "review_started_at", "review_passed_at", "delivery_archived_at"):
        self.engine._save_stage_checkpoint(project_dir, key)
    self.engine._clear_stage_checkpoint_cascade(project_dir, "review_started_at")
    checkpoints = self.engine._load_stage_checkpoints(project_dir)
    self.assertIn("outline_confirmed_at", checkpoints)  # untouched
    self.assertNotIn("review_started_at", checkpoints)
    self.assertNotIn("review_passed_at", checkpoints)
    self.assertNotIn("delivery_archived_at", checkpoints)
```

- [ ] **Step 5: Extend `get_workspace_summary` to surface new fields**

`backend/skill.py:514-542` currently returns `stage_code / status / completed_items / next_actions / workspace_dir / project_dir / materials`. The new design (spec §13.1, §9.3) requires it to also surface `checkpoints`, `length_targets`, `quality_progress`, `flags`, and `next_stage_hint`. These all come from the new `_infer_stage_state` return dict—`get_workspace_summary` just needs to forward them.

```python
def get_workspace_summary(self, project_ref: str) -> dict:
    project_path = self.get_project_path(project_ref)
    if not project_path:
        raise ValueError(f"项目不存在: {project_ref}")
    # Run migration once per project
    self._backfill_stage_checkpoints_if_missing(project_path)
    stage_state = self._infer_stage_state(project_path)
    project_record = self.get_project_record(project_ref) or {}

    # Compute next_stage_hint: only meaningful once review_passed_at is set
    checkpoints = stage_state.get("checkpoints", {})
    next_stage_hint = None
    if "review_passed_at" in checkpoints:
        next_stage_hint = "S6" if self._delivery_mode_requires_presentation(project_path) else "S7"

    # S2/S3 stall detection: if >30 min since last evidence write, surface a neutral hint
    stalled_since = None
    if stage_state["stage_code"] in ("S2", "S3"):
        last_write = self._last_evidence_write_at(project_path)
        if last_write is not None:
            elapsed = datetime.now() - last_write
            if elapsed.total_seconds() >= 30 * 60:
                stalled_since = last_write.isoformat(timespec="seconds")

    word_count = self._current_report_word_count(project_path)

    delivery_mode = self._extract_delivery_mode(project_path)

    return {
        "stage_code": stage_state["stage_code"],
        "status": stage_state.get("stage_status", "进行中"),
        "completed_items": stage_state["completed_items"],
        "skipped_items": stage_state.get("skipped_items", []),
        "next_actions": self._build_next_actions(stage_state),
        "workspace_dir": project_record.get("workspace_dir", ""),
        "project_dir": str(project_path),
        "materials": self.list_materials(project_ref),
        # New fields:
        "checkpoints": checkpoints,
        "length_targets": stage_state.get("length_targets", {}),
        "quality_progress": self._build_quality_progress(project_path, stage_state),
        "flags": stage_state.get("flags", {}),
        "next_stage_hint": next_stage_hint,
        "stalled_since": stalled_since,  # ISO timestamp or None; UI shows neutral hint when set
        "word_count": word_count,  # single source of truth; frontend must NOT recount locally
        "delivery_mode": delivery_mode,  # "仅报告" | "报告+演示"; drives S6 visibility
    }

def _extract_delivery_mode(self, project_path: Path) -> str:
    """Parse `交付形式` from plan/project-overview.md. Default "仅报告" if unparseable.
    Must be the single source of truth for frontend S6 visibility (spec §5.1)."""
    overview_path = project_path / "plan" / "project-overview.md"
    if not overview_path.exists():
        return "仅报告"
    text = overview_path.read_text(encoding="utf-8")
    m = re.search(r"交付形式[^\n]*?[:：]\s*([^\n]+)", text)
    if not m:
        return "仅报告"
    value = m.group(1).strip()
    return "报告+演示" if "演示" in value else "仅报告"

def _current_report_word_count(self, project_path: Path) -> int:
    """Return the maximum word count across all non-template report candidates, or 0 if none.

    IMPORTANT: this must align with `_has_effective_report_draft(min_words=X)`, which
    returns True if **any** candidate has word-count ≥ X. Taking the max here guarantees:
      max(counts) >= floor  ⇔  _has_effective_report_draft(min_words=floor) is True
    If this method instead returned "first non-template candidate", a user with
    `content/report.md` (800 words) + `output/final-report.md` (5000 words) would see
    `report_ready=True` in the flags but `word_count=800` in the workspace payload,
    making the frontend hide the "开始审查" button while the backend has already
    classified the project as ready — a real truth-source drift.
    """
    counts = []
    for candidate in self.REPORT_DRAFT_CANDIDATES:
        report_path = project_path / candidate
        if not report_path.exists():
            continue
        content = report_path.read_text(encoding="utf-8").strip()
        if not content or self._is_template_stub(content):
            continue
        counts.append(self._count_words(content))
    return max(counts) if counts else 0

def _last_evidence_write_at(self, project_path: Path) -> datetime | None:
    """Return the most recent mtime across notes / references / data-log / analysis-notes,
    or None if none of them exist. Used by stall detection per spec §9.3."""
    candidates = [
        project_path / "plan" / "notes.md",
        project_path / "plan" / "references.md",
        project_path / "plan" / "data-log.md",
        project_path / "plan" / "analysis-notes.md",
    ]
    mtimes = [p.stat().st_mtime for p in candidates if p.exists()]
    if not mtimes:
        return None
    return datetime.fromtimestamp(max(mtimes))

def record_stage_checkpoint(self, project_id: str, key: str, action: str) -> dict:
    """Public entry for Web layer to mutate stage checkpoints atomically.

    Performs project-lock acquisition → set or cascade-clear → sync stage tracking files,
    all within a single critical section. `backend/main.py` endpoints MUST call this
    instead of reaching into `_save_stage_checkpoint` / `_clear_stage_checkpoint_cascade`
    directly (spec §13.3 explicitly forbids it).
    """
    from backend.chat import _get_project_request_lock as _project_lock
    project_path = self.get_project_path(project_id)
    if project_path is None:
        raise ValueError(f"项目不存在: {project_id}")
    if action not in ("set", "clear"):
        raise ValueError(f"未知 action: {action}")
    lock = _project_lock(project_id)
    with lock:
        if action == "set":
            timestamp = self._save_stage_checkpoint(project_path, key)
            self._sync_stage_tracking_files(project_path)
            return {"status": "ok", "key": key, "timestamp": timestamp}
        self._clear_stage_checkpoint_cascade(project_path, key)
        self._sync_stage_tracking_files(project_path)
        return {"status": "ok", "key": key, "cleared": True}

def _build_quality_progress(self, project_path: Path, stage_state: dict) -> dict | None:
    stage = stage_state["stage_code"]
    targets = stage_state.get("length_targets", {})
    if stage == "S2":
        current = self._count_valid_data_log_sources(project_path)
        return {"label": "有效来源条目", "current": current, "target": targets.get("data_log_min", 0)}
    if stage == "S3":
        current = self._count_analysis_refs(project_path)
        return {"label": "分析证据引用", "current": current, "target": targets.get("analysis_refs_min", 0)}
    return None
```

Also refactor `_has_enough_data_log_sources` and `_has_enough_analysis_refs` so that the counting can be reused via `_count_valid_data_log_sources(project_path) -> int` and `_count_analysis_refs(project_path) -> int` helpers. The "has enough" variants become thin wrappers that compare the counter against the min threshold.

Add RED tests:
- `get_workspace_summary` response for S2 project returns `quality_progress = {"label": "有效来源条目", "current": N, "target": 8}`
- `next_stage_hint == "S6"` when `review_passed_at` set and delivery mode is 报告+演示
- `next_stage_hint == "S7"` when `review_passed_at` set and delivery mode is 仅报告
- `next_stage_hint is None` when `review_passed_at` absent

- [ ] **Step 6: Update existing test expectations (explicit line list)**

These tests in `tests/test_skill_engine.py` currently assert `stage_code` advances on file presence alone and must be updated. Run through each one and decide per test:

| Line | Current assertion | Action needed |
|---|---|---|
| 337 | `stage_code == "S0"` | Keep (S0 has no checkpoint dependency) |
| 509, 527, 545, 564, 583 | `stage_code == "S1"` | Keep (still S1 expected; but if the setup now has outline.md, add a comment clarifying it's still S1 because outline_confirmed_at is absent) |
| 594, 613, 630, 693, 813, 832, 844 | `stage_code == "S2"` | Requires `self.engine._save_stage_checkpoint(project_dir, "outline_confirmed_at")` in the setup; otherwise the assertion should change to `"S1"` |
| 648, 736, 782 | `stage_code == "S1"` | Verify these are still S1 under new rules (likely yes if outline files are missing) |
| 794, 929, 953, 989 | `stage_code == "S3"` | Requires outline checkpoint; data-log must have ≥ N=8 entries (or adjust expected_length fixture to a smaller target) |
| 857, 911, 974 | `stage_code == "S4"` | Requires outline checkpoint; data-log and analysis-notes meeting thresholds; report.md content must be below floor to stay at S4 (or the test may need to change to S5 with review_started_at added) |
| 1006, 1030 | `stage_code == "S5"` | Requires outline + review_started_at checkpoints; report.md word count ≥ floor |
| 1050 | `stage_code == "S0"` | Keep |

For `tests/test_chat_runtime.py`: any test asserting non-plan-write is granted based on keywords alone will need to either set `outline_confirmed_at` in setup or assert the new "blanket pass" behavior. Look for `_should_allow_non_plan_write` and `can_write_non_plan` references and update setups accordingly.

For each modified test, also verify the test's docstring / name still reflects what it's asserting under new rules.

- [ ] **Step 7: Run backend test suite, verify GREEN**

```
.venv\Scripts\python -m unittest discover tests -v
```

---

### Task 4: Wire Checkpoint Endpoints and Keyword Detection

**Files:**
- Modify: `backend/main.py`
- Modify: `backend/chat.py`
- Modify: `tests/test_main_api.py`
- Modify: `tests/test_chat_runtime.py`

- [ ] **Step 1: Add failing test for checkpoint endpoint idempotency (unittest style)**

`tests/test_main_api.py` uses minimal `unittest.TestCase` setUp (`TestClient(main_module.app)` + `register_desktop_bridge(None)`) and isolates per-test state via `@mock.patch` decorators against `main_module.skill_engine.*` or `backend.main.skill_engine.*`. **Match that style exactly**; do not mutate `main_module.settings.projects_dir` and do not POST `/api/projects` from setUp (that path depends on `ProjectInfo` schema validation and would couple the test to unrelated fields like `initial_material_paths`).

```python
class CheckpointEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main_module.app)
        main_module.register_desktop_bridge(None)

    def tearDown(self):
        main_module.register_desktop_bridge(None)

    @mock.patch("backend.main.skill_engine.record_stage_checkpoint")
    def test_checkpoint_set_delegates_to_public_service(self, mock_record):
        mock_record.return_value = {"status": "ok", "key": "outline_confirmed_at", "timestamp": "2026-04-17T12:00:00"}
        r = self.client.post("/api/projects/demo/checkpoints/outline-confirmed")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["timestamp"], "2026-04-17T12:00:00")
        mock_record.assert_called_once_with("demo", "outline_confirmed_at", "set")

    @mock.patch("backend.main.skill_engine.record_stage_checkpoint")
    def test_checkpoint_clear_passes_clear_action(self, mock_record):
        mock_record.return_value = {"status": "ok", "key": "outline_confirmed_at", "cleared": True}
        r = self.client.post("/api/projects/demo/checkpoints/outline-confirmed?action=clear")
        self.assertEqual(r.status_code, 200)
        mock_record.assert_called_once_with("demo", "outline_confirmed_at", "clear")

    def test_unknown_checkpoint_returns_404(self):
        r = self.client.post("/api/projects/demo/checkpoints/not-a-real-one")
        self.assertEqual(r.status_code, 404)

    @mock.patch("backend.main.skill_engine.record_stage_checkpoint")
    def test_missing_project_returns_404(self, mock_record):
        mock_record.side_effect = ValueError("项目不存在: demo")
        r = self.client.post("/api/projects/demo/checkpoints/outline-confirmed")
        self.assertEqual(r.status_code, 404)

    def test_unknown_action_returns_400(self):
        r = self.client.post("/api/projects/demo/checkpoints/outline-confirmed?action=weird")
        self.assertEqual(r.status_code, 400)
```

This keeps test_main_api.py isolated from the filesystem and ProjectInfo schema; cascade behavior is covered by Task 3's storage tests, not re-tested at the HTTP layer.

- [ ] **Step 2: Implement the five endpoints in `backend/main.py`** (under project lock)

All checkpoint writes and cascading clears must acquire the project-level lock to prevent concurrent write coverage (UI button + keyword chat arriving simultaneously). **Prerequisite (separate Step 0 below)**: a module-level `_get_project_request_lock` function must be added to `backend/chat.py`—the current implementation at `backend/chat.py:710` is an instance method on `ChatHandler`, which cannot be imported from `backend/main.py`. See Step 0.

**Execute Step 0 first (prerequisite for Step 2).** Ordering within Task 4: Step 0 → Step 1 → Step 2 → Step 3 → Step 4 → Step 5.

- [ ] **Step 0 (prerequisite): Expose `_get_project_request_lock` at module level in `backend/chat.py`**

Add near the existing `_PROJECT_REQUEST_LOCKS` / `_PROJECT_REQUEST_LOCKS_GUARD` globals (currently `backend/chat.py:60-61`):

```python
def _get_project_request_lock(project_id: str) -> threading.RLock:
    """Module-level accessor for the per-project RLock. Callers outside of
    ChatHandler (e.g., backend/main.py checkpoint endpoints) should use this."""
    lock_key = str(project_id or "")
    with _PROJECT_REQUEST_LOCKS_GUARD:
        lock = _PROJECT_REQUEST_LOCKS.get(lock_key)
        if lock is None:
            lock = threading.RLock()
            _PROJECT_REQUEST_LOCKS[lock_key] = lock
    return lock
```

Refactor the existing `ChatHandler._get_project_request_lock` (currently `backend/chat.py:710-717`) to delegate so both call paths share the same lock identity:

```python
def _get_project_request_lock(self, project_id: str):
    return _get_project_request_lock(project_id)
```

Add RED test in `tests/test_chat_runtime.py` verifying both calls return the **same** lock object for the same `project_id`:

```python
def test_module_and_instance_level_project_locks_share_identity(self):
    from backend.chat import _get_project_request_lock as module_lock
    handler = self._make_handler_with_project()
    module_obj = module_lock(self.project_id)
    instance_obj = handler._get_project_request_lock(self.project_id)
    self.assertIs(module_obj, instance_obj)
```

Per spec §13.3, endpoint must **not** reach into underscored private methods on the engine. It calls the public `record_stage_checkpoint(project_id, key, action)` service method which handles locking, save/cascade-clear, and stage-tracking sync atomically.

```python
_CHECKPOINT_ROUTES = {
    "outline-confirmed": "outline_confirmed_at",
    "review-started": "review_started_at",
    "review-passed": "review_passed_at",
    "presentation-ready": "presentation_ready_at",
    "delivery-archived": "delivery_archived_at",
}

@app.post("/api/projects/{project_id}/checkpoints/{name}")
async def set_checkpoint(project_id: str, name: str, action: str = "set"):
    key = _CHECKPOINT_ROUTES.get(name)
    if key is None:
        raise HTTPException(status_code=404, detail=f"未知 checkpoint: {name}")
    if action not in ("set", "clear"):
        raise HTTPException(status_code=400, detail=f"未知 action: {action}")
    try:
        return skill_engine.record_stage_checkpoint(project_id, key, action)
    except ValueError as exc:
        # record_stage_checkpoint raises ValueError with "项目不存在: ..." when project missing
        detail = str(exc)
        status = 404 if "项目不存在" in detail else 400
        raise HTTPException(status_code=status, detail=detail)
```

The lock is acquired inside `record_stage_checkpoint` (see Task 3 Step 5), so the endpoint stays thin. Update Task 4 Step 1's mocks accordingly—`@mock.patch("backend.main.skill_engine.record_stage_checkpoint")` instead of the individual private methods.

Extend `skill_engine.get_workspace_summary` to include `checkpoints`, `length_targets` (including `fallback_used`), `quality_progress`, and `next_stage_hint` in its return. These fields already come from the updated `_infer_stage_state`; the summary method just surfaces them to the HTTP response.

`next_stage_hint` logic: once `review_passed_at` is set, inspect `project-overview.md` for "交付形式: 报告+演示" → `"S6"` else `"S7"`. Otherwise `null`.

- [ ] **Step 3: Add RED test for advance keyword detection**

```python
def test_confirm_outline_keyword_writes_checkpoint(self):
    handler = self._make_handler_with_project()
    handler.chat(self.project_id, "大纲 ok，开始写", [], [])
    project_path = handler.skill_engine.get_project_path(self.project_id)
    checkpoints = handler.skill_engine._load_stage_checkpoints(project_path)
    self.assertIn("outline_confirmed_at", checkpoints)

def test_rollback_keyword_clears_checkpoint(self):
    handler = self._make_handler_with_project()
    project_path = handler.skill_engine.get_project_path(self.project_id)
    handler.skill_engine._save_stage_checkpoint(project_path, "outline_confirmed_at")
    handler.chat(self.project_id, "撤回大纲确认", [], [])
    checkpoints = handler.skill_engine._load_stage_checkpoints(project_path)
    self.assertNotIn("outline_confirmed_at", checkpoints)
```

- [ ] **Step 4: Implement stage-aware `_detect_stage_keyword` in `backend/chat.py`**

Per spec §8, keyword detection is stage-aware. Strong keywords are unambiguous across stages; weak keywords only trigger in the current stage. Question detection uses anchored regex, not `endswith`.

```python
# Strong keywords (stage-independent; unambiguous)
_STRONG_ADVANCE_KEYWORDS = {
    "outline_confirmed_at": ["确认大纲", "大纲没问题", "按这个大纲写", "就这个大纲", "就按这个版本"],
    "review_started_at": ["开始审查", "进入审查", "可以审查了", "开始 review"],
    "review_passed_at": ["审查通过", "审查没问题", "报告可以交付"],
    "presentation_ready_at": ["演示准备好了", "演示准备完成", "PPT 完成", "讲稿完成"],
    "delivery_archived_at": ["归档结束项目", "项目交付完成", "交付归档"],
}

# Weak keywords per current stage (stage-sensitive).
# NOTE: S4 intentionally excluded. S4 is the revision workbench where "挺好继续写下一节"
# is the highest-frequency affirmative sentence; letting weak keywords advance to S5 would
# constantly mis-trigger the "start review" hard gate. S4 → S5 requires either a strong
# keyword ("开始审查") or the UI button.
_WEAK_ADVANCE_BY_STAGE = {
    "S1": (["行", "可以", "同意", "没问题", "OK", "ok", "好的", "挺好的"], "outline_confirmed_at"),
    "S5": (["行", "可以", "挺好", "通过", "没问题"], "review_passed_at"),
    "S6": (["行", "可以", "OK", "ok"], "presentation_ready_at"),
    "S7": (["行", "可以", "归档吧"], "delivery_archived_at"),
}

# Rollback keywords (colloquial, not technical)
_ROLLBACK_KEYWORDS = {
    "outline_confirmed_at": ["大纲再改下", "大纲还要调整", "回去改大纲", "先别写了，大纲有问题"],
    "review_started_at": ["还要改报告", "再改改报告", "回到写作阶段", "暂停审查"],
    "review_passed_at": ["重新审查", "再看看", "审查没过"],
    "presentation_ready_at": ["演示再改", "讲稿还要调整"],
    "delivery_archived_at": ["还没归档", "撤回归档"],
}

_QUESTION_PATTERNS = [
    re.compile(r"(吗|么)[?？]?$"),
    re.compile(r"[?？]$"),
]
# Negation prefix window: 10 chars before the keyword. A negation word anywhere
# in that window (up to 9 chars between it and the keyword) suppresses the match.
# Examples that MUST suppress:
#   "先不要开始审查"            (negation 5 chars before "开始审查")
#   "别开始审查"                 (negation 0 chars before)
#   "不是说审查通过了吗"         (question + negation)
#   "不太建议现在开始审查"       (negation 6 chars before — requires ≥6-char window)
#   "其实我不想现在开始审查"     (negation 4 chars before)
# The `{0,9}$` inner limit combined with a 10-char `preceding` substring means
# the negation can sit at the very start of the window and still match.
# Whole-word/phrase negation markers. CRITICAL: do NOT include bare `非` — it would
# match inside common positive adverbs like `非常`, silently suppressing "非常同意" /
# "非常可以". Only negation-shaped phrases that start with 非 (并非 / 非要 / 非得) are safe.
_NEGATION_RE = re.compile(r"(不要|别|没|不是|不想|不|并非|非要|非得)[^。！？!?\n]{0,9}$")
_NEGATION_WINDOW_CHARS = 10

def _is_question(self, text: str) -> bool:
    return any(pattern.search(text) for pattern in self._QUESTION_PATTERNS)

def _phrase_hits(self, text: str, phrases: list[str]) -> bool:
    """Substring match with negation suppression. If any occurrence of the phrase
    has a clean (negation-free) preceding window of 10 chars, the phrase counts as
    a hit. Otherwise the match is suppressed."""
    for phrase in phrases:
        idx = text.find(phrase)
        while idx != -1:
            preceding = text[max(0, idx - self._NEGATION_WINDOW_CHARS): idx]
            if not self._NEGATION_RE.search(preceding):
                return True
            idx = text.find(phrase, idx + 1)
    return False

# Stage rank for "同类取最高阶段" tie-breaking per spec §8.4(5).
# Higher number = later stage. "审查通过归档吧" should resolve to delivery_archived_at,
# not review_passed_at.
_STAGE_RANK = {
    "outline_confirmed_at": 1,
    "review_started_at": 2,
    "review_passed_at": 3,
    "presentation_ready_at": 4,
    "delivery_archived_at": 5,
}

def _detect_stage_keyword(self, user_message: str, current_stage: str) -> tuple[str, str] | None:
    if not user_message:
        return None
    trimmed = user_message.strip()
    if self._is_question(trimmed):
        return None

    # Rollback: collect all hits, take the highest-rank one (later stage wins)
    rollback_hits = [
        key for key, phrases in self._ROLLBACK_KEYWORDS.items()
        if self._phrase_hits(trimmed, phrases)
    ]
    if rollback_hits:
        key = max(rollback_hits, key=lambda k: self._STAGE_RANK.get(k, 0))
        return ("clear", key)

    # Advance: collect STRONG + WEAK hits together, then max-rank wins.
    # CRITICAL: strong and weak must be ranked in the SAME pool, otherwise a strong
    # hit like "审查通过" would short-circuit before the weak hit "归档吧" (S7) gets
    # a chance to win by higher rank. Spec §8.4(5) explicitly requires "同类取最高阶段".
    advance_hits: list[str] = []
    for key, phrases in self._STRONG_ADVANCE_KEYWORDS.items():
        if self._phrase_hits(trimmed, phrases):
            advance_hits.append(key)
    weak_entry = self._WEAK_ADVANCE_BY_STAGE.get(current_stage)
    if weak_entry:
        phrases, target_key = weak_entry
        if self._phrase_hits(trimmed, phrases):
            advance_hits.append(target_key)

    if advance_hits:
        key = max(advance_hits, key=lambda k: self._STAGE_RANK.get(k, 0))
        return ("set", key)

    return None
```

Add RED tests for the tie-breaking rule:

```python
def test_strong_plus_weak_hits_take_highest_stage(self):
    handler = self._make_handler_with_project_at_stage("S7")
    # "审查通过归档吧" in S7:
    # - strong "审查通过" → review_passed_at (rank 3)
    # - weak "归档吧" → delivery_archived_at (rank 5, because current_stage=S7)
    # Expected: delivery_archived_at wins by highest rank.
    handler.chat(self.project_id, "审查通过归档吧", [], [])
    checkpoints = handler.skill_engine._load_stage_checkpoints(
        handler.skill_engine.get_project_path(self.project_id)
    )
    self.assertIn("delivery_archived_at", checkpoints)
    self.assertNotIn("review_passed_at", checkpoints)

def test_only_strong_hits_still_take_highest_stage(self):
    handler = self._make_handler_with_project_at_stage("S4")
    # "开始审查审查通过" in S4: both strong, rank 2 and 3. Rank 3 wins.
    # Note: this is a contrived combo for test coverage; real users rarely say both in one turn.
    handler.chat(self.project_id, "开始审查审查通过", [], [])
    checkpoints = handler.skill_engine._load_stage_checkpoints(
        handler.skill_engine.get_project_path(self.project_id)
    )
    self.assertIn("review_passed_at", checkpoints)
    self.assertNotIn("review_started_at", checkpoints)
```

Add RED tests for the negation regression:

```python
def test_negated_advance_does_not_trigger_checkpoint(self):
    handler = self._make_handler_with_project()
    # Each of these should NOT set any checkpoint (negation prefix suppression).
    # Includes cases that require the >=6-char window: "不太建议现在开始审查".
    for msg in [
        "先不要开始审查",
        "别开始审查",
        "不是说审查通过了吗",
        "不要归档吧",
        "不太建议现在开始审查",
        "其实我不想现在开始审查",
        "先别确认大纲",
    ]:
        handler.chat(self.project_id, msg, [], [])
        checkpoints = handler.skill_engine._load_stage_checkpoints(
            handler.skill_engine.get_project_path(self.project_id)
        )
        self.assertEqual(checkpoints, {}, f"message triggered unexpectedly: {msg}")

def test_positive_adverb_starting_with_fei_is_not_negation(self):
    """`非常同意` / `非常可以` must NOT be suppressed by the negation filter —
    the 非 in 非常 is not a negation marker. This guards against the round-5 regression
    where bare `非` was added to _NEGATION_RE."""
    handler = self._make_handler_with_project_at_stage("S1")
    handler.chat(self.project_id, "非常同意，就按这个大纲写", [], [])
    checkpoints = handler.skill_engine._load_stage_checkpoints(
        handler.skill_engine.get_project_path(self.project_id)
    )
    self.assertIn("outline_confirmed_at", checkpoints)
```

Hook into `_build_turn_context` with current stage, under project lock:

```python
def _build_turn_context(self, project_id: str, user_message: str) -> Dict[str, object]:
    project_path = self.skill_engine.get_project_path(project_id)
    if project_path:
        summary = self.skill_engine.get_workspace_summary(project_id)
        current_stage = summary.get("stage_code", "S0")
        detected = self._detect_stage_keyword(user_message, current_stage)
        if detected:
            action, key = detected
            lock = self._get_project_request_lock(project_id)
            with lock:
                if action == "set":
                    self.skill_engine._save_stage_checkpoint(project_path, key)
                else:
                    self.skill_engine._clear_stage_checkpoint_cascade(project_path, key)
                self.skill_engine._sync_stage_tracking_files(project_path)
    return self._new_turn_context(
        can_write_non_plan=self._should_allow_non_plan_write(project_id, user_message),
    )
```

Add RED tests covering:
- strong "确认大纲" sets outline regardless of stage
- weak "可以" in S1 sets outline; weak "可以" in S4 does **not** trigger anything (S4 excluded from weak table)
- weak "挺好" / "继续写下一节" in S4 does **not** trigger review_started_at (regression guard on the real UX bug)
- weak "可以" in S5 sets review_passed
- "就按这个大纲写吗？" doesn't trigger (question)
- "开始写报告嘛" triggers (句末是"嘛" 不是"吗"—should NOT be treated as a question; verify regex anchors on `(吗|么)[?？]?$` not `嘛`)
- rollback "大纲再改下" clears outline and cascades

- [ ] **Step 5: Update `_should_allow_non_plan_write` with blocking-first priority**

Per spec §11.4, the short-term blocking message (`先别写正文` etc.) MUST win over the outline blanket pass for the current turn. The stamp is preserved—next turn restores the pass.

```python
def _should_allow_non_plan_write(self, project_id: str, user_message: str) -> bool:
    normalized = (user_message or "").strip()
    if not normalized:
        return False

    # 1. Short-term blocking wins this turn; stamp stays intact
    if self._is_non_plan_write_blocking_message(normalized):
        return False

    # 2. Outline blanket pass after user confirmed the outline
    project_path = self.skill_engine.get_project_path(project_id)
    if project_path:
        checkpoints = self.skill_engine._load_stage_checkpoints(project_path)
        if "outline_confirmed_at" in checkpoints:
            return True

    # 3. Existing keyword + history + report-exists fallbacks (unchanged)
    if any(keyword in normalized for keyword in self.NON_PLAN_WRITE_ALLOW_KEYWORDS):
        return True
    # ... rest of existing function body unchanged ...
```

Add RED tests:
- `先别写正文` returns False even with `outline_confirmed_at` present
- Next turn without the blocking message, returns True again (stamp still there)
- `确认大纲` turn creates the stamp; the same turn returns True (the stamp is saved in `_build_turn_context` BEFORE `_should_allow_non_plan_write` runs because keyword handling precedes context assembly)

- [ ] **Step 6: Run tests, verify GREEN**

```
.venv\Scripts\python -m unittest tests.test_chat_runtime tests.test_main_api -v
```

---

### Task 5: Self-Signature Interception in `write_file`

**Files:**
- Modify: `backend/skill.py` (validators)
- Modify: `backend/chat.py` (hook)
- Modify: `tests/test_chat_runtime.py`

- [ ] **Step 1: Add RED test for self-signed review-checklist rejection**

```python
def test_write_file_rejects_self_signed_review_checklist(self):
    handler = self._make_handler_with_project()
    args = {
        "file_path": "plan/review-checklist.md",
        "content": "# 审查\n**审查人：咨询报告写作助手**\n**结论：建议通过**",
    }
    result = handler._execute_tool_call({"function": {"name": "write_file", "arguments": json.dumps(args)}}, self.project_id)
    self.assertEqual(result["status"], "error")
    self.assertIn("审查人", result["message"])

def test_write_file_rejects_delivery_log_with_archived_claim_without_checkpoint(self):
    handler = self._make_handler_with_project()
    args = {
        "file_path": "plan/delivery-log.md",
        "content": "## 项目状态\n已交付，归档完成",
    }
    result = handler._execute_tool_call({"function": {"name": "write_file", "arguments": json.dumps(args)}}, self.project_id)
    self.assertEqual(result["status"], "error")
```

- [ ] **Step 2: Add content validators to `backend/skill.py`**

Both `review-checklist.md` and `delivery-log.md` interceptors are **auto-disabled when the corresponding checkpoint stamp exists** (spec §12.3). This replaces the earlier "signed-file exemption" design.

```python
_SELF_SIGNATURE_PATTERNS = [
    re.compile(r"审查人\s*[:：]\s*(咨询报告写作助手|AI|助手|Claude|GPT|ChatGPT|gemini|模型)"),
]
# Premature "审查结论 / 建议通过" by the model when review hasn't even started.
# Spec §12.1 rule 2 — rejected whenever review_started_at is missing.
_PREMATURE_REVIEW_VERDICT_PATTERNS = [
    re.compile(r"审查结论\s*[:：]"),
    re.compile(r"建议通过"),
    re.compile(r"审查通过"),  # as a written verdict line, not a keyword trigger
]
_ARCHIVE_CLAIM_PATTERNS = [
    re.compile(r"(项目状态|交付状态)[^\n]*?[:：]?\s*(已完成|已交付|已归档|已结束)"),
]
# Covers single-line AND multi-line checkbox blocks.
# Single-line: `- [x] 客户反馈 ... （待记录）`
# Multi-line:  `- [x] 客户反馈` \n `（待记录）` — the placeholder lives on a following line
# inside the same checkbox block (until next `- [` or heading or blank-blank-line).
_DELIVERY_PLACEHOLDER_INLINE = re.compile(
    r"-\s*\[x\][^\n]*客户反馈[^\n]*[(（]?\s*(待记录|待补充|暂无)\s*[)）]?"
)
_DELIVERY_BLOCK_RE = re.compile(
    r"-\s*\[x\][^\n]*客户反馈[^\n]*\n(?P<body>(?:[^\n]*\n){0,5})",
    re.MULTILINE,
)
_PLACEHOLDER_WORDS_RE = re.compile(r"[(（]?\s*(待记录|待补充|暂无)\s*[)）]?")

def _delivery_log_has_placeholder_feedback(self, content: str) -> bool:
    if self._DELIVERY_PLACEHOLDER_INLINE.search(content):
        return True
    # Multi-line: scan up to 5 lines after a `- [x] ... 客户反馈` line; stop if we
    # hit another `- [` checkbox or a heading.
    for m in self._DELIVERY_BLOCK_RE.finditer(content):
        body = m.group("body")
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("- [") or stripped.startswith("#"):
                break
            if self._PLACEHOLDER_WORDS_RE.search(line):
                return True
    return False

def validate_self_signature(self, normalized_path: str, content: str, checkpoints: dict) -> str | None:
    """Return an error message if the content violates self-signature rules, else None.

    Interception is auto-disabled when the corresponding checkpoint stamp is present
    (per spec §12.3): review_passed_at disables review-checklist interception, and
    delivery_archived_at disables delivery-log interception.
    """
    if normalized_path == "plan/review-checklist.md":
        if "review_passed_at" in checkpoints:
            return None  # user already signed off via UI
        for pattern in self._SELF_SIGNATURE_PATTERNS:
            if pattern.search(content):
                return ("review-checklist.md 的\"审查人\"字段必须由真实用户签字，"
                        "请保留\"审查人：[待用户确认]\"让用户在 UI 上签字。")
        # Spec §12.1 rule 2: premature review verdict before review_started_at stamp
        if "review_started_at" not in checkpoints:
            for pattern in self._PREMATURE_REVIEW_VERDICT_PATTERNS:
                if pattern.search(content):
                    return ("review-checklist.md 的\"审查结论 / 建议通过\"字段必须在用户点击"
                            "\"完成撰写，开始审查\"按钮之后再写入。当前审查尚未开始，"
                            "请保留为空或\"[待审查]\"，并告知用户需要他们先点按钮进入审查阶段。")
    if normalized_path == "plan/delivery-log.md":
        if "delivery_archived_at" in checkpoints:
            return None  # user already archived via UI
        for pattern in self._ARCHIVE_CLAIM_PATTERNS:
            if pattern.search(content):
                return ("delivery-log.md 声明\"已归档/已交付\"需要用户点击 UI 的\"归档结束项目\"按钮。"
                        "请把状态保持为\"待归档\"，并告知用户需要他们点按钮。")
        if self._delivery_log_has_placeholder_feedback(content):
            return ("delivery-log.md 勾选\"客户反馈\"需要真实反馈内容，"
                    "请保留为未勾选，等用户补齐反馈后再勾。")
    return None
```

Add RED tests covering:
- `审查人：咨询报告写作助手` → rejected
- `审查人： 咨询报告写作助手`（全角空格）→ rejected
- **No `review_started_at` + `建议通过` or `审查结论：通过` written → rejected** (new regression guard for spec §12.1 rule 2)
- `review_started_at` set + `建议通过` → accepted (user opened review, model allowed to write verdict line if prompted, although best practice still defers to `review_passed_at`)
- `- [x] **反馈 A**：（待记录）` 单行全角括号 → rejected
- `- [x] 客户反馈\n（待记录）` 多行块 → rejected (new regression guard for multi-line placeholder)
- `- [x] 客户反馈\n客户说非常满意` 多行块 + 真实反馈 → accepted
- `review_passed_at` set + `审查人：咨询报告写作助手` → **accepted** (auto-disabled)
- `delivery_archived_at` set + `项目状态：已归档` → **accepted**

- [ ] **Step 3: Hook self-signature validator into `write_file` and emit `system_notice`**

Replace the existing `validate_plan_write` call site with the interception + notice-emit pattern. The notice emission is the **hard guarantee** per spec §11.2 that users see blocked operations even if the model ignores the prompt.

```python
normalized_path = self.skill_engine.validate_plan_write(project_id, args["file_path"])

# Protected-path guard (spec §6.3): `stage_checkpoints.json` is the canonical user-
# confirmation truth source. Only the 5 checkpoint endpoints (via record_stage_checkpoint)
# may mutate it. The model must NEVER be able to forge outline_confirmed_at /
# review_passed_at / etc. by calling write_file. Reject here before touching disk.
if self.skill_engine.is_protected_stage_checkpoints_path(normalized_path):
    reason = (
        "stage_checkpoints.json 是用户确认真值源，模型不能直接写入。"
        "推进阶段需要用户点击右侧工作区对应按钮（例如\"确认大纲，进入资料采集\"）。"
    )
    self._emit_system_notice_once(
        category="checkpoint_forge_blocked",
        path=normalized_path,
        reason=reason,
        user_action="请告知用户需要他们点击工作区按钮来推进阶段；不要尝试直接写这个文件。",
    )
    return {"status": "error", "message": reason}

project_path = self.skill_engine.get_project_path(project_id)
checkpoints = self.skill_engine._load_stage_checkpoints(project_path) if project_path else {}
signature_error = self.skill_engine.validate_self_signature(normalized_path, args["content"], checkpoints)
if signature_error:
    self._emit_system_notice_once(
        category="write_blocked",
        path=normalized_path,
        reason=signature_error,
        user_action="请联系用户在右侧工作区完成对应的确认后再写入",
    )
    return {"status": "error", "message": signature_error}
self.skill_engine.write_file(project_id, normalized_path, args["content"])
```

Add helper in `backend/skill.py` near the other validators:

```python
def is_protected_stage_checkpoints_path(self, normalized_path: str) -> bool:
    """Return True if the path points to stage_checkpoints.json (the user-confirmation
    truth source). Accepts both the canonical filename at project root and any
    normalized relative form that would resolve there.

    IMPORTANT: comparison must be case-insensitive. Windows filesystems are
    case-insensitive by default, so `Stage_Checkpoints.json` / `STAGE_CHECKPOINTS.JSON`
    all resolve to the same file and must be blocked.
    """
    if not normalized_path:
        return False
    tail = normalized_path.replace("\\", "/").rsplit("/", 1)[-1]
    return tail.casefold() == self.STAGE_CHECKPOINTS_FILENAME.casefold()
```

Add RED tests in `tests/test_chat_runtime.py`:

```python
def test_write_file_rejects_direct_write_to_stage_checkpoints(self):
    handler = self._make_handler_with_project()
    args = {
        "file_path": "stage_checkpoints.json",
        "content": '{"outline_confirmed_at": "2026-04-17T12:00:00"}',
    }
    result = handler._execute_tool_call(
        {"function": {"name": "write_file", "arguments": json.dumps(args)}},
        self.project_id,
    )
    self.assertEqual(result["status"], "error")
    self.assertIn("stage_checkpoints.json", result["message"])
    # Verify the forged stamp did NOT land
    cp = handler.skill_engine._load_stage_checkpoints(
        handler.skill_engine.get_project_path(self.project_id)
    )
    self.assertNotIn("outline_confirmed_at", cp)

def test_write_file_rejects_checkpoints_path_via_relative_and_case_variants(self):
    """Cover both relative-path shapes AND case-insensitive variants (Windows filesystems
    are case-insensitive, so `Stage_Checkpoints.json` resolves to the same file)."""
    handler = self._make_handler_with_project()
    variants = [
        "./stage_checkpoints.json",
        "stage_checkpoints.json",
        ".\\stage_checkpoints.json",
        "Stage_Checkpoints.json",
        "STAGE_CHECKPOINTS.JSON",
        ".\\STAGE_CHECKPOINTS.json",
        "plan/../Stage_Checkpoints.json",
    ]
    for p in variants:
        args = {"file_path": p, "content": "{}"}
        result = handler._execute_tool_call(
            {"function": {"name": "write_file", "arguments": json.dumps(args)}},
            self.project_id,
        )
        self.assertEqual(result["status"], "error", f"path {p} was not blocked")
```

Additionally, wrap all existing `write_file` error returns (the non-plan block at `chat.py:2049`, the fetch-url gate at `chat.py:2053`, etc.) with the same `_emit_system_notice_once` helper so every error path surfaces a visible notice.

- [ ] **Step 4: Implement `_emit_system_notice_once` in `backend/chat.py`**

```python
def _emit_system_notice_once(self, *, category: str, path: str | None = None,
                              reason: str, user_action: str) -> None:
    if self._turn_context.get("system_notice_emitted"):
        return
    notice = {
        "type": "system_notice",
        "category": category,
        "path": path,
        "reason": reason,
        "user_action": user_action,
    }
    self._turn_context["system_notice_emitted"] = True
    queue = self._turn_context.setdefault("pending_system_notices", [])
    queue.append(notice)
```

Both `chat` (non-streaming) and `chat_stream` must drain `pending_system_notices` to ensure the notice is surfaced even if the model ignores the SKILL.md prompt about surfacing tool errors.

**Concrete wiring — `chat_stream` (`_chat_stream_unlocked`, `backend/chat.py` around line 1202-1389):**

Find the tool-execution inner loop (currently iterates tool_calls and appends tool results to `current_turn_messages`). After each `tool_result = self._execute_tool_call(...)` call, add a drain block:

```python
# After self._execute_tool_call returns, drain any system notices the tool emitted.
for notice in self._turn_context.pop("pending_system_notices", []):
    yield {
        "type": "system_notice",
        "category": notice["category"],
        "path": notice.get("path"),
        "reason": notice["reason"],
        "user_action": notice["user_action"],
    }
```

Important: use `pop("pending_system_notices", [])` (not `get(...)`) so the queue is drained immediately and cannot be double-emitted. Alternatively keep `get` + clear the list; document whichever style is chosen.

**Concrete wiring — `chat` (`_chat_unlocked`, around line 1396-1532):**

Before returning `{"content": ..., "token_usage": ...}`, add:

```python
system_notices = [
    SystemNotice(
        category=n["category"],
        path=n.get("path"),
        reason=n["reason"],
        user_action=n["user_action"],
    )
    for n in self._turn_context.pop("pending_system_notices", [])
]
return {
    "content": content,
    "token_usage": token_usage,
    "system_notices": system_notices or None,
}
```

The caller (`backend/main.py:/api/chat`) must pass `system_notices` through into the `ChatResponse` model.

**Update `/api/chat` endpoint and `ChatResponse` model:**

```python
# backend/models.py
class SystemNotice(BaseModel):
    category: str
    path: Optional[str] = None
    reason: str
    user_action: str

class ChatResponse(BaseModel):
    content: str
    files_updated: Optional[List[str]] = None
    token_usage: Optional[TokenUsage] = None
    system_notices: Optional[List[SystemNotice]] = None
```

```python
# backend/main.py chat handler (around existing line 258)
return ChatResponse(
    content=result["content"],
    token_usage=result.get("token_usage"),
    system_notices=result.get("system_notices"),
)
```

**Document the new stream event type.** Add to the `/api/chat/stream` contract (if any) and the stream-event handling in frontend `ChatPanel.jsx`:

```json
{"type": "system_notice", "category": "write_blocked", "path": "content/report.md",
 "reason": "当前轮次还不能开始写正文", "user_action": "请点击确认大纲按钮"}
```

Add RED tests:
- Write a blocked content → chat_stream yields exactly one `system_notice` event with the reason
- Second blocked write in the same turn → no second notice (dedup by `system_notice_emitted` flag)
- Next turn, a new blocked write → notice emitted again (turn context reset)

- [ ] **Step 5: Run tests, verify GREEN**

```
.venv\Scripts\python -m unittest tests.test_chat_runtime -v
```

---

### Task 6: Update SKILL.md with Advancement Conditions and Error-Surfacing Rule

**Files:**
- Modify: `skill/SKILL.md`
- Modify: `tests/test_skill_assets.py` (if it exists and asserts on SKILL.md content)

- [ ] **Step 1: Append per-stage advancement conditions**

Under each `### Sx` section in SKILL.md, append one line. For example:

```
### S1 研究设计
...（原文）

**推进到 S2：** 必须在 UI 上点击"确认大纲，进入资料采集"，或用户在对话里明确说"确认大纲 / 按这个大纲写 / 大纲 ok"。
```

Do this for S1, S4, S5, S6 (conditional), S7.

- [ ] **Step 2: Add a top-level "工具错误处理" section**

Insert before the "## 写作约束" section:

```markdown
## 工具错误处理

当你调用 `write_file` / `web_search` / `fetch_url` 得到 `status: error` 时：

1. 必须在本轮的可见回复里告诉用户：
   - 哪个工具调用失败了
   - 失败的原因（error message 摘要）
   - 用户需要做什么才能让你继续（例如"请点击工作区的'确认大纲'按钮，或说'确认大纲开始写'"）
2. **严禁**在工具被挡时把本应写入文件的内容直接贴到聊天窗口作为替代输出。这是对用户的误导。
3. 错误处理回复应保持简洁、可操作，不要解释技术细节。
```

- [ ] **Step 3: Run skill-asset tests if present, verify GREEN**

```
.venv\Scripts\python -m unittest tests.test_skill_assets -v
```

---

### Task 7: Frontend StageAdvanceControl

**Files:**
- Create: `frontend/src/components/StageAdvanceControl.jsx`
- Modify: `frontend/src/components/WorkspacePanel.jsx`
- Create: `frontend/tests/stageAdvanceControl.test.mjs`

- [ ] **Step 1: Add failing frontend test for stage button rendering (dual-button S4)**

Per spec §9.2, S4 is NOT a single disabled/enabled button. It's a **dual-button** design: "继续扩写" is always present and clickable; "完成撰写，开始审查" appears as a second button only when word count meets the floor. Below-floor tests must verify the review button is absent, not merely disabled.

```javascript
import test from "node:test";
import assert from "node:assert";
import { getStageButtonsConfig, getRollbackOptions, getConfirmDialogCopy, getStallHint } from "../src/utils/stageAdvanceConfig.mjs";

test("S0 returns no buttons", () => {
  assert.deepStrictEqual(getStageButtonsConfig({ stage: "S0" }), { primary: null, secondary: null });
});

test("done stage returns no primary/secondary (project is archived)", () => {
  const cfg = getStageButtonsConfig({ stage: "done" });
  assert.strictEqual(cfg.primary, null);
  assert.strictEqual(cfg.secondary, null);
});

test("stall hint is surfaced for S2 when stalled_since is set", () => {
  const stall = getStallHint({ stage: "S2", stalledSince: "2026-04-17T09:00:00" });
  assert.match(stall, /采集资料/);
  assert.doesNotMatch(stall, /卡住|异常/);
});

test("stall hint is null for S4 regardless of idle time", () => {
  const stall = getStallHint({ stage: "S4", stalledSince: "2026-04-17T09:00:00" });
  assert.strictEqual(stall, null);
});

test("S1 shows a single primary button requiring outline.md presence", () => {
  const belowReady = getStageButtonsConfig({ stage: "S1", outlineReady: false });
  assert.strictEqual(belowReady.primary.disabled, true);
  const ready = getStageButtonsConfig({ stage: "S1", outlineReady: true });
  assert.strictEqual(ready.primary.disabled, false);
  assert.strictEqual(ready.primary.endpoint, "outline-confirmed");
  assert.strictEqual(ready.secondary, null);
});

test("S4 below floor shows continue-writing as primary, no review button", () => {
  const cfg = getStageButtonsConfig({ stage: "S4", wordCount: 1200, reportWordFloor: 4200 });
  assert.strictEqual(cfg.primary.label, "继续扩写");
  assert.strictEqual(cfg.primary.disabled, false);
  assert.strictEqual(cfg.secondary, null);
  assert.match(cfg.hint, /1200/);
  assert.match(cfg.hint, /4200/);
});

test("S4 at or above floor shows both continue-writing and start-review", () => {
  const cfg = getStageButtonsConfig({ stage: "S4", wordCount: 5000, reportWordFloor: 4200 });
  assert.strictEqual(cfg.primary.label, "继续扩写");
  assert.strictEqual(cfg.secondary.label, "完成撰写，开始审查");
  assert.strictEqual(cfg.secondary.endpoint, "review-started");
});

test("S5 uses next_stage_hint to pick between S6 or S7 pathing", () => {
  // review-passed endpoint is the same; routing happens server-side
  const s5ReportOnly = getStageButtonsConfig({ stage: "S5", deliveryMode: "仅报告" });
  assert.strictEqual(s5ReportOnly.primary.endpoint, "review-passed");
  const s5WithPres = getStageButtonsConfig({ stage: "S5", deliveryMode: "报告+演示" });
  assert.strictEqual(s5WithPres.primary.endpoint, "review-passed");
});

test("S6 only appears for 报告+演示", () => {
  const cfg = getStageButtonsConfig({ stage: "S6", deliveryMode: "仅报告" });
  assert.strictEqual(cfg.primary, null);
});

test("Rollback options are tiered: S2/S3/S4 primary is 'adjust outline' (no stamp clear)", () => {
  const s3 = getRollbackOptions({ stage: "S3" });
  assert.strictEqual(s3.primary[0].label, "调整大纲");
  assert.strictEqual(s3.primary[0].action, "send_chat"); // auto-send, not insert
  assert.ok(s3.primary[0].systemLabel); // has a user-visible marker
  assert.strictEqual(s3.advanced[0].label, "完全重置大纲确认");
  assert.strictEqual(s3.advanced[0].action, "clear");
});

test("S5 secondary button provides rollback without needing ⋯ menu", () => {
  const cfg = getStageButtonsConfig({ stage: "S5", deliveryMode: "仅报告" });
  assert.ok(cfg.secondary);
  assert.strictEqual(cfg.secondary.label, "回去再改");
  assert.strictEqual(cfg.secondary.endpoint, "review-started");
  assert.strictEqual(cfg.secondary.endpointAction, "clear");
});

test("done stage rollback menu keeps only 撤回归档 in advanced tier", () => {
  const rb = getRollbackOptions({ stage: "done" });
  assert.deepStrictEqual(rb.primary, []);
  assert.strictEqual(rb.advanced.length, 1);
  assert.strictEqual(rb.advanced[0].label, "撤回归档");
  assert.strictEqual(rb.advanced[0].endpoint, "delivery-archived");
  assert.strictEqual(rb.advanced[0].action, "clear");
});

test("Confirmation dialog copy avoids technical terms", () => {
  const copy = getConfirmDialogCopy({ rollbackType: "review_started_at_clear" });
  assert.doesNotMatch(copy, /S4|S5|plan 文件|content\/report\.md|checkpoint/);
  assert.match(copy, /撰写|正文|报告/);
});
```

- [ ] **Step 2: Create `frontend/src/utils/stageAdvanceConfig.mjs`**

```javascript
export function getStageButtonsConfig({ stage, wordCount = 0, reportWordFloor = 0,
                                         deliveryMode = "仅报告", outlineReady = false }) {
  const empty = { primary: null, secondary: null, hint: null };
  if (stage === "S0" || stage === "S2" || stage === "S3" || stage === "done") return empty;

  if (stage === "S1") {
    return {
      primary: {
        label: "确认大纲，进入资料采集",
        endpoint: "outline-confirmed",
        disabled: !outlineReady,
      },
      secondary: null,
      hint: outlineReady ? null : "需要先让助手写好大纲",
    };
  }

  if (stage === "S4") {
    const meetsFloor = wordCount >= reportWordFloor;
    return {
      primary: {
        label: "继续扩写",
        action: "chat_message",
        chatMessage: "请继续扩写正文",
        disabled: false,
        style: meetsFloor ? "secondary" : "primary",
      },
      secondary: meetsFloor
        ? { label: "完成撰写，开始审查", endpoint: "review-started", style: "primary" }
        : null,
      hint: `当前 ${wordCount} 字 / 目标 ${reportWordFloor} 字`,
    };
  }

  if (stage === "S5") {
    return {
      primary: { label: "审查通过，准备交付", endpoint: "review-passed", style: "primary", disabled: false },
      secondary: {
        label: "回去再改",
        endpoint: "review-started",
        endpointAction: "clear",
        cascadeWarning: true,  // confirm dialog uses the "review_started_at_clear" copy
        style: "secondary",
      },
      hint: null,
    };
  }

  if (stage === "S6") {
    if (deliveryMode !== "报告+演示") return empty;
    return {
      primary: { label: "演示准备完成", endpoint: "presentation-ready", disabled: false },
      secondary: null,
      hint: null,
    };
  }

  if (stage === "S7") {
    return {
      primary: { label: "归档，结束项目", endpoint: "delivery-archived", disabled: false },
      secondary: null,
      hint: null,
    };
  }

  return empty;
}

// Tiered rollback menu per spec §9.4.
// "conversation_only" actions DO NOT clear any stamp—they insert a chat message asking
// the assistant to revise outline.md, preserving the outline pass-through.
export function getRollbackOptions({ stage }) {
  if (stage === "S0" || stage === "S1") {
    return { primary: [], advanced: [] };
  }

  const adjustOutlineItem = {
    label: "调整大纲",
    action: "send_chat",  // auto-send (not insert) so user isn't confused by text appearing in their input
    chatMessage: "请帮我调整一下大纲",
    systemLabel: "[用户点击了「调整大纲」]",  // rendered inline in chat stream as a user-action marker
  };
  const fullResetItem = {
    label: "完全重置大纲确认",
    action: "clear",
    endpoint: "outline-confirmed",
    warn: "cascade",
  };

  if (stage === "S2" || stage === "S3" || stage === "S4") {
    return {
      primary: [adjustOutlineItem],
      advanced: [fullResetItem],
    };
  }

  if (stage === "S5") {
    // "回去再改" is already exposed as the S5 secondary button (§9.2.2), so the
    // one-click rollback doesn't need to appear in the `⋯` menu again. Keep the
    // advanced-tier "full reset" available for edge cases.
    return {
      primary: [],
      advanced: [fullResetItem],
    };
  }

  if (stage === "S6" || stage === "S7") {
    return {
      primary: [{ label: "回到审查阶段", action: "clear", endpoint: "review-passed", warn: "cascade" }],
      advanced: [
        { label: "撤回归档", action: "clear", endpoint: "delivery-archived" },
        fullResetItem,
      ],
    };
  }

  if (stage === "done") {
    // Project is archived. Only allow pulling it back to S7 via the "撤回归档" path.
    // No primary rollback — user should use the menu deliberately, not casually.
    return {
      primary: [],
      advanced: [
        { label: "撤回归档", action: "clear", endpoint: "delivery-archived" },
      ],
    };
  }

  return { primary: [], advanced: [] };
}

export function getStallHint({ stage, stalledSince }) {
  if (!stalledSince) return null;
  if (stage === "S2") return "需要继续采集资料吗？可以粘贴链接或上传材料。";
  if (stage === "S3") return "需要进一步分析吗？可以让助手基于已有证据再拆一层。";
  return null;
}

export function getConfirmDialogCopy({ rollbackType }) {
  switch (rollbackType) {
    case "review_started_at_clear":
      return "确认回到撰写阶段继续改报告？\n你写好的正文内容不会被删除，只是重新打开修改通道。";
    case "outline_confirmed_at_clear":
      return "确认重置大纲确认？\n你写好的报告正文不会被删除，但暂时无法继续修改，直到重新确认新的大纲后才能继续写。";
    case "review_passed_at_clear":
      return "确认回到审查阶段？\n审查状态会清除，你可以重新检查或让助手修改报告。";
    case "delivery_archived_at_clear":
      return "确认撤回归档？\n所有文件都会保留，只是项目重新回到待归档状态。";
    case "advance_generic":
      return "确认进入下一阶段？文件不会被删除，可随时回退。";
    default:
      return "确认此操作？文件不会被删除。";
  }
}
```

- [ ] **Step 3: Create `StageAdvanceControl.jsx`**

Render:
1. Two stacked buttons (or one): primary from `buttons.primary`, secondary from `buttons.secondary`. Below them the hint text `buttons.hint` (always neutral—no "disabled because" phrasing).
2. Primary button click handler:
   - If `action === "chat_message"` (S4 "继续扩写"): use the parent's `onInsertChatMessage(chatMessage)` callback to put the text into the ChatPanel input box (user can edit/send).
   - If `action === "send_chat"` (rollback "调整大纲"): use the parent's `onSendSystemActionMessage({label: systemLabel, message: chatMessage})` callback. This:
     - Appends a visible user-action marker to the chat history (e.g. rendered as a small gray card labelled "[用户点击了「调整大纲」]") so the assistant gets context and the user never sees "a message they didn't type" in their input box.
     - Then sends `chatMessage` as if the user sent it, triggering a normal chat turn.
   - If `endpoint` set: show the matching confirmation dialog from `getConfirmDialogCopy` (use `review_started_at_clear` for the S5 "回去再改" secondary; `advance_generic` for advance buttons) → POST endpoint with optional `action=clear` query param → call `onWorkspaceRefresh()`.
3. Secondary button click handler: same as primary when an `endpoint` is present.
4. A `⋯` icon button next to the buttons (hidden when stage is S0/S1). Click opens a popover:
   - Top section: list `rollback.primary` options (each shows confirmation dialog with the matched `getConfirmDialogCopy({ rollbackType: "<key>_clear" })` before firing).
   - "更多" expand toggle reveals `rollback.advanced` options with stronger warning copy.
5. `conversation_only` rollback options insert a chat message and do NOT hit any endpoint.
6. All endpoint calls funnel through a single `postCheckpoint(projectId, endpoint, action)` helper which handles 404/500 with a toast.

- [ ] **Step 4: Mount in `WorkspacePanel.jsx` + inline progress + fallback hint**

Props to pass in from the workspace response (all values are read from `/api/projects/{id}/workspace`; the frontend must not recompute any of them to avoid drift):
- `stage` (from `stage_code`)
- `wordCount` (from `word_count`)
- `reportWordFloor` (from `length_targets.report_word_floor`)
- `deliveryMode` (from a workspace response field; the backend derives it from `project-overview.md` "交付形式")
- `outlineReady` (from `flags.outline_ready`)
- `stalledSince` (from `stalled_since`)

Additional WorkspacePanel changes:
- S2/S3: render inline counter `已收集有效来源 3 / 8 条` using `quality_progress` from the workspace response.
- When `length_targets.fallback_used === true`: render a **clickable chip** above the buttons with text "预期字数：3000（默认值，点击修改）". On click, open the project-info editing panel (reuse the new-project form UI in edit mode). Per spec §7.5 rule 3: a static banner is insufficient because users don't know where "项目信息" lives in the UI. Add a RED test for the click handler wiring.
- When `stalled_since` is set (S2/S3 only, 30 min idle): render a neutral gray hint below the counter — S2: "需要继续采集资料吗？可以粘贴链接或上传材料。" S3: "需要进一步分析吗？可以让助手基于已有证据再拆一层。" Never use alarmist wording like "卡住" or "异常".
- When `stage_code === "done"` (spec §5 final state): main button disappears entirely, replace with a muted success banner "项目已归档 · {stage_status_timestamp}"; progress bar S7 segment renders as completed green. `⋯` menu keeps only "撤回归档" option in the advanced tier.
- After any successful checkpoint POST / conversation-only insertion, call `onWorkspaceRefresh()` to re-fetch workspace state.

- [ ] **Step 5: Add system-notice rendering test + ChatPanel change**

Create `frontend/tests/systemNoticeRendering.test.mjs`:

```javascript
import test from "node:test";
import assert from "node:assert";
import { formatSystemNotice } from "../src/utils/systemNotice.mjs";

test("system_notice event is formatted into a distinct card payload", () => {
  const notice = formatSystemNotice({
    type: "system_notice",
    category: "write_blocked",
    path: "content/report.md",
    reason: "当前轮次还不能开始写正文",
    user_action: "请点击确认大纲按钮",
  });
  assert.strictEqual(notice.kind, "system_card");
  assert.match(notice.text, /写入 content\/report\.md 被拦截/);
  assert.match(notice.text, /请点击确认大纲按钮/);
});
```

Create `frontend/src/utils/systemNotice.mjs` with the formatter, then wire `ChatPanel.jsx` to recognize `system_notice` events in the SSE stream and render them using a muted gray-border card style, clearly distinct from assistant messages.

- [ ] **Step 6: Run frontend tests, verify GREEN**

```
cd frontend && node --test tests/stageAdvanceControl.test.mjs tests/systemNoticeRendering.test.mjs
```

---

### Task 8: Smoke Test + Final Regression

**Files:**
- Modify: `tests/smoke_packaged_app.py`
- Modify: `tests/test_packaging_docs.py` (no change expected, just verify)

- [ ] **Step 1: Extend smoke test to exercise checkpoint endpoint**

In the `check_project_scaffolding` path, after verifying the project is created at S0:

```python
def check_checkpoint_flow(port: int, project_id: str) -> None:
    # Workspace starts at S0 with no checkpoints
    ws = http_get(f"/api/projects/{project_id}/workspace", port)
    if ws.get("checkpoints") != {}:
        raise SmokeFailure(f"新项目 checkpoints 应为空: {ws.get('checkpoints')}")
    # Set outline-confirmed (idempotent)
    r1 = http_post_json(f"/api/projects/{project_id}/checkpoints/outline-confirmed", port, {})
    r2 = http_post_json(f"/api/projects/{project_id}/checkpoints/outline-confirmed", port, {})
    if r1["timestamp"] != r2["timestamp"]:
        raise SmokeFailure("outline-confirmed 不是幂等的")
    # Clear it
    http_post_json(f"/api/projects/{project_id}/checkpoints/outline-confirmed?action=clear", port, {})
    ws = http_get(f"/api/projects/{project_id}/workspace", port)
    if "outline_confirmed_at" in (ws.get("checkpoints") or {}):
        raise SmokeFailure("outline-confirmed 清除未生效")
    log_step("checkpoint endpoint 幂等 set/clear", True)
```

Call `check_checkpoint_flow(port, created_project_id)` before `delete_test_project`.

- [ ] **Step 2: Run full backend + smoke**

```
.venv\Scripts\python -m unittest discover tests -v
.venv\Scripts\python tests\smoke_packaged_app.py
```

Both must be green.

- [ ] **Step 3: Manually verify UI (user-driven, out of scope for agent)**

These need human eyes on the real exe:

1. Start a new project, confirm S1 button says "确认大纲，进入资料采集"
2. Confirm outline, verify S2 has no button (auto-advance)
3. Write report to ~1000 words, verify only the "继续扩写" primary button is visible with "当前 X 字 / 目标 Y 字" neutral hint — the "开始审查" secondary button must NOT appear at all (not merely disabled)
4. Extend report past floor, verify button enables, click it, confirm S5 renders
5. Click `⋯` → "回退：回到 S4 继续改"，confirm returns to S4
6. Verify chat keywords: "撤回大纲确认" clears the outline checkpoint and updates UI

Mark task complete after steps 1–2 pass locally. Steps 3–6 tracked in `current-worklist.md` item #8 as the manual smoke pass.

---

## Rollout Order

1. Task 1 → Task 2 → Task 3 (backend core; lands as one coherent commit series)
2. Task 4 (endpoints + keyword detection, depends on Task 1-3)
3. Task 5 (interceptor, parallel with Task 4 after Task 1)
4. Task 6 (prompt updates, parallel with Task 5)
5. Task 7 (frontend, depends on Task 4)
6. Task 8 (smoke extension, last)

Each task should land as its own commit to keep bisect clean.
