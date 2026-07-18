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
        self.assertIn("--noconfirm", script)
        self.assertIn(".venv", script)
        self.assertIn('"python"', script.lower())
        self.assertIn('"venv"', script.lower())
        self.assertIn("managed_client_token.txt", script)
        self.assertIn("managed_search_pool.json", script)
        self.assertIn("resolve_bundle_pandoc", script)
        self.assertIn("Validate bundled Pandoc", script)

    def test_build_docs_describe_managed_default_and_windows_first_release(self):
        for doc_name in ["BUILD.md", "WINDOWS_BUILD.md"]:
            content = (ROOT / doc_name).read_text(encoding="utf-8")
            self.assertIn("Windows", content)
            self.assertIn("试用通道", content)
            self.assertIn("自定义 API", content)
            self.assertIn("可审草稿", content)
            self.assertIn("/client/v1/models", content)
            self.assertIn("client token", content)
            self.assertIn(".venv", content)
            self.assertIn("PyInstaller", content)
            self.assertIn("deepseek-v4-pro", content)
            self.assertIn("Pandoc", content)
            self.assertIn("pandoc.exe", content)

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
        self.assertIn("试用通道", content)
        self.assertIn("自定义 API", content)
        self.assertIn("Windows", content)
        self.assertIn("可审草稿", content)
        self.assertIn("deepseek-v4-pro", content)
        self.assertIn("Pandoc", content)
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
        # Rule 4: stage advancement must use the explicit tool
        self.assertIn("advance_stage", self.skill_md)
        self.assertIn('checkpoint_key="s0_interview_done_at"', self.skill_md)

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

    def test_legacy_stage_ack_instructions_removed(self):
        self.assertIn("advance_stage", self.skill_md)
        self.assertNotIn("<stage-ack", self.skill_md)
        self.assertNotIn("stage-ack 标签规范", self.skill_md)

    def test_common_stage_phrase_examples_table(self):
        # Checks a sample phrase from each checkpoint's user-intent examples
        self.assertIn("跳过访谈", self.skill_md)  # s0
        self.assertIn("确认大纲", self.skill_md)
        self.assertIn("开始审查", self.skill_md)
        self.assertIn("审查通过", self.skill_md)
        self.assertIn("演示准备完成", self.skill_md)
        self.assertIn("归档结束项目", self.skill_md)

    def test_report_writing_tool_contract_present(self):
        self.assertIn("append_report_draft", self.skill_md)
        self.assertIn("edit_file", self.skill_md)
        self.assertIn("read_file", self.skill_md)
        self.assertNotIn("draft-action 标签规范", self.skill_md)

    def test_s4_forbids_internal_dl_markers_and_gives_reader_facing_alternative(self):
        s4 = self.skill_md.split("### S4 报告撰写", 1)[1].split("### S4 写正文工具", 1)[0]
        self.assertIn("正文与附录**禁止出现** `[DL-...]` 内部编号标记", s4)
        self.assertIn("`analysis-notes.md` 专用", s4)
        self.assertIn("据国家数据局 2024 年 12 月发布的指导意见", s4)
        self.assertIn("文末「参考资料」章节集中列出", s4)

    def test_skill_md_s5_section_uses_new_workflow(self):
        self.assertIn("独立审查", self.skill_md)
        self.assertIn("plan/independent-review.md", self.skill_md)
        self.assertNotIn("完成 review-checklist.md", self.skill_md)
        # N7: the lint / AI-味自查 path is removed — must not survive in SKILL.md.
        self.assertNotIn("AI 味自查", self.skill_md)
        self.assertNotIn("plan/lint-report.md", self.skill_md)

    def test_lifecycle_and_tracking_templates_use_new_s5_workflow(self):
        repo_root = Path(__file__).resolve().parents[1]
        lifecycle = (repo_root / "skill" / "modules" / "consulting-lifecycle.md").read_text(encoding="utf-8")
        progress = (repo_root / "skill" / "plan-template" / "progress.md").read_text(encoding="utf-8")
        gates = (repo_root / "skill" / "plan-template" / "stage-gates.md").read_text(encoding="utf-8")
        tasks = (repo_root / "skill" / "plan-template" / "tasks.md").read_text(encoding="utf-8")

        self.assertIn("完成独立审查", lifecycle)
        self.assertIn("`independent-review.md`", progress)
        self.assertIn("独立审查完成（plan/independent-review.md）", gates)
        self.assertIn('点击工作区"独立审查"按钮', tasks)
        # N7: lint / AI-味自查 path removed from templates.
        self.assertNotIn("AI 味自查", lifecycle)
        self.assertNotIn("lint-report.md", progress)
        self.assertNotIn("AI 味自查完成（plan/lint-report.md）", gates)
        self.assertNotIn('点击工作区"AI 味自查"按钮', tasks)

    def test_skill_md_routing_section_replaced_with_system_injection(self):
        # 旧「模型 read_file 自取 modules」指令已删（app 沙箱够不到，已失效）
        self.assertNotIn("先读取 `modules/writing-core.md`", self.skill_md)
        # 旧整段路由标题不回流（防 read_file modules 路由指令回流）
        self.assertNotIn("## 路由与模块", self.skill_md)
        # 新「系统按报告类型注入方法论」说明在（注意连续子串，不被 markdown ** 打断）
        self.assertIn("由系统按报告类型自动注入", self.skill_md)

    def test_skill_md_s1_has_methodology_declaration_instruction(self):
        self.assertIn("方法论框架：", self.skill_md)
        # S1 段必须包含完整的方法论声明指引句（不只全文搜关键词）
        self.assertIn("形成 `outline.md`，并在文件顶部写一行方法论声明", self.skill_md)


if __name__ == "__main__":
    unittest.main()
