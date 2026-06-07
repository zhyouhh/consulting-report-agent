import json
import os
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
    ReviewSessionStore,
    extract_latest_review_candidate_from_messages,
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

    def _claim_store(self, project, run_id="run-1"):
        """store + run_id are required deps of run()'s success path (codex C3-review BLOCKER 1:
        no store=None direct-write bypass). Tests that drive run() to a successful commit must
        provide a claimed store. Returns (store, run_id)."""
        store = ReviewSessionStore()
        self.assertTrue(store.claim_first(project["id"], run_id, threading.Event()))
        return store, run_id

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
        store, run_id = self._claim_store(project)
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
            events = list(agent.run(project["id"], draft_word_count=100, store=store, run_id=run_id))

        event_types = [event["type"] for event in events]
        self.assertIn("progress", event_types)
        self.assertIn("tool_call", event_types)
        self.assertIn("tool_result", event_types)
        self.assertEqual(event_types[-1], "review-completed")
        self.assertEqual(events[-1]["path"], CANONICAL_REVIEW_PATH)

    def test_run_streams_content_delta(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        store, run_id = self._claim_store(project)
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
            events = list(agent.run(project["id"], draft_word_count=100, store=store, run_id=run_id))

        deltas = [event for event in events if event["type"] == "content_delta"]
        self.assertGreaterEqual(len(deltas), 3)
        self.assertEqual("".join(d["text"] for d in deltas), "审查完成，报告已生成")
        # 旧的一次性 content 事件不应再出现
        self.assertNotIn("content", [event["type"] for event in events])
        self.assertEqual(events[-1]["type"], "review-completed")

    def test_run_accumulates_tool_call_across_chunks(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        store, run_id = self._claim_store(project)
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
                events = list(agent.run(project["id"], draft_word_count=100, store=store, run_id=run_id))

        # name fragments "write" + "_file" must be joined; arguments joined into valid JSON
        self.assertEqual(captured_args["tool_name"], "write_file")
        self.assertEqual(captured_args["args"]["file_path"], CANONICAL_REVIEW_PATH)
        self.assertEqual(captured_args["args"]["content"], review)
        self.assertEqual(events[-1]["type"], "review-completed")

    def test_run_handles_out_of_order_tool_call_index(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        store, run_id = self._claim_store(project)
        # Genuinely out-of-order indexes: the first chunk lands at index 2 (forcing the
        # while-loop to pad placeholder slots 0 and 1), then 0 and 1 are filled later in
        # mixed order. All three are valid read_file calls. The accumulator must rebuild
        # three correctly-positioned tool_calls (id/name/arguments paired per index).
        read_chunks = iter([
            # arrives at index 2 first -> while-loop must create placeholders for 0,1,2
            self._delta_chunk(tool_calls=[self._tc_chunk(2, id="call-2", name="read_file", arguments="")]),
            # now index 0
            self._delta_chunk(tool_calls=[self._tc_chunk(0, id="call-0", name="read_file", arguments="")]),
            # interleave: index 1
            self._delta_chunk(tool_calls=[self._tc_chunk(1, id="call-1", name="read_file", arguments="")]),
            # arguments dribble in, per index, out of order
            self._delta_chunk(tool_calls=[self._tc_chunk(2, arguments='{"file_path":"plan/outline.md"}')]),
            self._delta_chunk(tool_calls=[self._tc_chunk(0, arguments='{"file_path":"plan/data-log.md"}')]),
            self._delta_chunk(tool_calls=[self._tc_chunk(1, arguments='{"file_path":"plan/analysis-notes.md"}')]),
        ])
        # round 2: write a valid report; round 3: final narration
        responses = [
            read_chunks,
            self._stream_single_tool_call(
                "write_file",
                {"file_path": CANONICAL_REVIEW_PATH, "content": self._complete_review_text()},
                "call-w",
            ),
            self._stream_text("审查完成，报告已生成"),
        ]

        executed = []
        original_execute = agent._execute_tool

        def spy_execute(project_id, tool_name, args):
            executed.append((tool_name, args))
            return original_execute(project_id, tool_name, args)

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = responses
            with mock.patch.object(agent, "_execute_tool", side_effect=spy_execute):
                events = list(agent.run(project["id"], draft_word_count=100, store=store, run_id=run_id))

        # round 1 executes the three reads in index order (0,1,2), each with the
        # argument fragment that was routed to that index — proves padding + per-index
        # accumulation, not first-come collapse.
        round1_reads = [e for e in executed if e[0] == "read_file"]
        self.assertEqual(
            round1_reads,
            [
                ("read_file", {"file_path": "plan/data-log.md"}),
                ("read_file", {"file_path": "plan/analysis-notes.md"}),
                ("read_file", {"file_path": "plan/outline.md"}),
            ],
        )
        self.assertEqual(events[-1]["type"], "review-completed")

    def test_run_strips_think_from_content_delta(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        store, run_id = self._claim_store(project)
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
            events = list(agent.run(project["id"], draft_word_count=100, store=store, run_id=run_id))

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
        store, run_id = self._claim_store(project)
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
                events = list(agent.run(project["id"], draft_word_count=100, store=store, run_id=run_id))

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
        # Three malformed shapes must all route through the same compliance bulkhead:
        #   (a) unknown tool name, (b) known name + bad-JSON arguments, (c) missing id.
        # Each: round voided (no tool exec), assistant placeholder + user corrective
        # appended (never a bare user / consecutive user), corrective names the reason.
        malformed_cases = [
            (
                "unknown_tool_name",
                [self._tc_chunk(0, id="call-bad", name="not_a_tool", arguments="{}")],
                "未知工具名",
            ),
            (
                "bad_json_arguments",
                [self._tc_chunk(0, id="call-bad", name="read_file", arguments="{not valid json")],
                "参数 JSON 异常",
            ),
            (
                "missing_id",
                [self._tc_chunk(0, id=None, name="read_file", arguments='{"file_path":"plan/outline.md"}')],
                "缺 id",
            ),
        ]

        for case_name, round1_tool_calls, expected_reason in malformed_cases:
            with self.subTest(case=case_name):
                engine, project, project_dir, agent = self._make_engine_project_and_agent()
                del engine, project_dir
                store, run_id = self._claim_store(project)
                responses = [
                    # round 1: malformed -> compliance bulkhead, round voided
                    iter([self._delta_chunk(tool_calls=round1_tool_calls)]),
                    # round 2: valid write
                    self._stream_single_tool_call(
                        "write_file",
                        {"file_path": CANONICAL_REVIEW_PATH, "content": self._complete_review_text()},
                        "call-2",
                    ),
                    # round 3: final narration
                    self._stream_text("审查完成，报告已生成"),
                ]

                call_seq = []

                def create_side_effect(**kwargs):
                    call_seq.append([dict(m) for m in kwargs["messages"]])
                    return responses[len(call_seq) - 1]

                executed = []
                original_execute = agent._execute_tool

                def spy_execute(project_id, tool_name, args):
                    executed.append((tool_name, args))
                    return original_execute(project_id, tool_name, args)

                with mock.patch("backend.independent_review.OpenAI") as mock_openai:
                    mock_openai.return_value.chat.completions.create.side_effect = create_side_effect
                    with mock.patch.object(agent, "_execute_tool", side_effect=spy_execute):
                        events = list(agent.run(project["id"], draft_word_count=100, store=store, run_id=run_id))

                # round 2's messages carry the bulkhead pair: assistant placeholder
                # (no tool_calls payload that would carry the malformed call) + user
                # corrective. Never two consecutive user messages.
                round2_messages = call_seq[1]
                roles = [m["role"] for m in round2_messages]
                self.assertEqual(roles[-2], "assistant")
                self.assertEqual(roles[-1], "user")
                self.assertNotIn("tool_calls", round2_messages[-2])
                self.assertIn("格式异常", round2_messages[-2]["content"])
                self.assertIn("格式异常", round2_messages[-1]["content"])
                self.assertIn(expected_reason, round2_messages[-1]["content"])
                for prev, cur in zip(roles, roles[1:]):
                    self.assertFalse(prev == "user" and cur == "user")
                # the malformed round must NOT execute the malformed tool; the only
                # _execute_tool call belongs to round 2's valid write_file.
                self.assertEqual(executed, [("write_file", {"file_path": CANONICAL_REVIEW_PATH, "content": self._complete_review_text()})])
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

    def _bad_review_responses_for_self_correct(self, bad_content, attempts=3):
        """Alternating write(bad)/narrate streams for `attempts` verify passes.

        Each verify attempt = one write_file round (produces the bad candidate) + one
        narration round (no tool call -> triggers verify). With self-correct ≤2, three
        attempts (1 initial + 2 corrections) exhaust the budget and surface the verify
        error rather than committing.
        """
        seq = []
        for i in range(attempts):
            seq.append(
                self._stream_single_tool_call(
                    "write_file",
                    {"file_path": CANONICAL_REVIEW_PATH, "content": bad_content},
                    f"call-{i}",
                )
            )
            seq.append(self._stream_text("审查完成，报告已生成"))
        return seq

    def test_run_requires_completion_marker(self):
        # Bad candidate (no marker) -> self-correct retries twice, then terminal error.
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        responses = self._bad_review_responses_for_self_correct("# 独立审查报告\n\n缺标记\n")

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = responses
            events = list(agent.run(project["id"], draft_word_count=100))

        self.assertIn("审查报告缺少完成标记", events[-1]["detail"])
        self.assertNotIn("review-completed", [event["type"] for event in events])
        # 1 initial verify attempt + 2 self-corrects = 3 attempts, each = write + narrate.
        self.assertEqual(
            mock_openai.return_value.chat.completions.create.call_count,
            2 * (agent.MAX_VERIFY_SELF_CORRECTS + 1),
        )

    def test_run_rejects_marker_without_all_anchors(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        incomplete_review = (
            "# 独立审查报告\n\n"
            + "\n未发现问题\n".join(INDEPENDENT_REVIEW_ANCHORS[:-1])
            + "\n\n"
            f"{INDEPENDENT_REVIEW_COMPLETION_MARKER}\n"
        )
        responses = self._bad_review_responses_for_self_correct(incomplete_review)

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
        # run() calls _serialize_assistant_tool_call_message on the accumulated dict
        # in BOTH branches: the tool-call branch (non-empty tool_calls) AND the
        # no-tool-call terminal branch (tool_calls == [], the final narration round).
        # Both shapes must serialize byte-identically to chat's collected-message
        # normalizer (which always emits tool_calls, never null fields, reasoning
        # preserved when non-empty) — including the empty-tool_calls case.
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
            # no-tool-call terminal round: empty tool_calls, with reasoning collected.
            {
                "role": "assistant",
                "content": "审查完成，报告已生成",
                "reasoning_content": "整体逻辑通顺。",
                "tool_calls": [],
            },
            # no-tool-call terminal round: empty tool_calls, no reasoning at all.
            {
                "role": "assistant",
                "content": "审查完成，报告已生成",
                "tool_calls": [],
            },
        ]
        for collected in collected_dicts:
            with self.subTest(collected_tool_calls=len(collected["tool_calls"])):
                self.assertEqual(
                    chat_handler._normalize_collected_assistant_tool_call_message(collected),
                    agent._serialize_assistant_tool_call_message(collected),
                )
                serialized = agent._serialize_assistant_tool_call_message(collected)
                # tool_calls key always present (matches chat), even when empty.
                self.assertIn("tool_calls", serialized)
                self.assertEqual(len(serialized["tool_calls"]), len(collected["tool_calls"]))
                # contract: empty/absent reasoning never appears; non-empty preserved.
                if collected.get("reasoning_content"):
                    self.assertEqual(serialized["reasoning_content"], collected["reasoning_content"])
                else:
                    self.assertNotIn("reasoning_content", serialized)

    # ---- Task 3.2: candidate staging + resume + self-correct + atomic commit ----

    def _serialized_write_assistant(self, content, call_id="call-w"):
        """A provider-valid assistant message carrying one write_file tool_call (canonical)."""
        return {
            "role": "assistant",
            "content": "已生成审查报告。",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps(
                            {"file_path": CANONICAL_REVIEW_PATH, "content": content},
                            ensure_ascii=False,
                        ),
                    },
                }
            ],
        }

    def _tool_result_msg(self, call_id, status="success", summary="审查报告已写入"):
        return {
            "role": "tool",
            "tool_call_id": call_id,
            "content": json.dumps({"status": status, "summary": summary}, ensure_ascii=False),
        }

    def test_extract_latest_review_candidate_from_messages(self):
        review = self._complete_review_text()
        messages = [
            {"role": "system", "content": "sys"},
            # an earlier successful write (older content) -> should be superseded.
            self._serialized_write_assistant("# 旧版报告\n（不完整）\n", "call-old"),
            self._tool_result_msg("call-old", status="success"),
            # a failed write (status error) -> must be ignored even though it's later.
            self._serialized_write_assistant("# 失败版\n", "call-fail"),
            self._tool_result_msg("call-fail", status="error"),
            # the latest successful write -> this is the candidate.
            self._serialized_write_assistant(review, "call-latest"),
            self._tool_result_msg("call-latest", status="success"),
        ]
        candidate = extract_latest_review_candidate_from_messages(messages)
        self.assertEqual(candidate, review)

        # no successful canonical write -> None.
        messages_none = [
            {"role": "system", "content": "sys"},
            self._serialized_write_assistant("# x\n", "call-1"),
            self._tool_result_msg("call-1", status="error"),
        ]
        self.assertIsNone(extract_latest_review_candidate_from_messages(messages_none))

        # snapshot from messages must NOT carry a candidate_text field (codex R1 BLOCKER 4):
        # the candidate lives only in the write_file tool_call arguments.
        snapshot = IndependentReviewAgent._build_provider_valid_snapshot(messages, 4, True)
        self.assertNotIn("candidate_text", snapshot)
        self.assertIn("messages", snapshot)

    def test_run_success_calls_atomic_commit_and_emits_mtime(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        store = ReviewSessionStore()
        self.assertTrue(store.claim_first(project["id"], "run-1", threading.Event()))
        responses = [
            self._stream_single_tool_call(
                "write_file",
                {"file_path": CANONICAL_REVIEW_PATH, "content": self._complete_review_text()},
                "call-1",
            ),
            self._stream_text("审查完成，报告已生成"),
        ]

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = responses
            events = list(agent.run(project["id"], draft_word_count=100, run_id="run-1", store=store))

        completed = [e for e in events if e["type"] == "review-completed"]
        self.assertEqual(len(completed), 1)
        mtime = completed[0]["report_mtime_ns"]
        self.assertIsInstance(mtime, str)
        # store now holds a done tombstone with the same opaque-string mtime.
        self.assertEqual(store.get_done_mtime(project["id"], "run-1"), mtime)
        # canonical was actually replaced (candidate staging -> atomic commit).
        canonical = Path(project["project_dir"]) / CANONICAL_REVIEW_PATH
        self.assertIn(INDEPENDENT_REVIEW_COMPLETION_MARKER, canonical.read_text(encoding="utf-8"))

    def test_run_candidate_staging_not_committed_until_verified(self):
        # First write is incomplete (no marker) -> self-correct; canonical must NOT be written
        # during the failed attempt. Second write is complete -> committed.
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        store = ReviewSessionStore()
        store.claim_first(project["id"], "run-1", threading.Event())
        canonical = Path(project["project_dir"]) / CANONICAL_REVIEW_PATH

        bad = "# 独立审查报告\n\n缺标记\n"
        good = self._complete_review_text()
        canonical_during_first_attempt = {}

        def create_side_effect(**kwargs):
            del kwargs
            idx = len(call_seq)
            call_seq.append(idx)
            if idx == 0:
                return self._stream_single_tool_call(
                    "write_file", {"file_path": CANONICAL_REVIEW_PATH, "content": bad}, "call-bad"
                )
            if idx == 1:
                # narration round triggers verify-fail; capture canonical content to prove
                # the incomplete candidate did NOT leak to the canonical file.
                canonical_during_first_attempt["text"] = (
                    canonical.read_text(encoding="utf-8") if canonical.exists() else ""
                )
                return self._stream_text("审查完成，报告已生成")
            if idx == 2:
                return self._stream_single_tool_call(
                    "write_file", {"file_path": CANONICAL_REVIEW_PATH, "content": good}, "call-good"
                )
            return self._stream_text("审查完成，报告已生成")

        call_seq = []
        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = create_side_effect
            events = list(agent.run(project["id"], draft_word_count=100, run_id="run-1", store=store))

        # the incomplete candidate was NOT committed to canonical during the failed attempt
        # (canonical still the pending stub, not the bad content, not yet the complete marker).
        first_text = canonical_during_first_attempt.get("text", "")
        self.assertNotIn("缺标记", first_text)
        self.assertNotIn(INDEPENDENT_REVIEW_COMPLETION_MARKER, first_text)
        # after the self-correct produces a complete report, it is committed.
        self.assertEqual(events[-1]["type"], "review-completed")
        self.assertIn(INDEPENDENT_REVIEW_COMPLETION_MARKER, canonical.read_text(encoding="utf-8"))

    def test_run_self_corrects_on_verify_fail_up_to_twice_then_errored(self):
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        store = ReviewSessionStore()
        store.claim_first(project["id"], "run-1", threading.Event())
        responses = self._bad_review_responses_for_self_correct("# 独立审查报告\n\n缺标记\n")

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = responses
            events = list(agent.run(project["id"], draft_word_count=100, run_id="run-1", store=store))

        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("审查报告缺少完成标记", events[-1]["detail"])
        # errored snapshot persisted, and it carries the corrective history (so resume
        # continues from "what is missing", not from scratch).
        status, snapshot = store.claim_resume(project["id"], "run-1", threading.Event())
        self.assertEqual(status, "errored")
        corrective_texts = [
            m["content"] for m in snapshot["messages"]
            if m.get("role") == "user" and "不合格" in (m.get("content") or "")
        ]
        self.assertEqual(len(corrective_texts), agent.MAX_VERIFY_SELF_CORRECTS)

    def test_run_resume_continues_from_snapshot_not_restart(self):
        # resume_snapshot already has the reads done; resume must NOT re-read from scratch.
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        store = ReviewSessionStore()
        store.claim_first(project["id"], "run-1", threading.Event())
        review = self._complete_review_text()
        resume_messages = [
            {"role": "system", "content": INDEPENDENT_REVIEW_SYSTEM_PROMPT},
            {"role": "assistant", "content": "已读完资料。", "tool_calls": [
                {"id": "call-r", "type": "function",
                 "function": {"name": "read_file",
                              "arguments": json.dumps({"file_path": "plan/data-log.md"}, ensure_ascii=False)}}
            ]},
            self._tool_result_msg("call-r", status="success", summary="读取 10 字"),
        ]
        resume_snapshot = {"messages": resume_messages, "iteration": 4, "review_written": False}
        # On resume the agent should directly write the report, then narrate -> 2 calls only.
        responses = [
            self._stream_single_tool_call(
                "write_file", {"file_path": CANONICAL_REVIEW_PATH, "content": review}, "call-w"
            ),
            self._stream_text("审查完成，报告已生成"),
        ]

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = responses
            events = list(agent.run(project["id"], run_id="run-1", store=store, resume_snapshot=resume_snapshot))

        # exactly 2 LLM calls (write + narrate) -> did not restart the read phase.
        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 2)
        # first request's messages start from the resumed history (the prior read tool result
        # is present), proving continuation rather than a fresh [system] start.
        first_messages = mock_openai.return_value.chat.completions.create.call_args_list[0].kwargs["messages"]
        self.assertTrue(any(m.get("role") == "tool" and m.get("tool_call_id") == "call-r" for m in first_messages))
        self.assertEqual(events[-1]["type"], "review-completed")

    def test_resume_after_staged_write_rebuilds_candidate_from_messages(self):
        # Staged write succeeded, then interrupted before the final atomic replace.
        # Resume rebuilds the candidate from messages (NOT from old canonical, NOT restart)
        # and proceeds straight to verify + atomic replace with a single narration round.
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        store = ReviewSessionStore()
        store.claim_first(project["id"], "run-1", threading.Event())
        review = self._complete_review_text()
        canonical = Path(project["project_dir"]) / CANONICAL_REVIEW_PATH
        # canonical starts as the pending stub (no complete marker) — proving resume must
        # rebuild the candidate from messages, not from this stale on-disk file.
        self.assertNotIn(INDEPENDENT_REVIEW_COMPLETION_MARKER, canonical.read_text(encoding="utf-8"))

        resume_messages = [
            {"role": "system", "content": INDEPENDENT_REVIEW_SYSTEM_PROMPT},
            self._serialized_write_assistant(review, "call-staged"),
            self._tool_result_msg("call-staged", status="success"),
        ]
        resume_snapshot = {"messages": resume_messages, "iteration": 5, "review_written": True}
        # Only a narration round is needed; the candidate already exists in messages.
        responses = [self._stream_text("审查完成，报告已生成")]

        executed = []
        original_execute = agent._execute_tool

        def spy_execute(project_id, tool_name, args):
            executed.append((tool_name, args))
            return original_execute(project_id, tool_name, args)

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = responses
            with mock.patch.object(agent, "_execute_tool", side_effect=spy_execute):
                events = list(agent.run(project["id"], run_id="run-1", store=store, resume_snapshot=resume_snapshot))

        # single LLM call (narration), no re-read / re-write executed on resume.
        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 1)
        self.assertEqual(executed, [])
        # candidate rebuilt from messages -> verify passes -> atomic replace happens.
        self.assertEqual(events[-1]["type"], "review-completed")
        self.assertIn(INDEPENDENT_REVIEW_COMPLETION_MARKER, canonical.read_text(encoding="utf-8"))

    def test_run_cancel_sets_errored_when_record_present_noop_when_discarded(self):
        # (a) disconnect mid-run with a live record -> snapshot persisted as errored (resumable).
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        store = ReviewSessionStore()
        cancel = threading.Event()
        store.claim_first(project["id"], "run-1", cancel)
        first_response = self._stream_single_tool_call("read_file", {"file_path": "plan/data-log.md"}, "call-1")

        def complete_then_cancel(**kwargs):
            del kwargs
            cancel.set()
            return first_response

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = complete_then_cancel
            events = list(agent.run(project["id"], draft_word_count=100, cancel_event=cancel, run_id="run-1", store=store))

        self.assertEqual(events[-1]["type"], "cancelled")
        status, snapshot = store.claim_resume(project["id"], "run-1", threading.Event())
        self.assertEqual(status, "errored")
        self.assertIsInstance(snapshot.get("messages"), list)

        # (b) discard cleared the record before cancel -> set_errored is a no-op (CAS).
        engine2, project2, project_dir2, agent2 = self._make_engine_project_and_agent()
        del engine2, project_dir2
        store2 = ReviewSessionStore()
        cancel2 = threading.Event()
        store2.claim_first(project2["id"], "run-2", cancel2)

        def discard_then_cancel(**kwargs):
            del kwargs
            store2.discard(project2["id"], "run-2")  # sets cancel2 + clears record
            return self._stream_single_tool_call("read_file", {"file_path": "plan/data-log.md"}, "call-2")

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = discard_then_cancel
            events2 = list(agent2.run(project2["id"], draft_word_count=100, cancel_event=cancel2, run_id="run-2", store=store2))

        self.assertEqual(events2[-1]["type"], "cancelled")
        # discarded record stays gone (set_errored no-op) -> a fresh first-claim succeeds.
        self.assertTrue(store2.claim_first(project2["id"], "run-3", threading.Event()))

    def test_run_supplement_merges_or_appends_user_avoiding_consecutive_user(self):
        review = self._complete_review_text()

        # Case A: resume tail is a tool result -> supplement appended as a standalone user
        # (no consecutive user), at a provider-valid boundary.
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        store = ReviewSessionStore()
        store.claim_first(project["id"], "run-1", threading.Event())
        resume_messages_tool_tail = [
            {"role": "system", "content": INDEPENDENT_REVIEW_SYSTEM_PROMPT},
            {"role": "assistant", "content": "读完。", "tool_calls": [
                {"id": "call-r", "type": "function",
                 "function": {"name": "read_file",
                              "arguments": json.dumps({"file_path": "plan/data-log.md"}, ensure_ascii=False)}}
            ]},
            self._tool_result_msg("call-r", status="success"),
        ]
        responses = [
            self._stream_single_tool_call("write_file", {"file_path": CANONICAL_REVIEW_PATH, "content": review}, "call-w"),
            self._stream_text("审查完成，报告已生成"),
        ]
        # snapshot messages at call time (run() mutates the live list in place).
        sent_per_call = []

        def capture_a(**kwargs):
            sent_per_call.append([dict(m) for m in kwargs["messages"]])
            return responses[len(sent_per_call) - 1]

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = capture_a
            list(agent.run(
                project["id"], run_id="run-1", store=store,
                resume_snapshot={"messages": resume_messages_tool_tail, "iteration": 3, "review_written": False},
                supplement="重点看第三章数据口径",
            ))
        sent = sent_per_call[0]
        roles = [m["role"] for m in sent]
        self.assertEqual(roles[-1], "user")
        self.assertIn("重点看第三章数据口径", sent[-1]["content"])
        for prev, cur in zip(roles, roles[1:]):
            self.assertFalse(prev == "user" and cur == "user")

        # Case B: resume tail is already a user/corrective -> supplement merged into it
        # (still no consecutive user).
        engine2, project2, project_dir2, agent2 = self._make_engine_project_and_agent()
        del engine2, project_dir2
        store2 = ReviewSessionStore()
        store2.claim_first(project2["id"], "run-2", threading.Event())
        resume_messages_user_tail = [
            {"role": "system", "content": INDEPENDENT_REVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": "上一版审查报告不合格：缺少完成标记。请补全。"},
        ]
        responses2 = [
            self._stream_single_tool_call("write_file", {"file_path": CANONICAL_REVIEW_PATH, "content": review}, "call-w2"),
            self._stream_text("审查完成，报告已生成"),
        ]
        sent_per_call2 = []

        def capture_b(**kwargs):
            sent_per_call2.append([dict(m) for m in kwargs["messages"]])
            return responses2[len(sent_per_call2) - 1]

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = capture_b
            list(agent2.run(
                project2["id"], run_id="run-2", store=store2,
                resume_snapshot={"messages": resume_messages_user_tail, "iteration": 2, "review_written": False},
                supplement="顺便检查参考文献",
            ))
        sent2 = sent_per_call2[0]
        roles2 = [m["role"] for m in sent2]
        # merged into the existing trailing user message -> still exactly one trailing user.
        self.assertEqual(roles2[-1], "user")
        self.assertEqual(roles2.count("user"), 1)
        self.assertIn("顺便检查参考文献", sent2[-1]["content"])
        self.assertIn("不合格", sent2[-1]["content"])  # original content preserved

    def test_run_snapshot_is_provider_valid_at_interrupt_boundaries(self):
        # Interrupt right after the assistant tool_call message is appended but before its
        # tool result -> snapshot must pad the missing tool result so the persisted messages
        # can be sent to the provider as-is (every tool_call paired with a tool result).
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        store = ReviewSessionStore()
        cancel = threading.Event()
        store.claim_first(project["id"], "run-1", cancel)

        # Stream a complete tool_call, then fire cancel as the very last stream step.
        # The chunk loop finishes naturally; the cancel is then caught at the top of the
        # tool-execution loop (assistant tool_call already appended, tool result not yet).
        def tool_stream_then_cancel():
            arg_text = json.dumps({"file_path": "plan/data-log.md"}, ensure_ascii=False)
            yield self._delta_chunk(tool_calls=[self._tc_chunk(0, id="call-x", name="read_file", arguments="")])
            yield self._delta_chunk(tool_calls=[self._tc_chunk(0, id=None, name=None, arguments=arg_text)])
            cancel.set()

        executed = []
        original_execute = agent._execute_tool

        def spy_execute(project_id, tool_name, args):
            executed.append((tool_name, args))
            return original_execute(project_id, tool_name, args)

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = [tool_stream_then_cancel()]
            with mock.patch.object(agent, "_execute_tool", side_effect=spy_execute):
                events = list(agent.run(project["id"], draft_word_count=100, cancel_event=cancel, run_id="run-1", store=store))

        self.assertEqual(events[-1]["type"], "cancelled")
        # the tool was never executed (cancel caught before exec) -> the live messages had a
        # dangling assistant tool_call with no tool result.
        self.assertEqual(executed, [])
        status, snapshot = store.claim_resume(project["id"], "run-1", threading.Event())
        self.assertEqual(status, "errored")
        msgs = snapshot["messages"]
        # padding guarantees: every assistant tool_call id has a matching tool result.
        tool_ids = {m["tool_call_id"] for m in msgs if m.get("role") == "tool"}
        dangling = [
            tc["id"]
            for m in msgs if m.get("role") == "assistant"
            for tc in (m.get("tool_calls") or [])
            if tc.get("id") and tc["id"] not in tool_ids
        ]
        self.assertEqual(dangling, [])
        self.assertIn("call-x", tool_ids)  # the interrupted call got a padded tool result

    # ---- C3 review fixes (BLOCKER 1/2/4/5) ----

    def test_run_success_path_requires_store_no_silent_write(self):
        # codex C3-review BLOCKER 1: store + run_id are required for the success commit; with
        # no store the agent must fail-fast (error) and must NOT silently direct-write canonical.
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        canonical = Path(project["project_dir"]) / CANONICAL_REVIEW_PATH
        before = canonical.read_text(encoding="utf-8")  # pending stub
        responses = [
            self._stream_single_tool_call(
                "write_file",
                {"file_path": CANONICAL_REVIEW_PATH, "content": self._complete_review_text()},
                "call-1",
            ),
            self._stream_text("审查完成，报告已生成"),
        ]

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = responses
            events = list(agent.run(project["id"], draft_word_count=100))  # no store / run_id

        self.assertEqual(events[-1]["type"], "error")
        self.assertNotIn("review-completed", [e["type"] for e in events])
        # canonical untouched: the complete report was NOT written via any direct-write bypass.
        self.assertEqual(canonical.read_text(encoding="utf-8"), before)
        self.assertNotIn(INDEPENDENT_REVIEW_COMPLETION_MARKER, canonical.read_text(encoding="utf-8"))

    def test_run_resume_does_not_reset_self_correct_budget(self):
        # codex C3-review BLOCKER 2: self-correct budget is counted from the conversation
        # history, so resume continues the budget instead of resetting it. Here the resume
        # snapshot already carries 1 corrective (1 of 2 used) -> only 1 more is allowed.
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        store, run_id = self._claim_store(project)
        bad = "# 独立审查报告\n\n缺标记\n"
        # resume history: one prior write(bad)+result and one prior corrective (1 used).
        resume_messages = [
            {"role": "system", "content": INDEPENDENT_REVIEW_SYSTEM_PROMPT},
            self._serialized_write_assistant(bad, "call-prev"),
            self._tool_result_msg("call-prev", status="success"),
            {"role": "user", "content": f"{agent.VERIFY_CORRECTIVE_PREFIX}审查报告缺少完成标记，请重试。请补全。"},
        ]
        resume_snapshot = {"messages": resume_messages, "iteration": 6, "review_written": True}
        # feed enough bad rounds; budget should stop it after exactly 1 more self-correct.
        responses = self._bad_review_responses_for_self_correct(bad, attempts=3)

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = responses
            events = list(agent.run(project["id"], run_id=run_id, store=store, resume_snapshot=resume_snapshot))

        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("审查报告缺少完成标记", events[-1]["detail"])
        # only 1 more self-correct cycle ran (write+narrate, then 1 correct -> write+narrate) = 4 calls.
        # if the budget had reset to 2, it would have been 6 calls.
        self.assertEqual(mock_openai.return_value.chat.completions.create.call_count, 4)
        # final errored snapshot carries exactly MAX correctives total (1 pre-existing + 1 added).
        status, snapshot = store.claim_resume(project["id"], run_id, threading.Event())
        self.assertEqual(status, "errored")
        correctives = [
            m for m in snapshot["messages"]
            if m.get("role") == "user" and (m.get("content") or "").startswith(agent.VERIFY_CORRECTIVE_PREFIX)
        ]
        self.assertEqual(len(correctives), agent.MAX_VERIFY_SELF_CORRECTS)

    def test_run_early_cancel_after_resume_preserves_snapshot(self):
        # codex C3-review BLOCKER 4: claim_resume clears the only snapshot into run()'s
        # resume_snapshot arg; if cancel fired before run() restored it, an early return
        # would lose the only resumable snapshot. The early-cancel path must write it back.
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        store = ReviewSessionStore()
        run_id = "run-resume"
        cancel = threading.Event()
        original_snapshot = {
            "messages": [
                {"role": "system", "content": INDEPENDENT_REVIEW_SYSTEM_PROMPT},
                {"role": "user", "content": "续审上下文"},
            ],
            "iteration": 7,
            "review_written": False,
        }
        store.claim_first(project["id"], run_id, threading.Event())
        store.set_errored(project["id"], run_id, original_snapshot)
        # endpoint resume dispatch: claim_resume flips running + hands snapshot to run().
        status, dispatched = store.claim_resume(project["id"], run_id, cancel)
        self.assertEqual(status, "errored")
        # disconnect happens before run() restores resume_snapshot.
        cancel.set()

        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            events = list(agent.run(
                project["id"], cancel_event=cancel, run_id=run_id, store=store, resume_snapshot=dispatched
            ))

        self.assertEqual(events[-1]["type"], "cancelled")
        mock_openai.assert_not_called()  # never reached the LLM
        # the unique snapshot was written back -> a subsequent resume still has it.
        status2, returned = store.claim_resume(project["id"], run_id, threading.Event())
        self.assertEqual(status2, "errored")
        self.assertEqual(returned, original_snapshot)

    def test_run_commit_time_cancel_disconnect_sets_errored_resumable(self):
        # codex C3-review BLOCKER 5 (agent integration): cancel fires after verify passes but
        # before atomic_commit. atomic_commit's cancel branch must CAS-flip to errored with a
        # snapshot (resumable), the temp must be cleaned up, and canonical must NOT be written.
        engine, project, project_dir, agent = self._make_engine_project_and_agent()
        del engine, project_dir
        store = ReviewSessionStore()
        run_id = "run-commit-cancel"
        cancel = threading.Event()
        store.claim_first(project["id"], run_id, cancel)
        canonical = Path(project["project_dir"]) / CANONICAL_REVIEW_PATH
        before = canonical.read_text(encoding="utf-8")  # pending stub
        responses = [
            self._stream_single_tool_call(
                "write_file",
                {"file_path": CANONICAL_REVIEW_PATH, "content": self._complete_review_text()},
                "call-1",
            ),
            self._stream_text("审查完成，报告已生成"),
        ]

        # verify passes but sets cancel as a side effect -> simulates disconnect landing in the
        # window between the no-tool-call cancel check and atomic_commit_report.
        original_verify = agent._verify_review_completeness

        def verify_then_cancel(candidate_text):
            result = original_verify(candidate_text)
            cancel.set()
            return result

        canonical_dir = canonical.parent
        with mock.patch("backend.independent_review.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = responses
            with mock.patch.object(agent, "_verify_review_completeness", side_effect=verify_then_cancel):
                events = list(agent.run(project["id"], draft_word_count=100, cancel_event=cancel, run_id=run_id, store=store))

        # committed nothing (canonical still the pending stub), surfaced an error not completion.
        self.assertNotIn("review-completed", [e["type"] for e in events])
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(canonical.read_text(encoding="utf-8"), before)
        # temp cleaned up (no leftover .independent-review-*.tmp).
        leftover = list(canonical_dir.glob(".independent-review-*.tmp"))
        self.assertEqual(leftover, [])
        # record is errored with a snapshot (resumable on reconnect), NOT cleared, NOT done.
        status, snapshot = store.claim_resume(project["id"], run_id, threading.Event())
        self.assertEqual(status, "errored")
        self.assertIsInstance(snapshot.get("messages"), list)


class ReviewSessionStoreTests(unittest.TestCase):
    """Task 3.1: in-process resume store — two-lock split / CAS no-revive / atomic replace /
    opaque-string mtime / discard-without-review-lock."""

    def setUp(self):
        self.store = ReviewSessionStore()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.canonical = os.path.join(self.tmpdir.name, "independent-review.md")

    def _stage_temp(self, content="report body"):
        fd, temp_path = tempfile.mkstemp(dir=self.tmpdir.name, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        return temp_path

    def test_store_claim_first_rejects_concurrent_running(self):
        self.assertTrue(self.store.claim_first("p", "run-1", threading.Event()))
        # already running -> second first-claim rejected (caller must release review lock).
        self.assertFalse(self.store.claim_first("p", "run-2", threading.Event()))

    def test_store_claim_first_overwrites_done_and_errored_tombstone(self):
        # done tombstone can be overwritten by a fresh first-run.
        temp = self._stage_temp()
        self.store.claim_first("p", "run-1", threading.Event())
        self.store.atomic_commit_report("p", "run-1", temp, self.canonical, {"messages": []})
        self.assertTrue(self.store.claim_first("p", "run-2", threading.Event()))
        # errored record can also be overwritten.
        self.store.set_errored("p", "run-2", {"messages": [1]})
        self.assertTrue(self.store.claim_first("p", "run-3", threading.Event()))

    def test_store_claim_resume_errored_returns_snapshot_and_flips_running(self):
        self.store.claim_first("p", "run-1", threading.Event())
        snap = {"messages": [{"role": "system"}], "iteration": 3}
        self.assertTrue(self.store.set_errored("p", "run-1", snap))
        status, returned = self.store.claim_resume("p", "run-1", threading.Event())
        self.assertEqual(status, "errored")
        self.assertEqual(returned, snap)
        # after claim_resume the record is running again with snapshot cleared; a second
        # resume now sees running -> reject (claimed by the first resumer).
        status2, _ = self.store.claim_resume("p", "run-1", threading.Event())
        self.assertEqual(status2, "reject")

    def test_store_claim_resume_done_returns_mtime(self):
        temp = self._stage_temp()
        self.store.claim_first("p", "run-1", threading.Event())
        mtime = self.store.atomic_commit_report("p", "run-1", temp, self.canonical, {"messages": []})
        status, returned = self.store.claim_resume("p", "run-1", threading.Event())
        self.assertEqual(status, "done")
        self.assertEqual(returned, mtime)
        self.assertIsInstance(returned, str)

    def test_store_claim_resume_reject_on_run_id_mismatch(self):
        self.store.claim_first("p", "run-1", threading.Event())
        self.store.set_errored("p", "run-1", {"messages": []})
        status, returned = self.store.claim_resume("p", "WRONG", threading.Event())
        self.assertEqual(status, "reject")
        self.assertIsNone(returned)
        # no record at all also rejects.
        status2, _ = self.store.claim_resume("absent", "run-x", threading.Event())
        self.assertEqual(status2, "reject")

    def test_store_set_errored_cas_rejects_stale_run(self):
        # superseded by a new run (discard + new first-claim) -> stale worker's set_errored no-op.
        self.store.claim_first("p", "run-1", threading.Event())
        self.store.discard("p", "run-1")
        self.store.claim_first("p", "run-2", threading.Event())
        self.assertFalse(self.store.set_errored("p", "run-1", {"messages": ["stale"]}))
        # the current run-2 record is untouched (still running, no stale snapshot).
        self.assertTrue(self.store.set_errored("p", "run-2", {"messages": ["fresh"]}))

    def test_store_set_errored_does_not_overwrite_done_tombstone(self):
        # codex R3 BLOCKER 1: late cancel/error must not flip a done tombstone to errored.
        temp = self._stage_temp()
        self.store.claim_first("p", "run-1", threading.Event())
        self.store.atomic_commit_report("p", "run-1", temp, self.canonical, {"messages": []})
        self.assertFalse(self.store.set_errored("p", "run-1", {"messages": ["late"]}))
        # tombstone preserved: resume still reports done.
        status, _ = self.store.claim_resume("p", "run-1", threading.Event())
        self.assertEqual(status, "done")

    def test_store_atomic_commit_writes_tombstone_and_returns_mtime_string(self):
        temp = self._stage_temp("final report")
        self.store.claim_first("p", "run-1", threading.Event())
        mtime = self.store.atomic_commit_report("p", "run-1", temp, self.canonical, {"messages": []})
        self.assertIsInstance(mtime, str)
        self.assertTrue(os.path.exists(self.canonical))
        self.assertFalse(os.path.exists(temp))  # temp consumed by os.replace
        self.assertEqual(Path(self.canonical).read_text(encoding="utf-8"), "final report")
        # tombstone exposed via get_done_mtime (run-bound).
        self.assertEqual(self.store.get_done_mtime("p", "run-1"), mtime)
        self.assertIsNone(self.store.get_done_mtime("p", "WRONG"))

    def test_store_atomic_commit_aborts_when_cancelled(self):
        # codex C3-review BLOCKER 5: cancel (disconnect) before commit -> NOT a dirty write,
        # and the record is CAS-flipped to errored with the snapshot (resumable), not left
        # running (which the worker finally would otherwise clear, losing the session).
        cancel = threading.Event()
        self.store.claim_first("p", "run-1", cancel)
        cancel.set()
        temp = self._stage_temp()
        snap = {"messages": [{"role": "system"}], "iteration": 4}
        result = self.store.atomic_commit_report("p", "run-1", temp, self.canonical, snap)
        self.assertIsNone(result)
        self.assertFalse(os.path.exists(self.canonical))  # canonical NOT replaced
        self.assertTrue(os.path.exists(temp))  # caller is responsible for deleting temp
        # record flipped to errored carrying the snapshot -> resume continues.
        status, returned = self.store.claim_resume("p", "run-1", threading.Event())
        self.assertEqual(status, "errored")
        self.assertEqual(returned, snap)

    def test_store_atomic_commit_aborts_on_run_id_mismatch(self):
        self.store.claim_first("p", "run-1", threading.Event())
        temp = self._stage_temp()
        result = self.store.atomic_commit_report("p", "STALE", temp, self.canonical, {"messages": []})
        self.assertIsNone(result)
        self.assertFalse(os.path.exists(self.canonical))

    def test_store_atomic_commit_os_replace_failure_keeps_errored_and_allows_resume(self):
        # codex R1 BLOCKER 3: os.replace failure -> CAS flip to errored with snapshot,
        # no running left, resume can continue.
        self.store.claim_first("p", "run-1", threading.Event())
        errored_snap = {"messages": [{"role": "system"}], "iteration": 2}
        # pass a non-existent temp path so os.replace raises.
        missing_temp = os.path.join(self.tmpdir.name, "does-not-exist.tmp")
        result = self.store.atomic_commit_report("p", "run-1", missing_temp, self.canonical, errored_snap)
        self.assertIsNone(result)
        self.assertFalse(os.path.exists(self.canonical))
        # record flipped to errored carrying the snapshot -> resume returns it.
        status, returned = self.store.claim_resume("p", "run-1", threading.Event())
        self.assertEqual(status, "errored")
        self.assertEqual(returned, errored_snap)

    def test_store_atomic_commit_cancel_returns_none_no_dirty_write(self):
        # cancelled before commit: no done tombstone, no canonical write. Per BLOCKER 5 the
        # record becomes errored+snapshot (resumable on reconnect), never done.
        cancel = threading.Event()
        self.store.claim_first("p", "run-1", cancel)
        cancel.set()
        temp = self._stage_temp()
        snap = {"messages": [{"role": "system"}]}
        self.assertIsNone(self.store.atomic_commit_report("p", "run-1", temp, self.canonical, snap))
        self.assertIsNone(self.store.get_done_mtime("p", "run-1"))
        self.assertFalse(os.path.exists(self.canonical))
        # errored, not done, not cleared.
        self.assertEqual(self.store.claim_resume("p", "run-1", threading.Event())[0], "errored")

    def test_store_atomic_commit_rejected_on_done_or_errored_no_overwrite(self):
        # codex C3-review BLOCKER 3: success path needs a status=="running" CAS too, else a
        # late atomic_commit with the same run_id could overwrite a done tombstone / errored
        # record. done and errored must both reject the commit (no os.replace, no state churn).
        # --- done tombstone is not overwritten ---
        temp1 = self._stage_temp("first")
        self.store.claim_first("p", "run-1", threading.Event())
        mtime = self.store.atomic_commit_report("p", "run-1", temp1, self.canonical, {"messages": []})
        self.assertIsInstance(mtime, str)
        first_text = Path(self.canonical).read_text(encoding="utf-8")
        # a second (stale) commit for the same run_id must be rejected and not touch canonical.
        temp2 = self._stage_temp("SECOND should not land")
        self.assertIsNone(self.store.atomic_commit_report("p", "run-1", temp2, self.canonical, {"messages": []}))
        self.assertEqual(Path(self.canonical).read_text(encoding="utf-8"), first_text)
        self.assertTrue(os.path.exists(temp2))  # not consumed by os.replace
        # tombstone + mtime preserved (status still done).
        self.assertEqual(self.store.get_done_mtime("p", "run-1"), mtime)

        # --- errored record is not flipped/overwritten by a commit ---
        self.store.claim_first("q", "run-q", threading.Event())
        errored_snap = {"messages": [{"role": "system"}], "iteration": 2}
        self.store.set_errored("q", "run-q", errored_snap)
        canonical_q = os.path.join(self.tmpdir.name, "review-q.md")
        temp_q = self._stage_temp("should not land")
        self.assertIsNone(self.store.atomic_commit_report("q", "run-q", temp_q, canonical_q, {"messages": ["other"]}))
        self.assertFalse(os.path.exists(canonical_q))  # no write while errored
        # snapshot untouched -> resume returns the original errored snapshot.
        status, returned = self.store.claim_resume("q", "run-q", threading.Event())
        self.assertEqual(status, "errored")
        self.assertEqual(returned, errored_snap)

    def test_store_discard_run_id_match_sets_cancel_and_clears(self):
        cancel = threading.Event()
        self.store.claim_first("p", "run-1", cancel)
        self.assertTrue(self.store.discard("p", "run-1"))
        self.assertTrue(cancel.is_set())
        # record cleared -> a fresh first-claim succeeds (no dead running).
        self.assertTrue(self.store.claim_first("p", "run-2", threading.Event()))

    def test_store_discard_no_op_on_mismatch(self):
        # old window's delayed discard must not kill the new run.
        cancel_new = threading.Event()
        self.store.claim_first("p", "run-new", cancel_new)
        self.assertFalse(self.store.discard("p", "run-old"))
        self.assertFalse(cancel_new.is_set())
        # new run still claimable for resume-after-error etc. (record intact, still running).
        self.assertFalse(self.store.claim_first("p", "run-other", threading.Event()))

    def test_store_finalize_orphan_running(self):
        # codex R2 BLOCKER 2+4. running + fallback_snapshot -> errored (resumable).
        self.store.claim_first("p", "run-1", threading.Event())
        snap = {"messages": [{"role": "system"}]}
        self.assertTrue(self.store.finalize_orphan_running("p", "run-1", snap))
        status, returned = self.store.claim_resume("p", "run-1", threading.Event())
        self.assertEqual(status, "errored")
        self.assertEqual(returned, snap)

        # running + no fallback_snapshot -> record cleared (first-run restartable).
        self.store.claim_first("q", "run-q", threading.Event())
        self.assertTrue(self.store.finalize_orphan_running("q", "run-q", None))
        self.assertTrue(self.store.claim_first("q", "run-q2", threading.Event()))

        # done -> no-op (don't resurrect a finished run).
        temp = self._stage_temp()
        self.store.claim_first("r", "run-r", threading.Event())
        self.store.atomic_commit_report("r", "run-r", temp, self.canonical, {"messages": []})
        self.assertFalse(self.store.finalize_orphan_running("r", "run-r", {"messages": ["x"]}))
        self.assertEqual(self.store.claim_resume("r", "run-r", threading.Event())[0], "done")

        # run_id mismatch / discarded -> no-op.
        self.store.claim_first("s", "run-s", threading.Event())
        self.assertFalse(self.store.finalize_orphan_running("s", "WRONG", {"messages": ["x"]}))
        self.store.discard("s", "run-s")
        self.assertFalse(self.store.finalize_orphan_running("s", "run-s", {"messages": ["x"]}))


if __name__ == "__main__":
    unittest.main()
