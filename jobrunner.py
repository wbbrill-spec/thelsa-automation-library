"""
jobrunner.py — heavy background jobs run in their own process (4 Nov 2026).

Until now every daily job (email survey/date search, FAIM document check,
Finance invoice-report reader, Moveware API costs) ran as a thread inside the
web server. One job using too much memory took the whole site down — for every
user, and it will for every company added later.

Now each job runs as a separate child process:
  * it gets a memory cap (JOB_MEM_LIMIT_MB, default 1200). A job that runs away
    is stopped on its own; the website keeps running;
  * all its memory goes back to the system when it ends;
  * its results are written to disk (job_state / the job's own file) and the
    website reads them back when it finishes;
  * only one runs at a time (the keeper already queues them).

JOB_ISOLATE=0 runs jobs as threads again (the old way).

Run by hand:  python -m jobrunner <job> <payload.json>
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import threading
import time

# job name -> (module, function that does the whole run, synchronously)
JOBS = {
    "survey_finder": ("survey_finder", "run"),
    "doc_check": ("doc_check", "run"),
    "invoice_mail": ("invoice_mail", "refresh"),
    "mw_costs": ("mw_costs", "build"),
}
_PROCS: dict = {}
_LOCK = threading.Lock()


def isolate() -> bool:
    return os.environ.get("JOB_ISOLATE", "1") == "1" and "pytest" not in sys.modules


def _mem_limit_bytes() -> int:
    try:
        return int(float(os.environ.get("JOB_MEM_LIMIT_MB", "1200")) * 1048576)
    except ValueError:
        return 1200 * 1048576


def _limit():                       # runs in the child, before the job starts
    try:
        import resource
        lim = _mem_limit_bytes()
        resource.setrlimit(resource.RLIMIT_AS, (lim, lim))
    except Exception:
        pass


def _payload_path(name: str) -> str:
    try:
        import job_state
        d = job_state._dir()
    except Exception:
        d = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(d, f"job_payload_{name}.json")


def _encode(o):
    import datetime as dt
    if isinstance(o, dt.date):
        return {"__date__": o.isoformat()}
    if isinstance(o, set):
        return sorted(o)
    return str(o)


def _decode(d):
    import datetime as dt
    if "__date__" in d and len(d) == 1:
        try:
            return dt.date.fromisoformat(d["__date__"])
        except Exception:
            return None
    return d


def running(name: str) -> bool:
    with _LOCK:
        p = _PROCS.get(name)
        return bool(p and p.poll() is None)


def start(name: str, *args) -> bool:
    """Start job `name` in a child process. False if it is already running."""
    if name not in JOBS:
        raise ValueError(name)
    mod = importlib.import_module(JOBS[name][0])
    with _LOCK:
        p = _PROCS.get(name)
        if p and p.poll() is None:
            return False
    path = _payload_path(name)
    with open(path, "w") as fh:
        json.dump({"args": list(args)}, fh, default=_encode)
    st = getattr(mod, "_STATE", None)
    lock = getattr(mod, "_LOCK", None)
    if isinstance(st, dict):
        if lock:
            with lock:
                st["running"] = True
                st["error"] = None
        else:
            st["running"] = True
    env = dict(os.environ, MALLOC_ARENA_MAX="2", FAIM_KEEPER="0", HEALTH_WATCH="0", JOB_ISOLATE="0")
    here = os.path.dirname(os.path.abspath(__file__))
    proc = subprocess.Popen([sys.executable, "-m", "jobrunner", name, path], cwd=here, env=env,
                            preexec_fn=_limit)
    with _LOCK:
        _PROCS[name] = proc
    threading.Thread(target=_wait, args=(name, proc, mod), daemon=True, name=f"job-{name}").start()
    return True


def _wait(name: str, proc, mod) -> None:
    code = proc.wait()
    try:
        import job_state
        st = getattr(mod, "_STATE", None)
        if name == "mw_costs":
            with mod._LOCK:
                mod._STATE.update(running=False, files={})
            mod.load_saved()
            if code != 0:
                with mod._LOCK:
                    mod._STATE["error"] = f"stopped (exit {code}) — memory cap or error"
        elif isinstance(st, dict):
            job_state.restore(name, st)       # results + finished time from disk
            if code != 0:
                st["error"] = (f"the job stopped (exit {code}): it hit its memory cap or failed; "
                               "the website was not affected")
                doc = job_state.load(name)
                doc.update(error=st["error"], finished=time.time(), finished_ok=False)
                job_state.save(name, doc)
                st["finished"] = doc["finished"]
        job_state.release_memory()
    except Exception:
        pass
    finally:
        try:
            st = getattr(mod, "_STATE", None)
            if isinstance(st, dict):
                st["running"] = False
        except Exception:
            pass


def main(argv) -> int:
    name, path = argv[1], argv[2]
    modname, fn = JOBS[name]
    with open(path) as fh:
        payload = json.load(fh, object_hook=_decode)
    mod = importlib.import_module(modname)
    getattr(mod, fn)(*payload.get("args") or [])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
