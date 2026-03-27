import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.chat import ChatHandler
from backend.config import Settings
from backend.skill import SkillEngine


class ChatRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.repo_skill_dir = Path(__file__).resolve().parents[1] / "skill"

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    def test_web_search_returns_searxng_results(
        self,
        mock_get,
        mock_openai,
    ):
        mock_get.return_value = mock.Mock(
            status_code=200,
            json=lambda: {
                "results": [
                    {
                        "title": "猪猪侠2024年市场观察",
                        "content": "围绕授权、票房和短视频热度的行业摘要。",
                        "url": "https://example.com/a",
                    },
                    {
                        "title": "咏声动漫公开信息",
                        "content": "公司动态与IP布局。",
                        "url": "https://example.com/b",
                    },
                ]
            },
        )
        settings = Settings(
            mode="managed",
            managed_base_url="https://newapi.z0y0h.work/client/v1",
            managed_model="gemini-3-flash",
            projects_dir=Path(tempfile.gettempdir()) / "dummy-projects",
            skill_dir=self.repo_skill_dir,
        )
        handler = ChatHandler(settings, SkillEngine(settings.projects_dir, self.repo_skill_dir))

        result = handler._web_search("猪猪侠 2024 咏声动漫")

        self.assertEqual(result["status"], "success")
        self.assertIn("猪猪侠2024年市场观察", result["results"])
        self.assertIn("咏声动漫公开信息", result["results"])
        self.assertIn("授权、票房和短视频热度", result["results"])
        mock_get.assert_called_once()

    @mock.patch("backend.chat.OpenAI")
    def test_write_file_blocks_report_draft_before_outline_confirmation(self, mock_openai):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="猪猪侠研究报告",
                target_audience="高层决策者",
                deadline="2026-04-01",
                expected_length="3000字",
            )
            settings = Settings(
                mode="managed",
                managed_base_url="https://newapi.z0y0h.work/client/v1",
                managed_model="gemini-3-flash",
                projects_dir=projects_dir,
                skill_dir=self.repo_skill_dir,
            )
            handler = ChatHandler(settings, engine)
            handler._turn_context = {"can_write_non_plan": False}

            tool_call = type(
                "ToolCall",
                (),
                {
                    "function": type(
                        "Function",
                        (),
                        {
                            "name": "write_file",
                            "arguments": '{"file_path":"report_draft_v1.md","content":"# 正文"}',
                        },
                    )(),
                },
            )()

            result = handler._execute_tool(project["id"], tool_call)

        self.assertEqual(result["status"], "error")
        self.assertIn("先确认大纲", result["message"])

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    def test_web_search_stops_retrying_after_search_backend_error(self, mock_get, mock_openai):
        mock_get.return_value = mock.Mock(
            status_code=503,
            text="service unavailable",
        )
        settings = Settings(
            mode="managed",
            managed_base_url="https://newapi.z0y0h.work/client/v1",
            managed_model="gemini-3-flash",
            projects_dir=Path(tempfile.gettempdir()) / "dummy-projects",
            skill_dir=self.repo_skill_dir,
        )
        handler = ChatHandler(settings, SkillEngine(settings.projects_dir, self.repo_skill_dir))
        handler._turn_context = {"can_write_non_plan": True}

        tool_call = type(
            "ToolCall",
            (),
            {
                "function": type(
                    "Function",
                    (),
                    {
                        "name": "web_search",
                        "arguments": '{"query":"猪猪侠 咏声动漫 2024"}',
                    },
                )(),
            },
        )()

        first_result = handler._execute_tool("demo", tool_call)
        second_result = handler._execute_tool("demo", tool_call)

        self.assertEqual(first_result["status"], "error")
        self.assertIn("搜索服务暂时不可用", first_result["message"])
        self.assertEqual(second_result["status"], "error")
        self.assertIn("本轮", second_result["message"])
        self.assertEqual(mock_get.call_count, 1)

    @mock.patch("backend.chat.OpenAI")
    def test_fetch_url_tool_is_registered(self, mock_openai):
        settings = Settings(
            mode="managed",
            managed_base_url="https://newapi.z0y0h.work/client/v1",
            managed_model="gemini-3-flash",
            projects_dir=Path(tempfile.gettempdir()) / "dummy-projects",
            skill_dir=self.repo_skill_dir,
        )
        handler = ChatHandler(settings, SkillEngine(settings.projects_dir, self.repo_skill_dir))

        tool_names = [tool["function"]["name"] for tool in handler._get_tools()]

        self.assertIn("fetch_url", tool_names)

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_reads_article_text_from_html(self, mock_getaddrinfo, mock_get, mock_openai):
        html = """
        <html>
          <head><title>示例页面</title></head>
          <body>
            <nav>导航</nav>
            <article>
              <h1>核心判断</h1>
              <p>这是网页正文。</p>
            </article>
          </body>
        </html>
        """
        response = mock.Mock()
        response.status_code = 200
        response.headers = {"Content-Type": "text/html; charset=utf-8"}
        response.iter_content = mock.Mock(return_value=[html.encode("utf-8")])
        mock_get.return_value = response
        mock_getaddrinfo.return_value = [
            (2, 1, 6, "", ("93.184.216.34", 443)),
        ]

        settings = Settings(
            mode="managed",
            managed_base_url="https://newapi.z0y0h.work/client/v1",
            managed_model="gemini-3-flash",
            projects_dir=Path(tempfile.gettempdir()) / "dummy-projects",
            skill_dir=self.repo_skill_dir,
        )
        handler = ChatHandler(settings, SkillEngine(settings.projects_dir, self.repo_skill_dir))

        tool_call = type(
            "ToolCall",
            (),
            {
                "function": type(
                    "Function",
                    (),
                    {
                        "name": "fetch_url",
                        "arguments": '{"url":"https://example.com/article"}',
                    },
                )(),
            },
        )()

        result = handler._execute_tool("demo", tool_call)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["title"], "示例页面")
        self.assertIn("核心判断", result["content"])
        self.assertIn("这是网页正文", result["content"])
        mock_get.assert_called_once()

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    def test_fetch_url_blocks_private_address(self, mock_get, mock_openai):
        settings = Settings(
            mode="managed",
            managed_base_url="https://newapi.z0y0h.work/client/v1",
            managed_model="gemini-3-flash",
            projects_dir=Path(tempfile.gettempdir()) / "dummy-projects",
            skill_dir=self.repo_skill_dir,
        )
        handler = ChatHandler(settings, SkillEngine(settings.projects_dir, self.repo_skill_dir))

        tool_call = type(
            "ToolCall",
            (),
            {
                "function": type(
                    "Function",
                    (),
                    {
                        "name": "fetch_url",
                        "arguments": '{"url":"http://127.0.0.1:8080/private"}',
                    },
                )(),
            },
        )()

        result = handler._execute_tool("demo", tool_call)

        self.assertEqual(result["status"], "error")
        self.assertIn("不允许访问", result["message"])
        mock_get.assert_not_called()
