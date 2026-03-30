import unittest
from unittest import mock

from fastapi.testclient import TestClient

import backend.main as main_module


class SettingsApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main_module.app)
        self.original = main_module.settings.model_dump()

    def tearDown(self):
        for key, value in self.original.items():
            setattr(main_module.settings, key, value)

    def test_get_settings_masks_custom_api_key(self):
        main_module.settings.mode = "custom"
        main_module.settings.custom_api_key = "very-secret"
        main_module.settings.api_key = "very-secret"
        response = self.client.get("/api/settings")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["custom_api_key"], "***")
        self.assertEqual(payload["api_key"], "***")

    def test_get_settings_includes_custom_context_limit_override(self):
        main_module.settings.custom_context_limit_override = 48000

        response = self.client.get("/api/settings")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["custom_context_limit_override"], 48000)

    def test_update_settings_preserves_existing_custom_key_when_payload_is_masked(self):
        main_module.settings.mode = "custom"
        main_module.settings.custom_api_key = "very-secret"
        main_module.settings.custom_api_base = "https://custom.example/v1"
        main_module.settings.custom_model = "gpt-4.1-mini"
        main_module.settings.api_key = "very-secret"
        main_module.settings.api_base = "https://custom.example/v1"
        main_module.settings.model = "gpt-4.1-mini"

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
        self.assertEqual(main_module.settings.custom_api_key, "very-secret")
        self.assertEqual(main_module.settings.api_key, "very-secret")

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
        self.assertEqual(main_module.settings.custom_context_limit_override, 32000)
        save_settings_mock.assert_called_once()
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
        self.assertEqual(main_module.settings.custom_context_limit_override, 4096)
        save_settings_mock.assert_called_once()
        saved_settings = save_settings_mock.call_args.args[0]
        self.assertEqual(saved_settings.custom_context_limit_override, 4096)

    def test_update_settings_preserves_existing_custom_context_limit_override_when_omitted(self):
        main_module.settings.mode = "custom"
        main_module.settings.custom_api_key = "very-secret"
        main_module.settings.custom_api_base = "https://custom.example/v1"
        main_module.settings.custom_model = "gpt-4.1-mini"
        main_module.settings.custom_context_limit_override = 64000
        main_module.settings.api_key = "very-secret"
        main_module.settings.api_base = "https://custom.example/v1"
        main_module.settings.model = "gpt-4.1-mini"

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
        self.assertEqual(main_module.settings.custom_context_limit_override, 64000)
        save_settings_mock.assert_called_once()
        saved_settings = save_settings_mock.call_args.args[0]
        self.assertEqual(saved_settings.custom_context_limit_override, 64000)
