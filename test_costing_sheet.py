"""Move Costing Sheet: line parsing, per-file cost, supplier checks, dashboard use."""
import datetime as dt
import io

import openpyxl
import pytest

import costing_sheet as cs
import exec_dash
import fx

D = dt.datetime


def _sheet(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


HEAD = ["ESTIMATED COSTS", "ACTUAL COSTS", "ACCRUALS AND INVOICING", "Description", "Qty", "Rate", "Value"]


def _rp(job, ct="Private"):
    return ["Removal Plan", ":", job, "Name", ":", "Some Customer", "Details", ":", "x", "Type", ":", "IMP",
            "RP Status : W", "Est Move : ", f"Client Type: {ct}", "Volume : 1", "Nett Pounds: 1"]


def _opt(no, status):
    return ["Cost Option", ":", no, "Opt", "details", "Surveyor", "Cost Status", ":", status, "Service", "", "",
            "Values Inclusive of Tax", ""]


def _line(desc, est, sell, act=None, date=None, po="", cred=""):
    return [desc, 1, est, est, None, None, sell, None, act, date, "C" if act is not None else "A",
            "Y" if act is not None else "N", po, cred]


def _total(cost, sell):
    return ["Option Total", None, None, cost, None, None, sell]


ROWS = [
    ["Won From: 01/01/26, Won To: 09/10/26, Type: DIN,EXP,EXT,IMP"],
    _rp("110578"),
    _opt("01", "C"), HEAD, _line("Cancelled line", 999, 999), _total(999, 999),
    _opt("02", "W"), HEAD,
    _line("Origin Services", 244200.24, 244200.24, 255750.26, D(2026, 5, 5), "418100", "Nobel Relocation"),
    _line("Freight", 87780.09, 198085.91, 87780.09, D(2026, 4, 1), "418101", "Landstar"),
    _line("Despacho", 1250, 1250, 20625.02, D(2026, 4, 16), "418118", "JE Forwarding"),
    _line("Insurance", 23882.15, 20588.06),
    ["Profit Margin"], _total(357112.48, 464124.21),
    _rp("110579"),
    _opt("01", "W"), HEAD,
    _line("Freight", 1000, 2000, 1000, D(2026, 3, 1), "418101", "Landstar"),   # same approval no. as 110578
    _line("Handling", 500, 800, 450, D(2026, 3, 2), "", "Thelsa Mudanzas"),    # no approval no.
    _total(1500, 2800),
    _rp("110579A"),
    _opt("01", "W"), HEAD, _line("Extra storage", 300, 400, 300, D(2026, 3, 9), "418300", "WH"), _total(300, 400),
]


@pytest.fixture
def parsed():
    return cs.parse(_sheet(ROWS))


def test_parse_keeps_lines_but_no_customer_names(parsed):
    assert parsed["period"] == {"won_from": "2026-01-01", "won_to": "2026-10-09"}
    assert set(parsed["files"]) == {"110578", "110579", "110579A"}
    assert "Some Customer" not in repr(parsed)
    w = [o for o in parsed["files"]["110578"]["options"] if o["status"] == "W"][0]
    assert [ln["desc"] for ln in w["lines"]] == ["Origin Services", "Freight", "Despacho", "Insurance"]
    assert w["lines"][1]["creditor"] == "Landstar" and w["lines"][1]["po"] == "418101"


def test_summary_counts_w_only_and_adds_sequels(parsed):
    s = cs.summarize(parsed)
    a = s["110578"]
    assert a["act"] == round(255750.26 + 87780.09 + 20625.02, 2)
    assert a["lines_pending"] == 1 and not a["complete"]
    assert a["cost_best"] == round(a["act"] + 23882.15, 2)          # provision for the open line
    b = s["110579"]
    assert b["parts"] == ["110579", "110579A"] and b["act"] == 1750.0 and b["complete"]


def test_not_a_costing_sheet():
    with pytest.raises(ValueError):
        cs.parse(_sheet([["hello"], ["world"]]))


def test_supplier_checks(parsed):
    rows = cs.supplier_checks(parsed, delivered={"110578": "2026-06-01"}, today=dt.date(2026, 10, 15))
    kinds = {(r["job"], r["check"]) for r in rows}
    assert ("110578", "over_quote") in kinds             # Despacho 1,250 → 20,625
    assert ("110579", "no_approval") in kinds
    assert ("110578", "po_on_two_files") in kinds and ("110579", "po_on_two_files") in kinds
    assert ("110578", "invoice_missing") in kinds        # insurance, delivered 136 days ago
    assert ("110578", "over_quote") in kinds and not any(
        r["check"] == "over_quote" and r["line"] == "Origin Services" for r in rows)   # +4.7% is fine


def test_currency_rules():
    assert cs.file_currency("110766", "USD", "MEU") == "MXN"        # known exception
    assert cs.file_currency("110900", "", "MEU") == "USD"
    assert cs.file_currency("110900", "", "MEM") == "MXN"
    assert cs.to_mxn(100, "USD", {"inv_orig": {"USD": 10}, "inv_mxn": 175}) == 1750.0
    assert cs.to_mxn(100, "USD", {}, 17.0) == 1700.0
    assert cs.to_mxn(100, "MXN") == 100
    assert cs.to_mxn(100, "", {}) is None


@pytest.fixture
def _offline(monkeypatch, tmp_path):
    monkeypatch.setenv("FX_CACHE_PATH", str(tmp_path / "fx.json"))
    monkeypatch.delenv("BANXICO_TOKEN", raising=False)
    monkeypatch.setattr(fx, "_fetch_yahoo", lambda codes: {})
    monkeypatch.setattr(fx, "_ecb", lambda y, m: {"rate": 18.0, "date": f"{y}-{m:02d}-30", "source": "ECB"})
    fx._mem.clear()
    fx._me.clear()


def test_dashboard_uses_sheet_cost_when_finance_has_none(_offline, parsed):
    ledger = {"110578": {"first": "2026-05-01", "last": "2026-05-01", "inv_mxn": 500000,
                         "inv_orig": {"MXN": 500000}, "cost_mxn": 0}}
    files = [{"job": "110578", "currency": "MXN", "branch": "MEM", "status": "W", "inv_list": []}]
    y = exec_dash.build(files, {"ledger": ledger}, dt.date(2026, 10, 8))["periods"]["ytd"]
    assert y["fin_pending_n"] == 1 and y["fin_sheet_n"] == 0
    y = exec_dash.build(files, {"ledger": ledger}, dt.date(2026, 10, 8),
                        costing=cs.summarize(parsed))["periods"]["ytd"]
    assert y["fin_pending_n"] == 0 and y["fin_sheet_n"] == 1 and y["fin_costed_n"] == 1
    assert y["fin_cost_mxn"] == cs.summarize(parsed)["110578"]["cost_best"]


def test_plan_dates_fill_gaps_only_when_done():
    files = [{"job": "110862", "pack": None, "delivery": None},
             {"job": "110801", "delivery": "2020-09-02"},
             {"job": "110492", "delivery": "2026-09-17"},
             {"job": "111147", "pack": None}]
    sit = {"110862": {"delivery": {"date": "2026-10-09", "status": "done"}},
           "110801": {"delivery": {"date": "2026-09-04", "status": "done"}},
           "110492": {"delivery": {"date": "2026-09-20", "status": "done"}},
           "111147": {"pack": {"date": "2026-12-07", "status": "scheduled"}}}
    out = {f["job"]: f for f in exec_dash.overlay_plan_dates(files, sit, dt.date(2026, 10, 15))}
    assert out["110862"]["delivery"] == "2026-10-09" and out["110862"]["delivery_src"] == "plan"
    assert out["110801"]["delivery"] == "2026-09-04"                  # 2020 typo replaced
    assert out["110492"]["delivery"] == "2026-09-17" and "delivery_src" not in out["110492"]
    assert out["111147"]["pack"] is None                              # future: never used
    assert files[0]["delivery"] is None                               # originals untouched


def test_compare_with_finance(parsed):
    s = cs.summarize(parsed)
    fin = {"files": {"110578": {"total_cost": 300000.0}, "110579": {"total_cost": 1760.0}}, "ledger": {}}
    rows = {r["job"]: r for r in cs.compare_finance(s, fin, {"110578": ("MXN", "MEM"), "110579": ("MXN", "MEM")})}
    assert rows["110579"]["finding"] == "match"
    assert rows["110578"]["finding"] == "higher" and rows["110578"]["difference_mxn"] > 0
