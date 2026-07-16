import os
import subprocess
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from backend import report_tools

_W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _write_min_docx(path, *, with_table_auto=False, with_update_fields=False):
    """后处理会真打开 pandoc 产物 zip——mock pandoc 必须写出最小合法 docx。"""
    tbl = '<w:tblW w:type="auto" w:w="0" />' if with_table_auto else ""
    update = '<w:updateFields w:val="true"/>' if with_update_fields else ""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f"<w:document {_W_NS}><w:body>{tbl}</w:body></w:document>")
        z.writestr("word/settings.xml", f"<w:settings {_W_NS}>{update}</w:settings>")
        z.writestr(
            "word/header2.xml",
            f"<w:hdr {_W_NS}><w:p><w:r><w:t>{report_tools._HEADER_TITLE_PLACEHOLDER}</w:t></w:r></w:p></w:hdr>",
        )
        z.writestr("word/media/keep.png", b"\x89PNG-bytes")


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


class BuildExportMarkdownTests(unittest.TestCase):
    """导出预处理纯函数：剥 H1 → 封面 + 目录域 + 正文。"""

    def test_strips_first_h1_and_builds_cover_and_toc(self):
        draft = "# 数字化转型报告\n\n## 执行摘要\n\n正文。\n"
        text, title = report_tools.build_export_markdown(draft, date_label="2026年7月16日")
        self.assertEqual(title, "数字化转型报告")
        self.assertNotIn("# 数字化转型报告", text)
        # 封面走 raw openxml 段落直引模板 styleId（标题不进 markdown 解析）
        self.assertIn('w:val="CoverTitle"', text)
        self.assertIn(">数字化转型报告</w:t>", text)
        self.assertIn("可审草稿", text)
        self.assertIn("2026年7月16日", text)
        self.assertIn('custom-style="TOC Title"', text)
        self.assertIn("目　录", text)
        self.assertIn('TOC \\o "2-4"', text)  # Word 目录域指令
        self.assertIn("## 执行摘要", text)  # 正文保留

    def test_only_first_h1_removed(self):
        draft = "# 标题\n\n正文。\n\n# 附件另一个一级标题\n"
        text, _ = report_tools.build_export_markdown(draft, date_label="d")
        self.assertIn("# 附件另一个一级标题", text)

    def test_h2_not_mistaken_for_title(self):
        draft = "## 不是标题\n\n正文。\n"
        text, title = report_tools.build_export_markdown(draft, date_label="d")
        self.assertEqual(title, "咨询报告")  # 回落标题
        self.assertIn("## 不是标题", text)  # 正文一字不动

    def test_h1_not_at_start_is_body_not_title(self):
        # 只认首个非空行的 H1：正文/代码块里出现的 `# ` 行绝不能被剥去当标题。
        draft = "开头一段说明。\n\n```\n# 注释行\n```\n\n# 后置一级标题\n"
        text, title = report_tools.build_export_markdown(draft, date_label="d")
        self.assertEqual(title, "咨询报告")
        self.assertIn("# 注释行", text)
        self.assertIn("# 后置一级标题", text)

    def test_title_inline_markup_stripped(self):
        draft = "# **粗体**标题[A]\n\n正文。\n"
        text, title = report_tools.build_export_markdown(draft, date_label="d")
        self.assertEqual(title, "粗体标题[A]")
        self.assertIn(">粗体标题[A]</w:t>", text)

    def test_title_markdown_structure_injection_stays_literal(self):
        # codex 红队实证：div 方案下 `# :::` 会提前闭合封面块。raw openxml 方案必须让
        # 行首结构字符全部作为字面文本进 <w:t>。
        draft = "# ::: 恶意标题\n\n正文。\n"
        text, title = report_tools.build_export_markdown(draft, date_label="d")
        self.assertEqual(title, "::: 恶意标题")
        self.assertIn(">::: 恶意标题</w:t>", text)
        self.assertNotIn('custom-style="Cover Title"', text)  # 标题绝不进 div

    def test_title_xml_specials_escaped(self):
        draft = "# 评估<A&B>报告\n\n正文。\n"
        text, title = report_tools.build_export_markdown(draft, date_label="d")
        self.assertEqual(title, "评估<A&B>报告")
        self.assertIn(">评估&lt;A&amp;B&gt;报告</w:t>", text)

    def test_title_xml_illegal_control_chars_removed(self):
        draft = "# 报\x00告\x01标题\n\n正文。\n"
        text, title = report_tools.build_export_markdown(draft, date_label="d")
        self.assertEqual(title, "报告标题")
        self.assertNotIn("\x00", text)

    def test_body_raw_openxml_neutralized(self):
        # 正文可注入活动域（DDEAUTO/INCLUDE*）且 updateFields 会自动执行——正文 raw
        # openxml 必须整类中和；全文允许的 {=openxml} 只剩封面 + TOC 两个可信常量块。
        draft = (
            "# 标题\n\n正文。\n\n"
            '```{=openxml}\n<w:fldSimple w:instr=" DDEAUTO c:\\evil "/>\n```\n\n'
            "行内 `<w:br/>`{=OpenXML} 注入。\n\n"
            "变体 `x`{ = openxml } 注入。\n"
        )
        text, _ = report_tools.build_export_markdown(draft, date_label="d")
        self.assertEqual(text.count("{=openxml}"), 2)  # 封面块 + TOC 块
        self.assertEqual(text.count(report_tools._RAW_OPENXML_NEUTRALIZED), 3)
        # 大小写/空白变体也不许残留：全文能命中 raw-attr 正则的只剩我们的 2 个常量块
        self.assertEqual(len(report_tools._RAW_OPENXML_ATTR_RE.findall(text)), 2)

    def test_default_date_label_is_cn_format(self):
        text, _ = report_tools.build_export_markdown("# t\n\n正文。\n")
        self.assertRegex(text, r"\d{4}年\d{1,2}月\d{1,2}日")


class PostprocessDocxTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.docx = Path(self._tmp.name) / "x.docx"

    def test_header_title_injected_with_xml_escape(self):
        _write_min_docx(self.docx)
        report_tools._postprocess_docx(self.docx, "A&B<报告>")
        with zipfile.ZipFile(self.docx) as z:
            header = z.read("word/header2.xml").decode("utf-8")
        self.assertNotIn(report_tools._HEADER_TITLE_PLACEHOLDER, header)
        self.assertIn("A&amp;B&lt;报告&gt;", header)

    def test_table_auto_width_rewritten_to_full_width(self):
        _write_min_docx(self.docx, with_table_auto=True)
        report_tools._postprocess_docx(self.docx, "t")
        with zipfile.ZipFile(self.docx) as z:
            doc = z.read("word/document.xml").decode("utf-8")
        self.assertNotIn('w:type="auto"', doc)
        self.assertIn('<w:tblW w:type="pct" w:w="5000" />', doc)

    def test_update_fields_inserted_once(self):
        _write_min_docx(self.docx)
        report_tools._postprocess_docx(self.docx, "t")
        with zipfile.ZipFile(self.docx) as z:
            settings = z.read("word/settings.xml").decode("utf-8")
        self.assertEqual(settings.count("updateFields"), 1)

    def test_update_fields_not_duplicated_when_present(self):
        _write_min_docx(self.docx, with_update_fields=True)
        report_tools._postprocess_docx(self.docx, "t")
        with zipfile.ZipFile(self.docx) as z:
            settings = z.read("word/settings.xml").decode("utf-8")
        self.assertEqual(settings.count("updateFields"), 1)

    def test_other_parts_pass_through_unchanged(self):
        _write_min_docx(self.docx)
        report_tools._postprocess_docx(self.docx, "t")
        with zipfile.ZipFile(self.docx) as z:
            self.assertEqual(z.read("word/media/keep.png"), b"\x89PNG-bytes")

    def test_header_title_xml_illegal_chars_removed_and_wellformed(self):
        # 直接调后处理也要挡 XML 非法字符（防调用方漏净化）：产物 header 必须可解析。
        from xml.etree import ElementTree as ET
        _write_min_docx(self.docx)
        report_tools._postprocess_docx(self.docx, "报\x00告\x01")
        with zipfile.ZipFile(self.docx) as z:
            header = z.read("word/header2.xml")
        ET.fromstring(header)  # well-formed
        self.assertIn("报告", header.decode("utf-8"))
        self.assertNotIn(b"\x00", header)


class ReferenceDocResolutionTests(unittest.TestCase):
    def test_repo_template_resolves_in_dev(self):
        # 开发/服务器态 get_base_path()=仓库根 → 入库模板必须能解析到
        path = report_tools._resolve_reference_doc()
        self.assertIsNotNone(path, "templates/docx/consulting_v1.docx 未解析到")
        self.assertTrue(str(path).endswith(str(Path("templates") / "docx" / "consulting_v1.docx")))

    @mock.patch("backend.report_tools.get_base_path")
    def test_missing_template_returns_none(self, m_base):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            m_base.return_value = Path(d)
            self.assertIsNone(report_tools._resolve_reference_doc())


class DocxTemplateIntegrityTests(unittest.TestCase):
    """守护入库模板：意外损坏/误删/关键样式丢失在 CI 即暴露（产物由脚本生成，勿手改）。"""

    def test_checked_in_template_has_required_styles_and_parts(self):
        from xml.etree import ElementTree as ET
        path = Path(__file__).resolve().parents[1] / "templates" / "docx" / "consulting_v1.docx"
        self.assertTrue(path.is_file())
        with zipfile.ZipFile(path) as z:
            self.assertIsNone(z.testzip())  # CRC 完整
            names = set(z.namelist())
            self.assertEqual(len(names), len(z.namelist()))  # 无重复 part
            for name in names:
                if name.endswith(".xml") or name.endswith(".rels"):
                    ET.fromstring(z.read(name))  # 所有 XML part well-formed
            # document.xml.rels 的相对 Target 必须都真实存在（页眉页脚 rel 断链=坏模板）
            rels = ET.fromstring(z.read("word/_rels/document.xml.rels"))
            for rel in rels:
                target = rel.get("Target", "")
                if rel.get("TargetMode") != "External" and not target.startswith("/"):
                    self.assertIn(f"word/{target}", names, f"rels 断链: {target}")
            for part in ("word/header1.xml", "word/header2.xml", "word/footer1.xml", "word/footer2.xml"):
                self.assertIn(part, names)
            styles = z.read("word/styles.xml").decode("utf-8")
            for needle in ("Cover Title", "Cover Subtitle", "Cover Date", "TOC Title",
                           "toc 1", "黑体", "宋体", "firstLineChars"):
                self.assertIn(needle, styles)
            doc = z.read("word/document.xml").decode("utf-8")
            self.assertIn("titlePg", doc)  # 封面页不带页眉页脚
            settings = z.read("word/settings.xml").decode("utf-8")
            self.assertIn("updateFields", settings)
            header = z.read("word/header2.xml").decode("utf-8")
            self.assertIn(report_tools._HEADER_TITLE_PLACEHOLDER, header)


class ExportReviewableDraftTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name) / "output"
        self.report = Path(self._tmp.name) / "report_draft_v1.md"
        self.report.write_text("# 测试报告\n\n正文。", encoding="utf-8")
        # 默认禁用目录固化：让导出主链路测试与真实机器有无 LibreOffice 无关（固化单测另立）
        p = mock.patch("backend.report_tools._resolve_libreoffice", return_value=None)
        p.start()
        self.addCleanup(p.stop)

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
            seen["cmd"] = list(cmd)
            _write_min_docx(o_path)
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
        # 后处理链真跑：页眉标题注入 + updateFields 兜底
        with zipfile.ZipFile(final) as z:
            self.assertIn("测试报告", z.read("word/header2.xml").decode("utf-8"))
            self.assertIn("updateFields", z.read("word/settings.xml").decode("utf-8"))
        # 无残留 temp（docx 与预处理 .md 都要清干净）
        self.assertEqual([p.name for p in self.out.glob("*")], ["report_draft_v1.docx"])

    @mock.patch("backend.report_tools._resolve_pandoc", return_value="/usr/bin/pandoc")
    @mock.patch("backend.report_tools.subprocess.run")
    def test_reference_doc_passed_to_pandoc(self, m_run, m_pandoc):
        seen = {}
        def fake_run(cmd, **kw):
            seen["cmd"] = list(cmd)
            _write_min_docx(Path(cmd[cmd.index("-o") + 1]))
            return mock.Mock(returncode=0, stdout="", stderr="")
        m_run.side_effect = fake_run
        res = report_tools.export_reviewable_draft(str(self.report), str(self.out))
        self.assertEqual(res["status"], "ok")
        cmd = seen["cmd"]
        self.assertIn("--reference-doc", cmd)
        ref = Path(cmd[cmd.index("--reference-doc") + 1])
        self.assertEqual(ref.name, "consulting_v1.docx")
        self.assertIn("已套用标准咨询排版", res["output"])

    @mock.patch("backend.report_tools._fixate_toc_fields", return_value=True)
    @mock.patch("backend.report_tools._resolve_pandoc", return_value="/usr/bin/pandoc")
    @mock.patch("backend.report_tools.subprocess.run")
    def test_message_when_toc_fixated(self, m_run, m_pandoc, m_fix):
        m_run.side_effect = lambda cmd, **kw: (
            _write_min_docx(Path(cmd[cmd.index("-o") + 1])),
            mock.Mock(returncode=0, stdout="", stderr=""))[1]
        res = report_tools.export_reviewable_draft(str(self.report), str(self.out))
        self.assertEqual(res["status"], "ok")
        self.assertIn("目录页码已自动生成", res["output"])
        self.assertNotIn("F9", res["output"])  # 固化成功不该再让用户手动更新

    @mock.patch("backend.report_tools._resolve_pandoc", return_value="/usr/bin/pandoc")
    @mock.patch("backend.report_tools.subprocess.run")
    def test_message_when_not_fixated_guides_wps(self, m_run, m_pandoc):
        # setUp 已禁 LibreOffice → 未固化分支：必须给 WPS 用户手动更新引导
        m_run.side_effect = lambda cmd, **kw: (
            _write_min_docx(Path(cmd[cmd.index("-o") + 1])),
            mock.Mock(returncode=0, stdout="", stderr=""))[1]
        res = report_tools.export_reviewable_draft(str(self.report), str(self.out))
        self.assertEqual(res["status"], "ok")
        self.assertIn("WPS", res["output"])
        self.assertIn("更新域", res["output"])

    @mock.patch("backend.report_tools._resolve_reference_doc", return_value=None)
    @mock.patch("backend.report_tools._resolve_pandoc", return_value="/usr/bin/pandoc")
    @mock.patch("backend.report_tools.subprocess.run")
    def test_missing_template_degrades_without_reference_doc(self, m_run, m_pandoc, m_ref):
        # 模板缺失（部署遗漏）不阻断导出：不带 --reference-doc、结果提示基础样式。
        seen = {}
        def fake_run(cmd, **kw):
            seen["cmd"] = list(cmd)
            _write_min_docx(Path(cmd[cmd.index("-o") + 1]))
            return mock.Mock(returncode=0, stdout="", stderr="")
        m_run.side_effect = fake_run
        res = report_tools.export_reviewable_draft(str(self.report), str(self.out))
        self.assertEqual(res["status"], "ok")
        self.assertNotIn("--reference-doc", seen["cmd"])
        self.assertIn("基础样式", res["output"])

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
        self.assertEqual([p.name for p in self.out.glob("*")], ["report_draft_v1.docx"])  # temp 清掉

    @mock.patch("backend.report_tools._resolve_pandoc", return_value="/usr/bin/pandoc")
    @mock.patch("backend.report_tools.subprocess.run")
    def test_corrupt_pandoc_output_fails_and_cleans_temp(self, m_run, m_pandoc):
        # pandoc rc=0 但产物不是合法 zip（极端）→ 后处理 BadZipFile → 友好失败 + 清 temp。
        def fake_run(cmd, **kw):
            Path(cmd[cmd.index("-o") + 1]).write_text("not-a-zip")
            return mock.Mock(returncode=0, stdout="", stderr="")
        m_run.side_effect = fake_run
        res = report_tools.export_reviewable_draft(str(self.report), str(self.out))
        self.assertEqual(res["status"], "error")
        self.assertIn("导出失败", res["output"])
        self.assertEqual([p.name for p in self.out.glob("*")], [])

    @mock.patch("backend.report_tools._resolve_pandoc", return_value=None)
    def test_no_pandoc_friendly_error(self, m_pandoc):
        res = report_tools.export_reviewable_draft(str(self.report), str(self.out))
        self.assertEqual(res["status"], "error")
        self.assertIn("pandoc", res["output"])

    @mock.patch("backend.report_tools._resolve_pandoc", return_value="/usr/bin/pandoc")
    def test_second_mkstemp_failure_cleans_first_temp(self, m_pandoc):
        # codex B4：预处理 .md 的 mkstemp 失败不得泄漏已创建的 .docx temp。
        real_mkstemp = report_tools.tempfile.mkstemp
        calls = {"n": 0}

        def flaky_mkstemp(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise OSError("no space")
            return real_mkstemp(*args, **kwargs)

        with mock.patch("backend.report_tools.tempfile.mkstemp", side_effect=flaky_mkstemp):
            res = report_tools.export_reviewable_draft(str(self.report), str(self.out))
        self.assertEqual(res["status"], "error")
        self.assertEqual(list(self.out.glob("*")), [])  # 第一份 temp 不残留

    @mock.patch("backend.report_tools._resolve_pandoc", return_value="/usr/bin/pandoc")
    @mock.patch("backend.report_tools.subprocess.run")
    def test_unexpected_error_cleans_temps_and_keeps_old_final(self, m_run, m_pandoc):
        # 白名单外的意外异常：允许上抛（endpoint 500），但 temp 必清、旧终名必保。
        final = self.out / "report_draft_v1.docx"
        self.out.mkdir(parents=True, exist_ok=True)
        final.write_text("OLD")

        def fake_run(cmd, **kw):
            _write_min_docx(Path(cmd[cmd.index("-o") + 1]))
            return mock.Mock(returncode=0, stdout="", stderr="")

        m_run.side_effect = fake_run
        with mock.patch("backend.report_tools._postprocess_docx", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                report_tools.export_reviewable_draft(str(self.report), str(self.out))
        self.assertEqual(final.read_text(), "OLD")
        self.assertEqual([p.name for p in self.out.glob("*")], ["report_draft_v1.docx"])


def _min_docx_bytes():
    """最小合法 docx（供固化产物校验测试用）：ZIP + 关键 part + 可解析 document.xml。"""
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        z.writestr("word/document.xml", f"<w:document {_W_NS}><w:body/></w:document>")
    return buf.getvalue()


class TocFixationTests(unittest.TestCase):
    """目录固化（LibreOffice）：尽力而为、失败静默降级、绝不让导出报错。"""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.docx = Path(self._tmp.name) / "x.docx"
        self.docx.write_text("ORIGINAL")  # 占位内容，验证是否被替换
        report_tools._uno_python_cache.clear()
        self.addCleanup(report_tools._uno_python_cache.clear)

    def _fake_popen(self, *, rc=0, out_bytes=None, stderr="", timeout=False):
        """构造 mock Popen：communicate 写 out.docx（argv[-2]）并返回 rc。"""
        def factory(cmd, **kw):
            m = mock.Mock()
            out_path = Path(cmd[-2])  # argv: [py, script, soffice, IN, OUT, PROFILE]

            def communicate(timeout=None):
                if timeout is not False and factory._raise_timeout:
                    factory._raise_timeout = False
                    raise subprocess.TimeoutExpired(cmd, 90)
                if out_bytes is not None:
                    out_path.write_bytes(out_bytes)
                return ("", stderr)

            m.communicate.side_effect = communicate
            m.returncode = rc
            m.poll.return_value = rc  # 已结束
            m.pid = 4242
            return m
        factory._raise_timeout = timeout
        return factory

    @mock.patch("backend.report_tools._resolve_libreoffice", return_value=None)
    def test_skip_when_no_libreoffice(self, m_lo):
        with mock.patch("backend.report_tools.subprocess.Popen") as m_popen:
            self.assertFalse(report_tools._fixate_toc_fields(self.docx))
            m_popen.assert_not_called()  # 无 LibreOffice：连 helper 都不起
        self.assertEqual(self.docx.read_text(), "ORIGINAL")

    @mock.patch("backend.report_tools._resolve_uno_python", return_value=None)
    @mock.patch("backend.report_tools._resolve_libreoffice", return_value="/usr/bin/soffice")
    def test_skip_when_no_uno_python(self, m_lo, m_uno):
        self.assertFalse(report_tools._fixate_toc_fields(self.docx))
        self.assertEqual(self.docx.read_text(), "ORIGINAL")

    @mock.patch("backend.report_tools._resolve_libreoffice", return_value="/usr/bin/soffice")
    def test_frozen_skips_fixation(self, m_lo):
        with mock.patch("backend.report_tools.sys") as m_sys:
            m_sys.frozen = True
            m_sys.platform = "linux"
            self.assertFalse(report_tools._fixate_toc_fields(self.docx))

    @mock.patch("backend.report_tools._resolve_uno_python", return_value="/usr/bin/python3")
    @mock.patch("backend.report_tools._resolve_libreoffice", return_value="/usr/bin/soffice")
    def test_success_replaces_docx_with_valid_output(self, m_lo, m_uno):
        # 固化助手 argv 形状 + soffice 路径透传 + 合法 docx 产物才替换
        seen = {}
        factory = self._fake_popen(rc=0, out_bytes=_min_docx_bytes())
        def spy(cmd, **kw):
            seen["cmd"] = list(cmd)
            seen["kw"] = kw
            return factory(cmd, **kw)
        with mock.patch("backend.report_tools.subprocess.Popen", side_effect=spy):
            self.assertTrue(report_tools._fixate_toc_fields(self.docx))
        self.assertEqual(seen["cmd"][0], "/usr/bin/python3")
        self.assertEqual(seen["cmd"][2], "/usr/bin/soffice")  # B4：soffice 路径真传给 helper
        self.assertTrue(seen["kw"].get("start_new_session"))  # B1：自成进程组才能 killpg
        with zipfile.ZipFile(self.docx) as z:  # 已被合法 docx 替换
            self.assertIn("word/document.xml", z.namelist())

    @mock.patch("backend.report_tools._resolve_uno_python", return_value="/usr/bin/python3")
    @mock.patch("backend.report_tools._resolve_libreoffice", return_value="/usr/bin/soffice")
    def test_corrupt_output_rejected_keeps_original(self, m_lo, m_uno):
        # codex B3：rc=0 但产物不是合法 docx（纯文本）→ 拒绝、保留原 docx
        factory = self._fake_popen(rc=0, out_bytes=b"not-a-docx")
        with mock.patch("backend.report_tools.subprocess.Popen", side_effect=factory):
            self.assertFalse(report_tools._fixate_toc_fields(self.docx))
        self.assertEqual(self.docx.read_text(), "ORIGINAL")

    @mock.patch("backend.report_tools._resolve_uno_python", return_value="/usr/bin/python3")
    @mock.patch("backend.report_tools._resolve_libreoffice", return_value="/usr/bin/soffice")
    def test_nonzero_rc_keeps_original_and_cleans_work(self, m_lo, m_uno):
        factory = self._fake_popen(rc=5, out_bytes=_min_docx_bytes(), stderr="boom")
        with mock.patch("backend.report_tools.subprocess.Popen", side_effect=factory):
            self.assertFalse(report_tools._fixate_toc_fields(self.docx))
        self.assertEqual(self.docx.read_text(), "ORIGINAL")
        # work 目录（cra-fixate-*）已 rmtree，无残留
        self.assertEqual([p.name for p in self.docx.parent.iterdir()], ["x.docx"])

    @mock.patch("backend.report_tools._terminate_process_group")
    @mock.patch("backend.report_tools._resolve_uno_python", return_value="/usr/bin/python3")
    @mock.patch("backend.report_tools._resolve_libreoffice", return_value="/usr/bin/soffice")
    def test_timeout_killpg_and_keeps_original(self, m_lo, m_uno, m_kill):
        factory = self._fake_popen(rc=0, out_bytes=_min_docx_bytes(), timeout=True)
        with mock.patch("backend.report_tools.subprocess.Popen", side_effect=factory):
            self.assertFalse(report_tools._fixate_toc_fields(self.docx))
        m_kill.assert_called()  # B1：超时必 killpg 整组（含 soffice 孙进程）
        self.assertEqual(self.docx.read_text(), "ORIGINAL")
        self.assertEqual([p.name for p in self.docx.parent.iterdir()], ["x.docx"])

    @mock.patch("backend.report_tools._resolve_uno_python", return_value="/usr/bin/python3")
    @mock.patch("backend.report_tools._resolve_libreoffice", return_value="/usr/bin/soffice")
    def test_never_raises_on_unexpected_error(self, m_lo, m_uno):
        # 兜底：Popen 本身抛（非 OSError/Timeout）也必须吞成 False、不影响导出
        with mock.patch("backend.report_tools.subprocess.Popen", side_effect=RuntimeError("boom")):
            self.assertFalse(report_tools._fixate_toc_fields(self.docx))
        self.assertEqual(self.docx.read_text(), "ORIGINAL")

    def test_resolve_uno_python_positive_cache_only(self):
        report_tools._uno_python_cache.clear()
        calls = []

        def fake_run(cmd, **kw):
            calls.append(list(cmd))
            ok = cmd[0] == "/usr/bin/python3"  # 第一个候选失败，第二个成功
            return mock.Mock(returncode=0 if ok else 1)

        with mock.patch("backend.report_tools.shutil.which", return_value="/opt/venv/python3"), \
             mock.patch("backend.report_tools.os.environ", {}), \
             mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("backend.report_tools.subprocess.run", side_effect=fake_run):
            self.assertEqual(report_tools._resolve_uno_python(), "/usr/bin/python3")
            calls.clear()
            self.assertEqual(report_tools._resolve_uno_python(), "/usr/bin/python3")
            self.assertEqual(calls, [])  # 正缓存命中，不再探测

    def test_resolve_uno_python_negative_not_cached(self):
        # 失败不缓存：下次仍探测（避免瞬态失败永久压住真实可用路径，codex NIT）
        report_tools._uno_python_cache.clear()
        n = {"probes": 0}

        def fake_run(cmd, **kw):
            n["probes"] += 1
            return mock.Mock(returncode=1)  # 全部 import uno 失败

        with mock.patch("backend.report_tools.shutil.which", return_value="/usr/bin/python3"), \
             mock.patch("backend.report_tools.os.environ", {}), \
             mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("backend.report_tools.subprocess.run", side_effect=fake_run):
            self.assertIsNone(report_tools._resolve_uno_python())
            first = n["probes"]
            self.assertIsNone(report_tools._resolve_uno_python())
            self.assertGreater(n["probes"], first)  # 第二次又探测了（没缓存 None）

    def test_looks_like_docx(self):
        good = Path(self._tmp.name) / "good.docx"
        good.write_bytes(_min_docx_bytes())
        self.assertTrue(report_tools._looks_like_docx(good))
        bad = Path(self._tmp.name) / "bad.docx"
        bad.write_text("not a zip")
        self.assertFalse(report_tools._looks_like_docx(bad))


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
        p = mock.patch("backend.report_tools._resolve_libreoffice", return_value=None)
        p.start()
        self.addCleanup(p.stop)

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
            _write_min_docx(Path(cmd[cmd.index("-o") + 1]))
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
            _write_min_docx(Path(cmd[cmd.index("-o") + 1]))
            return mock.Mock(returncode=0, stdout="", stderr="")

        m_run.side_effect = fake_run
        res = report_tools.export_reviewable_draft(str(self.report), str(self.out))
        self.assertEqual(res["status"], "ok")

    def test_real_pandoc_end_to_end(self):
        # 全链 make-or-break：真实 pandoc 验证 PNG 嵌入 + 模板样式/封面/目录/页眉全链
        # （无 pandoc 自动跳过）。
        import shutil
        if not shutil.which("pandoc"):
            self.skipTest("pandoc not installed")
        from backend.chart_render import render_chart
        png = render_chart(
            "bar", {"categories": ["甲", "乙"], "values": [3, 5]}, "乙类占比更高", "测试来源",
        )
        (self.content / "assets" / "chart-real.png").write_bytes(png)
        self.report.write_text(
            "# 端到端验证报告\n\n## 1. 结论\n\n结论段。\n\n"
            "![乙类占比更高](assets/chart-real.png)\n\n"
            "| 指标 | 数值 |\n| --- | --- |\n| A | 1 |\n\n"
            '```{=openxml}\n<w:fldSimple w:instr=" DDEAUTO c:\\evil "/>\n```\n',
            encoding="utf-8",
        )
        res = report_tools.export_reviewable_draft(str(self.report), str(self.out))
        self.assertEqual(res["status"], "ok")
        with zipfile.ZipFile(res["output_path"]) as docx:
            media = [n for n in docx.namelist() if n.startswith("word/media/")]
            self.assertTrue(media, "docx 内无嵌入图片 media")
            doc = docx.read("word/document.xml").decode("utf-8")
            self.assertIn('w:val="CoverTitle"', doc)  # 封面样式已套用
            self.assertNotIn('w:val="Heading1"', doc)  # H1 已剥离作封面
            self.assertIn('<w:tblW w:type="pct" w:w="5000" />', doc)  # 表格拉满行宽
            # 正文注入的活动域被中和为惰性文本：产物里绝无 fldSimple/DDEAUTO 域结构，
            # 唯一的域是我们自己的 TOC。
            self.assertNotIn("<w:fldSimple", doc)
            self.assertEqual(doc.count("<w:instrText"), 1)
            self.assertIn("TOC", doc)  # 目录域存在
            header = docx.read("word/header2.xml").decode("utf-8")
            self.assertIn("端到端验证报告", header)  # 页眉标题注入
            self.assertIn("updateFields", docx.read("word/settings.xml").decode("utf-8"))
