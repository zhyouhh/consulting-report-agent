import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from backend import report_tools


class ResolvePandocTests(unittest.TestCase):
    @mock.patch("backend.report_tools.shutil.which", return_value="/usr/bin/pandoc")
    @mock.patch("backend.report_tools.get_base_path")
    @mock.patch("backend.report_tools.sys")
    def test_non_windows_skips_bundled_exe_even_when_root_exe_exists(self, m_sys, m_base, m_which):
        # 守卫核心锁：仓库根真放一个 pandoc.exe，Linux 非 frozen 必须跳过它走系统 pandoc。
        # （破掉守卫的实现——非 Windows 也试 .exe——会返回 .exe 路径，本测试即失败。）
        import tempfile
        m_sys.platform = "linux"
        del m_sys.frozen  # getattr(sys, "frozen", False) → False
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "pandoc.exe").write_text("x")
            m_base.return_value = Path(d)
            self.assertEqual(report_tools._resolve_pandoc(), "/usr/bin/pandoc")
            m_which.assert_called_once_with("pandoc")

    @mock.patch("backend.report_tools.shutil.which", return_value="/usr/bin/pandoc")
    @mock.patch("backend.report_tools.get_base_path")
    @mock.patch("backend.report_tools.sys")
    def test_windows_prefers_bundled_exe_over_system(self, m_sys, m_base, m_which):
        # 即便系统 pandoc 存在（which 返回路径），Windows/打包态也优先包内 pandoc.exe。
        import tempfile
        m_sys.platform = "win32"
        del m_sys.frozen
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "pandoc.exe").write_text("x")
            m_base.return_value = Path(d)
            self.assertEqual(report_tools._resolve_pandoc(), str(Path(d) / "pandoc.exe"))

    @mock.patch("backend.report_tools.shutil.which", return_value="/usr/bin/pandoc")
    @mock.patch("backend.report_tools.get_base_path")
    @mock.patch("backend.report_tools.sys")
    def test_frozen_prefers_bundled_exe_even_on_non_windows(self, m_sys, m_base, m_which):
        # spec：打包态（sys.frozen）即便非 Windows 也优先包内 pandoc.exe（锁 frozen 分支，
        # 与 win32 分支独立）。
        import tempfile
        m_sys.platform = "linux"
        m_sys.frozen = True
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "pandoc.exe").write_text("x")
            m_base.return_value = Path(d)
            self.assertEqual(report_tools._resolve_pandoc(), str(Path(d) / "pandoc.exe"))

    @mock.patch("backend.report_tools.shutil.which", return_value=None)
    @mock.patch("backend.report_tools.get_base_path")
    @mock.patch("backend.report_tools.sys")
    def test_no_pandoc_returns_none(self, m_sys, m_base, m_which):
        import tempfile
        m_sys.platform = "linux"
        del m_sys.frozen
        with tempfile.TemporaryDirectory() as d:
            m_base.return_value = Path(d)  # 根目录无 pandoc.exe
            self.assertIsNone(report_tools._resolve_pandoc())


class ExportReviewableDraftTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name) / "output"
        self.report = Path(self._tmp.name) / "report_draft_v1.md"
        self.report.write_text("# t", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    @mock.patch("backend.report_tools._resolve_pandoc", return_value="/usr/bin/pandoc")
    @mock.patch("backend.report_tools.subprocess.run")
    def test_atomic_publish_replaces_final_only_on_success(self, m_run, m_pandoc):
        # pandoc 写同目录唯一 temp.docx（非直接写终名），成功后 os.replace 到终名。
        seen = {}
        def fake_run(cmd, **kw):
            o_path = Path(cmd[cmd.index("-o") + 1])
            seen["o"] = o_path
            o_path.write_text("docx-bytes")
            return mock.Mock(returncode=0, stdout="", stderr="")
        m_run.side_effect = fake_run
        res = report_tools.export_reviewable_draft(str(self.report), str(self.out))
        self.assertEqual(res["status"], "ok")
        final = self.out / "report_draft_v1.docx"
        # 锁死 temp + os.replace 机制：pandoc 的 -o 必须是 output 目录内的 temp 文件、且不是终名
        # （直接写终名的实现会让以下两条断言失败）。
        self.assertEqual(seen["o"].parent, self.out)
        self.assertNotEqual(seen["o"], final)
        self.assertEqual(res["output_path"], str(final))
        self.assertEqual(res["filename"], "report_draft_v1.docx")
        self.assertTrue(final.exists())
        self.assertEqual(final.read_text(), "docx-bytes")  # temp 内容经 replace 成为终名
        # 无残留 temp
        self.assertEqual([p.name for p in self.out.glob("*.docx")], ["report_draft_v1.docx"])

    @mock.patch("backend.report_tools._resolve_pandoc", return_value="/usr/bin/pandoc")
    @mock.patch("backend.report_tools.subprocess.run")
    def test_pandoc_failure_keeps_old_final_and_cleans_temp(self, m_run, m_pandoc):
        final = self.out / "report_draft_v1.docx"
        self.out.mkdir(parents=True, exist_ok=True)
        final.write_text("OLD")
        m_run.return_value = mock.Mock(returncode=1, stdout="", stderr="boom")
        res = report_tools.export_reviewable_draft(str(self.report), str(self.out))
        self.assertEqual(res["status"], "error")
        self.assertIn("boom", res["output"])
        self.assertEqual(final.read_text(), "OLD")  # 旧文件不动
        self.assertEqual([p.name for p in self.out.glob("*.docx")], ["report_draft_v1.docx"])  # temp 清掉

    @mock.patch("backend.report_tools._resolve_pandoc", return_value=None)
    def test_no_pandoc_friendly_error(self, m_pandoc):
        res = report_tools.export_reviewable_draft(str(self.report), str(self.out))
        self.assertEqual(res["status"], "error")
        self.assertIn("pandoc", res["output"])


class ExportChartAssetTests(unittest.TestCase):
    """图表 spec §4.6：导出前 asset 硬校验 + --resource-path 嵌图。"""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.content = Path(self._tmp.name) / "content"
        (self.content / "assets").mkdir(parents=True)
        self.out = Path(self._tmp.name) / "output"
        self.report = self.content / "report_draft_v1.md"

    @mock.patch("backend.report_tools._resolve_pandoc", return_value="/usr/bin/pandoc")
    @mock.patch("backend.report_tools.subprocess.run")
    def test_missing_asset_fails_with_list_before_pandoc(self, m_run, m_pandoc):
        # pandoc 缺图可能 rc=0 只告警 → 产出静默丢图的 docx，不可接受；缺失必须带清单失败。
        self.report.write_text("![a](assets/chart-gone.png)", encoding="utf-8")
        res = report_tools.export_reviewable_draft(str(self.report), str(self.out))
        self.assertEqual(res["status"], "error")
        self.assertIn("assets/chart-gone.png", res["output"])
        m_run.assert_not_called()  # 根本不进 pandoc

    @mock.patch("backend.report_tools._resolve_pandoc", return_value="/usr/bin/pandoc")
    @mock.patch("backend.report_tools.subprocess.run")
    def test_present_assets_pass_and_resource_path_added(self, m_run, m_pandoc):
        (self.content / "assets" / "chart-ok.png").write_bytes(b"\x89PNG")
        self.report.write_text("![a](assets/chart-ok.png)", encoding="utf-8")

        def fake_run(cmd, **kw):
            Path(cmd[cmd.index("-o") + 1]).write_text("docx")
            # --resource-path 指向草稿父目录（content/），相对引用才解析命中
            self.assertIn("--resource-path", cmd)
            self.assertEqual(cmd[cmd.index("--resource-path") + 1], str(self.content))
            return mock.Mock(returncode=0, stdout="", stderr="")

        m_run.side_effect = fake_run
        res = report_tools.export_reviewable_draft(str(self.report), str(self.out))
        self.assertEqual(res["status"], "ok")

    @mock.patch("backend.report_tools._resolve_pandoc", return_value="/usr/bin/pandoc")
    @mock.patch("backend.report_tools.subprocess.run")
    def test_chartless_report_no_regression(self, m_run, m_pandoc):
        self.report.write_text("# 无图报告\n\n正文。", encoding="utf-8")

        def fake_run(cmd, **kw):
            Path(cmd[cmd.index("-o") + 1]).write_text("docx")
            return mock.Mock(returncode=0, stdout="", stderr="")

        m_run.side_effect = fake_run
        res = report_tools.export_reviewable_draft(str(self.report), str(self.out))
        self.assertEqual(res["status"], "ok")

    def test_real_pandoc_embeds_png_media(self):
        # 全链 make-or-break：真实 pandoc 时验证 PNG 真进 docx media（无 pandoc 自动跳过）。
        import shutil
        import zipfile
        if not shutil.which("pandoc"):
            self.skipTest("pandoc not installed")
        from backend.chart_render import render_chart
        png = render_chart(
            "bar", {"categories": ["甲", "乙"], "values": [3, 5]}, "乙类占比更高", "测试来源",
        )
        (self.content / "assets" / "chart-real.png").write_bytes(png)
        self.report.write_text(
            "# 报告\n\n结论段。\n\n![乙类占比更高](assets/chart-real.png)\n", encoding="utf-8",
        )
        res = report_tools.export_reviewable_draft(str(self.report), str(self.out))
        self.assertEqual(res["status"], "ok")
        with zipfile.ZipFile(res["output_path"]) as docx:
            media = [n for n in docx.namelist() if n.startswith("word/media/")]
            self.assertTrue(media, "docx 内无嵌入图片 media")
