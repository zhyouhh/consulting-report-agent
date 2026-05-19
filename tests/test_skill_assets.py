import unittest
import codecs
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
                file_path.read_bytes().startswith(codecs.BOM_UTF8),
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
