"""health.py: tells a crash from a restart for an update, and holds jobs back when memory is high."""
import health


def test_restart_for_a_new_version_is_an_update_not_a_crash():
    assert health.classify_previous({}, "abc") == ""
    assert health.classify_previous({"beat_at": 1, "version": "old"}, "new") == "update"
    assert health.classify_previous({"beat_at": 1, "version": "abc", "clean": True}, "abc") == "restart"
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


def test_one_orderly_restart_is_not_emailed_three_are(monkeypatch, tmp_path):
    import time
    sent = []
    monkeypatch.setattr(health, "_path", lambda: str(tmp_path / "h.json"))
    monkeypatch.setattr(health, "_send", lambda s, b: sent.append(s))
    monkeypatch.setattr(health, "_version", lambda: "abc")
    for i in range(3):
        health._save({**health._load(), "beat_at": time.time(), "version": "abc", "clean": True})
        health._on_boot()
    assert len(sent) == 1 and "3 times" in sent[0]


def test_knock_tells_answer_from_no_answer():
    import socket, threading
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(5)
    port = srv.getsockname()[1]
    def serve():
        c, _ = srv.accept(); c.recv(100); c.sendall(b"HTTP/1.0 200 OK\r\n\r\n"); c.close()
    threading.Thread(target=serve, daemon=True).start()
    assert health.knock(port, 5) == ""
    # a listener that never answers
    assert health.knock(port, 0.5).startswith("no reply")
    srv.close()
    assert health.knock(port, 0.5).startswith("connect")


def test_thread_dump_names_threads():
    assert "--- thread MainThread" in health.thread_dump()
