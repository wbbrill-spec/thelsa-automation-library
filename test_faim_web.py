"""/faim picks its files by the Moveware Integration Guide and reads the shared book.

It never calls Moveware itself: the files come from mw_live's background auditor
(the same book /audit uses).
"""
import datetime as dt
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
    assert s["collected_before_window"] == 1
    assert s["collect_date_ahead"] == 1
    assert s["no_collect_date"] == 1


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


def test_booked_files_with_no_collect_date_are_reported_separately():
    s = snap([rec("1", "W"), rec("2", "W"), rec("3", "P")])["selection"]
    assert s["moves_in_window"] == 0
    assert s["no_collect_date_by_status"] == {"W": 2, "P": 1}


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
    assert "1 files with no collect date yet (1 of them booked)" in data["generatedNote"]


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
