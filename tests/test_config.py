import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.config import (
    Settings,
    get_default_managed_client_token,
    get_managed_search_pool_path,
    get_search_cache_path,
    get_search_runtime_state_path,
    heal_stale_managed_model,
    load_settings,
    load_managed_search_pool_config,
    normalize_settings_payload,
    save_settings,
)


class SettingsPersistenceTests(unittest.TestCase):
    def test_default_settings_use_managed_mode(self):
        settings = Settings()
        self.assertEqual(settings.mode, "managed")
        self.assertEqual(settings.managed_model, "deepseek-v4-pro")
        self.assertTrue(settings.managed_base_url)
        self.assertIn("search.z0y0h.work", settings.managed_search_api_url)

    def test_default_settings_populate_managed_runtime_aliases_on_first_launch(self):
        settings = Settings()

        self.assertEqual(settings.api_base, settings.managed_base_url)
        self.assertEqual(settings.model, settings.managed_model)
        self.assertEqual(settings.api_key, settings.managed_client_token)

    def test_save_and_load_preserves_custom_fields_but_starts_in_managed_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            settings = Settings(
                mode="custom",
                managed_base_url="https://managed.example/v1",
                managed_model="deepseek-v4-pro",
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
                managed_model="deepseek-v4-pro",
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
        self.assertEqual(loaded.model, "deepseek-v4-pro")
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
                    managed_model="deepseek-v4-pro",
                    managed_client_token=managed_token,
                )
                normalized = normalize_settings_payload(settings.model_dump())

            self.assertEqual(managed_token, "dedicated-client-token")
            self.assertEqual(normalized["api_key"], "dedicated-client-token")

    def test_managed_mode_strips_utf8_bom_from_bundle_token_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            (base_dir / "managed_client_token.txt").write_bytes(
                b"\xef\xbb\xbfdedicated-client-token"
            )

            with mock.patch("backend.config.get_base_path", return_value=base_dir):
                managed_token = get_default_managed_client_token()

        self.assertEqual(managed_token, "dedicated-client-token")

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
                  "managed_model": "deepseek-v4-pro",
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
                managed_model="deepseek-v4-pro",
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
                  "managed_model": "deepseek-v4-pro",
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

    def test_get_managed_search_pool_path_ignores_env_override_at_runtime(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle_dir = Path(tmpdir)
            expected = bundle_dir / "managed_search_pool.json"
            expected.write_text("{}", encoding="utf-8")

            with mock.patch.dict(
                "os.environ",
                {"CONSULTING_REPORT_MANAGED_SEARCH_POOL_FILE": str(bundle_dir / "portable-search-pool.json")},
                clear=False,
            ), mock.patch("backend.config.get_base_path", return_value=bundle_dir):
                resolved = get_managed_search_pool_path()

        self.assertEqual(resolved, expected)

    def test_load_managed_search_pool_config_reads_routing_and_limits(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle_dir = Path(tmpdir)
            (bundle_dir / "managed_search_pool.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "providers": {
                            "serper": {
                                "enabled": True,
                                "api_key": "serper-key",
                                "weight": 5,
                                "minute_limit": 60,
                                "daily_soft_limit": 1200,
                                "cooldown_seconds": 180,
                            },
                            "brave": {
                                "enabled": True,
                                "api_key": "brave-key",
                                "weight": 3,
                                "minute_limit": 30,
                                "daily_soft_limit": 600,
                                "cooldown_seconds": 180,
                            },
                        },
                        "routing": {
                            "primary": ["serper", "brave"],
                            "secondary": [],
                            "native_fallback": True,
                        },
                        "limits": {
                            "per_turn_searches": 2,
                            "project_minute_limit": 10,
                            "global_minute_limit": 20,
                            "memory_cache_ttl_seconds": 21600,
                            "project_cache_ttl_seconds": 86400,
                        },
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch("backend.config.get_base_path", return_value=bundle_dir):
                config = load_managed_search_pool_config()

        self.assertEqual(config.routing.primary, ["serper", "brave"])
        self.assertEqual(config.providers["serper"].minute_limit, 60)
        self.assertEqual(config.limits.project_cache_ttl_seconds, 86400)
        # 单 key 写法向后兼容：api_keys 自动回填为单元素元组。
        self.assertEqual(config.providers["serper"].api_key, "serper-key")
        self.assertEqual(config.providers["serper"].api_keys, ("serper-key",))

    def test_load_managed_search_pool_config_reads_multi_key_api_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle_dir = Path(tmpdir)
            (bundle_dir / "managed_search_pool.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "providers": {
                            "serper": {
                                "enabled": True,
                                "api_keys": ["k1", "k2", "k3"],
                                "weight": 5,
                                "minute_limit": 60,
                                "daily_soft_limit": 1200,
                                "cooldown_seconds": 180,
                            },
                        },
                        "routing": {
                            "primary": ["serper"],
                            "secondary": [],
                            "native_fallback": True,
                        },
                        "limits": {
                            "per_turn_searches": 5,
                            "project_minute_limit": 30,
                            "global_minute_limit": 60,
                            "memory_cache_ttl_seconds": 21600,
                            "project_cache_ttl_seconds": 86400,
                        },
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch("backend.config.get_base_path", return_value=bundle_dir):
                config = load_managed_search_pool_config()

        self.assertEqual(config.providers["serper"].api_keys, ("k1", "k2", "k3"))
        # api_key 恒为首个 key，供向后兼容的单 key 读取方使用。
        self.assertEqual(config.providers["serper"].api_key, "k1")
        self.assertEqual(config.limits.per_turn_searches, 5)

    def test_load_managed_search_pool_config_accepts_utf8_bom_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle_dir = Path(tmpdir)
            payload = json.dumps(
                {
                    "version": 1,
                    "providers": {
                        "serper": {
                            "enabled": True,
                            "api_key": "serper-key",
                            "weight": 5,
                            "minute_limit": 60,
                            "daily_soft_limit": 1200,
                            "cooldown_seconds": 180,
                        },
                    },
                    "routing": {
                        "primary": ["serper"],
                        "secondary": [],
                        "native_fallback": True,
                    },
                    "limits": {
                        "per_turn_searches": 2,
                        "project_minute_limit": 10,
                        "global_minute_limit": 20,
                        "memory_cache_ttl_seconds": 21600,
                        "project_cache_ttl_seconds": 86400,
                    },
                }
            ).encode("utf-8")
            (bundle_dir / "managed_search_pool.json").write_bytes(b"\xef\xbb\xbf" + payload)

            with mock.patch("backend.config.get_base_path", return_value=bundle_dir):
                config = load_managed_search_pool_config()

        self.assertEqual(config.providers["serper"].api_key, "serper-key")

    def test_load_managed_search_pool_config_rejects_unknown_routing_provider(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle_dir = Path(tmpdir)
            (bundle_dir / "managed_search_pool.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "providers": {
                            "serper": {
                                "enabled": True,
                                "api_key": "serper-key",
                                "weight": 5,
                                "minute_limit": 60,
                                "daily_soft_limit": 1200,
                                "cooldown_seconds": 180,
                            },
                        },
                        "routing": {
                            "primary": ["serper", "ghost"],
                            "secondary": [],
                            "native_fallback": True,
                        },
                        "limits": {
                            "per_turn_searches": 2,
                            "project_minute_limit": 10,
                            "global_minute_limit": 20,
                            "memory_cache_ttl_seconds": 21600,
                            "project_cache_ttl_seconds": 86400,
                        },
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch("backend.config.get_base_path", return_value=bundle_dir):
                with self.assertRaises(ValueError) as ctx:
                    load_managed_search_pool_config()

        self.assertIn("ghost", str(ctx.exception))

    def test_load_managed_search_pool_config_rejects_non_boolean_enabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle_dir = Path(tmpdir)
            (bundle_dir / "managed_search_pool.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "providers": {
                            "serper": {
                                "enabled": "false",
                                "api_key": "serper-key",
                                "weight": 5,
                                "minute_limit": 60,
                                "daily_soft_limit": 1200,
                                "cooldown_seconds": 180,
                            },
                        },
                        "routing": {
                            "primary": ["serper"],
                            "secondary": [],
                            "native_fallback": True,
                        },
                        "limits": {
                            "per_turn_searches": 2,
                            "project_minute_limit": 10,
                            "global_minute_limit": 20,
                            "memory_cache_ttl_seconds": 21600,
                            "project_cache_ttl_seconds": 86400,
                        },
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch("backend.config.get_base_path", return_value=bundle_dir):
                with self.assertRaises(ValueError) as ctx:
                    load_managed_search_pool_config()

        self.assertIn("enabled", str(ctx.exception))

    def test_load_managed_search_pool_config_rejects_non_boolean_native_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle_dir = Path(tmpdir)
            (bundle_dir / "managed_search_pool.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "providers": {
                            "serper": {
                                "enabled": True,
                                "api_key": "serper-key",
                                "weight": 5,
                                "minute_limit": 60,
                                "daily_soft_limit": 1200,
                                "cooldown_seconds": 180,
                            },
                        },
                        "routing": {
                            "primary": ["serper"],
                            "secondary": [],
                            "native_fallback": "false",
                        },
                        "limits": {
                            "per_turn_searches": 2,
                            "project_minute_limit": 10,
                            "global_minute_limit": 20,
                            "memory_cache_ttl_seconds": 21600,
                            "project_cache_ttl_seconds": 86400,
                        },
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch("backend.config.get_base_path", return_value=bundle_dir):
                with self.assertRaises(ValueError) as ctx:
                    load_managed_search_pool_config()

        self.assertIn("native_fallback", str(ctx.exception))

    def test_get_search_runtime_state_path_uses_user_config_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)

            with mock.patch("backend.config.get_user_config_dir", return_value=config_dir):
                resolved = get_search_runtime_state_path()

        self.assertEqual(resolved, config_dir / "search_runtime_state.json")

    def test_get_search_cache_path_uses_user_config_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)

            with mock.patch("backend.config.get_user_config_dir", return_value=config_dir):
                resolved = get_search_cache_path()

        self.assertEqual(resolved, config_dir / "search_cache.json")


class VisionSettingsTests(unittest.TestCase):
    def test_settings_has_vision_defaults(self):
        from backend.config import Settings
        s = Settings()
        self.assertEqual(s.managed_vision_model, "Qwen/Qwen3-VL-8B-Instruct")
        self.assertTrue(s.vision_enabled)

    def test_legacy_config_without_vision_fields_loads(self):
        from backend.config import normalize_settings_payload
        out = normalize_settings_payload({"mode": "managed"})
        self.assertIn("managed_vision_model", out)
        self.assertIn("vision_enabled", out)
        self.assertEqual(out["managed_vision_model"], "Qwen/Qwen3-VL-8B-Instruct")
        self.assertTrue(out["vision_enabled"])


class HealStaleManagedModelTests(unittest.TestCase):
    """Auto-heal: when stored managed_model is no longer in proxy /v1/models,
    swap to first available so old configs survive an exe upgrade with a
    renamed managed model.
    """

    def _models_payload(self, *ids: str) -> bytes:
        return json.dumps({
            "object": "list",
            "data": [{"id": m, "object": "model"} for m in ids],
        }).encode("utf-8")

    def _make_settings(self, **overrides) -> Settings:
        defaults = dict(
            mode="managed",
            managed_base_url="https://newapi.example/client/v1",
            managed_model="gemini-3-flash",
            managed_client_token="dummy-token",
        )
        defaults.update(overrides)
        return Settings(**defaults)

    def test_no_change_when_stored_model_is_in_allowed_list(self):
        settings = self._make_settings(managed_model="deepseek-v4-pro")
        calls = []

        def fake_fetch(url, headers, timeout):
            calls.append((url, headers, timeout))
            return self._models_payload("deepseek-v4-pro", "deepseek-v4-flash")

        updated, msg = heal_stale_managed_model(settings, http_fetch=fake_fetch)

        self.assertIs(updated, settings)
        self.assertIsNone(msg)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "https://newapi.example/client/v1/models")
        self.assertEqual(calls[0][1]["Authorization"], "Bearer dummy-token")

    def test_swaps_stale_model_to_first_allowed_and_returns_message(self):
        settings = self._make_settings(managed_model="gemini-3-flash")

        def fake_fetch(url, headers, timeout):
            return self._models_payload("deepseek-v4-pro", "deepseek-v4-flash")

        updated, msg = heal_stale_managed_model(settings, http_fetch=fake_fetch)

        self.assertIsNot(updated, settings)
        self.assertEqual(updated.managed_model, "deepseek-v4-pro")
        self.assertEqual(updated.model, "deepseek-v4-pro")
        # 原对象不可被就地修改
        self.assertEqual(settings.managed_model, "gemini-3-flash")
        self.assertIn("gemini-3-flash", msg)
        self.assertIn("deepseek-v4-pro", msg)

    def test_network_failure_returns_settings_unchanged(self):
        settings = self._make_settings(managed_model="gemini-3-flash")

        def fake_fetch(url, headers, timeout):
            raise ConnectionError("dns fail")

        updated, msg = heal_stale_managed_model(settings, http_fetch=fake_fetch)

        self.assertIs(updated, settings)
        self.assertIsNone(msg)

    def test_malformed_json_returns_settings_unchanged(self):
        settings = self._make_settings()

        def fake_fetch(url, headers, timeout):
            return b"not-json-at-all"

        updated, msg = heal_stale_managed_model(settings, http_fetch=fake_fetch)

        self.assertIs(updated, settings)
        self.assertIsNone(msg)

    def test_empty_allowed_list_returns_settings_unchanged(self):
        settings = self._make_settings()

        def fake_fetch(url, headers, timeout):
            return self._models_payload()

        updated, msg = heal_stale_managed_model(settings, http_fetch=fake_fetch)

        self.assertIs(updated, settings)
        self.assertIsNone(msg)

    def test_custom_mode_skips_check_entirely(self):
        settings = Settings(
            mode="custom",
            managed_model="gemini-3-flash",
            custom_api_base="https://custom.example/v1",
            custom_api_key="secret",
            custom_model="gpt-4.1-mini",
        )
        calls = []

        def fake_fetch(url, headers, timeout):
            calls.append(url)
            return self._models_payload("anything")

        updated, msg = heal_stale_managed_model(settings, http_fetch=fake_fetch)

        self.assertIs(updated, settings)
        self.assertIsNone(msg)
        self.assertEqual(calls, [])  # 不应触发 HTTP

    def test_empty_managed_base_url_returns_settings_unchanged(self):
        settings = self._make_settings(managed_base_url="")

        def fake_fetch(url, headers, timeout):
            raise AssertionError("should not fetch when base url is empty")

        updated, msg = heal_stale_managed_model(settings, http_fetch=fake_fetch)

        self.assertIs(updated, settings)
        self.assertIsNone(msg)
