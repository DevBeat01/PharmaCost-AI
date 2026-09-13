"""Settings resource-version actions must match their FastAPI routes."""
from pathlib import Path


def test_resource_delete_uses_the_collection_item_route():
    script = (
        Path(__file__).resolve().parents[2]
        / "app" / "static" / "js" / "settings.js"
    ).read_text(encoding="utf-8")

    assert "? `/api/settings/resources/${encodeURIComponent(id)}/rollback`" in script
    assert ": `/api/settings/resources/${encodeURIComponent(id)}`;" in script
    assert "/resources/${encodeURIComponent(id)}/${action}" not in script
