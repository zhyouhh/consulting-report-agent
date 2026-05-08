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

    def test_consulting_report_spec_requires_managed_search_pool_file(self):
        content = (ROOT / "consulting_report.spec").read_text(encoding="utf-8")
        self.assertIn("managed_search_pool.json", content)
        self.assertIn("validate_bundle_managed_search_pool", content)


class VersionInfoTests(unittest.TestCase):
    def test_version_info_file_exists_at_repo_root(self):
        repo = Path(__file__).resolve().parents[1]
        self.assertTrue((repo / "version_info.txt").is_file(),
                        "version_info.txt must exist at repo root")

    def test_version_info_contains_required_string_fields(self):
        repo = Path(__file__).resolve().parents[1]
        text = (repo / "version_info.txt").read_text(encoding="utf-8")
        for field in ("CompanyName", "FileDescription", "FileVersion",
                      "InternalName", "OriginalFilename", "ProductName",
                      "ProductVersion"):
            self.assertIn(field, text, f"version_info.txt missing {field}")
        self.assertIn("咨询报告写作助手", text)

    def test_consulting_report_spec_references_version_info(self):
        repo = Path(__file__).resolve().parents[1]
        text = (repo / "consulting_report.spec").read_text(encoding="utf-8")
        self.assertIn("version='version_info.txt'", text)


if __name__ == "__main__":
    unittest.main()
