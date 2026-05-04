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
