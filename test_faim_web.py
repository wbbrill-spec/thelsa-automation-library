"""/faim picks its files by the Moveware Integration Guide and reads the shared book.

It never calls Moveware itself: the files come from mw_live's background auditor
(the same book /audit uses).
"""
import datetime as dt
import re
import types

import pytest
from flask import Flask

import faim_web

TODAY = dt.date(2026, 10, 12)


def rec(job, status="W", pack=None, delivery=None, coordinator="Ana Sanchez", **extra):
    r = {"job": job, "job_id": job, "status": status, "pack": pack,
         "delivery": delivery, "coordinator": coordinator, "survey": None}
    r.update(extra)
    return r


def snap(files):
    return faim_web.faim_snapshot(files, today=TODAY)


# ── Which files count ───────────────────────────────────────────────────────────
def test_a_move_is_a_file_collected_inside_the_last_12_months():
    s = snap([
        rec("1", pack=dt.date(2025, 10, 12)),      # first day of the window
        rec("2", pack=TODAY),                      # last day of the window
        rec("3", pack=dt.date(2025, 10, 11)),      # one day too old
        rec("4", pack=dt.date(2026, 10, 13)),      # collect date still ahead
        rec("5", pack=None),                       # no collect date yet
    ])["selection"]
    assert s["window_start"] == "2025-10-12" and s["window_end"] == "2026-10-12"
    assert s["moves_in_window"] == 2
    assert s["key_date_before_window"] == 1
    assert s["key_date_ahead"] == 1
    assert s["no_key_date"] == 1


def test_lost_and_cancelled_files_are_left_out_and_counted():
    out = snap([
        rec("1", "W", pack=dt.date(2026, 5, 1)),
        rec("2", "P", pack=dt.date(2026, 5, 1)),
        rec("3", "L", pack=dt.date(2026, 5, 1)),
        rec("4", "C", pack=dt.date(2026, 5, 1)),
    ])
    assert out["metrics"]["tiles"] == {"moves": 4, "lost": 2, "audited": 2, "booked": 1,
                                       "pending": 1, "missingSurvey": 2}
    assert [b["file"] for b in out["metrics"]["breaches"]] == ["1", "2"]
    assert out["selection"]["moves_by_status"] == {"W": 1, "P": 1, "L": 1, "C": 1}


def test_lost_statuses_are_configurable(monkeypatch):
    monkeypatch.setattr(faim_web, "LOST_STATUSES", {"L", "C", "P"})
    t = snap([rec("1", "W", pack=dt.date(2026, 5, 1)),
              rec("2", "P", pack=dt.date(2026, 5, 1))])["metrics"]["tiles"]
    assert (t["lost"], t["audited"]) == (1, 1)


def test_a_sequel_is_its_own_move_but_the_same_file():
    s = snap([rec("412042", pack=dt.date(2026, 3, 1)),
              rec("412042A", pack=dt.date(2026, 6, 1))])["selection"]
    assert s["moves_in_window"] == 2 and s["move_files_in_window"] == 1


def test_booked_files_with_neither_date_are_reported_separately_by_type():
    s = snap([rec("1", "W", job_type="IMP"), rec("2", "W", job_type="IMP"),
              rec("3", "W", job_type="EXP"), rec("4", "P")])["selection"]
    assert s["moves_in_window"] == 0
    assert s["no_key_date_by_status"] == {"W": 3, "P": 1}
    assert s["booked_no_key_date_by_type"] == {"IMP": 2, "EXP": 1}


def test_an_import_with_no_pack_date_counts_by_its_delivery_date():
    out = snap([
        rec("1", pack=None, delivery=dt.date(2026, 6, 1)),      # import, delivered in window
        rec("2", pack=None, delivery=dt.date(2025, 6, 1)),      # delivered before the window
        rec("3", pack=None, delivery=dt.date(2026, 11, 1)),     # delivery still ahead
        rec("4", pack=dt.date(2026, 5, 1), delivery=dt.date(2026, 6, 1)),
    ])
    s = out["selection"]
    assert s["moves_in_window"] == 2
    assert s["moves_by_key_date"] == {"pack": 1, "delivery": 1}
    assert (s["key_date_before_window"], s["key_date_ahead"], s["no_key_date"]) == (1, 1, 0)


def test_the_pack_date_wins_when_a_file_has_both():
    # packed before the window, delivered inside it: dated by its pack date
    s = snap([rec("1", pack=dt.date(2025, 9, 1), delivery=dt.date(2025, 12, 1))])["selection"]
    assert s["moves_in_window"] == 0 and s["key_date_before_window"] == 1


def test_dates_saved_as_text_are_read_the_same_way():
    s = snap([rec("1", pack="2026-05-01", delivery="2026-06-01", survey="2026-04-01")])
    assert s["selection"]["moves_in_window"] == 1
    assert s["metrics"]["breaches"] == []


# ── Evidence coverage ───────────────────────────────────────────────────────────
def test_coverage_is_measured_over_audited_files_only():
    m = snap([
        rec("1", pack=dt.date(2026, 5, 1), survey=dt.date(2026, 4, 1),
            delivery=dt.date(2026, 6, 1)),
        rec("2", pack=dt.date(2026, 5, 1), coordinator=""),
        rec("3", "L", pack=dt.date(2026, 5, 1)),          # lost: not in any percentage
    ])["metrics"]
    crit = {c["name"]: c["v"] for c in m["byCriterion"]}
    assert crit == {"Coordinator assigned": 50, "Survey on file": 50,
                    "Delivery date logged": 50}
    assert m["byCoordinator"] == [{"name": "Ana Sanchez", "v": 100, "files": 1},
                                  {"name": "Unassigned", "v": 0, "files": 1}]
    assert m["breaches"][0]["file"] == "2"
    assert m["breaches"][0]["rule"] == "Missing: survey, delivery date, coordinator"
    assert m["breaches"][0]["cust"] == "Booked"


def test_a_file_whose_survey_was_not_read_yet_is_not_called_missing():
    old = rec("1", pack=dt.date(2026, 5, 1), delivery=dt.date(2026, 6, 1))
    del old["survey"]                                  # record read before the change
    new = rec("2", pack=dt.date(2026, 5, 1), delivery=dt.date(2026, 6, 1))
    out = snap([old, new])
    assert out["selection"]["survey_not_read_yet"] == 1
    assert out["metrics"]["tiles"]["missingSurvey"] == 1
    assert [b["file"] for b in out["metrics"]["breaches"]] == ["2"]


def test_gap_list_is_worst_first_booked_first_and_capped():
    files = [rec(str(i), "P", pack=dt.date(2026, 5, 1)) for i in range(20)]
    files.append(rec("B", "W", pack=dt.date(2026, 5, 1)))
    files.append(rec("X", "W", pack=dt.date(2026, 5, 1), coordinator=""))
    m = snap(files)["metrics"]
    assert [b["file"] for b in m["breaches"][:2]] == ["X", "B"]
    assert len(m["breaches"]) == 15 and m["gapFiles"] == 22


def test_window_start_handles_a_leap_day():
    assert faim_web._months_ago(dt.date(2028, 2, 29), 12) == dt.date(2027, 2, 28)


# ── Routes ──────────────────────────────────────────────────────────────────────
@pytest.fixture
def client(monkeypatch):
    calls = {"ensure": 0, "refresh": 0}
    book = {"files": [], "progress": {"complete": True, "audited_total": 0, "total": 0}}

    def _ensure():
        calls["ensure"] += 1

    def _refresh():
        calls["refresh"] += 1
        return calls["refresh"] == 1

    fake = types.SimpleNamespace(
        have_creds=lambda: True, ensure_auditor=_ensure, force_refresh=_refresh,
        audited_in_window=lambda: book["files"], audit_progress=lambda: book["progress"])
    monkeypatch.setattr(faim_web, "mw_live", fake)
    monkeypatch.setattr(faim_web, "_HAVE_MW", True)
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(faim_web.faim_bp)
    app.add_url_rule("/login", "login", lambda: "login")
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_email"] = "someone@thelsa.com"
    c.calls, c.book, c.fake = calls, book, fake
    return c


@pytest.mark.parametrize("path", ["/faim", "/faim/api/metrics", "/faim/raw", "/faim/rebuild"])
def test_every_faim_route_needs_a_signed_in_user(client, path):
    with client.session_transaction() as s:
        s.clear()
    assert client.get(path).status_code == 302


def test_without_credentials_nothing_is_read(client):
    client.fake.have_creds = lambda: False
    data = client.get("/faim/api/metrics").get_json()
    assert data["window"] == "not connected" and client.calls["ensure"] == 0


def test_an_empty_book_says_it_is_loading_and_starts_the_reader(client):
    data = client.get("/faim/api/metrics").get_json()
    assert data["window"] == "loading" and client.calls["ensure"] == 1
    assert data["tiles"]["audited"] == 0


def test_metrics_come_from_the_shared_book(client):
    today = dt.date.today()
    client.book["files"] = [rec("1", "W", pack=today), rec("2", "L", pack=today),
                            rec("3", "W")]
    data = client.get("/faim/api/metrics").get_json()
    assert data["window"] == "live"
    assert (data["tiles"]["moves"], data["tiles"]["lost"], data["tiles"]["audited"]) == (2, 1, 1)
    assert "1 files with neither date in Moveware (1 of them booked)" in data["generatedNote"]


def test_an_unfinished_book_is_labelled(client):
    client.book["files"] = [rec("1", pack=dt.date.today())]
    client.book["progress"] = {"complete": False, "audited_total": 700, "total": 11196}
    data = client.get("/faim/api/metrics").get_json()
    assert "book still loading" in data["window"]
    assert "700 of 11196 files" in data["generatedNote"]


def test_raw_gives_counts_and_no_names(client):
    client.book["files"] = [rec("1", pack=dt.date.today(), client="Jane Doe")]
    body = client.get("/faim/raw").get_data(as_text=True)
    assert '"moves_in_window":1' in body.replace(" ", "")
    assert "Jane Doe" not in body and "Ana Sanchez" not in body


def test_refresh_button_asks_the_shared_reader_once(client):
    assert client.post("/faim/rebuild").get_json() == {"started": True, "reason": ""}
    assert client.post("/faim/rebuild").get_json() == {"started": False,
                                                       "reason": "already running"}


# ── What the shared reader (mw_live) records for /faim ──────────────────────────
import mw_live


def _map(monkeypatch, job, roles_list=None):
    def fake_get(path):
        if path == f"/jobs/{job['id']}":
            return job
        if path.endswith("/roles"):
            return {"roles": roles_list or []}
        return {}
    monkeypatch.setattr(mw_live, "_get", fake_get)
    return mw_live._map_job({"id": str(job["id"])})


def _role(first="", last="", email="", **more):
    """A V2 role as Moveware sends it: the person sits on role.entity."""
    return dict({"firstName": "", "lastName": "", "email": "",
                 "entity": {"firstName": first, "lastName": last, "email": email}}, **more)


def test_survey_date_is_recorded(monkeypatch):
    m = _map(monkeypatch, {"id": 1, "status": "W", "activityDates": {
        "survey": {"date": "2026-04-02"}, "uplift": {"date": "2026-05-01"}}})
    assert m["survey"] == dt.date(2026, 4, 2) and m["pack"] == dt.date(2026, 5, 1)


def test_moveware_coordinator_is_read_from_the_role_entity(monkeypatch):
    m = _map(monkeypatch, {"id": 2, "status": "W", "name": "Doe/Jane", "roles": {
        "coordinator": _role("Itzel", "Urbina", "itzel@thelsa.com"),
        "moveManager": _role("Other", "Person")}})
    assert m["mw_coordinator"] == "Itzel Urbina"
    # the fields the audit page and its alerts use are left exactly as they were
    assert m["coordinator"] == "" and m["coordinator_email"] == ""


def test_move_manager_is_used_when_no_coordinator(monkeypatch):
    m = _map(monkeypatch, {"id": 3, "status": "W", "roles": {
        "coordinator": _role(), "moveManager": _role("Ana", "Sanchez")}})
    assert m["mw_coordinator"] == "Ana Sanchez"


def test_empty_roles_block_falls_back_to_the_roles_list(monkeypatch):
    m = _map(monkeypatch, {"id": 4, "status": "W", "roles": {}},
             roles_list=[_role("Jane", "Doe", type="originClient"),
                         _role("Dulce", "Araujo", type="coordinator")])
    assert m["mw_coordinator"] == "Dulce Araujo"


def test_no_coordinator_on_the_file_stays_blank(monkeypatch):
    m = _map(monkeypatch, {"id": 5, "status": "P", "roles": {"coordinator": _role()}})
    assert m["mw_coordinator"] == ""


def test_faim_prefers_the_moveware_coordinator_over_the_alert_name():
    out = snap([rec("1", pack=dt.date(2026, 5, 1), coordinator="", mw_coordinator="Itzel Urbina"),
                rec("2", pack=dt.date(2026, 5, 1), coordinator="Old Name", mw_coordinator=""),
                rec("3", pack=dt.date(2026, 5, 1), coordinator="Old Name")])
    names = sorted(c["name"] for c in out["metrics"]["byCoordinator"])
    assert names == ["Itzel Urbina", "Old Name", "Unassigned"]


def test_job_type_code_is_recorded(monkeypatch):
    m = _map(monkeypatch, {"id": 6, "status": "W", "type": "IMP"})
    assert m["job_type"] == "IMP"


# ── A failed re-read must not blank a good record ───────────────────────────────
def _book_with(monkeypatch, files):
    monkeypatch.setattr(mw_live, "_feed_total", lambda: 1)
    monkeypatch.setattr(mw_live, "_get_timed", lambda url, to: {"jobs": []})
    for k, v in {"files": dict(files), "old_ids": set(), "foreign_ids": set(), "total": 1,
                 "cycles": 0, "errors": 0, "kept_on_failed_read": 0, "ahead_read": 0,
                 "ahead_kept": 0}.items():
        monkeypatch.setitem(mw_live._AUDIT, k, v)        # restored after the test


def test_failed_reread_keeps_the_earlier_record(monkeypatch):
    good = {"job": "111", "status": "W", "pack": dt.date.today(), "detail_ok": True}
    _book_with(monkeypatch, {"111": good})
    monkeypatch.setattr(mw_live, "_get", lambda path: (_ for _ in ()).throw(TimeoutError()))
    mw_live._refresh_recent()
    assert mw_live._AUDIT["files"]["111"] is good
    assert mw_live.audit_progress()["kept_on_failed_read"] == 1


def test_a_record_saved_before_this_change_is_also_protected(monkeypatch):
    good = {"job": "111", "status": "P", "pack": dt.date.today()}      # no detail_ok key
    _book_with(monkeypatch, {"111": good})
    monkeypatch.setattr(mw_live, "_get", lambda path: (_ for _ in ()).throw(TimeoutError()))
    mw_live._refresh_recent()
    assert mw_live._AUDIT["files"]["111"] is good


def test_successful_reread_replaces_the_record_and_heals_a_hollow_one(monkeypatch):
    hollow = {"job": "222", "status": "", "pack": None}
    _book_with(monkeypatch, {"222": hollow})
    assert mw_live.audit_progress()["hollow_in_window"] == 1
    monkeypatch.setattr(mw_live, "_get", lambda path: (
        {"id": 222, "status": "W", "activityDates": {"uplift": {"date": "2026-05-01"}}}
        if path == "/jobs/222" else {}))
    mw_live._refresh_recent()
    rec_ = mw_live._AUDIT["files"]["222"]
    assert rec_["status"] == "W" and rec_["detail_ok"] is True
    assert mw_live.audit_progress()["hollow_in_window"] == 0


def test_detail_read_is_retried_once(monkeypatch):
    calls = {"n": 0}

    def flaky(path):
        if path == "/jobs/333":
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError()
            return {"id": 333, "status": "W"}
        return {}
    monkeypatch.setattr(mw_live, "_get", flaky)
    m = mw_live._map_job({"id": "333"})
    assert calls["n"] == 2 and m["status"] == "W" and m["detail_ok"] is True


def test_a_failed_read_is_marked_hollow(monkeypatch):
    monkeypatch.setattr(mw_live, "_get", lambda path: (_ for _ in ()).throw(TimeoutError()))
    m = mw_live._map_job({"id": "444"})
    assert m["detail_ok"] is False and m["status"] == ""


# ── New jobs above the highest id in the book ───────────────────────────────────
def _moveware_with(monkeypatch, existing_ids):
    def fake_get(path):
        m = re.fullmatch(r"/jobs/(\d+)", path)
        if m:
            if int(m.group(1)) in existing_ids:
                return {"id": int(m.group(1)), "status": "W",
                        "activityDates": {"pack": {"date": dt.date.today().isoformat()}}}
            raise OSError("HTTP 404")
        return {}
    monkeypatch.setattr(mw_live, "_get", fake_get)


def test_refresh_finds_new_jobs_above_the_highest_known_id(monkeypatch):
    _book_with(monkeypatch, {"500": {"job": "500", "status": "W", "detail_ok": True}})
    _moveware_with(monkeypatch, {500, 503, 510})
    mw_live._refresh_recent()
    assert {"500", "503", "510"} <= set(mw_live._AUDIT["files"])
    assert mw_live._AUDIT["files"]["510"]["status"] == "W"
    p = mw_live.audit_progress()
    assert p["ahead_kept"] == 2 and p["highest_id"] == 510


def test_ids_that_are_not_jobs_yet_are_not_remembered_as_foreign(monkeypatch):
    _book_with(monkeypatch, {"500": {"job": "500", "status": "W", "detail_ok": True}})
    _moveware_with(monkeypatch, {500})
    assert mw_live._probe_ahead() == 0
    assert mw_live._AUDIT["foreign_ids"] == set()
    assert set(mw_live._AUDIT["files"]) == {"500"}


def test_probe_keeps_going_while_each_band_finds_something(monkeypatch):
    monkeypatch.setattr(mw_live, "_GAP_AHEAD", 10)
    monkeypatch.setattr(mw_live, "_GAP_AHEAD_ROUNDS", 5)
    _book_with(monkeypatch, {"500": {"job": "500", "status": "W", "detail_ok": True}})
    _moveware_with(monkeypatch, {500, 505, 515, 525, 545})     # 545 is past an empty band
    assert mw_live._probe_ahead() == 3
    assert "525" in mw_live._AUDIT["files"] and "545" not in mw_live._AUDIT["files"]
    assert mw_live._AUDIT["ahead_read"] == 40                  # 3 bands with a hit + 1 empty
