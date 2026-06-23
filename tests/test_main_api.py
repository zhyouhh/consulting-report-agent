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
from backend.skill import SkillEngine
from backend.tenant import tenant_project_key
from tests.test_auth_api import AuthApiTestBase


async def _collect_streaming_chunks(response):
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    return chunks


# ---------------------------------------------------------------------------
# W2-B 多租户（T11c）：端点测试迁移辅助
#
# T11a/T11b 把全部 {project_id} 端点改成租户作用域：每个请求经 get_current_uid 取 uid，
# 再 require_project(pid, uid) → get_skill_engine(uid).get_project_record(pid) 解析归属。
# 全局 backend.main.skill_engine 已删除，旧测试的 @mock.patch("backend.main.skill_engine.X")
# 在 patch 期就炸。auth_required=False 时 get_current_uid 返回 "local"（单用户语义），
# 这正是迁移后端点测试应跑的方式。
#
# 下面两个工具把「单租户 local 引擎」装进 get_skill_engine 的缓存：
#   _LocalMockEngineMixin —— 装一个 MagicMock(spec=SkillEngine)，echo get_project_record
#       使 require_project 把 URL 里的 pid 直接当 canonical id（scope.project_id == URL pid），
#       供原先 @mock.patch decorator 类逐 test 配置具体方法 + 断言。
#   _install_local_engine(test, engine) —— 把传入的真 SkillEngine 装进 _engines["local"]、
#       关 auth 并在 cleanup 还原（原先 mock.patch.object(main, "skill_engine", eng) 的真引擎类用），
#       require_project 自然解析真项目、天然隔离。
# ---------------------------------------------------------------------------


def _install_local_engine(engine):
    """把 engine 作为 'local' 租户引擎装进 get_skill_engine 缓存。返回 cleanup 闭包。
    cleanup 还原 auth_required（默认 True），避免「auth off」泄漏到不 reload main 的其它测试文件。"""
    prev_auth = getattr(main_module.app.state, "auth_required", True)
    main_module.app.state.auth_required = False
    with main_module._engines_guard:
        previous = main_module._engines.get("local")
        main_module._engines["local"] = engine

    def _cleanup():
        main_module.app.state.auth_required = prev_auth
        with main_module._engines_guard:
            if previous is None:
                main_module._engines.pop("local", None)
            else:
                main_module._engines["local"] = previous

    return _cleanup


class _LocalMockEngineMixin:
    """auth off + 装一个 MagicMock 引擎当 local 租户，echo get_project_record。"""

    def _install_mock_engine(self):
        engine = mock.MagicMock(spec=SkillEngine)
        # echo：require_project 把 URL pid 直接 canonical 化（scope.project_id == URL pid）。
        engine.get_project_record.side_effect = lambda pid: {"id": pid, "name": pid}
        self.addCleanup(_install_local_engine(engine))
        return engine


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

    def test_chat_request_rejects_int_report_mtime_ns(self):
        # codex C4-spec NIT 4: lock the actual behavior for a raw int report_mtime_ns. pydantic
        # v2 REJECTS it (no silent int->str coercion in lax mode for str fields), which is the
        # safe outcome — a bare int would overflow JS Number.MAX_SAFE_INTEGER (2^53) on the wire.
        # If this ever starts silently coercing, this test fails and flags the JS-precision hazard.
        with self.assertRaises(ValidationError):
            ChatRequest.model_validate(
                {
                    "project_id": "demo",
                    "message_text": "",
                    "system_trigger": "independent_review_done",
                    "report_mtime_ns": 1760000000123456789,
                }
            )


class CheckpointTableInvariantTests(unittest.TestCase):
    def test_checkpoint_tables_key_sets_are_aligned(self):
        from backend.main import _CHECKPOINT_ROUTES
        from backend.skill import SkillEngine

        engine_keys = set(SkillEngine.STAGE_CHECKPOINT_KEYS)
        cascade_keys = set(SkillEngine._CASCADE_ORDER)
        route_keys = set(_CHECKPOINT_ROUTES.values())

        self.assertEqual(engine_keys, cascade_keys, "STAGE_CHECKPOINT_KEYS vs _CASCADE_ORDER")
        self.assertEqual(engine_keys, route_keys, "STAGE_CHECKPOINT_KEYS vs _CHECKPOINT_ROUTES values")


class CheckpointEndpointTests(_LocalMockEngineMixin, unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main_module.app)
        self.engine = self._install_mock_engine()
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

    def test_checkpoint_set_delegates_to_public_service(self):
        mock_record = self.engine.record_stage_checkpoint
        mock_record.return_value = {"status": "ok", "key": "outline_confirmed_at", "timestamp": "2026-04-17T12:00:00"}
        r = self.client.post("/api/projects/demo/checkpoints/outline-confirmed")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["timestamp"], "2026-04-17T12:00:00")
        mock_record.assert_called_once_with("demo", "outline_confirmed_at", "set")

    def test_checkpoint_clear_passes_clear_action(self):
        mock_record = self.engine.record_stage_checkpoint
        mock_record.return_value = {"status": "ok", "key": "outline_confirmed_at", "cleared": True}
        r = self.client.post("/api/projects/demo/checkpoints/outline-confirmed?action=clear")
        self.assertEqual(r.status_code, 200)
        mock_record.assert_called_once_with("demo", "outline_confirmed_at", "clear")

    def test_unknown_checkpoint_returns_404(self):
        r = self.client.post("/api/projects/demo/checkpoints/not-a-real-one")
        self.assertEqual(r.status_code, 404)

    def test_missing_project_returns_404(self):
        # require_project echoes a record (project resolves); the engine method then raises
        # ValueError("项目不存在") which the endpoint maps to 404.
        self.engine.record_stage_checkpoint.side_effect = ValueError("项目不存在: demo")
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
            # install the real engine as the 'local' tenant so require_project resolves the real
            # project and the endpoint exercises the real prerequisite gate.
            cleanup = _install_local_engine(engine)
            try:
                response = self.client.post("/api/projects/demo/checkpoints/review-started")
            finally:
                cleanup()

        self.assertEqual(response.status_code, 400)
        self.assertIn("data-log", response.json()["detail"])


class S0CheckpointEndpointTests(_LocalMockEngineMixin, unittest.TestCase):
    def setUp(self):
        self.main_module = main_module
        self.client = TestClient(main_module.app)
        # W2-B: install a MagicMock engine as the 'local' tenant (auth off) instead of the
        # removed global skill_engine singleton.
        self.mock_engine = self._install_mock_engine()
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


class WorkspaceApiTests(_LocalMockEngineMixin, unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main_module.app)
        self.engine = self._install_mock_engine()
        main_module.register_desktop_bridge(None)

    def tearDown(self):
        main_module.register_desktop_bridge(None)

    def test_workspace_endpoint_returns_stage_summary(self):
        self.engine.get_workspace_summary.return_value = {
            "stage_code": "S4",
            "status": "进行中",
            "completed_items": ["报告结构确定"],
            "next_actions": ["图表制作完成"],
        }

        response = self.client.get("/api/projects/demo/workspace")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["stage_code"], "S4")

    def test_workspace_endpoint_returns_404_for_missing_project(self):
        # require_project resolves via get_project_record; None → 404 (project not found).
        self.engine.get_project_record.side_effect = lambda pid: None
        response = self.client.get("/api/projects/definitely-missing-project/workspace")
        self.assertEqual(response.status_code, 404)

    def test_workspace_summary_includes_new_review_flags(self):
        mock_summary = self.engine.get_workspace_summary
        mock_summary.return_value = {
            "stage_code": "S5",
            "status": "进行中",
            "completed_items": [],
            "next_actions": [],
            "flags": {
                "review_checklist_ready": True,
                "independent_review_ready": False,
            },
        }

        response = self.client.get("/api/projects/demo/workspace")

        self.assertEqual(response.status_code, 200)
        flags = response.json()["flags"]
        self.assertIn("review_checklist_ready", flags)
        self.assertIs(flags["independent_review_ready"], False)
        # N7: lint / dual-report flags are gone.
        self.assertNotIn("lint_report_ready", flags)
        self.assertNotIn("review_reports_ready", flags)

    def test_deleted_lint_endpoints_are_gone(self):
        # N7: the /quality-check and /lint-report POST routes were removed entirely.
        # Assert directly against the route table — a status-code check is too weak
        # (a live handler returning 404 for a missing project would also pass).
        import backend.main as main_module
        deleted = {
            ("/api/projects/{project_id}/quality-check", "POST"),
            ("/api/projects/{project_id}/lint-report", "POST"),
        }
        live = {
            (getattr(route, "path", None), method)
            for route in main_module.app.routes
            for method in (getattr(route, "methods", None) or ())
        }
        for path, method in deleted:
            self.assertNotIn((path, method), live, f"{method} {path} should be gone")

    def test_clear_conversation_removes_new_and_legacy_sidecars(self):
        mock_get_project_path = self.engine.get_project_path
        with self.subTest("remove conversation and both sidecars"):
            import tempfile
            from pathlib import Path

            with tempfile.TemporaryDirectory() as tmpdir:
                project_path = Path(tmpdir)
                (project_path / "conversation.json").write_text("[]", encoding="utf-8")
                (project_path / "conversation_state.json").write_text("{}", encoding="utf-8")
                (project_path / "conversation_compact_state.json").write_text("{}", encoding="utf-8")
                mock_get_project_path.return_value = project_path

                # the endpoint also builds a ChatHandler for the request lock; stub get_chat_handler
                # so the MagicMock engine isn't passed to a real ChatHandler.__init__.
                handler = mock.Mock()
                handler._get_project_request_lock.return_value = threading.Lock()
                with mock.patch("backend.main.get_chat_handler", return_value=handler):
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

            # clear_conversation now takes a ProjectScope (resolved by require_project). Build one
            # directly with the mock engine so we drive the handler/lock path under test.
            self.engine.get_project_path.return_value = project_path
            scope = main_module.ProjectScope(
                uid="local",
                project_id="proj-demo",
                engine=self.engine,
                project_record={"id": "proj-demo", "name": "proj-demo"},
                lock_key=tenant_project_key("local", "proj-demo"),
            )

            def run_clear():
                try:
                    result_holder["result"] = asyncio.run(main_module.clear_conversation(scope))
                finally:
                    finished.set()

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

    def test_create_project_accepts_theme_like_display_name_without_slugging(self):
        mock_create_project = self.engine.create_project
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

    def test_select_materials_from_workspace_uses_bridge_and_imports_selection(self):
        mock_get_project_record = self.engine.get_project_record
        mock_add_materials = self.engine.add_materials
        mock_get_project_record.side_effect = None
        mock_get_project_record.return_value = {
            "id": "proj-demo",
            "name": "proj-demo",
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

    def test_upload_materials_stages_files_before_importing(self):
        mock_get_project_record = self.engine.get_project_record
        mock_add_materials = self.engine.add_materials
        mock_get_project_record.side_effect = None
        mock_get_project_record.return_value = {
            "id": "proj-demo",
            "name": "proj-demo",
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

    @mock.patch("backend.main.MAX_HEAVY_MATERIAL_BYTES", 10)
    def test_upload_oversized_file_returns_413(self):
        """Upload endpoint rejects files exceeding MAX_HEAVY_MATERIAL_BYTES with HTTP 413."""
        self.engine.get_project_record.side_effect = None
        self.engine.get_project_record.return_value = {
            "id": "proj-demo",
            "name": "proj-demo",
            "workspace_dir": "D:/Workspaces/demo",
        }
        oversized_data = b"X" * 20  # 20 bytes > patched limit of 10 bytes

        response = self.client.post(
            "/api/projects/proj-demo/materials/upload",
            files=[("files", ("big.pdf", BytesIO(oversized_data), "application/pdf"))],
        )

        self.assertEqual(response.status_code, 413)
        self.assertIn("超过上传限制", response.json()["detail"])

    @mock.patch("backend.main.MAX_HEAVY_MATERIAL_BYTES", 10)
    def test_upload_oversized_file_not_persisted(self):
        """Rejected oversized upload must not leave a file in the temp directory."""
        self.engine.get_project_record.side_effect = None
        self.engine.get_project_record.return_value = {
            "id": "proj-demo",
            "name": "proj-demo",
            "workspace_dir": "D:/Workspaces/demo",
        }
        oversized_data = b"X" * 20

        with mock.patch("backend.main.tempfile.TemporaryDirectory") as mock_tmpdir:
            import tempfile as _tf
            real_tmpdir = _tf.mkdtemp()
            mock_tmpdir.return_value.__enter__ = mock.Mock(return_value=real_tmpdir)
            mock_tmpdir.return_value.__exit__ = mock.Mock(return_value=False)

            try:
                response = self.client.post(
                    "/api/projects/proj-demo/materials/upload",
                    files=[("files", ("big.pdf", BytesIO(oversized_data), "application/pdf"))],
                )
                self.assertEqual(response.status_code, 413)
                # Partial temp file must be cleaned up
                leftover = list(Path(real_tmpdir).iterdir())
                self.assertEqual(leftover, [], f"Partial file left behind: {leftover}")
            finally:
                import shutil as _sh
                _sh.rmtree(real_tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # C4: POST /independent-review/stream (run-bound, resume) + discard
    # ------------------------------------------------------------------
    #
    # W2-B (T11c): the POST review endpoint keys the lock/store by scope.lock_key ==
    # tenant_project_key("local", canonical_pid). Since the mock engine echoes get_project_record,
    # the canonical pid == the URL pid, so the endpoint's store key is _skey(<URL pid>). Tests that
    # pre-seed the store or acquire the review lock with a bare pid must use _skey(pid) so the
    # computed lock_key matches. The store's own unit tests (test_independent_review.py) use opaque
    # keys and stay untouched.

    @staticmethod
    def _skey(project_id):
        return tenant_project_key("local", project_id)

    def _scope(self, project_id):
        # Build the ProjectScope that require_project would produce for the 'local' tenant, for
        # tests that call independent_review_stream_post(request, scope) directly (bypassing DI).
        return main_module.ProjectScope(
            uid="local",
            project_id=project_id,
            engine=self.engine,
            project_record={"id": project_id, "name": project_id},
            lock_key=self._skey(project_id),
        )

    def _seed_done_tombstone(self, project_id, run_id, body="report"):
        """Seed the store with a done tombstone via the real atomic commit path (composite key)."""
        store = main_module._REVIEW_SESSION_STORE
        key = self._skey(project_id)
        self.assertTrue(store.claim_first(key, run_id, threading.Event()))
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        canonical = os.path.join(tmpdir.name, "independent-review.md")
        fd, temp_path = tempfile.mkstemp(dir=tmpdir.name, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        mtime = store.atomic_commit_report(key, run_id, temp_path, canonical, {"messages": []})
        self.assertIsNotNone(mtime)
        self.addCleanup(store.discard, key, run_id)
        return mtime

    def _seed_errored(self, project_id, run_id, snapshot=None):
        store = main_module._REVIEW_SESSION_STORE
        key = self._skey(project_id)
        self.assertTrue(store.claim_first(key, run_id, threading.Event()))
        self.assertTrue(
            store.set_errored(key, run_id, snapshot or {"messages": [], "iteration": 2})
        )
        self.addCleanup(store.discard, key, run_id)

    def test_review_post_requires_run_id(self):
        self.engine.get_workspace_summary.return_value = {"stage_code": "S5"}
        response = self.client.post(
            "/api/projects/demo-post-norun/independent-review/stream",
            json={"resume": False},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("run_id required", response.json()["detail"])

    def test_review_post_requires_s5(self):
        self.engine.get_workspace_summary.return_value = {"stage_code": "S4"}
        response = self.client.post(
            "/api/projects/demo-post-s4/independent-review/stream",
            json={"resume": False, "run_id": "r1"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("独立审查只能在 S5 阶段使用", response.json()["detail"])

    @mock.patch("backend.main.IndependentReviewAgent")
    def test_review_post_first_run(self, mock_agent_cls):
        # resume=false: a worker starts with the frontend run_id + real store; on success a
        # done tombstone is written and the wrapper emits review-completed carrying that run_id.
        from backend.independent_review import CANONICAL_REVIEW_PATH

        project_id = "demo-post-first"
        run_id = "frontend-run-1"
        self.engine.get_workspace_summary.return_value = {"stage_code": "S5"}
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        captured = {}

        def fake_run(project_id_arg, run_id=None, store=None, resume_snapshot=None, cancel_event=None, store_key=None, **kwargs):
            del cancel_event, kwargs
            captured["run_id"] = run_id
            captured["store"] = store
            captured["resume_snapshot"] = resume_snapshot
            captured["store_key"] = store_key
            canonical = os.path.join(tmpdir.name, "independent-review.md")
            fd, temp_path = tempfile.mkstemp(dir=tmpdir.name, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("final report")
            # commit under the composite store_key the endpoint passes (matches get_done_mtime key).
            mtime = store.atomic_commit_report(store_key, run_id, temp_path, canonical, {"messages": []})
            captured["mtime"] = mtime
            yield {"type": "review-completed", "path": CANONICAL_REVIEW_PATH, "report_mtime_ns": mtime}

        mock_agent_cls.return_value.run.side_effect = fake_run
        self.addCleanup(main_module._REVIEW_SESSION_STORE.discard, self._skey(project_id), run_id)

        response = self.client.post(
            f"/api/projects/{project_id}/independent-review/stream",
            json={"resume": False, "run_id": run_id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["run_id"], run_id)
        self.assertEqual(captured["store_key"], self._skey(project_id))
        self.assertIs(captured["store"], main_module._REVIEW_SESSION_STORE)
        self.assertIsNone(captured["resume_snapshot"])
        self.assertIn("review-completed", response.text)
        self.assertIn(run_id, response.text)
        self.assertIn(str(captured["mtime"]), response.text)
        self.assertIn("data: [DONE]", response.text)

    @mock.patch("backend.main.IndependentReviewAgent")
    def test_review_post_constructs_agent_with_uid(self, mock_agent_cls):
        # W2-B/B2 endpoint-uid regression guard (✦ Codex NIT 2): the review endpoint must
        # construct IndependentReviewAgent with uid=scope.uid so the agent meters managed calls
        # under the right tenant's daily quota. In local (auth-off) mode scope.uid == "local".
        # The worker is created in the endpoint function body and driven to completion via the
        # streaming response, so mock_agent_cls.call_args is populated once the POST returns.
        from backend.independent_review import CANONICAL_REVIEW_PATH

        project_id = "demo-post-uid"
        run_id = "frontend-run-uid"
        self.engine.get_workspace_summary.return_value = {"stage_code": "S5"}
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)

        def fake_run(project_id_arg, run_id=None, store=None, store_key=None, **kwargs):
            del kwargs
            canonical = os.path.join(tmpdir.name, "independent-review.md")
            fd, temp_path = tempfile.mkstemp(dir=tmpdir.name, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("final report")
            mtime = store.atomic_commit_report(store_key, run_id, temp_path, canonical, {"messages": []})
            yield {"type": "review-completed", "path": CANONICAL_REVIEW_PATH, "report_mtime_ns": mtime}

        mock_agent_cls.return_value.run.side_effect = fake_run
        self.addCleanup(main_module._REVIEW_SESSION_STORE.discard, self._skey(project_id), run_id)

        response = self.client.post(
            f"/api/projects/{project_id}/independent-review/stream",
            json={"resume": False, "run_id": run_id},
        )

        self.assertEqual(response.status_code, 200)
        # construction kwargs carry uid; local tenant resolves to "local".
        self.assertEqual(mock_agent_cls.call_args.kwargs.get("uid"), "local")

    def test_review_post_first_run_409_when_lock_held(self):
        from backend.independent_review import get_independent_review_lock

        project_id = "demo-post-locked"
        self.engine.get_workspace_summary.return_value = {"stage_code": "S5"}
        lock = get_independent_review_lock(self._skey(project_id))
        self.assertTrue(lock.acquire(blocking=False))
        self.addCleanup(lock.release)

        response = self.client.post(
            f"/api/projects/{project_id}/independent-review/stream",
            json={"resume": False, "run_id": "r1"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("上一次独立审查仍在进行中", response.json()["detail"])

    def test_review_post_first_run_releases_lock_on_claim_fail(self):
        # If claim_first CAS fails (an active running record already exists) the review lock
        # must be released so subsequent requests are not wedged.
        from backend.independent_review import get_independent_review_lock

        project_id = "demo-post-claimfail"
        self.engine.get_workspace_summary.return_value = {"stage_code": "S5"}
        store = main_module._REVIEW_SESSION_STORE
        # pre-seed an active running record under a different run_id (composite key).
        self.assertTrue(store.claim_first(self._skey(project_id), "other-run", threading.Event()))
        self.addCleanup(store.discard, self._skey(project_id), "other-run")

        response = self.client.post(
            f"/api/projects/{project_id}/independent-review/stream",
            json={"resume": False, "run_id": "mine"},
        )
        self.assertEqual(response.status_code, 409)
        # lock was released despite the CAS failure.
        lock = get_independent_review_lock(self._skey(project_id))
        self.assertTrue(lock.acquire(blocking=False))
        lock.release()

    @mock.patch("backend.main.IndependentReviewAgent")
    def test_review_post_resume_errored_continues(self, mock_agent_cls):
        # resume=true against an errored record: the stored snapshot is handed to agent.run.
        project_id = "demo-post-resume-err"
        run_id = "run-resume-1"
        snapshot = {"messages": [{"role": "system", "content": "x"}], "iteration": 3}
        self.engine.get_workspace_summary.return_value = {"stage_code": "S5"}
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
    def test_review_post_resume_done_returns_completed_signal(self, mock_agent_cls):
        # resume=true against a done tombstone: NO worker runs, the wrapper just re-emits
        # review-completed with the stored mtime (recovers a lost success notification).
        project_id = "demo-post-resume-done"
        run_id = "run-done-1"
        self.engine.get_workspace_summary.return_value = {"stage_code": "S5"}
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

    @mock.patch("backend.main.IndependentReviewAgent")
    def test_review_resume_done_rereads_tombstone_before_completed(self, mock_agent_cls):
        # codex C4-quality BLOCKER + NIT 1: the resume-done branch must NOT emit a stale
        # review-completed from the cached done_mtime. After the handler sees done + releases the
        # lock, a concurrent discard clears the tombstone; emit_completion re-reads get_done_mtime
        # (now empty) and emits NEITHER completed NOR any stale mtime.
        project_id = "demo-resume-done-reread"
        run_id = "run-reread-done"
        self.engine.get_workspace_summary.return_value = {"stage_code": "S5"}
        mtime = self._seed_done_tombstone(project_id, run_id)
        store = main_module._REVIEW_SESSION_STORE
        scope = self._scope(project_id)

        async def call():
            class ConnectedRequest:
                async def json(self_inner):
                    return {"resume": True, "run_id": run_id}

                async def is_disconnected(self_inner):
                    return False

            # the handler runs claim_resume (sees done) + releases the lock during this await.
            response = await main_module.independent_review_stream_post(ConnectedRequest(), scope)
            # simulate a concurrent discard landing AFTER the handler resolved done_mtime but
            # BEFORE the stream's emit_completion re-reads the tombstone (composite key).
            store.discard(self._skey(project_id), run_id)
            return await _collect_streaming_chunks(response)

        chunks = asyncio.run(call())
        text = "".join(chunks)
        # the re-read intercepted the cleared tombstone: no stale completed, no stale mtime.
        self.assertNotIn("review-completed", text)
        self.assertNotIn(str(mtime), text)
        # still a connected stream → [DONE] terminator is emitted (only completed is suppressed).
        self.assertIn("data: [DONE]", text)

    @mock.patch("backend.main.IndependentReviewAgent")
    def test_review_post_disconnect_emits_neither_completed_nor_done(self, mock_agent_cls):
        # codex C4-quality BLOCKER + NIT 1: a disconnected POST stream emits neither
        # review-completed nor [DONE], for BOTH the done short-circuit and the worker path.
        # Exercise the done short-circuit (a tombstone exists) under a disconnected request.
        project_id = "demo-post-disconnect"
        run_id = "run-disc"
        self.engine.get_workspace_summary.return_value = {"stage_code": "S5"}
        self._seed_done_tombstone(project_id, run_id)
        scope = self._scope(project_id)

        async def call():
            class DisconnectedRequest:
                async def json(self_inner):
                    return {"resume": True, "run_id": run_id}

                async def is_disconnected(self_inner):
                    return True

            response = await main_module.independent_review_stream_post(DisconnectedRequest(), scope)
            return await _collect_streaming_chunks(response)

        chunks = asyncio.run(call())
        text = "".join(chunks)
        self.assertNotIn("review-completed", text)
        self.assertNotIn("[DONE]", text)
        # the agent never runs on a done-tombstone resume.
        mock_agent_cls.return_value.run.assert_not_called()

    def test_review_post_resume_reject_400(self):
        # resume against a missing / mismatched run_id is rejected (and the lock released).
        from backend.independent_review import get_independent_review_lock

        project_id = "demo-post-resume-reject"
        self.engine.get_workspace_summary.return_value = {"stage_code": "S5"}

        response = self.client.post(
            f"/api/projects/{project_id}/independent-review/stream",
            json={"resume": True, "run_id": "no-such-run"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("无可续审的会话", response.json()["detail"])
        lock = get_independent_review_lock(self._skey(project_id))
        self.assertTrue(lock.acquire(blocking=False))
        lock.release()

    def test_review_resume_409_when_worker_finalizing(self):
        # resume waits up to 3s for the review lock; if a worker holds it past the timeout the
        # endpoint returns 409 so the frontend backs off and retries.
        from backend.independent_review import get_independent_review_lock

        project_id = "demo-post-resume-busy"
        self.engine.get_workspace_summary.return_value = {"stage_code": "S5"}
        lock = get_independent_review_lock(self._skey(project_id))
        self.assertTrue(lock.acquire(blocking=False))
        self.addCleanup(lock.release)
        scope = self._scope(project_id)

        async def call():
            class ConnectedRequest:
                async def json(self_inner):
                    return {"resume": True, "run_id": "r1"}

                async def is_disconnected(self_inner):
                    return False

            with self.assertRaises(HTTPException) as ctx:
                await main_module.independent_review_stream_post(ConnectedRequest(), scope)
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
    def test_review_resume_uses_to_thread_not_blocking_loop(self, mock_agent_cls):
        # the blocking lock.acquire for resume must go through asyncio.to_thread so the event
        # loop is never blocked.
        project_id = "demo-post-resume-tothread"
        run_id = "run-tt-1"
        self.engine.get_workspace_summary.return_value = {"stage_code": "S5"}
        self._seed_errored(project_id, run_id)
        mock_agent_cls.return_value.run.return_value = iter(
            [{"type": "progress", "message": "ok"}]
        )
        scope = self._scope(project_id)

        async def call():
            class ConnectedRequest:
                async def json(self_inner):
                    return {"resume": True, "run_id": run_id}

                async def is_disconnected(self_inner):
                    return False

            response = await main_module.independent_review_stream_post(ConnectedRequest(), scope)
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
    def test_review_resume_rereads_store_after_lock(self, mock_agent_cls):
        # the record is errored at request time but flips to done while waiting for the lock;
        # after acquiring the lock claim_resume re-reads and reports the post-wait done state
        # (review-completed, no worker run).
        project_id = "demo-post-resume-reread"
        run_id = "run-reread-1"
        self.engine.get_workspace_summary.return_value = {"stage_code": "S5"}
        store = main_module._REVIEW_SESSION_STORE
        key = self._skey(project_id)
        self._seed_errored(project_id, run_id)
        scope = self._scope(project_id)

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

            response = await main_module.independent_review_stream_post(ConnectedRequest(), scope)
            return await _collect_streaming_chunks(response)

        real_to_thread = asyncio.to_thread

        async def flipping_to_thread(func, *args, **kwargs):
            result = await real_to_thread(func, *args, **kwargs)
            # after the blocking lock.acquire succeeds, convert errored -> done so the
            # subsequent claim_resume sees done. store keyed by composite key.
            if args and args[:1] == (True,) and flip["done_mtime"] is None:
                # we need the record running for atomic_commit; emulate the worker's commit:
                # set running via direct CAS then commit. The record is currently errored
                # (from _seed_errored); move it to running by claim_resume-ing under a temp
                # cancel event, then commit to done.
                kind, _payload = store.claim_resume(key, run_id, threading.Event())
                self.assertEqual(kind, "errored")  # now running
                canonical = os.path.join(tmpdir.name, "independent-review.md")
                fd, temp_path = tempfile.mkstemp(dir=tmpdir.name, suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write("done while waiting")
                flip["done_mtime"] = store.atomic_commit_report(
                    key, run_id, temp_path, canonical, {"messages": []}
                )
            return result

        with mock.patch("backend.main.asyncio.to_thread", side_effect=flipping_to_thread):
            chunks = asyncio.run(call())
        text = "".join(chunks)
        self.assertIn("review-completed", text)
        self.assertIn(str(flip["done_mtime"]), text)
        mock_agent_cls.return_value.run.assert_not_called()

    @mock.patch("backend.main.IndependentReviewAgent")
    def test_review_lock_released_on_done_and_reject_and_exception(self, mock_agent_cls):
        from backend.independent_review import get_independent_review_lock

        self.engine.get_workspace_summary.return_value = {"stage_code": "S5"}
        # locks/store keyed by composite key; resolve via _skey.
        lock_owner = lambda pid: get_independent_review_lock(self._skey(pid))

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
        self.addCleanup(main_module._REVIEW_SESSION_STORE.discard, self._skey(p_exc), "run-e")

    @mock.patch("backend.main.IndependentReviewAgent")
    def test_review_lock_released_even_if_response_generator_never_consumed(self, mock_agent_cls):
        # codex C5 red-team B3: Starlette runs stream_response + listen_for_disconnect concurrently
        # and cancels the task group when either finishes (starlette/responses.py). If the client
        # already disconnected, the disconnect listener can win before stream_response is scheduled,
        # so generate() may never execute. The worker (and thus its lock release) is created in the
        # ENDPOINT BODY, not inside generate(). Here we await the endpoint and NEVER consume the
        # response body (aclose it as Starlette would on cancel) — generate()'s body never runs, yet
        # the body-created worker must still finish and release the review lock (else that project's
        # review 409s until process restart).
        from backend.independent_review import get_independent_review_lock

        project_id = "demo-b3-no-consume"
        run_id = "run-b3"
        self.engine.get_workspace_summary.return_value = {"stage_code": "S5"}
        mock_agent_cls.return_value.run.return_value = iter([{"type": "progress", "message": "ok"}])
        self.addCleanup(main_module._REVIEW_SESSION_STORE.discard, self._skey(project_id), run_id)
        scope = self._scope(project_id)

        async def call():
            class ConnectedRequest:
                async def json(self_inner):
                    return {"resume": False, "run_id": run_id}

                async def is_disconnected(self_inner):
                    return False

            response = await main_module.independent_review_stream_post(ConnectedRequest(), scope)
            # Emulate Starlette cancelling before the body is ever iterated: close the generator
            # WITHOUT consuming it. generate()'s body never runs; only the body-created worker can
            # release the lock.
            await response.body_iterator.aclose()
            # Wait (in-loop) for the worker thread to finish and release the lock.
            lock = get_independent_review_lock(self._skey(project_id))
            for _ in range(200):
                if lock.acquire(blocking=False):
                    lock.release()
                    return True
                await asyncio.sleep(0.02)
            return False

        released = asyncio.run(call())
        self.assertTrue(
            released,
            "review lock leaked: a worker created inside generate() would never run when the "
            "response generator is cancelled before its first iteration (B3)",
        )

    @mock.patch("backend.main.IndependentReviewAgent")
    def test_review_completed_emitted_after_lock_release(self, mock_agent_cls):
        # when review-completed is emitted, the review lock is already free (the wrapper emits
        # it only after worker_task finished + run_worker finally released the lock).
        from backend.independent_review import CANONICAL_REVIEW_PATH, get_independent_review_lock

        project_id = "demo-completed-after-release"
        run_id = "run-after-rel"
        self.engine.get_workspace_summary.return_value = {"stage_code": "S5"}
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        self.addCleanup(main_module._REVIEW_SESSION_STORE.discard, self._skey(project_id), run_id)
        scope = self._scope(project_id)

        def fake_run(project_id_arg, run_id=None, store=None, store_key=None, **kwargs):
            del kwargs
            canonical = os.path.join(tmpdir.name, "independent-review.md")
            fd, temp_path = tempfile.mkstemp(dir=tmpdir.name, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("body")
            # commit under the composite store_key (matches the endpoint's get_done_mtime key).
            mtime = store.atomic_commit_report(store_key, run_id, temp_path, canonical, {"messages": []})
            yield {"type": "review-completed", "path": CANONICAL_REVIEW_PATH, "report_mtime_ns": mtime}

        mock_agent_cls.return_value.run.side_effect = fake_run

        async def call():
            class ConnectedRequest:
                async def json(self_inner):
                    return {"resume": False, "run_id": run_id}

                async def is_disconnected(self_inner):
                    return False

            response = await main_module.independent_review_stream_post(ConnectedRequest(), scope)
            chunks = []
            lock_free_at_completion = {"value": None}
            async for chunk in response.body_iterator:
                if "review-completed" in chunk:
                    lk = get_independent_review_lock(self._skey(project_id))
                    acquired = lk.acquire(blocking=False)
                    lock_free_at_completion["value"] = acquired
                    if acquired:
                        lk.release()
                chunks.append(chunk)
            return lock_free_at_completion["value"], chunks

        free, chunks = asyncio.run(call())
        text = "".join(chunks)
        self.assertIn("review-completed", text)
        self.assertTrue(free, "review lock must be free by the time review-completed is emitted")
        # codex C4-spec NIT 3: the wrapper emits review-completed exactly once, and the agent's
        # internal review-completed must be filtered out (the agent yields one too) — guards
        # against a regression that passes the internal event through and double-emits.
        completed = [
            json.loads(c[len("data: "):])
            for c in "".join(chunks).split("\n\n")
            if c.startswith("data: ") and '"review-completed"' in c
        ]
        self.assertEqual(len(completed), 1, f"expected exactly one review-completed, got {completed}")
        # the surviving event is the wrapper's (run_id present), NOT the agent's internal one
        # (which carries a 'path' and no 'run_id').
        self.assertEqual(completed[0]["run_id"], run_id)
        self.assertNotIn("path", completed[0])

    @mock.patch("backend.main.IndependentReviewAgent")
    def test_review_completed_carries_run_id_and_mtime(self, mock_agent_cls):
        from backend.independent_review import CANONICAL_REVIEW_PATH

        project_id = "demo-completed-payload"
        run_id = "run-payload"
        self.engine.get_workspace_summary.return_value = {"stage_code": "S5"}
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        self.addCleanup(main_module._REVIEW_SESSION_STORE.discard, self._skey(project_id), run_id)
        holder = {}

        def fake_run(project_id_arg, run_id=None, store=None, store_key=None, **kwargs):
            del kwargs
            canonical = os.path.join(tmpdir.name, "independent-review.md")
            fd, temp_path = tempfile.mkstemp(dir=tmpdir.name, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("body")
            # commit under the composite store_key (matches the endpoint's get_done_mtime key).
            holder["mtime"] = store.atomic_commit_report(store_key, run_id, temp_path, canonical, {"messages": []})
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
    def test_review_worker_error_does_not_emit_completed(self, mock_agent_cls):
        project_id = "demo-post-worker-error"
        run_id = "run-werr"
        self.engine.get_workspace_summary.return_value = {"stage_code": "S5"}
        mock_agent_cls.return_value.run.side_effect = RuntimeError("agent exploded")
        self.addCleanup(main_module._REVIEW_SESSION_STORE.discard, self._skey(project_id), run_id)

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
    def test_atomic_commit_failure_does_not_emit_completed(self, mock_agent_cls):
        # agent yields an error after a failed atomic replace (no done tombstone) -> the
        # wrapper must NOT synthesize review-completed.
        project_id = "demo-post-commit-fail"
        run_id = "run-commitfail"
        self.engine.get_workspace_summary.return_value = {"stage_code": "S5"}
        self.addCleanup(main_module._REVIEW_SESSION_STORE.discard, self._skey(project_id), run_id)

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
    def test_review_worker_exception_leaves_no_running_record(self, mock_agent_cls):
        # codex R2 BLOCKER 4: an uncaught worker exception must converge via finally
        # finalize_orphan_running, leaving no stuck running record (first-claim succeeds again).
        project_id = "demo-post-exc-norun"
        run_id = "run-exc"
        self.engine.get_workspace_summary.return_value = {"stage_code": "S5"}
        mock_agent_cls.return_value.run.side_effect = RuntimeError("boom mid-run")

        response = self.client.post(
            f"/api/projects/{project_id}/independent-review/stream",
            json={"resume": False, "run_id": run_id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('"type": "error"', response.text)
        # no dead running record: a brand-new first-claim succeeds (first-run, no resume_snapshot
        # so finalize cleared the record entirely). store keyed by composite key.
        self.assertTrue(
            main_module._REVIEW_SESSION_STORE.claim_first(self._skey(project_id), "fresh-after-exc", threading.Event())
        )
        main_module._REVIEW_SESSION_STORE.discard(self._skey(project_id), "fresh-after-exc")

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

        timeout = captured["timeout"]
        self.assertIsInstance(timeout, _httpx.Timeout)
        # codex C4-spec NIT 2: lock the concrete bounds so a regression to a flat float (or a
        # widened no-first-byte window) is caught.
        self.assertEqual(timeout.connect, 15.0)
        self.assertEqual(timeout.read, 60.0)
        self.assertEqual(timeout.write, 30.0)
        self.assertEqual(timeout.pool, 30.0)

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
        key = self._skey(project_id)
        cancel_event = threading.Event()
        self.assertTrue(store.claim_first(key, run_id, cancel_event))

        response = self.client.post(
            f"/api/projects/{project_id}/independent-review/discard",
            json={"run_id": run_id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["cancelled"])
        # cancel event set and record cleared (a fresh first-claim succeeds).
        self.assertTrue(cancel_event.is_set())
        self.assertTrue(store.claim_first(key, "after-discard", threading.Event()))
        store.discard(key, "after-discard")

    def test_discard_no_op_on_mismatch(self):
        project_id = "demo-discard-mismatch"
        run_id = "run-current"
        store = main_module._REVIEW_SESSION_STORE
        key = self._skey(project_id)
        cancel_event = threading.Event()
        self.assertTrue(store.claim_first(key, run_id, cancel_event))
        self.addCleanup(store.discard, key, run_id)

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
        key = self._skey(project_id)
        cancel_event = threading.Event()
        self.assertTrue(store.claim_first(key, run_id, cancel_event))
        lock = get_independent_review_lock(key)
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
        key = self._skey(project_id)
        cancel_event = threading.Event()
        self.assertTrue(store.claim_first(key, run_id, cancel_event))

        # user discards.
        self.assertTrue(
            self.client.post(
                f"/api/projects/{project_id}/independent-review/discard",
                json={"run_id": run_id},
            ).json()["cancelled"]
        )
        # the old worker tries to land an errored snapshot — must be a no-op.
        self.assertFalse(store.set_errored(key, run_id, {"messages": [], "iteration": 4}))
        self.assertFalse(store.finalize_orphan_running(key, run_id, {"messages": []}))
        # nothing running remains: a fresh first-claim succeeds.
        self.assertTrue(store.claim_first(key, "fresh-stale", threading.Event()))
        store.discard(key, "fresh-stale")

    @mock.patch("backend.main.export_reviewable_draft")
    def test_export_draft_endpoint_returns_output_path(self, mock_export_draft):
        self.engine.get_primary_report_path.return_value = "/tmp/report_draft_v1.md"
        self.engine.ensure_output_dir.return_value = "/tmp/output"
        mock_export_draft.return_value = {
            "status": "ok",
            "output": "已生成可审草稿: /tmp/output/report_draft_v1.docx",
            "output_path": "/tmp/output/report_draft_v1.docx",
            "filename": "report_draft_v1.docx",
        }

        response = self.client.post("/api/projects/demo/export-draft")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["output_path"], "/tmp/output/report_draft_v1.docx")
        self.assertEqual(response.json()["filename"], "report_draft_v1.docx")
        # 新签名：不再传 script_path
        mock_export_draft.assert_called_once_with(
            "/tmp/report_draft_v1.md",
            "/tmp/output",
        )

    def test_export_draft_route_not_blocking_async(self):
        # spec §3.2 守卫：导出端点必须是同步 def（FastAPI 跑线程池）或经 run_in_threadpool，
        # 不得在 async 路由里直接同步调 pandoc 阻塞事件循环。
        import inspect
        self.assertFalse(
            inspect.iscoroutinefunction(main_module.export_draft),
            "export_draft 必须是同步 def 路由（线程池执行），否则阻塞事件循环掐住 SSE 心跳",
        )

    def test_export_download_serves_deterministic_docx_for_owner(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "output"; out.mkdir()
            (out / "report_draft_v1.docx").write_bytes(b"PKdocxbytes")
            self.engine.ensure_output_dir.return_value = str(out)
            resp = self.client.get("/api/projects/demo/export-draft/download")
            self.assertEqual(resp.status_code, 200)
            self.assertIn("attachment", resp.headers["content-disposition"])
            self.assertIn("report_draft_v1.docx", resp.headers["content-disposition"])
            self.assertEqual(resp.content, b"PKdocxbytes")  # FileResponse 真回文件，非 JSON

    def test_export_download_404_when_not_generated(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "output"; out.mkdir()
            self.engine.ensure_output_dir.return_value = str(out)
            self.assertEqual(self.client.get("/api/projects/demo/export-draft/download").status_code, 404)

    def test_export_download_rejects_symlink_escape(self):
        import os, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "output"; out.mkdir()
            secret = Path(d) / "secret.docx"; secret.write_bytes(b"SECRET")
            try:
                os.symlink(secret, out / "report_draft_v1.docx")  # 指向 output 目录外
            except OSError:
                self.skipTest("无 symlink 权限（Windows 非管理员）")
            self.engine.ensure_output_dir.return_value = str(out)
            # resolve() 后越界 → output_dir not in target.parents → 404（不外泄目录外文件）
            self.assertEqual(self.client.get("/api/projects/demo/export-draft/download").status_code, 404)

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
            client_message_id=None,
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
                        "id": "att-1",
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
                    "id": "att-1",
                    "name": "bug.png",
                    "mime_type": "image/png",
                    "data_url": "data:image/png;base64,AAAA",
                }
            ],
            client_message_id=None,
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
                    "path": "plan/independent-review.md",
                    "reason": "需要先完成独立审查，才能标记审查通过。",
                    "user_action": "请先在 S5 阶段点击上方'独立审查'按钮，再确认审查通过。",
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

        # C5: the route must thread run-bound trigger metadata to the handler verbatim;
        # report_mtime_ns is a large opaque string and must survive end-to-end unchanged.
        big_mtime = "1760000000123456789"
        response = self.client.post(
            "/api/chat/stream",
            json={
                "project_id": "proj-demo",
                "message_text": "",
                "system_trigger": "independent_review_done",
                "attached_material_ids": [],
                "run_id": "run-abc-123",
                "report_mtime_ns": big_mtime,
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
            trigger_metadata={"run_id": "run-abc-123", "report_mtime_ns": big_mtime},
            client_message_id=None,
        )
        # The nanosecond mtime stays a string (never coerced to a JSON number / int).
        forwarded = handler.chat_stream.call_args.kwargs["trigger_metadata"]
        self.assertIsInstance(forwarded["report_mtime_ns"], str)
        self.assertEqual(forwarded["report_mtime_ns"], big_mtime)


class GetConversationSanitizeTests(_LocalMockEngineMixin, unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main_module.app)
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.project_path = Path(self.tmpdir.name) / "demo-project"
        self.project_path.mkdir(parents=True, exist_ok=True)
        self.engine = self._install_mock_engine()
        self.engine.get_project_path.return_value = self.project_path
        self.mock_get_project_path = self.engine.get_project_path

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


class R3FileApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main_module.app)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        from backend.skill import SkillEngine
        repo_skill_dir = Path(__file__).resolve().parents[1] / "skill"
        self.engine = SkillEngine(Path(self._tmp.name) / "projects", repo_skill_dir)
        project = self.engine.create_project({
            "name": "demo", "workspace_dir": str(Path(self._tmp.name) / "ws"),
            "project_type": "strategy-consulting", "theme": "t",
            "target_audience": "a", "deadline": "2026-04-01",
            "expected_length": "3000 words", "notes": "n",
        })
        self.pid = project["id"]
        self.project_dir = Path(project["project_dir"])
        (self.project_dir / "content").mkdir(parents=True, exist_ok=True)
        (self.project_dir / "content" / "report_draft_v1.md").write_text("初稿", encoding="utf-8")
        # W2-B: install the real engine as the 'local' tenant (auth off) so require_project resolves
        # the real project and the endpoints exercise the real engine, like the old global singleton.
        self.addCleanup(_install_local_engine(self.engine))

    def test_list_files_returns_structured_array(self):
        r = self.client.get(f"/api/projects/{self.pid}/files")
        self.assertEqual(r.status_code, 200)
        files = r.json()["files"]
        self.assertTrue(all({"path", "group", "stage", "editable", "mtime_ns"} <= set(f) for f in files))
        draft = next(f for f in files if f["path"] == "content/report_draft_v1.md")
        self.assertTrue(draft["editable"])
        self.assertIsInstance(draft["mtime_ns"], str)

    def test_read_file_returns_content_mtime_editable(self):
        r = self.client.get(f"/api/projects/{self.pid}/files/content/report_draft_v1.md")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["content"], "初稿")
        self.assertTrue(body["editable"])
        self.assertIsInstance(body["mtime_ns"], str)

    def test_read_readonly_file_editable_false(self):
        (self.project_dir / "plan" / "independent-review.md").write_text("审查", encoding="utf-8")
        r = self.client.get(f"/api/projects/{self.pid}/files/plan/independent-review.md")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["editable"])

    def _mtime(self, rel):
        return str((self.project_dir / rel).stat().st_mtime_ns)

    def test_post_write_success_returns_new_mtime(self):
        base = self._mtime("content/report_draft_v1.md")
        r = self.client.post(
            f"/api/projects/{self.pid}/files/content/report_draft_v1.md",
            json={"content": "改过的正文", "base_mtime_ns": base},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")
        self.assertIsInstance(r.json()["mtime_ns"], str)
        self.assertEqual(
            (self.project_dir / "content" / "report_draft_v1.md").read_text(encoding="utf-8"),
            "改过的正文",
        )

    def test_post_write_denied_readonly_403(self):
        (self.project_dir / "plan" / "independent-review.md").write_text("审查", encoding="utf-8")
        base = self._mtime("plan/independent-review.md")
        r = self.client.post(
            f"/api/projects/{self.pid}/files/plan/independent-review.md",
            json={"content": "试图篡改", "base_mtime_ns": base},
        )
        self.assertEqual(r.status_code, 403)

    def test_post_write_traversal_400(self):
        r = self.client.post(
            f"/api/projects/{self.pid}/files/../../../evil.md",
            json={"content": "x", "base_mtime_ns": "1"},
        )
        # HTTP client normalises traversal sequences before dispatch; the attack never
        # reaches the endpoint. Accept 400 (ValueError in _resolve_project_path),
        # 404 (path resolves outside project tree), or 405 (URL collapsed to a
        # different route that has no POST — all are "request rejected/blocked").
        self.assertIn(r.status_code, (400, 404, 405))

    def test_post_write_missing_file_404(self):
        # outline.md 在白名单但删除后不存在 → 404（用户只能改已存在文件，不新建）
        outline = self.project_dir / "plan" / "outline.md"
        if outline.exists():
            outline.unlink()
        r = self.client.post(
            f"/api/projects/{self.pid}/files/plan/outline.md",
            json={"content": "x", "base_mtime_ns": "1"},
        )
        self.assertEqual(r.status_code, 404)

    def test_post_write_stale_mtime_409(self):
        r = self.client.post(
            f"/api/projects/{self.pid}/files/content/report_draft_v1.md",
            json={"content": "x", "base_mtime_ns": "999999999999999999"},
        )
        self.assertEqual(r.status_code, 409)

    def test_post_write_rejects_numeric_base_mtime(self):
        base_int = int(self._mtime("content/report_draft_v1.md"))
        r = self.client.post(
            f"/api/projects/{self.pid}/files/content/report_draft_v1.md",
            json={"content": "x", "base_mtime_ns": base_int},  # number, 非 str
        )
        self.assertEqual(r.status_code, 422)  # pydantic str 字段拒绝 int

    def test_post_write_missing_project_404(self):
        r = self.client.post(
            "/api/projects/no-such-project/files/content/report_draft_v1.md",
            json={"content": "x", "base_mtime_ns": "1"},
        )
        self.assertEqual(r.status_code, 404)

    def test_post_write_serialized_under_request_lock(self):
        # 持有与聊天同一把 per-project 锁时，POST 必须阻塞到锁释放（CAS 串行化、不丢写）。
        import backend.chat as chat_mod
        import threading as _t
        from backend.tenant import tenant_project_key
        # W2-B 多租户：文件写端点经 handler._get_project_request_lock 取复合键 (uid, project_id) 锁，
        # 模拟「持同一把锁」须用同样的复合键（uid 默认 "local"），否则锁错把、测不到串行化。
        lock = chat_mod._get_project_request_lock(tenant_project_key("local", self.pid))
        base = self._mtime("content/report_draft_v1.md")
        done = {"status": None}

        def _save():
            r = self.client.post(
                f"/api/projects/{self.pid}/files/content/report_draft_v1.md",
                json={"content": "锁释放后才落盘", "base_mtime_ns": base},
            )
            done["status"] = r.status_code

        lock.acquire()
        try:
            t = _t.Thread(target=_save)
            t.start()
            t.join(timeout=1.0)
            # 锁未释放：请求应仍在等待，未完成
            self.assertIsNone(done["status"], "POST 不应在锁被持有时完成")
        finally:
            lock.release()
        t.join(timeout=5.0)
        self.assertEqual(done["status"], 200)
        self.assertEqual(
            (self.project_dir / "content" / "report_draft_v1.md").read_text(encoding="utf-8"),
            "锁释放后才落盘",
        )

    def test_post_write_409_when_file_changed_during_lock_wait(self):
        # codex 后端审 NIT 1：CAS 核心路径——POST 持锁等待期间，另一路（AI）写了同一文件、
        # mtime 前移；POST 拿到锁后 stat 复校应检出 stale → 409，绝不用旧 base 覆盖 AI 的写入。
        import backend.chat as chat_mod
        import threading as _t
        from backend.tenant import tenant_project_key
        rel = "content/report_draft_v1.md"
        full = self.project_dir / "content" / "report_draft_v1.md"
        base = self._mtime(rel)
        # W2-B 多租户：文件写端点取复合键 (uid, project_id) 锁；模拟须用同样复合键（uid 默认 "local"）。
        lock = chat_mod._get_project_request_lock(tenant_project_key("local", self.pid))
        done = {"status": None}

        def _save():
            r = self.client.post(
                f"/api/projects/{self.pid}/files/{rel}",
                json={"content": "想用旧 base 覆盖", "base_mtime_ns": base},
            )
            done["status"] = r.status_code

        lock.acquire()
        try:
            t = _t.Thread(target=_save)
            t.start()
            t.join(timeout=1.0)
            self.assertIsNone(done["status"], "POST 不应在锁被持有时完成")
            # 持锁期间另一路写入同文件并前移 mtime（模拟 AI 在用户保存排队时落盘）
            full.write_text("AI 在用户保存排队期间写入的新内容", encoding="utf-8")
            newer = int(base) + 10_000
            os.utime(full, ns=(newer, newer))
        finally:
            lock.release()
        t.join(timeout=5.0)
        self.assertEqual(done["status"], 409)
        # 用户的旧内容没有覆盖 AI 的写入
        self.assertEqual(full.read_text(encoding="utf-8"), "AI 在用户保存排队期间写入的新内容")

    def test_user_write_runs_on_dedicated_executor_not_anyio_pool(self):
        # codex 后端 quality NIT：锁死「保存临界区跑专用线程池」。回退到 run_in_threadpool 默认 anyio 池
        # 会让保存可能复用 chat_stream 的 RLock owner 线程、重入绕过 CAS——而行为测试 catch 不到（旧版
        # 也能过串行化测试），故用 source 断言守。
        main_src = (Path(__file__).resolve().parents[1] / "backend" / "main.py").read_text(encoding="utf-8")
        self.assertIn("_USER_WRITE_EXECUTOR", main_src)
        self.assertIn("run_in_executor(", main_src)
        self.assertNotIn("run_in_threadpool(_write_under_lock)", main_src)

    def _write_effective_reports(self):
        # N7: review_stale gate is _has_effective_independent_review (single report);
        # write the independent review with anchors + completion marker + substantive body.
        eng = self.engine
        ir_lines = ["# Independent review", ""]
        for anchor in eng.INDEPENDENT_REVIEW_ANCHORS:
            ir_lines += [anchor, "审查结论: 已完成实质复核。", "证据说明: 对照正文与资料核验。", ""]
        ir_lines.append(eng.INDEPENDENT_REVIEW_COMPLETION_MARKER)
        (self.project_dir / "plan" / "independent-review.md").write_text(
            "\n".join(ir_lines).strip() + "\n", encoding="utf-8")

    def test_workspace_review_stale_after_draft_edit(self):
        self._write_effective_reports()
        ir = self.project_dir / "plan" / "independent-review.md"
        draft = self.project_dir / "content" / "report_draft_v1.md"
        os.utime(ir, ns=(1000, 1000))
        os.utime(draft, ns=(2000, 2000))
        r = self.client.get(f"/api/projects/{self.pid}/workspace")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["flags"]["review_stale"])


class MaterialConversionStatusApiTests(unittest.TestCase):
    """N6 D2: GET /materials surfaces conversion_status per material (no 500 if unconverted)."""

    def setUp(self):
        self.client = TestClient(main_module.app)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        from backend.skill import SkillEngine
        repo_skill_dir = Path(__file__).resolve().parents[1] / "skill"
        self.engine = SkillEngine(Path(self._tmp.name) / "projects", repo_skill_dir)
        project = self.engine.create_project({
            "name": "demo", "workspace_dir": str(Path(self._tmp.name) / "ws"),
            "project_type": "strategy-consulting", "theme": "t",
            "target_audience": "a", "deadline": "2026-04-01",
            "expected_length": "3000 words", "notes": "n",
        })
        self.pid = project["id"]
        # W2-B: install the real engine as the 'local' tenant (auth off), like the old global singleton.
        self.addCleanup(_install_local_engine(self.engine))

    def _add_text_material(self, name="note.txt", body="some-content"):
        src = Path(self._tmp.name) / name
        src.write_text(body, encoding="utf-8")
        return self.engine.add_materials(self.pid, [str(src)], added_via="chat_upload")[0]

    def test_materials_endpoint_reports_conversion_status_field(self):
        # No converter wired → every material falls back to not_parsed, never 500.
        self._add_text_material()
        r = self.client.get(f"/api/projects/{self.pid}/materials")
        self.assertEqual(r.status_code, 200)
        items = r.json()["materials"]
        self.assertTrue(len(items) >= 1)
        for item in items:
            self.assertIn("conversion_status", item)
            self.assertIn("conversion_reason", item)
            self.assertIn(item["conversion_status"], {"not_parsed", "parsed", "failed"})
        self.assertEqual(items[0]["conversion_status"], "not_parsed")
        self.assertIsNone(items[0]["conversion_reason"])

    def test_materials_endpoint_reports_parsed_and_failed(self):
        from backend.material_conversion import MaterialConverter
        conv = MaterialConverter(
            cache_dir=Path(self._tmp.name) / "cache",
            vision_adapter=lambda *a: "V",
            ocr_adapter=lambda p: "O",
            capability_resolver=lambda: False,
        )
        self.engine.set_material_converter(conv)
        parsed = self._add_text_material(name="parsed.txt", body="parsed-body")
        failed = self._add_text_material(name="failed.txt", body="failed-body")
        not_yet = self._add_text_material(name="fresh.txt", body="fresh-body")

        # parsed: prime the .md cache via a real read
        self.engine.read_material_file(self.pid, parsed["id"])
        # failed: drop an .error tombstone at the material's key
        key = self.engine._cache_key_for_material(
            failed, self.engine.get_material_path(self.pid, failed["id"])
        )
        _, err_path = conv._cache_paths(key)
        err_path.write_text("文档解析失败：BoomError", encoding="utf-8")

        r = self.client.get(f"/api/projects/{self.pid}/materials")
        self.assertEqual(r.status_code, 200)
        by_id = {m["id"]: m for m in r.json()["materials"]}
        self.assertEqual(by_id[parsed["id"]]["conversion_status"], "parsed")
        self.assertIsNone(by_id[parsed["id"]]["conversion_reason"])
        self.assertEqual(by_id[failed["id"]]["conversion_status"], "failed")
        self.assertEqual(by_id[failed["id"]]["conversion_reason"], "文档解析失败：BoomError")
        self.assertEqual(by_id[not_yet["id"]]["conversion_status"], "not_parsed")

    # --- N6 Fix5: lock the advisory-status fallback paths (status probe must never raise) ---

    def test_status_falls_back_to_not_parsed_when_no_converter_wired(self):
        # (a) No converter set on the engine → list_materials / GET must report not_parsed, no 500.
        self.assertIsNone(getattr(self.engine, "_material_converter", None))
        self._add_text_material(name="no_converter.txt", body="x")
        items = self.engine.list_materials(self.pid)
        self.assertTrue(len(items) >= 1)
        for item in items:
            self.assertEqual(item["conversion_status"], "not_parsed")
            self.assertIsNone(item["conversion_reason"])
        r = self.client.get(f"/api/projects/{self.pid}/materials")
        self.assertEqual(r.status_code, 200)
        for item in r.json()["materials"]:
            self.assertEqual(item["conversion_status"], "not_parsed")

    def test_status_falls_back_to_not_parsed_when_content_sha256_missing(self):
        # (b) A material whose content_sha256 is missing → status probe short-circuits to not_parsed.
        from backend.material_conversion import MaterialConverter
        conv = MaterialConverter(
            cache_dir=Path(self._tmp.name) / "cache",
            vision_adapter=lambda *a: "V",
            ocr_adapter=lambda p: "O",
            capability_resolver=lambda: False,
        )
        self.engine.set_material_converter(conv)
        mat = self._add_text_material(name="no_hash.txt", body="hash-me")
        # Strip the add-time hash off the persisted record.
        record = self.engine.get_project_record(self.pid)
        materials = self.engine._load_materials(record)
        for m in materials:
            if m["id"] == mat["id"]:
                m.pop("content_sha256", None)
        self.engine._save_materials(record, materials)

        items = self.engine.list_materials(self.pid)
        by_id = {m["id"]: m for m in items}
        self.assertEqual(by_id[mat["id"]]["conversion_status"], "not_parsed")
        self.assertIsNone(by_id[mat["id"]]["conversion_reason"])
        r = self.client.get(f"/api/projects/{self.pid}/materials")
        self.assertEqual(r.status_code, 200)

    def test_status_falls_back_to_not_parsed_when_probe_raises(self):
        # (c) The converter's status_for_key raising must be swallowed → not_parsed, no 500.
        from backend.material_conversion import MaterialConverter
        conv = MaterialConverter(
            cache_dir=Path(self._tmp.name) / "cache",
            vision_adapter=lambda *a: "V",
            ocr_adapter=lambda p: "O",
            capability_resolver=lambda: False,
        )

        def _boom(_key):
            raise RuntimeError("probe exploded")

        conv.status_for_key = _boom  # type: ignore[method-assign]
        self.engine.set_material_converter(conv)
        mat = self._add_text_material(name="boom.txt", body="boom-body")

        items = self.engine.list_materials(self.pid)
        by_id = {m["id"]: m for m in items}
        self.assertEqual(by_id[mat["id"]]["conversion_status"], "not_parsed")
        self.assertIsNone(by_id[mat["id"]]["conversion_reason"])
        r = self.client.get(f"/api/projects/{self.pid}/materials")
        self.assertEqual(r.status_code, 200)

    def test_status_not_parsed_when_source_file_deleted_despite_cache(self):
        # (d) Source file deleted/moved but its old cache .md still exists → must report
        # not_parsed, never a stale "parsed" from the orphaned cache entry.
        from backend.material_conversion import MaterialConverter
        conv = MaterialConverter(
            cache_dir=Path(self._tmp.name) / "cache",
            vision_adapter=lambda *a: "V",
            ocr_adapter=lambda p: "O",
            capability_resolver=lambda: False,
        )
        self.engine.set_material_converter(conv)
        mat = self._add_text_material(name="will_delete.txt", body="parse-then-delete")
        # Convert it so a cache .md entry exists → now reads as parsed.
        self.engine.read_material_file(self.pid, mat["id"])
        by_id = {m["id"]: m for m in self.engine.list_materials(self.pid)}
        self.assertEqual(by_id[mat["id"]]["conversion_status"], "parsed")
        # Delete the source file out from under the material (external delete).
        self.engine.get_material_path(self.pid, mat["id"]).unlink()
        by_id2 = {m["id"]: m for m in self.engine.list_materials(self.pid)}
        self.assertEqual(by_id2[mat["id"]]["conversion_status"], "not_parsed")
        r = self.client.get(f"/api/projects/{self.pid}/materials")
        self.assertEqual(r.status_code, 200)


class CrossTenantApiTests(AuthApiTestBase):
    """W2-B (T11c): real end-to-end tenant isolation — two authenticated users, each driving the
    full HTTP stack (auth ON, real per-uid engines). B must get 404 on A's project both by id and
    by name, and B's project list must not leak A's project."""

    def setUp(self):
        super().setUp()  # auth_required=True + heal mock + singleton reset + self.client (=A's client)
        from fastapi.testclient import TestClient

        self.A = self.client
        self.B = TestClient(self.m.app)
        self.B.headers.update({"origin": self._ALLOWED_ORIGIN})  # fresh client：补 CSRF 同源
        for c, u in ((self.A, "alice"), (self.B, "bob")):
            c.post("/api/auth/register", json={"username": u, "password": "pw-123456", "invite_code": "JOIN"})
            c.post("/api/auth/login", json={"username": u, "password": "pw-123456"})

    def _create(self, c, name):
        return c.post(
            "/api/projects",
            json={
                "name": name,
                "project_type": "strategy",
                "theme": "t",
                "deadline": "2026-12-31",
                "expected_length": "1万字",
            },
        ).json()["project_id"]

    def test_b_cannot_read_a(self):
        pid = self._create(self.A, "A机密")
        # A owns the project (200); B is a different tenant (404 + empty project list).
        self.assertEqual(self.A.get(f"/api/projects/{pid}/workspace").status_code, 200)
        self.assertEqual(self.B.get(f"/api/projects/{pid}/workspace").status_code, 404)
        self.assertEqual(self.B.get("/api/projects").json(), [])

    def test_name_ref_no_leak(self):
        # require_project accepts a name alias; B must NOT resolve A's project by its display name.
        self._create(self.A, "A机密")
        self.assertEqual(self.B.get("/api/projects/A机密/workspace").status_code, 404)

    def test_b_cannot_download_a_export(self):
        pid = self._create(self.A, "A机密")
        self.assertEqual(self.B.get(f"/api/projects/{pid}/export-draft/download").status_code, 404)


class B2ChatQuotaTests(_LocalMockEngineMixin, unittest.TestCase):
    def setUp(self):
        import os, tempfile
        from backend import accounts
        self._tmp = tempfile.TemporaryDirectory(); self.addCleanup(self._tmp.cleanup)
        self._prev_root = os.environ.get("CRA_DATA_ROOT")
        os.environ["CRA_DATA_ROOT"] = self._tmp.name; self.addCleanup(self._restore_root)
        accounts.init_db()
        self.accounts = accounts
        self._install_mock_engine()
        self.client = TestClient(main_module.app)

    def _restore_root(self):
        import os
        if self._prev_root is None: os.environ.pop("CRA_DATA_ROOT", None)
        else: os.environ["CRA_DATA_ROOT"] = self._prev_root

    def test_chat_returns_friendly_when_quota_exhausted(self):
        import backend.metering as metering
        self.accounts.set_config("global_daily_cap_micro_yuan", "100")
        self.accounts.add_usage("local", metering.today_shanghai(), 100, 0, 0, 0)  # 已达上限
        # ✦ Codex NIT1：额度尽时预检必须在 build handler 前就友好返回（不半启动 turn）。
        with mock.patch("backend.main.get_chat_handler") as mock_get_handler:
            resp = self.client.post("/api/chat", json={"project_id": "demo", "message_text": "hi"})
            mock_get_handler.assert_not_called()       # 预检短路、未构造 handler
        self.assertEqual(resp.status_code, 200)        # 友好返回、非 500、不 build handler
        self.assertIn("额度", resp.json()["content"])    # 提示在 content（system_notices 是对象模型、置 None）
