"""图表资产层测试：原子写 / 引用扫描契约 / 孤儿清扫 / 缺图清单 / sidecar 注入。"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from backend import chart_assets


class WriteChartAssetTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name)

    def test_write_creates_png_and_sidecar_pair(self):
        rel = chart_assets.write_chart_asset(self.project, "chart-abc", b"png-bytes", {"kind": "bar"})
        self.assertEqual(rel, "content/assets/chart-abc.png")
        png = self.project / "content" / "assets" / "chart-abc.png"
        sidecar = self.project / "content" / "assets" / "chart-abc.json"
        self.assertEqual(png.read_bytes(), b"png-bytes")
        self.assertEqual(json.loads(sidecar.read_text(encoding="utf-8"))["kind"], "bar")
        # 无残留 temp（原子写）
        self.assertEqual(
            sorted(p.suffix for p in png.parent.iterdir()), [".json", ".png"],
        )

    def test_oversized_sidecar_trimmed_not_failed(self):
        big = {"kind": "bar", "title": "t", "source": "s", "data": "x" * (chart_assets.MAX_SIDECAR_BYTES + 100)}
        chart_assets.write_chart_asset(self.project, "chart-big", b"p", big)
        sidecar = json.loads(
            (self.project / "content" / "assets" / "chart-big.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("data", sidecar)
        self.assertIn("note", sidecar)

    def test_new_chart_id_shape(self):
        cid = chart_assets.new_chart_id()
        self.assertRegex(cid, r"^chart-[0-9a-f]{12}$")
        self.assertNotEqual(cid, chart_assets.new_chart_id())  # 每次铸新


class ScanReferencesTests(unittest.TestCase):
    """引用扫描契约（图表 spec §4.3，逐条钉死）。"""

    def test_markdown_image(self):
        self.assertEqual(
            chart_assets.scan_chart_references("前文 ![结论](assets/chart-a1.png) 后文"),
            {"chart-a1.png"},
        )

    def test_markdown_image_with_title_and_angle_brackets(self):
        text = '![a](assets/chart-a1.png "标题") 与 ![b](<assets/chart-b2.png>)'
        self.assertEqual(
            chart_assets.scan_chart_references(text), {"chart-a1.png", "chart-b2.png"},
        )

    def test_raw_html_img(self):
        text = '<img src="assets/chart-c3.png" alt="x"> 与 <IMG SRC=\'assets/chart-d4.png\'>'
        self.assertEqual(
            chart_assets.scan_chart_references(text), {"chart-c3.png", "chart-d4.png"},
        )

    def test_query_and_fragment_stripped(self):
        self.assertEqual(
            chart_assets.scan_chart_references("![a](assets/chart-a1.png?v=3#frag)"),
            {"chart-a1.png"},
        )

    def test_url_encoded_path_normalized(self):
        self.assertEqual(
            chart_assets.scan_chart_references("![a](assets/chart%2Da1.png)"),
            {"chart-a1.png"},
        )

    def test_dot_slash_and_content_prefix_tolerated(self):
        text = "![a](./assets/chart-a1.png) ![b](content/assets/chart-b2.png)"
        self.assertEqual(
            chart_assets.scan_chart_references(text), {"chart-a1.png", "chart-b2.png"},
        )

    def test_duplicate_references_dedupe(self):
        text = "![a](assets/chart-a1.png)\n![again](assets/chart-a1.png)"
        self.assertEqual(chart_assets.scan_chart_references(text), {"chart-a1.png"})

    def test_absolute_and_data_urls_ignored(self):
        text = ("![x](https://example.com/assets/chart-a1.png) "
                "![y](data:image/png;base64,AAA) ![z](/assets/chart-b2.png)")
        self.assertEqual(chart_assets.scan_chart_references(text), set())

    def test_traversal_names_ignored(self):
        text = "![x](assets/../secret.png) ![y](assets/sub/inner.png)"
        self.assertEqual(chart_assets.scan_chart_references(text), set())

    def test_non_assets_relative_ignored(self):
        self.assertEqual(chart_assets.scan_chart_references("![x](images/chart-a1.png)"), set())

    def test_empty_text(self):
        self.assertEqual(chart_assets.scan_chart_references(""), set())


class MissingAssetsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.content = Path(self._tmp.name) / "content"
        (self.content / "assets").mkdir(parents=True)
        self.report = self.content / "report_draft_v1.md"

    def test_missing_listed_present_not(self):
        (self.content / "assets" / "chart-ok.png").write_bytes(b"p")
        self.report.write_text(
            "![a](assets/chart-ok.png)\n![b](assets/chart-gone.png)", encoding="utf-8",
        )
        self.assertEqual(
            chart_assets.list_missing_assets(self.report), ["assets/chart-gone.png"],
        )

    def test_no_references_no_missing(self):
        self.report.write_text("无图正文", encoding="utf-8")
        self.assertEqual(chart_assets.list_missing_assets(self.report), [])

    def test_unreadable_report_returns_empty(self):
        self.assertEqual(
            chart_assets.list_missing_assets(self.content / "nonexistent.md"), [],
        )


class SweepOrphanTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name)
        self.assets = self.project / "content" / "assets"
        self.assets.mkdir(parents=True)

    def _make_asset(self, name: str, *, age_seconds: float = 0):
        png = self.assets / f"{name}.png"
        png.write_bytes(b"p")
        (self.assets / f"{name}.json").write_text("{}", encoding="utf-8")
        if age_seconds:
            old = time.time() - age_seconds
            os.utime(png, (old, old))
        return png

    def test_referenced_asset_never_removed(self):
        self._make_asset("chart-ref", age_seconds=99999)
        removed = chart_assets.sweep_orphan_assets(self.project, "![a](assets/chart-ref.png)")
        self.assertEqual(removed, [])
        self.assertTrue((self.assets / "chart-ref.png").exists())

    def test_young_orphan_protected_by_grace(self):
        self._make_asset("chart-young")  # 刚生成、还没插入（在途图）
        removed = chart_assets.sweep_orphan_assets(self.project, "")
        self.assertEqual(removed, [])
        self.assertTrue((self.assets / "chart-young.png").exists())

    def test_old_orphan_removed_with_sidecar(self):
        self._make_asset("chart-old", age_seconds=chart_assets.SWEEP_GRACE_SECONDS + 60)
        removed = chart_assets.sweep_orphan_assets(self.project, "无引用正文")
        self.assertEqual(removed, ["chart-old.png"])
        self.assertFalse((self.assets / "chart-old.png").exists())
        self.assertFalse((self.assets / "chart-old.json").exists())

    def test_missing_assets_dir_no_raise(self):
        empty_project = Path(self._tmp.name) / "other"
        empty_project.mkdir()
        self.assertEqual(chart_assets.sweep_orphan_assets(empty_project, ""), [])

    def test_foreign_files_untouched(self):
        # 非本管线命名的文件（用户手放的）不动
        foreign = self.assets / "手动放的图.png"
        foreign.write_bytes(b"p")
        old = time.time() - 99999
        os.utime(foreign, (old, old))
        chart_assets.sweep_orphan_assets(self.project, "")
        self.assertTrue(foreign.exists())


class LoadSidecarsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name)
        self.assets = self.project / "content" / "assets"
        self.assets.mkdir(parents=True)

    def test_loads_only_referenced_with_budget(self):
        (self.assets / "chart-a.json").write_text('{"kind":"bar"}', encoding="utf-8")
        (self.assets / "chart-b.json").write_text('{"kind":"pie"}', encoding="utf-8")
        draft = "![a](assets/chart-a.png)"
        result = chart_assets.load_referenced_sidecars(self.project, draft)
        self.assertEqual([r["name"] for r in result], ["chart-a.png"])
        self.assertIn("bar", result[0]["text"])

    def test_budget_truncation_marks_overflow(self):
        (self.assets / "chart-a.json").write_text("x" * 100, encoding="utf-8")
        (self.assets / "chart-b.json").write_text("y" * 100, encoding="utf-8")
        draft = "![a](assets/chart-a.png) ![b](assets/chart-b.png)"
        result = chart_assets.load_referenced_sidecars(self.project, draft, max_total_chars=150)
        self.assertEqual(len(result), 2)
        self.assertIn("略", result[1]["text"])

    def test_missing_sidecar_skipped(self):
        result = chart_assets.load_referenced_sidecars(self.project, "![a](assets/chart-none.png)")
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
