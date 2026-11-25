"""Small JSON state file holding each job's last run and last success.

The file lives at ``backend/data/scheduler_state.json`` (see
``files.data_dir``). It is written atomically. If the file is locked, the new
state stays in memory and is written with the next successful save, so a
locked file never makes a job look stale or crash a worker thread.
"""
import json
import logging
import threading
from datetime import datetime

from config.scheduling.clock import localize
from config.scheduling.files import atomic_write_text, data_dir

logger = logging.getLogger(__name__)

STATE_FILENAME = "scheduler_state.json"
_UNSEEN = object()
STATE_VERSION = 1
_PROCESS_LOCK = threading.RLock()


def default_state_path():
    return data_dir() / STATE_FILENAME


def _to_iso(value):
    return localize(value).isoformat() if value is not None else None


def _from_iso(value):
    if not value:
        return None
    try:
        return localize(datetime.fromisoformat(str(value)))
    except ValueError:
        return None


class JobStateStore:
    def __init__(self, path=None):
        self.path = path or default_state_path()
        self._jobs = None
        self._seen = _UNSEEN          # (mtime_ns, size) of the file at the last read or write
        self._dirty_jobs = set()      # jobs changed here but not yet written to disk
        self._warned_locked = False

    def _stamp(self):
        try:
            stat = self.path.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def _load(self):
        """The current jobs, re-read whenever another process (the server, or
        ``run_scheduled_job``) has changed the file. Values not yet written
        because the file was locked stay on top of what is on disk."""
        stamp = self._stamp()
        if self._jobs is not None and stamp == self._seen:
            return self._jobs
        jobs = {}
        try:
            raw = self.path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            if isinstance(payload, dict) and isinstance(payload.get("jobs"), dict):
                jobs = payload["jobs"]
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            logger.warning("Scheduler state file %s is unreadable; treating every job as never run.", self.path)
        for name in self._dirty_jobs:
            if self._jobs and name in self._jobs:
                jobs[name] = self._jobs[name]
        self._jobs = jobs
        self._seen = stamp
        return jobs

    def _save(self):
        payload = {"version": STATE_VERSION, "jobs": self._jobs or {}}
        try:
            atomic_write_text(self.path, json.dumps(payload, indent=2, sort_keys=True))
            self._dirty_jobs.clear()
            self._seen = self._stamp()
            self._warned_locked = False
        except OSError as exc:
            # Keep the in-memory value; it is written with the next save.
            if not self._warned_locked:
                logger.warning("Could not write scheduler state %s (%s); will retry on the next update.", self.path, exc)
                self._warned_locked = True

    def get(self, job_name):
        with _PROCESS_LOCK:
            return dict(self._load().get(job_name) or {})

    def last_success(self, job_name):
        return _from_iso(self.get(job_name).get("last_success"))

    def last_attempt(self, job_name):
        return _from_iso(self.get(job_name).get("last_attempt"))

    def slot_attempts(self, job_name, slot):
        entry = self.get(job_name)
        if slot is None or entry.get("slot") != _to_iso(slot):
            return 0
        return int(entry.get("slot_attempts") or 0)

    def get_extra(self, job_name, key, default=None):
        return (self.get(job_name).get("extra") or {}).get(key, default)

    def set_extra(self, job_name, key, value):
        with _PROCESS_LOCK:
            jobs = self._load()
            self._dirty_jobs.add(job_name)
            entry = jobs.setdefault(job_name, {})
            extra = entry.setdefault("extra", {})
            extra[key] = value
            self._save()

    def record(self, job_name, *, status, started_at, finished_at, reason, counts=None, error="", slot=None):
        with _PROCESS_LOCK:
            jobs = self._load()
            self._dirty_jobs.add(job_name)
            entry = jobs.setdefault(job_name, {})
            entry["last_attempt"] = _to_iso(started_at)
            entry["last_finished"] = _to_iso(finished_at)
            entry["last_status"] = status
            entry["last_reason"] = reason
            entry["last_duration_seconds"] = round((finished_at - started_at).total_seconds(), 3)
            entry["last_counts"] = counts or {}
            entry["last_error"] = (error or "")[:500]
            if status == "success":
                entry["last_success"] = _to_iso(started_at)
            slot_iso = _to_iso(slot)
            if status in ("success", "failed"):
                if entry.get("slot") != slot_iso:
                    entry["slot"] = slot_iso
                    entry["slot_attempts"] = 0
                entry["slot_attempts"] = int(entry.get("slot_attempts") or 0) + 1
            self._save()
