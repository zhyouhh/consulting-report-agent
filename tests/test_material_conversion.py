import unittest
from pathlib import Path
from backend.material_conversion import MaterialConverter


class ConverterConstructTests(unittest.TestCase):
    def _make(self, tmp):
        return MaterialConverter(
            cache_dir=Path(tmp),
            vision_adapter=lambda data_url, mime: "VISION:" + mime,
            ocr_adapter=lambda path: "OCR",
            capability_resolver=lambda: False,
        )

    def test_constructs_with_injected_deps(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            conv = self._make(tmp)
            self.assertIsNotNone(conv)

    def test_does_not_import_chat(self):
        import backend.material_conversion as mod
        import inspect
        src = inspect.getsource(mod)
        self.assertNotIn("import chat", src)
        self.assertNotIn("from backend.chat", src)
