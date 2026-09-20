import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))

import routers.report as report
import report.engine as report_engine_module
import report.pdf_export as pdf_export_module
from report.task_store import ReportTaskStore


class FakeReportEngine:
    def generate(self, product, month, output_path, report_type="monthly"):
        Path(output_path).write_bytes(b"docx")
        return {"product": product, "month": month, "report_type": report_type, "summary": "preview"}


def fake_export_pdf(preview, pdf_path, docx_path=None):
    Path(pdf_path).write_bytes(b"pdf")


def build_client():
    app = FastAPI()
    app.include_router(report.router)
    return TestClient(app)


def wait_for_completed(task_id, timeout=3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = report._TASK_STORE.get(task_id)
        history = report._load_history()
        if task and task.get("status") == "completed" and any(item.get("task_id") == task_id for item in history):
            return task
        time.sleep(0.01)
    raise AssertionError(f"report task {task_id} did not complete")


def test_history_keeps_all_tasks_and_files_without_record_limit():
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = Path(temp_dir) / "output"
        output_dir.mkdir()
        history_path = output_dir / "report_history.json"
        original_output_dir = report.OUTPUT_DIR
        original_history_path = report._HISTORY_PATH
        original_limit = report.REPORT_HISTORY_MAX_RECORDS
        original_tasks = report._tasks.copy()
        original_store = report._TASK_STORE
        report._TASK_STORE = ReportTaskStore(Path(temp_dir) / "tasks.sqlite3")
        report.OUTPUT_DIR = output_dir
        report._HISTORY_PATH = history_path
        report.REPORT_HISTORY_MAX_RECORDS = 2
        report._tasks.clear()

        try:
            client = build_client()
            with patch.object(report_engine_module, "ReportEngine", FakeReportEngine), patch.object(
                pdf_export_module, "export_pdf", fake_export_pdf
            ):
                task_ids = []
                for month in ("2026-01", "2026-02", "2026-03"):
                    response = client.post(
                        "/generate",
                        params={"product": "银黄口服液", "month": month},
                    )
                    assert response.status_code == 200
                    task_ids.append(response.json()["task_id"])
                    wait_for_completed(task_ids[-1])

            history = json.loads(history_path.read_text(encoding="utf-8"))
            assert len(history) == 3
            assert [item["task_id"] for item in history] == task_ids
            assert list(output_dir.glob(f"*{task_ids[0]}*.docx"))
            assert list(output_dir.glob(f"*{task_ids[0]}*.pdf"))
            assert task_ids[0] in report._tasks
            assert client.get(f"/{task_ids[0]}/status").status_code == 200
            assert client.get(
                f"/{task_ids[0]}/download", params={"format": "docx"}
            ).status_code == 200
            assert client.get(
                f"/{task_ids[0]}/download", params={"format": "pdf"}
            ).status_code == 200
        finally:
            report.OUTPUT_DIR = original_output_dir
            report._HISTORY_PATH = original_history_path
            report.REPORT_HISTORY_MAX_RECORDS = original_limit
            report._TASK_STORE = original_store
            report._tasks.clear()
            report._tasks.update(original_tasks)


def test_delete_removes_history_memory_and_files():
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = Path(temp_dir) / "output"
        output_dir.mkdir()
        history_path = output_dir / "report_history.json"
        original_output_dir = report.OUTPUT_DIR
        original_history_path = report._HISTORY_PATH
        original_limit = report.REPORT_HISTORY_MAX_RECORDS
        original_tasks = report._tasks.copy()
        original_store = report._TASK_STORE
        report._TASK_STORE = ReportTaskStore(Path(temp_dir) / "tasks.sqlite3")
        report.OUTPUT_DIR = output_dir
        report._HISTORY_PATH = history_path
        report.REPORT_HISTORY_MAX_RECORDS = 20
        report._tasks.clear()

        try:
            client = build_client()
            with patch.object(report_engine_module, "ReportEngine", FakeReportEngine), patch.object(
                pdf_export_module, "export_pdf", fake_export_pdf
            ):
                task_id = client.post(
                    "/generate",
                    params={"product": "银黄口服液", "month": "2026-04"},
                ).json()["task_id"]
                wait_for_completed(task_id)

            task = report._tasks[task_id]
            docx_path = Path(task["docx_path"])
            pdf_path = Path(task["pdf_path"])
            assert client.delete(f"/{task_id}").status_code == 200
            assert task_id not in report._tasks
            assert not docx_path.exists()
            assert not pdf_path.exists()
            assert all(item["task_id"] != task_id for item in json.loads(history_path.read_text(encoding="utf-8")))
            assert client.get(f"/{task_id}/status").status_code == 404
            assert client.delete(f"/{task_id}").status_code == 404
        finally:
            report.OUTPUT_DIR = original_output_dir
            report._HISTORY_PATH = original_history_path
            report.REPORT_HISTORY_MAX_RECORDS = original_limit
            report._TASK_STORE = original_store
            report._tasks.clear()
            report._tasks.update(original_tasks)


def test_invalid_task_id_and_outside_download_are_rejected():
    client = build_client()
    assert client.get("/bad/status").status_code == 400
    assert client.delete("../outside").status_code in {400, 404}


def test_cancel_removes_pending_task_and_marks_worker_cancelled():
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = Path(temp_dir) / "output"
        output_dir.mkdir()
        history_path = output_dir / "report_history.json"
        original_output_dir = report.OUTPUT_DIR
        original_history_path = report._HISTORY_PATH
        original_tasks = report._tasks.copy()
        original_store = report._TASK_STORE
        original_executor = report._REPORT_EXECUTOR
        report._TASK_STORE = ReportTaskStore(Path(temp_dir) / "tasks.sqlite3")

        class DeferredExecutor:
            def submit(self, fn, *args, **kwargs):
                return None

        report._REPORT_EXECUTOR = DeferredExecutor()
        report.OUTPUT_DIR = output_dir
        report._HISTORY_PATH = history_path
        report._tasks.clear()
        try:
            client = build_client()
            task_id = client.post("/generate", params={"product": "银黄口服液", "month": "2026-06"}).json()["task_id"]
            response = client.post(f"/{task_id}/cancel")
            assert response.status_code == 200
            assert response.json()["status"] == "cancelled"
            assert task_id not in report._tasks
            assert report._TASK_STORE.get(task_id) is None
            assert client.get(f"/{task_id}/status").status_code == 404
            assert task_id in report._REPORT_CANCELLED
        finally:
            report.OUTPUT_DIR = original_output_dir
            report._HISTORY_PATH = original_history_path
            report._TASK_STORE = original_store
            report._REPORT_EXECUTOR = original_executor
            report._tasks.clear()
            report._tasks.update(original_tasks)


def test_report_page_has_refresh_resume_and_cancel_flow():
    source = (Path(__file__).resolve().parents[2] / "app/static/js/report.js").read_text(encoding="utf-8")
    assert "resumeGeneratingReport" in source
    assert "/cancel`" in source
    assert "clearGeneratingReport" in source
    assert "resetToInitialReportState" in source
    assert "选择参数后点击“生成报告”查看预览" in source
    assert "estimateSeconds" in source
    assert "已用时${elapsed}秒，仍在处理中" in source
    assert "AppDialog.confirm" in source
    assert "AppDialog.alert" in source
    assert "window.confirm" not in source
    assert "alert(" not in source.replace("AppDialog.alert(", "")


def test_report_download_filename_includes_product_and_month():
    source = (Path(__file__).resolve().parents[2] / "app/static/js/report.js").read_text(encoding="utf-8")
    assert 'getReportDownloadFilename(extension)' in source
    assert '成本分析报告_${product}_${month}_${reportId}.${extension}' in source
    assert "const reportId = cleanPart(this._reportId, '报告编号');" in source
    assert 'download="${safe(filenameBase)}.docx"' in source
    assert 'download="${safe(filenameBase)}.pdf"' in source


def test_report_estimate_uses_recent_duration_median():
    original_store = report._TASK_STORE
    class FakeStore:
        def list(self, limit=20, completed_only=False):
            return [
                {"status": "completed", "duration_seconds": 42},
                {"status": "completed", "duration_seconds": 58},
                {"status": "completed", "duration_seconds": 90},
            ]
    report._TASK_STORE = FakeStore()
    try:
        assert report._estimate_report_seconds() == 58
    finally:
        report._TASK_STORE = original_store


def test_history_endpoint_paginates_all_completed_reports(tmp_path):
    original_store = report._TASK_STORE
    report._TASK_STORE = ReportTaskStore(tmp_path / "tasks.sqlite3")
    try:
        for number in range(12):
            report._TASK_STORE.upsert({
                "task_id": f"page{number:08d}", "status": "completed",
                "product": "银黄口服液", "month": "2026-01",
                "report_type": "monthly", "output_format": "docx",
                "created_at": f"2026-09-17 12:{number:02d}:00",
                "updated_at": f"2026-09-17 12:{number:02d}:00",
            })
        response = build_client().get("/history", params={"page": 2, "page_size": 5})
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 12
        assert data["page"] == 2
        assert data["page_size"] == 5
        assert data["total_pages"] == 3
        assert [item["task_id"] for item in data["items"]] == [
            "page00000006", "page00000005", "page00000004", "page00000003", "page00000002"
        ]
    finally:
        report._TASK_STORE = original_store


if __name__ == "__main__":
    test_history_keeps_all_tasks_and_files_without_record_limit()
    test_delete_removes_history_memory_and_files()
    test_invalid_task_id_and_outside_download_are_rejected()
    print("report lifecycle tests passed")
