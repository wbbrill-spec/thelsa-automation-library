"""health.py: tells a crash from a restart for an update, and holds jobs back when memory is high."""
import health


def test_restart_for_a_new_version_is_an_update_not_a_crash():
    assert health.classify_previous({}, "abc") == ""
    assert health.classify_previous({"beat_at": 1, "version": "old"}, "new") == "update"
    assert health.classify_previous({"beat_at": 1, "version": "abc", "clean": True}, "abc") == "update"
    assert health.classify_previous({"beat_at": 1, "version": "abc"}, "abc") == "crash"


def test_jobs_wait_while_memory_is_high(monkeypatch):
    monkeypatch.setattr(health, "memory", lambda: {"used": 400, "limit": 512, "pct": 78.1})
    assert health.memory_high()
    monkeypatch.setattr(health, "memory", lambda: {"used": 200, "limit": 512, "pct": 39.0})
    assert not health.memory_high()


def test_healthz_is_public_and_reports():
    import app
    r = app.app.test_client().get("/healthz")
    assert r.status_code == 200 and "unexpected_restarts_24h" in r.get_json()
