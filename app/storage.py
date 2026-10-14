from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    """Small SQLite repository; every state transition is persisted."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS meetings (
                  id TEXT PRIMARY KEY, title TEXT NOT NULL, source_path TEXT NOT NULL,
                  source_type TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                  id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id),
                  state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                  lease_until TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id),
                  step TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS transcripts (
                  meeting_id TEXT PRIMARY KEY REFERENCES meetings(id),
                  segments TEXT NOT NULL, source_version TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reports (
                  meeting_id TEXT PRIMARY KEY REFERENCES meetings(id),
                  report TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, meeting_id TEXT NOT NULL REFERENCES meetings(id),
                  speaker TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
                  text TEXT NOT NULL, tokens TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state, updated_at);
                CREATE INDEX IF NOT EXISTS idx_chunks_meeting ON chunks(meeting_id);
                """
            )

    def create_meeting(self, title: str, source_path: str, source_type: str) -> tuple[str, str]:
        meeting_id, job_id = uuid.uuid4().hex, uuid.uuid4().hex
        timestamp = now_iso()
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO meetings VALUES (?, ?, ?, ?, ?)", (meeting_id, title, source_path, source_type, timestamp))
            db.execute("INSERT INTO jobs VALUES (?, ?, 'queued', 0, NULL, NULL, ?, ?)", (job_id, meeting_id, timestamp, timestamp))
        return meeting_id, job_id

    def claim_job(self, lease_seconds: int) -> sqlite3.Row | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE state='queued' OR (state='running' AND lease_until < ?) ORDER BY created_at LIMIT 1",
                (now_iso(),),
            ).fetchone()
            if row is None:
                return None
            lease = datetime.now(timezone.utc).timestamp() + lease_seconds
            lease_until = datetime.fromtimestamp(lease, timezone.utc).isoformat()
            db.execute(
                "UPDATE jobs SET state='running', attempts=attempts+1, lease_until=?, updated_at=? WHERE id=?",
                (lease_until, now_iso(), row["id"]),
            )
            return db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()

    def set_job(self, job_id: str, state: str, error: str | None = None) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE jobs SET state=?, error=?, lease_until=NULL, updated_at=? WHERE id=?", (state, error, now_iso(), job_id))

    def append_event(self, job_id: str, step: str, state: str, payload: dict[str, Any] | None = None) -> None:
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO events(job_id,step,state,payload,created_at) VALUES(?,?,?,?,?)", (job_id, step, state, json.dumps(payload or {}, ensure_ascii=False), now_iso()))

    def save_transcript(self, meeting_id: str, segments: list[dict[str, Any]], source_version: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("INSERT OR REPLACE INTO transcripts VALUES(?,?,?,?)", (meeting_id, json.dumps(segments, ensure_ascii=False), source_version, now_iso()))

    def save_report(self, meeting_id: str, report: dict[str, Any]) -> None:
        with self._lock, self._connect() as db:
            db.execute("INSERT OR REPLACE INTO reports VALUES(?,?,?)", (meeting_id, json.dumps(report, ensure_ascii=False), now_iso()))

    def replace_chunks(self, meeting_id: str, chunks: list[dict[str, Any]]) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM chunks WHERE meeting_id=?", (meeting_id,))
            db.executemany("INSERT INTO chunks(meeting_id,speaker,start_ms,end_ms,text,tokens) VALUES(?,?,?,?,?,?)", [(meeting_id, c["speaker"], c["start_ms"], c["end_ms"], c["text"], json.dumps(c["tokens"], ensure_ascii=False)) for c in chunks])

    def meeting(self, meeting_id: str) -> sqlite3.Row | None:
        with self._connect() as db:
            return db.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()

    def job(self, job_id: str) -> sqlite3.Row | None:
        with self._connect() as db:
            return db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

    def events(self, job_id: str) -> list[sqlite3.Row]:
        with self._connect() as db:
            return list(db.execute("SELECT * FROM events WHERE job_id=? ORDER BY id", (job_id,)))

    def transcript(self, meeting_id: str) -> list[dict[str, Any]] | None:
        with self._connect() as db:
            row = db.execute("SELECT segments FROM transcripts WHERE meeting_id=?", (meeting_id,)).fetchone()
            return json.loads(row["segments"]) if row else None

    def report(self, meeting_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT report FROM reports WHERE meeting_id=?", (meeting_id,)).fetchone()
            return json.loads(row["report"]) if row else None

    def all_chunks(self) -> list[sqlite3.Row]:
        with self._connect() as db:
            return list(db.execute("SELECT * FROM chunks"))

