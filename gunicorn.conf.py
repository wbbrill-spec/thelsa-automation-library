"""Gunicorn settings, read automatically whatever the start command says.

One server process with threads (3 Nov 2026): with several processes each one
loaded the Moveware files into memory separately, the 512 MB server ran out and
restarted, and the site answered 502. One process shares that data; threads
still serve many people at once. GUNICORN_WORKERS / GUNICORN_THREADS override.
"""
import os

workers = int(os.environ.get("GUNICORN_WORKERS", "1"))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", "8"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "180"))
graceful_timeout = 30
keepalive = 5


def worker_exit(server, worker):
    """Record an orderly stop, so the health check does not report it as a
    crash (9 Nov 2026: Render's restarts were emailed as crashes)."""
    try:
        import health
        health.mark_clean_exit()
    except Exception:
        pass
