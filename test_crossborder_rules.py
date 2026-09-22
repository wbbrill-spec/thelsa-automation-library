"""The operational rules from the 21 Sep training and the 22 Sep meeting.

One test per decision, named so a failure says which business rule broke.
"""
import datetime as dt

import pytest

from crossborder import engine, grouping, rules
from crossborder.models import Hub, Leg, Shipment, Source, Stage

TODAY = dt.date(2026, 9, 22)
CROSSING = "McAllen → Monterrey (crossing)"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("CB_COMMERCIAL_PATTERNS", "CB_COMMERCIAL_PATTERNS_EXTRA", "CB_CONSOLIDATE_DTD",
              "CROSSBORDER_SHOW_DIPLOMATIC", "CROSSBORDER_SHOW_ALL", "CB_NO_DETOURS",
              "CB_HOLD_EXPORTS", "CB_THIRD_PARTY_CARRIERS", "CB_THELSA_TRUCK_U_BOXES"):
        monkeypatch.delenv(k, raising=False)


def ship(i, customer="Cliente", *, source=Source.TIM, hub=Hub.MEXICO_CITY, m3=10.0,
         stage=Stage.TO_BORDER, agent="UHaul", fmt="DA", corp="", extra=None,
         direction="import", ref="", note=None):
    s = Shipment(id=f"{source.value}:{i}", source=source, source_ref=str(i),
                 customer_name=customer, agent=agent, reference_number=ref,
                 destination=hub.value, destination_hub=hub, stage=stage, volume_m3=m3,
                 corporate_account=corp, process_format=fmt,
                 milestones={"green_light": dt.date(2026, 9, 1)},
                 extra={"direction": direction, "method": "ROAD", "service": "LTL", **(extra or {})})
    if note:
        grouping.apply_note(s, [note])
    return s


# ── D2: commercial / new furniture off the board ────────────────────────────
def test_commercial_freight_is_kept_off_the_board():
    """Gustavo's commercial sales and Rafael Larsa's new furniture are a
    separate import with a different broker, and are never consolidated."""
    assert rules.is_commercial(ship(1, agent="COMERCIAL"))
    assert rules.is_commercial(ship(2, extra={"folder": "Muebles Nuevos"}))
    assert rules.is_commercial(ship(3, corp="Larsa"))
    assert not rules.is_commercial(ship(4, agent="U-HAUL"))
    kept, report = rules.board_exclusions([ship(1, agent="COMERCIAL"), ship(4, agent="U-HAUL")])
    assert [s.id for s in kept] == ["TIM:4"] and report["by_rule"]["commercial"] == 1


def test_uso_can_name_the_commercial_lists_without_a_code_change(monkeypatch):
    monkeypatch.setenv("CB_COMMERCIAL_PATTERNS_EXTRA", "Proyectos Especiales")
    assert rules.is_commercial(ship(1, extra={"space": "Proyectos Especiales 2026"}))
    monkeypatch.setenv("CB_COMMERCIAL_PATTERNS", "-")
    assert not rules.is_commercial(ship(1, agent="COMERCIAL"))


def test_show_all_reports_without_removing(monkeypatch):
    monkeypatch.setenv("CROSSBORDER_SHOW_ALL", "1")
    kept, report = rules.board_exclusions([ship(1, agent="COMERCIAL")])
    assert len(kept) == 1 and report["removed"] == 0 and report["by_rule"]["commercial"] == 1


# ── D3: door-to-door moves ship direct ──────────────────────────────────────
def test_door_to_door_moves_are_never_offered_for_consolidation(monkeypatch):
    dtd = ship(1, fmt="DTD")
    assert dtd.is_door_to_door
    assert "door-to-door" in rules.consolidation_block(dtd)
    assert engine.make_item(dtd, TODAY) is None
    # …but they stay on the board: this is not a board exclusion.
    kept, _ = rules.board_exclusions([dtd])
    assert len(kept) == 1
    monkeypatch.setenv("CB_CONSOLIDATE_DTD", "1")
    assert engine.make_item(ship(1, fmt="DTD"), TODAY) is not None


def test_a_source_that_spells_door_to_door_out_is_recognised():
    assert ship(1, fmt="", extra={"service": "Door to Door Import"}).is_door_to_door
    assert ship(2, fmt="", extra={"service": "Puerta a puerta"}).is_door_to_door
    assert not ship(3, fmt="DA").is_door_to_door


# ── D6: Fernanda's consolidation note ───────────────────────────────────────
@pytest.mark.parametrize("note,grouped,blocked", [
    ("consolidado con Ana Ruiz", True, False),
    ("[CONSOLIDADO] grupo: MTY-CDMX-24SEP | con: Ana Ruiz", True, False),
    ("va junto con la carga de Ruiz", True, False),
    ("urgente, no consolidar", False, True),
    ("sale solo el jueves", False, True),
    ("se va con tercero", False, True),
    ("consolidado pero urgente, no consolidar", False, True),   # safest reading wins
    ("cliente confirmó la fecha de entrega", None, None),       # not about consolidation
])
def test_notes_are_read_the_way_a_coordinator_writes_them(note, grouped, blocked):
    parsed = grouping.parse_note(note)
    if grouped is None:
        assert parsed is None
        return
    assert parsed["grouped"] is grouped and parsed["do_not_consolidate"] is blocked


def test_the_note_names_the_group_and_the_other_customers():
    p = grouping.parse_note("[CONSOLIDADO] grupo: MTY-CDMX-24SEP | con: Ana Ruiz, Pedro Lara | tercero: Gran Casa")
    assert p["group"] == "MTY-CDMX-24SEP"
    assert p["with"] == ["Ana Ruiz", "Pedro Lara"]
    assert p["third_party"] == "Gran Casa"


def test_a_named_carrier_alone_is_enough():
    p = grouping.parse_note("se va con Gran Casa el viernes")
    assert p["third_party"] == "Gran Casa" and p["do_not_consolidate"]


def test_grouped_files_leave_the_suggestions_and_become_a_truck_to_join():
    joined = "consolidado con Beto"
    a = ship(1, "Ana", hub=Hub.GUADALAJARA, m3=20, stage=Stage.AT_HUB, note=joined)
    b = ship(2, "Beto", hub=Hub.GUADALAJARA, m3=15, stage=Stage.AT_HUB,
             note="consolidado con Ana")
    spare = ship(3, "Caro", hub=Hub.GUADALAJARA, m3=12, stage=Stage.AT_HUB)
    p = engine.plan([a, b, spare], today=TODAY)
    # The two grouped files are not in the suggestions any more…
    planned = {s["id"] for l in p["loads"] for s in l["shipments"]}
    assert planned == {"TIM:3"}
    # …they are one truck, with the third file offered as something to add.
    assert len(p["groups"]) == 1
    g = p["groups"][0]
    assert g["customers"] == 2 and g["m3"] == 35.0
    assert [c["customer"] for c in g["could_join"]] == ["Caro"]
    assert "Caro" in g["advice"]
    assert "already consolidated" in engine.screen(a)


def test_a_file_marked_urgent_is_left_alone_with_the_reason_said_out_loud():
    s = ship(1, "Prisa", note="urgente - sale directo")
    why = engine.screen(s)
    assert "coordinator's note" in why and "ships on its own" in why


# ── #6: detour stops ────────────────────────────────────────────────────────
def test_san_luis_potosi_and_queretaro_ride_the_mexico_city_truck(monkeypatch):
    slp = ship(1, hub=Hub.SAN_LUIS_POTOSI, stage=Stage.AT_HUB, m3=10)
    qro = ship(2, hub=Hub.QUERETARO, stage=Stage.AT_HUB, m3=10)
    cdmx = ship(3, hub=Hub.MEXICO_CITY, stage=Stage.AT_HUB, m3=10)
    p = engine.plan([slp, qro, cdmx], today=TODAY)
    # One truck, not three: both stops are on the way to Mexico City.
    assert len(p["loads"]) == 1
    ld = p["loads"][0]
    assert ld["hub"] == "Mexico City" and ld["m3"] == 30.0
    assert sorted(ld["detour_stops"]) == ["Querétaro", "San Luis Potosí"]
    # and the board can still say where each one gets off
    assert {s["detour_stop"] for s in ld["shipments"]} == {"Querétaro", "San Luis Potosí", ""}
    monkeypatch.setenv("CB_NO_DETOURS", "1")
    assert len(engine.plan([slp, qro, cdmx], today=TODAY)["loads"]) == 3


def test_san_luis_potosi_is_no_longer_read_as_monterrey_freight():
    from crossborder.models import hub_for_destination
    assert hub_for_destination("San Luis Potosí, S.L.P.") is Hub.SAN_LUIS_POTOSI


# ── #8: exports do not wait ─────────────────────────────────────────────────
def test_each_export_leaves_on_its_own_with_the_pairing_offered(monkeypatch):
    a = ship(1, "Exp A", source=Source.TMS, hub=Hub.UNKNOWN, m3=20, direction="export",
             extra={"direction": "export"})
    a.destination = "Brownsville, Texas"
    b = ship(2, "Exp B", source=Source.TMS, hub=Hub.UNKNOWN, m3=20, direction="export",
             extra={"direction": "export"})
    b.destination = "Houston, Texas"
    a.ready_date = b.ready_date = dt.date(2026, 9, 20)
    p = engine.plan([a, b], today=TODAY)
    assert len(p["loads"]) == 2 and all(l["ships_alone"] for l in p["loads"])
    assert p["loads"][0]["optional_pairings"][0]["combined_fill_pct"] == 45
    monkeypatch.setenv("CB_HOLD_EXPORTS", "1")
    assert len(engine.plan([a, b], today=TODAY)["loads"]) == 1


# ── #9: a Thelsa truck is not a 53 ft trailer ───────────────────────────────
def test_a_thelsa_truck_is_not_offered_for_more_u_boxes_than_it_holds():
    """Fernanda: about three U-Boxes on a Thelsa truck, ten on a hired 53 ft."""
    small = ship(1, "Tres", ref="AA100", m3=21.9, stage=Stage.AT_HUB)      # 3 boxes
    big = ship(2, "Cinco", ref="AA200", m3=36.5, stage=Stage.AT_HUB)       # 5 boxes
    p_small = engine.plan([small], today=TODAY)
    p_big = engine.plan([big], today=TODAY)
    assert p_small["loads"][0]["u_boxes"] == 3 and p_small["loads"][0]["thelsa_truck_ok"]
    assert p_big["loads"][0]["u_boxes"] == 5 and not p_big["loads"][0]["thelsa_truck_ok"]
    assert any("hired 53 ft trailer" in r for r in p_big["loads"][0]["shipments"][0]["reasons"])


def test_truck_offers_refuse_a_thelsa_truck_that_cannot_take_the_boxes():
    from crossborder import sit
    trucks = [{"unit": "T1", "date": "2026-09-24", "spare_m3": 80.0, "fill_pct": 5,
               "driver": "Luis", "lanes": ["MTY → Guadalajara"]}]
    load = {"hub": "Guadalajara", "m3": 36.5, "u_boxes": 5, "thelsa_truck_u_boxes": 3,
            "depart_by": "2026-09-26"}
    out = sit.offer_trucks([load], trucks, today=TODAY)[0]
    assert out["needs_hired_trailer"] and out["trucks"][0]["fits"] is False
    assert "hired 53 ft trailer" in out["trucks"][0]["why_not"]
    ok = sit.offer_trucks([{**load, "u_boxes": 2, "m3": 14.6}], trucks, today=TODAY)[0]
    assert ok["trucks"][0]["fits"] and not ok.get("needs_hired_trailer")


# ── #7: which Plan de Viajes services count ─────────────────────────────────
@pytest.mark.parametrize("tipo,counts,klass", [
    ("FOR", True, "foraneo"), ("CARGA FOR", True, "foraneo"),
    ("RADIAL", True, "radial"), ("RADIALES", True, "radial"),
    ("TRAN F COMP", True, "transfer"),
    ("LOC", False, "local"), ("LOCAL", False, "local"),
    ("DESEMPAQUE", False, "other"),
])
def test_foraneo_and_radiales_count_but_local_does_not(tipo, counts, klass):
    from crossborder.sit import Trip
    t = Trip(row=4, tipo=tipo)
    assert t.is_line_haul is counts and t.service_class == klass
