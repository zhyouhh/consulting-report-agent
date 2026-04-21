import unittest

from backend.stage_ack import StageAckEvent, StageAckParser, VALID_KEYS


class StageAckParseRawTests(unittest.TestCase):
    def setUp(self):
        self.parser = StageAckParser()

    def test_single_set_tag(self):
        events = self.parser.parse_raw("<stage-ack>outline_confirmed_at</stage-ack>")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].action, "set")
        self.assertEqual(events[0].key, "outline_confirmed_at")
        self.assertTrue(events[0].executable)
        self.assertIsNone(events[0].ignored_reason)

    def test_clear_action(self):
        events = self.parser.parse_raw(
            '<stage-ack action="clear">outline_confirmed_at</stage-ack>'
        )
        self.assertEqual(events[0].action, "clear")

    def test_explicit_set_action(self):
        events = self.parser.parse_raw(
            '<stage-ack action="set">s0_interview_done_at</stage-ack>'
        )
        self.assertEqual(events[0].action, "set")

    def test_unknown_key_yields_non_executable_event(self):
        events = self.parser.parse_raw("<stage-ack>not_a_real_key</stage-ack>")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].key, "not_a_real_key")
        self.assertFalse(events[0].executable)
        self.assertEqual(events[0].ignored_reason, "unknown_key")

    def test_all_six_valid_keys(self):
        keys = [
            "s0_interview_done_at",
            "outline_confirmed_at",
            "review_started_at",
            "review_passed_at",
            "presentation_ready_at",
            "delivery_archived_at",
        ]
        self.assertEqual(VALID_KEYS, frozenset(keys))
        for key in keys:
            events = self.parser.parse_raw(f"<stage-ack>{key}</stage-ack>")
            self.assertEqual(len(events), 1)
            self.assertTrue(events[0].executable)

    def test_multi_tag_preserves_order_no_dedup(self):
        events = self.parser.parse_raw(
            "<stage-ack>outline_confirmed_at</stage-ack>\n"
            '<stage-ack action="clear">outline_confirmed_at</stage-ack>\n'
            "<stage-ack>outline_confirmed_at</stage-ack>\n"
        )
        self.assertEqual([e.action for e in events], ["set", "clear", "set"])

    def test_tag_positions_captured(self):
        content = "前缀 <stage-ack>outline_confirmed_at</stage-ack> 后缀"
        events = self.parser.parse_raw(content)
        self.assertEqual(content[events[0].start:events[0].end], events[0].raw)


class StageAckPositionJudgeTests(unittest.TestCase):
    def setUp(self):
        self.parser = StageAckParser()

    def test_tail_independent_line_executable(self):
        events = self.parser.parse(
            "报告完成。\n\n<stage-ack>outline_confirmed_at</stage-ack>\n"
        )
        self.assertTrue(events[0].executable)
        self.assertIsNone(events[0].ignored_reason)

    def test_non_tail_tag(self):
        events = self.parser.parse(
            "<stage-ack>outline_confirmed_at</stage-ack>\n\n这段在 tag 后。\n"
        )
        self.assertFalse(events[0].executable)
        self.assertEqual(events[0].ignored_reason, "not_tail")

    def test_fenced_code_backtick(self):
        events = self.parser.parse(
            "示例：\n```md\n<stage-ack>outline_confirmed_at</stage-ack>\n```\n正文。\n"
        )
        self.assertFalse(events[0].executable)
        self.assertEqual(events[0].ignored_reason, "in_fenced_code")

    def test_fenced_code_tilde(self):
        events = self.parser.parse(
            "~~~\n<stage-ack>outline_confirmed_at</stage-ack>\n~~~\n"
        )
        self.assertFalse(events[0].executable)
        self.assertEqual(events[0].ignored_reason, "in_fenced_code")

    def test_inline_code(self):
        events = self.parser.parse(
            "示例：`<stage-ack>outline_confirmed_at</stage-ack>`\n"
        )
        self.assertFalse(events[0].executable)
        self.assertEqual(events[0].ignored_reason, "in_inline_code")

    def test_blockquote(self):
        events = self.parser.parse(
            "> <stage-ack>outline_confirmed_at</stage-ack>\n"
        )
        self.assertFalse(events[0].executable)
        self.assertEqual(events[0].ignored_reason, "in_blockquote")

    def test_not_independent_line(self):
        events = self.parser.parse(
            "推进 <stage-ack>outline_confirmed_at</stage-ack> 吧"
        )
        self.assertFalse(events[0].executable)
        self.assertEqual(events[0].ignored_reason, "not_independent_line")

    def test_trailing_whitespace_still_tail(self):
        events = self.parser.parse(
            "完成。\n<stage-ack>outline_confirmed_at</stage-ack>\n   \n"
        )
        self.assertTrue(events[0].executable)

    def test_multiple_tail_tags_all_executable(self):
        events = self.parser.parse(
            "回退再推进。\n"
            '<stage-ack action="clear">outline_confirmed_at</stage-ack>\n'
            "<stage-ack>outline_confirmed_at</stage-ack>\n"
        )
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e.executable for e in events))

    def test_unknown_key_even_at_tail_still_flagged_unknown(self):
        events = self.parser.parse(
            "正文。\n\n<stage-ack>bogus</stage-ack>\n"
        )
        self.assertFalse(events[0].executable)
        self.assertEqual(events[0].ignored_reason, "unknown_key")
