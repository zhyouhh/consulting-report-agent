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
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            skill_dir = Path(tmpdir) / "skill"
            plan_dir = projects_dir / "demo" / "plan"
            plan_dir.mkdir(parents=True)
            skill_dir.mkdir(parents=True)

            (skill_dir / "SKILL.md").write_text("系统提示", encoding="utf-8")
            (plan_dir / "project-overview.md").write_text("概览内容", encoding="utf-8")
            (plan_dir / "progress.md").write_text("进度内容", encoding="utf-8")
            (plan_dir / "stage-gates.md").write_text("门禁内容", encoding="utf-8")
            (plan_dir / "notes.md").write_text("备注内容", encoding="utf-8")

            settings = Settings(
                mode="managed",
                managed_base_url="https://newapi.z0y0h.work/client/v1",
                managed_model="gemini-3-flash",
                projects_dir=projects_dir,
                skill_dir=skill_dir,
            )
            engine = SkillEngine(projects_dir, skill_dir)
            handler = ChatHandler(settings, engine)

            prompt = handler._build_system_prompt("demo")

        self.assertIn("系统提示", prompt)
        self.assertIn("当前项目概览", prompt)
        self.assertIn("当前项目进度", prompt)
        self.assertIn("阶段门禁", prompt)
        self.assertIn("项目备注", prompt)
        self.assertNotIn("当前项目信息", prompt)
        self.assertNotIn("当前大纲", prompt)
