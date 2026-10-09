"""Executive /audit view: YTD + last-12-months, money pairs, EN/ES template."""
import datetime as dt

import pytest

import exec_dash
import fx

TODAY = dt.date(2026, 10, 8)


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    monkeypatch.setenv("FX_CACHE_PATH", str(tmp_path / "fx_cache.json"))
    monkeypatch.delenv("BANXICO_TOKEN", raising=False)
    monkeypatch.setattr(fx, "_fetch_yahoo", lambda codes: {})
    monkeypatch.setattr(fx, "_ecb", lambda y, m: {"rate": 18.0, "date": f"{y}-{m:02d}-30",
                                                   "source": "ECB reference rate"})
    fx._mem.clear()
    fx._me.clear()


def _f(job, **kw):
    base = {"job": job, "job_id": int(job), "client": "C", "status": "W", "sell": 0,
            "currency": "MXN", "inv_list": [], "pack": None, "delivery": None,
            "anchor": "2026-09-01", "coordinator": "", "coordinator_email": ""}
    base.update(kw)
    return base


def test_periods():
    p = exec_dash.periods(TODAY)
    assert p["ytd"] == (dt.date(2026, 1, 1), TODAY)
    assert p["l12m"][0] == dt.date(2025, 10, 9)


def test_spot_is_previous_month_end():
    s = fx.month_end_spot(TODAY)
    assert s["month"] == "2026-09" and s["rate"] == 18.0 and not s["stale"]


def test_pair_currency_split():
    assert exec_dash.pair(100, "MXN") == [100.0, 0.0]
    assert exec_dash.pair(100, "USD") == [0.0, 100.0]
    eur = exec_dash.pair(100, "EUR")
    assert eur[0] == 0.0 and eur[1] > 100      # EUR folded into USD at cross rate


def test_revenue_only_numbered_active_invoices_in_period():
    files = [_f("1", inv_list=[
        {"n": "500", "d": "2026-03-01", "c": "USD", "net": 1000, "st": "N"},
        {"n": "501", "d": "2026-03-02", "c": "MXN", "net": 5000, "st": "C"},   # cancelled
        {"n": "", "d": "", "c": "MXN", "net": 7000, "st": "N"},              # unnumbered
        {"n": "502", "d": "2025-11-01", "c": "MXN", "net": 2000, "st": "N"},  # L12M only
    ])]
    ex = exec_dash.build(files, {}, TODAY)
    y, l = ex["periods"]["ytd"], ex["periods"]["l12m"]
    assert y["inv_num"] == [0.0, 1000.0]
    assert l["inv_num"] == [2000.0, 1000.0]
    assert y["inv_unnum"] == [7000.0, 0.0] and y["unnum_files"] == 1


def test_ready_to_invoice_rules():
    files = [
        _f("10", sell=50000, pack="2026-09-01"),                                  # ready
        _f("11", sell=50000, pack="2026-09-01", status="L"),                       # lead
        _f("12", sell=50000, pack="2026-09-01", status="C"),                       # cancelled
        _f("13", sell=50000, pack="2026-09-01", coordinator="Edgarespino"),        # embassy, not delivered
        _f("14", sell=9_000_000, pack="2026-09-01"),                               # quote to check
        _f("15", sell=50000, pack="2026-09-01",
           inv_list=[{"n": "9", "d": "2026-09-05", "c": "MXN", "net": 50000, "st": "N"}]),  # billed
        _f("16", sell=50000, pack="2025-12-01"),                                   # L12M only
    ]
    ex = exec_dash.build(files, {}, TODAY)
    y = ex["periods"]["ytd"]
    assert [r["job"] for r in y["ready"]] == ["10"]
    assert y["ready_val"] == [50000.0, 0.0]
    assert [r["job"] for r in y["emb"]] == ["13"]
    assert [r["job"] for r in y["checks"]] == ["14"]
    assert y["billed_n"] == 1
    assert y["pct_billed"] == round(1 / 3 * 100, 1)
    assert {r["job"] for r in ex["periods"]["l12m"]["ready"]} == {"10", "16"}


def test_huge_embassy_quote_goes_to_checks_not_transit():
    files = [_f("30", sell=5_519_320, pack="2026-06-24", coordinator="Edgarespino")]
    y = exec_dash.build(files, {}, TODAY)["periods"]["ytd"]
    assert y["emb_n"] == 0 and y["emb_val"] == [0.0, 0.0]
    assert [r["job"] for r in y["checks"]] == ["30"] and y["checks"][0]["embassy"]


def test_diplom_type_from_finance_marks_embassy():
    files = [_f("20", sell=1000, pack="2026-09-01")]
    fin = {"files": {"20": {"type": "DIPLOM"}}, "ledger": {}}
    y = exec_dash.build(files, fin, TODAY)["periods"]["ytd"]
    assert y["ready_n"] == 0 and y["emb_n"] == 1


def test_finance_margin_excludes_cost_pending():
    ledger = {
        "1": {"first": "2026-02-01", "last": "2026-02-01", "inv_mxn": 100000, "inv_orig": {"MXN": 100000},
              "cost_mxn": 70000},
        "2": {"first": "2026-03-01", "last": "2026-03-01", "inv_mxn": 18000, "inv_orig": {"USD": 1000},
              "cost_mxn": 0},                                              # cost pending
        "3": {"first": "2026-04-01", "last": "2026-05-01", "inv_mxn": 10000, "inv_orig": {"MXN": 10000},
              "cost_mxn": 12000},                                          # below cost
        "4": {"first": "2024-01-01", "last": "2024-02-01", "inv_mxn": 5, "inv_orig": {"MXN": 5},
              "cost_mxn": 1},                                              # out of both periods
    }
    y = exec_dash.build([], {"ledger": ledger}, TODAY)["periods"]["ytd"]
    assert y["fin_costed_n"] == 2 and y["fin_pending_n"] == 1 and y["fin_neg_n"] == 1
    assert y["fin_margin"] == [110000 - 82000, 0.0]
    assert y["fin_margin_pct"] == round(28000 / 110000 * 100, 1)
    assert y["fin_sales_all"] == [110000.0, 1000.0]
    assert y["fin_last_invoice"] == "2026-05-01"


def test_template_renders_bilingual():
    from flask import Flask, render_template_string
    from exec_template import EXEC_TEMPLATE
    files = [_f("10", sell=50000, pack="2026-09-01", client="Ana & Co")]
    ex = exec_dash.build(files, {}, TODAY)
    ex.update(remap={"running": False}, query_sheet="https://example.org/sheet")
    m = {"total_disc_pair": [0, 0], "disc_files": 0, "coords_affected": 0, "ub_have_creds": False,
         "by_coordinator_disc": [], "disc_worklist": []}
    app = Flask(__name__)
    with app.app_context():
        html = render_template_string(EXEC_TEMPLATE, m=m, ex=ex)
    assert "Listos para facturar" in html and "Ready to invoice" in html
    assert 'data-x="50000.0"' in html
    assert "Ana &amp; Co" in html
    assert "plan: 17.4479" in html


def test_template_supplier_costs_tab():
    from flask import Flask, render_template_string
    from exec_template import EXEC_TEMPLATE
    import supplier_costs as sc
    msg = {"subject": "RE: X / 110886", "body": "Demoras: 2,040 USD + 15% finance fee", "sender": "a@insa.com.ec",
           "date": "2026-10-07", "mailbox": "m"}
    state = {"have_creds": True}
    state.update(sc.summarize(sc.merge(sc.rows_from_message(msg))))
    ex = exec_dash.build([], {}, TODAY)
    ex.update(remap={"running": False}, query_sheet="#", sc=state)
    m = {"total_disc_pair": [0, 0], "disc_files": 0, "coords_affected": 0, "ub_have_creds": False,
         "by_coordinator_disc": [], "disc_worklist": []}
    with Flask(__name__).app_context():
        html = render_template_string(EXEC_TEMPLATE, m=m, ex=ex)
    assert "Costos de proveedores" in html and "110886" in html and "2,346.00" in html
    ex["sc"] = {"have_creds": False, "rows": [], **sc.summarize({})}
    with Flask(__name__).app_context():
        html = render_template_string(EXEC_TEMPLATE, m=m, ex=ex)
    assert "Waiting on mailbox access" in html
