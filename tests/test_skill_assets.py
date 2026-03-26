import unittest
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
