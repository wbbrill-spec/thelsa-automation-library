"""Bill, 5 Nov 2026: a billed file with no cost in Finance's books, where Moveware
already holds the supplier's actual cost, is pointed out. The commission itself
keeps using Finance's cost: the margin part still waits."""
import pytest

from commissions import finance as F
from commissions import report as R
from commissions import web as W
from test_commissions_finance import D, book, row
from test_commissions_web import client, login, upload, xlsx  # noqa: F401


def mw(act=0.0, est=1000.0, ccy="MXN", lines=3, with_act=2, pending=1):
    return {"act": act, "est": est, "currency": ccy, "lines": lines, "lines_with_act": with_act, "lines_pending": pending}


def rep(tmp_path, costs):
    lines = F.load_base(book(tmp_path / "r.xlsx", [row("110001", 1000, date=D(2026, 7, 4)),
                                                   row("110002A", 2000, date=D(2026, 7, 5), folio=2),
                                                   row("110003", 3000, date=D(2026, 7, 6), folio=3)]))
    return R.build(lines, W.settings(), mw_costs=costs, mw_costs_read_on="2026-11-05 08:00")


def test_a_file_with_supplier_cost_in_moveware_is_pointed_out_and_pay_is_unchanged(tmp_path):
    plain = rep(tmp_path, None)
    got = rep(tmp_path, {"110001": mw(act=640.5), "110002": mw(act=99.0, ccy="USD", pending=0, with_act=3),
                         "110003": mw(act=0.0)})                       # 110003: quoted cost only, nothing to say
    c = {x["kind"]: x for x in got["checks"]}["cost_in_moveware"]
    by = {i["job"]: i for i in c["items"]}
    assert set(by) == {"110001", "110002A"}                          # a lot is found through its base file
    assert by["110001"]["amount"] == 640.5 and "1 line(s) still waiting" in by["110001"]["detail"]
    assert by["110002A"]["amount"] is None and "99.00 USD" in by["110002A"]["detail"]
    assert "2026-11-05 08:00" in by["110001"]["detail"] and "Finanzas" in by["110001"]["detail_es"]
    assert not c["routine"]
    assert got["total_all"]["total"] == pytest.approx(plain["total_all"]["total"])
    assert got["total_new"]["waiting_lines"] == plain["total_new"]["waiting_lines"] > 0
    assert got["mw_costs_loaded"] and not plain["mw_costs_loaded"]


def test_no_moveware_read_no_pointer(tmp_path):
    assert "cost_in_moveware" not in {x["kind"] for x in rep(tmp_path, {})["checks"]}


def test_the_page_shows_it(client, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(W, "_mw_costs", lambda: ({"110771": mw(act=640.5)}, "2026-11-05 08:00"))
    login(client)
    upload(client, xlsx(tmp_path))
    page = client.get("/commissions").data.decode()
    assert "but Moveware has the supplier" in page and "Moveware ya tiene el costo del proveedor" in page
    assert "640.50 MXN" in page and "2026-11-05 08:00" in page
