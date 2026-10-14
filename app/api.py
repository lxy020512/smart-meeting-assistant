from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .llm import DeepSeekClient
from .pipeline import MeetingPipeline
from .queue import DurableWorker
from .retrieval import search
from .storage import Store


settings = Settings.from_env()
settings.prepare()
store = Store(settings.database)
pipeline = MeetingPipeline(store, DeepSeekClient(settings))
worker = DurableWorker(store, settings, pipeline.execute)
app = FastAPI(title="智能会议助手", version="0.1.0")


@app.on_event("startup")
def start_worker() -> None:
    worker.start()


@app.on_event("shutdown")
def stop_worker() -> None:
    worker.stop()


def _row(row: Any) -> dict[str, Any]:
    return dict(row) if row else {}


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "model": settings.model_name, "model_configured": str(bool(settings.model_api_key)).lower()}


@app.post("/api/meetings")
async def upload_meeting(file: UploadFile = File(...), title: str | None = None) -> dict[str, str]:
    name = Path(file.filename or "meeting.txt").name
    suffix = Path(name).suffix.lower()
    allowed = {".txt", ".md", ".json", ".srt", ".vtt", ".wav", ".mp3", ".m4a", ".flac"}
    if suffix not in allowed:
        raise HTTPException(415, f"unsupported file type: {suffix}")
    target = settings.data_dir / "uploads" / f"{os.urandom(8).hex()}{suffix}"
    with target.open("wb") as output:
        shutil.copyfileobj(file.file, output)
    meeting_id, job_id = store.create_meeting(title or Path(name).stem, str(target), suffix.lstrip("."))
    return {"meeting_id": meeting_id, "job_id": job_id, "state": "queued"}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    job = store.job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return {"job": _row(job), "events": [{**_row(event), "payload": json.loads(event["payload"])} for event in store.events(job_id)]}


@app.get("/api/meetings/{meeting_id}")
def meeting_detail(meeting_id: str) -> dict[str, Any]:
    meeting = store.meeting(meeting_id)
    if not meeting:
        raise HTTPException(404, "meeting not found")
    job = store.job_for_meeting(meeting_id)
    return {"meeting": _row(meeting), "job": _row(job), "report": store.report(meeting_id), "transcript": store.transcript(meeting_id)}


@app.get("/api/meetings/{meeting_id}/report")
def meeting_report(meeting_id: str) -> dict[str, Any]:
    report = store.report(meeting_id)
    if not report:
        raise HTTPException(404, "report not ready")
    return report


@app.get("/api/search")
def meeting_search(q: str, top_k: int = 5) -> dict[str, Any]:
    return {"query": q, "results": search(store, q, max(1, min(top_k, 20)))}


web_dir = Path(__file__).resolve().parent.parent / "web"
if web_dir.exists():
    app.mount("/static", StaticFiles(directory=web_dir), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(web_dir / "index.html")

