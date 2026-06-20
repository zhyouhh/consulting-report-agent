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


class DocConvertCacheTests(unittest.TestCase):
    def _conv(self, tmp):
        from backend.material_conversion import MaterialConverter
        return MaterialConverter(
            cache_dir=Path(tmp), vision_adapter=lambda *a: "V",
            ocr_adapter=lambda p: "O", capability_resolver=lambda: False,
        )

    def test_txt_passthrough_and_cache_hit_skips_reconvert(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a.txt"; src.write_text("纯文本内容", encoding="utf-8")
            conv = self._conv(tmp)
            md = conv.convert_document(src)
            self.assertIn("纯文本内容", md)
            # 第二次命中缓存：断言不再走 _raw_convert_document（证明走缓存而非重转）
            with mock.patch.object(conv, "_raw_convert_document", side_effect=AssertionError("不应重转")):
                cached = conv.convert_document(src)
            self.assertEqual(cached, md)

    def test_failure_writes_tombstone_and_raises(self):
        import tempfile
        from backend.material_conversion import MaterialConversionError
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "bad.docx"; src.write_bytes(b"not a real docx")
            conv = self._conv(tmp)
            with self.assertRaises(MaterialConversionError):
                conv.convert_document(src)
            # 再次调用命中 tombstone 仍抛（不重复全量解析）
            with self.assertRaises(MaterialConversionError):
                conv.convert_document(src)
