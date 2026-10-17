"""Job-type date rules (MoveWare training 16 Oct 2026) across the audit."""
import datetime as dt

import costing_sheet as cs
import job_rules as jr
import milestone_plan as mp
import sit_milestones as sm

T = dt.date(2026, 10, 15)


def test_expected_by_type():
    assert not jr.delivery_expected("EXA") and not jr.delivery_expected("exa3")
    assert jr.pack_expected("EXA") and not jr.pack_expected("IMA") and not jr.pack_expected("IMA1")
    assert jr.pack_expected("XP") and jr.delivery_expected("XP") and jr.delivery_expected("")


def test_keyaction_matching():
    assert jr.is_milestone({"type": "Pack2", "keyaction": "Pack"}, "pack")
    assert not jr.is_milestone({"type": "Uplift", "keyaction": "Uplift"}, "pack")
    assert jr.is_milestone({"type": "Uplift O/S", "keyaction": "Uplift"}, "load")
    assert jr.is_milestone({"type": "Pack1"}, "pack")          # no keyaction returned


def test_confirmed_dates_only_completed():
    acts = [{"type": "Pack", "keyaction": "Pack", "date": "2026-10-20T00:00:00", "completed": "N"},
            {"type": "Delivery", "keyaction": "Delivery", "date": "2026-09-30T00:00:00", "completed": "Y"}]
    assert jr.confirmed_dates(acts) == {"pack": None, "delivery": "2026-09-30", "load": None, "has_load": False}
    acts.append({"type": "Uplift O/S", "date": "2026-09-28", "completed": "Y"})
    c = jr.confirmed_dates(acts)
    assert c["load"] == "2026-09-28" and c["has_load"]


def test_plan_skips_delivery_on_exa_and_pack_on_ima():
    sit = {"pack": {"date": "2026-09-01", "status": "done"}, "delivery": {"date": "2026-09-05", "status": "done"}}
    exa = mp.plan_for_job("1", sit, [], T, job_type="EXA")
    assert [s["milestone"] for s in exa] == ["load"]
    ima = mp.plan_for_job("2", sit, [], T, job_type="IMA")
    assert [s["milestone"] for s in ima] == ["delivery"]


def test_plan_uses_keyaction_and_prefers_plain_code():
    sit = {"pack": {"date": "2026-09-01", "status": "done"}}
    acts = [{"type": "UpliftandReturntoWH", "keyaction": "Uplift", "date": "2026-08-30T00:00:00", "completed": "N", "id": 7},
            {"type": "Uplift", "keyaction": "Uplift", "date": "2026-08-31T00:00:00", "completed": "N", "id": 8},
            {"type": "Pack", "keyaction": "Pack", "date": "2026-08-30T00:00:00", "completed": "N", "id": 9}]
    s = mp.plan_for_job("3", sit, acts, T, job_type="XP")
    assert len(s) == 1 and s[0]["action"] == "update" and s[0]["activity_id"] == 8


def test_sit_compare_not_missing_when_not_expected():
    jobs = {"110900": {"customer": "", "coordinator": "", "legs": [],
                       "pack": {"date": "2026-09-01", "status": "done"},
                       "delivery": {"date": "2026-09-05", "status": "done"}}}
    out = sm.compare(jobs, [{"job": "110900", "job_type": "EXA", "pack": "2026-09-01"}], T)
    assert out["delivery_missing"] == 0 and out["rows"][0]["gaps"] == []
    assert out["rows"][0]["not_expected"] == ["delivery"]


def test_duplicate_cost_line():
    parsed = {"files": {"110700": {"options": [{"status": "W", "lines": [
        {"desc": "Freight", "est": 1000, "act": 1000, "po": "500", "creditor": "Landstar"},
        {"desc": "Freight (new)", "est": 1000, "act": 1000, "po": "501", "creditor": "Landstar"},
        {"desc": "Handling", "est": 200, "act": 180, "po": "502", "creditor": "WH"}]}]}}}
    rows = [r for r in cs.supplier_checks(parsed, {}, T) if r["check"] == "duplicate_line"]
    assert len(rows) == 1 and rows[0]["times"] == 2 and rows[0]["pos"] == ["500", "501"]
    assert rows[0]["difference"] == 1000.0
