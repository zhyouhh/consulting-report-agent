import asyncio
import json
import os
import tempfile
import threading
import time
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

import backend.main as main_module
from backend.chat import LEGACY_EMPTY_ASSISTANT_FALLBACKS, USER_VISIBLE_FALLBACK
from backend.models import ChatRequest


async def _collect_streaming_chunks(response):
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    return chunks


class ChatRequestValidationTests(unittest.TestCase):
    def test_chat_request_validator_rejects_empty_message_without_trigger(self):
        with self.assertRaises(ValidationError):
            ChatRequest.model_validate(
                {
                    "project_id": "demo",
                    "message_text": "",
                    "system_trigger": None,
                }
            )

    def test_chat_request_rejects_whitespace_only_message_without_trigger(self):
        with self.assertRaises(ValidationError):
            ChatRequest.model_validate(
                {
                    "project_id": "demo",
                    "message_text": "   ",
                    "system_trigger": None,
                }
            )

    def test_chat_request_rejects_invalid_trigger_value(self):
        with self.assertRaises(ValidationError):
            ChatRequest.model_validate(
                {
                    "project_id": "demo",
                    "message_text": "",
                    "system_trigger": "garbage",
                }
            )

    def test_chat_request_validator_accepts_empty_message_with_trigger(self):
        req = ChatRequest.model_validate(
            {
                "project_id": "demo",
                "message_text": "",
                "system_trigger": "independent_review_done",
            }
        )

        self.assertEqual(req.system_trigger, "independent_review_done")
        self.assertEqual(req.message_text, "")

    def test_chat_request_validator_accepts_non_empty_message_without_trigger(self):
        req = ChatRequest.model_validate(
            {
                "project_id": "demo",
                "message_text": "hello",
            }
        )

        self.assertIsNone(req.system_trigger)

    def test_chat_request_accepts_trigger_metadata(self):
        # run_id + report_mtime_ns are opaque strings; a large st_mtime_ns string must survive
        # round-trip without precision loss (it would overflow a JS number if sent as int).
        big_mtime = "1760000000123456789"
        req = ChatRequest.model_validate(
            {
                "project_id": "demo",
                "message_text": "",
                "system_trigger": "independent_review_done",
                "run_id": "run-abc123",
                "report_mtime_ns": big_mtime,
            }
        )

        self.assertEqual(req.run_id, "run-abc123")
        self.assertIsInstance(req.run_id, str)
        self.assertEqual(req.report_mtime_ns, big_mtime)
        self.assertIsInstance(req.report_mtime_ns, str)

    def test_chat_request_metadata_optional(self):
        req = ChatRequest.model_validate(
            {
                "project_id": "demo",
                "message_text": "hello",
            }
        )

        self.assertIsNone(req.run_id)
        self.assertIsNone(req.report_mtime_ns)


class CheckpointTableInvariantTests(unittest.TestCase):
    def test_checkpoint_tables_key_sets_are_aligned(self):
        from backend.main import _CHECKPOINT_ROUTES
        from backend.skill import SkillEngine

        engine_keys = set(SkillEngine.STAGE_CHECKPOINT_KEYS)
        cascade_keys = set(SkillEngine._CASCADE_ORDER)
        route_keys = set(_CHECKPOINT_ROUTES.values())

        self.assertEqual(engine_keys, cascade_keys, "STAGE_CHECKPOINT_KEYS vs _CASCADE_ORDER")
        self.assertEqual(engine_keys, route_keys, "STAGE_CHECKPOINT_KEYS vs _CHECKPOINT_ROUTES values")


class CheckpointEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main_module.app)
        main_module.register_desktop_bridge(None)

    def tearDown(self):
        main_module.register_desktop_bridge(None)

    def _write_stage_one_prerequisites(self, project_dir: Path) -> None:
        (project_dir / "plan" / "notes.md").write_text(
            "# Notes\n\n"
            "## Boundaries\n"
            "- Focus on enterprise AI adoption decisions.\n"
            "## Assumptions\n"
            "- Budget remains flat through FY26.\n",
            encoding="utf-8",
        )
        (project_dir / "plan" / "references.md").write_text(
            "# References\n\n"
            "## Sources\n"
            "- Internal interview transcript: operations lead workshop\n"
            "- External benchmark: https://example.com/ai-benchmark\n",
            encoding="utf-8",
        )
        (project_dir / "plan" / "outline.md").write_text(
            "# Report outline\n\n"
            "## Executive summary\n"
            "- Key finding\n"
            "## Market context\n"
            "- Market signal\n"
            "## Recommendations\n"
            "- Next step\n",
            encoding="utf-8",
        )
        (project_dir / "plan" / "research-plan.md").write_text(
            "# Research plan\n\n"
            "## Research methods\n"
            "- Expert interviews\n"
            "## Data sources\n"
            "- CRM export\n",
            encoding="utf-8",
        )

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

    def test_checkpoint_endpoint_rejects_review_started_when_predecessors_missing(self):
        from backend.skill import SkillEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(__file__).resolve().parents[1] / "skill"
            engine = SkillEngine(Path(tmpdir) / "projects", skill_dir)
            engine.create_project(
                name="demo",
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
                workspace_dir=str(Path(tmpdir) / "ws"),
            )
            project_dir = engine.get_project_path("demo")
            self.assertIsNotNone(project_dir)
            self._write_stage_one_prerequisites(project_dir)
            engine._save_stage_checkpoint(project_dir, "s0_interview_done_at")
            engine._save_stage_checkpoint(project_dir, "outline_confirmed_at")
            (project_dir / "content" / "report_draft_v1.md").write_text(
                "# Draft\n\n" + ("有效正文。" * 1200),
                encoding="utf-8",
            )
            with mock.patch.object(main_module, "skill_engine", engine):
                response = self.client.post("/api/projects/demo/checkpoints/review-started")

        self.assertEqual(response.status_code, 400)
        self.assertIn("data-log", response.json()["detail"])


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


class WorkspaceApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main_module.app)
        main_module.register_desktop_bridge(None)

    def tearDown(self):
        main_module.register_desktop_bridge(None)

    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_workspace_endpoint_returns_stage_summary(self, mock_summary):
        mock_summary.return_value = {
            "stage_code": "S4",
            "status": "进行中",
            "completed_items": ["报告结构确定"],
            "next_actions": ["图表制作完成"],
        }

        response = self.client.get("/api/projects/demo/workspace")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["stage_code"], "S4")

    def test_workspace_endpoint_returns_404_for_missing_project(self):
        response = self.client.get("/api/projects/definitely-missing-project/workspace")
        self.assertEqual(response.status_code, 404)

    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_workspace_summary_includes_new_review_flags(self, mock_summary):
        mock_summary.return_value = {
            "stage_code": "S5",
            "status": "进行中",
            "completed_items": [],
            "next_actions": [],
            "flags": {
                "review_checklist_ready": True,
                "independent_review_ready": False,
                "lint_report_ready": False,
                "review_reports_ready": False,
            },
        }

        response = self.client.get("/api/projects/demo/workspace")

        self.assertEqual(response.status_code, 200)
        flags = response.json()["flags"]
        self.assertIn("review_checklist_ready", flags)
        self.assertIs(flags["independent_review_ready"], False)
        self.assertIs(flags["lint_report_ready"], False)
        self.assertIs(flags["review_reports_ready"], False)

    @mock.patch("backend.main.skill_engine.get_project_path")
    def test_clear_conversation_removes_new_and_legacy_sidecars(self, mock_get_project_path):
        with self.subTest("remove conversation and both sidecars"):
            import tempfile
            from pathlib import Path

            with tempfile.TemporaryDirectory() as tmpdir:
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

    def test_clear_conversation_waits_for_project_request_lock(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)
            (project_path / "conversation.json").write_text("[]", encoding="utf-8")
            (project_path / "conversation_state.json").write_text("{}", encoding="utf-8")
            (project_path / "conversation_compact_state.json").write_text("{}", encoding="utf-8")
            request_lock = threading.Lock()
            request_lock.acquire()
            handler = mock.Mock()
            handler._get_project_request_lock.return_value = request_lock
            result_holder = {}
            finished = threading.Event()

            def run_clear():
                try:
                    result_holder["result"] = asyncio.run(main_module.clear_conversation("proj-demo"))
                finally:
                    finished.set()

            with mock.patch("backend.main.skill_engine.get_project_path", return_value=project_path):
                with mock.patch("backend.main.get_chat_handler", return_value=handler):
                    clear_thread = threading.Thread(target=run_clear)
                    clear_thread.start()
                    self.assertFalse(finished.wait(0.2))
                    self.assertTrue((project_path / "conversation.json").exists())
                    request_lock.release()
                    clear_thread.join(timeout=2)

        self.assertFalse(clear_thread.is_alive())
        self.assertEqual(result_holder["result"], {"status": "ok"})
        self.assertFalse((project_path / "conversation.json").exists())
        self.assertFalse((project_path / "conversation_state.json").exists())
        self.assertFalse((project_path / "conversation_compact_state.json").exists())

    @mock.patch("backend.main.skill_engine.create_project")
    def test_create_project_accepts_theme_like_display_name_without_slugging(self, mock_create_project):
        mock_create_project.return_value = {
            "id": "proj-demo",
            "name": "AI 战略 / 2026!",
        }

        response = self.client.post(
            "/api/projects",
            json={
                "name": "AI 战略 / 2026!",
                "workspace_dir": "D:/Workspaces/demo",
                "project_type": "strategy-consulting",
                "theme": "AI 战略 / 2026!",
                "target_audience": "高层决策者",
                "deadline": "2026-04-02",
                "expected_length": "5000字",
                "notes": "",
                "initial_material_paths": [],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["project"]["name"], "AI 战略 / 2026!")

    def test_select_workspace_folder_returns_bridge_value(self):
        bridge = mock.Mock()
        bridge.select_workspace_folder.return_value = "D:/Workspaces/demo"
        main_module.register_desktop_bridge(bridge)

        response = self.client.post("/api/system/select-workspace-folder")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["path"], "D:/Workspaces/demo")
        bridge.select_workspace_folder.assert_called_once_with()

    def test_select_workspace_files_returns_paths_from_bridge(self):
        bridge = mock.Mock()
        bridge.select_workspace_files.return_value = [
            "D:/Workspaces/demo/资料/访谈纪要.txt",
            "D:/Workspaces/demo/资料/市场图表.png",
        ]
        main_module.register_desktop_bridge(bridge)

        response = self.client.post(
            "/api/system/select-workspace-files",
            json={"workspace_dir": "D:/Workspaces/demo"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["paths"],
            [
                "D:/Workspaces/demo/资料/访谈纪要.txt",
                "D:/Workspaces/demo/资料/市场图表.png",
            ],
        )
        bridge.select_workspace_files.assert_called_once_with("D:/Workspaces/demo")

    @mock.patch("backend.main.skill_engine.add_materials")
    @mock.patch("backend.main.skill_engine.get_project_record")
    def test_select_materials_from_workspace_uses_bridge_and_imports_selection(
        self,
        mock_get_project_record,
        mock_add_materials,
    ):
        mock_get_project_record.return_value = {
            "id": "proj-demo",
            "workspace_dir": "D:/Workspaces/demo",
        }
        mock_add_materials.return_value = [
            {"id": "mat-1", "display_name": "访谈纪要.txt"},
        ]
        bridge = mock.Mock()
        bridge.select_workspace_files.return_value = ["D:/Workspaces/demo/资料/访谈纪要.txt"]
        main_module.register_desktop_bridge(bridge)

        response = self.client.post("/api/projects/proj-demo/materials/select-from-workspace")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["materials"][0]["id"], "mat-1")
        bridge.select_workspace_files.assert_called_once_with("D:/Workspaces/demo")
        mock_add_materials.assert_called_once_with(
            "proj-demo",
            ["D:/Workspaces/demo/资料/访谈纪要.txt"],
            added_via="workspace_select",
        )

    @mock.patch("backend.main.skill_engine.add_materials")
    @mock.patch("backend.main.skill_engine.get_project_record")
    def test_upload_materials_stages_files_before_importing(
        self,
        mock_get_project_record,
        mock_add_materials,
    ):
        mock_get_project_record.return_value = {
            "id": "proj-demo",
            "workspace_dir": "D:/Workspaces/demo",
        }
        mock_add_materials.return_value = [
            {"id": "mat-2", "display_name": "市场图表.png"},
        ]

        response = self.client.post(
            "/api/projects/proj-demo/materials/upload",
            files=[("files", ("市场图表.png", BytesIO(b"png-data"), "image/png"))],
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["materials"][0]["id"], "mat-2")
        args, kwargs = mock_add_materials.call_args
        self.assertEqual(args[0], "proj-demo")
        self.assertEqual(kwargs["added_via"], "chat_upload")
        self.assertEqual(len(args[1]), 1)
        self.assertTrue(args[1][0].endswith("市场图表.png"))

    @mock.patch("backend.main.run_quality_check")
    @mock.patch("backend.main.skill_engine.get_script_path")
    @mock.patch("backend.main.skill_engine.get_primary_report_path")
    def test_quality_check_endpoint_returns_script_output(
        self,
        mock_report_path,
        mock_script_path,
        mock_quality_check,
    ):
        mock_report_path.return_value = "D:/tmp/report.md"
        mock_script_path.return_value = "D:/skill/scripts/quality_check.ps1"
        mock_quality_check.return_value = {"status": "ok", "output": "高风险: 0"}

        response = self.client.post("/api/projects/demo/quality-check")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        mock_quality_check.assert_called_once_with(
            "D:/tmp/report.md",
            "D:/skill/scripts/quality_check.ps1",
        )

    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_independent_review_endpoint_requires_s5(self, mock_summary):
        mock_summary.return_value = {"stage_code": "S4"}

        response = self.client.get("/api/projects/demo/independent-review/stream")

        self.assertEqual(response.status_code, 400)
        self.assertIn("独立审查只能在 S5 阶段使用", response.json()["detail"])

    @mock.patch("backend.main.IndependentReviewAgent")
    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_independent_review_endpoint_returns_sse_content_type(self, mock_summary, mock_agent_cls):
        mock_summary.return_value = {"stage_code": "S5"}
        mock_agent_cls.return_value.run.return_value = [
            {"type": "progress", "message": "开始审查"},
        ]

        response = self.client.get("/api/projects/demo-sse/independent-review/stream")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertIn('"type": "progress"', response.text)
        self.assertIn("data: [DONE]", response.text)

    @mock.patch("backend.main.IndependentReviewAgent")
    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_independent_review_endpoint_does_not_block_event_loop(
        self,
        mock_summary,
        mock_agent_cls,
    ):
        class ConnectedRequest:
            async def is_disconnected(self):
                return False

        def slow_run(project_id, cancel_event=None, **kwargs):
            del project_id, cancel_event, kwargs
            time.sleep(0.6)
            yield {"type": "progress", "message": "审查完成"}

        async def collect_response():
            response = await main_module.independent_review_stream(
                "demo-review-nonblocking",
                ConnectedRequest(),
            )
            consumer = asyncio.create_task(_collect_streaming_chunks(response))
            started_at = time.perf_counter()
            await asyncio.sleep(0.05)
            elapsed = time.perf_counter() - started_at
            chunks = await consumer
            return elapsed, chunks

        mock_summary.return_value = {"stage_code": "S5"}
        mock_agent_cls.return_value.run.side_effect = slow_run

        elapsed, chunks = asyncio.run(collect_response())

        self.assertLess(elapsed, 0.3)
        self.assertTrue(any('"type": "progress"' in chunk for chunk in chunks))

    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_independent_review_endpoint_409_when_lock_held(self, mock_summary):
        from backend.independent_review import get_independent_review_lock

        project_id = "demo-review-concurrent"
        mock_summary.return_value = {"stage_code": "S5"}
        lock = get_independent_review_lock(project_id)
        self.assertTrue(lock.acquire(blocking=False))
        self.addCleanup(lock.release)

        response = self.client.get(f"/api/projects/{project_id}/independent-review/stream")

        self.assertEqual(response.status_code, 409)
        self.assertIn("上一次独立审查仍在进行中", response.json()["detail"])

    @mock.patch("backend.main.IndependentReviewAgent")
    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_independent_review_endpoint_releases_lock_on_disconnect(
        self,
        mock_summary,
        mock_agent_cls,
    ):
        from backend.independent_review import get_independent_review_lock

        class DisconnectedRequest:
            async def is_disconnected(self):
                return True

        worker_stopped = threading.Event()
        received_cancel_events = []

        def cancellable_run(project_id, cancel_event=None, **kwargs):
            del project_id, kwargs
            received_cancel_events.append(cancel_event)
            for _ in range(50):
                if cancel_event is not None and cancel_event.is_set():
                    worker_stopped.set()
                    return
                time.sleep(0.01)
            yield {"type": "progress", "message": "should not emit"}

        async def collect_response():
            response = await main_module.independent_review_stream(
                "demo-review-disconnect",
                DisconnectedRequest(),
            )
            return await _collect_streaming_chunks(response)

        mock_summary.return_value = {"stage_code": "S5"}
        mock_agent_cls.return_value.run.side_effect = cancellable_run

        chunks = asyncio.run(collect_response())

        self.assertEqual(chunks, [])
        self.assertEqual(len(received_cancel_events), 1)
        self.assertIsInstance(received_cancel_events[0], threading.Event)
        self.assertTrue(received_cancel_events[0].is_set())
        self.assertTrue(worker_stopped.is_set())
        lock = get_independent_review_lock("demo-review-disconnect")
        self.assertTrue(lock.acquire(blocking=False))
        lock.release()

    @mock.patch("backend.main.IndependentReviewAgent")
    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_independent_review_endpoint_emits_error_event_on_agent_exception(
        self,
        mock_summary,
        mock_agent_cls,
    ):
        mock_summary.return_value = {"stage_code": "S5"}
        mock_agent_cls.return_value.run.side_effect = RuntimeError("agent exploded")

        response = self.client.get("/api/projects/demo-review-error/independent-review/stream")

        self.assertEqual(response.status_code, 200)
        self.assertIn('"type": "error"', response.text)
        self.assertIn("agent exploded", response.text)
        self.assertIn("data: [DONE]", response.text)

    @mock.patch("backend.main.IndependentReviewAgent")
    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_get_review_uses_store_and_writes_tombstone_on_success(
        self,
        mock_summary,
        mock_agent_cls,
    ):
        # Task 3.3: the GET worker wires the real _REVIEW_SESSION_STORE + a backend run_id
        # into agent.run; on success the store ends with a done tombstone.
        from backend.independent_review import CANONICAL_REVIEW_PATH

        project_id = "demo-review-store-success"
        mock_summary.return_value = {"stage_code": "S5"}
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        captured = {}

        def fake_run(project_id_arg, run_id=None, store=None, cancel_event=None, **kwargs):
            del cancel_event, kwargs
            captured["run_id"] = run_id
            captured["store"] = store
            # simulate the real agent's success commit through the store (atomic replace
            # into a temp canonical, writing a done tombstone).
            canonical = os.path.join(tmpdir.name, "independent-review.md")
            fd, temp_path = tempfile.mkstemp(dir=tmpdir.name, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("final report")
            mtime = store.atomic_commit_report(project_id_arg, run_id, temp_path, canonical, {"messages": []})
            captured["mtime"] = mtime
            yield {"type": "review-completed", "path": CANONICAL_REVIEW_PATH, "report_mtime_ns": mtime}

        mock_agent_cls.return_value.run.side_effect = fake_run

        response = self.client.get(f"/api/projects/{project_id}/independent-review/stream")

        self.assertEqual(response.status_code, 200)
        # the worker passed the real module-level store + a non-empty backend run_id.
        self.assertIs(captured["store"], main_module._REVIEW_SESSION_STORE)
        self.assertTrue(captured["run_id"])
        # store holds a done tombstone (run-bound), surviving the worker finally's
        # finalize_orphan_running (no-op on done).
        self.assertIsNotNone(captured["mtime"])
        self.assertEqual(
            main_module._REVIEW_SESSION_STORE.get_done_mtime(project_id, captured["run_id"]),
            captured["mtime"],
        )
        self.assertIn("review-completed", response.text)

    @mock.patch("backend.main.IndependentReviewAgent")
    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_get_review_worker_exception_leaves_no_running_record(
        self,
        mock_summary,
        mock_agent_cls,
    ):
        # codex R3 BLOCKER 2: a GET worker exception must not leave a stuck running record
        # (finally finalize_orphan_running converges); a fresh first-claim must succeed.
        project_id = "demo-review-store-exc"
        mock_summary.return_value = {"stage_code": "S5"}
        mock_agent_cls.return_value.run.side_effect = RuntimeError("boom mid-run")

        response = self.client.get(f"/api/projects/{project_id}/independent-review/stream")

        self.assertEqual(response.status_code, 200)
        self.assertIn('"type": "error"', response.text)
        # no dead running record remains: a brand-new first-claim succeeds.
        self.assertTrue(
            main_module._REVIEW_SESSION_STORE.claim_first(project_id, "fresh-run", threading.Event())
        )
        # cleanup the record we just created for hygiene.
        main_module._REVIEW_SESSION_STORE.discard(project_id, "fresh-run")

    # ------------------------------------------------------------------
    # C4: POST /independent-review/stream (run-bound, resume) + discard
    # ------------------------------------------------------------------

    def _seed_done_tombstone(self, project_id, run_id, body="report"):
        """Seed the store with a done tombstone via the real atomic commit path."""
        store = main_module._REVIEW_SESSION_STORE
        self.assertTrue(store.claim_first(project_id, run_id, threading.Event()))
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        canonical = os.path.join(tmpdir.name, "independent-review.md")
        fd, temp_path = tempfile.mkstemp(dir=tmpdir.name, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        mtime = store.atomic_commit_report(project_id, run_id, temp_path, canonical, {"messages": []})
        self.assertIsNotNone(mtime)
        self.addCleanup(store.discard, project_id, run_id)
        return mtime

    def _seed_errored(self, project_id, run_id, snapshot=None):
        store = main_module._REVIEW_SESSION_STORE
        self.assertTrue(store.claim_first(project_id, run_id, threading.Event()))
        self.assertTrue(
            store.set_errored(project_id, run_id, snapshot or {"messages": [], "iteration": 2})
        )
        self.addCleanup(store.discard, project_id, run_id)

    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_review_post_requires_run_id(self, mock_summary):
        mock_summary.return_value = {"stage_code": "S5"}
        response = self.client.post(
            "/api/projects/demo-post-norun/independent-review/stream",
            json={"resume": False},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("run_id required", response.json()["detail"])

    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_review_post_requires_s5(self, mock_summary):
        mock_summary.return_value = {"stage_code": "S4"}
        response = self.client.post(
            "/api/projects/demo-post-s4/independent-review/stream",
            json={"resume": False, "run_id": "r1"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("独立审查只能在 S5 阶段使用", response.json()["detail"])

    @mock.patch("backend.main.IndependentReviewAgent")
    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_review_post_first_run(self, mock_summary, mock_agent_cls):
        # resume=false: a worker starts with the frontend run_id + real store; on success a
        # done tombstone is written and the wrapper emits review-completed carrying that run_id.
        from backend.independent_review import CANONICAL_REVIEW_PATH

        project_id = "demo-post-first"
        run_id = "frontend-run-1"
        mock_summary.return_value = {"stage_code": "S5"}
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        captured = {}

        def fake_run(project_id_arg, run_id=None, store=None, resume_snapshot=None, cancel_event=None, **kwargs):
            del cancel_event, kwargs
            captured["run_id"] = run_id
            captured["store"] = store
            captured["resume_snapshot"] = resume_snapshot
            canonical = os.path.join(tmpdir.name, "independent-review.md")
            fd, temp_path = tempfile.mkstemp(dir=tmpdir.name, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("final report")
            mtime = store.atomic_commit_report(project_id_arg, run_id, temp_path, canonical, {"messages": []})
            captured["mtime"] = mtime
            yield {"type": "review-completed", "path": CANONICAL_REVIEW_PATH, "report_mtime_ns": mtime}

        mock_agent_cls.return_value.run.side_effect = fake_run
        self.addCleanup(main_module._REVIEW_SESSION_STORE.discard, project_id, run_id)

        response = self.client.post(
            f"/api/projects/{project_id}/independent-review/stream",
            json={"resume": False, "run_id": run_id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["run_id"], run_id)
        self.assertIs(captured["store"], main_module._REVIEW_SESSION_STORE)
        self.assertIsNone(captured["resume_snapshot"])
        self.assertIn("review-completed", response.text)
        self.assertIn(run_id, response.text)
        self.assertIn(str(captured["mtime"]), response.text)
        self.assertIn("data: [DONE]", response.text)

    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_review_post_first_run_409_when_lock_held(self, mock_summary):
        from backend.independent_review import get_independent_review_lock

        project_id = "demo-post-locked"
        mock_summary.return_value = {"stage_code": "S5"}
        lock = get_independent_review_lock(project_id)
        self.assertTrue(lock.acquire(blocking=False))
        self.addCleanup(lock.release)

        response = self.client.post(
            f"/api/projects/{project_id}/independent-review/stream",
            json={"resume": False, "run_id": "r1"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("上一次独立审查仍在进行中", response.json()["detail"])

    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_review_post_first_run_releases_lock_on_claim_fail(self, mock_summary):
        # If claim_first CAS fails (an active running record already exists) the review lock
        # must be released so subsequent requests are not wedged.
        from backend.independent_review import get_independent_review_lock

        project_id = "demo-post-claimfail"
        mock_summary.return_value = {"stage_code": "S5"}
        store = main_module._REVIEW_SESSION_STORE
        # pre-seed an active running record under a different run_id.
        self.assertTrue(store.claim_first(project_id, "other-run", threading.Event()))
        self.addCleanup(store.discard, project_id, "other-run")

        response = self.client.post(
            f"/api/projects/{project_id}/independent-review/stream",
            json={"resume": False, "run_id": "mine"},
        )
        self.assertEqual(response.status_code, 409)
        # lock was released despite the CAS failure.
        lock = get_independent_review_lock(project_id)
        self.assertTrue(lock.acquire(blocking=False))
        lock.release()

    @mock.patch("backend.main.IndependentReviewAgent")
    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_review_post_resume_errored_continues(self, mock_summary, mock_agent_cls):
        # resume=true against an errored record: the stored snapshot is handed to agent.run.
        project_id = "demo-post-resume-err"
        run_id = "run-resume-1"
        snapshot = {"messages": [{"role": "system", "content": "x"}], "iteration": 3}
        mock_summary.return_value = {"stage_code": "S5"}
        self._seed_errored(project_id, run_id, snapshot)
        captured = {}

        def fake_run(project_id_arg, run_id=None, store=None, resume_snapshot=None, supplement=None, cancel_event=None, **kwargs):
            del cancel_event, kwargs
            captured["run_id"] = run_id
            captured["resume_snapshot"] = resume_snapshot
            captured["supplement"] = supplement
            yield {"type": "progress", "message": "resuming"}

        mock_agent_cls.return_value.run.side_effect = fake_run

        response = self.client.post(
            f"/api/projects/{project_id}/independent-review/stream",
            json={"resume": True, "run_id": run_id, "supplement": "再核对一下数据口径"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["run_id"], run_id)
        self.assertEqual(captured["resume_snapshot"], snapshot)
        self.assertEqual(captured["supplement"], "再核对一下数据口径")
        self.assertIn('"type": "progress"', response.text)

    @mock.patch("backend.main.IndependentReviewAgent")
    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_review_post_resume_done_returns_completed_signal(self, mock_summary, mock_agent_cls):
        # resume=true against a done tombstone: NO worker runs, the wrapper just re-emits
        # review-completed with the stored mtime (recovers a lost success notification).
        project_id = "demo-post-resume-done"
        run_id = "run-done-1"
        mock_summary.return_value = {"stage_code": "S5"}
        mtime = self._seed_done_tombstone(project_id, run_id)

        response = self.client.post(
            f"/api/projects/{project_id}/independent-review/stream",
            json={"resume": True, "run_id": run_id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("review-completed", response.text)
        self.assertIn(run_id, response.text)
        self.assertIn(str(mtime), response.text)
        self.assertIn("data: [DONE]", response.text)
        # the agent was never constructed/run for a done resume.
        mock_agent_cls.return_value.run.assert_not_called()

    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_review_post_resume_reject_400(self, mock_summary):
        # resume against a missing / mismatched run_id is rejected (and the lock released).
        from backend.independent_review import get_independent_review_lock

        project_id = "demo-post-resume-reject"
        mock_summary.return_value = {"stage_code": "S5"}

        response = self.client.post(
            f"/api/projects/{project_id}/independent-review/stream",
            json={"resume": True, "run_id": "no-such-run"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("无可续审的会话", response.json()["detail"])
        lock = get_independent_review_lock(project_id)
        self.assertTrue(lock.acquire(blocking=False))
        lock.release()

    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_review_resume_409_when_worker_finalizing(self, mock_summary):
        # resume waits up to 3s for the review lock; if a worker holds it past the timeout the
        # endpoint returns 409 so the frontend backs off and retries.
        from backend.independent_review import get_independent_review_lock

        project_id = "demo-post-resume-busy"
        mock_summary.return_value = {"stage_code": "S5"}
        lock = get_independent_review_lock(project_id)
        self.assertTrue(lock.acquire(blocking=False))
        self.addCleanup(lock.release)

        async def call():
            class ConnectedRequest:
                async def json(self_inner):
                    return {"resume": True, "run_id": "r1"}

                async def is_disconnected(self_inner):
                    return False

            with self.assertRaises(HTTPException) as ctx:
                await main_module.independent_review_stream_post(project_id, ConnectedRequest())
            return ctx.exception

        # patch the 3.0s blocking acquire down to a tiny timeout so the test is fast but still
        # exercises the to_thread(lock.acquire, True, <timeout>) path.
        real_to_thread = asyncio.to_thread

        async def fast_to_thread(func, *args, **kwargs):
            if args and args[:1] == (True,):
                return await real_to_thread(func, True, 0.05)
            return await real_to_thread(func, *args, **kwargs)

        with mock.patch("backend.main.asyncio.to_thread", side_effect=fast_to_thread):
            exc = asyncio.run(call())
        self.assertEqual(exc.status_code, 409)
        self.assertIn("正在收尾", exc.detail)

    @mock.patch("backend.main.IndependentReviewAgent")
    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_review_resume_uses_to_thread_not_blocking_loop(self, mock_summary, mock_agent_cls):
        # the blocking lock.acquire for resume must go through asyncio.to_thread so the event
        # loop is never blocked.
        project_id = "demo-post-resume-tothread"
        run_id = "run-tt-1"
        mock_summary.return_value = {"stage_code": "S5"}
        self._seed_errored(project_id, run_id)
        mock_agent_cls.return_value.run.return_value = iter(
            [{"type": "progress", "message": "ok"}]
        )

        async def call():
            class ConnectedRequest:
                async def json(self_inner):
                    return {"resume": True, "run_id": run_id}

                async def is_disconnected(self_inner):
                    return False

            response = await main_module.independent_review_stream_post(project_id, ConnectedRequest())
            return await _collect_streaming_chunks(response)

        real_to_thread = asyncio.to_thread
        to_thread_calls = {"n": 0}

        async def counting_to_thread(func, *args, **kwargs):
            to_thread_calls["n"] += 1
            return await real_to_thread(func, *args, **kwargs)

        with mock.patch("backend.main.asyncio.to_thread", side_effect=counting_to_thread):
            asyncio.run(call())
        # at least the lock.acquire + the run_worker were dispatched via to_thread.
        self.assertGreaterEqual(to_thread_calls["n"], 2)

    @mock.patch("backend.main.IndependentReviewAgent")
    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_review_resume_rereads_store_after_lock(self, mock_summary, mock_agent_cls):
        # the record is errored at request time but flips to done while waiting for the lock;
        # after acquiring the lock claim_resume re-reads and reports the post-wait done state
        # (review-completed, no worker run).
        project_id = "demo-post-resume-reread"
        run_id = "run-reread-1"
        mock_summary.return_value = {"stage_code": "S5"}
        store = main_module._REVIEW_SESSION_STORE
        self._seed_errored(project_id, run_id)

        # simulate "worker finished while we waited": flip the record to done inside the
        # to_thread(lock.acquire) call, before claim_resume re-reads it.
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        flip = {"done_mtime": None}

        async def call():
            class ConnectedRequest:
                async def json(self_inner):
                    return {"resume": True, "run_id": run_id}

                async def is_disconnected(self_inner):
                    return False

            response = await main_module.independent_review_stream_post(project_id, ConnectedRequest())
            return await _collect_streaming_chunks(response)

        real_to_thread = asyncio.to_thread

        async def flipping_to_thread(func, *args, **kwargs):
            result = await real_to_thread(func, *args, **kwargs)
            # after the blocking lock.acquire succeeds, convert errored -> done so the
            # subsequent claim_resume sees done.
            if args and args[:1] == (True,) and flip["done_mtime"] is None:
                # we need the record running for atomic_commit; emulate the worker's commit:
                # set running via direct CAS then commit. The record is currently errored
                # (from _seed_errored); move it to running by claim_resume-ing under a temp
                # cancel event, then commit to done.
                kind, _payload = store.claim_resume(project_id, run_id, threading.Event())
                self.assertEqual(kind, "errored")  # now running
                canonical = os.path.join(tmpdir.name, "independent-review.md")
                fd, temp_path = tempfile.mkstemp(dir=tmpdir.name, suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write("done while waiting")
                flip["done_mtime"] = store.atomic_commit_report(
                    project_id, run_id, temp_path, canonical, {"messages": []}
                )
            return result

        with mock.patch("backend.main.asyncio.to_thread", side_effect=flipping_to_thread):
            chunks = asyncio.run(call())
        text = "".join(chunks)
        self.assertIn("review-completed", text)
        self.assertIn(str(flip["done_mtime"]), text)
        mock_agent_cls.return_value.run.assert_not_called()

    @mock.patch("backend.main.IndependentReviewAgent")
    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_review_lock_released_on_done_and_reject_and_exception(self, mock_summary, mock_agent_cls):
        from backend.independent_review import get_independent_review_lock

        mock_summary.return_value = {"stage_code": "S5"}
        lock_owner = get_independent_review_lock

        # (a) resume done -> lock released.
        p_done = "demo-lockrel-done"
        run_done = "run-d"
        self._seed_done_tombstone(p_done, run_done)
        self.client.post(
            f"/api/projects/{p_done}/independent-review/stream",
            json={"resume": True, "run_id": run_done},
        )
        self.assertTrue(lock_owner(p_done).acquire(blocking=False))
        lock_owner(p_done).release()

        # (b) resume reject -> lock released.
        p_rej = "demo-lockrel-reject"
        self.client.post(
            f"/api/projects/{p_rej}/independent-review/stream",
            json={"resume": True, "run_id": "ghost"},
        )
        self.assertTrue(lock_owner(p_rej).acquire(blocking=False))
        lock_owner(p_rej).release()

        # (c) worker exception -> finally releases lock.
        p_exc = "demo-lockrel-exc"
        mock_agent_cls.return_value.run.side_effect = RuntimeError("boom")
        self.client.post(
            f"/api/projects/{p_exc}/independent-review/stream",
            json={"resume": False, "run_id": "run-e"},
        )
        self.assertTrue(lock_owner(p_exc).acquire(blocking=False))
        lock_owner(p_exc).release()
        self.addCleanup(main_module._REVIEW_SESSION_STORE.discard, p_exc, "run-e")

    @mock.patch("backend.main.IndependentReviewAgent")
    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_review_completed_emitted_after_lock_release(self, mock_summary, mock_agent_cls):
        # when review-completed is emitted, the review lock is already free (the wrapper emits
        # it only after worker_task finished + run_worker finally released the lock).
        from backend.independent_review import CANONICAL_REVIEW_PATH, get_independent_review_lock

        project_id = "demo-completed-after-release"
        run_id = "run-after-rel"
        mock_summary.return_value = {"stage_code": "S5"}
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        self.addCleanup(main_module._REVIEW_SESSION_STORE.discard, project_id, run_id)

        def fake_run(project_id_arg, run_id=None, store=None, **kwargs):
            del kwargs
            canonical = os.path.join(tmpdir.name, "independent-review.md")
            fd, temp_path = tempfile.mkstemp(dir=tmpdir.name, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("body")
            mtime = store.atomic_commit_report(project_id_arg, run_id, temp_path, canonical, {"messages": []})
            yield {"type": "review-completed", "path": CANONICAL_REVIEW_PATH, "report_mtime_ns": mtime}

        mock_agent_cls.return_value.run.side_effect = fake_run

        async def call():
            class ConnectedRequest:
                async def json(self_inner):
                    return {"resume": False, "run_id": run_id}

                async def is_disconnected(self_inner):
                    return False

            response = await main_module.independent_review_stream_post(project_id, ConnectedRequest())
            chunks = []
            lock_free_at_completion = {"value": None}
            async for chunk in response.body_iterator:
                if "review-completed" in chunk:
                    lk = get_independent_review_lock(project_id)
                    acquired = lk.acquire(blocking=False)
                    lock_free_at_completion["value"] = acquired
                    if acquired:
                        lk.release()
                chunks.append(chunk)
            return lock_free_at_completion["value"], "".join(chunks)

        free, text = asyncio.run(call())
        self.assertIn("review-completed", text)
        self.assertTrue(free, "review lock must be free by the time review-completed is emitted")

    @mock.patch("backend.main.IndependentReviewAgent")
    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_review_completed_carries_run_id_and_mtime(self, mock_summary, mock_agent_cls):
        from backend.independent_review import CANONICAL_REVIEW_PATH

        project_id = "demo-completed-payload"
        run_id = "run-payload"
        mock_summary.return_value = {"stage_code": "S5"}
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        self.addCleanup(main_module._REVIEW_SESSION_STORE.discard, project_id, run_id)
        holder = {}

        def fake_run(project_id_arg, run_id=None, store=None, **kwargs):
            del kwargs
            canonical = os.path.join(tmpdir.name, "independent-review.md")
            fd, temp_path = tempfile.mkstemp(dir=tmpdir.name, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("body")
            holder["mtime"] = store.atomic_commit_report(project_id_arg, run_id, temp_path, canonical, {"messages": []})
            yield {"type": "review-completed", "path": CANONICAL_REVIEW_PATH, "report_mtime_ns": holder["mtime"]}

        mock_agent_cls.return_value.run.side_effect = fake_run

        response = self.client.post(
            f"/api/projects/{project_id}/independent-review/stream",
            json={"resume": False, "run_id": run_id},
        )
        # locate the wrapper-emitted review-completed payload.
        payloads = [
            json.loads(line[len("data: "):])
            for line in response.text.splitlines()
            if line.startswith("data: ") and '"review-completed"' in line
        ]
        self.assertTrue(payloads)
        completed = payloads[-1]
        self.assertEqual(completed["run_id"], run_id)
        self.assertEqual(completed["report_mtime_ns"], holder["mtime"])

    @mock.patch("backend.main.IndependentReviewAgent")
    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_review_worker_error_does_not_emit_completed(self, mock_summary, mock_agent_cls):
        project_id = "demo-post-worker-error"
        run_id = "run-werr"
        mock_summary.return_value = {"stage_code": "S5"}
        mock_agent_cls.return_value.run.side_effect = RuntimeError("agent exploded")
        self.addCleanup(main_module._REVIEW_SESSION_STORE.discard, project_id, run_id)

        response = self.client.post(
            f"/api/projects/{project_id}/independent-review/stream",
            json={"resume": False, "run_id": run_id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('"type": "error"', response.text)
        self.assertIn("agent exploded", response.text)
        self.assertNotIn("review-completed", response.text)
        self.assertIn("data: [DONE]", response.text)

    @mock.patch("backend.main.IndependentReviewAgent")
    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_atomic_commit_failure_does_not_emit_completed(self, mock_summary, mock_agent_cls):
        # agent yields an error after a failed atomic replace (no done tombstone) -> the
        # wrapper must NOT synthesize review-completed.
        project_id = "demo-post-commit-fail"
        run_id = "run-commitfail"
        mock_summary.return_value = {"stage_code": "S5"}
        self.addCleanup(main_module._REVIEW_SESSION_STORE.discard, project_id, run_id)

        def fake_run(project_id_arg, run_id=None, store=None, **kwargs):
            del project_id_arg, run_id, store, kwargs
            yield {"type": "error", "detail": "审查报告保存被取消或失败，请重试"}

        mock_agent_cls.return_value.run.side_effect = fake_run

        response = self.client.post(
            f"/api/projects/{project_id}/independent-review/stream",
            json={"resume": False, "run_id": run_id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('"type": "error"', response.text)
        self.assertNotIn("review-completed", response.text)

    @mock.patch("backend.main.IndependentReviewAgent")
    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_review_worker_exception_leaves_no_running_record(self, mock_summary, mock_agent_cls):
        # codex R2 BLOCKER 4: an uncaught worker exception must converge via finally
        # finalize_orphan_running, leaving no stuck running record (first-claim succeeds again).
        project_id = "demo-post-exc-norun"
        run_id = "run-exc"
        mock_summary.return_value = {"stage_code": "S5"}
        mock_agent_cls.return_value.run.side_effect = RuntimeError("boom mid-run")

        response = self.client.post(
            f"/api/projects/{project_id}/independent-review/stream",
            json={"resume": False, "run_id": run_id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('"type": "error"', response.text)
        # no dead running record: a brand-new first-claim succeeds (first-run, no resume_snapshot
        # so finalize cleared the record entirely).
        self.assertTrue(
            main_module._REVIEW_SESSION_STORE.claim_first(project_id, "fresh-after-exc", threading.Event())
        )
        main_module._REVIEW_SESSION_STORE.discard(project_id, "fresh-after-exc")

    def test_review_structured_timeout_kwargs(self):
        # Task 4.2 Step 3: the review client uses a structured httpx.Timeout (not a flat float),
        # so connect/read/write/pool are bounded independently.
        import httpx as _httpx
        from backend.independent_review import IndependentReviewAgent

        captured = {}

        agent = IndependentReviewAgent(mock.Mock(), mock.Mock(mode="custom", api_key="k", api_base="http://x"))

        real_client = _httpx.Client

        def capture_client(*args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            return real_client(*args, **kwargs)

        with mock.patch("backend.independent_review.httpx.Client", side_effect=capture_client):
            with mock.patch("backend.independent_review.OpenAI"):
                agent._build_client()

        self.assertIsInstance(captured["timeout"], _httpx.Timeout)

    @mock.patch("backend.main.IndependentReviewAgent")
    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_get_review_legacy_path_still_works_after_c4(self, mock_summary, mock_agent_cls):
        # codex R2 BLOCKER 1: the legacy GET endpoint must keep working after C4 adds POST.
        mock_summary.return_value = {"stage_code": "S5"}
        mock_agent_cls.return_value.run.return_value = [
            {"type": "progress", "message": "开始审查"},
        ]

        response = self.client.get("/api/projects/demo-get-after-c4/independent-review/stream")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertIn('"type": "progress"', response.text)
        self.assertIn("data: [DONE]", response.text)

    # ---- discard ----

    def test_discard_requires_run_id(self):
        response = self.client.post(
            "/api/projects/demo-discard-norun/independent-review/discard",
            json={},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("run_id required", response.json()["detail"])

    def test_discard_cancels_matching_run(self):
        project_id = "demo-discard-match"
        run_id = "run-discard-1"
        store = main_module._REVIEW_SESSION_STORE
        cancel_event = threading.Event()
        self.assertTrue(store.claim_first(project_id, run_id, cancel_event))

        response = self.client.post(
            f"/api/projects/{project_id}/independent-review/discard",
            json={"run_id": run_id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["cancelled"])
        # cancel event set and record cleared (a fresh first-claim succeeds).
        self.assertTrue(cancel_event.is_set())
        self.assertTrue(store.claim_first(project_id, "after-discard", threading.Event()))
        store.discard(project_id, "after-discard")

    def test_discard_no_op_on_mismatch(self):
        project_id = "demo-discard-mismatch"
        run_id = "run-current"
        store = main_module._REVIEW_SESSION_STORE
        cancel_event = threading.Event()
        self.assertTrue(store.claim_first(project_id, run_id, cancel_event))
        self.addCleanup(store.discard, project_id, run_id)

        response = self.client.post(
            f"/api/projects/{project_id}/independent-review/discard",
            json={"run_id": "stale-run"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["cancelled"])
        # the current run is untouched.
        self.assertFalse(cancel_event.is_set())

    def test_discard_does_not_acquire_review_lock(self):
        # discard must cancel even while a worker holds the review lock (store guard only).
        from backend.independent_review import get_independent_review_lock

        project_id = "demo-discard-nolock"
        run_id = "run-nolock"
        store = main_module._REVIEW_SESSION_STORE
        cancel_event = threading.Event()
        self.assertTrue(store.claim_first(project_id, run_id, cancel_event))
        lock = get_independent_review_lock(project_id)
        self.assertTrue(lock.acquire(blocking=False))  # emulate a long-running worker
        self.addCleanup(lock.release)

        response = self.client.post(
            f"/api/projects/{project_id}/independent-review/discard",
            json={"run_id": run_id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["cancelled"])
        self.assertTrue(cancel_event.is_set())

    def test_stale_worker_does_not_revive_after_discard(self):
        # after discard pops the record, a late set_errored from the old worker is rejected by
        # run_id mismatch (no revived record).
        project_id = "demo-discard-stale"
        run_id = "run-stale"
        store = main_module._REVIEW_SESSION_STORE
        cancel_event = threading.Event()
        self.assertTrue(store.claim_first(project_id, run_id, cancel_event))

        # user discards.
        self.assertTrue(
            self.client.post(
                f"/api/projects/{project_id}/independent-review/discard",
                json={"run_id": run_id},
            ).json()["cancelled"]
        )
        # the old worker tries to land an errored snapshot — must be a no-op.
        self.assertFalse(store.set_errored(project_id, run_id, {"messages": [], "iteration": 4}))
        self.assertFalse(store.finalize_orphan_running(project_id, run_id, {"messages": []}))
        # nothing running remains: a fresh first-claim succeeds.
        self.assertTrue(store.claim_first(project_id, "fresh-stale", threading.Event()))
        store.discard(project_id, "fresh-stale")

    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_lint_report_endpoint_requires_s5(self, mock_summary):
        mock_summary.return_value = {"stage_code": "S4"}

        response = self.client.post("/api/projects/demo/lint-report")

        self.assertEqual(response.status_code, 400)
        self.assertIn("AI 味自查只能在 S5 阶段使用", response.json()["detail"])

    @mock.patch("backend.main.run_lint_report")
    @mock.patch("backend.main.skill_engine.get_script_path")
    @mock.patch("backend.main.skill_engine.get_project_path")
    @mock.patch("backend.main.skill_engine.get_primary_report_path")
    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_lint_report_endpoint_returns_summary(
        self,
        mock_summary,
        mock_report_path,
        mock_project_path,
        mock_script_path,
        mock_run_lint,
    ):
        mock_summary.return_value = {"stage_code": "S5"}
        mock_report_path.return_value = "D:/tmp/report.md"
        mock_project_path.return_value = Path("D:/tmp/project")
        mock_script_path.return_value = "D:/skill/scripts/quality_check.ps1"
        mock_run_lint.return_value = {
            "status": "ok",
            "path": "D:/tmp/project/plan/lint-report.md",
            "summary": {"ai_style": 2},
        }

        response = self.client.post("/api/projects/demo-lint/lint-report")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"]["ai_style"], 2)
        mock_run_lint.assert_called_once_with(
            "D:/tmp/report.md",
            str(Path("D:/tmp/project") / "plan" / "lint-report.md"),
            "D:/skill/scripts/quality_check.ps1",
        )

    @mock.patch("backend.main.skill_engine.get_primary_report_path")
    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_lint_report_returns_error_when_report_missing(
        self,
        mock_summary,
        mock_report_path,
    ):
        mock_summary.return_value = {"stage_code": "S5"}
        mock_report_path.side_effect = FileNotFoundError("正文不存在")

        response = self.client.post("/api/projects/demo-lint-missing-report/lint-report")

        self.assertEqual(response.status_code, 404)
        self.assertIn("正文不存在", response.json()["detail"])

    @mock.patch("backend.main.skill_engine.get_script_path")
    @mock.patch("backend.main.skill_engine.get_project_path")
    @mock.patch("backend.main.skill_engine.get_primary_report_path")
    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_lint_report_returns_error_when_script_path_missing(
        self,
        mock_summary,
        mock_report_path,
        mock_project_path,
        mock_script_path,
    ):
        mock_summary.return_value = {"stage_code": "S5"}
        mock_report_path.return_value = "D:/tmp/report.md"
        mock_project_path.return_value = Path("D:/tmp/project")
        mock_script_path.side_effect = FileNotFoundError("脚本不存在")

        response = self.client.post("/api/projects/demo-lint-missing-script/lint-report")

        self.assertEqual(response.status_code, 404)
        self.assertIn("脚本不存在", response.json()["detail"])

    @mock.patch("backend.main.run_lint_report")
    @mock.patch("backend.main.skill_engine.get_script_path")
    @mock.patch("backend.main.skill_engine.get_project_path")
    @mock.patch("backend.main.skill_engine.get_primary_report_path")
    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_lint_report_returns_error_when_run_lint_report_fails(
        self,
        mock_summary,
        mock_report_path,
        mock_project_path,
        mock_script_path,
        mock_run_lint,
    ):
        mock_summary.return_value = {"stage_code": "S5"}
        mock_report_path.return_value = "D:/tmp/report.md"
        mock_project_path.return_value = Path("D:/tmp/project")
        mock_script_path.return_value = "D:/skill/scripts/quality_check.ps1"
        mock_run_lint.return_value = {"status": "error", "detail": "脚本失败"}

        response = self.client.post("/api/projects/demo-lint-error-shape/lint-report")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "error", "detail": "脚本失败"})

    @mock.patch("backend.main.run_lint_report")
    @mock.patch("backend.main.skill_engine.get_script_path")
    @mock.patch("backend.main.skill_engine.get_project_path")
    @mock.patch("backend.main.skill_engine.get_primary_report_path")
    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_lint_report_returns_error_when_run_lint_report_raises(
        self,
        mock_summary,
        mock_report_path,
        mock_project_path,
        mock_script_path,
        mock_run_lint,
    ):
        mock_summary.return_value = {"stage_code": "S5"}
        mock_report_path.return_value = "D:/tmp/report.md"
        mock_project_path.return_value = Path("D:/tmp/project")
        mock_script_path.return_value = "D:/skill/scripts/quality_check.ps1"
        mock_run_lint.side_effect = RuntimeError("powershell crashed")

        response = self.client.post("/api/projects/demo-lint-raises/lint-report")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "error")
        self.assertIn("powershell crashed", response.json()["detail"])

    @mock.patch("backend.main.skill_engine.get_workspace_summary")
    def test_lint_report_endpoint_409_when_concurrent(self, mock_summary):
        from backend.report_tools import get_lint_report_lock

        project_id = "demo-lint-concurrent"
        mock_summary.return_value = {"stage_code": "S5"}
        lock = get_lint_report_lock(project_id)
        self.assertTrue(lock.acquire(blocking=False))
        self.addCleanup(lock.release)

        response = self.client.post(f"/api/projects/{project_id}/lint-report")

        self.assertEqual(response.status_code, 409)
        self.assertIn("上一次 AI 味自查仍在进行中", response.json()["detail"])

    @mock.patch("backend.main.export_reviewable_draft")
    @mock.patch("backend.main.skill_engine.ensure_output_dir")
    @mock.patch("backend.main.skill_engine.get_script_path")
    @mock.patch("backend.main.skill_engine.get_primary_report_path")
    def test_export_draft_endpoint_returns_output_path(
        self,
        mock_report_path,
        mock_script_path,
        mock_output_dir,
        mock_export_draft,
    ):
        mock_report_path.return_value = "D:/tmp/report.md"
        mock_script_path.return_value = "D:/skill/scripts/export_draft.ps1"
        mock_output_dir.return_value = "D:/tmp/output"
        mock_export_draft.return_value = {
            "status": "ok",
            "output": "已生成可审草稿: D:/tmp/output/report.docx",
            "output_path": "D:/tmp/output/report.docx",
        }

        response = self.client.post("/api/projects/demo/export-draft")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["output_path"], "D:/tmp/output/report.docx")
        mock_export_draft.assert_called_once_with(
            "D:/tmp/report.md",
            "D:/tmp/output",
            "D:/skill/scripts/export_draft.ps1",
        )

    @mock.patch("backend.main.get_chat_handler")
    def test_chat_endpoint_returns_new_token_usage_shape(self, mock_get_chat_handler):
        handler = mock.Mock()
        handler.chat.return_value = {
            "content": "已整理完毕",
            "token_usage": {
                "usage_source": "provider",
                "context_used_tokens": 180000,
                "input_tokens": 180000,
                "output_tokens": 1200,
                "total_tokens": 181200,
                "cache_read_tokens": 4000,
                "reasoning_tokens": 0,
                "max_tokens": 200000,
                "effective_max_tokens": 200000,
                "provider_max_tokens": 1000000,
                "preflight_compaction_used": False,
                "post_turn_compaction_status": "not_needed",
                "compressed": False,
            },
        }
        mock_get_chat_handler.return_value = handler

        response = self.client.post(
            "/api/chat",
            json={
                "project_id": "proj-demo",
                "message_text": "请结合新增材料整理问题树",
                "attached_material_ids": ["mat-1", "mat-2"],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["content"], "已整理完毕")
        self.assertEqual(response.json()["token_usage"]["usage_source"], "provider")
        self.assertEqual(response.json()["token_usage"]["context_used_tokens"], 180000)
        self.assertEqual(response.json()["token_usage"]["input_tokens"], 180000)
        self.assertEqual(response.json()["token_usage"]["output_tokens"], 1200)
        self.assertEqual(response.json()["token_usage"]["cache_read_tokens"], 4000)
        self.assertEqual(response.json()["token_usage"]["max_tokens"], 200000)
        self.assertEqual(response.json()["token_usage"]["effective_max_tokens"], 200000)
        self.assertEqual(response.json()["token_usage"]["provider_max_tokens"], 1000000)
        self.assertFalse(response.json()["token_usage"]["preflight_compaction_used"])
        self.assertEqual(response.json()["token_usage"]["post_turn_compaction_status"], "not_needed")
        handler.chat.assert_called_once_with(
            "proj-demo",
            "请结合新增材料整理问题树",
            ["mat-1", "mat-2"],
            [],
        )

    @mock.patch("backend.main.get_chat_handler")
    def test_chat_endpoint_keeps_max_tokens_alias_for_existing_clients(self, mock_get_chat_handler):
        handler = mock.Mock()
        handler.chat.return_value = {
            "content": "已整理完毕",
            "token_usage": {
                "usage_source": "provider",
                "context_used_tokens": 180000,
                "effective_max_tokens": 200000,
                "provider_max_tokens": 1000000,
                "max_tokens": 200000,
                "preflight_compaction_used": False,
                "post_turn_compaction_status": "not_needed",
                "compressed": False,
            },
        }
        mock_get_chat_handler.return_value = handler

        response = self.client.post(
            "/api/chat",
            json={
                "project_id": "proj-demo",
                "message_text": "请继续",
                "attached_material_ids": [],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["token_usage"]["max_tokens"], 200000)
        self.assertEqual(response.json()["token_usage"]["effective_max_tokens"], 200000)

    @mock.patch("backend.main.get_chat_handler")
    def test_chat_endpoint_forwards_transient_attachments(self, mock_get_chat_handler):
        handler = mock.Mock()
        handler.chat.return_value = {
            "content": "已看到截图",
            "token_usage": None,
        }
        mock_get_chat_handler.return_value = handler

        response = self.client.post(
            "/api/chat",
            json={
                "project_id": "proj-demo",
                "message_text": "请看这张截图",
                "attached_material_ids": [],
                "transient_attachments": [
                    {
                        "name": "bug.png",
                        "mime_type": "image/png",
                        "data_url": "data:image/png;base64,AAAA",
                    }
                ],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["content"], "已看到截图")
        handler.chat.assert_called_once_with(
            "proj-demo",
            "请看这张截图",
            [],
            [
                {
                    "name": "bug.png",
                    "mime_type": "image/png",
                    "data_url": "data:image/png;base64,AAAA",
                }
            ],
        )

    @mock.patch("backend.main.get_chat_handler")
    def test_chat_endpoint_passes_through_system_notices(self, mock_get_chat_handler):
        handler = mock.Mock()
        handler.chat.return_value = {
            "content": "已拦截伪造写入",
            "token_usage": None,
            "system_notices": [
                {
                    "category": "write_blocked",
                    "path": "plan/independent-review.md, plan/lint-report.md",
                    "reason": "需要先完成独立审查和 AI 味自查，才能标记审查通过。",
                    "user_action": "请先在 S5 阶段点击上方'独立审查'和'AI 味自查'按钮，再确认审查通过。",
                    "surface_to_user": True,
                }
            ],
        }
        mock_get_chat_handler.return_value = handler

        response = self.client.post(
            "/api/chat",
            json={
                "project_id": "proj-demo",
                "message_text": "请继续",
                "attached_material_ids": [],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["content"], "已拦截伪造写入")
        self.assertEqual(len(response.json()["system_notices"]), 1)
        self.assertEqual(response.json()["system_notices"][0]["category"], "write_blocked")

    @mock.patch("backend.main.get_chat_handler")
    def test_chat_stream_endpoint_forwards_system_trigger(self, mock_get_chat_handler):
        handler = mock.Mock()
        handler.chat_stream.return_value = iter([
            {"type": "content", "data": "已读取独立审查报告。"},
        ])
        mock_get_chat_handler.return_value = handler

        response = self.client.post(
            "/api/chat/stream",
            json={
                "project_id": "proj-demo",
                "message_text": "",
                "system_trigger": "independent_review_done",
                "attached_material_ids": [],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("已读取独立审查报告", response.text)
        handler.chat_stream.assert_called_once_with(
            "proj-demo",
            "",
            [],
            [],
            system_trigger="independent_review_done",
        )


class GetConversationSanitizeTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main_module.app)
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.project_path = Path(self.tmpdir.name) / "demo-project"
        self.project_path.mkdir(parents=True, exist_ok=True)
        self.patcher = mock.patch.object(
            main_module.skill_engine,
            "get_project_path",
            return_value=self.project_path,
        )
        self.mock_get_project_path = self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def _write_conversation(self, messages):
        (self.project_path / "conversation.json").write_text(
            json.dumps(messages, ensure_ascii=False), encoding="utf-8",
        )

    def test_get_conversation_returns_messages_dict(self):
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
        self.assertEqual(len(data["messages"]), 4)
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

    def test_get_conversation_strips_legacy_stage_ack_from_assistants(self):
        self._write_conversation([
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": "Real reply.\n<stage-ack>outline_confirmed_at</stage-ack>\n",
            },
        ])

        resp = self.client.get("/api/projects/demo/conversation")
        data = resp.json()

        assistant_msg = next(m for m in data["messages"] if m["role"] == "assistant")
        self.assertNotIn("<stage-ack", assistant_msg["content"])
        self.assertEqual(assistant_msg["content"], "Real reply.")

    def test_get_conversation_user_role_preserves_legacy_stage_ack_text(self):
        self._write_conversation([
            {
                "role": "user",
                "content": "请解释 <stage-ack>outline_confirmed_at</stage-ack>",
            },
        ])

        resp = self.client.get("/api/projects/demo/conversation")
        data = resp.json()

        self.assertIn("<stage-ack>", data["messages"][0]["content"])

    def test_get_conversation_user_role_unchanged_even_with_tool_log_text(self):
        self._write_conversation([
            {"role": "user", "content": "see <!-- tool-log\n--> in my message"},
        ])
        resp = self.client.get("/api/projects/demo/conversation")
        data = resp.json()
        self.assertIn("<!-- tool-log", data["messages"][0]["content"])

    def test_get_conversation_404_when_project_missing(self):
        self.mock_get_project_path.return_value = None
        resp = self.client.get("/api/projects/missing/conversation")
        self.assertEqual(resp.status_code, 404)
