import unittest
import shutil
import tempfile
from unittest import mock
from pathlib import Path

from backend.report_tools import export_reviewable_draft, run_quality_check


class ReportToolsTests(unittest.TestCase):
    @mock.patch("backend.report_tools.subprocess.run")
    def test_run_quality_check_returns_stdout(self, mock_run):
        mock_run.return_value = mock.Mock(returncode=0, stdout="高风险: 0", stderr="")
        result = run_quality_check("D:/tmp/report.md", "D:/skill/scripts/quality_check.ps1")
        self.assertEqual(result["status"], "ok")
        self.assertIn("高风险: 0", result["output"])
        self.assertEqual(mock_run.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(mock_run.call_args.kwargs["errors"], "replace")

    @mock.patch("backend.report_tools.subprocess.run")
    def test_run_quality_check_returns_error_and_stderr_on_failure(self, mock_run):
        mock_run.return_value = mock.Mock(returncode=1, stdout="", stderr="脚本失败")
        result = run_quality_check("D:/tmp/report.md", "D:/skill/scripts/quality_check.ps1")
        self.assertEqual(result["status"], "error")
        self.assertIn("脚本失败", result["output"])

    @mock.patch("backend.report_tools.subprocess.run")
    def test_export_reviewable_draft_returns_output_path(self, mock_run):
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout="已生成可审草稿: D:/tmp/output/report.docx",
            stderr="",
        )
        result = export_reviewable_draft(
            "D:/tmp/report.md",
            "D:/tmp/output",
            "D:/skill/scripts/export_draft.ps1",
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(Path(result["output_path"]).name, "report.docx")
        self.assertEqual(mock_run.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(mock_run.call_args.kwargs["errors"], "replace")

    @unittest.skipIf(shutil.which("powershell") is None, "PowerShell is required for the script smoke test")
    def test_quality_check_script_stdout_decodes_chinese(self):
        root = Path(__file__).resolve().parents[1]
        script_path = root / "skill" / "scripts" / "quality_check.ps1"

        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "报告.md"
            report_path.write_text("# 报告标题\n\n这是一段用于检查脚本输出的正文。", encoding="utf-8")

            result = run_quality_check(str(report_path), str(script_path))

        self.assertEqual(result["status"], "ok")
        self.assertIn("[CHECK] 开始检查报告质量", result["output"])
        self.assertIn("低风险", result["output"])
        self.assertNotIn("??", result["output"])
        self.assertNotIn("\ufffd", result["output"])
