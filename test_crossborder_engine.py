"""Offline tests for the consolidation engine (crossborder/engine.py)."""
import datetime as dt

from crossborder import engine
from crossborder.models import Hub, Shipment, Source, Stage, TRUCK_53_M3

TODAY = dt.date(2026, 9, 9)


def tim(i, customer, hub, stage=Stage.TO_BORDER, lv=None, ub=None, m3=None, gl=dt.date(2026, 8, 25)):
    return Shipment(id=f"TIM:{i}", source=Source.TIM, source_ref=str(i), customer_name=customer, agent="UHaul",
                    destination=hub.value, destination_hub=hub, stage=stage, lift_vans=lv, u_boxes=ub, volume_m3=m3,
                    milestones={"green_light": gl} if stage in engine.READY_STAGES else {}, current_step="3.- Documentos Completos")


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


def test_lanes_imports_by_hub_exports_by_region():
    assert engine.lane_for(tim(1, "a", Hub.QUERETARO, lv=1)) == ("Import → Querétaro", "Querétaro")
    assert engine.lane_for(tms(2, "b", "Ohio", Hub.UNKNOWN, 4, direction="export")) == ("Export → Ohio", "Ohio")
    assert engine.lane_for(tms(3, "c", "Brownsville, Texas", Hub.UNKNOWN, 4, direction="export"))[1] == "Texas"


def test_plan_packs_anchor_then_fills_and_flags_light_trailers():
    ships = [
        tms(1, "FTL Guad", "Zapopan, Jalisco", Hub.GUADALAJARA, 52, service="FTL", delivery=dt.date(2026, 9, 20)),
        tim(2, "Small A", Hub.GUADALAJARA, lv=2),                       # 11.4 → fits the FTL trailer
        tim(3, "Small B", Hub.GUADALAJARA, ub=1),                       # 7.3  → fits
        tim(4, "Coming C", Hub.GUADALAJARA, stage=Stage.DOCS_PENDING, lv=3),   # 17.1 not ready
        tim(5, "Mty 1", Hub.MONTERREY, lv=1, gl=dt.date(2026, 8, 12)),  # deadline 9/11 → window risk
        tms(6, "Mty 2", "Monterrey, NL", Hub.MONTERREY, 60),            # anchor by size
        tms(7, "Mty 3", "Monterrey, NL", Hub.MONTERREY, 40),            # can't join 60 (100 > 88) → own trailer
        tms(8, "Export TX", "Brownsville, Texas", Hub.UNKNOWN, 20, direction="export"),
        tms(9, "Sea", "Merida, Yucatan", Hub.MERIDA, 30, method="SEA", service="FCL 20"),
    ]
    p = engine.plan(ships, today=TODAY)
    assert p["eligible"] == 8 and p["ready"] == 7 and p["coming"] == 1 and p["excluded"] == 1
    by_lane = {}
    for ld in p["loads"]:
        by_lane.setdefault(ld["lane"], []).append(ld)
    g = by_lane["Import → Guadalajara"]
    assert len(g) == 1 and g[0]["anchor"] == "TMS:1" and g[0]["m3"] == 70.7 and g[0]["fill_pct"] == 80
    assert g[0]["cross_silo"] and g[0]["depart_by"] == "2026-09-20"
    m = sorted(by_lane["Import → Monterrey"], key=lambda l: -l["m3"])
    assert [l["m3"] for l in m] == [65.7, 40.0]          # best-fit: the small one tops up the fullest trailer
    assert "TIM:5" in m[0]["window_risk"] and m[1]["light"]
    assert by_lane["Export → Texas"][0]["m3"] == 20.0 and by_lane["Export → Texas"][0]["light"]
    opp = {o["lane"]: o for o in p["opportunities"]}
    assert "Import → Guadalajara" not in opp                 # 80% is not light
    assert opp["Import → Monterrey"]["spare_m3"] == round(TRUCK_53_M3 - 40.0, 2)
    assert p["summary"]["trailers"] == 4 and p["summary"]["cross_silo"] == 2 and p["summary"]["window_risk"] == 1
    subject, body = engine.email_body(p)
    assert "4 trailers" in subject and "TRAILER 1" in body and "WINDOW RISK" in body and "OPPORTUNITIES" in body
    assert "Coming C" in body


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
    tx = [l for l in p["loads"] if l["lane"] == "Export → Texas"]
    assert len(tx) == 1 and tx[0]["m3"] == 70.0 and tx[0]["anchors"] == 2
    tn = [l for l in p["loads"] if l["lane"] == "Export → Tennessee"][0]
    assert tn["m3"] == 56.89 and "cubic feet" in tn["shipments"][0]["reasons"][0]
    assert p["unsized"] == 1 and "Import → Unassigned hub" in p["unsized_by_lane"]
    assert all(l["m3"] > 0 for l in p["loads"]) and p["summary"]["avg_fill_pct"] < 100
    _, body = engine.email_body(p)
    assert "NOT PLANNABLE" in body and "No volume" in body
