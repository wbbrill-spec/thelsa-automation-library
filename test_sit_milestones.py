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


def test_texas_border_towns_are_hubs_not_deliveries():
    assert sm.is_hub("BROWNSVILLE, TX") and sm.is_hub("PHARR TX") and sm.is_hub("Hidalgo, Texas")
    snaps = [(D(2026, 9, 30), [trip("110800", "BODEGA THELSA MEXICO", "BROWNSVILLE, TX",
                                    D(2026, 9, 10), D(2026, 9, 12), "INT C")])]
    j = sm.collect(snaps, TODAY)["110800"]
    assert j["pack"] is None and j["delivery"] is None


def test_moved_is_the_first_truck_day_even_between_hubs():
    snaps = [(D(2026, 9, 30), [
        trip("110800", "BODEGA THELSA MEXICO", "BODEGA THELSA VERACRUZ", D(2026, 9, 10), D(2026, 9, 11), "TRAN F COMP"),
        trip("110800", "BODEGA THELSA VERACRUZ", "PUERTO DE VERACRUZ", D(2026, 9, 14), D(2026, 9, 14), "INT C"),
        trip("110800", "X", "X", D(2026, 9, 1), None, "HUACAL"),          # a service, not a truck move
        trip("110800", "X", "X", D(2026, 9, 2), None, "DESEMPAQUE")])]
    j = sm.collect(snaps, TODAY)["110800"]
    assert j["moved"]["date"] == "2026-09-10" and j["moved"]["status"] == "done"


def test_one_workbook_per_month_the_last_one():
    named = [(D(2026, 8, 27), 1, "a27-1"), (D(2026, 8, 31), 2, "a31-2"), (D(2026, 8, 31), 3, "a31-3"),
             (D(2026, 9, 30), 1, "s30-1"), (D(2026, 9, 29), 3, "s29-3"),
             (D(2026, 10, 9), 3, "o9-3"), (D(2026, 10, 12), 2, "o12-2"),
             (D(2025, 6, 30), 3, "too old"), (D(2026, 10, 20), 1, "in the future")]
    got = sm.pick_workbooks(named, today=D(2026, 10, 13))
    assert got == [(D(2026, 8, 31), "a31-3"), (D(2026, 9, 30), "s30-1"), (D(2026, 10, 12), "o12-2")]


def test_a_closed_months_last_workbook_counts_as_published_at_month_end():
    # last August plan is dated the 28th; a row for the 30th still stands as done
    named = [(D(2026, 8, 28), 3, "aug")]
    (as_of, _item), = sm.pick_workbooks(named, today=D(2026, 10, 13))
    assert as_of == D(2026, 8, 31)
    jobs = sm.collect([(as_of, [trip("110700", "TOLUCA", "BODEGA THELSA", D(2026, 8, 30), D(2026, 8, 30))])], TODAY)
    assert jobs["110700"]["pack"]["status"] == "done"


def test_coverage_counts_rows_without_a_file_number():
    snaps = [(D(2026, 9, 30), [trip("110800", "A", "B", None, None), trip("", "A", "B", None, None),
                               trip("", "A", "B", None, None), trip("110801", "A", "B", None, None, src="TIM")])]
    assert sm.coverage(snaps) == {"tms_rows": 3, "tms_rows_with_file_number": 1,
                                  "tms_rows_without_file_number": 2, "customers_without_file_number": 1}
