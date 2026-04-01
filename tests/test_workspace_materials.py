import base64
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.chat import ChatHandler
from backend.config import Settings
from backend.skill import SkillEngine


PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/w8AAgMBgJ/l7wAAAABJRU5ErkJggg=="
)


class WorkspaceMaterialTests(unittest.TestCase):
    def setUp(self):
        self.repo_skill_dir = Path(__file__).resolve().parents[1] / "skill"

    def _write_stage_two_prerequisites(self, project_dir: Path):
        (project_dir / "plan" / "notes.md").write_text(
            "# Notes\n\n"
            "## Boundaries\n"
            "- Focus on enterprise AI adoption decisions.\n"
            "## Assumptions\n"
            "- Budget remains flat through FY26.\n",
            encoding="utf-8",
        )
        (project_dir / "plan" / "references.md").write_text(
            "# References\n\n"
            "## Sources\n"
            "- Internal interview transcript: operations lead workshop\n"
            "- External benchmark: https://example.com/ai-benchmark\n",
            encoding="utf-8",
        )
        (project_dir / "plan" / "outline.md").write_text(
            "# Report outline\n\n"
            "### Executive summary\n"
            "- Key finding\n"
            "### Market context\n"
            "- Market signal\n"
            "### Recommendations\n"
            "- Next step\n",
            encoding="utf-8",
        )
        (project_dir / "plan" / "research-plan.md").write_text(
            "# Research plan\n\n"
            "## Research methods\n"
            "- Expert interviews\n"
            "## Data sources\n"
            "- CRM export\n",
            encoding="utf-8",
        )
        (project_dir / "plan" / "data-log.md").write_text(
            "# Data log\n\n"
            "| Date | Type | Source | Fact |\n"
            "| --- | --- | --- | --- |\n"
            "| 2026-04-01 | Interview | Operations lead | Renewal rate down 8 percent |\n",
            encoding="utf-8",
        )
        (project_dir / "plan" / "analysis-notes.md").write_text(
            "# Analysis notes\n\n"
            "## Insight 1\n"
            "Conclusion: onboarding friction is driving renewal loss.\n"
            "Evidence: interview transcript and retention export.\n"
            "Impact: prioritize onboarding redesign.\n",
            encoding="utf-8",
        )
        (project_dir / "report_draft_v1.md").write_text(
            "# Draft\n\n## Executive summary\nA concrete report section.\n",
            encoding="utf-8",
        )
        (project_dir / "plan" / "review-checklist.md").write_text(
            "# Review checklist\n\n"
            "## Review cycle\n"
            "**Cycle**: 1\n"
            "- [x] Facts cross-checked against sources.\n",
            encoding="utf-8",
        )

    def _set_delivery_mode(self, project_dir: Path, delivery_mode: str):
        overview_path = project_dir / "plan" / "project-overview.md"
        content = overview_path.read_text(encoding="utf-8")
        updated = content.replace("**交付形式**: 仅报告", f"**交付形式**: {delivery_mode}")
        overview_path.write_text(updated, encoding="utf-8")

    def test_create_project_stores_workspace_metadata_and_initial_materials(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_projects_dir = Path(tmpdir) / "config-projects"
            workspace_dir = Path(tmpdir) / "客户项目"
            material_path = workspace_dir / "资料" / "访谈纪要.txt"
            material_path.parent.mkdir(parents=True)
            material_path.write_text("访谈纪要", encoding="utf-8")

            engine = SkillEngine(config_projects_dir, self.repo_skill_dir)

            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI 战略规划",
                target_audience="高层决策者",
                deadline="2026-04-01",
                expected_length="3000字",
                notes="已有访谈纪要",
                initial_material_paths=[str(material_path)],
            )

            project_dir = workspace_dir / ".consulting-report"
            self.assertEqual(project["name"], "demo")
            self.assertEqual(project["workspace_dir"], str(workspace_dir))
            self.assertEqual(project["project_dir"], str(project_dir))
            self.assertEqual(project["theme"], "AI 战略规划")
            self.assertEqual(project["target_audience"], "高层决策者")
            self.assertEqual(project["expected_length"], "3000字")
            self.assertTrue((project_dir / "plan" / "project-overview.md").exists())

            projects = engine.list_projects()
            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0]["id"], project["id"])

            materials = engine.list_materials(project["id"])
            self.assertEqual(len(materials), 1)
            self.assertEqual(materials[0]["source_type"], "workspace")
            self.assertEqual(materials[0]["stored_rel_path"], "资料/访谈纪要.txt")
            self.assertFalse((project_dir / "materials" / "imported" / "访谈纪要.txt").exists())

    def test_workspace_summary_backfills_stage_file_without_skipping_to_report_stage(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_projects_dir = Path(tmpdir) / "config-projects"
            workspace_dir = Path(tmpdir) / "客户项目"
            engine = SkillEngine(config_projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI 战略规划",
                target_audience="高层决策者",
                deadline="2026-04-01",
                expected_length="3000字",
            )

            project_dir = workspace_dir / ".consulting-report"
            (project_dir / "plan" / "stage-gates.md").unlink()
            (project_dir / "plan" / "outline.md").write_text(
                "# 大纲\n\n## 执行摘要\n- 结论\n## 建议\n- 下一步\n",
                encoding="utf-8",
            )
            (project_dir / "report_draft_v1.md").write_text(
                "# 第一章\n\n## 执行摘要\n形成了可交付的正文段落。\n",
                encoding="utf-8",
            )

            summary = engine.get_workspace_summary(project["id"])

            self.assertEqual(summary["stage_code"], "S1")
            self.assertEqual(summary["status"], "进行中")
            self.assertTrue((project_dir / "plan" / "stage-gates.md").exists())

    def test_workspace_summary_keeps_report_only_delivery_log_from_skipping_past_s4_without_review(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_projects_dir = Path(tmpdir) / "config-projects"
            workspace_dir = Path(tmpdir) / "客户项目"
            engine = SkillEngine(config_projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
                notes="",
            )

            project_dir = workspace_dir / ".consulting-report"
            self._write_stage_two_prerequisites(project_dir)
            (project_dir / "plan" / "review-checklist.md").unlink()
            (project_dir / "report_draft_v1.md").write_text(
                "# Draft\n\n## Executive summary\nA concrete report section.\n",
                encoding="utf-8",
            )
            (project_dir / "plan" / "delivery-log.md").write_text(
                "# Delivery log\n\n"
                "**Delivery date**: 2026-04-01\n"
                "**Delivery version**: final\n"
                "- Final report shared with client.\n",
                encoding="utf-8",
            )

            summary = engine.get_workspace_summary(project["id"])
            stage_gates_text = (project_dir / "plan" / "stage-gates.md").read_text(encoding="utf-8")

            self.assertEqual(summary["stage_code"], "S5")
            self.assertIn("review-checklist.md 完成", summary["next_actions"])
            self.assertNotIn("delivery-log.md 更新", summary["completed_items"])
            self.assertNotIn("- [/] presentation-plan.md 完成", stage_gates_text)

    def test_workspace_summary_advances_report_only_projects_to_s7_after_review_checklist_without_delivery_log(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_projects_dir = Path(tmpdir) / "config-projects"
            workspace_dir = Path(tmpdir) / "客户项目"
            engine = SkillEngine(config_projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
                notes="",
            )

            project_dir = workspace_dir / ".consulting-report"
            self._write_stage_two_prerequisites(project_dir)

            summary = engine.get_workspace_summary(project["id"])
            stage_gates_text = (project_dir / "plan" / "stage-gates.md").read_text(encoding="utf-8")

            self.assertEqual(summary["stage_code"], "S7")
            self.assertIn("delivery-log.md 更新", summary["next_actions"])
            self.assertNotIn("presentation-plan.md 完成", summary["next_actions"])
            self.assertIn("- [/] presentation-plan.md 完成", stage_gates_text)

    def test_workspace_summary_skips_s6_for_report_only_projects_when_delivery_log_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_projects_dir = Path(tmpdir) / "config-projects"
            workspace_dir = Path(tmpdir) / "客户项目"
            engine = SkillEngine(config_projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
                notes="",
            )

            project_dir = workspace_dir / ".consulting-report"
            self._write_stage_two_prerequisites(project_dir)
            (project_dir / "plan" / "delivery-log.md").write_text(
                "# Delivery log\n\n"
                "**Delivery date**: 2026-04-01\n"
                "**Delivery version**: final\n"
                "- Final report shared with client.\n",
                encoding="utf-8",
            )

            summary = engine.get_workspace_summary(project["id"])

            self.assertEqual(summary["stage_code"], "S7")
            self.assertNotIn("presentation-plan.md 完成", summary["next_actions"])

    def test_workspace_summary_requires_presentation_plan_for_report_and_presentation_projects(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_projects_dir = Path(tmpdir) / "config-projects"
            workspace_dir = Path(tmpdir) / "客户项目"
            engine = SkillEngine(config_projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
                notes="",
            )

            project_dir = workspace_dir / ".consulting-report"
            self._set_delivery_mode(project_dir, "报告+演示")
            self._write_stage_two_prerequisites(project_dir)
            (project_dir / "plan" / "delivery-log.md").write_text(
                "# Delivery log\n\n"
                "**Delivery date**: 2026-04-01\n"
                "**Delivery version**: briefing\n"
                "- Draft sent ahead of readout.\n",
                encoding="utf-8",
            )

            summary = engine.get_workspace_summary(project["id"])

            self.assertEqual(summary["stage_code"], "S6")
            self.assertIn("presentation-plan.md 完成", summary["next_actions"])
            self.assertNotIn("delivery-log.md 更新", summary["completed_items"])

    def test_import_material_copies_external_file_into_project(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_projects_dir = Path(tmpdir) / "config-projects"
            workspace_dir = Path(tmpdir) / "客户项目"
            external_dir = Path(tmpdir) / "外部资料"
            external_dir.mkdir()
            external_file = external_dir / "行业数据.txt"
            external_file.write_text("行业数据内容", encoding="utf-8")

            engine = SkillEngine(config_projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI 战略规划",
                target_audience="高层决策者",
                deadline="2026-04-01",
                expected_length="3000字",
                notes="",
            )

            imported = engine.add_materials(
                project["id"],
                [str(external_file)],
                added_via="chat_upload",
            )

            self.assertEqual(len(imported), 1)
            material = imported[0]
            copied_path = workspace_dir / ".consulting-report" / material["stored_rel_path"]
            self.assertEqual(material["source_type"], "imported")
            self.assertEqual(material["original_path"], str(external_file))
            self.assertTrue(copied_path.exists())
            self.assertEqual(copied_path.read_text(encoding="utf-8"), "行业数据内容")

    @mock.patch("backend.chat.OpenAI")
    def test_chat_handler_builds_multimodal_user_message_for_attached_images(self, mock_openai):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_projects_dir = Path(tmpdir) / "config-projects"
            workspace_dir = Path(tmpdir) / "客户项目"
            image_path = workspace_dir / "资料" / "市场图表.png"
            image_path.parent.mkdir(parents=True)
            image_path.write_bytes(base64.b64decode(PNG_1X1_BASE64))

            settings = Settings(
                mode="managed",
                managed_base_url="https://newapi.z0y0h.work/client/v1",
                managed_model="gemini-3-flash",
                projects_dir=config_projects_dir,
                skill_dir=self.repo_skill_dir,
            )
            engine = SkillEngine(config_projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI 战略规划",
                target_audience="高层决策者",
                deadline="2026-04-01",
                expected_length="3000字",
                notes="",
                initial_material_paths=[str(image_path)],
            )
            material = engine.list_materials(project["id"])[0]
            handler = ChatHandler(settings, engine)

            content = handler._build_user_content(
                project["id"],
                "请分析这张图表的核心结论。",
                [material["id"]],
            )

            self.assertEqual(content[0]["type"], "text")
            self.assertIn("请分析这张图表的核心结论。", content[0]["text"])
            self.assertIn(material["id"], content[0]["text"])
            self.assertEqual(content[1]["type"], "image_url")
            self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))
