import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipIf(shutil.which("powershell") is None, "PowerShell is required for lint report tests")
class LintReportScriptTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.script_path = self.root / "skill" / "scripts" / "quality_check.ps1"

    def _run_script(self, report_path: Path, output_path: Path | None = None, *, dry_run: bool = False):
        args = [
            shutil.which("powershell"),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.script_path),
            "-FilePath",
            str(report_path),
        ]
        if output_path is not None:
            args.extend(["-OutputPath", str(output_path)])
        if dry_run:
            args.append("-DryRun")
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    def test_lint_report_writes_markdown_with_marker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "report.md"
            output_path = Path(tmpdir) / "lint-report.md"
            report_path.write_text(
                "# 报告\n\n## 第一章 市场\n\n本章将分析市场。市场规模达到 5000 亿元。\n",
                encoding="utf-8",
            )

            result = self._run_script(report_path, output_path)

            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            report = output_path.read_text(encoding="utf-8-sig")
            self.assertIn("# AI 味自查报告", report)
            self.assertTrue(report.rstrip().endswith("<!-- lint-report:complete -->"))

    def test_lint_report_4_dimensions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "report.md"
            output_path = Path(tmpdir) / "lint-report.md"
            report_path.write_text(
                "# 报告\n\n"
                "## 第一章 市场\n\n"
                "本章将分析市场。市场规模达到 5000 亿元。\n"
                "XXX\n",
                encoding="utf-8",
            )

            result = self._run_script(report_path, output_path)

            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            report = output_path.read_text(encoding="utf-8-sig")
            self.assertIn("`AI 腔`", report)
            self.assertIn("`内容缺失`", report)
            self.assertIn("`缺标注`", report)
            self.assertIn("章节 So What 偏少", report)

    def test_lint_report_groups_by_section(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "report.md"
            output_path = Path(tmpdir) / "lint-report.md"
            report_path.write_text(
                "# 报告\n\n"
                "## 第一章 市场\n\n本章将分析市场。\n\n"
                "## 第二章 建议\n\n建议建立月度复盘机制。\n",
                encoding="utf-8",
            )

            result = self._run_script(report_path, output_path)

            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            report = output_path.read_text(encoding="utf-8-sig")
            self.assertIn("### 第一章 市场", report)
            self.assertIn("### 第二章 建议", report)

    def test_lint_report_negative_lookahead(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "report.md"
            output_path = Path(tmpdir) / "lint-report.md"
            report_path.write_text(
                "# 报告\n\n## 第一章 数据\n\n根据内部数据，差异非常显著（5%），建议优先处理。\n",
                encoding="utf-8",
            )

            result = self._run_script(report_path, output_path)

            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            report = output_path.read_text(encoding="utf-8-sig")
            self.assertNotIn("非常显著", report)

    def test_lint_report_dry_run_no_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "report.md"
            output_path = Path(tmpdir) / "lint-report.md"
            report_path.write_text("# 报告\n\n## 第一章\n\n本章将分析市场。\n", encoding="utf-8")

            result = self._run_script(report_path, output_path, dry_run=True)

            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            self.assertFalse(output_path.exists())
            self.assertIn("# AI 味自查报告", result.stdout)
            self.assertIn("<!-- lint-report:complete -->", result.stdout)

    def test_lint_report_top_n_truncation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "report.md"
            output_path = Path(tmpdir) / "lint-report.md"
            noisy_lines = ["本章将分析市场。 " + ("长文本" * 120) for _ in range(120)]
            report_path.write_text(
                "# 报告\n\n## 第一章 市场\n\n" + "\n".join(noisy_lines),
                encoding="utf-8",
            )

            result = self._run_script(report_path, output_path)

            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            report = output_path.read_text(encoding="utf-8-sig")
            self.assertIn("超长报告，仅显示前 30 条 issue", report)
            self.assertLessEqual(report.count("`AI 腔`"), 30)
