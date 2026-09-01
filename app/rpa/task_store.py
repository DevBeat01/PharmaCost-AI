"""SQLite-backed storage for RPA rectification tasks."""
import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path


class TaskStore:
    """Stores complete task payloads while keeping filter fields queryable."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS rpa_tasks (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    product TEXT NOT NULL,
                    month TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    task_json TEXT NOT NULL
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_rpa_tasks_filters ON rpa_tasks(status, priority, product, month)")

    def upsert(self, task: dict) -> None:
        payload = json.dumps(task, ensure_ascii=False, default=str)
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute("""
                INSERT INTO rpa_tasks (task_id, status, priority, product, month, created_at, task_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status = excluded.status,
                    priority = excluded.priority,
                    product = excluded.product,
                    month = excluded.month,
                    created_at = excluded.created_at,
                    task_json = excluded.task_json
            """, (
                task["task_id"], task["status"], task["priority"], task["product"],
                task["month"], task["created_at"], payload,
            ))

    def upsert_many(self, tasks: list[dict]) -> None:
        for task in tasks:
            self.upsert(task)

    def get(self, task_id: str) -> dict | None:
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute("SELECT task_json FROM rpa_tasks WHERE task_id = ?", (task_id,)).fetchone()
        return json.loads(row["task_json"]) if row else None

    def list(self, **filters) -> list[dict]:
        clauses, values = [], []
        for key in ("status", "priority", "product", "month"):
            if filters.get(key):
                clauses.append(f"{key} = ?")
                values.append(filters[key])
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                f"SELECT task_json FROM rpa_tasks{where} ORDER BY created_at DESC", values,
            ).fetchall()
        return [json.loads(row["task_json"]) for row in rows]

    def delete(self, task_id: str) -> dict | None:
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute("SELECT task_json FROM rpa_tasks WHERE task_id = ?", (task_id,)).fetchone()
            if not row:
                return None
            connection.execute("DELETE FROM rpa_tasks WHERE task_id = ?", (task_id,))
        return json.loads(row["task_json"])
