import unittest
from unittest import mock

from fastapi.testclient import TestClient

import backend.main as main_module


class ProjectCreateApiTests(unittest.TestCase):
    @mock.patch("backend.main.skill_engine.create_project")
    def test_create_project_route_passes_workspace_project_payload(self, mock_create_project):
        mock_create_project.return_value = {
            "id": "proj-demo",
            "name": "demo",
            "workspace_dir": "D:/Workspaces/demo",
            "project_dir": "D:/Workspaces/demo/.consulting-report",
        }
        client = TestClient(main_module.app)

        response = client.post(
            "/api/projects",
            json={
                "name": "demo",
                "workspace_dir": "D:/Workspaces/demo",
                "project_type": "strategy-consulting",
                "theme": "AI 战略规划",
                "target_audience": "高层决策者",
                "deadline": "2026-04-01",
                "expected_length": "3000字",
                "notes": "已有访谈纪要",
                "initial_material_paths": [
                    "D:/Workspaces/demo/资料/访谈纪要.txt",
                    "D:/Workspaces/demo/资料/市场图表.png",
                ],
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["project_id"], "proj-demo")

        args, _ = mock_create_project.call_args
        project_info = args[0]
        self.assertEqual(project_info.name, "demo")
        self.assertEqual(project_info.workspace_dir, "D:/Workspaces/demo")
        self.assertEqual(
            project_info.initial_material_paths,
            [
                "D:/Workspaces/demo/资料/访谈纪要.txt",
                "D:/Workspaces/demo/资料/市场图表.png",
            ],
        )
