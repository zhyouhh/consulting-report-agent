import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from build_support import (
    require_non_empty_bundle_text_file,
    validate_bundle_managed_search_pool,
    validate_bundle_managed_client_token,
)


class BuildSupportTests(unittest.TestCase):
    def test_require_non_empty_bundle_text_file_returns_existing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            token_file = root / "managed_client_token.txt"
            token_file.write_text("desktop-token", encoding="utf-8")

            resolved = require_non_empty_bundle_text_file(root, "managed_client_token.txt")

        self.assertEqual(resolved, token_file)

    def test_require_non_empty_bundle_text_file_rejects_missing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            with self.assertRaises(FileNotFoundError) as ctx:
                require_non_empty_bundle_text_file(root, "managed_client_token.txt")

        self.assertIn("managed_client_token.txt", str(ctx.exception))
        self.assertIn("打包", str(ctx.exception))

    def test_require_non_empty_bundle_text_file_rejects_blank_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            token_file = root / "managed_client_token.txt"
            token_file.write_text("   \n", encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                require_non_empty_bundle_text_file(root, "managed_client_token.txt")

        self.assertIn("managed_client_token.txt", str(ctx.exception))
        self.assertIn("为空", str(ctx.exception))

    def test_require_non_empty_bundle_text_file_accepts_absolute_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            token_file = root / "portable-search-pool.json"
            token_file.write_text("non-empty", encoding="utf-8")

            resolved = require_non_empty_bundle_text_file(root, str(token_file))

        self.assertEqual(resolved, token_file)

    @mock.patch("build_support.requests.get")
    def test_validate_bundle_managed_client_token_accepts_token_that_can_list_models(self, mock_get):
        mock_get.return_value = mock.Mock(status_code=200, text='{"object":"list"}')

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            token_file = root / "managed_client_token.txt"
            token_file.write_text("client-token", encoding="utf-8")

            validate_bundle_managed_client_token(
                root,
                "managed_client_token.txt",
                "https://newapi.z0y0h.work/client/v1/models",
            )

        mock_get.assert_called_once()
        self.assertEqual(
            mock_get.call_args.kwargs["headers"]["Authorization"],
            "Bearer client-token",
        )

    @mock.patch("build_support.requests.get")
    def test_validate_bundle_managed_client_token_rejects_invalid_client_token(self, mock_get):
        mock_get.return_value = mock.Mock(
            status_code=401,
            text='{"detail":"invalid bearer token"}',
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            token_file = root / "managed_client_token.txt"
            token_file.write_text("upstream-key-by-mistake", encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                validate_bundle_managed_client_token(
                    root,
                    "managed_client_token.txt",
                    "https://newapi.z0y0h.work/client/v1/models",
                )

        self.assertIn("invalid bearer token", str(ctx.exception))
        self.assertIn("client token", str(ctx.exception).lower())

    def test_validate_bundle_managed_search_pool_rejects_missing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            with self.assertRaises(FileNotFoundError) as ctx:
                validate_bundle_managed_search_pool(root, "managed_search_pool.json")

        self.assertIn("managed_search_pool.json", str(ctx.exception))
        self.assertIn("打包", str(ctx.exception))

    def test_validate_bundle_managed_search_pool_accepts_enabled_provider_with_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "managed_search_pool.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "providers": {
                            "serper": {
                                "enabled": True,
                                "api_key": "key",
                                "weight": 5,
                                "minute_limit": 60,
                                "daily_soft_limit": 1200,
                                "cooldown_seconds": 180,
                            }
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

            resolved = validate_bundle_managed_search_pool(root, "managed_search_pool.json")

        self.assertEqual(resolved, root / "managed_search_pool.json")

    def test_validate_bundle_managed_search_pool_rejects_enabled_provider_without_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "managed_search_pool.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "providers": {
                            "serper": {
                                "enabled": True,
                                "api_key": "",
                                "weight": 5,
                                "minute_limit": 60,
                                "daily_soft_limit": 1200,
                                "cooldown_seconds": 180,
                            }
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

            with self.assertRaises(ValueError) as ctx:
                validate_bundle_managed_search_pool(root, "managed_search_pool.json")

        self.assertIn("serper", str(ctx.exception))
        self.assertIn("api_key", str(ctx.exception))

    def test_validate_bundle_managed_search_pool_rejects_file_that_runtime_loader_would_reject(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "managed_search_pool.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "providers": {
                            "serper": {
                                "enabled": True,
                                "api_key": "key",
                                "weight": 5,
                            }
                        },
                        "routing": {
                            "primary": ["serper"],
                            "secondary": [],
                            "native_fallback": True,
                        },
                        "limits": {
                            "per_turn_searches": 2,
                        },
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                validate_bundle_managed_search_pool(root, "managed_search_pool.json")

    def test_validate_bundle_managed_search_pool_accepts_utf8_bom_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            payload = json.dumps(
                {
                    "version": 1,
                    "providers": {
                        "serper": {
                            "enabled": True,
                            "api_key": "key",
                            "weight": 5,
                            "minute_limit": 60,
                            "daily_soft_limit": 1200,
                            "cooldown_seconds": 180,
                        }
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
            (root / "managed_search_pool.json").write_bytes(b"\xef\xbb\xbf" + payload)

            resolved = validate_bundle_managed_search_pool(root, "managed_search_pool.json")

        self.assertEqual(resolved, root / "managed_search_pool.json")

    def test_validate_bundle_managed_search_pool_accepts_override_file_with_noncanonical_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            override = root / "portable-search-pool.json"
            override.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "providers": {
                            "serper": {
                                "enabled": True,
                                "api_key": "key",
                                "weight": 5,
                                "minute_limit": 60,
                                "daily_soft_limit": 1200,
                                "cooldown_seconds": 180,
                            }
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

            resolved = validate_bundle_managed_search_pool(root, str(override))

        self.assertEqual(resolved, override)


if __name__ == "__main__":
    unittest.main()
