import unittest
from unittest import mock

from fastapi.testclient import TestClient

import backend.main as main_module

from tests.test_auth_api import AuthApiTestBase


def _settings_body(**overrides):
    """拼完整必填体（mode + managed_model），custom 时补 custom_*。
    所有 settings/CSRF POST 测试复用，避免漏 managed_model 触发 422。"""
    body = {"mode": "managed", "managed_model": "deepseek-v4-pro"}
    body.update(overrides)
    return body


class SettingsIsolationTests(unittest.TestCase):
    def test_settings_isolated_per_uid(self):
        from backend.config import load_settings, save_settings, Settings
        import os, tempfile
        from unittest import mock
        tmp = os.path.realpath(tempfile.mkdtemp())
        with mock.patch.dict(os.environ, {"CRA_DATA_ROOT": tmp}):
            sa = Settings(); sa.custom_api_key = "A-KEY"; save_settings(sa, uid="ua")
            sb = Settings(); sb.custom_api_key = "B-KEY"; save_settings(sb, uid="ub")
            self.assertEqual(load_settings("ua").custom_api_key, "A-KEY")
            self.assertEqual(load_settings("ub").custom_api_key, "B-KEY")


class SettingsApiTests(AuthApiTestBase):
    def setUp(self):
        super().setUp()
        # Single-user semantics: get_current_uid → "local".
        self.m.app.state.auth_required = False
        # The endpoints read load_settings("local") from data_root/users/local/config.json;
        # preseed so GET has a deterministic baseline, and so per-test mutations are isolated.
        from backend.config import save_settings, Settings
        save_settings(Settings(), uid="local")
        self.client = TestClient(self.m.app)

    @property
    def main_module(self):
        return self.m

    def _save_local(self, settings):
        from backend.config import save_settings
        save_settings(settings, uid="local")

    def _load_local(self):
        from backend.config import load_settings
        return load_settings("local")

    # —— Task 3: managed_base_url 服务端只读 ——
    def test_normalize_forces_managed_base_url_constant(self):
        from backend.config import normalize_settings_payload, DEFAULT_MANAGED_BASE_URL
        out = normalize_settings_payload({"managed_base_url": "https://attacker.internal/v1"})
        self.assertEqual(out["managed_base_url"], DEFAULT_MANAGED_BASE_URL)
        self.assertEqual(out["api_base"], DEFAULT_MANAGED_BASE_URL)  # managed alias 也回常量

    def test_post_settings_without_managed_base_url_ok(self):
        # 前端不再发 managed_base_url（Task 18），带必填 managed_model 应 200 而非 422
        resp = self.client.post("/api/settings",
                                headers={"origin": "https://app.example.com"},
                                json=_settings_body())   # 不含 managed_base_url
        self.assertEqual(resp.status_code, 200)

    def test_post_settings_ignores_client_managed_base_url(self):
        from backend.config import DEFAULT_MANAGED_BASE_URL
        self.client.post("/api/settings",
                         headers={"origin": "https://app.example.com"},
                         json=_settings_body(managed_base_url="https://attacker.internal/v1"))
        s = self._load_local()
        self.assertEqual(s.managed_base_url, DEFAULT_MANAGED_BASE_URL)

    def test_get_settings_masks_custom_api_key(self):
        from backend.config import Settings
        s = Settings()
        s.custom_api_key = "very-secret"
        self._save_local(s)
        # normalize re-derives api_key from the managed client token at load time (non-empty) → masked.
        response = self.client.get("/api/settings")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["custom_api_key"], "***")
        self.assertEqual(payload["api_key"], "***")

    def test_get_settings_includes_custom_context_limit_override(self):
        from backend.config import Settings
        s = Settings()
        s.custom_context_limit_override = 48000
        self._save_local(s)

        response = self.client.get("/api/settings")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["custom_context_limit_override"], 48000)

    def test_update_settings_preserves_existing_custom_key_when_payload_is_masked(self):
        from backend.config import Settings
        s = Settings()
        s.custom_api_key = "very-secret"
        s.custom_api_base = "https://custom.example/v1"
        s.custom_model = "gpt-4.1-mini"
        self._save_local(s)

        response = self.client.post(
            "/api/settings",
            json={
                "mode": "custom",
                "managed_base_url": "https://newapi.z0y0h.work/client/v1",
                "managed_model": "gemini-3-flash",
                "custom_api_base": "https://custom.example/v1",
                "custom_api_key": "***",
                "custom_model": "gpt-4.1-mini",
            },
        )

        self.assertEqual(response.status_code, 200)
        # Masked key → existing custom_api_key preserved through the save round-trip on disk.
        self.assertEqual(self._load_local().custom_api_key, "very-secret")

    def test_update_settings_saves_custom_context_limit_override(self):
        with mock.patch.object(main_module, "save_settings") as save_settings_mock:
            response = self.client.post(
                "/api/settings",
                json={
                    "mode": "custom",
                    "managed_base_url": "https://newapi.z0y0h.work/client/v1",
                    "managed_model": "gemini-3-flash",
                    "custom_api_base": "https://custom.example/v1",
                    "custom_api_key": "new-secret",
                    "custom_model": "gpt-4.1-mini",
                    "custom_context_limit_override": 32000,
                },
            )

        self.assertEqual(response.status_code, 200)
        save_settings_mock.assert_called_once()
        self.assertEqual(save_settings_mock.call_args.kwargs.get("uid"), "local")
        saved_settings = save_settings_mock.call_args.args[0]
        self.assertEqual(saved_settings.custom_context_limit_override, 32000)

    def test_update_settings_clamps_too_small_custom_context_limit_override_to_4096(self):
        with mock.patch.object(main_module, "save_settings") as save_settings_mock:
            response = self.client.post(
                "/api/settings",
                json={
                    "mode": "custom",
                    "managed_base_url": "https://newapi.z0y0h.work/client/v1",
                    "managed_model": "gemini-3-flash",
                    "custom_api_base": "https://custom.example/v1",
                    "custom_api_key": "new-secret",
                    "custom_model": "gpt-4.1-mini",
                    "custom_context_limit_override": 3000,
                },
            )

        self.assertEqual(response.status_code, 200)
        save_settings_mock.assert_called_once()
        self.assertEqual(save_settings_mock.call_args.kwargs.get("uid"), "local")
        saved_settings = save_settings_mock.call_args.args[0]
        self.assertEqual(saved_settings.custom_context_limit_override, 4096)

    def test_update_settings_preserves_existing_custom_context_limit_override_when_omitted(self):
        from backend.config import Settings
        s = Settings()
        s.custom_api_key = "very-secret"
        s.custom_api_base = "https://custom.example/v1"
        s.custom_model = "gpt-4.1-mini"
        s.custom_context_limit_override = 64000
        self._save_local(s)

        with mock.patch.object(main_module, "save_settings") as save_settings_mock:
            response = self.client.post(
                "/api/settings",
                json={
                    "mode": "custom",
                    "managed_base_url": "https://newapi.z0y0h.work/client/v1",
                    "managed_model": "gemini-3-flash",
                    "custom_api_base": "https://custom.example/v1",
                    "custom_api_key": "***",
                    "custom_model": "gpt-4.1-mini",
                },
            )

        self.assertEqual(response.status_code, 200)
        save_settings_mock.assert_called_once()
        self.assertEqual(save_settings_mock.call_args.kwargs.get("uid"), "local")
        saved_settings = save_settings_mock.call_args.args[0]
        self.assertEqual(saved_settings.custom_context_limit_override, 64000)


    def test_settings_endpoint_requires_auth(self):
        # auth on + no session cookie -> 401
        self.m.app.state.auth_required = True
        client = TestClient(self.m.app)  # fresh client carries no cookies
        self.assertEqual(client.get("/api/settings").status_code, 401)

    def test_settings_isolated_between_authenticated_users(self):
        from backend import accounts
        self.m.app.state.auth_required = True
        ua = accounts.create_user("ua", "pw-123456")
        ub = accounts.create_user("ub", "pw-123456")
        ta = accounts.create_session(ua)
        tb = accounts.create_session(ub)
        ca = TestClient(self.m.app); ca.cookies.set("cra_session", ta)
        cb = TestClient(self.m.app); cb.cookies.set("cra_session", tb)
        # custom_api_base is NOT masked by GET → observe per-uid isolation through the endpoint.
        body = lambda base: {
            "mode": "managed",
            "managed_base_url": "https://x",
            "managed_model": "m",
            "custom_api_base": base,
            "custom_api_key": "",
            "custom_model": "",
        }
        self.assertEqual(ca.post("/api/settings", json=body("A-BASE")).status_code, 200)
        self.assertEqual(cb.post("/api/settings", json=body("B-BASE")).status_code, 200)
        self.assertEqual(ca.get("/api/settings").json()["custom_api_base"], "A-BASE")
        self.assertEqual(cb.get("/api/settings").json()["custom_api_base"], "B-BASE")
