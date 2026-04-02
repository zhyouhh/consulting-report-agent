import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class PackagingDocsTests(unittest.TestCase):
    def test_build_script_uses_windows_null_device_and_consulting_report_spec(self):
        content = (ROOT / "build.bat").read_text(encoding="utf-8")
        self.assertIn(">nul", content.lower())
        self.assertIn("pyinstaller consulting_report.spec", content)
        self.assertNotIn("/dev/null", content)
        self.assertIn("/client/v1/models", content)
        self.assertTrue(
            "CONSULTING_REPORT_MANAGED_CLIENT_TOKEN" in content
            or "managed_client_token.txt" in content
        )

    def test_build_docs_describe_managed_default_and_windows_first_release(self):
        for doc_name in ["BUILD.md", "WINDOWS_BUILD.md"]:
            content = (ROOT / doc_name).read_text(encoding="utf-8")
            self.assertIn("Windows", content)
            self.assertIn("默认通道", content)
            self.assertIn("自定义 API", content)
            self.assertIn("可审草稿", content)
            self.assertIn("/client/v1/models", content)
            self.assertIn("client token", content)

    def test_readme_describes_managed_mode_without_claiming_word_pdf_export(self):
        content = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("默认通道", content)
        self.assertIn("自定义 API", content)
        self.assertIn("Windows", content)
        self.assertIn("可审草稿", content)
        self.assertNotIn("Word/PDF", content)


if __name__ == "__main__":
    unittest.main()
