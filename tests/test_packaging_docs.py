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


class SkillMdS0InterviewLockTests(unittest.TestCase):
    def setUp(self):
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[1]
        self.skill_md = (repo_root / "skill" / "SKILL.md").read_text(encoding="utf-8")

    def test_s0_mandatory_block_present(self):
        self.assertIn("### S0 预访谈（强制）", self.skill_md)

    def test_s0_rules_present(self):
        # Rule 1: first-turn must ask clarifying questions
        self.assertIn("第一轮回复只能做一件事", self.skill_md)
        # Rule 2: four forbidden files
        self.assertIn("plan/outline.md", self.skill_md)
        self.assertIn("plan/research-plan.md", self.skill_md)
        self.assertIn("plan/data-log.md", self.skill_md)
        self.assertIn("plan/analysis-notes.md", self.skill_md)
        # Rule 4: tag emission on last line
        self.assertIn(
            "<stage-ack>s0_interview_done_at</stage-ack>", self.skill_md
        )

    def test_all_six_keys_in_appendix(self):
        for key in [
            "s0_interview_done_at",
            "outline_confirmed_at",
            "review_started_at",
            "review_passed_at",
            "presentation_ready_at",
            "delivery_archived_at",
        ]:
            self.assertIn(key, self.skill_md, f"Missing key {key} in SKILL.md")

    def test_escape_rule_for_examples(self):
        # Per spec: examples in body text MUST use escaped form, even in code fences
        self.assertIn("即使在 code fence", self.skill_md)

    def test_strong_keyword_examples_table(self):
        # Checks a sample phrase from each of the six key's strong-keyword set
        self.assertIn("跳过访谈", self.skill_md)  # s0
        self.assertIn("确认大纲", self.skill_md)
        self.assertIn("开始审查", self.skill_md)
        self.assertIn("审查通过", self.skill_md)
        self.assertIn("演示准备完成", self.skill_md)
        self.assertIn("归档结束项目", self.skill_md)

    def test_draft_action_tag_contract_present(self):
        self.assertIn("<draft-action>begin</draft-action>", self.skill_md)
        self.assertIn("draft-action 标签规范", self.skill_md)
        self.assertIn("<draft-action-replace>", self.skill_md)


if __name__ == "__main__":
    unittest.main()
