import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.config import (
    Settings,
    get_default_managed_client_token,
    load_settings,
    normalize_settings_payload,
    save_settings,
)


class SettingsPersistenceTests(unittest.TestCase):
    def test_default_settings_use_managed_mode(self):
        settings = Settings()
        self.assertEqual(settings.mode, "managed")
        self.assertEqual(settings.managed_model, "gemini-3-flash")
        self.assertTrue(settings.managed_base_url)

    def test_save_and_load_round_trip_custom_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            settings = Settings(
                mode="custom",
                managed_base_url="https://managed.example/v1",
                managed_model="gemini-3-flash",
                custom_api_base="https://custom.example/v1",
                custom_api_key="secret",
                custom_model="gpt-4.1-mini",
            )

            with mock.patch("backend.config.get_user_config_dir", return_value=config_dir):
                save_settings(settings)
                loaded = load_settings()

        self.assertEqual(loaded.mode, "custom")
        self.assertEqual(loaded.custom_api_base, "https://custom.example/v1")
        self.assertEqual(loaded.custom_model, "gpt-4.1-mini")

    def test_managed_mode_uses_managed_runtime_aliases_even_if_custom_secret_exists(self):
        settings = Settings(
            mode="managed",
            managed_base_url="https://newapi.z0y0h.work/client/v1",
            managed_model="gemini-3-flash",
            managed_client_token="desktop-managed-token",
            custom_api_base="https://custom.example/v1",
            custom_api_key="secret",
            custom_model="gpt-4.1-mini",
            api_key="secret",
            api_base="https://custom.example/v1",
            model="gpt-4.1-mini",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            with mock.patch("backend.config.get_user_config_dir", return_value=config_dir):
                save_settings(settings)
                loaded = load_settings()

        self.assertEqual(loaded.api_base, "https://newapi.z0y0h.work/client/v1")
        self.assertEqual(loaded.model, "gemini-3-flash")
        self.assertEqual(loaded.api_key, "desktop-managed-token")
        self.assertEqual(loaded.custom_api_key, "secret")

    def test_managed_mode_can_read_dedicated_client_token_from_bundle_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            (base_dir / "managed_client_token.txt").write_text("dedicated-client-token", encoding="utf-8")

            with mock.patch("backend.config.get_base_path", return_value=base_dir):
                managed_token = get_default_managed_client_token()

        settings = Settings(
            mode="managed",
            managed_base_url="https://newapi.z0y0h.work/client/v1",
            managed_model="gemini-3-flash",
            managed_client_token=managed_token,
        )
        normalized = normalize_settings_payload(settings.model_dump())

        self.assertEqual(managed_token, "dedicated-client-token")
        self.assertEqual(normalized["api_key"], "dedicated-client-token")
