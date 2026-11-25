"""Run a call with a hard wall-clock deadline.

Python cannot kill a thread, so a call that exceeds its deadline is
abandoned: it keeps running in its own daemon thread (and finishes or times
out on its own), while the caller moves on immediately. One hanging provider
therefore costs at most one deadline.

A single long-lived worker thread serves calls so database connections are
reused between assets. When a call times out, that worker is retired and the
next call gets a fresh worker.
"""
import logging
import queue
import threading

logger = logging.getLogger(__name__)


class DeadlineExceeded(Exception):
    pass


def _close_thread_db_connections():
    try:
        from django.db import connections

        connections.close_all()
    except Exception:  # noqa: BLE001 - best effort cleanup in a worker thread
        logger.debug("Could not close worker database connections.", exc_info=True)


class _Worker:
    def __init__(self, name):
        self.tasks = queue.Queue()
        self.retired = threading.Event()
        self.thread = threading.Thread(target=self._loop, name=name, daemon=True)
        self.thread.start()

    def _loop(self):
        try:
            while True:
                task = self.tasks.get()
                if task is None:
                    return
                function, box, done = task
                try:
                    box["value"] = function()
                except BaseException as exc:  # noqa: BLE001 - re-raised in the caller
                    box["error"] = exc
                finally:
                    done.set()
                if self.retired.is_set():
                    return
        finally:
            _close_thread_db_connections()


class DeadlineExecutor:
    def __init__(self, name):
        self.name = name
        self._worker = None
        self._abandoned = []
        self._counter = 0

    def _ensure_worker(self):
        if self._worker is None:
            self._counter += 1
            self._worker = _Worker(f"pwms-{self.name}-{self._counter}")
        return self._worker

    def run(self, function, timeout):
        worker = self._ensure_worker()
        box = {}
        done = threading.Event()
        worker.tasks.put((function, box, done))
        if not done.wait(timeout):
            worker.retired.set()
            self._abandoned.append(worker)
            self._worker = None
            raise DeadlineExceeded(f"exceeded {timeout:g}s deadline")
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def abandoned_count(self):
        """Abandoned calls that are still running."""
        self._abandoned = [worker for worker in self._abandoned if worker.thread.is_alive()]
        return len(self._abandoned)

    def close(self):
        if self._worker is not None:
            self._worker.tasks.put(None)
            self._worker = None
