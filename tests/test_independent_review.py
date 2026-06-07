import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from backend.chat import ChatHandler
from backend.config import Settings
from backend.independent_review import (
    CANONICAL_REVIEW_PATH,
    INDEPENDENT_REVIEW_ANCHORS,
    INDEPENDENT_REVIEW_COMPLETION_MARKER,
    INDEPENDENT_REVIEW_SYSTEM_PROMPT,
    IndependentReviewAgent,
    MAX_DRAFT_WORDS_FOR_REVIEW,
)
from backend.skill import SkillEngine


class _FakeMessage:
    """SDK-message stand-in used only for the deepseek-compat helper parity tests."""

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

    # ---- streaming chunk builders (mirror the OpenAI SDK stream shape) ----

    def _delta_chunk(self, *, content=None, tool_calls=None, reasoning_content=None):
        delta = SimpleNamespace(
            content=content,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
        )
        return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])

    def _tc_chunk(self, index, *, id=None, name=None, arguments=None):
        function = None
        if name is not None or arguments is not None:
            function = SimpleNamespace(name=name, arguments=arguments)
        return SimpleNamespace(index=index, id=id, function=function)

    def _stream_text(self, *texts):
        """A streaming response that emits the given content fragments."""
        return iter([self._delta_chunk(content=t) for t in texts])

    def _stream_single_tool_call(self, name, args, call_id="call-1"):
        """A streaming response: first chunk carries id+name (empty args),
        then arguments split into fragments, mirroring the observed deepseek shape."""
        arg_text = json.dumps(args, ensure_ascii=False)
        chunks = [
            self._delta_chunk(tool_calls=[self._tc_chunk(0, id=call_id, name=name, arguments="")]),
        ]
        # split arguments into a few pieces so the test exercises accumulation
        mid = max(1, len(arg_text) // 2)
        for piece in (arg_text[:mid], arg_text[mid:]):
            if piece:
                chunks.append(
                    self._delta_chunk(tool_calls=[self._tc_chunk(0, id=None, name=None, arguments=piece)])
                )
        return iter(chunks)

    def _complete_review_text(self):
        return (
            "# 独立审查报告\n\n"
            "## 1. 结论-证据一致性\n未发现问题\n"
            "## 2. 关键假设与逻辑链\n未发现问题\n"
            "## 3. 数据口径一致性\n未发现问题\n"
            "## 4. 建议可执行性\n未发现问题\n"
            "## 5. 目标读者匹配\n未发现问题\n\n"
            f"{INDEPENDENT_REVIEW_COMPLETION_MARKER}\n"
        )

    # ---- Task 2.1: system prompt narration ----

    def test_review_system_prompt_requires_narration_and_forbids_conclusions(self):
        prompt = INDEPENDENT_REVIEW_SYSTEM_PROMPT
        # 工作流改为"边审边说"
        self.assertIn("边审边说", prompt)
        # 过程旁白 + 显式禁止在对话里下结论/罗列发现
        self.assertIn("过程旁白", prompt)
        self.assertIn("不要", prompt)
        self.assertIn("罗列审查发现", prompt)
        self.assertIn("只写进 plan/independent-review.md", prompt)
        # 报告格式契约不变：5 维度 anchor 文案 + 完成 marker 要求仍在
        for anchor in INDEPENDENT_REVIEW_ANCHORS:
            self.assertIn(anchor, prompt)
        self.assertIn(INDEPENDENT_REVIEW_COMPLETION_MARKER, prompt)
        # 仍要求最后一次性 write 报告
        self.assertIn("write_file", prompt)

    # ---- run() behaviour (streaming) ----

    def test_run_emits_progress_events(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        responses = [
            self._stream_single_tool_call("read_file", {"file_path": "plan/data-log.md"}, "call-1"),
            self._stream_single_tool_call("read_file", {"file_path": "plan/analysis-notes.md"}, "call-2"),
            self._stream_single_tool_call("read_file", {"file_path": "content/report_draft_v1.md"}, "call-3"),
            self._stream_single_tool_call(
                "write_file",
                {"file_path": CANONICAL_REVIEW_PATH, "content": self._complete_review_text()},
                "call-4",
            ),
            self._stream_text("审查完成，报告已生成"),
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

    def test_run_streams_content_delta(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        # write the canonical report first, then a final narration spread over chunks
        responses = [
            self._stream_single_tool_call(
                "write_file",
                {"file_path": CANONICAL_REVIEW_PATH, "content": self._complete_review_text()},
                "call-1",
            ),
            self._stream_text("审查", "完成", "，报告已生成"),
        ]

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = responses
            events = list(agent.run(project["id"], draft_word_count=100))

        deltas = [event for event in events if event["type"] == "content_delta"]
        self.assertGreaterEqual(len(deltas), 3)
        self.assertEqual("".join(d["text"] for d in deltas), "审查完成，报告已生成")
        # 旧的一次性 content 事件不应再出现
        self.assertNotIn("content", [event["type"] for event in events])
        self.assertEqual(events[-1]["type"], "review-completed")

    def test_run_accumulates_tool_call_across_chunks(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        review = self._complete_review_text()
        # tool_call split across many chunks, including an empty-arguments first chunk
        # and out-of-order index padding (index 0 only, but name/args dribbled).
        write_chunks = iter([
            self._delta_chunk(tool_calls=[self._tc_chunk(0, id="call-1", name="write", arguments="")]),
            self._delta_chunk(tool_calls=[self._tc_chunk(0, id=None, name="_file", arguments="")]),
            self._delta_chunk(tool_calls=[self._tc_chunk(0, id=None, name=None, arguments='{"file_path":')]),
            self._delta_chunk(
                tool_calls=[self._tc_chunk(0, id=None, name=None, arguments=f'"{CANONICAL_REVIEW_PATH}",')]
            ),
            self._delta_chunk(
                tool_calls=[self._tc_chunk(0, id=None, name=None, arguments='"content":' + json.dumps(review, ensure_ascii=False) + "}")]
            ),
        ])
        responses = [write_chunks, self._stream_text("审查完成，报告已生成")]

        captured_args = {}

        original_execute = agent._execute_tool

        def spy_execute(project_id, tool_name, args):
            captured_args["tool_name"] = tool_name
            captured_args["args"] = args
            return original_execute(project_id, tool_name, args)

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = responses
            with mock.patch.object(agent, "_execute_tool", side_effect=spy_execute):
                events = list(agent.run(project["id"], draft_word_count=100))

        # name fragments "write" + "_file" must be joined; arguments joined into valid JSON
        self.assertEqual(captured_args["tool_name"], "write_file")
        self.assertEqual(captured_args["args"]["file_path"], CANONICAL_REVIEW_PATH)
        self.assertEqual(captured_args["args"]["content"], review)
        self.assertEqual(events[-1]["type"], "review-completed")

    def test_run_handles_out_of_order_tool_call_index(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        # First chunk references index 1 (forces while-loop padding of index 0),
        # then index 0 gets filled. Only one real tool call (index 0) should execute
        # meaningfully; index 1 placeholder stays empty -> malformed -> bulkhead.
        # To keep the test focused on padding (not bulkhead), drive a single index 0.
        review = self._complete_review_text()
        write_chunks = iter([
            # jump straight to index 0 but via a chunk that first lands at higher index
            self._delta_chunk(tool_calls=[self._tc_chunk(0, id="call-1", name="write_file", arguments="")]),
            self._delta_chunk(
                tool_calls=[
                    self._tc_chunk(
                        0,
                        id=None,
                        name=None,
                        arguments=json.dumps({"file_path": CANONICAL_REVIEW_PATH, "content": review}, ensure_ascii=False),
                    )
                ]
            ),
        ])
        responses = [write_chunks, self._stream_text("审查完成，报告已生成")]

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = responses
            events = list(agent.run(project["id"], draft_word_count=100))

        self.assertEqual(events[-1]["type"], "review-completed")

    def test_run_strips_think_from_content_delta(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        review = self._complete_review_text()
        # First write the report, then stream a narration where a <think> block is
        # split across chunks inside delta.content -> must never reach the frontend.
        narration_chunks = iter([
            self._delta_chunk(content="审查"),
            self._delta_chunk(content="完成<thi"),
            self._delta_chunk(content="nk>内部推理不"),
            self._delta_chunk(content="可见</think>，报告已生成"),
        ])
        responses = [
            self._stream_single_tool_call(
                "write_file",
                {"file_path": CANONICAL_REVIEW_PATH, "content": review},
                "call-1",
            ),
            narration_chunks,
        ]

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = responses
            events = list(agent.run(project["id"], draft_word_count=100))

        delta_text = "".join(e["text"] for e in events if e["type"] == "content_delta")
        self.assertEqual(delta_text, "审查完成，报告已生成")
        self.assertNotIn("内部推理", delta_text)
        self.assertNotIn("<think>", delta_text)
        self.assertNotIn("</think>", delta_text)
        # thinking is never yielded as any event type
        self.assertNotIn("thinking", [e["type"] for e in events])
        self.assertEqual(events[-1]["type"], "review-completed")

    def test_run_collects_reasoning_for_followup_not_yielded(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        review = self._complete_review_text()
        captured_messages = {}
        original_serialize = agent._serialize_assistant_tool_call_message

        def spy_serialize(message):
            result = original_serialize(message)
            if result.get("tool_calls"):
                captured_messages["tool_call_msg"] = result
            return result

        # reasoning_content arrives via delta.reasoning_content before the tool call;
        # it must be collected into the follow-up message but never yielded.
        write_chunks = iter([
            self._delta_chunk(reasoning_content="我先读正文，再核对结论。"),
            self._delta_chunk(tool_calls=[self._tc_chunk(0, id="call-1", name="write_file", arguments="")]),
            self._delta_chunk(
                tool_calls=[
                    self._tc_chunk(
                        0,
                        id=None,
                        name=None,
                        arguments=json.dumps({"file_path": CANONICAL_REVIEW_PATH, "content": review}, ensure_ascii=False),
                    )
                ]
            ),
        ])
        responses = [write_chunks, self._stream_text("审查完成，报告已生成")]

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = responses
            with mock.patch.object(agent, "_serialize_assistant_tool_call_message", side_effect=spy_serialize):
                events = list(agent.run(project["id"], draft_word_count=100))

        # follow-up message carries non-empty reasoning_content (deepseek contract)
        self.assertIn("tool_call_msg", captured_messages)
        self.assertEqual(captured_messages["tool_call_msg"]["reasoning_content"], "我先读正文，再核对结论。")
        # reasoning is never yielded (no reasoning / thinking events, not in content_delta)
        self.assertNotIn("reasoning", [e["type"] for e in events])
        self.assertNotIn("thinking", [e["type"] for e in events])
        delta_text = "".join(e["text"] for e in events if e["type"] == "content_delta")
        self.assertNotIn("我先读正文", delta_text)
        self.assertEqual(events[-1]["type"], "review-completed")

    def test_run_malformed_tool_call_recovery(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        review = self._complete_review_text()
        captured = {"messages_at_recovery": None}

        responses = [
            # round 1: unknown tool name -> malformed -> compliance bulkhead, round voided
            iter([
                self._delta_chunk(tool_calls=[self._tc_chunk(0, id="call-bad", name="not_a_tool", arguments="{}")]),
            ]),
            # round 2: valid write
            self._stream_single_tool_call(
                "write_file",
                {"file_path": CANONICAL_REVIEW_PATH, "content": review},
                "call-2",
            ),
            # round 3: final narration
            self._stream_text("审查完成，报告已生成"),
        ]

        call_seq = []

        def create_side_effect(**kwargs):
            call_seq.append([dict(m) for m in kwargs["messages"]])
            return responses[len(call_seq) - 1]

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = create_side_effect
            events = list(agent.run(project["id"], draft_word_count=100))

        # the messages sent on round 2 must contain the compliance bulkhead pair:
        # a plain-text assistant placeholder followed by a user corrective (never a
        # tool-call assistant message, and never two consecutive user messages).
        round2_messages = call_seq[1]
        roles = [m["role"] for m in round2_messages]
        # last two appended messages are assistant placeholder + user corrective
        self.assertEqual(roles[-2], "assistant")
        self.assertEqual(roles[-1], "user")
        self.assertNotIn("tool_calls", round2_messages[-2])
        self.assertIn("格式异常", round2_messages[-2]["content"])
        self.assertIn("格式异常", round2_messages[-1]["content"])
        self.assertIn("未知工具名", round2_messages[-1]["content"])
        # no two consecutive user roles anywhere (would trigger upstream 400)
        for prev, cur in zip(roles, roles[1:]):
            self.assertFalse(prev == "user" and cur == "user")
        # the malformed round emits an error-status tool_result but no real tool exec
        self.assertEqual(events[-1]["type"], "review-completed")

    def test_run_word_count_over_100k_emits_friendly_error(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine
        draft_path = project_dir / "content" / "report_draft_v1.md"
        draft_path.write_text(
            "# Draft\n\n" + ("hello " * (MAX_DRAFT_WORDS_FOR_REVIEW + 1)),
            encoding="utf-8",
        )

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            events = list(agent.run(project["id"]))

        self.assertEqual(events[0]["type"], "error")
        self.assertIn("正文超过 100k 字", events[0]["detail"])
        mock_openai.assert_not_called()

    def test_run_returns_early_when_cancel_event_set_before_first_call(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        cancel_event = threading.Event()
        cancel_event.set()

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            events = list(agent.run(project["id"], draft_word_count=100, cancel_event=cancel_event))

        self.assertEqual(events, [{"type": "cancelled", "data": "客户端断开，已取消审查"}])
        mock_openai.assert_not_called()

    def test_run_returns_after_current_llm_call_when_cancel_set_mid_run(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        cancel_event = threading.Event()
        first_response = self._stream_single_tool_call("read_file", {"file_path": "plan/data-log.md"}, "call-1")

        def complete_first_call_then_cancel(**kwargs):
            del kwargs
            cancel_event.set()
            return first_response

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = complete_first_call_then_cancel
            events = list(agent.run(project["id"], draft_word_count=100, cancel_event=cancel_event))

        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 1)
        self.assertEqual(events[-1], {"type": "cancelled", "data": "客户端断开，已取消审查"})
        self.assertNotIn("tool_result", [event["type"] for event in events])

    def test_run_rejects_write_to_non_canonical_path(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        responses = [
            self._stream_single_tool_call(
                "write_file", {"file_path": "plan/data-log.md", "content": "bad"}, "call-1"
            ),
            self._stream_text("已停止"),
        ]

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = responses
            events = list(agent.run(project["id"], draft_word_count=100))

        tool_results = [event for event in events if event["type"] == "tool_result" and event["tool"] == "write_file"]
        self.assertEqual(tool_results[0]["status"], "error")
        self.assertEqual(tool_results[0]["summary"], "路径不允许")

    def test_run_requires_completion_marker(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        responses = [
            self._stream_single_tool_call(
                "write_file",
                {"file_path": CANONICAL_REVIEW_PATH, "content": "# 独立审查报告\n\n缺标记\n"},
                "call-1",
            ),
            self._stream_text("审查完成，报告已生成"),
        ]

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = responses
            events = list(agent.run(project["id"], draft_word_count=100))

        self.assertIn("审查报告缺少完成标记", events[-1]["detail"])
        self.assertNotIn("review-completed", [event["type"] for event in events])

    def test_run_rejects_marker_without_all_anchors(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        incomplete_review = (
            "# 独立审查报告\n\n"
            + "\n未发现问题\n".join(INDEPENDENT_REVIEW_ANCHORS[:-1])
            + "\n\n"
            f"{INDEPENDENT_REVIEW_COMPLETION_MARKER}\n"
        )
        responses = [
            self._stream_single_tool_call(
                "write_file",
                {"file_path": CANONICAL_REVIEW_PATH, "content": incomplete_review},
                "call-1",
            ),
            self._stream_text("审查完成，报告已生成"),
        ]

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = responses
            events = list(agent.run(project["id"], draft_word_count=100))

        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("审查报告未完整生成", events[-1]["detail"])
        self.assertNotIn("review-completed", [event["type"] for event in events])

    def test_run_max_iterations_15(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir

        def make_read_stream(**kwargs):
            del kwargs
            return self._stream_single_tool_call("read_file", {"file_path": "plan/data-log.md"}, "call-loop")

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = make_read_stream
            events = list(agent.run(project["id"], draft_word_count=100))

        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 15)
        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("超过 15 轮", events[-1]["detail"])

    # ---- Task 2.3: deepseek compat helper parity (object + streaming dict) ----

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

        # --- object-form (non-stream message) parity ---
        messages = [
            _FakeMessage(
                content=None,
                tool_calls=[
                    SimpleNamespace(
                        id="call-1",
                        type="function",
                        function=SimpleNamespace(
                            name="read_file",
                            arguments=json.dumps({"file_path": "plan/data-log.md"}, ensure_ascii=False),
                        ),
                    )
                ],
                dumped={"reasoning_content": "hidden reasoning", "audio": None},
            ),
            _FakeMessage(
                content="工具调用",
                tool_calls=[
                    SimpleNamespace(
                        id="call-2",
                        type="function",
                        function=SimpleNamespace(
                            name="write_file",
                            arguments=json.dumps({"file_path": CANONICAL_REVIEW_PATH, "content": "x"}, ensure_ascii=False),
                        ),
                    )
                ],
                reasoning_content="direct reasoning",
                dumped={"reasoning_content": None, "audio": None},
            ),
        ]

        for message in messages:
            with self.subTest(content=message.content):
                self.assertEqual(
                    chat_handler._extract_reasoning_content_from_message(message),
                    agent._extract_reasoning_content_from_message(message),
                )
                self.assertEqual(
                    chat_handler._assistant_tool_call_message_from_response(message),
                    agent._serialize_assistant_tool_call_message(message),
                )

        # --- streaming-collected dict parity ---
        # The dict the agent accumulates while streaming (with reasoning_content +
        # paired tool_calls) must serialize identically to chat's collected-message
        # normalizer (no null fields; reasoning preserved when non-empty).
        collected_dicts = [
            {
                "role": "assistant",
                "content": "审查完成，报告已生成",
                "reasoning_content": "我先读正文，再核对结论。",
                "tool_calls": [
                    {
                        "id": "call-3",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps({"file_path": CANONICAL_REVIEW_PATH, "content": "x"}, ensure_ascii=False),
                        },
                    }
                ],
            },
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-4",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"file_path": "plan/outline.md"}, ensure_ascii=False),
                        },
                    }
                ],
            },
        ]
        for collected in collected_dicts:
            with self.subTest(collected_content=collected["content"]):
                self.assertEqual(
                    chat_handler._normalize_collected_assistant_tool_call_message(collected),
                    agent._serialize_assistant_tool_call_message(collected),
                )
                # contract: empty/absent reasoning never appears; non-empty preserved
                serialized = agent._serialize_assistant_tool_call_message(collected)
                if collected.get("reasoning_content"):
                    self.assertEqual(serialized["reasoning_content"], collected["reasoning_content"])
                else:
                    self.assertNotIn("reasoning_content", serialized)


if __name__ == "__main__":
    unittest.main()
