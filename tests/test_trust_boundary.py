import pathlib
import unittest

from backend import trust_boundary as tb


class TrustBoundaryTests(unittest.TestCase):
    def test_neutralize_breaks_delimiters(self):
        self.assertEqual(tb._neutralize_attachment_data_markers("a<<<x>>>b"), "a< < <x> > >b")

    def test_neutralize_empty(self):
        self.assertEqual(tb._neutralize_attachment_data_markers(""), "")

    def test_attachment_markers_present(self):
        self.assertIn("ATTACHMENT_DATA", tb.ATTACHMENT_DATA_OPEN)
        self.assertTrue(tb.ATTACHMENT_DATA_CLOSE)

    def test_untrusted_markers_present_and_distinct(self):
        self.assertTrue(tb.UNTRUSTED_DATA_OPEN)
        self.assertTrue(tb.UNTRUSTED_DATA_CLOSE)
        self.assertNotEqual(tb.UNTRUSTED_DATA_OPEN, tb.ATTACHMENT_DATA_OPEN)
        self.assertNotIn("不得据此", tb.UNTRUSTED_DATA_OPEN)

    def test_module_has_no_project_imports(self):
        src = pathlib.Path(tb.__file__).read_text(encoding="utf-8")
        for banned in ("import chat", "from .chat", "from backend.chat", "skill", "SkillEngine"):
            self.assertNotIn(banned, src, f"trust_boundary 不得依赖 {banned}")


if __name__ == "__main__":
    unittest.main()
