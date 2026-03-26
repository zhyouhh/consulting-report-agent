import unittest
from unittest import mock

from fastapi.testclient import TestClient

import backend.main as main_module


class WorkspaceApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main_module.app)

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
