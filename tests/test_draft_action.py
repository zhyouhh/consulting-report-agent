import unittest
from backend.draft_action import DraftActionParser, DraftActionEvent

class DraftActionParserBasicTests(unittest.TestCase):
    def setUp(self):
        self.parser = DraftActionParser()

    def test_parse_begin(self):
        events = self.parser.parse("Reply\n<draft-action>begin</draft-action>")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].intent, "begin")
        self.assertTrue(events[0].executable)

    def test_parse_continue(self):
        events = self.parser.parse("Reply\n<draft-action>continue</draft-action>")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].intent, "continue")

    def test_parse_section_with_label(self):
        events = self.parser.parse("Reply\n<draft-action>section:第二章 战力演化</draft-action>")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].intent, "section")
        self.assertEqual(events[0].section_label, "第二章 战力演化")

    def test_parse_replace_nested(self):
        content = "Reply\n<draft-action-replace>\n  <old>原文</old>\n  <new>新文</new>\n</draft-action-replace>"
        events = self.parser.parse(content)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].intent, "replace")
        self.assertEqual(events[0].old_text, "原文")
        self.assertEqual(events[0].new_text, "新文")

    def test_strip_simple_tag(self):
        content = "Reply\n<draft-action>begin</draft-action>"
        result = self.parser.strip(content)
        self.assertEqual(result.strip(), "Reply")

    def test_strip_replace_block(self):
        content = "Reply\n<draft-action-replace>\n<old>x</old>\n<new>y</new>\n</draft-action-replace>"
        result = self.parser.strip(content)
        self.assertEqual(result.strip(), "Reply")

    def test_unknown_intent_ignored(self):
        events = self.parser.parse("Reply\n<draft-action>unknown</draft-action>")
        # 不识别（正则不匹配）
        self.assertEqual(len(events), 0)

    def test_section_label_too_long_ignored(self):
        long = "x" * 100
        events = self.parser.parse(f"Reply\n<draft-action>section:{long}</draft-action>")
        # 80 字符上限
        self.assertEqual(len(events), 0)


class DraftActionParserPositionTests(unittest.TestCase):
    def setUp(self):
        self.parser = DraftActionParser()

    def test_in_fenced_code_ignored_but_stripped(self):
        content = "Reply\n```\n<draft-action>begin</draft-action>\n```"
        events = self.parser.parse(content)
        self.assertTrue(all(not e.executable for e in events))
        self.assertEqual(events[0].ignored_reason, "in_fenced_code")
        # strip 仍然剥
        result = self.parser.strip(content)
        self.assertNotIn("<draft-action", result)

    def test_in_inline_code_ignored(self):
        content = "Reply `<draft-action>begin</draft-action>`"
        events = self.parser.parse(content)
        self.assertTrue(all(not e.executable for e in events))

    def test_in_blockquote_ignored(self):
        content = "Reply\n> <draft-action>begin</draft-action>"
        events = self.parser.parse(content)
        self.assertTrue(all(not e.executable for e in events))

    def test_non_tail_ignored(self):
        content = "<draft-action>begin</draft-action>\nMore text after."
        events = self.parser.parse(content)
        self.assertTrue(all(not e.executable for e in events))
        self.assertEqual(events[0].ignored_reason, "not_tail")

    def test_non_independent_line_ignored(self):
        content = "Reply <draft-action>begin</draft-action>"
        events = self.parser.parse(content)
        self.assertTrue(all(not e.executable for e in events))

    def test_tail_with_trailing_whitespace_ok(self):
        content = "Reply\n<draft-action>begin</draft-action>\n\n   "
        events = self.parser.parse(content)
        self.assertTrue(events[0].executable)
