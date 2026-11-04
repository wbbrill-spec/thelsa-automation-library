"""Background-job state that survives restarts (4 Nov 2026).

The daily jobs (survey_finder, doc_check, invoice_mail) kept their "finished"
time and results only in memory. After every restart the keeper therefore saw
them as never run and started them again 10 minutes later. Each run pushed the
server's memory up, the server was killed, restarted, and the same jobs ran
again: a crash loop (12 restarts in 24 h on 4 Nov 2026).

Now each job's last state (started, finished, error, results) is written to the
persistent disk next to the audit snapshot and read back on boot, so a job that
already ran today is not repeated after a restart, and its page keeps showing
the last results.

Also: release_memory() hands memory Python has freed back to the operating
system (glibc malloc_trim), so one heavy job does not permanently raise the
server's memory floor.
"""
import ctypes
import gc
import json
import os
import time

# A job that started but never finished (the server died during it) is not
# retried for this long, so a job that kills the server cannot loop.
CRASHED_RETRY_S = float(os.environ.get("JOB_CRASH_RETRY_HOURS", "6")) * 3600


def _dir() -> str:
    try:
        import mw_live
        d = os.path.dirname(mw_live._CACHE_PATH)
        if d and os.path.isdir(d) and os.access(d, os.W_OK):
            return d
    except Exception:
        pass
    for base in ("/var/data", "/data"):
        if os.path.isdir(base) and os.access(base, os.W_OK):
            return base
    return os.path.dirname(os.path.abspath(__file__))


def _path(name: str) -> str:
    return os.path.join(_dir(), f"job_state_{name}.json")


def load(name: str) -> dict:
    """The saved state of a job, or {}."""
    try:
        with open(_path(name)) as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def save(name: str, state: dict) -> None:
    """Write a job's state; best-effort, never raises."""
    try:
        doc = dict(state)
        doc["running"] = False
        tmp = _path(name) + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(doc, fh, default=str)
        os.replace(tmp, _path(name))
    except Exception:
        pass


def mark_started(name: str) -> None:
    """Record that a run began (kept even if the server dies during it)."""
    doc = load(name)
    doc["started"] = time.time()
    doc["finished_ok"] = False
    save(name, doc)


def restore(name: str, state: dict) -> None:
    """Fill an in-memory _STATE dict from disk at import (never 'running')."""
    doc = load(name)
    for k, v in doc.items():
        if k in state and k != "running":
            state[k] = v
    state["running"] = False


def recently_crashed(name: str) -> bool:
    """True when the last run started within CRASHED_RETRY_S and never finished."""
    doc = load(name)
    started = doc.get("started") or 0
    finished = doc.get("finished") or 0
    return bool(started and finished < started and time.time() - started < CRASHED_RETRY_S)


def release_memory() -> None:
    """Collect garbage and give freed memory back to the OS (Linux/glibc)."""
    try:
        gc.collect()
    except Exception:
        pass
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass
