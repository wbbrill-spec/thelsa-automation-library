"""Offline tests for the TIM list-per-shipment reader (crossborder/tim.py)."""
import datetime as dt

from crossborder import tim
from crossborder.models import Stage

DA_STEPS = [
    "1.- Presentarse con el Cliente", "2.- Solicitar Documentos a Cliente y/o Usuario",
    "3.- Documentos Completos", "4.- Luz Verde Envío a Bodega", "5.- Confirmar Recepción en Bodega",
    "6.- Enviar Documentos al Agente Aduanal", "7.- Programar Importación y Cruce Fronterizo",
    "8.- Informar Cruce Exitoso", "9.- Descarga en Bodega Thelsa Monterrey",
    "10.- Programar Logistica de Entrega", "11.- Programar Entrega", "12.- Dia de Entrega",
    "13.- Cierre de Servicio",
]
DTD_EXTRA = ["2.- Programar Recolección", "4.- Dia Previo a Recolección", "5.- Dia de Recolección",
             "6.- Documentos de Origen", "8.- Programar Flete Bodega Agente-Bodega Frontera"]


def _ms(d: dt.date) -> str:
    return str(int(dt.datetime(d.year, d.month, d.day, 12, tzinfo=dt.timezone.utc).timestamp() * 1000))


def make_tasks(done_through: int, closed_on: dt.date = dt.date(2026, 7, 20), in_progress=None):
    """DA checklist with steps 1..done_through complete (ClickUp returns them unordered)."""
    tasks = []
    for i, name in enumerate(DA_STEPS, start=1):
        done = i <= done_through
        st = {"status": "complete", "type": "closed"} if done else (
            {"status": "in progress", "type": "custom"} if in_progress == i else {"status": "to do", "type": "open"})
        tasks.append({"id": f"s{i}", "name": name, "parent": None, "orderindex": str(1000 - i),
                      "status": st, "date_closed": _ms(closed_on) if done else None,
                      "date_updated": _ms(closed_on), "assignees": []})
        tasks.append({"id": f"s{i}a", "name": "sub", "parent": f"s{i}", "orderindex": "1",
                      "status": st, "date_closed": _ms(closed_on) if done else None,
                      "date_updated": _ms(closed_on), "assignees": [{"username": "Fernanda"}]})
    return list(reversed(tasks))


LST = {"id": "901114027604", "name": "Rosa Molina - UHaul - 121722"}


def test_parse_list_name_variants():
    assert tim.parse_list_name("Rosa Molina - UHaul - 121722") == {
        "customer": "Rosa Molina", "agent": "UHaul", "reference": "121722"}
    assert tim.parse_list_name("Carolina Angrizano - TIM-48007-26", "PARTICULARES") == {
        "customer": "Carolina Angrizano", "agent": "Particulares", "reference": "TIM-48007-26"}
    assert tim.parse_list_name("Louise Posnick - Logicstics - MX133987 / LOGIC-31845")["reference"] == "MX133987 / LOGIC-31845"
    assert tim.parse_list_name("Norm Carrillo", "RAINIER") == {"customer": "Norm Carrillo", "agent": "Rainier", "reference": ""}


def test_stage_from_current_step():
    s = tim.build_shipment(LST, make_tasks(4), folder="U-HAUL", space="Logistics Coordination",
                           team_id="9011168761", completed=False, today=dt.date(2026, 9, 8))
    assert s.customer_name == "Rosa Molina" and s.agent == "UHaul" and s.reference_number == "121722"
    assert s.process_format == "DA" and s.steps_done == 4 and s.steps_total == 13
    assert s.current_step.startswith("5.-") and s.stage is Stage.TO_BORDER
    assert s.milestones["green_light"] == dt.date(2026, 7, 20)
    assert s.ready_date == dt.date(2026, 7, 20)
    assert s.days_since_progress == 50 and "stalled" in s.status_flags
    assert s.assignees == ["Fernanda"]
    assert s.url.endswith("/v/li/901114027604")
    assert s.to_dict()["milestones"]["green_light"] == "2026-07-20"


def test_every_da_step_maps_to_a_stage():
    expected = [Stage.BOOKED, Stage.DOCS_PENDING, Stage.DOCS_PENDING, Stage.GREEN_LIGHT, Stage.TO_BORDER,
                Stage.CUSTOMS, Stage.CUSTOMS, Stage.CUSTOMS, Stage.AT_HUB, Stage.ONWARD,
                Stage.OUT_FOR_DELIVERY, Stage.OUT_FOR_DELIVERY, Stage.DELIVERED]
    assert [tim.stage_for_step(n) for n in DA_STEPS] == expected
    assert [tim.stage_for_step(n) for n in DTD_EXTRA] == [
        Stage.BOOKED, Stage.BOOKED, Stage.BOOKED, Stage.DOCS_PENDING, Stage.TO_BORDER]


def test_all_done_and_completed_space():
    s = tim.build_shipment(LST, make_tasks(13, closed_on=dt.date(2026, 9, 1)), folder="U-HAUL",
                           space="Logistics Coordination", team_id="t", completed=False, today=dt.date(2026, 9, 8))
    assert s.stage is Stage.DELIVERED and s.milestones["delivered"] == dt.date(2026, 9, 1) and s.status_flags == []
    c = tim.build_shipment(LST, make_tasks(13), folder=None, space="Completed 2026", team_id="t", completed=True)
    assert c.stage is Stage.CLOSED and not c.is_open


def test_fresh_progress_not_stalled_and_in_progress_flag():
    s = tim.build_shipment(LST, make_tasks(8, closed_on=dt.date(2026, 9, 6), in_progress=9), folder="U-HAUL",
                           space="LC", team_id="t", completed=False, today=dt.date(2026, 9, 8))
    assert s.stage is Stage.AT_HUB and s.status_flags == ["in_progress"]
    assert s.clearance_date == dt.date(2026, 9, 6)


def test_fetch_walks_folders_and_skips_placeholders():
    class Fake:
        requests_made = 0
        def teams(self): return [{"id": "9011168761"}]
        def spaces(self, tid): return [{"id": "sp1", "name": "Logistics Coordination"}, {"id": "sp2", "name": "Completed 2026"}]
        def folders(self, sid): return [{"id": "f1", "name": "U-HAUL", "lists": [
            {"id": "l0", "name": "List"}, {"id": "l1", "name": "Rosa Molina - UHaul - 121722"}]}] if sid == "sp1" else []
        def folderless_lists(self, sid): return [{"id": "l2", "name": "Norm Carrillo"}] if sid == "sp1" else [
            {"id": "l3", "name": "Alejandra Roa - UHaul - 122322"}]
        def list_tasks(self, lid, include_closed=False): return make_tasks(4) if lid != "l3" else make_tasks(13)
    ships, diag = tim.fetch_tim_shipments(Fake(), include_completed=True)
    assert sorted(s.source_ref for s in ships) == ["l1", "l2", "l3"]     # sorted by agent, customer
    by_ref = {s.source_ref: s for s in ships}
    assert by_ref["l3"].stage is Stage.CLOSED and diag["skipped_lists"] == ["List"]
    assert diag["folders"][0] == {"space": "Logistics Coordination", "folder": "U-HAUL", "shipments": 1}
    assert diag["count"] == 3 and diag["unmapped_steps"] == {}
