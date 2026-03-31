import tempfile
import unittest
from pathlib import Path

from build_support import require_non_empty_bundle_text_file


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


if __name__ == "__main__":
    unittest.main()
