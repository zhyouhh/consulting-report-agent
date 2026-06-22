import importlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

# 复用必填体 helper（含 managed_model）。跨文件 import 会触发 import backend.main（可接受）。
from tests.test_settings_api import _settings_body


class _CsrfTestBase(unittest.TestCase):
    """web 态（auth_required=True）+ 显式 CRA_ALLOWED_ORIGIN 的隔离基类。
    self.client 默认带同源 Origin；缺失 Origin 用例须用 bare client（见 Step 3b）。"""

    _ORIGIN = "https://app.example.com"

    def setUp(self):
        self._tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self._env = mock.patch.dict(os.environ, {
            "CRA_DATA_ROOT": str(self._tmp),
            "CRA_INVITE_CODE": "JOIN",
            "CRA_ALLOWED_ORIGIN": self._ORIGIN,
        })
        self._env.start()
        from backend import accounts, config
        importlib.reload(config)
        importlib.reload(accounts)
        self._heal = mock.patch("backend.config.heal_stale_managed_model", side_effect=lambda s: (s, None))
        self._heal.start()
        import backend.main as m
        importlib.reload(m)
        self.m = m
        m.app.state.auth_required = True
        self._reset_module_singletons()
        self.client = TestClient(m.app)
        self.client.headers.update({"origin": self._ORIGIN})
        # 注册 + 登录拿 cookie（self.client 已带同源 Origin，注册/登录 POST 不被 CSRF 拦）。
        self.client.post("/api/auth/register",
                         json={"username": "alice", "password": "pw-123456", "invite_code": "JOIN"})
        self.client.post("/api/auth/login",
                         json={"username": "alice", "password": "pw-123456"})

    def _reset_module_singletons(self):
        from backend import chat as cm, independent_review as im
        cm._PROJECT_REQUEST_LOCKS.clear()
        cm._CONVERSATION_STATE_LOCKS.clear()
        cm._SEARCH_ROUTER_SINGLETON = None
        im._INDEPENDENT_REVIEW_LOCKS.clear()
        with im._REVIEW_SESSION_STORE._guard:
            im._REVIEW_SESSION_STORE._records.clear()

    def tearDown(self):
        self._heal.stop()
        self._env.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)


class CsrfTests(_CsrfTestBase):
    def test_cross_site_origin_post_rejected(self):
        resp = self.client.post("/api/settings",
                                headers={"origin": "https://evil.example.com"},
                                json=_settings_body())
        self.assertEqual(resp.status_code, 403)

    def test_same_site_origin_post_allowed(self):
        resp = self.client.post("/api/settings",
                                headers={"origin": self._ORIGIN},
                                json=_settings_body())
        self.assertNotEqual(resp.status_code, 403)

    def test_get_not_csrf_checked(self):
        resp = self.client.get("/api/auth/me", headers={"origin": "https://evil.example.com"})
        self.assertNotEqual(resp.status_code, 403)

    def test_missing_origin_and_referer_rejected_on_state_change(self):
        # self.client 带默认 Origin，headers={} 不会删它（httpx 合并默认头）。
        # 必须用全新、不带默认 Origin 的 bare client，复用已登录会话 cookie。
        bare = TestClient(self.m.app)
        bare.cookies.update(self.client.cookies)
        resp = bare.post("/api/settings", json=_settings_body())
        self.assertEqual(resp.status_code, 403)

    def test_referer_fallback_same_site_allowed(self):
        # 无 Origin 但 Referer 同源 → 退 Referer 校验通过。用 bare client 显式只发 Referer。
        bare = TestClient(self.m.app)
        bare.cookies.update(self.client.cookies)
        resp = bare.post("/api/settings",
                         headers={"referer": f"{self._ORIGIN}/some/path"},
                         json=_settings_body())
        self.assertNotEqual(resp.status_code, 403)

    def test_sse_chat_stream_same_origin_not_csrf_blocked(self):
        # /api/chat/stream 是 POST，同源 Origin 不应被 CSRF 403（可能因别的原因失败，但不是 403）。
        resp = self.client.post("/api/chat/stream",
                                headers={"origin": self._ORIGIN},
                                json={"project_id": "nonexistent", "message_text": "hi"})
        self.assertNotEqual(resp.status_code, 403)

    def test_cors_not_wildcard(self):
        import inspect
        from backend import main as m
        src = inspect.getsource(m)
        self.assertIn("allow_origins=list(allowed_origins())", src)
        self.assertNotRegex(src, r'allow_origins=\[\s*["\']\*["\']\s*\]')

    def test_production_loopback_origin_post_rejected(self):
        # 生产 web 态（auth_required=True + cookie_secure=True）不信任 dev/loopback 源：
        # 来自 loopback origin 的状态变更 POST 被 CSRF 403（只有 CRA_ALLOWED_ORIGIN 配的站点源放行）。
        self.m.app.state.cookie_secure = True
        resp = self.client.post("/api/settings",
                                headers={"origin": "http://localhost:3000"},
                                json=_settings_body())
        self.assertEqual(resp.status_code, 403)

    def test_nonproduction_loopback_origin_post_allowed(self):
        # 非生产（cookie_secure 未置/False，如本地 http 调试）仍放行 loopback 源，方便 vite dev。
        self.m.app.state.cookie_secure = False
        resp = self.client.post("/api/settings",
                                headers={"origin": "http://localhost:3000"},
                                json=_settings_body())
        self.assertNotEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
