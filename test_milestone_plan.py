"""Dry run of the Plan de Viajes → MoveWare diary write-back."""
import datetime as dt

import milestone_plan as mp

T = dt.date(2026, 10, 15)


def _sit(pack=None, deliv=None):
    j = {}
    if pack:
        j["pack"] = {"date": pack[0], "status": pack[1]}
    if deliv:
        j["delivery"] = {"date": deliv[0], "status": deliv[1]}
    return j


def act(type_, date, completed="N", by="", id_=1):
    return {"type": type_, "date": date + "T00:00:00", "completed": completed, "completedby": by, "id": id_}


def test_create_when_missing_and_done():
    s = mp.plan_for_job("110862", _sit(deliv=("2026-10-09", "done")), [], T)
    assert len(s) == 1 and s[0]["action"] == "create"
    assert s[0]["body"]["type"] == "Delivery" and s[0]["body"]["completed"] == "Y"


def test_never_for_future_or_unconfirmed():
    assert mp.plan_for_job("1", _sit(pack=("2026-12-07", "scheduled")), [], T) == []
    assert mp.plan_for_job("1", _sit(pack=("2026-09-17", "unconfirmed")), [], T) == []


def test_update_open_action():
    s = mp.plan_for_job("111041", _sit(pack=("2026-08-17", "done")), [act("Pack", "2026-08-19")], T)
    assert s[0]["action"] == "update" and s[0]["body"]["date"] == "2026-08-17"


def test_fix_mistyped_year():
    s = mp.plan_for_job("110801", _sit(deliv=("2026-09-04", "done")),
                        [act("Delivery", "2020-09-02", "Y", "EH1")], T)
    assert s[0]["action"] == "update" and "mistyped" in s[0]["why"]


def test_person_date_is_reviewed_not_overwritten():
    s = mp.plan_for_job("110859", _sit(deliv=("2026-09-30", "done")),
                        [act("Delivery", "2026-09-28", "Y", "SR2")], T)
    assert s[0]["action"] == "review"


def test_matching_completed_needs_nothing():
    assert mp.plan_for_job("110492", _sit(deliv=("2026-09-17", "done")),
                           [act("Delivery", "2026-09-17", "Y", "SB1")], T) == []


def test_future_completed_is_flagged():
    s = mp.plan_for_job("111147", _sit(pack=("2026-12-07", "scheduled")),
                        [act("Pack", "2026-12-07", "Y", "EH1")], T)
    assert [x["action"] for x in s] == ["review"] and "future" in s[0]["why"]
    assert mp.summarize(s) == {"create": 0, "update": 0, "review": 1, "files": 1}
