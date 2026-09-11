"""Demo data exists to show the dashboard while Moveware and Graph are blocked.

The tests that matter here are the safety ones: it is off unless asked for, it
never lands in the cache the email drafters read, and every row is labelled.
"""
import datetime as dt

import pytest

from crossborder import demo, web
from crossborder.models import Source, Stage


class Args(dict):
    """Stand-in for request.args."""


TODAY = dt.date(2026, 9, 11)


# ── the switch ───────────────────────────────────────────────────────────────


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("CROSSBORDER_DEMO", raising=False)
    assert demo.demo_mode(Args()) == "off"
    assert demo.is_demo(Args()) is False


def test_query_param_turns_it_on(monkeypatch):
    monkeypatch.delenv("CROSSBORDER_DEMO", raising=False)
    assert demo.demo_mode(Args(demo="1")) == "mix"
    assert demo.demo_mode(Args(demo="only")) == "only"


def test_an_explicit_zero_beats_the_environment(monkeypatch):
    """A demo left switched on in Render must be turnable off from the URL."""
    monkeypatch.setenv("CROSSBORDER_DEMO", "1")
    assert demo.demo_mode(Args()) == "mix"
    assert demo.demo_mode(Args(demo="0")) == "off"


# ── the fleet ────────────────────────────────────────────────────────────────


def test_it_generates_what_was_asked_for():
    ships = demo.demo_shipments(TODAY)
    assert sum(1 for s in ships if s.source is Source.TMS) == 35
    assert sum(1 for s in ships if s.source is Source.TRS) == 120


def test_every_row_is_labelled_three_ways():
    for s in demo.demo_shipments(TODAY):
        assert s.extra["demo"] is True
        assert s.reference_number.startswith("DEMO-")
        assert s.customer_name.endswith("(demo)")
        assert s.id.split(":", 1)[1].startswith("DEMO-")


def test_ids_are_unique_and_cannot_collide_with_live_ones():
    ships = demo.demo_shipments(TODAY)
    ids = [s.id for s in ships]
    assert len(set(ids)) == len(ids)


def test_it_is_deterministic():
    a = demo.demo_shipments(TODAY)
    b = demo.demo_shipments(TODAY)
    assert [s.customer_name for s in a] == [s.customer_name for s in b]
    assert [s.volume_m3 for s in a] == [s.volume_m3 for s in b]


def test_the_rows_are_plannable():
    """A demo whose shipments the engine refuses to pack shows an empty plan."""
    from crossborder import engine
    p = engine.plan(demo.demo_shipments(TODAY), today=TODAY)
    assert p["loads"], "the consolidation engine must have something to suggest"
    for ld in p["loads"]:
        assert ld["m3"] > 0
        # One SIT trip on a T-unit (100 m³) is bigger than the 88 m³ baseline
        # trailer. That may ride alone — it must never have been packed with
        # something else on top.
        assert ld["m3"] <= ld["truck_m3"] or len(ld["shipments"]) == 1


def test_domestic_trips_never_share_a_trailer_with_a_crossing():
    from crossborder import engine
    p = engine.plan(demo.demo_shipments(TODAY), today=TODAY)
    for ld in p["loads"]:
        srcs = {it["source"] for it in ld["shipments"]}
        assert not ({"TRS"} & srcs and srcs - {"TRS"}), \
            f"lane {ld['lane']} mixes domestic and cross-border: {srcs}"


def test_trs_trips_stay_inside_mexico():
    for s in demo.trs_shipments(20, TODAY):
        assert s.extra["direction"] == "domestic"
        assert s.extra["plaza_origen"] != s.extra["plaza_destino"]
        assert s.stage not in (Stage.CUSTOMS, Stage.TO_BORDER), "domestic moves never clear customs"


def test_volumes_fit_a_real_unit():
    for s in demo.trs_shipments(120, TODAY):
        assert 0 < s.volume_m3 <= s.extra["unidad_m3"]


# ── the safety rules ─────────────────────────────────────────────────────────


def test_demo_rows_never_enter_the_cache(monkeypatch):
    """_with_demo mixes at the edge of one request. The drafters read the cache,
    so a simulated shipment must not be reachable from there."""
    monkeypatch.delenv("CROSSBORDER_DEMO", raising=False)
    web._CACHE.update(at=1.0, shipments=[], diag={}, completed=False, error=None)
    ships, diag = web._with_demo([], {}, Args(demo="1"))
    assert len(ships) == 155
    assert web._CACHE["shipments"] == [], "the cache must be untouched"


def test_mix_keeps_the_live_rows_and_only_flags_the_fake_ones(monkeypatch):
    monkeypatch.delenv("CROSSBORDER_DEMO", raising=False)
    from crossborder.models import Shipment
    live = [Shipment(id="TIM:1", source=Source.TIM, source_ref="1", customer_name="Real")]
    ships, diag = web._with_demo(live, {}, Args(demo="1"))
    assert ships[0].id == "TIM:1" and not ships[0].extra.get("demo")
    assert diag["demo"]["count"] == 155 and diag["demo"]["mode"] == "mix"
    assert sum(1 for s in ships if s.extra.get("demo")) == 155


def test_demo_only_hides_the_live_rows(monkeypatch):
    monkeypatch.delenv("CROSSBORDER_DEMO", raising=False)
    from crossborder.models import Shipment
    live = [Shipment(id="TIM:1", source=Source.TIM, source_ref="1")]
    ships, diag = web._with_demo(live, {}, Args(demo="only"))
    assert all(s.extra.get("demo") for s in ships)


def test_no_demo_means_no_change(monkeypatch):
    monkeypatch.delenv("CROSSBORDER_DEMO", raising=False)
    ships, diag = web._with_demo([], {"a": 1}, Args())
    assert ships == [] and diag == {"a": 1}


def test_drafting_is_refused_while_the_environment_is_in_demo_mode(monkeypatch):
    monkeypatch.setenv("CROSSBORDER_DEMO", "1")
    assert "demo" in (web._demo_blocks_drafting() or "").lower()
    out = web.create_alert_drafts(actor="test")
    assert out["ok"] is False and "demo" in out["reason"].lower()
    out = web.create_plan_draft(actor="test")
    assert out["ok"] is False and "demo" in out["reason"].lower()


def test_drafting_is_allowed_when_demo_is_off(monkeypatch):
    monkeypatch.delenv("CROSSBORDER_DEMO", raising=False)
    assert web._demo_blocks_drafting() is None
