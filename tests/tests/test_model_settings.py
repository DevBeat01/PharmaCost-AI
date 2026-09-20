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


def test_named_provider_call_does_not_use_other_provider():
    import llm.client as client_module

    class Response:
        choices = [type("Choice", (), {"message": type("Message", (), {"content": "备用结果"})()})()]

    backup = type("Provider", (), {})()
    backup.chat = type("Chat", (), {})()
    backup.chat.completions = type("Completions", (), {})()
    backup.chat.completions.create = lambda **kwargs: Response()
    client = client_module.LLMClient.__new__(client_module.LLMClient)
    client._providers = [("mimo", "model", backup)]

    assert client.chat_provider("mimo", "prompt") == "备用结果"


def test_completion_options_follow_per_provider_thinking_switch():
    import llm.client as client_module

    client = client_module.LLMClient.__new__(client_module.LLMClient)
    client.thinking_settings = {
        "deepseek": {"enabled": False},
        "mimo": {"enabled": True},
    }
    assert client._completion_options("deepseek-v4", "deepseek")["extra_body"]["enable_thinking"] is False
    assert client._completion_options("mimo-v2.5", "mimo")["extra_body"]["enable_thinking"] is True

    client.thinking_settings["mimo"]["capability"] = "unsupported"
    assert client._completion_options("mimo-v2.5", "mimo") == {}


def test_probe_classifies_toggleable_model(monkeypatch):
    import llm.client as client_module

    class Delta:
        def __init__(self, reasoning=None): self.reasoning_content = reasoning
    class Chunk:
        def __init__(self, reasoning=None): self.choices = [type("Choice", (), {"delta": Delta(reasoning)})()]
    provider = type("Provider", (), {})()
    provider.chat = type("Chat", (), {})()
    provider.chat.completions = type("Completions", (), {})()
    def create(**kwargs):
        return iter([Chunk("思考")]) if kwargs["extra_body"]["enable_thinking"] else iter([Chunk()])
    provider.chat.completions.create = create
    client = client_module.LLMClient.__new__(client_module.LLMClient)
    client._providers = [("deepseek", "model", provider)]
    result = client.probe_thinking_capability("deepseek")
    assert result["capability"] == "configurable"
