# -*- coding: utf-8 -*-
"""统一的 DOCX 排版工具：中文报告用的标题、正文、表格、提示框、图表、页眉页脚。

设计目标：全套文档（需求、设计、报价、手册、测试）视觉一致，且不依赖 Word 模板。
"""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

BODY_FONT = "宋体"
HEAD_FONT = "微软雅黑"
ACCENT = RGBColor(0x1F, 0x3B, 0x6B)      # 深藏蓝
ACCENT_HEX = "1F3B6B"
ACCENT2 = RGBColor(0x2E, 0x6D, 0xA4)     # 中蓝
GREY = RGBColor(0x59, 0x59, 0x59)
LIGHT = "EEF3FA"
LIGHT2 = "F7F9FC"
BORDER = "C9D4E3"


def _set_run_font(run, name: str, size: float, bold: bool = False,
                  color: RGBColor | None = None, italic: bool = False) -> None:
    run.font.name = name
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    if color is not None:
        run.font.color.rgb = color
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), name)
    rfonts.set(qn("w:ascii"), name)
    rfonts.set(qn("w:hAnsi"), name)


def _shade(cell, hex_color: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tc_pr.append(shd)


def _cell_margins(cell, top=60, bottom=60, left=100, right=100) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    mar = OxmlElement("w:tcMar")
    for tag, val in (("top", top), ("left", left), ("bottom", bottom), ("right", right)):
        node = OxmlElement(f"w:{tag}")
        node.set(qn("w:w"), str(val))
        node.set(qn("w:type"), "dxa")
        mar.append(node)
    tc_pr.append(mar)


def _borders(table, color: str = BORDER, size: int = 4) -> None:
    tbl = table._tbl
    tbl_pr = tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), str(size))
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), color)
        borders.append(el)
    tbl_pr.append(borders)


def _repeat_header(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    tr_pr.append(el)


def _field(paragraph, instr: str) -> None:
    run = paragraph.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr_el = OxmlElement("w:instrText")
    instr_el.set(qn("xml:space"), "preserve")
    instr_el.text = instr
    fld_sep = OxmlElement("w:fldChar")
    fld_sep.set(qn("w:fldCharType"), "separate")
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_begin)
    run._r.append(instr_el)
    run._r.append(fld_sep)
    run._r.append(fld_end)


def new_document(title: str = "", subtitle: str = "", footer_text: str = "",
                 landscape: bool = False) -> Document:
    doc = Document()
    section = doc.sections[0]
    section.page_width = Cm(29.7 if landscape else 21.0)
    section.page_height = Cm(21.0 if landscape else 29.7)
    section.left_margin = Cm(2.2)
    section.right_margin = Cm(2.2)
    section.top_margin = Cm(2.3)
    section.bottom_margin = Cm(2.0)

    style = doc.styles["Normal"]
    style.font.name = BODY_FONT
    style.font.size = Pt(10.5)
    style.font.color.rgb = RGBColor(0x22, 0x22, 0x22)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)
    pf = style.paragraph_format
    pf.line_spacing = 1.45
    pf.space_after = Pt(4)
    pf.space_before = Pt(0)
    pf.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    for name, size, color, before, after in (
        ("Heading 1", 17, ACCENT, 16, 8),
        ("Heading 2", 13, ACCENT, 12, 5),
        ("Heading 3", 11.5, ACCENT2, 9, 3),
    ):
        st = doc.styles[name]
        st.font.name = HEAD_FONT
        st.font.size = Pt(size)
        st.font.bold = True
        st.font.color.rgb = color
        st.element.rPr.rFonts.set(qn("w:eastAsia"), HEAD_FONT)
        st.paragraph_format.space_before = Pt(before)
        st.paragraph_format.space_after = Pt(after)
        st.paragraph_format.keep_with_next = True
        st.paragraph_format.line_spacing = 1.2

    # 页脚：文档名 + 页码
    footer = section.footer
    fp = footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if footer_text:
        r = fp.add_run(footer_text + "    ")
        _set_run_font(r, BODY_FONT, 8, color=GREY)
    r = fp.add_run("第 ")
    _set_run_font(r, BODY_FONT, 8, color=GREY)
    _field(fp, " PAGE ")
    r = fp.add_run(" 页 / 共 ")
    _set_run_font(r, BODY_FONT, 8, color=GREY)
    _field(fp, " NUMPAGES ")
    r = fp.add_run(" 页")
    _set_run_font(r, BODY_FONT, 8, color=GREY)

    # 页眉细线
    header = section.header
    hp = header.paragraphs[0]
    hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    r = hp.add_run("某某科技有限公司 · 报销助手智能体系统")
    _set_run_font(r, HEAD_FONT, 8, color=RGBColor(0x8A, 0x8A, 0x8A))
    p_pr = hp._p.get_or_add_pPr()
    pbdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "4")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), BORDER)
    pbdr.append(bottom)
    p_pr.append(pbdr)
    return doc


def title_page(doc: Document, eyebrow: str, title: str, subtitle: str,
               meta: list[tuple[str, str]], note: str = "") -> None:
    """封面：眉标 + 主标题 + 副标题 + 元信息表 + 声明。"""
    for _ in range(3):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(0)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(eyebrow)
    _set_run_font(r, HEAD_FONT, 11, color=ACCENT2)
    r.font.letter_spacing = None

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after = Pt(6)
    r = p.add_run(title)
    _set_run_font(r, HEAD_FONT, 28, bold=True, color=ACCENT)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(subtitle)
    _set_run_font(r, HEAD_FONT, 13, color=GREY)

    # 装饰线
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(18)
    p.paragraph_format.space_after = Pt(24)
    p_pr = p._p.get_or_add_pPr()
    pbdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "10")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), ACCENT_HEX)
    pbdr.append(bottom)
    p_pr.append(pbdr)

    t = doc.add_table(rows=len(meta), cols=2)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    for i, (k, v) in enumerate(meta):
        row = t.rows[i]
        row.cells[0].width = Cm(4.2)
        row.cells[1].width = Cm(9.0)
        c0, c1 = row.cells
        c0.paragraphs[0].text = ""
        r = c0.paragraphs[0].add_run(k)
        _set_run_font(r, HEAD_FONT, 10, color=GREY)
        c0.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT
        r = c1.paragraphs[0].add_run(v)
        _set_run_font(r, BODY_FONT, 10.5, color=RGBColor(0x22, 0x22, 0x22))
        for c in (c0, c1):
            c.paragraphs[0].paragraph_format.space_after = Pt(2)

    if note:
        for _ in range(2):
            doc.add_paragraph()
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(note)
        _set_run_font(r, BODY_FONT, 8.5, color=RGBColor(0x99, 0x99, 0x99))
    doc.add_page_break()


def add_toc(doc: Document, note: str = "提示：在 Word 中按 Ctrl+A 后 F9 可一键更新目录页码。") -> None:
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    r = p.add_run("目  录")
    _set_run_font(r, HEAD_FONT, 18, bold=True, color=ACCENT)
    p.paragraph_format.space_after = Pt(10)

    p = doc.add_paragraph()
    _field(p, ' TOC \\o "1-1" \\h \\z \\u ')

    p = doc.add_paragraph()
    r = p.add_run(note)
    _set_run_font(r, BODY_FONT, 8, color=RGBColor(0x99, 0x99, 0x99))

    # 让 Word 打开时自动更新域
    settings = doc.settings.element
    update = OxmlElement("w:updateFields")
    update.set(qn("w:val"), "true")
    settings.append(update)
    doc.add_page_break()


def h1(doc: Document, text: str) -> None:
    doc.add_heading(text, level=1)


def h2(doc: Document, text: str) -> None:
    doc.add_heading(text, level=2)


def h3(doc: Document, text: str) -> None:
    doc.add_heading(text, level=3)


def para(doc: Document, text: str, bold_prefix: str = "", size: float = 10.5,
         color: RGBColor | None = None, indent: bool = True, space_after: float = 4,
         italic: bool = False) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space_after)
    if indent:
        p.paragraph_format.first_line_indent = Pt(21)
    if bold_prefix:
        r = p.add_run(bold_prefix)
        _set_run_font(r, BODY_FONT, size, bold=True, color=ACCENT)
    for idx, part in enumerate(text.split("**")):
        if not part:
            continue
        r = p.add_run(part)
        _set_run_font(r, BODY_FONT, size, bold=(idx % 2 == 1), color=color, italic=italic)


def bullets(doc: Document, items: list[str], numbered: bool = False) -> None:
    for i, item in enumerate(items, 1):
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Pt(24)
        p.paragraph_format.first_line_indent = Pt(-12)
        p.paragraph_format.space_after = Pt(2)
        prefix = f"{i}. " if numbered else "• "
        r = p.add_run(prefix)
        _set_run_font(r, BODY_FONT, 10.5, bold=True, color=ACCENT2)
        # 支持 **加粗** 简单标记
        parts = item.split("**")
        for idx, part in enumerate(parts):
            if not part:
                continue
            r = p.add_run(part)
            _set_run_font(r, BODY_FONT, 10.5, bold=(idx % 2 == 1))


def table(doc: Document, header: list[str], rows: list[list[str]],
          widths: list[float] | None = None, font_size: float = 9,
          header_fill: str = ACCENT_HEX, zebra: bool = True,
          align: str = "left") -> None:
    t = doc.add_table(rows=1, cols=len(header))
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    _borders(t)

    # 统一把列宽按比例缩放到正文宽度（16.4cm），避免超宽溢出页面
    if widths:
        total = sum(widths)
        widths = [w * 16.4 / total for w in widths]

    hdr = t.rows[0]
    _repeat_header(hdr)
    for i, text in enumerate(header):
        cell = hdr.cells[i]
        _shade(cell, header_fill)
        _cell_margins(cell)
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.line_spacing = 1.15
        r = p.add_run(str(text))
        _set_run_font(r, HEAD_FONT, font_size, bold=True, color=RGBColor(0xFF, 0xFF, 0xFF))
        if widths:
            cell.width = Cm(widths[i])

    for ri, row in enumerate(rows):
        cells = t.add_row().cells
        # 行不跨页断开，避免半行数据出现在下一页顶部
        tr_pr = t.rows[-1]._tr.get_or_add_trPr()
        cant = OxmlElement("w:cantSplit")
        cant.set(qn("w:val"), "true")
        tr_pr.append(cant)
        for ci, val in enumerate(row):
            cell = cells[ci]
            if zebra and ri % 2 == 1:
                _shade(cell, LIGHT2)
            _cell_margins(cell)
            if widths:
                cell.width = Cm(widths[ci])
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = 1.2
            p.alignment = (WD_ALIGN_PARAGRAPH.CENTER if align == "center"
                           else WD_ALIGN_PARAGRAPH.LEFT)
            text = str(val)
            for idx, part in enumerate(text.split("**")):
                if not part:
                    continue
                r = p.add_run(part)
                _set_run_font(r, BODY_FONT, font_size, bold=(idx % 2 == 1),
                              color=RGBColor(0x22, 0x22, 0x22))
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def callout(doc: Document, title: str, body: str, fill: str = "EEF3FA",
            accent: str = "2E6DA4") -> None:
    t = doc.add_table(rows=1, cols=1)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    cell = t.rows[0].cells[0]
    _shade(cell, fill)
    _cell_margins(cell, top=120, bottom=120, left=160, right=160)
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    for edge, sz in (("left", 24), ("top", 4), ("bottom", 4), ("right", 4)):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), str(sz))
        el.set(qn("w:color"), accent if edge == "left" else "D6E0EE")
        borders.append(el)
    tc_pr.append(borders)

    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(2)
    r = p.add_run(title)
    _set_run_font(r, HEAD_FONT, 10, bold=True, color=ACCENT)
    p2 = cell.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.LEFT
    is_code = "\n" in body
    p2.paragraph_format.space_after = Pt(0)
    p2.paragraph_format.line_spacing = 1.1 if is_code else 1.45
    for idx, part in enumerate(body.split("**")):
        if not part:
            continue
        r = p2.add_run(part)
        _set_run_font(r, "Consolas" if is_code else BODY_FONT,
                      8.5 if is_code else 9.5, bold=(idx % 2 == 1),
                      color=RGBColor(0x33, 0x33, 0x33))
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def kpi_row(doc: Document, items: list[tuple[str, str, str]]) -> None:
    """三个 KPI 卡片：(数值, 指标名, 说明)。"""
    t = doc.add_table(rows=2, cols=len(items))
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = True
    for ci, (value, label, desc) in enumerate(items):
        c = t.rows[0].cells[ci]
        _shade(c, LIGHT)
        _cell_margins(c, top=140, bottom=20)
        p = c.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(0)
        r = p.add_run(value)
        _set_run_font(r, HEAD_FONT, 20, bold=True, color=ACCENT)
        c2 = t.rows[1].cells[ci]
        _shade(c2, LIGHT)
        _cell_margins(c2, top=0, bottom=120)
        p2 = c2.paragraphs[0]
        p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p2.paragraph_format.space_after = Pt(0)
        r = p2.add_run(label)
        _set_run_font(r, BODY_FONT, 9.5, bold=True)
        p3 = c2.add_paragraph()
        p3.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p3.paragraph_format.space_after = Pt(0)
        r = p3.add_run(desc)
        _set_run_font(r, BODY_FONT, 8, color=GREY)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def figure(doc: Document, path: str | Path, caption: str, width: float = 15.6) -> None:
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(2)
    p.add_run().add_picture(str(path), width=Cm(width))
    cap = doc.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap.paragraph_format.space_after = Pt(8)
    r = cap.add_run(caption)
    _set_run_font(r, BODY_FONT, 8.5, color=GREY)


def page_break(doc: Document) -> None:
    doc.add_page_break()


def section_divider(doc: Document, text: str) -> None:
    """小节引导句：加粗引言，用于长文档过渡。"""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(2)
    r = p.add_run(text)
    _set_run_font(r, BODY_FONT, 10, italic=True, color=GREY)
