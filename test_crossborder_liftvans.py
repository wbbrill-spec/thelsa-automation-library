"""US Embassy / Consulate bookings ship in lift vans (Bill, 2026-09-21).

Moveware holds their GROSS volume including the vans; lift vans = m³ ÷ 5.7;
a 53 ft trailer takes 13. Volumes below are the live files on 2026-09-21.
"""
import datetime as dt

import pytest

from crossborder import engine
from crossborder.models import Hub, Shipment, Source, Stage

TODAY = dt.date(2026, 9, 21)
EMB = "EMBAJADA DE LOS ESTADOS UNIDOS DE AMERICA"
CON = "U.S. Consulate General Matamoros"


# NOTE: these files are planned as IMPORTS so the packing rules under test here
# are the lift-van ones, not the "exports never wait" rule (Fernanda, 22 Sep),
# which deliberately gives every export a trailer of its own. US Embassy and
# Consulate files no longer reach the board at all (Edgar, 22 Sep) — the
# capacity maths below still governs any lift-van-loaded freight.
def ship(i, m3, corp=EMB, service="FTL", bill_to="", direction="import", dest="Zapopan, Jalisco"):
    return Shipment(id=f"TMS:{i}", source=Source.TMS, source_ref=str(i), customer_name=f"C{i}",
                    destination=dest, destination_hub=Hub.UNKNOWN, stage=Stage.TO_BORDER, volume_m3=m3,
                    ready_date=dt.date(2026, 9, 18), corporate_account=corp,
                    extra={"direction": direction, "method": "ROAD", "service": service, "bill_to": bill_to})


# ── recognising the bookings ────────────────────────────────────────────────
@pytest.mark.parametrize("corp,expect", [
    (EMB, True), (CON, True),
    ("CONSULADO GENERAL DE ESTADOS UNIDOS EN MONTERREY", True),
    ("US Embassy Mexico City", True),
    ("Embajada de Canadá", False),        # the rule is about US missions only
    ("Dow", False), ("CORPORATIVO", False), ("", False),
])
def test_us_mission_detection(corp, expect):
    assert ship(1, 30, corp=corp).is_us_diplomatic is expect


def test_detected_via_bill_to_when_account_is_a_placeholder():
    assert ship(1, 30, corp="CORPORATIVO", bill_to=EMB).is_us_diplomatic


# ── gross m³ → lift vans ────────────────────────────────────────────────────
@pytest.mark.parametrize("m3,vans", [(35, 6), (52, 9), (30, 5), (26, 5), (25, 4), (10, 2), (4, 1)])
def test_lift_vans_are_gross_volume_over_5_7_to_nearest(m3, vans):
    assert ship(1, m3).lift_vans_planned == vans


def test_round_up_option(monkeypatch):
    monkeypatch.setenv("CB_LV_ROUNDING", "up")
    assert ship(1, 35).lift_vans_planned == 7 and ship(1, 25).lift_vans_planned == 5


def test_non_diplomatic_has_no_lift_vans():
    assert ship(1, 35, corp="Dow").lift_vans_planned == 0


# ── capacity in the engine ──────────────────────────────────────────────────
def test_item_consumes_positions_not_gross_m3():
    it = engine.make_item(ship(1, 35), TODAY)
    assert it.lift_vans == 6
    assert it.space == pytest.approx(6 * 88 / 13, abs=0.01)     # 40.6, not 35
    assert any("6 lift vans" in r and "6 of 13" in r for r in it.reasons)


def test_thirteen_lift_vans_fill_a_trailer_exactly():
    # two 35 m³ embassy files = 6 + 6 = 12 vans: one trailer, one position spare
    p = engine.plan([ship(1, 35), ship(2, 35)], today=TODAY)
    assert len(p["loads"]) == 1 and p["loads"][0]["lift_vans"] == 12
    assert p["loads"][0]["free_lift_van_positions"] == 1


def test_gross_m3_that_fits_88_can_still_overflow_13_vans():
    """The mistake this rule prevents: 35 + 52 = 87 m³ looks like one trailer,
    but it is 6 + 9 = 15 lift vans and needs two."""
    p = engine.plan([ship(1, 35), ship(2, 52)], today=TODAY)
    assert len(p["loads"]) == 2
    assert sorted(l["lift_vans"] for l in p["loads"]) == [6, 9]


def test_loose_freight_fills_the_positions_left():
    """Vans plus loose share one scale: 12 vans leave one position ≈ 6.8 m³."""
    loose_small = ship(3, 6, corp="Dow", service="LTL")
    loose_big = ship(4, 12, corp="Dow", service="LTL")
    p = engine.plan([ship(1, 35), ship(2, 35), loose_small, loose_big], today=TODAY)
    with_vans = next(l for l in p["loads"] if l["lift_vans"])
    ids = {s["id"] for s in with_vans["shipments"]}
    assert "TMS:3" in ids and "TMS:4" not in ids


def test_load_dict_and_email_speak_in_lift_vans():
    p = engine.plan([ship(1, 35)], today=TODAY)
    ld = p["loads"][0]
    assert ld["lift_vans"] == 6 and ld["lift_van_positions"] == 13
    assert ld["shipments"][0]["lift_vans"] == 6 and ld["shipments"][0]["us_diplomatic"]
    _, body = engine.email_body(p)
    assert "6 of 13 lift van positions" in body and "6 lift vans" in body


def test_ordinary_loads_are_unchanged():
    p = engine.plan([ship(1, 35, corp="Dow")], today=TODAY)
    ld = p["loads"][0]
    assert ld["lift_vans"] == 0 and ld["space_m3"] == 35 and ld["fill_pct"] == 40
