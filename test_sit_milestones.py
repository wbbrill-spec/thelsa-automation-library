"""Pack / delivery dates for TMS files from the SIT Plan de Viajes history."""
import datetime as dt
from types import SimpleNamespace as NS

import sit_milestones as sm

TODAY = dt.date(2026, 10, 11)
D = dt.date


def trip(job, origin, dest, load, unload, tipo="FOR", src="TMS"):
    return NS(source=NS(value=src), job_no=job, customer="C", coordinator="X", origin=origin,
              destination=dest, tipo=tipo, load_date=load, unload_date=unload, unit="A1")


def test_workbook_date():
    assert sm.workbook_date("PLAN DE VIAJES 09 DE OCTUBRE 2026 (2).xlsm") == (D(2026, 10, 9), 2)
    assert sm.workbook_date("PLAN DE VIAJES 27 DE AGOSTO 2026 (1).xlsm") == (D(2026, 8, 27), 1)
    assert sm.workbook_date("otro.xlsx") == (None, 0)


def test_hub_detection():
    assert sm.is_hub("BODEGA THELSA MONTERREY") and sm.is_hub("PUERTO DE MANZANILLO")
    assert not sm.is_hub("SAN PEDRO GARZA GARCIA, NL")


def test_export_pack_and_import_delivery():
    snaps = [
        (D(2026, 9, 28), [trip("110900", "SAN PEDRO, NL", "PUERTO DE VERACRUZ", D(2026, 10, 1), D(2026, 10, 3)),
                          trip("110901", "BODEGA THELSA CDMX", "COYOACAN, CDMX", D(2026, 10, 2), D(2026, 10, 2))]),
        (D(2026, 10, 2), [trip("110900", "SAN PEDRO, NL", "PUERTO DE VERACRUZ", D(2026, 10, 1), D(2026, 10, 3)),
                          trip("110901", "BODEGA THELSA CDMX", "COYOACAN, CDMX", D(2026, 10, 2), D(2026, 10, 2))]),
    ]
    jobs = sm.collect(snaps, TODAY)
    assert jobs["110900"]["pack"]["date"] == "2026-10-01" and jobs["110900"]["pack"]["status"] == "done"
    assert jobs["110900"]["delivery"] is None          # unloads at the port: not a delivery
    assert jobs["110901"]["pack"] is None               # loads at a warehouse: not a pack
    assert jobs["110901"]["delivery"] == {"date": "2026-10-02", "status": "done", "last_seen": "2026-10-02",
                                          "from": "BODEGA THELSA CDMX", "to": "COYOACAN, CDMX", "tipo": "FOR"}


def test_statuses_and_reschedule():
    snaps = [
        (D(2026, 10, 1), [trip("110950", "TOLUCA", "BODEGA THELSA", D(2026, 10, 5), D(2026, 10, 6)),
                          trip("110951", "PUEBLA", "AICM", D(2026, 10, 20), D(2026, 10, 21))]),
        (D(2026, 10, 3), [trip("110951", "PUEBLA", "AICM", D(2026, 10, 22), D(2026, 10, 23))]),
    ]
    jobs = sm.collect(snaps, TODAY)
    assert jobs["110950"]["pack"]["status"] == "unconfirmed"   # dropped out before its day
    assert jobs["110951"]["pack"] == {"date": "2026-10-22", "status": "scheduled", "last_seen": "2026-10-03",
                                      "from": "PUEBLA", "to": "AICM", "tipo": "FOR"}


def test_ignores_tim_and_rows_without_file_number():
    snaps = [(D(2026, 10, 1), [trip("", "A", "B", D(2026, 10, 1), D(2026, 10, 1)),
                               trip("110960", "A", "B", D(2026, 10, 1), D(2026, 10, 1), src="TIM")])]
    assert sm.collect(snaps, TODAY) == {}


def test_compare_against_moveware():
    snaps = [(D(2026, 10, 2), [trip("110900A", "SAN PEDRO, NL", "PUERTO DE VERACRUZ", D(2026, 10, 1), D(2026, 10, 3))])]
    jobs = sm.collect(snaps, TODAY)
    out = sm.compare(jobs, [{"job": "110900", "pack": None, "delivery": None, "status": "W"}], TODAY)
    assert out["pack_missing"] == 1 and out["rows"][0]["in_moveware"]
    assert out["rows"][0]["gaps"] == ["pack missing in MoveWare"]
    out = sm.compare(jobs, [{"job": "110900A", "pack": D(2026, 9, 30), "delivery": None}], TODAY)
    assert out["differs"] == 1


def test_unpack_service_is_delivery_not_pack():
    snaps = [(D(2026, 8, 13), [trip("110739", "PUERTO VERACRUZ", "BODEGA THELSA GDL", None, D(2026, 8, 8), "INT D"),
                               trip("110739", "BODEGA THELSA GDL", "ZAPOPAN, JAL", D(2026, 8, 10), D(2026, 8, 12)),
                               trip("110739", "ZAPOPAN, JAL", "ZAPOPAN, JAL", D(2026, 8, 13), None, "DESEMPAQUE")])]
    j = sm.collect(snaps, TODAY)["110739"]
    assert j["pack"] is None and j["delivery"]["date"] == "2026-08-13"
