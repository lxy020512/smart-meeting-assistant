from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LeaseLost(RuntimeError):
    """A superseded worker cannot publish."""


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
              CREATE TABLE IF NOT EXISTS meetings (
                id TEXT PRIMARY KEY,title TEXT NOT NULL,source_path TEXT NOT NULL,
                source_type TEXT NOT NULL,created_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,meeting_id TEXT NOT NULL REFERENCES meetings(id),state TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,lease_until TEXT,error TEXT,
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL,lease_token TEXT,
                idempotency_key TEXT UNIQUE,available_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL REFERENCES jobs(id),
                step TEXT NOT NULL,state TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS checkpoints (
                job_id TEXT NOT NULL REFERENCES jobs(id),step TEXT NOT NULL,payload TEXT NOT NULL,
                PRIMARY KEY(job_id,step));
              CREATE TABLE IF NOT EXISTS transcripts (
                meeting_id TEXT PRIMARY KEY REFERENCES meetings(id),segments TEXT NOT NULL,
                source_version TEXT NOT NULL,created_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS reports (
                meeting_id TEXT PRIMARY KEY REFERENCES meetings(id),report TEXT NOT NULL,created_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,meeting_id TEXT NOT NULL REFERENCES meetings(id),
                speaker TEXT NOT NULL,start_ms INTEGER NOT NULL,end_ms INTEGER NOT NULL,
                text TEXT NOT NULL,tokens TEXT NOT NULL,vector TEXT,source_version TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state,available_at);
              CREATE INDEX IF NOT EXISTS idx_chunks_meeting ON chunks(meeting_id);
            """)

    @contextmanager
    def _db(self, immediate=False):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if immediate:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _fence(db, job_id, token):
        if job_id and token:
            row = db.execute("SELECT 1 FROM jobs WHERE id=? AND lease_token=? AND state='running' AND lease_until>?", (job_id, token, now_iso())).fetchone()
            if not row:
                raise LeaseLost("worker lease expired or superseded")

    def create_meeting(self, title, source_path, source_type, idempotency_key=None):
        timestamp = now_iso()
        with self._db(immediate=True) as db:
            if idempotency_key:
                previous = db.execute("SELECT meeting_id,id FROM jobs WHERE idempotency_key=?", (idempotency_key,)).fetchone()
                if previous:
                    return previous["meeting_id"], previous["id"]
            meeting_id, job_id = uuid.uuid4().hex, uuid.uuid4().hex
            db.execute("INSERT INTO meetings VALUES(?,?,?,?,?)", (meeting_id, title, source_path, source_type, timestamp))
            db.execute("INSERT INTO jobs VALUES(?,?,'queued',0,NULL,NULL,?,?,NULL,?,?)", (job_id, meeting_id, timestamp, timestamp, idempotency_key, timestamp))
            return meeting_id, job_id

    def claim_job(self, lease_seconds, max_attempts=3):
        current = now_iso()
        with self._db(immediate=True) as db:
            db.execute("UPDATE jobs SET state='dead',error='crash retry budget exhausted',lease_token=NULL WHERE state='running' AND lease_until<? AND attempts>=?", (current, max_attempts))
            row = db.execute("SELECT * FROM jobs WHERE attempts<? AND ((state='queued' AND available_at<=?) OR (state='running' AND lease_until<?)) ORDER BY created_at LIMIT 1", (max_attempts, current, current)).fetchone()
            if not row:
                return None
            lease = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
            db.execute("UPDATE jobs SET state='running',attempts=attempts+1,lease_until=?,lease_token=?,updated_at=? WHERE id=?", (lease, uuid.uuid4().hex, current, row["id"]))
            return db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()

    def heartbeat(self, job_id, token, seconds):
        lease = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
        with self._db(immediate=True) as db:
            self._fence(db, job_id, token)
            db.execute("UPDATE jobs SET lease_until=?,updated_at=? WHERE id=?", (lease, now_iso(), job_id))

    def set_job(self, job_id, state, error=None, token=None, delay=0):
        available = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
        with self._db(immediate=True) as db:
            self._fence(db, job_id, token)
            db.execute("UPDATE jobs SET state=?,error=?,lease_until=NULL,lease_token=NULL,updated_at=?,available_at=? WHERE id=?", (state, error, now_iso(), available, job_id))

    def append_event(self, job_id, step, state, payload=None, token=None):
        with self._db(immediate=True) as db:
            self._fence(db, job_id, token)
            db.execute("INSERT INTO events(job_id,step,state,payload,created_at) VALUES(?,?,?,?,?)", (job_id, step, state, json.dumps(payload or {}, ensure_ascii=False), now_iso()))

    def checkpoint(self, job_id, step):
        row = self._one("SELECT payload FROM checkpoints WHERE job_id=? AND step=?", (job_id, step))
        return json.loads(row[0]) if row else None

    def save_checkpoint(self, job_id, step, payload, token=None):
        with self._db(immediate=True) as db:
            self._fence(db, job_id, token)
            db.execute("INSERT OR REPLACE INTO checkpoints VALUES(?,?,?)", (job_id, step, json.dumps(payload, ensure_ascii=False)))

    def save_transcript(self, meeting_id, segments, source_version, job_id=None, token=None):
        with self._db(immediate=True) as db:
            self._fence(db, job_id, token)
            db.execute("INSERT OR REPLACE INTO transcripts VALUES(?,?,?,?)", (meeting_id, json.dumps(segments, ensure_ascii=False), source_version, now_iso()))

    def save_report(self, meeting_id, report, job_id=None, token=None):
        with self._db(immediate=True) as db:
            self._fence(db, job_id, token)
            db.execute("INSERT OR REPLACE INTO reports VALUES(?,?,?)", (meeting_id, json.dumps(report, ensure_ascii=False), now_iso()))

    def replace_chunks(self, meeting_id, chunks, job_id=None, token=None):
        with self._db(immediate=True) as db:
            self._fence(db, job_id, token)
            db.execute("DELETE FROM chunks WHERE meeting_id=?", (meeting_id,))
            db.executemany("INSERT INTO chunks(meeting_id,speaker,start_ms,end_ms,text,tokens,vector,source_version) VALUES(?,?,?,?,?,?,?,?)", [(meeting_id, c["speaker"], c["start_ms"], c["end_ms"], c["text"], json.dumps(c["tokens"], ensure_ascii=False), json.dumps(c.get("vector")), c.get("source_version", "")) for c in chunks])

    def _one(self, sql, args=()):
        with self._db() as db:
            return db.execute(sql, args).fetchone()

    def _many(self, sql, args=()):
        with self._db() as db:
            return list(db.execute(sql, args))

    def meeting(self, meeting_id):
        return self._one("SELECT * FROM meetings WHERE id=?", (meeting_id,))

    def job(self, job_id):
        return self._one("SELECT * FROM jobs WHERE id=?", (job_id,))

    def job_for_meeting(self, meeting_id):
        return self._one("SELECT * FROM jobs WHERE meeting_id=? ORDER BY created_at DESC LIMIT 1", (meeting_id,))

    def meetings(self, limit=50):
        return self._many("SELECT m.*,j.id AS job_id,j.state FROM meetings m JOIN jobs j ON j.meeting_id=m.id ORDER BY m.created_at DESC LIMIT ?", (limit,))

    def events(self, job_id):
        return self._many("SELECT * FROM events WHERE job_id=? ORDER BY id", (job_id,))

    def transcript(self, meeting_id):
        row = self._one("SELECT segments FROM transcripts WHERE meeting_id=?", (meeting_id,))
        return json.loads(row[0]) if row else None

    def source_version(self, meeting_id):
        row = self._one("SELECT source_version FROM transcripts WHERE meeting_id=?", (meeting_id,))
        return row[0] if row else ""

    def report(self, meeting_id):
        row = self._one("SELECT report FROM reports WHERE meeting_id=?", (meeting_id,))
        return json.loads(row[0]) if row else None

    def all_chunks(self, meeting_id=None):
        return self._many("SELECT * FROM chunks WHERE meeting_id=?" if meeting_id else "SELECT * FROM chunks", (meeting_id,) if meeting_id else ())
