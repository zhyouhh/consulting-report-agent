# S0 Interview + Stage-Ack Signal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement S0 pre-interview gate and XML tag-based stage-ack signaling across 6 stage checkpoints to replace brittle weak-keyword detection, per the 2026-04-21 APPROVED design spec.

**Architecture:** New `backend/stage_ack.py` module (parser + position judge + strip). Schema-incremental migration for `stage_checkpoints.json`. SKILL.md §S0 mandatory one-round interview rule. `backend/chat.py`: tail-guard streaming + post-stream parser hookup on both `_chat_unlocked` and `_chat_stream_unlocked` + S0 `write_file` gate inside `_execute_tool` + NON_PLAN_WRITE S0/S1 patch + `_load_conversation` sanitize + weak-keyword table removal. `backend/main.py` s0 endpoint `action=set` → 400. Frontend: `rollbackMenuLogic.js` S2+ s0 clear option + `workspaceSummary.js` raw-flags preserved + `chatPresentation.js` defensive strip.

**Tech Stack:** Python 3.11/3.12 (`unittest` + `pytest`, `re`, `dataclasses`), Node 20 LTS (`node:test`), React 18 (functional components), FastAPI, OpenAI client streaming, PyWebView + Vite. **Windows-optimized commands** (PowerShell for file ops).

---

## Source of Truth

Design spec: **`docs/superpowers/specs/2026-04-21-s0-interview-and-stage-ack-design.md`** (commit `80e74a2`, APPROVED Round 5).

Every task cites the spec sections it implements. If a plan step conflicts with the spec, **spec wins**.

## File Map

**New files:**
- `backend/stage_ack.py` — `StageAckEvent` dataclass + `StageAckParser` class (parse_raw + parse + strip + position judge)
- `tests/test_stage_ack.py` — parser unit tests

**Modified (backend):**
- `backend/skill.py` — add `s0_interview_done_at` to `STAGE_CHECKPOINT_KEYS` / `_CASCADE_ORDER` / `CHECKPOINT_PREREQ`; change `stage_zero_complete` judge; upgrade `_backfill_stage_checkpoints_if_missing` to schema-incremental migration; add `flags.s0_interview_done` to `_infer_stage_state` return
- `backend/chat.py` — delete `_WEAK_ADVANCE_BY_STAGE` + weak branch in `_detect_stage_keyword`; add `_STRONG_ADVANCE_KEYWORDS["s0_interview_done_at"]`; S0 `has_prior_s0_assistant_turn` soft gate; `_should_allow_non_plan_write` S0/S1 patch; S0 `write_file` gate in `_execute_tool`; `_chat_stream_unlocked` tail guard; `_chat_unlocked` + `_chat_stream_unlocked` finalize hookup with tag priority over keywords; `_load_conversation` sanitize residual tags
- `backend/main.py` — `_CHECKPOINT_ROUTES` add `"s0-interview-done"`; POST handler returns 400 when `s0_interview_done_at` + `action=set`

**Modified (frontend):**
- `frontend/src/utils/rollbackMenuLogic.js` — new `getAdvancedRollbackOptions(stageCode)` returning s0 clear option for S2+
- `frontend/src/components/RollbackMenu.jsx` — render advanced options alongside existing first-level option
- `frontend/src/utils/workspaceSummary.js` — preserve raw `flags` and add `s0InterviewDone` camelCase field
- `frontend/src/utils/chatPresentation.js` — new `stripStageAckTags`; invoke inside `splitAssistantMessageBlocks` entry
- `frontend/src/components/ChatPanel.jsx` — (no direct changes; `splitAssistantMessageBlocks` is the strip entry)

**Modified (docs/skill):**
- `skill/SKILL.md` — §S0 mandatory interview block inserted after 启动门禁; §S1-S7 weak-keyword wording replaced; new appendix "stage-ack 标签规范" + strong-keyword table
- `tests/test_packaging_docs.py` — lock new SKILL.md phrases

**Modified (tests):**
- `tests/test_chat_runtime.py` — tag pipeline (14+ cases per §8 spec), soft gate, NON_PLAN_WRITE patch, S0 write gate, sanitize, stream leak guards
- `tests/test_skill_engine.py` — new checkpoint in STAGE_CHECKPOINT_KEYS, infer state, schema migration 6 cases, flags.s0_interview_done
- `tests/test_main_api.py` — s0 endpoint routes (clear 200, set 400, clear idempotent)
- `frontend/tests/rollbackMenuLogic.test.mjs` (new if absent) — S2+ s0 advanced option
- `frontend/tests/workspaceSummary.test.mjs` — `flags.s0InterviewDone` surfaced + raw flags preserved
- `frontend/tests/chatPresentation.test.mjs` — assistant tag stripped through `splitAssistantMessageBlocks`

## Task Order & Dependencies

Per spec Rollout Phase 1 "迁移兼容测试先行" — infra + migration must land before parser + chat-runtime changes, so old projects are never打回 S0.

```
Task A-C (skill.py checkpoint infra + migration)   ← first
   └─> Task G (main.py s0 endpoint needs s0 key in STAGE_CHECKPOINT_KEYS)
   └─> Task D-F (parser — independent of skill.py, but keys must exist for whitelist)
       └─> Task L (tail guard) + Task M (finalize hookup) need Parser
Task H-K (chat.py keyword + gate + write gate) parallel with Parser
Task N (sanitize) depends on Parser
Task O-Q (frontend) independent
Task R (SKILL.md + packaging docs test)
Task S (integration + smoke) last
```

Sequential execution recommended. 19 tasks.

---

### Task A: `backend/skill.py` — add `s0_interview_done_at` to checkpoint infrastructure

**Spec:** §4 checkpoint 矩阵, §5 `CHECKPOINT_PREREQ[s0] = None`

**Files:**
- Modify: `backend/skill.py` (~lines 38-56 for key sets, ~line 169 for prereq dict)
- Modify: `tests/test_skill_engine.py` (append new test class)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_skill_engine.py`:

```python
class S0CheckpointInfrastructureTests(unittest.TestCase):
    def test_s0_in_stage_checkpoint_keys(self):
        from backend.skill import SkillEngine
        self.assertIn("s0_interview_done_at", SkillEngine.STAGE_CHECKPOINT_KEYS)

    def test_s0_first_in_cascade_order(self):
        from backend.skill import SkillEngine
        self.assertEqual(SkillEngine._CASCADE_ORDER[0], "s0_interview_done_at")

    def test_s0_prereq_none_entry_present(self):
        from backend.skill import SkillEngine
        self.assertIn("s0_interview_done_at", SkillEngine.CHECKPOINT_PREREQ)
        self.assertIsNone(SkillEngine.CHECKPOINT_PREREQ["s0_interview_done_at"])

    def test_cascade_order_covers_all_keys_assertion_still_holds(self):
        # SkillEngine has `assert set(_CASCADE_ORDER) == STAGE_CHECKPOINT_KEYS`
        # at class-body level. If Task A broke parity, import fails outright.
        import backend.skill
        self.assertTrue(hasattr(backend.skill, "SkillEngine"))

    def test_s0_prereq_notice_returns_none(self):
        import tempfile
        from pathlib import Path
        from backend.skill import SkillEngine
        with tempfile.TemporaryDirectory() as tmp:
            engine = SkillEngine(Path(tmp) / "p", Path(tmp) / "s")
            self.assertIsNone(
                engine.get_stage_checkpoint_prereq_notice("s0_interview_done_at")
            )
```

- [ ] **Step 2: Run to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_skill_engine.py::S0CheckpointInfrastructureTests -v
```

Expected: 3 out of 5 fail (KeyError / AssertionError on the new key); the parity assertion might already fail at import if any change was mid-way.

- [ ] **Step 3: Implement in `backend/skill.py`**

Change `STAGE_CHECKPOINT_KEYS` (~line 39):

```python
    STAGE_CHECKPOINT_KEYS = {
        "s0_interview_done_at",
        "outline_confirmed_at",
        "review_started_at",
        "review_passed_at",
        "presentation_ready_at",
        "delivery_archived_at",
    }
```

Change `_CASCADE_ORDER` (~line 47):

```python
    _CASCADE_ORDER = [
        "s0_interview_done_at",
        "outline_confirmed_at",
        "review_started_at",
        "review_passed_at",
        "presentation_ready_at",
        "delivery_archived_at",
    ]
```

Add to `CHECKPOINT_PREREQ` (~line 169, insert as first entry):

```python
    CHECKPOINT_PREREQ = {
        "s0_interview_done_at": None,
        "outline_confirmed_at": (
            "_has_effective_outline",
            "plan/outline.md",
            "需要先生成有效报告大纲，才能确认大纲并进入资料采集。",
            "请先让助手补齐 `plan/outline.md`，再确认大纲。",
        ),
        # ... (existing 4 entries unchanged)
    }
```

No change to `get_stage_checkpoint_prereq_notice` / `_validate_stage_checkpoint_prereq` — they already short-circuit when `prereq` is falsy (existing `if not prereq: return None/return`).

- [ ] **Step 4: Run to verify pass**

```powershell
.venv\Scripts\python -m pytest tests/test_skill_engine.py -v
```

Expected: all pre-existing + 5 new pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/skill.py tests/test_skill_engine.py
git commit -m "Add s0_interview_done_at to stage checkpoint infrastructure"
```

---

### Task B: `_infer_stage_state.stage_zero_complete` + `flags.s0_interview_done`

**Spec:** §7 `_infer_stage_state: stage_zero_complete` 从 `project_overview_ready` 改成 checkpoint-based; `flags.s0_interview_done` 字段

**Files:**
- Modify: `backend/skill.py` (~line 1257, `_infer_stage_state`)
- Modify: `tests/test_skill_engine.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_skill_engine.py`:

```python
class S0StageInferenceTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from backend.skill import SkillEngine
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        projects_dir = Path(self.tmp.name) / "projects"
        skill_dir = Path(self.tmp.name) / "skill"
        projects_dir.mkdir()
        skill_dir.mkdir()
        self.engine = SkillEngine(projects_dir, skill_dir)
        project = self.engine.create_project(
            name="demo-s0",
            workspace_dir=str(Path(self.tmp.name) / "ws"),
            project_type="strategy-consulting",
            theme="S0 test",
            target_audience="CFO",
            deadline="2026-12-31",
            expected_length="3000",
        )
        self.project_path = Path(project["project_dir"])

    def test_s0_without_checkpoint_stays_s0(self):
        state = self.engine._infer_stage_state(self.project_path)
        self.assertEqual(state["stage_code"], "S0")

    def test_s0_with_checkpoint_advances_to_s1(self):
        import json
        from datetime import datetime
        (self.project_path / "stage_checkpoints.json").write_text(
            json.dumps({
                "s0_interview_done_at": datetime.now().isoformat(timespec="seconds"),
            }),
            encoding="utf-8",
        )
        state = self.engine._infer_stage_state(self.project_path)
        self.assertEqual(state["stage_code"], "S1")

    def test_flags_has_s0_interview_done(self):
        state = self.engine._infer_stage_state(self.project_path)
        self.assertIn("s0_interview_done", state["flags"])
        self.assertFalse(state["flags"]["s0_interview_done"])

    def test_flags_s0_true_after_checkpoint(self):
        import json
        (self.project_path / "stage_checkpoints.json").write_text(
            json.dumps({"s0_interview_done_at": "2026-04-21T12:00:00"}),
            encoding="utf-8",
        )
        state = self.engine._infer_stage_state(self.project_path)
        self.assertTrue(state["flags"]["s0_interview_done"])

    def test_build_completed_s0_only_lights_overview(self):
        # S0 stage, project-overview.md exists (from create_project),
        # no s0_interview_done_at checkpoint — should only light item [2]
        from backend.skill import SkillEngine
        state = self.engine._infer_stage_state(self.project_path)
        completed = state["completed_items"]
        overview_item = SkillEngine.STAGE_CHECKLIST_ITEMS["S0"][2]  # "project-overview.md 创建"
        self.assertIn(overview_item, completed)
        # Other S0 items NOT complete yet
        interview_item = SkillEngine.STAGE_CHECKLIST_ITEMS["S0"][0]  # "需求访谈完成"
        self.assertNotIn(interview_item, completed)
```

- [ ] **Step 2: Run to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_skill_engine.py::S0StageInferenceTests -v
```

Expected: `test_s0_without_checkpoint_stays_s0` and flags tests fail (current `stage_zero_complete = project_overview_ready` means S0 → S1 as soon as overview exists).

- [ ] **Step 3: Implement in `backend/skill.py`**

In `_infer_stage_state` (~line 1232), change:

```python
        project_overview_ready = self._is_effective_plan_file(project_path, "project-overview.md")
        # ... (existing flag setups unchanged above)

        interview_done = "s0_interview_done_at" in checkpoints  # NEW
        outline_confirmed = "outline_confirmed_at" in checkpoints
        # ... (rest of checkpoint flag setup unchanged)

        stage_zero_complete = project_overview_ready and interview_done  # CHANGED
        # ... (rest of stage_*_complete chain unchanged)
```

And in the `flags` dict near the end of `_infer_stage_state` (~line 1304), add:

```python
        flags = {
            "project_overview_ready": project_overview_ready,
            # ... (existing fields unchanged)
            "s0_interview_done": interview_done,  # NEW — used by frontend workspaceSummary
            "outline_confirmed": outline_confirmed,
            # ... (rest unchanged)
        }
```

- [ ] **Step 4: Run to verify pass**

```powershell
.venv\Scripts\python -m pytest tests/test_skill_engine.py -v
```

Existing tests that relied on `stage_code == S1` right after project creation will now return S0. Fix those fixtures by setting `s0_interview_done_at` in the checkpoints file of each such test's project (add a helper if many use it). **Do not revert the judge change.**

- [ ] **Step 5: Commit**

```powershell
git add backend/skill.py tests/test_skill_engine.py
git commit -m "Gate S0 completion on s0_interview_done_at checkpoint"
```

---

### Task C: Schema-incremental migration for `_backfill_stage_checkpoints_if_missing`

**Spec:** §7 schema 增量迁移, Rollout Phase 1 迁移兼容测试先行, Risks 已迁移项目缺 s0 schema

**Files:**
- Modify: `backend/skill.py` (~line 232, `_backfill_stage_checkpoints_if_missing`)
- Modify: `tests/test_skill_engine.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_skill_engine.py`:

```python
class S0SchemaMigrationTests(unittest.TestCase):
    def setUp(self):
        import tempfile, json
        from pathlib import Path
        from backend.skill import SkillEngine
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.projects_dir = Path(self.tmp.name) / "projects"
        self.skill_dir = Path(self.tmp.name) / "skill"
        self.projects_dir.mkdir()
        self.skill_dir.mkdir()
        self.engine = SkillEngine(self.projects_dir, self.skill_dir)
        self.project_path = self.projects_dir / "proj-test"
        (self.project_path / "plan").mkdir(parents=True)

    def _write_stage_gates(self, stage_code):
        (self.project_path / "plan" / "stage-gates.md").write_text(
            f"# 项目阶段与门禁\n\n## 当前阶段\n\n**阶段**: {stage_code}\n",
            encoding="utf-8",
        )

    def _write_checkpoints(self, data):
        import json
        (self.project_path / "stage_checkpoints.json").write_text(
            json.dumps(data), encoding="utf-8"
        )

    def _read_checkpoints(self):
        import json
        path = self.project_path / "stage_checkpoints.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def test_file_missing_stage_s0_creates_with_marker_no_s0(self):
        self._write_stage_gates("S0")
        self.engine._backfill_stage_checkpoints_if_missing(self.project_path)
        raw = self._read_checkpoints()
        self.assertIn("__migrated_at", raw)
        self.assertNotIn("s0_interview_done_at", raw)  # stage=S0 does not backfill

    def test_file_missing_stage_s1_backfills_s0(self):
        self._write_stage_gates("S1")
        self.engine._backfill_stage_checkpoints_if_missing(self.project_path)
        raw = self._read_checkpoints()
        self.assertIn("s0_interview_done_at", raw)
        # outline_confirmed_at still gated at stage >= S2
        self.assertNotIn("outline_confirmed_at", raw)

    def test_file_missing_stage_s2_backfills_both_s0_and_outline(self):
        self._write_stage_gates("S2")
        self.engine._backfill_stage_checkpoints_if_missing(self.project_path)
        raw = self._read_checkpoints()
        self.assertIn("s0_interview_done_at", raw)
        self.assertIn("outline_confirmed_at", raw)

    def test_file_exists_missing_s0_stage_s1_backfills_s0(self):
        # Simulates a 4-17 spec project: file exists with marker but no s0 key
        self._write_checkpoints({"__migrated_at": "2026-04-17T10:00:00"})
        self._write_stage_gates("S1")
        self.engine._backfill_stage_checkpoints_if_missing(self.project_path)
        raw = self._read_checkpoints()
        self.assertIn("s0_interview_done_at", raw)

    def test_file_exists_missing_s0_stage_s0_does_not_backfill(self):
        self._write_checkpoints({"__migrated_at": "2026-04-17T10:00:00"})
        self._write_stage_gates("S0")
        self.engine._backfill_stage_checkpoints_if_missing(self.project_path)
        raw = self._read_checkpoints()
        self.assertNotIn("s0_interview_done_at", raw)

    def test_file_exists_with_outline_confirmed_backfills_s0(self):
        # outline is downstream → imply s0 done (4-17 spec project mid-stage)
        self._write_checkpoints({
            "__migrated_at": "2026-04-17T10:00:00",
            "outline_confirmed_at": "2026-04-18T09:00:00",
        })
        # no stage-gates.md this time — rely on downstream-checkpoint heuristic
        self.engine._backfill_stage_checkpoints_if_missing(self.project_path)
        raw = self._read_checkpoints()
        self.assertIn("s0_interview_done_at", raw)
        self.assertEqual(raw["outline_confirmed_at"], "2026-04-18T09:00:00")

    def test_file_exists_has_s0_noop(self):
        ts = "2026-04-20T08:00:00"
        self._write_checkpoints({
            "__migrated_at": "2026-04-17T10:00:00",
            "s0_interview_done_at": ts,
        })
        self._write_stage_gates("S2")
        self.engine._backfill_stage_checkpoints_if_missing(self.project_path)
        raw = self._read_checkpoints()
        self.assertEqual(raw["s0_interview_done_at"], ts)

    def test_idempotent_second_call_no_change(self):
        self._write_stage_gates("S2")
        self.engine._backfill_stage_checkpoints_if_missing(self.project_path)
        first = self._read_checkpoints()
        self.engine._backfill_stage_checkpoints_if_missing(self.project_path)
        second = self._read_checkpoints()
        self.assertEqual(first, second)
```

- [ ] **Step 2: Run to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_skill_engine.py::S0SchemaMigrationTests -v
```

Expected: multiple fails (existing `_backfill_stage_checkpoints_if_missing` only runs when file doesn't exist).

- [ ] **Step 3: Rewrite `_backfill_stage_checkpoints_if_missing` in `backend/skill.py`** (~line 232)

```python
    def _backfill_stage_checkpoints_if_missing(self, project_path):
        """Schema-incremental migration for stage_checkpoints.json.

        Runs idempotently on every project load. Scenarios:
        1. File missing → create with __migrated_at; backfill by stage-gates.md
        2. File exists, missing s0_interview_done_at:
           - stage-gates.md shows stage ≥ S1, OR any downstream checkpoint set
             → backfill s0
           - otherwise (stage=S0, no downstream) → do NOT backfill
        3. Key already present → no-op
        4. outline_confirmed_at still backfills only at stage ≥ S2 (unchanged)
        """
        checkpoints_path = self._stage_checkpoints_path(project_path)
        stage_gates_path = Path(project_path) / "plan" / "stage-gates.md"
        timestamp = datetime.now().isoformat(timespec="seconds")

        raw = self._read_raw_stage_checkpoints(project_path)
        file_existed_before = checkpoints_path.exists()
        changed = False

        if not file_existed_before:
            raw = {self.MIGRATION_MARKER_KEY: timestamp}
            changed = True

        current_stage = None
        if stage_gates_path.exists():
            stage_text = stage_gates_path.read_text(encoding="utf-8")
            current_stage = self._extract_stage_code(stage_text)

        # Downstream = any checkpoint other than s0
        has_downstream = any(
            key in raw
            for key in self._CASCADE_ORDER
            if key != "s0_interview_done_at"
        )

        # Backfill s0 when stage >= S1 OR downstream present
        if "s0_interview_done_at" not in raw:
            stage_ok = False
            if current_stage:
                try:
                    stage_ok = self._stage_index(current_stage) >= self._stage_index("S1")
                except ValueError:
                    stage_ok = False  # malformed stage-gates; stay cautious
            if stage_ok or has_downstream:
                raw["s0_interview_done_at"] = timestamp
                changed = True

        # Preserve legacy outline backfill (stage >= S2)
        if "outline_confirmed_at" not in raw and current_stage:
            try:
                if self._stage_index(current_stage) >= self._stage_index("S2"):
                    raw["outline_confirmed_at"] = timestamp
                    changed = True
            except ValueError:
                pass

        if changed:
            self._write_raw_stage_checkpoints(project_path, raw)
```

- [ ] **Step 4: Run to verify pass**

```powershell
.venv\Scripts\python -m pytest tests/test_skill_engine.py -v
```

Expected: all 8 new pass + pre-existing pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/skill.py tests/test_skill_engine.py
git commit -m "Upgrade stage checkpoint backfill to schema-incremental migration"
```

---

### Task D: `StageAckEvent` dataclass + `StageAckParser.parse_raw`

**Spec:** §2 扫描正则 + 6 KEY 白名单 + 多 tag 按序（不去重）, Appendix A 正则 `[a-z_0-9]+`; unknown key per review Task 12 §3 — **识别为 non-executable with ignored_reason="unknown_key"**, not dropped.

**Files:**
- Create: `backend/stage_ack.py`
- Create: `tests/test_stage_ack.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_stage_ack.py`:

```python
import unittest

from backend.stage_ack import StageAckEvent, StageAckParser, VALID_KEYS


class StageAckParseRawTests(unittest.TestCase):
    def setUp(self):
        self.parser = StageAckParser()

    def test_single_set_tag(self):
        events = self.parser.parse_raw("<stage-ack>outline_confirmed_at</stage-ack>")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].action, "set")
        self.assertEqual(events[0].key, "outline_confirmed_at")
        self.assertTrue(events[0].executable)
        self.assertIsNone(events[0].ignored_reason)

    def test_clear_action(self):
        events = self.parser.parse_raw(
            '<stage-ack action="clear">outline_confirmed_at</stage-ack>'
        )
        self.assertEqual(events[0].action, "clear")

    def test_explicit_set_action(self):
        events = self.parser.parse_raw(
            '<stage-ack action="set">s0_interview_done_at</stage-ack>'
        )
        self.assertEqual(events[0].action, "set")

    def test_unknown_key_yields_non_executable_event(self):
        events = self.parser.parse_raw("<stage-ack>not_a_real_key</stage-ack>")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].key, "not_a_real_key")
        self.assertFalse(events[0].executable)
        self.assertEqual(events[0].ignored_reason, "unknown_key")

    def test_all_six_valid_keys(self):
        keys = [
            "s0_interview_done_at",
            "outline_confirmed_at",
            "review_started_at",
            "review_passed_at",
            "presentation_ready_at",
            "delivery_archived_at",
        ]
        self.assertEqual(VALID_KEYS, frozenset(keys))
        for key in keys:
            events = self.parser.parse_raw(f"<stage-ack>{key}</stage-ack>")
            self.assertEqual(len(events), 1)
            self.assertTrue(events[0].executable)

    def test_multi_tag_preserves_order_no_dedup(self):
        events = self.parser.parse_raw(
            "<stage-ack>outline_confirmed_at</stage-ack>\n"
            '<stage-ack action="clear">outline_confirmed_at</stage-ack>\n'
            "<stage-ack>outline_confirmed_at</stage-ack>\n"
        )
        self.assertEqual([e.action for e in events], ["set", "clear", "set"])

    def test_tag_positions_captured(self):
        content = "前缀 <stage-ack>outline_confirmed_at</stage-ack> 后缀"
        events = self.parser.parse_raw(content)
        self.assertEqual(content[events[0].start:events[0].end], events[0].raw)
```

- [ ] **Step 2: Run to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_stage_ack.py -v
```

Expected: `ModuleNotFoundError: No module named 'backend.stage_ack'`.

- [ ] **Step 3: Implement `backend/stage_ack.py`**

```python
"""XML tag parser for stage checkpoint acknowledgment signals.

Per 2026-04-21 design spec §2: assistant outputs <stage-ack>KEY</stage-ack>
or <stage-ack action="clear">KEY</stage-ack> at the tail of a reply to advance
or rollback a stage checkpoint. This module parses those tags out of assistant
content.

  raw_match → classify (position + key) → execute/ignore → strip

Position judgment (fenced/inline/blockquote/non-tail) is in parse().
Chat runtime hookup (tag priority over keywords, prereq validation, soft
gate) is in `backend/chat.py`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


VALID_KEYS = frozenset({
    "s0_interview_done_at",
    "outline_confirmed_at",
    "review_started_at",
    "review_passed_at",
    "presentation_ready_at",
    "delivery_archived_at",
})

TAG_PATTERN = re.compile(
    r'<stage-ack(?:\s+action="(set|clear)")?>([a-z_0-9]+)</stage-ack>',
    re.IGNORECASE,
)


@dataclass
class StageAckEvent:
    raw: str
    action: str
    key: str
    start: int
    end: int
    executable: bool = True
    ignored_reason: str | None = None


class StageAckParser:
    """Parse and strip stage-ack tags from assistant content.

    - `parse_raw(content)` finds every well-formed <stage-ack>KEY</stage-ack>
      occurrence (including unknown keys), in order. Unknown keys yield events
      flagged executable=False / ignored_reason="unknown_key" so the caller
      can log a warning without dropping the strip obligation.
    - `parse(content)` runs parse_raw then position-classifies each event,
      setting executable=False with ignored_reason ∈ {unknown_key, in_fenced_code,
      in_inline_code, in_blockquote, not_independent_line, not_tail}.
    - `strip(content)` removes every tag span regardless of executable flag.
    """

    def parse_raw(self, content: str) -> list[StageAckEvent]:
        if not content:
            return []
        events: list[StageAckEvent] = []
        for match in TAG_PATTERN.finditer(content):
            action = match.group(1) or "set"
            key = match.group(2)
            if key in VALID_KEYS:
                executable = True
                ignored = None
            else:
                executable = False
                ignored = "unknown_key"
            events.append(StageAckEvent(
                raw=match.group(0),
                action=action,
                key=key,
                start=match.start(),
                end=match.end(),
                executable=executable,
                ignored_reason=ignored,
            ))
        return events
```

- [ ] **Step 4: Run to verify pass**

```powershell
.venv\Scripts\python -m pytest tests/test_stage_ack.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```powershell
git add backend/stage_ack.py tests/test_stage_ack.py
git commit -m "Add StageAckParser parse_raw with unknown-key events"
```

---

### Task E: `StageAckParser.parse()` with position judgment

**Spec:** §2 可执行 tag 必须尾部 + 独立行 + fenced/inline/blockquote 外

**Files:**
- Modify: `backend/stage_ack.py`
- Modify: `tests/test_stage_ack.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_stage_ack.py`:

```python
class StageAckPositionJudgeTests(unittest.TestCase):
    def setUp(self):
        self.parser = StageAckParser()

    def test_tail_independent_line_executable(self):
        events = self.parser.parse(
            "报告完成。\n\n<stage-ack>outline_confirmed_at</stage-ack>\n"
        )
        self.assertTrue(events[0].executable)
        self.assertIsNone(events[0].ignored_reason)

    def test_non_tail_tag(self):
        events = self.parser.parse(
            "<stage-ack>outline_confirmed_at</stage-ack>\n\n这段在 tag 后。\n"
        )
        self.assertFalse(events[0].executable)
        self.assertEqual(events[0].ignored_reason, "not_tail")

    def test_fenced_code_backtick(self):
        events = self.parser.parse(
            "示例：\n```md\n<stage-ack>outline_confirmed_at</stage-ack>\n```\n正文。\n"
        )
        self.assertFalse(events[0].executable)
        self.assertEqual(events[0].ignored_reason, "in_fenced_code")

    def test_fenced_code_tilde(self):
        events = self.parser.parse(
            "~~~\n<stage-ack>outline_confirmed_at</stage-ack>\n~~~\n"
        )
        self.assertFalse(events[0].executable)
        self.assertEqual(events[0].ignored_reason, "in_fenced_code")

    def test_inline_code(self):
        events = self.parser.parse(
            "示例：`<stage-ack>outline_confirmed_at</stage-ack>`\n"
        )
        self.assertFalse(events[0].executable)
        self.assertEqual(events[0].ignored_reason, "in_inline_code")

    def test_blockquote(self):
        events = self.parser.parse(
            "> <stage-ack>outline_confirmed_at</stage-ack>\n"
        )
        self.assertFalse(events[0].executable)
        self.assertEqual(events[0].ignored_reason, "in_blockquote")

    def test_not_independent_line(self):
        events = self.parser.parse(
            "推进 <stage-ack>outline_confirmed_at</stage-ack> 吧"
        )
        self.assertFalse(events[0].executable)
        self.assertEqual(events[0].ignored_reason, "not_independent_line")

    def test_trailing_whitespace_still_tail(self):
        events = self.parser.parse(
            "完成。\n<stage-ack>outline_confirmed_at</stage-ack>\n   \n"
        )
        self.assertTrue(events[0].executable)

    def test_multiple_tail_tags_all_executable(self):
        events = self.parser.parse(
            "回退再推进。\n"
            '<stage-ack action="clear">outline_confirmed_at</stage-ack>\n'
            "<stage-ack>outline_confirmed_at</stage-ack>\n"
        )
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e.executable for e in events))

    def test_unknown_key_even_at_tail_still_flagged_unknown(self):
        events = self.parser.parse(
            "正文。\n\n<stage-ack>bogus</stage-ack>\n"
        )
        self.assertFalse(events[0].executable)
        self.assertEqual(events[0].ignored_reason, "unknown_key")
```

- [ ] **Step 2: Run to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_stage_ack.py::StageAckPositionJudgeTests -v
```

Expected: `AttributeError: 'StageAckParser' object has no attribute 'parse'`.

- [ ] **Step 3: Implement `parse` with position judge**

Append to `backend/stage_ack.py` (inside `StageAckParser` class):

```python
    FENCED_RE = re.compile(r"^( {0,3})(```|~~~)", re.MULTILINE)

    def parse(self, content: str) -> list[StageAckEvent]:
        if not content:
            return []
        events = self.parse_raw(content)
        if not events:
            return []

        fenced_spans = self._fenced_spans(content)
        tail_anchor = self._tail_anchor(content, events)

        for event in events:
            if event.ignored_reason == "unknown_key":
                continue  # already non-executable
            reason = self._classify_position(content, event, fenced_spans, tail_anchor)
            if reason is not None:
                event.executable = False
                event.ignored_reason = reason
        return events

    def _classify_position(
        self,
        content: str,
        event: StageAckEvent,
        fenced_spans: list[tuple[int, int]],
        tail_anchor: int,
    ) -> str | None:
        # Fenced code has highest precedence
        for start, end in fenced_spans:
            if start <= event.start < end:
                return "in_fenced_code"

        # Line-local context
        line_start = content.rfind("\n", 0, event.start) + 1
        line_end_nl = content.find("\n", event.end)
        line_end = line_end_nl if line_end_nl != -1 else len(content)
        before = content[line_start:event.start]
        after = content[event.end:line_end]

        # Blockquote: optional whitespace then `>`
        if re.match(r"^\s*>", before):
            return "in_blockquote"

        # Inline code: odd count of backticks before on same line
        if before.count("`") % 2 == 1:
            return "in_inline_code"

        # Independent line: only whitespace flanking on the same line
        if before.strip() or after.strip():
            return "not_independent_line"

        # Tail: event must start at or after the last non-tag non-whitespace
        if event.start < tail_anchor:
            return "not_tail"

        return None

    def _fenced_spans(self, content: str) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        open_start: int | None = None
        open_fence: str | None = None
        for match in self.FENCED_RE.finditer(content):
            fence = match.group(2)
            fence_line_start = match.start()
            if open_start is None:
                open_start = fence_line_start
                open_fence = fence
            elif fence == open_fence:
                line_end_nl = content.find("\n", match.end())
                close_end = line_end_nl + 1 if line_end_nl != -1 else len(content)
                spans.append((open_start, close_end))
                open_start = None
                open_fence = None
        if open_start is not None:
            spans.append((open_start, len(content)))
        return spans

    def _tail_anchor(
        self,
        content: str,
        events: list[StageAckEvent],
    ) -> int:
        """Return offset one past the last non-tag non-whitespace char.

        Any event starting >= this offset is at the tail.
        """
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

- [ ] **Step 4: Run to verify pass**

```powershell
.venv\Scripts\python -m pytest tests/test_stage_ack.py -v
```

Expected: all pass (7 from Task D + 10 from Task E = 17).

- [ ] **Step 5: Commit**

```powershell
git add backend/stage_ack.py tests/test_stage_ack.py
git commit -m "Add tag position judgment to StageAckParser"
```

---

### Task F: `StageAckParser.strip()` removes all tags

**Spec:** §2 剥离 (executable or not, unknown or not); §2 collapse blank lines caused by removal

**Files:**
- Modify: `backend/stage_ack.py`
- Modify: `tests/test_stage_ack.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_stage_ack.py`:

```python
class StageAckStripTests(unittest.TestCase):
    def setUp(self):
        self.parser = StageAckParser()

    def test_strip_executable_tag(self):
        content = "完成。\n<stage-ack>outline_confirmed_at</stage-ack>\n"
        out = self.parser.strip(content)
        self.assertNotIn("<stage-ack", out)
        self.assertIn("完成。", out)

    def test_strip_non_executable_fenced_tag(self):
        content = "示例：\n```md\n<stage-ack>outline_confirmed_at</stage-ack>\n```\n"
        out = self.parser.strip(content)
        self.assertNotIn("<stage-ack", out)
        self.assertIn("```md", out)

    def test_strip_unknown_key_tag(self):
        content = "正文。\n<stage-ack>bogus</stage-ack>"
        self.assertNotIn("<stage-ack", self.parser.strip(content))

    def test_strip_clear_action_tag(self):
        content = '<stage-ack action="clear">outline_confirmed_at</stage-ack>'
        self.assertNotIn("<stage-ack", self.parser.strip(content))

    def test_strip_multiple_tags(self):
        content = (
            "A\n<stage-ack>outline_confirmed_at</stage-ack>\n"
            "B\n<stage-ack>review_started_at</stage-ack>\n"
        )
        out = self.parser.strip(content)
        self.assertNotIn("<stage-ack", out)
        self.assertIn("A", out)
        self.assertIn("B", out)

    def test_strip_no_tag_unchanged(self):
        content = "纯正文。"
        self.assertEqual(self.parser.strip(content), content)

    def test_strip_collapses_3plus_newlines_to_2(self):
        content = "头。\n<stage-ack>outline_confirmed_at</stage-ack>\n\n\n尾。"
        out = self.parser.strip(content)
        self.assertNotIn("\n\n\n", out)
        self.assertIn("头。", out)
        self.assertIn("尾。", out)
```

- [ ] **Step 2: Run to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_stage_ack.py::StageAckStripTests -v
```

Expected: `AttributeError: 'StageAckParser' object has no attribute 'strip'`.

- [ ] **Step 3: Implement `strip`**

Append to `backend/stage_ack.py` (inside `StageAckParser`):

```python
    STRIP_PATTERN = re.compile(
        r'<stage-ack(?:\s+action="(?:set|clear)")?>[a-z_0-9]+</stage-ack>',
        re.IGNORECASE,
    )

    def strip(self, content: str) -> str:
        """Remove every <stage-ack>…</stage-ack> occurrence regardless of key
        validity or position. Collapse runs of 3+ newlines caused by the
        removal down to 2.
        """
        if not content or "<stage-ack" not in content.lower():
            return content
        result = self.STRIP_PATTERN.sub("", content)
        return re.sub(r"\n{3,}", "\n\n", result)
```

- [ ] **Step 4: Run to verify pass**

```powershell
.venv\Scripts\python -m pytest tests/test_stage_ack.py -v
```

Expected: 24 passed.

- [ ] **Step 5: Commit**

```powershell
git add backend/stage_ack.py tests/test_stage_ack.py
git commit -m "Add StageAckParser.strip for tag removal"
```

---

### Task G: `backend/main.py` — s0 endpoint route + 400 for `action=set`

**Spec:** §7 backend/main.py, §8 `s0 set route returns 400 Bad Request`

**Files:**
- Modify: `backend/main.py` (`_CHECKPOINT_ROUTES` + POST handler)
- Modify: `tests/test_main_api.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_main_api.py` (adapt to existing `CheckpointEndpointTests` style — mock `record_stage_checkpoint` instead of building a real project):

```python
class S0CheckpointEndpointTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from unittest import mock
        import backend.main as main_module
        self.main_module = main_module
        self.client = TestClient(main_module.app)
        # Patch the skill_engine singleton
        self.patcher = mock.patch.object(
            main_module, "skill_engine", autospec=True
        )
        self.mock_engine = self.patcher.start()
        self.addCleanup(self.patcher.stop)
        # Successful record returns {"status":"ok","key":...,"timestamp":...}
        self.mock_engine.record_stage_checkpoint.return_value = {
            "status": "ok", "key": "s0_interview_done_at",
            "timestamp": "2026-04-21T12:00:00",
        }

    def test_s0_clear_route_returns_200_and_calls_engine(self):
        resp = self.client.post(
            "/api/projects/demo/checkpoints/s0-interview-done",
            params={"action": "clear"},
        )
        self.assertEqual(resp.status_code, 200)
        self.mock_engine.record_stage_checkpoint.assert_called_once_with(
            "demo", "s0_interview_done_at", "clear"
        )

    def test_s0_set_route_returns_400_and_does_not_call_engine(self):
        resp = self.client.post(
            "/api/projects/demo/checkpoints/s0-interview-done",
            params={"action": "set"},
        )
        self.assertEqual(resp.status_code, 400)
        detail = resp.json()["detail"]
        self.assertIn("s0", detail.lower())
        self.mock_engine.record_stage_checkpoint.assert_not_called()

    def test_s0_clear_idempotent_when_engine_returns_ok(self):
        # engine mock returns ok regardless; endpoint should still 200
        resp = self.client.post(
            "/api/projects/demo/checkpoints/s0-interview-done",
            params={"action": "clear"},
        )
        self.assertEqual(resp.status_code, 200)

    def test_other_checkpoint_set_unaffected(self):
        # Sanity: outline-confirmed set still works
        resp = self.client.post(
            "/api/projects/demo/checkpoints/outline-confirmed",
            params={"action": "set"},
        )
        self.assertIn(resp.status_code, {200, 400})  # whichever the existing
        # suite asserts is fine — we just check we did not break it
```

- [ ] **Step 2: Run to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_main_api.py::S0CheckpointEndpointTests -v
```

Expected: 404 (route not mapped) on clear/set routes.

- [ ] **Step 3: Implement in `backend/main.py`**

Locate `_CHECKPOINT_ROUTES` (grep if line number drifted) and add the s0 entry as the FIRST key (per `_CASCADE_ORDER`):

```python
_CHECKPOINT_ROUTES = {
    "s0-interview-done": "s0_interview_done_at",
    "outline-confirmed": "outline_confirmed_at",
    # ... existing entries unchanged
}
```

In the checkpoint POST handler, add the s0-set guard before dispatching to `record_stage_checkpoint`:

```python
@app.post("/api/projects/{project_id}/checkpoints/{name}")
def post_checkpoint(project_id: str, name: str, action: str = "set"):
    key = _CHECKPOINT_ROUTES.get(name)
    if key is None:
        raise HTTPException(404, detail=f"未知 checkpoint: {name}")
    if action not in ("set", "clear"):
        raise HTTPException(400, detail=f"非法 action: {action}")
    if key == "s0_interview_done_at" and action == "set":
        raise HTTPException(
            400,
            detail=(
                "s0_interview_done_at 不能通过 endpoint 直接 set："
                "endpoint 层无对话上下文，无法执行 S0 对话级软门槛。"
                "set 只能走 StageAckParser / strong 关键词软门槛 / schema migration。"
            ),
        )
    try:
        return skill_engine.record_stage_checkpoint(project_id, key, action)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))
```

- [ ] **Step 4: Run to verify pass**

```powershell
.venv\Scripts\python -m pytest tests/test_main_api.py -v
```

Expected: all existing + 4 new pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/main.py tests/test_main_api.py
git commit -m "Add s0-interview-done route; reject action=set with 400"
```

---

### Task H: Delete `_WEAK_ADVANCE_BY_STAGE`; add `_STRONG_ADVANCE_KEYWORDS[s0]`; update `_STAGE_RANK`

**Spec:** §3 关键词表重构

**Files:**
- Modify: `backend/chat.py` (~lines 158-170 + `_detect_stage_keyword` ~line 3015 + `_STAGE_RANK` ~line 184)
- Modify: `tests/test_chat_runtime.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_chat_runtime.py`:

```python
class KeywordTableRestructureTests(unittest.TestCase):
    def test_weak_advance_table_absent(self):
        from backend.chat import ChatHandler
        self.assertFalse(
            hasattr(ChatHandler, "_WEAK_ADVANCE_BY_STAGE"),
            "_WEAK_ADVANCE_BY_STAGE must be removed per spec",
        )

    def test_s0_strong_keywords_present(self):
        from backend.chat import ChatHandler
        self.assertIn("s0_interview_done_at", ChatHandler._STRONG_ADVANCE_KEYWORDS)
        for phrase in ["跳过访谈", "不用问了", "先写大纲吧", "够了开始吧", "直接开始"]:
            self.assertIn(
                phrase,
                ChatHandler._STRONG_ADVANCE_KEYWORDS["s0_interview_done_at"],
            )

    def test_stage_rank_has_s0_first(self):
        from backend.chat import ChatHandler
        self.assertEqual(ChatHandler._STAGE_RANK["s0_interview_done_at"], 0)
        self.assertEqual(ChatHandler._STAGE_RANK["outline_confirmed_at"], 1)


class WeakKeywordNoLongerTriggersTests(ChatRuntimeTests):
    def test_ok_in_s1_returns_none(self):
        handler = self._make_handler_with_project()
        result = handler._detect_stage_keyword("OK", "S1", self.project_id)
        self.assertIsNone(result)

    def test_keyi_in_s5_returns_none(self):
        handler = self._make_handler_with_project()
        result = handler._detect_stage_keyword("可以", "S5", self.project_id)
        self.assertIsNone(result)

    def test_strong_keyword_still_works(self):
        handler = self._make_handler_with_project()
        result = handler._detect_stage_keyword("确认大纲", "S1", self.project_id)
        self.assertEqual(result, ("set", "outline_confirmed_at"))
```

Note: `WeakKeywordNoLongerTriggersTests` extends `ChatRuntimeTests` (line 23 in `tests/test_chat_runtime.py`) to inherit `_make_handler_with_project`.

- [ ] **Step 2: Run to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::KeywordTableRestructureTests tests/test_chat_runtime.py::WeakKeywordNoLongerTriggersTests -v
```

Expected: 2+ fails.

- [ ] **Step 3: Implement in `backend/chat.py`**

**Delete** lines around 165-170:

```python
    _WEAK_ADVANCE_BY_STAGE = { ... }  # DELETE ENTIRE BLOCK
```

**Update `_STRONG_ADVANCE_KEYWORDS`** (~line 158):

```python
    _STRONG_ADVANCE_KEYWORDS = {
        "s0_interview_done_at": [
            "跳过访谈", "不用问了", "先写大纲吧", "够了开始吧", "直接开始",
        ],
        "outline_confirmed_at": [
            "确认大纲", "大纲没问题", "按这个大纲写", "就这个大纲", "就按这个版本",
        ],
        "review_started_at": [
            "开始审查", "进入审查", "可以审查了", "开始 review",
        ],
        "review_passed_at": [
            "审查通过", "审查没问题", "报告可以交付",
        ],
        "presentation_ready_at": [
            "演示准备好了", "演示准备完成", "PPT 完成", "讲稿完成",
        ],
        "delivery_archived_at": [
            "归档结束项目", "项目交付完成", "交付归档",
        ],
    }
```

**Update `_STAGE_RANK`** (~line 184):

```python
    _STAGE_RANK = {
        "s0_interview_done_at": 0,
        "outline_confirmed_at": 1,
        "review_started_at": 2,
        "review_passed_at": 3,
        "presentation_ready_at": 4,
        "delivery_archived_at": 5,
    }
```

**Refactor `_detect_stage_keyword`** (~line 3015), remove weak branch + accept `project_id` param (Task I will consume it):

```python
    def _detect_stage_keyword(
        self,
        user_message: str,
        current_stage: str,
        project_id: str | None = None,  # For Task I S0 soft gate
    ) -> tuple[str, str] | None:
        if not user_message:
            return None
        trimmed = user_message.strip()
        if self._is_question(trimmed):
            return None

        rollback_hits = [
            key for key, phrases in self._ROLLBACK_KEYWORDS.items()
            if self._phrase_hits(trimmed, phrases)
        ]
        if rollback_hits:
            key = max(rollback_hits, key=lambda k: self._STAGE_RANK.get(k, 0))
            return ("clear", key)

        advance_hits: list[str] = []
        for key, phrases in self._STRONG_ADVANCE_KEYWORDS.items():
            if self._phrase_hits(trimmed, phrases):
                advance_hits.append(key)
        # NOTE: weak keyword branch removed per 2026-04-21 spec §3

        if advance_hits:
            key = max(advance_hits, key=lambda k: self._STAGE_RANK.get(k, 0))
            return ("set", key)
        return None
```

**Update call site in `_build_turn_context`** (~line 3052, where `_detect_stage_keyword(user_message, current_stage)` is called):

```python
            detected = self._detect_stage_keyword(user_message, current_stage, project_id)
```

- [ ] **Step 4: Run the keyword-table tests and sweep existing weak-keyword tests**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -v
```

Existing tests asserting `"OK"` / `"可以"` set `outline_confirmed_at` will fail. Convert each one to "no-op at S1 without strong keyword" (assert `is None`), or delete if the behavior intentionally gone.

- [ ] **Step 5: Commit**

```powershell
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "Remove weak advance keyword table; add s0 strong keywords"
```

---

### Task I: `_detect_stage_keyword` — S0 `has_prior_s0_assistant_turn` soft gate

**Spec:** §3 S0 软门槛

**Files:**
- Modify: `backend/chat.py`
- Modify: `tests/test_chat_runtime.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_chat_runtime.py`:

```python
class S0SoftGateTests(ChatRuntimeTests):
    def _write_conversation(self, messages):
        import json
        (self.project_dir / "conversation.json").write_text(
            json.dumps(messages, ensure_ascii=False), encoding="utf-8"
        )

    def test_has_prior_assistant_true_when_assistant_exists(self):
        handler = self._make_handler_with_project()
        self._write_conversation([
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "请回答：1) 读者是谁？"},
        ])
        self.assertTrue(handler._has_prior_s0_assistant_turn(self.project_id))

    def test_has_prior_assistant_false_when_only_user(self):
        handler = self._make_handler_with_project()
        self._write_conversation([{"role": "user", "content": "你好"}])
        self.assertFalse(handler._has_prior_s0_assistant_turn(self.project_id))

    def test_tool_role_does_not_count(self):
        handler = self._make_handler_with_project()
        self._write_conversation([
            {"role": "user", "content": "你好"},
            {"role": "tool", "content": "..."},
        ])
        self.assertFalse(handler._has_prior_s0_assistant_turn(self.project_id))

    def test_s0_strong_keyword_before_any_assistant_ignored(self):
        handler = self._make_handler_with_project()
        self._write_conversation([{"role": "user", "content": "你好"}])
        result = handler._detect_stage_keyword(
            "直接开始", "S0", self.project_id
        )
        self.assertIsNone(result)

    def test_s0_strong_keyword_after_assistant_triggers(self):
        handler = self._make_handler_with_project()
        self._write_conversation([
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "请回答：1) 读者是谁？"},
        ])
        result = handler._detect_stage_keyword(
            "不用问了", "S0", self.project_id
        )
        self.assertEqual(result, ("set", "s0_interview_done_at"))

    def test_s0_without_project_id_rejects_s0_set(self):
        # Safety: if caller forgets project_id, s0 soft gate must err on the
        # side of not triggering (better to miss a set than to bypass the gate).
        handler = self._make_handler_with_project()
        result = handler._detect_stage_keyword("直接开始", "S0", None)
        self.assertIsNone(result)
```

- [ ] **Step 2: Run to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::S0SoftGateTests -v
```

Expected: `AttributeError` on `_has_prior_s0_assistant_turn`.

- [ ] **Step 3: Implement helper + integrate into `_detect_stage_keyword`**

Add helper method to `ChatHandler` (near other `_has_*` helpers, e.g., after `_phrase_hits`):

```python
    def _has_prior_s0_assistant_turn(self, project_id: str) -> bool:
        """Return True if the project's conversation history contains at
        least one role=='assistant' message.

        Per spec §3 S0 soft gate: s0_interview_done_at strong keyword /
        stage-ack tag only fires after the assistant has already delivered
        at least one turn (typically the mandatory S0 clarification block).
        Frontend-assembled welcome messages are role=user and don't count.
        Tool role also doesn't count.
        """
        if not project_id:
            return False
        try:
            conv = self._load_conversation(project_id)
        except Exception:
            return False
        return any(m.get("role") == "assistant" for m in conv)
```

Modify `_detect_stage_keyword` (the `advance_hits` branch added in Task H):

```python
        if advance_hits:
            key = max(advance_hits, key=lambda k: self._STAGE_RANK.get(k, 0))
            # S0 soft gate: reject s0 set unless at least one assistant turn exists
            if (
                key == "s0_interview_done_at"
                and not self._has_prior_s0_assistant_turn(project_id)
            ):
                return None
            return ("set", key)
        return None
```

- [ ] **Step 4: Run to verify pass**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -v
```

- [ ] **Step 5: Commit**

```powershell
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "Add S0 has_prior_s0_assistant_turn soft gate to keyword detection"
```

---

### Task J: `_should_allow_non_plan_write` S0/S1 patch

**Spec:** §7 `_should_allow_non_plan_write` 补漏洞 — "开始写"等通用允许关键词不得在 S0/S1 无 `outline_confirmed_at` 时打开非 plan 写入

**Files:**
- Modify: `backend/chat.py` (~line 3094, `_should_allow_non_plan_write`)
- Modify: `tests/test_chat_runtime.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_chat_runtime.py`:

```python
class NonPlanWriteS0S1PatchTests(ChatRuntimeTests):
    def _set_checkpoints(self, checkpoints):
        import json
        (self.project_dir / "stage_checkpoints.json").write_text(
            json.dumps(checkpoints), encoding="utf-8"
        )

    def test_s0_stage_direct_start_keyword_blocked(self):
        handler = self._make_handler_with_project()
        # project is fresh — stage should be S0 (no s0_interview_done_at yet)
        self.assertFalse(
            handler._should_allow_non_plan_write(self.project_id, "开始写")
        )

    def test_s1_without_outline_confirmed_blocked(self):
        handler = self._make_handler_with_project()
        self._set_checkpoints({"s0_interview_done_at": "2026-04-21T10:00:00"})
        # S1 (s0 done, no outline yet) should still block
        self.assertFalse(
            handler._should_allow_non_plan_write(self.project_id, "开始写报告")
        )

    def test_s4_with_outline_confirmed_allows_direct_start(self):
        handler = self._make_handler_with_project()
        # Advance to S4 by setting the relevant checkpoints
        self._set_checkpoints({
            "s0_interview_done_at": "2026-04-21T10:00:00",
            "outline_confirmed_at": "2026-04-21T11:00:00",
        })
        # Also create the effective outline / research-plan etc. to pass
        # _infer_stage_state — or just assert the S0/S1 patch: the patch
        # checks `stage_code in {S0, S1}` — so any stage outside that
        # set passes the patch. We need to set up enough fixture to reach
        # S4 in _infer_stage_state. The simplest way is to set outline
        # confirmed AND enough downstream flags. For this unit test we
        # test the PATCH, not _infer_stage_state itself: mock it.
        from unittest import mock
        with mock.patch.object(
            handler.skill_engine, "_infer_stage_state",
            return_value={"stage_code": "S4"},
        ):
            self.assertTrue(
                handler._should_allow_non_plan_write(self.project_id, "开始写正文")
            )
```

- [ ] **Step 2: Run to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::NonPlanWriteS0S1PatchTests -v
```

Expected: first 2 return True incorrectly.

- [ ] **Step 3: Apply patch in `_should_allow_non_plan_write`** (~line 3094)

Find the `NON_PLAN_WRITE_ALLOW_KEYWORDS` branch and add an S0/S1 guard **before** it returns True:

```python
        if any(keyword in normalized for keyword in self.NON_PLAN_WRITE_ALLOW_KEYWORDS):
            # §7 patch: S0/S1 without outline_confirmed_at must not bypass via
            # generic "开始写" allow-keyword; otherwise user's innocuous
            # "开始写" would both set s0 and open non-plan writes, skipping
            # outline confirmation entirely.
            if project_path:
                stage_state = self.skill_engine._infer_stage_state(project_path)
                stage_code = stage_state.get("stage_code")
                if stage_code in {"S0", "S1"}:
                    checkpoints = self.skill_engine._load_stage_checkpoints(project_path)
                    if "outline_confirmed_at" not in checkpoints:
                        return False
            return True
```

(The `project_path` variable should already be bound in the enclosing scope; reuse it.)

- [ ] **Step 4: Run to verify pass**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -v
```

- [ ] **Step 5: Commit**

```powershell
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "Block non-plan-write direct-start keywords in S0/S1 pre-outline"
```

---

### Task K: S0 `write_file` gate inside `_execute_tool`

**Spec:** §1 禁止 4 文件 (outline/research-plan/data-log/analysis-notes), §7 S0 write_file 门禁 + system_notice

**Files:**
- Modify: `backend/chat.py` (~line 2137, `_execute_tool` write_file branch)
- Modify: `tests/test_chat_runtime.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_chat_runtime.py`:

```python
class S0WriteFileGateTests(ChatRuntimeTests):
    S0_BLOCKED = [
        "plan/outline.md",
        "plan/research-plan.md",
        "plan/data-log.md",
        "plan/analysis-notes.md",
    ]
    S0_ALLOWED = [
        "plan/notes.md",
        "plan/references.md",
        "plan/project-overview.md",
    ]

    def _make_tool_call(self, file_path, content):
        import json
        from types import SimpleNamespace
        return SimpleNamespace(
            id="call-test",
            function=SimpleNamespace(
                name="write_file",
                arguments=json.dumps({"file_path": file_path, "content": content}),
            ),
        )

    def test_s0_blocks_each_of_four_files(self):
        handler = self._make_handler_with_project()
        for path in self.S0_BLOCKED:
            tool_call = self._make_tool_call(path, "# content\n" * 5)
            result = handler._execute_tool(self.project_id, tool_call)
            self.assertEqual(result["status"], "error", f"{path} should be blocked")
            self.assertIn("S0 阶段", result["message"])

    def test_s0_allows_non_blocked_plan_files(self):
        handler = self._make_handler_with_project()
        for path in self.S0_ALLOWED:
            tool_call = self._make_tool_call(path, "# content\n" * 5)
            result = handler._execute_tool(self.project_id, tool_call)
            self.assertEqual(
                result["status"], "success", f"{path} should be allowed"
            )

    def test_s0_write_emits_system_notice(self):
        handler = self._make_handler_with_project()
        tool_call = self._make_tool_call("plan/outline.md", "# x\n")
        handler._execute_tool(self.project_id, tool_call)
        notices = handler._turn_context.get("pending_system_notices", [])
        self.assertTrue(any(
            "S0 阶段" in n.get("reason", "") for n in notices
        ))

    def test_s0_write_notice_mentions_analysis_notes(self):
        handler = self._make_handler_with_project()
        tool_call = self._make_tool_call("plan/analysis-notes.md", "# x\n")
        handler._execute_tool(self.project_id, tool_call)
        notices = handler._turn_context.get("pending_system_notices", [])
        # Reason must list all four file categories per §1 spec
        reason_text = " ".join(n.get("reason", "") for n in notices)
        self.assertIn("分析笔记", reason_text)

    def test_post_s0_outline_write_not_blocked(self):
        import json
        handler = self._make_handler_with_project()
        (self.project_dir / "stage_checkpoints.json").write_text(
            json.dumps({"s0_interview_done_at": "2026-04-21T10:00:00"}),
            encoding="utf-8",
        )
        tool_call = self._make_tool_call("plan/outline.md", "# 大纲\n## 章节\n" * 3)
        result = handler._execute_tool(self.project_id, tool_call)
        # S1 stage — outline.md is the expected write, should succeed
        self.assertEqual(result["status"], "success")
```

- [ ] **Step 2: Run to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::S0WriteFileGateTests -v
```

Expected: blocks fail (not yet implemented).

- [ ] **Step 3: Insert S0 gate in `_execute_tool` write_file branch** (`backend/chat.py` ~line 2137)

Add class-level constant near other constants (e.g., after `NON_PLAN_WRITE_FOLLOW_UP_KEYWORDS`):

```python
    _S0_BLOCKED_PLAN_FILES = frozenset({
        "plan/outline.md",
        "plan/research-plan.md",
        "plan/data-log.md",
        "plan/analysis-notes.md",
    })
```

Inside `_execute_tool`, add the gate at the top of the `if func_name == "write_file":` branch, **before** `_should_block_non_plan_write`:

```python
            if func_name == "write_file":
                normalized_early = self.skill_engine._to_posix(
                    args["file_path"]
                ).lstrip("/")
                project_path = self.skill_engine.get_project_path(project_id)
                if (
                    project_path
                    and normalized_early in self._S0_BLOCKED_PLAN_FILES
                ):
                    stage_state = self.skill_engine._infer_stage_state(project_path)
                    if stage_state.get("stage_code") == "S0":
                        reason = (
                            "S0 阶段：请先对 seed 做一轮澄清，"
                            "再写大纲/研究计划/资料清单/分析笔记"
                        )
                        self._emit_system_notice_once(
                            category="s0_write_blocked",
                            path=normalized_early,
                            reason=reason,
                            user_action=(
                                "请先按 SKILL.md §S0 发一轮 3-5 个打包追问，"
                                "用户回答或跳过后再写正式产出文件。"
                            ),
                        )
                        return {"status": "error", "message": reason}
                # ... existing _should_block_non_plan_write check + rest of branch
```

(The existing branch continues unchanged below.)

- [ ] **Step 4: Run to verify pass**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -v
```

- [ ] **Step 5: Commit**

```powershell
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "Gate S0 write_file for four downstream plan files"
```

---

### Task L: `_chat_stream_unlocked` tail guard (prefix-match streaming strip)

**Spec:** §2 剥离 tail guard (non-fixed window), Risks 流式 tag 泄漏

**Files:**
- Modify: `backend/chat.py` (`_chat_stream_unlocked` ~line 1274; consider adding a pure helper for testability)
- Modify: `tests/test_chat_runtime.py`

- [ ] **Step 1: Write failing tests (pure helper approach for testability)**

Append to `tests/test_chat_runtime.py`:

```python
class StreamTailGuardHelperTests(unittest.TestCase):
    """Unit tests for the pure stream_split_safe_tail helper.

    Semantics:
      stream_split_safe_tail(buffer) -> (safe_to_emit, held_tail)
      - If buffer does NOT yet contain the substring "<stage-ack", returns
        (buffer_without_possible_prefix_suffix, possible_prefix_suffix).
        "possible prefix suffix" = longest suffix of buffer that is a prefix of
        "<stage-ack" (i.e., the streaming split could be inside an incomplete
        opening tag).
      - If buffer contains "<stage-ack" at position p, returns
        (buffer[:p], buffer[p:]).
      - The held_tail is emitted by the caller only at stream close, after
        StageAckParser.strip() has scrubbed it.
    """

    def test_no_tag_no_dangling_prefix(self):
        from backend.chat import stream_split_safe_tail
        safe, held = stream_split_safe_tail("纯正文没 tag 可能。")
        self.assertEqual(safe, "纯正文没 tag 可能。")
        self.assertEqual(held, "")

    def test_chunk_cut_at_lt(self):
        from backend.chat import stream_split_safe_tail
        safe, held = stream_split_safe_tail("正文 <")
        self.assertEqual(safe, "正文 ")
        self.assertEqual(held, "<")

    def test_chunk_cut_at_lt_s(self):
        from backend.chat import stream_split_safe_tail
        safe, held = stream_split_safe_tail("正文 <s")
        self.assertEqual(held, "<s")

    def test_chunk_cut_at_partial_stage(self):
        from backend.chat import stream_split_safe_tail
        safe, held = stream_split_safe_tail("正文 <stage-a")
        self.assertEqual(held, "<stage-a")

    def test_full_open_tag_held(self):
        from backend.chat import stream_split_safe_tail
        safe, held = stream_split_safe_tail(
            "正文 <stage-ack>outline_confirmed_at"
        )
        self.assertEqual(safe, "正文 ")
        self.assertTrue(held.startswith("<stage-ack>"))

    def test_complete_tag_held(self):
        from backend.chat import stream_split_safe_tail
        safe, held = stream_split_safe_tail(
            "正文 <stage-ack>outline_confirmed_at</stage-ack>"
        )
        self.assertEqual(safe, "正文 ")
        # Full tag is held — caller strips it at stream close
        self.assertIn("<stage-ack>", held)

    def test_lt_without_stage_ack_not_held(self):
        from backend.chat import stream_split_safe_tail
        # "<" at end with no "<stage-ack" prefix possibility AFTER enough chars
        safe, held = stream_split_safe_tail("正文 <div>")
        self.assertEqual(safe, "正文 <div>")
        self.assertEqual(held, "")

    def test_multi_tag_tail_held(self):
        from backend.chat import stream_split_safe_tail
        tail = (
            "<stage-ack>outline_confirmed_at</stage-ack>\n"
            '<stage-ack action="clear">outline_confirmed_at</stage-ack>\n'
            "<stage-ack>outline_confirmed_at</stage-ack>\n"
        )
        buffer = "正文段。\n" + tail
        safe, held = stream_split_safe_tail(buffer)
        self.assertEqual(safe, "正文段。\n")
        self.assertEqual(held, tail)
        self.assertGreater(len(tail.encode("utf-8")), 128)
```

- [ ] **Step 2: Run to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::StreamTailGuardHelperTests -v
```

Expected: `ImportError: cannot import name 'stream_split_safe_tail'`.

- [ ] **Step 3: Implement pure helper at module level of `backend/chat.py`**

Add near the top of `backend/chat.py` (module-level, not inside class):

```python
_STAGE_ACK_MARKER = "<stage-ack"


def stream_split_safe_tail(buffer: str) -> tuple[str, str]:
    """Split buffer into (safe_to_emit_now, held_until_stream_close).

    Called by _chat_stream_unlocked after every new content delta is
    accumulated. Held portion must NOT be sent to the frontend until stream
    close and StageAckParser.strip() has scrubbed it.

    Rules:
      1. If "<stage-ack" occurs at position p, hold from p to end.
      2. Otherwise, if buffer's suffix is a prefix of "<stage-ack"
         (e.g., "<" / "<s" / "<stage-a"), hold that suffix.
      3. Otherwise, emit the whole buffer.

    Note: rule 1 uses `find`, not `rfind` — the earliest "<stage-ack"
    anchors the hold. Using rfind would match the '<' in a closing
    </stage-ack> and leak the opening "<stage-ack".
    """
    if not buffer:
        return "", ""

    # Rule 1: full marker seen anywhere — hold from first occurrence
    idx = buffer.lower().find(_STAGE_ACK_MARKER)
    if idx != -1:
        return buffer[:idx], buffer[idx:]

    # Rule 2: buffer's tail might be an incomplete marker prefix
    marker_len = len(_STAGE_ACK_MARKER)
    max_overlap = min(marker_len - 1, len(buffer))
    for overlap in range(max_overlap, 0, -1):
        suffix = buffer[-overlap:].lower()
        if _STAGE_ACK_MARKER.startswith(suffix):
            return buffer[:-overlap], buffer[-overlap:]

    # Rule 3: safe to emit all
    return buffer, ""
```

Now wire into `_chat_stream_unlocked` (~line 1274). Locate where content deltas are yielded; replace the direct yield with an accumulator + safe-tail split:

```python
        accumulated = ""
        stream_buffer = ""
        for chunk in openai_stream_iter:
            delta_text = chunk.choices[0].delta.content or ""
            accumulated += delta_text
            stream_buffer += delta_text
            safe, held = stream_split_safe_tail(stream_buffer)
            if safe:
                yield {"type": "content", "data": safe}
            stream_buffer = held
        # Stream close: finalization happens in Task M.
        # For THIS task, just ensure the held tail is stripped before any
        # downstream consumer receives it. Task M will:
        #   1. Call StageAckParser on `accumulated`
        #   2. Record checkpoints / emit system_notice
        #   3. yield stripped tail as final "content" event
```

(Task M will complete the final emission.)

- [ ] **Step 4: Run helper tests**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::StreamTailGuardHelperTests -v
```

Expected: 8 pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "Add stream tail guard helper for stage-ack tag leak prevention"
```

---

### Task M: `_finalize_assistant_turn` — both chat paths, tag priority, system_notice yield

**Spec:** §2 剥离 + parse + record, §7 `_chat_unlocked` 和 `_chat_stream_unlocked` 都接入, Tag priority > keyword, Risks 多项

**Files:**
- Modify: `backend/chat.py` (both chat paths + new `_finalize_assistant_turn`)
- Modify: `tests/test_chat_runtime.py`

- [ ] **Step 1: Write failing tests (per spec §8, covering 12+ cases)**

Append to `tests/test_chat_runtime.py`:

```python
class StageAckFinalizePipelineTests(ChatRuntimeTests):
    def _write_effective_outline(self):
        (self.project_dir / "plan" / "outline.md").write_text(
            "# 大纲\n## 章节 1\n- 要点 A\n## 章节 2\n- 要点 B\n",
            encoding="utf-8",
        )

    def _set_checkpoints(self, data):
        import json
        (self.project_dir / "stage_checkpoints.json").write_text(
            json.dumps(data), encoding="utf-8"
        )

    def _write_conversation(self, messages):
        import json
        (self.project_dir / "conversation.json").write_text(
            json.dumps(messages, ensure_ascii=False), encoding="utf-8"
        )

    def test_valid_set_tag_sets_checkpoint_strips_content(self):
        handler = self._make_handler_with_project()
        self._set_checkpoints({"s0_interview_done_at": "2026-04-21T10:00:00"})
        self._write_effective_outline()
        stripped = handler._finalize_assistant_turn(
            self.project_id,
            "大纲完成。\n\n<stage-ack>outline_confirmed_at</stage-ack>\n",
        )
        self.assertNotIn("<stage-ack", stripped)
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertIn("outline_confirmed_at", checkpoints)

    def test_tag_in_code_fence_not_executed_still_stripped(self):
        handler = self._make_handler_with_project()
        self._set_checkpoints({"s0_interview_done_at": "2026-04-21T10:00:00"})
        self._write_effective_outline()
        stripped = handler._finalize_assistant_turn(
            self.project_id,
            "示例：\n```md\n<stage-ack>outline_confirmed_at</stage-ack>\n```\n结尾。\n",
        )
        self.assertNotIn("<stage-ack", stripped)
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertNotIn("outline_confirmed_at", checkpoints)

    def test_multi_tag_executed_in_order(self):
        handler = self._make_handler_with_project()
        self._set_checkpoints({
            "s0_interview_done_at": "2026-04-21T10:00:00",
            "outline_confirmed_at": "2026-04-21T11:00:00",
        })
        self._write_effective_outline()
        handler._finalize_assistant_turn(
            self.project_id,
            "回退再推进。\n"
            '<stage-ack action="clear">outline_confirmed_at</stage-ack>\n'
            "<stage-ack>outline_confirmed_at</stage-ack>\n",
        )
        # Final state: outline_confirmed_at is set (the last action wins
        # by sequential execution)
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertIn("outline_confirmed_at", checkpoints)

    def test_set_missing_prereq_emits_notice_no_checkpoint(self):
        handler = self._make_handler_with_project()
        self._set_checkpoints({"s0_interview_done_at": "2026-04-21T10:00:00"})
        # outline.md NOT written — prereq will fail
        handler._finalize_assistant_turn(
            self.project_id,
            "大纲没写但强推。\n<stage-ack>outline_confirmed_at</stage-ack>\n",
        )
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertNotIn("outline_confirmed_at", checkpoints)
        notices = handler._turn_context.get("pending_system_notices", [])
        self.assertTrue(any("outline.md" in str(n) for n in notices))

    def test_s0_tag_first_turn_without_prior_assistant_rejected(self):
        handler = self._make_handler_with_project()
        self._write_conversation([{"role": "user", "content": "你好"}])
        # No assistant history
        handler._finalize_assistant_turn(
            self.project_id,
            "先简化流程。\n<stage-ack>s0_interview_done_at</stage-ack>\n",
        )
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertNotIn("s0_interview_done_at", checkpoints)
        notices = handler._turn_context.get("pending_system_notices", [])
        self.assertTrue(any("S0" in n.get("reason", "") for n in notices))

    def test_s0_tag_after_prior_assistant_succeeds(self):
        handler = self._make_handler_with_project()
        self._write_conversation([
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "请回答：1) 读者是谁？"},
        ])
        stripped = handler._finalize_assistant_turn(
            self.project_id,
            "记录了。\n<stage-ack>s0_interview_done_at</stage-ack>\n",
        )
        self.assertNotIn("<stage-ack", stripped)
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertIn("s0_interview_done_at", checkpoints)

    def test_unknown_key_tag_stripped_no_checkpoint_no_notice(self):
        handler = self._make_handler_with_project()
        handler._finalize_assistant_turn(
            self.project_id,
            "写错 key。\n<stage-ack>bogus_key</stage-ack>\n",
        )
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertNotIn("bogus_key", checkpoints)
        # Per spec §2: unknown key logs warning but does NOT emit system_notice
        notices = handler._turn_context.get("pending_system_notices", [])
        self.assertFalse(any(
            "bogus_key" in n.get("reason", "") or
            "bogus_key" in n.get("path", "") for n in notices
        ))

    def test_clear_idempotent_through_tag(self):
        handler = self._make_handler_with_project()
        # Clear when not set — should be idempotent
        handler._finalize_assistant_turn(
            self.project_id,
            '回退。\n<stage-ack action="clear">outline_confirmed_at</stage-ack>\n',
        )
        # No assertion failure; no notice raised
        notices = handler._turn_context.get("pending_system_notices", [])
        self.assertFalse(any("outline" in n.get("reason", "") for n in notices))

    def test_executable_tag_wins_over_pending_keyword(self):
        """User said '确认大纲' (keyword → stored as pending_stage_keyword in
        _build_turn_context, NOT executed yet). Assistant then emits an
        executable tag pointing at a DIFFERENT checkpoint. The tag must win;
        pending keyword is discarded without setting outline_confirmed_at."""
        handler = self._make_handler_with_project()
        self._set_checkpoints({
            "s0_interview_done_at": "2026-04-21T10:00:00",
            "outline_confirmed_at": "2026-04-21T11:00:00",
        })
        # Build effective report draft so review_started_at prereq passes
        (self.project_dir / "content").mkdir(exist_ok=True)
        (self.project_dir / "content" / "report.md").write_text(
            "# Report\n\n" + ("数据资产核算。" * 400),
            encoding="utf-8",
        )
        # Simulate keyword pending (what _build_turn_context would store)
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        handler._turn_context["pending_stage_keyword"] = ("set", "outline_confirmed_at")
        # Clear outline_confirmed_at first so we can see whether pending keyword
        # would have set it (it shouldn't — tag wins)
        handler.skill_engine._clear_stage_checkpoint(
            self.project_dir, "outline_confirmed_at"
        )
        # Assistant tag points at review_started_at
        handler._finalize_assistant_turn(
            self.project_id,
            "进入审查。\n<stage-ack>review_started_at</stage-ack>\n",
        )
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        # Tag's target set
        self.assertIn("review_started_at", checkpoints)
        # Pending keyword target NOT set (tag won; keyword discarded)
        self.assertNotIn("outline_confirmed_at", checkpoints)
        # pending_stage_keyword cleared
        self.assertIsNone(handler._turn_context.get("pending_stage_keyword"))

    def test_pending_keyword_fallback_fires_when_no_executable_tag(self):
        """Assistant has only a non-executable tag (e.g., inside code fence);
        pending keyword falls back to record_stage_checkpoint."""
        handler = self._make_handler_with_project()
        self._set_checkpoints({"s0_interview_done_at": "2026-04-21T10:00:00"})
        self._write_effective_outline()
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        handler._turn_context["pending_stage_keyword"] = ("set", "outline_confirmed_at")
        # Non-executable tag (inside code fence) must NOT block fallback
        handler._finalize_assistant_turn(
            self.project_id,
            "示例：\n```md\n<stage-ack>review_started_at</stage-ack>\n```\n完。\n",
        )
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        # Keyword fallback set outline_confirmed_at
        self.assertIn("outline_confirmed_at", checkpoints)
        # Non-executable tag target NOT set
        self.assertNotIn("review_started_at", checkpoints)

    def test_pending_keyword_fallback_emits_prereq_notice_on_failure(self):
        """Pending keyword set fails prereq → emit notice, no checkpoint."""
        handler = self._make_handler_with_project()
        self._set_checkpoints({"s0_interview_done_at": "2026-04-21T10:00:00"})
        # NO effective outline — prereq will fail
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        handler._turn_context["pending_stage_keyword"] = ("set", "outline_confirmed_at")
        handler._finalize_assistant_turn(
            self.project_id,
            "没 tag 的正文。\n",
        )
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertNotIn("outline_confirmed_at", checkpoints)
        notices = handler._turn_context.get("pending_system_notices", [])
        self.assertTrue(any("outline.md" in str(n) for n in notices))

    def test_user_message_tag_not_parsed_by_finalize(self):
        # Finalize operates on assistant content only; user tag is never
        # fed to it.
        handler = self._make_handler_with_project()
        # No exception, no checkpoint change
        stripped = handler._finalize_assistant_turn(
            self.project_id,
            "用户问到了 <stage-ack>outline_confirmed_at</stage-ack>"
            " 这种语法。\n",  # non-tail tag
        )
        self.assertNotIn("<stage-ack", stripped)
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertNotIn("outline_confirmed_at", checkpoints)

    def test_compaction_receives_stripped_content(self):
        handler = self._make_handler_with_project()
        self._set_checkpoints({"s0_interview_done_at": "2026-04-21T10:00:00"})
        self._write_effective_outline()
        final = handler._finalize_assistant_turn(
            self.project_id,
            "完成。\n<stage-ack>outline_confirmed_at</stage-ack>\n",
        )
        # Whatever the caller persists must have no tag
        self.assertNotIn("<stage-ack", final)


class ChatPathIntegrationTests(ChatRuntimeTests):
    """End-to-end integration with mocked provider, verifying:
      - finalize runs on both chat() and chat_stream() paths
      - conversation.json persisted without tag (and post-turn compaction input too)
      - stream SSE order: content → system_notice → usage
      - unknown key logs WARNING via logger `backend.chat`, no system_notice
      - user-role tag survives literal into conversation.json
      - set+clear final clear; clear+set final set
      - keyword fallback works when assistant has no executable tag
    """
    def _set_checkpoints(self, data):
        import json
        (self.project_dir / "stage_checkpoints.json").write_text(
            json.dumps(data), encoding="utf-8"
        )

    def _write_effective_outline(self):
        (self.project_dir / "plan" / "outline.md").write_text(
            "# 大纲\n## 章节 1\n- 要点 A\n## 章节 2\n- 要点 B\n",
            encoding="utf-8",
        )

    def _mock_non_stream_completion(self, full_text):
        from types import SimpleNamespace
        return SimpleNamespace(
            id="mock-id",
            choices=[SimpleNamespace(
                message=SimpleNamespace(
                    role="assistant",
                    content=full_text,
                    tool_calls=None,
                ),
                finish_reason="stop",
            )],
            usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=10, total_tokens=20,
            ),
        )

    def _mock_stream_chunks(self, full_text, chunk_size=5):
        from types import SimpleNamespace
        def _iter():
            for i in range(0, len(full_text), chunk_size):
                piece = full_text[i:i+chunk_size]
                yield SimpleNamespace(
                    id="mock-id",
                    choices=[SimpleNamespace(
                        delta=SimpleNamespace(content=piece, role=None, tool_calls=None),
                        finish_reason=None,
                    )],
                    usage=None,
                )
            yield SimpleNamespace(
                id="mock-id",
                choices=[SimpleNamespace(
                    delta=SimpleNamespace(content=None, role=None, tool_calls=None),
                    finish_reason="stop",
                )],
                usage=SimpleNamespace(
                    prompt_tokens=10, completion_tokens=10, total_tokens=20,
                ),
            )
        return _iter()

    def test_non_stream_chat_strips_tag_and_persists_cleanly(self):
        """Real handler.chat() path: returned message has no tag AND
        conversation.json saves stripped content."""
        from unittest import mock
        handler = self._make_handler_with_project()
        self._set_checkpoints({"s0_interview_done_at": "2026-04-21T10:00:00"})
        self._write_effective_outline()
        assistant_text = "大纲已批准。\n\n<stage-ack>outline_confirmed_at</stage-ack>\n"
        with mock.patch.object(
            handler.client.chat.completions, "create",
            return_value=self._mock_non_stream_completion(assistant_text),
        ):
            response = handler.chat(project_id=self.project_id, user_message="你看行吗")
        # Response has no tag
        response_text = response.get("message") or response.get("content") or ""
        self.assertNotIn("<stage-ack", response_text)
        # conversation.json has no tag
        import json
        conv = json.loads(
            (self.project_dir / "conversation.json").read_text(encoding="utf-8")
        )
        assistant_msgs = [m for m in conv if m["role"] == "assistant"]
        self.assertTrue(assistant_msgs)
        self.assertNotIn("<stage-ack", assistant_msgs[-1]["content"])
        # Checkpoint set via tag
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertIn("outline_confirmed_at", checkpoints)

    def test_stream_chat_never_leaks_tag_to_frontend(self):
        """Real handler.chat_stream(): even with chunk_size=5 splitting
        mid-tag, no SSE content event contains '<stage-ack'."""
        from unittest import mock
        handler = self._make_handler_with_project()
        self._set_checkpoints({"s0_interview_done_at": "2026-04-21T10:00:00"})
        self._write_effective_outline()
        assistant_text = "大纲已批准。\n\n<stage-ack>outline_confirmed_at</stage-ack>\n"
        with mock.patch.object(
            handler.client.chat.completions, "create",
            return_value=self._mock_stream_chunks(assistant_text, chunk_size=5),
        ):
            events = list(handler.chat_stream(
                project_id=self.project_id, user_message="",
            ))
        content_events = [e for e in events if e.get("type") == "content"]
        combined = "".join(e["data"] for e in content_events)
        self.assertNotIn("<stage-ack", combined)
        self.assertIn("大纲已批准", combined)
        # conversation_state.json / conversation.json tag-free too
        import json
        conv = json.loads(
            (self.project_dir / "conversation.json").read_text(encoding="utf-8")
        )
        for msg in conv:
            self.assertNotIn("<stage-ack", msg.get("content", "") or "")

    def test_stream_system_notice_before_usage(self):
        """SSE yield order: system_notice emitted by finalize must precede
        the usage event, otherwise frontend notice rendering breaks."""
        from unittest import mock
        handler = self._make_handler_with_project()
        self._set_checkpoints({"s0_interview_done_at": "2026-04-21T10:00:00"})
        # NO outline → prereq fail → finalize emits notice
        assistant_text = "强推大纲。\n<stage-ack>outline_confirmed_at</stage-ack>\n"
        with mock.patch.object(
            handler.client.chat.completions, "create",
            return_value=self._mock_stream_chunks(assistant_text, chunk_size=5),
        ):
            events = list(handler.chat_stream(
                project_id=self.project_id, user_message="",
            ))
        notice_indices = [i for i, e in enumerate(events) if e.get("type") == "system_notice"]
        usage_indices = [i for i, e in enumerate(events) if e.get("type") == "usage"]
        self.assertTrue(notice_indices, "finalize must yield system_notice")
        self.assertTrue(usage_indices, "stream must yield usage")
        self.assertLess(
            max(notice_indices), min(usage_indices),
            "system_notice must precede usage in SSE stream",
        )

    def test_unknown_key_logs_warning_no_notice(self):
        """Unknown key: log WARNING via backend.chat logger, no system_notice."""
        from unittest import mock
        import logging
        handler = self._make_handler_with_project()
        assistant_text = "错 key。\n<stage-ack>bogus_key</stage-ack>\n"
        with mock.patch.object(
            handler.client.chat.completions, "create",
            return_value=self._mock_non_stream_completion(assistant_text),
        ):
            with self.assertLogs("backend.chat", level="WARNING") as cm:
                response = handler.chat(project_id=self.project_id, user_message="")
        self.assertTrue(
            any("bogus_key" in record for record in cm.output),
            f"Expected warning mentioning bogus_key, got {cm.output!r}",
        )
        notices = response.get("system_notices") or []
        for n in notices:
            self.assertNotIn("bogus_key", str(n))

    def test_user_message_tag_preserved_as_literal(self):
        """User writes <stage-ack> as part of a question. Must survive into
        conversation.json unchanged, never parsed."""
        from unittest import mock
        handler = self._make_handler_with_project()
        self._set_checkpoints({"s0_interview_done_at": "2026-04-21T10:00:00"})
        user_text = "请问 <stage-ack>outline_confirmed_at</stage-ack> 是什么意思？"
        with mock.patch.object(
            handler.client.chat.completions, "create",
            return_value=self._mock_non_stream_completion("这是 stage-ack tag 语法。"),
        ):
            handler.chat(project_id=self.project_id, user_message=user_text)
        import json
        conv = json.loads(
            (self.project_dir / "conversation.json").read_text(encoding="utf-8")
        )
        user_msgs = [m for m in conv if m["role"] == "user"]
        self.assertTrue(
            any("<stage-ack>" in m["content"] for m in user_msgs),
            "user's literal tag must be preserved",
        )
        # Checkpoint NOT set (tag was user-role, not parsed)
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertNotIn("outline_confirmed_at", checkpoints)

    def test_set_then_clear_same_key_final_clear(self):
        """Assistant emits `set outline; clear outline` in that order.
        Final state: outline_confirmed_at NOT set."""
        from unittest import mock
        handler = self._make_handler_with_project()
        self._set_checkpoints({
            "s0_interview_done_at": "2026-04-21T10:00:00",
            "outline_confirmed_at": "2026-04-21T11:00:00",
        })
        self._write_effective_outline()
        assistant_text = (
            "设后清。\n"
            "<stage-ack>outline_confirmed_at</stage-ack>\n"
            '<stage-ack action="clear">outline_confirmed_at</stage-ack>\n'
        )
        with mock.patch.object(
            handler.client.chat.completions, "create",
            return_value=self._mock_non_stream_completion(assistant_text),
        ):
            handler.chat(project_id=self.project_id, user_message="")
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertNotIn("outline_confirmed_at", checkpoints)

    def test_clear_then_set_same_key_final_set(self):
        from unittest import mock
        handler = self._make_handler_with_project()
        self._set_checkpoints({
            "s0_interview_done_at": "2026-04-21T10:00:00",
            "outline_confirmed_at": "2026-04-21T11:00:00",
        })
        self._write_effective_outline()
        assistant_text = (
            "清后设。\n"
            '<stage-ack action="clear">outline_confirmed_at</stage-ack>\n'
            "<stage-ack>outline_confirmed_at</stage-ack>\n"
        )
        with mock.patch.object(
            handler.client.chat.completions, "create",
            return_value=self._mock_non_stream_completion(assistant_text),
        ):
            handler.chat(project_id=self.project_id, user_message="")
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertIn("outline_confirmed_at", checkpoints)

    def test_keyword_fallback_when_no_tag(self):
        """User says strong keyword; assistant emits no tag.
        Keyword fallback in _finalize_assistant_turn sets the checkpoint."""
        from unittest import mock
        handler = self._make_handler_with_project()
        self._set_checkpoints({"s0_interview_done_at": "2026-04-21T10:00:00"})
        self._write_effective_outline()
        with mock.patch.object(
            handler.client.chat.completions, "create",
            return_value=self._mock_non_stream_completion("好的，按大纲写。"),
        ):
            handler.chat(project_id=self.project_id, user_message="确认大纲")
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertIn("outline_confirmed_at", checkpoints)
```

- [ ] **Step 2: Run to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::StageAckFinalizePipelineTests -v
```

Expected: `AttributeError: no _finalize_assistant_turn`.

- [ ] **Step 3: Implement tag-priority architecture in `backend/chat.py`**

**Three coordinated changes required** (tag wins over pending keyword; keyword fallback only when no executable tag):

**3a.** Modify `_new_turn_context` to include `pending_stage_keyword` field:

```python
    def _new_turn_context(self, can_write_non_plan):
        return {
            "can_write_non_plan": can_write_non_plan,
            # ... all existing fields ...
            "checkpoint_event": None,
            "pending_stage_keyword": None,  # NEW — (action, key) queued by
                                            # _build_turn_context, consumed
                                            # by _finalize_assistant_turn if
                                            # assistant has no executable tag
        }
```

**3b.** Modify `_build_turn_context` (~line 3046) — **defer `set` keyword to finalize**; clear still runs immediately (rollback shouldn't be contested by tags):

```python
    def _build_turn_context(self, project_id: str, user_message: str):
        self._turn_context = self._new_turn_context(can_write_non_plan=False)
        project_path = self.skill_engine.get_project_path(project_id)
        if project_path:
            summary = self.skill_engine.get_workspace_summary(project_id)
            current_stage = summary.get("stage_code", "S0")
            detected = self._detect_stage_keyword(user_message, current_stage, project_id)
            if detected:
                action, key = detected
                if action == "clear":
                    # Rollback keyword executes immediately — tags don't
                    # compete with rollback intent.
                    try:
                        self.skill_engine.record_stage_checkpoint(project_id, key, action)
                    except ValueError as exc:
                        notice = self.skill_engine.get_stage_checkpoint_prereq_notice(key)
                        if notice:
                            self._emit_system_notice_once(
                                category="checkpoint_prereq_missing",
                                path=notice["path"],
                                reason=notice["reason"],
                                user_action=notice["user_action"],
                            )
                        else:
                            raise exc
                    else:
                        self._turn_context["checkpoint_event"] = {"action": action, "key": key}
                else:
                    # action == "set" — DEFER to _finalize_assistant_turn so
                    # executable tag (if any) can win. Spec §3 "tag 赢；无 tag
                    # 才 keyword 兜底".
                    self._turn_context["pending_stage_keyword"] = (action, key)
        self._turn_context["can_write_non_plan"] = self._should_allow_non_plan_write(
            project_id, user_message
        )
        return self._turn_context
```

**3c.** Add `_finalize_assistant_turn` + `_apply_stage_ack_event` helper to `ChatHandler`:

```python
    def _finalize_assistant_turn(self, project_id: str, full_content: str) -> str:
        """Resolve stage-ack tags + pending strong-keyword fallback for the
        completed assistant turn. Returns tag-stripped content for persistence.

        Priority (spec §3):
          1. Parse tags via StageAckParser.
          2. If ≥1 executable tag exists:
                execute tags in order; DISCARD pending_stage_keyword (tag wins)
          3. Else (no executable tag — none present, or all non-executable):
                execute pending_stage_keyword (strong-keyword fallback)
                via record_stage_checkpoint + prereq notice on failure.
          4. Unknown-key events: log WARNING, no system_notice.

        Always returns `parser.strip(full_content)`, regardless of path taken.
        Must be called under `_get_project_request_lock` to serialize with
        other checkpoint writers.
        """
        from backend.stage_ack import StageAckParser
        import logging

        parser = StageAckParser()
        events = parser.parse(full_content)
        executable_events = [e for e in events if e.executable]
        pending = self._turn_context.get("pending_stage_keyword")

        lock = _get_project_request_lock(project_id)
        with lock:
            # Unknown-key warnings (always log, regardless of other paths)
            for event in events:
                if event.ignored_reason == "unknown_key":
                    logging.getLogger("backend.chat").warning(
                        "Ignoring stage-ack with unknown key: %r", event.key,
                    )

            if executable_events:
                # Tag path wins — discard pending keyword
                self._turn_context["pending_stage_keyword"] = None
                for event in events:
                    if event.executable:
                        self._apply_stage_ack_event(project_id, event)
            elif pending:
                # No executable tag — fall back to pending strong keyword
                action, key = pending
                self._turn_context["pending_stage_keyword"] = None
                try:
                    self.skill_engine.record_stage_checkpoint(project_id, key, action)
                except ValueError:
                    notice = self.skill_engine.get_stage_checkpoint_prereq_notice(key)
                    if notice:
                        self._emit_system_notice_once(
                            category="stage_keyword_prereq_missing",
                            path=notice["path"],
                            reason=notice["reason"],
                            user_action=notice["user_action"],
                        )

        return parser.strip(full_content)

    def _apply_stage_ack_event(self, project_id: str, event) -> None:
        """Execute one executable stage-ack event (called under project lock)."""
        # S0 soft gate (tag path)
        if (
            event.key == "s0_interview_done_at"
            and event.action == "set"
            and not self._has_prior_s0_assistant_turn(project_id)
        ):
            self._emit_system_notice_once(
                category="s0_tag_soft_gate",
                path=None,
                reason=(
                    "S0 阶段第一轮必须先对 seed 做一轮打包追问，"
                    "再推进；本轮 tag 不执行。"
                ),
                user_action=(
                    "请模型按 SKILL.md §S0 先发 3-5 个澄清问题，"
                    "下一轮再发 tag。"
                ),
            )
            return
        try:
            self.skill_engine.record_stage_checkpoint(
                project_id, event.key, event.action
            )
        except ValueError:
            notice = self.skill_engine.get_stage_checkpoint_prereq_notice(event.key)
            if notice:
                self._emit_system_notice_once(
                    category="stage_ack_prereq_missing",
                    path=notice["path"],
                    reason=notice["reason"],
                    user_action=notice["user_action"],
                )
```

**3d.** Wire into `_chat_unlocked` (non-stream, ~line 1476) — grep for the spot where `assistant` response content is finalized and persisted. Replace:

```python
        # OLD: persist raw assistant content directly
        # NEW: call finalize FIRST, then persist the stripped content and
        # attach pending system_notices to the response body.
        stripped = self._finalize_assistant_turn(project_id, assistant_full_text)
        # Use `stripped` everywhere `assistant_full_text` was previously used
        # for persistence, compaction, and the response body:
        #   - conversation.json appends the stripped assistant message
        #   - conversation_state.json post-turn compaction receives stripped
        #   - response["message"] = stripped
        #   - response["system_notices"] = list(
        #         self._turn_context.get("pending_system_notices") or []
        #     )
        # After emitting, clear pending_system_notices so they don't leak
        # into the next turn.
        response = {
            # ... existing fields ...
            "message": stripped,
            "system_notices": list(
                self._turn_context.get("pending_system_notices") or []
            ),
        }
        self._turn_context["pending_system_notices"] = []
        return response
```

> **Implementer note:** find the actual assistant-content variable name (e.g., `assistant_full_text`, `message_content`) and the persist call (e.g., `_save_conversation`, `_append_assistant_message`) via grep. Don't invent names. The invariant is: **whatever string gets persisted must pass through `_finalize_assistant_turn()` first.**

**3e.** Wire into `_chat_stream_unlocked` (stream, ~line 1274) — at the point where the stream loop exits after accumulating the full response:

```python
        # After the Task L tail-guard stream loop exits with `accumulated`
        # as full content and `stream_buffer` as the held tail:
        stripped = self._finalize_assistant_turn(project_id, accumulated)

        # Emit the remainder: stripped content minus what we already yielded.
        # Already-emitted length = total accumulated length minus held buffer.
        already_emitted_len = len(accumulated) - len(stream_buffer)
        remainder = stripped[already_emitted_len:]
        if remainder:
            yield {"type": "content", "data": remainder}

        # CRITICAL ORDER: system_notice events must be yielded BEFORE usage,
        # otherwise frontend renders usage and moves on without showing notices.
        for notice in list(self._turn_context.get("pending_system_notices") or []):
            # `notice` is already SSE-ready: `_emit_system_notice_once` (~line 3083)
            # returns {"type": "system_notice", "category", "path", "reason",
            # "user_action"} at the top level. Yield it DIRECTLY — do NOT wrap
            # as {"type":"system_notice","data":notice}, which would put the
            # real fields under `data` and render empty in the existing frontend.
            yield notice
        self._turn_context["pending_system_notices"] = []

        # Then the existing usage yield:
        # yield {"type": "usage", "data": ...}

        # Persist `stripped` (NOT `accumulated`) to conversation.json and
        # feed `stripped` to post-turn compaction. Locate the existing
        # `_save_conversation` or equivalent call and replace its content
        # argument.
```

**Architectural summary:** `_build_turn_context` queues a `set` keyword without executing it; `_finalize_assistant_turn` either (a) executes tags and discards the pending keyword, or (b) executes the pending keyword as fallback. This realizes spec §3: "tag 存在 → tag 赢; 无可执行 tag → strong 关键词兜底".

- [ ] **Step 4: Run tests**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -v
```

Existing tests that used to see tags in persisted `conversation.json` will now see clean content. Update any such fixtures.

- [ ] **Step 5: Commit**

```powershell
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "Hook StageAckParser into both chat paths with tag priority"
```

---

### Task N: `_load_conversation` sanitize historical residual tags

**Spec:** §7 `_load_conversation` sanitize, Risks 历史残留 tag 污染 prompt

**Files:**
- Modify: `backend/chat.py` (`_load_conversation`)
- Modify: `tests/test_chat_runtime.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_chat_runtime.py`:

```python
class LoadConversationSanitizeTests(ChatRuntimeTests):
    def _write_conv(self, messages):
        import json
        (self.project_dir / "conversation.json").write_text(
            json.dumps(messages, ensure_ascii=False), encoding="utf-8"
        )

    def test_assistant_residual_tag_stripped(self):
        handler = self._make_handler_with_project()
        self._write_conv([
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": (
                "回复。\n<stage-ack>outline_confirmed_at</stage-ack>\n"
            )},
        ])
        loaded = handler._load_conversation(self.project_id)
        self.assertNotIn("<stage-ack", loaded[1]["content"])
        self.assertIn("回复。", loaded[1]["content"])

    def test_user_role_tag_preserved_as_literal(self):
        handler = self._make_handler_with_project()
        self._write_conv([{
            "role": "user",
            "content": "我写的 <stage-ack>xxx</stage-ack> 是什么意思？",
        }])
        loaded = handler._load_conversation(self.project_id)
        self.assertIn("<stage-ack>", loaded[0]["content"])

    def test_no_tag_messages_unchanged(self):
        handler = self._make_handler_with_project()
        original = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好，请问..."},
        ]
        self._write_conv(original)
        loaded = handler._load_conversation(self.project_id)
        self.assertEqual(
            [(m["role"], m["content"]) for m in loaded],
            [(m["role"], m["content"]) for m in original],
        )
```

- [ ] **Step 2: Run to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py::LoadConversationSanitizeTests -v
```

- [ ] **Step 3: Sanitize in `_load_conversation`**

Find `_load_conversation` in `backend/chat.py` (grep: `def _load_conversation`). At the return point, before returning, sanitize assistant-role messages only:

```python
    def _load_conversation(self, project_id: str) -> list[dict]:
        # ... existing load logic producing `messages` ...
        from backend.stage_ack import StageAckParser
        parser = StageAckParser()
        sanitized = []
        for message in messages:
            role = message.get("role")
            content = message.get("content", "") or ""
            if role == "assistant" and "<stage-ack" in content.lower():
                new_message = dict(message)
                new_message["content"] = parser.strip(content)
                sanitized.append(new_message)
            else:
                sanitized.append(message)
        return sanitized
```

- [ ] **Step 4: Run to verify pass**

```powershell
.venv\Scripts\python -m pytest tests/test_chat_runtime.py -v
```

- [ ] **Step 5: Commit**

```powershell
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "Sanitize residual stage-ack tags in legacy assistant messages"
```

---

### Task O: Frontend — `rollbackMenuLogic.js` — S2+ advanced rollback option for s0

**Spec:** §7 frontend RollbackMenu S2+ "回到需求访谈", §4 checkpoint 矩阵

**Files:**
- Modify: `frontend/src/utils/rollbackMenuLogic.js` (add `getAdvancedRollbackOptions`)
- Modify: `frontend/src/components/RollbackMenu.jsx` (render advanced options after first-level option)
- Modify: `frontend/tests/rollbackMenu.test.mjs` (or new file — import from logic file only)

- [ ] **Step 1: Write failing tests**

Append to `frontend/tests/rollbackMenu.test.mjs`:

```javascript
import test from "node:test";
import assert from "node:assert/strict";
import {
  ROLLBACK_HIDDEN_STAGES,
  getAdvancedRollbackOptions,
  OPTION_KIND_CLEAR_CHECKPOINT,
} from "../src/utils/rollbackMenuLogic.js";

test("getAdvancedRollbackOptions returns empty array at S0 and S1", () => {
  assert.deepEqual(getAdvancedRollbackOptions("S0"), []);
  assert.deepEqual(getAdvancedRollbackOptions("S1"), []);
});

test("getAdvancedRollbackOptions at S2 contains s0 entry", () => {
  const opts = getAdvancedRollbackOptions("S2");
  const s0 = opts.find(o => o.checkpoint === "s0-interview-done");
  assert.ok(s0, "S2 must expose s0 rollback option");
  assert.equal(s0.kind, OPTION_KIND_CLEAR_CHECKPOINT);
  assert.equal(s0.action, "clear");
  assert.ok(s0.label.includes("需求访谈"));
  assert.ok(s0.confirmBody.includes("表单信息不会删"));
});

for (const stage of ["S3", "S4", "S5", "S6", "S7"]) {
  test(`getAdvancedRollbackOptions at ${stage} contains s0 entry`, () => {
    const opts = getAdvancedRollbackOptions(stage);
    assert.ok(opts.some(o => o.checkpoint === "s0-interview-done"));
  });
}

test("ROLLBACK_HIDDEN_STAGES still hides menu at S0/S1", () => {
  assert.ok(ROLLBACK_HIDDEN_STAGES.has("S0"));
  assert.ok(ROLLBACK_HIDDEN_STAGES.has("S1"));
});
```

- [ ] **Step 2: Run to verify fail**

```powershell
cd frontend
node --test tests/rollbackMenu.test.mjs
```

Expected: import fails or function missing.

- [ ] **Step 3: Add `getAdvancedRollbackOptions` in `rollbackMenuLogic.js`**

Append to `frontend/src/utils/rollbackMenuLogic.js`:

```javascript
const S0_ROLLBACK_OPTION = {
  kind: OPTION_KIND_CLEAR_CHECKPOINT,
  label: '回到需求访谈',
  confirmTitle: '确认回到需求访谈？',
  confirmBody:
    '之前的表单信息不会删；回到 S0 继续补充澄清；' +
    '当前大纲、研究计划、数据日志等下游产出也会被清空。',
  checkpoint: 's0-interview-done',
  action: 'clear',
}

/**
 * Returns the advanced (secondary) rollback options for the given stage,
 * rendered AFTER the first-level option. Currently exposes:
 *   - s0 interview rollback for S2+ (cascades — clears all downstream
 *     checkpoints when the user confirms)
 * Empty array at S0 and S1 (menu is hidden there per ROLLBACK_HIDDEN_STAGES).
 * @param {string} stageCode
 * @returns {object[]}
 */
export function getAdvancedRollbackOptions(stageCode) {
  if (ROLLBACK_HIDDEN_STAGES.has(stageCode)) return []
  switch (stageCode) {
    case 'S2':
    case 'S3':
    case 'S4':
    case 'S5':
    case 'S6':
    case 'S7':
      return [S0_ROLLBACK_OPTION]
    default:
      return []
  }
}
```

**Then in `RollbackMenu.jsx`**: after the existing first-level option rendering, iterate over `getAdvancedRollbackOptions(stageCode)` and render each as a menu item. Reuse the existing click handler pattern (`onClick` → `postCheckpoint(checkpoint, action)` for `OPTION_KIND_CLEAR_CHECKPOINT`). Separate advanced section with a divider or subheader ("高级回退") to distinguish it from first-level option.

> Implementer note: the exact RollbackMenu.jsx structure should be read first. If the existing component only renders one option, you'll need to extend it to render a list. Keep the logic-to-presentation split — logic stays in `rollbackMenuLogic.js`.

- [ ] **Step 4: Run to verify pass**

```powershell
cd frontend
node --test tests/
```

Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add frontend/src/utils/rollbackMenuLogic.js frontend/src/components/RollbackMenu.jsx frontend/tests/rollbackMenu.test.mjs
git commit -m "Add S2+ advanced rollback option for s0 interview"
```

---

### Task P: Frontend — `workspaceSummary` surface `s0InterviewDone` preserving raw flags

**Spec:** §7 frontend workspaceSummary `flags.s0InterviewDone`

**Files:**
- Modify: `frontend/src/utils/workspaceSummary.js`
- Modify: `frontend/tests/workspaceSummary.test.mjs`

- [ ] **Step 1: Write failing tests**

Append to `frontend/tests/workspaceSummary.test.mjs`:

```javascript
test("summarizeWorkspace surfaces s0InterviewDone from raw flags", () => {
  const raw = {
    stage_code: "S0",
    flags: {
      s0_interview_done: false,
      outline_ready: false,
      project_overview_ready: true,
    },
  };
  const summary = summarizeWorkspace(raw);
  assert.equal(summary.flags.s0InterviewDone, false);
});

test("summarizeWorkspace preserves raw flags (outline_ready, etc.)", () => {
  const raw = {
    stage_code: "S1",
    flags: {
      s0_interview_done: true,
      outline_ready: true,
      project_overview_ready: true,
      other_flag: "value",
    },
  };
  const summary = summarizeWorkspace(raw);
  assert.equal(summary.flags.outline_ready, true, "raw snake_case flag kept");
  assert.equal(summary.flags.other_flag, "value");
  assert.equal(summary.flags.s0InterviewDone, true, "camelCase added");
});

test("summarizeWorkspace with no flags field returns empty-ish flags", () => {
  const summary = summarizeWorkspace({ stage_code: "S0" });
  assert.equal(summary.flags.s0InterviewDone, false);
});
```

- [ ] **Step 2: Run to verify fail**

```powershell
cd frontend
node --test tests/workspaceSummary.test.mjs
```

- [ ] **Step 3: Update `summarizeWorkspace`**

In `frontend/src/utils/workspaceSummary.js`, change the `flags:` line:

```javascript
  return {
    // ... existing fields up through deliveryMode
    flags: {
      ...(source.flags || {}),
      s0InterviewDone: Boolean((source.flags || {}).s0_interview_done),
    },
    // ... remaining fields
  };
```

Key: preserve raw snake_case flags via `...spread` AND add `s0InterviewDone` camelCase. `isS1ConfirmOutlineEnabled` and other consumers that read `flags.outline_ready` must continue to work.

- [ ] **Step 4: Run to verify pass**

```powershell
cd frontend
node --test tests/
```

- [ ] **Step 5: Commit**

```powershell
git add frontend/src/utils/workspaceSummary.js frontend/tests/workspaceSummary.test.mjs
git commit -m "Surface s0InterviewDone flag in workspaceSummary"
```

---

### Task Q: Frontend — `chatPresentation.js` defensive tag strip

**Spec:** §7 frontend `ChatPanel` 二级保险剥 tag

**Files:**
- Modify: `frontend/src/utils/chatPresentation.js`
- Modify: `frontend/tests/chatPresentation.test.mjs`

- [ ] **Step 1: Write failing tests**

Append to `frontend/tests/chatPresentation.test.mjs`:

```javascript
import { stripStageAckTags } from "../src/utils/chatPresentation.js";

test("stripStageAckTags removes valid-key assistant tag", () => {
  const content = "完成。\n<stage-ack>outline_confirmed_at</stage-ack>\n";
  const out = stripStageAckTags(content);
  assert.ok(!out.includes("<stage-ack"));
});

test("stripStageAckTags removes unknown-key tag too", () => {
  const content = "回复。\n<stage-ack>bogus</stage-ack>";
  const out = stripStageAckTags(content);
  assert.ok(!out.includes("<stage-ack"));
});

test("stripStageAckTags removes clear-action tag", () => {
  const content = '<stage-ack action="clear">outline_confirmed_at</stage-ack>';
  assert.equal(stripStageAckTags(content), "");
});

test("stripStageAckTags preserves no-tag content", () => {
  assert.equal(stripStageAckTags("纯正文"), "纯正文");
});

test("splitAssistantMessageBlocks applies stripStageAckTags first", () => {
  const blocks = splitAssistantMessageBlocks(
    "完成。\n<stage-ack>outline_confirmed_at</stage-ack>\n",
  );
  // No tool lines, so this should be a single text block with tag stripped
  assert.equal(blocks.length, 1);
  assert.equal(blocks[0].type, "text");
  assert.ok(!blocks[0].content.includes("<stage-ack"));
});
```

- [ ] **Step 2: Run to verify fail**

```powershell
cd frontend
node --test tests/chatPresentation.test.mjs
```

- [ ] **Step 3: Add `stripStageAckTags` and apply inside `splitAssistantMessageBlocks`**

In `frontend/src/utils/chatPresentation.js`:

```javascript
const STAGE_ACK_TAG_RE = /<stage-ack(?:\s+action="(?:set|clear)")?>[a-z_0-9]+<\/stage-ack>/gi;

export function stripStageAckTags(content = "") {
  if (!content || !content.toLowerCase().includes("<stage-ack")) {
    return content;
  }
  return content.replace(STAGE_ACK_TAG_RE, "").replace(/\n{3,}/g, "\n\n");
}

export function splitAssistantMessageBlocks(content = "") {
  const safeContent = stripStageAckTags(content);
  const lines = safeContent.split("\n");
  const blocks = [];
  let textBuffer = [];
  // ... rest of existing logic unchanged, using `safeContent` instead of
  // `content` ...
}
```

`ChatPanel.jsx` already calls `splitAssistantMessageBlocks(msg.content)` so no `.jsx` edits needed — the strip happens at the logic layer.

- [ ] **Step 4: Run to verify pass**

```powershell
cd frontend
node --test tests/
```

- [ ] **Step 5: Commit**

```powershell
git add frontend/src/utils/chatPresentation.js frontend/tests/chatPresentation.test.mjs
git commit -m "Add defensive stripStageAckTags in splitAssistantMessageBlocks"
```

---

### Task R: `skill/SKILL.md` update + `tests/test_packaging_docs.py` lock

**Spec:** §6 SKILL.md 规则调整（前置位置 + 硬格式 + 附录 + 强关键词短语表）

**Files:**
- Modify: `skill/SKILL.md`
- Modify: `tests/test_packaging_docs.py`

- [ ] **Step 1: Write failing packaging-doc tests**

Append to `tests/test_packaging_docs.py`:

```python
class SkillMdS0InterviewLockTests(unittest.TestCase):
    def setUp(self):
        from pathlib import Path
        repo_root = Path(__file__).resolve().parents[1]
        self.skill_md = (repo_root / "skill" / "SKILL.md").read_text(encoding="utf-8")

    def test_s0_mandatory_block_present(self):
        self.assertIn("### S0 预访谈（强制）", self.skill_md)

    def test_s0_rules_present(self):
        # Rule 1: first-turn must ask clarifying questions
        self.assertIn("第一轮回复只能做一件事", self.skill_md)
        # Rule 2: four forbidden files
        self.assertIn("plan/outline.md", self.skill_md)
        self.assertIn("plan/research-plan.md", self.skill_md)
        self.assertIn("plan/data-log.md", self.skill_md)
        self.assertIn("plan/analysis-notes.md", self.skill_md)
        # Rule 4: tag emission on last line
        self.assertIn(
            "<stage-ack>s0_interview_done_at</stage-ack>", self.skill_md
        )

    def test_all_six_keys_in_appendix(self):
        for key in [
            "s0_interview_done_at",
            "outline_confirmed_at",
            "review_started_at",
            "review_passed_at",
            "presentation_ready_at",
            "delivery_archived_at",
        ]:
            self.assertIn(key, self.skill_md, f"Missing key {key} in SKILL.md")

    def test_escape_rule_for_examples(self):
        # Per spec: examples in body text MUST use escaped form, even in code fences
        self.assertIn("即使在 code fence", self.skill_md)

    def test_strong_keyword_examples_table(self):
        # Checks a sample phrase from each of the six key's strong-keyword set
        self.assertIn("跳过访谈", self.skill_md)  # s0
        self.assertIn("确认大纲", self.skill_md)
        self.assertIn("开始审查", self.skill_md)
        self.assertIn("审查通过", self.skill_md)
        self.assertIn("演示准备完成", self.skill_md)
        self.assertIn("归档结束项目", self.skill_md)
```

- [ ] **Step 2: Run to verify fail**

```powershell
.venv\Scripts\python -m pytest tests/test_packaging_docs.py::SkillMdS0InterviewLockTests -v
```

Expected: all 5 fail (SKILL.md not yet updated).

- [ ] **Step 3: Update `skill/SKILL.md`**

**3a.** Insert the §S0 mandatory block immediately after the 启动门禁 section (use `Get-Content -TotalCount 100 -LiteralPath skill/SKILL.md` to locate):

```markdown
### S0 预访谈（强制）

当前阶段是 S0 且本项目 `stage_checkpoints.json` 还没有 `s0_interview_done_at` 时：

1. 你的**第一轮回复**只能做一件事：基于 `plan/project-overview.md` 提出 3-5 个打包的澄清问题（一条消息内全发完）。
2. 第一轮**禁止**：
   - 调用 `write_file` 写入 `plan/outline.md`、`plan/research-plan.md`、`plan/data-log.md`、`plan/analysis-notes.md`
   - 输出 `<stage-ack>s0_interview_done_at</stage-ack>`
3. 用户回答问题后，或用户明确说"跳过访谈 / 不用问了 / 直接开始"后，才可以更新 `plan/project-overview.md`；用户跳过就沿用 seed 不改。
4. 完成上述处理后，在回复**最后单独一行**输出：

`<stage-ack>s0_interview_done_at</stage-ack>`

不要解释这个 tag。不要把 tag 放进代码块、列表、引用、正文中间。

### S0 追问维度建议清单

从以下 6 条里选 3-5 条，内容按 seed 自由改写：
- 决策场景（这份报告将拿去做什么决定？）
- 读者深度（读者对主题的既有了解？）
- 期望核心发现（最想在报告里看到的 1-2 个洞察）
- 时间 / 资源约束（除截止日外是否有其他约束）
- 已有假设（心中已经有哪些预判想验证或推翻）
- 关键风险与盲区（最担心报告漏掉什么）
```

**3b.** In §S1-S7 各 "推进到 Sx" 段, 删除对 "挺好继续写""这段可以" 等弱表达的引用. 改为统一表述:

> 必须等用户在工作区点击对应按钮，或用户明确表达推进意图时，你在回复**最后单独一行**输出 `<stage-ack>KEY</stage-ack>`（KEY 见附录）。用户明确回退意图时输出 `<stage-ack action="clear">KEY</stage-ack>`。

**3c.** 追加附录到文件末尾:

```markdown
## 附录：stage-ack 标签规范

阶段推进 / 回退的控制信号。只在用户明确表达推进或回退意图时使用。

**合法 KEY（6 个）**：
- `s0_interview_done_at`
- `outline_confirmed_at`
- `review_started_at`
- `review_passed_at`
- `presentation_ready_at`
- `delivery_archived_at`

**语法**：
- Set：`<stage-ack>KEY</stage-ack>`
- Clear：`<stage-ack action="clear">KEY</stage-ack>`

**用法规则**：
- 只在用户明确表达推进 / 回退意图时发
- 不要每条消息都发
- 不要发未列出的 KEY
- tag **必须放在回复最后、单独一行、代码块外**
- **正文中需要展示 XML 示例时必须使用转义文本**（如 `\<stage-ack\>...\</stage-ack\>`）；**即使在 code fence 内也不要输出真实 `<stage-ack>` 标签**——真实 tag 不管放哪里都会被 parser 识别并剥离

**强关键词短语表**（用户习惯说法，供你理解意图；非要求模型输出）：
- s0_interview_done_at：跳过访谈 / 不用问了 / 先写大纲吧 / 够了开始吧 / 直接开始
- outline_confirmed_at：确认大纲 / 大纲没问题 / 按这个大纲写 / 就这个大纲 / 就按这个版本
- review_started_at：开始审查 / 进入审查 / 可以审查了 / 开始 review
- review_passed_at：审查通过 / 审查没问题 / 报告可以交付
- presentation_ready_at：演示准备好了 / 演示准备完成 / PPT 完成 / 讲稿完成
- delivery_archived_at：归档结束项目 / 项目交付完成 / 交付归档
```

- [ ] **Step 4: Run to verify pass**

```powershell
.venv\Scripts\python -m pytest tests/test_packaging_docs.py -v
```

If other sentences in `test_packaging_docs.py` assert specific weak-keyword phrases that we've now removed, update or delete those assertions.

- [ ] **Step 5: Commit**

```powershell
git add skill/SKILL.md tests/test_packaging_docs.py
git commit -m "Update SKILL.md with S0 mandatory interview and stage-ack rules"
```

---

### Task S: Integration — full suites + build + repackage + three-round smoke

**Spec:** Rollout Phase 1 steps 4-6

**Files:**
- (no code changes — verification + packaging only)

- [ ] **Step 1: Full backend test suite**

```powershell
.venv\Scripts\python -m pytest tests/ -q
```

Expected: all pass. Baseline was 403 passed / 1 skipped; new tests from Tasks A-R significantly expand this.

- [ ] **Step 2: Full frontend test suite**

```powershell
cd frontend
node --test tests/
```

Expected: 140+ pass (baseline 140 + Task O/P/Q new tests).

- [ ] **Step 3: Frontend production build**

```powershell
cd frontend
npm run build
```

Expected: `✓ built in …`, 0 errors.

- [ ] **Step 4: Clean dist and rebuild desktop package**

```powershell
if (Test-Path -LiteralPath dist) { Remove-Item -LiteralPath dist -Recurse -Force }
build.bat
```

> **Prerequisites (set before build.bat)**: `managed_client_token.txt` and `managed_search_pool.json` must exist in repo root (see `CLAUDE.md` "构建期私有文件"), or corresponding env vars `CONSULTING_REPORT_MANAGED_CLIENT_TOKEN` / `CONSULTING_REPORT_MANAGED_SEARCH_POOL_FILE`.

Expected: `dist\咨询报告助手\` produced, roughly 91 MB ± few hundred KB.

- [ ] **Step 5: Three-round smoke test (per Rollout Phase 1 step 6)**

Launch `dist\咨询报告助手\咨询报告助手.exe` and perform these three scenarios in sequence (clean user data between rounds: `Remove-Item -Recurse -Force $HOME/.consulting-report`):

**Round 1 — Normal S0 interview**:
- New project form, submit.
- Welcome message appears.
- Model's first turn should deliver 3-5 packaged clarification questions; verify no `write_file` to outline/research-plan/data-log/analysis-notes.
- Answer the questions.
- Model second turn should merge answers and emit tag; workspace should advance to S1.

**Round 2 — User skip**:
- New project, different theme.
- Welcome → Model asks 3-5 questions.
- User replies "跳过访谈" (or "直接开始").
- Workspace advances to S1; `project-overview.md` is seed text only (unchanged).

**Round 3 — Legacy project (no regression)**:
- Pre-create a project dir at stage S2 (with pre-existing `stage_checkpoints.json` = `{"__migrated_at": "2026-04-17T10:00:00", "outline_confirmed_at": "2026-04-18T..."}` — NO s0 key).
- Launch app, open the legacy project.
- Workspace should show stage S2 (NOT打回 S0).
- Verify `stage_checkpoints.json` now contains `s0_interview_done_at` (backfilled at load).

- [ ] **Step 6: Commit (if lock files changed)**

```powershell
git status
```

If `frontend/package-lock.json` or similar got touched by `npm run build`, commit:

```powershell
git add frontend/package-lock.json
git commit -m "Refresh frontend package lock after build"
```

If no changes, skip commit.

---

## Self-Review

**Spec coverage check:**
- §1 S0 流程 → Tasks A-C (infra), D-F (parser), K (write gate), L-M (stream + finalize)
- §2 tag 语法 + 防注入 + 剥离 → Tasks D-F, L (tail guard), M (finalize + compaction strip), N (sanitize)
- §3 关键词重构 + S0 软门槛 → Tasks H, I
- §4 checkpoint 矩阵 → Tasks A, G, O
- §5 前置校验 → Tasks A, G (400), M (finalize prereq notice)
- §6 SKILL.md → Task R (with packaging-doc lock)
- §7 代码变更清单 → Tasks A-Q distributed per file
- §8 测试计划 → each task's Step 1 covers the spec's required cases
- Risks → tail guard (L), schema migration (C), sanitize (N), NON_PLAN_WRITE patch (J), S0 soft gate (I, M)
- Rollout Phase 1 → migration先行 (C before D-F), integration+smoke in S

**Placeholder scan:** All Step 1 tests use real `_make_handler_with_project()` or standalone class unit tests. No `pass`, `handler = ...`, `similarly for S3-S7`, or placeholder commit messages.

**Type / name consistency:**
- `StageAckEvent`, `StageAckParser.parse_raw/parse/strip`, `stream_split_safe_tail`, `_has_prior_s0_assistant_turn`, `_finalize_assistant_turn`, `_S0_BLOCKED_PLAN_FILES`, `getAdvancedRollbackOptions`, `stripStageAckTags` — used consistently.
- `flags.s0_interview_done` (backend snake_case) → `flags.s0InterviewDone` (frontend camelCase via `summarizeWorkspace`), matched in Tasks B + P.
- Real backend functions: `_execute_tool`, `_chat_unlocked`, `_chat_stream_unlocked`, `_should_block_non_plan_write`, `_should_allow_non_plan_write`, `_load_conversation`, `_make_handler_with_project` (test helper).

**Windows-safe commands:** All shell invocations use `.venv\Scripts\python`, PowerShell idioms for file ops (no `rm -rf`), `build.bat` for packaging, `cd frontend && ...` for Node.

**Ordering rationale:** Task A-C land first to satisfy Rollout's "migration先行". Task G can follow A without parser. Tasks D-F (parser) then enable L-M (chat runtime integration). H-K patch chat.py independently. N-Q are independent. R (SKILL.md) and S (integration) last.

---

## Open Risks for Implementer

1. **Task M stream wiring**: the exact names of assistant-message persistence and compaction entry points in `backend/chat.py` must be located by grep — don't invent helpers. `_chat_unlocked` (line 1476) is non-stream; `_chat_stream_unlocked` (line 1274) is stream. Both must pass `stripped = _finalize_assistant_turn(...)` into the persist step.

2. **Task K S0 write gate placement**: insert BEFORE `_should_block_non_plan_write` so S0 fast-rejects without confusing messages. Use `skill_engine._to_posix(args["file_path"]).lstrip("/")` to normalize before comparison.

3. **Task L tail guard subtlety**: `stream_split_safe_tail` uses `find` (first occurrence), not `rfind`. `rfind` would anchor on the `<` in `</stage-ack>` — leaking the opening `<stage-ack` before it.

4. **Task C migration idempotency**: `_backfill_stage_checkpoints_if_missing` now runs on every load. Verify via benchmark it adds < 5 ms per call on a project with existing checkpoints (stat + json parse + maybe no write).

5. **Task H existing tests sweep**: budget ~30 min to update tests asserting old weak-keyword behavior. Don't refactor — just delete the assertions or change to "is None".

6. **Task R packaging tests**: if `test_packaging_docs.py` has other SKILL.md sentence locks broken by weak-keyword wording removal, update those. Run `.venv\Scripts\python -m pytest tests/test_packaging_docs.py -v` to see what surfaces.
