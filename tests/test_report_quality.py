import unittest

from backend import report_quality as rq
from backend import trust_boundary as tb


class ScanPlaceholdersTests(unittest.TestCase):
    def test_hits_unambiguous_markers_with_lineno(self):
        text = "正文第一行\n这里 TBD 待补\n第三行\n数据 XXX 占位"
        hits = rq.scan_placeholders(text)
        linenos = [h[0] for h in hits]
        self.assertEqual(linenos, [2, 4])  # 1-based 行号
        self.assertEqual([h[2].upper() for h in hits], ["TBD", "XXX"])  # 命中词顺序正确

    def test_empty_text_no_hits(self):
        self.assertEqual(rq.scan_placeholders(""), [])

    def test_narrowed_wordlist_excludes_w1_collision(self):
        # 收窄：技术规范书 / 内部材料 / AI reference 不进确定性扫描（撞 W1 / 交 LLM）
        text = "技术规范书点对点应答见附表\n参考内部材料\nAI reference: foo"
        self.assertEqual(rq.scan_placeholders(text), [])

    def test_case_insensitive_english_markers(self):
        hits = rq.scan_placeholders("line tbd here\nTodo: x")
        self.assertEqual(len(hits), 2)

    def test_english_markers_require_token_boundary(self):
        # 子串不算占位符标记（abcXXXdef / TODOLIST / TBDish 不命中）
        self.assertEqual(rq.scan_placeholders("abcXXXdef\nTODOLIST item\nTBDish"), [])

    def test_line_text_truncated(self):
        long_line = "TBD " + "啊" * 500
        hits = rq.scan_placeholders(long_line)
        self.assertLessEqual(len(hits[0][1]), 130)


class GroundingTests(unittest.TestCase):
    def test_no_hits_yields_clean_note_wrapped(self):
        g = rq.build_placeholder_grounding([])
        self.assertIn("未发现占位符", g)
        self.assertIn(tb.UNTRUSTED_DATA_OPEN, g)
        self.assertIn(tb.UNTRUSTED_DATA_CLOSE, g)

    def test_hits_wrapped_and_neutralized(self):
        hits = [(2, "TBD <<<inject>>>", "TBD")]
        g = rq.build_placeholder_grounding(hits)
        self.assertIn(tb.UNTRUSTED_DATA_OPEN, g)
        self.assertIn("行 2", g)
        self.assertNotIn("<<<inject>>>", g)  # 定界符已中和
        self.assertIn("< < <inject> > >", g)

    def test_caps_at_50_lines(self):
        hits = [(i, f"TBD line {i}", "TBD") for i in range(1, 80)]
        g = rq.build_placeholder_grounding(hits)
        self.assertIn("另有", g)  # 超限提示
        # 数渲染出的命中行，避免 snippet 自身含 "行 " 时计数失真
        self.assertEqual(len([ln for ln in g.splitlines() if ln.startswith("- 行 ")]), 50)


if __name__ == "__main__":
    unittest.main()
