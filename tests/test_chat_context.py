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
        self.assertIn("当前项目概览", prompt)
        self.assertIn("当前项目进度", prompt)
        self.assertIn("阶段门禁", prompt)
        self.assertIn("项目备注", prompt)
        self.assertNotIn("legacy project info", prompt)
        self.assertNotIn("当前项目信息", prompt)
        self.assertNotIn("当前大纲", prompt)
