import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))

import routers.report as report
import report.engine as report_engine_module
import report.pdf_export as pdf_export_module


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


def test_history_trim_removes_stale_tasks_and_files():
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = Path(temp_dir) / "output"
        output_dir.mkdir()
        history_path = output_dir / "report_history.json"
        original_output_dir = report.OUTPUT_DIR
        original_history_path = report._HISTORY_PATH
        original_limit = report.REPORT_HISTORY_MAX_RECORDS
        original_tasks = report._tasks.copy()
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

            history = json.loads(history_path.read_text(encoding="utf-8"))
            assert len(history) == 2
            assert [item["task_id"] for item in history] == task_ids[1:]
            assert not list(output_dir.glob(f"*{task_ids[0]}*.docx"))
            assert not list(output_dir.glob(f"*{task_ids[0]}*.pdf"))
            assert task_ids[0] not in report._tasks
            assert client.get(f"/{task_ids[0]}/status").status_code == 404
            assert client.get(
                f"/{task_ids[0]}/download", params={"format": "docx"}
            ).status_code == 404
            assert client.get(
                f"/{task_ids[0]}/download", params={"format": "pdf"}
            ).status_code == 404
        finally:
            report.OUTPUT_DIR = original_output_dir
            report._HISTORY_PATH = original_history_path
            report.REPORT_HISTORY_MAX_RECORDS = original_limit
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
            report._tasks.clear()
            report._tasks.update(original_tasks)


def test_invalid_task_id_and_outside_download_are_rejected():
    client = build_client()
    assert client.get("/bad/status").status_code == 400
    assert client.delete("../outside").status_code in {400, 404}


if __name__ == "__main__":
    test_history_trim_removes_stale_tasks_and_files()
    test_delete_removes_history_memory_and_files()
    test_invalid_task_id_and_outside_download_are_rejected()
    print("report lifecycle tests passed")
