"""SIT's real trucks as input to the consolidation engine.

The engine plans against a hypothetical 88 m³ trailer. SIT already runs trucks
to these hubs every week with space left on them, so the question worth
answering is not "how many trailers would this need" but "which truck that is
ALREADY GOING has room". These cover that, and the rule that it only ever
advises.
"""
import datetime as dt

from crossborder import sit

TODAY = dt.date(2026, 9, 15)

TRUCKS = [
    {"unit": "A23", "date": "2026-09-17", "spare_m3": 30.0, "fill_pct": 65,
     "driver": "J. Ruiz", "lanes": ["MEX → Zapopan, Jalisco"]},
    {"unit": "A31", "date": "2026-09-18", "spare_m3": 8.0, "fill_pct": 90,
     "driver": "L. Diaz", "lanes": ["MEX → Guadalajara, Jal."]},
    {"unit": "T6", "date": "2026-09-16", "spare_m3": 45.0, "fill_pct": 55,
     "driver": "P. Mora", "lanes": ["MTY → Monterrey, N.L."]},
    {"unit": "A9", "date": "2026-09-19", "spare_m3": 20.0, "fill_pct": 76,
     "driver": "R. Sosa", "lanes": ["MEX → Villahermosa, Tab."]},
]


# ── which hub is a truck going to ────────────────────────────────────────────


def test_a_truck_lane_resolves_to_a_hub():
    assert sit.truck_hub(TRUCKS[0]) == "Guadalajara"     # Zapopan
    assert sit.truck_hub(TRUCKS[2]) == "Monterrey"
    assert sit.truck_hub(TRUCKS[3]) == "Mérida"          # Villahermosa


def test_an_unmappable_lane_is_unknown_not_a_crash():
    assert sit.truck_hub({"lanes": ["X → Nowheresville"]}) == "Unknown"
    assert sit.truck_hub({}) == "Unknown"


# ── the number the team cannot get anywhere else ─────────────────────────────


def test_spare_space_is_totalled_by_hub():
    """'You have 38 m³ of paid-for empty space going to Guadalajara this week.'"""
    by = sit.spare_by_hub(TRUCKS)
    assert by["Guadalajara"]["spare_m3"] == 38.0      # A23 30 + A31 8
    assert by["Guadalajara"]["trucks"] == 2
    assert sorted(by["Guadalajara"]["units"]) == ["A23", "A31"]
    assert by["Monterrey"]["spare_m3"] == 45.0


def test_hubs_are_ordered_by_most_spare_space():
    assert list(sit.spare_by_hub(TRUCKS))[0] == "Monterrey"


# ── offering a real truck for a suggested load ───────────────────────────────


def _load(hub, m3, depart):
    return {"lane": f"Import → {hub}", "hub": hub, "m3": m3, "depart_by": depart}


def test_a_truck_going_the_same_way_in_time_is_offered():
    out = sit.offer_trucks([_load("Guadalajara", 25.0, "2026-09-19")], TRUCKS, TODAY)
    units = [t["unit"] for t in out[0]["trucks"]]
    assert units[0] == "A23", "the one that fits comes first"
    assert out[0]["trucks"][0]["fits"] is True


def test_a_truck_too_small_is_still_shown_but_not_marked_fitting():
    """Splitting a shipment onto a truck already rolling can still beat booking
    a new trailer — so a partial is worth seeing, just not claimed as a fit."""
    out = sit.offer_trucks([_load("Monterrey", 60.0, "2026-09-20")], TRUCKS, TODAY)
    assert out[0]["trucks"][0]["unit"] == "T6"
    assert out[0]["trucks"][0]["fits"] is False


def test_a_truck_to_a_different_hub_is_never_offered():
    out = sit.offer_trucks([_load("Mexico City", 10.0, "2026-09-20")], TRUCKS, TODAY)
    assert out[0]["trucks"] == []


def test_a_truck_leaving_after_the_deadline_is_not_offered():
    out = sit.offer_trucks([_load("Guadalajara", 5.0, "2026-09-12")], TRUCKS, TODAY)
    assert out[0]["trucks"] == [], "it departs after the load must be gone"


def test_a_little_slack_is_allowed_past_the_deadline():
    """A truck a day or two late is a conversation, not a no."""
    out = sit.offer_trucks([_load("Guadalajara", 5.0, "2026-09-16")], TRUCKS, TODAY)
    assert [t["unit"] for t in out[0]["trucks"]] == ["A23", "A31"]


def test_a_truck_in_the_past_is_never_offered():
    out = sit.offer_trucks([_load("Monterrey", 5.0, "2026-09-25")], TRUCKS,
                           dt.date(2026, 9, 18))
    assert out[0]["trucks"] == []


def test_a_full_truck_is_not_offered():
    full = [{**TRUCKS[0], "spare_m3": 0.0}]
    out = sit.offer_trucks([_load("Guadalajara", 5.0, "2026-09-19")], full, TODAY)
    assert out[0]["trucks"] == []


def test_the_load_itself_is_never_modified():
    """The engine's own numbers must survive untouched — this only annotates."""
    ld = _load("Guadalajara", 25.0, "2026-09-19")
    out = sit.offer_trucks([ld], TRUCKS, TODAY)
    assert out[0]["m3"] == 25.0 and out[0]["lane"] == ld["lane"]
    assert "trucks" not in ld, "the input dict must not be mutated"


def test_no_trucks_means_an_empty_offer_not_an_error():
    out = sit.offer_trucks([_load("Guadalajara", 25.0, "2026-09-19")], [], TODAY)
    assert out[0]["trucks"] == []
