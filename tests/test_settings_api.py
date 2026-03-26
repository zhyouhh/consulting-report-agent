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
