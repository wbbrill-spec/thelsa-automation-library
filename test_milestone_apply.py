"""Diary write-back executor: switches, what it writes, what it never writes."""
import pytest

import milestone_apply as ma

STEPS = [
    {"job": "110801", "milestone": "pack", "action": "create", "plan_date": "2026-10-14",
     "body": {"type": "Uplift", "date": "2026-10-14", "comment": "Load", "completed": "Y", "completedby": "AUT"}},
    {"job": "110802", "milestone": "delivery", "action": "update", "plan_date": "2026-10-15", "activity_id": 77,
     "body": {"date": "2026-10-15", "completed": "Y", "completedby": "AUT"}},
    {"job": "110803", "milestone": "pack", "action": "review", "plan_date": "2026-10-13", "activity_id": 5},
]
IDS = {"110801": 9001, "110802": 9002, "110803": 9003}


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    for k in ("MW_DIARY_WRITE", "MW_AUTO_USER", "DRY_RUN", "MW_DIARY_WRITE_MAX"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(ma, "_log_path", lambda: str(tmp_path / "w.jsonl"))


@pytest.fixture
def calls(monkeypatch):
    sent = []
    monkeypatch.setattr(ma, "_send", lambda m, p, b: sent.append((m, p, b)) or {})
    monkeypatch.setattr(ma, "_verified", lambda jid, s: True)
    return sent


def test_off_by_default_and_placeholder_user_refused(monkeypatch, calls):
    assert not ma.write_enabled() and "MW_DIARY_WRITE" in ma.blocked_reason()
    monkeypatch.setenv("MW_DIARY_WRITE", "1")
    assert not ma.write_enabled() and "MW_AUTO_USER" in ma.blocked_reason()
    monkeypatch.setenv("MW_AUTO_USER", "aut")
    assert not ma.write_enabled()
    out = ma.apply(STEPS, IDS)
    assert out["written"] == 0 and calls == []
    monkeypatch.setenv("MW_AUTO_USER", "TAU")
    monkeypatch.setenv("DRY_RUN", "1")
    assert not ma.write_enabled()


def test_writes_create_and_update_never_review(monkeypatch, calls):
    monkeypatch.setenv("MW_DIARY_WRITE", "1")
    monkeypatch.setenv("MW_AUTO_USER", "tau")
    out = ma.apply(STEPS, IDS)
    assert out["written"] == 2 and out["verified"] == 2 and out["review_left"] == 1
    assert calls[0][0] == "POST" and calls[0][1] == "/jobs/9001/activities"
    assert calls[0][2]["completedby"] == "TAU" and calls[0][2]["type"] == "Uplift"
    assert calls[1][0] == "PATCH" and calls[1][1] == "/jobs/9002/activities/77"
    assert all("9003" not in c[1] for c in calls)
    log = ma.recent_log()
    assert [r["job"] for r in log] == ["110801", "110802"] and all(r["result"] == "verified" for r in log)


def test_cap_missing_id_and_failure(monkeypatch):
    monkeypatch.setenv("MW_DIARY_WRITE", "1")
    monkeypatch.setenv("MW_AUTO_USER", "TAU")
    monkeypatch.setenv("MW_DIARY_WRITE_MAX", "1")

    def boom(m, p, b):
        raise OSError("HTTP 500")
    monkeypatch.setattr(ma, "_send", boom)
    out = ma.apply(STEPS, {})
    assert out["skipped"] == 2 and out["written"] == 0           # one no-id, one over the cap
    out = ma.apply(STEPS, IDS)
    assert out["failed"] == 1 and "HTTP 500" in ma.recent_log()[-1]["result"]


def test_successful_write_clears_cached_diary(monkeypatch, calls):
    from assistant import mw_confirm
    monkeypatch.setenv("MW_DIARY_WRITE", "1")
    monkeypatch.setenv("MW_AUTO_USER", "TAU")
    mw_confirm._CACHE["110801"] = {"load": None, "at": 0}
    ma.apply(STEPS[:1], IDS)
    assert "110801" not in mw_confirm._CACHE


def test_run_once_does_nothing_when_off():
    assert ma.run_once() is None
