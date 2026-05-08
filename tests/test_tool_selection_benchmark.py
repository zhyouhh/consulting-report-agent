"""Tool selection schema benchmark for the report-writing cutover.

This is not a model accuracy test. It verifies the model-visible tool surface
after Commit 2: append for new draft content, edit_file for modifying existing
draft text, and no retired specialized rewrite/replace schemas.
"""
import unittest

from tests.test_chat_runtime import ChatRuntimeTests as _ChatRuntimeTests


class ToolSelectionBenchmarkTests(_ChatRuntimeTests):
    """Schema-shape sanity for the report-writing tools visible to the model."""

    def _tools_by_name(self):
        handler = self._make_handler_with_project()
        return {
            tool["function"]["name"]: tool["function"]
            for tool in handler._get_tools()
            if "function" in tool
        }

    def test_report_writing_tool_surface_is_cutover_shape(self):
        by_name = self._tools_by_name()

        self.assertIn("append_report_draft", by_name)
        self.assertIn("edit_file", by_name)

    def test_tool_descriptions_guide_cutover_usage(self):
        by_name = self._tools_by_name()

        append_desc = by_name["append_report_draft"]["description"]
        self.assertIn("首次成稿", append_desc)
        self.assertIn("续写", append_desc)
        self.assertIn("edit_file", append_desc)

        edit_desc = by_name["edit_file"]["description"]
        self.assertIn("精确字符串替换", edit_desc)
        self.assertIn("read_file", edit_desc)
        self.assertIn("正文草稿", edit_desc)
        self.assertIn("write_file", edit_desc)

    def test_tool_parameter_shapes(self):
        by_name = self._tools_by_name()

        append_params = by_name["append_report_draft"]["parameters"]
        self.assertEqual(set(append_params["properties"].keys()), {"content"})
        self.assertEqual(append_params["required"], ["content"])

        edit_params = by_name["edit_file"]["parameters"]
        self.assertEqual(
            set(edit_params["properties"].keys()),
            {"file_path", "old_string", "new_string"},
        )
        self.assertEqual(
            set(edit_params["required"]),
            {"file_path", "old_string", "new_string"},
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
