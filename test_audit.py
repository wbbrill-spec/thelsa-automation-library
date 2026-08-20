"""
Automated test suite for the Move-File Audit dashboard.

Run:  pytest            (from the repo root)
CI:   .github/workflows/ci.yml runs this on every push / PR.

Covers the three pieces of logic most likely to regress and silently produce
wrong numbers on the live dashboard:
  1. Line-level quote<->invoice reconciliation (the job 110995 false-positive fix)
  2. The rolling 12-month audit window + background-auditor walk
  3. compute_metrics guards and rollups (past source of divide-by-zero 500s)
"""
import datetime as dt
import re

import pytest
from jinja2 import Template

import audit_web as aw
import mw_live as mw

TODAY = dt.date.today()


def _mkfile(**kw):
    base = dict(
        job=1, client="C", coordinator="Ana", mode="sea", stage="closed",
        invoiced=True, inv_amt=0, sell=0, est=0, act=0,
        q_lines=None, i_lines=None, est_wt=None, act_wt=None,
        gaps=[], open_gaps=0, gap_value=0,
        rev_reported=None, rev_lines=None, n_rev_lines=0, pack=None, delivery=None,
    )
    base.update(kw)
    return base


LIVE_COUNTS = {
    "total": 11008, "total_approx": True, "active": None, "active_estimated": True,
    "exhausted": False, "pages": 0, "audit_running": True, "audit_wrapped": False,
    "window_complete": False, "window_days": 365, "excluded_old": 0,
}


# ════════════════════════════════════════════════════════════════════════════
# 1. Line-level quote <-> invoice reconciliation
# ════════════════════════════════════════════════════════════════════════════
def test_reconcile_exact_match_no_extras():
    q = [{"desc": "Removal", "value": 186240.68}, {"desc": "Insurance", "value": 9925.54}]
    i = [{"desc": "Removal", "value": 186240.68}, {"desc": "Insurance", "value": 9925.54}]
    matched, added, missing = aw._reconcile_lines(q, i)
    assert round(matched, 2) == 196166.22
    assert added == [] and missing == []


def test_reconcile_extra_invoice_line_is_added():
    q = [{"desc": "Removal", "value": 12000}]
    i = [{"desc": "Removal", "value": 12000}, {"desc": "Storage", "value": 3000}]
    matched, added, missing = aw._reconcile_lines(q, i)
    assert round(matched, 2) == 12000.0
    assert len(added) == 1 and added[0]["value"] == 3000
    assert missing == []


def test_reconcile_unchosen_quote_option_is_missing_not_error():
    q = [{"desc": "Main", "value": 12000}, {"desc": "Premium alt", "value": 18000}]
    i = [{"desc": "Main", "value": 12000}]
    matched, added, missing = aw._reconcile_lines(q, i)
    assert added == []
    assert any(m["value"] == 18000 for m in missing)


def test_reconcile_tolerates_tax_rounding():
    q = [{"desc": "Removal", "value": 10000.00}]
    i = [{"desc": "Removal", "value": 10000.40}]
    _, added, _ = aw._reconcile_lines(q, i)
    assert added == []


def test_reconcile_each_quote_line_matches_once():
    q = [{"desc": "A", "value": 500}]
    i = [{"desc": "A", "value": 500}, {"desc": "B", "value": 500}]
    _, added, _ = aw._reconcile_lines(q, i)
    assert len(added) == 1


def test_scope_driver_note_weight_increase():
    note = aw._scope_driver_note(_mkfile(est_wt=760, act_wt=1217))
    assert "1,217" in note and "760" in note and "%" in note


def test_scope_driver_note_empty_when_no_material_change():
    assert aw._scope_driver_note(_mkfile(est_wt=760, act_wt=760)) == ""
    assert aw._scope_driver_note(_mkfile()) == ""


def test_job_110995_multi_option_fully_invoiced_does_not_flag():
    """Regression: main + insurance options both invoiced exactly -> no flag."""
    f = _mkfile(
        job=110995, inv_amt=196166.22, sell=186240.68,
        q_lines=[{"desc": "Removal", "value": 186240.68}, {"desc": "Insurance", "value": 9925.54}],
        i_lines=[{"desc": "Removal", "value": 186240.68}, {"desc": "Insurance", "value": 9925.54}],
        est_wt=760, act_wt=760,
    )
    aw.check_calculations([f])
    assert f["disc_flags"] == []
    assert f["disc_value"] == 0


def test_genuine_extra_charge_flags_as_informational_scope_item():
    f = _mkfile(
        job=110996, coordinator="Ben", inv_amt=15000, sell=12000,
        q_lines=[{"desc": "Removal", "value": 12000}],
        i_lines=[{"desc": "Removal", "value": 12000}, {"desc": "Storage", "value": 3000}],
        est_wt=600, act_wt=900,
    )
    aw.check_calculations([f])
    assert f["disc_value"] == 3000
    flag = f["disc_flags"][0]
    assert flag["type"] == "extra_charges"
    assert flag["info"] is True
    assert "+50%" in flag["label"]
    assert "Storage" in flag["added"]


def test_not_invoiced_file_never_flags():
    f = _mkfile(job=200, invoiced=False, inv_amt=0, sell=5000,
                q_lines=[{"desc": "Removal", "value": 5000}], i_lines=[])
    aw.check_calculations([f])
    assert f["disc_value"] == 0


# ════════════════════════════════════════════════════════════════════════════
# 2. Rolling 12-month window + background-auditor walk
# ════════════════════════════════════════════════════════════════════════════
def _wf(deliv=None, pack=None):
    return {"delivery": deliv, "pack": pack}


def test_recent_delivery_is_in_window():
    assert mw._is_out_of_window(_wf(deliv=TODAY - dt.timedelta(days=30))) is False


def test_old_delivery_is_out_of_window():
    assert mw._is_out_of_window(_wf(deliv=TODAY - dt.timedelta(days=400))) is True


def test_undated_open_file_is_in_window():
    assert mw._is_out_of_window(_wf()) is False


def test_pack_used_when_no_delivery():
    assert mw._is_out_of_window(_wf(pack=TODAY - dt.timedelta(days=500))) is True
    assert mw._is_out_of_window(_wf(pack=TODAY - dt.timedelta(days=10))) is False


def test_window_boundary_is_inclusive():
    cut = mw._window_cutoff()
    assert mw._is_out_of_window(_wf(deliv=cut)) is False
    assert mw._is_out_of_window(_wf(deliv=cut - dt.timedelta(days=1))) is True


@pytest.fixture
def fake_feed(monkeypatch):
    state = {"total": 400, "recent_from": 300, "map_calls": 0}

    def get_timed(url, to):
        off = int(re.search(r"offset=(\d+)", url).group(1))
        lim = int(re.search(r"limit=(\d+)", url).group(1))
        start = (off - 1) * lim + 1
        ids = [i for i in range(start, min(start + lim, state["total"] + 1))]
        return {"jobs": [{"id": str(i)} for i in ids]}

    def map_job(j):
        state["map_calls"] += 1
        i = int(j["id"])
        d = (TODAY - dt.timedelta(days=20)) if i >= state["recent_from"] else (TODAY - dt.timedelta(days=500))
        return {"job": j["id"], "delivery": d, "pack": None}

    monkeypatch.setattr(mw, "_feed_total", lambda: state["total"])
    monkeypatch.setattr(mw, "_get_timed", get_timed)
    monkeypatch.setattr(mw, "_page_jobs", lambda p: p["jobs"])
    monkeypatch.setattr(mw, "_job_status", lambda j: "W")
    monkeypatch.setattr(mw, "_map_job", map_job)
    mw._AUDIT.update({
        "files": {}, "old_ids": set(), "total": None, "page": None, "cycles": 0,
        "errors": 0, "consec_old": 0, "started_at": None, "last_cycle_at": None,
        "wrapped": False, "window_complete": False,
        "last_full_at": None, "saved_at": None, "refetch": False,
    })
    return state


def _run_until_complete(max_cycles=2000):
    c = 0
    while not mw._AUDIT["window_complete"] and c < max_cycles:
        mw._auditor_cycle()
        c += 1
    return c


def test_walk_caches_all_in_window_files(fake_feed):
    _run_until_complete()
    cached = sorted(int(x) for x in mw._AUDIT["files"])
    assert cached == list(range(300, 401))


def test_walk_stops_without_scanning_whole_history(fake_feed):
    _run_until_complete()
    assert mw._AUDIT["window_complete"] is True
    assert len(mw._AUDIT["old_ids"]) <= mw._WINDOW_STOP + mw._AUDIT_PAGE


def test_excluded_files_are_not_refetched(fake_feed):
    _run_until_complete()
    calls = fake_feed["map_calls"]
    for _ in range(5):
        mw._auditor_cycle()
    assert fake_feed["map_calls"] - calls == 0


def test_running_window_picks_up_new_files(fake_feed):
    _run_until_complete()
    before = len(mw._AUDIT["files"])
    fake_feed["total"] = 405
    for _ in range(60):
        mw._auditor_cycle()
    after = sorted(int(x) for x in mw._AUDIT["files"])
    assert len(after) > before
    assert {401, 402, 403, 404, 405}.issubset(set(after))


def test_feed_smaller_than_window_completes(fake_feed):
    fake_feed["recent_from"] = 0
    _run_until_complete()
    assert mw._AUDIT["window_complete"] is True
    assert len(mw._AUDIT["files"]) == 400


# ── persistence (survives restart) ──────────────────────────────────────────
def test_snapshot_round_trip_preserves_files_and_dates(tmp_path, monkeypatch):
    monkeypatch.setattr(mw, "_CACHE_PATH", str(tmp_path / "cache.json"))
    d = TODAY - dt.timedelta(days=12)
    mw._AUDIT.update({
        "files": {"110995": {"job": "110995", "sell": 196166.22, "delivery": d, "pack": None,
                             "q_lines": [{"desc": "Removal", "value": 186240.68}], "invoiced": True}},
        "old_ids": {"100001", "100002"}, "total": 11008, "window_complete": True,
        "last_full_at": 1234.0, "saved_at": None, "refetch": False,
    })
    mw._persist_snapshot()

    # wipe in-memory state, then reload from disk
    mw._AUDIT.update({"files": {}, "old_ids": set(), "total": None,
                      "window_complete": False, "last_full_at": None})
    assert mw._load_snapshot() is True
    assert set(mw._AUDIT["files"]) == {"110995"}
    f = mw._AUDIT["files"]["110995"]
    assert f["delivery"] == d and isinstance(f["delivery"], dt.date)   # date survived
    assert f["q_lines"][0]["value"] == 186240.68
    assert mw._AUDIT["old_ids"] == {"100001", "100002"}
    assert mw._AUDIT["window_complete"] is True
    assert mw._AUDIT["last_full_at"] == 1234.0


def test_snapshot_ignored_if_window_length_changed(tmp_path, monkeypatch):
    monkeypatch.setattr(mw, "_CACHE_PATH", str(tmp_path / "cache.json"))
    mw._AUDIT.update({"files": {"1": {"job": "1"}}, "old_ids": set(), "total": 10,
                      "window_complete": True, "last_full_at": 1.0})
    mw._persist_snapshot()
    monkeypatch.setattr(mw, "_WINDOW_DAYS", 730)   # window redefined
    mw._AUDIT.update({"files": {}, "window_complete": False})
    assert mw._load_snapshot() is False            # stale snapshot rejected


def test_missing_snapshot_returns_false(tmp_path, monkeypatch):
    monkeypatch.setattr(mw, "_CACHE_PATH", str(tmp_path / "nope.json"))
    assert mw._load_snapshot() is False


# ── scheduled refresh re-fetches cached files ───────────────────────────────
def test_refresh_repicks_up_new_invoice_on_cached_file(fake_feed, monkeypatch):
    _run_until_complete()
    assert "400" in mw._AUDIT["files"]
    # Simulate a new invoice landing on an already-cached file: change what the
    # feed's mapper returns for id 400, then run a refresh cycle set.
    base_map = mw._map_job
    def new_map(j):
        m = base_map(j)
        if m and j["id"] == "400":
            m["inv_amt"] = 99999
        return m
    monkeypatch.setattr(mw, "_map_job", new_map)
    # enter refresh mode (what the loop does when _REFRESH_SECONDS elapses)
    mw._AUDIT.update({"refetch": True, "window_complete": False, "consec_old": 0,
                      "old_ids": set(), "page": mw._auditor_last_page(mw._AUDIT["total"])})
    for _ in range(2000):
        mw._auditor_cycle()
        if mw._AUDIT["window_complete"]:
            break
    assert mw._AUDIT["files"]["400"]["inv_amt"] == 99999   # refreshed in place


# ════════════════════════════════════════════════════════════════════════════
# 3. compute_metrics guards + rollups + template rendering
# ════════════════════════════════════════════════════════════════════════════
def test_empty_files_do_not_raise():
    m = aw.compute_metrics([], live_counts=LIVE_COUNTS, cost_available=False)
    assert m["sample_n"] == 0 and m["tot_revenue"] == 0 and m["total_disc"] == 0


def test_window_fields_exposed():
    m = aw.compute_metrics([], live_counts=LIVE_COUNTS, cost_available=False)
    assert m["window_days"] == 365 and m["window_months"] == 12
    assert m["cost_available"] is False


def test_revenue_sums_invoiced_where_billed_else_quoted():
    files = [
        _mkfile(job=1, invoiced=True, inv_amt=196166.22, sell=186240.68),
        _mkfile(job=2, invoiced=False, inv_amt=0, sell=5000),
    ]
    aw.check_calculations(files)
    m = aw.compute_metrics(files, live_counts=LIVE_COUNTS, cost_available=False)
    assert m["tot_revenue"] == round(196166.22 + 5000)


def test_only_flagged_files_appear_in_disc_worklist():
    clean = _mkfile(
        job=110995, inv_amt=196166.22, sell=186240.68,
        q_lines=[{"desc": "Removal", "value": 186240.68}, {"desc": "Ins", "value": 9925.54}],
        i_lines=[{"desc": "Removal", "value": 186240.68}, {"desc": "Ins", "value": 9925.54}],
        est_wt=760, act_wt=760, delivery=TODAY - dt.timedelta(days=20),
    )
    flagged = _mkfile(
        job=110996, coordinator="Ben", inv_amt=15000, sell=12000,
        q_lines=[{"desc": "Removal", "value": 12000}],
        i_lines=[{"desc": "Removal", "value": 12000}, {"desc": "Storage", "value": 3000}],
        est_wt=600, act_wt=900, delivery=TODAY - dt.timedelta(days=10),
    )
    files = [clean, flagged]
    aw.check_calculations(files)
    m = aw.compute_metrics(files, live_counts=LIVE_COUNTS, cost_available=False)
    assert {r["job"] for r in m["disc_worklist"]} == {110996}
    assert m["disc_files"] == 1 and m["total_disc"] == 3000 and m["coords_affected"] == 1


def test_template_renders_all_live_states():
    tmpl = Template(aw.TEMPLATE)
    m0 = aw.compute_metrics([], live_counts=LIVE_COUNTS, cost_available=False)
    assert "12-month" in tmpl.render(m=m0, demo=False)
    counts = dict(LIVE_COUNTS, window_complete=True, excluded_old=4237,
                  last_full_at=dt.datetime.now().timestamp() - 2 * 3600,
                  refresh_seconds=6 * 3600)
    f = _mkfile(job=1, inv_amt=1000, sell=1000,
                q_lines=[{"desc": "A", "value": 1000}], i_lines=[{"desc": "A", "value": 1000}],
                delivery=TODAY - dt.timedelta(days=5))
    aw.check_calculations([f])
    m = aw.compute_metrics([f], live_counts=counts, cost_available=False)
    assert m["last_updated"] == "2 hours ago" and m["refresh_hours"] == 6
    html = tmpl.render(m=m, demo=False)
    assert "Rolling 12-month audit" in html
    assert "updated 2 hours ago" in html and "auto-refreshes every 6h" in html


def test_demo_dataset_renders():
    files = aw.reconcile(aw.load_move_files())
    aw.check_calculations(files)
    m = aw.compute_metrics(files, live_counts=None, cost_available=True)
    assert len(Template(aw.TEMPLATE).render(m=m, demo=True)) > 1000
