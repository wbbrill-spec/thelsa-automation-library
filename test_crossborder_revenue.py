"""Revenue, currency and the books-FX basis.

The point of these tests is to stop a plausible-looking money bug reaching a
leadership dashboard. Two specific mistakes are guarded hard:

  * adding MXN and USD together (wrong by ~17x), and
  * reading `roles.<x>.name` instead of `roles.<x>.entity.name`, which returns
    "" for every job and looks exactly like Moveware having no agents on file.

Payload shapes below are copied from live Thelsa jobs (111131, 111135, 110872)
on 2026-09-16, not invented.
"""
from __future__ import annotations

import datetime as dt

import pytest

from crossborder import engine, fx, tms
from crossborder.models import Shipment, Source


# ── fx.normalize_currency ────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expect", [
    ("ACC USD", "USD"),          # what V2 actually sends on `payment`
    ("ACC MXN", "MXN"),
    ("USD", "USD"),
    ("  mxn  ", "MXN"),
    ("", ""),
    (None, ""),
    ("ACC EUR", ""),             # unsupported → blank, never a guess
    ("ACCOUNT", ""),
])
def test_normalize_currency(raw, expect):
    assert fx.normalize_currency(raw) == expect


# ── fx.rate_for ──────────────────────────────────────────────────────────────
def test_rate_for_known_month_is_not_provisional():
    rate, month, provisional = fx.rate_for(dt.date(2026, 7, 15))
    assert (rate, month, provisional) == (16.908, "2026-07", False)


def test_rate_for_month_past_the_table_carries_forward_and_flags_it():
    """Sept 2026 has no rate on file yet. Carry August forward, but say so."""
    rate, month, provisional = fx.rate_for(dt.date(2026, 9, 16))
    assert rate == 17.0 and month == "2026-08" and provisional is True


def test_rate_for_h1_uses_the_blended_rate():
    assert fx.rate_for(dt.date(2026, 3, 1))[0] == 17.36


def test_rate_for_no_date_is_provisional():
    assert fx.rate_for(None)[2] is True


def test_books_table_env_override(monkeypatch):
    monkeypatch.setenv("CB_FX_BOOKS", "2026-09:16.94,2026-10:16.5")
    table = fx.books_table()
    assert table["2026-09"] == 16.94 and table["2026-10"] == 16.5
    assert table["2026-07"] == 16.908          # defaults survive
    assert fx.rate_for(dt.date(2026, 9, 16), table) == (16.94, "2026-09", False)


def test_books_table_ignores_garbage_rather_than_breaking_the_board(monkeypatch):
    monkeypatch.setenv("CB_FX_BOOKS", "nonsense,2026-13:x,2026-09:-4,2026-09:16.94")
    assert fx.books_table()["2026-09"] == 16.94


# ── fx.convert ───────────────────────────────────────────────────────────────
def test_convert_mxn_to_usd_at_the_months_rate():
    # The live August file: 268,512.16 MXN at 17.0 ≈ 15,795 USD.
    assert fx.convert(268512.16, "ACC MXN", "USD", dt.date(2026, 8, 20)) == 15794.83


def test_convert_usd_to_mxn_round_trips():
    mxn = fx.convert(1000, "USD", "MXN", dt.date(2026, 7, 1))
    assert mxn == 16908.0
    assert fx.convert(mxn, "MXN", "USD", dt.date(2026, 7, 1)) == 1000.0


def test_convert_same_currency_is_untouched():
    assert fx.convert(2532.84, "ACC USD", "USD", dt.date(2026, 8, 1)) == 2532.84


def test_convert_without_a_currency_returns_none_not_zero():
    """A missing currency must not be silently treated as the target one."""
    assert fx.convert(1000, "", "USD", dt.date(2026, 8, 1)) is None
    assert fx.convert(None, "USD", "MXN") is None
    assert fx.convert("not a number", "USD", "MXN") is None


# ── tms._role: the entity.name trap ──────────────────────────────────────────
LIVE_ROLES = {                                   # shape from job 111135
    "billTo": {"type": "billTo", "name": "", "entity": {"name": "Sirva International BRGS"}},
    "bookingAgent": {"entity": {"name": "Sirva"}},
    "corporateAccount": {"entity": {"name": "Dow"}},
    "destinationAgent": {"entity": {"firstName": "Ana", "lastName": "Reyes"}},
}


def test_role_reads_the_nested_entity_name():
    assert tms._role({"roles": LIVE_ROLES}, "billTo") == "Sirva International BRGS"
    assert tms._role({"roles": LIVE_ROLES}, "corporateAccount") == "Dow"


def test_role_falls_back_to_a_person_name():
    assert tms._role({"roles": LIVE_ROLES}, "destinationAgent") == "Ana Reyes"


def test_role_missing_or_malformed_is_blank_not_an_error():
    assert tms._role({"roles": LIVE_ROLES}, "originAgent") == ""
    assert tms._role({}, "billTo") == ""
    assert tms._role({"roles": "nope"}, "billTo") == ""
    assert tms._role({"roles": {"billTo": "Plain String"}}, "billTo") == "Plain String"


# ── tms._job_value / _job_currency ───────────────────────────────────────────
def test_job_value_reads_v2_job_value():
    assert tms._job_value({"jobValue": 2532.84}, {}, {}) == 2532.84


def test_job_value_falls_back_to_the_v1_extras_field():
    """TMS_MW_ENV=v1 is the rollback path; it must still report revenue."""
    assert tms._job_value({}, {}, {"revenue": "7504.68"}) == 7504.68


def test_job_value_missing_is_none_not_zero():
    assert tms._job_value({}, {}, {}) is None
    assert tms._job_value({"jobValue": 0}, {}, {}) is None


def test_job_currency_parses_the_payment_string():
    assert tms._job_currency({"payment": "ACC USD"}, {}) == "USD"
    assert tms._job_currency({"payment": "ACC MXN"}, {}) == "MXN"


def test_job_currency_unknown_is_blank():
    assert tms._job_currency({"payment": "ACC"}, {}) == ""
    assert tms._job_currency({}, {}) == ""


# ── build_shipment: the FX month anchor ──────────────────────────────────────
def _row(**kw):
    row = {"id": 111131, "status": "W",
           "addresses": {"origin": {"country": "Mexico"}, "destination": {"country": "United States"}}}
    row.update(kw)
    return row


def _detail(**kw):
    d = {"id": 111131, "jobValue": 2532.84, "payment": "ACC USD",
         "customerType": "Company", "invoiceStatus": "N", "roles": LIVE_ROLES,
         "activityDates": {"created": {"date": "2026-06-02"}}}
    d.update(kw)
    return d


def test_export_prices_off_the_pack_date():
    """Bill's rule: exports earn at the pack/load date."""
    d = _detail(activityDates={"created": {"date": "2026-06-02"}, "pack": {"date": "2026-07-20"}})
    s = tms.build_shipment(_row(), d, today=dt.date(2026, 9, 16))
    assert s.extra["direction"] == "export"
    assert s.revenue_month == "2026-07"
    assert s.revenue_month_basis == "pack"


def test_import_prices_off_the_delivery_date():
    row = _row(addresses={"origin": {"country": "United States"},
                          "destination": {"country": "Mexico"}})
    d = _detail(activityDates={"created": {"date": "2026-06-02"},
                               "delivery": {"date": "2026-08-11"}})
    s = tms.build_shipment(row, d, today=dt.date(2026, 9, 16))
    assert s.extra["direction"] == "import"
    assert s.revenue_month == "2026-08"
    assert s.revenue_month_basis == "delivery"


def test_falls_back_to_the_booked_date_and_says_so():
    """8 of 9 live imports have no delivery date. That must be visible, not hidden."""
    s = tms.build_shipment(_row(), _detail(), today=dt.date(2026, 9, 16))
    assert s.revenue_month == "2026-06"
    assert s.revenue_month_basis == "booked"


def test_build_shipment_carries_the_commercial_fields():
    s = tms.build_shipment(_row(), _detail(), today=dt.date(2026, 9, 16))
    assert s.revenue == 2532.84
    assert s.revenue_currency == "USD"
    assert s.corporate_account == "Dow"
    assert s.booking_agent == "Sirva"
    assert s.agent == "Sirva"                    # booking agent now names the file
    assert s.customer_type == "Company"
    assert s.invoice_status == "N"


def test_no_revenue_leaves_the_month_blank():
    s = tms.build_shipment(_row(), _detail(jobValue=None), today=dt.date(2026, 9, 16))
    assert s.revenue is None and s.revenue_month == "" and s.revenue_month_basis == ""


# ── Shipment.is_corporate / corporate_account_named ──────────────────────────
def test_placeholder_corporate_account_is_corporate_but_not_named():
    s = Shipment(id="TMS:1", source=Source.TMS, source_ref="1", corporate_account="CORPORATIVO")
    assert s.is_corporate is True
    assert s.corporate_account_named is False


def test_real_corporate_account_is_named():
    s = Shipment(id="TMS:1", source=Source.TMS, source_ref="1", corporate_account="SCHLUMBERGER")
    assert s.is_corporate and s.corporate_account_named


def test_private_move_is_not_corporate():
    s = Shipment(id="TMS:1", source=Source.TMS, source_ref="1")
    assert s.is_corporate is False


def test_diplomatic_counts_as_corporate():
    s = Shipment(id="TMS:1", source=Source.TMS, source_ref="1", customer_type="Diplomatic")
    assert s.is_corporate is True


def test_to_dict_exposes_the_commercial_fields():
    s = Shipment(id="TMS:1", source=Source.TMS, source_ref="1", revenue=100.0,
                 revenue_currency="USD", corporate_account="Dow")
    d = s.to_dict()
    assert d["revenue"] == 100.0 and d["revenue_currency"] == "USD"
    assert d["is_corporate"] is True and d["corporate_account_named"] is True


# ── engine._revenue_by_currency ──────────────────────────────────────────────
def _ship(revenue, currency, month, sid="TMS:1"):
    return Shipment(id=sid, source=Source.TMS, source_ref=sid, revenue=revenue,
                    revenue_currency=currency, revenue_month=month)


def test_revenue_buckets_never_mix_currencies():
    """The whole point: 268,512 MXN + 2,532 USD is not 271,044 of anything."""
    out = engine._revenue_by_currency([
        _ship(268512.16, "MXN", "2026-08", "a"),
        _ship(2532.84, "USD", "2026-08", "b"),
    ])
    assert {b["currency"] for b in out} == {"MXN", "USD"}
    assert out[0]["currency"] == "MXN" and out[0]["amount"] == 268512.16
    assert out[1]["amount"] == 2532.84


def test_revenue_buckets_sum_within_one_currency():
    out = engine._revenue_by_currency([
        _ship(1000, "USD", "2026-07", "a"), _ship(500, "USD", "2026-07", "b")])
    assert out == [{"currency": "USD", "amount": 1500.0, "files": 2,
                    "month": "2026-07", "months": ["2026-07"]}]


def test_revenue_bucket_spanning_months_has_no_single_month():
    """Two months in one bucket means the UI must convert file by file."""
    out = engine._revenue_by_currency([
        _ship(1000, "USD", "2026-07", "a"), _ship(500, "USD", "2026-08", "b")])
    assert out[0]["month"] == "" and out[0]["months"] == ["2026-07", "2026-08"]


def test_revenue_buckets_skip_files_with_no_revenue_rather_than_counting_zero():
    out = engine._revenue_by_currency([
        _ship(1000, "USD", "2026-07", "a"), _ship(None, "", "", "b"), _ship(250, "", "", "c")])
    assert len(out) == 1 and out[0]["files"] == 1 and out[0]["amount"] == 1000.0


def test_load_dict_exposes_revenue(monkeypatch):
    """A load carries its revenue split by currency, ready for the card."""
    ships = [_ship(1000, "USD", "2026-07", "a"), _ship(17000, "MXN", "2026-07", "b")]
    for s in ships:
        s.volume_m3 = 10.0
        s.destination = "Monterrey, Nuevo Leon"
    buckets = engine._revenue_by_currency(ships)
    assert sorted(b["currency"] for b in buckets) == ["MXN", "USD"]


# ── fx.describe ──────────────────────────────────────────────────────────────
def test_describe_tells_the_ui_the_basis_and_the_last_real_month():
    info = fx.describe()
    assert info["basis"] == "books" and info["base"] == "MXN"
    assert info["last_month"] == "2026-08"
    assert info["table"]["2026-07"] == 16.908
    assert "provisional" in info
