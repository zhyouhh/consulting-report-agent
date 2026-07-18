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

    def test_internal_citation_variants_are_detected(self):
        text = "\n".join(
            [
                "结论 [DL-2026-01]",
                "补充 [DL-001]",
                "合并 [DL-2026-01/06]",
                "同框 [DL-2026-01、DL-2026-03]",
                "行内连写 [DL-001][DL-002]",
            ]
        )
        hits = rq.scan_placeholders(text)
        self.assertEqual([hit[0] for hit in hits], [1, 2, 3, 4, 5])
        self.assertEqual(hits[0][2], "[DL-2026-01]")
        self.assertEqual(hits[3][2], "[DL-2026-01、DL-2026-03]")
        self.assertEqual(
            rq.INTERNAL_CITATION_RE.findall("[DL-001][DL-002]"),
            ["[DL-001]", "[DL-002]"],
        )

    def test_internal_citation_false_positives_and_cross_line_group_are_rejected(self):
        text = "\n".join(
            [
                "型号 [DL-2026-01 型设备]",
                "普通脚注 [注]",
                "数字脚注 [1]",
                "非规范小写 [dl-001]",
                "跨行 [DL-2026-01",
                "、DL-2026-02]",
            ]
        )
        self.assertEqual(rq.scan_placeholders(text), [])
        self.assertIsNone(
            rq.INTERNAL_CITATION_RE.search("[DL-2026-01\n、DL-2026-02]")
        )

    def test_internal_citation_group_accepts_only_horizontal_separator_space(self):
        self.assertIsNotNone(
            rq.INTERNAL_CITATION_RE.fullmatch("[DL-2026-01\t， \tDL-002]")
        )
        self.assertIsNone(
            rq.INTERNAL_CITATION_RE.fullmatch("[DL-2026-01\n，DL-002]")
        )


class GroundingTests(unittest.TestCase):
    def test_no_hits_yields_clean_note_wrapped(self):
        g = rq.build_placeholder_grounding([])
        self.assertIn("未发现占位符", g)
        self.assertIn("内部资料编号标记（[DL-...]）", g)
        self.assertIn(tb.UNTRUSTED_DATA_OPEN, g)
        self.assertIn(tb.UNTRUSTED_DATA_CLOSE, g)

    def test_hits_wrapped_and_neutralized(self):
        hits = [(2, "TBD <<<inject>>>", "TBD")]
        g = rq.build_placeholder_grounding(hits)
        self.assertIn(tb.UNTRUSTED_DATA_OPEN, g)
        self.assertIn("行 2", g)
        self.assertNotIn("<<<inject>>>", g)  # 定界符已中和
        self.assertIn("< < <inject> > >", g)

    def test_internal_citation_category_named_in_hit_grounding(self):
        g = rq.build_placeholder_grounding([(3, "结论 [DL-001]", "[DL-001]")])
        self.assertIn("内部资料编号标记（[DL-...]）", g)
        self.assertIn("行 3（[DL-001]）", g)

    def test_caps_at_50_lines(self):
        hits = [(i, f"TBD line {i}", "TBD") for i in range(1, 80)]
        g = rq.build_placeholder_grounding(hits)
        self.assertIn("另有", g)  # 超限提示
        # 数渲染出的命中行，避免 snippet 自身含 "行 " 时计数失真
        self.assertEqual(len([ln for ln in g.splitlines() if ln.startswith("- 行 ")]), 50)


if __name__ == "__main__":
    unittest.main()
