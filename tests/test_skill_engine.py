import json
import os
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

    def _make_project(self) -> Path:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        engine, project_dir = self._create_engine_and_project(tmpdir.name)
        self.engine = engine
        return project_dir

    def _write_stage_gates_at_stage(self, project_dir: Path, stage_code: str):
        (project_dir / "plan" / "stage-gates.md").write_text(
            f"# Stage gates\n\n**阶段**: {stage_code}\n**状态**: 进行中\n",
            encoding="utf-8",
        )

    def _mark_s0_done(self, project_dir: Path):
        checkpoints_path = project_dir / "stage_checkpoints.json"
        checkpoints = {}
        if checkpoints_path.exists():
            checkpoints = json.loads(checkpoints_path.read_text(encoding="utf-8"))
        checkpoints.setdefault(
            "s0_interview_done_at",
            datetime.now().isoformat(timespec="seconds"),
        )
        checkpoints_path.write_text(
            json.dumps(checkpoints, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

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
        self._mark_s0_done(project_dir)

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

    def _make_project_with_all_s1_files(self) -> Path:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        engine, project_dir = self._create_engine_and_project(tmpdir.name)
        self.engine = engine
        overview_path = project_dir / "plan" / "project-overview.md"
        overview_text = overview_path.read_text(encoding="utf-8").replace(
            "**预期篇幅**: 3000 words",
            "**预期篇幅**: 6000 字",
        )
        overview_path.write_text(overview_text, encoding="utf-8")
        self._write_stage_two_prerequisites(project_dir)
        return project_dir

    def _make_project_past_outline_confirm(self) -> Path:
        project_dir = self._make_project_with_all_s1_files()
        self.engine._save_stage_checkpoint(project_dir, "outline_confirmed_at")
        return project_dir

    def _make_project_past_s3(self) -> Path:
        project_dir = self._make_project_past_outline_confirm()
        self._write_data_log_with_n_sources(project_dir, n=8)
        self._write_analysis_with_refs(project_dir, ref_count=5)
        return project_dir

    def _make_project_past_s4(self) -> Path:
        project_dir = self._make_project_past_s3()
        self._write_report(project_dir, word_count=4300)
        return project_dir

    def _make_project_past_s5(self) -> Path:
        project_dir = self._make_project_past_s4()
        (project_dir / "plan" / "review-checklist.md").write_text(
            "# 审查清单\n\n"
            "- [x] 事实与数据来源已核对\n"
            "- [x] 关键结论与证据一致\n"
            "- [x] 结构逻辑完整\n",
            encoding="utf-8",
        )
        self.engine._save_stage_checkpoint(project_dir, "review_started_at")
        self.engine._save_stage_checkpoint(project_dir, "review_passed_at")
        return project_dir

    def _write_data_log_with_n_sources(self, project_dir: Path, n: int):
        lines = ["# Data log", ""]
        for idx in range(1, n + 1):
            lines.extend(
                [
                    f"### [DL-{idx:03d}] Source {idx}",
                    f"- 来源: https://example.com/source-{idx}",
                    f"- 摘要: 第 {idx} 条来源记录包含实质证据。",
                    "",
                ]
            )
        (project_dir / "plan" / "data-log.md").write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    def _write_analysis_with_refs(self, project_dir: Path, ref_count: int):
        lines = ["# Analysis notes", "", "## Core insights", ""]
        for idx in range(1, ref_count + 1):
            lines.extend(
                [
                    f"### Insight {idx}",
                    f"Conclusion: 洞察 {idx} 聚焦关键业务问题。",
                    f"Evidence: 依据 [DL-{idx:03d}] 与相关访谈记录。",
                    f"Impact: 建议将洞察 {idx} 转化为执行动作。",
                    "",
                ]
            )
        (project_dir / "plan" / "analysis-notes.md").write_text(
            "\n".join(lines).strip() + "\n",
            encoding="utf-8",
        )

    def _write_report(self, project_dir: Path, word_count: int):
        body = "研" * word_count
        (project_dir / "report_draft_v1.md").write_text(
            "# Draft\n\n"
            "## Executive summary\n"
            f"{body}\n",
            encoding="utf-8",
        )

    def _write_report_draft(self, project_dir: Path, words: int):
        body = " ".join(f"word{idx}" for idx in range(words))
        (project_dir / "report_draft_v1.md").write_text(
            "# Draft\n\n"
            "## Executive summary\n"
            f"{body}\n",
            encoding="utf-8",
        )

    def _write_review_checklist(self, project_dir: Path):
        (project_dir / "plan" / "review-checklist.md").write_text(
            "# Review checklist\n\n"
            "- [x] Facts and sources checked\n"
            "- [x] Conclusions align with evidence\n"
            "- [x] Structure and logic reviewed\n",
            encoding="utf-8",
        )

    def _write_delivery_log(self, project_dir: Path):
        (project_dir / "plan" / "delivery-log.md").write_text(
            "# Delivery log\n\n"
            "Delivery date: 2026-04-10\n"
            "Shared with client: executive steering committee\n"
            "Feedback: client requested follow-up workshop\n",
            encoding="utf-8",
        )

    def _assert_items_include(self, items, fragment: str):
        self.assertTrue(any(fragment in item for item in items), msg=f"Expected `{fragment}` in {items}")

    def _assert_items_exclude(self, items, fragment: str):
        self.assertFalse(any(fragment in item for item in items), msg=f"Did not expect `{fragment}` in {items}")

    def test_create_project_initializes_formal_plan_templates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = projects_dir / "demo"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            engine.create_project(
                "demo",
                "strategy-consulting",
                "AI 鎴樼暐瑙勫垝",
                "楂樺眰鍐崇瓥鑰?",
                "2026-04-01",
                "3000瀛?",
                "宸叉湁璁胯皥绾",
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

    def test_create_project_rejects_non_directory_workspace_with_clean_message(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_file = Path(tmpdir) / "workspace.txt"
            workspace_file.write_text("not a directory", encoding="utf-8")
            engine = SkillEngine(projects_dir, self.repo_skill_dir)

            with self.assertRaisesRegex(ValueError, "工作目录无效"):
                engine.create_project(self._project_payload(workspace_file))

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

        self.assertNotIn("闃舵0锛氶」鐩垵濮嬪寲", template_text)
        self.assertNotIn("闃舵1锛氬ぇ绾茶璁?", template_text)
        self.assertNotIn("闃舵2锛氬垎娈垫挵鍐?", template_text)
        self.assertNotIn("闃舵3锛氳川閲忓鏌?", template_text)
        self.assertNotIn("闃舵4锛氭暣鍚堝鍑?", template_text)

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
        self.assertIn("仅当交付形式 = `报告+演示`", lifecycle_text)

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
        self.assertIn("content/final-report.md", template_text)
        self.assertIn("output/final-report.md", template_text)
        self.assertIn("交付形式 = 报告+演示", template_text)
        self.assertIn("presentation-plan.md 完成", template_text)
        self.assertIn("仅报告", template_text)
        self.assertIn("delivery-log.md 更新", template_text)

    def test_workspace_summary_reads_stage_from_real_stage_gates_template(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                "demo",
                "strategy-consulting",
                "AI 鎴樼暐瑙勫垝",
                "楂樺眰鍐崇瓥鑰?",
                "2026-04-01",
                "3000瀛?",
                "宸叉湁璁胯皥绾",
            )
            self._mark_s0_done(Path(project["project_dir"]))

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S1")
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
                "AI 鎴樼暐瑙勫垝",
                "楂樺眰鍐崇瓥鑰?",
                "2026-04-01",
                "3000瀛?",
                "宸叉湁璁胯皥绾",
            )

            (projects_dir / "demo" / ".consulting-report" / "plan" / "project-info.md").write_text(
                "legacy project info should stay out of core context",
                encoding="utf-8",
            )
            (projects_dir / "demo" / ".consulting-report" / "plan" / "tasks.md").write_text(
                "# 浠诲姟娓呭崟\n\n## 褰撳墠闃舵\n**闃舵**: S1\n\n### S1 鐮旂┒璁捐\n- [ ] 鏇存柊 references.md\n",
                encoding="utf-8",
            )
            context = engine.build_project_context("demo")
            self.assertNotIn("legacy project info should stay out of core context", context)

            self.assertIn("## 当前项目概览", context)
            self.assertIn("## 当前项目进度", context)
            self.assertIn("## 阶段门禁", context)
            self.assertIn("## 项目备注", context)
            self.assertIn("## 当前阶段任务", context)
            self.assertIn("project-overview.md 创建", context)
            self.assertNotIn("当前项目信息", context)
            self.assertNotIn("当前大纲", context)

    def test_build_project_context_rewrites_stale_stage_tracking_files_before_reading_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            plan_dir = project_dir / "plan"
            (plan_dir / "tasks.md").write_text(
                "# 娴犺濮熷〒鍛礋\n\n## 瑜版挸澧犻梼鑸殿唽\n**闂冭埖顔?*: S4\n- [ ] stale task\n",
                encoding="utf-8",
            )
            (plan_dir / "progress.md").write_text(
                "# 妞ゅ湱娲版潻娑樺\n\n**闂冭埖顔?*: S4\n",
                encoding="utf-8",
            )
            (plan_dir / "stage-gates.md").write_text(
                "# 闂冭埖顔岄梻銊ь洣\n\n**闂冭埖顔?*: S4\n",
                encoding="utf-8",
            )

            context = engine.build_project_context("demo")

            self.assertNotIn("stale task", context)
            self.assertNotIn("**闂冭埖顔?*: S4", context)
            self.assertIn("S0", context)

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
            (content_dir / "outline.md").write_text("# 澶х翰", encoding="utf-8")
            (content_dir / "report.md").write_text("# 姝ｆ枃", encoding="utf-8")
            report_path = engine.get_primary_report_path("demo")

            self.assertTrue(report_path.endswith("report.md"))

    def test_write_file_rejects_unregistered_plan_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, _project_dir = self._create_engine_and_project(tmpdir)

            with self.assertRaisesRegex(ValueError, "gate-control.md"):
                engine.write_file("demo", "plan/gate-control.md", "# Gate control")

    def test_write_file_rejects_backend_owned_stage_tracking_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, _project_dir = self._create_engine_and_project(tmpdir)

            for file_path in ("plan/stage-gates.md", "plan/progress.md", "plan/tasks.md"):
                with self.assertRaisesRegex(ValueError, "backend-generated"):
                    engine.write_file("demo", file_path, "# stale")

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
            self._assert_items_include(summary["completed_items"], "outline.md")
            self._assert_items_include(summary["next_actions"], "research-plan.md")

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
            self._assert_items_exclude(summary["completed_items"], "research-plan.md")
            self._assert_items_include(summary["next_actions"], "research-plan.md")

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
            self._assert_items_exclude(summary["completed_items"], "references.md")
            self._assert_items_include(summary["next_actions"], "references.md")

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
            self._assert_items_exclude(summary["completed_items"], "references.md")
            self._assert_items_include(summary["next_actions"], "references.md")

    def test_workspace_summary_keeps_stage_at_s1_when_reference_lines_still_embed_placeholder_brackets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(
                project_dir,
                references_text=(
                    "# References\n\n"
                    "## Sources\n"
                    "- 案例引用：[公司/项目名称]案例\n"
                    "- 数据引用：数据来源于[来源名称]\n"
                ),
            )

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S1")
            self._assert_items_exclude(summary["completed_items"], "references.md")
            self._assert_items_include(summary["next_actions"], "references.md")

    def test_workspace_summary_advances_to_s2_when_research_design_files_meet_evidence_gate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(project_dir)
            engine._save_stage_checkpoint(project_dir, "outline_confirmed_at")

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S2")
            self._assert_items_include(summary["completed_items"], "research-plan.md")
            self._assert_items_include(summary["next_actions"], "data-log.md")

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
            engine._save_stage_checkpoint(project_dir, "outline_confirmed_at")

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S2")
            self._assert_items_include(summary["completed_items"], "references.md")

    def test_workspace_summary_accepts_numbered_reference_entries_as_evidence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(
                project_dir,
                references_text=(
                    "# References\n\n"
                    "1. Company annual report (2025): renewal trend summary.\n"
                    "2. Industry benchmark memo (2025): onboarding conversion study.\n"
                ),
            )
            engine._save_stage_checkpoint(project_dir, "outline_confirmed_at")

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S2")
            self._assert_items_include(summary["completed_items"], "references.md")

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
            self._assert_items_exclude(summary["completed_items"], "research-plan.md")
            self._assert_items_include(summary["next_actions"], "research-plan.md")

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
            self._mark_s0_done(project_dir)
            engine._save_stage_checkpoint(project_dir, "outline_confirmed_at")

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
            self._mark_s0_done(project_dir)

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S1")
            self._assert_items_exclude(summary["completed_items"], "notes.md")
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
            self._mark_s0_done(project_dir)

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S1")
            self._assert_items_exclude(summary["completed_items"], "notes.md")
            self.assertTrue(any("notes.md" in item for item in summary["next_actions"]))

    def test_workspace_summary_advances_to_s3_when_data_log_is_effective(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(project_dir)
            engine._save_stage_checkpoint(project_dir, "outline_confirmed_at")
            self._write_data_log_with_n_sources(project_dir, n=8)

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S3")
            self._assert_items_include(summary["completed_items"], "data-log.md")
            self._assert_items_include(summary["next_actions"], "analysis-notes.md")

    def test_count_valid_data_log_sources_accepts_dl_id_entries_with_source_markers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            (project_dir / "plan" / "data-log.md").write_text(
                "# Data log\n\n"
                "### [DL-2024-01] 财政部数据资源暂行规定\n"
                "- **来源**：财政部\n"
                "- **时间**：2024-01-01\n"
                "- **URL**：https://www.example.com/policy\n"
                "- **用途**：政策基石，用于第一章背景部分\n\n"
                "### [DL-2024-02] 内部材料中的预算口径\n"
                "- **来源**：预算模型\n"
                "- **时间**：2024-01-02\n"
                "- **URL**：material:mat-123\n"
                "- **用途**：用于测算假设\n\n"
                "### [DL-2024-03] 运营负责人访谈\n"
                "- **来源**：运营负责人\n"
                "- **时间**：2024-01-03\n"
                "访谈:运营负责人-2024-01-03\n"
                "- **用途**：用于识别执行阻力\n\n"
                "### [DL-2024-04] 客户调研反馈\n"
                "- **来源**：客户问卷\n"
                "- **时间**：2024-01-04\n"
                "调研:客户问卷-2024-01-04\n"
                "- **用途**：用于需求优先级判断\n",
                encoding="utf-8",
            )

            count = engine._count_valid_data_log_sources(project_dir)

        self.assertEqual(count, 4)

    def test_count_valid_data_log_sources_ignores_markdown_table_rows_even_with_source_urls(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            (project_dir / "plan" / "data-log.md").write_text(
                "# Data log\n\n"
                "| 时间 | 来源 | 事实描述 | 用途 |\n"
                "| --- | --- | --- | --- |\n"
                "| 2024-01-01 | https://www.example.com/policy | 政策发布 | 背景 |\n"
                "| 2024-01-02 | material:mat-123 | 内部预算口径 | 测算 |\n"
                "| 2024-01-03 | 访谈:运营负责人-2024-01-03 | 执行阻力 | 访谈证据 |\n",
                encoding="utf-8",
            )

            count = engine._count_valid_data_log_sources(project_dir)

        self.assertEqual(count, 0)

    def test_workspace_summary_keeps_stage_at_s2_when_data_log_only_contains_placeholder_rows_after_small_edit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(project_dir)
            engine._save_stage_checkpoint(project_dir, "outline_confirmed_at")
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
            self._assert_items_exclude(summary["completed_items"], "data-log.md")
            self.assertTrue(any("data-log.md" in item for item in summary["next_actions"]))

    def test_workspace_summary_keeps_stage_at_s2_when_data_log_only_contains_bullet_placeholders(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(project_dir)
            engine._save_stage_checkpoint(project_dir, "outline_confirmed_at")
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
            self._assert_items_exclude(summary["completed_items"], "data-log.md")
            self.assertTrue(any("data-log.md" in item for item in summary["next_actions"]))

    def test_workspace_summary_keeps_stage_at_s2_when_analysis_notes_exist_without_data_log(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(project_dir)
            engine._save_stage_checkpoint(project_dir, "outline_confirmed_at")
            self._write_analysis_notes(project_dir)

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S2")
            self._assert_items_include(summary["next_actions"], "data-log.md")
            self._assert_items_exclude(summary["completed_items"], "analysis-notes.md")

    def test_workspace_summary_advances_to_s4_when_analysis_notes_are_complete_without_report_draft(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(project_dir)
            engine._save_stage_checkpoint(project_dir, "outline_confirmed_at")
            self._write_data_log_with_n_sources(project_dir, n=8)
            self._write_analysis_with_refs(project_dir, ref_count=5)

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S4")
            self._assert_items_include(summary["completed_items"], "analysis-notes.md")
            self._assert_items_include(summary["next_actions"], "report_draft_v1.md")

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
            self._mark_s0_done(project_dir)
            engine._save_stage_checkpoint(project_dir, "outline_confirmed_at")
            self._write_data_log_with_n_sources(project_dir, n=8)
            self._write_analysis_with_refs(project_dir, ref_count=5)

            summary = engine.get_workspace_summary("demo")
            stage_gates_text = (project_dir / "plan" / "stage-gates.md").read_text(encoding="utf-8")

            self.assertEqual(summary["stage_code"], "S4")
            self.assertIn("S4", stage_gates_text)

    def test_workspace_summary_keeps_stage_at_s3_when_analysis_notes_are_only_keyword_headings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(project_dir)
            engine._save_stage_checkpoint(project_dir, "outline_confirmed_at")
            self._write_data_log_with_n_sources(project_dir, n=8)
            (project_dir / "plan" / "analysis-notes.md").write_text(
                "# Analysis notes\n\n"
                "## Conclusion\n"
                "## Evidence\n"
                "## Impact\n",
                encoding="utf-8",
            )

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S3")
            self._assert_items_exclude(summary["completed_items"], "analysis-notes.md")
            self._assert_items_include(summary["next_actions"], "analysis-notes.md")

    def test_workspace_summary_keeps_stage_at_s3_when_analysis_notes_only_rephrase_template_labels(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(project_dir)
            engine._save_stage_checkpoint(project_dir, "outline_confirmed_at")
            self._write_data_log_with_n_sources(project_dir, n=8)
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
            self._assert_items_exclude(summary["completed_items"], "analysis-notes.md")
            self.assertTrue(any("analysis-notes.md" in item for item in summary["next_actions"]))

    def test_workspace_summary_accepts_template_aligned_analysis_notes_with_chinese_labels(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(project_dir)
            engine._save_stage_checkpoint(project_dir, "outline_confirmed_at")
            self._write_data_log_with_n_sources(project_dir, n=8)
            (project_dir / "plan" / "analysis-notes.md").write_text(
                "# 分析笔记\n\n"
                "## 核心洞察\n"
                "### 洞察 1\n"
                "**结论**：续约风险主要来自导入期摩擦。\n"
                "**证据**：[DL-001]、[DL-002]、[DL-003]、[DL-004]、[DL-005] 互相印证。\n"
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
            engine._save_stage_checkpoint(project_dir, "outline_confirmed_at")
            self._write_data_log_with_n_sources(project_dir, n=8)
            (project_dir / "report_draft_v1.md").write_text(
                "# Draft\n\n## Executive summary\nA concrete report section.\n",
                encoding="utf-8",
            )

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S3")
            self._assert_items_include(summary["next_actions"], "analysis-notes.md")
            self._assert_items_exclude(summary["completed_items"], "report_draft_v1.md")

    def test_workspace_summary_advances_to_s5_when_report_draft_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(project_dir)
            engine._save_stage_checkpoint(project_dir, "outline_confirmed_at")
            self._write_data_log_with_n_sources(project_dir, n=8)
            self._write_analysis_with_refs(project_dir, ref_count=5)
            self._write_report(project_dir, word_count=4300)
            engine._save_stage_checkpoint(project_dir, "review_started_at")

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S5")
            self._assert_items_include(summary["completed_items"], "report_draft_v1.md")
            self._assert_items_include(summary["next_actions"], "review-checklist.md")

    def test_workspace_summary_keeps_stage_at_s5_when_review_checklist_has_only_one_checked_item(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, project_dir = self._create_engine_and_project(tmpdir)
            self._write_stage_two_prerequisites(project_dir)
            engine._save_stage_checkpoint(project_dir, "outline_confirmed_at")
            self._write_data_log_with_n_sources(project_dir, n=8)
            self._write_analysis_with_refs(project_dir, ref_count=5)
            self._write_report(project_dir, word_count=4300)
            engine._save_stage_checkpoint(project_dir, "review_started_at")
            (project_dir / "plan" / "review-checklist.md").write_text(
                "# 审查清单\n\n"
                "- [x] 事实与数据来源已核对\n"
                "- [ ] 关键结论与证据一致\n"
                "- [ ] 结构逻辑完整\n",
                encoding="utf-8",
            )

            summary = engine.get_workspace_summary("demo")

            self.assertEqual(summary["stage_code"], "S5")
            self._assert_items_include(summary["next_actions"], "review-checklist.md")

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
            self._assert_items_exclude(summary["completed_items"], "report_draft_v1.md")
            self.assertEqual(summary["next_actions"][0], "需求访谈完成")

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

    def test_workspace_summary_s2_reports_data_log_quality_progress(self):
        project_dir = self._make_project_past_outline_confirm()
        self._write_data_log_with_n_sources(project_dir, n=3)

        summary = self.engine.get_workspace_summary("demo")

        self.assertEqual(summary["stage_code"], "S2")
        self.assertEqual(
            summary["quality_progress"],
            {"label": "有效来源条目", "current": 3, "target": 8},
        )

    def test_workspace_summary_next_stage_hint_s6_when_review_passed_and_presentation_required(self):
        project_dir = self._make_project_past_s5()
        overview_path = project_dir / "plan" / "project-overview.md"
        overview_text = overview_path.read_text(encoding="utf-8").replace(
            "**交付形式**: 仅报告",
            "**交付形式**: 报告+演示",
        )
        overview_path.write_text(overview_text, encoding="utf-8")

        summary = self.engine.get_workspace_summary("demo")

        self.assertEqual(summary["next_stage_hint"], "S6")

    def test_workspace_summary_next_stage_hint_s7_when_review_passed_and_report_only(self):
        project_dir = self._make_project_past_s5()

        summary = self.engine.get_workspace_summary("demo")

        self.assertEqual(summary["next_stage_hint"], "S7")

    def test_workspace_summary_next_stage_hint_none_without_review_passed_checkpoint(self):
        project_dir = self._make_project_past_s4()
        self.engine._save_stage_checkpoint(project_dir, "review_started_at")

        summary = self.engine.get_workspace_summary("demo")

        self.assertIsNone(summary["next_stage_hint"])

    def test_workspace_summary_word_count_uses_max_report_candidate_count(self):
        project_dir = self._make_project()
        (project_dir / "content" / "report.md").write_text(
            ("短" * 800) + "\n",
            encoding="utf-8",
        )
        (project_dir / "output" / "final-report.md").write_text(
            ("长" * 5000) + "\n",
            encoding="utf-8",
        )

        summary = self.engine.get_workspace_summary("demo")

        self.assertEqual(summary["word_count"], 5000)

    def test_workspace_summary_sets_stalled_since_when_s2_evidence_is_old(self):
        project_dir = self._make_project_past_outline_confirm()
        self._write_data_log_with_n_sources(project_dir, n=3)
        old_time = datetime.now().timestamp() - 31 * 60
        for file_name in ("notes.md", "references.md", "data-log.md", "analysis-notes.md"):
            path = project_dir / "plan" / file_name
            os.utime(path, (old_time, old_time))

        summary = self.engine.get_workspace_summary("demo")

        self.assertEqual(summary["stage_code"], "S2")
        self.assertIsNotNone(summary["stalled_since"])

    def test_workspace_summary_stalled_since_none_for_s4_even_when_evidence_is_old(self):
        project_dir = self._make_project_past_s3()
        old_time = datetime.now().timestamp() - 31 * 60
        for file_name in ("notes.md", "references.md", "data-log.md", "analysis-notes.md"):
            path = project_dir / "plan" / file_name
            os.utime(path, (old_time, old_time))

        summary = self.engine.get_workspace_summary("demo")

        self.assertEqual(summary["stage_code"], "S4")
        self.assertIsNone(summary["stalled_since"])

    def test_workspace_summary_delivery_mode_reports_presentation_mode(self):
        project_dir = self._make_project()
        overview_path = project_dir / "plan" / "project-overview.md"
        overview_text = overview_path.read_text(encoding="utf-8").replace(
            "**交付形式**: 仅报告",
            "**交付形式**: 报告+演示",
        )
        overview_path.write_text(overview_text, encoding="utf-8")

        summary = self.engine.get_workspace_summary("demo")

        self.assertEqual(summary["delivery_mode"], "报告+演示")

    def test_workspace_summary_delivery_mode_defaults_to_report_only_when_key_absent(self):
        project_dir = self._make_project()
        overview_path = project_dir / "plan" / "project-overview.md"
        overview_text = "\n".join(
            line for line in overview_path.read_text(encoding="utf-8").splitlines()
            if "交付形式" not in line
        )
        overview_path.write_text(overview_text, encoding="utf-8")

        summary = self.engine.get_workspace_summary("demo")

        self.assertEqual(summary["delivery_mode"], "仅报告")

    def test_infer_stage_holds_at_s1_without_outline_checkpoint(self):
        project_dir = self._make_project_with_all_s1_files()
        state = self.engine._infer_stage_state(project_dir)
        self.assertEqual(state["stage_code"], "S1")

    def test_infer_stage_advances_to_s2_once_outline_checkpoint_set(self):
        project_dir = self._make_project_with_all_s1_files()
        self.engine._save_stage_checkpoint(project_dir, "outline_confirmed_at")
        state = self.engine._infer_stage_state(project_dir)
        self.assertEqual(state["stage_code"], "S2")

    def test_infer_stage_holds_at_s3_when_analysis_refs_insufficient(self):
        project_dir = self._make_project_past_outline_confirm()
        self._write_data_log_with_n_sources(project_dir, n=8)
        self._write_analysis_with_refs(project_dir, ref_count=1)
        state = self.engine._infer_stage_state(project_dir)
        self.assertEqual(state["stage_code"], "S3")

    def test_infer_stage_holds_at_s4_when_word_count_below_floor(self):
        project_dir = self._make_project_past_s3()
        self._write_report(project_dir, word_count=1200)
        state = self.engine._infer_stage_state(project_dir)
        self.assertEqual(state["stage_code"], "S4")

    def test_infer_stage_holds_at_s5_without_review_passed_checkpoint(self):
        project_dir = self._make_project_past_s4()
        self.engine._save_stage_checkpoint(project_dir, "review_started_at")
        state = self.engine._infer_stage_state(project_dir)
        self.assertEqual(state["stage_code"], "S5")

    def test_infer_stage_returns_done_after_delivery_archived(self):
        project_dir = self._make_project_past_s5()
        for key in ("review_passed_at", "delivery_archived_at"):
            self.engine._save_stage_checkpoint(project_dir, key)
        self._write_delivery_log(project_dir)
        state = self.engine._infer_stage_state(project_dir)
        self.assertEqual(state["stage_code"], "done")
        self.assertEqual(state["stage_status"], "已归档")

    def test_infer_stage_stays_at_s7_when_archived_stamp_missing(self):
        project_dir = self._make_project_past_s5()
        self.engine._save_stage_checkpoint(project_dir, "review_passed_at")
        self._write_delivery_log(project_dir)
        state = self.engine._infer_stage_state(project_dir)
        self.assertEqual(state["stage_code"], "S7")
        self.assertEqual(state["stage_status"], "进行中")

    def test_migration_only_backfills_outline_even_for_old_s7_projects(self):
        project_dir = self._make_project()
        self._write_stage_gates_at_stage(project_dir, "S7")
        self._write_report_draft(project_dir, words=5000)

        self.engine._backfill_stage_checkpoints_if_missing(project_dir)

        checkpoints = self.engine._load_stage_checkpoints(project_dir)
        self.assertIn("outline_confirmed_at", checkpoints)
        self.assertNotIn("review_started_at", checkpoints)
        self.assertNotIn("review_passed_at", checkpoints)
        self.assertNotIn("delivery_archived_at", checkpoints)

    def test_backfill_stage_checkpoints_is_idempotent(self):
        project_dir = self._make_project()
        self._write_stage_gates_at_stage(project_dir, "S3")

        self.engine._backfill_stage_checkpoints_if_missing(project_dir)
        checkpoints_path = self.engine._stage_checkpoints_path(project_dir)
        first_content = checkpoints_path.read_text(encoding="utf-8")

        self.engine._backfill_stage_checkpoints_if_missing(project_dir)
        second_content = checkpoints_path.read_text(encoding="utf-8")

        self.assertEqual(first_content, second_content)

    def test_clear_cascade_clears_all_subsequent_checkpoints(self):
        project_dir = self._make_project()
        for key in ("outline_confirmed_at", "review_started_at", "review_passed_at", "delivery_archived_at"):
            self.engine._save_stage_checkpoint(project_dir, key)
        raw = self.engine._read_raw_stage_checkpoints(project_dir)
        raw[self.engine.MIGRATION_MARKER_KEY] = "2026-04-20T18:00:00"
        self.engine._write_raw_stage_checkpoints(project_dir, raw)

        self.engine._clear_stage_checkpoint_cascade(project_dir, "review_started_at")

        checkpoints = self.engine._load_stage_checkpoints(project_dir)
        raw_checkpoints = self.engine._read_raw_stage_checkpoints(project_dir)
        self.assertIn("outline_confirmed_at", checkpoints)
        self.assertNotIn("review_started_at", checkpoints)
        self.assertNotIn("review_passed_at", checkpoints)
        self.assertNotIn("delivery_archived_at", checkpoints)
        self.assertEqual(raw_checkpoints[self.engine.MIGRATION_MARKER_KEY], "2026-04-20T18:00:00")

    def test_record_stage_checkpoint_set_and_clear_roundtrip(self):
        project_dir = self._make_project()
        self._write_stage_two_prerequisites(project_dir)

        result = self.engine.record_stage_checkpoint("demo", "outline_confirmed_at", "set")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["key"], "outline_confirmed_at")
        self.assertIn("outline_confirmed_at", self.engine._load_stage_checkpoints(project_dir))

        cleared = self.engine.record_stage_checkpoint("demo", "outline_confirmed_at", "clear")
        self.assertEqual(cleared, {"status": "ok", "key": "outline_confirmed_at", "cleared": True})
        self.assertNotIn("outline_confirmed_at", self.engine._load_stage_checkpoints(project_dir))

    def test_record_stage_checkpoint_rejects_outline_confirmation_without_effective_outline(self):
        project_dir = self._make_project()

        with self.assertRaises(ValueError):
            self.engine.record_stage_checkpoint("demo", "outline_confirmed_at", "set")

        self.assertNotIn("outline_confirmed_at", self.engine._read_raw_stage_checkpoints(project_dir))
        self.assertEqual(self.engine._load_stage_checkpoints(project_dir), {})


class S0CheckpointInfrastructureTests(unittest.TestCase):
    def test_s0_in_stage_checkpoint_keys(self):
        from backend.skill import SkillEngine
        self.assertIn("s0_interview_done_at", SkillEngine.STAGE_CHECKPOINT_KEYS)

    def test_s0_first_in_cascade_order(self):
        from backend.skill import SkillEngine
        self.assertEqual(SkillEngine._CASCADE_ORDER[0], "s0_interview_done_at")

    def test_s0_prereq_none_entry_present(self):
        from backend.skill import SkillEngine
        self.assertIn("s0_interview_done_at", SkillEngine.CHECKPOINT_PREREQ)
        self.assertIsNone(SkillEngine.CHECKPOINT_PREREQ["s0_interview_done_at"])

    def test_cascade_order_covers_all_keys_assertion_still_holds(self):
        # SkillEngine has `assert set(_CASCADE_ORDER) == STAGE_CHECKPOINT_KEYS`
        # at class-body level. If Task A broke parity, import fails outright.
        import backend.skill
        self.assertTrue(hasattr(backend.skill, "SkillEngine"))

    def test_s0_prereq_notice_returns_none(self):
        import tempfile
        from pathlib import Path
        from backend.skill import SkillEngine
        with tempfile.TemporaryDirectory() as tmp:
            engine = SkillEngine(Path(tmp) / "p", Path(tmp) / "s")
            self.assertIsNone(
                engine.get_stage_checkpoint_prereq_notice("s0_interview_done_at")
            )


class S0StageInferenceTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from backend.skill import SkillEngine
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        projects_dir = Path(self.tmp.name) / "projects"
        skill_dir = Path(__file__).resolve().parents[1] / "skill"
        projects_dir.mkdir()
        self.engine = SkillEngine(projects_dir, skill_dir)
        project = self.engine.create_project(
            name="demo-s0",
            workspace_dir=str(Path(self.tmp.name) / "ws"),
            project_type="strategy-consulting",
            theme="S0 test",
            target_audience="CFO",
            deadline="2026-12-31",
            expected_length="3000",
        )
        self.project_path = Path(project["project_dir"])

    def test_s0_without_checkpoint_stays_s0(self):
        state = self.engine._infer_stage_state(self.project_path)
        self.assertEqual(state["stage_code"], "S0")

    def test_s0_with_checkpoint_advances_to_s1(self):
        import json
        from datetime import datetime
        (self.project_path / "stage_checkpoints.json").write_text(
            json.dumps({
                "s0_interview_done_at": datetime.now().isoformat(timespec="seconds"),
            }),
            encoding="utf-8",
        )
        state = self.engine._infer_stage_state(self.project_path)
        self.assertEqual(state["stage_code"], "S1")

    def test_flags_has_s0_interview_done(self):
        state = self.engine._infer_stage_state(self.project_path)
        self.assertIn("s0_interview_done", state["flags"])
        self.assertFalse(state["flags"]["s0_interview_done"])

    def test_flags_s0_true_after_checkpoint(self):
        import json
        (self.project_path / "stage_checkpoints.json").write_text(
            json.dumps({"s0_interview_done_at": "2026-04-21T12:00:00"}),
            encoding="utf-8",
        )
        state = self.engine._infer_stage_state(self.project_path)
        self.assertTrue(state["flags"]["s0_interview_done"])

    def test_build_completed_s0_only_lights_overview(self):
        # S0 stage, project-overview.md exists (from create_project),
        # no s0_interview_done_at checkpoint — should only light item [2]
        from backend.skill import SkillEngine
        state = self.engine._infer_stage_state(self.project_path)
        completed = state["completed_items"]
        overview_item = SkillEngine.STAGE_CHECKLIST_ITEMS["S0"][2]  # "project-overview.md 创建"
        self.assertIn(overview_item, completed)
        # Other S0 items NOT complete yet
        interview_item = SkillEngine.STAGE_CHECKLIST_ITEMS["S0"][0]  # "需求访谈完成"
        self.assertNotIn(interview_item, completed)


class S0SchemaMigrationTests(unittest.TestCase):
    def setUp(self):
        import tempfile, json
        from pathlib import Path
        from backend.skill import SkillEngine
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.projects_dir = Path(self.tmp.name) / "projects"
        self.skill_dir = Path(self.tmp.name) / "skill"
        self.projects_dir.mkdir()
        self.skill_dir.mkdir()
        self.engine = SkillEngine(self.projects_dir, self.skill_dir)
        self.project_path = self.projects_dir / "proj-test"
        (self.project_path / "plan").mkdir(parents=True)

    def _write_stage_gates(self, stage_code):
        (self.project_path / "plan" / "stage-gates.md").write_text(
            f"# 项目阶段与门禁\n\n## 当前阶段\n\n**阶段**: {stage_code}\n",
            encoding="utf-8",
        )

    def _write_checkpoints(self, data):
        import json
        (self.project_path / "stage_checkpoints.json").write_text(
            json.dumps(data), encoding="utf-8"
        )

    def _read_checkpoints(self):
        import json
        path = self.project_path / "stage_checkpoints.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def test_file_missing_stage_s0_creates_with_marker_no_s0(self):
        self._write_stage_gates("S0")
        self.engine._backfill_stage_checkpoints_if_missing(self.project_path)
        raw = self._read_checkpoints()
        self.assertIn("__migrated_at", raw)
        self.assertNotIn("s0_interview_done_at", raw)  # stage=S0 does not backfill

    def test_file_missing_stage_s1_backfills_s0(self):
        self._write_stage_gates("S1")
        self.engine._backfill_stage_checkpoints_if_missing(self.project_path)
        raw = self._read_checkpoints()
        self.assertIn("s0_interview_done_at", raw)
        # outline_confirmed_at still gated at stage >= S2
        self.assertNotIn("outline_confirmed_at", raw)

    def test_file_missing_stage_s2_backfills_both_s0_and_outline(self):
        self._write_stage_gates("S2")
        self.engine._backfill_stage_checkpoints_if_missing(self.project_path)
        raw = self._read_checkpoints()
        self.assertIn("s0_interview_done_at", raw)
        self.assertIn("outline_confirmed_at", raw)

    def test_file_exists_missing_s0_stage_s1_backfills_s0(self):
        # Simulates a 4-17 spec project: file exists with marker but no s0 key
        self._write_checkpoints({"__migrated_at": "2026-04-17T10:00:00"})
        self._write_stage_gates("S1")
        self.engine._backfill_stage_checkpoints_if_missing(self.project_path)
        raw = self._read_checkpoints()
        self.assertIn("s0_interview_done_at", raw)

    def test_file_exists_missing_s0_stage_s0_does_not_backfill(self):
        self._write_checkpoints({"__migrated_at": "2026-04-17T10:00:00"})
        self._write_stage_gates("S0")
        self.engine._backfill_stage_checkpoints_if_missing(self.project_path)
        raw = self._read_checkpoints()
        self.assertNotIn("s0_interview_done_at", raw)

    def test_file_exists_with_outline_confirmed_backfills_s0(self):
        # outline is downstream → imply s0 done (4-17 spec project mid-stage)
        self._write_checkpoints({
            "__migrated_at": "2026-04-17T10:00:00",
            "outline_confirmed_at": "2026-04-18T09:00:00",
        })
        # no stage-gates.md this time — rely on downstream-checkpoint heuristic
        self.engine._backfill_stage_checkpoints_if_missing(self.project_path)
        raw = self._read_checkpoints()
        self.assertIn("s0_interview_done_at", raw)
        self.assertEqual(raw["outline_confirmed_at"], "2026-04-18T09:00:00")

    def test_file_exists_has_s0_noop(self):
        ts = "2026-04-20T08:00:00"
        self._write_checkpoints({
            "__migrated_at": "2026-04-17T10:00:00",
            "s0_interview_done_at": ts,
        })
        self._write_stage_gates("S2")
        self.engine._backfill_stage_checkpoints_if_missing(self.project_path)
        raw = self._read_checkpoints()
        self.assertEqual(raw["s0_interview_done_at"], ts)

    def test_idempotent_second_call_no_change(self):
        self._write_stage_gates("S2")
        self.engine._backfill_stage_checkpoints_if_missing(self.project_path)
        first = self._read_checkpoints()
        self.engine._backfill_stage_checkpoints_if_missing(self.project_path)
        second = self._read_checkpoints()
        self.assertEqual(first, second)
