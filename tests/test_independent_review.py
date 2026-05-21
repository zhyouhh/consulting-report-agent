import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from backend.chat import ChatHandler
from backend.config import Settings
from backend.independent_review import (
    CANONICAL_REVIEW_PATH,
    INDEPENDENT_REVIEW_COMPLETION_MARKER,
    IndependentReviewAgent,
)
from backend.skill import SkillEngine


class _FakeMessage:
    def __init__(self, *, content="", tool_calls=None, dumped=None, reasoning_content=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self._dumped = dumped
        if reasoning_content is not None:
            self.reasoning_content = reasoning_content

    def model_dump(self):
        if self._dumped is None:
            return {}
        return self._dumped


class IndependentReviewAgentTests(unittest.TestCase):
    def setUp(self):
        self.repo_skill_dir = Path(__file__).resolve().parents[1] / "skill"

    def _make_settings(self, projects_dir: Path, **overrides):
        payload = {
            "mode": "managed",
            "managed_base_url": "https://newapi.z0y0h.work/client/v1",
            "managed_model": "deepseek-v4-pro",
            "projects_dir": projects_dir,
            "skill_dir": self.repo_skill_dir,
        }
        payload.update(overrides)
        return Settings(**payload)

    def _make_engine_project_and_agent(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        projects_dir = Path(tmpdir.name) / "projects"
        workspace_dir = Path(tmpdir.name) / "workspace"
        engine = SkillEngine(projects_dir, self.repo_skill_dir)
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
        project_dir = Path(project["project_dir"])
        draft_path = project_dir / "content" / "report_draft_v1.md"
        draft_path.write_text("# Draft\n\n## 第一章\n\n短正文。\n", encoding="utf-8")
        settings = self._make_settings(projects_dir)
        return engine, project, project_dir, IndependentReviewAgent(skill_engine=engine, settings=settings)

    def _tool_call(self, name: str, args: dict, call_id: str = "call-1"):
        return SimpleNamespace(
            id=call_id,
            type="function",
            function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)),
        )

    def _response(self, message):
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    def test_run_emits_progress_events(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        review = (
            "# 独立审查报告\n\n"
            "## 1. 结论-证据一致性\n未发现问题\n"
            "## 2. 关键假设与逻辑链\n未发现问题\n"
            "## 3. 数据口径一致性\n未发现问题\n"
            "## 4. 建议可执行性\n未发现问题\n"
            "## 5. 目标读者匹配\n未发现问题\n\n"
            f"{INDEPENDENT_REVIEW_COMPLETION_MARKER}\n"
        )
        responses = [
            self._response(_FakeMessage(tool_calls=[self._tool_call("read_file", {"file_path": "plan/data-log.md"}, "call-1")])),
            self._response(_FakeMessage(tool_calls=[self._tool_call("read_file", {"file_path": "plan/analysis-notes.md"}, "call-2")])),
            self._response(_FakeMessage(tool_calls=[self._tool_call("read_file", {"file_path": "content/report_draft_v1.md"}, "call-3")])),
            self._response(_FakeMessage(tool_calls=[self._tool_call("write_file", {"file_path": CANONICAL_REVIEW_PATH, "content": review}, "call-4")])),
            self._response(_FakeMessage(content="完成", tool_calls=[])),
        ]

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = responses
            events = list(agent.run(project["id"], draft_word_count=100))

        event_types = [event["type"] for event in events]
        self.assertIn("progress", event_types)
        self.assertIn("tool_call", event_types)
        self.assertIn("tool_result", event_types)
        self.assertEqual(event_types[-1], "review-completed")
        self.assertEqual(events[-1]["path"], CANONICAL_REVIEW_PATH)

    def test_run_word_count_over_30k_emits_friendly_error(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            events = list(agent.run(project["id"], draft_word_count=30001))

        self.assertEqual(events[0]["type"], "error")
        self.assertIn("正文超过 30k 字", events[0]["detail"])
        mock_openai.assert_not_called()

    def test_run_rejects_write_to_non_canonical_path(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        responses = [
            self._response(_FakeMessage(tool_calls=[self._tool_call("write_file", {"file_path": "plan/data-log.md", "content": "bad"}, "call-1")])),
            self._response(_FakeMessage(content="完成", tool_calls=[])),
        ]

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = responses
            events = list(agent.run(project["id"], draft_word_count=100))

        tool_results = [event for event in events if event["type"] == "tool_result"]
        self.assertEqual(tool_results[0]["status"], "error")
        self.assertEqual(tool_results[0]["summary"], "路径不允许")

    def test_run_requires_completion_marker(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        responses = [
            self._response(_FakeMessage(tool_calls=[self._tool_call("write_file", {"file_path": CANONICAL_REVIEW_PATH, "content": "# 独立审查报告\n\n缺标记\n"}, "call-1")])),
            self._response(_FakeMessage(content="完成", tool_calls=[])),
        ]

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = responses
            events = list(agent.run(project["id"], draft_word_count=100))

        self.assertIn("审查报告缺少完成标记", events[-1]["detail"])
        self.assertNotIn("review-completed", [event["type"] for event in events])

    def test_run_max_iterations_15(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        response = self._response(
            _FakeMessage(tool_calls=[self._tool_call("read_file", {"file_path": "plan/data-log.md"}, "call-loop")])
        )

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = [response] * agent.MAX_ITERATIONS
            events = list(agent.run(project["id"], draft_word_count=100))

        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 15)
        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("超过 15 轮", events[-1]["detail"])

    def test_deepseek_compat_helpers_match_chat_helpers(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del project, project_dir
        settings = self._make_settings(Path(tempfile.gettempdir()) / "projects")
        chat_handler = ChatHandler(settings=settings, skill_engine=engine)
        test_models = [
            "deepseek-v4-pro",
            "DeepSeek-Reasoner",
            "deepseek-chat",
            "gpt-4.1",
            "gpt-4o-mini",
            "claude-sonnet-4-6",
            "managed-custom-model",
            "",
        ]

        for model in test_models:
            with self.subTest(model=model):
                self.assertEqual(
                    chat_handler._should_send_explicit_tool_choice(model),
                    agent._should_send_explicit_tool_choice(model),
                )

        tool_call = self._tool_call("read_file", {"file_path": "plan/data-log.md"})
        message = _FakeMessage(
            content=None,
            tool_calls=[tool_call],
            dumped={"reasoning_content": "hidden reasoning", "audio": None},
        )
        serialized = agent._serialize_assistant_tool_call_message(message)
        self.assertEqual(serialized["content"], "")
        self.assertEqual(serialized["reasoning_content"], "hidden reasoning")
        self.assertNotIn("audio", serialized)
        self.assertEqual(serialized["tool_calls"][0]["function"]["name"], "read_file")
