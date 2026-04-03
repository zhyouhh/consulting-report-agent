import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.chat import ChatHandler
from backend.config import Settings
from backend.skill import SkillEngine


class ChatContextTests(unittest.TestCase):
    @mock.patch("backend.chat.OpenAI")
    def test_build_system_prompt_uses_v2_project_context(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            skill_dir = Path(tmpdir) / "skill"
            workspace_dir = Path(tmpdir) / "workspace"
            skill_dir.mkdir(parents=True)
            (skill_dir / "modules").mkdir(parents=True)

            (skill_dir / "SKILL.md").write_text("system prompt", encoding="utf-8")
            (skill_dir / "modules" / "consulting-lifecycle.md").write_text(
                "lifecycle guidance",
                encoding="utf-8",
            )

            engine = SkillEngine(projects_dir, skill_dir)
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
            (plan_dir / "project-overview.md").write_text("overview content", encoding="utf-8")
            (plan_dir / "progress.md").write_text("progress content", encoding="utf-8")
            (plan_dir / "stage-gates.md").write_text("stage gates content", encoding="utf-8")
            (plan_dir / "notes.md").write_text("notes content", encoding="utf-8")
            (plan_dir / "project-info.md").write_text("legacy project info", encoding="utf-8")

            settings = Settings(
                mode="managed",
                managed_base_url="https://newapi.z0y0h.work/client/v1",
                managed_model="gemini-3-flash",
                projects_dir=projects_dir,
                skill_dir=skill_dir,
            )
            handler = ChatHandler(settings, engine)

            prompt = handler._build_system_prompt(project["id"])

        self.assertIn("system prompt", prompt)
        self.assertIn("lifecycle guidance", prompt)
        self.assertIn("overview content", prompt)
        self.assertIn("notes content", prompt)
        self.assertIn("project-overview.md 创建", prompt)
        self.assertNotIn("legacy project info", prompt)
        self.assertNotIn("褰撳墠椤圭洰淇℃伅", prompt)
        self.assertNotIn("褰撳墠澶х翰", prompt)

    @mock.patch("backend.chat.OpenAI")
    def test_build_system_prompt_rewrites_stale_stage_tracking_files_before_prompt_context(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            repo_skill_dir = Path(__file__).resolve().parents[1] / "skill"
            engine = SkillEngine(projects_dir, repo_skill_dir)
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
            (plan_dir / "tasks.md").write_text("# fake\n\n**闃舵**: S4\n- [ ] stale task\n", encoding="utf-8")
            (plan_dir / "progress.md").write_text("# fake\n\n**闃舵**: S4\n", encoding="utf-8")
            (plan_dir / "stage-gates.md").write_text("# fake\n\n**闃舵**: S4\n", encoding="utf-8")

            settings = Settings(
                mode="managed",
                managed_base_url="https://newapi.z0y0h.work/client/v1",
                managed_model="gemini-3-flash",
                projects_dir=projects_dir,
                skill_dir=repo_skill_dir,
            )
            handler = ChatHandler(settings, engine)
            handler._turn_context = {"can_write_non_plan": False, "web_search_disabled": False}

            prompt = handler._build_system_prompt(project["id"])

        self.assertNotIn("stale task", prompt)
        self.assertNotIn("**闃舵**: S4", prompt)
        self.assertIn("S0", prompt)
