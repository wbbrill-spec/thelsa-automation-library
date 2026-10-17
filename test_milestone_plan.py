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
    s = mp.plan_for_job("111041", _sit(pack=("2026-08-17", "done")), [act("Uplift", "2026-08-19")], T)
    assert s[0]["action"] == "update" and s[0]["body"]["date"] == "2026-08-17"


def test_load_counts_not_pack():
    # A Pack reminder alone is not the load: the Load (Uplift) action is created.
    s = mp.plan_for_job("111041", _sit(pack=("2026-08-17", "done")), [act("Pack", "2026-08-16")], T)
    assert s[0]["action"] == "create" and s[0]["body"]["type"] == "Uplift" and s[0]["body"]["comment"] == "Load"
    # Variants of Load are matched, not duplicated.
    s = mp.plan_for_job("1", _sit(pack=("2026-08-17", "done")),
                        [{"type": "Uplift O/S", "keyaction": "Uplift", "date": "2026-08-17T00:00:00",
                          "completed": "Y", "completedby": "SR2", "id": 4}], T)
    assert s == []


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
                        [act("Uplift", "2026-12-07", "Y", "EH1")], T)
    assert [x["action"] for x in s] == ["review"] and "future" in s[0]["why"]
    assert mp.summarize(s) == {"create": 0, "update": 0, "review": 1, "files": 1}


def test_load_and_pack_written_from_their_own_plan_rows():
    sit = {"load": {"date": "2026-10-07", "status": "done"},
           "packing": {"date": "2026-10-05", "status": "done"},
           "pack": {"date": "2026-10-05", "status": "done"}}
    s = mp.plan_for_job("111200", sit, [act("Pack", "2026-10-06", id_=2)], T, job_type="XP")
    by = {x["milestone"]: x for x in s}
    assert by["load"]["action"] == "create" and by["load"]["body"]["type"] == "Uplift"
    assert by["load"]["body"]["date"] == "2026-10-07"
    assert by["pack"]["action"] == "update" and by["pack"]["body"]["date"] == "2026-10-05"


def test_packing_row_alone_never_becomes_the_load():
    sit = {"load": None, "packing": {"date": "2026-10-05", "status": "done"},
           "pack": {"date": "2026-10-05", "status": "done"}}
    s = mp.plan_for_job("111202", sit, [], T, job_type="XP")
    assert [x["body"]["type"] for x in s] == ["Pack"]


def test_no_pack_written_on_agent_import():
    sit = {"load": None, "packing": {"date": "2026-10-05", "status": "done"}}
    assert mp.plan_for_job("111203", sit, [], T, job_type="IMA") == []
