"""Offline tests for the consolidation engine (crossborder/engine.py)."""
import datetime as dt

from crossborder import engine
from crossborder.models import Hub, Leg, Shipment, Source, Stage, TRUCK_53_M3

CROSSING = "McAllen → Monterrey (crossing)"

TODAY = dt.date(2026, 9, 9)


def tim(i, customer, hub, stage=Stage.TO_BORDER, lv=None, ub=None, m3=None, gl=dt.date(2026, 8, 25)):
    # Green light is on record once the file is past documents — including after
    # it has crossed, which is what gives an at-hub shipment its delivery window.
    ms = {"green_light": gl} if stage in engine.READY_STAGES | engine.AT_HUB_STAGES else {}
    return Shipment(id=f"TIM:{i}", source=Source.TIM, source_ref=str(i), customer_name=customer, agent="UHaul",
                    destination=hub.value, destination_hub=hub, stage=stage, lift_vans=lv, u_boxes=ub, volume_m3=m3,
                    milestones=ms, current_step="3.- Documentos Completos")


def tms(i, customer, dest, hub, m3, *, direction="import", method="ROAD", service="LTL", uplift=dt.date(2026, 9, 5),
        delivery=None, kg=None, stage=Stage.BOOKED):
    return Shipment(id=f"TMS:{i}", source=Source.TMS, source_ref=str(i), customer_name=customer, agent="Allied",
                    destination=dest, destination_hub=hub, stage=stage, volume_m3=m3, weight=kg, ready_date=uplift,
                    delivery_date=delivery, extra={"direction": direction, "method": method, "service": service})


def test_eligibility_rules():
    assert engine.make_item(tms(1, "Sea job", "Merida, Yucatan", Hub.MERIDA, 32, method="SEA", service="FCL 20"), TODAY) is None
    assert engine.make_item(tim(2, "Delivered", Hub.MONTERREY, stage=Stage.OUT_FOR_DELIVERY, lv=1), TODAY) is None
    it = engine.make_item(tim(3, "Ready", Hub.MONTERREY, lv=2), TODAY)
    assert it.ready and it.m3 == 11.4 and it.deadline == dt.date(2026, 9, 24)
    nr = engine.make_item(tim(4, "Docs", Hub.MONTERREY, stage=Stage.DOCS_PENDING, ub=1), TODAY)
    assert not nr.ready and nr.m3 == 7.3 and "documents not complete" in nr.reasons[0]
    ftl = engine.make_item(tms(5, "Big", "Zapopan, Jalisco", Hub.GUADALAJARA, 52, service="FTL"), TODAY)
    assert ftl.anchor and ftl.ready
    late = engine.make_item(tms(6, "Later", "CDMX", Hub.MEXICO_CITY, 4, uplift=dt.date(2026, 10, 15)), TODAY)
    assert not late.ready and "beyond" in late.reasons[0]


def test_lanes_are_two_stage_for_imports_and_regions_for_exports():
    """An import is planned twice: the crossing, then the truck out of the hub."""
    # Before the border, every import shares one trailer whatever city it is for.
    assert engine.lane_for(tim(1, "a", Hub.QUERETARO, lv=1)) == (CROSSING, "Monterrey", Leg.CROSSING)
    assert engine.lane_for(tim(2, "b", Hub.GUADALAJARA, lv=1))[0] == CROSSING
    # After it, the corridor out of the hub — and Querétaro is a drop on the
    # Mexico City run, not a destination of its own (Fernanda, 22 Sep).
    # Both are drops on the Mexico City run, so they land in the SAME lane as
    # Mexico City freight — that is the whole point of a detour stop.
    assert engine.lane_for(tim(3, "c", Hub.QUERETARO, stage=Stage.AT_HUB, lv=1)) == (
        "Monterrey → Mexico City", "Mexico City", Leg.ONWARD)
    assert engine.lane_for(tim(4, "d", Hub.SAN_LUIS_POTOSI, stage=Stage.AT_HUB, lv=1))[0] == (
        "Monterrey → Mexico City")
    assert engine.lane_for(tim(5, "e", Hub.GUADALAJARA, stage=Stage.AT_HUB, lv=1))[0] == (
        "Monterrey → Guadalajara")
    # Exports keep their state/region lane.
    assert engine.lane_for(tms(6, "f", "Ohio", Hub.UNKNOWN, 4, direction="export"))[:2] == ("Export → Ohio", "Ohio")
    assert engine.lane_for(tms(7, "g", "Brownsville, Texas", Hub.UNKNOWN, 4, direction="export"))[1] == "Texas"


def test_freight_that_only_needs_local_delivery_is_not_planned_as_a_line_haul():
    """Once Monterrey freight is in the Monterrey hub there is no truck to share."""
    s = tim(1, "Mty", Hub.MONTERREY, stage=Stage.AT_HUB, lv=1)
    assert "delivers locally" in engine.screen(s)
    assert engine.make_item(s, TODAY) is None


def test_onward_leg_packs_anchor_then_fills_and_flags_light_trailers():
    """Stage 2: freight in the hub, packed onto the truck to its city."""
    ships = [
        tms(1, "FTL Guad", "Zapopan, Jalisco", Hub.GUADALAJARA, 52, service="FTL",
            delivery=dt.date(2026, 9, 20), stage=Stage.AT_HUB),
        tim(2, "Small A", Hub.GUADALAJARA, stage=Stage.AT_HUB, lv=2),      # 11.4 → fits the FTL trailer
        tim(3, "Small B", Hub.GUADALAJARA, stage=Stage.AT_HUB, ub=1),      # 8.8 of trailer → fits
        tim(4, "Coming C", Hub.GUADALAJARA, stage=Stage.DOCS_PENDING, lv=3),   # has not crossed yet
        tms(9, "Sea", "Merida, Yucatan", Hub.MERIDA, 30, method="SEA", service="FCL 20"),
    ]
    p = engine.plan(ships, today=TODAY)
    by_lane = {}
    for ld in p["loads"]:
        by_lane.setdefault(ld["lane"], []).append(ld)
    g = by_lane["Monterrey → Guadalajara"]
    # 52 + 11.4 m³, plus one U-Box at 8.8 m³ of trailer (1 of 10 positions — Bill,
    # 2026-09-21) rather than its 7.3 m³ usable volume: 72.2 of 88 → 82%.
    assert len(g) == 1 and g[0]["anchor"] == "TMS:1" and g[0]["m3"] == 70.7 and g[0]["fill_pct"] == 82
    assert g[0]["u_boxes"] == 1 and g[0]["cross_silo"] and g[0]["depart_by"] == "2026-09-20"
    assert g[0]["leg"] == "onward"
    # "Coming C" has not crossed and its documents are not done, so it cannot
    # join the onward truck. It waits on the crossing lane, where it belongs —
    # offering it here would be a promise we could not keep.
    assert [i["id"] for i in p["coming_by_lane"][CROSSING]] == ["TIM:4"]
    assert p["summary"]["onward_trailers"] == 1 and p["summary"]["crossing_trailers"] == 0
    assert [lg["leg"] for lg in p["legs"]] == ["onward"]


def test_crossing_leg_puts_every_destination_on_one_trailer():
    """Stage 1: it all crosses together, whatever city it is bound for."""
    ships = [
        tim(5, "Mty 1", Hub.MONTERREY, lv=1, gl=dt.date(2026, 8, 12)),  # deadline 9/11 → window risk
        tms(6, "Gdl 2", "Zapopan, Jalisco", Hub.GUADALAJARA, 60),
        tms(7, "Cdmx 3", "Ciudad de Mexico", Hub.MEXICO_CITY, 40),
        tms(8, "Export TX", "Brownsville, Texas", Hub.UNKNOWN, 20, direction="export"),
    ]
    p = engine.plan(ships, today=TODAY)
    by_lane = {}
    for ld in p["loads"]:
        by_lane.setdefault(ld["lane"], []).append(ld)
    # Three different final cities, one crossing: 60 + 40 does not fit on one
    # trailer, so two cross, and the small Monterrey file tops up the fullest.
    m = sorted(by_lane[CROSSING], key=lambda l: -l["m3"])
    assert [l["m3"] for l in m] == [65.7, 40.0]
    assert "TIM:5" in m[0]["window_risk"] and m[1]["light"]
    assert all(l["leg"] == "crossing" for l in m)
    # Each member still says where it goes after the hub.
    assert {s["hub"] for s in m[0]["shipments"]} == {"Monterrey", "Guadalajara"}
    tx = by_lane["Export → Texas"][0]
    assert tx["m3"] == 20.0 and tx["light"] and tx["ships_alone"]
    opp = {o["lane"]: o for o in p["opportunities"]}
    assert opp[CROSSING]["spare_m3"] == round(TRUCK_53_M3 - 40.0, 2)
    subject, body = engine.email_body(p)
    assert "3 trailers" in subject and "TRAILER 1" in body and "WINDOW RISK" in body
    assert "Stage 1 — border crossing" in body


def test_real_world_fixes_shared_ftl_regions_cuft_unsized():
    ships = [
        tms(1, "FTL A", "Brownsville, Texas", Hub.UNKNOWN, 35, direction="export", service="FTL"),
        tms(2, "FTL B", "TEXAS", Hub.UNKNOWN, 35, direction="export", service="FTL"),
        tms(3, "FTL C", "United States Charlotte North Carolina", Hub.UNKNOWN, 10, direction="export", service="FTL"),
        tms(4, "Cuft typo", "Nashville, TN", Hub.UNKNOWN, 2009, direction="export"),
        tim(5, "No volume", Hub.UNKNOWN),                                  # 0 m³ → unsized
    ]
    assert engine.export_region(ships[1]) == "Texas" and engine.export_region(ships[2]) == "North Carolina"
    assert engine.export_region(ships[3]) == "Tennessee"
    p = engine.plan(ships, today=TODAY)
    # Exports do not wait (Fernanda, 22 Sep): each leaves on its own, and the
    # pairing that would have filled one trailer is OFFERED rather than assumed.
    tx = [l for l in p["loads"] if l["lane"] == "Export → Texas"]
    assert len(tx) == 2 and all(l["m3"] == 35.0 and l["ships_alone"] for l in tx)
    assert tx[0]["optional_pairings"][0]["combined_fill_pct"] == 80
    assert "could share with" in tx[0]["pairing_advice"]
    tn = [l for l in p["loads"] if l["lane"] == "Export → Tennessee"][0]
    assert tn["m3"] == 56.89 and "cubic feet" in tn["shipments"][0]["reasons"][0]
    assert p["unsized"] == 1 and CROSSING in p["unsized_by_lane"]
    assert all(l["m3"] > 0 for l in p["loads"]) and p["summary"]["avg_fill_pct"] < 100
    _, body = engine.email_body(p)
    assert "NOT PLANNABLE" in body and "No volume" in body
