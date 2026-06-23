import unittest
import tempfile
from pathlib import Path


class SkillAssetTests(unittest.TestCase):
    def test_runtime_skill_assets_include_referenced_cross_platform_files(self):
        root = Path(__file__).resolve().parents[1]
        required_files = [
            root / "skill" / "evals" / "capability-map.json",
        ]

        for file_path in required_files:
            self.assertTrue(file_path.exists(), f"缺少运行资产: {file_path}")

        # N7: lint / quality_check scripts are deleted — must not be present.
        self.assertFalse((root / "skill" / "scripts" / "quality_check.sh").exists())
        self.assertFalse((root / "skill" / "scripts" / "quality_check.ps1").exists())

    def test_active_docs_no_longer_reference_export_scripts(self):
        root = Path(__file__).resolve().parents[1]
        # 扫 skill 文档 + 根 BUILD/WINDOWS_BUILD（BUILD.md 也引用 export_draft.ps1）
        targets = list((root / "skill").rglob("*.md")) + [root / "BUILD.md", root / "WINDOWS_BUILD.md"]
        offenders = []
        for p in targets:
            if not p.exists():
                continue
            text = p.read_text(encoding="utf-8")
            if "export_draft.ps1" in text or "export_draft.sh" in text or "scripts/export_draft" in text:
                offenders.append(str(p.relative_to(root)))
        self.assertEqual(offenders, [], f"文档仍引用退役导出脚本: {offenders}")
        self.assertFalse((root / "skill" / "scripts" / "export_draft.ps1").exists())
        self.assertFalse((root / "skill" / "scripts" / "export_draft.sh").exists())

    def test_formal_plan_files_includes_independent_review_output(self):
        from backend.skill import SkillEngine

        self.assertIn("independent-review.md", SkillEngine.FORMAL_PLAN_FILES)
        # N7: lint-report.md is retired — no longer an official plan file.
        self.assertNotIn("lint-report.md", SkillEngine.FORMAL_PLAN_FILES)

    def test_formal_plan_files_no_longer_includes_review_checklist_after_cutover(self):
        from backend.skill import SkillEngine

        self.assertNotIn("review-checklist.md", SkillEngine.FORMAL_PLAN_FILES)

    def test_initialize_project_creates_independent_review_stub_only(self):
        from backend.skill import SkillEngine

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, root / "skill")
            project = engine.create_project(
                {
                    "name": "demo",
                    "workspace_dir": str(workspace_dir),
                    "project_type": "strategy-consulting",
                    "theme": "AI strategy review",
                    "target_audience": "executive audience",
                    "deadline": "2026-04-01",
                    "expected_length": "3000 words",
                    "notes": "",
                }
            )
            plan_dir = Path(project["project_dir"]) / "plan"

            self.assertTrue((plan_dir / "independent-review.md").exists())
            self.assertIn("independent-review:pending", (plan_dir / "independent-review.md").read_text(encoding="utf-8"))
            # N7: lint-report.md is retired — never generated for new projects.
            self.assertFalse((plan_dir / "lint-report.md").exists())

    def test_independent_review_stub_is_template_content(self):
        from backend.skill import SkillEngine

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = SkillEngine(Path(tmpdir) / "projects", root / "skill")
            project = engine.create_project(
                {
                    "name": "demo",
                    "workspace_dir": str(Path(tmpdir) / "workspace"),
                    "project_type": "strategy-consulting",
                    "theme": "AI strategy review",
                    "target_audience": "executive audience",
                    "deadline": "2026-04-01",
                    "expected_length": "3000 words",
                    "notes": "",
                }
            )
            project_dir = Path(project["project_dir"])
            independent_text = (project_dir / "plan" / "independent-review.md").read_text(encoding="utf-8")

            self.assertTrue(engine._is_template_content(independent_text, "independent-review.md"))
            self.assertFalse(engine._has_effective_independent_review(project_dir))

    def test_validate_plan_write_accepts_independent_review_rejects_lint_report(self):
        from backend.skill import SkillEngine

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = SkillEngine(Path(tmpdir) / "projects", root / "skill")
            project = engine.create_project(
                {
                    "name": "demo",
                    "workspace_dir": str(Path(tmpdir) / "workspace"),
                    "project_type": "strategy-consulting",
                    "theme": "AI strategy review",
                    "target_audience": "executive audience",
                    "deadline": "2026-04-01",
                    "expected_length": "3000 words",
                    "notes": "",
                }
            )

            self.assertEqual(
                engine.validate_plan_write(project["id"], "plan/independent-review.md"),
                "plan/independent-review.md",
            )
            # N7: lint-report.md is retired — no longer an official plan file, so it is rejected.
            with self.assertRaises(ValueError):
                engine.validate_plan_write(project["id"], "plan/lint-report.md")

