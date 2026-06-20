import unittest
from backend import material_limits as ml


class MaterialLimitsTests(unittest.TestCase):
    def test_is_heavy_suffix_case_insensitive(self):
        self.assertTrue(ml.is_heavy_suffix(".DOCX"))
        self.assertFalse(ml.is_heavy_suffix(".txt"))

    def test_truncate_transcript(self):
        text, cut = ml.truncate_transcript("a" * (ml.MAX_TRANSCRIPT_CHARS + 10))
        self.assertTrue(cut)
        self.assertEqual(len(text), ml.MAX_TRANSCRIPT_CHARS)
        text2, cut2 = ml.truncate_transcript("short")
        self.assertFalse(cut2)
        self.assertEqual(text2, "short")
