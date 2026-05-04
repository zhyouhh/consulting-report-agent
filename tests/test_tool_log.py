import json
import unittest

from backend.chat import ChatHandler


class PairToolCallsWithResultsTests(unittest.TestCase):
    def setUp(self):
        self.handler = ChatHandler.__new__(ChatHandler)

    def test_basic_pair_one_call_one_result(self):
        msgs = [
            {"role": "assistant", "tool_calls": [
                {"id": "call_1", "function": {"name": "web_search", "arguments": '{"q":"x"}'}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": '{"status":"success","results":[]}'},
        ]
        pairs = self.handler._pair_tool_calls_with_results(msgs)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].name, "web_search")
        self.assertEqual(pairs[0].result["status"], "success")

    def test_skip_text_only_assistant(self):
        msgs = [
            {"role": "assistant", "content": "thinking..."},
            {"role": "assistant", "tool_calls": [
                {"id": "call_1", "function": {"name": "x", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": '{}'},
        ]
        pairs = self.handler._pair_tool_calls_with_results(msgs)
        self.assertEqual(len(pairs), 1)

    def test_skip_retry_user_barrier(self):
        # Simulates the malformed-retry barrier from chat.py:3267-3281
        msgs = [
            {"role": "assistant", "content": "（上条工具调用被上游合并...）"},
            {"role": "user", "content": "刚才的 tool_calls 格式异常..."},
            {"role": "assistant", "tool_calls": [
                {"id": "call_1", "function": {"name": "x", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": '{}'},
        ]
        pairs = self.handler._pair_tool_calls_with_results(msgs)
        self.assertEqual(len(pairs), 1)

    def test_skip_tool_with_no_matching_id(self):
        msgs = [
            {"role": "tool", "tool_call_id": "orphan", "content": '{}'},
            {"role": "assistant", "tool_calls": [
                {"id": "call_1", "function": {"name": "x", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": '{}'},
        ]
        pairs = self.handler._pair_tool_calls_with_results(msgs)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].name, "x")

    def test_handle_malformed_json_tool_result(self):
        msgs = [
            {"role": "assistant", "tool_calls": [
                {"id": "call_1", "function": {"name": "x", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "not-json"},
        ]
        pairs = self.handler._pair_tool_calls_with_results(msgs)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].result["status"], "error")
        self.assertIn("raw", pairs[0].result)

    def test_empty_messages_returns_empty(self):
        pairs = self.handler._pair_tool_calls_with_results([])
        self.assertEqual(pairs, [])

    def test_multi_calls_in_one_assistant_paired_individually(self):
        msgs = [
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "function": {"name": "a", "arguments": "{}"}},
                {"id": "c2", "function": {"name": "b", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"r":1}'},
            {"role": "tool", "tool_call_id": "c2", "content": '{"r":2}'},
        ]
        pairs = self.handler._pair_tool_calls_with_results(msgs)
        self.assertEqual(len(pairs), 2)
        self.assertEqual(pairs[0].name, "a")
        self.assertEqual(pairs[1].name, "b")


class AppendToolLogTests(unittest.TestCase):
    def setUp(self):
        self.handler = ChatHandler.__new__(ChatHandler)

    def test_format_success_with_short_args(self):
        msgs = [
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "function": {"name": "web_search", "arguments": '{"query":"x"}'}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"status":"success","results":[1,2,3]}'},
        ]
        result = self.handler._append_tool_log_to_assistant("Hello world.", msgs)
        self.assertIn("<!-- tool-log", result)
        self.assertIn("web_search", result)
        self.assertIn("✓", result)
        self.assertIn("-->", result)
        self.assertTrue(result.startswith("Hello world."))

    def test_format_error_with_brief(self):
        msgs = [
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "function": {"name": "write_file", "arguments": '{"file_path":"plan/x.md"}'}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"status":"error","message":"some error"}'},
        ]
        result = self.handler._append_tool_log_to_assistant("Reply.", msgs)
        self.assertIn("✗", result)

    def test_append_report_draft_path_from_result(self):
        """v2: append_report_draft 真实 schema 只有 content；路径在 result["path"]"""
        msgs = [
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "function": {"name": "append_report_draft",
                 "arguments": '{"content":"new section text..."}'}},
            ]},
            {"role": "tool", "tool_call_id": "c1",
             "content": '{"status":"success","path":"content/report_draft_v1.md"}'},
        ]
        result = self.handler._append_tool_log_to_assistant("Reply.", msgs)
        self.assertIn("append_report_draft", result)
        self.assertIn("content/report_draft_v1.md", result)
        self.assertNotIn("new section text", result)

    def test_max_iterations_tool_log_full_chain(self):
        """spec §5.5 — 撞 max_iterations=20 时 tool-log 应附加全部 20 条"""
        msgs = []
        for i in range(20):
            msgs.append({"role": "assistant", "tool_calls": [
                {"id": f"c{i}", "function": {"name": "web_search",
                 "arguments": f'{{"query":"q{i}"}}'}},
            ]})
            msgs.append({"role": "tool", "tool_call_id": f"c{i}",
                         "content": '{"status":"success","results":[]}'})
        result = self.handler._append_tool_log_to_assistant(
            "抱歉，工具调用轮次过多，已停止本轮，请缩小检索范围或改成分步提问。", msgs,
        )
        log_lines = [l for l in result.split("\n") if l.startswith("- web_search(")]
        self.assertEqual(len(log_lines), 20)

    def test_no_pairs_no_log_appended(self):
        result = self.handler._append_tool_log_to_assistant("Reply.", [])
        self.assertEqual(result, "Reply.")

    def test_truncate_long_args(self):
        long_arg = "a" * 200
        msgs = [
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "function": {"name": "web_search", "arguments": json.dumps({"query": long_arg})}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"status":"success"}'},
        ]
        result = self.handler._append_tool_log_to_assistant("Reply.", msgs)
        for line in result.split("\n"):
            self.assertLessEqual(len(line), 120)


class InsertBeforeTailTagsTests(unittest.TestCase):
    def setUp(self):
        self.handler = ChatHandler.__new__(ChatHandler)

    def test_no_tail_tags_appends_at_end(self):
        result = self.handler._insert_before_tail_tags("body text", "BLOCK")
        self.assertTrue(result.endswith("BLOCK"))

    def test_inserts_before_stage_ack_tail(self):
        content = "body\n\n<stage-ack>outline_confirmed_at</stage-ack>"
        result = self.handler._insert_before_tail_tags(content, "INJ")
        inj_pos = result.find("INJ")
        ack_pos = result.find("<stage-ack")
        self.assertLess(inj_pos, ack_pos)

    def test_inserts_before_draft_action_tail(self):
        content = "body\n\n<draft-action>begin</draft-action>"
        result = self.handler._insert_before_tail_tags(content, "INJ")
        inj_pos = result.find("INJ")
        tag_pos = result.find("<draft-action")
        self.assertLess(inj_pos, tag_pos)

    def test_inserts_before_draft_action_replace_block(self):
        content = "body\n\n<draft-action-replace>\n  <old>x</old>\n  <new>y</new>\n</draft-action-replace>"
        result = self.handler._insert_before_tail_tags(content, "INJ")
        inj_pos = result.find("INJ")
        tag_pos = result.find("<draft-action-replace")
        self.assertLess(inj_pos, tag_pos)

    def test_inserts_before_mixed_stage_ack_and_draft_action(self):
        content = "body\n\n<draft-action>begin</draft-action>\n<stage-ack>outline_confirmed_at</stage-ack>"
        result = self.handler._insert_before_tail_tags(content, "INJ")
        inj_pos = result.find("INJ")
        for tag in ("<draft-action", "<stage-ack"):
            self.assertLess(inj_pos, result.find(tag))

    def test_trailing_whitespace_preserved(self):
        content = "body\n\n<stage-ack>outline_confirmed_at</stage-ack>\n\n"
        result = self.handler._insert_before_tail_tags(content, "INJ")
        self.assertIn("INJ", result)


class StripToolLogCommentsTests(unittest.TestCase):
    def test_strips_well_formed_single_line(self):
        from backend.chat import strip_tool_log_comments
        s = "Reply.\n<!-- tool-log\n- web_search ✓\n-->"
        result = strip_tool_log_comments(s)
        self.assertEqual(result, "Reply.")

    def test_strips_multi_line(self):
        from backend.chat import strip_tool_log_comments
        s = "Reply.\n<!-- tool-log\n- a ✓\n- b ✗ err\n-->"
        result = strip_tool_log_comments(s)
        self.assertEqual(result, "Reply.")

    def test_handles_unclosed_truncated_stream(self):
        from backend.chat import strip_tool_log_comments
        s = "Reply.\n<!-- tool-log\n- partial ✓"
        result = strip_tool_log_comments(s)
        self.assertEqual(result, "Reply.")

    def test_handles_nested_dash_dash(self):
        from backend.chat import strip_tool_log_comments
        s = "Reply.\n<!-- tool-log\n- some -- tool ✓\n-->"
        result = strip_tool_log_comments(s)
        self.assertEqual(result, "Reply.")

    def test_no_tool_log_comment_unchanged(self):
        from backend.chat import strip_tool_log_comments
        s = "Reply with no comment.\n<!-- regular html comment -->"
        result = strip_tool_log_comments(s)
        self.assertEqual(result, s)
