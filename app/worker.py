import signal
import threading

from .config import Settings
from .llm import DeepSeekClient
from .pipeline import MeetingPipeline
from .queue import DurableWorker
from .storage import Store


def main():
    settings = Settings.from_env()
    settings.prepare()
    store, client = Store(settings.database), DeepSeekClient(settings)
    worker = DurableWorker(store, settings, MeetingPipeline(store, client, settings).execute)
    stopped = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    worker.start()
    try:
        while not stopped.wait(1):
            pass
    finally:
        worker.stop()


if __name__ == "__main__":
    main()
