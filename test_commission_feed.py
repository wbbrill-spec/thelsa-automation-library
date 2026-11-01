"""AuditTrack reads the reports uploaded on the Commission Report (commission_feed)."""
import datetime as dt
from dataclasses import asdict

import pytest

import commission_feed as cf
from commissions import store
from commissions.invoices import Invoice
from commissions.payments import Payment
from commissions.quotes import Removal


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("COMMISSION_DATABASE_URL", f"sqlite:///{tmp_path}/c.db")
    store.reset_for_tests()
    cf.clear_cache()
    yield
    store.reset_for_tests()
    cf.clear_cache()


def inv(job, series, folio, total, currency="MXN", company="TRS", date="2026-09-10", cancels="", rate=None):
    return Invoice(company=company, kind="CORP", job=job, date=date, series=series, folio=folio,
                   currency=currency, total=total, subtotal=round(total / 1.16, 2), pesos=round(total / 1.16, 2),
                   rate=rate, cancels=cancels, row=1)


def put_register(invoices):
    store.put("invoice_register", {"records": {"invoices": [asdict(x) for x in invoices]}})


def put_payments(payments, to="2026-09-30"):
    store.put("payment_reports", {"sources": {"TRS": {
        "records": {"payments": [asdict(p) for p in payments],
                    "info": {"company": "TRS", "key": "TRS", "from": "2026-01-01", "to": to,
                             "currencies": ["MXN"], "paid": {}}},
        "at": "2026-10-05T10:00:00"}}})


def test_nothing_uploaded_means_nothing_changes():
    assert cf.invoiced_files() == {"files": set(), "to": ""}
    assert cf.open_invoices()["open"] == []
    assert cf.price_differences([{"job": "111154", "sell": 17880, "currency": "USD"}]) == []


def test_files_in_the_register_count_as_invoiced_but_not_cancelled_ones():
    put_register([inv("110887", "TRS", "100", 11600.0), inv("110900A", "TRS", "101", 5800.0),
                  inv("110950", "TRS", "102", 2320.0),
                  inv("", "TRS", "103", 2320.0, cancels="TRS 102")])
    got = cf.invoiced_files()
    assert got["files"] == {"110887", "110900"}
    assert got["to"] == "2026-09-10"


def test_open_invoices_only_where_a_payments_report_covers_the_company():
    put_register([inv("110887", "TRS", "100", 11600.0),                     # paid in full
                  inv("110888", "TRS", "101", 11600.0),                     # half paid
                  inv("110889", "TRS", "102", 11600.0),                     # nothing paid
                  inv("110890", "TMS", "5", 11600.0, company="TMS SA")])    # no TMS SA report
    put_payments([Payment("2026-09-20", "MXN", "TRS", "100", 11600.0, 1, 2, "TRS"),
                  Payment("2026-09-20", "MXN", "TRS", "101", 5800.0, 1, 3, "TRS")])
    got = cf.open_invoices(today=dt.date(2026, 10, 31))
    assert [(r["folio"], r["jobs"]) for r in got["open"]] == [("101", ["110888"]), ("102", ["110889"])]
    assert got["open"][0]["paid_share"] == 0.5
    assert got["not_covered"] == {"TMS SA": 1}


def test_an_invoice_after_the_payments_report_ends_is_not_called_unpaid():
    put_register([inv("110889", "TRS", "102", 11600.0, date="2026-10-15")])
    put_payments([], to="2026-09-30")
    got = cf.open_invoices()
    assert got["open"] == [] and got["not_covered"] == {"TRS": 1}


def test_booked_price_difference_is_flagged():
    rem = [Removal("111154", "IMP", "MEU", "Company", "2026-10-01", "W", 9790.74, 11357.26, 5),
           Removal("111155", "IMP", "MEU", "Company", "2026-10-01", "W", 1000.0, 1160.0, 6)]
    store.put("removals_summary", {"records": {"jobs": [asdict(r) for r in rem],
                                               "control": {"listed": 2, "listed_ex": 10790.74,
                                                           "total_files": 2, "total_ex": 10790.74}}})
    files = [{"job": "111154", "sell": 17880.0, "currency": "USD", "coordinator": "Sara Reyes"},
             {"job": "111155", "sell": 1160.0, "currency": "USD"}]                     # = booked price with IVA
    got = cf.price_differences(files)
    assert got == [{"job": "111154", "moveware": 17880.0, "booked": 9790.74, "currency": "USD",
                    "difference": 8089.26, "coordinator": "Sara Reyes"}]


def test_the_ready_to_invoice_view_uses_the_register(monkeypatch):
    import audit_web
    monkeypatch.setattr(audit_web, "_load_finance_cost", lambda: {})
    put_register([inv("110887", "TRS", "100", 11600.0)])
    assert "110887" in audit_web._finance_invoice_view()["invoiced"]


def test_a_costing_sheet_uploaded_for_commissions_also_feeds_the_supplier_checks(monkeypatch):
    import costing_sheet
    from commissions import web as W
    seen = []
    monkeypatch.setattr(costing_sheet, "parse", lambda data: {"period": {"won_to": "2026-10-31"}, "files": {}, "raw": data})
    monkeypatch.setattr(costing_sheet, "save", lambda parsed, by="": seen.append((parsed["raw"], by)))
    W._feed_audittrack("costing_sheet", b"xlsx-bytes", "lupita@thelsa.com")
    W._feed_audittrack("removals_summary", b"other", "lupita@thelsa.com")      # read from the store instead
    assert seen == [(b"xlsx-bytes", "lupita@thelsa.com")]


def test_a_bad_costing_sheet_never_breaks_the_commission_upload(monkeypatch):
    import costing_sheet
    from commissions import web as W
    monkeypatch.setattr(costing_sheet, "parse", lambda data: (_ for _ in ()).throw(ValueError("not a sheet")))
    W._feed_audittrack("costing_sheet", b"junk", "lupita@thelsa.com")          # no exception


def test_lettered_lots_are_added_to_their_file_before_comparing():
    rem = [Removal("110873", "IMP", "MEM", "Company", "2026-10-01", "W", 104976.53, 121772.77, 5)]
    store.put("removals_summary", {"records": {"jobs": [asdict(r) for r in rem],
                                               "control": {"listed": 1, "listed_ex": 104976.53,
                                                           "total_files": 1, "total_ex": 104976.53}}})
    files = [{"job": "110873", "sell": 21140.43, "currency": "MXN"},
             {"job": "110873A", "sell": 47684.70, "currency": "MXN"},
             {"job": "110873B", "sell": 36151.40, "currency": "MXN"}]
    assert cf.price_differences(files) == []
