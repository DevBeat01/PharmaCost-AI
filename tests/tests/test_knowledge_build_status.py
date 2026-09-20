import threading
import time

import pytest

from routers import settings
from rag import build_knowledge


def _reset_scheduler(monkeypatch, tmp_path):
    previous = settings._knowledge_build_thread
    if previous and previous.is_alive():
        previous.join(timeout=5)
    monkeypatch.setattr(settings, "KNOWLEDGE_BUILD_STATUS_PATH", tmp_path / "knowledge-status.json")
    monkeypatch.setattr(settings, "_knowledge_build_thread", None)
    monkeypatch.setattr(settings, "_knowledge_build_pending", False)
    monkeypatch.setattr(
        settings,
        "_knowledge_build_status",
        {"task_id": None, "status": "idle", "pending": False, "error": None},
    )


def _wait_for(predicate, timeout=3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("knowledge build did not reach expected state")


def test_running_build_coalesces_changes_and_reruns(monkeypatch, tmp_path):
    _reset_scheduler(monkeypatch, tmp_path)
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def fake_build():
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(2)
        return 17

    monkeypatch.setattr(build_knowledge, "build_knowledge_base", fake_build)
    monkeypatch.setattr(build_knowledge, "_file_signatures", lambda: {"a.txt": {}})

    first = settings.request_knowledge_build("upload")
    assert entered.wait(1)
    second = settings.request_knowledge_build("delete")
    assert second["task_id"] == first["task_id"]
    assert second["pending"] is True
    release.set()

    _wait_for(lambda: len(calls) == 2 and settings._knowledge_status_snapshot()["status"] == "completed")
    final = settings._knowledge_status_snapshot()
    assert final["trigger"] == "coalesced"
    assert final["chunk_count"] == 17
    assert final["document_count"] == 1
    assert final["pending"] is False


def test_failed_build_exposes_error_for_retry(monkeypatch, tmp_path):
    _reset_scheduler(monkeypatch, tmp_path)

    def fail_build():
        raise RuntimeError("embedding unavailable")

    monkeypatch.setattr(build_knowledge, "build_knowledge_base", fail_build)
    started = settings.request_knowledge_build("retry")
    _wait_for(lambda: settings._knowledge_status_snapshot()["status"] == "failed")
    final = settings._knowledge_status_snapshot()
    assert final["task_id"] == started["task_id"]
    assert final["trigger"] == "retry"
    assert final["finished_at"]
    assert "embedding unavailable" in final["error"]


def test_status_endpoint_returns_build_and_index(monkeypatch):
    monkeypatch.setattr(settings, "_knowledge_build_status", {"task_id": "kb-1", "status": "completed", "chunk_count": 9})
    monkeypatch.setattr(settings, "_knowledge_build_pending", False)
    result = __import__("asyncio").run(settings.knowledge_build_status())
    assert result["knowledge_build"]["task_id"] == "kb-1"
    assert "index_chunks" in result["index"]


def test_vector_build_failure_does_not_commit_manifest_or_keyword_index(monkeypatch):
    document = {
        "source": "导入/a.txt", "text": "new knowledge", "doc_type": "general",
        "chunk_size": 500, "mtime_ns": 1, "size": 13,
    }
    monkeypatch.setattr(build_knowledge, "_read_manifest", lambda: {})
    monkeypatch.setattr(build_knowledge, "_file_signatures", lambda: {"导入/a.txt": {"fingerprint": "old", "doc_type": "general"}})
    monkeypatch.setattr(build_knowledge, "_parse_all_knowledge_documents", lambda: [document])
    monkeypatch.setattr(build_knowledge, "_split_document", lambda doc: [{"content": doc["text"], "source": doc["source"], "doc_type": "general", "chunk_index": 0}])
    monkeypatch.setattr(build_knowledge, "_read_index_meta", lambda: {})
    monkeypatch.setattr(build_knowledge.VectorStore, "is_index_ready", lambda *args: False)
    monkeypatch.setattr(build_knowledge.VectorStore, "ensure_embedding_model_ready", lambda: True)
    monkeypatch.setattr(build_knowledge.VectorStore, "replace_documents_atomically", lambda docs, on_promoted=None: (_ for _ in ()).throw(RuntimeError("staging failed")))
    keyword_commits = []
    manifest_commits = []
    monkeypatch.setattr(build_knowledge, "build_bm25_index", lambda docs: keyword_commits.append(docs))
    monkeypatch.setattr(build_knowledge, "_write_manifest", lambda value: manifest_commits.append(value))

    with pytest.raises(RuntimeError, match="staging failed"):
        build_knowledge._build_knowledge_base_locked()

    assert keyword_commits == []
    assert manifest_commits == []
