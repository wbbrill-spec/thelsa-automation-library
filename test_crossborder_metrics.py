"""The consolidation scoreboard — is consolidation actually improving?

The programme's case is that filling trucks saves money. Until 23 Sep nobody
was measuring it, so nobody could say whether the team filled trucks any better
this month than last. These tests pin the numbers that claim will be argued on.
"""
import datetime as dt

import pytest

from crossborder import metrics
from crossborder.models import TRUCK_53_M3

TODAY = dt.date(2026, 9, 23)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CB_METRICS_PATH", str(tmp_path / "m.json"))
    for k in ("CB_TRUCK_COST_MXN", "CB_LANE_COST"):
        monkeypatch.delenv(k, raising=False)
    metrics.reset()


def load(files, space, lane="Monterrey → Mexico City", leg="onward", m3=None):
    return {"lane": lane, "leg": leg, "space_m3": space, "m3": m3 if m3 is not None else space,
            "fill_pct": round(space / TRUCK_53_M3 * 100),
            "shipments": [{"id": f"X{i}"} for i in range(files)]}


def group(files, space, lane="Monterrey → Mexico City", leg="onward"):
    return {"lane": lane, "leg": leg, "customers": files, "space_m3": space,
            "m3": space, "fill_pct": round(space / TRUCK_53_M3 * 100)}


def test_utilisation_is_space_used_over_trailers_opened():
    p = {"loads": [load(3, 44.0), load(2, 22.0)], "groups": []}
    m = metrics.summarise(p, TODAY)
    assert m["loads"] == 2 and m["space_m3"] == 66.0
    assert m["capacity_m3"] == round(2 * TRUCK_53_M3, 1)
    assert m["utilisation_pct"] == round(66.0 / (2 * TRUCK_53_M3) * 100)


def test_the_teams_own_consolidations_count_too():
    """Measuring only our suggestions would flatter us — Fernanda's own
    groupings are the real work and belong in the same number."""
    p = {"loads": [load(2, 20.0)], "groups": [group(8, 64.0)]}
    m = metrics.summarise(p, TODAY)
    assert m["loads"] == 2 and m["actual_loads"] == 1
    assert m["files_on_trucks"] == 10 and m["files_per_load"] == 5.0


def test_solo_trucks_are_counted_because_that_is_what_should_fall():
    p = {"loads": [load(1, 12.0), load(1, 9.0), load(4, 50.0)], "groups": []}
    m = metrics.summarise(p, TODAY)
    assert m["solo_loads"] == 2 and m["solo_pct"] == 67 and m["shared_loads"] == 1


def test_an_export_travelling_alone_is_policy_not_a_failure_to_consolidate():
    """Fernanda: exports do not wait. On the live board today four of seven
    loads were exports doing exactly what they should, and counting them as
    solo trucks read 57% — a number that would have been quoted as a problem."""
    exports = [{**load(1, 20.0, lane="Export → Texas", leg="export"), "ships_alone": True}
               for _ in range(4)]
    p = {"loads": exports + [load(1, 12.0), load(3, 40.0)], "groups": []}
    m = metrics.summarise(p, TODAY)
    assert m["alone_by_policy"] == 4
    assert m["consolidatable_loads"] == 2
    assert m["solo_loads"] == 1 and m["solo_pct"] == 50


def test_trucks_avoided_is_files_beyond_the_first_on_each_load():
    """Eight files on one truck is seven trucks that did not run."""
    p = {"loads": [load(8, 64.0)], "groups": []}
    m = metrics.summarise(p, TODAY)
    assert m["savings"]["trucks_avoided"] == 7
    assert m["savings"]["assumed_mxn"] == 7 * 22000


def test_a_load_carrying_one_file_avoids_nothing():
    m = metrics.summarise({"loads": [load(1, 12.0)], "groups": []}, TODAY)
    assert m["savings"] is None


def test_the_saving_compares_against_the_small_lot_rate(monkeypatch):
    """D15, 23 Sep. Three files, 30 m³ between them — 10 m³ each, so each one
    alone is a single 15 m³ compartido lot at 22,000. Together they pay for one
    trailer. 66,000 against 22,000 = 44,000 saved."""
    monkeypatch.delenv("CB_TRUCK_COST_MXN", raising=False)
    monkeypatch.delenv("CB_SMALL_LOT_MXN", raising=False)
    m = metrics.summarise({"loads": [load(3, 30.0)], "groups": []}, TODAY)
    assert m["savings"]["alone_mxn"] == 66000
    assert m["savings"]["together_mxn"] == 22000
    assert m["savings"]["assumed_mxn"] == 44000
    # and it still says out loud what it assumed
    assert "compartido" in m["savings"]["assumption"]


def test_both_sides_of_the_comparison_are_configurable(monkeypatch):
    monkeypatch.setenv("CB_TRUCK_COST_MXN", "30000")
    monkeypatch.setenv("CB_SMALL_LOT_MXN", "18000")
    m = metrics.summarise({"loads": [load(3, 30.0)], "groups": []}, TODAY)
    assert m["savings"]["alone_mxn"] == 54000
    assert m["savings"]["assumed_mxn"] == 54000 - 30000


def test_a_small_lot_never_costs_more_than_hiring_the_whole_trailer(monkeypatch):
    """The rate sheet quotes one bracket, up to 15 m³. Charging two brackets
    for 16 m³ assumes somebody would pay 44,000 rather than hire a whole
    trailer for 22,000, which they would not — and it inflated the live
    savings figure above the cruder method it replaced."""
    monkeypatch.delenv("CB_SMALL_LOT_MXN", raising=False)
    monkeypatch.delenv("CB_TRUCK_COST_MXN", raising=False)
    assert metrics.small_lot_cost(15.0) == 22000
    assert metrics.small_lot_cost(15.5) == 22000      # capped, not doubled
    assert metrics.small_lot_cost(90.0) == 22000
    assert metrics.small_lot_cost(0) == 22000


def test_the_cap_follows_a_configured_trailer_price(monkeypatch):
    monkeypatch.setenv("CB_TRUCK_COST_MXN", "40000")
    monkeypatch.delenv("CB_SMALL_LOT_MXN", raising=False)
    assert metrics.small_lot_cost(30.0) == 40000      # two lots would be 44,000


def test_consolidating_can_never_show_a_negative_saving():
    assert metrics.consolidation_saving(1, 40.0) == {}
    s = metrics.consolidation_saving(2, 400.0)
    assert s["saved_mxn"] >= 0


def test_a_lane_can_carry_its_own_price(monkeypatch):
    monkeypatch.setenv("CB_LANE_COST", "Monterrey → Mexico City:18000,Guadalajara:25000")
    assert metrics.truck_cost("Monterrey → Mexico City") == 18000
    assert metrics.truck_cost("Monterrey → Guadalajara") == 25000
    assert metrics.truck_cost("Somewhere else") == 22000     # the default


def test_paid_for_empty_space_excludes_the_unknown_hub():
    p = {"loads": [load(2, 20.0)], "groups": [],
         "spare_by_hub": {"a": {"hub": "Monterrey", "spare_m3": 100.0},
                          "b": {"hub": "Unknown", "spare_m3": 230.0}}}
    assert metrics.summarise(p, TODAY)["paid_spare_m3"] == 100.0


# ── the curve ───────────────────────────────────────────────────────────────
def test_one_point_a_day_and_the_last_reading_wins():
    metrics.record({"as_of": "2026-09-23", "utilisation_pct": 50, "files_per_load": 2.0})
    metrics.record({"as_of": "2026-09-23", "utilisation_pct": 72, "files_per_load": 3.0})
    h = metrics.history()
    assert h["count"] == 1 and h["points"][0]["utilisation_pct"] == 72


def test_the_change_over_the_period_is_what_settles_the_argument():
    metrics.record({"as_of": "2026-08-23", "utilisation_pct": 58, "files_per_load": 1.8, "solo_pct": 40})
    metrics.record({"as_of": "2026-09-23", "utilisation_pct": 72, "files_per_load": 3.1, "solo_pct": 15})
    c = metrics.history()["change"]
    assert c["utilisation_pct"] == 14 and c["files_per_load"] == 1.3
    assert c["solo_pct"] == -25          # fewer solo trucks is the good direction


def test_one_day_on_record_offers_no_trend():
    metrics.record({"as_of": "2026-09-23", "utilisation_pct": 72})
    assert "change" not in metrics.history()


def test_nothing_planned_measures_to_zero_not_an_error():
    m = metrics.summarise({"loads": [], "groups": []}, TODAY)
    assert m["loads"] == 0 and m["utilisation_pct"] == 0 and m["savings"] is None
