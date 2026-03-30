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
        self.assertIn("search.z0y0h.work", settings.managed_search_api_url)

    def test_save_and_load_preserves_custom_fields_but_starts_in_managed_mode(self):
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

        self.assertEqual(loaded.mode, "managed")
        self.assertEqual(loaded.custom_api_base, "https://custom.example/v1")
        self.assertEqual(loaded.custom_model, "gpt-4.1-mini")

    def test_save_and_load_preserves_custom_context_limit_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            settings = Settings(
                custom_context_limit_override=32000,
            )

            with mock.patch("backend.config.get_user_config_dir", return_value=config_dir):
                save_settings(settings)
                loaded = load_settings()

        self.assertEqual(loaded.custom_context_limit_override, 32000)

    def test_managed_mode_uses_managed_runtime_aliases_even_if_custom_secret_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            bundle_dir = Path(tmpdir) / "bundle"
            bundle_dir.mkdir()
            (bundle_dir / "managed_client_token.txt").write_text("desktop-managed-token", encoding="utf-8")

            settings = Settings(
                mode="managed",
                managed_base_url="https://newapi.z0y0h.work/client/v1",
                managed_model="gemini-3-flash",
                managed_client_token="outdated-config-token",
                custom_api_base="https://custom.example/v1",
                custom_api_key="secret",
                custom_model="gpt-4.1-mini",
                api_key="secret",
                api_base="https://custom.example/v1",
                model="gpt-4.1-mini",
            )

            with mock.patch("backend.config.get_user_config_dir", return_value=config_dir), \
                    mock.patch("backend.config.get_base_path", return_value=bundle_dir):
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

    def test_old_desktop_config_uses_runtime_paths_runtime_token_and_resets_mode_to_managed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config-home"
            config_dir.mkdir(parents=True)
            bundle_dir = Path(tmpdir) / "bundle"
            (bundle_dir / "skill").mkdir(parents=True)
            (bundle_dir / "managed_client_token.txt").write_text("bundle-client-token", encoding="utf-8")
            (config_dir / "config.json").write_text(
                """
                {
                  "config_version": 3,
                  "mode": "custom",
                  "managed_base_url": "https://newapi.z0y0h.work/client/v1",
                  "managed_model": "gemini-3-flash",
                  "managed_client_token": "managed",
                  "custom_api_base": "https://custom.example/v1",
                  "custom_api_key": "secret",
                  "custom_model": "gpt-4.1-mini",
                  "projects_dir": "D:\\\\CCprojects\\\\consulting-report-agent\\\\projects",
                  "skill_dir": "D:\\\\CCprojects\\\\consulting-report-agent\\\\skill"
                }
                """.strip(),
                encoding="utf-8",
            )

            with mock.patch("backend.config.get_user_config_dir", return_value=config_dir), \
                    mock.patch("backend.config.get_base_path", return_value=bundle_dir):
                loaded = load_settings()

        self.assertEqual(loaded.mode, "managed")
        self.assertEqual(loaded.managed_client_token, "bundle-client-token")
        self.assertEqual(loaded.custom_api_base, "https://custom.example/v1")
        self.assertEqual(loaded.custom_model, "gpt-4.1-mini")
        self.assertEqual(loaded.projects_dir, config_dir / "projects")
        self.assertEqual(loaded.skill_dir, bundle_dir / "skill")

    def test_save_settings_does_not_persist_runtime_or_session_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            settings = Settings(
                mode="custom",
                managed_base_url="https://newapi.z0y0h.work/client/v1",
                managed_model="gemini-3-flash",
                custom_api_base="https://custom.example/v1",
                custom_api_key="secret",
                custom_model="gpt-4.1-mini",
                projects_dir=config_dir / "projects",
                skill_dir=Path(tmpdir) / "bundle" / "skill",
            )

            with mock.patch("backend.config.get_user_config_dir", return_value=config_dir):
                save_settings(settings)

            saved = (config_dir / "config.json").read_text(encoding="utf-8")

        self.assertNotIn("projects_dir", saved)
        self.assertNotIn("skill_dir", saved)
        self.assertNotIn("managed_client_token", saved)
        self.assertNotIn('"mode"', saved)
        self.assertNotIn('"api_key"', saved)
        self.assertNotIn('"api_base"', saved)
        self.assertNotIn('"model"', saved)

    def test_load_old_config_without_custom_context_limit_override_uses_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            (config_dir / "config.json").write_text(
                """
                {
                  "config_version": 4,
                  "managed_base_url": "https://newapi.z0y0h.work/client/v1",
                  "managed_model": "gemini-3-flash",
                  "managed_search_api_url": "https://search.z0y0h.work/search",
                  "custom_api_base": "https://custom.example/v1",
                  "custom_api_key": "secret",
                  "custom_model": "gpt-4.1-mini"
                }
                """.strip(),
                encoding="utf-8",
            )

            with mock.patch("backend.config.get_user_config_dir", return_value=config_dir):
                loaded = load_settings()

        self.assertIsNone(loaded.custom_context_limit_override)
