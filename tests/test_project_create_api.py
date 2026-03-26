import unittest
from unittest import mock

from fastapi.testclient import TestClient

import backend.main as main_module


class ProjectCreateApiTests(unittest.TestCase):
    @mock.patch("backend.main.skill_engine.create_project")
    def test_create_project_route_passes_v2_fields(self, mock_create_project):
        client = TestClient(main_module.app)

        response = client.post(
            "/api/projects",
            json={
                "name": "demo",
                "project_type": "strategy-consulting",
                "theme": "AI 战略规划",
                "target_audience": "高层决策者",
                "deadline": "2026-04-01",
                "expected_length": "3000字",
                "notes": "已有访谈纪要",
            },
        )

        self.assertEqual(response.status_code, 200)
        mock_create_project.assert_called_once_with(
            "demo",
            "strategy-consulting",
            "AI 战略规划",
            "高层决策者",
            "2026-04-01",
            "3000字",
            "已有访谈纪要",
        )
