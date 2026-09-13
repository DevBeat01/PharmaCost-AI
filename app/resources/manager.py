"""Versioned storage and registry for runtime-managed resources.

The registry deliberately stores uploaded files outside the contest source
directories.  Existing services can continue reading their current paths,
while this layer keeps immutable versions and an atomic active-version marker.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ResourceManager:
    """Persist resource versions and the currently published version."""

    def __init__(self, root: str | Path, db_path: str | Path):
        self.root = Path(root)
        self.db_path = Path(db_path)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS resource_versions (
                    resource_id TEXT PRIMARY KEY,
                    resource_type TEXT NOT NULL,
                    logical_key TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    filename TEXT NOT NULL,
                    storage_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    published_at TEXT,
                    UNIQUE(resource_type, logical_key, version)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_resource_lookup "
                "ON resource_versions(resource_type, logical_key, status)"
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _safe_component(value: str) -> str:
        cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(value))
        return cleaned.strip("._") or "resource"

    def save_bytes(
        self,
        resource_type: str,
        logical_key: str,
        filename: str,
        content: bytes,
        *,
        metadata: dict[str, Any] | None = None,
        status: str = "ready",
    ) -> dict[str, Any]:
        """Write an immutable version and return its registry record."""
        if not content:
            raise ValueError("resource content cannot be empty")
        digest = hashlib.sha256(content).hexdigest()
        safe_type = self._safe_component(resource_type)
        safe_key = self._safe_component(logical_key)
        suffix = Path(filename).suffix.lower()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM resource_versions WHERE resource_type=? AND logical_key=? "
                "AND sha256=? ORDER BY version DESC LIMIT 1",
                (resource_type, logical_key, digest),
            ).fetchone()
            if row:
                # Content-addressed uploads reuse the version, but the latest
                # import name/validation metadata must still be reflected in
                # the settings UI.
                try:
                    existing_metadata = json.loads(row["metadata_json"] or "{}")
                except json.JSONDecodeError:
                    existing_metadata = {}
                existing_metadata.update(metadata or {})
                existing_metadata.setdefault("original_filename", Path(filename).name)
                conn.execute(
                    "UPDATE resource_versions SET filename=?, metadata_json=? WHERE resource_id=?",
                    (Path(filename).name, json.dumps(existing_metadata, ensure_ascii=False), row["resource_id"]),
                )
                row = conn.execute(
                    "SELECT * FROM resource_versions WHERE resource_id=?", (row["resource_id"],)
                ).fetchone()
                return self._row_to_dict(row)
            next_version = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS next_version "
                "FROM resource_versions WHERE resource_type=? AND logical_key=?",
                (resource_type, logical_key),
            ).fetchone()["next_version"]
            resource_id = f"res-{uuid.uuid4().hex[:12]}"
            folder = self.root / safe_type / safe_key
            folder.mkdir(parents=True, exist_ok=True)
            stored_name = f"v{next_version:04d}-{digest[:12]}{suffix}"
            path = folder / stored_name
            path.write_bytes(content)
            now = self._now()
            conn.execute(
                """INSERT INTO resource_versions
                (resource_id, resource_type, logical_key, version, filename,
                 storage_path, sha256, size, status, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    resource_id, resource_type, logical_key, next_version,
                    Path(filename).name, str(path), digest, len(content), status,
                    json.dumps(metadata or {}, ensure_ascii=False), now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM resource_versions WHERE resource_id=?", (resource_id,)
            ).fetchone()
            return self._row_to_dict(row)

    def publish(self, resource_id: str) -> dict[str, Any]:
        """Atomically make a ready version active and archive the previous one."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM resource_versions WHERE resource_id=?", (resource_id,)
            ).fetchone()
            if not row:
                raise KeyError(resource_id)
            if not Path(row["storage_path"]).is_file():
                raise FileNotFoundError(row["storage_path"])
            conn.execute(
                "UPDATE resource_versions SET status='archived' WHERE resource_type=? "
                "AND logical_key=? AND status='active'",
                (row["resource_type"], row["logical_key"]),
            )
            conn.execute(
                "UPDATE resource_versions SET status='active', published_at=? "
                "WHERE resource_id=?",
                (self._now(), resource_id),
            )
            updated = conn.execute(
                "SELECT * FROM resource_versions WHERE resource_id=?", (resource_id,)
            ).fetchone()
            return self._row_to_dict(updated)

    def archive(self, resource_id: str) -> dict[str, Any]:
        """Archive a version without activating another one."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM resource_versions WHERE resource_id=?", (resource_id,)
            ).fetchone()
            if not row:
                raise KeyError(resource_id)
            conn.execute(
                "UPDATE resource_versions SET status='archived' WHERE resource_id=?",
                (resource_id,),
            )
            updated = conn.execute(
                "SELECT * FROM resource_versions WHERE resource_id=?", (resource_id,)
            ).fetchone()
            return self._row_to_dict(updated)

    def discard(self, resource_id: str) -> bool:
        """Delete a non-active candidate version."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM resource_versions WHERE resource_id=?", (resource_id,)
            ).fetchone()
            if not row or row["status"] == "active":
                return False
            conn.execute("DELETE FROM resource_versions WHERE resource_id=?", (resource_id,))
        Path(row["storage_path"]).unlink(missing_ok=True)
        return True

    def active_path(self, resource_type: str, logical_key: str) -> Path | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT storage_path FROM resource_versions WHERE resource_type=? "
                "AND logical_key=? AND status='active' ORDER BY version DESC LIMIT 1",
                (resource_type, logical_key),
            ).fetchone()
        if not row:
            return None
        path = Path(row["storage_path"])
        return path if path.is_file() else None

    def get(self, resource_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM resource_versions WHERE resource_id=?", (resource_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def active_record(self, resource_type: str, logical_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM resource_versions WHERE resource_type=? AND logical_key=? "
                "AND status='active' ORDER BY version DESC LIMIT 1",
                (resource_type, logical_key),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def list(self, resource_type: str | None = None, logical_key: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM resource_versions"
        conditions: list[str] = []
        values: list[str] = []
        if resource_type:
            conditions.append("resource_type=?")
            values.append(resource_type)
        if logical_key:
            conditions.append("logical_key=?")
            values.append(logical_key)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY resource_type, logical_key, version DESC"
        with self._connect() as conn:
            rows = conn.execute(query, values).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def summary(self) -> dict[str, Any]:
        records = self.list()
        active = [record for record in records if record["status"] == "active"]
        return {
            "total_versions": len(records),
            "active_versions": len(active),
            "active": active,
            "last_updated_at": max((r["created_at"] for r in records), default=None),
        }

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        if row is None:
            return {}
        result = dict(row)
        try:
            result["metadata"] = json.loads(result.pop("metadata_json") or "{}")
        except json.JSONDecodeError:
            result["metadata"] = {}
        result["path"] = result.pop("storage_path")
        return result


_APP_DIR = Path(__file__).resolve().parent.parent
resource_manager = ResourceManager(
    _APP_DIR / "settings" / "resources",
    _APP_DIR / "settings" / "resource_registry.sqlite3",
)
