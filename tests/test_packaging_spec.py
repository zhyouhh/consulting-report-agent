import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class PackagingSpecTests(unittest.TestCase):
    def test_consulting_report_spec_is_windows_focused(self):
        content = (ROOT / "consulting_report.spec").read_text(encoding="utf-8")
        self.assertIn("webview.platforms.winforms", content)
        self.assertIn("webview.platforms.edgechromium", content)
        self.assertIn("'webview.platforms.qt'", content)
        self.assertIn("'webview.platforms.gtk'", content)
        self.assertIn("'webview.platforms.cocoa'", content)

    def test_consulting_report_spec_requires_non_empty_managed_token_file(self):
        content = (ROOT / "consulting_report.spec").read_text(encoding="utf-8")
        self.assertIn("require_non_empty_bundle_text_file", content)
        self.assertIn("managed_client_token.txt", content)


if __name__ == "__main__":
    unittest.main()
