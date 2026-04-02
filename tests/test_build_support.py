import tempfile
import unittest
from pathlib import Path
from unittest import mock

from build_support import (
    require_non_empty_bundle_text_file,
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


if __name__ == "__main__":
    unittest.main()
