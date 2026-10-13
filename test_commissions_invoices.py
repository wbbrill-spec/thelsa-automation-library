"""Finance's register of invoices issued, and what it changes in deciding which
invoices are collected.

The fixture has the shape of "FACTURADO ACUMULADO YTD" and carries made-up
customer and staff names, so the tests can check that none of it is kept."""
import datetime as dt
import json

import openpyxl
import pytest

from commissions import engine as E
from commissions import invoices as I
from commissions import payments as P
from commissions import report as R
from test_commissions_finance import D, MXN, USD, load, row  # noqa: F401  (load is a fixture)
from test_commissions_payments import pay, report

NAMES = ("ACME HIDDEN SA", "Jane Secret", "FERNANDA", "PHILIPPE")
S = E.Settings(budgets=dict(E.BUDGET_2026))
HEAD = ["Cía", "Mes", "Tipo", "EXPEDIENTE", "Ce.Co", "Fecha", "Serie", "Folio", "FACTURA", "Razón Social", "Total Factura",
        "Factura que cancela", "Subtotal EUR", "Subtotal USD", "Subtotal MX", "FILE2", "Moneda", "TC EUR", "TC MX", "TC USD",
        "Total MX", "Total USD", "VENDEDOR", "ADM. DE VENTAS"]


def inv(co, kind, job, date, series, folio, total, sub, ccy="MXP", rate=17.5, cancels=None):
    """One row of the register. `sub` is the subtotal in the invoice's currency."""
    usd = sub if ccy == "USD" else round(sub / rate, 2)
    mx = sub * rate if ccy == "USD" else sub
    return [co, "Junio", kind, job, kind, date, series, folio, f"{series} {folio}", "ACME HIDDEN SA", total, cancels,
            None, usd, mx, job, ccy, None, 1, rate, None, None, "Jane Secret", "FERNANDA"]


def register(tmp_path, rows, name="reg.xlsx", head=HEAD):
    wb = openpyxl.Workbook()
    wb.active.title = "INVOICE YTD"
    wb.active.append(head)
    for r in rows:
        wb.active.append(r)
    p = tmp_path / name
    wb.save(p)
    return p


def test_register_is_read_without_customer_or_staff_names(tmp_path):
    p = register(tmp_path, [
        inv("TRS", "CORP", 110001, D(2026, 6, 5), "TMS", 1, 116000.0, 100000.0),
        inv("LLC", "PART", "110002A", D(2026, 6, 9), "PART", 365, 5000.0, 5000.0, ccy="USD", rate=17.19),
        inv("SA", "AGTS", "PHILIPPE", D(2026, 8, 12), "AGTS", 83, 1937.22, 1937.22, ccy="USD"),      # a name where the file goes
        inv("LLC", "NCPART", 110002, D(2026, 6, 30), "NCPART", 59, -5000.0, -5000.0, ccy="USD", cancels="PART365"),
        inv("XX", "weird type!", 110003, 46200, "CORP", "0146", 10.0, 10.0, ccy="EUR", rate=99.0),
        [None] * len(HEAD)])
    got, info = I.load(p)
    assert info == {"rows": 5, "invoices": 5, "from": "2026-06-05", "to": "2026-08-12",
                    "companies": {"TRS": 1, "TMS LLC": 2, "TMS SA": 1, "?": 1}}
    assert [(x.company, x.kind, x.job, x.date, x.series, x.folio, x.currency, x.total, x.subtotal, x.rate, x.cancels) for x in got] == [
        ("TRS", "CORP", "110001", "2026-06-05", "TMS", "1", "MXN", 116000.0, 100000.0, 17.5, ""),
        ("TMS LLC", "PART", "110002A", "2026-06-09", "PART", "365", "USD", 5000.0, 5000.0, 17.19, ""),
        ("TMS SA", "AGTS", "", "2026-08-12", "AGTS", "83", "USD", 1937.22, 1937.22, 17.5, ""),
        ("TMS LLC", "NCPART", "110002", "2026-06-30", "NCPART", "59", "USD", -5000.0, -5000.0, 17.5, "PART 365"),
        ("", "", "110003", "2026-06-27", "CORP", "146", "EUR", 10.0, 0.0, None, "")]
    doc = json.dumps(I.to_records(got))
    for name in NAMES:
        assert name not in doc
    assert I.from_records(json.loads(doc)) == (got, info)
    for bad in ({"invoices": []}, {"invoices": [{"x": 1}]}, {}, {"invoices": [dict(json.loads(doc)["invoices"][0], job="Jane Secret")]},
                {"invoices": [dict(json.loads(doc)["invoices"][0], series="ACME HIDDEN SA")]}):
        with pytest.raises(I.InvoiceRegisterError):
            I.from_records(bad)


def test_register_refuses_other_workbooks_and_cells_it_cannot_read(tmp_path):
    for rows, head, words in (
            ([inv("TRS", "CORP", 110001, D(2026, 6, 5), "TMS", 1, 1.0, 1.0)], HEAD[:6], "does not look like"),
            ([inv("TRS", "CORP", 110001, D(2026, 6, 5), "Jane Secret SA de CV", 1, 1.0, 1.0)], HEAD, "row 2: the invoice series or folio"),
            ([inv("TRS", "CORP", 110001, D(2026, 6, 5), "TMS", "Jane Secret", 1.0, 1.0)], HEAD, "row 2: the invoice series or folio"),
            ([inv("TRS", "CORP", 110001, "Jane Secret", "TMS", 1, 1.0, 1.0)], HEAD, "row 2: the invoice date"),
            ([inv("TRS", "CORP", 110001, D(2026, 6, 5), "TMS", 1, "Jane Secret", 1.0)], HEAD, "row 2: the invoice total is not a number"),
            ([], HEAD, "no invoices")):
        with pytest.raises(I.InvoiceRegisterError) as e:
            I.load(register(tmp_path, rows, head=head))
        assert words in str(e.value) and "Jane" not in str(e.value) and e.value.es
    (tmp_path / "junk.xlsx").write_bytes(b"junk")
    with pytest.raises(I.InvoiceRegisterError):
        I.load(tmp_path / "junk.xlsx")


# ── what the register changes in the collected invoices ──────────────────────
def _sources(tmp_path, mxp=(), usd=(), **kw):
    ps, info = P.load(report(tmp_path, mxp=mxp, usd=usd, **kw))
    return ps, dict(reach=R.payment_reach([(ps, info)]), companies=R.company_reach([(ps, info)]))


def test_real_series_and_company_come_from_the_register(load, tmp_path):
    """The margin report writes CORP-PART where the series goes; the payment is of PART 365."""
    lines = load([row("110002", sales=85950.0, usd=5000.0, ccy=USD, fx=17.19, date=D(2026, 6, 9), series="CORP-PART", folio=365, entity=None),
                  row("110003", sales=1000.0, date=D(2026, 6, 9), series="CORP-PART", folio=365, entity=None)])   # not in the register
    reg, _ = I.load(register(tmp_path, [inv("LLC", "PART", 110002, D(2026, 6, 9), "PART", 365, 5000.0, 5000.0, ccy="USD", rate=17.19)]))
    assert [(i["series"], i["company"]) for i in R.invoice_map(lines)[("2026-06", "110002")]] == [("CORP-PART", "")]
    m = R.invoice_map(lines, reg)
    assert [(i["series"], i["company"], i["total"], i["currency"]) for i in m[("2026-06", "110002")]] == [("PART", "TMS LLC", 5000.0, "USD")]
    assert m[("2026-06", "110003")][0]["series"] == "PART"          # same folio and date: the only series there is
    ps, kw = _sources(tmp_path, usd=[pay(46190, 365, 5000.0, series="PART")], company="THELSA MOBILITY SOLUTIONS LLC")
    status, _p, summary = R.auto_payments(lines, ps, register=reg, **kw)
    assert status[("2026-06", "110002")] == {"paid": True, "date": "2026-06-17"}
    assert R.auto_payments(lines, ps, **kw)[0] == {}                 # without the register the payment finds no invoice
    assert summary["register"] and [(c["company"], c["report"], c["full"]) for c in summary["companies"]] == [("TMS LLC", True, 2)]


def test_a_company_report_covers_that_company_only(load, tmp_path):
    """Two companies use the series CORP. A payment in one company's report is not of the other's invoice."""
    lines = load([row("110001", sales=100000.0, date=D(2026, 6, 5), series="CORP", folio=146, entity="SA"),
                  row("110002", sales=100000.0, date=D(2026, 6, 5), series="CORP", folio=146, entity="LLC"),
                  row("110003", sales=50000.0, date=D(2026, 6, 8), series="CORP", folio=147, entity="SA"),
                  row("110004", sales=50000.0, date=D(2026, 6, 8), series="CORP", folio=121, entity="LLC")])
    ps, kw = _sources(tmp_path, mxp=[pay(46190, 146, 116000.0, series="CORP")], company="THELSA MOBILITY SOLUTIONS LLC")
    assert kw["companies"] == {"TMS LLC": "2026-09-30"}
    status, _p, summary = R.auto_payments(lines, ps, **kw)
    assert {k: v["paid"] for k, v in status.items()} == {("2026-06", "110002"): True, ("2026-06", "110004"): False}
    assert {c["company"]: (c["report"], c["invoices"], c["covered"]) for c in summary["companies"]} == {
        "TMS LLC": (True, 2, 2), "TMS SA": (False, 2, 0)}
    # a report of a company not known by name speaks for the series it shows, as before
    ps, kw = _sources(tmp_path, mxp=[pay(46190, 146, 116000.0, series="CORP")], company="Some Other Company")
    assert kw["companies"] == {} and set(R.auto_payments(lines, ps, **kw)[0]) == set(R.invoice_map(lines))
    # with no payments at all the summary still says whose reports are needed
    rep = R.build(lines, S, {}, register=I.load(register(tmp_path, [inv("SA", "CORP", 110001, D(2026, 6, 5), "CORP", 146, 116000.0, 100000.0)]))[0])
    assert rep["register_loaded"] and rep["paid_lines"] == 0
    assert [(c["company"], c["report"]) for c in rep["collection"]["companies"]] == [("TMS LLC", False), ("TMS SA", False)]


def test_the_whole_invoice_is_compared_not_one_file_of_it(load, tmp_path):
    """An invoice for two files, half paid: the payment is more than either file's part, and still not in full."""
    lines = load([row("110001", sales=60000.0, date=D(2026, 6, 5), series="TMS", folio=1, entity="TRS"),
                  row("110002", sales=40000.0, date=D(2026, 6, 5), series="TMS", folio=1, entity="TRS"),
                  row("110003", sales=30000.0, date=D(2026, 6, 6), series="TMS", folio=2, entity="TRS"),
                  row("110004", sales=20000.0, date=D(2026, 6, 6), series="TMS", folio=2, entity="TRS")])
    ps, kw = _sources(tmp_path, mxp=[pay(46190, 1, 58000.0), pay(46190, 2, 58000.0)])
    status, problems, _s = R.auto_payments(lines, ps, **kw)
    assert {k[1]: v["paid"] for k, v in status.items()} == {"110001": False, "110002": False, "110003": True, "110004": True}
    assert sorted(p["job"] for p in problems) == ["110001", "110002"] and "58%" in problems[0]["detail"]


def test_in_full_is_measured_against_the_invoice_total(load, tmp_path):
    lines = load([
        row("110001", sales=100000.0, date=D(2026, 6, 5), series="TMS", folio=1, entity="TRS"),     # paid less the IVA withheld on freight
        row("110002", sales=100000.0, date=D(2026, 6, 5), series="TMS", folio=2, entity="TRS"),     # paid without the IVA
        row("110003", sales=100000.0, date=D(2026, 6, 5), series="TMS", folio=3, entity="TRS"),     # no IVA on it: 97 % is short
        row("110004", sales=175130.0, usd=10000.0, ccy=USD, fx=17.513, date=D(2026, 6, 5), series="TMS", folio=4, entity="TRS"),  # pesos, rate moved 3 %
        row("110005", sales=175130.0, usd=10000.0, ccy=USD, fx=17.513, date=D(2026, 6, 5), series="TMS", folio=5, entity="TRS"),  # pesos, 89 %
        row("110006", sales=100000.0, date=D(2026, 6, 5), series="TMS", folio=6, entity="TRS"),     # half credited, the rest paid
        row("110006", sales=-50000.0, date=D(2026, 6, 20), series="NCTMS", folio=1, cancels="TMS 6", entity="TRS")])
    reg, _ = I.load(register(tmp_path, [
        inv("TRS", "CORP", 110001, D(2026, 6, 5), "TMS", 1, 116000.0, 100000.0), inv("TRS", "CORP", 110002, D(2026, 6, 5), "TMS", 2, 116000.0, 100000.0),
        inv("TRS", "CORP", 110003, D(2026, 6, 5), "TMS", 3, 100000.0, 100000.0),
        inv("TRS", "CORP", 110004, D(2026, 6, 5), "TMS", 4, 11600.0, 10000.0, ccy="USD", rate=17.513),
        inv("TRS", "CORP", 110005, D(2026, 6, 5), "TMS", 5, 11600.0, 10000.0, ccy="USD", rate=17.513),
        inv("TRS", "CORP", 110006, D(2026, 6, 5), "TMS", 6, 116000.0, 100000.0)]))
    ps, kw = _sources(tmp_path, mxp=[pay(46190, 1, 112000.0), pay(46190, 2, 100000.0), pay(46190, 3, 97000.0),
                                     pay(46190, 4, 197050.0), pay(46190, 5, 180800.0), pay(46190, 6, 58000.0)])
    status, problems, _s = R.auto_payments(lines, ps, register=reg, **kw)
    assert {k[1]: v["paid"] for k, v in status.items()} == {
        "110001": True, "110002": False, "110003": False, "110004": True, "110005": False, "110006": True}
    assert sorted((p["job"], p["detail"].split("reach ")[1][:3]) for p in problems) == [("110002", "86%"), ("110003", "97%"), ("110005", "89%")]
    # without the register only Finance's amount without IVA can be compared: the invoice paid without its IVA passes
    loose = {k[1]: v["paid"] for k, v in R.auto_payments(lines, ps, **kw)[0].items()}
    assert loose["110002"] is True and loose["110003"] is False and loose["110006"] is True


def test_report_with_the_register(load, tmp_path):
    lines = load([row("110002", sales=85950.0, usd=5000.0, ccy=USD, fx=17.19, date=D(2026, 6, 9), series="CORP-PART", folio=365, entity="LLC"),
                  row("110009", sales=1000.0, date=D(2026, 6, 9), series="CORP", folio=1, entity="SA")])
    reg, _ = I.load(register(tmp_path, [inv("LLC", "PART", 110002, D(2026, 6, 9), "PART", 365, 5000.0, 5000.0, ccy="USD", rate=17.19)]))
    ps, kw = _sources(tmp_path, usd=[pay(46190, 365, 5000.0, series="PART")], company="THELSA MOBILITY SOLUTIONS LLC")
    rep = R.build(lines, S, {}, collected=ps, collected_reach=kw["reach"], collected_companies=kw["companies"], register=reg)
    by = {r["job"]: r for r in rep["lines"]}
    assert (by["110002"]["payment"], by["110002"]["payment_source"], by["110002"]["paid_on"]) == ("Paid", "file", "2026-06-17")
    assert by["110009"]["payment"] == "" and rep["total_all"]["payable"] == pytest.approx(by["110002"]["total"])
    assert json.dumps(rep)
