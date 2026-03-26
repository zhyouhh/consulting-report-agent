import unittest

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
