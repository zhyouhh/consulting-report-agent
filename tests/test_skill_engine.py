import json
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from backend.skill import SkillEngine


class SkillEngineTests(unittest.TestCase):
    def setUp(self):
        self.repo_skill_dir = Path(__file__).resolve().parents[1] / "skill"

    def _project_payload(self, workspace_dir: Path, **overrides):
        payload = {
            "name": "demo",
            "workspace_dir": str(workspace_dir),
            "project_type": "strategy-consulting",
            "theme": "AI strategy review",
            "target_audience": "executive audience",
            "deadline": "2026-04-01",
            "expected_length": "3000 words",
            "notes": "existing interview notes",
        }
        payload.update(overrides)
        return payload

    def _create_engine_and_project(self, tmpdir: str):
        projects_dir = Path(tmpdir) / "projects"
        workspace_dir = Path(tmpdir) / "workspace"
        engine = SkillEngine(projects_dir, self.repo_skill_dir)
        project = engine.create_project(self._project_payload(workspace_dir))
        return engine, Path(project["project_dir"])

    def _write_stage_two_prerequisites(
        self,
        project_dir: Path,
        *,
        references_text: str | None = None,
        include_research_plan: bool = True,
    ):
        (project_dir / "plan" / "notes.md").write_text(
            "# Notes\n\n"
            "## Boundaries\n"
            "- Focus on enterprise AI adoption decisions.\n"
            "## Out of scope\n"
            "- Do not cover vendor procurement.\n"
            "## Assumptions\n"
            "- Budget remains flat through FY26.\n",
            encoding="utf-8",
        )
        (project_dir / "plan" / "references.md").write_text(
            references_text
            or (
                "# References\n\n"
                "## Sources\n"
                "- Internal interview transcript: operations lead workshop\n"
                "- External benchmark: https://example.com/ai-benchmark\n"
            ),
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
        if include_research_plan:
            (project_dir / "plan" / "research-plan.md").write_text(
                "# Research plan\n\n"
                "## Research methods\n"
                "- Expert interviews\n"
                "## Data sources\n"
                "- CRM export\n",
                encoding="utf-8",
            )

    def _write_evidence_gate_prerequisites(self, project_dir: Path, *, source_count: int = 2):
        (project_dir / "plan" / "notes.md").write_text(
            "# Notes\n\n"
            "## Boundaries\n"
            "- Focus on enterprise AI adoption decisions.\n"
            "## Out of scope\n"
            "- Do not cover vendor procurement.\n"
            "## Assumptions\n"
            "- Budget remains flat through FY26.\n",
            encoding="utf-8",
        )
        reference_lines = [
            "# References",
            "",
            "## Sources",
            "- Internal interview transcript: operations lead workshop",
        ]
        if source_count >= 2:
            reference_lines.append("- External benchmark: https://example.com/ai-benchmark")
        (project_dir / "plan" / "references.md").write_text(
            "\n".join(reference_lines) + "\n",
            encoding="utf-8",
        )

    def _write_data_log(self, project_dir: Path):
        (project_dir / "plan" / "data-log.md").write_text(
            "# Data log\n\n"
            "| Date | Type | Source | Fact |\n"
            "| --- | --- | --- | --- |\n"
            "| 2026-04-01 | Interview | Operations lead | Renewal rate down 8 percent |\n",
            encoding="utf-8",
        )

    def _write_analysis_notes(self, project_dir: Path):
        (project_dir / "plan" / "analysis-notes.md").write_text(
            "# Analysis notes\n\n"
            "## Insight 1\n"
            "Conclusion: onboarding friction is driving renewal loss.\n"
            "Evidence: interview transcript and retention export.\n"
            "Impact: prioritize onboarding redesign.\n",
            encoding="utf-8",
        )

    def test_create_project_initializes_formal_plan_templates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = projects_dir / "demo"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            engine.create_project(
                "demo",
                "strategy-consulting",
                "AI 战略规划",
                "高层决策者",
                "2026-04-01",
                "3000字",
                "已有访谈纪要",
            )

            created_file_names = {
                path.name for path in (workspace_dir / ".consulting-report" / "plan").glob("*.md")
            }
            expected_files = {
                "project-overview.md",
                "progress.md",
                "stage-gates.md",
                "notes.md",
                "outline.md",
                "research-plan.md",
                "references.md",
                "tasks.md",
                "review.md",
                "data-log.md",
                "analysis-notes.md",
                "review-checklist.md",
                "presentation-plan.md",
                "delivery-log.md",
            }

            self.assertTrue(expected_files.issubset(created_file_names))
            self.assertNotIn("project-info.md", created_file_names)

    def test_create_project_initializes_only_registered_formal_plan_templates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = projects_dir / "demo"
            skill_dir = Path(tmpdir) / "skill"
            template_dir = skill_dir / "plan-template"
            shutil.copytree(self.repo_skill_dir / "plan-template", template_dir)
            (template_dir / "project-info.md").write_text("# legacy", encoding="utf-8")
            (template_dir / "scratchpad.md").write_text("# ad hoc", encoding="utf-8")

            engine = SkillEngine(projects_dir, skill_dir)
            engine.create_project(
                "demo",
                "strategy-consulting",
                "theme",
                "executive audience",
                "2026-04-01",
                "3000 words",
                "existing notes",
            )

            created_file_names = {
                path.name for path in (workspace_dir / ".consulting-report" / "plan").glob("*.md")
            }

            self.assertNotIn("project-info.md", created_file_names)
            self.assertNotIn("scratchpad.md", created_file_names)

    def test_create_project_defaults_to_managed_workspace_under_projects_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)

            project = engine.create_project(
                "demo",
                "strategy-consulting",
                "theme",
                "executive audience",
                "2026-04-01",
                "3000 words",
                "existing notes",
            )

            expected_workspace_dir = projects_dir / "demo"
            expected_project_dir = expected_workspace_dir / ".consulting-report"

            self.assertEqual(Path(project["workspace_dir"]), expected_workspace_dir)
            self.assertEqual(Path(project["project_dir"]), expected_project_dir)
            self.assertTrue((expected_project_dir / "plan" / "project-overview.md").exists())

    def test_get_project_path_ignores_unregistered_legacy_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            legacy_dir = projects_dir / "legacy-demo"
            legacy_dir.mkdir(parents=True)

            engine = SkillEngine(projects_dir, self.repo_skill_dir)

            self.assertIsNone(engine.get_project_path("legacy-demo"))

    def test_tasks_template_uses_s0_to_s7_instead_of_legacy_phase_buckets(self):
        template_text = (self.repo_skill_dir / "plan-template" / "tasks.md").read_text(encoding="utf-8")

        for stage_code in ("S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7"):
            self.assertIn(stage_code, template_text)

        self.assertNotIn("阶段0：项目初始化", template_text)
        self.assertNotIn("阶段1：大纲设计", template_text)
        self.assertNotIn("阶段2：分段撰写", template_text)
        self.assertNotIn("阶段3：质量审查", template_text)
        self.assertNotIn("阶段4：整合导出", template_text)

    def test_progress_template_uses_stage_codes_in_milestones(self):
        template_text = (self.repo_skill_dir / "plan-template" / "progress.md").read_text(encoding="utf-8")

        for stage_code in ("S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7"):
            self.assertIn(stage_code, template_text)

        self.assertIn("| S0 | 项目启动 |", template_text)
        self.assertIn("| S4 | 报告撰写 |", template_text)
        self.assertIn("| S7 | 交付归档 |", template_text)

    def test_consulting_lifecycle_module_aligns_stage_files_and_optional_s6(self):
        lifecycle_text = (self.repo_skill_dir / "modules" / "consulting-lifecycle.md").read_text(encoding="utf-8")

        self.assertIn("stage-gates.md", lifecycle_text)
        self.assertIn("project-overview.md", lifecycle_text)
        self.assertIn("notes.md", lifecycle_text)
        self.assertIn("references.md", lifecycle_text)
        self.assertIn("outline.md", lifecycle_text)
        self.assertIn("research-plan.md", lifecycle_text)
        self.assertIn("仅当交付形式 = 报告+演示", lifecycle_text)

    def test_capability_map_routes_lifecycle_to_stage_artifacts(self):
        capability_map = json.loads(
            (self.repo_skill_dir / "evals" / "capability-map.json").read_text(encoding="utf-8")
        )
        lifecycle = next(
            item for item in capability_map["capabilities"] if item["module"] == "consulting-lifecycle"
        )

        self.assertIn("stage-gates", lifecycle["outputs"])
        self.assertIn("progress", lifecycle["outputs"])
        self.assertIn("tasks", lifecycle["outputs"])
        self.assertNotIn("progress-notes", lifecycle["outputs"])

    def test_project_overview_template_contains_aligned_metadata_fields(self):
        template_text = (self.repo_skill_dir / "plan-template" / "project-overview.md").read_text(encoding="utf-8")

        self.assertIn("**项目名称**:", template_text)
        self.assertIn("**报告类型**:", template_text)
        self.assertIn("**报告主题**:", template_text)
        self.assertIn("## 项目背景", template_text)
        self.assertIn("**目标读者**:", template_text)
        self.assertIn("**预期篇幅**:", template_text)
        self.assertIn("**交付时间**:", template_text)
        self.assertIn("## 特殊要求", template_text)
        self.assertIn("**交付形式**: 仅报告", template_text)
        self.assertIn("## 成功标准", template_text)

    def test_stage_gates_template_aligns_stage_evidence_and_conditional_s6(self):
        template_text = (self.repo_skill_dir / "plan-template" / "stage-gates.md").read_text(encoding="utf-8")

        self.assertIn("project-overview.md 创建", template_text)
        self.assertIn("notes.md 更新", template_text)
        self.assertIn("references.md 更新", template_text)
        self.assertIn("outline.md 完成", template_text)
        self.assertIn("research-plan.md 完成", template_text)
        self.assertIn("data-log.md 更新", template_text)
        self.assertIn("analysis-notes.md 创建/更新", template_text)
        self.assertIn("review-checklist.md 完成", template_text)
        self.assertIn("report_draft_v1.md", template_text)
        self.assertIn("content/report.md", template_text)
        self.assertIn("content/draft.md", template_text)
        self.assertIn("output/final-report.md", template_text)
        self.assertIn("交付形式 = 报告+演示", template_text)
        self.assertIn("presentation-plan.md 完成", template_text)
        self.assertIn("仅报告", template_text)
        self.assertIn("delivery-log.md 更新", template_text)

    def test_workspace_summary_reads_stage_from_real_stage_gates_template(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            engine.create_project(
                "demo",
                "strategy-consulting",
                "AI 战略规划",
                "高层决策者",
                "2026-04-01",
                "3000字",
                "已有访谈纪要",
            )

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S0")
            self.assertEqual(summary["status"], "进行中")
            self.assertTrue(summary["next_actions"])
            self.assertTrue(summary["next_actions"])

    def test_build_project_context_uses_v2_labels_not_legacy_labels(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            engine.create_project(
                "demo",
                "strategy-consulting",
                "AI 战略规划",
                "高层决策者",
                "2026-04-01",
                "3000字",
                "已有访谈纪要",
            )

            (projects_dir / "demo" / ".consulting-report" / "plan" / "project-info.md").write_text(
                "legacy project info should stay out of core context",
                encoding="utf-8",
            )
            (projects_dir / "demo" / ".consulting-report" / "plan" / "tasks.md").write_text(
                "# 任务清单\n\n## 当前阶段\n**阶段**: S1\n\n### S1 研究设计\n- [ ] 更新 references.md\n",
                encoding="utf-8",
            )
            context = engine.build_project_context("demo")
            self.assertNotIn("legacy project info should stay out of core context", context)

            self.assertIn("当前项目概览", context)
            self.assertIn("当前项目进度", context)
            self.assertIn("阶段门禁", context)
            self.assertIn("项目备注", context)
            self.assertIn("当前阶段任务", context)
            self.assertIn("更新 references.md", context)
            self.assertNotIn("当前项目信息", context)
            self.assertNotIn("当前大纲", context)

    def test_workspace_summary_raises_for_missing_project(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)

            with self.assertRaises(ValueError):
                engine.get_workspace_summary("missing")

    def test_primary_report_path_prefers_report_file_over_outline_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            engine.create_project(
                "demo",
                "strategy-consulting",
                "theme",
                "executive audience",
                "2026-04-01",
                "3000 words",
                "existing notes",
            )
            content_dir = projects_dir / "demo" / ".consulting-report" / "content"
            content_dir.mkdir(parents=True, exist_ok=True)
            (content_dir / "outline.md").write_text("# 大纲", encoding="utf-8")
            (content_dir / "report.md").write_text("# 正文", encoding="utf-8")
            report_path = engine.get_primary_report_path("demo")

            self.assertTrue(report_path.endswith("report.md"))

    def test_write_file_rejects_unregistered_plan_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, _project_dir = self._create_engine_and_project(tmpdir)

            with self.assertRaisesRegex(ValueError, "gate-control.md"):
                engine.write_file("demo", "plan/gate-control.md", "# Gate control")

    def test_is_formal_plan_file_accepts_uppercase_plan_markdown_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, _project_dir = self._create_engine_and_project(tmpdir)

            self.assertTrue(engine.is_formal_plan_file("plan/OUTLINE.MD"))

    def test_write_file_rejects_outline_before_evidence_gate_is_satisfied(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, _project_dir = self._create_engine_and_project(tmpdir)

            with self.assertRaisesRegex(ValueError, "notes.md"):
                engine.write_file("demo", "plan/outline.md", "# Report outline")

    def test_write_file_rejects_uppercase_outline_path_before_evidence_gate_is_satisfied(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, _project_dir = self._create_engine_and_project(tmpdir)

            with self.assertRaisesRegex(ValueError, "notes.md"):
                engine.write_file("demo", "plan/OUTLINE.MD", "# Report outline")

    def test_write_file_rejects_outline_when_references_have_only_one_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_evidence_gate_prerequisites(project_dir, source_count=1)

            with self.assertRaisesRegex(ValueError, "2-source"):
                engine.write_file("demo", "plan/outline.md", "# Report outline")

    def test_write_file_rejects_research_plan_before_evidence_gate_is_satisfied(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, _project_dir = self._create_engine_and_project(tmpdir)

            with self.assertRaisesRegex(ValueError, "references.md"):
                engine.write_file("demo", "plan/research-plan.md", "# Research plan")

    def test_write_file_rejects_research_plan_when_references_have_only_one_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_evidence_gate_prerequisites(project_dir, source_count=1)

            with self.assertRaisesRegex(ValueError, "2-source"):
                engine.write_file("demo", "plan/research-plan.md", "# Research plan")

    def test_write_file_allows_outline_after_evidence_gate_is_satisfied(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_evidence_gate_prerequisites(project_dir)

            engine.write_file(
                "demo",
                "plan/OUTLINE.MD",
                "# Report outline\n\n## Executive summary\n- Key finding\n## Recommendations\n- Next step\n",
            )

            self.assertIn(
                "Executive summary",
                (project_dir / "plan" / "outline.md").read_text(encoding="utf-8"),
            )

    def test_workspace_summary_keeps_stage_at_s1_when_outline_is_effective_without_research_plan(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(project_dir, include_research_plan=False)

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S1")
            self.assertIn("outline.md 完成", summary["completed_items"])
            self.assertIn("research-plan.md 完成", summary["next_actions"])

    def test_workspace_summary_keeps_stage_at_s1_when_research_plan_is_only_keyword_headings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(project_dir, include_research_plan=False)
            (project_dir / "plan" / "research-plan.md").write_text(
                "# Research plan\n\n"
                "## Research methods\n"
                "## Data sources\n"
                "## Execution steps\n",
                encoding="utf-8",
            )

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S1")
            self.assertNotIn("research-plan.md 完成", summary["completed_items"])
            self.assertIn("research-plan.md 完成", summary["next_actions"])

    def test_workspace_summary_keeps_stage_at_s1_when_references_do_not_meet_minimum_evidence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(
                project_dir,
                references_text=(
                    "# References\n\n"
                    "## Sources\n"
                    "- Internal interview transcript: operations lead workshop\n"
                ),
            )

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S1")
            self.assertNotIn("references.md 更新", summary["completed_items"])
            self.assertIn("references.md 更新", summary["next_actions"])

    def test_workspace_summary_keeps_stage_at_s1_when_bracketed_references_are_still_placeholders(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(
                project_dir,
                references_text=(
                    "# References\n\n"
                    "## Sources\n"
                    "- [TBD] 待补来源\n"
                    "- [Source name] 待确认\n"
                ),
            )

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S1")
            self.assertNotIn("references.md 更新", summary["completed_items"])
            self.assertIn("references.md 更新", summary["next_actions"])

    def test_workspace_summary_keeps_stage_at_s1_when_reference_lines_still_embed_placeholder_brackets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(
                project_dir,
                references_text=(
                    "# References\n\n"
                    "## Sources\n"
                    "- 案例引用：参考[公司/项目名称]案例\n"
                    "- 数据引用：数据来源于[来源名称]\n"
                ),
            )

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S1")
            self.assertNotIn("references.md 更新", summary["completed_items"])
            self.assertIn("references.md 更新", summary["next_actions"])

    def test_workspace_summary_advances_to_s2_when_research_design_files_meet_evidence_gate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(project_dir)

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S2")
            self.assertIn("research-plan.md 完成", summary["completed_items"])
            self.assertIn("data-log.md 更新", summary["next_actions"])

    def test_workspace_summary_accepts_two_project_material_titles_as_reference_evidence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(
                project_dir,
                references_text=(
                    "# References\n\n"
                    "## Sources\n"
                    "- 客户访谈纪要\n"
                    "- CRM留存导出\n"
                ),
            )

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S2")
            self.assertIn("references.md 更新", summary["completed_items"])

    def test_workspace_summary_keeps_stage_at_s1_when_research_plan_has_two_generic_sections_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(project_dir, include_research_plan=False)
            (project_dir / "plan" / "research-plan.md").write_text(
                "# Research plan\n\n"
                "## Background\n"
                "This note summarizes why the topic matters.\n\n"
                "## Risks\n"
                "This note lists open risks and caveats.\n",
                encoding="utf-8",
            )

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S1")
            self.assertNotIn("research-plan.md 完成", summary["completed_items"])
            self.assertIn("research-plan.md 完成", summary["next_actions"])

    def test_workspace_summary_accepts_template_aligned_notes_sections_for_stage_one_completion(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            (project_dir / "plan" / "notes.md").write_text(
                "# Project notes\n\n"
                "## Client preferences\n"
                "- Prefer concise executive language.\n"
                "## Key decisions\n"
                "**Decision**: Focus on renewal risk.\n"
                "**Reason**: This is the urgent client ask.\n"
                "## Important findings\n"
                "**Finding**: Onboarding friction is driving churn.\n"
                "**Impact**: Recommendations should prioritize onboarding.\n",
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
                "## Executive summary\n"
                "- Key finding\n"
                "## Recommendations\n"
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

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S2")
            self.assertTrue(any("notes.md" in item for item in summary["completed_items"]))

    def test_workspace_summary_keeps_stage_at_s1_when_notes_only_tweak_placeholder_template(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            (project_dir / "plan" / "notes.md").write_text(
                "# Project notes\n\n"
                "## Client preferences\n"
                "### Writing preferences\n"
                "- [Preferred style]\n"
                "## Glossary\n"
                "| Term | Definition | Usage |\n"
                "| --- | --- | --- |\n"
                "| | | |\n",
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
                "## Executive summary\n"
                "- Key finding\n"
                "## Recommendations\n"
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

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S1")
            self.assertNotIn("notes.md 鏇存柊", summary["completed_items"])
            self.assertTrue(any("notes.md" in item for item in summary["next_actions"]))

    def test_workspace_summary_keeps_stage_at_s1_when_notes_have_only_one_real_bullet_among_placeholders(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            (project_dir / "plan" / "notes.md").write_text(
                "# Project notes\n\n"
                "## Client preferences\n"
                "- Prefer concise executive language.\n"
                "## Key decisions\n"
                "**Decision**:\n"
                "**Reason**:\n"
                "## Glossary\n"
                "| Term | Definition | Usage |\n"
                "| --- | --- | --- |\n"
                "| | | |\n",
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
                "## Executive summary\n"
                "- Key finding\n"
                "## Recommendations\n"
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

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S1")
            self.assertNotIn("notes.md 鏇存柊", summary["completed_items"])
            self.assertTrue(any("notes.md" in item for item in summary["next_actions"]))

    def test_workspace_summary_advances_to_s3_when_data_log_is_effective(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(project_dir)
            self._write_data_log(project_dir)

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S3")
            self.assertIn("data-log.md 更新", summary["completed_items"])
            self.assertIn("analysis-notes.md 创建/更新", summary["next_actions"])

    def test_workspace_summary_keeps_stage_at_s2_when_data_log_only_contains_placeholder_rows_after_small_edit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(project_dir)
            (project_dir / "plan" / "data-log.md").write_text(
                "# Data log\n\n"
                "## Source index\n\n"
                "| Date | Type | Source | Fact | Section |\n"
                "| --- | --- | --- | --- | --- |\n"
                "| [YYYY-MM-DD] | [Interview] | [Source name] | [Fact placeholder] | [Section] |\n",
                encoding="utf-8",
            )

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S2")
            self.assertNotIn("data-log.md 鏇存柊", summary["completed_items"])
            self.assertTrue(any("data-log.md" in item for item in summary["next_actions"]))

    def test_workspace_summary_keeps_stage_at_s2_when_data_log_only_contains_bullet_placeholders(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(project_dir)
            (project_dir / "plan" / "data-log.md").write_text(
                "# Data log\n\n"
                "## Interview notes\n"
                "- 时间：\n"
                "- 对象：\n"
                "- 关键要点：\n",
                encoding="utf-8",
            )

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S2")
            self.assertNotIn("data-log.md 鏇存柊", summary["completed_items"])
            self.assertTrue(any("data-log.md" in item for item in summary["next_actions"]))

    def test_workspace_summary_keeps_stage_at_s2_when_analysis_notes_exist_without_data_log(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(project_dir)
            self._write_analysis_notes(project_dir)

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S2")
            self.assertIn("data-log.md 更新", summary["next_actions"])
            self.assertNotIn("analysis-notes.md 创建/更新", summary["completed_items"])

    def test_workspace_summary_advances_to_s4_when_analysis_notes_are_complete_without_report_draft(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(project_dir)
            self._write_data_log(project_dir)
            self._write_analysis_notes(project_dir)

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S4")
            self.assertIn("analysis-notes.md 创建/更新", summary["completed_items"])
            self.assertIn(
                "report_draft_v1.md / content/report.md / content/draft.md / output/final-report.md 任一形成有效草稿",
                summary["next_actions"],
            )

    def test_workspace_summary_advances_to_s4_with_bracketed_references_and_structured_research_plan(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            (project_dir / "plan" / "notes.md").write_text(
                "# Notes\n\n"
                "## Boundaries\n"
                "- Focus on flight mechanics and strategic necessity.\n"
                "## Assumptions\n"
                "- Treat the fictional energy source as internally consistent.\n",
                encoding="utf-8",
            )
            (project_dir / "plan" / "references.md").write_text(
                "# References\n\n"
                "## Sources\n"
                "- [1] Official series bible. (2024). Flight parameters appendix.\n"
                "- [2] Physics explainer blog. (2023). Warp-drive thought experiment.\n",
                encoding="utf-8",
            )
            (project_dir / "plan" / "outline.md").write_text(
                "# Outline\n\n"
                "## Executive summary\n"
                "- Core conclusion\n"
                "## Mechanism\n"
                "- Energy conversion model\n"
                "## Constraints\n"
                "- Atmospheric heating tradeoff\n",
                encoding="utf-8",
            )
            (project_dir / "plan" / "research-plan.md").write_text(
                "# Research plan\n\n"
                "## Research objective\n"
                "Clarify the mechanism, necessity, and operational constraints of flight.\n\n"
                "## Core research questions\n"
                "- How lift is generated without conventional wings.\n"
                "- How energy output maps to acceleration.\n\n"
                "## Phase plan\n"
                "### Phase 1\n"
                "- Gather source facts and parameter claims.\n"
                "### Phase 2\n"
                "- Build a lightweight physics model and test assumptions.\n\n"
                "## Key assumptions\n"
                "- Fictional anti-gravity can be modeled as a local field effect.\n",
                encoding="utf-8",
            )
            self._write_data_log(project_dir)
            self._write_analysis_notes(project_dir)

            summary = engine.get_workspace_summary("demo")
            stage_gates_text = (project_dir / "plan" / "stage-gates.md").read_text(encoding="utf-8")

            self.assertEqual(summary["stage_code"], "S4")
            self.assertIn("S4", stage_gates_text)

    def test_workspace_summary_keeps_stage_at_s3_when_analysis_notes_are_only_keyword_headings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(project_dir)
            self._write_data_log(project_dir)
            (project_dir / "plan" / "analysis-notes.md").write_text(
                "# Analysis notes\n\n"
                "## Conclusion\n"
                "## Evidence\n"
                "## Impact\n",
                encoding="utf-8",
            )

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S3")
            self.assertNotIn("analysis-notes.md 创建/更新", summary["completed_items"])
            self.assertIn("analysis-notes.md 创建/更新", summary["next_actions"])

    def test_workspace_summary_keeps_stage_at_s3_when_analysis_notes_only_rephrase_template_labels(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(project_dir)
            self._write_data_log(project_dir)
            (project_dir / "plan" / "analysis-notes.md").write_text(
                "# Analysis notes\n\n"
                "## Core insights\n\n"
                "### Insight 2\n"
                "**Conclusion**:\n"
                "**Evidence**:\n"
                "**Impact**:\n"
                "## Structured draft\n"
                "- Key finding:\n"
                "- Recommendation direction:\n",
                encoding="utf-8",
            )

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S3")
            self.assertNotIn("analysis-notes.md 鍒涘缓/鏇存柊", summary["completed_items"])
            self.assertTrue(any("analysis-notes.md" in item for item in summary["next_actions"]))

    def test_workspace_summary_accepts_template_aligned_analysis_notes_with_chinese_labels(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(project_dir)
            self._write_data_log(project_dir)
            (project_dir / "plan" / "analysis-notes.md").write_text(
                "# 分析笔记\n\n"
                "## 核心洞察\n"
                "### 洞察 1\n"
                "**结论**：续约风险主要来自导入期摩擦。\n"
                "**证据**：访谈记录与留存数据互相印证。\n"
                "**影响**：建议优先改造 onboarding 流程。\n",
                encoding="utf-8",
            )

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S4")
            self.assertTrue(any("analysis-notes.md" in item for item in summary["completed_items"]))

    def test_workspace_summary_keeps_stage_at_s3_when_report_draft_exists_without_analysis_notes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(project_dir)
            self._write_data_log(project_dir)
            (project_dir / "report_draft_v1.md").write_text(
                "# Draft\n\n## Executive summary\nA concrete report section.\n",
                encoding="utf-8",
            )

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S3")
            self.assertIn("analysis-notes.md 创建/更新", summary["next_actions"])
            self.assertNotIn(
                "report_draft_v1.md / content/report.md / content/draft.md / output/final-report.md 任一形成有效草稿",
                summary["completed_items"],
            )

    def test_workspace_summary_advances_to_s5_when_report_draft_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(project_dir)
            self._write_data_log(project_dir)
            self._write_analysis_notes(project_dir)
            (project_dir / "report_draft_v1.md").write_text(
                "# Draft\n\n## Executive summary\nA concrete report section.\n",
                encoding="utf-8",
            )

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S5")
            self.assertIn(
                "report_draft_v1.md / content/report.md / content/draft.md / output/final-report.md 任一形成有效草稿",
                summary["completed_items"],
            )
            self.assertIn("review-checklist.md 完成", summary["next_actions"])

    def test_workspace_summary_keeps_stage_at_s0_when_project_overview_is_invalid_even_with_later_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(project_dir)
            self._write_data_log(project_dir)
            self._write_analysis_notes(project_dir)
            (project_dir / "report_draft_v1.md").write_text(
                "# Draft\n\n## Executive summary\nA concrete report section.\n",
                encoding="utf-8",
            )
            (project_dir / "plan" / "project-overview.md").write_text(
                "# Project overview\n\n",
                encoding="utf-8",
            )

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S0")
            self.assertNotIn(
                "report_draft_v1.md / content/report.md / content/draft.md / output/final-report.md 任一形成有效草稿",
                summary["completed_items"],
            )
            self.assertIn("需求访谈完成", summary["next_actions"][0])

    def test_workspace_summary_preserves_untracked_manual_stage_gate_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            stage_gates_path = project_dir / "plan" / "stage-gates.md"
            original = stage_gates_path.read_text(encoding="utf-8")
            stage_gates_path.write_text(
                original + "\n- [x] Manual client follow-up captured\n",
                encoding="utf-8",
            )

            engine.get_workspace_summary("demo")

            refreshed = stage_gates_path.read_text(encoding="utf-8")

            self.assertIn("- [x] Manual client follow-up captured", refreshed)
