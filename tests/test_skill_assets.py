import unittest
import shutil
import subprocess
import tempfile
from pathlib import Path


class SkillAssetTests(unittest.TestCase):
    def test_runtime_skill_assets_include_referenced_cross_platform_files(self):
        root = Path(__file__).resolve().parents[1]
        required_files = [
            root / "skill" / "evals" / "capability-map.json",
            root / "skill" / "scripts" / "quality_check.sh",
            root / "skill" / "scripts" / "export_draft.sh",
        ]

        for file_path in required_files:
            self.assertTrue(file_path.exists(), f"缺少运行资产: {file_path}")

    def test_windows_powershell_scripts_use_utf8_bom(self):
        root = Path(__file__).resolve().parents[1]
        ps1_files = [
            root / "skill" / "scripts" / "quality_check.ps1",
            root / "skill" / "scripts" / "export_draft.ps1",
        ]

        for file_path in ps1_files:
            self.assertTrue(file_path.exists(), f"缺少 PowerShell 脚本: {file_path}")
            self.assertTrue(
                file_path.read_bytes().startswith(b"\xef\xbb\xbf"),
                f"{file_path.name} 必须带 UTF-8 BOM，否则 Windows PowerShell 会按 ANSI 解析中文并报错",
            )

    def test_windows_powershell_scripts_force_utf8_stdout(self):
        root = Path(__file__).resolve().parents[1]
        ps1_files = [
            root / "skill" / "scripts" / "quality_check.ps1",
            root / "skill" / "scripts" / "export_draft.ps1",
        ]

        for file_path in ps1_files:
            text = file_path.read_text(encoding="utf-8-sig")
            self.assertIn("[Console]::OutputEncoding", text)
            self.assertIn("$OutputEncoding", text)

    def test_quality_check_ps1_runs_directly_with_windows_powershell(self):
        powershell = shutil.which("powershell")
        if not powershell:
            self.skipTest("Windows PowerShell is not available")

        root = Path(__file__).resolve().parents[1]
        script_path = root / "skill" / "scripts" / "quality_check.ps1"
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "report.md"
            report_path.write_text("# 测试报告\n\n数据来源：内部测试。\n", encoding="utf-8")

            result = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script_path),
                    "-FilePath",
                    str(report_path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_export_draft_ps1_prefers_bundled_pandoc_before_system_path(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "skill" / "scripts" / "export_draft.ps1").read_text(
            encoding="utf-8-sig"
        )

        bundled_probe = "..\\..\\pandoc.exe"
        self.assertIn(bundled_probe, script)
        self.assertLess(script.index(bundled_probe), script.index("Get-Command pandoc"))
        self.assertIn("应用包内", script)
        self.assertIn("系统 PATH", script)
