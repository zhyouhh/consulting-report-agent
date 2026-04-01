import unittest
from unittest import mock
from io import BytesIO

from fastapi.testclient import TestClient

import backend.main as main_module


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
    def test_chat_endpoint_forwards_attached_material_ids(self, mock_get_chat_handler):
        handler = mock.Mock()
        handler.chat.return_value = {
            "content": "已整理完毕",
            "token_usage": {
                "current_tokens": 1200,
                "max_tokens": 500000,
                "effective_max_tokens": 500000,
                "provider_max_tokens": 1000000,
                "compressed": False,
                "usage_mode": "actual",
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
        self.assertEqual(response.json()["token_usage"]["max_tokens"], 500000)
        self.assertEqual(response.json()["token_usage"]["effective_max_tokens"], 500000)
        self.assertEqual(response.json()["token_usage"]["provider_max_tokens"], 1000000)
        self.assertEqual(response.json()["token_usage"]["usage_mode"], "actual")
        handler.chat.assert_called_once_with(
            "proj-demo",
            "请结合新增材料整理问题树",
            ["mat-1", "mat-2"],
            [],
        )

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
