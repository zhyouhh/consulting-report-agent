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

        self.assertEqual(result["content"], "最终答复")
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

        self.assertEqual(result["content"], "最终答复")
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

        self.assertEqual(result["content"], "最终答复")
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
                            "arguments": '{"file_path":"report_draft_v1.md","content":"# 正文"}',
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
            (project_dir / "content" / "final-report.md").write_text(
                "# Final report\n\n## Executive summary\nA concrete section.\n",
                encoding="utf-8",
            )
            handler = ChatHandler(self._make_settings(projects_dir=projects_dir), engine)

            self.assertTrue(handler._should_allow_non_plan_write(project["id"], "请扩写到5000字"))
            self.assertTrue(handler._should_allow_non_plan_write(project["id"], "帮我润色一下现有正文"))

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
                    "file_path": "plan/review-checklist.md",
                    "content": "审查人：咨询报告写作助手",
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
        self.assertEqual(notices[0]["category"], "write_blocked")
        self.assertEqual(notices[0]["path"], "plan/review-checklist.md")
        self.assertTrue(notices[0]["reason"])
        self.assertTrue(notices[0]["user_action"])

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
    def test_system_notice_deduplicated_within_turn(self, mock_openai):
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

    @mock.patch("backend.chat.OpenAI")
    def test_system_notice_reset_between_turns(self, mock_openai):
        handler = self._make_handler_with_project()
        blocked_call = self._make_stream_tool_call_chunk(
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
                        "file_path": "plan/review-checklist.md",
                        "content": "审查人：咨询报告写作助手",
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
        self.assertEqual(result["system_notices"][0].category, "write_blocked")

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

        self.assertEqual(result["content"], "已实际写入 `plan/outline.md`，请确认大纲。")
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
    def test_expected_plan_writes_include_report_draft_targets_when_assistant_claims_report_saved(self, mock_openai):
        del mock_openai
        handler = ChatHandler(
            self._make_settings(),
            SkillEngine(Path(tempfile.gettempdir()) / "expected-report-write-projects", self.repo_skill_dir),
        )

        expected = handler._expected_plan_writes_for_message(
            "我已写入 `report_draft_v1.md` 和 `content/final-report.md`，并完成正文初稿。"
        )

        self.assertIn("report_draft_v1.md", expected)
        self.assertIn("content/final-report.md", expected)

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
    def test_expected_plan_writes_include_versioned_content_report_drafts_when_assistant_claims_saved(self, mock_openai):
        del mock_openai
        handler = ChatHandler(
            self._make_settings(),
            SkillEngine(Path(tempfile.gettempdir()) / "expected-content-report-v5-projects", self.repo_skill_dir),
        )

        expected = handler._expected_plan_writes_for_message(
            "已同步至 `content/report_draft_v5.md`，后续可继续审查。"
        )

        self.assertIn("content/report_draft_v5.md", expected)

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
        self.assertEqual(len(persisted["events"]), 2)
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
    def test_build_turn_context_sets_outline_checkpoint_from_keyword(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_stage_one_prerequisites(self.project_dir)

        handler._build_turn_context(self.project_id, "确认大纲，开始写")
        checkpoints = handler.skill_engine._load_stage_checkpoints(self.project_dir)

        self.assertIn("outline_confirmed_at", checkpoints)

    @mock.patch("backend.chat.OpenAI")
    def test_build_turn_context_records_checkpoint_event_on_set(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_stage_one_prerequisites(self.project_dir)

        handler._turn_context = handler._build_turn_context(self.project_id, "确认大纲")

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
    def test_build_turn_context_strong_outline_keyword_without_effective_outline_emits_prereq_notice(
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
        self.assertEqual(
            turn_context["pending_system_notices"],
            [
                {
                    "type": "system_notice",
                    "category": "checkpoint_prereq_missing",
                    "path": "plan/outline.md",
                    "reason": "需要先生成有效报告大纲，才能确认大纲并进入资料采集。",
                    "user_action": "请先让助手补齐 `plan/outline.md`，再确认大纲。",
                }
            ],
        )

    @mock.patch("backend.chat.OpenAI")
    def test_build_turn_context_confirm_outline_turn_immediately_allows_non_plan_write(self, mock_openai):
        del mock_openai
        handler = self._make_handler_with_project()
        self._write_stage_one_prerequisites(self.project_dir)

        turn_context = handler._build_turn_context(self.project_id, "确认大纲")

        self.assertTrue(turn_context["can_write_non_plan"])
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
