"""混合检索器 — 向量检索 + BM25"""
import sys
import logging
import json
import hashlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import RETRIEVAL_TOP_K, BM25_WEIGHT, VECTOR_WEIGHT
from rag.vector_store import VectorStore, get_vector_store
from rank_bm25 import BM25Okapi
import jieba

logger = logging.getLogger("rag.retriever")


# BM25全局实例（启动时构建）
_bm25 = None
_bm25_corpus = []
_bm25_metadata = []
_BM25_CACHE_PATH = Path(__file__).resolve().parent / "bm25_index_cache.json"


def _documents_fingerprint(documents: list[dict]) -> str:
    payload = [(doc.get('source', ''), doc.get('chunk_index', 0), doc.get('content', '')) for doc in documents]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def build_bm25_index(documents: list[dict]):
    """构建BM25索引"""
    global _bm25, _bm25_corpus, _bm25_metadata
    fingerprint = _documents_fingerprint(documents)
    try:
        cached = json.loads(_BM25_CACHE_PATH.read_text(encoding='utf-8'))
        if cached.get('fingerprint') == fingerprint:
            _bm25_corpus = cached.get('corpus', [])
            _bm25_metadata = cached.get('metadata', [])
            _bm25 = BM25Okapi(_bm25_corpus) if _bm25_corpus else None
            logger.info("复用本地 BM25 索引: %s 个片段", len(_bm25_corpus))
            return
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        pass
    _bm25_corpus = []
    _bm25_metadata = []
    for doc in documents:
        tokens = list(jieba.cut(doc['content']))
        _bm25_corpus.append(tokens)
        _bm25_metadata.append({
            'content': doc['content'],
            'source': doc['source'],
            'doc_type': doc.get('doc_type', ''),
            'chunk_index': doc.get('chunk_index', 0),
        })
    if _bm25_corpus:
        _bm25 = BM25Okapi(_bm25_corpus)
    try:
        _BM25_CACHE_PATH.write_text(json.dumps({
            'fingerprint': fingerprint,
            'corpus': _bm25_corpus,
            'metadata': _bm25_metadata,
        }, ensure_ascii=False), encoding='utf-8')
    except OSError:
        logger.warning("BM25索引缓存写入失败", exc_info=True)


def bm25_search(query: str, top_k: int = 5) -> list[dict]:
    """BM25关键词检索"""
    if _bm25 is None or not _bm25_corpus:
        return []
    tokens = list(jieba.cut(query))
    scores = _bm25.get_scores(tokens)
    top_indices = scores.argsort()[-top_k:][::-1]
    results = []
    for idx in top_indices:
        if scores[idx] > 0:
            meta = _bm25_metadata[idx]
            results.append({
                'content': meta['content'],
                'source': meta['source'],
                'doc_type': meta['doc_type'],
                'chunk_index': meta.get('chunk_index', 0),
                'score': float(scores[idx]),
                'method': 'bm25',
            })
    return results


def vector_search(query: str, top_k: int = 5) -> list[dict]:
    """向量语义检索（知识库未就绪时返回空,由混合检索回退到BM25）"""
    if not VectorStore.is_embedding_model_ready():
        logger.info("向量检索跳过（本地嵌入模型未就绪）")
        return []
    try:
        vs = get_vector_store()
        results = vs.search(query, top_k)
        for r in results:
            r['method'] = 'vector'
        return results
    except Exception:
        logger.warning("向量检索跳过（知识库未就绪）")
        return []


def hybrid_search(query: str, top_k: int = None) -> list[dict]:
    """混合检索: 向量(0.7) + BM25(0.3)"""
    k = top_k or RETRIEVAL_TOP_K

    vec_results = vector_search(query, k * 2)
    bm25_results = bm25_search(query, k * 2)

    # 归一化分数
    vec_max = max(r['score'] for r in vec_results) if vec_results else 1
    bm25_max = max(r['score'] for r in bm25_results) if bm25_results else 1

    # 合并并按加权分数排序
    seen = set()
    merged = []
    for r in vec_results:
        key = r['content'][:100]
        if key not in seen:
            seen.add(key)
            r['hybrid_score'] = (r['score'] / vec_max) * VECTOR_WEIGHT
            merged.append(r)

    for r in bm25_results:
        key = r['content'][:100]
        if key in seen:
            # 已存在，追加BM25分数
            for m in merged:
                if m['content'][:100] == key:
                    m['hybrid_score'] += (r['score'] / bm25_max) * BM25_WEIGHT
                    m['method'] = 'hybrid'
                    break
        else:
            seen.add(key)
            r['hybrid_score'] = (r['score'] / bm25_max) * BM25_WEIGHT
            merged.append(r)

    merged.sort(key=lambda x: x.get('hybrid_score', 0), reverse=True)
    return merged[:k]
