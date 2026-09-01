"""PDF报告导出。"""
import logging
import re
from html import escape
from pathlib import Path

try:
    import win32com.client
except ImportError:
    win32com = None

logger = logging.getLogger(__name__)

_FONT_NAME = "SimHei"
_FONT_PATHS = ("C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/simsunb.ttf")


def _load_reportlab():
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as error:
        raise RuntimeError(
            "无法使用 Word 转换 PDF，且未安装 reportlab 备用导出依赖"
        ) from error

    return {
        "A4": A4,
        "Paragraph": Paragraph,
        "ParagraphStyle": ParagraphStyle,
        "SimpleDocTemplate": SimpleDocTemplate,
        "Spacer": Spacer,
        "TA_CENTER": TA_CENTER,
        "TTFont": TTFont,
        "Table": Table,
        "TableStyle": TableStyle,
        "colors": colors,
        "getSampleStyleSheet": getSampleStyleSheet,
        "mm": mm,
        "pdfmetrics": pdfmetrics,
    }


def _font_name(pdfmetrics, TTFont):
    if _FONT_NAME not in pdfmetrics.getRegisteredFontNames():
        for path in _FONT_PATHS:
            if Path(path).exists():
                pdfmetrics.registerFont(TTFont(_FONT_NAME, path))
                break
    return _FONT_NAME if _FONT_NAME in pdfmetrics.getRegisteredFontNames() else "Helvetica"


def _export_word_pdf(docx_path: str, output_path: str) -> bool:
    if win32com is None:
        return False

    word = None
    document = None
    output = Path(output_path).resolve()

    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        document = word.Documents.Open(str(Path(docx_path).resolve()), ReadOnly=True)
        document.ExportAsFixedFormat(
            OutputFileName=str(output),
            ExportFormat=17,
            OpenAfterExport=False,
            OptimizeFor=0,
            Range=0,
            From=1,
            To=1,
            Item=0,
            IncludeDocProps=True,
            KeepIRM=True,
            CreateBookmarks=0,
            DocStructureTags=True,
            BitmapMissingFonts=True,
            UseISO19005_1=False,
        )
        return output.exists() and output.stat().st_size > 0
    except Exception:
        logger.exception("Word 模板转 PDF 失败，改用 PDF 备用导出")
        return False
    finally:
        if document is not None:
            try:
                document.Close(False)
            except Exception:
                logger.exception("关闭 Word 文档失败")
        if word is not None:
            try:
                word.Quit()
            except Exception:
                logger.exception("退出 Word 进程失败")


def export_pdf(report: dict, output_path: str, docx_path: str = ""):
    if docx_path and _export_word_pdf(docx_path, output_path):
        return output_path

    reportlab = _load_reportlab()
    A4 = reportlab["A4"]
    Paragraph = reportlab["Paragraph"]
    ParagraphStyle = reportlab["ParagraphStyle"]
    SimpleDocTemplate = reportlab["SimpleDocTemplate"]
    Spacer = reportlab["Spacer"]
    TA_CENTER = reportlab["TA_CENTER"]
    TTFont = reportlab["TTFont"]
    Table = reportlab["Table"]
    TableStyle = reportlab["TableStyle"]
    colors = reportlab["colors"]
    getSampleStyleSheet = reportlab["getSampleStyleSheet"]
    mm = reportlab["mm"]
    pdfmetrics = reportlab["pdfmetrics"]

    font = _font_name(pdfmetrics, TTFont)
    styles = getSampleStyleSheet()
    title = ParagraphStyle("ReportTitle", parent=styles["Title"], fontName=font, alignment=TA_CENTER, fontSize=20, leading=28, textColor=colors.HexColor("#17324d"))
    heading = ParagraphStyle("ReportHeading", parent=styles["Heading2"], fontName=font, fontSize=13, leading=19, textColor=colors.HexColor("#17324d"), spaceBefore=10, spaceAfter=6)
    body = ParagraphStyle("ReportBody", parent=styles["BodyText"], fontName=font, fontSize=9.5, leading=16, textColor=colors.HexColor("#263746"), wordWrap="CJK")
    meta = ParagraphStyle("ReportMeta", parent=body, alignment=TA_CENTER, textColor=colors.HexColor("#617284"))
    doc = SimpleDocTemplate(output_path, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm, title=report.get("title", "成本分析报告"), author="制药成本智能分析系统")

    def draw_header_footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.HexColor("#617284"))
        canvas.drawString(18 * mm, 10 * mm, "制药成本智能分析报告")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"第 {canvas.getPageNumber()} 页")
        canvas.restoreState()

    generated_at = report.get("generated_at") or report.get("created_at") or ""
    story = [Spacer(1, 28 * mm), Paragraph(report.get("title", "成本分析报告"), title), Spacer(1, 8 * mm), Paragraph(f"产品：{report.get('product', '')}　分析月份：{report.get('month', '')}", meta)]
    if generated_at:
        story.extend([Spacer(1, 3 * mm), Paragraph(f"编制时间：{generated_at}", meta)])
    story.append(Spacer(1, 18 * mm))

    # Word 转换不可用时，直接读取 DOCX 的 body，完整保留模板章节和动态表格。
    # 这比仅使用 preview.sections 更可靠，尤其覆盖 1.1/2.1/3.1/4.2 等明细。
    docx_story_loaded = False
    if docx_path and Path(docx_path).exists():
        try:
            from docx import Document
            from docx.table import Table as DocxTable
            from docx.text.paragraph import Paragraph as DocxParagraph
            from docx.oxml.ns import qn
            word_doc = Document(str(Path(docx_path).resolve()))
            body_story = []
            for element in word_doc.element.body:
                tag = element.tag.rsplit('}', 1)[-1]
                if tag == 'p':
                    para = DocxParagraph(element, word_doc._body)
                    text = para.text.strip()
                    if not text:
                        continue
                    if len(text) <= 42 and '成本分析' in text and text.endswith('报告'):
                        body_story.append(Paragraph(escape(text), title))
                    elif re.match(r'^(?:[一二三四五六七八九十]+、|第[一二三四五六七八九十]+章)', text):
                        body_story.append(Paragraph(escape(text), heading))
                    elif re.match(r'^\d+\.\d+(?:\.\d+)?(?:\s+|$)', text):
                        body_story.append(Paragraph(escape(text), heading))
                    elif text.startswith('知识库参考：'):
                        body_story.append(Paragraph(escape(text), meta))
                    else:
                        body_story.append(Paragraph(escape(text).replace('\n', '<br/>'), body))
                    body_story.append(Spacer(1, 2 * mm))
                elif tag == 'tbl':
                    table = DocxTable(element, word_doc._body)
                    rows = []
                    for row in table.rows:
                        rows.append([Paragraph(escape(cell.text.strip()).replace('\n', '<br/>'), body) for cell in row.cells])
                    if rows and rows[0]:
                        col_count = len(rows[0])
                        col_width = (A4[0] - 36 * mm) / max(1, col_count)
                        pdf_table = Table(rows, colWidths=[col_width] * col_count, repeatRows=1)
                        pdf_table.setStyle(TableStyle([
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eaf0f5")),
                            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#c6d2dc")),
                            ("FONTNAME", (0, 0), (-1, -1), font),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("LEFTPADDING", (0, 0), (-1, -1), 4),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                            ("TOPPADDING", (0, 0), (-1, -1), 3),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                        ]))
                        body_story.extend([pdf_table, Spacer(1, 3 * mm)])
            if body_story:
                # body_story 已包含 DOCX 封面、文控信息和正文，避免与
                # 备用导出的摘要标题重复。
                story = [Spacer(1, 10 * mm)]
                story.extend(body_story)
                docx_story_loaded = True
        except Exception:
            logger.exception("读取 DOCX 内容失败，改用预览摘要导出 PDF")

    if docx_story_loaded:
        doc.build(story, onFirstPage=draw_header_footer, onLaterPages=draw_header_footer)
        return output_path

    def table_rows(lines):
        if len(lines) < 2:
            return None
        split = lambda line: [part.strip() for part in line.strip().strip('|').split('|')]
        headers = split(lines[0])
        has_separator = bool(re.match(
            r'^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$',
            lines[1]))
        if not has_separator and (len(headers) < 2 or len(split(lines[1])) < 2):
            return None
        width = len(headers)
        data_lines = lines[2:] if has_separator else lines[1:]
        rows = [(split(line) + [''] * width)[:width] for line in data_lines if '|' in line]
        return [headers] + rows

    for section in report.get("sections", []):
        story.append(Paragraph(section.get("title", ""), heading))
        lines = str(section.get("content", "")).replace("\r\n", "\n").splitlines()
        i = 0
        while i < len(lines):
            next_index = i + 1
            while next_index < len(lines) and not lines[next_index].strip():
                next_index += 1
            if next_index < len(lines) and '|' in lines[i] and '|' in lines[next_index]:
                start = i
                table_lines = [lines[i]]
                i += 1
                while i < len(lines):
                    if not lines[i].strip():
                        i += 1
                        continue
                    if '|' not in lines[i]:
                        break
                    table_lines.append(lines[i])
                    i += 1
                rows = table_rows(table_lines)
                if rows:
                    data = [[Paragraph(escape(str(cell)), body) for cell in row] for row in rows]
                    col_width = 163 * mm / max(1, len(rows[0]))
                    table = Table(data, colWidths=[col_width] * len(rows[0]), repeatRows=1)
                    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eaf0f5")), ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#c6d2dc")), ("FONTNAME", (0, 0), (-1, -1), font), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]))
                    story.append(table)
                    story.append(Spacer(1, 2 * mm))
                    continue
                i = start
            paragraph = lines[i].strip()
            if paragraph:
                story.append(Paragraph(escape(paragraph), body))
                story.append(Spacer(1, 2 * mm))
            i += 1
    references = report.get("references", [])
    if references:
        story.append(Paragraph("知识库来源", heading))
        data = [[Paragraph("序号", body), Paragraph("来源", body)]] + [[Paragraph(str(i), body), Paragraph(str(ref).replace("&", "&amp;"), body)] for i, ref in enumerate(references, 1)]
        table = Table(data, colWidths=[18 * mm, 145 * mm], repeatRows=1)
        table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eaf0f5")), ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#c6d2dc")), ("FONTNAME", (0, 0), (-1, -1), font), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6)]))
        story.append(table)
    doc.build(story, onFirstPage=draw_header_footer, onLaterPages=draw_header_footer)
    return output_path
