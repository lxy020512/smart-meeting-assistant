from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from .config import Settings
from .storage import Store

logger = logging.getLogger(__name__)


class DurableWorker:
    """Lease-based worker with bounded retry and an explicit dead state."""

    def __init__(self, store: Store, settings: Settings, handler: Callable[[str, str], None], max_attempts: int = 3):
        self.store = store
        self.settings = settings
        self.handler = handler
        self.max_attempts = max_attempts
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="meeting-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def run_once(self) -> bool:
        job = self.store.claim_job(self.settings.worker_lease_seconds)
        if not job:
            return False
        job_id, meeting_id = job["id"], job["meeting_id"]
        self.store.append_event(job_id, "worker", "claimed", {"attempt": job["attempts"]})
        try:
            self.handler(meeting_id, job_id)
            self.store.set_job(job_id, "succeeded")
            self.store.append_event(job_id, "worker", "succeeded")
        except Exception as exc:  # noqa: BLE001 - persisted for operator inspection
            message = f"{type(exc).__name__}: {exc}"
            retry = job["attempts"] < self.max_attempts
            self.store.set_job(job_id, "queued" if retry else "dead", message)
            self.store.append_event(job_id, "worker", "retry" if retry else "dead", {"error": message})
            logger.exception("meeting job %s failed", job_id)
        return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            if not self.run_once():
                self._stop.wait(self.settings.worker_poll_seconds)

