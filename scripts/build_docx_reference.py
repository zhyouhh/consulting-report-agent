"""生成咨询报告 docx 导出模板（pandoc reference-doc）——模板即代码。

用法（开发期，本机需装 pandoc ≥ 3；产物入库，运行时不依赖本脚本。字节级可复现
以「相同 pandoc/Python 版本」为前提——不同 pandoc 的默认 reference.docx 底版不同）：

    python scripts/build_docx_reference.py

以 `pandoc --print-default-data-file reference.docx` 为底，打样式补丁后写出
`templates/docx/consulting_v1.docx`。调整排版改本脚本重新生成，不要手改产物。

样式设计（通用中文咨询风，Word/WPS 双兼容）：
- 正文：宋体小四（西文 Times New Roman）、1.5 倍行距、首行缩进 2 字符、两端对齐
- 标题：黑体分级（章三号/节四号/小节小四），海军蓝 #1B2A4A 点缀，章前自动换页
- 表格：上下 1.5pt 海军蓝粗线 + 表头底纹 + 浅色内横线（三线表风），单元格五号不缩进
- 页面：A4，左右边距 2.3cm（保 6.4in 图表不溢出），封面页无页眉页脚（titlePg）
- 页眉：居中报告标题占位 `{{REPORT_TITLE}}`（导出后处理替换），页脚：第 X 页
- 目录：TOC 1-3 样式 + 「TOC Title」自动换页；模板 settings.xml 带 updateFields
- 编号：**不做标题自动编号**——正文骨架让模型手写编号（`## 1. 背景`、`第一条`），
  模板自动编号会撞出双重编号
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "templates" / "docx" / "consulting_v1.docx"

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
W = f"{{{NS['w']}}}"

for _p, _u in NS.items():
    if _p not in ("ct", "rel"):
        ET.register_namespace(_p, _u)
ET.register_namespace("", NS["ct"])  # [Content_Types].xml 默认命名空间

# ---- 视觉常量（改排版先改这里） -------------------------------------------------
NAVY = "1B2A4A"          # 海军蓝主色（与产品前端一致）
GRAY_TEXT = "595959"     # 次要文字
GRAY_LINE = "BFBFBF"     # 细分隔线
TABLE_INNER = "C9D2E0"   # 表格内横线（浅蓝灰）
TABLE_HEAD_FILL = "E8EDF5"  # 表头底纹

PAGE_W, PAGE_H = 11906, 16838          # A4 twips
MARGIN_LR = 1304                        # 2.3cm：文字区 9298 twips ≈ 6.46in ≥ 6.4in 图宽
MARGIN_TB = 1440                        # 2.54cm
CONTENT_W = PAGE_W - 2 * MARGIN_LR
TOC_TAB_POS = CONTENT_W - 10            # 目录页码右对齐制表位

HEADER_TITLE_PLACEHOLDER = "{{REPORT_TITLE}}"


def _el(xml: str) -> ET.Element:
    wrapper = f'<root xmlns:w="{NS["w"]}" xmlns:r="{NS["r"]}">{xml}</root>'
    return ET.fromstring(wrapper)[0]


def _serialize(root: ET.Element) -> bytes:
    return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + ET.tostring(
        root, encoding="unicode"
    ).encode("utf-8")


# ---- 样式定义 -------------------------------------------------------------------

def _heading(style_id: str, name: str, *, size: int, color: str, east_font: str = "黑体",
             before: int, after: int, outline: int, page_break: bool = False,
             bold: bool = False) -> str:
    pb = "<w:pageBreakBefore/>" if page_break else ""
    b = "<w:b/><w:bCs/>" if bold else ""
    return f"""
<w:style w:type="paragraph" w:styleId="{style_id}">
  <w:name w:val="{name}"/>
  <w:basedOn w:val="Normal"/>
  <w:next w:val="BodyText"/>
  <w:pPr>
    {pb}<w:keepNext/><w:keepLines/>
    <w:spacing w:before="{before}" w:after="{after}" w:line="360" w:lineRule="auto"/>
    <w:jc w:val="left"/>
    <w:outlineLvl w:val="{outline}"/>
  </w:pPr>
  <w:rPr>
    <w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="{east_font}" w:cs="Arial"/>
    {b}<w:color w:val="{color}"/>
    <w:sz w:val="{size}"/><w:szCs w:val="{size}"/>
  </w:rPr>
</w:style>"""


def _toc_entry(style_id: str, name: str, *, indent: int, bold: bool = False) -> str:
    b = "<w:b/><w:bCs/>" if bold else ""
    return f"""
<w:style w:type="paragraph" w:styleId="{style_id}">
  <w:name w:val="{name}"/>
  <w:basedOn w:val="Normal"/>
  <w:pPr>
    <w:tabs><w:tab w:val="right" w:leader="dot" w:pos="{TOC_TAB_POS}"/></w:tabs>
    <w:spacing w:before="60" w:after="60" w:line="360" w:lineRule="auto"/>
    <w:ind w:left="{indent}"/>
    <w:jc w:val="left"/>
  </w:pPr>
  <w:rPr>{b}<w:noProof/></w:rPr>
</w:style>"""


# 完整替换/新建的样式（按 styleId 匹配，存在则替换、不存在则追加）
STYLE_OVERRIDES: dict[str, str] = {
    # 基底：对齐/行距在 Normal；字体字号进 docDefaults（表格样式的字号才能生效）
    "Normal": """
<w:style w:type="paragraph" w:default="1" w:styleId="Normal">
  <w:name w:val="Normal"/>
  <w:qFormat/>
  <w:pPr><w:spacing w:before="0" w:after="0" w:line="360" w:lineRule="auto"/><w:jc w:val="both"/></w:pPr>
</w:style>""",
    "BodyText": """
<w:style w:type="paragraph" w:styleId="BodyText">
  <w:name w:val="Body Text"/>
  <w:basedOn w:val="Normal"/>
  <w:pPr><w:ind w:firstLineChars="200" w:firstLine="480"/></w:pPr>
</w:style>""",
    "FirstParagraph": """
<w:style w:type="paragraph" w:styleId="FirstParagraph">
  <w:name w:val="First Paragraph"/>
  <w:basedOn w:val="BodyText"/>
</w:style>""",
    # 紧凑段：pandoc 用于列表项/表格单元格——必须显式取消首行缩进
    "Compact": """
<w:style w:type="paragraph" w:styleId="Compact">
  <w:name w:val="Compact"/>
  <w:basedOn w:val="BodyText"/>
  <w:pPr><w:spacing w:before="20" w:after="20"/><w:ind w:firstLineChars="0" w:firstLine="0"/></w:pPr>
</w:style>""",
    "Heading1": _heading("Heading1", "heading 1", size=36, color=NAVY,
                         before=340, after=240, outline=0, page_break=True),
    "Heading2": _heading("Heading2", "heading 2", size=32, color=NAVY,
                         before=320, after=240, outline=1, page_break=True),
    "Heading3": _heading("Heading3", "heading 3", size=28, color=NAVY,
                         before=260, after=130, outline=2),
    "Heading4": _heading("Heading4", "heading 4", size=24, color="333333",
                         before=240, after=120, outline=3),
    "Heading5": _heading("Heading5", "heading 5", size=24, color="333333",
                         east_font="宋体", before=240, after=120, outline=4, bold=True),
    "Heading6": _heading("Heading6", "heading 6", size=24, color=GRAY_TEXT,
                         east_font="宋体", before=240, after=120, outline=5, bold=True),
    # 引用块：左侧灰线 + 灰字
    "BlockText": f"""
<w:style w:type="paragraph" w:styleId="BlockText">
  <w:name w:val="Block Text"/>
  <w:basedOn w:val="Normal"/>
  <w:pPr>
    <w:pBdr><w:left w:val="single" w:sz="18" w:space="8" w:color="{GRAY_LINE}"/></w:pBdr>
    <w:spacing w:before="120" w:after="120"/>
    <w:ind w:left="360" w:right="360"/>
  </w:pPr>
  <w:rPr><w:color w:val="{GRAY_TEXT}"/></w:rPr>
</w:style>""",
    # 题注（图/表）：居中灰色小五
    "Caption": f"""
<w:style w:type="paragraph" w:styleId="Caption">
  <w:name w:val="Caption"/>
  <w:basedOn w:val="Normal"/>
  <w:pPr><w:spacing w:before="80" w:after="200"/><w:jc w:val="center"/></w:pPr>
  <w:rPr><w:color w:val="{GRAY_TEXT}"/><w:sz w:val="18"/><w:szCs w:val="18"/></w:rPr>
</w:style>""",
    "ImageCaption": """
<w:style w:type="paragraph" w:styleId="ImageCaption">
  <w:name w:val="Image Caption"/>
  <w:basedOn w:val="Caption"/>
</w:style>""",
    "TableCaption": """
<w:style w:type="paragraph" w:styleId="TableCaption">
  <w:name w:val="Table Caption"/>
  <w:basedOn w:val="Caption"/>
</w:style>""",
    "Figure": """
<w:style w:type="paragraph" w:styleId="Figure">
  <w:name w:val="Figure"/>
  <w:basedOn w:val="Normal"/>
  <w:pPr><w:spacing w:before="120" w:after="120"/><w:ind w:firstLineChars="0" w:firstLine="0"/><w:jc w:val="center"/></w:pPr>
</w:style>""",
    "CaptionedFigure": """
<w:style w:type="paragraph" w:styleId="CaptionedFigure">
  <w:name w:val="Captioned Figure"/>
  <w:basedOn w:val="Figure"/>
  <w:pPr><w:keepNext/></w:pPr>
</w:style>""",
    "Hyperlink": f"""
<w:style w:type="character" w:styleId="Hyperlink">
  <w:name w:val="Hyperlink"/>
  <w:rPr><w:color w:val="{NAVY}"/><w:u w:val="single"/></w:rPr>
</w:style>""",
    "VerbatimChar": """
<w:style w:type="character" w:styleId="VerbatimChar">
  <w:name w:val="Verbatim Char"/>
  <w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:eastAsia="宋体"/><w:sz w:val="20"/><w:szCs w:val="20"/><w:shd w:val="clear" w:color="auto" w:fill="F2F2F2"/></w:rPr>
</w:style>""",
    # 表格：三线表风 + 表头底纹；单元格五号（依赖 docDefaults 承载正文字号才不被盖）
    "Table": f"""
<w:style w:type="table" w:styleId="Table">
  <w:name w:val="Table"/>
  <w:tblPr>
    <w:tblBorders>
      <w:top w:val="single" w:sz="12" w:space="0" w:color="{NAVY}"/>
      <w:bottom w:val="single" w:sz="12" w:space="0" w:color="{NAVY}"/>
      <w:insideH w:val="single" w:sz="4" w:space="0" w:color="{TABLE_INNER}"/>
    </w:tblBorders>
    <w:tblCellMar>
      <w:top w:w="57" w:type="dxa"/><w:left w:w="108" w:type="dxa"/>
      <w:bottom w:w="57" w:type="dxa"/><w:right w:w="108" w:type="dxa"/>
    </w:tblCellMar>
  </w:tblPr>
  <w:rPr><w:sz w:val="21"/><w:szCs w:val="21"/></w:rPr>
  <w:tblStylePr w:type="firstRow">
    <w:rPr><w:b/><w:bCs/><w:color w:val="{NAVY}"/></w:rPr>
    <w:tcPr>
      <w:shd w:val="clear" w:color="auto" w:fill="{TABLE_HEAD_FILL}"/>
      <w:tcBorders><w:bottom w:val="single" w:sz="8" w:space="0" w:color="{NAVY}"/></w:tcBorders>
    </w:tcPr>
  </w:tblStylePr>
</w:style>""",
    # 封面（导出预处理用 custom-style 块引用这些名字）
    "CoverTitle": f"""
<w:style w:type="paragraph" w:customStyle="1" w:styleId="CoverTitle">
  <w:name w:val="Cover Title"/>
  <w:basedOn w:val="Normal"/>
  <w:pPr>
    <w:pBdr><w:bottom w:val="single" w:sz="8" w:space="16" w:color="{NAVY}"/></w:pBdr>
    <w:spacing w:before="3400" w:after="360" w:line="240" w:lineRule="auto"/>
    <w:jc w:val="center"/>
  </w:pPr>
  <w:rPr>
    <w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="黑体" w:cs="Arial"/>
    <w:color w:val="{NAVY}"/>
    <w:sz w:val="52"/><w:szCs w:val="52"/>
  </w:rPr>
</w:style>""",
    "CoverSubtitle": f"""
<w:style w:type="paragraph" w:customStyle="1" w:styleId="CoverSubtitle">
  <w:name w:val="Cover Subtitle"/>
  <w:basedOn w:val="Normal"/>
  <w:pPr><w:spacing w:before="120" w:after="120" w:line="240" w:lineRule="auto"/><w:jc w:val="center"/></w:pPr>
  <w:rPr>
    <w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="黑体" w:cs="Arial"/>
    <w:color w:val="{GRAY_TEXT}"/>
    <w:sz w:val="28"/><w:szCs w:val="28"/>
  </w:rPr>
</w:style>""",
    "CoverDate": f"""
<w:style w:type="paragraph" w:customStyle="1" w:styleId="CoverDate">
  <w:name w:val="Cover Date"/>
  <w:basedOn w:val="Normal"/>
  <w:pPr><w:spacing w:before="6200" w:after="0" w:line="240" w:lineRule="auto"/><w:jc w:val="center"/></w:pPr>
  <w:rPr><w:color w:val="{GRAY_TEXT}"/><w:sz w:val="28"/><w:szCs w:val="28"/></w:rPr>
</w:style>""",
    # 目录页标题：自动换新页（配合 Heading1/2 的 pageBreakBefore，导出预处理无需插分页符）
    "TOCTitle": f"""
<w:style w:type="paragraph" w:customStyle="1" w:styleId="TOCTitle">
  <w:name w:val="TOC Title"/>
  <w:basedOn w:val="Normal"/>
  <w:pPr>
    <w:pageBreakBefore/><w:keepNext/>
    <w:spacing w:before="0" w:after="300" w:line="360" w:lineRule="auto"/>
    <w:jc w:val="center"/>
  </w:pPr>
  <w:rPr>
    <w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="黑体" w:cs="Arial"/>
    <w:color w:val="{NAVY}"/>
    <w:sz w:val="32"/><w:szCs w:val="32"/>
  </w:rPr>
</w:style>""",
    "TOC1": _toc_entry("TOC1", "toc 1", indent=0, bold=True),
    "TOC2": _toc_entry("TOC2", "toc 2", indent=440),
    "TOC3": _toc_entry("TOC3", "toc 3", indent=880),
}

DOC_DEFAULTS = """
<w:docDefaults>
  <w:rPrDefault>
    <w:rPr>
      <w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="宋体" w:cs="Times New Roman"/>
      <w:sz w:val="24"/><w:szCs w:val="24"/>
      <w:lang w:val="zh-CN" w:eastAsia="zh-CN" w:bidi="ar-SA"/>
    </w:rPr>
  </w:rPrDefault>
  <w:pPrDefault/>
</w:docDefaults>"""

SECT_PR = f"""
<w:sectPr>
  <w:footnotePr><w:numRestart w:val="eachSect"/></w:footnotePr>
  <w:headerReference w:type="first" r:id="rId901"/>
  <w:headerReference w:type="default" r:id="rId902"/>
  <w:footerReference w:type="first" r:id="rId903"/>
  <w:footerReference w:type="default" r:id="rId904"/>
  <w:pgSz w:w="{PAGE_W}" w:h="{PAGE_H}"/>
  <w:pgMar w:top="{MARGIN_TB}" w:right="{MARGIN_LR}" w:bottom="{MARGIN_TB}" w:left="{MARGIN_LR}" w:header="720" w:footer="720" w:gutter="0"/>
  <w:pgNumType w:start="0"/>
  <w:titlePg/>
  <w:cols w:space="425"/>
</w:sectPr>"""

_EMPTY_HF_BODY = "<w:p><w:pPr><w:spacing w:before='0' w:after='0' w:line='240' w:lineRule='auto'/></w:pPr></w:p>"

HEADER_FIRST = f"""<w:hdr xmlns:w="{NS['w']}">{_EMPTY_HF_BODY}</w:hdr>"""

HEADER_DEFAULT = f"""<w:hdr xmlns:w="{NS['w']}">
<w:p>
  <w:pPr>
    <w:pBdr><w:bottom w:val="single" w:sz="4" w:space="4" w:color="{GRAY_LINE}"/></w:pBdr>
    <w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/>
    <w:jc w:val="center"/>
    <w:rPr><w:color w:val="{GRAY_TEXT}"/><w:sz w:val="18"/><w:szCs w:val="18"/></w:rPr>
  </w:pPr>
  <w:r>
    <w:rPr><w:color w:val="{GRAY_TEXT}"/><w:sz w:val="18"/><w:szCs w:val="18"/></w:rPr>
    <w:t xml:space="preserve">{HEADER_TITLE_PLACEHOLDER}</w:t>
  </w:r>
</w:p>
</w:hdr>"""

FOOTER_FIRST = f"""<w:ftr xmlns:w="{NS['w']}">{_EMPTY_HF_BODY}</w:ftr>"""

_FOOTER_RPR = f'<w:rPr><w:color w:val="{GRAY_TEXT}"/><w:sz w:val="18"/><w:szCs w:val="18"/></w:rPr>'

FOOTER_DEFAULT = f"""<w:ftr xmlns:w="{NS['w']}">
<w:p>
  <w:pPr>
    <w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/>
    <w:jc w:val="center"/>
    <w:rPr><w:color w:val="{GRAY_TEXT}"/><w:sz w:val="18"/><w:szCs w:val="18"/></w:rPr>
  </w:pPr>
  <w:r>{_FOOTER_RPR}<w:t xml:space="preserve">第 </w:t></w:r>
  <w:r>{_FOOTER_RPR}<w:fldChar w:fldCharType="begin"/></w:r>
  <w:r>{_FOOTER_RPR}<w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>
  <w:r>{_FOOTER_RPR}<w:fldChar w:fldCharType="separate"/></w:r>
  <w:r>{_FOOTER_RPR}<w:t>1</w:t></w:r>
  <w:r>{_FOOTER_RPR}<w:fldChar w:fldCharType="end"/></w:r>
  <w:r>{_FOOTER_RPR}<w:t xml:space="preserve"> 页</w:t></w:r>
</w:p>
</w:ftr>"""

HF_PARTS = {
    "word/header1.xml": (HEADER_FIRST, "rId901", "header",
                         "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header"),
    "word/header2.xml": (HEADER_DEFAULT, "rId902", "header",
                         "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header"),
    "word/footer1.xml": (FOOTER_FIRST, "rId903", "footer",
                         "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer"),
    "word/footer2.xml": (FOOTER_DEFAULT, "rId904", "footer",
                         "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer"),
}


# ---- 打补丁 ---------------------------------------------------------------------

def patch_styles(data: bytes) -> bytes:
    root = ET.fromstring(data)
    # docDefaults 整体替换（承载中文正文字体字号——表格样式字号依赖它才能生效）
    old_defaults = root.find(f"{W}docDefaults")
    if old_defaults is not None:
        root.remove(old_defaults)
    root.insert(0, _el(DOC_DEFAULTS))
    # 样式按 styleId 替换或新增
    existing = {s.get(f"{W}styleId"): s for s in root.findall(f"{W}style")}
    for style_id, xml in STYLE_OVERRIDES.items():
        new_style = _el(xml)
        old = existing.get(style_id)
        if old is not None:
            idx = list(root).index(old)
            root.remove(old)
            root.insert(idx, new_style)
        else:
            root.append(new_style)
    return _serialize(root)


def patch_document(data: bytes) -> bytes:
    root = ET.fromstring(data)
    body = root.find(f"{W}body")
    old_sect = body.find(f"{W}sectPr")
    if old_sect is not None:
        body.remove(old_sect)
    body.append(_el(SECT_PR))
    return _serialize(root)


def patch_document_rels(data: bytes) -> bytes:
    ET.register_namespace("", NS["rel"])
    root = ET.fromstring(data)
    for part, (_, rid, _, reltype) in HF_PARTS.items():
        rel = ET.SubElement(root, f"{{{NS['rel']}}}Relationship")
        rel.set("Id", rid)
        rel.set("Type", reltype)
        rel.set("Target", part.removeprefix("word/"))
    return _serialize(root)


def patch_content_types(data: bytes) -> bytes:
    root = ET.fromstring(data)
    for part, (_, _, kind, _) in HF_PARTS.items():
        override = ET.SubElement(root, f"{{{NS['ct']}}}Override")
        override.set("PartName", f"/{part}")
        override.set(
            "ContentType",
            f"application/vnd.openxmlformats-officedocument.wordprocessingml.{kind}+xml",
        )
    return _serialize(root)


def patch_settings(data: bytes) -> bytes:
    """settings.xml 加 updateFields（Word/WPS 打开时刷新目录与页码域）。"""
    root = ET.fromstring(data)
    if root.find(f"{W}updateFields") is None:
        update = ET.Element(f"{W}updateFields")
        update.set(f"{W}val", "true")
        root.insert(0, update)
    return _serialize(root)


def build(output_path: Path = OUTPUT_PATH) -> Path:
    pandoc = shutil.which("pandoc")
    if not pandoc:
        raise SystemExit("需要本机 pandoc 生成底版：brew install pandoc / apt install pandoc")
    version_line = subprocess.run(
        [pandoc, "--version"], capture_output=True, text=True, check=True,
    ).stdout.splitlines()[0]
    version = version_line.split()[-1]
    if int(version.split(".")[0]) < 3:
        raise SystemExit(
            f"生成模板需 pandoc ≥ 3（当前 {version}）：不同版本的默认 reference.docx 不同，"
            "低版本生成的产物与入库模板不可比对。"
        )
    print(f"base: pandoc {version} default reference.docx")
    base = subprocess.run(
        [pandoc, "--print-default-data-file", "reference.docx"],
        capture_output=True, check=True,
    ).stdout

    import io

    src = zipfile.ZipFile(io.BytesIO(base))
    patchers = {
        "word/styles.xml": patch_styles,
        "word/document.xml": patch_document,
        "word/_rels/document.xml.rels": patch_document_rels,
        "[Content_Types].xml": patch_content_types,
        "word/settings.xml": patch_settings,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # 固定时间戳：产物字节可复现，git diff 干净
    stamp = (2026, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as out:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename in patchers:
                data = patchers[item.filename](data)
            out.writestr(zipfile.ZipInfo(item.filename, date_time=stamp), data)
        for part, (xml, _, _, _) in HF_PARTS.items():
            out.writestr(
                zipfile.ZipInfo(part, date_time=stamp),
                b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + xml.encode("utf-8"),
            )
    return output_path


def self_check(path: Path) -> None:
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        for part in HF_PARTS:
            assert part in names, f"缺 {part}"
        styles = z.read("word/styles.xml").decode("utf-8")
        for needle in ("Cover Title", "TOC Title", "toc 1", "黑体", "宋体", NAVY):
            assert needle in styles, f"styles.xml 缺 {needle}"
        doc = z.read("word/document.xml").decode("utf-8")
        for needle in ("titlePg", "rId902", 'w:start="0"'):
            assert needle in doc, f"document.xml 缺 {needle}"
        settings = z.read("word/settings.xml").decode("utf-8")
        assert "updateFields" in settings, "settings.xml 缺 updateFields"
        header = z.read("word/header2.xml").decode("utf-8")
        assert HEADER_TITLE_PLACEHOLDER in header, "header2.xml 缺标题占位符"
    print(f"OK {path} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else OUTPUT_PATH
    self_check(build(target))
