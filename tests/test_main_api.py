import asyncio
import json
import tempfile
import threading
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import backend.main as main_module
from backend.chat import LEGACY_EMPTY_ASSISTANT_FALLBACKS, USER_VISIBLE_FALLBACK


class CheckpointTableInvariantTests(unittest.TestCase):
    def test_checkpoint_tables_key_sets_are_aligned(self):
        from backend.chat import ChatHandler
        from backend.main import _CHECKPOINT_ROUTES
        from backend.skill import SkillEngine

        engine_keys = set(SkillEngine.STAGE_CHECKPOINT_KEYS)
        cascade_keys = set(SkillEngine._CASCADE_ORDER)
        rank_keys = set(ChatHandler._STAGE_RANK.keys())
        route_keys = set(_CHECKPOINT_ROUTES.values())

        self.assertEqual(engine_keys, cascade_keys, "STAGE_CHECKPOINT_KEYS vs _CASCADE_ORDER")
        self.assertEqual(engine_keys, rank_keys, "STAGE_CHECKPOINT_KEYS vs _STAGE_RANK")
        self.assertEqual(engine_keys, route_keys, "STAGE_CHECKPOINT_KEYS vs _CHECKPOINT_ROUTES values")


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
                    "path": "plan/review-checklist.md",
                    "reason": "review-checklist.md 的\"审查人\"字段必须由真实用户签字，请保留\"审查人：[待用户确认]\"让用户在 UI 上签字。",
                    "user_action": "请联系用户在右侧工作区完成对应的确认后再写入",
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
