import threading
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
        from unittest import mock
        from backend.material_conversion import MaterialConversionError
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "bad.docx"; src.write_bytes(b"not a real docx")
            conv = self._conv(tmp)
            with self.assertRaises(MaterialConversionError):
                conv.convert_document(src)
            # 第二次必须命中 tombstone、绝不重新解析：patch _raw_convert_document 断言不被调用
            with mock.patch.object(conv, "_raw_convert_document",
                                   side_effect=AssertionError("应命中 tombstone，不应重新解析")):
                with self.assertRaises(MaterialConversionError):
                    conv.convert_document(src)

    def test_snapshot_enforces_heavy_size_cap(self):
        import tempfile
        from unittest import mock
        from backend import material_limits
        from backend.material_conversion import MaterialConversionError
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "big.pdf"; src.write_bytes(b"%PDF-1.4 " + b"x" * 4096)
            conv = self._conv(tmp)
            with mock.patch.object(material_limits, "MAX_HEAVY_MATERIAL_BYTES", 100):
                with self.assertRaises(MaterialConversionError):
                    conv.convert_document(src)


class StatusForKeyTests(unittest.TestCase):
    """N6 D2: read-only status probe (no transcription / no request). v1 set = {not_parsed, parsed, failed}."""

    def _conv(self, tmp):
        return MaterialConverter(
            cache_dir=Path(tmp), vision_adapter=lambda *a: "V",
            ocr_adapter=lambda p: "O", capability_resolver=lambda: False,
        )

    def test_not_parsed_when_no_cache(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            conv = self._conv(tmp)
            status, reason = conv.status_for_key("deadbeef-n6-v1")
            self.assertEqual(status, "not_parsed")
            self.assertIsNone(reason)

    def test_parsed_when_md_cache_exists(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a.txt"; src.write_text("内容", encoding="utf-8")
            conv = self._conv(tmp)
            key = conv._cache_key(src)
            conv.convert_document(src)  # primes the .md cache
            status, reason = conv.status_for_key(key)
            self.assertEqual(status, "parsed")
            self.assertIsNone(reason)

    def test_failed_returns_tombstone_text(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            conv = self._conv(tmp)
            key = "feedface-n6-v1"
            _, err_path = conv._cache_paths(key)
            err_path.write_text("文档解析失败：BoomError", encoding="utf-8")
            status, reason = conv.status_for_key(key)
            self.assertEqual(status, "failed")
            self.assertEqual(reason, "文档解析失败：BoomError")


class CacheGCTests(unittest.TestCase):
    def test_release_only_deletes_when_no_refs(self):
        import tempfile
        from backend.material_conversion import MaterialConverter
        with tempfile.TemporaryDirectory() as tmp:
            conv = MaterialConverter(cache_dir=Path(tmp), vision_adapter=lambda *a: "V",
                                     ocr_adapter=lambda p: "O", capability_resolver=lambda: False)
            src = Path(tmp) / "a.txt"; src.write_text("同内容", encoding="utf-8")
            key = conv._cache_key(src)
            conv.convert_document(src)
            md_path, _ = conv._cache_paths(key)
            self.assertTrue(md_path.exists())
            # 两个材料引用同 hash：mat1, mat2
            conv.retain(key, "mat1"); conv.retain(key, "mat2")
            conv.release(key, "mat1")
            self.assertTrue(md_path.exists())   # 还有 mat2 引用
            conv.release(key, "mat2")
            self.assertFalse(md_path.exists())  # 无引用才删

    def test_process_wide_key_lock_serializes_converter_instances_and_preserves_refs(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            relative_alias = cache_dir / ".." / cache_dir.name
            first = MaterialConverter(
                cache_dir=cache_dir,
                vision_adapter=lambda *a: "V",
                ocr_adapter=lambda p: "O",
                capability_resolver=lambda: False,
            )
            second = MaterialConverter(
                cache_dir=relative_alias,
                vision_adapter=lambda *a: "V",
                ocr_adapter=lambda p: "O",
                capability_resolver=lambda: False,
            )
            key = "same-content-n6-v1"

            first_read_done = threading.Event()
            release_first_read = threading.Event()
            second_entered_read = threading.Event()
            state_lock = threading.Lock()
            active = 0
            max_concurrent = 0
            original_first_read = first._read_refs
            original_second_read = second._read_refs

            def instrumented_read(original, *, block=False):
                def read(ref_key):
                    nonlocal active, max_concurrent
                    with state_lock:
                        active += 1
                        max_concurrent = max(max_concurrent, active)
                    try:
                        refs = original(ref_key)
                        if block:
                            first_read_done.set()
                            if not release_first_read.wait(timeout=3):
                                raise AssertionError("timed out releasing first retain")
                        else:
                            second_entered_read.set()
                        return refs
                    finally:
                        with state_lock:
                            active -= 1

                return read

            first._read_refs = instrumented_read(original_first_read, block=True)
            second._read_refs = instrumented_read(original_second_read)
            second_done = threading.Event()

            first_thread = threading.Thread(target=first.retain, args=(key, "mat-a"))

            def retain_second():
                try:
                    second.retain(key, "mat-b")
                finally:
                    second_done.set()

            second_thread = threading.Thread(target=retain_second)
            first_thread.start()
            self.assertTrue(first_read_done.wait(timeout=3))
            second_thread.start()
            # A converter-instance lock would let the second read/write overlap here and lose
            # mat-b when the first stale read resumes. The process-level canonical-path lock
            # keeps it outside the critical section.
            self.assertFalse(second_entered_read.wait(timeout=0.15))
            self.assertFalse(second_done.is_set())
            release_first_read.set()
            first_thread.join(timeout=3)
            second_thread.join(timeout=3)

            self.assertFalse(first_thread.is_alive())
            self.assertFalse(second_thread.is_alive())
            self.assertEqual(max_concurrent, 1)
            self.assertEqual(first._read_refs(key), {"mat-a", "mat-b"})

    def test_different_cache_keys_do_not_share_one_global_lock(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            converter = MaterialConverter(
                cache_dir=Path(tmp),
                vision_adapter=lambda *a: "V",
                ocr_adapter=lambda p: "O",
                capability_resolver=lambda: False,
            )
            inside_read = threading.Barrier(2)
            original_read = converter._read_refs
            state_lock = threading.Lock()
            active = 0
            max_concurrent = 0

            def observed_read(key):
                nonlocal active, max_concurrent
                with state_lock:
                    active += 1
                    max_concurrent = max(max_concurrent, active)
                try:
                    inside_read.wait(timeout=3)
                    return original_read(key)
                finally:
                    with state_lock:
                        active -= 1

            converter._read_refs = observed_read
            threads = [
                threading.Thread(target=converter.retain, args=(f"key-{index}", f"mat-{index}"))
                for index in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=3)

            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(max_concurrent, 2)


class LegacyConvertTests(unittest.TestCase):
    def _conv(self, tmp):
        from backend.material_conversion import MaterialConverter
        return MaterialConverter(cache_dir=Path(tmp), vision_adapter=lambda *a: "V",
                                 ocr_adapter=lambda p: "O", capability_resolver=lambda: False)

    def test_doc_no_soffice_raises_friendly(self):
        import tempfile
        from unittest import mock
        from backend.material_conversion import MaterialConversionError
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "old.doc"; src.write_bytes(b"\xd0\xcf\x11\xe0legacy")
            conv = self._conv(tmp)
            with mock.patch("backend.material_conversion.shutil.which", return_value=None):
                with self.assertRaises(MaterialConversionError) as ctx:
                    conv.convert_document(src)
            self.assertIn("老版本", str(ctx.exception))

    def test_doc_soffice_success_then_markitdown(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "old.doc"; src.write_bytes(b"\xd0\xcf\x11\xe0legacy")
            conv = self._conv(tmp)
            with mock.patch("backend.material_conversion.shutil.which", return_value="/usr/bin/soffice"), \
                 mock.patch("backend.material_conversion.subprocess.run") as run, \
                 mock.patch.object(conv, "_markitdown_convert", return_value="转换后正文"):
                def _fake_run(cmd, **kw):
                    out = Path(cmd[cmd.index("--outdir") + 1]) / (src.stem + ".docx"); out.write_text("x")
                    return mock.Mock(returncode=0)
                run.side_effect = _fake_run
                self.assertEqual(conv.convert_document(src), "转换后正文")

    def test_doc_soffice_timeout_friendly(self):
        import tempfile, subprocess
        from unittest import mock
        from backend.material_conversion import MaterialConversionError
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "old.doc"; src.write_bytes(b"\xd0\xcf\x11\xe0legacy")
            conv = self._conv(tmp)
            with mock.patch("backend.material_conversion.shutil.which", return_value="/usr/bin/soffice"), \
                 mock.patch("backend.material_conversion.subprocess.run", side_effect=subprocess.TimeoutExpired("soffice", 120)):
                with self.assertRaises(MaterialConversionError) as ctx:
                    conv.convert_document(src)
            self.assertIn("超时", str(ctx.exception))

    def test_xls_markitdown_first_no_soffice(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "old.xls"; src.write_bytes(b"\xd0\xcf\x11\xe0xls")
            conv = self._conv(tmp)
            with mock.patch("backend.material_conversion.shutil.which", return_value=None), \
                 mock.patch.object(conv, "_markitdown_convert", return_value="表格正文"):
                self.assertEqual(conv.convert_document(src), "表格正文")

    def test_no_shared_soffice_residue_after_conversion(self):
        # 隔离回归：转换后 cache_dir 下不留共享 _soffice 目录（防同名/并发串台）
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "old.doc"; src.write_bytes(b"\xd0\xcf\x11\xe0legacy")
            conv = self._conv(tmp)
            with mock.patch("backend.material_conversion.shutil.which", return_value="/usr/bin/soffice"), \
                 mock.patch("backend.material_conversion.subprocess.run") as run, \
                 mock.patch.object(conv, "_markitdown_convert", return_value="正文A"):
                def _fake_run(cmd, **kw):
                    out = Path(cmd[cmd.index("--outdir") + 1]) / (src.stem + ".docx"); out.write_text("x")
                    return mock.Mock(returncode=0)
                run.side_effect = _fake_run
                conv.convert_document(src)
            self.assertFalse((conv.cache_dir / "_soffice").exists())


class ImageTranscribeTests(unittest.TestCase):
    def _conv(self, tmp, *, multimodal, vision="VIS", ocr="OCRTXT", namespace="visM-p1-ocrR1"):
        from backend.material_conversion import MaterialConverter
        return MaterialConverter(cache_dir=Path(tmp),
            vision_adapter=lambda data_url, mime: vision,
            ocr_adapter=lambda p: ocr, capability_resolver=lambda: multimodal,
            image_cache_namespace=namespace)

    def test_textonly_uses_vision_and_caches(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "a.png"; img.write_bytes(b"\x89PNG fake")
            conv = self._conv(tmp, multimodal=False, vision="VIS-OK")
            self.assertEqual(conv.transcribe_image(img, "image/png"), "VIS-OK")
            conv._vision_adapter = lambda *a: (_ for _ in ()).throw(AssertionError("不应重转"))
            self.assertEqual(conv.transcribe_image(img, "image/png"), "VIS-OK")

    def test_cache_miss_when_vision_namespace_changes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "a.png"; img.write_bytes(b"\x89PNG fake")
            self.assertEqual(self._conv(tmp, multimodal=False, vision="OLD", namespace="ns-A").transcribe_image(img, "image/png"), "OLD")
            self.assertEqual(self._conv(tmp, multimodal=False, vision="NEW", namespace="ns-B").transcribe_image(img, "image/png"), "NEW")

    def test_dotted_namespace_keys_do_not_collide(self):
        """Fix1: 视觉模型名带点（gpt-4.1 / gpt-4.2）使 cache key 含点。with_suffix 会把
        ...img-visM-gpt-4.1-... 截成 ...img-visM-gpt-4.md → 不同 namespace 撞同一文件。
        string-concat 后两个 key 的 .md 路径必须不同，各自返回自己的转写。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "a.png"; img.write_bytes(b"\x89PNG fake")
            conv_a = self._conv(tmp, multimodal=False, vision="TXT-A", namespace="visM-gpt-4.1")
            conv_b = self._conv(tmp, multimodal=False, vision="TXT-B", namespace="visM-gpt-4.2")
            key_a = conv_a._cache_key(img, extra=conv_a.image_cache_extra)
            key_b = conv_b._cache_key(img, extra=conv_b.image_cache_extra)
            md_a, _ = conv_a._cache_paths(key_a)
            md_b, _ = conv_b._cache_paths(key_b)
            # 两个 key 的 .md（和派生缓存路径）必须互不相同，无碰撞。
            self.assertNotEqual(md_a, md_b)
            self.assertEqual(conv_a.transcribe_image(img, "image/png"), "TXT-A")
            self.assertEqual(conv_b.transcribe_image(img, "image/png"), "TXT-B")
            # 字面 key 在文件名里完整保留（点未被 with_suffix 吞掉）。
            self.assertTrue(md_a.name.endswith("gpt-4.1.md") or "gpt-4.1" in md_a.name)
            self.assertTrue(md_b.name.endswith("gpt-4.2.md") or "gpt-4.2" in md_b.name)

    def test_vision_fail_falls_to_ocr(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "a.png"; img.write_bytes(b"\x89PNG")
            def boom(*a): raise RuntimeError("vision down")
            from backend.material_conversion import MaterialConverter
            conv = MaterialConverter(cache_dir=Path(tmp), vision_adapter=boom,
                ocr_adapter=lambda p: "OCR-FALLBACK", capability_resolver=lambda: False)
            self.assertEqual(conv.transcribe_image(img, "image/png"), "OCR-FALLBACK")

    def test_all_fail_raises(self):
        import tempfile
        from backend.material_conversion import MaterialConversionError
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "a.png"; img.write_bytes(b"\x89PNG")
            def boom(*a): raise RuntimeError("down")
            from backend.material_conversion import MaterialConverter
            conv = MaterialConverter(cache_dir=Path(tmp), vision_adapter=boom,
                ocr_adapter=lambda p: "", capability_resolver=lambda: False)
            with self.assertRaises(MaterialConversionError):
                conv.transcribe_image(img, "image/png")

    def test_transient_data_url_no_persistent_cache(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            from backend.material_conversion import MaterialConverter
            conv = MaterialConverter(cache_dir=Path(tmp)/"cache", vision_adapter=lambda *a: "图说Z",
                                     ocr_adapter=lambda p: "O", capability_resolver=lambda: False)
            out = conv.transcribe_image_data_url("data:image/png;base64,Zg==", "image/png")
            self.assertEqual(out, "图说Z")
            residue = [f for f in os.listdir(Path(tmp)/"cache") if f.endswith((".md", ".error", ".refs"))]
            self.assertEqual(residue, [])


class ConverterHardeningTests(unittest.TestCase):
    def test_markitdown_plugins_disabled_and_no_url_fetch(self):
        import inspect, backend.material_conversion as m
        src = inspect.getsource(m)
        self.assertIn("enable_plugins=False", src)        # markitdown 禁插件
        self.assertNotIn("http://", src)                  # 不内嵌 URL 抓取
        self.assertNotIn("requests.get", src)

    def test_subprocess_calls_have_timeout(self):
        import inspect, backend.material_conversion as m
        src = inspect.getsource(m.MaterialConverter._libreoffice_to_markdown)  # 真实方法名
        self.assertIn("timeout=", src)                    # LibreOffice 带超时

    def test_transcribe_image_data_url_friendly_fails_on_bad_input(self):
        import tempfile
        from backend.material_conversion import MaterialConverter, MaterialConversionError
        with tempfile.TemporaryDirectory() as tmp:
            conv = MaterialConverter(cache_dir=Path(tmp), vision_adapter=lambda *a: "V",
                                     ocr_adapter=lambda p: "O", capability_resolver=lambda: False)
            with self.assertRaises(MaterialConversionError):
                conv.transcribe_image_data_url("data:image/png;base64,!!!notbase64!!!", "image/png")
