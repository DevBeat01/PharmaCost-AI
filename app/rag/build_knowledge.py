"""知识库构建脚本"""
import json
import hashlib
import threading
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import KNOWLEDGE_DIR, KNOWLEDGE_UPLOAD_DIR, CHUNK_OVERLAP
from rag.pdf_parser import parse_document, _classify_doc, _get_chunk_size
from rag.vector_store import VectorStore, get_vector_store
from rag.retriever import build_bm25_index
from langchain_text_splitters import RecursiveCharacterTextSplitter


_MANIFEST_PATH = Path(__file__).resolve().parent / "knowledge_manifest.json"
_INDEX_META_PATH = Path(__file__).resolve().parent / "knowledge_index_meta.json"
_BUILD_LOCK = threading.Lock()


def _read_manifest() -> dict:
    try:
        value = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _file_signatures() -> dict[str, dict]:
    """Read document metadata without parsing large PDF/DOCX files."""
    signatures = {}
    for directory, prefix in ((Path(KNOWLEDGE_DIR), ""), (Path(KNOWLEDGE_UPLOAD_DIR), "导入/")):
        if not directory.exists():
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix.lower() not in {".pdf", ".docx", ".txt"} or path.name.startswith("~$"):
                continue
            stat = path.stat()
            source = f"{prefix}{path.name}"
            doc_type = _classify_doc(path.name)
            signatures[source] = {
                "fingerprint": f"{source}:{stat.st_mtime_ns}:{stat.st_size}",
                "doc_type": doc_type,
                "chunk_size": _get_chunk_size(doc_type),
            }
    return signatures


def _parse_all_knowledge_documents() -> list[dict]:
    """Parse bundled and administrator-imported documents into one source-aware collection."""
    documents = []
    for directory, prefix in ((Path(KNOWLEDGE_DIR), ""), (Path(KNOWLEDGE_UPLOAD_DIR), "导入/")):
        if not directory.exists():
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix.lower() not in {".pdf", ".docx", ".txt"} or path.name.startswith("~$"):
                continue
            text = parse_document(path)
            if not text.strip():
                continue
            doc_type = _classify_doc(path.name)
            stat = path.stat()
            documents.append({
                "source": f"{prefix}{path.name}", "path": str(path), "text": text,
                "doc_type": doc_type, "chunk_size": _get_chunk_size(doc_type),
                "mtime_ns": stat.st_mtime_ns, "size": stat.st_size,
            })
    return documents


def _manifest_fingerprint(manifest: dict) -> str:
    payload = [
        {
            "source": source,
            "fingerprint": entry.get("fingerprint", ""),
            "doc_type": entry.get("doc_type", ""),
            "chunks": len(entry.get("chunks", [])),
        }
        for source, entry in sorted(manifest.items())
        if isinstance(entry, dict)
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _read_index_meta() -> dict:
    try:
        value = json.loads(_INDEX_META_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_index_meta(fingerprint: str, chunk_count: int):
    temp = _INDEX_META_PATH.with_suffix(".tmp")
    temp.write_text(
        json.dumps({"fingerprint": fingerprint, "chunk_count": chunk_count}, ensure_ascii=False),
        encoding="utf-8",
    )
    temp.replace(_INDEX_META_PATH)


def _write_manifest(manifest: dict) -> None:
    temp = _MANIFEST_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    temp.replace(_MANIFEST_PATH)


def _restore_file(path: Path, previous: bytes | None) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
    else:
        temp = path.with_suffix(path.suffix + ".restore")
        temp.write_bytes(previous)
        temp.replace(path)


def _fingerprint(doc: dict) -> str:
    return f"{doc['source']}:{doc['mtime_ns']}:{doc['size']}"


def _split_document(doc: dict) -> list[dict]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=doc['chunk_size'],
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", "。", "；", " "],
    )
    return [
        {
            'content': chunk,
            'source': doc['source'],
            'doc_type': doc['doc_type'],
            'chunk_index': i,
        }
        for i, chunk in enumerate(splitter.split_text(doc['text']))
    ]


def build_knowledge_base():
    """增量构建知识库；文档未变化时复用本地 Chroma 向量索引。"""
    if not _BUILD_LOCK.acquire(blocking=False):
        print("知识库正在由其他线程构建，跳过本次重复任务")
        return 0
    try:
        return _build_knowledge_base_locked()
    finally:
        _BUILD_LOCK.release()


def _build_knowledge_base_locked():
    print("开始构建知识库...")
    manifest = _read_manifest()
    signatures = _file_signatures()
    manifest_is_current = (
        set(manifest) == set(signatures)
        and all(
            isinstance(manifest.get(source), dict)
            and manifest[source].get("fingerprint") == info["fingerprint"]
            and manifest[source].get("doc_type") == info["doc_type"]
            for source, info in signatures.items()
        )
    )

    if manifest_is_current:
        # Reuse cached chunks and avoid parsing every PDF/DOCX on every startup.
        documents = [{"source": source} for source in signatures]
    else:
        documents = _parse_all_knowledge_documents()
        manifest = {}

    current_sources = {doc['source'] for doc in documents}
    if not manifest_is_current:
        for doc in documents:
            fingerprint = _fingerprint(doc)
            cached = manifest.get(doc['source'])
            chunks = cached.get('chunks', []) if cached and cached.get('fingerprint') == fingerprint else _split_document(doc)
            manifest[doc['source']] = {
                'fingerprint': fingerprint,
                'doc_type': doc['doc_type'],
                'chunks': chunks,
            }

    for source in set(manifest) - current_sources:
        del manifest[source]

    all_chunks = []
    for entry in manifest.values():
        all_chunks.extend(entry.get('chunks', []))

    index_fingerprint = _manifest_fingerprint(manifest)
    index_meta = _read_index_meta()
    index_reusable = (
        manifest_is_current
        and index_meta.get("fingerprint") == index_fingerprint
        and index_meta.get("chunk_count") == len(all_chunks)
        and VectorStore.is_index_ready(index_fingerprint, len(all_chunks))
    )
    if index_reusable:
        build_bm25_index(all_chunks)
        print(f"复用本地 Chroma 向量索引: {len(all_chunks)} 个片段")
    else:
        if not VectorStore.ensure_embedding_model_ready():
            raise RuntimeError("嵌入模型下载或初始化失败，已保留上一版知识库")
        old_manifest = _MANIFEST_PATH.read_bytes() if _MANIFEST_PATH.is_file() else None
        old_meta = _INDEX_META_PATH.read_bytes() if _INDEX_META_PATH.is_file() else None

        def commit_metadata():
            try:
                build_bm25_index(all_chunks)
                _write_manifest(manifest)
                _write_index_meta(index_fingerprint, len(all_chunks))
            except Exception:
                _restore_file(_MANIFEST_PATH, old_manifest)
                _restore_file(_INDEX_META_PATH, old_meta)
                raise

        VectorStore.replace_documents_atomically(all_chunks, on_promoted=commit_metadata)
        print(f"Chroma 向量索引构建完成: {len(all_chunks)} 个片段")
    if index_reusable:
        # Keep metadata durable even when the existing vector collection is reused.
        _write_manifest(manifest)
        _write_index_meta(index_fingerprint, len(all_chunks))
    print(f"知识库构建完成: {len(documents)} 个文档, {len(all_chunks)} 个片段")
    return len(all_chunks)


if __name__ == "__main__":
    build_knowledge_base()
