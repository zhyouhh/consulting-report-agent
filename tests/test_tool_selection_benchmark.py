"""Tool selection schema benchmark — Spec §7.8 §8.7.

This is NOT a real model accuracy test (mocking LLM tool selection without
the real model is meaningless). Real model behavior is verified in Task 6.3
cutover smoke 5 sessions (reality_test).

This unit test only verifies the 4-tool schema is well-formed: each tool
schema is registered, descriptions contain semantic-disambiguation keywords,
and parameter shapes match spec.
"""
import unittest

from tests.test_chat_runtime import ChatRuntimeTests as _ChatRuntimeTests


class ToolSelectionBenchmarkTests(_ChatRuntimeTests):
    """Schema-shape sanity for the 4 specialized writing tools."""

    BENCHMARK_SUITE = [
        # (user_message, expected_primary_tool_family, notes)
        ("开始写报告正文", "append_report_draft", "begin keyword"),
        ("继续写下一章", "append_report_draft", "continue keyword"),
        ("请把第二章重写一下", "rewrite_report_section", "section prefix"),
        ("重写第二章和第三章", None, "multi-prefix → reject"),
        ("把正文里的'渠道效率'改成'渠道质量'", "replace_report_text", "replace pattern"),
        ("把'增长'改成'高质量增长'", "replace_report_text", "replace pattern (no 正文/报告)"),
        ("整篇重写，推倒重来", "rewrite_report_draft", "whole rewrite explicit"),
        ("全文重写，但保留原来的章节结构", "rewrite_report_draft", "whole rewrite + constraint"),
        ("第二章太弱了，改强一点", "rewrite_report_section", "section + 改 (改强 = colloquial)"),
        ("继续写到5000字，然后导出", "append_report_draft", "continue + secondary export"),
    ]

    def test_4_tools_registered(self):
        """All 4 specialized writing tools must be present in _get_tools()."""
        handler = self._make_handler_with_project()
        tools = handler._get_tools()
        names = {t["function"]["name"] for t in tools if "function" in t}
        expected = {
            "append_report_draft",
            "rewrite_report_section",
            "replace_report_text",
            "rewrite_report_draft",
        }
        self.assertEqual(names & expected, expected, f"Missing tools: {expected - names}")

    def test_tool_descriptions_contain_disambiguators(self):
        """Each new tool description must contain semantic keywords helping
        the model distinguish between them."""
        handler = self._make_handler_with_project()
        tools = handler._get_tools()
        by_name = {t["function"]["name"]: t["function"] for t in tools if "function" in t}
        # rewrite_report_section: must mention 章/节
        self.assertIn("章", by_name["rewrite_report_section"]["description"])
        self.assertTrue(
            "唯一" in by_name["rewrite_report_section"]["description"]
            or "定位" in by_name["rewrite_report_section"]["description"]
        )
        # replace_report_text: must mention 唯一
        self.assertIn("唯一", by_name["replace_report_text"]["description"])
        # rewrite_report_draft: must mention 整篇 / 推倒 / 全文
        whole_kws = ("整篇", "推倒", "全文")
        self.assertTrue(
            any(kw in by_name["rewrite_report_draft"]["description"] for kw in whole_kws)
        )

    def test_tool_parameter_shapes(self):
        """Each new tool must have its parameter signature."""
        handler = self._make_handler_with_project()
        tools = handler._get_tools()
        by_name = {t["function"]["name"]: t["function"] for t in tools if "function" in t}
        # rewrite_report_section: only content
        sec_params = by_name["rewrite_report_section"]["parameters"]
        self.assertEqual(set(sec_params["properties"].keys()), {"content"})
        self.assertEqual(sec_params["required"], ["content"])
        # replace_report_text: old + new (both required)
        rep_params = by_name["replace_report_text"]["parameters"]
        self.assertEqual(set(rep_params["properties"].keys()), {"old", "new"})
        self.assertEqual(set(rep_params["required"]), {"old", "new"})
        # rewrite_report_draft: only content
        draft_params = by_name["rewrite_report_draft"]["parameters"]
        self.assertEqual(set(draft_params["properties"].keys()), {"content"})
        self.assertEqual(draft_params["required"], ["content"])

    def test_obligation_detector_matches_benchmark(self):
        """detect_canonical_draft_write_obligation should align with the
        benchmark suite expected tool family (where defined)."""
        from backend.report_writing import detect_canonical_draft_write_obligation

        for user_msg, expected_tool, note in self.BENCHMARK_SUITE:
            obligation = detect_canonical_draft_write_obligation(user_msg)
            if expected_tool is None:
                # multi-prefix → expected_tool=None means we can't predict;
                # detector may still detect "rewrite_section" family.
                # For multi-prefix case: detector triggers on "重写"
                # but tool will reject due to ambiguity. Both are valid.
                continue
            if expected_tool == "append_report_draft":
                # detector returns family "begin" or "continue"
                self.assertIsNotNone(
                    obligation, f"Detector should fire for: {user_msg!r}"
                )
                self.assertIn(
                    obligation["tool_family"],
                    ("begin", "continue"),
                    f"For {user_msg!r}: got {obligation['tool_family']}",
                )
            elif expected_tool == "rewrite_report_section":
                self.assertIsNotNone(
                    obligation, f"Detector should fire for: {user_msg!r}"
                )
                self.assertEqual(
                    obligation["tool_family"],
                    "rewrite_section",
                    f"For {user_msg!r}: got {obligation}",
                )
            elif expected_tool == "replace_report_text":
                self.assertIsNotNone(
                    obligation, f"Detector should fire for: {user_msg!r}"
                )
                self.assertEqual(
                    obligation["tool_family"],
                    "replace_text",
                    f"For {user_msg!r}: got {obligation}",
                )
            elif expected_tool == "rewrite_report_draft":
                self.assertIsNotNone(
                    obligation, f"Detector should fire for: {user_msg!r}"
                )
                self.assertEqual(
                    obligation["tool_family"],
                    "rewrite_draft",
                    f"For {user_msg!r}: got {obligation}",
                )


for _inherited_test_name in dir(_ChatRuntimeTests):
    if (
        _inherited_test_name.startswith("test_")
        and _inherited_test_name not in ToolSelectionBenchmarkTests.__dict__
    ):
        setattr(ToolSelectionBenchmarkTests, _inherited_test_name, None)
del _inherited_test_name
del _ChatRuntimeTests


if __name__ == "__main__":
    unittest.main()
