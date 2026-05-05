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


from backend.report_writing import (
    assistant_text_claims_modification,
    check_no_prior_canonical_mutation_in_turn,
    check_no_fetch_url_pending,
)

# Also import remaining helpers for forward reference (used in CheckHelpersTests)
from backend.report_writing import (
    check_report_writing_stage, check_outline_confirmed,
    check_no_mixed_intent_in_turn,
    check_read_before_write_canonical_draft,
)


class AssistantTextClaimsModificationTests(unittest.TestCase):
    def test_explicit_completion_returns_true(self):
        self.assertTrue(assistant_text_claims_modification(
            "我已经把第二章重写完毕，请查看。",
        ))
        self.assertTrue(assistant_text_claims_modification(
            "正文已同步更新到 content/report_draft_v1.md。",
        ))
        self.assertTrue(assistant_text_claims_modification(
            "草稿完成第三章的扩写。",
        ))

    def test_intent_only_returns_false(self):
        self.assertFalse(assistant_text_claims_modification(
            "我会重写第二章，请稍等。",
        ))
        self.assertFalse(assistant_text_claims_modification(
            "我准备开始起草正文。",
        ))

    def test_unrelated_text_returns_false(self):
        self.assertFalse(assistant_text_claims_modification(
            "我不太确定这块怎么处理。",
        ))

    def test_intent_plus_completion_returns_true(self):
        # "我会修改" + "已完成" 混合 — 仍按完成处理（model 在文本里同时混合时算撒谎风险）
        self.assertTrue(assistant_text_claims_modification(
            "我会重写第二章，已经完成了起草。",
        ))


class CheckHelpersTests(unittest.TestCase):
    def test_check_no_prior_canonical_mutation_in_turn_pass(self):
        self.assertIsNone(check_no_prior_canonical_mutation_in_turn({}))
        self.assertIsNone(check_no_prior_canonical_mutation_in_turn(
            {"canonical_draft_mutation": None},
        ))

    def test_check_no_prior_canonical_mutation_in_turn_reject(self):
        msg = check_no_prior_canonical_mutation_in_turn(
            {"canonical_draft_mutation": {"tool": "rewrite_report_section"}},
        )
        self.assertIsNotNone(msg)
        self.assertIn("本轮已经修改过", msg)

    def test_check_no_fetch_url_pending_no_search_pass(self):
        self.assertIsNone(check_no_fetch_url_pending({}))

    def test_check_no_fetch_url_pending_search_no_fetch_reject(self):
        msg = check_no_fetch_url_pending(
            {"web_search_performed": True, "fetch_url_performed": False},
        )
        self.assertIsNotNone(msg)
        self.assertIn("fetch_url", msg)

    def test_check_no_fetch_url_pending_both_pass(self):
        self.assertIsNone(check_no_fetch_url_pending(
            {"web_search_performed": True, "fetch_url_performed": True},
        ))


if __name__ == "__main__":
    unittest.main()
