import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as xml_escape

from . import chart_assets
from .config import get_base_path

# 导出排版模板（pandoc reference-doc），由 scripts/build_docx_reference.py 生成入库。
# 打包态经 PyInstaller datas 收进 _internal/templates/docx/，两态相对路径一致。
_REFERENCE_DOC_RELPATH = Path("templates") / "docx" / "consulting_v1.docx"

# 页眉标题占位符：与 scripts/build_docx_reference.py 中 HEADER_TITLE_PLACEHOLDER 一致，
# 导出后处理时替换为报告标题。
_HEADER_TITLE_PLACEHOLDER = "{{REPORT_TITLE}}"

_EXPORT_FALLBACK_TITLE = "咨询报告"
_COVER_SUBTITLE = "可审草稿"

# Word 目录域：\o "2-4" 对应正文 ##/###/#### 层级（H1 是报告标题、被剥去做封面）。
# w:dirty + 模板 settings.xml 的 updateFields 双保险，Word/WPS 打开时刷新目录与页码。
# ⚠️ 全文允许的 raw openxml 只有这里与封面段落（可信常量）——正文侧一律中和（见
# _neutralize_raw_openxml），否则恶意正文可注入 DDEAUTO/INCLUDE* 活动域并被
# updateFields 自动执行。
_TOC_FIELD_OPENXML = """```{=openxml}
<w:p>
  <w:r><w:fldChar w:fldCharType="begin" w:dirty="true"/></w:r>
  <w:r><w:instrText xml:space="preserve"> TOC \\o "2-4" \\h \\z \\u </w:instrText></w:r>
  <w:r><w:fldChar w:fldCharType="separate"/></w:r>
  <w:r><w:t>（目录将在打开文档时自动更新）</w:t></w:r>
  <w:r><w:fldChar w:fldCharType="end"/></w:r>
</w:p>
```"""

_H1_LINE_RE = re.compile(r"^#\s+(.+?)\s*$")
_INLINE_MARKUP_RE = re.compile(r"[*_`]+")
# 正文（模型/用户可控）里的 raw openxml 属性一律中和成惰性写法：`{-openxml-}` 不是
# 合法 raw attribute，pandoc 会当普通代码块/文本处理。大小写与空白变体都要拦。
_RAW_OPENXML_ATTR_RE = re.compile(r"\{\s*=\s*openxml\s*\}", re.IGNORECASE)
_RAW_OPENXML_NEUTRALIZED = "{-openxml-}"
# XML 1.0 非法字符（NUL/控制符/孤立代理/FFFE-FFFF）：xml_escape 不处理，混进页眉/封面
# 会产出 Word 打不开的坏 docx，必须在源头剥除。
_XML_ILLEGAL_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff￾￿]")
# pandoc 对 GFM 表格写死 <w:tblW w:type="auto"/>，模板样式盖不动；后处理改 100% 行宽。
_TBLW_AUTO_RE = re.compile(rb'<w:tblW w:type="auto" w:w="0"\s*/>')
_TBLW_FULL_WIDTH = b'<w:tblW w:type="pct" w:w="5000" />'


def _resolve_pandoc() -> str | None:
    """解析 pandoc 可执行：仅打包态/Windows 才优先包内 pandoc.exe（防 Linux 误 exec
    Windows 二进制——WINDOWS_BUILD.md 要求维护者把 pandoc.exe 放仓库根，而 get_base_path()
    开发/服务器态=仓库根），否则走系统 pandoc。"""
    if getattr(sys, "frozen", False) or sys.platform == "win32":
        base = get_base_path()
        for candidate in (base / "pandoc.exe", base / "pandoc" / "pandoc.exe"):
            if candidate.is_file():
                return str(candidate)
    system = shutil.which("pandoc")
    return system or None


def _resolve_reference_doc() -> Path | None:
    candidate = get_base_path() / _REFERENCE_DOC_RELPATH
    return candidate if candidate.is_file() else None


def _today_label() -> str:
    now = datetime.now(timezone(timedelta(hours=8)))
    return f"{now.year}年{now.month}月{now.day}日"


def _sanitize_xml_text(text: str) -> str:
    return _XML_ILLEGAL_RE.sub("", text)


def _neutralize_raw_openxml(body: str) -> str:
    return _RAW_OPENXML_ATTR_RE.sub(_RAW_OPENXML_NEUTRALIZED, body)


def _split_leading_title(draft_text: str) -> tuple[str | None, str]:
    """只认首个非空行的 H1 作报告标题（canonical draft 契约：首行即 H1）——避免把
    正文/代码块里的 `# ` 行误当标题剥掉。返回 (title, body)；无标题时 body 原样。"""
    lines = draft_text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        match = _H1_LINE_RE.match(line.rstrip("\r\n"))
        if not match:
            return None, draft_text
        title = _INLINE_MARKUP_RE.sub("", match.group(1))
        title = _sanitize_xml_text(title).replace("\t", " ").strip()
        return (title or None), "".join(lines[:index] + lines[index + 1:])
    return None, draft_text


def extract_report_title(draft_text: str) -> str | None:
    return _split_leading_title(draft_text)[0]


def _cover_paragraph_xml(style_id: str, text: str) -> str:
    """封面段落走 raw openxml 直引模板 styleId——标题是不可信文本，绝不进 markdown
    解析（`:::`/`- `/`1. ` 等行首结构会破坏 fenced div，codex 红队实证），只做 XML 转义。"""
    return (
        f'<w:p><w:pPr><w:pStyle w:val="{style_id}"/></w:pPr>'
        f'<w:r><w:t xml:space="preserve">{xml_escape(_sanitize_xml_text(text))}</w:t></w:r></w:p>'
    )


def build_export_markdown(draft_text: str, *, date_label: str | None = None) -> tuple[str, str]:
    """导出预处理（纯函数）：剥首行 H1 → 封面（标题/副题/日期）+ 目录（TOC 域）+ 正文。

    返回 (export_markdown, title)。分页不靠插分页符：模板里 TOC Title 与
    Heading 1/2 均带 pageBreakBefore，封面→目录→正文自然分页、不产生空白页。
    正文经 _neutralize_raw_openxml 中和后拼入——raw openxml 直通只保留给本函数
    生成的可信常量块。
    """
    title, body = _split_leading_title(draft_text)
    display_title = title or _EXPORT_FALLBACK_TITLE

    cover_block = "\n".join(
        [
            "```{=openxml}",
            _cover_paragraph_xml("CoverTitle", display_title),
            _cover_paragraph_xml("CoverSubtitle", _COVER_SUBTITLE),
            _cover_paragraph_xml("CoverDate", date_label or _today_label()),
            "```",
        ]
    )
    cover = "\n\n".join(
        [
            cover_block,
            '::: {custom-style="TOC Title"}\n目　录\n:::',
            _TOC_FIELD_OPENXML,
        ]
    )
    return f"{cover}\n\n{_neutralize_raw_openxml(body).lstrip()}\n", display_title


def _postprocess_docx(docx_path: Path, header_title: str) -> None:
    """产物 docx 后处理（zip 重写）：页眉标题占位符替换 + updateFields 兜底 + 表格拉满
    行宽。保留原 ZipInfo 元数据逐项透传；重复 part 名视为损坏拒绝；改动过的 XML part
    先做 well-formed 校验（防坏 docx 顶掉旧终名）再写回，临时文件原子替换。"""
    with zipfile.ZipFile(docx_path) as src:
        infos = src.infolist()
        names = [item.filename for item in infos]
        if len(names) != len(set(names)):
            raise zipfile.BadZipFile("docx 内出现重复 part 名")
        entries = [(item, src.read(item.filename)) for item in infos]

    escaped_title = xml_escape(_sanitize_xml_text(header_title))
    placeholder = _HEADER_TITLE_PLACEHOLDER.encode("utf-8")
    modified: list[tuple[zipfile.ZipInfo, bytes]] = []
    for item, data in entries:
        name = item.filename
        new_data = data
        if name.startswith("word/header") and placeholder in data:
            new_data = data.replace(placeholder, escaped_title.encode("utf-8"))
        elif name == "word/document.xml":
            new_data = _TBLW_AUTO_RE.sub(_TBLW_FULL_WIDTH, data)
        elif name == "word/settings.xml" and b"updateFields" not in data:
            new_data = re.sub(
                rb"(<w:settings[^>]*>)",
                rb'\1<w:updateFields w:val="true"/>',
                data,
                count=1,
            )
        if new_data is not data:
            ET.fromstring(new_data)  # 改动过的 part 必须仍是合法 XML，否则整体走失败路径
        modified.append((item, new_data))

    fd, tmp_name = tempfile.mkstemp(dir=str(docx_path.parent), suffix=".docx")
    try:
        os.close(fd)
        with zipfile.ZipFile(tmp_name, "w", zipfile.ZIP_DEFLATED) as out:
            for item, data in modified:
                out.writestr(item, data)
        os.replace(tmp_name, docx_path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def export_reviewable_draft(report_path: str, output_dir: str) -> dict:
    """把报告 markdown 用 pandoc 导出为可审 docx。原子发布：pandoc 写同目录唯一 temp.docx
    → 后处理 → 成功 os.replace 到终名；任一失败（含意外异常）经 finally 清全部 temp、
    保留旧终名。全程锁外（依赖 R3 原子写不变式）。排版：封面/目录预处理 + reference-doc
    模板 + 产物后处理。"""
    pandoc = _resolve_pandoc()
    if not pandoc:
        return {
            "status": "error",
            "output": "未找到 pandoc：请在服务器安装 pandoc（Linux：apt install pandoc），或重装完整的桌面安装包。",
            "output_path": "",
            "filename": "",
        }

    # 导出前 asset 硬校验（图表 spec §4.6）：pandoc 缺图可能 rc=0 只告警，产出静默丢图的
    # docx——不可接受。缺失就带清单友好失败、不进 pandoc。全程只读 assets、绝不 sweep。
    missing_assets = chart_assets.list_missing_assets(Path(report_path))
    if missing_assets:
        missing_list = "、".join(missing_assets)
        return {
            "status": "error",
            "output": (
                f"导出中止：正文引用了 {len(missing_assets)} 张缺失的图片（{missing_list}）。"
                "请让助手重新生成对应图表，或删除正文中的失效图片引用后再导出。"
            ),
            "output_path": "",
            "filename": "",
        }

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    final_path = out_dir / (Path(report_path).stem + ".docx")

    try:
        draft_text = Path(report_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {
            "status": "error",
            "output": f"导出失败：无法读取报告正文（{exc}）。",
            "output_path": "",
            "filename": "",
        }
    export_markdown, report_title = build_export_markdown(draft_text)

    reference_doc = _resolve_reference_doc()

    tmp_path: Path | None = None
    md_path: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(dir=str(out_dir), suffix=".docx")
        tmp_path = Path(tmp_name)  # 先记路径再 close：close 意外失败 finally 也能清
        os.close(fd)  # Windows 文件占用：pandoc 才能写该路径
        md_fd, md_name = tempfile.mkstemp(dir=str(out_dir), suffix=".md")
        md_path = Path(md_name)
        os.close(md_fd)
        md_path.write_text(export_markdown, encoding="utf-8")
        # --resource-path 指向草稿父目录（content/）：正文里 `assets/x.png` 相对引用命中，
        # pandoc 原生把 PNG 作为嵌入 media 打进 docx（图表 spec §4.6）。
        cmd = [pandoc, str(md_path), "--resource-path", str(Path(report_path).parent)]
        if reference_doc is not None:
            cmd += ["--reference-doc", str(reference_doc)]
        cmd += ["-o", str(tmp_path)]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            return {
                "status": "error",
                "output": result.stderr or result.stdout or "pandoc 导出失败，未生成可审草稿。",
                "output_path": "",
                "filename": "",
            }
        _postprocess_docx(tmp_path, report_title)
        os.replace(tmp_path, final_path)
        tmp_path = None  # 已发布，finally 不再清
    except (OSError, zipfile.BadZipFile, ET.ParseError) as exc:
        return {
            "status": "error",
            "output": f"导出失败：{exc}",
            "output_path": "",
            "filename": "",
        }
    finally:
        # 意外异常也不许泄漏 temp（codex B4）：未发布的 tmp docx 与预处理 md 一律清。
        if md_path is not None:
            md_path.unlink(missing_ok=True)
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    style_note = (
        "已套用标准咨询排版（封面、目录、页眉页码）。"
        if reference_doc is not None
        else "提示：未找到排版模板，本次以基础样式导出。"
    )
    return {
        "status": "ok",
        "output": (
            f"已生成可审草稿: {final_path}\n"
            f"说明: {style_note}在 Word/WPS 中打开时如提示“是否更新此文档中的域”，请选“是”以刷新目录页码。"
            "当前产物用于预审和传阅，不替代最终交付排版。"
        ),
        "output_path": str(final_path),
        "filename": final_path.name,
    }
