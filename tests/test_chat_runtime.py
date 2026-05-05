import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

import httpx
import requests

from backend.chat import ChatHandler
from backend.config import (
    ManagedSearchLimitsConfig,
    ManagedSearchPoolConfig,
    ManagedSearchProviderConfig,
    ManagedSearchRoutingConfig,
    Settings,
)
from backend.skill import SkillEngine


class ChatRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.repo_skill_dir = Path(__file__).resolve().parents[1] / "skill"
        self._curl_cffi_patcher = mock.patch("backend.chat.curl_cffi_requests", None, create=True)
        self._curl_cffi_patcher.start()
        self.addCleanup(self._curl_cffi_patcher.stop)

    def _make_tool_call(self, name: str, arguments: str):
        return type(
            "ToolCall",
            (),
            {
                "function": type(
                    "Function",
                    (),
                    {
                        "name": name,
                        "arguments": arguments,
                    },
                )(),
            },
        )()

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

    def _write_stage_one_prerequisites(self, project_dir: Path):
        self._write_evidence_gate_prerequisites(project_dir)
        (project_dir / "plan" / "outline.md").write_text(
            "# Report outline\n\n"
            "## Executive summary\n"
            "- Summarize the AI strategy recommendation.\n"
            "## Market context\n"
            "- Explain adoption pressure and executive tradeoffs.\n"
            "## Recommendations\n"
            "- Prioritize operating model changes and governance steps.\n",
            encoding="utf-8",
        )
        (project_dir / "plan" / "research-plan.md").write_text(
            "# Research plan\n\n"
            "## Research methods\n"
            "- Interview department owners and review internal adoption metrics.\n"
            "## Data sources\n"
            "- Use CRM exports, operating reports, and external benchmark studies.\n"
            "## Execution steps\n"
            "- Collect evidence, map themes, and synthesize findings.\n",
            encoding="utf-8",
        )

    def _make_chunk(self, *, content=None, tool_calls=None):
        delta = SimpleNamespace(content=content, tool_calls=tool_calls)
        return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])

    def _make_usage_chunk(self, **usage_fields):
        return SimpleNamespace(choices=[], usage=SimpleNamespace(**usage_fields))

    def _make_stream_tool_call_chunk(self, index, *, id=None, name=None, arguments=None):
        function = None
        if name is not None or arguments is not None:
            function = SimpleNamespace(name=name, arguments=arguments)
        return SimpleNamespace(index=index, id=id, function=function)

    def _make_settings(self, **overrides):
        payload = {
            "mode": "managed",
            "managed_base_url": "https://newapi.z0y0h.work/client/v1",
            "managed_model": "gemini-3-flash",
            "projects_dir": Path(tempfile.gettempdir()) / "dummy-projects",
            "skill_dir": self.repo_skill_dir,
        }
        payload.update(overrides)
        return Settings(
            **payload,
        )

    def _make_handler_with_project(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        projects_dir = Path(tmpdir.name) / "projects"
        workspace_dir = Path(tmpdir.name) / "workspace"
        engine = SkillEngine(projects_dir, self.repo_skill_dir)
        project = engine.create_project(
            name="demo",
            workspace_dir=str(workspace_dir),
            project_type="strategy-consulting",
            theme="AI strategy review",
            target_audience="executive audience",
            deadline="2026-04-01",
            expected_length="3000 words",
        )
        handler = ChatHandler(
            self._make_settings(
                mode="managed",
                managed_model="gemini-3-flash",
                projects_dir=projects_dir,
            ),
            engine,
        )
        self.project_id = project["id"]
        self.project_dir = Path(project["project_dir"])
        return handler

    def _finalize_assistant_for_test(
        self,
        handler,
        assistant_message: str,
        *,
        history: list | None = None,
        current_user: dict | None = None,
        current_turn_messages: list | None = None,
        user_message: str = "",
    ):
        history = [] if history is None else history
        current_user = current_user or {
            "role": "user",
            "content": user_message,
            "attached_material_ids": [],
        }
        current_turn_messages = [] if current_turn_messages is None else current_turn_messages
        return handler._finalize_assistant_turn(
            self.project_id,
            history,
            current_user,
            assistant_message,
            current_turn_messages,
            user_message=user_message,
        )

    def _allow_public_fetch_host(self, mock_getaddrinfo, ip: str = "93.184.216.34"):
        mock_getaddrinfo.return_value = [
            (2, 1, 6, "", (ip, 443)),
        ]

    def _make_search_pool_config(self):
        provider = ManagedSearchProviderConfig(
            enabled=True,
            api_key="k",
            weight=1,
            minute_limit=60,
            daily_soft_limit=1200,
            cooldown_seconds=180,
        )
        return ManagedSearchPoolConfig(
            version=1,
            providers={
                "serper": provider,
                "brave": provider,
                "tavily": provider,
                "exa": provider,
            },
            routing=ManagedSearchRoutingConfig(
                primary=["serper", "brave"],
                secondary=["tavily", "exa"],
                native_fallback=True,
            ),
            limits=ManagedSearchLimitsConfig(
                per_turn_searches=2,
                project_minute_limit=10,
                global_minute_limit=20,
                memory_cache_ttl_seconds=60,
                project_cache_ttl_seconds=300,
            ),
        )

    def _make_fetch_response(
        self,
        *,
        url: str,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
        encoding: str | None = None,
        apparent_encoding: str = "utf-8",
    ):
        response = mock.Mock()
        response.url = url
        response.status_code = status_code
        response.headers = headers or {}
        response.encoding = encoding
        response.apparent_encoding = apparent_encoding
        response.iter_content = mock.Mock(return_value=[body])
        response.close = mock.Mock()
        return response

    @mock.patch("backend.chat.OpenAI")
    def test_get_active_model_name_prefers_mode_specific_field(self, mock_openai):
        managed_handler = ChatHandler(
            self._make_settings(
                mode="managed",
                managed_model="gemini-3-flash",
                model="legacy-managed-model",
            ),
            SkillEngine(Path(tempfile.gettempdir()) / "managed-projects", self.repo_skill_dir),
        )
        custom_handler = ChatHandler(
            self._make_settings(
                mode="custom",
                custom_api_base="https://custom.example/v1",
                custom_api_key="secret",
                custom_model="gpt-5-mini",
                model="legacy-custom-model",
            ),
            SkillEngine(Path(tempfile.gettempdir()) / "custom-projects", self.repo_skill_dir),
        )

        self.assertEqual(managed_handler._get_active_model_name(), "gemini-3-flash")
        self.assertEqual(custom_handler._get_active_model_name(), "gpt-5-mini")

    @mock.patch("backend.chat.OpenAI")
    def test_chat_returns_provider_real_usage_fields(self, mock_openai):
        mock_openai.return_value.chat.completions.create.return_value = SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=175000,
                completion_tokens=1200,
                total_tokens=176200,
                prompt_tokens_details=SimpleNamespace(cached_tokens=4000),
                completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
            ),
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="完成",
                        tool_calls=[],
                    )
                )
            ],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    model="legacy-model-should-not-win",
                    projects_dir=projects_dir,
                ),
                engine,
            )

            result = handler.chat(project["id"], "请继续")

        self.assertEqual(result["token_usage"]["usage_source"], "provider")
        self.assertEqual(result["token_usage"]["context_used_tokens"], 175000)
        self.assertEqual(result["token_usage"]["input_tokens"], 175000)
        self.assertEqual(result["token_usage"]["output_tokens"], 1200)
        self.assertEqual(result["token_usage"]["total_tokens"], 176200)
        self.assertEqual(result["token_usage"]["cache_read_tokens"], 4000)
        self.assertEqual(result["token_usage"]["reasoning_tokens"], 0)
        self.assertEqual(result["token_usage"]["max_tokens"], 200000)
        self.assertEqual(result["token_usage"]["effective_max_tokens"], 200000)
        self.assertEqual(result["token_usage"]["provider_max_tokens"], 1000000)
        self.assertFalse(result["token_usage"]["preflight_compaction_used"])
        self.assertEqual(result["token_usage"]["post_turn_compaction_status"], "not_needed")
        self.assertFalse(result["token_usage"]["compressed"])
        self.assertEqual(
            mock_openai.return_value.chat.completions.create.call_args.kwargs["model"],
            "gemini-3-flash",
        )

    @mock.patch("backend.chat.OpenAI")
    def test_chat_marks_usage_unavailable_without_provider_fields(self, mock_openai):
        mock_openai.return_value.chat.completions.create.return_value = SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="完成",
                        tool_calls=[],
                    )
                )
            ],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )

            result = handler.chat(project["id"], "请继续")

        self.assertEqual(result["token_usage"]["usage_source"], "unavailable")
        self.assertIsNone(result["token_usage"]["context_used_tokens"])
        self.assertIsNone(result["token_usage"]["input_tokens"])
        self.assertIsNone(result["token_usage"]["output_tokens"])
        self.assertEqual(result["token_usage"]["max_tokens"], 200000)
        self.assertEqual(result["token_usage"]["effective_max_tokens"], 200000)
        self.assertEqual(result["token_usage"]["provider_max_tokens"], 1000000)

    @mock.patch("backend.chat.OpenAI")
    def test_managed_stream_requests_use_extended_read_timeout(self, mock_openai):
        mock_openai.return_value.chat.completions.create.return_value = iter([
            self._make_chunk(content="第一段"),
        ])
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )

            events = list(handler.chat_stream(project["id"], "继续"))
        request_timeout = mock_openai.return_value.chat.completions.create.call_args.kwargs["timeout"]

        self.assertTrue(any(event["type"] == "content" for event in events))
        self.assertIsInstance(request_timeout, httpx.Timeout)
        self.assertEqual(request_timeout.connect, 15.0)
        self.assertEqual(request_timeout.read, 180.0)
        self.assertEqual(request_timeout.write, 30.0)
        self.assertEqual(request_timeout.pool, 30.0)

    @mock.patch("backend.chat.OpenAI")
    def test_chat_stream_emits_friendly_error_when_provider_read_times_out_mid_stream(self, mock_openai):
        def failing_stream():
            yield self._make_chunk(content="第一段")
            raise Exception("The read operation timed out")

        mock_openai.return_value.chat.completions.create.return_value = failing_stream()
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )

            events = list(handler.chat_stream(project["id"], "继续"))

        self.assertEqual(events[0], {"type": "content", "data": "第一段"})
        error_events = [event for event in events if event["type"] == "error"]
        self.assertEqual(len(error_events), 1)
        self.assertIn("默认通道", error_events[0]["data"])
        self.assertIn("超时", error_events[0]["data"])
        self.assertNotIn("The read operation timed out", error_events[0]["data"])

    @mock.patch("backend.chat.OpenAI")
    def test_stream_provider_error_redacts_request_body_when_stream_creation_fails(self, mock_openai):
        handler = self._make_handler_with_project()
        secret_message = "SECRET_STREAM_REPORT_TEXT"
        mock_openai.return_value.chat.completions.create.side_effect = RuntimeError(
            f"provider echoed request body: {secret_message}"
        )

        with mock.patch("backend.chat.time.sleep"):
            events = list(handler.chat_stream(self.project_id, secret_message))

        error_events = [event for event in events if event["type"] == "error"]
        self.assertEqual(len(error_events), 1)
        self.assertIn("API调用失败", error_events[0]["data"])
        self.assertIn("provider echoed request body", error_events[0]["data"])
        self.assertIn("[redacted]", error_events[0]["data"])
        self.assertNotIn(secret_message, error_events[0]["data"])

    @mock.patch("backend.chat.OpenAI")
    def test_stream_provider_error_redacts_request_body_when_stream_iteration_fails(self, mock_openai):
        handler = self._make_handler_with_project()
        secret_message = "SECRET_STREAM_REPORT_TEXT"

        def failing_stream():
            yield self._make_chunk(content="第一段")
            raise RuntimeError(f"provider echoed request body: {secret_message}")

        mock_openai.return_value.chat.completions.create.return_value = failing_stream()

        events = list(handler.chat_stream(self.project_id, secret_message))

        self.assertEqual(events[0], {"type": "content", "data": "第一段"})
        error_events = [event for event in events if event["type"] == "error"]
        self.assertEqual(len(error_events), 1)
        self.assertIn("API调用失败", error_events[0]["data"])
        self.assertIn("provider echoed request body", error_events[0]["data"])
        self.assertIn("[redacted]", error_events[0]["data"])
        self.assertNotIn(secret_message, error_events[0]["data"])

    @mock.patch("backend.chat.OpenAI")
    def test_chat_stream_emits_tool_start_as_soon_as_tool_name_arrives(self, mock_openai):
        consumed_chunks = []

        def tool_only_stream():
            consumed_chunks.append("chunk-1")
            yield self._make_chunk(
                tool_calls=[
                    self._make_stream_tool_call_chunk(
                        0,
                        id="call-1",
                        name="web_search",
                        arguments='{"query":"',
                    )
                ]
            )
            consumed_chunks.append("chunk-2")
            yield self._make_chunk(
                tool_calls=[
                    self._make_stream_tool_call_chunk(
                        0,
                        arguments='ultraman flight"}',
                    )
                ]
            )

        mock_openai.return_value.chat.completions.create.return_value = tool_only_stream()
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )

            with mock.patch.object(
                handler,
                "_execute_tool",
                return_value={"status": "success", "results": "ok"},
            ) as execute_tool:
                stream = handler.chat_stream(project["id"], "继续")
                first_event = next(stream)
                self.assertEqual(consumed_chunks, ["chunk-1"])
                remaining_events = list(stream)

        tool_events = [first_event, *[event for event in remaining_events if event["type"] == "tool"]]
        self.assertGreaterEqual(len(tool_events), 2)
        self.assertEqual(tool_events[0]["data"], "🔧 准备调用工具: web_search")
        self.assertEqual(
            sum(event["data"].startswith("🔧 调用工具: web_search(") for event in tool_events),
            1,
        )
        execute_tool.assert_called_once()
        self.assertEqual(execute_tool.call_args.args[1].function.name, "web_search")
        self.assertEqual(
            execute_tool.call_args.args[1].function.arguments,
            '{"query":"ultraman flight"}',
        )

    @mock.patch("backend.chat.OpenAI")
    def test_normalize_usage_prefers_prompt_tokens_for_context_used(self, mock_openai):
        handler = ChatHandler(
            self._make_settings(),
            SkillEngine(Path(tempfile.gettempdir()) / "normalize-projects", self.repo_skill_dir),
        )
        policy = handler._resolve_context_policy()

        normalized = handler._normalize_provider_usage(
            SimpleNamespace(prompt_tokens=180000, completion_tokens=800, total_tokens=180800),
            policy,
            preflight_compaction_used=False,
        )

        self.assertEqual(normalized["context_used_tokens"], 180000)
        self.assertEqual(normalized["usage_source"], "provider")

    @mock.patch("backend.chat.OpenAI")
    def test_normalize_usage_falls_back_to_total_tokens_without_guessing(self, mock_openai):
        handler = ChatHandler(
            self._make_settings(),
            SkillEngine(Path(tempfile.gettempdir()) / "normalize-projects", self.repo_skill_dir),
        )
        policy = handler._resolve_context_policy()

        normalized = handler._normalize_provider_usage(
            SimpleNamespace(total_tokens=140000),
            policy,
            preflight_compaction_used=False,
        )

        self.assertEqual(normalized["context_used_tokens"], 140000)
        self.assertEqual(normalized["usage_source"], "provider_partial")

    @mock.patch("backend.chat.OpenAI")
    def test_normalize_usage_accepts_input_and_output_token_shapes(self, mock_openai):
        handler = ChatHandler(
            self._make_settings(),
            SkillEngine(Path(tempfile.gettempdir()) / "normalize-projects", self.repo_skill_dir),
        )
        policy = handler._resolve_context_policy()

        normalized = handler._normalize_provider_usage(
            SimpleNamespace(input_tokens=91000, output_tokens=1200, total_tokens=92200),
            policy,
            preflight_compaction_used=False,
        )

        self.assertEqual(normalized["input_tokens"], 91000)
        self.assertEqual(normalized["output_tokens"], 1200)
        self.assertEqual(normalized["context_used_tokens"], 91000)
        self.assertEqual(normalized["usage_source"], "provider")

    @mock.patch("backend.chat.OpenAI")
    def test_chat_stream_emits_provider_real_usage_payload_when_final_usage_chunk_arrives(self, mock_openai):
        mock_openai.return_value.chat.completions.create.return_value = iter([
            self._make_chunk(content="第一段"),
            self._make_usage_chunk(prompt_tokens=175000, completion_tokens=900, total_tokens=175900),
        ])
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )

            events = list(handler.chat_stream(project["id"], "继续"))

        usage_event = next(event for event in events if event["type"] == "usage")
        self.assertEqual(usage_event["data"]["usage_source"], "provider")
        self.assertEqual(usage_event["data"]["context_used_tokens"], 175000)
        self.assertEqual(usage_event["data"]["input_tokens"], 175000)
        self.assertEqual(usage_event["data"]["output_tokens"], 900)

    @mock.patch("backend.chat.OpenAI")
    def test_load_conversation_state_returns_empty_state_when_file_is_missing(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )

            state = handler._load_conversation_state(project["id"])

        self.assertEqual(
            state,
            {
                "version": 1,
                "events": [],
                "memory_entries": [],
                "compact_state": None,
                "draft_followup_state": None,
            },
        )
        self.assertFalse((Path(project["project_dir"]) / "conversation_state.json").exists())

    @mock.patch("backend.chat.OpenAI")
    def test_load_conversation_state_migrates_legacy_compact_sidecar(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )
            project_dir = Path(project["project_dir"])
            legacy_path = project_dir / "conversation_compact_state.json"
            state_path = project_dir / "conversation_state.json"
            legacy_path.write_text(
                json.dumps(
                    {
                        "summary_text": "旧摘要",
                        "source_message_count": 2,
                        "last_compacted_at": "2026-04-13T12:00:00",
                        "trigger_usage": {"context_used_tokens": 190000},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            state = handler._load_conversation_state(
                project["id"],
                history=[
                    {"role": "user", "content": "第一条"},
                    {"role": "assistant", "content": "第二条"},
                ],
            )

            persisted = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(state["version"], 1)
        self.assertEqual(state["events"], [])
        self.assertEqual(state["memory_entries"], [])
        self.assertEqual(state["compact_state"]["summary_text"], "旧摘要")
        self.assertEqual(state["compact_state"]["source_message_count"], 2)
        self.assertEqual(state["compact_state"]["source_memory_entry_count"], 0)
        self.assertEqual(persisted["compact_state"]["summary_text"], "旧摘要")
        self.assertFalse(legacy_path.exists())

    @mock.patch("backend.chat.OpenAI")
    def test_load_conversation_state_renames_broken_legacy_compact_sidecar_and_recovers_empty_state(
        self,
        mock_openai,
    ):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )
            project_dir = Path(project["project_dir"])
            legacy_path = project_dir / "conversation_compact_state.json"
            legacy_path.write_text("{broken json", encoding="utf-8")

            state = handler._load_conversation_state(project["id"])

            broken_files = list(project_dir.glob("conversation_compact_state.json.broken-*"))
            broken_payload = broken_files[0].read_text(encoding="utf-8") if broken_files else None

        self.assertEqual(
            state,
            {
                "version": 1,
                "events": [],
                "memory_entries": [],
                "compact_state": None,
                "draft_followup_state": None,
            },
        )
        self.assertFalse(legacy_path.exists())
        self.assertEqual(len(broken_files), 1)
        self.assertEqual(broken_payload, "{broken json")

    @mock.patch("backend.chat.OpenAI")
    def test_load_conversation_state_renames_invalid_legacy_compact_sidecar_and_recovers_empty_state(
        self,
        mock_openai,
    ):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )
            project_dir = Path(project["project_dir"])
            legacy_path = project_dir / "conversation_compact_state.json"
            legacy_path.write_text(
                json.dumps({"summary_text": "", "source_message_count": "two"}, ensure_ascii=False),
                encoding="utf-8",
            )

            state = handler._load_conversation_state(project["id"])

            broken_files = list(project_dir.glob("conversation_compact_state.json.broken-*"))
            broken_payload = broken_files[0].read_text(encoding="utf-8") if broken_files else None

        self.assertEqual(
            state,
            {
                "version": 1,
                "events": [],
                "memory_entries": [],
                "compact_state": None,
                "draft_followup_state": None,
            },
        )
        self.assertFalse(legacy_path.exists())
        self.assertEqual(len(broken_files), 1)
        self.assertEqual(
            broken_payload,
            json.dumps({"summary_text": "", "source_message_count": "two"}, ensure_ascii=False),
        )

    @mock.patch("backend.chat.OpenAI")
    def test_load_conversation_state_discards_drifted_compact_state(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )
            project_dir = Path(project["project_dir"])
            state_path = project_dir / "conversation_state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "events": [{"type": "note", "content": "保留我"}],
                        "memory_entries": [{"id": "memory-1", "content": "保留记忆"}],
                        "compact_state": {
                            "summary_text": "过期摘要",
                            "source_message_count": 3,
                            "source_memory_entry_count": 2,
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            state = handler._load_conversation_state(
                project["id"],
                history=[
                    {"role": "user", "content": "第一条"},
                    {"role": "assistant", "content": "第二条"},
                ],
            )

            persisted = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(state["events"], [{"type": "note", "content": "保留我"}])
        self.assertEqual(state["memory_entries"], [{"id": "memory-1", "content": "保留记忆"}])
        self.assertIsNone(state["compact_state"])
        self.assertIsNone(persisted["compact_state"])

    @mock.patch("backend.chat.OpenAI")
    def test_load_conversation_state_renames_broken_json_and_recovers_empty_state(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )
            project_dir = Path(project["project_dir"])
            state_path = project_dir / "conversation_state.json"
            state_path.write_text("{broken json", encoding="utf-8")

            state = handler._load_conversation_state(project["id"])

            broken_files = list(project_dir.glob("conversation_state.json.broken-*"))
            broken_payload = broken_files[0].read_text(encoding="utf-8") if broken_files else None

        self.assertEqual(
            state,
            {
                "version": 1,
                "events": [],
                "memory_entries": [],
                "compact_state": None,
                "draft_followup_state": None,
            },
        )
        self.assertFalse(state_path.exists())
        self.assertEqual(len(broken_files), 1)
        self.assertEqual(broken_payload, "{broken json")

    @mock.patch("backend.chat.OpenAI")
    def test_save_compact_state_atomically_preserves_existing_events_and_memory_entries(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )
            project_dir = Path(project["project_dir"])
            state_path = project_dir / "conversation_state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "events": [{"type": "note", "content": "保留事件"}],
                        "memory_entries": [{"id": "memory-1", "content": "保留记忆"}],
                        "compact_state": None,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            handler._save_compact_state_atomically(
                project["id"],
                {
                    "summary_text": "新摘要",
                    "source_message_count": 2,
                },
            )

            persisted = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(persisted["events"], [{"type": "note", "content": "保留事件"}])
        self.assertEqual(persisted["memory_entries"], [{"id": "memory-1", "content": "保留记忆"}])
        self.assertEqual(persisted["compact_state"]["summary_text"], "新摘要")
        self.assertEqual(persisted["compact_state"]["source_memory_entry_count"], 0)

    @mock.patch("backend.chat.OpenAI")
    def test_load_conversation_state_rewrites_under_state_lock_for_legacy_migrate_and_drift_cleanup(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )
            project_dir = Path(project["project_dir"])
            state_path = project_dir / "conversation_state.json"
            legacy_path = project_dir / "conversation_compact_state.json"
            lock = handler._get_conversation_state_lock(project["id"])

            migrate_result = {}
            migrate_done = threading.Event()
            lock.acquire()
            try:
                legacy_path.write_text(
                    json.dumps({"summary_text": "旧摘要", "source_message_count": 2}, ensure_ascii=False),
                    encoding="utf-8",
                )

                def load_migrate():
                    migrate_result["state"] = handler._load_conversation_state(
                        project["id"],
                        history=[
                            {"role": "user", "content": "第一条"},
                            {"role": "assistant", "content": "第二条"},
                        ],
                    )
                    migrate_done.set()

                migrate_thread = threading.Thread(target=load_migrate)
                migrate_thread.start()
                self.assertFalse(migrate_done.wait(0.2))
                self.assertFalse(state_path.exists())
            finally:
                lock.release()

            migrate_thread.join(timeout=2)
            self.assertFalse(migrate_thread.is_alive())
            migrated = json.loads(state_path.read_text(encoding="utf-8"))

            drift_done = threading.Event()
            lock.acquire()
            try:
                state_path.write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "events": [{"type": "note", "content": "保留我"}],
                            "memory_entries": [{"id": "memory-1", "content": "保留记忆"}],
                            "compact_state": {
                                "summary_text": "过期摘要",
                                "source_message_count": 3,
                                "source_memory_entry_count": 2,
                            },
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                def load_drift():
                    migrate_result["drift_state"] = handler._load_conversation_state(
                        project["id"],
                        history=[
                            {"role": "user", "content": "第一条"},
                            {"role": "assistant", "content": "第二条"},
                        ],
                    )
                    drift_done.set()

                drift_thread = threading.Thread(target=load_drift)
                drift_thread.start()
                self.assertFalse(drift_done.wait(0.2))
                persisted_while_locked = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertIsNotNone(persisted_while_locked["compact_state"])
            finally:
                lock.release()

            drift_thread.join(timeout=2)
            self.assertFalse(drift_thread.is_alive())
            drifted = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(migrate_result["state"]["compact_state"]["summary_text"], "旧摘要")
        self.assertEqual(migrated["compact_state"]["summary_text"], "旧摘要")
        self.assertFalse(legacy_path.exists())
        self.assertIsNone(migrate_result["drift_state"]["compact_state"])
        self.assertEqual(drifted["events"], [{"type": "note", "content": "保留我"}])
        self.assertEqual(drifted["memory_entries"], [{"id": "memory-1", "content": "保留记忆"}])
        self.assertIsNone(drifted["compact_state"])

    @mock.patch("backend.chat.OpenAI")
    def test_chat_auto_compact_persists_sidecar_and_skips_compacted_history_next_turn(self, mock_openai):
        mock_openai.return_value.chat.completions.create.side_effect = [
            SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=195000, completion_tokens=500, total_tokens=195500),
                choices=[SimpleNamespace(message=SimpleNamespace(content="第一轮完成", tool_calls=[]))],
            ),
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="紧凑摘要", tool_calls=[]))],
            ),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )

            result = handler.chat(project["id"], "请继续")

            state_path = Path(project["project_dir"]) / "conversation_state.json"
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            next_conversation = handler._build_provider_conversation(
                project["id"],
                handler._load_conversation(project["id"]),
                {
                    "role": "user",
                    "content": "第二轮继续",
                    "attached_material_ids": [],
                    "transient_attachments": [],
                },
            )

        self.assertEqual(result["token_usage"]["post_turn_compaction_status"], "completed")
        self.assertEqual(payload["compact_state"]["source_message_count"], 2)
        self.assertEqual(payload["compact_state"]["source_memory_entry_count"], 0)
        self.assertIn("紧凑摘要", payload["compact_state"]["summary_text"])
        serialized = json.dumps(next_conversation, ensure_ascii=False)
        self.assertIn("紧凑摘要", serialized)
        self.assertNotIn("第一轮完成", serialized)
        self.assertNotIn("请继续", serialized)

    @mock.patch("backend.chat.OpenAI")
    def test_chat_auto_compact_covers_visible_messages_and_memory_entries(self, mock_openai):
        mock_openai.return_value.chat.completions.create.side_effect = [
            SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=195000, completion_tokens=500, total_tokens=195500),
                choices=[SimpleNamespace(message=SimpleNamespace(content="第一轮完成", tool_calls=[]))],
            ),
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="记忆和历史摘要", tool_calls=[]))],
            ),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )
            project_dir = Path(project["project_dir"])
            (project_dir / "conversation_state.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "events": [],
                        "memory_entries": [
                            {"category": "workspace", "source_key": "file:plan/a.md", "content": "已读文件 A"},
                            {"category": "evidence", "source_key": "url:https://example.com/b", "content": "访谈要点 B"},
                        ],
                        "compact_state": None,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = handler.chat(project["id"], "请继续")

            state_path = project_dir / "conversation_state.json"
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            next_conversation = handler._build_provider_conversation(
                project["id"],
                handler._load_conversation(project["id"]),
                {
                    "role": "user",
                    "content": "第二轮继续",
                    "attached_material_ids": [],
                    "transient_attachments": [],
                },
            )

        summary_prompt = mock_openai.return_value.chat.completions.create.call_args_list[1].kwargs["messages"][1]["content"]

        self.assertEqual(result["token_usage"]["post_turn_compaction_status"], "completed")
        self.assertEqual(payload["compact_state"]["source_message_count"], 2)
        self.assertEqual(payload["compact_state"]["source_memory_entry_count"], 0)
        self.assertIn("已读文件 A", summary_prompt)
        self.assertIn("访谈要点 B", summary_prompt)
        self.assertIn("请继续", summary_prompt)
        self.assertIn("第一轮完成", summary_prompt)
        self.assertEqual(payload["memory_entries"], [])
        serialized = json.dumps(next_conversation, ensure_ascii=False)
        self.assertIn("记忆和历史摘要", serialized)
        self.assertNotIn("[工作记忆]", serialized)
        self.assertNotIn("已读文件 A", serialized)
        self.assertNotIn("访谈要点 B", serialized)

    @mock.patch("backend.chat.OpenAI")
    def test_finalize_post_turn_compaction_drops_covered_memory_entries_and_slims_old_events(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )
            project_dir = Path(project["project_dir"])
            initial_state = {
                "version": 1,
                "events": [
                    {
                        "id": "event-1",
                        "type": "tool_result",
                        "tool_name": "fetch_url",
                        "source_key": "url:https://example.com/a",
                        "source_ref": "https://example.com/a",
                        "title": "示例 A",
                        "recorded_at": "2026-04-14T10:00:00",
                        "content": "冗余正文",
                        "result": {"status": "success", "content": "过长结果"},
                    },
                    {
                        "type": "tool_result",
                        "tool_name": "read_file",
                        "source_key": "file:plan/outline.md",
                        "source_ref": "plan/outline.md",
                        "recorded_at": "2026-04-14T10:01:00",
                        "payload": {"content": "# 旧大纲"},
                    },
                ],
                "memory_entries": [
                    {"category": "workspace", "source_key": "file:plan/a.md", "content": "旧记忆 A"},
                    {"category": "evidence", "source_key": "url:https://example.com/b", "content": "旧记忆 B"},
                ],
                "compact_state": None,
            }
            history = [
                {"role": "user", "content": "请继续"},
                {"role": "assistant", "content": "第一轮完成"},
            ]
            (project_dir / "conversation_state.json").write_text(
                json.dumps(initial_state, ensure_ascii=False),
                encoding="utf-8",
            )

            with mock.patch.object(handler, "_summarize_messages", return_value="压缩摘要"):
                token_usage = handler._finalize_post_turn_compaction(
                    project["id"],
                    history,
                    {
                        "usage_source": "provider",
                        "context_used_tokens": 195000,
                        "effective_max_tokens": 200000,
                        "input_tokens": 195000,
                        "output_tokens": 500,
                        "total_tokens": 195500,
                    },
                )

            persisted = json.loads((project_dir / "conversation_state.json").read_text(encoding="utf-8"))
            next_conversation = handler._build_provider_conversation(
                project["id"],
                history,
                {
                    "role": "user",
                    "content": "下一轮",
                    "attached_material_ids": [],
                    "transient_attachments": [],
                },
            )

        self.assertEqual(token_usage["post_turn_compaction_status"], "completed")
        self.assertEqual(persisted["memory_entries"], [])
        self.assertEqual(persisted["compact_state"]["summary_text"], "压缩摘要")
        self.assertEqual(persisted["compact_state"]["source_message_count"], 2)
        self.assertEqual(persisted["compact_state"]["source_memory_entry_count"], 0)
        self.assertEqual(len(persisted["events"]), 2)
        self.assertEqual(persisted["events"][0]["id"], "event-1")
        self.assertEqual(persisted["events"][0]["tool_name"], "fetch_url")
        self.assertEqual(persisted["events"][0]["source_key"], "url:https://example.com/a")
        self.assertEqual(persisted["events"][0]["source_ref"], "https://example.com/a")
        self.assertEqual(persisted["events"][0]["title"], "示例 A")
        self.assertNotIn("content", persisted["events"][0])
        self.assertNotIn("result", persisted["events"][0])
        self.assertEqual(persisted["events"][1]["recorded_at"], "2026-04-14T10:01:00")
        self.assertEqual(persisted["events"][1]["tool_name"], "read_file")
        self.assertEqual(persisted["events"][1]["source_key"], "file:plan/outline.md")
        self.assertEqual(persisted["events"][1]["source_ref"], "plan/outline.md")
        self.assertNotIn("payload", persisted["events"][1])
        serialized = json.dumps(next_conversation, ensure_ascii=False)
        self.assertIn("压缩摘要", serialized)
        self.assertNotIn("旧记忆 A", serialized)
        self.assertNotIn("旧记忆 B", serialized)

    @mock.patch("backend.chat.OpenAI")
    def test_finalize_post_turn_compaction_trims_old_excerpts_when_sidecar_is_still_too_large(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )
            project_dir = Path(project["project_dir"])
            huge_excerpt = "E" * 50000
            initial_state = {
                "version": 1,
                "events": [
                    {
                        "type": "tool_result",
                        "tool_name": "fetch_url",
                        "source_key": "url:https://example.com/a",
                        "source_ref": "https://example.com/a",
                        "title": "示例 A",
                        "recorded_at": "2026-04-14T10:00:00",
                        "excerpt": huge_excerpt,
                        "content": "冗余正文",
                    }
                ],
                "memory_entries": [
                    {"category": "evidence", "source_key": "url:https://example.com/a", "content": "旧记忆 A"},
                ],
                "compact_state": None,
            }
            history = [
                {"role": "user", "content": "请继续"},
                {"role": "assistant", "content": "第一轮完成"},
            ]
            (project_dir / "conversation_state.json").write_text(
                json.dumps(initial_state, ensure_ascii=False),
                encoding="utf-8",
            )
            before_size = len((project_dir / "conversation_state.json").read_text(encoding="utf-8"))

            with mock.patch.object(handler, "_summarize_messages", return_value="压缩摘要"):
                token_usage = handler._finalize_post_turn_compaction(
                    project["id"],
                    history,
                    {
                        "usage_source": "provider",
                        "context_used_tokens": 195000,
                        "effective_max_tokens": 200000,
                        "input_tokens": 195000,
                        "output_tokens": 500,
                        "total_tokens": 195500,
                    },
                )

            persisted = json.loads((project_dir / "conversation_state.json").read_text(encoding="utf-8"))
            after_size = len((project_dir / "conversation_state.json").read_text(encoding="utf-8"))

        self.assertEqual(token_usage["post_turn_compaction_status"], "completed")
        self.assertEqual(persisted["memory_entries"], [])
        self.assertEqual(persisted["compact_state"]["source_memory_entry_count"], 0)
        self.assertEqual(len(persisted["events"]), 1)
        self.assertEqual(persisted["events"][0]["tool_name"], "fetch_url")
        self.assertEqual(persisted["events"][0]["source_key"], "url:https://example.com/a")
        self.assertEqual(persisted["events"][0]["source_ref"], "https://example.com/a")
        self.assertNotIn("content", persisted["events"][0])
        self.assertNotIn("excerpt", persisted["events"][0])
        self.assertLess(after_size, before_size)

    @mock.patch("backend.chat.OpenAI")
    def test_chat_discards_compact_sidecar_when_history_becomes_shorter_than_source_count(self, mock_openai):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )
            project_dir = Path(project["project_dir"])
            state_path = project_dir / "conversation_state.json"
            conversation_path = project_dir / "conversation.json"
            state_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "events": [],
                        "memory_entries": [],
                        "compact_state": {
                            "summary_text": "旧摘要",
                            "source_message_count": 8,
                            "source_memory_entry_count": 0,
                            "last_compacted_at": "2026-04-13T12:00:00",
                            "trigger_usage": {"context_used_tokens": 190000},
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            conversation_path.write_text(
                json.dumps([{"role": "user", "content": "只剩一条"}], ensure_ascii=False),
                encoding="utf-8",
            )

            provider_conversation = handler._build_provider_conversation(
                project["id"],
                handler._load_conversation(project["id"]),
                {
                    "role": "user",
                    "content": "下一轮",
                    "attached_material_ids": [],
                    "transient_attachments": [],
                },
            )

            payload = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertIsNone(payload["compact_state"])
        self.assertNotIn("旧摘要", json.dumps(provider_conversation, ensure_ascii=False))

    @mock.patch("backend.chat.OpenAI")
    def test_build_provider_conversation_orders_compact_memory_visible_history_and_current_turn(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )
            project_dir = Path(project["project_dir"])
            (project_dir / "conversation.json").write_text(
                json.dumps(
                    [
                        {"role": "user", "content": "已压缩问题"},
                        {"role": "assistant", "content": "已压缩回答"},
                        {"role": "user", "content": "最近问题"},
                        {"role": "assistant", "content": "最近回答"},
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (project_dir / "conversation_state.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "events": [],
                        "memory_entries": [
                            {"category": "workspace", "source_key": "file:old.md", "content": "已进摘要的记忆"},
                            {"category": "workspace", "source_key": "file:recent.md", "content": "保留的记忆 A"},
                            {"category": "evidence", "source_key": "url:https://example.com", "content": "保留的记忆 B"},
                        ],
                        "compact_state": {
                            "summary_text": "压缩摘要",
                            "source_message_count": 2,
                            "source_memory_entry_count": 1,
                            "last_compacted_at": "2026-04-13T12:00:00",
                            "trigger_usage": {"context_used_tokens": 190000},
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            provider_conversation = handler._build_provider_conversation(
                project["id"],
                handler._load_conversation(project["id"]),
                {
                    "role": "user",
                    "content": "当前追问",
                    "attached_material_ids": [],
                    "transient_attachments": [],
                },
            )

        self.assertEqual(provider_conversation[0]["role"], "system")
        self.assertEqual(provider_conversation[1], {"role": "assistant", "content": "[对话摘要]\n压缩摘要"})
        self.assertEqual(provider_conversation[2]["role"], "assistant")
        memory_items = handler._split_memory_block_items(provider_conversation[2])
        self.assertEqual(memory_items, ["保留的记忆 A", "保留的记忆 B"])
        self.assertEqual(provider_conversation[3], {"role": "user", "content": "最近问题"})
        self.assertEqual(provider_conversation[4], {"role": "assistant", "content": "最近回答"})
        self.assertEqual(provider_conversation[5], {"role": "user", "content": "当前追问"})

    @mock.patch("backend.chat.OpenAI")
    def test_build_provider_conversation_keeps_sidecar_memory_out_of_recent_messages(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )
            project_dir = Path(project["project_dir"])
            (project_dir / "conversation_state.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "events": [],
                        "memory_entries": [
                            {"category": "workspace", "source_key": "file:a.md", "content": "只在记忆里 A"},
                            {"category": "workspace", "source_key": "file:b.md", "content": "只在记忆里 B"},
                        ],
                        "compact_state": None,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            provider_conversation = handler._build_provider_conversation(
                project["id"],
                [],
                {
                    "role": "user",
                    "content": "当前追问",
                    "attached_material_ids": [],
                    "transient_attachments": [],
                },
            )

        self.assertEqual(provider_conversation[1]["role"], "assistant")
        self.assertEqual(
            handler._split_memory_block_items(provider_conversation[1]),
            ["只在记忆里 A", "只在记忆里 B"],
        )
        self.assertEqual(provider_conversation[2], {"role": "user", "content": "当前追问"})
        self.assertNotIn("只在记忆里 A", json.dumps(provider_conversation[2:], ensure_ascii=False))

    @mock.patch("backend.chat.OpenAI")
    def test_build_provider_conversation_keeps_updated_covered_memory_visible_on_next_turn(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )
            project_dir = Path(project["project_dir"])
            outline_path = project_dir / "plan" / "outline.md"
            outline_path.parent.mkdir(parents=True, exist_ok=True)
            outline_path.write_text("# 更新后的大纲", encoding="utf-8")
            (project_dir / "conversation_state.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "events": [],
                        "memory_entries": [
                            {"category": "workspace", "source_key": "file:plan/outline.md", "content": "# 旧大纲"},
                            {"category": "workspace", "source_key": "file:plan/notes.md", "content": "保留的记忆 B"},
                        ],
                        "compact_state": {
                            "summary_text": "压缩摘要",
                            "source_message_count": 0,
                            "source_memory_entry_count": 1,
                            "last_compacted_at": "2026-04-13T12:00:00",
                            "trigger_usage": {"context_used_tokens": 190000},
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = handler._execute_tool(
                project["id"],
                self._make_tool_call(
                    "read_file",
                    json.dumps({"file_path": "plan/outline.md"}, ensure_ascii=False),
                ),
            )
            persisted = json.loads((project_dir / "conversation_state.json").read_text(encoding="utf-8"))
            provider_conversation = handler._build_provider_conversation(
                project["id"],
                [],
                {
                    "role": "user",
                    "content": "当前追问",
                    "attached_material_ids": [],
                    "transient_attachments": [],
                },
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            [entry["content"] for entry in persisted["memory_entries"]],
            ["# 旧大纲", "保留的记忆 B", "# 更新后的大纲"],
        )
        self.assertEqual(provider_conversation[1], {"role": "assistant", "content": "[对话摘要]\n压缩摘要"})
        self.assertEqual(
            handler._split_memory_block_items(provider_conversation[2]),
            ["保留的记忆 B", "来源: plan/outline.md\n# 更新后的大纲"],
        )

    @mock.patch("backend.chat.OpenAI")
    def test_chat_marks_post_turn_compaction_completed_when_threshold_is_hit(self, mock_openai):
        mock_openai.return_value.chat.completions.create.side_effect = [
            SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=195000, completion_tokens=500, total_tokens=195500),
                choices=[SimpleNamespace(message=SimpleNamespace(content="第一轮完成", tool_calls=[]))],
            ),
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="紧凑摘要", tool_calls=[]))],
            ),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )

            result = handler.chat(project["id"], "请继续")

        self.assertEqual(result["token_usage"]["post_turn_compaction_status"], "completed")

    @mock.patch("backend.chat.OpenAI")
    def test_chat_marks_post_turn_compaction_skipped_when_usage_is_unavailable(self, mock_openai):
        mock_openai.return_value.chat.completions.create.return_value = SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(message=SimpleNamespace(content="完成", tool_calls=[]))],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )

            result = handler.chat(project["id"], "请继续")

        self.assertEqual(result["token_usage"]["post_turn_compaction_status"], "skipped_unavailable")

    @mock.patch("backend.chat.OpenAI")
    def test_chat_marks_post_turn_compaction_failed_when_sidecar_write_raises(self, mock_openai):
        mock_openai.return_value.chat.completions.create.side_effect = [
            SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=195000, completion_tokens=500, total_tokens=195500),
                choices=[SimpleNamespace(message=SimpleNamespace(content="第一轮完成", tool_calls=[]))],
            ),
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="紧凑摘要", tool_calls=[]))],
            ),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )

            with mock.patch.object(handler, "_save_compact_state_atomically", side_effect=OSError("disk full")):
                result = handler.chat(project["id"], "请继续")

        self.assertEqual(result["content"], "第一轮完成")
        self.assertEqual(result["token_usage"]["post_turn_compaction_status"], "failed")

    @mock.patch("backend.chat.OpenAI")
    def test_chat_stream_reports_failed_compaction_when_sidecar_write_raises(self, mock_openai):
        mock_openai.return_value.chat.completions.create.return_value = iter([
            self._make_chunk(content="第一段"),
            self._make_usage_chunk(prompt_tokens=195000, completion_tokens=500, total_tokens=195500),
        ])
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )

            with mock.patch.object(handler, "_summarize_messages", return_value="紧凑摘要"):
                with mock.patch.object(handler, "_save_compact_state_atomically", side_effect=OSError("disk full")):
                    events = list(handler.chat_stream(project["id"], "继续"))

        self.assertEqual(events[0], {"type": "content", "data": "第一段"})
        self.assertFalse(any(event["type"] == "error" for event in events))
        usage_event = next(event for event in events if event["type"] == "usage")
        self.assertEqual(usage_event["data"]["post_turn_compaction_status"], "failed")

    @mock.patch("backend.chat.OpenAI")
    def test_chat_stream_skips_same_request_memory_block_but_keeps_it_for_next_request(self, mock_openai):
        def tool_only_stream():
            yield self._make_chunk(
                tool_calls=[
                    self._make_stream_tool_call_chunk(
                        0,
                        id="call-1",
                        name="read_file",
                        arguments='{"file_path":"plan/outline.md"}',
                    )
                ]
            )

        def final_stream():
            yield self._make_chunk(content="已经继续处理")
            yield self._make_usage_chunk(prompt_tokens=1200, completion_tokens=100, total_tokens=1300)

        mock_openai.return_value.chat.completions.create.side_effect = [
            tool_only_stream(),
            final_stream(),
            final_stream(),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            project_dir = Path(project["project_dir"])
            outline_path = project_dir / "plan" / "outline.md"
            outline_path.parent.mkdir(parents=True, exist_ok=True)
            outline_path.write_text("# 大纲", encoding="utf-8")
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )

            first_events = list(handler.chat_stream(project["id"], "继续", max_iterations=2))
            second_events = list(handler.chat_stream(project["id"], "下一轮继续", max_iterations=1))

            persisted = json.loads((project_dir / "conversation_state.json").read_text(encoding="utf-8"))

        self.assertTrue(any(event["type"] == "content" and event["data"] == "已经继续处理" for event in first_events))
        self.assertTrue(any(event["type"] == "content" and event["data"] == "已经继续处理" for event in second_events))
        self.assertEqual(len(persisted["memory_entries"]), 1)
        self.assertEqual(persisted["memory_entries"][0]["source_key"], "file:plan/outline.md")
        self.assertEqual(persisted["memory_entries"][0]["content"], "# 大纲")
        self.assertEqual(persisted["memory_entries"][0]["source_ref"], "plan/outline.md")
        second_request_messages = mock_openai.return_value.chat.completions.create.call_args_list[1].kwargs["messages"]
        self.assertFalse(any(handler._is_memory_block_message(message) for message in second_request_messages))
        next_request_messages = mock_openai.return_value.chat.completions.create.call_args_list[2].kwargs["messages"]
        memory_message = next(
            (message for message in next_request_messages if handler._is_memory_block_message(message)),
            None,
        )
        self.assertIsNotNone(memory_message)
        self.assertEqual(handler._split_memory_block_items(memory_message), ["来源: plan/outline.md\n# 大纲"])

    @mock.patch("backend.chat.OpenAI")
    def test_chat_stream_retry_keeps_include_usage_after_transient_error(self, mock_openai):
        mock_openai.return_value.chat.completions.create.side_effect = [
            Exception("temporary network hiccup"),
            iter([
                self._make_chunk(content="第一段"),
                self._make_usage_chunk(prompt_tokens=175000, completion_tokens=900, total_tokens=175900),
            ]),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )

            events = list(handler.chat_stream(project["id"], "继续"))

        self.assertTrue(any(event["type"] == "usage" for event in events))
        self.assertEqual(
            mock_openai.return_value.chat.completions.create.call_args_list[0].kwargs.get("stream_options"),
            {"include_usage": True},
        )
        self.assertEqual(
            mock_openai.return_value.chat.completions.create.call_args_list[1].kwargs.get("stream_options"),
            {"include_usage": True},
        )

    @mock.patch("backend.chat.OpenAI")
    def test_post_turn_compaction_summarizes_provider_history_with_material_metadata(self, mock_openai):
        mock_openai.return_value.chat.completions.create.side_effect = [
            SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=195000, completion_tokens=500, total_tokens=195500),
                choices=[SimpleNamespace(message=SimpleNamespace(content="第一轮完成", tool_calls=[]))],
            ),
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="紧凑摘要", tool_calls=[]))],
            ),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            workspace_dir.mkdir(parents=True, exist_ok=True)
            material_path = workspace_dir / "访谈纪要.txt"
            material_path.write_text("这里是访谈纪要正文", encoding="utf-8")
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            material = engine.add_materials(project["id"], [str(material_path)], added_via="workspace_select")[0]
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )

            handler.chat(project["id"], "请结合材料继续", [material["id"]])

        summary_prompt = mock_openai.return_value.chat.completions.create.call_args_list[1].kwargs["messages"][1]["content"]
        self.assertIn("[本轮附带材料]", summary_prompt)
        self.assertIn(material["display_name"], summary_prompt)

    @mock.patch("backend.chat.OpenAI")
    def test_chat_stream_waits_for_complete_tool_name_before_emitting_start_event(self, mock_openai):
        consumed_chunks = []

        def fragmented_tool_name_stream():
            consumed_chunks.append("chunk-1")
            yield self._make_chunk(
                tool_calls=[
                    self._make_stream_tool_call_chunk(
                        0,
                        id="call-1",
                        name="web_",
                    )
                ]
            )
            consumed_chunks.append("chunk-2")
            yield self._make_chunk(
                tool_calls=[
                    self._make_stream_tool_call_chunk(
                        0,
                        name="search",
                        arguments='{"query":"',
                    )
                ]
            )
            consumed_chunks.append("chunk-3")
            yield self._make_chunk(
                tool_calls=[
                    self._make_stream_tool_call_chunk(
                        0,
                        arguments='ultraman flight"}',
                    )
                ]
            )

        mock_openai.return_value.chat.completions.create.return_value = fragmented_tool_name_stream()
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )

            with mock.patch.object(
                handler,
                "_execute_tool",
                return_value={"status": "success", "results": "ok"},
            ) as execute_tool:
                stream = handler.chat_stream(project["id"], "继续")
                first_event = next(stream)
                self.assertEqual(consumed_chunks, ["chunk-1", "chunk-2"])
                remaining_events = list(stream)

        tool_events = [first_event, *[event for event in remaining_events if event["type"] == "tool"]]
        self.assertEqual(tool_events[0]["data"], "🔧 准备调用工具: web_search")
        execute_tool.assert_called_once()
        self.assertEqual(execute_tool.call_args.args[1].function.name, "web_search")
        self.assertEqual(
            execute_tool.call_args.args[1].function.arguments,
            '{"query":"ultraman flight"}',
        )

    @mock.patch("backend.chat.OpenAI")
    def test_image_token_estimate_does_not_scale_with_base64_length(self, mock_openai):
        handler = ChatHandler(
            self._make_settings(),
            SkillEngine(Path(tempfile.gettempdir()) / "image-projects", self.repo_skill_dir),
        )
        small_image_message = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "看图"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                ],
            }
        ]
        large_image_message = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "看图"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{('A' * 200000)}"}},
                ],
            }
        ]

        small_estimate = handler._estimate_tokens(small_image_message)
        large_estimate = handler._estimate_tokens(large_image_message)

        self.assertEqual(small_estimate, large_estimate)

    @mock.patch("backend.chat.OpenAI")
    def test_to_provider_message_includes_transient_images(self, mock_openai):
        handler = ChatHandler(
            self._make_settings(),
            SkillEngine(Path(tempfile.gettempdir()) / "transient-image-projects", self.repo_skill_dir),
        )
        provider_message = handler._to_provider_message(
            "demo",
            {
                "role": "user",
                "content": "请看这张截图",
                "attached_material_ids": [],
                "transient_attachments": [
                    {
                        "name": "bug.png",
                        "mime_type": "image/png",
                        "data_url": "data:image/png;base64,AAAA",
                    }
                ],
            },
            include_images=True,
        )

        self.assertEqual(provider_message["role"], "user")
        self.assertEqual(provider_message["content"][0]["type"], "text")
        self.assertEqual(provider_message["content"][1]["type"], "image_url")
        self.assertEqual(
            provider_message["content"][1]["image_url"]["url"],
            "data:image/png;base64,AAAA",
        )

    @mock.patch("backend.chat.OpenAI")
    def test_build_persisted_user_message_omits_transient_attachments(self, mock_openai):
        handler = ChatHandler(
            self._make_settings(),
            SkillEngine(Path(tempfile.gettempdir()) / "persisted-message-projects", self.repo_skill_dir),
        )

        persisted = handler._build_persisted_user_message(
            user_message="请看这张截图",
            attached_material_ids=["mat-1"],
        )

        self.assertEqual(
            persisted,
            {
                "role": "user",
                "content": "请看这张截图",
                "attached_material_ids": ["mat-1"],
            },
        )

    @mock.patch("backend.chat.OpenAI")
    def test_estimate_tokens_counts_assistant_tool_call_arguments(self, mock_openai):
        handler = ChatHandler(
            self._make_settings(),
            SkillEngine(Path(tempfile.gettempdir()) / "tool-call-token-projects", self.repo_skill_dir),
        )
        long_arguments = json.dumps(
            {
                "file_path": "plan/outline.md",
                "content": "段落" * 400,
            },
            ensure_ascii=False,
        )
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": long_arguments,
                        },
                    }
                ],
            }
        ]

        estimate = handler._estimate_tokens(messages)

        self.assertGreaterEqual(estimate, handler._estimate_text_tokens(long_arguments))

    @mock.patch("backend.chat.OpenAI")
    def test_compress_conversation_drops_orphan_tool_messages(self, mock_openai):
        mock_openai.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="压缩摘要",
                    )
                )
            ],
        )
        handler = ChatHandler(
            self._make_settings(keep_recent_messages=2),
            SkillEngine(Path(tempfile.gettempdir()) / "compress-projects", self.repo_skill_dir),
        )
        conversation = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "旧问题"},
            {"role": "assistant", "content": "旧回答"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tool-1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"file_path":"plan/outline.md"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "tool-1", "content": '{"status":"success"}'},
            {"role": "user", "content": "继续"},
        ]

        compressed = handler._compress_conversation(conversation)

        self.assertEqual(compressed[0]["role"], "system")
        self.assertEqual(compressed[1]["role"], "assistant")
        self.assertEqual(compressed[-1], {"role": "user", "content": "继续"})
        tool_messages = [message for message in compressed if message.get("role") == "tool"]
        self.assertEqual(len(tool_messages), 1)
        tool_index = compressed.index(tool_messages[0])
        paired_assistant = compressed[tool_index - 1]
        self.assertEqual(paired_assistant.get("role"), "assistant")
        self.assertEqual(paired_assistant.get("tool_calls", [])[0]["id"], tool_messages[0]["tool_call_id"])

    @mock.patch("backend.chat.OpenAI")
    def test_fit_budget_hard_stops_when_current_turn_itself_cannot_fit(self, mock_openai):
        handler = ChatHandler(
            self._make_settings(
                mode="custom",
                custom_api_base="https://custom.example/v1",
                custom_api_key="secret",
                custom_model="gpt-5-mini",
                custom_context_limit_override=4096,
                keep_recent_messages=1,
            ),
            SkillEngine(Path(tempfile.gettempdir()) / "budget-projects", self.repo_skill_dir),
        )
        conversation = [
            {"role": "system", "content": "规则"},
            {"role": "user", "content": "X" * 20000},
        ]

        with self.assertRaisesRegex(ValueError, "超过模型上下文预算"):
            handler._fit_conversation_to_budget(conversation)

    @mock.patch("backend.chat.OpenAI")
    def test_fit_budget_trims_oldest_visible_user_assistant_pair_as_one_group(self, mock_openai):
        del mock_openai
        handler = ChatHandler(
            self._make_settings(),
            SkillEngine(Path(tempfile.gettempdir()) / "budget-projects", self.repo_skill_dir),
        )
        conversation = [
            {"role": "system", "content": "system"},
            {"role": "assistant", "content": "[对话摘要]\n压缩摘要"},
            handler._build_memory_block_message(["保留记忆"]),
            {"role": "user", "content": "最近问题1"},
            {"role": "assistant", "content": "最近回答1"},
            {"role": "user", "content": "当前追问"},
        ]

        def estimate_message_tokens(message):
            return {
                "system": 8,
                "[对话摘要]\n压缩摘要": 6,
                "最近问题1": 10,
                "最近回答1": 10,
                "当前追问": 8,
            }.get(message["content"], 12)

        policy = SimpleNamespace(
            compress_threshold=44,
            effective_context_limit=64,
            provider_context_limit=64,
            reserved_output_tokens=8,
        )

        with mock.patch.object(handler, "_resolve_context_policy", return_value=policy):
            with mock.patch.object(handler, "_estimate_message_tokens", side_effect=estimate_message_tokens):
                fitted, _, compressed, returned_policy = handler._fit_conversation_to_budget(conversation)

        self.assertTrue(compressed)
        self.assertIs(returned_policy, policy)
        self.assertEqual(
            fitted,
            [
                {"role": "system", "content": "system"},
                {"role": "assistant", "content": "[对话摘要]\n压缩摘要"},
                handler._build_memory_block_message(["保留记忆"]),
                {"role": "user", "content": "当前追问"},
            ],
        )

    @mock.patch("backend.chat.OpenAI")
    def test_fit_budget_trims_recent_visible_messages_before_memory_block(self, mock_openai):
        del mock_openai
        handler = ChatHandler(
            self._make_settings(),
            SkillEngine(Path(tempfile.gettempdir()) / "budget-projects", self.repo_skill_dir),
        )
        conversation = [
            {"role": "system", "content": "system"},
            {"role": "assistant", "content": "[对话摘要]\n压缩摘要"},
            handler._build_memory_block_message(["保留记忆"]),
            {"role": "assistant", "content": "最近回答1"},
            {"role": "user", "content": "当前追问"},
        ]

        def estimate_message_tokens(message):
            return {
                "system": 8,
                "[对话摘要]\n压缩摘要": 6,
                "最近回答1": 10,
                "当前追问": 8,
            }.get(message["content"], 12)

        policy = SimpleNamespace(
            compress_threshold=34,
            effective_context_limit=64,
            provider_context_limit=64,
            reserved_output_tokens=8,
        )

        with mock.patch.object(handler, "_resolve_context_policy", return_value=policy):
            with mock.patch.object(handler, "_estimate_message_tokens", side_effect=estimate_message_tokens):
                fitted, _, compressed, returned_policy = handler._fit_conversation_to_budget(conversation)

        self.assertTrue(compressed)
        self.assertIs(returned_policy, policy)
        self.assertEqual(
            fitted,
            [
                {"role": "system", "content": "system"},
                {"role": "assistant", "content": "[对话摘要]\n压缩摘要"},
                handler._build_memory_block_message(["保留记忆"]),
                {"role": "user", "content": "当前追问"},
            ],
        )

    @mock.patch("backend.chat.OpenAI")
    def test_fit_budget_trims_memory_entries_as_whole_items_when_entry_contains_blank_lines(self, mock_openai):
        del mock_openai
        handler = ChatHandler(
            self._make_settings(),
            SkillEngine(Path(tempfile.gettempdir()) / "budget-projects", self.repo_skill_dir),
        )
        first_entry = "第一条记忆的第一段\n\n第一条记忆的第二段"
        second_entry = "第二条记忆"
        conversation = [
            {"role": "system", "content": "system"},
            {"role": "assistant", "content": "[对话摘要]\n压缩摘要"},
            handler._build_memory_block_message([first_entry, second_entry]),
            {"role": "user", "content": "当前追问"},
        ]

        def estimate_message_tokens(message):
            content = message["content"]
            if content == "system":
                return 8
            if content == "[对话摘要]\n压缩摘要":
                return 6
            if content == "当前追问":
                return 8
            if "第一条记忆的第一段" in content and "第二条记忆" in content:
                return 24
            if "第一条记忆的第二段" in content and "第二条记忆" in content:
                return 8
            if "第二条记忆" in content:
                return 8
            return 24

        policy = SimpleNamespace(
            compress_threshold=30,
            effective_context_limit=64,
            provider_context_limit=64,
            reserved_output_tokens=8,
        )

        with mock.patch.object(handler, "_resolve_context_policy", return_value=policy):
            with mock.patch.object(handler, "_estimate_message_tokens", side_effect=estimate_message_tokens):
                fitted, _, compressed, returned_policy = handler._fit_conversation_to_budget(conversation)

        self.assertTrue(compressed)
        self.assertIs(returned_policy, policy)
        self.assertEqual(
            fitted,
            [
                {"role": "system", "content": "system"},
                {"role": "assistant", "content": "[对话摘要]\n压缩摘要"},
                handler._build_memory_block_message([second_entry]),
                {"role": "user", "content": "当前追问"},
            ],
        )
        serialized = json.dumps(fitted, ensure_ascii=False)
        self.assertNotIn("第一条记忆的第二段", serialized)

    @mock.patch("backend.chat.OpenAI")
    def test_fit_budget_followup_preserves_current_turn_tool_chain_with_explicit_boundary(self, mock_openai):
        del mock_openai
        handler = ChatHandler(
            self._make_settings(),
            SkillEngine(Path(tempfile.gettempdir()) / "budget-projects", self.repo_skill_dir),
        )
        conversation = [
            {"role": "system", "content": "system"},
            {"role": "assistant", "content": "[对话摘要]\n压缩摘要"},
            handler._build_memory_block_message(["保留记忆"]),
            {"role": "user", "content": "旧问题"},
            {"role": "assistant", "content": "旧回答"},
            {"role": "user", "content": "当前追问"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tool-1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"file_path":"plan/outline.md"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "tool-1", "content": '{"status":"success"}'},
        ]

        def estimate_message_tokens(message):
            content = message.get("content", "")
            if content == "system":
                return 8
            if content == "[对话摘要]\n压缩摘要":
                return 6
            if content == "旧问题":
                return 10
            if content == "旧回答":
                return 10
            if content == "当前追问":
                return 8
            if message.get("role") == "assistant" and message.get("tool_calls"):
                return 10
            if message.get("role") == "tool":
                return 8
            return 8

        policy = SimpleNamespace(
            compress_threshold=50,
            effective_context_limit=64,
            provider_context_limit=64,
            reserved_output_tokens=8,
        )

        with mock.patch.object(handler, "_resolve_context_policy", return_value=policy):
            with mock.patch.object(handler, "_estimate_message_tokens", side_effect=estimate_message_tokens):
                fitted, _, compressed, returned_policy = handler._fit_conversation_to_budget(
                    conversation,
                    current_turn_start_index=5,
                )

        self.assertTrue(compressed)
        self.assertIs(returned_policy, policy)
        self.assertEqual(
            fitted,
            [
                {"role": "system", "content": "system"},
                {"role": "assistant", "content": "[对话摘要]\n压缩摘要"},
                handler._build_memory_block_message(["保留记忆"]),
                {"role": "user", "content": "当前追问"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "tool-1",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": '{"file_path":"plan/outline.md"}'},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "tool-1", "content": '{"status":"success"}'},
            ],
        )

    @mock.patch("backend.chat.OpenAI")
    def test_chat_reapplies_budget_fit_before_followup_completion_after_tool_result(self, mock_openai):
        tool_call = SimpleNamespace(
            id="tool-1",
            function=SimpleNamespace(
                name="read_file",
                arguments='{"file_path":"plan/outline.md"}',
            ),
        )
        mock_openai.return_value.chat.completions.create.side_effect = [
            SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="",
                            tool_calls=[tool_call],
                        )
                    )
                ],
            ),
            SimpleNamespace(
                usage=SimpleNamespace(total_tokens=321),
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="最终答复",
                            tool_calls=[],
                        )
                    )
                ],
            ),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    projects_dir=projects_dir,
                ),
                engine,
            )
            policy = handler._resolve_context_policy()
            fit_inputs = []
            compressed_followup = [
                {"role": "system", "content": "system"},
                {"role": "assistant", "content": "[压缩摘要]"},
                {"role": "tool", "tool_call_id": "tool-1", "content": '{"status":"success"}'},
            ]

            def fit_side_effect(conversation, **kwargs):
                current_turn_start_index = kwargs.get("current_turn_start_index", len(conversation) - 1)
                fit_inputs.append(conversation)
                if len(fit_inputs) == 1:
                    result = (conversation, handler._estimate_tokens(conversation), False, policy)
                else:
                    result = (compressed_followup, handler._estimate_tokens(compressed_followup), True, policy)
                if kwargs.get("return_current_turn_start_index"):
                    return (*result, current_turn_start_index)
                return result

            with mock.patch.object(handler, "_fit_conversation_to_budget", side_effect=fit_side_effect) as fit_mock:
                with mock.patch.object(
                    handler,
                    "_execute_tool",
                    return_value={"status": "success", "content": "工具结果" * 2000},
                ):
                    result = handler.chat(project["id"], "继续", max_iterations=2)

        self.assertIn("最终答复", result["content"])
        self.assertEqual(fit_mock.call_count, 2)
        self.assertTrue(any(message.get("role") == "tool" for message in fit_inputs[1]))
        self.assertEqual(
            mock_openai.return_value.chat.completions.create.call_args_list[1].kwargs["messages"],
            compressed_followup,
        )

    @mock.patch("backend.chat.OpenAI")
    def test_chat_does_not_reinject_same_request_tool_result_via_memory_block(self, mock_openai):
        tool_call = SimpleNamespace(
            id="tool-1",
            function=SimpleNamespace(
                name="read_file",
                arguments='{"file_path":"plan/outline.md"}',
            ),
        )
        mock_openai.return_value.chat.completions.create.side_effect = [
            SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="",
                            tool_calls=[tool_call],
                        )
                    )
                ],
            ),
            SimpleNamespace(
                usage=SimpleNamespace(total_tokens=321),
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="最终答复",
                            tool_calls=[],
                        )
                    )
                ],
            ),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            project_dir = Path(project["project_dir"])
            outline_path = project_dir / "plan" / "outline.md"
            outline_path.parent.mkdir(parents=True, exist_ok=True)
            outline_path.write_text("# 大纲正文", encoding="utf-8")
            handler = ChatHandler(
                self._make_settings(
                    projects_dir=projects_dir,
                ),
                engine,
            )
            policy = handler._resolve_context_policy()

            with mock.patch.object(
                handler,
                "_fit_conversation_to_budget",
                side_effect=lambda conversation, **kwargs: (
                    conversation,
                    handler._estimate_tokens(conversation),
                    False,
                    policy,
                    kwargs.get("current_turn_start_index", len(conversation) - 1),
                ) if kwargs.get("return_current_turn_start_index") else (
                    conversation,
                    handler._estimate_tokens(conversation),
                    False,
                    policy,
                ),
            ):
                result = handler.chat(project["id"], "继续", max_iterations=2)

            second_call_messages = mock_openai.return_value.chat.completions.create.call_args_list[1].kwargs["messages"]
            persisted_state = json.loads((project_dir / "conversation_state.json").read_text(encoding="utf-8"))

        self.assertIn("最终答复", result["content"])
        self.assertTrue(any(message.get("role") == "tool" for message in second_call_messages))
        self.assertFalse(any(handler._is_memory_block_message(message) for message in second_call_messages))
        self.assertEqual(len(persisted_state["memory_entries"]), 1)
        self.assertEqual(persisted_state["memory_entries"][0]["source_key"], "file:plan/outline.md")

    @mock.patch("backend.chat.OpenAI")
    def test_failed_same_request_tool_result_does_not_hide_existing_memory_block(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            project_dir = Path(project["project_dir"])
            (project_dir / "conversation_state.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "events": [],
                        "memory_entries": [
                            {
                                "category": "workspace",
                                "source_key": "file:plan/outline.md",
                                "source_ref": "plan/outline.md",
                                "content": "# 已有大纲",
                            }
                        ],
                        "compact_state": None,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            handler = ChatHandler(
                self._make_settings(
                    projects_dir=projects_dir,
                ),
                engine,
            )
            current_turn_messages = [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "tool-1",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": '{"file_path":"plan/outline.md"}'},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "tool-1",
                    "content": json.dumps({"status": "error", "message": "读取失败"}, ensure_ascii=False),
                },
            ]

            conversation, _ = handler._build_provider_turn_conversation(
                project["id"],
                [],
                {"role": "user", "content": "继续", "attached_material_ids": [], "transient_attachments": []},
                current_turn_messages=current_turn_messages,
                exclude_current_turn_memory=True,
            )

        memory_message = next(
            (message for message in conversation if handler._is_memory_block_message(message)),
            None,
        )
        self.assertIsNotNone(memory_message)
        self.assertEqual(handler._split_memory_block_items(memory_message), ["来源: plan/outline.md\n# 已有大纲"])

    @mock.patch("backend.chat.OpenAI")
    def test_chat_serializes_same_project_requests_with_request_lock(self, mock_openai):
        provider_lock = threading.Lock()
        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()
        active_calls = 0
        max_active_calls = 0
        call_order = []

        def create_side_effect(**kwargs):
            nonlocal active_calls, max_active_calls
            with provider_lock:
                active_calls += 1
                max_active_calls = max(max_active_calls, active_calls)
                is_first = not first_entered.is_set()
            if is_first:
                first_entered.set()
                release_first.wait(timeout=2)
                response_text = "第一轮完成"
            else:
                second_entered.set()
                response_text = "第二轮完成"
            with provider_lock:
                call_order.append(response_text)
                active_calls -= 1
            return SimpleNamespace(
                usage=SimpleNamespace(total_tokens=128),
                choices=[SimpleNamespace(message=SimpleNamespace(content=response_text, tool_calls=[]))],
            )

        mock_openai.return_value.chat.completions.create.side_effect = create_side_effect

        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )
            results = {}

            def run_chat(slot, prompt):
                results[slot] = handler.chat(project["id"], prompt)

            first_thread = threading.Thread(target=run_chat, args=("first", "先处理我"))
            second_thread = threading.Thread(target=run_chat, args=("second", "再处理我"))

            first_thread.start()
            self.assertTrue(first_entered.wait(1.0))
            second_thread.start()
            self.assertFalse(second_entered.wait(0.2))
            release_first.set()
            first_thread.join(timeout=2)
            second_thread.join(timeout=2)

        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertEqual(call_order, ["第一轮完成", "第二轮完成"])
        self.assertEqual(max_active_calls, 1)
        self.assertEqual(results["first"]["content"], "第一轮完成")
        self.assertEqual(results["second"]["content"], "第二轮完成")

    @mock.patch("backend.chat.OpenAI")
    def test_project_request_lock_is_shared_across_handler_instances(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            first_handler = ChatHandler(
                self._make_settings(
                    projects_dir=projects_dir,
                ),
                engine,
            )
            second_handler = ChatHandler(
                self._make_settings(
                    projects_dir=projects_dir,
                ),
                engine,
            )

            first_lock = first_handler._get_project_request_lock(project["id"])
            second_lock = second_handler._get_project_request_lock(project["id"])

        self.assertIs(first_lock, second_lock)

    @mock.patch("backend.chat.OpenAI")
    def test_module_and_instance_level_project_locks_share_identity(self, mock_openai):
        del mock_openai
        from backend.chat import _get_project_request_lock as module_lock

        handler = self._make_handler_with_project()
        self._write_stage_one_prerequisites(self.project_dir)

        module_obj = module_lock(self.project_id)
        instance_obj = handler._get_project_request_lock(self.project_id)
        with mock.patch("backend.main.get_chat_handler") as mock_get_chat_handler:
            handler.skill_engine.record_stage_checkpoint(
                self.project_id,
                "outline_confirmed_at",
                "set",
            )
            checkpoint_obj = module_lock(self.project_id)

        self.assertIs(module_obj, instance_obj)
        self.assertIs(module_obj, checkpoint_obj)
        mock_get_chat_handler.assert_not_called()

    @mock.patch("backend.chat.OpenAI")
    def test_chat_falls_back_to_estimated_usage_when_final_tool_round_has_no_provider_usage(self, mock_openai):
        tool_call = SimpleNamespace(
            id="tool-1",
            function=SimpleNamespace(
                name="read_file",
                arguments='{"file_path":"plan/outline.md"}',
            ),
        )
        mock_openai.return_value.chat.completions.create.side_effect = [
            SimpleNamespace(
                usage=SimpleNamespace(total_tokens=777),
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="",
                            tool_calls=[tool_call],
                        )
                    )
                ],
            ),
            SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="最终答复",
                            tool_calls=[],
                        )
                    )
                ],
            ),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    projects_dir=projects_dir,
                ),
                engine,
            )
            policy = handler._resolve_context_policy()

            with mock.patch.object(
                handler,
                "_fit_conversation_to_budget",
                side_effect=lambda conversation, **kwargs: (
                    conversation,
                    0,
                    False,
                    policy,
                    kwargs.get("current_turn_start_index", len(conversation) - 1),
                ) if kwargs.get("return_current_turn_start_index") else (conversation, 0, False, policy),
            ):
                with mock.patch.object(handler, "_estimate_tokens", return_value=1234):
                    with mock.patch.object(
                        handler,
                        "_execute_tool",
                        return_value={"status": "success", "content": "工具结果"},
                    ):
                        result = handler.chat(project["id"], "继续", max_iterations=2)

        self.assertIn("最终答复", result["content"])
        self.assertEqual(result["token_usage"]["usage_source"], "unavailable")
        self.assertIsNone(result["token_usage"]["context_used_tokens"])
        self.assertEqual(result["token_usage"]["post_turn_compaction_status"], "skipped_unavailable")

    @unittest.skip("replaced by tempdir-backed variant below")
    @mock.patch("backend.chat.OpenAI")
    def test_chat_request_max_tokens_is_bounded_by_policy_reserved_budget(self, mock_openai):
        mock_openai.return_value.chat.completions.create.return_value = SimpleNamespace(
            usage=SimpleNamespace(total_tokens=123),
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="完成",
                        tool_calls=[],
                    )
                )
            ],
        )
        handler = ChatHandler(
            self._make_settings(
                mode="custom",
                custom_api_base="https://custom.example/v1",
                custom_api_key="secret",
                custom_model="gpt-5-mini",
                custom_context_limit_override=4096,
            ),
            SkillEngine(Path(tempfile.gettempdir()) / "small-budget-projects", self.repo_skill_dir),
        )

        handler.chat("demo", "请继续")

        self.assertEqual(
            mock_openai.return_value.chat.completions.create.call_args.kwargs["max_tokens"],
            2048,
        )

    @mock.patch("backend.chat.OpenAI")
    def test_chat_request_max_tokens_is_bounded_by_policy_reserved_budget_with_real_project(self, mock_openai):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            mock_openai.return_value.chat.completions.create.return_value = SimpleNamespace(
                usage=SimpleNamespace(total_tokens=123),
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="瀹屾垚",
                            tool_calls=[],
                        )
                    )
                ],
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="custom",
                    custom_api_base="https://custom.example/v1",
                    custom_api_key="secret",
                    custom_model="gpt-5-mini",
                    custom_context_limit_override=4096,
                    projects_dir=projects_dir,
                ),
                engine,
            )
            policy = handler._resolve_context_policy()

            with mock.patch.object(
                handler,
                "_fit_conversation_to_budget",
                side_effect=lambda conversation, **kwargs: (
                    conversation,
                    0,
                    False,
                    policy,
                    kwargs.get("current_turn_start_index", len(conversation) - 1),
                ) if kwargs.get("return_current_turn_start_index") else (conversation, 0, False, policy),
            ):
                handler.chat("demo", "璇风户缁?")

            self.assertEqual(
                mock_openai.return_value.chat.completions.create.call_args.kwargs["max_tokens"],
                2048,
            )

    @mock.patch("backend.chat.OpenAI")
    def test_web_search_returns_compatibility_text_and_provider_metadata(self, mock_openai):
        settings = Settings(
            mode="managed",
            managed_base_url="https://newapi.z0y0h.work/client/v1",
            managed_model="gemini-3-flash",
            projects_dir=Path(tempfile.gettempdir()) / "dummy-projects",
            skill_dir=self.repo_skill_dir,
        )
        handler = ChatHandler(settings, SkillEngine(settings.projects_dir, self.repo_skill_dir))
        fake_router = mock.Mock()
        fake_router.search.return_value = {
            "status": "success",
            "provider": "serper",
            "cached": False,
            "native_fallback_used": False,
            "result_type": "success",
            "items": [
                {
                    "title": "猪猪侠2025观察",
                    "snippet": "授权与票房摘要",
                    "url": "https://example.com/a",
                    "domain": "example.com",
                    "score": 0.9,
                }
            ],
            "results": "搜索结果：\n1. 猪猪侠2025观察\n授权与票房摘要\n链接: https://example.com/a",
        }

        with mock.patch.object(handler, "_get_search_router", return_value=fake_router):
            result = handler._web_search("猪猪侠 2025", project_id="demo", turn_search_count=0)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["provider"], "serper")
        self.assertIn("猪猪侠2025观察", result["results"])
        self.assertEqual(result["items"][0]["domain"], "example.com")
        fake_router.search.assert_called_once_with(
            "猪猪侠 2025",
            project_id="demo",
            turn_search_count=0,
            native_search=handler._search_with_native_provider,
        )

    @mock.patch("backend.chat.OpenAI")
    def test_execute_tool_increments_web_search_count_after_success(self, mock_openai):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(self._make_settings(projects_dir=projects_dir), engine)
            handler._turn_context = {
                "can_write_non_plan": True,
                "web_search_disabled": False,
                "web_search_performed": False,
                "fetch_url_performed": False,
                "web_search_count": 0,
            }

            with mock.patch.object(
                handler,
                "_web_search",
                return_value={"status": "success", "provider": "serper", "results": "ok"},
            ):
                result = handler._execute_tool(
                    project["id"],
                    self._make_tool_call("web_search", '{"query":"第一次"}'),
                )

        self.assertEqual(result["status"], "success")
        self.assertEqual(handler._turn_context["web_search_count"], 1)
        self.assertTrue(handler._turn_context["web_search_performed"])

    @mock.patch("backend.chat.OpenAI")
    def test_execute_tool_tracks_web_search_count_and_blocks_third_search_in_same_turn(self, mock_openai):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(self._make_settings(projects_dir=projects_dir), engine)
            handler._turn_context = {
                "can_write_non_plan": True,
                "web_search_disabled": False,
                "web_search_performed": False,
                "fetch_url_performed": False,
                "web_search_count": 2,
            }

            with mock.patch.object(
                handler,
                "_web_search",
                return_value={
                    "status": "error",
                    "error_type": "quota_exhausted",
                    "limit_scope": "per_turn",
                    "message": "当前内置搜索额度已用尽，请稍后再试。",
                },
            ):
                result = handler._execute_tool(
                    project["id"],
                    self._make_tool_call("web_search", '{"query":"第三次"}'),
                )

        self.assertEqual(result["status"], "error")
        self.assertIn("搜索额度已用尽", result["message"])
        self.assertEqual(handler._turn_context["web_search_count"], 2)

    @mock.patch("backend.chat.OpenAI")
    def test_execute_tool_increments_web_search_count_for_non_per_turn_quota_rejection(self, mock_openai):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(self._make_settings(projects_dir=projects_dir), engine)
            handler._turn_context = {
                "can_write_non_plan": True,
                "web_search_disabled": False,
                "web_search_performed": False,
                "fetch_url_performed": False,
                "web_search_count": 1,
            }

            with mock.patch.object(
                handler,
                "_web_search",
                return_value={
                    "status": "error",
                    "error_type": "quota_exhausted",
                    "limit_scope": "global_minute",
                    "message": "当前内置搜索额度已用尽，请稍后再试。",
                },
            ):
                result = handler._execute_tool(
                    project["id"],
                    self._make_tool_call("web_search", '{"query":"分钟限额"}'),
                )

        self.assertEqual(result["status"], "error")
        self.assertEqual(handler._turn_context["web_search_count"], 2)

    @mock.patch("backend.chat.OpenAI")
    def test_native_search_helper_returns_none_when_model_is_not_supported(self, mock_openai):
        handler = ChatHandler(
            self._make_settings(
                mode="managed",
                managed_model="gemini-3-flash",
            ),
            SkillEngine(Path(tempfile.gettempdir()) / "native-projects", self.repo_skill_dir),
        )

        result = handler._search_with_native_provider("OpenAI news")

        self.assertIsNone(result)
        mock_openai.return_value.responses.create.assert_not_called()

    @mock.patch("backend.chat.OpenAI")
    def test_native_search_helper_uses_openai_responses_api_when_supported(self, mock_openai):
        mock_client = mock_openai.return_value
        mock_client.responses.create.return_value = SimpleNamespace(output_text="Latest updates from OpenAI")
        handler = ChatHandler(
            self._make_settings(
                mode="custom",
                custom_api_base="https://api.openai.com/v1",
                custom_api_key="secret",
                custom_model="gpt-5",
            ),
            SkillEngine(Path(tempfile.gettempdir()) / "native-projects", self.repo_skill_dir),
        )

        result = handler._search_with_native_provider("OpenAI news")

        self.assertIsNotNone(result)
        self.assertEqual(result.provider, "native")
        mock_client.responses.create.assert_called_once()
        self.assertEqual(
            mock_client.responses.create.call_args.kwargs["tools"],
            [{"type": "web_search"}],
        )

    @mock.patch("backend.chat.OpenAI")
    def test_search_router_is_shared_across_handlers(self, mock_openai):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            settings = self._make_settings(projects_dir=projects_dir)
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            handler_a = ChatHandler(settings, engine)
            handler_b = ChatHandler(settings, engine)

            with mock.patch("backend.chat._SEARCH_ROUTER_SINGLETON", None), mock.patch(
                "backend.chat.load_managed_search_pool_config",
                return_value=self._make_search_pool_config(),
            ), mock.patch("backend.chat.SearchStateStore"), mock.patch(
                "backend.chat.SerperProvider"
            ), mock.patch("backend.chat.BraveProvider"), mock.patch(
                "backend.chat.TavilyProvider"
            ), mock.patch("backend.chat.ExaProvider"), mock.patch(
                "backend.chat.SearchRouter"
            ) as mock_router_cls:
                router_a = handler_a._get_search_router()
                router_b = handler_b._get_search_router()

        self.assertIs(router_a, router_b)
        mock_router_cls.assert_called_once()

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
                            "arguments": '{"file_path":"content/report_draft_v1.md","content":"# 正文"}',
                        },
                    )(),
                },
            )()

            result = handler._execute_tool(project["id"], tool_call)

        self.assertEqual(result["status"], "error")
        self.assertIn("先确认大纲", result["message"])

    @mock.patch("backend.chat.OpenAI")
    def test_handler_write_file_rejects_unregistered_plan_file(self, mock_openai):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            settings = Settings(
                mode="managed",
                managed_base_url="https://newapi.z0y0h.work/client/v1",
                managed_model="gemini-3-flash",
                projects_dir=projects_dir,
                skill_dir=self.repo_skill_dir,
            )
            handler = ChatHandler(settings, engine)
            handler._turn_context = {"can_write_non_plan": True, "web_search_disabled": False}

            result = handler._execute_tool(
                project["id"],
                self._make_tool_call(
                    "write_file",
                    '{"file_path":"plan/gate-control.md","content":"# Gate control"}',
                ),
            )

        self.assertEqual(result["status"], "error")
        self.assertIn("gate-control.md", result["message"])

    @mock.patch("backend.chat.OpenAI")
    def test_handler_write_file_rejects_backend_owned_stage_tracking_files(self, mock_openai):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            settings = Settings(
                mode="managed",
                managed_base_url="https://newapi.z0y0h.work/client/v1",
                managed_model="gemini-3-flash",
                projects_dir=projects_dir,
                skill_dir=self.repo_skill_dir,
            )
            handler = ChatHandler(settings, engine)
            handler._turn_context = {"can_write_non_plan": True, "web_search_disabled": False}

            result = handler._execute_tool(
                project["id"],
                self._make_tool_call(
                    "write_file",
                    '{"file_path":"plan/tasks.md","content":"# stale"}',
                ),
            )

        self.assertEqual(result["status"], "error")
        self.assertIn("backend-generated", result["message"])

    @mock.patch("backend.chat.OpenAI")
    def test_should_allow_non_plan_write_uses_recent_conversation_history_after_outline_confirmation(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(self._make_settings(projects_dir=projects_dir), engine)
            handler._save_conversation(
                project["id"],
                [
                    {"role": "user", "content": "大纲没问题，继续写正文吧"},
                    {"role": "assistant", "content": "收到，我继续推进正文草稿。"},
                ],
            )

            self.assertTrue(handler._should_allow_non_plan_write(project["id"], "继续"))

    @mock.patch("backend.chat.OpenAI")
    def test_should_allow_non_plan_write_respects_newer_blocking_instruction(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(self._make_settings(projects_dir=projects_dir), engine)
            handler._save_conversation(
                project["id"],
                [
                    {"role": "user", "content": "大纲没问题，继续写正文吧"},
                    {"role": "assistant", "content": "收到。"},
                    {"role": "user", "content": "先别写正文，先补计划"},
                ],
            )

            self.assertFalse(handler._should_allow_non_plan_write(project["id"], "继续"))

    @mock.patch("backend.chat.OpenAI")
    def test_should_block_non_plan_write_when_user_says_start_writing_plainly_in_s0(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(self._make_settings(projects_dir=projects_dir), engine)

            self.assertFalse(handler._should_allow_non_plan_write(project["id"], "你开始写吧"))

    @mock.patch("backend.chat.OpenAI")
    def test_should_block_non_plan_write_when_content_final_report_exists_and_user_asks_to_continue_in_s0(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            project_dir = Path(project["project_dir"])
            (project_dir / "content").mkdir(exist_ok=True)
            (project_dir / "content" / "final-report.md").write_text(
                "# Final report\n\n## Executive summary\nA concrete section.\n",
                encoding="utf-8",
            )
            handler = ChatHandler(self._make_settings(projects_dir=projects_dir), engine)

            self.assertFalse(handler._should_allow_non_plan_write(project["id"], "继续完善"))

    @mock.patch("backend.chat.OpenAI")
    def test_should_allow_non_plan_write_when_existing_report_exists_and_user_asks_to_expand(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            project_dir = Path(project["project_dir"])
            (project_dir / "content").mkdir(exist_ok=True)
            (project_dir / "content" / "report_draft_v1.md").write_text(
                "# Draft\n\n## Executive summary\nA concrete section.\n",
                encoding="utf-8",
            )
            handler = ChatHandler(self._make_settings(projects_dir=projects_dir), engine)

            self.assertTrue(handler._should_allow_non_plan_write(project["id"], "请扩写到5000字"))
            self.assertTrue(handler._should_allow_non_plan_write(project["id"], "帮我润色一下现有正文"))

    @mock.patch("backend.chat.OpenAI")
    def test_write_file_rejects_legacy_report_draft_paths_with_canonical_hint(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)

        for legacy_path in (
            "report_draft_v1.md",
            "content/report.md",
            "content/draft.md",
            "content/final-report.md",
            "output/final-report.md",
            "content/report_draft_v5.md",
        ):
            with self.subTest(legacy_path=legacy_path):
                result = handler._execute_tool(
                    self.project_id,
                    self._make_tool_call(
                        "write_file",
                        json.dumps(
                            {"file_path": legacy_path, "content": "# Legacy draft"},
                            ensure_ascii=False,
                        ),
                    ),
                )

                self.assertEqual(result["status"], "error")
                self.assertIn("content/report_draft_v1.md", result["message"])
                self.assertFalse((self.project_dir / legacy_path).exists())

    @mock.patch("backend.chat.OpenAI")
    def test_append_report_draft_creates_canonical_draft_via_write_gate(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        from backend.draft_action import DraftActionEvent
        handler._turn_context["draft_action_events"] = [
            DraftActionEvent(raw="...", intent="begin", executable=True)
        ]

        result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "append_report_draft",
                json.dumps({"content": "## 第三章：IP 强度对比\n\n" + ("正文" * 80)}, ensure_ascii=False),
            ),
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["path"], "content/report_draft_v1.md")
        self.assertTrue((self.project_dir / "content" / "report_draft_v1.md").exists())

    @mock.patch("backend.chat.OpenAI")
    def test_append_report_draft_appends_with_clean_blank_line_boundary(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        draft_path = self.project_dir / "content" / "report_draft_v1.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text("# Draft\n\n## 第一章\n\n已有正文\n", encoding="utf-8")

        result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "append_report_draft",
                json.dumps({"content": "## 第二章\n\n" + ("新增正文" * 60)}, ensure_ascii=False),
            ),
        )

        text = draft_path.read_text(encoding="utf-8")
        self.assertEqual(result["status"], "success")
        self.assertIn("已有正文\n\n## 第二章", text)

    @mock.patch("backend.chat.OpenAI")
    def test_read_before_write_requires_same_turn_read_for_existing_generic_write_file(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        target_path = self.project_dir / "plan" / "notes.md"
        target_path.write_text("# Notes\n\n旧内容\n", encoding="utf-8")
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)

        blocked = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "write_file",
                json.dumps(
                    {
                        "file_path": "plan/notes.md",
                        "content": "# Notes\n\n新内容\n",
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        self.assertEqual(blocked["status"], "error")
        self.assertIn("read_file", blocked["message"])
        self.assertEqual(target_path.read_text(encoding="utf-8"), "# Notes\n\n旧内容\n")

        read_result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "read_file",
                json.dumps({"file_path": "plan/notes.md"}, ensure_ascii=False),
            ),
        )
        allowed = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "write_file",
                json.dumps(
                    {
                        "file_path": "plan/notes.md",
                        "content": "# Notes\n\n新内容\n",
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        self.assertEqual(read_result["status"], "success")
        self.assertEqual(allowed["status"], "success")
        self.assertEqual(target_path.read_text(encoding="utf-8"), "# Notes\n\n新内容\n")

    @mock.patch("backend.chat.OpenAI")
    def test_read_before_write_requires_same_turn_read_for_existing_generic_edit_file(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        target_path = self.project_dir / "plan" / "notes.md"
        target_path.write_text("# Notes\n\n旧内容\n", encoding="utf-8")
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)

        blocked = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "edit_file",
                json.dumps(
                    {
                        "file_path": "plan/notes.md",
                        "old_string": "旧内容",
                        "new_string": "新内容",
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        self.assertEqual(blocked["status"], "error")
        self.assertIn("read_file", blocked["message"])
        self.assertEqual(target_path.read_text(encoding="utf-8"), "# Notes\n\n旧内容\n")

        read_result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "read_file",
                json.dumps({"file_path": "plan/notes.md"}, ensure_ascii=False),
            ),
        )
        allowed = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "edit_file",
                json.dumps(
                    {
                        "file_path": "plan/notes.md",
                        "old_string": "旧内容",
                        "new_string": "新内容",
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        self.assertEqual(read_result["status"], "success")
        self.assertEqual(allowed["status"], "success")
        self.assertEqual(target_path.read_text(encoding="utf-8"), "# Notes\n\n新内容\n")

    @mock.patch("backend.chat.OpenAI")
    def test_canonical_draft_write_file_is_rejected_even_when_write_is_otherwise_allowed(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        draft_path = self._write_partial_report_draft("既有正文" * 120)
        before = draft_path.read_text(encoding="utf-8")
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)

        result = handler._execute_tool(
            self.project_id,
            self._make_write_report_tool_call(content="# Draft\n\n整份替换内容\n"),
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("write_file", result["message"])
        self.assertIn("append_report_draft", result["message"])
        self.assertEqual(draft_path.read_text(encoding="utf-8"), before)

    @mock.patch("backend.chat.OpenAI")
    def test_append_report_draft_is_exempt_from_same_turn_read_before_write(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        draft_path = self._write_partial_report_draft("已有正文" * 120)
        before = draft_path.read_text(encoding="utf-8")

        result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "append_report_draft",
                json.dumps({"content": "## 第二章\n\n" + ("新增正文" * 60)}, ensure_ascii=False),
            ),
        )

        self.assertEqual(result["status"], "success")
        self.assertNotEqual(draft_path.read_text(encoding="utf-8"), before)

    @mock.patch("backend.chat.OpenAI")
    def test_canonical_edit_file_requires_same_turn_read_before_write(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        draft_path = self._write_partial_report_draft("执行摘要保留旧版表述。")
        original = draft_path.read_text(encoding="utf-8")
        exec_summary = "## 第一章\n\n执行摘要保留旧版表述。"
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            handler._turn_context = handler._build_turn_context(
                self.project_id,
                "把第一章改强一点",
            )
            snapshots = handler._build_required_write_snapshots(
                self.project_id,
                "把第一章改强一点",
            )
        handler._turn_context["required_write_snapshots"] = snapshots

        blocked = handler._execute_tool(
            self.project_id,
            self._make_edit_report_tool_call(
                old_string=exec_summary,
                new_string="## 第一章\n\n更强的章节版本。",
            ),
        )

        self.assertEqual(blocked["status"], "error")
        self.assertIn("read_file", blocked["message"])
        self.assertEqual(draft_path.read_text(encoding="utf-8"), original)

        read_result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "read_file",
                json.dumps({"file_path": "content/report_draft_v1.md"}, ensure_ascii=False),
            ),
        )
        allowed = handler._execute_tool(
            self.project_id,
            self._make_edit_report_tool_call(
                old_string=exec_summary,
                new_string="## 第一章\n\n更强的章节版本。",
            ),
        )

        self.assertEqual(read_result["status"], "success")
        self.assertEqual(allowed["status"], "success")
        self.assertIn("更强的章节版本", draft_path.read_text(encoding="utf-8"))

    @mock.patch("backend.chat.OpenAI")
    def test_canonical_draft_mutation_blocks_second_successful_mutation_in_same_turn(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        draft_path = self._write_partial_report_draft("既有正文" * 120)
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S4"),
        ):
            handler._turn_context = handler._build_turn_context(
                self.project_id,
                "继续写正文",
            )
            snapshots = handler._build_required_write_snapshots(
                self.project_id,
                "继续写正文",
            )
        handler._turn_context["required_write_snapshots"] = snapshots
        handler._turn_context["canonical_draft_decision"]["preflight_keyword_intent"] = "continue"

        first = handler._execute_tool(
            self.project_id,
            self._make_append_report_tool_call(),
        )
        after_first = draft_path.read_text(encoding="utf-8")
        second = handler._execute_tool(
            self.project_id,
            self._make_append_report_tool_call(call_id="call-append-2"),
        )

        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "error")
        self.assertIn("本轮已经成功", second["message"])
        self.assertEqual(draft_path.read_text(encoding="utf-8"), after_first)

    @mock.patch("backend.chat.OpenAI")
    def test_append_report_draft_rejects_short_content(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)

        result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "append_report_draft",
                json.dumps({"content": "## 小结\n\n太短"}, ensure_ascii=False),
            ),
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("至少 80", result["message"])
        self.assertFalse((self.project_dir / "content" / "report_draft_v1.md").exists())

    @mock.patch("backend.chat.OpenAI")
    def test_append_report_draft_blocked_when_non_plan_write_disallowed(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        handler._turn_context = handler._new_turn_context(can_write_non_plan=False)

        result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "append_report_draft",
                json.dumps({"content": "## 第三章：IP 强度对比\n\n" + ("正文" * 80)}, ensure_ascii=False),
            ),
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("先确认大纲", result["message"])
        self.assertFalse((self.project_dir / "content" / "report_draft_v1.md").exists())

    @mock.patch("backend.chat.OpenAI")
    def test_append_report_draft_memory_entry_refreshes_canonical_source_key(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)

        result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "append_report_draft",
                json.dumps({"content": "## 第三章：IP 强度对比\n\n" + ("正文" * 80)}, ensure_ascii=False),
            ),
        )
        state_path = self.project_dir / "conversation_state.json"
        persisted = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(persisted["memory_entries"]), 1)
        self.assertEqual(
            persisted["memory_entries"][0]["source_key"],
            "file:content/report_draft_v1.md",
        )
        self.assertEqual(
            persisted["memory_entries"][0]["content"],
            (self.project_dir / "content" / "report_draft_v1.md").read_text(encoding="utf-8"),
        )

    @mock.patch("backend.chat.OpenAI")
    def test_append_report_draft_event_tool_name_stays_real_tool_name(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)

        result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "append_report_draft",
                json.dumps({"content": "## 第三章：IP 强度对比\n\n" + ("正文" * 80)}, ensure_ascii=False),
            ),
        )
        persisted = json.loads((self.project_dir / "conversation_state.json").read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(persisted["events"]), 1)
        self.assertEqual(persisted["events"][0]["tool_name"], "append_report_draft")
        self.assertEqual(persisted["events"][0]["source_key"], "file:content/report_draft_v1.md")

    @mock.patch("backend.chat.OpenAI")
    def test_append_report_draft_success_maps_to_current_turn_source_key(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        current_turn_messages = [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call-append",
                        "type": "function",
                        "function": {
                            "name": "append_report_draft",
                            "arguments": json.dumps(
                                {"content": "## 第三章：IP 强度对比\n\n" + ("正文" * 80)},
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-append",
                "content": json.dumps(
                    {"status": "success", "path": "content/report_draft_v1.md"},
                    ensure_ascii=False,
                ),
            },
        ]

        self.assertEqual(
            handler._current_turn_successful_tool_source_keys(
                self.project_id,
                current_turn_messages,
            ),
            {"file:content/report_draft_v1.md"},
        )

    @mock.patch("backend.chat.OpenAI")
    def test_current_turn_successful_tool_source_keys_include_edit_file_success(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        current_turn_messages = [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call-edit",
                        "type": "function",
                        "function": {
                            "name": "edit_file",
                            "arguments": json.dumps(
                                {
                                    "file_path": "plan\\notes.md",
                                    "old_string": "旧内容",
                                    "new_string": "新内容",
                                },
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-edit",
                "content": json.dumps(
                    {"status": "success", "message": "已写入文件: plan/notes.md"},
                    ensure_ascii=False,
                ),
            },
        ]

        self.assertEqual(
            handler._current_turn_successful_tool_source_keys(
                self.project_id,
                current_turn_messages,
            ),
            {"file:plan/notes.md"},
        )

    def _required_write_paths_for_stage(
        self,
        handler: ChatHandler,
        stage_code: str,
        user_message: str,
        *,
        can_write_non_plan: bool = True,
    ) -> set[str]:
        handler._turn_context = handler._new_turn_context(can_write_non_plan=can_write_non_plan)
        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value={"stage_code": stage_code},
        ):
            return set(handler._build_required_write_snapshots(self.project_id, user_message))

    def _classify_canonical_draft_for_stage(
        self,
        handler: ChatHandler,
        stage_code: str,
        user_message: str,
    ) -> dict:
        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state(stage_code),
        ):
            return handler._classify_canonical_draft_turn(self.project_id, user_message)

    def _save_draft_followup_state(
        self,
        handler: ChatHandler,
        *,
        reported_under_target: bool = True,
        asked_continue_expand: bool = True,
        current_count: int = 1800,
        target_word_count: int = 3000,
        continuation_threshold_count: int | None = None,
    ) -> None:
        state = handler._empty_conversation_state()
        state["draft_followup_state"] = {
            "reported_under_target": reported_under_target,
            "asked_continue_expand": asked_continue_expand,
            "current_count": current_count,
            "target_word_count": target_word_count,
            "continuation_threshold_count": continuation_threshold_count,
        }
        handler._save_conversation_state_atomically(self.project_id, state)

    def _save_previous_assistant_turn(self, handler: ChatHandler, content: str = "上轮已说明正文仍需继续扩写。") -> None:
        handler._save_conversation(
            self.project_id,
            [{"role": "assistant", "content": content}],
        )

    def _mock_stage_state(self, stage_code: str) -> dict:
        return {
            "stage_code": stage_code,
            "stage_status": "进行中",
            "completed_items": [],
            "skipped_items": [],
            "checkpoints": {
                "outline_confirmed_at": "2026-04-23T10:00:00",
            },
            "length_targets": {
                "report_word_floor": 3000,
                "data_log_min": 0,
                "analysis_refs_min": 0,
                "fallback_used": False,
            },
            "flags": {},
        }

    def _write_partial_report_draft(self, body: str = "已有正文") -> Path:
        draft_path = self.project_dir / "content" / "report_draft_v1.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text("# Draft\n\n## 第一章\n\n" + body + "\n", encoding="utf-8")
        return draft_path

    def _make_non_stream_response(self, content: str, *, total_tokens: int = 32):
        return SimpleNamespace(
            usage=SimpleNamespace(total_tokens=total_tokens),
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=content,
                        tool_calls=[],
                    )
                )
            ],
        )

    def _make_non_stream_tool_response(self, tool_call):
        return SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="",
                        tool_calls=[tool_call],
                    )
                )
            ],
        )

    def _make_append_report_tool_call(self, call_id: str = "call-append", content: str | None = None):
        append_content = content
        if append_content is None:
            append_content = "## 第二章：策略建议\n\n" + ("新增正文" * 80)
        return SimpleNamespace(
            id=call_id,
            function=SimpleNamespace(
                name="append_report_draft",
                arguments=json.dumps(
                    {"content": append_content},
                    ensure_ascii=False,
                ),
            ),
        )

    def _make_read_tool_call(
        self,
        file_path: str,
        *,
        call_id: str = "call-read-file",
    ):
        return SimpleNamespace(
            id=call_id,
            function=SimpleNamespace(
                name="read_file",
                arguments=json.dumps(
                    {"file_path": file_path},
                    ensure_ascii=False,
                ),
            ),
        )

    def _make_edit_report_tool_call(
        self,
        *,
        old_string: str = "旧结论",
        new_string: str = "新结论",
        call_id: str = "call-edit-report",
    ):
        return SimpleNamespace(
            id=call_id,
            function=SimpleNamespace(
                name="edit_file",
                arguments=json.dumps(
                    {
                        "file_path": "content/report_draft_v1.md",
                        "old_string": old_string,
                        "new_string": new_string,
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    def _make_write_report_tool_call(
        self,
        *,
        content: str,
        file_path: str = "content/report_draft_v1.md",
        call_id: str = "call-write-report",
    ):
        return SimpleNamespace(
            id=call_id,
            function=SimpleNamespace(
                name="write_file",
                arguments=json.dumps(
                    {
                        "file_path": file_path,
                        "content": content,
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    def _make_append_report_stream_chunk(self, call_id: str = "call-append"):
        return self._make_chunk(
            tool_calls=[
                self._make_stream_tool_call_chunk(
                    0,
                    id=call_id,
                    name="append_report_draft",
                    arguments=json.dumps(
                        {"content": "## 第二章：策略建议\n\n" + ("新增正文" * 80)},
                        ensure_ascii=False,
                    ),
                )
            ]
        )

    def _make_read_stream_chunk(
        self,
        file_path: str,
        *,
        call_id: str = "call-read-file",
    ):
        return self._make_chunk(
            tool_calls=[
                self._make_stream_tool_call_chunk(
                    0,
                    id=call_id,
                    name="read_file",
                    arguments=json.dumps(
                        {"file_path": file_path},
                        ensure_ascii=False,
                    ),
                )
            ]
        )

    def _make_edit_report_stream_chunk(
        self,
        *,
        old_string: str = "旧结论",
        new_string: str = "新结论",
        call_id: str = "call-edit-report",
    ):
        return self._make_chunk(
            tool_calls=[
                self._make_stream_tool_call_chunk(
                    0,
                    id=call_id,
                    name="edit_file",
                    arguments=json.dumps(
                        {
                            "file_path": "content/report_draft_v1.md",
                            "old_string": old_string,
                            "new_string": new_string,
                        },
                        ensure_ascii=False,
                    ),
                )
            ]
        )

    def _read_saved_conversation(self) -> list[dict]:
        return json.loads(
            (self.project_dir / "conversation.json").read_text(encoding="utf-8")
        )

    def _read_file_for_turn(
        self,
        handler: ChatHandler,
        file_path: str,
        project_id: str | None = None,
    ):
        effective_project_id = project_id or self.project_id
        snapshot = handler._snapshot_project_file(effective_project_id, file_path)
        if not snapshot.get("exists"):
            return None

        result = handler._execute_tool(
            effective_project_id,
            self._make_read_tool_call(file_path),
        )
        self.assertEqual(result["status"], "success")
        return result

    @mock.patch("backend.chat.OpenAI")
    def test_canonical_draft_decision_s4_first_draft_request_requires_append(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()

        decision = self._classify_canonical_draft_for_stage(handler, "S4", "开始写正文")

        self.assertEqual(decision["mode"], "require")
        self.assertEqual(decision["priority"], "P4")
        self.assertEqual(decision["expected_tool_family"], "append_report_draft")

    @mock.patch("backend.chat.OpenAI")
    def test_canonical_draft_decision_edit_only_without_draft_returns_fixed_reject(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()

        decision = self._classify_canonical_draft_for_stage(handler, "S5", "把报告里旧结论改成新结论")

        self.assertEqual(decision["mode"], "reject")
        self.assertEqual(
            decision["fixed_message"],
            "当前还没有正文草稿，请先用 append_report_draft 起草第一版。",
        )

    @mock.patch("backend.chat.OpenAI")
    def test_chat_immediately_rejects_no_draft_replace_without_provider_execution(self, mock_openai):
        handler = self._make_handler_with_project()

        result = handler.chat(self.project_id, "把报告里旧结论改成新结论")

        self.assertEqual(
            result["content"],
            "当前还没有正文草稿，请先用 append_report_draft 起草第一版。",
        )
        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 0)

    @mock.patch("backend.chat.OpenAI")
    def test_chat_immediately_rejects_no_draft_full_rewrite_without_provider_execution(self, mock_openai):
        handler = self._make_handler_with_project()

        result = handler.chat(self.project_id, "请全文重写这份报告正文")

        self.assertEqual(
            result["content"],
            "当前还没有正文草稿，请先用 append_report_draft 起草第一版。",
        )
        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 0)

    @mock.patch("backend.chat.OpenAI")
    def test_chat_immediately_rejects_split_turn_case_without_provider_execution(self, mock_openai):
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("现有章节。" * 120)

        result = handler.chat(self.project_id, "先扩到 5000 字再导出并运行质量检查")

        self.assertIn("拆成多个回合", result["content"])
        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 0)

    @mock.patch("backend.chat.OpenAI")
    def test_threshold_recheck_p5a_already_met_returns_guidance_only_without_mutation(self, mock_openai):
        handler = self._make_handler_with_project()
        draft_path = self._write_partial_report_draft("现有章节。" * 120)
        before = draft_path.read_text(encoding="utf-8")

        result = handler.chat(self.project_id, "先扩到 500 字再导出")

        self.assertIn("导出", result["content"])
        self.assertIn("下一轮", result["content"])
        self.assertIn("500", result["content"])
        self.assertEqual(draft_path.read_text(encoding="utf-8"), before)
        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 0)

    @mock.patch("backend.chat.OpenAI")
    def test_canonical_draft_decision_whole_draft_rewrite_uses_full_file_edit_path(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("执行摘要保留旧版表述。")

        decision = self._classify_canonical_draft_for_stage(handler, "S5", "请全文重写这份报告正文")

        self.assertEqual(decision["mode"], "require")
        self.assertEqual(decision["priority"], "P3")
        self.assertEqual(decision["expected_tool_family"], "edit_file")
        self.assertEqual(decision["required_edit_scope"], "full_draft")

    @mock.patch("backend.chat.OpenAI")
    def test_canonical_draft_decision_whole_draft_rewrite_without_draft_returns_fixed_reject(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()

        decision = self._classify_canonical_draft_for_stage(handler, "S5", "请全文重写这份报告正文")

        self.assertEqual(decision["mode"], "reject")
        self.assertEqual(
            decision["fixed_message"],
            "当前还没有正文草稿，请先用 append_report_draft 起草第一版。",
        )

    @mock.patch("backend.chat.OpenAI")
    def test_canonical_draft_decision_followup_state_unlocks_implicit_append_in_s4(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("现有章节偏短。" * 120)
        self._save_draft_followup_state(
            handler,
            current_count=1800,
            target_word_count=3000,
            continuation_threshold_count=5000,
        )

        decision = self._classify_canonical_draft_for_stage(
            handler,
            "S4",
            "目标5000字喔？而且每章现在都太单薄了",
        )

        self.assertEqual(decision["mode"], "require")
        self.assertEqual(decision["priority"], "P8")
        self.assertEqual(decision["expected_tool_family"], "append_report_draft")

    @mock.patch("backend.chat.OpenAI")
    def test_canonical_draft_decision_followup_state_does_not_force_append_for_unrelated_action(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("现有章节偏短。" * 120)
        self._save_draft_followup_state(handler, current_count=1800, target_word_count=3000)

        decision = self._classify_canonical_draft_for_stage(
            handler,
            "S4",
            "目标5000字喔？而且每章现在都太单薄了，先开始审查",
        )

        self.assertEqual(decision["mode"], "no_write")
        self.assertEqual(decision["priority"], "P6")

    @mock.patch("backend.chat.OpenAI")
    def test_canonical_draft_decision_p8_does_not_match_distinct_non_expansion_action(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("现有章节偏短。" * 120)
        self._save_draft_followup_state(
            handler,
            current_count=1800,
            target_word_count=3000,
            continuation_threshold_count=5000,
        )

        decision = self._classify_canonical_draft_for_stage(
            handler,
            "S4",
            "太单薄了，顺便把封面标题改一下",
        )

        self.assertNotEqual(decision["mode"], "require")
        self.assertNotEqual(decision["priority"], "P8")
        self.assertNotEqual(decision["expected_tool_family"], "append_report_draft")

    @mock.patch("backend.chat.OpenAI")
    def test_canonical_draft_decision_followup_state_is_s4_only(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("现有章节偏短。" * 120)
        self._save_draft_followup_state(
            handler,
            current_count=1800,
            target_word_count=3000,
            continuation_threshold_count=5000,
        )

        for stage_code in ("S5", "done"):
            with self.subTest(stage_code=stage_code):
                decision = self._classify_canonical_draft_for_stage(
                    handler,
                    stage_code,
                    "目标5000字喔？而且每章现在都太单薄了",
                )
                self.assertNotEqual(decision["mode"], "require")
                self.assertNotEqual(decision["priority"], "P8")

    @mock.patch("backend.chat.OpenAI")
    def test_load_draft_followup_state_reads_sidecar_without_conversation_history(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("现有章节偏短。" * 120)
        self._save_draft_followup_state(handler, current_count=1800, target_word_count=3000)

        with mock.patch.object(handler, "_load_conversation", side_effect=AssertionError("should not read conversation")):
            state = handler._load_draft_followup_state(self.project_id)
            decision = self._classify_canonical_draft_for_stage(
                handler,
                "S4",
                "目标5000字喔？而且每章现在都太单薄了",
            )

        self.assertIsNotNone(state)
        self.assertEqual(decision["mode"], "require")
        self.assertEqual(decision["priority"], "P8")

    @mock.patch("backend.chat.OpenAI")
    def test_canonical_draft_decision_explicit_continuation_authorizes_append_without_history(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("已有章节。" * 100)

        for message in ("继续写正文", "扩写正文"):
            with self.subTest(message=message):
                decision = self._classify_canonical_draft_for_stage(handler, "S7", message)
                self.assertEqual(decision["mode"], "require")
                self.assertEqual(decision["priority"], "P9")
                self.assertEqual(decision["expected_tool_family"], "append_report_draft")

    @mock.patch("backend.chat.OpenAI")
    def test_mixed_intent_canonical_draft_decision_routes_target_then_export(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("现有章节。" * 120)

        decision = self._classify_canonical_draft_for_stage(handler, "S4", "先扩到 5000 字再导出")

        self.assertEqual(decision["mode"], "require")
        self.assertEqual(decision["priority"], "P5A")
        self.assertEqual(decision["expected_tool_family"], "append_report_draft")
        self.assertEqual(decision["mixed_intent_secondary_family"], "export")
        self.assertEqual(decision["effective_turn_target_count"], 5000)

    @mock.patch("backend.chat.OpenAI")
    def test_mixed_intent_canonical_draft_decision_already_at_target_returns_p5a_no_write(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("现有章节。" * 1200)

        decision = self._classify_canonical_draft_for_stage(handler, "S4", "先扩到 500 字再导出")

        self.assertEqual(decision["mode"], "no_write")
        self.assertEqual(decision["priority"], "P5A")
        self.assertEqual(decision["mixed_intent_secondary_family"], "export")
        self.assertEqual(decision["effective_turn_target_count"], 500)

    @mock.patch("backend.chat.OpenAI")
    def test_mixed_intent_canonical_draft_decision_without_draft_but_with_first_draft_intent_falls_to_p4(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()

        decision = self._classify_canonical_draft_for_stage(
            handler,
            "S4",
            "开始写正文，写到 5000 字再导出",
        )

        self.assertEqual(decision["mode"], "require")
        self.assertEqual(decision["priority"], "P4")
        self.assertEqual(decision["expected_tool_family"], "append_report_draft")

    @mock.patch("backend.chat.OpenAI")
    def test_mixed_intent_canonical_draft_decision_routes_inspect_then_continue_if_needed(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("现有章节。" * 120)

        decision = self._classify_canonical_draft_for_stage(handler, "S4", "看看现在多少字，不够就继续写")

        self.assertEqual(decision["mode"], "require")
        self.assertEqual(decision["priority"], "P5A")
        self.assertEqual(decision["expected_tool_family"], "append_report_draft")
        self.assertEqual(decision["mixed_intent_secondary_family"], "inspect_word_count")
        self.assertEqual(decision["effective_turn_target_count"], 3000)

    @mock.patch("backend.chat.OpenAI")
    def test_mixed_intent_canonical_draft_decision_does_not_treat_incidental_digit_phrase_as_explicit_target(
        self,
        mock_openai,
    ):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("现有章节。" * 120)

        decision = self._classify_canonical_draft_for_stage(
            handler,
            "S4",
            "看看现在多少字，结合 2025 字节跳动案例，不够就继续写",
        )

        self.assertEqual(decision["mode"], "require")
        self.assertEqual(decision["priority"], "P5A")
        self.assertEqual(decision["mixed_intent_secondary_family"], "inspect_word_count")
        self.assertEqual(decision["effective_turn_target_count"], 3000)

    @mock.patch("backend.chat.OpenAI")
    def test_mixed_intent_canonical_draft_decision_routes_section_edit_before_export(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft(
            "## 执行摘要\n\n旧版摘要。\n\n## 第一章\n\n现有章节。"
        )

        decision = self._classify_canonical_draft_for_stage(handler, "S5", "把执行摘要改强一点后导出")

        self.assertEqual(decision["mode"], "require")
        self.assertEqual(decision["priority"], "P5B")
        self.assertEqual(decision["expected_tool_family"], "edit_file")
        self.assertEqual(decision["required_edit_scope"], "section")
        self.assertEqual(decision["mixed_intent_secondary_family"], "export")

    @mock.patch("backend.chat.OpenAI")
    def test_canonical_draft_decision_parent_and_child_heading_phrase_targets_single_deepest_section(
        self,
        mock_openai,
    ):
        del mock_openai
        handler = self._make_handler_with_project()
        draft_path = self.project_dir / "content" / "report_draft_v1.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(
            "# 报告草稿\n\n"
            "## 第一章\n\n"
            "章节总述。\n\n"
            "### 市场分析\n\n"
            "市场分析原文。\n\n"
            "### 竞争态势\n\n"
            "竞争态势原文。",
            encoding="utf-8",
        )

        decision = self._classify_canonical_draft_for_stage(
            handler,
            "S5",
            "把第一章市场分析改强一点",
        )

        self.assertEqual(decision["mode"], "require")
        self.assertEqual(decision["expected_tool_family"], "edit_file")
        self.assertEqual(decision["required_edit_scope"], "section")
        self.assertEqual(decision["rewrite_target_label"], "市场分析")
        self.assertNotEqual(decision["priority"], "P2_MULTI_SECTION")

    @mock.patch("backend.chat.OpenAI")
    def test_canonical_draft_decision_duplicate_headings_require_specific_section(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        draft_path = self.project_dir / "content" / "report_draft_v1.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(
            "# 报告草稿\n\n"
            "## 第一章\n\n"
            "### 市场分析\n\n"
            "第一章市场分析原文。\n\n"
            "## 第二章\n\n"
            "### 市场分析\n\n"
            "第二章市场分析原文。",
            encoding="utf-8",
        )

        decision = self._classify_canonical_draft_for_stage(
            handler,
            "S5",
            "把市场分析改强一点",
        )

        self.assertEqual(decision["mode"], "reject")
        self.assertEqual(decision["fixed_message"], "请指明具体章节。")

    @mock.patch("backend.chat.OpenAI")
    def test_mixed_intent_canonical_draft_decision_rejects_multiple_secondary_actions(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("现有章节。" * 120)

        decision = self._classify_canonical_draft_for_stage(
            handler,
            "S4",
            "先扩到 5000 字再导出并运行质量检查",
        )

        self.assertEqual(decision["mode"], "reject")
        self.assertEqual(decision["priority"], "P5_MULTI")
        self.assertIn("拆成", decision["fixed_message"])

    @mock.patch("backend.chat.OpenAI")
    def test_mixed_intent_canonical_draft_decision_rejects_multiple_secondary_actions_for_implicit_p8_candidate(
        self,
        mock_openai,
    ):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("现有章节偏短。" * 120)
        self._save_draft_followup_state(
            handler,
            current_count=1800,
            target_word_count=3000,
            continuation_threshold_count=5000,
        )

        decision = self._classify_canonical_draft_for_stage(
            handler,
            "S4",
            "太单薄了，看看现在多少字再导出",
        )

        self.assertEqual(decision["mode"], "reject")
        self.assertEqual(decision["priority"], "P5_MULTI")
        self.assertIn("拆成", decision["fixed_message"])

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_snapshots_include_canonical_path_for_s4_body_intent(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()

        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        self.assertTrue(
            handler._message_has_report_body_write_intent(self.project_id, "继续写正文", "S4")
        )
        self.assertEqual(
            self._required_write_paths_for_stage(handler, "S4", "继续写正文"),
            {"content/report_draft_v1.md"},
        )

    @mock.patch("backend.chat.OpenAI")
    def test_non_table_s4_phrases_do_not_authorize_canonical_draft_write(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()

        for message in ("写第三章", "继续写吧", "扩写第三章"):
            with self.subTest(message=message):
                handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
                self.assertFalse(
                    handler._message_has_report_body_write_intent(self.project_id, message, "S4")
                )
                self.assertEqual(
                    self._required_write_paths_for_stage(handler, "S4", message),
                    set(),
                )

    @mock.patch("backend.chat.OpenAI")
    def test_report_body_write_intent_ignores_generic_s4_short_continue_prompt(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()

        for assistant_message in (
            "如果需要我继续检查资料，请回复“继续”。",
            "请回复“继续”。",
            "报告正文已经生成。若无问题，请回复“继续”开始审查。",
        ):
            with self.subTest(assistant_message=assistant_message):
                handler._save_conversation(
                    self.project_id,
                    [{"role": "assistant", "content": assistant_message}],
                )
                handler._turn_context = handler._new_turn_context(can_write_non_plan=True)

                self.assertFalse(
                    handler._message_has_report_body_write_intent(self.project_id, "继续", "S4")
                )
                self.assertEqual(
                    self._required_write_paths_for_stage(handler, "S4", "继续"),
                    set(),
                )

    @mock.patch("backend.chat.OpenAI")
    def test_report_body_write_intent_ignores_contextual_short_continue_without_structured_followup_state(
        self,
        mock_openai,
    ):
        del mock_openai
        handler = self._make_handler_with_project()
        handler._save_conversation(
            self.project_id,
            [
                {
                    "role": "assistant",
                    "content": "若无问题，请回复“继续”，我将补全剩余章节。",
                },
                {
                    "role": "assistant",
                    "content": "报告正文仍偏短。若无问题，请回复“继续”开始扩写。",
                }
            ],
        )
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)

        self.assertFalse(handler._message_has_report_body_write_intent(self.project_id, "继续", "S4"))
        self.assertEqual(
            self._required_write_paths_for_stage(handler, "S4", "继续"),
            set(),
        )

    @mock.patch("backend.chat.OpenAI")
    def test_report_body_write_intent_ignores_s4_questions_and_review_transition(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()

        for message in ("现在字数多少？", "开始审查"):
            with self.subTest(message=message):
                handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
                self.assertFalse(
                    handler._message_has_report_body_write_intent(self.project_id, message, "S4")
                )
                self.assertEqual(
                    self._required_write_paths_for_stage(handler, "S4", message),
                    set(),
                )

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_is_not_created_when_non_plan_write_disallowed(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        handler._turn_context = handler._new_turn_context(can_write_non_plan=False)

        self.assertFalse(
            handler._message_has_report_body_write_intent(self.project_id, "先别继续写", "S4")
        )
        self.assertEqual(
            self._required_write_paths_for_stage(
                handler,
                "S4",
                "先别继续写",
                can_write_non_plan=False,
            ),
            set(),
        )

    @mock.patch("backend.chat.OpenAI")
    def test_report_body_write_intent_for_s5_plus_requires_explicit_body_edit(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()

        cases = [
            ("S5", "开始审查", False, False),
            ("S5", "开始第三章质量检查", False, False),
            ("S5", "扩写正文", True, True),
            ("S5", "把报告里 X 改成 Y", True, False),
            ("S6", "把报告里 X 改成 Y", True, False),
            ("S7", "继续写报告正文", True, True),
            ("done", "继续写报告正文", True, True),
            ("S6", "导出可审草稿", False, False),
            ("S7", "归档", False, False),
        ]
        for stage_code, message, expected_intent, expected_required_path in cases:
            with self.subTest(stage_code=stage_code, message=message):
                handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
                self.assertEqual(
                    handler._message_has_report_body_write_intent(self.project_id, message, stage_code),
                    expected_intent,
                )
                expected_paths = {"content/report_draft_v1.md"} if expected_required_path else set()
                self.assertEqual(
                    self._required_write_paths_for_stage(handler, stage_code, message),
                    expected_paths,
                )

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_is_never_created_before_s4(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()

        for stage_code in ("S0", "S1", "S2", "S3"):
            with self.subTest(stage_code=stage_code):
                handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
                self.assertFalse(
                    handler._message_has_report_body_write_intent(self.project_id, "继续写正文", stage_code)
                )
                self.assertEqual(
                    self._required_write_paths_for_stage(handler, stage_code, "继续写正文"),
                    set(),
                )

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_snapshots_use_hash_and_substantive_new_file(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        normalized_path = "content/report_draft_v1.md"
        draft_path = self.project_dir / "content" / "report_draft_v1.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)

        initial = handler._snapshot_project_file(self.project_id, normalized_path)

        self.assertEqual(initial["path"], normalized_path)
        self.assertFalse(initial["exists"])
        self.assertIsNone(initial["sha256"])
        self.assertIsNone(initial["mtime"])
        self.assertEqual(initial["word_count"], 0)

        draft_path.write_text("## 小结\n\n太短", encoding="utf-8")
        satisfied, missing = handler._required_writes_satisfied(self.project_id, {normalized_path: initial})
        self.assertFalse(satisfied)
        self.assertEqual(missing, [normalized_path])

        draft_path.write_text("## 第三章\n\n" + ("正文" * 80), encoding="utf-8")
        satisfied, missing = handler._required_writes_satisfied(self.project_id, {normalized_path: initial})
        self.assertTrue(satisfied)
        self.assertEqual(missing, [])

        changed_initial = handler._snapshot_project_file(self.project_id, normalized_path)
        original_hash = changed_initial["sha256"]
        draft_path.write_text("## 第三章\n\n" + ("更新正文" * 80), encoding="utf-8")
        changed, missing = handler._required_writes_satisfied(
            self.project_id,
            {normalized_path: changed_initial},
        )
        self.assertTrue(changed)
        self.assertEqual(missing, [])
        self.assertNotEqual(
            handler._snapshot_project_file(self.project_id, normalized_path)["sha256"],
            original_hash,
        )

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_rejects_destructive_short_rewrite_for_existing_non_replace(
        self,
        mock_openai,
    ):
        del mock_openai
        handler = self._make_handler_with_project()
        normalized_path = "content/report_draft_v1.md"
        draft_path = self._write_partial_report_draft("既有正文" * 120)
        before = handler._snapshot_project_file(self.project_id, normalized_path)

        draft_path.write_text("# Draft\n\n太短", encoding="utf-8")
        current = handler._snapshot_project_file(self.project_id, normalized_path)
        satisfied, missing = handler._required_writes_satisfied(
            self.project_id,
            {normalized_path: before},
        )

        self.assertNotEqual(current["sha256"], before["sha256"])
        self.assertLess(current["word_count"], before["word_count"])
        self.assertFalse(satisfied)
        self.assertEqual(missing, [normalized_path])

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_tool_rejects_destructive_write_file_before_disk_mutation(
        self,
        mock_openai,
    ):
        del mock_openai
        handler = self._make_handler_with_project()
        draft_path = self._write_partial_report_draft("既有正文" * 120)
        before = draft_path.read_text(encoding="utf-8")
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S4"),
        ):
            snapshots = handler._build_required_write_snapshots(self.project_id, "继续写正文")
        handler._turn_context["required_write_snapshots"] = snapshots

        result = handler._execute_tool(
            self.project_id,
            self._make_write_report_tool_call(content="# Draft\n\n太短"),
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("append_report_draft", result["message"])
        self.assertIn("read_file", result["message"])
        self.assertIn("edit_file", result["message"])
        self.assertNotIn("write_file", result["message"])
        self.assertEqual(draft_path.read_text(encoding="utf-8"), before)

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_tool_rejects_append_like_whole_file_edit_before_disk_mutation(
        self,
        mock_openai,
    ):
        del mock_openai
        handler = self._make_handler_with_project()
        draft_path = self._write_partial_report_draft("既有正文" * 120)
        before = draft_path.read_text(encoding="utf-8")
        append_like_new_content = before.rstrip() + "\n\n## 第二章：策略建议\n\n" + ("新增正文" * 80)
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S4"),
        ):
            handler._turn_context = handler._build_turn_context(
                self.project_id,
                "继续写正文",
            )
            snapshots = handler._build_required_write_snapshots(self.project_id, "继续写正文")
        handler._turn_context["required_write_snapshots"] = snapshots

        read_result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "read_file",
                json.dumps({"file_path": "content/report_draft_v1.md"}, ensure_ascii=False),
            ),
        )
        result = handler._execute_tool(
            self.project_id,
            self._make_edit_report_tool_call(
                old_string=before,
                new_string=append_like_new_content,
            ),
        )

        self.assertEqual(read_result["status"], "success")
        self.assertEqual(result["status"], "error")
        self.assertIn("append_report_draft", result["message"])
        self.assertIn("edit_file", result["message"])
        self.assertEqual(draft_path.read_text(encoding="utf-8"), before)

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_rejects_replace_text_write_file_before_disk_mutation(
        self,
        mock_openai,
    ):
        del mock_openai
        handler = self._make_handler_with_project()
        draft_path = self._write_partial_report_draft("旧结论\n\n原始段落")
        before = draft_path.read_text(encoding="utf-8")
        destructive_content = (
            "# Draft\n\n## 第一章\n\n"
            "新结论\n\n"
            + ("看似完整但不是精确替换的正文。" * 80)
        )
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            snapshots = handler._build_required_write_snapshots(
                self.project_id,
                "把报告里旧结论改成新结论",
            )
        handler._turn_context["required_write_snapshots"] = snapshots

        result = handler._execute_tool(
            self.project_id,
            self._make_write_report_tool_call(content=destructive_content),
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("write_file", result["message"])
        self.assertIn("edit_file", result["message"])
        self.assertEqual(draft_path.read_text(encoding="utf-8"), before)

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_rejects_replace_text_append_report_draft_before_disk_mutation(
        self,
        mock_openai,
    ):
        del mock_openai
        handler = self._make_handler_with_project()
        draft_path = self._write_partial_report_draft("旧结论\n\n原始段落")
        before = draft_path.read_text(encoding="utf-8")
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            snapshots = handler._build_required_write_snapshots(
                self.project_id,
                "把报告里旧结论改成新结论",
            )
        handler._turn_context["required_write_snapshots"] = snapshots

        result = handler._execute_tool(
            self.project_id,
            self._make_append_report_tool_call(
                content="## 追加说明\n\n" + ("新结论" * 80),
            ),
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("append_report_draft", result["message"])
        self.assertIn("edit_file", result["message"])
        self.assertEqual(draft_path.read_text(encoding="utf-8"), before)

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_rejects_replace_text_oversized_edit_file_before_disk_mutation(
        self,
        mock_openai,
    ):
        del mock_openai
        handler = self._make_handler_with_project()
        draft_path = self._write_partial_report_draft(
            "旧结论\n\n"
            + "\n".join(f"第 {index} 条原始段落包含稳定上下文。" for index in range(180))
        )
        before = draft_path.read_text(encoding="utf-8")
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            snapshots = handler._build_required_write_snapshots(
                self.project_id,
                "把报告里旧结论改成新结论",
            )
        handler._turn_context["required_write_snapshots"] = snapshots
        self._read_file_for_turn(handler, "content/report_draft_v1.md")

        result = handler._execute_tool(
            self.project_id,
            self._make_edit_report_tool_call(
                old_string=before,
                new_string=before.replace("旧结论", "新结论", 1),
            ),
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("old_string", result["message"])
        self.assertIn("局部", result["message"])
        self.assertEqual(draft_path.read_text(encoding="utf-8"), before)

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_rejects_replace_text_short_full_draft_edit_before_disk_mutation(
        self,
        mock_openai,
    ):
        del mock_openai
        handler = self._make_handler_with_project()
        draft_path = self._write_partial_report_draft(
            "旧结论\n\n"
            + "\n".join(f"短草稿第 {index} 段仍需保留。" for index in range(35))
        )
        before = draft_path.read_text(encoding="utf-8")
        self.assertLess(len(before), 1000)
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            snapshots = handler._build_required_write_snapshots(
                self.project_id,
                "把报告里旧结论改成新结论",
            )
        handler._turn_context["required_write_snapshots"] = snapshots
        self._read_file_for_turn(handler, "content/report_draft_v1.md")

        result = handler._execute_tool(
            self.project_id,
            self._make_edit_report_tool_call(
                old_string=before,
                new_string="新结论",
            ),
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("old_string", result["message"])
        self.assertIn("局部", result["message"])
        self.assertEqual(draft_path.read_text(encoding="utf-8"), before)

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_rejects_replace_text_short_full_draft_edit_with_large_locality_before_disk_mutation(
        self,
        mock_openai,
    ):
        del mock_openai
        handler = self._make_handler_with_project()
        old_string = (
            "本段承接前文，说明渠道转型已经进入深水区，管理层需要先统一增长口径，再配置样板市场资源。"
            "旧结论仍停留在经验判断，没有区分存量客户维护、新增客户获取和区域伙伴协同，"
            "也没有解释组织能力、激励机制、数据口径之间的缺口。"
            "该段还把短期促销误写成长期战略，容易误导管理层判断，并弱化了总部与一线之间的责任边界。"
            "因此需要只替换结论词，不得吞掉上下文，避免报告在审查时丢失证据链。"
        )
        draft_path = self._write_partial_report_draft(
            "# 报告草稿\n\n"
            "第一段说明项目背景、访谈范围、样本口径和数据来源，保留客户当前战略语境，"
            "并引出后续渠道诊断。这里还记录了董事会关注的问题、历史增长曲线和关键假设，"
            "作为后文判断依据。\n\n"
            f"{old_string}\n\n"
            "第三段继续讨论落地节奏、组织分工、预算安排、风险预案和后续审查安排，"
            "保持原有逻辑，不应被本次替换影响。最后一段保留复盘口径和下一步资料清单。"
        )
        before = draft_path.read_text(encoding="utf-8")
        self.assertGreaterEqual(len(before), 330)
        self.assertLessEqual(len(before), 370)
        self.assertGreaterEqual(len(old_string), 180)
        self.assertLessEqual(len(old_string), 200)
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            snapshots = handler._build_required_write_snapshots(
                self.project_id,
                "把报告里旧结论改成新结论",
            )
        handler._turn_context["required_write_snapshots"] = snapshots
        self._read_file_for_turn(handler, "content/report_draft_v1.md")

        result = handler._execute_tool(
            self.project_id,
            self._make_edit_report_tool_call(
                old_string=old_string,
                new_string="新结论",
            ),
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("old_string", result["message"])
        self.assertIn("局部", result["message"])
        self.assertEqual(draft_path.read_text(encoding="utf-8"), before)

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_rejects_replace_text_oversized_new_string_before_disk_mutation(
        self,
        mock_openai,
    ):
        del mock_openai
        handler = self._make_handler_with_project()
        draft_path = self._write_partial_report_draft(
            "旧结论\n\n"
            + "\n".join(f"中等草稿第 {index} 段需要完整保留。" for index in range(20))
        )
        before = draft_path.read_text(encoding="utf-8")
        unrelated_content = "\n".join(
            f"无关扩写段落 {index}，不属于本次局部替换，应被门禁拦截。"
            for index in range(30)
        )
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            snapshots = handler._build_required_write_snapshots(
                self.project_id,
                "把报告里旧结论改成新结论",
            )
        handler._turn_context["required_write_snapshots"] = snapshots
        self._read_file_for_turn(handler, "content/report_draft_v1.md")

        result = handler._execute_tool(
            self.project_id,
            self._make_edit_report_tool_call(
                old_string="旧结论",
                new_string=f"新结论\n\n{unrelated_content}",
            ),
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("new_string", result["message"])
        self.assertIn("局部", result["message"])
        self.assertEqual(draft_path.read_text(encoding="utf-8"), before)

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_accepts_replace_text_local_edit_file(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        draft_path = self._write_partial_report_draft("旧结论\n\n原始段落")
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            snapshots = handler._build_required_write_snapshots(
                self.project_id,
                "把报告里旧结论改成新结论",
            )
        handler._turn_context["required_write_snapshots"] = snapshots
        self._read_file_for_turn(handler, "content/report_draft_v1.md")

        result = handler._execute_tool(
            self.project_id,
            self._make_edit_report_tool_call(
                old_string="旧结论",
                new_string="新结论",
            ),
        )

        updated = draft_path.read_text(encoding="utf-8")
        self.assertEqual(result["status"], "success")
        self.assertIn("新结论", updated)
        self.assertNotIn("旧结论", updated)
        self.assertIn("原始段落", updated)

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_accepts_localized_replace_when_same_old_phrase_still_exists_elsewhere(
        self,
        mock_openai,
    ):
        del mock_openai
        handler = self._make_handler_with_project()
        normalized_path = "content/report_draft_v1.md"
        draft_path = self.project_dir / "content" / "report_draft_v1.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        targeted_old = "旧结论\n\n第一段保持上下文。"
        targeted_new = "新结论\n\n第一段保持上下文。"
        untouched_old = "旧结论\n\n第二段仍保留原词。"
        draft_path.write_text(
            "# 报告草稿\n\n"
            f"{targeted_old}\n\n"
            "## 第一章\n\n章节正文。\n\n"
            f"{untouched_old}",
            encoding="utf-8",
        )
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            snapshots = handler._build_required_write_snapshots(
                self.project_id,
                "把报告里旧结论改成新结论",
            )
        handler._turn_context["required_write_snapshots"] = snapshots
        self._read_file_for_turn(handler, "content/report_draft_v1.md")

        result = handler._execute_tool(
            self.project_id,
            self._make_edit_report_tool_call(
                old_string=targeted_old,
                new_string=targeted_new,
            ),
        )
        successful_write_events = {
            normalized_path: [
                {
                    "path": normalized_path,
                    "tool": "edit_file",
                    "arguments": {
                        "file_path": normalized_path,
                        "old_string": targeted_old,
                        "new_string": targeted_new,
                    },
                    "raw_arguments": "",
                }
            ]
        }
        satisfied, missing = handler._required_writes_satisfied(
            self.project_id,
            snapshots,
            successful_write_events,
        )
        updated = draft_path.read_text(encoding="utf-8")

        self.assertEqual(result["status"], "success")
        self.assertTrue(satisfied)
        self.assertEqual(missing, [])
        self.assertIn(targeted_new, updated)
        self.assertIn(untouched_old, updated)

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_accepts_existing_draft_append_growth(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        normalized_path = "content/report_draft_v1.md"
        draft_path = self._write_partial_report_draft("既有正文" * 80)
        before = handler._snapshot_project_file(self.project_id, normalized_path)

        draft_path.write_text(
            draft_path.read_text(encoding="utf-8")
            + "\n\n## 第二章\n\n"
            + ("新增正文" * 80),
            encoding="utf-8",
        )
        current = handler._snapshot_project_file(self.project_id, normalized_path)
        satisfied, missing = handler._required_writes_satisfied(
            self.project_id,
            {normalized_path: before},
        )

        self.assertNotEqual(current["sha256"], before["sha256"])
        self.assertGreater(current["word_count"], before["word_count"])
        self.assertTrue(satisfied)
        self.assertEqual(missing, [])

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_accepts_existing_draft_same_word_count_hash_change(
        self,
        mock_openai,
    ):
        del mock_openai
        handler = self._make_handler_with_project()
        normalized_path = "content/report_draft_v1.md"
        draft_path = self.project_dir / "content" / "report_draft_v1.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text("# Draft\n\n## 第一章\n\n" + ("甲" * 120), encoding="utf-8")
        before = handler._snapshot_project_file(self.project_id, normalized_path)

        draft_path.write_text("# Draft\n\n## 第一章\n\n" + ("乙" * 120), encoding="utf-8")
        current = handler._snapshot_project_file(self.project_id, normalized_path)
        satisfied, missing = handler._required_writes_satisfied(
            self.project_id,
            {normalized_path: before},
        )

        self.assertNotEqual(current["sha256"], before["sha256"])
        self.assertEqual(current["word_count"], before["word_count"])
        self.assertTrue(satisfied)
        self.assertEqual(missing, [])

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_accepts_shorter_full_draft_rewrite_via_edit_file(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        normalized_path = "content/report_draft_v1.md"
        draft_path = self.project_dir / "content" / "report_draft_v1.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(
            "# 报告草稿\n\n"
            "## 执行摘要\n\n"
            + ("长摘要" * 120)
            + "\n\n## 第一章\n\n"
            + ("第一章展开说明" * 160),
            encoding="utf-8",
        )
        before_text = draft_path.read_text(encoding="utf-8")

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            handler._turn_context = handler._build_turn_context(
                self.project_id,
                "请全文重写这份报告正文",
            )
            snapshots = handler._build_required_write_snapshots(
                self.project_id,
                "请全文重写这份报告正文",
            )
        handler._turn_context["required_write_snapshots"] = snapshots
        self._read_file_for_turn(handler, "content/report_draft_v1.md")

        rewritten = (
            "# 报告草稿\n\n"
            "## 执行摘要\n\n新的精简摘要。\n\n"
            "## 第一章\n\n"
            + ("重写后的第一章聚焦关键结论。" * 40)
        )
        result = handler._execute_tool(
            self.project_id,
            self._make_edit_report_tool_call(
                old_string=before_text,
                new_string=rewritten,
            ),
        )
        current = handler._snapshot_project_file(self.project_id, normalized_path)
        satisfied, missing = handler._required_writes_satisfied(
            self.project_id,
            snapshots,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(draft_path.read_text(encoding="utf-8"), rewritten)
        self.assertLess(current["word_count"], int(snapshots[normalized_path]["word_count"]))
        self.assertTrue(satisfied)
        self.assertEqual(missing, [])

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_accepts_shorter_section_rewrite_via_edit_file(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        normalized_path = "content/report_draft_v1.md"
        draft_path = self.project_dir / "content" / "report_draft_v1.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        long_summary = "## 执行摘要\n\n" + ("这里是很长的执行摘要说明。" * 80)
        body = "## 第一章\n\n" + ("第一章保留原有详细分析。" * 120)
        draft_path.write_text(
            "# 报告草稿\n\n" + long_summary + "\n\n" + body,
            encoding="utf-8",
        )

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            handler._turn_context = handler._build_turn_context(
                self.project_id,
                "把执行摘要改强一点",
            )
            snapshots = handler._build_required_write_snapshots(
                self.project_id,
                "把执行摘要改强一点",
            )
        handler._turn_context["required_write_snapshots"] = snapshots
        self._read_file_for_turn(handler, "content/report_draft_v1.md")

        shorter_summary = "## 执行摘要\n\n更强但更短的执行摘要。"
        result = handler._execute_tool(
            self.project_id,
            self._make_edit_report_tool_call(
                old_string=long_summary,
                new_string=shorter_summary,
            ),
        )
        current = handler._snapshot_project_file(self.project_id, normalized_path)
        satisfied, missing = handler._required_writes_satisfied(
            self.project_id,
            snapshots,
        )

        self.assertEqual(result["status"], "success")
        updated = draft_path.read_text(encoding="utf-8")
        self.assertIn(shorter_summary, updated)
        self.assertNotIn(long_summary, updated)
        self.assertIn(body, updated)
        self.assertLess(current["word_count"], int(snapshots[normalized_path]["word_count"]))
        self.assertTrue(satisfied)
        self.assertEqual(missing, [])

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_accepts_exact_section_snapshot_with_nested_subheads(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        normalized_path = "content/report_draft_v1.md"
        draft_path = self.project_dir / "content" / "report_draft_v1.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        section = (
            "## 执行摘要\n\n"
            "先说明总体判断。\n\n"
            "### 关键发现\n\n"
            "这里是细化发现。\n\n"
            "### 行动建议\n\n"
            "这里是细化建议。"
        )
        body = "## 第一章\n\n" + ("第一章保留原有详细分析。" * 60)
        draft_path.write_text(
            "# 报告草稿\n\n" + section + "\n\n" + body,
            encoding="utf-8",
        )

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            handler._turn_context = handler._build_turn_context(
                self.project_id,
                "把执行摘要改强一点",
            )
            snapshots = handler._build_required_write_snapshots(
                self.project_id,
                "把执行摘要改强一点",
            )
        handler._turn_context["required_write_snapshots"] = snapshots
        self._read_file_for_turn(handler, "content/report_draft_v1.md")

        target_section = snapshots[normalized_path]["rewrite_target_snapshot"]
        rewritten_section = (
            "## 执行摘要\n\n"
            "新的总体判断。\n\n"
            "### 关键发现\n\n"
            "新的细化发现。\n\n"
            "### 行动建议\n\n"
            "新的细化建议。"
        )
        result = handler._execute_tool(
            self.project_id,
            self._make_edit_report_tool_call(
                old_string=target_section,
                new_string=rewritten_section,
            ),
        )
        current = handler._snapshot_project_file(self.project_id, normalized_path)
        satisfied, missing = handler._required_writes_satisfied(
            self.project_id,
            snapshots,
        )

        self.assertEqual(result["status"], "success")
        updated = draft_path.read_text(encoding="utf-8")
        self.assertIn(rewritten_section, updated)
        self.assertIn(body, updated)
        self.assertTrue(satisfied)
        self.assertEqual(missing, [])
        self.assertGreater(current["word_count"], 0)

    @mock.patch("backend.chat.OpenAI")
    def test_section_rewrite_request_rejects_full_draft_or_multi_section_new_string_with_exact_old_snapshot(
        self,
        mock_openai,
    ):
        del mock_openai
        payloads = {
            "full_draft": (
                "# 报告草稿\n\n"
                "## 执行摘要\n\n新的整篇执行摘要。\n\n"
                "## 第一章\n\n新的第一章内容。\n\n"
                "## 第二章\n\n新的第二章内容。"
            ),
            "multi_section": (
                "## 执行摘要\n\n更强的章节摘要。\n\n"
                "## 第一章\n\n这一整章不该出现在章节级改写里。"
            ),
        }

        for payload_name, new_payload in payloads.items():
            with self.subTest(payload_name=payload_name):
                handler = self._make_handler_with_project()
                normalized_path = "content/report_draft_v1.md"
                draft_path = self.project_dir / "content" / "report_draft_v1.md"
                draft_path.parent.mkdir(parents=True, exist_ok=True)
                section = (
                    "## 执行摘要\n\n"
                    "先说明总体判断。\n\n"
                    "### 关键发现\n\n"
                    "这里是细化发现。\n\n"
                    "### 行动建议\n\n"
                    "这里是细化建议。"
                )
                chapter_one = "## 第一章\n\n" + ("第一章保留原有详细分析。" * 60)
                chapter_two = "## 第二章\n\n" + ("第二章保留原有详细分析。" * 40)
                original = "# 报告草稿\n\n" + section + "\n\n" + chapter_one + "\n\n" + chapter_two
                draft_path.write_text(original, encoding="utf-8")

                with mock.patch.object(
                    handler.skill_engine,
                    "_infer_stage_state",
                    return_value=self._mock_stage_state("S5"),
                ):
                    handler._turn_context = handler._build_turn_context(
                        self.project_id,
                        "把执行摘要改强一点",
                    )
                    snapshots = handler._build_required_write_snapshots(
                        self.project_id,
                        "把执行摘要改强一点",
                    )
                handler._turn_context["required_write_snapshots"] = snapshots
                self._read_file_for_turn(handler, normalized_path)

                target_section = snapshots[normalized_path]["rewrite_target_snapshot"]
                result = handler._execute_tool(
                    self.project_id,
                    self._make_edit_report_tool_call(
                        old_string=target_section,
                        new_string=new_payload,
                    ),
                )

                self.assertEqual(result["status"], "error")
                self.assertIn("new_string", result["message"])
                self.assertIn("目标章节", result["message"])
                self.assertIn("局部范围", result["message"])
                self.assertEqual(draft_path.read_text(encoding="utf-8"), original)

    @mock.patch("backend.chat.OpenAI")
    def test_section_rewrite_request_rejects_whole_file_edit_file_and_preserves_file(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        draft_path = self.project_dir / "content" / "report_draft_v1.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        original = (
            "# 报告草稿\n\n"
            "## 执行摘要\n\n"
            + ("这里是很长的执行摘要说明。" * 60)
            + "\n\n## 第一章\n\n"
            + ("第一章保留原有详细分析。" * 80)
        )
        draft_path.write_text(original, encoding="utf-8")

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            handler._turn_context = handler._build_turn_context(
                self.project_id,
                "把执行摘要改强一点",
            )
            snapshots = handler._build_required_write_snapshots(
                self.project_id,
                "把执行摘要改强一点",
            )
        handler._turn_context["required_write_snapshots"] = snapshots
        self._read_file_for_turn(handler, "content/report_draft_v1.md")

        result = handler._execute_tool(
            self.project_id,
            self._make_edit_report_tool_call(
                old_string=original,
                new_string=original.replace("这里是很长的执行摘要说明。", "更短摘要。"),
            ),
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("目标章节", result["message"])
        self.assertEqual(draft_path.read_text(encoding="utf-8"), original)

    @mock.patch("backend.chat.OpenAI")
    def test_section_rewrite_request_rejects_append_report_draft_and_preserves_file(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        draft_path = self.project_dir / "content" / "report_draft_v1.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        original = (
            "# 报告草稿\n\n"
            "## 执行摘要\n\n"
            + ("这里是很长的执行摘要说明。" * 60)
            + "\n\n## 第一章\n\n"
            + ("第一章保留原有详细分析。" * 80)
        )
        draft_path.write_text(original, encoding="utf-8")

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            handler._turn_context = handler._build_turn_context(
                self.project_id,
                "把第一章改强一点",
            )
            snapshots = handler._build_required_write_snapshots(
                self.project_id,
                "把第一章改强一点",
            )
        handler._turn_context["required_write_snapshots"] = snapshots

        result = handler._execute_tool(
            self.project_id,
            self._make_append_report_tool_call(
                content="## 第一章\n\n" + ("新的补写内容。" * 80),
            ),
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("append_report_draft", result["message"])
        self.assertIn("edit_file", result["message"])
        self.assertEqual(draft_path.read_text(encoding="utf-8"), original)

    @mock.patch("backend.chat.OpenAI")
    def test_full_draft_rewrite_request_rejects_partial_edit_file_and_preserves_file(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        draft_path = self.project_dir / "content" / "report_draft_v1.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        original = (
            "# 报告草稿\n\n"
            "## 执行摘要\n\n"
            + ("执行摘要原文。" * 60)
            + "\n\n## 第一章\n\n"
            + ("第一章原文。" * 80)
        )
        draft_path.write_text(original, encoding="utf-8")
        partial_old_string = "## 执行摘要\n\n" + ("执行摘要原文。" * 60)

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            handler._turn_context = handler._build_turn_context(
                self.project_id,
                "请全文重写这份报告正文",
            )
            snapshots = handler._build_required_write_snapshots(
                self.project_id,
                "请全文重写这份报告正文",
            )
        handler._turn_context["required_write_snapshots"] = snapshots
        self._read_file_for_turn(handler, "content/report_draft_v1.md")

        result = handler._execute_tool(
            self.project_id,
            self._make_edit_report_tool_call(
                old_string=partial_old_string,
                new_string="## 执行摘要\n\n新的局部摘要。",
            ),
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("整份旧稿", result["message"])
        self.assertEqual(draft_path.read_text(encoding="utf-8"), original)

    @mock.patch("backend.chat.OpenAI")
    def test_full_draft_rewrite_request_rejects_append_report_draft_and_preserves_file(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        draft_path = self.project_dir / "content" / "report_draft_v1.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        original = (
            "# 报告草稿\n\n"
            "## 执行摘要\n\n"
            + ("执行摘要原文。" * 60)
            + "\n\n## 第一章\n\n"
            + ("第一章原文。" * 80)
        )
        draft_path.write_text(original, encoding="utf-8")

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            handler._turn_context = handler._build_turn_context(
                self.project_id,
                "请全文重写这份报告正文",
            )
            snapshots = handler._build_required_write_snapshots(
                self.project_id,
                "请全文重写这份报告正文",
            )
        handler._turn_context["required_write_snapshots"] = snapshots

        result = handler._execute_tool(
            self.project_id,
            self._make_append_report_tool_call(
                content="## 新版报告\n\n" + ("重写后的完整草稿。" * 80),
            ),
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("append_report_draft", result["message"])
        self.assertIn("edit_file", result["message"])
        self.assertEqual(draft_path.read_text(encoding="utf-8"), original)

    @mock.patch("backend.chat.OpenAI")
    def test_full_draft_rewrite_missing_old_string_guidance_uses_read_then_edit(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("既有正文" * 120)

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            handler._turn_context = handler._build_turn_context(
                self.project_id,
                "请全文重写这份报告正文",
            )

        result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "edit_file",
                json.dumps(
                    {
                        "file_path": "content/report_draft_v1.md",
                        "old_string": "",
                        "new_string": "# 新草稿\n\n重写版本",
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("read_file", result["message"])
        self.assertIn("edit_file", result["message"])
        self.assertNotIn("write_file", result["message"])

    @mock.patch("backend.chat.OpenAI")
    def test_multi_section_rewrite_request_rejects_one_section_edit_and_preserves_file(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        draft_path = self.project_dir / "content" / "report_draft_v1.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        exec_summary = "## 执行摘要\n\n" + ("执行摘要原文。" * 30)
        chapter_one = "## 第一章\n\n" + ("第一章原文。" * 40)
        original = "# 报告草稿\n\n" + exec_summary + "\n\n" + chapter_one
        draft_path.write_text(original, encoding="utf-8")

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            handler._turn_context = handler._build_turn_context(
                self.project_id,
                "把执行摘要和第一章改强一点",
            )
            snapshots = handler._build_required_write_snapshots(
                self.project_id,
                "把执行摘要和第一章改强一点",
            )
        handler._turn_context["required_write_snapshots"] = snapshots
        self._read_file_for_turn(handler, "content/report_draft_v1.md")

        decision = handler._turn_context["canonical_draft_decision"]
        result = handler._execute_tool(
            self.project_id,
            self._make_edit_report_tool_call(
                old_string=exec_summary,
                new_string="## 执行摘要\n\n更强的单章节摘要。",
            ),
        )

        self.assertEqual(decision["expected_tool_family"], "edit_file")
        self.assertEqual(decision["required_edit_scope"], "full_draft")
        self.assertEqual(result["status"], "error")
        self.assertIn("整份旧稿", result["message"])
        self.assertEqual(draft_path.read_text(encoding="utf-8"), original)

    @mock.patch("backend.chat.OpenAI")
    def test_full_rewrite_retry_and_error_messages_never_recommend_write_file(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("既有正文" * 120)

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            handler._turn_context = handler._build_turn_context(
                self.project_id,
                "请全文重写这份报告正文",
            )
            snapshots = handler._build_required_write_snapshots(
                self.project_id,
                "请全文重写这份报告正文",
            )
        handler._turn_context["required_write_snapshots"] = snapshots

        feedback = handler._build_required_write_feedback(["content/report_draft_v1.md"])
        failure = handler._build_required_write_failure_message(["content/report_draft_v1.md"])
        prewrite_error = handler._validate_required_report_draft_prewrite(
            self.project_id,
            "content/report_draft_v1.md",
            "# 新草稿\n\n更短版本",
            source_tool_name="write_file",
            source_tool_args={"file_path": "content/report_draft_v1.md", "content": "# 新草稿\n\n更短版本"},
        )

        self.assertIn("read_file", feedback)
        self.assertIn("edit_file", feedback)
        self.assertNotIn("write_file", feedback)
        self.assertIn("read_file", failure)
        self.assertIn("edit_file", failure)
        self.assertNotIn("write_file", failure)
        self.assertIsInstance(prewrite_error, str)
        self.assertIn("read_file", prewrite_error)
        self.assertIn("edit_file", prewrite_error)
        self.assertNotIn("write_file", prewrite_error)

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_snapshot_carries_inline_replacement_intent(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("旧结论\n\n原始段落")
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)

        for message in (
            "把报告里旧结论改成新结论",
            "把正文中旧结论改为新结论",
            "把报告里的旧结论替换成新结论",
            "把报告里旧结论，换成新结论",
            "把报告里旧结论 换成 新结论",
        ):
            with self.subTest(message=message), mock.patch.object(
                handler.skill_engine,
                "_infer_stage_state",
                return_value=self._mock_stage_state("S5"),
            ):
                snapshots = handler._build_required_write_snapshots(self.project_id, message)

            snapshot = snapshots["content/report_draft_v1.md"]
            self.assertEqual(snapshot["intent_kind"], "replace_text")
            self.assertEqual(snapshot["old_text"], "旧结论")
            self.assertEqual(snapshot["new_text"], "新结论")

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_rejects_append_that_leaves_replaced_text_in_place(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        draft_path = self._write_partial_report_draft("旧结论\n\n原始段落")
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            snapshots = handler._build_required_write_snapshots(
                self.project_id,
                "把报告里旧结论改成新结论",
            )

        draft_path.write_text(
            draft_path.read_text(encoding="utf-8") + "\n\n## 追加说明\n\n" + ("新结论" * 80),
            encoding="utf-8",
        )

        satisfied, missing = handler._required_writes_satisfied(
            self.project_id,
            snapshots,
            {"content/report_draft_v1.md": {"append_report_draft"}},
        )

        self.assertFalse(satisfied)
        self.assertEqual(missing, ["content/report_draft_v1.md"])

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_rejects_destructive_tiny_rewrite_for_replacement(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        draft_path = self._write_partial_report_draft("旧结论\n\n原始段落")
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            snapshots = handler._build_required_write_snapshots(
                self.project_id,
                "把报告里旧结论改成新结论",
            )

        draft_path.write_text("新结论", encoding="utf-8")

        satisfied, missing = handler._required_writes_satisfied(
            self.project_id,
            snapshots,
            {"content/report_draft_v1.md": {"write_file"}},
        )

        self.assertFalse(satisfied)
        self.assertEqual(missing, ["content/report_draft_v1.md"])

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_rejects_alias_destructive_write_after_valid_edit(self, mock_openai):
        handler = self._make_handler_with_project()
        handler.skill_engine._save_stage_checkpoint(self.project_dir, "outline_confirmed_at")
        handler.skill_engine._save_stage_checkpoint(self.project_dir, "review_started_at")
        draft_path = self._write_partial_report_draft("旧结论\n\n原始段落")
        final_message = "已完成替换。"
        mock_openai.return_value.chat.completions.create.side_effect = [
            self._make_non_stream_tool_response(
                self._make_read_tool_call("content/report_draft_v1.md")
            ),
            self._make_non_stream_tool_response(self._make_edit_report_tool_call()),
            self._make_non_stream_tool_response(
                self._make_write_report_tool_call(
                    file_path="content/./report_draft_v1.md",
                    content="新结论",
                    call_id="call-alias-destructive-write",
                )
            ),
            self._make_non_stream_response(final_message),
            self._make_non_stream_response(final_message),
            self._make_non_stream_response(final_message),
        ]

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            result = handler.chat(
                self.project_id,
                "把报告里旧结论改成新结论",
                max_iterations=6,
            )

        saved = self._read_saved_conversation()
        final_request_messages = mock_openai.return_value.chat.completions.create.call_args_list[3].kwargs["messages"]
        tool_results = [
            json.loads(message["content"])
            for message in final_request_messages
            if message.get("role") == "tool"
        ]
        updated = draft_path.read_text(encoding="utf-8")

        self.assertTrue(
            any(
                item.get("status") == "error"
                and "write_file" in item.get("message", "")
                for item in tool_results
            )
        )
        self.assertIn(final_message, result["content"])
        self.assertIn(result["content"], saved[-1]["content"])
        self.assertIn("<!-- tool-log", saved[-1]["content"])
        self.assertIn("新结论", updated)
        self.assertIn("原始段落", updated)
        self.assertNotIn("旧结论", updated)
        self.assertNotEqual(updated, "新结论")
        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 4)

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_punctuation_replace_intent_rejects_append_only(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        draft_path = self._write_partial_report_draft("旧结论\n\n原始段落")
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            snapshots = handler._build_required_write_snapshots(
                self.project_id,
                "把报告里旧结论，换成新结论",
            )

        snapshot = snapshots["content/report_draft_v1.md"]
        self.assertEqual(snapshot["intent_kind"], "replace_text")
        self.assertEqual(snapshot["old_text"], "旧结论")
        self.assertEqual(snapshot["new_text"], "新结论")

        draft_path.write_text(
            draft_path.read_text(encoding="utf-8") + "\n\n## 追加说明\n\n" + ("新结论" * 80),
            encoding="utf-8",
        )

        satisfied, missing = handler._required_writes_satisfied(
            self.project_id,
            snapshots,
            {"content/report_draft_v1.md": {"append_report_draft"}},
        )

        self.assertFalse(satisfied)
        self.assertEqual(missing, ["content/report_draft_v1.md"])

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_snapshot_rejects_unvalidated_paths(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        outside_path = self.project_dir.parent / "outside.txt"
        outside_path.write_text("## 外部文件\n\n" + ("外部正文" * 80), encoding="utf-8")

        snapshot = handler._snapshot_project_file(self.project_id, "../outside.txt")

        self.assertEqual(snapshot["path"], "../outside.txt")
        self.assertFalse(snapshot["exists"])
        self.assertIsNone(snapshot["sha256"])
        self.assertEqual(snapshot["word_count"], 0)
        self.assertFalse(
            handler._project_file_has_substantive_required_write(
                self.project_id,
                "../outside.txt",
            )
        )

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_chat_stream_retries_text_only_completion_until_append_tool_mutates_draft(self, mock_openai):
        handler = self._make_handler_with_project()
        draft_path = self._write_partial_report_draft()
        before = draft_path.read_text(encoding="utf-8")
        false_completion = "报告全文已存入 content/report_draft_v1.md。"
        final_message = "已追加第二章正文。"
        mock_openai.return_value.chat.completions.create.side_effect = [
            iter([self._make_chunk(content=false_completion)]),
            iter([self._make_append_report_stream_chunk()]),
            iter([self._make_chunk(content=final_message)]),
        ]

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S4"),
        ):
            events = list(handler.chat_stream(self.project_id, "继续写正文", max_iterations=4))

        content = "".join(event["data"] for event in events if event["type"] == "content")
        tool_messages = [event["data"] for event in events if event["type"] == "tool"]
        saved = self._read_saved_conversation()

        self.assertTrue(
            any("报告正文" in message and "未检测到" in message for message in tool_messages)
        )
        self.assertNotIn(false_completion, content)
        self.assertIn(final_message, content)
        self.assertNotEqual(draft_path.read_text(encoding="utf-8"), before)
        self.assertEqual(saved[-1]["role"], "assistant")
        self.assertIn(final_message, saved[-1]["content"])
        self.assertIn("<!-- tool-log", saved[-1]["content"])
        self.assertNotIn(false_completion, saved[-1]["content"])
        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 3)

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_chat_stream_accepts_append_report_draft_success_without_retry(self, mock_openai):
        handler = self._make_handler_with_project()
        draft_path = self._write_partial_report_draft()
        before = draft_path.read_text(encoding="utf-8")
        final_message = "已追加第二章正文。"
        mock_openai.return_value.chat.completions.create.side_effect = [
            iter([self._make_append_report_stream_chunk()]),
            iter([self._make_chunk(content=final_message)]),
        ]

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S4"),
        ):
            events = list(handler.chat_stream(self.project_id, "继续写正文", max_iterations=3))

        tool_messages = [event["data"] for event in events if event["type"] == "tool"]
        saved = self._read_saved_conversation()

        self.assertFalse(
            any("报告正文" in message and "未检测到" in message for message in tool_messages)
        )
        self.assertNotEqual(draft_path.read_text(encoding="utf-8"), before)
        self.assertIn(final_message, saved[-1]["content"])
        self.assertIn("<!-- tool-log", saved[-1]["content"])
        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 2)

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_chat_stream_allows_s5_start_review_text_only(self, mock_openai):
        handler = self._make_handler_with_project()
        draft_path = self._write_partial_report_draft()
        before = draft_path.read_text(encoding="utf-8")
        final_message = "开始审查，我会按清单检查事实、逻辑和语言。"
        mock_openai.return_value.chat.completions.create.return_value = iter(
            [self._make_chunk(content=final_message)]
        )

        with mock.patch.object(
            handler,
            "_detect_stage_keyword",
            return_value=None,
        ), mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            events = list(handler.chat_stream(self.project_id, "开始审查", max_iterations=2))

        content = "".join(event["data"] for event in events if event["type"] == "content")
        tool_messages = [event["data"] for event in events if event["type"] == "tool"]

        self.assertIn(final_message, content)
        self.assertFalse(
            any("报告正文" in message and "未检测到" in message for message in tool_messages)
        )
        self.assertEqual(draft_path.read_text(encoding="utf-8"), before)
        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 1)

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_chat_stream_retries_s5_text_only_edit_until_edit_file_replaces_target(self, mock_openai):
        handler = self._make_handler_with_project()
        handler.skill_engine._save_stage_checkpoint(self.project_dir, "outline_confirmed_at")
        handler.skill_engine._save_stage_checkpoint(self.project_dir, "review_started_at")
        draft_path = self._write_partial_report_draft("旧结论\n\n原始段落")
        false_completion = "已把报告里的旧结论改成新结论。"
        final_message = "已将报告中的旧结论改成新结论。"
        mock_openai.return_value.chat.completions.create.side_effect = [
            iter([self._make_chunk(content=false_completion)]),
            iter([self._make_read_stream_chunk("content/report_draft_v1.md")]),
            iter([self._make_edit_report_stream_chunk()]),
            iter([self._make_chunk(content=final_message)]),
        ]

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            events = list(
                handler.chat_stream(
                    self.project_id,
                    "把报告里旧结论改成新结论",
                    max_iterations=4,
                )
            )

        content = "".join(event["data"] for event in events if event["type"] == "content")
        updated = draft_path.read_text(encoding="utf-8")
        retry_messages = mock_openai.return_value.chat.completions.create.call_args_list[1].kwargs["messages"]
        retry_feedback = [
            message.get("content", "")
            for message in retry_messages
            if message.get("role") == "user" and "未检测到" in message.get("content", "")
        ]

        self.assertTrue(retry_feedback)
        self.assertIn("edit_file", retry_feedback[-1])
        self.assertIn("read_file", retry_feedback[-1])
        self.assertNotIn("append_report_draft", retry_feedback[-1])
        self.assertNotIn("write_file", retry_feedback[-1])
        self.assertIn(final_message, content)
        self.assertNotIn(false_completion, content)
        self.assertIn("新结论", updated)
        self.assertNotIn("旧结论", updated)
        self.assertNotIn("旧结论\n\n新结论", updated)
        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 4)

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_non_stream_accepts_edit_when_new_text_contains_old_text(self, mock_openai):
        handler = self._make_handler_with_project()
        handler.skill_engine._save_stage_checkpoint(self.project_dir, "outline_confirmed_at")
        handler.skill_engine._save_stage_checkpoint(self.project_dir, "review_started_at")
        draft_path = self._write_partial_report_draft("2024\n\n原始段落")
        final_message = "已将报告中的 2024 改成 2024年。"
        mock_openai.return_value.chat.completions.create.side_effect = [
            self._make_non_stream_tool_response(
                self._make_read_tool_call("content/report_draft_v1.md")
            ),
            self._make_non_stream_tool_response(
                self._make_edit_report_tool_call(old_string="2024", new_string="2024年")
            ),
            self._make_non_stream_response(final_message),
        ]

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            result = handler.chat(
                self.project_id,
                "把报告里2024改成2024年",
                max_iterations=4,
        )

        updated = draft_path.read_text(encoding="utf-8")

        self.assertIn(final_message, result["content"])
        saved = self._read_saved_conversation()
        self.assertIn(final_message, saved[-1]["content"])
        self.assertIn("<!-- tool-log", saved[-1]["content"])
        self.assertIn("2024年", updated)
        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 3)

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_non_stream_retries_text_only_completion_until_append_tool_mutates_draft(self, mock_openai):
        handler = self._make_handler_with_project()
        draft_path = self._write_partial_report_draft()
        before = draft_path.read_text(encoding="utf-8")
        false_completion = "报告全文已存入 content/report_draft_v1.md。"
        final_message = "已追加第二章正文。"
        mock_openai.return_value.chat.completions.create.side_effect = [
            self._make_non_stream_response(false_completion),
            self._make_non_stream_tool_response(self._make_append_report_tool_call()),
            self._make_non_stream_response(final_message),
        ]

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S4"),
        ):
            result = handler.chat(self.project_id, "继续写正文", max_iterations=4)

        self.assertGreaterEqual(
            mock_openai.return_value.chat.completions.create.call_count,
            2,
        )
        retry_messages = mock_openai.return_value.chat.completions.create.call_args_list[1].kwargs["messages"]
        saved = self._read_saved_conversation()

        self.assertTrue(
            any(
                message.get("role") == "user"
                and "append_report_draft" in message.get("content", "")
                and "未检测到" in message.get("content", "")
                for message in retry_messages
            )
        )
        self.assertIn(final_message, result["content"])
        self.assertNotEqual(draft_path.read_text(encoding="utf-8"), before)
        self.assertIn(final_message, saved[-1]["content"])
        self.assertIn("<!-- tool-log", saved[-1]["content"])
        self.assertNotIn(false_completion, saved[-1]["content"])
        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 3)

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_non_stream_blocks_destructive_write_file_before_append_retry(self, mock_openai):
        handler = self._make_handler_with_project()
        draft_path = self._write_partial_report_draft("既有正文" * 80)
        before = draft_path.read_text(encoding="utf-8")
        final_message = "已追加第二章正文。"
        mock_openai.return_value.chat.completions.create.side_effect = [
            self._make_non_stream_tool_response(
                self._make_write_report_tool_call(content="# Draft\n\n太短")
            ),
            self._make_non_stream_tool_response(
                self._make_append_report_tool_call(
                    content="## 第二章：策略建议\n\n" + ("新增正文" * 160),
                )
            ),
            self._make_non_stream_response(final_message),
        ]

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S4"),
        ):
            result = handler.chat(self.project_id, "继续写正文", max_iterations=4)

        retry_messages = mock_openai.return_value.chat.completions.create.call_args_list[1].kwargs["messages"]
        tool_results = [
            json.loads(message["content"])
            for message in retry_messages
            if message.get("role") == "tool"
        ]
        final_draft = draft_path.read_text(encoding="utf-8")
        saved = self._read_saved_conversation()

        self.assertTrue(
            any(
                item.get("status") == "error"
                and "append_report_draft" in item.get("message", "")
                and "read_file" in item.get("message", "")
                and "edit_file" in item.get("message", "")
                and "write_file" not in item.get("message", "")
                for item in tool_results
            )
        )
        self.assertIn(final_message, result["content"])
        self.assertTrue(final_draft.startswith(before.rstrip()))
        self.assertIn("既有正文", final_draft)
        self.assertIn("新增正文", final_draft)
        self.assertIn(final_message, saved[-1]["content"])
        self.assertIn("<!-- tool-log", saved[-1]["content"])
        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 3)

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_non_stream_retries_s5_text_only_edit_until_edit_file_replaces_target(self, mock_openai):
        handler = self._make_handler_with_project()
        handler.skill_engine._save_stage_checkpoint(self.project_dir, "outline_confirmed_at")
        handler.skill_engine._save_stage_checkpoint(self.project_dir, "review_started_at")
        draft_path = self._write_partial_report_draft("旧结论\n\n原始段落")
        false_completion = "已把报告里的旧结论改成新结论。"
        final_message = "已将报告中的旧结论改成新结论。"
        mock_openai.return_value.chat.completions.create.side_effect = [
            self._make_non_stream_response(false_completion),
            self._make_non_stream_tool_response(
                self._make_read_tool_call("content/report_draft_v1.md")
            ),
            self._make_non_stream_tool_response(self._make_edit_report_tool_call()),
            self._make_non_stream_response(final_message),
        ]

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            result = handler.chat(
                self.project_id,
                "把报告里旧结论改成新结论",
                max_iterations=4,
            )

        updated = draft_path.read_text(encoding="utf-8")
        retry_messages = mock_openai.return_value.chat.completions.create.call_args_list[1].kwargs["messages"]
        retry_feedback = [
            message.get("content", "")
            for message in retry_messages
            if message.get("role") == "user" and "未检测到" in message.get("content", "")
        ]
        saved = self._read_saved_conversation()

        self.assertTrue(retry_feedback)
        self.assertIn("edit_file", retry_feedback[-1])
        self.assertIn("read_file", retry_feedback[-1])
        self.assertNotIn("append_report_draft", retry_feedback[-1])
        self.assertNotIn("write_file", retry_feedback[-1])
        self.assertIn(final_message, result["content"])
        self.assertIn(final_message, saved[-1]["content"])
        self.assertIn("<!-- tool-log", saved[-1]["content"])
        self.assertNotIn(false_completion, saved[-1]["content"])
        self.assertIn("新结论", updated)
        self.assertNotIn("旧结论", updated)
        self.assertNotIn("旧结论\n\n新结论", updated)
        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 4)

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_non_stream_rejects_unrelated_edit_then_destructive_write_file_for_replace_text(self, mock_openai):
        handler = self._make_handler_with_project()
        handler.skill_engine._save_stage_checkpoint(self.project_dir, "outline_confirmed_at")
        handler.skill_engine._save_stage_checkpoint(self.project_dir, "review_started_at")
        draft_path = self._write_partial_report_draft("旧结论\n\n原始段落")
        before = draft_path.read_text(encoding="utf-8")
        false_completion = "已把报告里的旧结论改成新结论。"
        destructive_content = "# Draft\n\n" + ("新结论" * 120)
        final_message = "已完成替换。"
        mock_openai.return_value.chat.completions.create.side_effect = [
            self._make_non_stream_response(false_completion),
            self._make_non_stream_tool_response(
                self._make_edit_report_tool_call(
                    old_string="原始段落",
                    new_string="原始段落（无关补充）",
                    call_id="call-unrelated-edit",
                )
            ),
            self._make_non_stream_tool_response(
                self._make_write_report_tool_call(content=destructive_content)
            ),
            self._make_non_stream_response(final_message),
            self._make_non_stream_response(final_message),
        ]

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            result = handler.chat(
                self.project_id,
                "把报告里旧结论改成新结论",
                max_iterations=6,
            )

        saved = self._read_saved_conversation()
        updated = draft_path.read_text(encoding="utf-8")

        self.assertIn("这轮没有检测到报告草稿", result["content"])
        self.assertNotEqual(result["content"], final_message)
        self.assertIn(result["content"], saved[-1]["content"])
        self.assertIn("<!-- tool-log", saved[-1]["content"])
        self.assertIn("旧结论", updated)
        self.assertNotIn("原始段落（无关补充）", updated)
        self.assertNotIn("新结论", updated)
        self.assertEqual(updated, before)
        self.assertNotEqual(updated, destructive_content)
        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 5)

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_non_stream_retries_s5_inline_edit_when_append_report_draft_leaves_old_text(self, mock_openai):
        handler = self._make_handler_with_project()
        handler.skill_engine._save_stage_checkpoint(self.project_dir, "outline_confirmed_at")
        handler.skill_engine._save_stage_checkpoint(self.project_dir, "review_started_at")
        draft_path = self._write_partial_report_draft("旧结论\n\n原始段落")
        false_completion = "已把报告里的旧结论改成新结论。"
        wrong_append_completion = "已追加新结论。"
        final_message = "已将报告中的旧结论改成新结论。"
        mock_openai.return_value.chat.completions.create.side_effect = [
            self._make_non_stream_response(false_completion),
            self._make_non_stream_tool_response(
                self._make_append_report_tool_call(
                    content="## 追加说明\n\n" + ("新结论" * 80),
                )
            ),
            self._make_non_stream_response(wrong_append_completion),
            self._make_non_stream_tool_response(
                self._make_read_tool_call("content/report_draft_v1.md")
            ),
            self._make_non_stream_tool_response(self._make_edit_report_tool_call()),
            self._make_non_stream_response(final_message),
        ]

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            result = handler.chat(
                self.project_id,
                "把报告里旧结论改成新结论",
                max_iterations=6,
            )

        updated = draft_path.read_text(encoding="utf-8")
        post_append_retry_messages = mock_openai.return_value.chat.completions.create.call_args_list[3].kwargs["messages"]
        saved = self._read_saved_conversation()

        self.assertTrue(
            any(
                message.get("role") == "user"
                and "edit_file" in message.get("content", "")
                and "read_file" in message.get("content", "")
                and "append_report_draft" not in message.get("content", "")
                and "write_file" not in message.get("content", "")
                for message in post_append_retry_messages
            )
        )
        self.assertIn(final_message, result["content"])
        self.assertIn(final_message, saved[-1]["content"])
        self.assertIn("<!-- tool-log", saved[-1]["content"])
        self.assertNotIn(false_completion, saved[-1]["content"])
        self.assertNotIn(wrong_append_completion, saved[-1]["content"])
        self.assertIn("新结论", updated)
        self.assertNotIn("旧结论", updated)
        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 6)

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_non_stream_accepts_append_report_draft_success_without_retry(self, mock_openai):
        handler = self._make_handler_with_project()
        draft_path = self._write_partial_report_draft()
        before = draft_path.read_text(encoding="utf-8")
        final_message = "已追加第二章正文。"
        mock_openai.return_value.chat.completions.create.side_effect = [
            self._make_non_stream_tool_response(self._make_append_report_tool_call()),
            self._make_non_stream_response(final_message),
        ]

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S4"),
        ):
            result = handler.chat(self.project_id, "继续写正文", max_iterations=3)

        second_call_messages = mock_openai.return_value.chat.completions.create.call_args_list[1].kwargs["messages"]
        saved = self._read_saved_conversation()

        self.assertFalse(
            any(
                message.get("role") == "user"
                and "append_report_draft" in message.get("content", "")
                and "未检测到" in message.get("content", "")
                for message in second_call_messages
            )
        )
        self.assertIn(final_message, result["content"])
        self.assertNotEqual(draft_path.read_text(encoding="utf-8"), before)
        self.assertIn(final_message, saved[-1]["content"])
        self.assertIn("<!-- tool-log", saved[-1]["content"])
        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 2)

    @mock.patch("backend.chat.OpenAI")
    def test_chat_persists_followup_state_from_structured_under_target_report_and_p8_uses_real_runtime_state(
        self,
        mock_openai,
    ):
        handler = self._make_handler_with_project()
        draft_path = self._write_partial_report_draft("现有章节偏短。" * 120)
        before = draft_path.read_text(encoding="utf-8")
        initial_count = handler._snapshot_project_file(
            self.project_id,
            "content/report_draft_v1.md",
        )["word_count"]
        state_path = Path(handler._get_conversation_state_path(self.project_id))
        mock_openai.return_value.chat.completions.create.side_effect = [
            self._make_non_stream_response("当前正文约 1800/3000 字。"),
            self._make_non_stream_tool_response(
                self._make_append_report_tool_call(
                    content="## 第二章：策略建议\n\n" + ("新增正文" * 80),
                )
            ),
            self._make_non_stream_response("已追加第二章正文。"),
        ]

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S4"),
        ):
            first = handler.chat(self.project_id, "看看现在多少字", max_iterations=2)
            first_state = json.loads(state_path.read_text(encoding="utf-8"))["draft_followup_state"]
            second = handler.chat(
                self.project_id,
                "目标5000字喔？而且每章现在都太单薄了",
                max_iterations=4,
            )

        self.assertIn("1800/3000", first["content"])
        self.assertIsNotNone(first_state)
        self.assertTrue(first_state["reported_under_target"])
        self.assertFalse(first_state["asked_continue_expand"])
        self.assertEqual(first_state["current_count"], initial_count)
        self.assertEqual(first_state["target_word_count"], 3000)
        self.assertIn("已追加第二章正文。", second["content"])
        self.assertNotEqual(draft_path.read_text(encoding="utf-8"), before)
        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 3)

    @mock.patch("backend.chat.OpenAI")
    def test_draft_followup_state_defaults_to_null_and_missing_field_loads_as_null(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self.assertIsNone(handler._empty_conversation_state()["draft_followup_state"])

        state_path = Path(handler._get_conversation_state_path(self.project_id))
        state_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "events": [],
                    "memory_entries": [],
                    "compact_state": None,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        loaded = handler._load_conversation_state(self.project_id)

        self.assertIn("draft_followup_state", loaded)
        self.assertIsNone(loaded["draft_followup_state"])

    @mock.patch("backend.chat.OpenAI")
    def test_persist_draft_followup_state_does_not_parse_assistant_text_without_structured_flags(
        self,
        mock_openai,
    ):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("现有章节偏短。" * 120)
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)

        handler._persist_draft_followup_state_for_turn(
            self.project_id,
            "当前 1800/3000 字，仍需继续补全。要我继续扩写正文吗？",
            user_message="看看现在多少字",
        )
        saved = handler._load_conversation_state(self.project_id)["draft_followup_state"]

        self.assertIsNone(saved)

    @mock.patch("backend.chat.OpenAI")
    def test_persist_draft_followup_state_uses_structured_turn_flags(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("现有章节偏短。" * 120)
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        handler._turn_context["draft_followup_flags"] = {
            "reported_under_target": True,
            "asked_continue_expand": True,
            "continuation_threshold_count": None,
        }

        handler._persist_draft_followup_state_for_turn(
            self.project_id,
            "普通说明，不带旧版提示关键词。",
        )
        saved = handler._load_conversation_state(self.project_id)["draft_followup_state"]

        self.assertIsNotNone(saved)
        self.assertTrue(saved["reported_under_target"])
        self.assertTrue(saved["asked_continue_expand"])
        self.assertIsNone(saved["continuation_threshold_count"])

    @mock.patch("backend.chat.OpenAI")
    def test_persist_draft_followup_state_uses_under_target_report_turn_data(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("现有章节偏短。" * 120)

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S4"),
        ):
            handler._turn_context = handler._build_turn_context(
                self.project_id,
                "看看现在多少字",
            )

        handler._persist_draft_followup_state_for_turn(
            self.project_id,
            "普通汇报，不带旧版提示关键词。",
            user_message="看看现在多少字",
        )
        saved = handler._load_conversation_state(self.project_id)["draft_followup_state"]

        self.assertIsNotNone(saved)
        self.assertTrue(saved["reported_under_target"])
        self.assertFalse(saved["asked_continue_expand"])
        self.assertIsNone(saved["continuation_threshold_count"])

    @mock.patch("backend.chat.OpenAI")
    def test_persist_draft_followup_state_uses_canonical_mutation_progress_when_append_still_under_target(
        self,
        mock_openai,
    ):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("现有章节偏短。" * 120)

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S4"),
        ):
            handler._turn_context = handler._build_turn_context(
                self.project_id,
                "继续写正文",
            )

        result = handler._execute_tool(
            self.project_id,
            self._make_append_report_tool_call(
                content="## 第二章：策略建议\n\n" + ("新增正文" * 40),
            ),
        )
        handler._persist_draft_followup_state_for_turn(
            self.project_id,
            "已追加第二章正文。",
            user_message="继续写正文",
        )
        saved = handler._load_conversation_state(self.project_id)["draft_followup_state"]

        self.assertEqual(result["status"], "success")
        self.assertIsNotNone(saved)
        self.assertTrue(saved["reported_under_target"])
        self.assertFalse(saved["asked_continue_expand"])
        self.assertIsNone(saved["continuation_threshold_count"])

    @mock.patch("backend.chat.OpenAI")
    def test_persist_draft_followup_state_clears_when_canonical_mutation_reaches_target(
        self,
        mock_openai,
    ):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("现有章节偏短。" * 120)

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S4"),
        ):
            handler._turn_context = handler._build_turn_context(
                self.project_id,
                "继续写正文",
            )

        result = handler._execute_tool(
            self.project_id,
            self._make_append_report_tool_call(
                content="## 第二章：策略建议\n\n" + ("新增正文" * 1200),
            ),
        )
        handler._persist_draft_followup_state_for_turn(
            self.project_id,
            "已追加第二章正文。",
            user_message="继续写正文",
        )
        saved = handler._load_conversation_state(self.project_id)["draft_followup_state"]

        self.assertEqual(result["status"], "success")
        self.assertIsNone(saved)

    @mock.patch("backend.chat.OpenAI")
    def test_persist_draft_followup_state_survives_when_default_target_met_but_continuation_threshold_unmet(
        self,
        mock_openai,
    ):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("正文" * 1800)
        current_count = handler._snapshot_project_file(
            self.project_id,
            "content/report_draft_v1.md",
        )["word_count"]
        self.assertGreaterEqual(current_count, 3000)
        self.assertLess(current_count, 5000)
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        handler._turn_context["draft_followup_flags"] = {
            "reported_under_target": True,
            "asked_continue_expand": True,
            "continuation_threshold_count": 5000,
        }

        handler._persist_draft_followup_state_for_turn(
            self.project_id,
            "普通说明，不带旧版提示关键词。",
        )
        saved = handler._load_conversation_state(self.project_id)["draft_followup_state"]

        self.assertIsNotNone(saved)
        self.assertEqual(saved["current_count"], current_count)
        self.assertEqual(saved["target_word_count"], 3000)
        self.assertEqual(saved["continuation_threshold_count"], 5000)

    @mock.patch("backend.chat.OpenAI")
    def test_second_p8_append_turn_preserves_carried_threshold_and_keeps_next_implicit_append_authority(
        self,
        mock_openai,
    ):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("正文" * 1800)
        current_count = handler._snapshot_project_file(
            self.project_id,
            "content/report_draft_v1.md",
        )["word_count"]
        self.assertGreaterEqual(current_count, 3000)
        self.assertLess(current_count, 5000)
        self._save_draft_followup_state(
            handler,
            current_count=current_count,
            target_word_count=3000,
            continuation_threshold_count=5000,
        )

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S4"),
        ):
            handler._turn_context = handler._build_turn_context(
                self.project_id,
                "太单薄了，再展开一点",
            )
        decision = handler._turn_context["canonical_draft_decision"]
        self.assertEqual(decision["priority"], "P8")

        append_result = handler._execute_tool(
            self.project_id,
            self._make_append_report_tool_call(content="补充分析" * 30),
        )
        handler._persist_draft_followup_state_for_turn(
            self.project_id,
            "已继续补写正文。",
            user_message="太单薄了，再展开一点",
        )
        saved = handler._load_conversation_state(self.project_id)["draft_followup_state"]
        next_decision = self._classify_canonical_draft_for_stage(
            handler,
            "S4",
            "还是太单薄了",
        )

        self.assertEqual(append_result["status"], "success")
        self.assertIsNotNone(saved)
        self.assertGreaterEqual(saved["current_count"], 3000)
        self.assertLess(saved["current_count"], 5000)
        self.assertEqual(saved["target_word_count"], 3000)
        self.assertEqual(saved["continuation_threshold_count"], 5000)
        self.assertEqual(next_decision["mode"], "require")
        self.assertEqual(next_decision["priority"], "P8")
        self.assertEqual(next_decision["expected_tool_family"], "append_report_draft")

    @mock.patch("backend.chat.OpenAI")
    def test_p7_inspect_turn_preserves_carried_threshold_above_default_target(
        self,
        mock_openai,
    ):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("正文" * 1800)
        current_count = handler._snapshot_project_file(
            self.project_id,
            "content/report_draft_v1.md",
        )["word_count"]
        self.assertGreaterEqual(current_count, 3000)
        self.assertLess(current_count, 5000)
        self._save_draft_followup_state(
            handler,
            current_count=current_count,
            target_word_count=3000,
            continuation_threshold_count=5000,
        )

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S4"),
        ):
            handler._turn_context = handler._build_turn_context(
                self.project_id,
                "看看现在多少字",
            )
        decision = handler._turn_context["canonical_draft_decision"]
        self.assertEqual(decision["priority"], "P7")

        handler._persist_draft_followup_state_for_turn(
            self.project_id,
            "当前正文约 3608/3000 字。",
            user_message="看看现在多少字",
        )
        saved = handler._load_conversation_state(self.project_id)["draft_followup_state"]

        self.assertIsNotNone(saved)
        self.assertTrue(saved["reported_under_target"])
        self.assertFalse(saved["asked_continue_expand"])
        self.assertEqual(saved["current_count"], current_count)
        self.assertEqual(saved["target_word_count"], 3000)
        self.assertEqual(saved["continuation_threshold_count"], 5000)

    @mock.patch("backend.chat.OpenAI")
    def test_intervening_nonwriting_turn_clears_priority8_authority(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("现有章节偏短。" * 120)
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        handler._turn_context["draft_followup_flags"] = {
            "reported_under_target": True,
            "asked_continue_expand": True,
            "continuation_threshold_count": 5000,
        }

        handler._persist_draft_followup_state_for_turn(
            self.project_id,
            "普通说明，不带旧版提示关键词。",
        )
        first_state = handler._load_conversation_state(self.project_id)["draft_followup_state"]
        self.assertIsNotNone(first_state)

        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        handler._turn_context["draft_followup_flags"] = {
            "reported_under_target": False,
            "asked_continue_expand": False,
            "continuation_threshold_count": None,
        }
        handler._persist_draft_followup_state_for_turn(
            self.project_id,
            "这轮先不写正文。",
        )
        second_state = handler._load_conversation_state(self.project_id)["draft_followup_state"]
        decision = self._classify_canonical_draft_for_stage(
            handler,
            "S4",
            "目标5000字喔？而且每章现在都太单薄了",
        )

        self.assertIsNone(second_state)
        self.assertEqual(decision["mode"], "no_write")
        self.assertEqual(decision["priority"], "P10")

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_tool_rejects_longer_write_file_substitute_before_append_turn(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        draft_path = self._write_partial_report_draft("既有正文" * 120)
        before = draft_path.read_text(encoding="utf-8")
        longer_content = before.rstrip() + "\n\n## 第二章：策略建议\n\n" + ("新增正文" * 80)
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S4"),
        ):
            handler._turn_context = handler._build_turn_context(
                self.project_id,
                "继续写正文",
            )
            snapshots = handler._build_required_write_snapshots(
                self.project_id,
                "继续写正文",
            )
        handler._turn_context["required_write_snapshots"] = snapshots

        result = handler._execute_tool(
            self.project_id,
            self._make_write_report_tool_call(content=longer_content),
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("append_report_draft", result["message"])
        self.assertEqual(draft_path.read_text(encoding="utf-8"), before)

    @mock.patch("backend.chat.OpenAI")
    def test_p5a_inherits_followup_threshold_count_over_default_target(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("正文" * 1800)
        current_count = handler._snapshot_project_file(
            self.project_id,
            "content/report_draft_v1.md",
        )["word_count"]
        self.assertGreater(current_count, 3000)
        self.assertLess(current_count, 5000)
        self._save_draft_followup_state(
            handler,
            current_count=current_count,
            target_word_count=3000,
            continuation_threshold_count=5000,
        )

        decision = self._classify_canonical_draft_for_stage(
            handler,
            "S4",
            "看看现在多少字，不够就继续写",
        )

        self.assertEqual(decision["mode"], "require")
        self.assertEqual(decision["priority"], "P5A")
        self.assertEqual(decision["effective_turn_target_count"], 5000)

    @mock.patch("backend.chat.OpenAI")
    def test_append_report_draft_returns_final_on_disk_report_progress_and_effective_turn_target(
        self,
        mock_openai,
    ):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("正文" * 1800)

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S4"),
        ):
            handler._turn_context = handler._build_turn_context(
                self.project_id,
                "先扩到 5000 字再导出",
            )

        result = handler._execute_tool(
            self.project_id,
            self._make_append_report_tool_call(
                content="## 第二章：策略建议\n\n" + ("新增正文" * 40),
            ),
        )
        final_count = handler._snapshot_project_file(
            self.project_id,
            "content/report_draft_v1.md",
        )["word_count"]

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["report_progress"],
            {
                "current_count": final_count,
                "target_word_count": 3000,
                "meets_target": True,
            },
        )
        self.assertEqual(result["effective_turn_target_count"], 5000)
        self.assertFalse(result["effective_turn_target_met"])

    @mock.patch("backend.chat.OpenAI")
    def test_canonical_edit_file_returns_final_on_disk_report_progress(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("旧结论\n\n" + ("现有正文" * 200))
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)

        read_result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "read_file",
                json.dumps({"file_path": "content/report_draft_v1.md"}, ensure_ascii=False),
            ),
        )
        result = handler._execute_tool(
            self.project_id,
            self._make_edit_report_tool_call(
                old_string="旧结论",
                new_string="新结论",
            ),
        )
        final_count = handler._snapshot_project_file(
            self.project_id,
            "content/report_draft_v1.md",
        )["word_count"]

        self.assertEqual(read_result["status"], "success")
        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["report_progress"],
            {
                "current_count": final_count,
                "target_word_count": 3000,
                "meets_target": False,
            },
        )
        self.assertNotIn("effective_turn_target_count", result)
        self.assertNotIn("effective_turn_target_met", result)

    @mock.patch("backend.chat.OpenAI")
    def test_implicit_continuation_s4_without_legacy_keywords_requires_canonical_draft_write(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("现有章节偏短。" * 120)
        self._save_draft_followup_state(handler, current_count=1800, target_word_count=3000)

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S4"),
        ):
            turn_context = handler._build_turn_context(
                self.project_id,
                "目标5000字喔？而且每章现在都太单薄了",
            )

        self.assertTrue(turn_context["can_write_non_plan"])
        self.assertEqual(
            self._required_write_paths_for_stage(
                handler,
                "S4",
                "目标5000字喔？而且每章现在都太单薄了",
            ),
            {"content/report_draft_v1.md"},
        )

    @mock.patch("backend.chat.OpenAI")
    def test_implicit_continuation_s3_still_hits_existing_stage_gate_rejection(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("现有章节偏短。" * 120)
        self._save_draft_followup_state(handler, current_count=1800, target_word_count=3000)

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S3"),
        ):
            handler._turn_context = handler._build_turn_context(
                self.project_id,
                "目标5000字喔？而且每章现在都太单薄了",
            )
            result = handler._execute_tool(
                self.project_id,
                self._make_append_report_tool_call(),
            )

        self.assertEqual(result["status"], "error")
        self.assertIn("当前轮次还不能开始写正文", result["message"])

    @mock.patch("backend.chat.OpenAI")
    def test_implicit_continuation_retry_path_fires_when_required_canonical_write_is_skipped(self, mock_openai):
        handler = self._make_handler_with_project()
        draft_path = self._write_partial_report_draft("现有章节偏短。" * 120)
        before = draft_path.read_text(encoding="utf-8")
        self._save_draft_followup_state(handler, current_count=1800, target_word_count=3000)
        false_completion = "已经补到 5000 字了。"
        final_message = "已继续补写正文。"
        mock_openai.return_value.chat.completions.create.side_effect = [
            self._make_non_stream_response(false_completion),
            self._make_non_stream_tool_response(self._make_append_report_tool_call()),
            self._make_non_stream_response(final_message),
        ]

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S4"),
        ):
            result = handler.chat(
                self.project_id,
                "目标5000字喔？而且每章现在都太单薄了",
                max_iterations=4,
            )

        retry_messages = mock_openai.return_value.chat.completions.create.call_args_list[1].kwargs["messages"]
        saved = self._read_saved_conversation()

        self.assertTrue(
            any(
                message.get("role") == "user"
                and "append_report_draft" in message.get("content", "")
                and "未检测到" in message.get("content", "")
                for message in retry_messages
            )
        )
        self.assertIn(final_message, result["content"])
        self.assertNotEqual(draft_path.read_text(encoding="utf-8"), before)
        self.assertIn(final_message, saved[-1]["content"])
        self.assertIn("<!-- tool-log", saved[-1]["content"])
        self.assertNotIn(false_completion, saved[-1]["content"])

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_feedback_for_canonical_append_omits_write_file(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("现有章节偏短。" * 120)
        self._save_draft_followup_state(handler, current_count=1800, target_word_count=3000)

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S4"),
        ):
            handler._turn_context = handler._build_turn_context(
                self.project_id,
                "目标5000字喔？而且每章现在都太单薄了",
            )

        feedback = handler._build_required_write_feedback(["content/report_draft_v1.md"])

        self.assertIn("append_report_draft", feedback)
        self.assertNotIn("write_file", feedback)
        self.assertNotIn("edit_file", feedback)

    @mock.patch("backend.chat.OpenAI")
    def test_required_draft_write_feedback_for_canonical_full_rewrite_mentions_read_then_edit_only(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_partial_report_draft("现有章节偏短。" * 120)

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S5"),
        ):
            handler._turn_context = handler._build_turn_context(
                self.project_id,
                "请全文重写这份报告正文",
            )

        feedback = handler._build_required_write_feedback(["content/report_draft_v1.md"])

        self.assertIn("read_file", feedback)
        self.assertIn("edit_file", feedback)
        self.assertNotIn("write_file", feedback)
        self.assertNotIn("append_report_draft", feedback)

    @mock.patch("backend.chat.OpenAI")
    def test_threshold_recheck_p5a_unmet_after_one_append_returns_guidance_only(self, mock_openai):
        handler = self._make_handler_with_project()
        draft_path = self._write_partial_report_draft("现有章节偏短。" * 120)
        before = draft_path.read_text(encoding="utf-8")
        mock_openai.return_value.chat.completions.create.side_effect = [
            self._make_non_stream_tool_response(self._make_append_report_tool_call()),
        ]

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S4"),
        ):
            result = handler.chat(self.project_id, "先扩到 5000 字再导出", max_iterations=3)

        self.assertNotEqual(draft_path.read_text(encoding="utf-8"), before)
        self.assertIn("导出", result["content"])
        self.assertIn("下一轮", result["content"])
        self.assertRegex(result["content"], r"仍(未达到|需继续)")
        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 1)

    @mock.patch("backend.chat.OpenAI")
    def test_threshold_recheck_p5a_met_after_one_append_returns_ready_next_turn_guidance(self, mock_openai):
        handler = self._make_handler_with_project()
        append_content = "## 第二章：策略建议\n\n" + ("新增正文" * 80)
        self._write_partial_report_draft("正文" * 2300)
        current_count = handler._snapshot_project_file(
            self.project_id,
            "content/report_draft_v1.md",
        )["word_count"]
        append_count = handler.skill_engine._count_words(append_content)
        target_count = current_count + max(10, append_count // 2)
        self.assertLess(current_count, target_count)
        mock_openai.return_value.chat.completions.create.side_effect = [
            self._make_non_stream_tool_response(
                self._make_append_report_tool_call(content=append_content),
            ),
        ]

        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S4"),
        ):
            result = handler.chat(
                self.project_id,
                f"先扩到 {target_count} 字再导出",
                max_iterations=3,
            )

        final_count = handler._snapshot_project_file(
            self.project_id,
            "content/report_draft_v1.md",
        )["word_count"]
        self.assertGreaterEqual(final_count, target_count)
        self.assertIn("导出", result["content"])
        self.assertIn("下一轮", result["content"])
        self.assertIn(str(target_count), result["content"])
        self.assertIn("已达到", result["content"])
        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 1)

    @mock.patch("backend.chat.OpenAI")
    def test_mixed_intent_secondary_families_stay_guidance_only_when_p5a_is_already_met(self, mock_openai):
        cases = [
            ("先扩到 500 字再导出", "导出"),
            ("先扩到 500 字再运行质量检查", "质量检查"),
            ("看看文件，不够就继续写", "看看文件"),
            ("看看现在多少字，不够就继续写", "看看现在多少字"),
        ]
        for message, marker in cases:
            with self.subTest(message=message):
                handler = self._make_handler_with_project()
                self._write_partial_report_draft("正文" * 1800)
                current_count = handler._snapshot_project_file(
                    self.project_id,
                    "content/report_draft_v1.md",
                )["word_count"]
                self.assertGreaterEqual(current_count, 3000)
                mock_openai.return_value.chat.completions.create.reset_mock()

                with mock.patch.object(
                    handler.skill_engine,
                    "_infer_stage_state",
                    return_value=self._mock_stage_state("S4"),
                ):
                    result = handler.chat(self.project_id, message)

                self.assertIn(marker, result["content"])
                self.assertIn("下一轮", result["content"])
                self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 0)

    @mock.patch("backend.chat.OpenAI")
    def test_mixed_intent_secondary_families_stay_guidance_only_after_one_append(self, mock_openai):
        cases = [
            ("先扩到 5000 字再导出", "导出"),
            ("先扩到 5000 字再运行质量检查", "质量检查"),
            ("看看文件，不够就继续写", "看看文件"),
            ("看看现在多少字，不够就继续写", "看看现在多少字"),
        ]
        for message, marker in cases:
            with self.subTest(message=message):
                handler = self._make_handler_with_project()
                draft_path = self._write_partial_report_draft("现有章节偏短。" * 120)
                before = draft_path.read_text(encoding="utf-8")
                mock_openai.return_value.chat.completions.create.reset_mock()
                mock_openai.return_value.chat.completions.create.side_effect = [
                    self._make_non_stream_tool_response(self._make_append_report_tool_call()),
                ]

                with mock.patch.object(
                    handler.skill_engine,
                    "_infer_stage_state",
                    return_value=self._mock_stage_state("S4"),
                ):
                    result = handler.chat(self.project_id, message, max_iterations=3)

                self.assertNotEqual(draft_path.read_text(encoding="utf-8"), before)
                self.assertIn("本轮不执行", result["content"])
                self.assertIn(marker, result["content"])
                self.assertIn("下一轮", result["content"])
                self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 1)

    @mock.patch("backend.chat.OpenAI")
    def test_debug_dump_request_skips_when_env_flag_disabled(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        request_kwargs = {
            "model": "gemini-3-flash",
            "messages": [{"role": "user", "content": "SECRET_REPORT_TEXT"}],
            "tools": [{"type": "function", "function": {"name": "write_file"}}],
            "tool_choice": "auto",
            "stream": True,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            debug_dir = home / ".consulting-report" / "debug"
            with mock.patch("pathlib.Path.home", return_value=home), mock.patch.dict(
                "os.environ",
                {},
                clear=True,
            ):
                handler._debug_dump_request(request_kwargs, label="stream")

            self.assertFalse(debug_dir.exists())

    @mock.patch("backend.chat.OpenAI")
    def test_debug_dump_request_redacts_messages_when_env_flag_enabled(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        request_kwargs = {
            "model": "gemini-3-flash",
            "messages": [
                {"role": "user", "content": "SECRET_REPORT_TEXT"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-write",
                            "type": "function",
                            "function": {
                                "name": "write_file",
                                "arguments": json.dumps(
                                    {
                                        "file_path": "content/report_draft_v1.md",
                                        "content": "SECRET_REPORT_TEXT",
                                    }
                                ),
                            },
                        }
                    ],
                },
            ],
            "tools": [
                {"type": "function", "function": {"name": "write_file"}},
                {"type": "function", "function": {"name": "append_report_draft"}},
            ],
            "tool_choice": "auto",
            "stream": True,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            debug_dir = home / ".consulting-report" / "debug"
            with mock.patch("pathlib.Path.home", return_value=home), mock.patch.dict(
                "os.environ",
                {"CONSULTING_REPORT_DEBUG_DUMP": "1"},
                clear=True,
            ):
                handler._debug_dump_request(
                    request_kwargs,
                    label="stream",
                    note="iteration=1",
                    error=RuntimeError("provider echoed request body: SECRET_REPORT_TEXT"),
                )

            payload_path = debug_dir / "payload-latest.json"
            payload_exists = payload_path.exists()
            raw_payload = payload_path.read_text(encoding="utf-8")
            payload = json.loads(raw_payload)

        self.assertTrue(payload_exists)
        self.assertNotIn("SECRET_REPORT_TEXT", raw_payload)
        self.assertEqual(payload["model"], "gemini-3-flash")
        self.assertEqual(payload["tool_choice"], "auto")
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["tools"], ["write_file", "append_report_draft"])
        self.assertEqual(payload["error"]["type"], "RuntimeError")
        self.assertNotIn("SECRET_REPORT_TEXT", payload["error"]["message"])
        self.assertLessEqual(len(payload["error"]["message"]), 240)
        self.assertEqual(payload["messages"][0]["role"], "user")
        self.assertEqual(payload["messages"][0]["content"], "[redacted]")
        self.assertEqual(payload["messages"][0]["content_length"], len("SECRET_REPORT_TEXT"))
        self.assertEqual(
            payload["messages"][1]["tool_calls"][0]["function"]["arguments"],
            "[redacted]",
        )

    @mock.patch("backend.chat.OpenAI")
    def test_debug_dump_request_redacts_non_stream_failure_when_env_flag_enabled(self, mock_openai):
        handler = self._make_handler_with_project()
        secret_message = "SECRET_NOSTREAM_REPORT_TEXT"
        mock_openai.return_value.chat.completions.create.side_effect = RuntimeError(
            f"provider echoed request body: {secret_message}"
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            debug_dir = home / ".consulting-report" / "debug"
            with mock.patch("pathlib.Path.home", return_value=home), mock.patch.dict(
                "os.environ",
                {"CONSULTING_REPORT_DEBUG_DUMP": "1"},
                clear=True,
            ):
                result = handler.chat(self.project_id, secret_message)

            payload_path = debug_dir / "payload-latest.json"
            error_paths = list(debug_dir.glob("error-*-nostream.json"))

            self.assertTrue(payload_path.exists())
            self.assertTrue(error_paths)
            raw_payload = payload_path.read_text(encoding="utf-8")
            raw_error_dump = error_paths[0].read_text(encoding="utf-8")
            payload = json.loads(raw_payload)

        combined_dump = raw_payload + "\n" + raw_error_dump
        self.assertIn("API调用失败", result["content"])
        self.assertIn("provider echoed request body", result["content"])
        self.assertIn("[redacted]", result["content"])
        self.assertNotIn(secret_message, result["content"])
        self.assertNotIn(secret_message, combined_dump)
        self.assertEqual(payload["label"], "nostream")
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["error"]["type"], "RuntimeError")
        self.assertNotIn(secret_message, payload["error"]["message"])

    @mock.patch("backend.chat.OpenAI")
    def test_debug_dump_request_redacts_image_url_data_url_from_error_dump(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        secret_fragment = "UNIQUE_IMAGE_SECRET_FRAGMENT_7f3b64"
        data_url = f"data:image/png;base64,AAA{secret_fragment}BBB"
        request_kwargs = {
            "model": "gemini-3-flash",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请看这张图"},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "tools": [{"type": "function", "function": {"name": "write_file"}}],
            "tool_choice": "auto",
            "stream": True,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            debug_dir = home / ".consulting-report" / "debug"
            with mock.patch("pathlib.Path.home", return_value=home), mock.patch.dict(
                "os.environ",
                {"CONSULTING_REPORT_DEBUG_DUMP": "1"},
                clear=True,
            ):
                handler._debug_dump_request(
                    request_kwargs,
                    label="stream",
                    error=RuntimeError(f"provider echoed request body: {data_url}"),
                )

            payload_path = debug_dir / "payload-latest.json"
            error_paths = list(debug_dir.glob("error-*-stream.json"))
            raw_payload = payload_path.read_text(encoding="utf-8")
            raw_error_dump = error_paths[0].read_text(encoding="utf-8")
            payload = json.loads(raw_payload)

        combined_dump = raw_payload + "\n" + raw_error_dump
        self.assertTrue(error_paths)
        self.assertNotIn(data_url, combined_dump)
        self.assertNotIn(secret_fragment, combined_dump)
        self.assertIn("[redacted]", payload["error"]["message"])
        self.assertLessEqual(len(payload["error"]["message"]), 240)

    @mock.patch("backend.chat.OpenAI")
    def test_debug_dump_request_redacts_truncated_image_data_url_payload_from_error_dump(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        payload_secret = "AAAUNIQUE_IMAGE_SECRET_FRAGMENT_7f3b64BBB"
        truncated_payload = "AAAUNIQUE_IMAGE_SECRET_FRAGMENT_7f3b64"
        data_url = f"data:image/png;base64,{payload_secret}"
        request_kwargs = {
            "model": "gemini-3-flash",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请看这张图"},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "tools": [{"type": "function", "function": {"name": "write_file"}}],
            "tool_choice": "auto",
            "stream": True,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            debug_dir = home / ".consulting-report" / "debug"
            with mock.patch("pathlib.Path.home", return_value=home), mock.patch.dict(
                "os.environ",
                {"CONSULTING_REPORT_DEBUG_DUMP": "1"},
                clear=True,
            ):
                handler._debug_dump_request(
                    request_kwargs,
                    label="stream",
                    error=RuntimeError(f"provider echoed truncated payload: {truncated_payload}"),
                )

            payload_path = debug_dir / "payload-latest.json"
            error_paths = list(debug_dir.glob("error-*-stream.json"))
            raw_payload = payload_path.read_text(encoding="utf-8")
            raw_error_dump = error_paths[0].read_text(encoding="utf-8")
            payload = json.loads(raw_payload)

        combined_dump = raw_payload + "\n" + raw_error_dump
        self.assertTrue(error_paths)
        self.assertNotIn("UNIQUE_IMAGE_SECRET_FRAGMENT", combined_dump)
        self.assertNotIn(truncated_payload, combined_dump)
        self.assertIn("[redacted]", payload["error"]["message"])
        self.assertLessEqual(len(payload["error"]["message"]), 240)

    @mock.patch("backend.chat.OpenAI")
    def test_debug_dump_request_redacts_truncated_base64url_image_payload_with_hyphen(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        hyphenated_fragment = "UNIQUESECRET7f3B-URLSAFEPAYLOAD9z"
        data_url = f"data:image/png;base64,AAA{hyphenated_fragment}BBB"
        request_kwargs = {
            "model": "gemini-3-flash",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "please inspect this image"},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "tools": [{"type": "function", "function": {"name": "write_file"}}],
            "tool_choice": "auto",
            "stream": True,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            debug_dir = home / ".consulting-report" / "debug"
            with mock.patch("pathlib.Path.home", return_value=home), mock.patch.dict(
                "os.environ",
                {"CONSULTING_REPORT_DEBUG_DUMP": "1"},
                clear=True,
            ):
                handler._debug_dump_request(
                    request_kwargs,
                    label="stream",
                    error=RuntimeError(f"provider echoed truncated payload: {hyphenated_fragment}"),
                )

            payload_path = debug_dir / "payload-latest.json"
            error_paths = list(debug_dir.glob("error-*-stream.json"))
            raw_payload = payload_path.read_text(encoding="utf-8")
            raw_error_dump = error_paths[0].read_text(encoding="utf-8")
            payload = json.loads(raw_payload)

        combined_dump = raw_payload + "\n" + raw_error_dump
        self.assertTrue(error_paths)
        self.assertNotIn(hyphenated_fragment, combined_dump)
        self.assertIn("[redacted]", payload["error"]["message"])
        self.assertLessEqual(len(payload["error"]["message"]), 240)

    @mock.patch("backend.chat.OpenAI")
    def test_should_allow_non_plan_write_uses_expand_request_as_history_approval(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(self._make_settings(projects_dir=projects_dir), engine)
            handler._save_conversation(
                project["id"],
                [
                    {"role": "user", "content": "请把现有正文扩写到5000字"},
                    {"role": "assistant", "content": "收到，我继续扩写正文。"},
                ],
            )

            self.assertTrue(handler._should_allow_non_plan_write(project["id"], "继续"))

    @mock.patch("backend.chat.OpenAI")
    def test_handler_write_file_requires_fetch_url_after_web_search_before_formal_external_write(self, mock_openai):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            settings = Settings(
                mode="managed",
                managed_base_url="https://newapi.z0y0h.work/client/v1",
                managed_model="gemini-3-flash",
                projects_dir=projects_dir,
                skill_dir=self.repo_skill_dir,
            )
            handler = ChatHandler(settings, engine)
            handler._turn_context = {
                "can_write_non_plan": True,
                "web_search_disabled": False,
                "web_search_performed": True,
                "fetch_url_performed": False,
            }

            result = handler._execute_tool(
                project["id"],
                self._make_tool_call(
                    "write_file",
                    '{"file_path":"plan/references.md","content":"# References\\n\\n- Example source"}',
                ),
            )

        self.assertEqual(result["status"], "error")
        self.assertIn("fetch_url", result["message"])

    @mock.patch("backend.chat.OpenAI")
    def test_handler_write_file_rejects_outline_in_s0_before_evidence_gate(self, mock_openai):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            settings = Settings(
                mode="managed",
                managed_base_url="https://newapi.z0y0h.work/client/v1",
                managed_model="gemini-3-flash",
                projects_dir=projects_dir,
                skill_dir=self.repo_skill_dir,
            )
            handler = ChatHandler(settings, engine)
            handler._turn_context = {"can_write_non_plan": True, "web_search_disabled": False}

            result = handler._execute_tool(
                project["id"],
                self._make_tool_call(
                    "write_file",
                    '{"file_path":"plan/outline.md","content":"# Report outline"}',
                ),
            )

        self.assertEqual(result["status"], "error")
        self.assertIn("S0 阶段", result["message"])
        self.assertIn("大纲", result["message"])
        self.assertIn("澄清", result["message"])

    @mock.patch("backend.chat.OpenAI")
    def test_handler_write_file_rejects_outline_in_s0_with_one_reference_source(self, mock_openai):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            self._write_evidence_gate_prerequisites(Path(project["project_dir"]), source_count=1)
            settings = Settings(
                mode="managed",
                managed_base_url="https://newapi.z0y0h.work/client/v1",
                managed_model="gemini-3-flash",
                projects_dir=projects_dir,
                skill_dir=self.repo_skill_dir,
            )
            handler = ChatHandler(settings, engine)
            handler._turn_context = {"can_write_non_plan": True, "web_search_disabled": False}

            result = handler._execute_tool(
                project["id"],
                self._make_tool_call(
                    "write_file",
                    '{"file_path":"plan/outline.md","content":"# Report outline"}',
                ),
            )

        self.assertEqual(result["status"], "error")
        self.assertIn("S0 阶段", result["message"])

    @mock.patch("backend.chat.OpenAI")
    def test_handler_write_file_rejects_research_plan_in_s0_before_evidence_gate(self, mock_openai):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            settings = Settings(
                mode="managed",
                managed_base_url="https://newapi.z0y0h.work/client/v1",
                managed_model="gemini-3-flash",
                projects_dir=projects_dir,
                skill_dir=self.repo_skill_dir,
            )
            handler = ChatHandler(settings, engine)
            handler._turn_context = {"can_write_non_plan": True, "web_search_disabled": False}

            result = handler._execute_tool(
                project["id"],
                self._make_tool_call(
                    "write_file",
                    '{"file_path":"plan/research-plan.md","content":"# Research plan"}',
                ),
            )

        self.assertEqual(result["status"], "error")
        self.assertIn("S0 阶段", result["message"])
        self.assertIn("研究计划", result["message"])
        self.assertIn("澄清", result["message"])

    @mock.patch("backend.chat.OpenAI")
    def test_handler_write_file_rejects_research_plan_in_s0_with_one_reference_source(self, mock_openai):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            self._write_evidence_gate_prerequisites(Path(project["project_dir"]), source_count=1)
            settings = Settings(
                mode="managed",
                managed_base_url="https://newapi.z0y0h.work/client/v1",
                managed_model="gemini-3-flash",
                projects_dir=projects_dir,
                skill_dir=self.repo_skill_dir,
            )
            handler = ChatHandler(settings, engine)
            handler._turn_context = {"can_write_non_plan": True, "web_search_disabled": False}

            result = handler._execute_tool(
                project["id"],
                self._make_tool_call(
                    "write_file",
                    '{"file_path":"plan/research-plan.md","content":"# Research plan"}',
                ),
            )

        self.assertEqual(result["status"], "error")
        self.assertIn("S0 阶段", result["message"])

    @mock.patch("backend.chat.OpenAI")
    def test_handler_write_file_allows_outline_after_evidence_gate_is_satisfied(self, mock_openai):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            self._write_evidence_gate_prerequisites(Path(project["project_dir"]))
            settings = Settings(
                mode="managed",
                managed_base_url="https://newapi.z0y0h.work/client/v1",
                managed_model="gemini-3-flash",
                projects_dir=projects_dir,
                skill_dir=self.repo_skill_dir,
            )
            handler = ChatHandler(settings, engine)
            handler._turn_context = {"can_write_non_plan": False, "web_search_disabled": False}
            self._read_file_for_turn(handler, "./plan/OUTLINE.MD", project["id"])

            result = handler._execute_tool(
                project["id"],
                self._make_tool_call(
                    "write_file",
                    '{"file_path":"./plan/OUTLINE.MD","content":"# Report outline\\n\\n## Executive summary\\n- Key finding\\n## Recommendations\\n- Next step"}',
                ),
            )

            self.assertEqual(result["status"], "success")
            self.assertIn("plan/outline.md", result["message"])
            self.assertIn(
                "Executive summary",
                (Path(project["project_dir"]) / "plan" / "outline.md").read_text(encoding="utf-8"),
            )

    @mock.patch("backend.chat.OpenAI")
    def test_write_file_rejects_self_signed_review_checklist(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        handler._turn_context = {"can_write_non_plan": True, "web_search_disabled": False}
        self._read_file_for_turn(handler, "plan/review-checklist.md")

        result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "write_file",
                json.dumps(
                    {
                        "file_path": "plan/review-checklist.md",
                        "content": "**审查人：咨询报告写作助手**\n...",
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("审查人", result["message"])

    @mock.patch("backend.chat.OpenAI")
    def test_write_file_rejects_self_signed_review_checklist_with_fullwidth_space(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        handler._turn_context = {"can_write_non_plan": True, "web_search_disabled": False}
        self._read_file_for_turn(handler, "plan/review-checklist.md")

        result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "write_file",
                json.dumps(
                    {
                        "file_path": "plan/review-checklist.md",
                        "content": "审查人： 咨询报告写作助手",
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("审查人", result["message"])

    @mock.patch("backend.chat.OpenAI")
    def test_write_file_rejects_premature_review_verdict_without_checkpoint(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        handler._turn_context = {"can_write_non_plan": True, "web_search_disabled": False}
        self._read_file_for_turn(handler, "plan/review-checklist.md")

        result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "write_file",
                json.dumps(
                    {
                        "file_path": "plan/review-checklist.md",
                        "content": "审查结论：通过\n建议通过",
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("建议通过", result["message"])

    @mock.patch("backend.chat.OpenAI")
    def test_write_file_accepts_review_verdict_after_review_started(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        handler.skill_engine._save_stage_checkpoint(self.project_dir, "review_started_at")
        handler._turn_context = {"can_write_non_plan": True, "web_search_disabled": False}
        self._read_file_for_turn(handler, "plan/review-checklist.md")

        result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "write_file",
                json.dumps(
                    {
                        "file_path": "plan/review-checklist.md",
                        "content": "审查结论：建议通过",
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        self.assertEqual(result["status"], "success")

    @mock.patch("backend.chat.OpenAI")
    def test_write_file_auto_disables_review_interception_when_review_passed(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        handler.skill_engine._save_stage_checkpoint(self.project_dir, "review_passed_at")
        handler._turn_context = {"can_write_non_plan": True, "web_search_disabled": False}
        self._read_file_for_turn(handler, "plan/review-checklist.md")

        result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "write_file",
                json.dumps(
                    {
                        "file_path": "plan/review-checklist.md",
                        "content": "审查人：咨询报告写作助手",
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        self.assertEqual(result["status"], "success")

    @mock.patch("backend.chat.OpenAI")
    def test_write_file_rejects_inline_placeholder_feedback(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        handler._turn_context = {"can_write_non_plan": True, "web_search_disabled": False}
        self._read_file_for_turn(handler, "plan/delivery-log.md")

        result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "write_file",
                json.dumps(
                    {
                        "file_path": "plan/delivery-log.md",
                        "content": "- [x] **反馈 A**：（待记录）",
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("客户反馈", result["message"])

    @mock.patch("backend.chat.OpenAI")
    def test_write_file_rejects_multiline_placeholder_feedback(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        handler._turn_context = {"can_write_non_plan": True, "web_search_disabled": False}
        self._read_file_for_turn(handler, "plan/delivery-log.md")

        result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "write_file",
                json.dumps(
                    {
                        "file_path": "plan/delivery-log.md",
                        "content": "- [x] 客户反馈\n（待记录）",
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("客户反馈", result["message"])

    @mock.patch("backend.chat.OpenAI")
    def test_write_file_accepts_multiline_real_feedback(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        handler._turn_context = {"can_write_non_plan": True, "web_search_disabled": False}
        self._read_file_for_turn(handler, "plan/delivery-log.md")

        result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "write_file",
                json.dumps(
                    {
                        "file_path": "plan/delivery-log.md",
                        "content": "- [x] 客户反馈\n客户说非常满意",
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        self.assertEqual(result["status"], "success")

    @mock.patch("backend.chat.OpenAI")
    def test_write_file_rejects_archived_status_claim_without_checkpoint(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        handler._turn_context = {"can_write_non_plan": True, "web_search_disabled": False}
        self._read_file_for_turn(handler, "plan/delivery-log.md")

        result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "write_file",
                json.dumps(
                    {
                        "file_path": "plan/delivery-log.md",
                        "content": "## 项目状态\n已交付，归档完成",
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("归档结束项目", result["message"])

    @mock.patch("backend.chat.OpenAI")
    def test_write_file_auto_disables_delivery_interception_when_archived(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        handler.skill_engine._save_stage_checkpoint(self.project_dir, "delivery_archived_at")
        handler._turn_context = {"can_write_non_plan": True, "web_search_disabled": False}
        self._read_file_for_turn(handler, "plan/delivery-log.md")

        result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "write_file",
                json.dumps(
                    {
                        "file_path": "plan/delivery-log.md",
                        "content": "## 项目状态\n已交付，归档完成",
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        self.assertEqual(result["status"], "success")

    @mock.patch("backend.chat.OpenAI")
    def test_write_file_rejects_direct_write_to_stage_checkpoints(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        handler._turn_context = {"can_write_non_plan": True, "web_search_disabled": False}

        result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "write_file",
                json.dumps(
                    {
                        "file_path": "stage_checkpoints.json",
                        "content": '{"outline_confirmed_at": "2026-04-17T12:00:00"}',
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)

        self.assertEqual(result["status"], "error")
        self.assertIn("stage_checkpoints.json", result["message"])
        self.assertNotIn("outline_confirmed_at", checkpoints)

    @mock.patch("backend.chat.OpenAI")
    def test_write_file_rejects_checkpoints_path_via_relative_and_case_variants(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        handler._turn_context = {"can_write_non_plan": True, "web_search_disabled": False}
        variants = [
            "./stage_checkpoints.json",
            "stage_checkpoints.json",
            ".\\stage_checkpoints.json",
            "Stage_Checkpoints.json",
            "STAGE_CHECKPOINTS.JSON",
            ".\\STAGE_CHECKPOINTS.json",
            "plan/../Stage_Checkpoints.json",
        ]

        for path in variants:
            result = handler._execute_tool(
                self.project_id,
                self._make_tool_call(
                    "write_file",
                    json.dumps(
                        {"file_path": path, "content": "{}"},
                        ensure_ascii=False,
                    ),
                ),
            )
            self.assertEqual(result["status"], "error", f"path {path} was not blocked")

    @mock.patch("backend.chat.OpenAI")
    def test_chat_stream_yields_system_notice_on_blocked_write(self, mock_openai):
        handler = self._make_handler_with_project()
        blocked_call = self._make_stream_tool_call_chunk(
            0,
            id="call-1",
            name="write_file",
            arguments=json.dumps(
                {
                    "file_path": "stage_checkpoints.json",
                    "content": '{"outline_confirmed_at": "2026-04-17T12:00:00"}',
                },
                ensure_ascii=False,
            ),
        )
        mock_openai.return_value.chat.completions.create.side_effect = [
            iter([self._make_chunk(tool_calls=[blocked_call])]),
            iter([self._make_chunk(content="收到")]),
        ]

        events = list(handler.chat_stream(self.project_id, "继续", max_iterations=2))
        notices = [event for event in events if event["type"] == "system_notice"]

        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["category"], "non_plan_write_blocked")
        self.assertIsNone(notices[0]["path"])
        self.assertTrue(notices[0]["reason"])
        self.assertTrue(notices[0]["user_action"])
        self.assertNotIn("surface_to_user", notices[0])

    @mock.patch("backend.chat.OpenAI")
    def test_write_file_blocks_data_log_format_hint_write_in_s0(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        (self.project_dir / "plan" / "data-log.md").unlink()
        handler._turn_context = {"can_write_non_plan": True, "web_search_disabled": False}

        result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "write_file",
                json.dumps(
                    {
                        "file_path": "plan/data-log.md",
                        "content": (
                            "# Data log\n\n"
                            "### [DL-2024-01] 财政部数据资源暂行规定\n"
                            "- **来源**：财政部\n"
                            "- **时间**：2024-01-01\n"
                            "- **URL**：https://www.example.com/policy\n"
                            "- **用途**：政策基石\n"
                        ),
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        notices = handler._turn_context.get("pending_system_notices", [])

        self.assertEqual(result["status"], "error")
        self.assertIn("S0 阶段", result["message"])
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["category"], "s0_write_blocked")
        self.assertEqual(notices[0]["path"], "plan/data-log.md")
        self.assertIn("资料清单", notices[0]["reason"])
        self.assertIn("SKILL.md §S0", notices[0]["user_action"])

    @mock.patch("backend.chat.OpenAI")
    def test_write_file_rejects_analysis_notes_without_dl_refs_after_data_log_ready(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_stage_one_prerequisites(self.project_dir)
        handler.skill_engine._save_stage_checkpoint(self.project_dir, "s0_interview_done_at")
        handler.skill_engine._save_stage_checkpoint(self.project_dir, "outline_confirmed_at")
        (self.project_dir / "plan" / "data-log.md").write_text(
            "\n\n".join(
                f"### [DL-2026-{index:02d}] 事实 {index}\n- **URL**：https://example.com/{index}"
                for index in range(1, 9)
            ),
            encoding="utf-8",
        )
        handler._turn_context = {
            "can_write_non_plan": True,
            "web_search_disabled": False,
            "pending_system_notices": [],
            "user_notice_emitted": False,
            "internal_notice_emitted": False,
        }
        self._read_file_for_turn(handler, "plan/analysis-notes.md")

        result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "write_file",
                json.dumps(
                    {
                        "file_path": "plan/analysis-notes.md",
                        "content": (
                            "# 分析笔记\n\n"
                            "### 战斗力天花板对比\n"
                            "- **发现**：猪猪侠具备五灵封印，蝙蝠侠依赖地狱蝙蝠装甲。\n"
                            "- **推论**：胜负关键是能否拖过 10 分钟。\n"
                            "- **影响**：报告正文应突出非对称博弈。\n"
                        ),
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        notices = handler._turn_context.get("pending_system_notices", [])

        self.assertEqual(result["status"], "error")
        self.assertIn("analysis-notes.md", result["message"])
        self.assertIn("[DL-2026-01]", result["message"])
        self.assertEqual(notices[0]["category"], "analysis_refs_missing")

    @mock.patch("backend.chat.OpenAI")
    def test_system_notice_dual_class_notices_can_coexist_within_turn(self, mock_openai):
        handler = self._make_handler_with_project()
        first_call = self._make_stream_tool_call_chunk(
            0,
            id="call-1",
            name="write_file",
            arguments=json.dumps(
                {
                    "file_path": "plan/review-checklist.md",
                    "content": "审查人：咨询报告写作助手",
                },
                ensure_ascii=False,
            ),
        )
        second_call = self._make_stream_tool_call_chunk(
            1,
            id="call-2",
            name="write_file",
            arguments=json.dumps(
                {
                    "file_path": "stage_checkpoints.json",
                    "content": '{"outline_confirmed_at": "2026-04-17T12:00:00"}',
                },
                ensure_ascii=False,
            ),
        )
        mock_openai.return_value.chat.completions.create.side_effect = [
            iter([self._make_chunk(tool_calls=[first_call, second_call])]),
            iter([self._make_chunk(content="收到")]),
        ]

        events = list(handler.chat_stream(self.project_id, "继续", max_iterations=2))
        notices = [event for event in events if event["type"] == "system_notice"]

        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["category"], "non_plan_write_blocked")
        self.assertNotIn("surface_to_user", notices[0])

    @mock.patch("backend.chat.OpenAI")
    def test_system_notice_reset_between_turns(self, mock_openai):
        handler = self._make_handler_with_project()
        blocked_call = self._make_stream_tool_call_chunk(
            0,
            id="call-1",
            name="write_file",
            arguments=json.dumps(
                {
                    "file_path": "stage_checkpoints.json",
                    "content": '{"outline_confirmed_at": "2026-04-17T12:00:00"}',
                },
                ensure_ascii=False,
            ),
        )
        mock_openai.return_value.chat.completions.create.side_effect = [
            iter([self._make_chunk(tool_calls=[blocked_call])]),
            iter([self._make_chunk(content="第一轮")]),
            iter([self._make_chunk(tool_calls=[blocked_call])]),
            iter([self._make_chunk(content="第二轮")]),
        ]

        first_events = list(handler.chat_stream(self.project_id, "继续", max_iterations=2))
        second_events = list(handler.chat_stream(self.project_id, "继续", max_iterations=2))

        first_notices = [event for event in first_events if event["type"] == "system_notice"]
        second_notices = [event for event in second_events if event["type"] == "system_notice"]
        self.assertEqual(len(first_notices), 1)
        self.assertEqual(len(second_notices), 1)

    @mock.patch("backend.chat.OpenAI")
    def test_chat_non_streaming_includes_system_notices_in_response(self, mock_openai):
        handler = self._make_handler_with_project()
        blocked_tool_call = SimpleNamespace(
            id="call-1",
            function=SimpleNamespace(
                name="write_file",
                arguments=json.dumps(
                    {
                        "file_path": "stage_checkpoints.json",
                        "content": '{"outline_confirmed_at": "2026-04-17T12:00:00"}',
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        mock_openai.return_value.chat.completions.create.side_effect = [
            SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="",
                            tool_calls=[blocked_tool_call],
                        )
                    )
                ],
            ),
            SimpleNamespace(
                usage=SimpleNamespace(total_tokens=123),
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="收到",
                            tool_calls=[],
                        )
                    )
                ],
            ),
        ]

        result = handler.chat(self.project_id, "继续", max_iterations=2)

        self.assertIn("system_notices", result)
        self.assertEqual(len(result["system_notices"]), 1)
        self.assertEqual(result["system_notices"][0].category, "non_plan_write_blocked")

    @mock.patch("backend.chat.OpenAI")
    def test_chat_retries_when_assistant_claims_outline_written_without_actual_write(self, mock_openai):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="消费品牌战略研究",
                target_audience="管理层",
                deadline="2026-04-01",
                expected_length="3000字",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )

            first_response = SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=(
                                "### 报告大纲\n"
                                "第1章 执行摘要\n"
                                "第2章 市场分析\n"
                                "我已更新 `plan/outline.md`，你可以继续确认。"
                            ),
                            tool_calls=[],
                        )
                    )
                ],
            )
            tool_call = SimpleNamespace(
                id="call-1",
                function=SimpleNamespace(
                    name="write_file",
                    arguments='{"file_path":"plan/outline.md","content":"# 报告大纲\\n\\n## 第1章 执行摘要"}',
                ),
            )
            second_response = SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="",
                            tool_calls=[tool_call],
                        )
                    )
                ],
            )
            final_response = SimpleNamespace(
                usage=SimpleNamespace(total_tokens=256),
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="已实际写入 `plan/outline.md`，请确认大纲。",
                            tool_calls=[],
                        )
                    )
                ],
            )
            mock_openai.return_value.chat.completions.create.side_effect = [
                first_response,
                second_response,
                final_response,
            ]

            with mock.patch.object(
                handler,
                "_execute_tool",
                return_value={"status": "success", "message": "已写入文件: plan/outline.md"},
            ) as execute_tool:
                result = handler.chat(project["id"], "先给我一版大纲", max_iterations=4)

        self.assertIn("已实际写入 `plan/outline.md`，请确认大纲。", result["content"])
        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 3)
        self.assertEqual(execute_tool.call_count, 1)
        second_call_messages = mock_openai.return_value.chat.completions.create.call_args_list[1].kwargs["messages"]
        self.assertTrue(
            any(
                message.get("role") == "user"
                and "刚刚声称已更新" in message.get("content", "")
                for message in second_call_messages
            )
        )

    @mock.patch("backend.chat.OpenAI")
    def test_expected_plan_writes_include_stage_gates_and_tasks_when_assistant_claims_updates(self, mock_openai):
        del mock_openai
        handler = ChatHandler(
            self._make_settings(),
            SkillEngine(Path(tempfile.gettempdir()) / "expected-write-projects", self.repo_skill_dir),
        )

        expected = handler._expected_plan_writes_for_message(
            "我已更新 `plan/stage-gates.md`、`plan/tasks.md`，并同步了当前阶段与任务清单。"
        )

        self.assertIn("plan/stage-gates.md", expected)
        self.assertIn("plan/tasks.md", expected)

    @mock.patch("backend.chat.OpenAI")
    def test_expected_plan_writes_include_only_canonical_report_draft_when_assistant_claims_report_saved(self, mock_openai):
        del mock_openai
        handler = ChatHandler(
            self._make_settings(),
            SkillEngine(Path(tempfile.gettempdir()) / "expected-report-write-projects", self.repo_skill_dir),
        )

        expected = handler._expected_plan_writes_for_message(
            "我已写入 `content/report_draft_v1.md`，并完成正文初稿。"
        )

        self.assertEqual(expected, {"content/report_draft_v1.md"})

    @mock.patch("backend.chat.OpenAI")
    def test_expected_plan_writes_include_content_report_draft_v1_when_assistant_claims_saved(self, mock_openai):
        del mock_openai
        handler = ChatHandler(
            self._make_settings(),
            SkillEngine(Path(tempfile.gettempdir()) / "expected-content-report-v1-projects", self.repo_skill_dir),
        )

        expected = handler._expected_plan_writes_for_message(
            "第二章已完成，已同步至 `content/report_draft_v1.md`。"
        )

        self.assertIn("content/report_draft_v1.md", expected)

    @mock.patch("backend.chat.OpenAI")
    def test_expected_plan_writes_ignore_legacy_or_versioned_report_draft_paths(self, mock_openai):
        del mock_openai
        handler = ChatHandler(
            self._make_settings(),
            SkillEngine(Path(tempfile.gettempdir()) / "expected-content-report-v5-projects", self.repo_skill_dir),
        )

        expected = handler._expected_plan_writes_for_message(
            "已同步至 `report_draft_v1.md`、`content/report.md`、`content/report_draft_v5.md` 和 `output/final-report.md`。"
        )

        self.assertEqual(expected, set())

    @mock.patch("backend.chat.OpenAI")
    def test_expected_plan_writes_include_literal_file_tool_calls_and_data_log_entries(self, mock_openai):
        del mock_openai
        handler = ChatHandler(
            self._make_settings(),
            SkillEngine(Path(tempfile.gettempdir()) / "expected-pseudo-tool-projects", self.repo_skill_dir),
        )

        expected = handler._expected_plan_writes_for_message(
            "以下是新采集的事实条目，我将立即通过 `edit_file` 将其追加至 `plan/data-log.md`：\n\n"
            "### [DL-2026-03] 咏声动漫营收结构\n"
            "- **URL**：https://example.com/revenue\n\n"
            "*(工具调用)*\n"
            "edit_file(file_path=\"plan/data-log.md\", old_string=\"...\", new_string=\"...\")\n"
            "edit_file(file_path=\"plan/analysis-notes.md\", old_string=\"...\", new_string=\"...\")\n"
        )

        self.assertIn("plan/data-log.md", expected)
        self.assertIn("plan/analysis-notes.md", expected)

    @mock.patch("backend.chat.OpenAI")
    def test_extract_successful_write_path_accepts_edit_file_success(self, mock_openai):
        del mock_openai
        handler = ChatHandler(
            self._make_settings(),
            SkillEngine(Path(tempfile.gettempdir()) / "edit-write-path-projects", self.repo_skill_dir),
        )

        path = handler._extract_successful_write_path(
            "edit_file",
            '{"file_path":"plan/data-log.md","old_string":"a","new_string":"ab"}',
            {"status": "success", "message": "已写入文件: plan/data-log.md"},
        )

        self.assertEqual(path, "plan/data-log.md")

    @mock.patch("backend.chat.OpenAI")
    def test_chat_retries_when_assistant_prints_pseudo_edit_file_instead_of_calling_tool(self, mock_openai):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="消费品牌战略研究",
                target_audience="管理层",
                deadline="2026-04-01",
                expected_length="5000字",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )

            first_response = SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=(
                                "### [DL-2026-03] 新来源\n"
                                "- **URL**：https://example.com/source\n\n"
                                "edit_file(file_path=\"plan/data-log.md\", old_string=\"...\", new_string=\"...\")"
                            ),
                            tool_calls=[],
                        )
                    )
                ],
            )
            tool_call = SimpleNamespace(
                id="call-edit",
                function=SimpleNamespace(
                    name="edit_file",
                    arguments=json.dumps(
                        {
                            "file_path": "plan/data-log.md",
                            "old_string": "# 事实记录 (Data Log)\n",
                            "new_string": "# 事实记录 (Data Log)\n\n### [DL-2026-03] 新来源\n- **URL**：https://example.com/source\n",
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            second_response = SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="",
                            tool_calls=[tool_call],
                        )
                    )
                ],
            )
            final_response = SimpleNamespace(
                usage=SimpleNamespace(total_tokens=128),
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="已真实写入 `plan/data-log.md`。",
                            tool_calls=[],
                        )
                    )
                ],
            )
            mock_openai.return_value.chat.completions.create.side_effect = [
                first_response,
                second_response,
                final_response,
            ]

            with mock.patch.object(
                handler,
                "_execute_tool",
                return_value={"status": "success", "message": "已写入文件: plan/data-log.md"},
            ) as execute_tool:
                result = handler.chat(project["id"], "补来源", max_iterations=4)

        self.assertIn("已真实写入 `plan/data-log.md`。", result["content"])
        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 3)
        self.assertEqual(execute_tool.call_count, 1)
        retry_messages = mock_openai.return_value.chat.completions.create.call_args_list[1].kwargs["messages"]
        self.assertTrue(
            any(
                message.get("role") == "user"
                and "不要把 `edit_file(...)`" in message.get("content", "")
                for message in retry_messages
            )
        )

    @mock.patch("backend.chat.OpenAI")
    def test_chat_retries_self_correction_loop_before_saving_assistant_message(self, mock_openai):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="消费品牌战略研究",
                target_audience="管理层",
                deadline="2026-04-01",
                expected_length="3000字",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )

            loop_response = SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=(
                                "（修正：我将直接开始。）\n"
                                "（纠正：我需要等待确认。）\n"
                                "（修正：由于之前已经确认，我继续。）\n"
                                "（对不起，我需要停止自言自语。）"
                            ),
                            tool_calls=[],
                        )
                    )
                ],
            )
            final_response = SimpleNamespace(
                usage=SimpleNamespace(total_tokens=64),
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="我会先补齐 data-log.md 的来源，不开始正文。",
                            tool_calls=[],
                        )
                    )
                ],
            )
            mock_openai.return_value.chat.completions.create.side_effect = [
                loop_response,
                final_response,
            ]

            result = handler.chat(project["id"], "继续", max_iterations=3)
            saved = json.loads(
                (Path(project["project_dir"]) / "conversation.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result["content"], "我会先补齐 data-log.md 的来源，不开始正文。")
        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 2)
        self.assertNotIn("停止自言自语", saved[-1]["content"])

    @mock.patch("backend.chat.OpenAI")
    def test_chat_stream_warns_and_retries_when_assistant_claims_file_update_without_write(self, mock_openai):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="消费品牌战略研究",
                target_audience="管理层",
                deadline="2026-04-01",
                expected_length="3000字",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )

            first_stream = [
                self._make_chunk(content="我已更新 "),
                self._make_chunk(content="`plan/notes.md`。"),
            ]
            second_stream = [
                self._make_chunk(
                    tool_calls=[
                        self._make_stream_tool_call_chunk(
                            0,
                            id="call-2",
                            name="write_file",
                            arguments='{"file_path":"plan/notes.md","content":"# 项目笔记"}',
                        )
                    ]
                )
            ]
            final_stream = [
                self._make_chunk(content="现在已经真实写入 notes。"),
            ]
            mock_openai.return_value.chat.completions.create.side_effect = [
                iter(first_stream),
                iter(second_stream),
                iter(final_stream),
            ]

            with mock.patch.object(
                handler,
                "_execute_tool",
                return_value={"status": "success", "message": "已写入文件: plan/notes.md"},
            ):
                events = list(handler.chat_stream(project["id"], "把备注记一下", max_iterations=4))

        tool_messages = [event["data"] for event in events if event["type"] == "tool"]
        content_messages = [event["data"] for event in events if event["type"] == "content"]
        self.assertTrue(any("声称已更新文件但未实际写入" in message for message in tool_messages))
        self.assertTrue(any("调用工具: write_file" in message for message in tool_messages))
        self.assertIn("现在已经真实写入 notes。", "".join(content_messages))

    @mock.patch("backend.chat.OpenAI")
    def test_execute_read_material_file_persists_evidence_event_and_memory(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            material_path = workspace_dir / "materials" / "evidence.txt"
            material_path.parent.mkdir(parents=True, exist_ok=True)
            material_path.write_text("一手访谈纪要", encoding="utf-8")
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            material = engine.add_materials(project["id"], [str(material_path)], added_via="test")[0]
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )

            result = handler._execute_tool(
                project["id"],
                self._make_tool_call(
                    "read_material_file",
                    json.dumps({"material_id": material["id"]}, ensure_ascii=False),
                ),
            )

            state_path = Path(project["project_dir"]) / "conversation_state.json"
            persisted = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(persisted["events"]), 1)
        self.assertEqual(persisted["events"][0]["tool_name"], "read_material_file")
        self.assertEqual(persisted["events"][0]["category"], "evidence")
        self.assertEqual(persisted["events"][0]["source_key"], f"material:{material['id']}")
        self.assertIn("recorded_at", persisted["events"][0])
        self.assertNotIn("arguments", persisted["events"][0])
        self.assertNotIn("result", persisted["events"][0])
        self.assertEqual(len(persisted["memory_entries"]), 1)
        self.assertEqual(persisted["memory_entries"][0]["category"], "evidence")
        self.assertEqual(persisted["memory_entries"][0]["source_key"], f"material:{material['id']}")
        self.assertEqual(persisted["memory_entries"][0]["content"], "一手访谈纪要")

    @mock.patch("backend.chat.OpenAI")
    def test_write_file_upserts_workspace_memory_for_same_path(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )

            first = handler._execute_tool(
                project["id"],
                self._make_tool_call(
                    "write_file",
                    json.dumps(
                        {"file_path": "notes\\draft.md", "content": "第一版内容"},
                        ensure_ascii=False,
                    ),
                ),
            )
            self._read_file_for_turn(handler, "notes\\draft.md", project["id"])
            second = handler._execute_tool(
                project["id"],
                self._make_tool_call(
                    "write_file",
                    json.dumps(
                        {"file_path": "notes\\draft.md", "content": "第二版内容"},
                        ensure_ascii=False,
                    ),
                ),
            )

            state_path = Path(project["project_dir"]) / "conversation_state.json"
            persisted = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "success")
        self.assertEqual(len(persisted["events"]), 3)
        self.assertEqual(
            [event["tool_name"] for event in persisted["events"]],
            ["write_file", "read_file", "write_file"],
        )
        self.assertNotIn("arguments", persisted["events"][0])
        self.assertNotIn("result", persisted["events"][0])
        self.assertEqual(len(persisted["memory_entries"]), 1)
        self.assertEqual(persisted["memory_entries"][0]["category"], "workspace")
        self.assertEqual(persisted["memory_entries"][0]["source_key"], "file:notes/draft.md")
        self.assertEqual(persisted["memory_entries"][0]["source_ref"], "notes/draft.md")
        self.assertEqual(persisted["memory_entries"][0]["content"], "第二版内容")

    @mock.patch("backend.chat.OpenAI")
    def test_workspace_memory_read_then_write_same_path_keeps_only_current_entry(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )
            project_dir = Path(project["project_dir"])
            target_path = project_dir / "notes" / "draft.md"
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text("第一版内容", encoding="utf-8")

            read_result = handler._execute_tool(
                project["id"],
                self._make_tool_call(
                    "read_file",
                    json.dumps({"file_path": "notes\\draft.md"}, ensure_ascii=False),
                ),
            )
            write_result = handler._execute_tool(
                project["id"],
                self._make_tool_call(
                    "write_file",
                    json.dumps(
                        {"file_path": "notes\\draft.md", "content": "第二版内容"},
                        ensure_ascii=False,
                    ),
                ),
            )
            persisted = json.loads((project_dir / "conversation_state.json").read_text(encoding="utf-8"))

        self.assertEqual(read_result["status"], "success")
        self.assertEqual(write_result["status"], "success")
        self.assertEqual(len(persisted["memory_entries"]), 1)
        self.assertEqual(persisted["memory_entries"][0]["source_key"], "file:notes/draft.md")
        self.assertEqual(persisted["memory_entries"][0]["source_ref"], "notes/draft.md")
        self.assertEqual(persisted["memory_entries"][0]["content"], "第二版内容")

    @mock.patch("backend.chat.OpenAI")
    def test_build_provider_conversation_includes_memory_entry_provenance_when_available(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )
            project_dir = Path(project["project_dir"])
            (project_dir / "conversation_state.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "events": [],
                        "memory_entries": [
                            {
                                "category": "workspace",
                                "source_key": "file:plan/outline.md",
                                "source_ref": "plan/outline.md",
                                "content": "# 大纲",
                            }
                        ],
                        "compact_state": None,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            provider_conversation = handler._build_provider_conversation(
                project["id"],
                [],
                {
                    "role": "user",
                    "content": "当前追问",
                    "attached_material_ids": [],
                    "transient_attachments": [],
                },
            )

        self.assertEqual(provider_conversation[1]["role"], "assistant")
        self.assertEqual(
            handler._split_memory_block_items(provider_conversation[1]),
            ["来源: plan/outline.md\n# 大纲"],
        )

    @mock.patch("backend.chat.OpenAI")
    def test_mutate_conversation_state_preserves_existing_events_memory_and_compact_state(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )
            state_path = Path(project["project_dir"]) / "conversation_state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "events": [{"type": "seed-event", "content": "旧事件"}],
                        "memory_entries": [{"category": "workspace", "source_key": "file:old.md", "content": "旧记忆"}],
                        "compact_state": {
                            "summary_text": "旧摘要",
                            "source_message_count": 2,
                            "source_memory_entry_count": 1,
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            handler._mutate_conversation_state(
                project["id"],
                lambda state: (
                    state["events"].append({"type": "tool_result", "tool_name": "read_file"}),
                    state["memory_entries"].append(
                        {"category": "workspace", "source_key": "file:new.md", "content": "新记忆"}
                    ),
                    state,
                )[-1],
            )

            persisted = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(
            persisted["events"],
            [
                {"type": "seed-event", "content": "旧事件"},
                {"type": "tool_result", "tool_name": "read_file"},
            ],
        )
        self.assertEqual(
            persisted["memory_entries"],
            [
                {"category": "workspace", "source_key": "file:old.md", "content": "旧记忆"},
                {"category": "workspace", "source_key": "file:new.md", "content": "新记忆"},
            ],
        )
        self.assertEqual(persisted["compact_state"]["summary_text"], "旧摘要")
        self.assertEqual(persisted["compact_state"]["source_message_count"], 2)
        self.assertEqual(persisted["compact_state"]["source_memory_entry_count"], 1)

    @mock.patch("backend.chat.OpenAI")
    def test_write_file_returns_success_even_when_sidecar_persistence_fails(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )
            target_path = Path(project["project_dir"]) / "notes" / "draft.md"

            with mock.patch.object(
                handler,
                "_save_conversation_state_atomically",
                side_effect=RuntimeError("sidecar exploded"),
            ):
                result = handler._execute_tool(
                    project["id"],
                    self._make_tool_call(
                        "write_file",
                        json.dumps(
                            {"file_path": "notes/draft.md", "content": "保留主写入成功"},
                            ensure_ascii=False,
                        ),
                    ),
                )
            file_exists = target_path.exists()
            written_content = target_path.read_text(encoding="utf-8") if file_exists else None

        self.assertEqual(result["status"], "success")
        self.assertTrue(file_exists)
        self.assertEqual(written_content, "保留主写入成功")

    @mock.patch("backend.chat.OpenAI")
    def test_read_file_persists_workspace_memory_with_normalized_source_key(self, mock_openai):
        del mock_openai
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            project_dir = Path(project["project_dir"])
            target_path = project_dir / "plan" / "outline.md"
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text("# 大纲", encoding="utf-8")
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )

            result = handler._execute_tool(
                project["id"],
                self._make_tool_call(
                    "read_file",
                    json.dumps({"file_path": "plan\\outline.md"}, ensure_ascii=False),
                ),
            )

            state_path = project_dir / "conversation_state.json"
            persisted = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(persisted["memory_entries"]), 1)
        self.assertEqual(persisted["memory_entries"][0]["category"], "workspace")
        self.assertEqual(persisted["memory_entries"][0]["source_key"], "file:plan/outline.md")
        self.assertEqual(persisted["memory_entries"][0]["source_ref"], "plan/outline.md")
        self.assertEqual(persisted["memory_entries"][0]["content"], "# 大纲")

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_success_persists_evidence_event_and_memory_with_final_url(
        self,
        mock_getaddrinfo,
        mock_get,
        mock_openai,
    ):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        final_url = "https://example.com/final-article"
        mock_get.return_value = self._make_fetch_response(
            url=final_url,
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=(
                b"<html><head><title>Example</title></head>"
                b"<body><article>Readable body.</article></body></html>"
            ),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )

            result = handler._execute_tool(
                project["id"],
                self._make_tool_call(
                    "fetch_url",
                    json.dumps({"url": "https://example.com/start"}, ensure_ascii=False),
                ),
            )

            state_path = Path(project["project_dir"]) / "conversation_state.json"
            persisted = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["final_url"], final_url)
        self.assertEqual(result["url"], final_url)
        self.assertEqual(len(persisted["events"]), 1)
        self.assertEqual(persisted["events"][0]["tool_name"], "fetch_url")
        self.assertEqual(persisted["events"][0]["category"], "evidence")
        self.assertEqual(persisted["events"][0]["source_key"], f"url:{final_url}")
        self.assertIn("recorded_at", persisted["events"][0])
        self.assertNotIn("arguments", persisted["events"][0])
        self.assertNotIn("result", persisted["events"][0])
        self.assertEqual(len(persisted["memory_entries"]), 1)
        self.assertEqual(persisted["memory_entries"][0]["category"], "evidence")
        self.assertEqual(persisted["memory_entries"][0]["source_key"], f"url:{final_url}")

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_failure_does_not_persist_long_term_memory(
        self,
        mock_getaddrinfo,
        mock_get,
        mock_openai,
    ):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        mock_get.return_value = self._make_fetch_response(
            url="https://example.com/missing",
            status_code=404,
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=b"<html><body>missing</body></html>",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            workspace_dir = Path(tmpdir) / "workspace"
            engine = SkillEngine(projects_dir, self.repo_skill_dir)
            project = engine.create_project(
                name="demo",
                workspace_dir=str(workspace_dir),
                project_type="strategy-consulting",
                theme="AI strategy review",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
            )
            handler = ChatHandler(
                self._make_settings(
                    mode="managed",
                    managed_model="gemini-3-flash",
                    projects_dir=projects_dir,
                ),
                engine,
            )

            result = handler._execute_tool(
                project["id"],
                self._make_tool_call(
                    "fetch_url",
                    json.dumps({"url": "https://example.com/missing"}, ensure_ascii=False),
                ),
            )

            state_path = Path(project["project_dir"]) / "conversation_state.json"

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "http_status_404")
        self.assertFalse(state_path.exists())

    @mock.patch("backend.chat.OpenAI")
    def test_web_search_stops_retrying_after_search_backend_error(self, mock_openai):
        settings = Settings(
            mode="managed",
            managed_base_url="https://newapi.z0y0h.work/client/v1",
            managed_model="gemini-3-flash",
            projects_dir=Path(tempfile.gettempdir()) / "dummy-projects",
            skill_dir=self.repo_skill_dir,
        )
        handler = ChatHandler(settings, SkillEngine(settings.projects_dir, self.repo_skill_dir))
        handler._turn_context = {"can_write_non_plan": True}
        fake_router = mock.Mock()
        fake_router.search.return_value = {
            "status": "error",
            "error_type": "backend_error",
            "message": "搜索服务暂时不可用，请稍后再试。",
            "disable_for_turn": True,
        }

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

        with mock.patch.object(handler, "_get_search_router", return_value=fake_router):
            first_result = handler._execute_tool("demo", tool_call)
            second_result = handler._execute_tool("demo", tool_call)

        self.assertEqual(first_result["status"], "error")
        self.assertIn("搜索服务暂时不可用", first_result["message"])
        self.assertEqual(second_result["status"], "error")
        self.assertIn("本轮", second_result["message"])
        fake_router.search.assert_called_once()

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
    @mock.patch("backend.chat.curl_cffi_requests", create=True)
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_prefers_curl_cffi_before_requests(
        self,
        mock_getaddrinfo,
        mock_requests_get,
        mock_curl_cffi_requests,
        mock_openai,
    ):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        mock_curl_cffi_requests.get.return_value = self._make_fetch_response(
            url="https://example.com/article",
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=b"<html><body><article>curl_cffi body.</article></body></html>",
        )
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        result = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"https://example.com/article"}'),
        )

        self.assertEqual(result["status"], "success")
        self.assertIn("curl_cffi body", result["content"])
        mock_curl_cffi_requests.get.assert_called()
        mock_requests_get.assert_not_called()

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.curl_cffi_requests", create=True)
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_falls_back_to_requests_when_curl_cffi_errors(
        self,
        mock_getaddrinfo,
        mock_requests_get,
        mock_curl_cffi_requests,
        mock_openai,
    ):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        mock_curl_cffi_requests.get.side_effect = RuntimeError("curl transport failed")
        mock_requests_get.return_value = self._make_fetch_response(
            url="https://example.com/article",
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=b"<html><body><article>requests fallback body.</article></body></html>",
        )
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        result = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"https://example.com/article"}'),
        )

        self.assertEqual(result["status"], "success")
        self.assertIn("requests fallback body", result["content"])
        mock_curl_cffi_requests.get.assert_called()
        mock_requests_get.assert_called()

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
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_success_preserves_url_and_adds_final_url(self, mock_getaddrinfo, mock_get, mock_openai):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        mock_get.return_value = self._make_fetch_response(
            url="https://example.com/final",
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=(
                b"<html><head><title>Example</title></head>"
                b"<body><article>Hello world.</article></body></html>"
            ),
        )
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        result = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"https://example.com/start"}'),
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["url"], "https://example.com/final")
        self.assertEqual(result["final_url"], "https://example.com/final")
        self.assertEqual(result["content_type"], "text/html")
        self.assertNotIn("error_type", result)

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_allows_same_host_redirect(self, mock_getaddrinfo, mock_get, mock_openai):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        mock_get.side_effect = [
            self._make_fetch_response(
                url="https://example.com/start",
                status_code=302,
                headers={"Location": "/final", "Content-Type": "text/html"},
            ),
            self._make_fetch_response(
                url="https://example.com/final",
                headers={"Content-Type": "text/html; charset=utf-8"},
                body=b"<html><body><article>Readable body.</article></body></html>",
            ),
        ]
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        result = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"https://example.com/start"}'),
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["final_url"], "https://example.com/final")
        self.assertGreaterEqual(mock_get.call_count, 2)

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_allows_www_bare_domain_redirect(self, mock_getaddrinfo, mock_get, mock_openai):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        mock_get.side_effect = [
            self._make_fetch_response(
                url="https://example.com/start",
                status_code=302,
                headers={"Location": "https://www.example.com/final", "Content-Type": "text/html"},
            ),
            self._make_fetch_response(
                url="https://www.example.com/final",
                headers={"Content-Type": "text/html; charset=utf-8"},
                body=b"<html><body><article>Readable body.</article></body></html>",
            ),
        ]
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        result = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"https://example.com/start"}'),
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["final_url"], "https://www.example.com/final")
        self.assertGreaterEqual(mock_get.call_count, 2)

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_allows_public_cross_host_redirect(self, mock_getaddrinfo, mock_get, mock_openai):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        mock_get.side_effect = [
            self._make_fetch_response(
                url="https://example.com/start",
                status_code=302,
                headers={"Location": "https://canonical.example.net/final", "Content-Type": "text/html"},
            ),
            self._make_fetch_response(
                url="https://canonical.example.net/final",
                headers={"Content-Type": "text/html; charset=utf-8"},
                body=b"<html><body><article>Canonical target.</article></body></html>",
            ),
        ]
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        result = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"https://example.com/start"}'),
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["final_url"], "https://canonical.example.net/final")

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_blocks_private_cross_host_redirect(self, mock_getaddrinfo, mock_get, mock_openai):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        mock_get.return_value = self._make_fetch_response(
            url="https://example.com/start",
            status_code=302,
            headers={"Location": "https://localhost/private", "Content-Type": "text/html"},
        )
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        result = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"https://example.com/start"}'),
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("不允许访问", result["message"])

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_rejects_redirect_limit(self, mock_getaddrinfo, mock_get, mock_openai):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        mock_get.side_effect = [
            self._make_fetch_response(
                url=f"https://example.com/{index}",
                status_code=302,
                headers={"Location": f"/{index + 1}", "Content-Type": "text/html"},
            )
            for index in range(8)
        ]
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        result = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"https://example.com/0"}'),
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "redirect_limit_exceeded")

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_upgrades_http_to_https_first(self, mock_getaddrinfo, mock_get, mock_openai):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        mock_get.return_value = self._make_fetch_response(
            url="https://example.com/page",
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=b"<html><body><article>Secure body.</article></body></html>",
        )
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        result = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"http://example.com/page"}'),
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(mock_get.call_args_list[0].args[0], "https://example.com/page")

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_falls_back_to_http_only_for_tls_failure(self, mock_getaddrinfo, mock_get, mock_openai):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        mock_get.side_effect = [
            requests.exceptions.SSLError("tls failed"),
            self._make_fetch_response(
                url="http://example.com/page",
                headers={"Content-Type": "text/html; charset=utf-8"},
                body=b"<html><body><article>HTTP fallback body.</article></body></html>",
            ),
        ]
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        result = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"http://example.com/page"}'),
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            [call.args[0] for call in mock_get.call_args_list],
            ["https://example.com/page", "http://example.com/page"],
        )

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_rejects_response_body_over_hard_limit(self, mock_getaddrinfo, mock_get, mock_openai):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        mock_get.return_value = self._make_fetch_response(
            url="https://example.com/huge",
            headers={"Content-Type": "text/plain; charset=utf-8"},
            body=b"x" * (ChatHandler.FETCH_URL_MAX_BYTES + 1),
        )
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        result = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"https://example.com/huge"}'),
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "response_too_large")

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_allows_large_html_page_under_updated_limit(self, mock_getaddrinfo, mock_get, mock_openai):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        large_article = (
            "<html><head><title>Large page</title></head><body><article>"
            + ("人工智能发展趋势 " * 45000)
            + "</article></body></html>"
        ).encode("utf-8")
        self.assertGreater(len(large_article), 700_000)
        mock_get.return_value = self._make_fetch_response(
            url="https://example.com/large",
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=large_article,
        )
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        result = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"https://example.com/large"}'),
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["title"], "Large page")
        self.assertTrue(result["truncated"])

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_decodes_meta_charset_gb18030_html(self, mock_getaddrinfo, mock_get, mock_openai):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        title_text = "政策"
        body_text = "中国经济发展"
        body = (
            f'<html><head><meta charset="gb18030"><title>{title_text}</title></head>'
            f"<body><article>{body_text}</article></body></html>"
        ).encode("gb18030")
        mock_get.return_value = self._make_fetch_response(
            url="https://gov.example.cn/policy",
            headers={"Content-Type": "text/html"},
            body=body,
            apparent_encoding="utf-8",
        )
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        result = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"https://gov.example.cn/policy"}'),
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["title"], title_text)
        self.assertIn(body_text, result["content"])

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_classifies_challenge_page(self, mock_getaddrinfo, mock_get, mock_openai):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        mock_get.return_value = self._make_fetch_response(
            url="https://blocked.example.com",
            status_code=403,
            headers={"Content-Type": "text/html", "cf-mitigated": "challenge"},
            body=b"<html><title>Just a moment...</title><body>cf challenge ray id</body></html>",
        )
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        result = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"https://blocked.example.com"}'),
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "challenge_page")

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_classifies_baidu_shell_as_non_readable(self, mock_getaddrinfo, mock_get, mock_openai):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        mock_get.return_value = self._make_fetch_response(
            url="https://baike.baidu.com/item/demo",
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=(
                "<html><title>百度安全验证</title><body>"
                "访问过于频繁，请稍后再试"
                "<script>location.href='/index/'</script>"
                "</body></html>"
            ).encode("utf-8"),
        )
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        result = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"https://baike.baidu.com/item/demo"}'),
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "non_readable_page")

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_caches_success_within_same_project(self, mock_getaddrinfo, mock_get, mock_openai):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        mock_get.return_value = self._make_fetch_response(
            url="https://example.com/article",
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=b"<html><body><article>Cache me.</article></body></html>",
        )
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        first = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"https://example.com/article"}'),
        )
        second = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"https://example.com/article"}'),
        )

        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "success")
        self.assertEqual(mock_get.call_count, 1)

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_negative_caches_404(self, mock_getaddrinfo, mock_get, mock_openai):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        mock_get.return_value = self._make_fetch_response(
            url="https://example.com/missing",
            status_code=404,
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=b"<html><body>missing</body></html>",
        )
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        first = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"https://example.com/missing"}'),
        )
        second = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"https://example.com/missing"}'),
        )

        self.assertEqual(first["error_type"], "http_status_404")
        self.assertEqual(second["error_type"], "http_status_404")
        self.assertEqual(mock_get.call_count, 1)

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_negative_caches_redirect_limit_exceeded(self, mock_getaddrinfo, mock_get, mock_openai):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        mock_get.side_effect = [
            self._make_fetch_response(
                url=f"https://example.com/{index}",
                status_code=302,
                headers={"Location": f"/{index + 1}", "Content-Type": "text/html"},
            )
            for index in range(8)
        ]
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        first = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"https://example.com/start"}'),
        )
        second = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"https://example.com/start"}'),
        )

        self.assertEqual(first["error_type"], "redirect_limit_exceeded")
        self.assertEqual(second["error_type"], "redirect_limit_exceeded")
        self.assertEqual(mock_get.call_count, 6)

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_does_not_negative_cache_403(self, mock_getaddrinfo, mock_get, mock_openai):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        mock_get.return_value = self._make_fetch_response(
            url="https://blocked.example.com",
            status_code=403,
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=b"<html><body>Forbidden</body></html>",
        )
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        first = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"https://blocked.example.com"}'),
        )
        second = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"https://blocked.example.com"}'),
        )

        self.assertEqual(first["error_type"], "http_status_403")
        self.assertEqual(second["error_type"], "http_status_403")
        self.assertEqual(mock_get.call_count, 2)

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_cache_is_scoped_per_project_id(self, mock_getaddrinfo, mock_get, mock_openai):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        mock_get.return_value = self._make_fetch_response(
            url="https://example.com/article",
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=b"<html><body><article>Project cache.</article></body></html>",
        )
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        first = handler._execute_tool(
            "project-a",
            self._make_tool_call("fetch_url", '{"url":"https://example.com/article"}'),
        )
        second = handler._execute_tool(
            "project-b",
            self._make_tool_call("fetch_url", '{"url":"https://example.com/article"}'),
        )

        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "success")
        self.assertEqual(mock_get.call_count, 2)

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_cache_separates_http_fallback_mode(self, mock_getaddrinfo, mock_get, mock_openai):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        mock_get.side_effect = [
            requests.exceptions.SSLError("tls failed"),
            self._make_fetch_response(
                url="http://example.com/page",
                headers={"Content-Type": "text/html; charset=utf-8"},
                body=b"<html><body><article>HTTP fallback body.</article></body></html>",
            ),
            self._make_fetch_response(
                url="https://example.com/page",
                headers={"Content-Type": "text/html; charset=utf-8"},
                body=b"<html><body><article>HTTPS body.</article></body></html>",
            ),
        ]
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        first = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"http://example.com/page"}'),
        )
        second = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"https://example.com/page"}'),
        )

        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "success")
        self.assertIn("HTTP fallback body", first["content"])
        self.assertIn("HTTPS body", second["content"])
        self.assertEqual(mock_get.call_count, 3)

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_reuses_http_fallback_cache_without_retrying_https(
        self,
        mock_getaddrinfo,
        mock_get,
        mock_openai,
    ):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        mock_get.side_effect = [
            requests.exceptions.SSLError("tls failed"),
            self._make_fetch_response(
                url="http://example.com/page",
                headers={"Content-Type": "text/html; charset=utf-8"},
                body=b"<html><body><article>HTTP fallback body.</article></body></html>",
            ),
        ]
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        first = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"http://example.com/page"}'),
        )
        second = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"http://example.com/page"}'),
        )

        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "success")
        self.assertEqual(mock_get.call_count, 2)

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_returns_plain_text_verbatim(self, mock_getaddrinfo, mock_get, mock_openai):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        mock_get.return_value = self._make_fetch_response(
            url="https://example.com/readme.txt",
            headers={"Content-Type": "text/plain; charset=utf-8"},
            body=b"line one\nline two\n",
        )
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        result = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"https://example.com/readme.txt"}'),
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["content"], "line one\nline two")
        self.assertEqual(result["content_type"], "text/plain")

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_rejects_pdf_with_typed_error(self, mock_getaddrinfo, mock_get, mock_openai):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        mock_get.return_value = self._make_fetch_response(
            url="https://example.com/file.pdf",
            headers={"Content-Type": "application/pdf"},
            body=b"%PDF-1.7",
        )
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        result = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"https://example.com/file.pdf"}'),
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "unsupported_content_type")

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_falls_back_when_trafilatura_returns_empty(self, mock_getaddrinfo, mock_get, mock_openai):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        mock_get.return_value = self._make_fetch_response(
            url="https://example.com/fallback",
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=(
                b"<html><body><main><h1>Title</h1><p>Paragraph one.</p>"
                b"<p>Paragraph two.</p></main></body></html>"
            ),
        )
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        with mock.patch("trafilatura.extract", return_value=""):
            result = handler._execute_tool(
                "demo",
                self._make_tool_call("fetch_url", '{"url":"https://example.com/fallback"}'),
            )

        self.assertEqual(result["status"], "success")
        self.assertIn("Paragraph one.", result["content"])

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_rejects_script_shell_when_trafilatura_returns_empty(
        self,
        mock_getaddrinfo,
        mock_get,
        mock_openai,
    ):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        mock_get.return_value = self._make_fetch_response(
            url="https://example.com/redirect",
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=(
                b"<html><body><script>window.location='/login'</script>"
                b"<div>Redirecting...</div></body></html>"
            ),
        )
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        with mock.patch("trafilatura.extract", return_value=""):
            result = handler._execute_tool(
                "demo",
                self._make_tool_call("fetch_url", '{"url":"https://example.com/redirect"}'),
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "non_readable_page")

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_allows_real_article_that_mentions_redirecting(
        self,
        mock_getaddrinfo,
        mock_get,
        mock_openai,
    ):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        mock_get.return_value = self._make_fetch_response(
            url="https://example.com/article-about-redirects",
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=(
                b"<html><body><article><h1>Redirect guide</h1>"
                b"<p>If your app shows Redirecting..., inspect the window.location flow first.</p>"
                b"<p>This article explains when to use location.replace and how to avoid loops.</p>"
                b"</article></body></html>"
            ),
        )
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        with mock.patch("trafilatura.extract", return_value=""):
            result = handler._execute_tool(
                "demo",
                self._make_tool_call("fetch_url", '{"url":"https://example.com/article-about-redirects"}'),
            )

        self.assertEqual(result["status"], "success")
        self.assertIn("window.location flow", result["content"])

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_prefers_utf8_when_header_charset_is_misdeclared(
        self,
        mock_getaddrinfo,
        mock_get,
        mock_openai,
    ):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        expected_text = "中国经济发展"
        mock_get.return_value = self._make_fetch_response(
            url="https://example.com/misdeclared",
            headers={"Content-Type": "text/html; charset=latin1"},
            body=f"<html><body><article>{expected_text}</article></body></html>".encode("utf-8"),
            apparent_encoding="utf-8",
        )
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        result = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"https://example.com/misdeclared"}'),
        )

        self.assertEqual(result["status"], "success")
        self.assertIn(expected_text, result["content"])

    @mock.patch("backend.chat.OpenAI")
    @mock.patch("backend.chat.requests.get")
    @mock.patch("backend.chat.socket.getaddrinfo")
    def test_fetch_url_ignores_apparent_encoding_when_stream_is_already_consumed(
        self,
        mock_getaddrinfo,
        mock_get,
        mock_openai,
    ):
        del mock_openai
        self._allow_public_fetch_host(mock_getaddrinfo)
        response = self._make_fetch_response(
            url="https://example.com/article",
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=b"<html><body><article>Readable body.</article></body></html>",
        )
        type(response).apparent_encoding = mock.PropertyMock(side_effect=RuntimeError("already consumed"))
        mock_get.return_value = response
        handler = ChatHandler(self._make_settings(), SkillEngine(self._make_settings().projects_dir, self.repo_skill_dir))

        result = handler._execute_tool(
            "demo",
            self._make_tool_call("fetch_url", '{"url":"https://example.com/article"}'),
        )

        self.assertEqual(result["status"], "success")
        self.assertIn("Readable body.", result["content"])

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

    @mock.patch("backend.chat.OpenAI")
    def test_detect_stage_keyword_confirm_outline_triggers_on_any_stage(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()

        for stage in ("S0", "S1", "S4", "S5", "S7"):
            self.assertEqual(
                handler._detect_stage_keyword("确认大纲", stage),
                ("set", "outline_confirmed_at"),
            )

    @mock.patch("backend.chat.OpenAI")
    def test_detect_stage_keyword_weak_can_no_longer_advances_on_any_stage(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()

        self.assertIsNone(handler._detect_stage_keyword("可以", "S1"))
        self.assertIsNone(handler._detect_stage_keyword("可以", "S4"))

    @mock.patch("backend.chat.OpenAI")
    def test_detect_stage_keyword_s4_weak_affirmations_do_not_start_review(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()

        for message in ("挺好", "挺好继续写下一节"):
            self.assertIsNone(handler._detect_stage_keyword(message, "S4"))

    @mock.patch("backend.chat.OpenAI")
    def test_detect_stage_keyword_s5_weak_can_no_longer_advances_review_passed(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()

        self.assertIsNone(handler._detect_stage_keyword("可以", "S5"))

    @mock.patch("backend.chat.OpenAI")
    def test_detect_stage_keyword_question_does_not_trigger(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()

        self.assertIsNone(handler._detect_stage_keyword("就按这个大纲写吗？", "S1"))

    @mock.patch("backend.chat.OpenAI")
    def test_detect_stage_keyword_ma_suffix_is_not_treated_as_question(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()

        self.assertFalse(handler._is_question("开始写报告嘛"))
        self.assertEqual(
            handler._detect_stage_keyword("确认大纲嘛", "S1"),
            ("set", "outline_confirmed_at"),
        )

    @mock.patch("backend.chat.OpenAI")
    def test_detect_stage_keyword_weak_suffix_no_longer_overrides_strong_hit(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()

        self.assertEqual(
            handler._detect_stage_keyword("审查通过归档吧", "S7"),
            ("set", "review_passed_at"),
        )

    @mock.patch("backend.chat.OpenAI")
    def test_detect_stage_keyword_multiple_strong_hits_take_highest_stage_rank(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()

        self.assertEqual(
            handler._detect_stage_keyword("开始审查审查通过", "S4"),
            ("set", "review_passed_at"),
        )

    @mock.patch("backend.chat.OpenAI")
    def test_detect_stage_keyword_negated_messages_do_not_trigger(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()

        cases = [
            ("先不要开始审查", "S4"),
            ("别开始审查", "S4"),
            ("不是说审查通过了吗", "S5"),
            ("不要归档吧", "S7"),
            ("不太建议现在开始审查", "S4"),
            ("其实我不想现在开始审查", "S4"),
            ("先别确认大纲", "S1"),
        ]
        for message, stage in cases:
            self.assertIsNone(
                handler._detect_stage_keyword(message, stage),
                f"message triggered unexpectedly: {message}",
            )

    @mock.patch("backend.chat.OpenAI")
    def test_detect_stage_keyword_positive_fei_phrase_is_not_treated_as_negation(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()

        self.assertEqual(
            handler._detect_stage_keyword("非常同意，就按这个大纲写", "S1"),
            ("set", "outline_confirmed_at"),
        )

    @mock.patch("backend.chat.OpenAI")
    def test_detect_stage_keyword_rollback_returns_outline_clear_action(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()

        self.assertEqual(
            handler._detect_stage_keyword("大纲再改下", "S4"),
            ("clear", "outline_confirmed_at"),
        )

    @mock.patch("backend.chat.OpenAI")
    def test_build_turn_context_defers_outline_checkpoint_until_finalize(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_stage_one_prerequisites(self.project_dir)

        turn_context = handler._build_turn_context(self.project_id, "确认大纲，开始写")
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)

        self.assertNotIn("outline_confirmed_at", checkpoints)
        self.assertEqual(
            turn_context["pending_stage_keyword"],
            ("set", "outline_confirmed_at"),
        )
        self._finalize_assistant_for_test(handler, "好的，按大纲写。")
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertIn("outline_confirmed_at", checkpoints)

    @mock.patch("backend.chat.OpenAI")
    def test_build_turn_context_records_checkpoint_event_on_set_finalize(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_stage_one_prerequisites(self.project_dir)

        handler._turn_context = handler._build_turn_context(self.project_id, "确认大纲")

        self.assertIsNone(handler._turn_context["checkpoint_event"])
        self.assertEqual(
            handler._turn_context["pending_stage_keyword"],
            ("set", "outline_confirmed_at"),
        )
        self._finalize_assistant_for_test(handler, "好的，按大纲写。")
        self.assertEqual(
            handler._turn_context["checkpoint_event"],
            {"action": "set", "key": "outline_confirmed_at"},
        )

    @mock.patch("backend.chat.OpenAI")
    def test_build_turn_context_rollback_clears_outline_checkpoint_cascade(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        handler.skill_engine._save_stage_checkpoint(self.project_dir, "outline_confirmed_at")
        handler.skill_engine._save_stage_checkpoint(self.project_dir, "review_started_at")

        handler._build_turn_context(self.project_id, "大纲再改下")
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)

        self.assertNotIn("outline_confirmed_at", checkpoints)
        self.assertNotIn("review_started_at", checkpoints)

    @mock.patch("backend.chat.OpenAI")
    def test_build_turn_context_records_checkpoint_event_on_clear(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        handler.skill_engine._save_stage_checkpoint(self.project_dir, "outline_confirmed_at")

        handler._turn_context = handler._build_turn_context(self.project_id, "大纲再改下")

        self.assertEqual(
            handler._turn_context["checkpoint_event"],
            {"action": "clear", "key": "outline_confirmed_at"},
        )

    @mock.patch("backend.chat.OpenAI")
    def test_build_turn_context_empty_message_has_no_stage_checkpoint_side_effect(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()

        handler._build_turn_context(self.project_id, "")

        self.assertEqual(handler.skill_engine._load_stage_checkpoints(self.project_dir), {})

    @mock.patch("backend.chat.OpenAI")
    def test_build_turn_context_no_checkpoint_event_when_no_keyword(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()

        handler._turn_context = handler._build_turn_context(self.project_id, "随便聊聊")

        self.assertIsNone(handler._turn_context["checkpoint_event"])

    @mock.patch("backend.chat.OpenAI")
    def test_should_allow_non_plan_write_blocking_message_beats_outline_blanket_pass(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_stage_one_prerequisites(self.project_dir)
        handler.skill_engine._save_stage_checkpoint(self.project_dir, "outline_confirmed_at")

        self.assertFalse(handler._should_allow_non_plan_write(self.project_id, "先别写正文"))
        self.assertIn(
            "outline_confirmed_at",
            handler.skill_engine._load_stage_checkpoints(self.project_dir),
        )

    @mock.patch("backend.chat.OpenAI")
    def test_should_allow_non_plan_write_rejects_s2_even_with_outline_checkpoint(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        handler.skill_engine._save_stage_checkpoint(self.project_dir, "s0_interview_done_at")
        self._write_stage_one_prerequisites(self.project_dir)
        handler.skill_engine._save_stage_checkpoint(self.project_dir, "outline_confirmed_at")

        self.assertEqual(handler.skill_engine._infer_stage_state(self.project_dir)["stage_code"], "S2")
        self.assertFalse(handler._should_allow_non_plan_write(self.project_id, "继续"))

    @mock.patch("backend.chat.OpenAI")
    def test_build_turn_context_weak_affirmation_has_no_checkpoint_side_effect(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        handler.skill_engine._save_stage_checkpoint(self.project_dir, "s0_interview_done_at")

        turn_context = handler._build_turn_context(self.project_id, "没问题，继续吧")
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)

        self.assertNotIn("outline_confirmed_at", checkpoints)
        self.assertIsNone(turn_context["checkpoint_event"])
        self.assertEqual(turn_context["pending_system_notices"], [])

    @mock.patch("backend.chat.OpenAI")
    def test_build_turn_context_strong_outline_keyword_without_effective_outline_emits_prereq_notice_on_finalize(
        self,
        mock_openai,
    ):
        del mock_openai
        handler = self._make_handler_with_project()
        handler.skill_engine._save_stage_checkpoint(self.project_dir, "s0_interview_done_at")

        turn_context = handler._build_turn_context(self.project_id, "确认大纲")
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)

        self.assertNotIn("outline_confirmed_at", checkpoints)
        self.assertIsNone(turn_context["checkpoint_event"])
        self._finalize_assistant_for_test(handler, "好的，按大纲写。")
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertNotIn("outline_confirmed_at", checkpoints)
        self.assertIsNone(turn_context["checkpoint_event"])
        self.assertEqual(
            turn_context["pending_system_notices"],
            [
                {
                    "type": "system_notice",
                    "category": "stage_keyword_prereq_missing",
                    "path": "plan/outline.md",
                    "reason": "需要先生成有效报告大纲，才能确认大纲并进入资料采集。",
                    "user_action": "请先让助手补齐 `plan/outline.md`，再确认大纲。",
                    "surface_to_user": True,
                }
            ],
        )

    @mock.patch("backend.chat.OpenAI")
    def test_build_turn_context_confirm_outline_turn_allows_non_plan_write_after_finalize(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_stage_one_prerequisites(self.project_dir)

        turn_context = handler._build_turn_context(self.project_id, "确认大纲")

        self.assertFalse(turn_context["can_write_non_plan"])
        self.assertNotIn(
            "outline_confirmed_at",
            handler.skill_engine._load_stage_checkpoints(self.project_dir),
        )
        self._finalize_assistant_for_test(handler, "好的，按大纲写。")
        self.assertTrue(handler._should_allow_non_plan_write(self.project_id, "确认大纲"))
        self.assertIn(
            "outline_confirmed_at",
            handler.skill_engine._load_stage_checkpoints(self.project_dir),
        )

    @mock.patch("backend.chat.OpenAI")
    def test_should_block_start_writing_in_fresh_s0(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()

        self.assertFalse(handler._should_allow_non_plan_write(self.project_id, "开始写"))


class KeywordTableRestructureTests(unittest.TestCase):
    def test_weak_advance_table_absent(self):
        from backend.chat import ChatHandler
        self.assertFalse(
            hasattr(ChatHandler, "_WEAK_ADVANCE_BY_STAGE"),
            "_WEAK_ADVANCE_BY_STAGE must be removed per spec",
        )

    def test_s0_strong_keywords_present(self):
        from backend.chat import ChatHandler
        self.assertIn("s0_interview_done_at", ChatHandler._STRONG_ADVANCE_KEYWORDS)
        for phrase in ["跳过访谈", "不用问了", "先写大纲吧", "够了开始吧", "直接开始"]:
            self.assertIn(
                phrase,
                ChatHandler._STRONG_ADVANCE_KEYWORDS["s0_interview_done_at"],
            )

    def test_stage_rank_has_s0_first(self):
        from backend.chat import ChatHandler
        self.assertEqual(ChatHandler._STAGE_RANK["s0_interview_done_at"], 0)
        self.assertEqual(ChatHandler._STAGE_RANK["outline_confirmed_at"], 1)


class WeakKeywordNoLongerTriggersTests(ChatRuntimeTests):
    def test_ok_in_s1_returns_none(self):
        handler = self._make_handler_with_project()
        result = handler._detect_stage_keyword("OK", "S1", self.project_id)
        self.assertIsNone(result)

    def test_keyi_in_s5_returns_none(self):
        handler = self._make_handler_with_project()
        result = handler._detect_stage_keyword("可以", "S5", self.project_id)
        self.assertIsNone(result)

    def test_strong_keyword_still_works(self):
        handler = self._make_handler_with_project()
        result = handler._detect_stage_keyword("确认大纲", "S1", self.project_id)
        self.assertEqual(result, ("set", "outline_confirmed_at"))


for _inherited_test_name in dir(ChatRuntimeTests):
    if (
        _inherited_test_name.startswith("test_")
        and _inherited_test_name not in WeakKeywordNoLongerTriggersTests.__dict__
    ):
        setattr(WeakKeywordNoLongerTriggersTests, _inherited_test_name, None)
del _inherited_test_name


from backend.draft_action import DraftActionEvent


class DraftActionPreCheckTests(ChatRuntimeTests):
    def _seed_outline_confirmed(self, handler):
        """Helper: 让 outline_confirmed_at checkpoint 已 set + stage 推到 S4，
        否则 _validate_draft_action_event 会先在 stage_too_early/outline_not_confirmed
        分支拒绝，测不到 no_draft/section/replace 校验。"""
        # 用现有 _write_stage_one_prerequisites 准备 outline + research-plan
        self._write_stage_one_prerequisites(self.project_dir)
        # mock _infer_stage_state 返回 S4 + outline_confirmed_at 已 set
        # 简化：直接落 checkpoints + stage S4 推断逻辑会自然认到
        from datetime import datetime
        ckpt_path = self.project_dir / "stage_checkpoints.json"
        ckpt_path.write_text(
            json.dumps({"outline_confirmed_at": datetime.now().isoformat(timespec="seconds")}),
            encoding="utf-8",
        )
        stage_patcher = mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value={"stage_code": "S4"},
        )
        stage_patcher.start()
        self.addCleanup(stage_patcher.stop)

    def _seed_draft(self, content: str):
        """Helper: 写 content/report_draft_v1.md"""
        draft_path = self.project_dir / "content" / "report_draft_v1.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(content, encoding="utf-8")

    def test_section_intent_no_draft_returns_no_draft_message(self):
        handler = self._make_handler_with_project()
        self._seed_outline_confirmed(handler)
        # 故意不调 _seed_draft → draft 不存在
        event = DraftActionEvent(
            raw="...", intent="section", section_label="第二章",
        )
        result = handler._validate_draft_action_event(self.project_id, event)
        self.assertFalse(result.executable)
        self.assertEqual(result.ignored_reason, "no_draft")

    def test_replace_intent_no_draft_returns_no_draft_message(self):
        handler = self._make_handler_with_project()
        self._seed_outline_confirmed(handler)
        event = DraftActionEvent(
            raw="...", intent="replace", old_text="x", new_text="y",
        )
        result = handler._validate_draft_action_event(self.project_id, event)
        self.assertFalse(result.executable)
        self.assertEqual(result.ignored_reason, "no_draft")

    def test_continue_intent_no_draft_auto_degrade_to_begin(self):
        handler = self._make_handler_with_project()
        self._seed_outline_confirmed(handler)
        event = DraftActionEvent(raw="...", intent="continue")
        result = handler._validate_draft_action_event(self.project_id, event)
        self.assertTrue(result.executable)
        self.assertEqual(result.intent, "begin")  # 降级

    def test_section_label_unique_match(self):
        handler = self._make_handler_with_project()
        self._seed_outline_confirmed(handler)
        self._seed_draft(
            "# 报告\n\n## 第一章 序言\n背景内容\n\n## 第二章 战力演化\n演化分析\n"
        )
        event = DraftActionEvent(
            raw="...", intent="section", section_label="第二章 战力演化",
        )
        result = handler._validate_draft_action_event(self.project_id, event)
        self.assertTrue(result.executable)

    def test_section_label_partial_match_ambiguous(self):
        handler = self._make_handler_with_project()
        self._seed_outline_confirmed(handler)
        self._seed_draft(
            "# 报告\n\n## 第二章 战力演化\n正文\n\n## 第二章附录\n附录内容\n"
        )
        event = DraftActionEvent(
            raw="...", intent="section", section_label="第二章",
        )
        result = handler._validate_draft_action_event(self.project_id, event)
        self.assertFalse(result.executable)
        self.assertEqual(result.ignored_reason, "section_ambiguous")

    def test_section_label_with_extra_suffix_rejects(self):
        """v2 fix1 regression: tag with extra text after heading must NOT match.

        Old code used `_resolve_section_rewrite_targets` which checks
        `heading.label in section_label` (wrong direction). A tag like
        'section:第二章 战力演化 请重写' would falsely match heading
        '第二章 战力演化'. New matcher requires section_label to be a
        prefix of heading.
        """
        handler = self._make_handler_with_project()
        self._seed_outline_confirmed(handler)
        self._seed_draft(
            "# 报告\n\n## 第二章 战力演化\n演化分析\n"
        )
        event = DraftActionEvent(
            raw="...", intent="section",
            section_label="第二章 战力演化 请重写",
        )
        result = handler._validate_draft_action_event(self.project_id, event)
        self.assertFalse(result.executable)
        self.assertEqual(result.ignored_reason, "section_not_found")

    def test_replace_old_text_not_unique_rejects(self):
        handler = self._make_handler_with_project()
        self._seed_outline_confirmed(handler)
        self._seed_draft("X 出现一次。\n然后 X 又出现一次。\n")  # X 出现两次
        event = DraftActionEvent(
            raw="...", intent="replace", old_text="X", new_text="Y",
        )
        result = handler._validate_draft_action_event(self.project_id, event)
        self.assertFalse(result.executable)
        self.assertEqual(result.ignored_reason, "replace_target_invalid")


for _inherited_test_name in dir(ChatRuntimeTests):
    if (
        _inherited_test_name.startswith("test_")
        and _inherited_test_name not in DraftActionPreCheckTests.__dict__
    ):
        setattr(DraftActionPreCheckTests, _inherited_test_name, None)
del _inherited_test_name


class EmptyAssistantFallbackTests(ChatRuntimeTests):
    def test_finalize_empty_assistant_does_not_persist_assistant(self):
        handler = self._make_handler_with_project()
        history = []
        current_user = {"role": "user", "content": "test", "attached_material_ids": []}
        fallback = handler._finalize_empty_assistant_turn(
            self.project_id, history, current_user,
            diagnostic="stream_truncated",
        )
        self.assertEqual(len(history), 1)
        self.assertEqual(history[-1]["role"], "user")

    def test_finalize_empty_assistant_returns_user_visible_fallback(self):
        handler = self._make_handler_with_project()
        history = []
        current_user = {"role": "user", "content": "test", "attached_material_ids": []}
        fallback = handler._finalize_empty_assistant_turn(
            self.project_id, history, current_user,
            diagnostic="stream_truncated",
        )
        self.assertIn("没有产出可见回复", fallback)
        self.assertIn("换个说法再发", fallback)

    def test_finalize_empty_assistant_records_event(self):
        handler = self._make_handler_with_project()
        from backend.chat import USER_VISIBLE_FALLBACK
        history = []
        current_user = {"role": "user", "content": "test", "attached_material_ids": []}
        handler._finalize_empty_assistant_turn(
            self.project_id, history, current_user,
            diagnostic="tool_only_no_text",
        )
        state = handler._load_conversation_state(self.project_id, history)
        events = state.get("events", [])
        empty_events = [e for e in events if e.get("type") == "empty_assistant"]
        self.assertGreaterEqual(len(empty_events), 1)
        self.assertEqual(empty_events[-1]["diagnostic"], "tool_only_no_text")

    def test_user_visible_fallback_constant_exists(self):
        from backend.chat import USER_VISIBLE_FALLBACK
        self.assertIsInstance(USER_VISIBLE_FALLBACK, str)
        self.assertIn("没有产出可见回复", USER_VISIBLE_FALLBACK)


for _inherited_test_name in dir(ChatRuntimeTests):
    if (
        _inherited_test_name.startswith("test_")
        and _inherited_test_name not in EmptyAssistantFallbackTests.__dict__
    ):
        setattr(EmptyAssistantFallbackTests, _inherited_test_name, None)
del _inherited_test_name


class AssistantTurnOrchestratorTests(ChatRuntimeTests):
    def test_only_stage_ack_turn_records_checkpoint_then_a3(self):
        """assistant 只回 <stage-ack>outline_confirmed_at</stage-ack> →
        checkpoint 落戳 + 走 A3 不持久化空文本"""
        handler = self._make_handler_with_project()
        self._write_stage_one_prerequisites(self.project_dir)
        history = []
        current_user = {"role": "user", "content": "确认大纲", "attached_material_ids": []}
        assistant_msg = "<stage-ack>outline_confirmed_at</stage-ack>"
        result = self._finalize_assistant_for_test(
            handler, assistant_msg, history=history, current_user=current_user,
            current_turn_messages=[], user_message="确认大纲",
        )
        ckpt = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertIn("outline_confirmed_at", ckpt)
        self.assertEqual(history[-1]["role"], "user")
        from backend.chat import USER_VISIBLE_FALLBACK
        self.assertEqual(result, USER_VISIBLE_FALLBACK)

    def test_stage_ack_executed_before_empty_check(self):
        handler = self._make_handler_with_project()
        self._write_stage_one_prerequisites(self.project_dir)
        history = []
        current_user = {"role": "user", "content": "确认", "attached_material_ids": []}
        assistant_msg = "<stage-ack>outline_confirmed_at</stage-ack>"
        with mock.patch.object(handler, "_apply_stage_ack_event") as mock_apply:
            self._finalize_assistant_for_test(
                handler, assistant_msg, history=history, current_user=current_user,
                current_turn_messages=[], user_message="确认",
            )
            self.assertTrue(mock_apply.called)

    def test_normal_turn_persists_with_tool_log(self):
        handler = self._make_handler_with_project()
        history = []
        current_user = {"role": "user", "content": "搜一下", "attached_material_ids": []}
        assistant_msg = "好的，已搜到结果。"
        current_turn_messages = [
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "function": {"name": "web_search",
                 "arguments": '{"query":"猪猪侠"}'}},
            ]},
            {"role": "tool", "tool_call_id": "c1",
             "content": '{"status":"success","results":[1,2]}'},
        ]
        self._finalize_assistant_for_test(
            handler, assistant_msg, history=history, current_user=current_user,
            current_turn_messages=current_turn_messages, user_message="搜一下",
        )
        self.assertEqual(history[-1]["role"], "assistant")
        self.assertIn("好的，已搜到结果。", history[-1]["content"])
        self.assertIn("<!-- tool-log", history[-1]["content"])
        self.assertIn("web_search", history[-1]["content"])

    def test_tool_only_turn_walks_a3_no_tool_log_persisted(self):
        handler = self._make_handler_with_project()
        history = []
        current_user = {"role": "user", "content": "test", "attached_material_ids": []}
        assistant_msg = ""
        current_turn_messages = [
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "function": {"name": "web_search", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"status":"success"}'},
        ]
        result = self._finalize_assistant_for_test(
            handler, assistant_msg, history=history, current_user=current_user,
            current_turn_messages=current_turn_messages, user_message="test",
        )
        from backend.chat import USER_VISIBLE_FALLBACK
        self.assertEqual(result, USER_VISIBLE_FALLBACK)
        self.assertEqual(history[-1]["role"], "user")


for _inherited_test_name in dir(ChatRuntimeTests):
    if (
        _inherited_test_name.startswith("test_")
        and _inherited_test_name not in AssistantTurnOrchestratorTests.__dict__
    ):
        setattr(AssistantTurnOrchestratorTests, _inherited_test_name, None)
del _inherited_test_name


class S0SoftGateTests(ChatRuntimeTests):
    def _write_conversation(self, messages):
        import json
        (self.project_dir / "conversation.json").write_text(
            json.dumps(messages, ensure_ascii=False), encoding="utf-8"
        )

    def test_has_prior_assistant_true_when_assistant_exists(self):
        handler = self._make_handler_with_project()
        self._write_conversation([
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "请回答：1) 读者是谁？"},
        ])
        self.assertTrue(handler._has_prior_s0_assistant_turn(self.project_id))

    def test_has_prior_assistant_false_when_only_user(self):
        handler = self._make_handler_with_project()
        self._write_conversation([{"role": "user", "content": "你好"}])
        self.assertFalse(handler._has_prior_s0_assistant_turn(self.project_id))

    def test_tool_role_does_not_count(self):
        handler = self._make_handler_with_project()
        self._write_conversation([
            {"role": "user", "content": "你好"},
            {"role": "tool", "content": "..."},
        ])
        self.assertFalse(handler._has_prior_s0_assistant_turn(self.project_id))

    def test_s0_strong_keyword_before_any_assistant_ignored(self):
        handler = self._make_handler_with_project()
        self._write_conversation([{"role": "user", "content": "你好"}])
        result = handler._detect_stage_keyword(
            "直接开始", "S0", self.project_id
        )
        self.assertIsNone(result)

    def test_s0_strong_keyword_after_assistant_triggers(self):
        handler = self._make_handler_with_project()
        self._write_conversation([
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "请回答：1) 读者是谁？"},
        ])
        result = handler._detect_stage_keyword(
            "不用问了", "S0", self.project_id
        )
        self.assertEqual(result, ("set", "s0_interview_done_at"))

    def test_s0_without_project_id_rejects_s0_set(self):
        # Safety: if caller forgets project_id, s0 soft gate must err on the
        # side of not triggering (better to miss a set than to bypass the gate).
        handler = self._make_handler_with_project()
        result = handler._detect_stage_keyword("直接开始", "S0", None)
        self.assertIsNone(result)


for _inherited_test_name in dir(ChatRuntimeTests):
    if (
        _inherited_test_name.startswith("test_")
        and _inherited_test_name not in S0SoftGateTests.__dict__
    ):
        setattr(S0SoftGateTests, _inherited_test_name, None)
del _inherited_test_name


class NonPlanWriteS0S1PatchTests(ChatRuntimeTests):
    def _set_checkpoints(self, checkpoints):
        import json
        (self.project_dir / "stage_checkpoints.json").write_text(
            json.dumps(checkpoints), encoding="utf-8"
        )

    def test_s0_stage_direct_start_keyword_blocked(self):
        handler = self._make_handler_with_project()
        # project is fresh — stage should be S0 (no s0_interview_done_at yet)
        self.assertFalse(
            handler._should_allow_non_plan_write(self.project_id, "开始写")
        )

    def test_s1_without_outline_confirmed_blocked(self):
        handler = self._make_handler_with_project()
        self._set_checkpoints({"s0_interview_done_at": "2026-04-21T10:00:00"})
        # S1 (s0 done, no outline yet) should still block
        self.assertFalse(
            handler._should_allow_non_plan_write(self.project_id, "开始写报告")
        )

    def test_s4_with_outline_confirmed_allows_direct_start(self):
        handler = self._make_handler_with_project()
        # Advance to S4 by setting the relevant checkpoints
        self._set_checkpoints({
            "s0_interview_done_at": "2026-04-21T10:00:00",
            "outline_confirmed_at": "2026-04-21T11:00:00",
        })
        # Also create the effective outline / research-plan etc. to pass
        # _infer_stage_state — or just assert the S0/S1 patch: the patch
        # checks `stage_code in {S0, S1}` — so any stage outside that
        # set passes the patch. We need to set up enough fixture to reach
        # S4 in _infer_stage_state. The simplest way is to set outline
        # confirmed AND enough downstream flags. For this unit test we
        # test the PATCH, not _infer_stage_state itself: mock it.
        from unittest import mock
        with mock.patch.object(
            handler.skill_engine, "_infer_stage_state",
            return_value={"stage_code": "S4"},
        ):
            self.assertTrue(
                handler._should_allow_non_plan_write(self.project_id, "开始写正文")
            )


for _inherited_test_name in dir(ChatRuntimeTests):
    if (
        _inherited_test_name.startswith("test_")
        and _inherited_test_name not in NonPlanWriteS0S1PatchTests.__dict__
    ):
        setattr(NonPlanWriteS0S1PatchTests, _inherited_test_name, None)
del _inherited_test_name


class S0WriteFileGateTests(ChatRuntimeTests):
    S0_BLOCKED = [
        "plan/outline.md",
        "plan/research-plan.md",
        "plan/data-log.md",
        "plan/analysis-notes.md",
    ]
    S0_ALLOWED = [
        "plan/notes.md",
        "plan/references.md",
        "plan/project-overview.md",
    ]

    def _make_tool_call(self, file_path, content):
        import json
        from types import SimpleNamespace
        return SimpleNamespace(
            id="call-test",
            function=SimpleNamespace(
                name="write_file",
                arguments=json.dumps({"file_path": file_path, "content": content}),
            ),
        )

    def test_s0_blocks_each_of_four_files(self):
        handler = self._make_handler_with_project()
        for path in self.S0_BLOCKED:
            tool_call = self._make_tool_call(path, "# content\n" * 5)
            result = handler._execute_tool(self.project_id, tool_call)
            self.assertEqual(result["status"], "error", f"{path} should be blocked")
            self.assertIn("S0 阶段", result["message"])

    def test_s0_allows_non_blocked_plan_files(self):
        handler = self._make_handler_with_project()
        for path in self.S0_ALLOWED:
            self._read_file_for_turn(handler, path)
            tool_call = self._make_tool_call(path, "# content\n" * 5)
            result = handler._execute_tool(self.project_id, tool_call)
            self.assertEqual(
                result["status"], "success", f"{path} should be allowed"
            )

    def test_s0_write_emits_system_notice(self):
        handler = self._make_handler_with_project()
        tool_call = self._make_tool_call("plan/outline.md", "# x\n")
        handler._execute_tool(self.project_id, tool_call)
        notices = handler._turn_context.get("pending_system_notices", [])
        self.assertTrue(any(
            "S0 阶段" in n.get("reason", "") for n in notices
        ))

    def test_s0_write_notice_mentions_analysis_notes(self):
        handler = self._make_handler_with_project()
        tool_call = self._make_tool_call("plan/analysis-notes.md", "# x\n")
        handler._execute_tool(self.project_id, tool_call)
        notices = handler._turn_context.get("pending_system_notices", [])
        # Reason must list all four file categories per §1 spec
        reason_text = " ".join(n.get("reason", "") for n in notices)
        self.assertIn("分析笔记", reason_text)

    def test_post_s0_outline_write_not_blocked(self):
        import json
        handler = self._make_handler_with_project()
        (self.project_dir / "stage_checkpoints.json").write_text(
            json.dumps({"s0_interview_done_at": "2026-04-21T10:00:00"}),
            encoding="utf-8",
        )
        self._write_evidence_gate_prerequisites(self.project_dir)
        self._read_file_for_turn(handler, "plan/outline.md")
        tool_call = self._make_tool_call("plan/outline.md", "# 大纲\n## 章节\n" * 3)
        result = handler._execute_tool(self.project_id, tool_call)
        # S1 stage — outline.md is the expected write, should succeed
        self.assertEqual(result["status"], "success")


for _inherited_test_name in dir(ChatRuntimeTests):
    if (
        _inherited_test_name.startswith("test_")
        and _inherited_test_name not in S0WriteFileGateTests.__dict__
    ):
        setattr(S0WriteFileGateTests, _inherited_test_name, None)
del _inherited_test_name


class StreamTailGuardHelperTests(unittest.TestCase):
    """Unit tests for the pure stream_split_safe_tail helper.

    Semantics:
      stream_split_safe_tail(buffer) -> (safe_to_emit, held_tail)
      - If buffer does NOT yet contain the substring "<stage-ack", returns
        (buffer_without_possible_prefix_suffix, possible_prefix_suffix).
        "possible prefix suffix" = longest suffix of buffer that is a prefix of
        "<stage-ack" (i.e., the streaming split could be inside an incomplete
        opening tag).
      - If buffer contains "<stage-ack" at position p, returns
        (buffer[:p], buffer[p:]).
      - The held_tail is emitted by the caller only at stream close, after
        StageAckParser.strip() has scrubbed it.
    """

    def test_no_tag_no_dangling_prefix(self):
        from backend.chat import stream_split_safe_tail
        safe, held = stream_split_safe_tail("纯正文没 tag 可能。")
        self.assertEqual(safe, "纯正文没 tag 可能。")
        self.assertEqual(held, "")

    def test_chunk_cut_at_lt(self):
        from backend.chat import stream_split_safe_tail
        safe, held = stream_split_safe_tail("正文 <")
        self.assertEqual(safe, "正文 ")
        self.assertEqual(held, "<")

    def test_chunk_cut_at_lt_s(self):
        from backend.chat import stream_split_safe_tail
        safe, held = stream_split_safe_tail("正文 <s")
        self.assertEqual(held, "<s")

    def test_chunk_cut_at_partial_stage(self):
        from backend.chat import stream_split_safe_tail
        safe, held = stream_split_safe_tail("正文 <stage-a")
        self.assertEqual(held, "<stage-a")

    def test_full_open_tag_held(self):
        from backend.chat import stream_split_safe_tail
        safe, held = stream_split_safe_tail(
            "正文 <stage-ack>outline_confirmed_at"
        )
        self.assertEqual(safe, "正文 ")
        self.assertTrue(held.startswith("<stage-ack>"))

    def test_complete_tag_held(self):
        from backend.chat import stream_split_safe_tail
        safe, held = stream_split_safe_tail(
            "正文 <stage-ack>outline_confirmed_at</stage-ack>"
        )
        self.assertEqual(safe, "正文 ")
        # Full tag is held - caller strips it at stream close
        self.assertIn("<stage-ack>", held)

    def test_lt_without_stage_ack_not_held(self):
        from backend.chat import stream_split_safe_tail
        # "<" at end with no "<stage-ack" prefix possibility AFTER enough chars
        safe, held = stream_split_safe_tail("正文 <div>")
        self.assertEqual(safe, "正文 <div>")
        self.assertEqual(held, "")

    def test_multi_tag_tail_held(self):
        from backend.chat import stream_split_safe_tail
        tail = (
            "<stage-ack>outline_confirmed_at</stage-ack>\n"
            '<stage-ack action="clear">outline_confirmed_at</stage-ack>\n'
            "<stage-ack>outline_confirmed_at</stage-ack>\n"
        )
        buffer = "正文段。\n" + tail
        safe, held = stream_split_safe_tail(buffer)
        self.assertEqual(safe, "正文段。\n")
        self.assertEqual(held, tail)
        self.assertGreater(len(tail.encode("utf-8")), 128)


class StageAckFinalizePipelineTests(ChatRuntimeTests):
    def _write_effective_outline(self):
        (self.project_dir / "plan" / "outline.md").write_text(
            "# 大纲\n## 章节 1\n- 要点 A\n## 章节 2\n- 要点 B\n",
            encoding="utf-8",
        )

    def _set_checkpoints(self, data):
        import json
        (self.project_dir / "stage_checkpoints.json").write_text(
            json.dumps(data), encoding="utf-8"
        )

    def _write_conversation(self, messages):
        import json
        (self.project_dir / "conversation.json").write_text(
            json.dumps(messages, ensure_ascii=False), encoding="utf-8"
        )

    def test_valid_set_tag_sets_checkpoint_strips_content(self):
        handler = self._make_handler_with_project()
        self._set_checkpoints({"s0_interview_done_at": "2026-04-21T10:00:00"})
        self._write_effective_outline()
        stripped = self._finalize_assistant_for_test(
            handler,
            "大纲完成。\n\n<stage-ack>outline_confirmed_at</stage-ack>\n",
        )
        self.assertNotIn("<stage-ack", stripped)
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertIn("outline_confirmed_at", checkpoints)

    def test_tag_in_code_fence_not_executed_still_stripped(self):
        handler = self._make_handler_with_project()
        self._set_checkpoints({"s0_interview_done_at": "2026-04-21T10:00:00"})
        self._write_effective_outline()
        stripped = self._finalize_assistant_for_test(
            handler,
            "示例：\n```md\n<stage-ack>outline_confirmed_at</stage-ack>\n```\n结尾。\n",
        )
        self.assertNotIn("<stage-ack", stripped)
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertNotIn("outline_confirmed_at", checkpoints)

    def test_fenced_code_tag_logs_warning_no_notice(self):
        handler = self._make_handler_with_project()
        with self.assertLogs(level="WARNING") as cm:
            self._finalize_assistant_for_test(
                handler,
                "示例：\n```md\n<stage-ack>outline_confirmed_at</stage-ack>\n```\n结尾。\n",
            )
        joined = "\n".join(cm.output)
        self.assertIn("outline_confirmed_at", joined)
        self.assertIn("in_fenced_code", joined)
        notices = handler._turn_context.get("pending_system_notices", [])
        self.assertFalse(any("outline_confirmed_at" in str(n) for n in notices))

    def test_non_tail_tag_logs_warning_no_notice(self):
        handler = self._make_handler_with_project()
        with self.assertLogs(level="WARNING") as cm:
            self._finalize_assistant_for_test(
                handler,
                "<stage-ack>outline_confirmed_at</stage-ack>\n\n非 tail 后面还有正文。\n",
            )
        joined = "\n".join(cm.output)
        self.assertIn("outline_confirmed_at", joined)
        self.assertIn("not_tail", joined)
        notices = handler._turn_context.get("pending_system_notices", [])
        self.assertFalse(any("outline_confirmed_at" in str(n) for n in notices))

    def test_multi_tag_executed_in_order(self):
        handler = self._make_handler_with_project()
        self._set_checkpoints({
            "s0_interview_done_at": "2026-04-21T10:00:00",
            "outline_confirmed_at": "2026-04-21T11:00:00",
        })
        self._write_effective_outline()
        self._finalize_assistant_for_test(
            handler,
            "回退再推进。\n"
            '<stage-ack action="clear">outline_confirmed_at</stage-ack>\n'
            "<stage-ack>outline_confirmed_at</stage-ack>\n",
        )
        # Final state: outline_confirmed_at is set (the last action wins
        # by sequential execution)
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertIn("outline_confirmed_at", checkpoints)

    def test_set_missing_prereq_emits_notice_no_checkpoint(self):
        handler = self._make_handler_with_project()
        self._set_checkpoints({"s0_interview_done_at": "2026-04-21T10:00:00"})
        # outline.md NOT written - prereq will fail
        self._finalize_assistant_for_test(
            handler,
            "大纲没写但强推。\n<stage-ack>outline_confirmed_at</stage-ack>\n",
        )
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertNotIn("outline_confirmed_at", checkpoints)
        notices = handler._turn_context.get("pending_system_notices", [])
        self.assertTrue(any("outline.md" in str(n) for n in notices))

    def test_s0_tag_first_turn_without_prior_assistant_rejected(self):
        handler = self._make_handler_with_project()
        self._write_conversation([{"role": "user", "content": "你好"}])
        # No assistant history
        self._finalize_assistant_for_test(
            handler,
            "先简化流程。\n<stage-ack>s0_interview_done_at</stage-ack>\n",
        )
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertNotIn("s0_interview_done_at", checkpoints)
        notices = handler._turn_context.get("pending_system_notices", [])
        self.assertTrue(any("S0" in n.get("reason", "") for n in notices))

    def test_s0_tag_after_prior_assistant_succeeds(self):
        handler = self._make_handler_with_project()
        self._write_conversation([
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "请回答：1) 读者是谁？"},
        ])
        stripped = self._finalize_assistant_for_test(
            handler,
            "记录了。\n<stage-ack>s0_interview_done_at</stage-ack>\n",
        )
        self.assertNotIn("<stage-ack", stripped)
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertIn("s0_interview_done_at", checkpoints)

    def test_unknown_key_tag_stripped_no_checkpoint_no_notice(self):
        handler = self._make_handler_with_project()
        self._finalize_assistant_for_test(
            handler,
            "写错 key。\n<stage-ack>bogus_key</stage-ack>\n",
        )
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertNotIn("bogus_key", checkpoints)
        # Per spec §2: unknown key logs warning but does NOT emit system_notice
        notices = handler._turn_context.get("pending_system_notices", [])
        self.assertFalse(any(
            "bogus_key" in n.get("reason", "") or
            "bogus_key" in n.get("path", "") for n in notices
        ))

    def test_clear_idempotent_through_tag(self):
        handler = self._make_handler_with_project()
        # Clear when not set - should be idempotent
        self._finalize_assistant_for_test(
            handler,
            '回退。\n<stage-ack action="clear">outline_confirmed_at</stage-ack>\n',
        )
        # No assertion failure; no notice raised
        notices = handler._turn_context.get("pending_system_notices", [])
        self.assertFalse(any("outline" in n.get("reason", "") for n in notices))

    def test_executable_tag_wins_over_pending_keyword(self):
        """User said '确认大纲' (keyword → stored as pending_stage_keyword in
        _build_turn_context, NOT executed yet). Assistant then emits an
        executable tag pointing at a DIFFERENT checkpoint. The tag must win;
        pending keyword is discarded without setting outline_confirmed_at."""
        handler = self._make_handler_with_project()
        self._set_checkpoints({
            "s0_interview_done_at": "2026-04-21T10:00:00",
            "outline_confirmed_at": "2026-04-21T11:00:00",
        })
        # Build effective report draft so review_started_at prereq passes
        (self.project_dir / "content").mkdir(exist_ok=True)
        (self.project_dir / "content" / "report_draft_v1.md").write_text(
            "# Report\n\n" + ("数据资产核算。" * 400),
            encoding="utf-8",
        )
        # Simulate keyword pending (what _build_turn_context would store)
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        handler._turn_context["pending_stage_keyword"] = ("set", "outline_confirmed_at")
        # Clear outline_confirmed_at first so we can see whether pending keyword
        # would have set it (it shouldn't - tag wins)
        handler.skill_engine._clear_stage_checkpoint(
            self.project_dir, "outline_confirmed_at"
        )
        # Assistant tag points at review_started_at
        self._finalize_assistant_for_test(
            handler,
            "进入审查。\n<stage-ack>review_started_at</stage-ack>\n",
        )
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        # Tag's target set
        self.assertIn("review_started_at", checkpoints)
        # Pending keyword target NOT set (tag won; keyword discarded)
        self.assertNotIn("outline_confirmed_at", checkpoints)
        # pending_stage_keyword cleared
        self.assertIsNone(handler._turn_context.get("pending_stage_keyword"))

    def test_pending_keyword_fallback_fires_when_no_executable_tag(self):
        """Assistant has only a non-executable tag (e.g., inside code fence);
        pending keyword falls back to record_stage_checkpoint."""
        handler = self._make_handler_with_project()
        self._set_checkpoints({"s0_interview_done_at": "2026-04-21T10:00:00"})
        self._write_effective_outline()
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        handler._turn_context["pending_stage_keyword"] = ("set", "outline_confirmed_at")
        # Non-executable tag (inside code fence) must NOT block fallback
        self._finalize_assistant_for_test(
            handler,
            "示例：\n```md\n<stage-ack>review_started_at</stage-ack>\n```\n完。\n",
        )
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        # Keyword fallback set outline_confirmed_at
        self.assertIn("outline_confirmed_at", checkpoints)
        # Non-executable tag target NOT set
        self.assertNotIn("review_started_at", checkpoints)

    def test_pending_keyword_fallback_emits_prereq_notice_on_failure(self):
        """Pending keyword set fails prereq → emit notice, no checkpoint."""
        handler = self._make_handler_with_project()
        self._set_checkpoints({"s0_interview_done_at": "2026-04-21T10:00:00"})
        # NO effective outline - prereq will fail
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        handler._turn_context["pending_stage_keyword"] = ("set", "outline_confirmed_at")
        self._finalize_assistant_for_test(
            handler,
            "没 tag 的正文。\n",
        )
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertNotIn("outline_confirmed_at", checkpoints)
        notices = handler._turn_context.get("pending_system_notices", [])
        self.assertTrue(any("outline.md" in str(n) for n in notices))

    def test_user_message_tag_not_parsed_by_finalize(self):
        # Finalize operates on assistant content only; user tag is never
        # fed to it.
        handler = self._make_handler_with_project()
        # No exception, no checkpoint change
        stripped = self._finalize_assistant_for_test(
            handler,
            "用户问到了 <stage-ack>outline_confirmed_at</stage-ack>"
            " 这种语法。\n",  # non-tail tag
        )
        self.assertNotIn("<stage-ack", stripped)
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertNotIn("outline_confirmed_at", checkpoints)

    def test_compaction_receives_stripped_content(self):
        handler = self._make_handler_with_project()
        self._set_checkpoints({"s0_interview_done_at": "2026-04-21T10:00:00"})
        self._write_effective_outline()
        final = self._finalize_assistant_for_test(
            handler,
            "完成。\n<stage-ack>outline_confirmed_at</stage-ack>\n",
        )
        # Whatever the caller persists must have no tag
        self.assertNotIn("<stage-ack", final)


for _inherited_test_name in dir(ChatRuntimeTests):
    if (
        _inherited_test_name.startswith("test_")
        and _inherited_test_name not in StageAckFinalizePipelineTests.__dict__
    ):
        setattr(StageAckFinalizePipelineTests, _inherited_test_name, None)
del _inherited_test_name


class ChatPathIntegrationTests(ChatRuntimeTests):
    """End-to-end integration with mocked provider, verifying:
      - finalize runs on both chat() and chat_stream() paths
      - conversation.json persisted without tag (and post-turn compaction input too)
      - stream SSE order: content → system_notice → usage
      - unknown key logs WARNING via logger `backend.chat`, no system_notice
      - user-role tag survives literal into conversation.json
      - set+clear final clear; clear+set final set
      - keyword fallback works when assistant has no executable tag
    """
    def _set_checkpoints(self, data):
        import json
        (self.project_dir / "stage_checkpoints.json").write_text(
            json.dumps(data), encoding="utf-8"
        )

    def _write_effective_outline(self):
        (self.project_dir / "plan" / "outline.md").write_text(
            "# 大纲\n## 章节 1\n- 要点 A\n## 章节 2\n- 要点 B\n",
            encoding="utf-8",
        )

    def _mock_non_stream_completion(self, full_text):
        from types import SimpleNamespace
        return SimpleNamespace(
            id="mock-id",
            choices=[SimpleNamespace(
                message=SimpleNamespace(
                    role="assistant",
                    content=full_text,
                    tool_calls=None,
                ),
                finish_reason="stop",
            )],
            usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=10, total_tokens=20,
            ),
        )

    def _mock_stream_chunks(self, full_text, chunk_size=5):
        from types import SimpleNamespace
        def _iter():
            for i in range(0, len(full_text), chunk_size):
                piece = full_text[i:i+chunk_size]
                yield SimpleNamespace(
                    id="mock-id",
                    choices=[SimpleNamespace(
                        delta=SimpleNamespace(content=piece, role=None, tool_calls=None),
                        finish_reason=None,
                    )],
                    usage=None,
                )
            yield SimpleNamespace(
                id="mock-id",
                choices=[SimpleNamespace(
                    delta=SimpleNamespace(content=None, role=None, tool_calls=None),
                    finish_reason="stop",
                )],
                usage=SimpleNamespace(
                    prompt_tokens=10, completion_tokens=10, total_tokens=20,
                ),
            )
        return _iter()

    def test_non_stream_chat_strips_tag_and_persists_cleanly(self):
        """Real handler.chat() path: returned message has no tag AND
        conversation.json saves stripped content."""
        from unittest import mock
        handler = self._make_handler_with_project()
        self._set_checkpoints({"s0_interview_done_at": "2026-04-21T10:00:00"})
        self._write_effective_outline()
        assistant_text = "大纲已批准。\n\n<stage-ack>outline_confirmed_at</stage-ack>\n"
        with mock.patch.object(
            handler.client.chat.completions, "create",
            return_value=self._mock_non_stream_completion(assistant_text),
        ):
            response = handler.chat(project_id=self.project_id, user_message="你看行吗")
        # Response has no tag
        response_text = response.get("message") or response.get("content") or ""
        self.assertNotIn("<stage-ack", response_text)
        # conversation.json has no tag
        import json
        conv = json.loads(
            (self.project_dir / "conversation.json").read_text(encoding="utf-8")
        )
        assistant_msgs = [m for m in conv if m["role"] == "assistant"]
        self.assertTrue(assistant_msgs)
        self.assertNotIn("<stage-ack", assistant_msgs[-1]["content"])
        # Checkpoint set via tag
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertIn("outline_confirmed_at", checkpoints)

    def test_stream_chat_never_leaks_tag_to_frontend(self):
        """Real handler.chat_stream(): even with chunk_size=5 splitting
        mid-tag, no SSE content event contains '<stage-ack'."""
        from unittest import mock
        handler = self._make_handler_with_project()
        self._set_checkpoints({"s0_interview_done_at": "2026-04-21T10:00:00"})
        self._write_effective_outline()
        assistant_text = "大纲已批准。\n\n<stage-ack>outline_confirmed_at</stage-ack>\n"
        with mock.patch.object(
            handler.client.chat.completions, "create",
            return_value=self._mock_stream_chunks(assistant_text, chunk_size=5),
        ):
            events = list(handler.chat_stream(
                project_id=self.project_id, user_message="",
            ))
        content_events = [e for e in events if e.get("type") == "content"]
        combined = "".join(e["data"] for e in content_events)
        self.assertNotIn("<stage-ack", combined)
        self.assertIn("大纲已批准", combined)
        # conversation_state.json / conversation.json tag-free too
        import json
        conv = json.loads(
            (self.project_dir / "conversation.json").read_text(encoding="utf-8")
        )
        for msg in conv:
            self.assertNotIn("<stage-ack", msg.get("content", "") or "")

    def test_stream_system_notice_before_usage(self):
        """SSE yield order: system_notice emitted by finalize must precede
        the usage event, otherwise frontend notice rendering breaks."""
        from unittest import mock
        handler = self._make_handler_with_project()
        self._set_checkpoints({"s0_interview_done_at": "2026-04-21T10:00:00"})
        # NO outline → prereq fail → finalize emits notice
        assistant_text = "强推大纲。\n<stage-ack>outline_confirmed_at</stage-ack>\n"
        with mock.patch.object(
            handler.client.chat.completions, "create",
            return_value=self._mock_stream_chunks(assistant_text, chunk_size=5),
        ):
            events = list(handler.chat_stream(
                project_id=self.project_id, user_message="",
            ))
        notice_indices = [i for i, e in enumerate(events) if e.get("type") == "system_notice"]
        usage_indices = [i for i, e in enumerate(events) if e.get("type") == "usage"]
        self.assertTrue(notice_indices, "finalize must yield system_notice")
        self.assertTrue(usage_indices, "stream must yield usage")
        self.assertLess(
            max(notice_indices), min(usage_indices),
            "system_notice must precede usage in SSE stream",
        )

    def test_unknown_key_logs_warning_no_notice(self):
        """Unknown key: log WARNING via backend.chat logger, no system_notice."""
        from unittest import mock
        handler = self._make_handler_with_project()
        assistant_text = "错 key。\n<stage-ack>bogus_key</stage-ack>\n"
        with mock.patch.object(
            handler.client.chat.completions, "create",
            return_value=self._mock_non_stream_completion(assistant_text),
        ):
            with self.assertLogs("backend.chat", level="WARNING") as cm:
                response = handler.chat(project_id=self.project_id, user_message="")
        self.assertTrue(
            any("bogus_key" in record for record in cm.output),
            f"Expected warning mentioning bogus_key, got {cm.output!r}",
        )
        notices = response.get("system_notices") or []
        for n in notices:
            self.assertNotIn("bogus_key", str(n))

    def test_user_message_tag_preserved_as_literal(self):
        """User writes <stage-ack> as part of a question. Must survive into
        conversation.json unchanged, never parsed."""
        from unittest import mock
        handler = self._make_handler_with_project()
        self._set_checkpoints({"s0_interview_done_at": "2026-04-21T10:00:00"})
        user_text = "请问 <stage-ack>outline_confirmed_at</stage-ack> 是什么意思？"
        with mock.patch.object(
            handler.client.chat.completions, "create",
            return_value=self._mock_non_stream_completion("这是 stage-ack tag 语法。"),
        ):
            handler.chat(project_id=self.project_id, user_message=user_text)
        import json
        conv = json.loads(
            (self.project_dir / "conversation.json").read_text(encoding="utf-8")
        )
        user_msgs = [m for m in conv if m["role"] == "user"]
        self.assertTrue(
            any("<stage-ack>" in m["content"] for m in user_msgs),
            "user's literal tag must be preserved",
        )
        # Checkpoint NOT set (tag was user-role, not parsed)
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertNotIn("outline_confirmed_at", checkpoints)

    def test_set_then_clear_same_key_final_clear(self):
        """Assistant emits `set outline; clear outline` in that order.
        Final state: outline_confirmed_at NOT set."""
        from unittest import mock
        handler = self._make_handler_with_project()
        self._set_checkpoints({
            "s0_interview_done_at": "2026-04-21T10:00:00",
            "outline_confirmed_at": "2026-04-21T11:00:00",
        })
        self._write_effective_outline()
        assistant_text = (
            "设后清。\n"
            "<stage-ack>outline_confirmed_at</stage-ack>\n"
            '<stage-ack action="clear">outline_confirmed_at</stage-ack>\n'
        )
        with mock.patch.object(
            handler.client.chat.completions, "create",
            return_value=self._mock_non_stream_completion(assistant_text),
        ):
            handler.chat(project_id=self.project_id, user_message="")
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertNotIn("outline_confirmed_at", checkpoints)

    def test_clear_then_set_same_key_final_set(self):
        from unittest import mock
        handler = self._make_handler_with_project()
        self._set_checkpoints({
            "s0_interview_done_at": "2026-04-21T10:00:00",
            "outline_confirmed_at": "2026-04-21T11:00:00",
        })
        self._write_effective_outline()
        assistant_text = (
            "清后设。\n"
            '<stage-ack action="clear">outline_confirmed_at</stage-ack>\n'
            "<stage-ack>outline_confirmed_at</stage-ack>\n"
        )
        with mock.patch.object(
            handler.client.chat.completions, "create",
            return_value=self._mock_non_stream_completion(assistant_text),
        ):
            handler.chat(project_id=self.project_id, user_message="")
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertIn("outline_confirmed_at", checkpoints)

    def test_keyword_fallback_when_no_tag(self):
        """User says strong keyword; assistant emits no tag.
        Keyword fallback in _finalize_assistant_turn sets the checkpoint."""
        from unittest import mock
        handler = self._make_handler_with_project()
        self._set_checkpoints({"s0_interview_done_at": "2026-04-21T10:00:00"})
        self._write_effective_outline()
        with mock.patch.object(
            handler.client.chat.completions, "create",
            return_value=self._mock_non_stream_completion("好的，按大纲写。"),
        ):
            handler.chat(project_id=self.project_id, user_message="确认大纲")
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)
        self.assertIn("outline_confirmed_at", checkpoints)


for _inherited_test_name in dir(ChatRuntimeTests):
    if (
        _inherited_test_name.startswith("test_")
        and _inherited_test_name not in ChatPathIntegrationTests.__dict__
    ):
        setattr(ChatPathIntegrationTests, _inherited_test_name, None)
del _inherited_test_name


class LoadConversationSanitizeTests(ChatRuntimeTests):
    def _write_conv(self, messages):
        import json
        (self.project_dir / "conversation.json").write_text(
            json.dumps(messages, ensure_ascii=False), encoding="utf-8"
        )

    def test_assistant_residual_tag_stripped(self):
        handler = self._make_handler_with_project()
        self._write_conv([
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": (
                "回复。\n<stage-ack>outline_confirmed_at</stage-ack>\n"
            )},
        ])
        loaded = handler._load_conversation(self.project_id)
        self.assertNotIn("<stage-ack", loaded[1]["content"])
        self.assertIn("回复。", loaded[1]["content"])

    def test_user_role_tag_preserved_as_literal(self):
        handler = self._make_handler_with_project()
        self._write_conv([{
            "role": "user",
            "content": "我写的 <stage-ack>xxx</stage-ack> 是什么意思？",
        }])
        loaded = handler._load_conversation(self.project_id)
        self.assertIn("<stage-ack>", loaded[0]["content"])

    def test_no_tag_messages_unchanged(self):
        handler = self._make_handler_with_project()
        original = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好，请问..."},
        ]
        self._write_conv(original)
        loaded = handler._load_conversation(self.project_id)
        self.assertEqual(
            [(m["role"], m["content"]) for m in loaded],
            [(m["role"], m["content"]) for m in original],
        )


for _inherited_test_name in dir(ChatRuntimeTests):
    if (
        _inherited_test_name.startswith("test_")
        and _inherited_test_name not in LoadConversationSanitizeTests.__dict__
    ):
        setattr(LoadConversationSanitizeTests, _inherited_test_name, None)
del _inherited_test_name


class SystemNoticeFieldTests(unittest.TestCase):
    def test_surface_to_user_is_required_no_default(self):
        from backend.models import SystemNotice
        # 不传 surface_to_user 必须抛 ValidationError / TypeError
        with self.assertRaises(Exception):
            SystemNotice(category="test", reason="r", user_action="a")

    def test_surface_to_user_true_accepted(self):
        from backend.models import SystemNotice
        notice = SystemNotice(
            category="test", reason="r", user_action="a", surface_to_user=True,
        )
        self.assertTrue(notice.surface_to_user)

    def test_surface_to_user_false_accepted(self):
        from backend.models import SystemNotice
        notice = SystemNotice(
            category="test", reason="r", user_action="a", surface_to_user=False,
        )
        self.assertFalse(notice.surface_to_user)


class SystemNoticeServerSideFilterTests(ChatRuntimeTests):
    def test_internal_notice_not_in_sse_yield(self):
        handler = self._make_handler_with_project()
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        handler._turn_context["pending_system_notices"] = [
            {
                "type": "system_notice",
                "category": "x_user",
                "path": None,
                "reason": "r1",
                "user_action": "a1",
                "surface_to_user": True,
            },
            {
                "type": "system_notice",
                "category": "x_internal",
                "path": None,
                "reason": "r2",
                "user_action": "a2",
                "surface_to_user": False,
            },
        ]

        yielded = list(handler._yield_user_visible_notices())

        self.assertEqual(len(yielded), 1)
        self.assertEqual(yielded[0]["category"], "x_user")

    def test_internal_notice_logged_when_filtered(self):
        handler = self._make_handler_with_project()
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        handler._turn_context["pending_system_notices"] = [
            {
                "type": "system_notice",
                "category": "x_internal",
                "path": None,
                "reason": "internal_r",
                "user_action": "a",
                "surface_to_user": False,
            },
        ]

        with self.assertLogs("backend.chat", level="INFO") as caplog:
            list(handler._yield_user_visible_notices())

        self.assertTrue(any("internal-notice" in message for message in caplog.output))

    def test_non_stream_response_filters_internal_notices(self):
        handler = self._make_handler_with_project()
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        handler._turn_context["pending_system_notices"] = [
            {
                "type": "system_notice",
                "category": "x_user",
                "path": None,
                "reason": "r1",
                "user_action": "a1",
                "surface_to_user": True,
            },
            {
                "type": "system_notice",
                "category": "x_internal",
                "path": None,
                "reason": "r2",
                "user_action": "a2",
                "surface_to_user": False,
            },
        ]

        notices = handler._collect_user_visible_system_notices()

        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].category, "x_user")
        self.assertTrue(notices[0].surface_to_user)


for _inherited_test_name in dir(ChatRuntimeTests):
    if (
        _inherited_test_name.startswith("test_")
        and _inherited_test_name not in SystemNoticeServerSideFilterTests.__dict__
    ):
        setattr(SystemNoticeServerSideFilterTests, _inherited_test_name, None)
del _inherited_test_name


class SystemNoticeDualDedupeTests(ChatRuntimeTests):
    def test_user_and_internal_can_coexist_same_turn(self):
        handler = self._make_handler_with_project()
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        handler._emit_system_notice_once(
            category="write_blocked", path=None,
            reason="internal hint", user_action="model fix",
            surface_to_user=False,
        )
        handler._emit_system_notice_once(
            category="non_plan_write_blocked", path=None,
            reason="user must confirm", user_action="please click",
            surface_to_user=True,
        )
        notices = handler._turn_context["pending_system_notices"]
        self.assertEqual(len(notices), 2)
        self.assertEqual(notices[0]["surface_to_user"], False)
        self.assertEqual(notices[1]["surface_to_user"], True)

    def test_internal_notice_does_not_block_user_notice(self):
        handler = self._make_handler_with_project()
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        handler._emit_system_notice_once(
            category="write_blocked", reason="r1", user_action="a1",
            surface_to_user=False,
        )
        handler._emit_system_notice_once(
            category="s0_write_blocked", reason="r2", user_action="a2",
            surface_to_user=True,
        )
        notices = handler._turn_context["pending_system_notices"]
        self.assertEqual(len(notices), 2)

    def test_user_notice_does_not_block_internal_notice(self):
        handler = self._make_handler_with_project()
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        handler._emit_system_notice_once(
            category="s0_write_blocked", reason="r", user_action="a",
            surface_to_user=True,
        )
        handler._emit_system_notice_once(
            category="write_blocked", reason="r2", user_action="a2",
            surface_to_user=False,
        )
        notices = handler._turn_context["pending_system_notices"]
        self.assertEqual(len(notices), 2)

    def test_same_class_internal_still_deduped(self):
        handler = self._make_handler_with_project()
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        for _ in range(3):
            handler._emit_system_notice_once(
                category="write_blocked", reason="r", user_action="a",
                surface_to_user=False,
            )
        notices = handler._turn_context["pending_system_notices"]
        self.assertEqual(len(notices), 1)

    def test_same_class_user_still_deduped(self):
        handler = self._make_handler_with_project()
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        for _ in range(3):
            handler._emit_system_notice_once(
                category="s0_write_blocked", reason="r", user_action="a",
                surface_to_user=True,
            )
        notices = handler._turn_context["pending_system_notices"]
        self.assertEqual(len(notices), 1)

    def test_surface_to_user_required_param(self):
        handler = self._make_handler_with_project()
        handler._turn_context = handler._new_turn_context(can_write_non_plan=True)
        with self.assertRaises(TypeError):
            handler._emit_system_notice_once(
                category="x", reason="r", user_action="a",
            )


class ToolResultQualityHintTests(ChatRuntimeTests):
    def _seed_data_log(self, project_dir, n_entries):
        lines = ["# Data log\n"]
        for i in range(n_entries):
            lines.extend([
                f"\n### [DL-2026-{i+1:02d}] entry {i+1}",
                f"- **来源**: source-{i+1}",
                f"- **时间**: 2026-05-04",
                f"- **URL**: https://example.com/{i+1}",
                f"- **用途**: test",
                "",
            ])
        (project_dir / "plan" / "data-log.md").write_text("\n".join(lines), encoding="utf-8")

    def _seed_outline_for_data_log_min_7(self, project_dir):
        """触发 data_log_min=7（5000 字 → ceil(5000/1000*1.3)=7）"""
        overview = project_dir / "plan" / "project-overview.md"
        text = overview.read_text(encoding="utf-8")
        text = text.replace("3000 words", "5000 字").replace("3000", "5000")
        overview.write_text(text, encoding="utf-8")

    def test_write_data_log_appends_quality_hint_when_s2(self):
        handler = self._make_handler_with_project()
        self._seed_outline_for_data_log_min_7(self.project_dir)
        self._seed_data_log(self.project_dir, 5)
        with mock.patch.object(handler.skill_engine, "_infer_stage_state", return_value={
            "stage_code": "S2",
            "quality_progress": {"label": "条 有效来源", "current": 5, "target": 7},
        }):
            result = {"status": "success", "path": "plan/data-log.md"}
            handler._maybe_attach_quality_hint(
                self.project_id,
                tool_name="write_file",
                tool_args={"file_path": "plan/data-log.md"},
                result=result,
            )
        self.assertIn("quality_hint", result)
        self.assertIn("5/7", result["quality_hint"])
        self.assertIn("有效来源", result["quality_hint"])

    def test_write_other_plan_file_no_quality_hint(self):
        handler = self._make_handler_with_project()
        result = {"status": "success", "path": "plan/notes.md"}
        handler._maybe_attach_quality_hint(
            self.project_id,
            tool_name="write_file",
            tool_args={"file_path": "plan/notes.md"},
            result=result,
        )
        self.assertNotIn("quality_hint", result)

    def test_write_content_draft_no_quality_hint(self):
        handler = self._make_handler_with_project()
        result = {"status": "success"}
        handler._maybe_attach_quality_hint(
            self.project_id,
            tool_name="edit_file",
            tool_args={"file_path": "content/report_draft_v1.md"},
            result=result,
        )
        self.assertNotIn("quality_hint", result)

    def test_quality_hint_absent_when_target_zero(self):
        handler = self._make_handler_with_project()
        with mock.patch.object(handler.skill_engine, "_infer_stage_state", return_value={
            "stage_code": "S2",
            "quality_progress": {"label": "条", "current": 0, "target": 0},
        }):
            result = {"status": "success"}
            handler._maybe_attach_quality_hint(
                self.project_id, tool_name="write_file",
                tool_args={"file_path": "plan/data-log.md"}, result=result,
            )
        self.assertNotIn("quality_hint", result)

    def test_quality_hint_absent_when_stage_not_s2_s3(self):
        handler = self._make_handler_with_project()
        with mock.patch.object(handler.skill_engine, "_infer_stage_state", return_value={
            "stage_code": "S4",
            "quality_progress": None,
        }):
            result = {"status": "success"}
            handler._maybe_attach_quality_hint(
                self.project_id, tool_name="write_file",
                tool_args={"file_path": "plan/data-log.md"}, result=result,
            )
        self.assertNotIn("quality_hint", result)

    def test_edit_data_log_also_appends_quality_hint(self):
        handler = self._make_handler_with_project()
        self._seed_outline_for_data_log_min_7(self.project_dir)
        self._seed_data_log(self.project_dir, 5)
        with mock.patch.object(handler.skill_engine, "_infer_stage_state", return_value={
            "stage_code": "S2",
            "quality_progress": {"label": "条 有效来源", "current": 5, "target": 7},
        }):
            result = {"status": "success"}
            handler._maybe_attach_quality_hint(
                self.project_id, tool_name="edit_file",
                tool_args={"file_path": "plan/data-log.md"}, result=result,
            )
        self.assertIn("quality_hint", result)

    def test_write_analysis_notes_appends_when_s3(self):
        handler = self._make_handler_with_project()
        with mock.patch.object(handler.skill_engine, "_infer_stage_state", return_value={
            "stage_code": "S3",
            "quality_progress": {"label": "项 分析引用", "current": 3, "target": 4},
        }):
            result = {"status": "success"}
            handler._maybe_attach_quality_hint(
                self.project_id, tool_name="write_file",
                tool_args={"file_path": "plan/analysis-notes.md"}, result=result,
            )
        self.assertIn("quality_hint", result)
        self.assertIn("3/4", result["quality_hint"])


for _inherited_test_name in dir(ChatRuntimeTests):
    if (
        _inherited_test_name.startswith("test_")
        and _inherited_test_name not in ToolResultQualityHintTests.__dict__
    ):
        setattr(ToolResultQualityHintTests, _inherited_test_name, None)
del _inherited_test_name


for _inherited_test_name in dir(ChatRuntimeTests):
    if (
        _inherited_test_name.startswith("test_")
        and _inherited_test_name not in SystemNoticeDualDedupeTests.__dict__
    ):
        setattr(SystemNoticeDualDedupeTests, _inherited_test_name, None)
del _inherited_test_name


class CoalesceConsecutiveUserTests(ChatRuntimeTests):
    def test_two_str_user_messages_merged(self):
        handler = self._make_handler_with_project()
        conv = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "first"},
            {"role": "user", "content": "second"},
        ]
        result = handler._coalesce_consecutive_user_messages(conv)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[1]["role"], "user")
        self.assertEqual(result[1]["content"], "first\n\nsecond")

    def test_str_plus_multipart_merged_to_array(self):
        handler = self._make_handler_with_project()
        conv = [
            {"role": "user", "content": "text"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hi"},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            },
        ]
        result = handler._coalesce_consecutive_user_messages(conv)
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0]["content"], list)
        self.assertEqual(result[0]["content"][0], {"type": "text", "text": "text"})
        self.assertEqual(result[0]["content"][1], {"type": "text", "text": "hi"})

    def test_two_multipart_arrays_merged(self):
        handler = self._make_handler_with_project()
        conv = [
            {"role": "user", "content": [{"type": "text", "text": "a"}]},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:..."}}]},
        ]
        result = handler._coalesce_consecutive_user_messages(conv)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]["content"]), 2)

    def test_does_not_modify_original_history(self):
        handler = self._make_handler_with_project()
        original_msg = {"role": "user", "content": "first"}
        conv = [original_msg, {"role": "user", "content": "second"}]
        handler._coalesce_consecutive_user_messages(conv)
        self.assertEqual(original_msg["content"], "first")

    def test_alternating_user_assistant_no_merge(self):
        handler = self._make_handler_with_project()
        conv = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
        ]
        result = handler._coalesce_consecutive_user_messages(conv)
        self.assertEqual(len(result), 3)

    def test_none_content_normalized_to_empty_string(self):
        handler = self._make_handler_with_project()
        conv = [
            {"role": "user", "content": None},
            {"role": "user", "content": "after"},
        ]
        result = handler._coalesce_consecutive_user_messages(conv)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["content"], "after")

    def test_invoked_in_build_provider_turn_conversation(self):
        handler = self._make_handler_with_project()
        history = [
            {"role": "user", "content": "first", "attached_material_ids": []},
            {"role": "user", "content": "second", "attached_material_ids": []},
        ]
        current = {"role": "user", "content": "current", "attached_material_ids": []}
        conv, _ = handler._build_provider_turn_conversation(
            self.project_id,
            history,
            current,
        )
        user_msgs = [m for m in conv if m.get("role") == "user"]
        self.assertEqual(len(user_msgs), 1)

    def test_coalesce_recomputes_current_turn_start_index(self):
        handler = self._make_handler_with_project()
        history = [{"role": "user", "content": "previous", "attached_material_ids": []}]
        current = {"role": "user", "content": "current", "attached_material_ids": []}
        conv, idx = handler._build_provider_turn_conversation(
            self.project_id,
            history,
            current,
        )
        user_msgs = [m for m in conv if m.get("role") == "user"]
        self.assertEqual(len(user_msgs), 1)
        self.assertEqual(conv[idx].get("role"), "user")
        self.assertIn("current", conv[idx]["content"])


for _inherited_test_name in dir(ChatRuntimeTests):
    if (
        _inherited_test_name.startswith("test_")
        and _inherited_test_name not in CoalesceConsecutiveUserTests.__dict__
    ):
        setattr(CoalesceConsecutiveUserTests, _inherited_test_name, None)
del _inherited_test_name


class HistorySanitizeTests(ChatRuntimeTests):
    def test_legacy_fallback_skipped_in_provider_message(self):
        handler = self._make_handler_with_project()
        msg = {"role": "assistant", "content": "（本轮无回复）"}
        result = handler._to_provider_message(self.project_id, msg, include_images=False)
        self.assertIsNone(result)

    def test_user_visible_fallback_skipped_in_provider_message(self):
        from backend.chat import USER_VISIBLE_FALLBACK
        handler = self._make_handler_with_project()
        msg = {"role": "assistant", "content": USER_VISIBLE_FALLBACK}
        result = handler._to_provider_message(self.project_id, msg, include_images=False)
        self.assertIsNone(result)

    def test_normal_assistant_passes_through(self):
        handler = self._make_handler_with_project()
        msg = {"role": "assistant", "content": "normal reply"}
        result = handler._to_provider_message(self.project_id, msg, include_images=False)
        self.assertEqual(result["content"], "normal reply")

    def test_user_role_with_legacy_text_not_sanitized(self):
        handler = self._make_handler_with_project()
        msg = {"role": "user", "content": "（本轮无回复）"}
        result = handler._to_provider_message(self.project_id, msg, include_images=False)
        self.assertEqual(result["content"], "（本轮无回复）")


for _inherited_test_name in dir(ChatRuntimeTests):
    if (
        _inherited_test_name.startswith("test_")
        and _inherited_test_name not in HistorySanitizeTests.__dict__
    ):
        setattr(HistorySanitizeTests, _inherited_test_name, None)
del _inherited_test_name


class StreamSplitSafeTailDraftActionTests(unittest.TestCase):
    """模块级 helper 独立测试，不需要 ChatHandler。"""

    def test_draft_action_simple_marker_held(self):
        from backend.chat import stream_split_safe_tail
        # buffer 中段就含 "<draft-action" → 从此位置起全部 hold
        emit, hold = stream_split_safe_tail("Hello <draft-action>begin</draft-action>")
        self.assertEqual(emit, "Hello ")
        self.assertEqual(hold, "<draft-action>begin</draft-action>")

    def test_draft_action_replace_marker_held(self):
        from backend.chat import stream_split_safe_tail
        emit, hold = stream_split_safe_tail("Reply <draft-action-replace>")
        self.assertEqual(emit, "Reply ")
        self.assertEqual(hold, "<draft-action-replace>")

    def test_draft_action_partial_prefix_at_tail_held(self):
        from backend.chat import stream_split_safe_tail
        # 末尾恰好是某 marker 的前缀（如 "<draft-act"）→ hold 该尾段
        emit, hold = stream_split_safe_tail("Ok content <draft-act")
        self.assertEqual(emit, "Ok content ")
        self.assertEqual(hold, "<draft-act")

    def test_stage_ack_marker_still_held(self):
        # 回归：stage-ack marker 行为不变
        from backend.chat import stream_split_safe_tail
        emit, hold = stream_split_safe_tail("Hi <stage-ack>x</stage-ack>")
        self.assertEqual(emit, "Hi ")
        self.assertIn("<stage-ack", hold)

    def test_no_marker_emit_all(self):
        from backend.chat import stream_split_safe_tail
        emit, hold = stream_split_safe_tail("plain text no markers here")
        self.assertEqual(emit, "plain text no markers here")
        self.assertEqual(hold, "")

    def test_earliest_marker_anchors_hold(self):
        from backend.chat import stream_split_safe_tail
        # 同时含 stage-ack 和 draft-action，靠前的赢
        emit, hold = stream_split_safe_tail("Hi <draft-action>x</draft-action> <stage-ack>y</stage-ack>")
        self.assertEqual(emit, "Hi ")
        self.assertTrue(hold.startswith("<draft-action"))


class PreflightCheckTests(ChatRuntimeTests):
    def _put_draft(self, body: str) -> None:
        """fix4 test helper: write content/report_draft_v1.md under self.project_dir."""
        draft_path = self.project_dir / "content" / "report_draft_v1.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(body, encoding="utf-8")

    def test_preflight_keyword_intent_begin_for_start_writing(self):
        handler = self._make_handler_with_project()
        decision = handler._preflight_canonical_draft_check(
            self.project_id, "开始写报告吧", stage_code="S4",
        )
        self.assertEqual(decision["preflight_keyword_intent"], "begin")

    def test_preflight_keyword_intent_continue_for_continue_writing(self):
        handler = self._make_handler_with_project()
        decision = handler._preflight_canonical_draft_check(
            self.project_id, "继续写", stage_code="S4",
        )
        self.assertEqual(decision["preflight_keyword_intent"], "continue")

    def test_preflight_keyword_intent_none_for_unrelated(self):
        handler = self._make_handler_with_project()
        decision = handler._preflight_canonical_draft_check(
            self.project_id, "你好", stage_code="S4",
        )
        self.assertIsNone(decision["preflight_keyword_intent"])

    def test_preflight_keyword_intent_never_section_replace_when_target_unresolved(self):
        """v5 §4.12 安全契约：keyword 命中但 target 未 resolve 时仍返回 None。
        （v4 原 test 'never section/replace' 在 v5 失效——target 能 resolve 时允许返回 section/replace。）"""
        handler = self._make_handler_with_project()
        # 无 draft 文件 → section/replace 都 resolve 不出 target
        for msg in ["重写第二章", "把 X 改成 Y", "section:foo", "replace this"]:
            decision = handler._preflight_canonical_draft_check(
                self.project_id, msg, stage_code="S4",
            )
            self.assertIsNone(
                decision["preflight_keyword_intent"],
                f"target unresolved but got intent={decision.get('preflight_keyword_intent')} for msg={msg!r}",
            )

    def test_preflight_section_keyword_with_unique_heading_returns_section(self):
        """fix4 v5: '把第二章重写一下' + draft 含唯一第二章 heading → preflight_keyword_intent='section'"""
        handler = self._make_handler_with_project()
        self._put_draft("## 第一章 引言\n\n内容A\n\n## 第二章 战力演化\n\n内容B\n")
        decision = handler._preflight_canonical_draft_check(
            self.project_id, "把第二章重写一下", stage_code="S4",
        )
        self.assertEqual(decision.get("preflight_keyword_intent"), "section")
        self.assertEqual(decision.get("mode"), "require")
        self.assertEqual(decision.get("priority"), "P_PREFLIGHT_OK")
        self.assertEqual(decision.get("rewrite_target_label"), "第二章 战力演化")
        self.assertIn("内容B", str(decision.get("rewrite_target_snapshot") or ""))

    def test_preflight_section_keyword_without_draft_returns_none(self):
        """fix4 v5: '重写第二章' 但 draft 不存在 → preflight_keyword_intent=None"""
        handler = self._make_handler_with_project()
        decision = handler._preflight_canonical_draft_check(
            self.project_id, "重写第二章",
        )
        self.assertIsNone(decision.get("preflight_keyword_intent"))

    def test_preflight_section_zero_candidates_returns_none(self):
        """fix4 v5: prefix '第二章' 在 draft 中匹配 0 个 → preflight=None"""
        handler = self._make_handler_with_project()
        self._put_draft("## 第一章 引言\n\n内容A\n")
        decision = handler._preflight_canonical_draft_check(
            self.project_id, "把第二章重写一下",
        )
        self.assertIsNone(decision.get("preflight_keyword_intent"))

    def test_preflight_section_multi_candidate_returns_none(self):
        """fix4-fix1 v5: prefix '第二章' 在 draft 中匹配 ≥2 个 heading → preflight=None"""
        handler = self._make_handler_with_project()
        self._put_draft(
            "## 第一章 引言\n内容0\n"
            "## 第二章 战力演化\n内容A\n"
            "## 第二章 战略意义\n内容B\n"
        )
        decision = handler._preflight_canonical_draft_check(
            self.project_id, "把第二章重写一下",
        )
        self.assertIsNone(decision.get("preflight_keyword_intent"))

    def test_preflight_section_multi_prefix_distinct_targets_returns_none(self):
        """fix4-fix1: user msg 含多个章节前缀且分别命中不同 heading → preflight=None (避免兜底改错章节)"""
        handler = self._make_handler_with_project()
        self._put_draft(
            "## 第一章 引言\n内容0\n"
            "## 第二章 战力演化\n内容A\n"
            "## 第三章 战略意义\n内容B\n"
        )
        decision = handler._preflight_canonical_draft_check(
            self.project_id, "把第二章和第三章都重写一下",
        )
        self.assertIsNone(decision.get("preflight_keyword_intent"))

    def test_preflight_section_partial_multi_prefix_returns_none(self):
        """fix4-fix2 (Bug 7): user msg 含两个章节前缀，一个 unique resolve 一个未命中 →
        preflight=None (fail-fast，不能只 fallback 一个把另一个丢了)"""
        handler = self._make_handler_with_project()
        self._put_draft(
            "## 第一章 引言\n内容0\n"
            "## 第二章 战力演化\n内容A\n"
            # NO 第四章 in draft
        )
        decision = handler._preflight_canonical_draft_check(
            self.project_id, "把第二章和第四章都重写一下",
        )
        self.assertIsNone(decision.get("preflight_keyword_intent"))

    def test_preflight_section_ambiguous_prefix_plus_unique_returns_none(self):
        """fix4-fix2 (Bug 7): 一个 prefix 多重命中 + 另一个 unique resolve →
        preflight=None (任意一个 prefix 没 resolve 就 fail-fast)"""
        handler = self._make_handler_with_project()
        self._put_draft(
            "## 第二章 战力演化\n内容A\n"
            "## 第二章 战略意义\n内容B\n"  # 第二章 ambiguous (2 candidates)
            "## 第三章 总结\n内容C\n"
        )
        decision = handler._preflight_canonical_draft_check(
            self.project_id, "把第二章和第三章都重写一下",
        )
        self.assertIsNone(decision.get("preflight_keyword_intent"))

    def test_preflight_replace_keyword_with_unique_old_text_returns_replace(self):
        """fix4 v5: '把"体能"改成"力量"' + draft 含唯一"体能" → preflight_keyword_intent='replace'"""
        handler = self._make_handler_with_project()
        self._put_draft("## 第一章\n体能很重要\n")
        decision = handler._preflight_canonical_draft_check(
            self.project_id, "把报告里的体能改成力量", stage_code="S4",
        )
        self.assertEqual(decision.get("preflight_keyword_intent"), "replace")
        self.assertEqual(decision.get("mode"), "require")
        self.assertEqual(decision.get("priority"), "P_PREFLIGHT_OK")
        self.assertEqual(decision.get("old_text"), "体能")
        self.assertEqual(decision.get("new_text"), "力量")

    def test_preflight_replace_keyword_change_to_synonym_works(self):
        """fix4-fix1: '改为' 同义词应跟 '改成' 一样触发 replace fallback"""
        handler = self._make_handler_with_project()
        self._put_draft("## 第一章\n体能很重要\n")
        decision = handler._preflight_canonical_draft_check(
            self.project_id, "把报告里的体能改为力量", stage_code="S4",
        )
        self.assertEqual(decision.get("preflight_keyword_intent"), "replace")
        self.assertEqual(decision.get("old_text"), "体能")
        self.assertEqual(decision.get("new_text"), "力量")

    def test_preflight_replace_old_text_not_in_draft_returns_none(self):
        """fix4 v5: replace 关键词命中但 draft 不含 old_text → preflight=None"""
        handler = self._make_handler_with_project()
        self._put_draft("## 第一章\n力量很重要\n")
        decision = handler._preflight_canonical_draft_check(
            self.project_id, "把报告里的体能改成力量",
        )
        self.assertIsNone(decision.get("preflight_keyword_intent"))

    def test_preflight_begin_keyword_takes_priority_over_section(self):
        """fix4 v5: begin 优先级仍然 > section（dict 顺序保留）"""
        handler = self._make_handler_with_project()
        self._put_draft("## 第一章\n内容\n")
        decision = handler._preflight_canonical_draft_check(
            self.project_id, "开始写报告吧，再重写一下",
        )
        # 含 "开始写报告" begin 关键词 + "重写" section 关键词；begin 应优先
        self.assertEqual(decision.get("preflight_keyword_intent"), "begin")

    def test_preflight_s0_with_draft_intent_rejects(self):
        handler = self._make_handler_with_project()
        decision = handler._preflight_canonical_draft_check(
            self.project_id, "开始写报告吧", stage_code="S0",
        )
        self.assertEqual(decision["mode"], "reject")
        # surface_to_user system_notice 应被发出
        notices = handler._turn_context.get("pending_system_notices", [])
        user_notices = [n for n in notices if n.get("surface_to_user")]
        self.assertTrue(any("S0" in (n.get("reason") or "") or "大纲" in (n.get("reason") or "") for n in user_notices))

    def test_preflight_no_decisions_no_keyword_no_change(self):
        # "你好" 在 S4 → no_write
        handler = self._make_handler_with_project()
        decision = handler._preflight_canonical_draft_check(
            self.project_id, "你好", stage_code="S4",
        )
        self.assertEqual(decision["mode"], "no_write")

    def test_preflight_begin_wins_over_continue_when_both_match(self):
        """v2 显式：begin/continue 双命中时，按 dict 顺序 begin 在前，begin 赢"""
        handler = self._make_handler_with_project()
        decision = handler._preflight_canonical_draft_check(
            self.project_id, "开始写报告，然后继续写", stage_code="S4",
        )
        self.assertEqual(decision["preflight_keyword_intent"], "begin")


for _inherited_test_name in dir(ChatRuntimeTests):
    if (
        _inherited_test_name.startswith("test_")
        and _inherited_test_name not in PreflightCheckTests.__dict__
    ):
        setattr(PreflightCheckTests, _inherited_test_name, None)
del _inherited_test_name


class GateCanonicalDraftToolCallTests(ChatRuntimeTests):
    """注意：append_report_draft 真实 schema 只有 content（chat.py:4187-4202）；
    write_file/edit_file 写 content/* 才有 file_path。"""

    def test_append_report_draft_with_begin_tag_passes(self):
        from backend.draft_action import DraftActionEvent
        handler = self._make_handler_with_project()
        tags = [DraftActionEvent(raw="...", intent="begin", executable=True)]
        decision = {"preflight_keyword_intent": None}
        result = handler._gate_canonical_draft_tool_call(
            self.project_id, "append_report_draft",
            {"content": "new section"},  # 真实 schema 只有 content
            decision, tags,
        )
        self.assertIsNone(result)  # pass

    def test_append_report_draft_with_keyword_fallback_passes(self):
        handler = self._make_handler_with_project()
        decision = {"preflight_keyword_intent": "begin"}
        result = handler._gate_canonical_draft_tool_call(
            self.project_id, "append_report_draft",
            {"content": "new section"},
            decision, [],
        )
        self.assertIsNone(result)

    def test_append_report_draft_with_conflicting_section_tag_blocks(self):
        """v2 fix1 regression: section/replace tag is incompatible with append_report_draft.
        Even if keyword_intent is begin/continue, the tagless fallback MUST NOT fire when
        an executable non-{begin,continue} tag is present - that's a contradictory turn,
        not a tagless turn.
        """
        from backend.draft_action import DraftActionEvent
        handler = self._make_handler_with_project()
        tags = [DraftActionEvent(raw="...", intent="section", section_label="x", executable=True)]
        decision = {"preflight_keyword_intent": "begin"}
        result = handler._gate_canonical_draft_tool_call(
            self.project_id, "append_report_draft",
            {"content": "new section"},
            decision, tags,
        )
        self.assertIsNotNone(result)  # must block

    def test_edit_file_no_tag_blocked_for_canonical_draft_path(self):
        handler = self._make_handler_with_project()
        decision = {"preflight_keyword_intent": "begin"}  # 即使 keyword 命中也不放行 edit_file
        result = handler._gate_canonical_draft_tool_call(
            self.project_id, "edit_file",
            {"file_path": "content/report_draft_v1.md", "old_string": "x", "new_string": "y"},
            decision, [],
        )
        self.assertIsNotNone(result)  # block

    def test_edit_file_with_section_tag_passes(self):
        from backend.draft_action import DraftActionEvent
        handler = self._make_handler_with_project()
        tags = [DraftActionEvent(raw="...", intent="section", section_label="x", executable=True)]
        decision = {"preflight_keyword_intent": None}
        result = handler._gate_canonical_draft_tool_call(
            self.project_id, "edit_file",
            {"file_path": "content/report_draft_v1.md", "old_string": "x", "new_string": "y"},
            decision, tags,
        )
        self.assertIsNone(result)

    def test_edit_file_with_replace_tag_passes(self):
        from backend.draft_action import DraftActionEvent
        handler = self._make_handler_with_project()
        tags = [DraftActionEvent(raw="...", intent="replace", old_text="x", new_text="y", executable=True)]
        decision = {"preflight_keyword_intent": None}
        result = handler._gate_canonical_draft_tool_call(
            self.project_id, "edit_file",
            {"file_path": "content/report_draft_v1.md", "old_string": "x", "new_string": "y"},
            decision, tags,
        )
        self.assertIsNone(result)

    def test_fallback_signal_only_from_preflight_keyword_intent(self):
        """关键防御测试：偷偷塞 intent_kind="section" 不能让 gate 放行 edit_file"""
        handler = self._make_handler_with_project()
        decision = {
            "preflight_keyword_intent": None,
            "intent_kind": "section",  # 偷塞
            "expected_tool_family": "edit_file",
        }
        result = handler._gate_canonical_draft_tool_call(
            self.project_id, "edit_file",
            {"file_path": "content/report_draft_v1.md", "old_string": "x", "new_string": "y"},
            decision, [],
        )
        self.assertIsNotNone(result)  # 必须 block

    def test_non_executable_tag_does_not_pass(self):
        from backend.draft_action import DraftActionEvent
        handler = self._make_handler_with_project()
        tags = [DraftActionEvent(raw="...", intent="section", section_label="x",
                                  executable=False, ignored_reason="no_draft")]
        decision = {"preflight_keyword_intent": None}
        result = handler._gate_canonical_draft_tool_call(
            self.project_id, "edit_file",
            {"file_path": "content/report_draft_v1.md", "old_string": "x", "new_string": "y"},
            decision, tags,
        )
        self.assertIsNotNone(result)

    def test_non_canonical_path_passes_unchecked(self):
        """写其他路径不归 gate 管"""
        handler = self._make_handler_with_project()
        result = handler._gate_canonical_draft_tool_call(
            self.project_id, "write_file",
            {"file_path": "plan/notes.md", "content": "..."},
            {}, [],
        )
        self.assertIsNone(result)

    def test_record_tagless_fallback_event_writes_state(self):
        handler = self._make_handler_with_project()
        decision = {"preflight_keyword_intent": "begin"}
        handler._gate_canonical_draft_tool_call(
            self.project_id, "append_report_draft",
            {"content": "x"},
            decision, [],
        )
        state = handler._load_conversation_state(self.project_id, [])
        events = [e for e in state.get("events", []) if e.get("type") == "tagless_draft_fallback"]
        self.assertGreaterEqual(len(events), 1)

    def test_append_report_draft_no_file_path_still_gated(self):
        """v3 关键回归测试：append_report_draft 真实 schema 没 file_path，
        但 gate 不能因此绕过——它按工具名识别 canonical draft 目标"""
        handler = self._make_handler_with_project()
        # 没 file_path、没 tag、没 keyword_intent → 必须 block
        result = handler._gate_canonical_draft_tool_call(
            self.project_id, "append_report_draft",
            {"content": "any text"},  # 真实 schema 只有 content
            {"preflight_keyword_intent": None},
            [],
        )
        self.assertIsNotNone(result)  # 必须 block，不能因为缺 file_path 就 pass

    def test_gate_edit_file_section_keyword_fallback_passes(self):
        """fix4 v5: edit_file + tag_intents 空 + keyword_intent='section' → pass + record fallback"""
        handler = self._make_handler_with_project()
        decision = handler._make_canonical_draft_decision(
            stage_code="S4", mode="require", priority="P_PREFLIGHT_OK",
            preflight_keyword_intent="section",
            rewrite_target_label="第二章 战力演化",
        )
        block = handler._gate_canonical_draft_tool_call(
            self.project_id,
            tool_name="edit_file",
            tool_args={"file_path": "content/report_draft_v1.md"},
            decision=decision,
            tags=[],
        )
        self.assertIsNone(block)
        events = handler._load_conversation_state(self.project_id).get("events", [])
        self.assertTrue(
            any(e.get("type") == "tagless_draft_fallback"
                and e.get("fallback_intent") == "section" for e in events),
        )

    def test_gate_edit_file_replace_keyword_fallback_passes(self):
        """fix4 v5: edit_file + tag_intents 空 + keyword_intent='replace' → pass + record"""
        handler = self._make_handler_with_project()
        decision = handler._make_canonical_draft_decision(
            stage_code="S4", mode="require", priority="P_PREFLIGHT_OK",
            preflight_keyword_intent="replace",
            old_text="体能", new_text="力量",
        )
        block = handler._gate_canonical_draft_tool_call(
            self.project_id,
            tool_name="edit_file",
            tool_args={"file_path": "content/report_draft_v1.md"},
            decision=decision,
            tags=[],
        )
        self.assertIsNone(block)
        events = handler._load_conversation_state(self.project_id).get("events", [])
        self.assertTrue(
            any(e.get("type") == "tagless_draft_fallback"
                and e.get("fallback_intent") == "replace" for e in events),
        )

    def test_gate_edit_file_no_tag_no_keyword_blocks(self):
        """fix4 v5: tag 空 + keyword_intent=None → block (UX 跟旧通道一致 fail-fast)"""
        handler = self._make_handler_with_project()
        decision = handler._make_canonical_draft_decision(
            stage_code="S4", mode="no_write", priority="P_PREFLIGHT_OK",
            preflight_keyword_intent=None,
        )
        block = handler._gate_canonical_draft_tool_call(
            self.project_id,
            tool_name="edit_file",
            tool_args={"file_path": "content/report_draft_v1.md"},
            decision=decision,
            tags=[],
        )
        self.assertIsNotNone(block)
        self.assertIn("draft-action", block)

    def test_gate_edit_file_with_section_tag_still_passes(self):
        """fix4 v5 regression: 显式 section tag 仍优先放行（不依赖 fallback）"""
        from backend.draft_action import DraftActionEvent
        handler = self._make_handler_with_project()
        decision = handler._make_canonical_draft_decision(
            stage_code="S4", mode="require", priority="P_PREFLIGHT_OK",
            preflight_keyword_intent=None,
        )
        tag = DraftActionEvent(
            raw="<draft-action>section:第二章</draft-action>",
            intent="section", section_label="第二章",
            old_text=None, new_text=None, start=0, end=10,
            executable=True, ignored_reason=None,
        )
        block = handler._gate_canonical_draft_tool_call(
            self.project_id,
            tool_name="edit_file",
            tool_args={"file_path": "content/report_draft_v1.md"},
            decision=decision,
            tags=[tag],
        )
        self.assertIsNone(block)

    def test_execute_plan_write_invokes_gate_before_legacy_block(self):
        """Task 19 fix2 P0 regression: gate must run BEFORE _non_plan_write_block_reason.

        Setup: turn_context has canonical_draft_decision with mode=no_write (the Bug A
        legacy-classifier path) AND draft_action_events containing executable begin tag
        (model correctly emitted tag). Without fix2, legacy block fires first and gate
        never runs (gate_block event count stays 0). With fix2, gate sees the begin tag
        and the canonical write passes through, advancing to subsequent checks.
        """
        from backend.draft_action import DraftActionEvent

        handler = self._make_handler_with_project()
        # Set turn_context: legacy classified no_write (Bug A symptom)
        handler._turn_context["canonical_draft_decision"] = {
            "mode": "no_write",
            "stage_code": "S4",
        }
        # Model emitted explicit begin tag (Phase 2 happy path)
        handler._turn_context["draft_action_events"] = [
            DraftActionEvent(
                raw="<draft-action>begin</draft-action>",
                intent="begin",
                executable=True,
            ),
        ]
        # Call _execute_plan_write with canonical draft path
        result = handler._execute_plan_write(
            self.project_id,
            file_path="content/report_draft_v1.md",
            content="# Report\n\nFirst draft body.\n",
            source_tool_name="append_report_draft",
            source_tool_args={"content": "First draft body."},
            persist_func_name="write_file",
            persist_args={
                "file_path": "content/report_draft_v1.md",
                "content": "# Report\n\nFirst draft body.\n",
            },
        )
        # Must NOT return the legacy "本轮用户没有要求修改正文草稿" message
        msg = result.get("message") or ""
        self.assertNotIn(
            "本轮用户没有要求修改正文草稿",
            msg,
            f"legacy non_plan_write_block_reason fired before gate; result={result}",
        )

    def test_execute_plan_write_blocks_when_no_tag_no_keyword(self):
        """Task 19 fix2 regression: when neither tag nor preflight_keyword_intent,
        gate blocks at _execute_plan_write level (not legacy block)."""
        handler = self._make_handler_with_project()
        handler._turn_context["canonical_draft_decision"] = {
            "mode": "require",  # legacy allows
            "stage_code": "S4",
            "preflight_keyword_intent": None,  # no preflight signal
        }
        handler._turn_context["draft_action_events"] = []  # no tags
        result = handler._execute_plan_write(
            self.project_id,
            file_path="content/report_draft_v1.md",
            content="x" * 100,
            source_tool_name="append_report_draft",
            source_tool_args={"content": "x" * 100},
            persist_func_name="write_file",
            persist_args={
                "file_path": "content/report_draft_v1.md",
                "content": "x" * 100,
            },
        )
        self.assertEqual(result.get("status"), "error")
        msg = result.get("message") or ""
        # gate block message contains "请先在回复中发 <draft-action> tag"
        self.assertIn("<draft-action>", msg)

    def test_build_turn_context_injects_preflight_keyword_intent_for_begin(self):
        """Task 19 fix3 P0 regression: _build_turn_context must inject
        preflight_keyword_intent into canonical_draft_decision so gate fallback
        (spec §4.8) can fire for tagless 'begin' utterances."""
        handler = self._make_handler_with_project()
        handler._build_turn_context(self.project_id, "开始写报告吧")
        decision = handler._turn_context.get("canonical_draft_decision") or {}
        self.assertEqual(decision.get("preflight_keyword_intent"), "begin")

    def test_build_turn_context_injects_keyword_intent_for_continue(self):
        """Phase 2a continue keyword recognition: '继续写第三章' should resolve
        to continue intent via the silent preflight injection."""
        handler = self._make_handler_with_project()
        handler._build_turn_context(self.project_id, "继续写第三章")
        decision = handler._turn_context.get("canonical_draft_decision") or {}
        self.assertEqual(decision.get("preflight_keyword_intent"), "continue")

    def test_build_required_write_snapshots_uses_injected_decision_for_section_fallback(self):
        """fix4-fix2 (Bug 8): tagless section fallback should populate
        required_write_snapshots with rewrite_target_label/snapshot/required_edit_scope.
        Without the cached-decision fix, snapshots are empty (legacy classify alone
        can't see section keyword fallback)."""
        handler = self._make_handler_with_project()
        draft_path = self.project_dir / "content" / "report_draft_v1.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(
            "## 第一章 引言\n内容0\n## 第二章 战力演化\n内容B\n", encoding="utf-8",
        )
        with mock.patch.object(
            handler.skill_engine,
            "_infer_stage_state",
            return_value=self._mock_stage_state("S4"),
        ):
            handler._build_turn_context(self.project_id, "把第二章重写一下")
            # Sanity: inject promoted mode + populated target fields
            decision = handler._turn_context.get("canonical_draft_decision") or {}
            self.assertEqual(decision.get("mode"), "require")
            self.assertEqual(decision.get("required_edit_scope"), "section")
            self.assertEqual(decision.get("rewrite_target_label"), "第二章 战力演化")

            # Now snapshot builder should pick up the injected decision
            snapshots = handler._build_required_write_snapshots(
                self.project_id, "把第二章重写一下",
            )
        canonical_path = handler.skill_engine.REPORT_DRAFT_PATH
        self.assertIn(canonical_path, snapshots)
        snap = snapshots[canonical_path]
        self.assertEqual(snap.get("required_edit_scope"), "section")
        self.assertEqual(snap.get("rewrite_target_label"), "第二章 战力演化")
        self.assertIn("内容B", str(snap.get("rewrite_target_snapshot") or ""))

    def test_build_turn_context_silent_no_user_notice_on_s0_reject(self):
        """Silent contract: when silent preflight rejects (S0 + draft intent),
        no surface_to_user notice may be emitted by the inject path."""
        handler = self._make_handler_with_project()
        # Project has no S0 prereq -> preflight will reject in S0
        handler._build_turn_context(self.project_id, "开始写报告吧")
        notices = handler._turn_context.get("pending_system_notices", [])
        user_notices = [n for n in notices if n.get("surface_to_user")]
        self.assertEqual(
            user_notices, [],
            f"silent preflight injection leaked user-visible notice: {user_notices}",
        )

    def test_execute_plan_write_fallback_passes_for_tagless_begin(self):
        """End-to-end: with fix3, '开始写报告吧' -> preflight_keyword_intent=begin
        injected -> gate fallback path passes append_report_draft -> write is
        NOT blocked at gate level (may still error for other reasons; key is
        gate doesn't return CANONICAL_DRAFT_REQUIRES_EXPLICIT_TAG_MESSAGE)."""
        handler = self._make_handler_with_project()
        handler._build_turn_context(self.project_id, "开始写报告吧")
        # Sanity: the inject worked
        decision = handler._turn_context.get("canonical_draft_decision") or {}
        self.assertEqual(decision.get("preflight_keyword_intent"), "begin")
        # Gate should not block append_report_draft via tag-required message
        result = handler._execute_plan_write(
            self.project_id,
            file_path="content/report_draft_v1.md",
            content="x" * 200,
            source_tool_name="append_report_draft",
            source_tool_args={"content": "x" * 200},
            persist_func_name="write_file",
            persist_args={"file_path": "content/report_draft_v1.md", "content": "x" * 200},
        )
        msg = result.get("message") or ""
        # Gate-block message contains "<draft-action>" - must NOT appear
        self.assertNotIn(
            "请先在回复中发 <draft-action>",
            msg,
            f"gate blocked despite keyword fallback eligibility; result={result}",
        )

    def test_execute_plan_write_section_fallback_passes_for_tagless_section_request(self):
        """fix4 v5 e2e: '把第二章重写一下' + draft 含唯一第二章 →
        _execute_plan_write 不返回 'draft-action' block message"""
        handler = self._make_handler_with_project()
        draft_path = self.project_dir / "content" / "report_draft_v1.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(
            "## 第一章 引言\n内容A\n## 第二章 战力演化\n内容B\n", encoding="utf-8",
        )
        handler._build_turn_context(self.project_id, "把第二章重写一下")
        decision = handler._turn_context.get("canonical_draft_decision") or {}
        self.assertEqual(decision.get("preflight_keyword_intent"), "section")
        # fix4-fix1 (Bug 2): target enforcement fields must be propagated by inject
        self.assertEqual(decision.get("rewrite_target_label"), "第二章 战力演化")
        self.assertEqual(decision.get("required_edit_scope"), "section")
        self.assertIn("内容B", str(decision.get("rewrite_target_snapshot") or ""))
        result = handler._execute_plan_write(
            self.project_id,
            file_path="content/report_draft_v1.md",
            content="## 第二章 战力演化\n新内容B\n",
            source_tool_name="edit_file",
            source_tool_args={
                "file_path": "content/report_draft_v1.md",
                "old_string": "## 第二章 战力演化\n内容B",
                "new_string": "## 第二章 战力演化\n新内容B",
            },
            persist_func_name="edit_file",
            persist_args={
                "file_path": "content/report_draft_v1.md",
                "old_string": "## 第二章 战力演化\n内容B",
                "new_string": "## 第二章 战力演化\n新内容B",
            },
        )
        msg = result.get("message") or ""
        self.assertNotIn(
            "请先在回复中发 <draft-action>", msg,
            f"gate blocked despite section keyword fallback eligibility; result={result}",
        )


for _inherited_test_name in dir(ChatRuntimeTests):
    if (
        _inherited_test_name.startswith("test_")
        and _inherited_test_name not in GateCanonicalDraftToolCallTests.__dict__
    ):
        setattr(GateCanonicalDraftToolCallTests, _inherited_test_name, None)
del _inherited_test_name


class DraftDecisionCompareEventTests(ChatRuntimeTests):
    def test_compare_event_written_per_turn(self):
        """跑一个常规 turn，conversation_state 应含一条 draft_decision_compare 事件。"""
        handler = self._make_handler_with_project()
        # 触发 _record_draft_decision_compare_event 直接调用（不走完整 turn）
        handler._record_draft_decision_compare_event(
            self.project_id,
            turn_id="t1", user_message="开始写报告吧",
            old_decision={"mode": "no_write", "priority": "P10"},
            new_decision={"mode": "require", "priority": "P_PREFLIGHT_OK",
                          "preflight_keyword_intent": "begin"},
            tags=[],
            fallback_used=False, fallback_tool=None, fallback_intent=None,
            blocked_missing_tag=False, blocked_tool=None,
            new_channel_exception=None,
        )
        state = handler._load_conversation_state(self.project_id, [])
        events = [e for e in state.get("events", []) if e.get("type") == "draft_decision_compare"]
        self.assertEqual(len(events), 1)
        e = events[-1]
        for key in ("turn_id", "user_message_hash", "old_decision", "new_decision",
                    "agreement", "divergence_reason", "tag_present", "fallback_used",
                    "fallback_tool", "fallback_intent", "blocked_missing_tag",
                    "blocked_tool", "new_channel_exception", "recorded_at"):
            self.assertIn(key, e)

    def test_compare_agreement_correctly_computed(self):
        handler = self._make_handler_with_project()
        handler._record_draft_decision_compare_event(
            self.project_id, turn_id="t1", user_message="x",
            old_decision={"mode": "no_write"},
            new_decision={"mode": "no_write"},
            tags=[], fallback_used=False, fallback_tool=None, fallback_intent=None,
            blocked_missing_tag=False, blocked_tool=None, new_channel_exception=None,
        )
        state = handler._load_conversation_state(self.project_id, [])
        e = state["events"][-1]
        self.assertTrue(e["agreement"])
        self.assertIsNone(e["divergence_reason"])

    def test_compare_disagreement_records_divergence(self):
        handler = self._make_handler_with_project()
        handler._record_draft_decision_compare_event(
            self.project_id, turn_id="t1", user_message="x",
            old_decision={"mode": "no_write"},
            new_decision={"mode": "require"},
            tags=[], fallback_used=False, fallback_tool=None, fallback_intent=None,
            blocked_missing_tag=False, blocked_tool=None, new_channel_exception=None,
        )
        state = handler._load_conversation_state(self.project_id, [])
        e = state["events"][-1]
        self.assertFalse(e["agreement"])
        self.assertIn("no_write", e["divergence_reason"])

    def test_exception_event_written_when_new_channel_crashes(self):
        handler = self._make_handler_with_project()
        handler._record_draft_decision_exception_event(
            self.project_id, turn_id="t2", stage="preflight",
            exception_class="ValueError", exception_message="test",
        )
        state = handler._load_conversation_state(self.project_id, [])
        events = [e for e in state["events"] if e.get("type") == "draft_decision_exception"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["stage"], "preflight")
        self.assertEqual(events[0]["exception_class"], "ValueError")

    def test_compare_includes_tag_present_per_intent(self):
        from backend.draft_action import DraftActionEvent
        handler = self._make_handler_with_project()
        tags = [
            DraftActionEvent(raw="...", intent="begin", executable=True),
            DraftActionEvent(raw="...", intent="section", executable=False),
        ]
        handler._record_draft_decision_compare_event(
            self.project_id, turn_id="t3", user_message="x",
            old_decision={"mode": "require"}, new_decision={"mode": "require"},
            tags=tags,
            fallback_used=False, fallback_tool=None, fallback_intent=None,
            blocked_missing_tag=False, blocked_tool=None, new_channel_exception=None,
        )
        state = handler._load_conversation_state(self.project_id, [])
        tp = state["events"][-1]["tag_present"]
        self.assertTrue(tp["begin"])
        self.assertFalse(tp["section"])  # executable=False 不算

    def test_compare_writer_silent_does_not_emit_notice_in_s0_reject(self):
        """v2 fix1 P0 regression: when compare writer's preflight rejects in S0/S1,
        it MUST NOT emit a user-visible system_notice (Phase 2a silent channel contract)."""
        handler = self._make_handler_with_project()
        # Force project into S0 by NOT writing any stage prerequisites.
        # turn_context starts empty.
        handler._turn_context = handler._new_turn_context(can_write_non_plan=False)
        # Run compare writer with a draft-intent message in S0.
        handler._run_phase2a_compare_writer(self.project_id, "开始写报告吧")
        # surface_to_user notice MUST NOT have been emitted by the new channel.
        notices = handler._turn_context.get("pending_system_notices", [])
        user_notices = [
            n for n in notices
            if (isinstance(n, dict) and n.get("surface_to_user") is True)
        ]
        self.assertEqual(
            user_notices, [],
            f"compare writer leaked user-visible notice into pending: {user_notices}",
        )

    def test_preflight_silent_param_skips_notice(self):
        """Direct test of silent param: preflight in silent mode does not emit notice."""
        handler = self._make_handler_with_project()
        decision = handler._preflight_canonical_draft_check(
            self.project_id, "开始写报告吧", stage_code="S0", silent=True,
        )
        # decision still rejects (return value contract unchanged)
        self.assertEqual(decision["mode"], "reject")
        # but no surface_to_user notice was emitted
        notices = handler._turn_context.get("pending_system_notices", [])
        user_notices = [n for n in notices if n.get("surface_to_user")]
        self.assertEqual(user_notices, [])


for _inherited_test_name in dir(ChatRuntimeTests):
    if (
        _inherited_test_name.startswith("test_")
        and _inherited_test_name not in DraftDecisionCompareEventTests.__dict__
    ):
        setattr(DraftDecisionCompareEventTests, _inherited_test_name, None)
del _inherited_test_name


class ExtractUserMessageTextTests(ChatRuntimeTests):
    def test_str_content_returns_as_is(self):
        handler = self._make_handler_with_project()
        self.assertEqual(handler._extract_user_message_text({"content": "plain"}), "plain")

    def test_multipart_extracts_text_parts_only(self):
        handler = self._make_handler_with_project()
        msg = {"content": [
            {"type": "text", "text": "first"},
            {"type": "image_url", "image_url": {"url": "data:..."}},
            {"type": "text", "text": "second"},
        ]}
        result = handler._extract_user_message_text(msg)
        self.assertEqual(result, "first\n\nsecond")

    def test_none_message_returns_empty(self):
        handler = self._make_handler_with_project()
        self.assertEqual(handler._extract_user_message_text(None), "")

    def test_image_only_multipart_returns_empty(self):
        handler = self._make_handler_with_project()
        msg = {"content": [{"type": "image_url", "image_url": {"url": "..."}}]}
        self.assertEqual(handler._extract_user_message_text(msg), "")


for _inherited_test_name in dir(ChatRuntimeTests):
    if (
        _inherited_test_name.startswith("test_")
        and _inherited_test_name not in ExtractUserMessageTextTests.__dict__
    ):
        setattr(ExtractUserMessageTextTests, _inherited_test_name, None)
del _inherited_test_name


class NewTurnContextFieldsTests(ChatRuntimeTests):
    def test_new_turn_context_has_user_message_text(self):
        handler = self._make_handler_with_project()
        ctx = handler._new_turn_context(can_write_non_plan=True)
        self.assertEqual(ctx.get("user_message_text"), "")

    def test_new_turn_context_has_obligation_default_none(self):
        handler = self._make_handler_with_project()
        ctx = handler._new_turn_context(can_write_non_plan=True)
        self.assertIsNone(ctx.get("canonical_draft_write_obligation"))

    def test_new_turn_context_has_read_file_snapshots_empty_dict(self):
        handler = self._make_handler_with_project()
        ctx = handler._new_turn_context(can_write_non_plan=True)
        self.assertEqual(ctx.get("read_file_snapshots"), {})


for _inherited_test_name in dir(ChatRuntimeTests):
    if (
        _inherited_test_name.startswith("test_")
        and _inherited_test_name not in NewTurnContextFieldsTests.__dict__
    ):
        setattr(NewTurnContextFieldsTests, _inherited_test_name, None)
del _inherited_test_name


class BuildTurnContextCachesUserMessageTests(ChatRuntimeTests):
    def test_build_turn_context_caches_user_message_text(self):
        handler = self._make_handler_with_project()
        handler._build_turn_context(self.project_id, "把第二章重写一下")
        self.assertEqual(
            handler._turn_context.get("user_message_text"),
            "把第二章重写一下",
        )


for _inherited_test_name in dir(ChatRuntimeTests):
    if (
        _inherited_test_name.startswith("test_")
        and _inherited_test_name not in BuildTurnContextCachesUserMessageTests.__dict__
    ):
        setattr(BuildTurnContextCachesUserMessageTests, _inherited_test_name, None)
del _inherited_test_name


class CanonicalDraftWriteObligationTurnContextTests(ChatRuntimeTests):
    def test_obligation_set_for_section_keyword(self):
        handler = self._make_handler_with_project()
        handler._build_turn_context(self.project_id, "把第二章重写一下")
        ob = handler._turn_context.get("canonical_draft_write_obligation")
        self.assertIsNotNone(ob)
        self.assertEqual(ob["tool_family"], "rewrite_section")

    def test_obligation_none_for_unrelated(self):
        handler = self._make_handler_with_project()
        handler._build_turn_context(self.project_id, "你好，能介绍一下项目吗？")
        ob = handler._turn_context.get("canonical_draft_write_obligation")
        self.assertIsNone(ob)


for _inherited_test_name in dir(ChatRuntimeTests):
    if (
        _inherited_test_name.startswith("test_")
        and _inherited_test_name not in CanonicalDraftWriteObligationTurnContextTests.__dict__
    ):
        setattr(CanonicalDraftWriteObligationTurnContextTests, _inherited_test_name, None)
del _inherited_test_name


class ReadFileSnapshotHookTests(ChatRuntimeTests):
    def test_read_file_records_canonical_draft_mtime(self):
        handler = self._make_handler_with_project()
        # prepare draft file
        draft_path = self.project_dir / "content" / "report_draft_v1.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text("# 报告\n## 第一章\n内容\n", encoding="utf-8")
        handler._build_turn_context(self.project_id, "看一下正文")
        # trigger read_file
        result = handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "read_file",
                json.dumps({"file_path": "content/report_draft_v1.md"}),
            ),
        )
        self.assertEqual(result.get("status"), "success")
        snapshots = handler._turn_context.get("read_file_snapshots") or {}
        self.assertIn("content/report_draft_v1.md", snapshots)
        self.assertAlmostEqual(
            snapshots["content/report_draft_v1.md"],
            draft_path.stat().st_mtime,
            places=3,
        )

    def test_read_file_does_not_record_for_plan_path(self):
        handler = self._make_handler_with_project()
        # plan/* not recorded
        plan_path = self.project_dir / "plan" / "outline.md"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text("大纲", encoding="utf-8")
        handler._build_turn_context(self.project_id, "看一下大纲")
        handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "read_file",
                json.dumps({"file_path": "plan/outline.md"}),
            ),
        )
        snapshots = handler._turn_context.get("read_file_snapshots") or {}
        self.assertNotIn("plan/outline.md", snapshots)


for _inherited_test_name in dir(ChatRuntimeTests):
    if (
        _inherited_test_name.startswith("test_")
        and _inherited_test_name not in ReadFileSnapshotHookTests.__dict__
    ):
        setattr(ReadFileSnapshotHookTests, _inherited_test_name, None)
del _inherited_test_name


class ToolSchemaRegistrationTests(ChatRuntimeTests):
    def test_get_tools_lists_all_4_write_tools(self):
        handler = self._make_handler_with_project()
        tools = handler._get_tools()
        names = {t["function"]["name"] for t in tools if "function" in t}
        self.assertIn("append_report_draft", names)
        self.assertIn("rewrite_report_section", names)
        self.assertIn("replace_report_text", names)
        self.assertIn("rewrite_report_draft", names)

    def test_rewrite_report_section_schema_only_content_param(self):
        handler = self._make_handler_with_project()
        tools = handler._get_tools()
        sec = next(t for t in tools if t.get("function", {}).get("name") == "rewrite_report_section")
        params = sec["function"]["parameters"]
        self.assertEqual(set(params["properties"].keys()), {"content"})
        self.assertEqual(params["required"], ["content"])

    def test_replace_report_text_schema_old_new(self):
        handler = self._make_handler_with_project()
        tools = handler._get_tools()
        rep = next(t for t in tools if t.get("function", {}).get("name") == "replace_report_text")
        params = rep["function"]["parameters"]
        self.assertEqual(set(params["properties"].keys()), {"old", "new"})
        self.assertEqual(set(params["required"]), {"old", "new"})


for _inherited_test_name in dir(ChatRuntimeTests):
    if (
        _inherited_test_name.startswith("test_")
        and _inherited_test_name not in ToolSchemaRegistrationTests.__dict__
    ):
        setattr(ToolSchemaRegistrationTests, _inherited_test_name, None)
del _inherited_test_name


class _WriteToolTestMixin:
    """Shared test helpers for new write tool tests."""

    def _put_draft(self, body):
        draft_path = self.project_dir / "content" / "report_draft_v1.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(body, encoding="utf-8")
        return draft_path

    def _setup_outline_confirmed_s4(self, handler):
        """Patch skill_engine to report S4 + set outline_confirmed_at checkpoint."""
        # Save the outline_confirmed_at checkpoint (needed for check_outline_confirmed)
        handler.skill_engine._save_stage_checkpoint(
            self.project_dir, "outline_confirmed_at",
        )
        # Mock _infer_stage_state to return S4 (avoids needing full file tree)
        original_infer = handler.skill_engine._infer_stage_state
        def _mock_infer(project_path):
            result = original_infer(project_path)
            result = dict(result)
            result["stage_code"] = "S4"
            return result
        handler.skill_engine._infer_stage_state = _mock_infer

    def _trigger_read_file(self, handler):
        handler._execute_tool(
            self.project_id,
            self._make_tool_call(
                "read_file",
                json.dumps({"file_path": "content/report_draft_v1.md"}),
            ),
        )


class RewriteReportSectionToolTests(_WriteToolTestMixin, ChatRuntimeTests):
    def test_happy_path_rewrites_section(self):
        handler = self._make_handler_with_project()
        self._setup_outline_confirmed_s4(handler)
        self._put_draft("# 报告\n## 第一章 引言\n旧内容0\n## 第二章 战力分析\n旧内容B\n")
        handler._build_turn_context(self.project_id, "把第二章重写一下")
        self._trigger_read_file(handler)
        result = handler._tool_rewrite_report_section(
            self.project_id, content="## 第二章 战力分析\n新内容B\n",
        )
        self.assertEqual(result.get("status"), "success")
        actual = (self.project_dir / "content" / "report_draft_v1.md").read_text(encoding="utf-8")
        self.assertIn("新内容B", actual)
        self.assertNotIn("旧内容B", actual)
        self.assertIn("旧内容0", actual)  # 第一章不动

    def test_stage_pre_s4_rejects(self):
        handler = self._make_handler_with_project()
        # 不 set outline_confirmed_at, 阶段保持 S0
        handler._build_turn_context(self.project_id, "把第二章重写一下")
        result = handler._tool_rewrite_report_section(
            self.project_id, content="## 第二章\n新内容\n",
        )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("S4", result.get("message", ""))

    def test_outline_unconfirmed_rejects(self):
        handler = self._make_handler_with_project()
        # 模拟 S4 阶段但没有 outline_confirmed_at 检查点
        original_infer = handler.skill_engine._infer_stage_state
        def _mock_infer_s4(project_path):
            result = dict(original_infer(project_path))
            result["stage_code"] = "S4"
            return result
        handler.skill_engine._infer_stage_state = _mock_infer_s4
        # 注意：不保存 outline_confirmed_at
        handler._build_turn_context(self.project_id, "把第二章重写一下")
        result = handler._tool_rewrite_report_section(
            self.project_id, content="## 第二章\n新内容\n",
        )
        # check_outline_confirmed 应该报错（outline_confirmed_at 未设置）
        self.assertEqual(result.get("status"), "error")
        self.assertIn("确认大纲", result.get("message", ""))

    def test_mutation_limit_blocks_second_call(self):
        handler = self._make_handler_with_project()
        self._setup_outline_confirmed_s4(handler)
        self._put_draft("# 报告\n## 第二章 战力分析\n内容\n")
        handler._build_turn_context(self.project_id, "把第二章重写一下")
        self._trigger_read_file(handler)
        # 第一次成功
        handler._tool_rewrite_report_section(
            self.project_id, content="## 第二章 战力分析\n新内容1\n",
        )
        # mutation 已 set，第二次应 reject
        result = handler._tool_rewrite_report_section(
            self.project_id, content="## 第二章 战力分析\n新内容2\n",
        )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("本轮已经修改过", result.get("message", ""))

    def test_draft_missing_rejects(self):
        handler = self._make_handler_with_project()
        self._setup_outline_confirmed_s4(handler)
        # 不 put_draft
        handler._build_turn_context(self.project_id, "把第二章重写一下")
        result = handler._tool_rewrite_report_section(
            self.project_id, content="## 第二章\n新内容\n",
        )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("草稿", result.get("message", ""))

    def test_user_msg_no_section_prefix_rejects(self):
        handler = self._make_handler_with_project()
        self._setup_outline_confirmed_s4(handler)
        self._put_draft("# 报告\n## 第二章\n内容\n")
        handler._build_turn_context(self.project_id, "重写一下")  # 没说哪一章
        self._trigger_read_file(handler)
        result = handler._tool_rewrite_report_section(
            self.project_id, content="## 第二章\n新内容\n",
        )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("章/节", result.get("message", ""))

    def test_partial_multi_prefix_rejects(self):
        handler = self._make_handler_with_project()
        self._setup_outline_confirmed_s4(handler)
        self._put_draft("# 报告\n## 第二章\n内容\n")  # 没第三章
        handler._build_turn_context(self.project_id, "把第二章和第三章重写")
        self._trigger_read_file(handler)
        result = handler._tool_rewrite_report_section(
            self.project_id, content="## 第二章\n新内容\n",
        )
        self.assertEqual(result.get("status"), "error")

    def test_content_no_h2_prefix_rejects(self):
        handler = self._make_handler_with_project()
        self._setup_outline_confirmed_s4(handler)
        self._put_draft("# 报告\n## 第二章\n内容\n")
        handler._build_turn_context(self.project_id, "把第二章重写")
        self._trigger_read_file(handler)
        result = handler._tool_rewrite_report_section(
            self.project_id, content="新内容（缺 ## 标题）",
        )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("`## 章节标题`", result.get("message", ""))

    def test_content_multiple_h2_rejects(self):
        handler = self._make_handler_with_project()
        self._setup_outline_confirmed_s4(handler)
        self._put_draft("# 报告\n## 第二章\n内容\n")
        handler._build_turn_context(self.project_id, "把第二章重写")
        self._trigger_read_file(handler)
        result = handler._tool_rewrite_report_section(
            self.project_id, content="## 第二章\nA\n## 第三章\nB\n",
        )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("多个章节", result.get("message", ""))

    def test_content_exceeds_cap_rejects(self):
        handler = self._make_handler_with_project()
        self._setup_outline_confirmed_s4(handler)
        target_snap = "## 第二章 X\n" + "短内容" * 10
        self._put_draft("# 报告\n" + target_snap + "\n")
        handler._build_turn_context(self.project_id, "把第二章重写")
        self._trigger_read_file(handler)
        # cap = max(3000, 3 * len(target_snap)) ≈ 3000
        oversized = "## 第二章 X\n" + ("X" * 5000)
        result = handler._tool_rewrite_report_section(
            self.project_id, content=oversized,
        )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("超过", result.get("message", ""))

    def test_no_read_before_write_rejects(self):
        handler = self._make_handler_with_project()
        self._setup_outline_confirmed_s4(handler)
        self._put_draft("# 报告\n## 第二章\n内容\n")
        handler._build_turn_context(self.project_id, "把第二章重写")
        # 不 trigger_read_file
        result = handler._tool_rewrite_report_section(
            self.project_id, content="## 第二章\n新内容\n",
        )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("read_file", result.get("message", ""))


for _inherited_test_name in dir(ChatRuntimeTests):
    if (
        _inherited_test_name.startswith("test_")
        and _inherited_test_name not in RewriteReportSectionToolTests.__dict__
    ):
        setattr(RewriteReportSectionToolTests, _inherited_test_name, None)
del _inherited_test_name


class ReplaceReportTextToolTests(_WriteToolTestMixin, ChatRuntimeTests):
    def test_happy_path_replaces_unique_text(self):
        handler = self._make_handler_with_project()
        self._setup_outline_confirmed_s4(handler)
        self._put_draft("# 报告\n## 第一章\n渠道效率是关键指标。\n")
        handler._build_turn_context(self.project_id, "把渠道效率改成渠道质量")
        self._trigger_read_file(handler)
        result = handler._tool_replace_report_text(
            self.project_id, old="渠道效率", new="渠道质量",
        )
        self.assertEqual(result.get("status"), "success")
        actual = (self.project_dir / "content" / "report_draft_v1.md").read_text(encoding="utf-8")
        self.assertIn("渠道质量", actual)
        self.assertNotIn("渠道效率", actual)

    def test_zero_occurrences_rejects(self):
        handler = self._make_handler_with_project()
        self._setup_outline_confirmed_s4(handler)
        self._put_draft("# 报告\n## 第一章\n内容A\n")
        handler._build_turn_context(self.project_id, "把不存在的文字改掉")
        self._trigger_read_file(handler)
        result = handler._tool_replace_report_text(
            self.project_id, old="不存在的文字XYZ", new="新文字",
        )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("未找到", result.get("message", ""))

    def test_multiple_occurrences_rejects(self):
        handler = self._make_handler_with_project()
        self._setup_outline_confirmed_s4(handler)
        self._put_draft("# 报告\n## 第一章\n重复 重复\n")
        handler._build_turn_context(self.project_id, "把重复改成单次")
        self._trigger_read_file(handler)
        result = handler._tool_replace_report_text(
            self.project_id, old="重复", new="单次",
        )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("不唯一", result.get("message", ""))

    def test_empty_old_rejects(self):
        handler = self._make_handler_with_project()
        self._setup_outline_confirmed_s4(handler)
        self._put_draft("# 报告\n## 第一章\n内容\n")
        handler._build_turn_context(self.project_id, "替换文字")
        self._trigger_read_file(handler)
        result = handler._tool_replace_report_text(
            self.project_id, old="", new="新内容",
        )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("`old`", result.get("message", ""))

    def test_mutation_limit_blocks_second_call(self):
        handler = self._make_handler_with_project()
        self._setup_outline_confirmed_s4(handler)
        self._put_draft("# 报告\n## 第一章\n渠道效率是关键。\n")
        handler._build_turn_context(self.project_id, "把渠道效率改成渠道质量")
        self._trigger_read_file(handler)
        # 第一次成功
        handler._tool_replace_report_text(
            self.project_id, old="渠道效率", new="渠道质量",
        )
        # mutation 已 set，第二次应 reject
        result = handler._tool_replace_report_text(
            self.project_id, old="渠道质量", new="其他",
        )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("本轮已经修改过", result.get("message", ""))

    def test_stage_pre_s4_rejects(self):
        handler = self._make_handler_with_project()
        # 不 setup S4，阶段保持 S0
        self._put_draft("# 报告\n## 第一章\n内容\n")
        handler._build_turn_context(self.project_id, "替换文字")
        result = handler._tool_replace_report_text(
            self.project_id, old="内容", new="新内容",
        )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("S4", result.get("message", ""))

    def test_no_read_before_write_rejects(self):
        handler = self._make_handler_with_project()
        self._setup_outline_confirmed_s4(handler)
        self._put_draft("# 报告\n## 第一章\n内容\n")
        handler._build_turn_context(self.project_id, "替换文字")
        # 不 trigger_read_file
        result = handler._tool_replace_report_text(
            self.project_id, old="内容", new="新内容",
        )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("read_file", result.get("message", ""))


for _inherited_test_name in dir(ChatRuntimeTests):
    if (
        _inherited_test_name.startswith("test_")
        and _inherited_test_name not in ReplaceReportTextToolTests.__dict__
    ):
        setattr(ReplaceReportTextToolTests, _inherited_test_name, None)
del _inherited_test_name
