"""Persistent report-task storage used by the HTTP API and worker thread."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path


class ReportTaskStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=30000")
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self):
        with self._lock, self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS report_tasks (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    product TEXT NOT NULL,
                    month TEXT NOT NULL,
                    report_type TEXT NOT NULL,
                    output_format TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    task_json TEXT NOT NULL
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_report_tasks_created ON report_tasks(created_at DESC)")

    def upsert(self, task: dict):
        payload = json.dumps(task, ensure_ascii=False, default=str)
        with self._lock, self._connect() as connection:
            connection.execute("""
                INSERT INTO report_tasks(task_id,status,product,month,report_type,output_format,created_at,updated_at,task_json)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status=excluded.status, updated_at=excluded.updated_at, task_json=excluded.task_json
            """, (
                task["task_id"], task.get("status", "queued"), task["product"], task["month"],
                task.get("report_type", "monthly"), task.get("output_format", "docx"),
                task.get("created_at", ""), task.get("updated_at", task.get("created_at", "")), payload,
            ))

    def get(self, task_id: str) -> dict | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT task_json FROM report_tasks WHERE task_id=?", (task_id,)).fetchone()
        return json.loads(row["task_json"]) if row else None

    def list(self, limit: int = 100, completed_only: bool = False, offset: int = 0) -> list[dict]:
        where = " WHERE status='completed'" if completed_only else ""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"SELECT task_json FROM report_tasks{where} ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
                (limit, max(0, offset)),
            ).fetchall()
        return [json.loads(row["task_json"]) for row in rows]

    def count(self, completed_only: bool = False) -> int:
        where = " WHERE status='completed'" if completed_only else ""
        with self._lock, self._connect() as connection:
            row = connection.execute(f"SELECT COUNT(*) AS total FROM report_tasks{where}").fetchone()
        return int(row["total"] if row else 0)

    def delete(self, task_id: str) -> dict | None:
        task = self.get(task_id)
        if not task:
            return None
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM report_tasks WHERE task_id=?", (task_id,))
        return task
