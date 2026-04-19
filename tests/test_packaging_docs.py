import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class PackagingDocsTests(unittest.TestCase):
    def test_build_script_uses_windows_null_device_and_consulting_report_spec(self):
        wrapper = (ROOT / "build.bat").read_text(encoding="utf-8")
        script = (ROOT / "build.ps1").read_text(encoding="utf-8")
        self.assertIn("build.ps1", wrapper)
        self.assertIn("consulting_report.spec", script)
        self.assertIn("/client/v1/models", script)
        self.assertIn(".venv", script)
        self.assertIn('"python"', script.lower())
        self.assertIn('"venv"', script.lower())
        self.assertIn("managed_client_token.txt", script)
        self.assertIn("managed_search_pool.json", script)

    def test_build_docs_describe_managed_default_and_windows_first_release(self):
        for doc_name in ["BUILD.md", "WINDOWS_BUILD.md"]:
            content = (ROOT / doc_name).read_text(encoding="utf-8")
            self.assertIn("Windows", content)
            self.assertIn("默认通道", content)
            self.assertIn("自定义 API", content)
            self.assertIn("可审草稿", content)
            self.assertIn("/client/v1/models", content)
            self.assertIn("client token", content)
            self.assertIn(".venv", content)
            self.assertIn("PyInstaller", content)

    def test_build_docs_describe_search_pool_and_runtime_storage(self):
        for doc_name in ["BUILD.md", "WINDOWS_BUILD.md"]:
            content = (ROOT / doc_name).read_text(encoding="utf-8")
            self.assertIn("managed_search_pool.json", content)
            self.assertIn("search_runtime_state.json", content)
            self.assertIn("search_cache.json", content)
            self.assertIn("minute_limit", content)
            self.assertIn("daily_soft_limit", content)
            self.assertIn("cooldown_seconds", content)
            self.assertIn("project_minute_limit", content)
            self.assertIn("global_minute_limit", content)
            self.assertIn("memory_cache_ttl_seconds", content)
            self.assertIn("project_cache_ttl_seconds", content)

    def test_readme_describes_managed_mode_without_claiming_word_pdf_export(self):
        content = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("默认通道", content)
        self.assertIn("自定义 API", content)
        self.assertIn("Windows", content)
        self.assertIn("可审草稿", content)
        self.assertNotIn("Word/PDF", content)


if __name__ == "__main__":
    unittest.main()
