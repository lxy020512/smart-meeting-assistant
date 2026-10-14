from __future__ import annotations

import logging
import threading

from .config import Settings
from .storage import LeaseLost, Store

logger = logging.getLogger(__name__)


class DurableWorker:
    """Atomic claims, heartbeats, fencing and bounded retry."""

    def __init__(self, store: Store, settings: Settings, handler, max_attempts=3):
        self.store, self.settings, self.handler = store, settings, handler
        self.max_attempts = max_attempts
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if not self._thread or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="meeting-worker", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def run_once(self):
        job = self.store.claim_job(self.settings.worker_lease_seconds, self.max_attempts)
        if not job:
            return False
        job_id, token = job["id"], job["lease_token"]
        done = threading.Event()

        def heartbeat():
            while not done.wait(self.settings.worker_lease_seconds / 3):
                try:
                    self.store.heartbeat(job_id, token, self.settings.worker_lease_seconds)
                except LeaseLost:
                    return
                except Exception:
                    logger.exception("heartbeat failed")

        pulse = threading.Thread(target=heartbeat, daemon=True)
        pulse.start()
        self.store.append_event(job_id, "worker", "claimed", {"attempt": job["attempts"]}, token)
        try:
            self.handler(job["meeting_id"], job_id, token)
            self.store.append_event(job_id, "worker", "succeeded", token=token)
            self.store.set_job(job_id, "succeeded", token=token)
        except LeaseLost:
            logger.info("stale worker stopped publishing")
        except Exception as exc:
            retry = job["attempts"] < self.max_attempts
            try:
                message = f"{type(exc).__name__}: job failed; inspect failed stage"
                self.store.append_event(job_id, "worker", "retry" if retry else "dead", {"error": message}, token)
                self.store.set_job(job_id, "queued" if retry else "dead", message, token, delay=min(job["attempts"] ** 2, 9) if retry else 0)
            except LeaseLost:
                pass
        finally:
            done.set()
            pulse.join(timeout=1)
        return True

    def _loop(self):
        while not self._stop.is_set():
            try:
                if not self.run_once():
                    self._stop.wait(self.settings.worker_poll_seconds)
            except Exception:
                logger.exception("worker loop failed")
                self._stop.wait(1)
