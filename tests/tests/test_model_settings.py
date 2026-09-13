"""Model settings safety and runtime reload behavior."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))


def test_api_key_mask_is_compact():
    from routers.settings import _mask_key

    masked = _mask_key("sk-" + "x" * 100)
    assert len(masked) <= 20
    assert masked.startswith("sk-x")
    assert masked.endswith("xxxx")


def test_stream_client_ignores_provider_keepalive_chunks(monkeypatch):
    import llm.client as client_module

    client = client_module.LLMClient.__new__(client_module.LLMClient)
    class Delta:
        content = "hello"
    class Chunk:
        choices = [type("Choice", (), {"delta": Delta()})()]
    class KeepAlive:
        choices = []
    provider = type("Provider", (), {})()
    client._providers = [("test", "model", provider)]
    provider.chat = type("Chat", (), {})()
    provider.chat.completions = type("Completions", (), {})()
    provider.chat.completions.create = lambda **kwargs: iter([KeepAlive(), Chunk()])
    assert list(client.chat_stream("prompt")) == ["hello"]
