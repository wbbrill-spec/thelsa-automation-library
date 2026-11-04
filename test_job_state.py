import time

import job_state


def test_state_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(job_state, "_dir", lambda: str(tmp_path))
    job_state.mark_started("demo")
    assert job_state.recently_crashed("demo")          # started, never finished
    job_state.save("demo", {"running": True, "finished": time.time() + 1,
                            "results": [{"file": "111"}], "error": None})
    state = {"running": True, "finished": None, "results": [], "error": "x"}
    job_state.restore("demo", state)
    assert state["running"] is False
    assert state["results"] == [{"file": "111"}]
    assert state["error"] is None
    assert not job_state.recently_crashed("demo")


def test_crash_retry_expires(tmp_path, monkeypatch):
    monkeypatch.setattr(job_state, "_dir", lambda: str(tmp_path))
    job_state.save("old", {"started": time.time() - 7 * 3600, "finished": None})
    assert not job_state.recently_crashed("old")       # past the 6 h wait


def test_missing_state_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(job_state, "_dir", lambda: str(tmp_path))
    assert job_state.load("nothing") == {}
    job_state.release_memory()                          # never raises
