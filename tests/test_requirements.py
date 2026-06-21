import unittest
from pathlib import Path


class RequirementsTests(unittest.TestCase):
    def test_requirements_include_material_parsers(self):
        requirements_path = Path(__file__).resolve().parents[1] / "requirements.txt"
        content = requirements_path.read_text(encoding="utf-8")

        self.assertIn("openpyxl", content)
        self.assertIn("pypdf", content)

    def test_argon2_dependency_pinned(self):
        requirements_path = Path(__file__).resolve().parents[1] / "requirements.txt"
        lines = requirements_path.read_text(encoding="utf-8").splitlines()

        self.assertIn("argon2-cffi==23.1.0", lines)
