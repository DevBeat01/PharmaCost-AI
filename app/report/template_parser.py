"""Word模板解析器 — 处理{{占位符}}替换与动态表格行插入"""
import re
import copy
from pathlib import Path
from docx import Document
from docx.text.paragraph import Paragraph
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml.ns import qn


class TemplateParser:
    """解析Word模板，替换{{占位符}}，支持动态插入表格行"""

    PLACEHOLDER_RE = re.compile(r'\{\{(.+?)\}\}')

    def __init__(self, template_path):
        self.path = Path(template_path)
        self.doc = Document(str(self.path))
        self.parsed = False

    # ===== 查找 =====

    def find_placeholders(self) -> dict[str, list]:
        """扫描整个文档，返回 {占位符名: [位置描述, ...]}"""
        result = {}
        for i, para in enumerate(self.doc.paragraphs):
            for m in self.PLACEHOLDER_RE.finditer(para.text):
                result.setdefault(m.group(1), []).append(f"paragraph[{i}]")
        for ti, table in enumerate(self.doc.tables):
            for ri, row in enumerate(table.rows):
                for ci, cell in enumerate(row.cells):
                    for m in self.PLACEHOLDER_RE.finditer(cell.text):
                        result.setdefault(m.group(1), []).append(
                            f"table[{ti}][{ri},{ci}]")
        self.parsed = True
        return result

    def placeholder_contexts(self) -> dict[str, list[dict]]:
        """Return the nearest template heading for every placeholder.

        Templates may be uploaded by users, so the report engine must derive
        the semantic context from the active document instead of assuming the
        bundled template layout.
        """
        contexts: dict[str, list[dict]] = {}
        current_heading = ''
        current_level = 0
        for index, para in enumerate(self.doc.paragraphs):
            text = para.text.strip()
            heading = self._heading_info(text)
            if heading:
                current_heading, current_level = heading
            for match in self.PLACEHOLDER_RE.finditer(text):
                contexts.setdefault(match.group(1), []).append({
                    'location': f'paragraph[{index}]',
                    'heading': current_heading,
                    'heading_level': current_level,
                })
        for ti, table in enumerate(self.doc.tables):
            for ri, row in enumerate(table.rows):
                for ci, cell in enumerate(row.cells):
                    for match in self.PLACEHOLDER_RE.finditer(cell.text):
                        contexts.setdefault(match.group(1), []).append({
                            'location': f'table[{ti}][{ri},{ci}]',
                            'heading': current_heading,
                            'heading_level': current_level,
                        })
        return contexts

    @staticmethod
    def _heading_info(text: str):
        value = str(text or '').strip()
        if re.match(r'^[一二三四五六七八九十]+、', value):
            return value, 1
        matched = re.match(r'^(\d+(?:\.\d+){0,2})(?:\s+|$)', value)
        if matched:
            return value, matched.group(1).count('.') + 1
        return None

    # ===== 段落替换 =====

    @staticmethod
    def _build_run_offsets(para):
        """构建 (累计偏移, run索引) 列表"""
        offsets = []
        pos = 0
        for i, run in enumerate(para.runs):
            offsets.append((pos, i))
            pos += len(run.text)
        return offsets

    def _replace_in_paragraph(self, para, mapping):
        """在单个段落中替换占位符（处理跨run的情况）"""
        full = ''.join(r.text for r in para.runs)
        if '{{' not in full:
            return

        # 查找所有匹配
        matches = list(self.PLACEHOLDER_RE.finditer(full))
        if not matches:
            return

        offsets = self._build_run_offsets(para)

        for match in reversed(matches):
            key = match.group(1)
            if key not in mapping:
                continue
            repl = self._clean_markdown_text(mapping[key])
            mstart, mend = match.start(), match.end()

            # 找出涉及的run
            affected = []
            for run_idx, (rstart, ri) in enumerate(offsets):
                rtext = para.runs[ri].text
                rend = rstart + len(rtext)
                if rend > mstart and rstart < mend:
                    affected.append((ri, rstart, rend))

            if not affected:
                continue

            first_ri, first_start, _ = affected[0]

            # 重写第一个run的文本（保留前缀 + 替换值）
            prefix = para.runs[first_ri].text[:mstart - first_start]
            para.runs[first_ri].text = prefix + repl

            # 清空后续受影响的run（保留后缀在最后一个run）
            for _, (ri, rstart, rend) in enumerate(affected[1:], 1):
                suffix_start = mend - rstart
                if ri == affected[-1][0]:
                    para.runs[ri].text = para.runs[ri].text[
                        min(suffix_start, len(para.runs[ri].text)):]
                else:
                    para.runs[ri].text = ''

        return

    # ===== 单元格替换 =====

    @staticmethod
    def _cell_full_text(cell) -> str:
        return '\n'.join(p.text for p in cell.paragraphs)

    def _replace_in_cell(self, cell, mapping):
        """替换单元格内所有段落中的占位符"""
        for para in cell.paragraphs:
            self._replace_paragraph_markdown(para, mapping)

    # ===== 统一替换 =====

    def replace_placeholders(self, mapping: dict):
        """替换段落和已有表格行中的占位符"""
        for para in self.doc.paragraphs:
            self._replace_paragraph_markdown(para, mapping)
        for table in self.doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    self._replace_in_cell(cell, mapping)

    @staticmethod
    def _markdown_table(lines):
        """解析 Markdown/管道分隔表格，返回 (headers, rows)。

        部分模型输出会省略 Markdown 要求的 ``|---|`` 分隔行；只要首行
        和后续行均为多列管道分隔数据，也按表格处理，避免整段内容被当
        作普通文本写入 Word。
        """
        if len(lines) < 2:
            return None
        split = lambda line: [part.strip() for part in line.strip().strip('|').split('|')]
        headers = split(lines[0])
        has_separator = bool(re.match(
            r'^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$',
            lines[1]))
        if has_separator:
            data_lines = lines[2:]
        else:
            # 无分隔线时，至少要求第二行也是两列以上，降低误识别普通
            # 文本的概率；后续行由 _markdown_blocks 按管道连续性截取。
            if len(headers) < 2 or len(split(lines[1])) < 2:
                return None
            data_lines = lines[1:]
        rows = [split(line) for line in data_lines if '|' in line]
        width = len(headers)
        rows = [(row + [''] * width)[:width] for row in rows]
        return headers, rows

    def _insert_markdown_table_after(self, para, headers, rows):
        """在段落后插入 Word 表格，并沿用模板默认字体。"""
        table = self.doc.add_table(rows=1, cols=len(headers))
        # 模板可能来自中文 Word 环境，英文样式名 ``Table Grid`` 不一定存在。
        # 优先复用模板已有表格样式，找不到时使用 Word 默认样式，并手动补边框。
        style_name = None
        if self.doc.tables:
            try:
                style_name = self.doc.tables[0].style.name
            except (AttributeError, KeyError):
                style_name = None
        if style_name:
            try:
                table.style = style_name
            except (KeyError, ValueError):
                pass
        self._set_table_borders(table)
        for idx, value in enumerate(headers):
            table.rows[0].cells[idx].text = self._clean_markdown_text(value)
        for row in rows:
            cells = table.add_row().cells
            for idx, value in enumerate(row):
                cells[idx].text = self._clean_markdown_text(value)
        anchor = para._element if hasattr(para, '_element') else para
        anchor.addnext(table._element)
        self._format_table(table)
        return table

    @staticmethod
    def _markdown_blocks(value):
        """按原始顺序拆分普通文本和 Markdown 表格块。"""
        lines = value.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        blocks = []
        text_lines = []

        def flush_text():
            if text_lines:
                text = '\n'.join(text_lines).strip()
                if text:
                    blocks.append(('text', text))
                text_lines.clear()

        i = 0
        while i < len(lines):
            # 模型常在表头、分隔行及数据行之间插入空行。跳过这些空行
            # 进行探测和收集，但不要把普通段落中的单个管道符误判为表格。
            if '|' in lines[i]:
                start = i
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j < len(lines) and '|' in lines[j]:
                    table_lines = [lines[i]]
                    k = i + 1
                    while k < len(lines):
                        if not lines[k].strip():
                            k += 1
                            continue
                        if '|' not in lines[k]:
                            break
                        table_lines.append(lines[k])
                        k += 1
                    parsed = TemplateParser._markdown_table(table_lines)
                    if parsed:
                        flush_text()
                        blocks.append(('table', parsed))
                        i = k
                        continue
            text_lines.append(lines[i])
            i += 1
        flush_text()
        return blocks

    def _insert_paragraph_after(self, anchor, text=''):
        """在指定 XML 节点后插入段落，并返回 Paragraph 包装对象。"""
        source = anchor._p if isinstance(anchor, Paragraph) else None
        if source is not None:
            paragraph_el = copy.deepcopy(source)
            for child in list(paragraph_el):
                if child.tag.endswith('}pPr'):
                    continue
                paragraph_el.remove(child)
        else:
            from docx.oxml import OxmlElement
            paragraph_el = OxmlElement('w:p')
        anchor_el = anchor._element if hasattr(anchor, '_element') else anchor
        anchor_el.addnext(paragraph_el)
        paragraph = Paragraph(paragraph_el, self.doc._body)
        if text:
            paragraph.add_run(self._clean_markdown_text(text))
        return paragraph

    def _insert_text_block_after(self, anchor, text, replace_anchor=False):
        """将模型返回的多行文本拆为独立 Word 段落，返回最后一个锚点。"""
        cleaned = self._clean_markdown_text(text)
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        if not lines:
            if replace_anchor:
                self._set_paragraph_text(anchor, '')
            return anchor
        if replace_anchor:
            self._set_paragraph_text(anchor, lines[0])
            self._format_paragraph(anchor, self._numbered_role(lines[0]))
            current = anchor
            start = 1
        else:
            current = anchor
            start = 0
        for line in lines[start:]:
            current = self._insert_paragraph_after(current, line)
            self._format_paragraph(current, self._numbered_role(line))
        return current

    @staticmethod
    def _set_paragraph_text(para, text):
        """清空段落正文但保留段落属性，并写入单行文本。"""
        for child in list(para._p):
            if child.tag != qn('w:pPr'):
                para._p.remove(child)
        if text:
            para.add_run(str(text))

    @staticmethod
    def _clean_markdown_text(value):
        """清理模型输出中的 Markdown 源码，保留可读文本。"""
        text = str(value or '').replace('\r\n', '\n').replace('\r', '\n')
        text = re.sub(r'```(?:[\w+-]+)?\s*\n?', '', text)
        text = text.replace('```', '')
        text = re.sub(r'^\s{0,3}#{1,6}\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*>\s?', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*[-*+]\s+', '• ', text, flags=re.MULTILINE)
        # Preserve 1. / 1.1 / 1.1.1 content numbering.  These are semantic
        # report items, not unordered bullets.
        text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)
        text = re.sub(r'\*\*(.*?)\*\*', r'\1', text, flags=re.DOTALL)
        text = re.sub(r'__(.*?)__', r'\1', text, flags=re.DOTALL)
        text = re.sub(r'~~(.*?)~~', r'\1', text, flags=re.DOTALL)
        text = re.sub(r'(?<!\*)\*(?!\s)(.*?)(?<!\s)\*', r'\1', text)
        text = re.sub(r'(?<!_)_(?!\s)(.*?)(?<!\s)_', r'\1', text)
        text = re.sub(r'`([^`]+)`', r'\1', text)
        text = re.sub(r'^\s*[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
        return re.sub(r'\n{3,}', '\n\n', text).strip()

    @staticmethod
    def _numbered_role(text):
        value = str(text or '').strip()
        if re.match(r'^\d+(?:\.\d+){1,2}\s+', value):
            return 'numbered2'
        if re.match(r'^\d+[.)]\s+', value):
            return 'numbered1'
        return None

    @staticmethod
    def _set_run_font(run, size=10.5, bold=None, color='000000', italic=False):
        run.font.name = '宋体'
        run._element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'), '宋体')
        run.font.size = Pt(size)
        if bold is not None:
            run.bold = bold
        run.italic = italic
        run.font.color.rgb = RGBColor.from_string(color)

    @classmethod
    def _paragraph_role(cls, text):
        value = str(text or '').strip()
        if not value:
            return 'blank'
        if len(value) <= 42 and '成本分析' in value and value.endswith('报告'):
            return 'title'
        if value.startswith('知识库参考：'):
            return 'citation'
        if re.match(r'^(?:[一二三四五六七八九十]+、|第[一二三四五六七八九十]+章)', value):
            return 'heading1'
        if re.match(r'^\d+\.\d+(?:\.\d+)?(?:\s+|$)', value):
            return 'heading2'
        if value.startswith(('• ', '- ', '* ')):
            return 'bullet'
        if len(value) <= 42 and re.match(r'^(?:重点|需关注|建议|异常|风险|结论|核心|注意)\s*[:：]?', value):
            return 'heading3'
        if len(value) <= 28 and re.search(r'(分析|概览|小结|结论|建议|预警|判断|汇总|变化|推算|说明|因素)$', value):
            return 'heading3'
        return 'body'

    @classmethod
    def _format_paragraph(cls, para, role=None):
        """应用报告统一段落层级与行距。"""
        role = role or cls._paragraph_role(para.text)
        fmt = para.paragraph_format
        fmt.line_spacing = 1.25
        fmt.keep_together = False
        fmt.keep_with_next = role in ('heading1', 'heading2', 'heading3')
        if role == 'title':
            fmt.space_before = Pt(8)
            fmt.space_after = Pt(10)
            para.alignment = WD_ALIGN_PARAGRAPH.LEFT
            for run in para.runs:
                cls._set_run_font(run, 17, True, '000000')
        elif role == 'heading1':
            fmt.space_before = Pt(13)
            fmt.space_after = Pt(5)
            para.alignment = WD_ALIGN_PARAGRAPH.LEFT
            for run in para.runs:
                cls._set_run_font(run, 15, True, '000000')
        elif role == 'heading2':
            fmt.space_before = Pt(9)
            fmt.space_after = Pt(4)
            para.alignment = WD_ALIGN_PARAGRAPH.LEFT
            for run in para.runs:
                cls._set_run_font(run, 12, True, '000000')
        elif role == 'heading3':
            fmt.space_before = Pt(6)
            fmt.space_after = Pt(2)
            for run in para.runs:
                cls._set_run_font(run, 11, True, '000000')
        elif role == 'bullet':
            fmt.left_indent = Pt(14)
            fmt.first_line_indent = Pt(-10)
            fmt.space_before = Pt(0)
            fmt.space_after = Pt(2)
            for run in para.runs:
                cls._set_run_font(run, 10.5, False)
        elif role in ('numbered1', 'numbered2'):
            value = str(para.text or '').strip()
            depth = 1 if role == 'numbered1' else value.split(None, 1)[0].count('.') + 1
            fmt.left_indent = Pt(15 + (depth - 1) * 13)
            fmt.first_line_indent = Pt(-13)
            fmt.space_before = Pt(2)
            fmt.space_after = Pt(4)
            for run in para.runs:
                # 1. / 1.1 等是填充正文的层级，不是模板章节标题。
                cls._set_run_font(run, 10.5, False)
        elif role == 'citation':
            fmt.space_before = Pt(4)
            fmt.space_after = Pt(7)
            fmt.left_indent = Pt(8)
            for run in para.runs:
                cls._set_run_font(run, 9, False, '000000', italic=True)
        else:
            fmt.left_indent = Pt(0)
            fmt.first_line_indent = Pt(0)
            fmt.space_before = Pt(0)
            fmt.space_after = Pt(5)
            for run in para.runs:
                cls._set_run_font(run, 10.5, False)

    @classmethod
    def _format_table(cls, table):
        """统一动态/模板表格的字体、间距、表头和边框。"""
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = True
        for ri, row in enumerate(table.rows):
            tr_pr = row._tr.get_or_add_trPr()
            cant_split = tr_pr.find(qn('w:cantSplit'))
            if cant_split is None:
                tr_pr.append(row._tr.makeelement(qn('w:cantSplit'), {}))
            for cell in row.cells:
                cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                tc_pr = cell._tc.get_or_add_tcPr()
                shd = tc_pr.find(qn('w:shd'))
                if ri == 0:
                    if shd is None:
                        shd = cell._tc.makeelement(qn('w:shd'), {})
                        tc_pr.append(shd)
                    shd.set(qn('w:fill'), 'EAF0F5')
                for para in cell.paragraphs:
                    para.paragraph_format.space_before = Pt(0)
                    para.paragraph_format.space_after = Pt(0)
                    para.paragraph_format.line_spacing = 1.0
                    para.alignment = WD_ALIGN_PARAGRAPH.CENTER if ri == 0 else WD_ALIGN_PARAGRAPH.LEFT
                    for run in para.runs:
                        cls._set_run_font(run, 9.2, ri == 0, '000000')

    def _format_report_document(self):
        """对生成报告正文统一排版，保留封面与模板控制信息的原有布局。"""
        started = False
        # 按 body 原始顺序处理，避免将封面/文控表格压缩成正文表格字号。
        for element in self.doc.element.body:
            tag = element.tag.rsplit('}', 1)[-1]
            if tag == 'p':
                para = Paragraph(element, self.doc._body)
                text = para.text.strip()
                if not started and re.match(r'^(?:一、封面与基本信息|一、)', text):
                    started = True
                if started and text:
                    # Generated numbered items already carry a hanging indent.
                    # Preserve that role so a body item such as 2.1 is not
                    # later mistaken for the template's 2.1 heading.
                    numbered_role = self._numbered_role(text)
                    if numbered_role and para.paragraph_format.left_indent is not None:
                        self._format_paragraph(para, numbered_role)
                    else:
                        self._format_paragraph(para)
            elif tag == 'tbl' and started:
                from docx.table import Table
                self._format_table(Table(element, self.doc._body))

    @staticmethod
    def _set_table_borders(table):
        """为动态表格设置兼容 Word/WPS 的实线边框。"""
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn

        tbl_pr = table._tbl.tblPr
        borders = tbl_pr.first_child_found_in('w:tblBorders')
        if borders is None:
            borders = OxmlElement('w:tblBorders')
            tbl_pr.append(borders)
        for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
            tag = qn(f'w:{edge}')
            element = borders.find(tag)
            if element is None:
                element = OxmlElement(f'w:{edge}')
                borders.append(element)
            element.set(qn('w:val'), 'single')
            element.set(qn('w:sz'), '4')
            element.set(qn('w:space'), '0')
            element.set(qn('w:color'), 'B7C3D0')

    def _replace_paragraph_markdown(self, para, mapping):
        """替换段落占位符，并把占位符值中的 Markdown 表格转为 Word 表格。"""
        full = ''.join(run.text for run in para.runs)
        matches = list(self.PLACEHOLDER_RE.finditer(full))
        if not matches:
            return
        # 常规段落沿用统一替换逻辑，确保同一段落中多个占位符的偏移计算正确。
        if not any('|' in str(mapping.get(match.group(1), '')) for match in matches):
            # 文本型分析占位符需要按行写成多个 Word 段落，确保数字层级
            # 可读，而不是挤在一个模板段落中。
            if len(matches) == 1:
                value = str(mapping.get(matches[0].group(1), '') or '')
                if '\n' in value:
                    self._insert_text_block_after(para, value, replace_anchor=True)
                    return
            self._replace_in_paragraph(para, mapping)
            return
        # 当前模板的分析占位符通常独占一个段落。按内容顺序插入文本段落和表格，
        # 避免把 1.1/2.1/3.1/4.2 下的表格统一挪到长段落末尾。
        if len(matches) == 1:
            key = matches[0].group(1)
            value = str(mapping.get(key, '') or '')
            blocks = self._markdown_blocks(value)
            if any(kind == 'table' for kind, _ in blocks):
                first_text = blocks[0][1] if blocks and blocks[0][0] == 'text' else ''
                self._insert_text_block_after(para, first_text, replace_anchor=True)
                anchor = para
                # 只有首个块本身是文本时才已写入原占位符；若首个块是
                # 表格，后续文本必须正常插入到表格之后。
                used_first_text = not (blocks and blocks[0][0] == 'text')
                for kind, content in blocks:
                    if kind == 'text':
                        if not used_first_text:
                            used_first_text = True
                            continue
                        anchor = self._insert_text_block_after(anchor, content)
                    else:
                        # 返回的 Table 作为下一块的锚点，支持连续表格及
                        # 表格之间仅有空行的模型输出。
                        anchor = self._insert_markdown_table_after(anchor, *content)
                return

        # 多占位符或非表格内容沿用原有跨 run 替换逻辑。
        self._replace_in_paragraph(para, mapping)

    def replace_table_placeholders(self, mapping: dict):
        """仅替换所有表格行中的占位符"""
        for table in self.doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    self._replace_in_cell(cell, mapping)

    def append_knowledge_sources(self, references: list[str]):
        """Append the actual RAG sources when the template has no source placeholder."""
        if not references:
            return
        self.doc.add_heading('知识库来源', level=1)
        for index, reference in enumerate(references, 1):
            self.doc.add_paragraph(f'{index}. {reference}')

    # ===== 查找表格 =====

    def find_table_by_header(self, header_keyword: str):
        """通过首行单元格文本查找表格"""
        for table in self.doc.tables:
            if not table.rows:
                continue
            first_row_text = ' '.join(
                c.text.strip() for c in table.rows[0].cells)
            if header_keyword in first_row_text:
                return table
        return None

    def find_table_by_header_all(self, *keywords) -> object:
        """通过多个关键词同时匹配首行来查找表格"""
        for table in self.doc.tables:
            if not table.rows:
                continue
            first_row_text = ' '.join(
                c.text.strip() for c in table.rows[0].cells)
            if all(k in first_row_text for k in keywords):
                return table
        return None

    # ===== 动态插入表格行 =====

    def insert_table_rows(self, table, data_rows: list, template_row_idx: int = 1):
        """
        在表格中插入动态数据行。

        参数:
            table: python-docx Table 对象
            data_rows: list of list of str — 每行的单元格文本
            template_row_idx: 模板行的索引（默认1，即第2行）

        插入后新行的占位符与模板行一致，后续由 replace_table_placeholders 替换。
        如果模板行有 {{xxx}} 占位符但 data_rows 中不含对应键，
        则该占位符在新行中保持原样，后续被全局替换。
        """
        if not data_rows or template_row_idx >= len(table.rows):
            return

        template_row = table.rows[template_row_idx]
        template_tr = template_row._tr
        tbl = table._tbl

        for row_data in data_rows:
            new_tr = copy.deepcopy(template_tr)
            cells = new_tr.findall(
                './/{http://schemas.openxmlformats.org/wordprocessingml/2006}tc')
            for ci, cell_text in enumerate(row_data):
                if ci >= len(cells):
                    break
                cell_el = cells[ci]
                paras = cell_el.findall(
                    '{http://schemas.openxmlformats.org/wordprocessingml/2006}p')
                if not paras:
                    continue
                # 保留第一个段落，清空后续
                first_p = paras[0]
                runs = first_p.findall(
                    './/{http://schemas.openxmlformats.org/wordprocessingml/2006}r')
                if runs:
                    t = runs[0].find(
                        '{http://schemas.openxmlformats.org/wordprocessingml/2006}t')
                    if t is not None:
                        t.text = cell_text
                    else:
                        from docx.oxml.ns import qn
                        t = first_p.makeelement(qn('w:t'), {})
                        t.text = cell_text
                        runs[0].append(t)
                for extra_p in paras[1:]:
                    cell_el.remove(extra_p)

            # 插入到模板行之后（每次都在模板行的下一个位置）
            tbl.insert(tbl.index(template_tr) + 1, new_tr)

    # ===== 表格搜索/插入（按关键词） =====

    def find_and_insert_rows(self, header_keyword: str, data_rows: list,
                             template_row_idx: int = 1) -> bool:
        """通过关键词找到表格并插入数据行"""
        table = self.find_table_by_header(header_keyword)
        if table is None:
            return False
        self.insert_table_rows(table, data_rows, template_row_idx)
        return True

    # ===== 动态插入全新表格 =====

    def insert_new_table_after_placeholder(self, placeholder: str, headers: list[str],
                                           data_rows: list[list[str]],
                                           style: str = 'Table Grid') -> bool:
        """
        在包含指定占位符的段落之后插入全新表格。

        参数:
            placeholder: 占位符名称（不含{{}}），如"原材料成本明细表格"
            headers: 表头列表，如["序号", "原材料名称", ...]
            data_rows: 数据行列表，每行是字符串列表
            style: 表格样式名称

        返回: 是否成功插入
        """
        # 1. 找到占位符段落
        target_para = None
        target_idx = -1
        for i, para in enumerate(self.doc.paragraphs):
            if f'{{{{{placeholder}}}}}' in para.text:
                target_para = para
                target_idx = i
                break

        if target_para is None:
            return False

        # 2. 构建表格XML
        from docx.oxml.ns import qn
        from lxml import etree

        nsmap = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        tbl = etree.SubElement(etree.Element('dummy'), qn('w:tbl'))

        # 表格属性
        tblPr = etree.SubElement(tbl, qn('w:tblPr'))
        tblStyle = etree.SubElement(tblPr, qn('w:tblStyle'))
        # 使用模板中已存在的表格样式 ID，避免英文 ``Table Grid`` 在
        # 中文 Word 模板中不存在而产生无效样式引用。
        style_id = None
        if self.doc.tables:
            try:
                style_id = self.doc.tables[0].style.style_id
            except (AttributeError, KeyError, ValueError):
                style_id = None
        tblStyle.set(qn('w:val'), str(style_id or style.replace(' ', '')))
        tblW = etree.SubElement(tblPr, qn('w:tblW'))
        tblW.set(qn('w:w'), '0')
        tblW.set(qn('w:type'), 'auto')
        tblBorders = etree.SubElement(tblPr, qn('w:tblBorders'))
        for border_name in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
            border = etree.SubElement(tblBorders, qn(f'w:{border_name}'))
            border.set(qn('w:val'), 'single')
            border.set(qn('w:sz'), '4')
            border.set(qn('w:space'), '0')
            border.set(qn('w:color'), '000000')

        num_cols = len(headers)

        # WordprocessingML 要求表格在行之前声明 tblGrid。此前仅写入
        # tr/tc 的简化 XML 虽可被部分 Word 版本打开，但 python-docx/WPS
        # 会将其视为损坏表格，导致预览或二次读取失败。
        tblGrid = etree.SubElement(tbl, qn('w:tblGrid'))
        grid_width = max(1, int(9000 / max(1, num_cols)))
        for _ in range(num_cols):
            grid_col = etree.SubElement(tblGrid, qn('w:gridCol'))
            grid_col.set(qn('w:w'), str(grid_width))

        # 3. 创建表头行
        self._add_table_row(tbl, headers, is_header=True, num_cols=num_cols)

        # 4. 创建数据行
        for row_data in data_rows:
            self._add_table_row(tbl, row_data, is_header=False, num_cols=num_cols)

        # 5. 插入到段落之后
        target_para._element.addnext(tbl)

        # 6. 清空占位符文本
        for run in target_para.runs:
            if '{{' in run.text or '}}' in run.text:
                run.text = ''

        return True

    @staticmethod
    def _add_table_row(tbl, cell_texts: list[str], is_header: bool = False,
                       num_cols: int = 0):
        """向表格XML元素添加一行"""
        from docx.oxml.ns import qn
        from lxml import etree

        nsmap = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        tr = etree.SubElement(tbl, qn('w:tr'))

        n = max(num_cols, len(cell_texts))
        for ci in range(n):
            text = cell_texts[ci] if ci < len(cell_texts) else ''
            tc = etree.SubElement(tr, qn('w:tc'))

            # 单元格属性
            tcPr = etree.SubElement(tc, qn('w:tcPr'))
            tcW = etree.SubElement(tcPr, qn('w:tcW'))
            tcW.set(qn('w:w'), '0')
            tcW.set(qn('w:type'), 'auto')

            # 段落
            p = etree.SubElement(tc, qn('w:p'))
            pPr = etree.SubElement(p, qn('w:pPr'))
            if is_header:
                jc = etree.SubElement(pPr, qn('w:jc'))
                jc.set(qn('w:val'), 'center')

            # Run
            r = etree.SubElement(p, qn('w:r'))
            rPr = etree.SubElement(r, qn('w:rPr'))
            if is_header:
                b = etree.SubElement(rPr, qn('w:b'))
            sz = etree.SubElement(rPr, qn('w:sz'))
            sz.set(qn('w:val'), '20')  # 10pt
            szCs = etree.SubElement(rPr, qn('w:szCs'))
            szCs.set(qn('w:val'), '20')
            color = etree.SubElement(rPr, qn('w:color'))
            color.set(qn('w:val'), '000000')

            t = etree.SubElement(r, qn('w:t'))
            t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
            t.text = TemplateParser._clean_markdown_text(text)

    def find_and_insert_new_table(self, placeholder: str, headers: list[str],
                                  data_rows: list[list[str]]) -> bool:
        """便捷方法：找到占位符段落并插入新表格"""
        return self.insert_new_table_after_placeholder(
            placeholder, headers, data_rows)

    # ===== 保存 =====

    def save(self, output_path):
        """保存文档"""
        self._format_report_document()
        self._force_black_fonts()
        self._remove_cover_wordart()
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        self.doc.save(output_path)

    def _force_black_fonts(self):
        """统一正文、表格及页眉页脚字体颜色为黑色，保留原有加粗。"""
        def format_part(part):
            for para in part.paragraphs:
                for run in para.runs:
                    run.font.color.rgb = RGBColor(0, 0, 0)
            for table in part.tables:
                for row in table.rows:
                    for cell in row.cells:
                        format_part(cell)

        format_part(self.doc)
        for section in self.doc.sections:
            for part in (
                section.header, section.first_page_header, section.even_page_header,
                section.footer, section.first_page_footer, section.even_page_footer,
            ):
                format_part(part)

        # python-docx 不会把超链接、文本框等特殊容器中的 run 暴露到
        # ``paragraphs``，直接遍历 XML 可避免模板样式把这些文字渲染成蓝色。
        def format_xml_runs(root):
            for run in root.iter(qn('w:r')):
                rpr = run.find(qn('w:rPr'))
                if rpr is None:
                    rpr = self._new_xml_element('w:rPr')
                    run.insert(0, rpr)
                color = rpr.find(qn('w:color'))
                if color is None:
                    color = self._new_xml_element('w:color')
                    rpr.append(color)
                color.set(qn('w:val'), '000000')

        format_xml_runs(self.doc.element)
        for section in self.doc.sections:
            for part in (
                section.header, section.first_page_header, section.even_page_header,
                section.footer, section.first_page_footer, section.even_page_footer,
            ):
                format_xml_runs(part._element)

    @staticmethod
    def _new_xml_element(tag):
        from docx.oxml import OxmlElement
        return OxmlElement(tag)

    def _remove_cover_wordart(self):
        """移除封面页中易被 WPS 错误栅格化的 VML WordArt 水印。

        模板的封面水印使用 36pt VML textpath，WPS 转 PDF 时会将其渲染为
        黑色实心横块。正常 logo（DrawingML 图片）和正文页小号水印不受影响。
        """
        from docx.oxml.ns import qn

        vml_textpath = '{urn:schemas-microsoft-com:vml}textpath'

        for section in self.doc.sections:
            header_parts = (
                section.header,
                section.first_page_header,
                section.even_page_header,
            )
            for header in header_parts:
                root = header._element
                for pict in list(root.iter(qn('w:pict'))):
                    textpaths = list(pict.iter(vml_textpath))
                    if any('font-size:36pt' in (node.get('style') or '') for node in textpaths):
                        parent = pict.getparent()
                        if parent is not None:
                            parent.remove(pict)
