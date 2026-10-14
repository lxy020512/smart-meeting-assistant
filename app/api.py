from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings
from .export import markdown_report
from .llm import DeepSeekClient
from .pipeline import MeetingPipeline
from .queue import DurableWorker
from .retrieval import EvidenceIndex, answer_question
from .storage import Store


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    meeting_id: str | None = None


def create_app(settings=None):
    settings = settings or Settings.from_env()
    settings.prepare()
    store = Store(settings.database)
    client = DeepSeekClient(settings)
    index = EvidenceIndex(store, settings)
    pipeline = MeetingPipeline(store, client, settings, index)
    worker = DurableWorker(store, settings, pipeline.execute)

    @asynccontextmanager
    async def lifespan(app):
        if settings.embedded_worker:
            worker.start()
        yield
        worker.stop()
        # The daemon may still be finishing an external call during shutdown.
        if not worker._thread or not worker._thread.is_alive():
            client.close()

    app = FastAPI(title="智能会议助手", version="1.0.0", lifespan=lifespan)
    app.state.store, app.state.worker = store, worker

    @app.middleware("http")
    async def authorize(request: Request, call_next):
        if settings.api_token and request.url.path.startswith("/api/"):
            supplied = request.headers.get("authorization", "")
            if not hmac.compare_digest(supplied, "Bearer " + settings.api_token):
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": "invalid API token"}, status_code=401)
        return await call_next(request)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "model_configured": bool(settings.model_api_key), "asr_backend": settings.asr_backend, "semantic_enabled": settings.semantic_enabled}

    @app.post("/api/meetings", status_code=202)
    async def upload_meeting(file: UploadFile = File(...), title: str = Form(""), idempotency_key: str | None = Header(default=None)):
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in {".txt", ".md", ".json", ".srt", ".vtt", ".wav", ".mp3", ".m4a", ".flac"}:
            raise HTTPException(415, "unsupported file type")
        target = settings.data_dir / "uploads" / (uuid.uuid4().hex + suffix)
        digest, size = hashlib.sha256(), 0
        try:
            with target.open("wb") as output:
                while block := await file.read(1024 * 1024):
                    size += len(block)
                    if size > settings.max_upload_mb * 1024 * 1024:
                        raise HTTPException(413, "file exceeds upload limit")
                    digest.update(block)
                    output.write(block)
            if not size:
                raise HTTPException(400, "empty upload")
            title = title.strip()[:200] or Path(file.filename or "会议").stem[:200]
            # Reusing a client key with another payload produces a separate job.
            key = hashlib.sha256((idempotency_key + digest.hexdigest() + title).encode()).hexdigest() if idempotency_key else None
            meeting_id, job_id = store.create_meeting(title, str(target), suffix.lstrip("."), key)
            if store.meeting(meeting_id)["source_path"] != str(target):
                target.unlink(missing_ok=True)
            return {"meeting_id": meeting_id, "job_id": job_id, "state": store.job(job_id)["state"]}
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    @app.get("/api/meetings")
    def meetings():
        return [{"id": row["id"], "title": row["title"], "created_at": row["created_at"], "job_id": row["job_id"], "state": row["state"]} for row in store.meetings()]

    def public_job(job):
        return {k: job[k] for k in ("id", "meeting_id", "state", "attempts", "error", "created_at", "updated_at")}

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str):
        job = store.job(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        return {"job": public_job(job), "events": [{**dict(e), "payload": json.loads(e["payload"])} for e in store.events(job_id)]}

    @app.get("/api/meetings/{meeting_id}")
    def meeting_detail(meeting_id: str):
        meeting = store.meeting(meeting_id)
        if not meeting:
            raise HTTPException(404, "meeting not found")
        return {"meeting": {k: meeting[k] for k in ("id", "title", "created_at", "source_type")}, "job": public_job(store.job_for_meeting(meeting_id)), "report": store.report(meeting_id), "transcript": store.transcript(meeting_id)}

    @app.get("/api/meetings/{meeting_id}/report")
    def meeting_report(meeting_id: str):
        report = store.report(meeting_id)
        if not report:
            raise HTTPException(404, "report not ready")
        return report

    @app.get("/api/meetings/{meeting_id}/export")
    def export(meeting_id: str):
        report = meeting_report(meeting_id)
        return PlainTextResponse(markdown_report(report), headers={"Content-Disposition": 'attachment; filename="meeting-report.md"'})

    @app.get("/api/meetings/{meeting_id}/audio")
    def audio(meeting_id: str):
        row = store.meeting(meeting_id)
        if not row or row["source_type"] not in {"wav", "mp3", "m4a", "flac"}:
            raise HTTPException(404, "audio unavailable")
        return FileResponse(row["source_path"])

    @app.get("/api/search")
    def search(q: str = Query(min_length=1, max_length=1000), top_k: int = Query(5, ge=1, le=20), meeting_id: str | None = None):
        return {"query": q, "results": index.search(q, top_k, meeting_id)}

    @app.post("/api/ask")
    def ask(question: Question):
        return answer_question(index, client, question.question, question.meeting_id)

    web = Path(__file__).resolve().parent.parent / "web"
    app.mount("/static", StaticFiles(directory=web), name="static")

    @app.get("/")
    def page():
        return FileResponse(web / "index.html")

    return app
