import base64
import json
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

    def _mark_s0_done(self, project_dir: Path):
        checkpoints_path = project_dir / "stage_checkpoints.json"
        checkpoints = {}
        if checkpoints_path.exists():
            checkpoints = json.loads(checkpoints_path.read_text(encoding="utf-8"))
        checkpoints.setdefault("s0_interview_done_at", "2026-01-01T00:00:00")
        checkpoints_path.write_text(
            json.dumps(checkpoints, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _write_stage_two_prerequisites(self, project_dir: Path):
        self._mark_s0_done(project_dir)
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
            "### [DL-001] Interview 1\n"
            "- 来源: https://example.com/interview-1\n"
            "- 摘要: Renewal rate down 8 percent.\n\n"
            "### [DL-002] Interview 2\n"
            "- 来源: https://example.com/interview-2\n"
            "- 摘要: Onboarding delay extends go-live by two weeks.\n\n"
            "### [DL-003] CRM export\n"
            "- 来源: material:crm-export\n"
            "- 摘要: Trial-to-paid conversion fell 11 percent.\n\n"
            "### [DL-004] Benchmark report\n"
            "- 来源: https://example.com/benchmark\n"
            "- 摘要: Peers cut onboarding friction with guided rollout.\n",
            encoding="utf-8",
        )
        (project_dir / "plan" / "analysis-notes.md").write_text(
            "# Analysis notes\n\n"
            "## Insight 1\n"
            "Conclusion: onboarding friction is driving renewal loss.\n"
            "Evidence: [DL-001] and [DL-003] show the same failure pattern.\n"
            "Impact: prioritize onboarding redesign.\n\n"
            "## Insight 2\n"
            "Conclusion: go-live delays reduce stakeholder confidence.\n"
            "Evidence: [DL-002] highlights the implementation bottleneck.\n"
            "Impact: tighten enablement support during rollout.\n\n"
            "## Insight 3\n"
            "Conclusion: the market already treats guided rollout as table stakes.\n"
            "Evidence: [DL-004] confirms the competitive benchmark.\n"
            "Impact: position onboarding redesign as a retention move, not polish.\n",
            encoding="utf-8",
        )
        (project_dir / "content" / "report_draft_v1.md").write_text(
            "# Draft\n\n" + ("报" * 2200) + "\n",
            encoding="utf-8",
        )
        (project_dir / "plan" / "review-checklist.md").write_text(
            "# Review checklist\n\n"
            "## Review cycle\n"
            "**Cycle**: 1\n"
            "- [x] Facts cross-checked against sources.\n"
            "- [x] Conclusions aligned with evidence.\n"
            "- [x] Structure logic reviewed end-to-end.\n",
            encoding="utf-8",
        )
        (project_dir / "plan" / "review.md").write_text(
            "# Review log\n\n"
            "## Cycle 1\n"
            "- Facts cross-checked against interview notes and references.\n"
            "- Language tightened for executive readability.\n",
            encoding="utf-8",
        )

    def _set_delivery_mode(self, project_dir: Path, delivery_mode: str):
        overview_path = project_dir / "plan" / "project-overview.md"
        content = overview_path.read_text(encoding="utf-8")
        updated = content.replace("**交付形式**: 仅报告", f"**交付形式**: {delivery_mode}")
        overview_path.write_text(updated, encoding="utf-8")

    def _save_checkpoints(self, engine: SkillEngine, project_dir: Path, *keys: str):
        for key in keys:
            engine._save_stage_checkpoint(project_dir, key)

    def test_create_project_stores_workspace_metadata_and_initial_materials(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_projects_dir = Path(tmpdir) / "config-projects"
            workspace_dir = Path(tmpdir) / "瀹㈡埛椤圭洰"
            material_path = workspace_dir / "璧勬枡" / "璁胯皥绾.txt"
            material_path.parent.mkdir(parents=True)
            material_path.write_text("璁胯皥绾", encoding="utf-8")

            engine = SkillEngine(config_projects_dir, self.repo_skill_dir)

            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI 鎴樼暐瑙勫垝",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
                notes="宸叉湁璁胯皥绾",
                initial_material_paths=[str(material_path)],
            )

            project_dir = workspace_dir / ".consulting-report"
            self.assertEqual(project["name"], "demo")
            self.assertEqual(project["workspace_dir"], str(workspace_dir))
            self.assertEqual(project["project_dir"], str(project_dir))
            self.assertEqual(project["theme"], "AI 鎴樼暐瑙勫垝")
            self.assertEqual(project["target_audience"], "executive audience")
            self.assertEqual(project["expected_length"], "3000 words")
            self.assertTrue((project_dir / "plan" / "project-overview.md").exists())

            projects = engine.list_projects()
            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0]["id"], project["id"])

            materials = engine.list_materials(project["id"])
            self.assertEqual(len(materials), 1)
            self.assertEqual(materials[0]["source_type"], "workspace")
            self.assertEqual(materials[0]["stored_rel_path"], "璧勬枡/璁胯皥绾.txt")
            self.assertFalse((project_dir / "materials" / "imported" / "璁胯皥绾.txt").exists())

    def test_workspace_summary_backfills_stage_file_without_skipping_to_report_stage(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_projects_dir = Path(tmpdir) / "config-projects"
            workspace_dir = Path(tmpdir) / "瀹㈡埛椤圭洰"
            engine = SkillEngine(config_projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI 鎴樼暐瑙勫垝",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )

            project_dir = workspace_dir / ".consulting-report"
            self._mark_s0_done(project_dir)
            (project_dir / "plan" / "stage-gates.md").unlink()
            (project_dir / "plan" / "outline.md").write_text(
                "# 澶х翰\n\n## 鎵ц鎽樿\n- 缁撹\n## 寤鸿\n- 涓嬩竴姝n",
                encoding="utf-8",
            )
            (project_dir / "content" / "report_draft_v1.md").write_text(
                "# 绗竴绔燶n\n## 鎵ц鎽樿\n褰㈡垚浜嗗彲浜や粯鐨勬鏂囨钀姐€俓n",
                encoding="utf-8",
            )

            summary = engine.get_workspace_summary(project["id"])

            self.assertEqual(summary["stage_code"], "S1")
            self.assertEqual(summary["status"], "进行中")
            self.assertTrue((project_dir / "plan" / "stage-gates.md").exists())

    def test_workspace_summary_keeps_report_only_delivery_log_from_skipping_past_s4_without_review(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_projects_dir = Path(tmpdir) / "config-projects"
            workspace_dir = Path(tmpdir) / "瀹㈡埛椤圭洰"
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
            self._save_checkpoints(
                engine,
                project_dir,
                "outline_confirmed_at",
                "review_started_at",
            )
            (project_dir / "plan" / "review-checklist.md").unlink()
            (project_dir / "content" / "report_draft_v1.md").write_text("# Draft\n\n" + ("报" * 2200) + "\n", encoding="utf-8")
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
            self.assertNotIn("delivery-log.md 鏇存柊", summary["completed_items"])
            self.assertNotIn("- [/] presentation-plan.md 瀹屾垚", stage_gates_text)

    def test_workspace_summary_advances_report_only_projects_to_s7_after_review_artifacts_without_delivery_log(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_projects_dir = Path(tmpdir) / "config-projects"
            workspace_dir = Path(tmpdir) / "瀹㈡埛椤圭洰"
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
            self._save_checkpoints(
                engine,
                project_dir,
                "outline_confirmed_at",
                "review_started_at",
                "review_passed_at",
            )

            summary = engine.get_workspace_summary(project["id"])
            stage_gates_text = (project_dir / "plan" / "stage-gates.md").read_text(encoding="utf-8")

            self.assertEqual(summary["stage_code"], "S7")
            self.assertIn("delivery-log.md 更新", summary["next_actions"])
            self.assertNotIn("presentation-plan.md 瀹屾垚", summary["next_actions"])
            self.assertIn("- [/] presentation-plan.md 完成", stage_gates_text)

    def test_workspace_summary_accepts_review_notes_with_labeled_results(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_projects_dir = Path(tmpdir) / "config-projects"
            workspace_dir = Path(tmpdir) / "鐎广垺鍩涙い鍦窗"
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
            self._save_checkpoints(
                engine,
                project_dir,
                "outline_confirmed_at",
                "review_started_at",
                "review_passed_at",
            )
            (project_dir / "plan" / "review.md").write_text(
                "# Review log\n\n"
                "## Review cycle 1\n"
                "**Review time**: 2026-04-01\n"
                "**Check result**: Facts cross-checked and chart labels corrected.\n"
                "**Handling result**: Revised wording and footnotes synced.\n",
                encoding="utf-8",
            )

            summary = engine.get_workspace_summary(project["id"])

            self.assertEqual(summary["stage_code"], "S7")
            self.assertTrue(any("delivery-log.md" in item for item in summary["next_actions"]))

    def test_workspace_summary_review_notes_with_metadata_only_do_not_block_report_only_s7(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_projects_dir = Path(tmpdir) / "config-projects"
            workspace_dir = Path(tmpdir) / "鐎广垺鍩涙い鍦窗"
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
            self._save_checkpoints(
                engine,
                project_dir,
                "outline_confirmed_at",
                "review_started_at",
                "review_passed_at",
            )
            (project_dir / "plan" / "review.md").write_text(
                "# Review log\n\n"
                "## Review cycle 1\n"
                "**Review time**: 2026-04-01\n",
                encoding="utf-8",
            )

            summary = engine.get_workspace_summary(project["id"])

            self.assertEqual(summary["stage_code"], "S7")
            self.assertNotIn("review.md", " ".join(summary["next_actions"]))

    def test_workspace_summary_review_notes_with_checkbox_status_only_do_not_block_report_only_s7(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_projects_dir = Path(tmpdir) / "config-projects"
            workspace_dir = Path(tmpdir) / "鐎广垺鍩涙い鍦窗"
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
            self._save_checkpoints(
                engine,
                project_dir,
                "outline_confirmed_at",
                "review_started_at",
                "review_passed_at",
            )
            (project_dir / "plan" / "review.md").write_text(
                "# Review log\n\n"
                "## Review cycle 1\n"
                "**Revision status**: [ ] Pending | [ ] Done\n"
                "**Approval**: [ ] Yes | [ ] No, continue revising\n",
                encoding="utf-8",
            )

            summary = engine.get_workspace_summary(project["id"])

            self.assertEqual(summary["stage_code"], "S7")
            self.assertNotIn("review.md", " ".join(summary["next_actions"]))

    def test_workspace_summary_advances_report_only_projects_to_s7_without_review_notes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_projects_dir = Path(tmpdir) / "config-projects"
            workspace_dir = Path(tmpdir) / "閻庡箍鍨洪崺喑佮波銇勯崷顓熺獥"
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
            self._save_checkpoints(
                engine,
                project_dir,
                "outline_confirmed_at",
                "review_started_at",
                "review_passed_at",
            )
            (project_dir / "plan" / "review.md").unlink()

            summary = engine.get_workspace_summary(project["id"])

            self.assertEqual(summary["stage_code"], "S7")
            self.assertNotIn("review.md", " ".join(summary["next_actions"]))
            self.assertTrue(any("delivery-log.md" in item for item in summary["next_actions"]))

    def test_workspace_summary_keeps_report_only_projects_at_s5_without_review_notes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_projects_dir = Path(tmpdir) / "config-projects"
            workspace_dir = Path(tmpdir) / "鐎广垺鍩涙い鍦窗"
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
            self._save_checkpoints(
                engine,
                project_dir,
                "outline_confirmed_at",
                "review_started_at",
            )
            (project_dir / "plan" / "review.md").unlink()

            summary = engine.get_workspace_summary(project["id"])

            self.assertEqual(summary["stage_code"], "S5")
            self.assertIn("review.md", " ".join(summary["next_actions"]))
            self.assertNotIn("delivery-log.md 更新", summary["completed_items"])

    def test_workspace_summary_template_like_review_notes_do_not_block_report_only_s7(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_projects_dir = Path(tmpdir) / "config-projects"
            workspace_dir = Path(tmpdir) / "鐎广垺鍩涙い鍦窗"
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
            self._save_checkpoints(
                engine,
                project_dir,
                "outline_confirmed_at",
                "review_started_at",
                "review_passed_at",
            )
            (project_dir / "plan" / "review.md").write_text(
                "# Review log\n\n"
                "## Content quality\n"
                "- [ ] Every key claim has support.\n"
                "- [ ] Logic chain is complete.\n"
                "## Review cycle\n"
                "**Review time**:\n"
                "**Problems found**:\n"
                "1.\n"
                "2.\n",
                encoding="utf-8",
            )

            summary = engine.get_workspace_summary(project["id"])

            self.assertEqual(summary["stage_code"], "S7")
            self.assertNotIn("review.md", " ".join(summary["next_actions"]))

    def test_workspace_summary_skips_s6_for_report_only_projects_when_delivery_log_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_projects_dir = Path(tmpdir) / "config-projects"
            workspace_dir = Path(tmpdir) / "瀹㈡埛椤圭洰"
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
            self._save_checkpoints(
                engine,
                project_dir,
                "outline_confirmed_at",
                "review_started_at",
                "review_passed_at",
            )
            (project_dir / "plan" / "delivery-log.md").write_text(
                "# Delivery log\n\n"
                "**Delivery date**: 2026-04-01\n"
                "**Delivery version**: final\n"
                "- Final report shared with client.\n",
                encoding="utf-8",
            )

            summary = engine.get_workspace_summary(project["id"])

            self.assertEqual(summary["stage_code"], "S7")
            self.assertNotIn("presentation-plan.md 瀹屾垚", summary["next_actions"])

    def test_workspace_summary_requires_presentation_plan_for_report_and_presentation_projects(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_projects_dir = Path(tmpdir) / "config-projects"
            workspace_dir = Path(tmpdir) / "瀹㈡埛椤圭洰"
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
            self._save_checkpoints(
                engine,
                project_dir,
                "outline_confirmed_at",
                "review_started_at",
                "review_passed_at",
            )
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
            self.assertNotIn("delivery-log.md 鏇存柊", summary["completed_items"])

    def test_import_material_copies_external_file_into_project(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_projects_dir = Path(tmpdir) / "config-projects"
            workspace_dir = Path(tmpdir) / "瀹㈡埛椤圭洰"
            external_dir = Path(tmpdir) / "澶栭儴璧勬枡"
            external_dir.mkdir()
            external_file = external_dir / "琛屼笟鏁版嵁.txt"
            external_file.write_text("琛屼笟鏁版嵁鍐呭", encoding="utf-8")

            engine = SkillEngine(config_projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI 鎴樼暐瑙勫垝",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
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
            self.assertEqual(copied_path.read_text(encoding="utf-8"), "琛屼笟鏁版嵁鍐呭")

    def test_add_materials_rejects_missing_source_with_clean_message(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_projects_dir = Path(tmpdir) / "config-projects"
            workspace_dir = Path(tmpdir) / "workspace"
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

            with self.assertRaisesRegex(ValueError, "材料不存在"):
                engine.add_materials(
                    project["id"],
                    [str(Path(tmpdir) / "missing.txt")],
                    added_via="manual",
                )

    @mock.patch("backend.chat.OpenAI")
    def test_chat_handler_builds_multimodal_user_message_for_attached_images(self, mock_openai):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_projects_dir = Path(tmpdir) / "config-projects"
            workspace_dir = Path(tmpdir) / "瀹㈡埛椤圭洰"
            image_path = workspace_dir / "璧勬枡" / "甯傚満鍥捐〃.png"
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
                theme="AI 鎴樼暐瑙勫垝",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
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
