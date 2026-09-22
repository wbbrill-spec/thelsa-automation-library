"""Plan de Viajes history — the dashboard keeping the receipts (training, 21 Sep).

Sara and Fernanda screenshot the workbook because accepted services sometimes
vanish or move date. These tests are the promise that they no longer have to.
"""
import datetime as dt

import pytest

from crossborder import plan_history
from crossborder.sit import Trip
from crossborder.models import Source


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("CB_PLAN_HISTORY_PATH", str(tmp_path / "history.json"))
    monkeypatch.delenv("CB_PLAN_HISTORY_TRACK_NEW", raising=False)
    plan_history.reset()


def trip(row, customer, *, job="", origin="MTY", dest="CDMX", unit="T1",
         load="2026-09-24", source=Source.TMS):
    return Trip(row=row, ejecutivo=f"{source.value}/Fernanda", source=source,
                coordinator="Fernanda", job_no=job, customer=customer, origin=origin,
                destination=dest, tipo="FOR", unit=unit,
                load_date=dt.date.fromisoformat(load) if load else None, m3=12.0)


def test_only_thelsa_rows_are_tracked():
    ours = trip(4, "Ana", job="110719")
    theirs = Trip(row=5, ejecutivo="OTRO/Juan", customer="Zeta", origin="MTY",
                  destination="GDL", tipo="FOR", unit="T9",
                  load_date=dt.date(2026, 9, 24))
    snap = plan_history.snapshot([ours, theirs])
    assert list(snap) == ["job:110719"]


def test_a_service_that_disappears_is_remembered():
    plan_history.record([trip(4, "Ana", job="110719"), trip(5, "Beto", job="110801")])
    out = plan_history.record([trip(4, "Ana", job="110719")])
    assert out["changes"]["vanished"] == 1
    ev = plan_history.history()["events"][0]
    assert ev["kind"] == "vanished" and "Beto" in ev["detail"]
    assert "no longer in it" in ev["detail"]


def test_a_date_change_is_remembered_with_both_dates():
    plan_history.record([trip(4, "Ana", job="110719", load="2026-09-24")])
    out = plan_history.record([trip(4, "Ana", job="110719", load="2026-09-26")])
    assert out["changes"]["date_changed"] == 1
    ev = plan_history.history()["events"][0]
    assert ev["from"] == "2026-09-24" and ev["to"] == "2026-09-26"
    assert "moved from 2026-09-24 to 2026-09-26" in ev["detail"]


def test_a_truck_change_is_remembered():
    plan_history.record([trip(4, "Ana", job="110719", unit="T1")])
    out = plan_history.record([trip(4, "Ana", job="110719", unit="T7")])
    assert out["changes"]["unit_changed"] == 1
    assert "changed truck from T1 to T7" in plan_history.history()["events"][0]["detail"]


def test_tim_rows_with_no_reference_still_match_on_customer_and_route():
    a = trip(4, "Carla Ruiz", source=Source.TIM)
    plan_history.record([a])
    out = plan_history.record([trip(9, "Carla Ruiz", source=Source.TIM, load="2026-09-28")])
    assert out["changes"]["date_changed"] == 1 and out["changes"]["vanished"] == 0


def test_the_first_run_records_nothing_and_new_services_are_quiet_by_default():
    first = plan_history.record([trip(4, "Ana", job="110719")])
    assert first["first_run"] and first["recorded"] == 0
    second = plan_history.record([trip(4, "Ana", job="110719"), trip(5, "Beto", job="110801")])
    assert second["changes"]["new"] == 1 and second["recorded"] == 0


def test_new_services_can_be_tracked_when_asked(monkeypatch):
    monkeypatch.setenv("CB_PLAN_HISTORY_TRACK_NEW", "1")
    plan_history.record([trip(4, "Ana", job="110719")])
    out = plan_history.record([trip(4, "Ana", job="110719"), trip(5, "Beto", job="110801")])
    assert out["recorded"] == 1 and out["events"][0]["kind"] == "new"


def test_history_can_be_filtered_and_is_newest_first():
    plan_history.record([trip(4, "Ana", job="110719"), trip(5, "Beto", job="110801")])
    plan_history.record([trip(4, "Ana", job="110719", load="2026-09-25")])   # Beto gone, Ana moved
    h = plan_history.history()
    assert [e["kind"] for e in h["events"]] == ["date_changed", "vanished"]
    assert plan_history.history(kinds=["vanished"])["count"] == 1


def test_a_job_split_across_units_keeps_the_earliest_load_day():
    snap = plan_history.snapshot([trip(4, "Ana", job="110719", load="2026-09-26", unit="T2"),
                                  trip(5, "Ana", job="110719", load="2026-09-24", unit="T1")])
    assert snap["job:110719"]["load_date"] == "2026-09-24"


def test_a_corrupt_state_file_does_not_break_the_refresh(tmp_path, monkeypatch):
    p = tmp_path / "broken.json"
    p.write_text("{not json")
    monkeypatch.setenv("CB_PLAN_HISTORY_PATH", str(p))
    out = plan_history.record([trip(4, "Ana", job="110719")])
    assert out["tracked"] == 1 and out["first_run"]
