# tests/test_report_writing.py
import unittest
from backend.report_writing import resolve_section_target


def _fake_heading_nodes(items):
    """items: list[(label, snapshot, start, end)]"""
    return [
        {"label": label, "snapshot": snap, "start": s, "end": e, "section_snapshot": snap}
        for label, snap, s, e in items
    ]


class ResolveSectionTargetTests(unittest.TestCase):
    def setUp(self):
        self.draft = "# 报告\n## 第一章 引言\n内容0\n## 第二章 战力分析\n内容B\n## 第三章 总结\n内容C\n"
        self.nodes = _fake_heading_nodes([
            ("第一章 引言", "## 第一章 引言\n内容0", 5, 25),
            ("第二章 战力分析", "## 第二章 战力分析\n内容B", 25, 50),
            ("第三章 总结", "## 第三章 总结\n内容C", 50, 75),
        ])

    def test_unique_prefix_returns_target(self):
        result = resolve_section_target(
            "重写第二章", self.draft,
            extract_markdown_heading_nodes=lambda _: self.nodes,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["label"], "第二章 战力分析")

    def test_zero_candidates_returns_none(self):
        result = resolve_section_target(
            "重写第四章", self.draft,
            extract_markdown_heading_nodes=lambda _: self.nodes,
        )
        self.assertIsNone(result)

    def test_partial_multi_prefix_fail_fast(self):
        # 第二章 unique，第四章 not in draft → fail-fast
        result = resolve_section_target(
            "把第二章和第四章重写", self.draft,
            extract_markdown_heading_nodes=lambda _: self.nodes,
        )
        self.assertIsNone(result)

    def test_multi_prefix_distinct_targets_returns_none(self):
        # 两个 prefix 都 unique 但指向不同 heading
        result = resolve_section_target(
            "把第二章和第三章重写", self.draft,
            extract_markdown_heading_nodes=lambda _: self.nodes,
        )
        self.assertIsNone(result)

    def test_multi_prefix_same_target_returns_target(self):
        # 重复 prefix 都指向同一个 heading
        result = resolve_section_target(
            "第二章再说第二章", self.draft,
            extract_markdown_heading_nodes=lambda _: self.nodes,
        )
        self.assertIsNotNone(result)

    def test_section_node_compound_excluded(self):
        # 第二章节 不应匹配 第二章
        result = resolve_section_target(
            "改第二章节", "# 报告\n## 第二章 X\n内容\n",
            extract_markdown_heading_nodes=lambda _: _fake_heading_nodes(
                [("第二章 X", "## 第二章 X\n内容", 5, 30)],
            ),
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
