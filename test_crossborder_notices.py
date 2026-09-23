"""
The consolidation notice (D32, 23 Sep) — and the bug Bill caught the same day.

The first version only looked at the planner's SUGGESTED loads. The trucks a
coordinator has already built — Fernanda's groups, read from her ClickUp note
— carried no checkbox and were not searchable, so the one place a coordinator
most wants to add a file and tell people about it was the one place the
feature did not reach.
"""
import datetime as dt

from crossborder import notices

TODAY = dt.date(2026, 9, 23)


def files(n, m3=10.0, source="TIM"):
    return [{"id": f"{source}:{i}", "customer": f"Customer {i}", "source": source,
             "reference": f"1300{i}", "m3": m3, "destination": "Mexico City"}
            for i in range(1, n + 1)]


def setup_function(_):
    notices.reset()


def teardown_function(_):
    notices.reset()


# ── the notice itself ────────────────────────────────────────────────────────
def test_it_says_who_is_on_the_truck_and_how_full_it_is():
    d = notices.build(files(3), "Monterrey → Mexico City")
    assert d["summary"]["files"] == 3
    assert d["summary"]["m3"] == 30.0
    for name in ("Customer 1", "Customer 2", "Customer 3"):
        assert name in d["body_en"] and name in d["body_es"]


def test_it_goes_out_in_both_languages():
    d = notices.build(files(2), "Monterrey → Mérida")
    assert "Please confirm from your side" in d["body_en"]
    assert "Por favor confirmen de su lado" in d["body_es"]
    assert d["body_en"] in d["body"] and d["body_es"] in d["body"]


def test_nothing_is_ever_sent():
    """A consolidation notice asks another company to hold space on a truck.
    It does not leave without a person reading it."""
    d = notices.build(files(2))
    assert d["sent"] is False
    assert "Nothing has been sent" in d["note"]


def test_it_shows_what_consolidating_saved():
    d = notices.build(files(3), "Monterrey → Mexico City")
    sv = d["summary"]["saving"]
    assert sv["alone_mxn"] == 66000       # three separate small lots
    assert sv["together_mxn"] == 22000    # one trailer
    assert sv["saved_mxn"] == 44000
    assert "44,000" in d["body_en"]


def test_a_single_file_is_not_a_consolidation():
    assert notices.build(files(1))["summary"]["saving"] == {}


def test_the_recipients_are_the_people_who_have_to_act(monkeypatch):
    monkeypatch.delenv("CB_NOTICE_TO", raising=False)
    to = notices.recipients()
    assert "fernandamora@thelsa.com" in to
    assert "sarareyes@thelsa.com" in to
    assert "gustavogonzalez@thelsa.com" in to


def test_recipients_can_be_changed_without_a_deploy(monkeypatch):
    monkeypatch.setenv("CB_NOTICE_TO", "iram@trs.example; ops@thelsa.com")
    assert notices.recipients() == ["iram@trs.example", "ops@thelsa.com"]


# ── the record, which is what the scorecard counts ───────────────────────────
def test_a_pick_is_remembered(monkeypatch, tmp_path):
    monkeypatch.setenv("CB_NOTICES_PATH", str(tmp_path / "n.json"))
    notices.record(notices.build(files(3), "Monterrey → Mexico City"))
    notices.record(notices.build(files(2), "Monterrey → Mérida"))
    r = notices.recent()
    assert r["count"] == 2
    assert r["files"] == 5
    assert r["saved_mxn"] > 0


def test_an_old_pick_falls_out_of_the_window(monkeypatch, tmp_path):
    monkeypatch.setenv("CB_NOTICES_PATH", str(tmp_path / "n.json"))
    notices.record(notices.build(files(2)))
    assert notices.recent(days=30)["count"] == 1
    assert notices.recent(days=0)["count"] == 1     # 0 means "do not filter"


def test_a_broken_store_never_takes_the_board_down(monkeypatch):
    """Recording a pick is a nicety. It must not be load-bearing."""
    monkeypatch.setenv("CB_NOTICES_PATH", "/proc/nope/cannot/write.json")
    row = notices.record(notices.build(files(2)))
    assert row["files"] == 2                        # returns, does not raise
    assert notices.recent()["count"] == 0


# ── the bug: groups were unreachable ─────────────────────────────────────────
def _plan_with_a_group():
    """What the engine hands back: a suggested load, plus a truck Fernanda is
    already filling with files that could still join it."""
    return {
        "loads": [{"lane": "Monterrey → Mérida",
                   "shipments": [{"id": "TIM:99", "customer": "Suggested One", "m3": 8.0}]}],
        "groups": [{
            "key": "g1", "lane": "McAllen → Monterrey (crossing)",
            "members": [{"id": "TIM:1", "customer": "Ana Saldivar", "m3": 5.7,
                         "destination": "Cuernavaca, MOR"},
                        {"id": "TIM:2", "customer": "Carol Ann Ashworth", "m3": 7.3,
                         "destination": "Mérida, YUC"}],
            "could_join": [{"id": "TIM:3", "customer": "Rebecca Dawn Hunter",
                            "space_m3": 26.4, "destination": "Guadalajara"}],
        }],
    }


def _collect(plan, ids):
    """Mirrors what the endpoint does when it resolves the ticked boxes."""
    wanted, picked, seen, lane = set(ids), [], set(), ""
    for ld in plan.get("loads") or []:
        for it in ld.get("shipments") or []:
            if it.get("id") in wanted and it["id"] not in seen:
                seen.add(it["id"]); picked.append(it); lane = lane or ld.get("lane", "")
    for g in plan.get("groups") or []:
        for it in (g.get("members") or []) + (g.get("could_join") or []):
            if it.get("id") in wanted and it["id"] not in seen:
                seen.add(it["id"]); picked.append(it); lane = lane or g.get("lane", "")
    return picked, lane


def test_members_of_an_existing_consolidation_can_be_picked():
    picked, lane = _collect(_plan_with_a_group(), ["TIM:1", "TIM:2"])
    assert [p["customer"] for p in picked] == ["Ana Saldivar", "Carol Ann Ashworth"]
    assert lane == "McAllen → Monterrey (crossing)"


def test_a_file_can_be_added_to_a_truck_that_is_already_being_filled():
    """The whole point of the card: 31 m³ free and six files that could use
    it. Ticking one has to reach the notice."""
    picked, _ = _collect(_plan_with_a_group(), ["TIM:1", "TIM:2", "TIM:3"])
    assert len(picked) == 3
    assert "Rebecca Dawn Hunter" in [p["customer"] for p in picked]


def test_suggestions_and_an_existing_truck_do_not_collide():
    picked, _ = _collect(_plan_with_a_group(), ["TIM:99", "TIM:1"])
    assert len(picked) == 2


def test_the_same_file_is_never_counted_twice():
    plan = _plan_with_a_group()
    plan["loads"][0]["shipments"].append({"id": "TIM:1", "customer": "Ana Saldivar", "m3": 5.7})
    picked, _ = _collect(plan, ["TIM:1"])
    assert len(picked) == 1


def test_one_lift_van_is_not_one_lift_vans():
    """This message goes to another company. It should not read like a
    placeholder somebody forgot to finish."""
    d = notices.build([{"id": "a", "customer": "A", "m3": 5.7, "lift_vans": 1},
                       {"id": "b", "customer": "B", "m3": 7.3, "lift_vans": 3}])
    assert "1 lift vans" not in d["body_en"]
    assert "1 lift van" in d["body_en"] and "3 lift vans" in d["body_en"]
    assert "1 huacales" not in d["body_es"]
    assert "1 huacal" in d["body_es"] and "3 huacales" in d["body_es"]
    # and no stray space before the comma
    assert " ," not in d["body_en"] and " ," not in d["body_es"]
    assert "5.7 m\u00b3, 1 lift van" in d["body_en"]


def test_u_boxes_read_the_same_in_both_languages():
    d = notices.build([{"id": "a", "customer": "A", "m3": 14.6, "u_boxes": 2},
                       {"id": "b", "customer": "B", "m3": 7.3, "u_boxes": 1}])
    for body in (d["body_en"], d["body_es"]):
        assert "2 U-Boxes" in body and "1 U-Box" in body
        assert "U-Boxs" not in body
