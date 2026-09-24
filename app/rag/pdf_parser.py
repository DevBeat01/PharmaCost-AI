"""知识文档解析模块"""
from pathlib import Path

try:
    import pymupdf as fitz
except ImportError:
    # PyMuPDF 1.24.0 及更早版本仅提供 `fitz` 顶层模块，`pymupdf` 别名自 1.24.3 引入。
    import fitz as fitz
from docx import Document


def parse_pdf(pdf_path: str) -> str:
    doc = fitz.open(pdf_path)
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def parse_docx(docx_path: str) -> str:
    document = Document(docx_path)
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        parts.extend(" | ".join(cell.text for cell in row.cells) for row in table.rows)
    return "\n".join(part for part in parts if part.strip())


def parse_txt(txt_path: str) -> str:
    path = Path(txt_path)
    return path.read_text(encoding="utf-8-sig", errors="replace")


def parse_document(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return parse_pdf(str(path))
    if suffix == ".docx":
        return parse_docx(str(path))
    if suffix == ".txt":
        return parse_txt(str(path))
    raise ValueError(f"不支持的知识文档格式: {path.suffix}")


def parse_all_documents(knowledge_dir: str) -> list[dict]:
    knowledge_path = Path(knowledge_dir)
    documents = []
    for path in sorted(knowledge_path.iterdir()):
        if path.suffix.lower() not in {".pdf", ".docx", ".txt"} or path.name.startswith("~$"):
            continue
        text = parse_document(path)
        if not text.strip():
            continue
        doc_type = _classify_doc(path.name)
        documents.append({
            "source": path.name,
            "path": str(path),
            "text": text,
            "doc_type": doc_type,
            "chunk_size": _get_chunk_size(doc_type),
            "mtime_ns": path.stat().st_mtime_ns,
            "size": path.stat().st_size,
        })
    return documents


def parse_all_pdfs(knowledge_dir: str) -> list[dict]:
    return parse_all_documents(knowledge_dir)


def _classify_doc(filename: str) -> str:
    """根据文件名判断文档类型"""
    if '配方' in filename:
        return 'recipe'
    elif '工艺' in filename:
        return 'process'
    elif '设备' in filename:
        return 'equipment'
    elif 'GMP' in filename or 'gmp' in filename:
        return 'gmp'
    return 'general'


def _get_chunk_size(doc_type: str) -> int:
    """获取文档类型对应的切分大小"""
    sizes = {
        'recipe': 400,
        'process': 600,
        'equipment': 300,
        'gmp': 500,
        'general': 500,
    }
    return sizes.get(doc_type, 500)
