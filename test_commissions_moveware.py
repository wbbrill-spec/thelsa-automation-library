"""What the commission report takes from Moveware: bookings per month and how
Moveware classes a job. Works on the records mw_live already holds; no reads."""
import datetime as dt

import pytest

import mw_live
from commissions import engine as E
from commissions import finance as F
from commissions import moveware as M

S = E.Settings()


def fl(job, typ, sales=1000.0, date=dt.date(2026, 6, 1), cost=0.0):
    return F.FinLine(row=1, type=typ, job=job, file=job[:6], date=date, entity="TMS SA",
                     series="X", folio="1", currency="MXN", currency_source="stated", fx=1.0,
                     usd_equiv=None, net_sales_mxn=sales, supplier_cost_mxn=cost, interco_cost_mxn=0.0,
                     cancels="", has_comment=False, embassy_billed=False)


def mw(job, ct="", agent=False, corp=False, status="W", booked=dt.date(2026, 6, 10), sell=1000.0, ccy="MXN"):
    return {"job": job, "number": job, "status": status, "booked": booked, "sell": sell, "currency": ccy,
            "customer_type": ct, "has_booking_agent": agent, "has_corporate_account": corp}


FIN = [fl("110771", "AGENTE"), fl("110771A", "AGENTE"), fl("110771B", "CORP-PART"),
       fl("110800", "CORP-PART"), fl("110801", "CORP-PART"), fl("110930", "DIPLOM"),
       fl("110894", "AGENTE"), fl("110894", "CORP-PART"), fl("110999", "CORP-PART")]
MW = [mw("110771", agent=True), mw("110771A", agent=True), mw("110771B"),
      mw("110800", ct="Company", corp=True), mw("110801", agent=True), mw("110930", ct="Diplomatic"),
      mw("110894", agent=True)]


def test_crosstab_measures_instead_of_assuming():
    t = M.type_crosstab(FIN, MW)
    assert t["finance_jobs"] == 8 and t["matched"] == 6
    assert t["pairs"][("AGENTE", ("(blank)", "yes", "no"))] == 2
    assert t["pairs"][("CORP-PART", ("Company", "no", "yes"))] == 1
    assert t["pairs"][("DIPLOM", ("Diplomatic", "no", "no"))] == 1
    assert t["not_in_moveware_reader"] == ["110999"]
    assert t["typed_two_ways_by_finance"] == ["110894"]
    # a booking agent mostly means AGENTE here, with one exception to look at
    sig = t["by_signature"][("(blank)", "yes", "no")]
    assert sig == {"points_to": "AGENTE", "jobs": 3, "agree": 2, "exceptions": 1}


def test_lots_are_matched_as_their_own_jobs():
    t = M.type_crosstab(FIN, MW)
    assert ("CORP-PART", ("(blank)", "no", "no")) in t["pairs"]        # 110771B, not its file


def test_unknown_roles_are_shown_as_unknown():
    f = mw("110802")
    f["has_booking_agent"] = f["has_corporate_account"] = None
    assert M.signature(f) == ("(blank)", "?", "?")


def test_bookings_by_month_counts_won_jobs_in_scope_at_the_internal_rate():
    files = [mw("110800", sell=100000), mw("110771B", sell=2000, ccy="USD"),
             mw("110801", sell=50000, booked=dt.date(2026, 7, 1)),
             mw("110771", sell=999999),                                   # agent: out of scope
             mw("110803", sell=70000, status="P"),                        # not won
             mw("110804", sell=70000, booked=None), mw("110805", sell=0),
             mw("110806", sell=500, ccy="EUR")]
    fin = FIN + [fl(j, "CORP-PART") for j in ("110803", "110804", "110805", "110806")]
    b = M.bookings_by_month(files, M.typed_by_finance(fin), S)
    assert set(b) == {"months", "skipped", "won_without_number"} and b["won_without_number"] == 0
    assert b["months"]["2026-06"] == {"bookings": pytest.approx(100000 + 2000 * 16.5), "jobs": 2}
    assert b["months"]["2026-07"] == {"bookings": pytest.approx(50000), "jobs": 1}
    assert b["skipped"] == {"won, no date won": ["110804"], "won, no sale price": ["110805"],
                            "currency EUR": ["110806"]}


def test_bookings_do_not_depend_on_order_or_on_how_a_number_arrives():
    import itertools
    files = [mw("110800", sell=100000), mw("110800", sell=250000, booked=dt.date(2026, 7, 2)),
             mw("110801", sell=None), mw("110801", sell=None),
             mw("110771B", sell=1000), mw("110771B", sell=1000)]
    results = [M.bookings_by_month(list(p), M.typed_by_finance(FIN), S) for p in itertools.permutations(files)]
    assert all(r == results[0] for r in results)
    assert results[0]["months"] == {"2026-06": {"bookings": pytest.approx(1000), "jobs": 1}}
    assert results[0]["skipped"] == {
        "held twice by the reader with different details, not counted": ["110800"],
        "held twice by the reader, counted once": ["110771B", "110801"],
        "won, no sale price": ["110801"]}
    odd = [dict(mw("x", sell=100000), number=110800.0, job=None, status=" w "),
           dict(mw("x", sell="2,000.00"), number=" 110771 b ", currency="usd"),
           dict(mw("x", sell=5), number="", job="")]
    b = M.bookings_by_month(odd, M.typed_by_finance(FIN), S)
    assert b["months"]["2026-06"] == {"bookings": pytest.approx(100000 + 2000 * 16.5), "jobs": 2}
    assert b["won_without_number"] == 1


def test_a_sale_price_that_is_not_a_number_never_reaches_a_total():
    files = [mw("110800", sell=float("nan")), mw("110801", sell=float("inf")), mw("110771B", sell="about 5000")]
    b = M.bookings_by_month(files, M.typed_by_finance(FIN), S)
    assert b["months"] == {}
    assert b["skipped"] == {"won, sale price is not a number": ["110771B", "110800", "110801"]}


def test_a_date_typed_as_text_never_becomes_a_month():
    assert M._month(dt.datetime(2026, 6, 10, 9, 30)) == "2026-06"
    assert M._month("2026-06-10") == "2026-06" and M._month("2026-06-10T00:00:00") == "2026-06"
    for bad in ("10/06/2026", "June 10", "2026-13-01", "2026-6-1", "", None, 20260610):
        assert M._month(bad) == ""
    files = [mw("110800", sell=100000, booked="10/06/2026"), mw("110801", sell=5, booked="2026-07-02")]
    b = M.bookings_by_month(files, M.typed_by_finance(FIN), S)
    assert list(b["months"]) == ["2026-07"]
    assert b["skipped"] == {"won, date won not readable": ["110800"]}


def test_a_job_the_reader_holds_twice_is_counted_once():
    files = [mw("110800", sell=100000), mw("110800", sell=100000), mw("110800", sell=100000)]
    b = M.bookings_by_month(files, M.typed_by_finance(FIN), S)
    assert b["months"]["2026-06"] == {"bookings": pytest.approx(100000), "jobs": 1}
    assert b["skipped"] == {"held twice by the reader, counted once": ["110800"]}


def test_a_job_finance_typed_two_ways_takes_its_latest_type():
    """110894: invoiced as AGENTE in May, re-classified, cost posted as CORP-PART in June."""
    fin = [fl("110894", "AGENTE", date=dt.date(2026, 5, 29)),
           fl("110894", "CORP-PART", sales=0.0, cost=5000.0, date=dt.date(2026, 6, 30)),
           fl("110895", "CORP-PART", date=dt.date(2026, 5, 2)),
           fl("110895", "AGENTE", sales=0.0, cost=10.0, date=dt.date(2026, 6, 30)),
           fl("110896", "CORP-PART", date=dt.date(2026, 6, 1)), fl("110896", "AGENTE", date=dt.date(2026, 6, 1))]
    ok = M.typed_by_finance(fin)
    assert ok(mw("110894")) and not ok(mw("110895")) and not ok(mw("110896"))
    assert ok.two_ways == ["110894", "110895", "110896"] and ok.undecided == ["110896"]


def test_reader_records_date_won_and_moveware_marks_without_names(monkeypatch):
    detail = {"id": 110782, "number": "110771A", "status": "W", "jobValue": 5000, "currency": "USD",
              "customerType": {"code": "", "text": "Company"},
              "activityDates": {"created": {"date": "2026-05-01"}, "booked": {"date": "2026-06-10"}},
              "roles": {"bookingAgent": {"entity": {"id": 77, "name": "Secret Agent GmbH"}},
                        "corporateAccount": {"entity": {}}}}
    monkeypatch.setattr(mw_live, "_get", lambda path: detail if path == "/jobs/110782" else {})
    m = mw_live._map_job({"id": "110782", "number": "110771A", "status": "W"})
    assert m["booked"] == dt.date(2026, 6, 10)
    assert m["customer_type"] == "Company"
    assert m["has_booking_agent"] is True and m["has_corporate_account"] is False
    assert M.signature(m) == ("Company", "yes", "no")
    marks = {k: m[k] for k in ("customer_type", "has_booking_agent", "has_corporate_account", "booked")}
    assert "Secret Agent" not in repr(marks)


def test_empty_roles_block_means_not_known(monkeypatch):
    detail = {"id": 111000, "number": "111000", "status": "W", "roles": {}}
    monkeypatch.setattr(mw_live, "_get", lambda path: detail if path == "/jobs/111000" else {})
    m = mw_live._map_job({"id": "111000", "number": "111000", "status": "W"})
    assert m["has_booking_agent"] is None and m["has_corporate_account"] is None
