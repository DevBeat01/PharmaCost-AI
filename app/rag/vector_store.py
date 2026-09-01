"""ChromaDB向量库管理"""
import chromadb
import logging
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import CHROMA_DB_PATH


logger = logging.getLogger("rag.vector_store")
_COLLECTION_NAME = "pharma_knowledge"
_INDEX_META_PATH = Path(__file__).resolve().parent / "knowledge_index_meta.json"


class VectorStore:
    def __init__(self, collection_name: str = _COLLECTION_NAME):
        self.client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
        self.collection_name = collection_name
        self.embedding_function = self._create_embedding_function()
        self.collection = self._get_collection()

    @staticmethod
    def _model_cache_dir() -> Path:
        return (
            Path.home()
            / ".cache"
            / "chroma"
            / "onnx_models"
            / "all-MiniLM-L6-v2"
        )

    @classmethod
    def _sentence_transformer_model_ready(cls) -> bool:
        model_dir = cls._model_cache_dir()
        return (
            (model_dir / "model.safetensors").is_file()
            and (model_dir / "config.json").is_file()
            and (model_dir / "tokenizer.json").is_file()
        )

    @staticmethod
    def is_embedding_model_ready() -> bool:
        model_dir = VectorStore._model_cache_dir()
        onnx_ready = (
            (model_dir / "onnx").is_dir()
            and (model_dir / "onnx" / "model.onnx").is_file()
            and (model_dir / "onnx" / "tokenizer.json").is_file()
        )
        return onnx_ready or VectorStore._sentence_transformer_model_ready()

    @classmethod
    def _create_embedding_function(cls):
        if cls._sentence_transformer_model_ready():
            from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

            logger.info("使用本地 Sentence-Transformers 嵌入模型: %s", cls._model_cache_dir())
            return SentenceTransformerEmbeddingFunction(
                model_name=str(cls._model_cache_dir()),
                normalize_embeddings=True,
            )

        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
        return DefaultEmbeddingFunction()

    def _get_collection(self):
        options = {
            "name": self.collection_name,
            "metadata": {"hnsw:space": "cosine"},
            "embedding_function": self.embedding_function,
        }
        try:
            return self.client.get_or_create_collection(**options)
        except ValueError as exc:
            if "embedding function already exists" not in str(exc).lower():
                raise
            # Vectors from the old embedding function are incompatible with the
            # configured local model, so rebuild this derived index from source documents.
            logger.info("检测到嵌入模型变更，正在重建 Chroma 向量索引")
            self.client.delete_collection(self.collection_name)
            return self.client.get_or_create_collection(**options)

    @classmethod
    def ensure_embedding_model_ready(cls) -> bool:
        """Use a local model when present, otherwise download Chroma's default model."""
        if cls.is_embedding_model_ready():
            return True

        try:
            from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

            logger.info("本地嵌入模型缺失，正在下载 Chroma 默认模型 all-MiniLM-L6-v2")
            # The first embedding request makes Chroma download and unpack its ONNX model.
            DefaultEmbeddingFunction()(["初始化本地嵌入模型"])
        except Exception:
            logger.exception("本地嵌入模型下载或初始化失败")
            return False

        ready = cls.is_embedding_model_ready()
        if not ready:
            logger.error("嵌入模型初始化完成，但所需模型文件仍不可用")
        return ready

    @classmethod
    def is_index_ready(cls, fingerprint: str, chunk_count: int) -> bool:
        """Check persisted index completeness without loading the embedding model."""
        try:
            meta = json.loads(_INDEX_META_PATH.read_text(encoding="utf-8"))
            if meta.get("fingerprint") != fingerprint or meta.get("chunk_count") != chunk_count:
                return False
            client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
            collection = client.get_collection(_COLLECTION_NAME)
            return collection.count() == chunk_count
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
            count = client.get_collection(_COLLECTION_NAME).count()
        except Exception:
            count = 0
        return {"embedding_model_ready": ready, "index_chunks": count, "path": str(CHROMA_DB_PATH)}

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
