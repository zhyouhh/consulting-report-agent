import unittest
from unittest import mock

from fastapi.testclient import TestClient

import backend.main as main_module
from backend.skill import SkillEngine
from tests.test_auth_api import AuthApiTestBase


class ProjectCreateApiTests(unittest.TestCase):
    def setUp(self):
        # W2-B (T11c): the create route resolves uid via get_current_uid; auth off → "local".
        # In desktop/local mode (auth off) the server does NOT reallocate workspace_dir, so the
        # client payload (workspace_dir + initial_material_paths) reaches create_project verbatim —
        # which is exactly what this test asserts. Install a MagicMock engine for the 'local' tenant.
        self._prev_auth = getattr(main_module.app.state, "auth_required", True)
        main_module.app.state.auth_required = False
        self.engine = mock.MagicMock(spec=SkillEngine)
        with main_module._engines_guard:
            self._prev_engine = main_module._engines.get("local")
            main_module._engines["local"] = self.engine

    def tearDown(self):
        main_module.app.state.auth_required = self._prev_auth
        with main_module._engines_guard:
            if self._prev_engine is None:
                main_module._engines.pop("local", None)
            else:
                main_module._engines["local"] = self._prev_engine

    def test_create_project_route_passes_workspace_project_payload(self):
        mock_create_project = self.engine.create_project
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


class WebCreateTests(AuthApiTestBase):
    def _login(self):
        self.client.post("/api/auth/register", json={"username": "alice", "password": "pw-123456", "invite_code": "JOIN"})
        self.client.post("/api/auth/login", json={"username": "alice", "password": "pw-123456"})
        return self.client.get("/api/auth/me").json()["uid"]

    _BASE = {"name": "x", "project_type": "strategy", "theme": "t", "deadline": "2026-12-31", "expected_length": "1万字"}

    def test_web_rejects_client_path_and_allocates(self):
        uid = self._login()
        bad = self.client.post("/api/projects", json={**self._BASE, "workspace_dir": "/etc/evil"})
        self.assertEqual(bad.status_code, 400)
        ok = self.client.post("/api/projects", json=dict(self._BASE))
        self.assertEqual(ok.status_code, 200)
        from pathlib import Path
        from backend.tenant import user_projects_dir
        ws = Path(ok.json()["project"]["workspace_dir"])
        # 强断言：工作区是 user_projects_dir(uid) 的直接子目录，且叶子是 32 位 hex（防兄弟前缀蒙混）。
        self.assertEqual(ws.parent, user_projects_dir(uid))
        self.assertRegex(ws.name, r"^[0-9a-f]{32}$")

    def test_web_rejects_initial_material_paths(self):
        self._login()
        r = self.client.post("/api/projects", json={**self._BASE, "initial_material_paths": ["/etc/passwd"]})
        self.assertEqual(r.status_code, 400)

    def test_web_rejects_even_empty_client_workspace_dir(self):
        # 按字段是否发送拒绝：即便发空串也拒（契约=web 客户端不得带该字段）。
        self._login()
        r = self.client.post("/api/projects", json={**self._BASE, "workspace_dir": ""})
        self.assertEqual(r.status_code, 400)
