"""ChromaDB向量库管理"""
import chromadb
import logging
import json
import uuid
from pathlib import Path
import sys
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from config import CHROMA_DB_PATH


logger = logging.getLogger("rag.vector_store")
_COLLECTION_NAME = "pharma_knowledge"
_INDEX_META_PATH = Path(__file__).resolve().parent / "knowledge_index_meta.json"
_EMBEDDING_DIMENSION = int(getattr(config, "EMBEDDING_DIMENSION", 1024))
_EMBEDDING_BATCH_SIZE = 10


class EmbeddingConfigurationError(RuntimeError):
    """Raised when the independent DashScope embedding configuration is absent."""


class DashScopeEmbeddingFunction:
    """Chroma embedding function backed by the OpenAI-compatible DashScope API.

    Chroma calls the same function for documents and query text, so accepting a
    list here keeps both ``add(documents=...)`` and ``query(query_texts=...)``
    compatible with the 0.5 API.
    """

    def __init__(self):
        api_key = str(getattr(config, "DASHSCOPE_API_KEY", "") or "").strip()
        if not api_key:
            raise EmbeddingConfigurationError(
                "未配置百炼嵌入模型密钥，请设置 DASHSCOPE_API_KEY"
            )
        self.model = str(
            getattr(config, "DASHSCOPE_EMBEDDING_MODEL", "qwen3.7-text-embedding")
        ).strip()
        self.client = OpenAI(
            api_key=api_key,
            base_url=str(getattr(config, "DASHSCOPE_BASE_URL", "")).rstrip("/"),
        )

    def __call__(self, input):
        if isinstance(input, str):
            texts = [input]
        else:
            texts = list(input or [])
        if not texts:
            return []
        vectors = []
        # DashScope-compatible deployments commonly cap one request at ten
        # inputs. Keep the public function batch-friendly while avoiding a
        # failure when a knowledge build contains many chunks.
        for start in range(0, len(texts), _EMBEDDING_BATCH_SIZE):
            batch = texts[start:start + _EMBEDDING_BATCH_SIZE]
            response = self.client.embeddings.create(model=self.model, input=batch)
            raw_data = response.get("data") if isinstance(response, dict) else getattr(response, "data", None)
            data = list(raw_data or [])
            if len(data) != len(batch):
                raise RuntimeError(
                    f"百炼嵌入接口返回数量不一致: 请求 {len(batch)}，返回 {len(data)}"
                )
            # OpenAI-compatible providers normally preserve order, but honoring
            # an explicit index makes batch responses deterministic as well.
            data.sort(key=lambda item: getattr(item, "index", 0) if not isinstance(item, dict) else item.get("index", 0))
            for item in data:
                embedding = item.get("embedding") if isinstance(item, dict) else getattr(item, "embedding", None)
                vector = [float(value) for value in (embedding or [])]
                if len(vector) != _EMBEDDING_DIMENSION:
                    raise RuntimeError(
                        f"百炼嵌入维度不符合要求: 期望 {_EMBEDDING_DIMENSION}，实际 {len(vector)}"
                    )
                vectors.append(vector)
        return vectors

    def embed_query(self, input):
        """Chroma uses this hook for ``query_texts`` in recent releases."""
        return self(input)

    @staticmethod
    def name() -> str:
        return "dashscope_qwen3_7_text_embedding"

    def get_config(self) -> dict:
        # Do not persist the secret in Chroma metadata/config.
        return {
            "model": self.model,
            "base_url": str(getattr(config, "DASHSCOPE_BASE_URL", "")).rstrip("/"),
            "dimension": _EMBEDDING_DIMENSION,
        }

    def default_space(self):
        return "cosine"

    def supported_spaces(self):
        return ["cosine"]


class VectorStore:
    def __init__(self, collection_name: str = _COLLECTION_NAME):
        self.client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
        self.collection_name = collection_name
        self.embedding_function = self._create_embedding_function()
        self.collection = self._get_collection()

    @staticmethod
    def is_embedding_model_ready() -> bool:
        return bool(str(getattr(config, "DASHSCOPE_API_KEY", "") or "").strip())

    @classmethod
    def _create_embedding_function(cls):
        logger.info(
            "使用百炼 API 嵌入模型: %s (%s, %s维)",
            getattr(config, "DASHSCOPE_EMBEDDING_MODEL", "qwen3.7-text-embedding"),
            getattr(config, "DASHSCOPE_BASE_URL", ""),
            _EMBEDDING_DIMENSION,
        )
        return DashScopeEmbeddingFunction()

    @staticmethod
    def _collection_metadata() -> dict:
        return {
            "hnsw:space": "cosine",
            "embedding_provider": "dashscope",
            "embedding_model": str(getattr(config, "DASHSCOPE_EMBEDDING_MODEL", "qwen3.7-text-embedding")),
            "embedding_dimension": _EMBEDDING_DIMENSION,
        }

    @classmethod
    def _collection_is_compatible(cls, collection) -> bool:
        metadata = getattr(collection, "metadata", None) or {}
        try:
            dimension = int(metadata.get("embedding_dimension", 0) or 0)
        except (TypeError, ValueError):
            return False
        return (
            metadata.get("embedding_provider") == "dashscope"
            and metadata.get("embedding_model") == str(getattr(config, "DASHSCOPE_EMBEDDING_MODEL", "qwen3.7-text-embedding"))
            and dimension == _EMBEDDING_DIMENSION
        )

    def _get_collection(self):
        options = {"name": self.collection_name, "metadata": self._collection_metadata(), "embedding_function": self.embedding_function}
        existing = None
        try:
            existing = self.client.get_collection(name=self.collection_name)
        except Exception:
            # Chroma raises when the named collection does not exist; create it below.
            existing = None
        if existing is not None and not self._collection_is_compatible(existing):
            logger.info("检测到旧嵌入向量索引，删除并使用百炼 1024 维模型完整重建")
            self.client.delete_collection(self.collection_name)
        try:
            return self.client.get_or_create_collection(**options)
        except ValueError as exc:
            if "embedding function already exists" not in str(exc).lower():
                raise
            self.client.delete_collection(self.collection_name)
            return self.client.get_or_create_collection(**options)

    @classmethod
    def ensure_embedding_model_ready(cls) -> bool:
        """Validate the independent DashScope embedding API configuration."""
        if cls.is_embedding_model_ready():
            return True
        raise EmbeddingConfigurationError(
            "未配置百炼嵌入模型，请设置 DASHSCOPE_API_KEY（不会回退到 DEEPSEEK_API_KEY 或 MIMO_API_KEY）"
        )

    @classmethod
    def is_index_ready(cls, fingerprint: str, chunk_count: int) -> bool:
        """Check persisted index completeness without loading the embedding model."""
        try:
            meta = json.loads(_INDEX_META_PATH.read_text(encoding="utf-8"))
            if meta.get("fingerprint") != fingerprint or meta.get("chunk_count") != chunk_count:
                return False
            client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
            collection = client.get_collection(_COLLECTION_NAME)
            return cls._collection_is_compatible(collection) and collection.count() == chunk_count
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
            return False

    def add_documents(self, documents: list[dict]):
        """批量添加文档到向量库"""
        ids = []
        texts = []
        metadatas = []

        for i, doc in enumerate(documents):
            doc_id = f"{doc['source']}_{doc.get('chunk_index', i)}"
            ids.append(doc_id)
            texts.append(doc['content'])
            metadatas.append({
                'source': doc['source'],
                'doc_type': doc.get('doc_type', 'general'),
                'chunk_index': doc.get('chunk_index', i),
            })

        if ids:
            self.collection.add(
                ids=ids,
                documents=texts,
                metadatas=metadatas,
            )

    @classmethod
    def replace_documents_atomically(cls, documents: list[dict], on_promoted=None) -> int:
        """Build a temporary collection, validate it, then swap it into service.

        The current collection is renamed to a backup during the short swap and
        restored if the temporary collection cannot be promoted.
        """
        global vector_store
        client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
        embedding_function = cls._create_embedding_function()
        token = uuid.uuid4().hex[:10]
        temp_name = f"{_COLLECTION_NAME}_staging_{token}"
        backup_name = f"{_COLLECTION_NAME}_backup_{token}"
        staging = client.get_or_create_collection(
            name=temp_name,
            metadata=cls._collection_metadata(),
            embedding_function=embedding_function,
        )
        old = None
        old_renamed = False
        staging_promoted = False
        try:
            ids, texts, metadatas = [], [], []
            for i, doc in enumerate(documents):
                ids.append(f"{doc['source']}_{doc.get('chunk_index', i)}")
                texts.append(doc["content"])
                metadatas.append({
                    "source": doc["source"],
                    "doc_type": doc.get("doc_type", "general"),
                    "chunk_index": doc.get("chunk_index", i),
                })
            if ids:
                staging.add(ids=ids, documents=texts, metadatas=metadatas)
            if staging.count() != len(documents):
                raise RuntimeError(f"临时向量索引校验失败: 期望 {len(documents)}，实际 {staging.count()}")
            try:
                old = client.get_collection(_COLLECTION_NAME)
            except Exception:
                old = None
            if old is not None:
                old.modify(name=backup_name)
                old_renamed = True
            staging.modify(name=_COLLECTION_NAME)
            staging_promoted = True
            if client.get_collection(_COLLECTION_NAME, embedding_function=embedding_function).count() != len(documents):
                raise RuntimeError("新向量索引上线后校验失败")
            if on_promoted is not None:
                on_promoted()
            if old_renamed:
                try:
                    client.delete_collection(backup_name)
                except Exception:
                    logger.warning("上一版知识库备份集合清理失败: %s", backup_name, exc_info=True)
            vector_store = None
            return len(documents)
        except Exception:
            # If promotion failed after the old collection was renamed, restore it.
            if staging_promoted:
                try:
                    client.delete_collection(_COLLECTION_NAME)
                except Exception:
                    pass
            if old_renamed:
                try:
                    client.get_collection(backup_name).modify(name=_COLLECTION_NAME)
                except Exception:
                    logger.exception("恢复上一版知识库向量索引失败")
            try:
                client.delete_collection(temp_name)
            except Exception:
                pass
            vector_store = None
            raise

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """语义检索"""
        results = self.collection.query(
            query_texts=[query],
            n_results=top_k,
        )
        docs = []
        if results and results['documents'] and results['documents'][0]:
            for i, doc in enumerate(results['documents'][0]):
                metadata = results['metadatas'][0][i] if results['metadatas'] else {}
                distance = results['distances'][0][i] if results['distances'] else 0
                docs.append({
                    'content': doc,
                    'source': metadata.get('source', ''),
                    'doc_type': metadata.get('doc_type', ''),
                    'chunk_index': metadata.get('chunk_index', i),
                    'score': 1 - distance,
                })
        return docs

    def count(self) -> int:
        return self.collection.count()

    @classmethod
    def status(cls) -> dict:
        """Return a cheap readiness snapshot for health checks and UI diagnostics."""
        ready = cls.is_embedding_model_ready()
        try:
            client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
            collection = client.get_collection(_COLLECTION_NAME)
            count = collection.count() if cls._collection_is_compatible(collection) else 0
        except Exception:
            count = 0
        return {
            "embedding_model_ready": ready,
            "embedding_mode": "api",
            "embedding_provider": "dashscope",
            "embedding_model": str(getattr(config, "DASHSCOPE_EMBEDDING_MODEL", "qwen3.7-text-embedding")),
            "embedding_base_url": str(getattr(config, "DASHSCOPE_BASE_URL", "")),
            "embedding_dimension": _EMBEDDING_DIMENSION,
            "embedding_api_key_configured": ready,
            "index_chunks": count,
            "path": str(CHROMA_DB_PATH),
        }

    def clear(self):
        """清空集合"""
        self.client.delete_collection(self.collection.name)
        self.collection = self._get_collection()


vector_store = None


def get_vector_store() -> VectorStore:
    global vector_store
    if vector_store is None:
        vector_store = VectorStore()
    return vector_store
