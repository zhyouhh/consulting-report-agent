import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

import httpx

from backend.chat import ChatHandler
from backend.config import Settings
from backend.skill import SkillEngine


class ChatRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.repo_skill_dir = Path(__file__).resolve().parents[1] / "skill"

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

    def _make_chunk(self, *, content=None, tool_calls=None):
        delta = SimpleNamespace(content=content, tool_calls=tool_calls)
        return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])

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
    def test_managed_gemini_chat_usage_uses_dynamic_context_policy(self, mock_openai):
        mock_openai.return_value.chat.completions.create.return_value = SimpleNamespace(
            usage=SimpleNamespace(total_tokens=4321),
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

        self.assertEqual(result["token_usage"]["current_tokens"], 4321)
        self.assertEqual(result["token_usage"]["max_tokens"], 500000)
        self.assertEqual(result["token_usage"]["effective_max_tokens"], 500000)
        self.assertEqual(result["token_usage"]["provider_max_tokens"], 1000000)
        self.assertFalse(result["token_usage"]["compressed"])
        self.assertEqual(result["token_usage"]["usage_mode"], "actual")
        self.assertEqual(
            mock_openai.return_value.chat.completions.create.call_args.kwargs["model"],
            "gemini-3-flash",
        )

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

            def fit_side_effect(conversation):
                fit_inputs.append(conversation)
                if len(fit_inputs) == 1:
                    return conversation, handler._estimate_tokens(conversation), False, policy
                return compressed_followup, handler._estimate_tokens(compressed_followup), True, policy

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
                side_effect=lambda conversation: (conversation, 0, False, policy),
            ):
                with mock.patch.object(handler, "_estimate_tokens", return_value=1234):
                    with mock.patch.object(
                        handler,
                        "_execute_tool",
                        return_value={"status": "success", "content": "工具结果"},
                    ):
                        result = handler.chat(project["id"], "继续", max_iterations=2)

        self.assertEqual(result["content"], "最终答复")
        self.assertEqual(result["token_usage"]["usage_mode"], "estimated")
        self.assertEqual(result["token_usage"]["current_tokens"], 1234)

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
                side_effect=lambda conversation: (conversation, 0, False, policy),
            ):
                handler.chat("demo", "璇风户缁?")

            self.assertEqual(
                mock_openai.return_value.chat.completions.create.call_args.kwargs["max_tokens"],
                2048,
            )

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
    def test_should_allow_non_plan_write_when_content_final_report_exists_and_user_asks_to_continue(self, mock_openai):
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

            self.assertTrue(handler._should_allow_non_plan_write(project["id"], "继续完善"))

    @mock.patch("backend.chat.OpenAI")
    def test_handler_write_file_rejects_outline_before_evidence_gate_is_satisfied(self, mock_openai):
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
        self.assertIn("notes.md", result["message"])
        self.assertIn("references.md", result["message"])
        self.assertIn("2-source", result["message"])

    @mock.patch("backend.chat.OpenAI")
    def test_handler_write_file_rejects_outline_when_references_have_only_one_source(self, mock_openai):
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
        self.assertIn("2-source", result["message"])

    @mock.patch("backend.chat.OpenAI")
    def test_handler_write_file_rejects_research_plan_before_evidence_gate_is_satisfied(self, mock_openai):
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
        self.assertIn("notes.md", result["message"])
        self.assertIn("references.md", result["message"])
        self.assertIn("2-source", result["message"])

    @mock.patch("backend.chat.OpenAI")
    def test_handler_write_file_rejects_research_plan_when_references_have_only_one_source(self, mock_openai):
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
        self.assertIn("2-source", result["message"])

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
