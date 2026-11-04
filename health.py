"""
health.py — proof the site stays up (Bill, 3 Nov 2026: "how do we verify this
is fixed? I'm not telling my team to try again only to have this happen again").

  * Every minute the app writes a heartbeat to disk: time, memory used, the
    server's memory limit, the highest use so far, and which background job runs.
  * When the app starts, it reads the last heartbeat. If the previous run ended
    WITHOUT an update being deployed (same code version), the app crashed: Bill
    gets an email at once with the last memory reading and the job that was
    running. A restart for an update is noted, not alarmed.
  * Each morning (7am Mexico City) Bill gets one line: crashes in the last 24
    hours (0 is the goal) and the highest memory use.
  * /healthz shows the same in a browser: up since, crashes, memory.
  * Background jobs are held back while memory is above 70% of the limit.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time

TO = os.environ.get("HEALTH_ALERT_TO", "bill.brill@inflectionpointnow.com")
BEAT_S = int(os.environ.get("HEALTH_BEAT_S", "10"))
_BOOT = time.time()
_PEAK = {"bytes": 0, "job": ""}
_STARTED = False


def _dir() -> str:
    for d in ("/var/data", "/data"):
        if os.path.isdir(d) and os.access(d, os.W_OK):
            return d
    return os.path.dirname(os.path.abspath(__file__))


def _path() -> str:
    return os.path.join(_dir(), "health.json")


def _read_int(p):
    try:
        with open(p) as fh:
            v = fh.read().strip()
        return None if v in ("", "max") else int(v)
    except Exception:
        return None


def memory() -> dict:
    """{'used': bytes, 'limit': bytes or None, 'pct': 0-100 or None}."""
    used = (_read_int("/sys/fs/cgroup/memory.current")
            or _read_int("/sys/fs/cgroup/memory/memory.usage_in_bytes"))
    if used:   # leave out file cache the system can drop (as `docker stats` does)
        for stat, key in (("/sys/fs/cgroup/memory.stat", "inactive_file"),
                          ("/sys/fs/cgroup/memory/memory.stat", "total_inactive_file")):
            try:
                with open(stat) as fh:
                    for line in fh:
                        k, v = line.split()
                        if k == key:
                            used = max(0, used - int(v))
                            raise StopIteration
            except StopIteration:
                break
            except Exception:
                continue
    limit = (_read_int("/sys/fs/cgroup/memory.max")
             or _read_int("/sys/fs/cgroup/memory/memory.limit_in_bytes"))
    if limit and limit > 1 << 50:
        limit = None
    if used is None:
        try:
            import resource
            used = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        except Exception:
            used = 0
    pct = round(used / limit * 100, 1) if (limit and used) else None
    return {"used": used or 0, "limit": limit, "pct": pct}


def memory_high(threshold: float = None) -> bool:
    try:
        threshold = threshold or float(os.environ.get("HEALTH_MEM_PAUSE_PCT", "70"))
    except ValueError:
        threshold = 70.0
    pct = memory()["pct"]
    return bool(pct and pct >= threshold)


def _version() -> str:
    return (os.environ.get("RENDER_GIT_COMMIT") or "")[:12]


def _job() -> str:
    import sys
    fw = sys.modules.get("faim_web")
    try:
        return fw._heavy_busy() if fw else ""
    except Exception:
        return ""


def _load() -> dict:
    try:
        with open(_path()) as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save(doc: dict) -> None:
    tmp = _path() + ".tmp"
    try:
        with open(tmp, "w") as fh:
            json.dump(doc, fh)
        os.replace(tmp, _path())
    except Exception:
        pass


def _mb(b) -> str:
    return f"{(b or 0) / 1048576:,.0f} MB"


def _send(subject: str, body: str) -> None:
    try:
        from engine.mailer import GraphMailer
        from coordinator_alerts import app_subject
        GraphMailer(mailbox=os.environ.get("GRAPH_SENDER", "bbrill@thelsa.com")).send_mail(
            TO, app_subject(subject), body)
    except Exception:
        pass


def classify_previous(prev: dict, version_now: str) -> str:
    """'' (first run / nothing known), 'update' (restart for a new version) or
    'crash' (same version, the previous run just stopped)."""
    if not prev.get("beat_at"):
        return ""
    if prev.get("clean"):
        return "update"
    if (prev.get("version") or "") != (version_now or ""):
        return "update"
    return "crash"


def _on_boot() -> None:
    prev = _load()
    kind = classify_previous(prev, _version())
    crashes = [c for c in prev.get("crashes", []) if time.time() - c.get("at", 0) < 7 * 86400]
    if kind == "crash":
        lim = prev.get("limit")
        c = {"at": time.time(), "last_beat": prev.get("beat_at"), "mem": prev.get("used"),
             "limit": lim, "pct": prev.get("pct"), "job": prev.get("job") or "", "peak": prev.get("peak")}
        crashes.append(c)
        when = dt.datetime.fromtimestamp(prev["beat_at"], dt.timezone(dt.timedelta(hours=-6)))
        _send("ALERT: the app restarted unexpectedly",
              "The FAIM Audit Assistant stopped and restarted on its own (no update was deployed).\n\n"
              f"Last sign of life: {when:%Y-%m-%d %H:%M} (Mexico City)\n"
              f"Memory at that moment: {_mb(prev.get('used'))} of {_mb(lim) if lim else 'unknown'}"
              f" ({prev.get('pct')}%)\n"
              f"Highest memory in that run: {_mb(prev.get('peak'))}\n"
              f"Background job running: {prev.get('job') or 'none'}\n"
              f"Unexpected restarts in the last 7 days: {len(crashes)}\n\n"
              "Last readings before it stopped (time, MB, what was running):\n"
              + "\n".join(f"  {dt.datetime.fromtimestamp(r['t'], dt.timezone(dt.timedelta(hours=-6))):%H:%M:%S}  "
                           f"{r.get('mb')} MB  job={r.get('job') or '-'}  audit={r.get('audit')}  threads={r.get('threads')}"
                           for r in (prev.get("recent") or [])[-12:]) + "\n\n"
              "The site is back up now. Claude will look into this.")
    _save({"boot_at": _BOOT, "version": _version(), "crashes": crashes, "prev_recent": prev.get("recent") or [],
           "last_kind": kind, "summary_day": prev.get("summary_day", "")})


def _context() -> dict:
    """What the app is doing right now, to tie a memory jump to its cause."""
    import sys
    out = {}
    mw = sys.modules.get("mw_live")
    try:
        a = mw._AUDIT
        out["audit"] = {"files": len(a.get("files") or {}), "cycles": a.get("cycles"),
                        "complete": a.get("window_complete"), "gapfill_done": a.get("gapfill_done"),
                        "refetch": a.get("refetch")}
    except Exception:
        pass
    names = {}
    for t in threading.enumerate():
        n = t.name.split("-")[0] if t.name.startswith(("ThreadPoolExecutor", "Thread")) else t.name
        names[n] = names.get(n, 0) + 1
    out["threads"] = names
    return out


def _beat() -> None:
    m = memory()
    job = _job()
    if m["used"] > _PEAK["bytes"]:
        _PEAK.update(bytes=m["used"], job=job)
    doc = _load()
    ctx = _context()
    ring = (doc.get("recent") or [])[-29:]
    ring.append({"t": round(time.time()), "mb": round(m["used"] / 1048576), "job": job, **ctx})
    doc["recent"] = ring
    doc.update(beat_at=time.time(), version=_version(), pid=os.getpid(), used=m["used"], limit=m["limit"],
               pct=m["pct"], job=job, peak=_PEAK["bytes"], peak_job=_PEAK["job"], clean=False)
    _summary(doc)
    _save(doc)


def _summary(doc: dict) -> None:
    mx = dt.datetime.now(dt.timezone(dt.timedelta(hours=-6)))
    day = mx.date().isoformat()
    if mx.hour != 7 or doc.get("summary_day") == day:
        return
    doc["summary_day"] = day
    n = sum(1 for c in doc.get("crashes", []) if time.time() - c.get("at", 0) < 86400)
    up_h = (time.time() - _BOOT) / 3600
    lim = doc.get("limit")
    _send(f"Daily health: {n} unexpected restart{'s' if n != 1 else ''} in 24h",
          f"Unexpected restarts in the last 24 hours: {n}\n"
          f"Up without a break for: {up_h:,.1f} hours\n"
          f"Highest memory since then: {_mb(_PEAK['bytes'])}"
          f"{' of ' + _mb(lim) if lim else ''} (during: {_PEAK['job'] or 'no background job'})\n")


def mark_clean_exit() -> None:
    doc = _load()
    doc["clean"] = True
    _save(doc)


_ERR = {"last": ""}


TRIM_S = float(os.environ.get("HEALTH_TRIM_SECONDS", "300"))


def _loop() -> None:
    last_trim = 0.0
    while True:
        try:
            _beat()
        except Exception as e:
            _ERR["last"] = f"{type(e).__name__}: {e}"[:200]
        # Give memory Python has freed back to the OS every few minutes, so a
        # burst (an audit refresh, a page build) does not raise the floor for
        # good (4 Nov 2026 crash loop).
        if TRIM_S > 0 and time.time() - last_trim >= TRIM_S:
            last_trim = time.time()
            try:
                import job_state
                job_state.release_memory()
            except Exception:
                pass
        time.sleep(BEAT_S)


def start() -> None:
    """Once per process, outside tests."""
    global _STARTED
    import sys
    if _STARTED or "pytest" in sys.modules or os.environ.get("HEALTH_WATCH", "1") != "1":
        return
    _STARTED = True
    try:
        import fcntl
        global _LOCK_FH
        _LOCK_FH = open("/tmp/thelsa_health.lock", "w")
        fcntl.flock(_LOCK_FH, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        return
    try:
        _on_boot()
    except Exception:
        pass
    threading.Thread(target=_loop, daemon=True, name="health").start()


def status() -> dict:
    doc = _load()
    m = memory()
    crashes = doc.get("crashes", [])
    return {"up_since": dt.datetime.fromtimestamp(_BOOT, dt.timezone.utc).isoformat(timespec="seconds"),
            "up_hours": round((time.time() - _BOOT) / 3600, 2),
            "version": _version(),
            "unexpected_restarts_24h": sum(1 for c in crashes if time.time() - c.get("at", 0) < 86400),
            "unexpected_restarts_7d": len(crashes),
            "memory_mb": round(m["used"] / 1048576), "memory_limit_mb": round(m["limit"] / 1048576) if m["limit"] else None,
            "memory_pct": m["pct"],
            "peak_mb": round(max(_PEAK["bytes"], doc.get("peak") or 0) / 1048576),
            "peak_during": doc.get("peak_job") or _PEAK["job"],
            "background_job": doc.get("job") or _job(),
            "last_beat_s_ago": round(time.time() - doc["beat_at"]) if doc.get("beat_at") else None,
            "beat_pid": doc.get("pid"), "this_pid": os.getpid(),
            "beat_error": _ERR.get("last", ""),
            "recent": (doc.get("recent") or [])[-6:],
            "before_last_restart": (doc.get("prev_recent") or [])[-12:]}
