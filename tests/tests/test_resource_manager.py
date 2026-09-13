from pathlib import Path

from resources.manager import ResourceManager


def test_resource_versions_publish_and_history(tmp_path: Path):
    manager = ResourceManager(tmp_path / "resources", tmp_path / "registry.sqlite3")

    first = manager.save_bytes("data", "cost_2026", "cost.csv", b"a,b\n1,2\n")
    assert first["version"] == 1
    assert first["status"] == "ready"
    published = manager.publish(first["resource_id"])
    assert published["status"] == "active"
    assert manager.active_path("data", "cost_2026").read_bytes() == b"a,b\n1,2\n"

    # The same content is content-addressed and does not create a duplicate version.
    duplicate = manager.save_bytes("data", "cost_2026", "renamed.csv", b"a,b\n1,2\n")
    assert duplicate["resource_id"] == first["resource_id"]
    assert duplicate["filename"] == "renamed.csv"
    assert duplicate["metadata"]["original_filename"] == "renamed.csv"

    second = manager.save_bytes("data", "cost_2026", "cost.csv", b"a,b\n3,4\n")
    assert second["version"] == 2
    manager.publish(second["resource_id"])
    assert manager.active_record("data", "cost_2026")["resource_id"] == second["resource_id"]
    history = manager.list("data", "cost_2026")
    assert {item["status"] for item in history} == {"active", "archived"}


def test_discard_only_removes_non_active_version(tmp_path: Path):
    manager = ResourceManager(tmp_path / "resources", tmp_path / "registry.sqlite3")
    ready = manager.save_bytes("template", "default", "report.docx", b"docx")
    assert manager.discard(ready["resource_id"]) is True
    assert manager.get(ready["resource_id"]) is None

    active = manager.save_bytes("template", "default", "report.docx", b"docx-2")
    manager.publish(active["resource_id"])
    assert manager.discard(active["resource_id"]) is False
    assert manager.get(active["resource_id"])["status"] == "active"


def test_metadata_is_returned_as_json(tmp_path: Path):
    manager = ResourceManager(tmp_path / "resources", tmp_path / "registry.sqlite3")
    record = manager.save_bytes(
        "knowledge", "guide.txt", "guide.txt", b"guide",
        metadata={"validation": {"valid": True}, "tags": ["gmp"]},
    )
    assert record["metadata"]["validation"]["valid"] is True
    assert record["metadata"]["tags"] == ["gmp"]
