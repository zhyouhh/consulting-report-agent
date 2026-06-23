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
