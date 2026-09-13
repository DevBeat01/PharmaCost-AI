"""Authentication behavior shared by normal and SSE API requests."""
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))


def test_session_secret_is_stable_without_explicit_environment_secret(monkeypatch):
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    import security

    first = security.AUTH_SECRET
    importlib.reload(security)
    assert security.AUTH_SECRET == first


def test_stream_client_sends_same_origin_credentials():
    source = (Path(__file__).resolve().parents[2] / "app/static/js/app.js").read_text(encoding="utf-8")
    assert "credentials: 'same-origin'" in source
    assert "resp.status === 401" in source


def test_dashboard_keeps_partial_stream_on_disconnect():
    source = (Path(__file__).resolve().parents[2] / "app/static/js/dashboard.js").read_text(encoding="utf-8")
    assert "归因流中断，保留已生成内容" in source
    assert "if (accumulated.trim())" in source
