"""The /commissions pages: who may open them, what an upload keeps, and that the
page, the stored report and the Excel download all show the same numbers."""
import io
import json

import openpyxl
import pytest
from flask import Flask

from commissions import engine as E
from commissions import finance as F
from commissions import report as R
from commissions import store
from commissions import web as W
from test_commissions_finance import ROWS, book, cost, row

LUPITA, OUTSIDER = "maria.gonzalez@thelsa.com", "someone.else@thelsa.com"
NAMES = ("Acme", "Jane Roe", "Big Co", "Mary Major", "EMBAJADA", "Marcel", "MARCEL", "Supplier", "RECLASIF")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COMMISSION_DATABASE_URL", f"sqlite:///{tmp_path}/c.db")
    monkeypatch.delenv("COMMISSION_USERS", raising=False)
    store.reset_for_tests()
    app = Flask(__name__)
    app.secret_key = "test"
    app.add_url_rule("/login", "login", lambda: "login")
    app.register_blueprint(W.commissions_bp)
    with app.test_client() as c:
        c.application_ref = app
        yield c
    store.reset_for_tests()


def login(c, email=LUPITA):
    with c.session_transaction() as s:
        s["user_email"] = email
        s["commission_csrf"] = "tok"


def xlsx(tmp_path, rows=ROWS, name="m.xlsx"):
    return open(book(tmp_path / name, rows), "rb").read()


def upload(c, data, filename="Margen x Expediente 2609.xlsx", csrf="tok"):
    return c.post("/commissions/upload", data={"csrf": csrf, "report": (io.BytesIO(data), filename)},
                  content_type="multipart/form-data")


# ── who may open it ──────────────────────────────────────────────────────────
def test_needs_a_login(client):
    assert client.get("/commissions").status_code == 302
    assert client.post("/commissions/upload").status_code == 302


def test_every_commission_route_is_closed_to_everyone_not_on_the_list(client):
    """A new route cannot ship open: every rule under /commissions is tried."""
    login(client, OUTSIDER)
    rules = [r for r in client.application_ref.url_map.iter_rules() if r.rule.startswith("/commissions")]
    assert len(rules) >= 4
    for r in rules:
        for method in r.methods - {"HEAD", "OPTIONS"}:
            assert client.open(r.rule, method=method).status_code == 404, (r.rule, method)


def test_the_list_is_lupita_mario_rogelio_and_bill_unless_render_says_otherwise(client, monkeypatch):
    assert W.allowed_users() == {"maria.gonzalez@thelsa.com", "mariotorres@thelsa.com", "rogeliofranco@thelsa.com",
                                 "bbrill@thelsa.com", "bill.brill@inflectionpointnow.com"}
    for who in ("MarioTorres@thelsa.com", "rogeliofranco@thelsa.com"):
        login(client, who)
        assert client.get("/commissions").status_code == 200
    monkeypatch.setenv("COMMISSION_USERS", "only.one@thelsa.com; ")
    login(client, LUPITA)
    assert client.get("/commissions").status_code == 404
    login(client, "only.one@thelsa.com")
    assert client.get("/commissions").status_code == 200


def test_page_before_any_upload(client):
    login(client)
    r = client.get("/commissions")
    assert r.status_code == 200 and b"No report has been uploaded yet" in r.data
    assert r.headers["Cache-Control"] == "no-store"
    assert client.get("/commissions/workbook").status_code == 302


# ── upload ───────────────────────────────────────────────────────────────────
def test_upload_needs_the_form_token(client, tmp_path):
    login(client)
    assert upload(client, xlsx(tmp_path), csrf="wrong").status_code == 400
    assert upload(client, xlsx(tmp_path), csrf="").status_code == 400
    assert store.get("finance_report")[0] is None


def test_upload_keeps_lines_and_no_names(client, tmp_path):
    login(client)
    assert upload(client, xlsx(tmp_path)).status_code == 302
    doc, meta = store.get("finance_report")
    assert meta["by"] == LUPITA and len(doc["records"]) == len(ROWS) - 1 and len(doc["sha256"]) == 64
    with store.engine().connect() as c:
        everything = " ".join(str(v) for row_ in c.exec_driver_sql("select * from commission_docs") for v in row_)
    page = client.get("/commissions").data.decode()
    for name in NAMES:
        assert name not in everything and name not in page
    assert "Report loaded: 19 lines dated 2026-01-12 to 2026-08-05" in page
    log = store.get("uploads")[0]
    assert len(log) == 1 and log[0]["by"] == LUPITA and log[0]["lines"] == 19


def test_page_shows_the_same_numbers_as_the_engine(client, tmp_path):
    login(client)
    upload(client, xlsx(tmp_path))
    page = client.get("/commissions").data.decode()
    rep = R.build(F.load_base(io.BytesIO(xlsx(tmp_path))), W.settings())
    assert f"{rep['total_new']['total']:,.2f}" in page and f"{rep['total_new']['billed']:,.0f}" in page
    assert "Trial. Not for payment." in page and "= budget" in page
    assert "Exchange rate looks mistyped" in page and "110633" in page
    assert "waits for cost" in page
    for p in rep["people"]:
        assert p["name"].replace("&", "&amp;") in page and f"{p['if_collected']:,.2f}" in page


@pytest.mark.parametrize("what", ["not excel name", "garbage", "money on a row with no job", "text amount", "empty"])
def test_a_report_that_cannot_be_read_changes_nothing(client, tmp_path, what):
    login(client)
    upload(client, xlsx(tmp_path))
    before = store.get("finance_report")[0]
    good = row("110001", 1000)
    stray = row(None, 500, typ=None, folio=2)
    stray[3] = None
    bad = {"not excel name": (xlsx(tmp_path), "report.csv"), "garbage": (b"PK\x03\x04 not a workbook", "r.xlsx"),
           "money on a row with no job": (xlsx(tmp_path, [good, stray], "s.xlsx"), "r.xlsx"),
           "text amount": (xlsx(tmp_path, [row("110001", "ver con Sr. Perez")], "t.xlsx"), "r.xlsx"),
           "empty": (b"", "r.xlsx")}[what]
    assert upload(client, *bad).status_code == 302
    page = client.get("/commissions").data.decode()
    assert store.get("finance_report")[0] == before
    assert len(store.get("uploads")[0]) == 1
    assert "Perez" not in page
    assert ("Nothing was changed" in page) or ("must be an Excel workbook" in page)
    if what in ("money on a row with no job", "text amount"):
        assert "row 4" in page or "row 3" in page


def test_a_second_upload_replaces_the_first_and_keeps_the_previous_one(client, tmp_path):
    login(client)
    upload(client, xlsx(tmp_path))
    first = store.get("finance_report")[0]
    upload(client, xlsx(tmp_path, [row("110001", 1000), cost("110001", 400)], "two.xlsx"))
    assert len(store.get("finance_report")[0]["records"]) == 2
    assert store.get("finance_report_previous")[0] == first
    assert len(store.get("uploads")[0]) == 2


def test_an_upload_too_large_is_refused(client, tmp_path, monkeypatch):
    login(client)
    monkeypatch.setattr(W, "MAX_UPLOAD", 100)
    upload(client, xlsx(tmp_path))
    assert store.get("finance_report")[0] is None
    assert "larger than 20 MB" in client.get("/commissions").data.decode()


def test_a_stored_report_that_was_changed_by_hand_is_refused(client, tmp_path):
    login(client)
    upload(client, xlsx(tmp_path))
    doc = store.get("finance_report")[0]
    doc["records"][0]["job"] = "ACME CORPORATION"
    store.put("finance_report", doc, by="x")
    page = client.get("/commissions").data.decode()
    assert "could not be read back" in page and "ACME" not in page
    assert client.get("/commissions/workbook").status_code == 302


# ── bookings ─────────────────────────────────────────────────────────────────
def test_bookings_change_sales_reach(client, tmp_path):
    login(client)
    upload(client, xlsx(tmp_path))
    budget = E.BUDGET_2026["2026-05"]
    r = client.post("/commissions/bookings", data={"csrf": "tok", "b_2026-05": f"{budget * 1.5:,.2f}", "b_2026-06": ""})
    assert r.status_code == 302
    assert store.get("bookings")[0] == {"2026-05": pytest.approx(round(budget * 1.5, 2))}
    page = client.get("/commissions").data.decode()
    assert "0.9000" in page and "150.0%" in page                 # May: 1.5 x 0.60
    assert "Bookings are not entered for 2 month(s)" in page     # June and July of the fixture
    rep = R.build(F.from_records(store.get("finance_report")[0]["records"]), W.settings(), store.get("bookings")[0])
    assert rep["months"][0]["reach"] == pytest.approx(0.9) and f"{rep['total_new']['total']:,.2f}" in page


@pytest.mark.parametrize("bad", ["abc", "1.234.567,00", "-5", "0", "12,34", "1e6", "100.123"])
def test_bookings_that_are_not_an_amount_are_not_saved(client, bad):
    login(client)
    client.post("/commissions/bookings", data={"csrf": "tok", "b_2026-05": "2,000,000.00"})
    client.post("/commissions/bookings", data={"csrf": "tok", "b_2026-05": bad, "b_2026-06": "3,000,000"})
    assert store.get("bookings")[0] == {"2026-05": 2000000.0}
    assert "Bookings were not saved" in client.get("/commissions").data.decode()


def test_bookings_need_the_form_token_and_blank_clears_a_month(client):
    login(client)
    assert client.post("/commissions/bookings", data={"b_2026-05": "1"}).status_code == 400
    client.post("/commissions/bookings", data={"csrf": "tok", "b_2026-05": "2000000"})
    client.post("/commissions/bookings", data={"csrf": "tok", "b_2026-05": ""})
    assert store.get("bookings")[0] == {}


# ── Excel download ───────────────────────────────────────────────────────────
def test_workbook_download_matches_the_page(client, tmp_path):
    login(client)
    upload(client, xlsx(tmp_path))
    client.post("/commissions/bookings", data={"csrf": "tok", "b_2026-05": "3,000,000.00"})
    r = client.get("/commissions/workbook")
    assert r.status_code == 200 and r.headers["Content-Disposition"].endswith('to 2026-08-05.xlsx"')
    wb = openpyxl.load_workbook(io.BytesIO(r.data))
    assert wb.sheetnames == ["Summary", "Settings", "Lines", "Checks"]
    st = wb["Settings"]
    may = next(i for i in range(1, 60) if st.cell(row=i, column=1).value == "2026-05")
    assert st.cell(row=may, column=6).value == 3000000.0           # typed-in bookings
    assert st.cell(row=may + 1, column=6).value == f"=E{may + 1}"  # June: still the placeholder
    text = " ".join(str(c.value) for ws in wb for r_ in ws.iter_rows() for c in r_ if c.value is not None)
    for name in NAMES:
        assert name not in text
    rep = R.build(F.load_base(io.BytesIO(xlsx(tmp_path))), W.settings(), {"2026-05": 3000000.0})
    jobs_in_book = [r_[1].value for r_ in wb["Lines"].iter_rows(min_row=5) if r_[1].value]
    assert jobs_in_book == [x["job"] for x in rep["lines"]]


# ── the report as numbers ────────────────────────────────────────────────────
def test_report_totals_are_the_sum_of_their_months_and_people_get_all_of_it(tmp_path):
    lines = F.load_base(book(tmp_path / "r.xlsx", ROWS))
    rep = R.build(lines, W.settings(), {"2026-05": 1_000_000, "2026-07": float("nan"), "2026-06": -5, "x": "1"})
    assert rep["placeholder_months"] == ["2026-06", "2026-07"]       # nan and negative bookings are not bookings
    assert rep["total_all"]["total"] == pytest.approx(sum(m["total"] for m in rep["months"]))
    assert rep["total_new"]["total"] == pytest.approx(sum(r["total"] for r in rep["lines"]))
    assert sum(p["if_collected"] for p in rep["people"]) == pytest.approx(rep["total_new"]["total"])
    assert rep["control"] == pytest.approx(0, abs=1e-6)
    assert rep["total_new"]["payable"] == 0
    assert [c["kind"] for c in rep["checks"]][:2] == ["fx_outlier", "embassy_billed_in_scope"]
    assert json.dumps(rep)                                           # plain numbers and text all the way down


def test_a_month_without_a_budget_is_not_calculated_and_is_named(tmp_path):
    from test_commissions_finance import D
    lines = F.load_base(book(tmp_path / "r.xlsx", [row("110001", 1000), row("110002", 2000, date=D(2027, 1, 5), folio=2)]))
    rep = R.build(lines, W.settings())
    assert rep["months_without_budget"] == ["2027-01"] and [m["month"] for m in rep["months"]] == ["2026-05"]


def test_records_round_trip_and_refuse_anything_the_reader_did_not_write(tmp_path):
    lines = F.load_base(book(tmp_path / "r.xlsx", ROWS))
    recs = json.loads(json.dumps(F.to_records(lines)))
    assert F.from_records(recs) == lines
    for field, value in (("job", "ACME"), ("series", "GARCIA"), ("folio", "12b"), ("type", "PRIVADO"),
                         ("net_sales_mxn", "1000"), ("net_sales_mxn", float("nan")), ("cancels", "see Juan"),
                         ("currency", "GBP"), ("date", "yesterday"), ("extra", 1), ("entity", "Garcia SA")):
        bad = json.loads(json.dumps(recs))
        bad[3][field] = value
        with pytest.raises(F.FinanceReportError) as e:
            F.from_records(bad)
        assert "stored line 4" in str(e.value) and str(value) not in str(e.value)


# ── MXN / USD and EN / ES ────────────────────────────────────────────────────
def test_page_carries_both_languages_and_both_currencies(client, tmp_path):
    import re
    login(client)
    upload(client, xlsx(tmp_path))
    page = client.get("/commissions").data.decode()
    assert 'id="ccySeg"' in page and 'data-c="usd"' in page and 'id="langSeg"' in page and 'data-l="es"' in page
    for es in ("Reporte de comisiones TMS", "Prueba. No usar para pago.", "Facturado", "Costo real", "Margen bruto",
               "Alcance de ventas", "Por mes de factura", "Puntos por revisar en el reporte de Finanzas",
               "Ventas contratadas por mes", "Subir reporte", "Descargar en Excel", "Cálculo nuevo",
               "El tipo de cambio parece mal capturado", "fila 17: tipo de cambio 12.6231", "espera costo",
               "Compradora y Gerente de Costos (Lupita)", "Reporte cargado: 19 líneas", "mayo 2026"):
        assert es in page, es
    rep = R.build(F.load_base(io.BytesIO(xlsx(tmp_path))), W.settings())
    spans = re.findall(r'class="m" data-x="([-0-9.]+)" data-u="([-0-9.]*)" data-d="(\d)">([^<]*)<', page)
    assert len(spans) > 100
    assert all(u != "" for _x, u, _d, _t in spans)                  # every amount has its dollar figure
    pairs = {(round(float(x), 2), round(float(u), 2)) for x, u, _d, _t in spans}
    new = rep["total_new"]
    assert (round(new["total"], 2), round(new["usd"]["total"], 2)) in pairs
    may = rep["months"][0]
    assert may["month"] == "2026-05" and may["rate"] == 16.5                      # the commission's own rate
    assert (round(may["billed"], 2), round(may["billed"] / 16.5, 2)) in pairs
    assert "internal rate of 16.50 pesos per dollar" in page and "tipo de cambio interno de 16.50" in page
    # without the script the page still reads in pesos
    assert f">{new['total']:,.2f}<" in page


def test_dollar_view_at_the_internal_rate_shows_the_dollars_invoiced():
    from test_commissions_finance import USD
    import tempfile, pathlib
    lines = F.load_base(book(pathlib.Path(tempfile.mkdtemp()) / "r.xlsx",
                             [row("110001", 175000, usd=10000, ccy=USD, fx=17.5), row("110002", 33000, folio=2)]))
    rep = R.build(lines, W.settings())
    assert W.settings().sales_fx == "internal"
    assert rep["rates"] == {"by_month": {"2026-05": 16.5}, "other": 16.5, "from_report": False}
    by = {x["job"]: x for x in rep["lines"]}
    assert by["110001"]["billed"] == pytest.approx(165000) and by["110001"]["usd"]["billed"] == pytest.approx(10000)
    assert by["110002"]["usd"]["billed"] == pytest.approx(2000)
    assert rep["total_all"]["usd"]["total"] == pytest.approx(rep["total_all"]["total"] / 16.5)


def test_dollar_view_with_spot_uses_each_months_own_rate_and_adds_up():
    from test_commissions_finance import D, USD
    rows = [row("110001", 175000, usd=10000, ccy=USD, fx=17.5), row("110002", 12623.1, usd=1000, ccy=USD, fx=12.6231, folio=2),
            row("110003", 176000, usd=10000, ccy=USD, fx=17.6, folio=3),
            row("110004", 180000, usd=10000, ccy=USD, fx=18.0, folio=4, date=D(2026, 6, 3)),
            row("110005", 90000, folio=5, date=D(2026, 7, 3))]                    # a month with no dollar invoice
    import tempfile, pathlib
    lines = F.load_base(book(pathlib.Path(tempfile.mkdtemp()) / "r.xlsx", rows))
    spot = E.Settings(budgets=dict(E.BUDGET_2026), sales_fx="spot")
    rep = R.build(lines, spot)
    rates = rep["rates"]["by_month"]
    assert rates["2026-05"] == pytest.approx(17.5) and rates["2026-06"] == pytest.approx(18.0)
    assert rates["2026-07"] == pytest.approx(17.55) == rep["rates"]["other"]      # the middle of all of them
    for m in rep["months"]:
        assert m["usd"]["billed"] == pytest.approx(m["billed"] / m["rate"])
    for k in R.MONEY:
        assert rep["total_all"]["usd"][k] == pytest.approx(sum(m["usd"][k] for m in rep["months"]))
    assert sum(p["usd"]["if_collected"] for p in rep["people"]) == pytest.approx(rep["total_new"]["usd"]["total"])
    assert sum(x["usd"]["total"] for x in rep["lines"]) == pytest.approx(rep["total_all"]["usd"]["total"])
    none = R.build([ln for ln in lines if ln.job == "110005"], spot)
    assert none["rates"]["from_report"] is False and none["rates"]["other"] == 16.5


def test_every_point_and_every_label_has_its_spanish(tmp_path):
    assert set(R.LABELS_ES) == set(R.LABELS)
    assert all(R.LABELS_ES[k] != R.LABELS[k] for k in R.LABELS)
    lines = F.load_base(book(tmp_path / "r.xlsx", ROWS))
    for s in (E.Settings(), E.Settings(sales_fx="internal")):
        for p in F.problems(lines, s):
            assert p["detail_es"] and p["detail_es"] != p["detail"], p["kind"]
            assert "row " not in p["detail_es"]


def test_messages_come_in_both_languages(client, tmp_path):
    login(client)
    upload(client, xlsx(tmp_path, [row("110001", "ver nota")], "t.xlsx"))
    page = client.get("/commissions").data.decode()
    assert "the cell holds text, not an amount" in page and "la celda tiene texto, no un importe" in page
    assert "No se cambió nada" in page
    client.post("/commissions/bookings", data={"csrf": "tok", "b_2026-05": "abc"})
    page = client.get("/commissions").data.decode()
    assert "May 2026" in page and "No se guardaron las ventas contratadas" in page and "mayo 2026" in page
