"""DashScope embedding configuration and OpenAI-compatible response tests."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))


def test_embedding_client_uses_only_dashscope_config(monkeypatch):
    import config
    from rag import vector_store

    seen = {}

    class FakeEmbeddings:
        def create(self, **kwargs):
            seen["request"] = kwargs
            return {"data": [
                {"index": i, "embedding": [0.1] * 1024}
                for i, _ in enumerate(kwargs["input"])
            ]}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            seen["client"] = kwargs
            self.embeddings = FakeEmbeddings()

    monkeypatch.setattr(vector_store, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "dashscope-test-key")
    monkeypatch.setattr(config, "DASHSCOPE_BASE_URL", "https://embedding.example/v1")
    monkeypatch.setattr(config, "DASHSCOPE_EMBEDDING_MODEL", "qwen3.7-text-embedding")
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "must-not-be-used")
    monkeypatch.setattr(config, "MIMO_API_KEY", "must-not-be-used")

    vectors = vector_store.DashScopeEmbeddingFunction()(["a", "b"])

    assert len(vectors) == 2
    assert all(len(vector) == 1024 for vector in vectors)
    assert seen["client"] == {
        "api_key": "dashscope-test-key",
        "base_url": "https://embedding.example/v1",
    }
    assert seen["request"] == {
        "model": "qwen3.7-text-embedding",
        "input": ["a", "b"],
    }


def test_missing_dashscope_key_does_not_fallback_to_text_keys(monkeypatch):
    import config
    from rag.vector_store import EmbeddingConfigurationError, VectorStore

    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "")
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "configured-text-key")
    monkeypatch.setattr(config, "MIMO_API_KEY", "configured-text-key")

    assert VectorStore.is_embedding_model_ready() is False
    with pytest.raises(EmbeddingConfigurationError, match="DASHSCOPE_API_KEY"):
        VectorStore.ensure_embedding_model_ready()


def test_wrong_embedding_dimension_is_rejected(monkeypatch):
    import config
    from rag import vector_store

    class FakeEmbeddings:
        def create(self, **kwargs):
            return SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.1])])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.embeddings = FakeEmbeddings()

    monkeypatch.setattr(vector_store, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "dashscope-test-key")

    with pytest.raises(RuntimeError, match="1024"):
        vector_store.DashScopeEmbeddingFunction()(["text"])
