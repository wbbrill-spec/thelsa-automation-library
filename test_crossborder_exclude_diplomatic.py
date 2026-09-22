"""US Embassy / Consulate shipments are removed from the board (Bill, 2026-09-22)."""
from crossborder.models import Shipment, Source
from crossborder.web import exclude_us_diplomatic


def ship(i, corp="", bill_to=""):
    return Shipment(id=f"TMS:{i}", source=Source.TMS, source_ref=str(i),
                    corporate_account=corp, extra={"bill_to": bill_to})


def test_embassy_and_consulate_are_removed_everything_else_kept():
    ships = [ship(1, "EMBAJADA DE LOS ESTADOS UNIDOS DE AMERICA"),
             ship(2, "U.S. Consulate General Matamoros"),
             ship(3, "CORPORATIVO", bill_to="EMBAJADA DE LOS ESTADOS UNIDOS DE AMERICA"),
             ship(4, "Dow"), ship(5, "Embajada de Canadá"), ship(6)]
    kept, removed = exclude_us_diplomatic(ships)
    assert removed == 3
    assert [s.id for s in kept] == ["TMS:4", "TMS:5", "TMS:6"]


def test_switch_brings_them_back(monkeypatch):
    monkeypatch.setenv("CROSSBORDER_SHOW_DIPLOMATIC", "1")
    kept, removed = exclude_us_diplomatic([ship(1, "U.S. Consulate General Matamoros")])
    assert removed == 0 and len(kept) == 1
