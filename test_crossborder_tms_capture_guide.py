"""
The Moveware Data Capture Guide, applied to Thelsa (Bill, 4 Oct 2026).

Each test below is a rule from that guide, and each one failed before this
file existed. The measurements quoted in the docstrings were taken against the
live database on 2026-10-04 through the read-only passthrough.
"""
import datetime as dt

from crossborder import tms
from crossborder.models import Stage

TODAY = dt.date(2026, 10, 4)


def row(**kw):
    base = {"id": "111145", "number": "111135A", "status": "W", "method": "ROAD",
            "branchCode": "SLU", "activityDates": {"created": {"date": "2026-09-15"}}}
    base.update(kw)
    return base


def ad(**kw):
    return {"activityDates": {k: {"date": v} for k, v in kw.items() if v}}


# ── §2: the number staff can actually look up ────────────────────────────────
def test_the_display_number_is_published_not_the_internal_id():
    """Live: job id 111145 IS the job staff know as 111135A. The board used to
    print 111145 — a number that finds a different file in Moveware, or none."""
    assert tms.display_number(row()) == "111135A"


def test_the_internal_id_is_only_the_fallback():
    assert tms.display_number({"id": "111170"}) == "111170"


def test_the_detail_wins_over_the_list_row_for_the_number():
    assert tms.display_number(row(), {"number": "111135B"}) == "111135B"


def test_a_file_number_is_the_removal_without_its_sequel_letter():
    assert tms.base_number("111135A") == "111135"
    assert tms.base_number("412042W") == "412042"
    assert tms.base_number("111135") == "111135"


def test_a_sequel_is_recognised_as_one():
    assert tms.is_sequel("111170A") is True
    assert tms.is_sequel("111170") is False


def test_the_shipment_carries_the_display_number_and_keeps_the_id_for_tracing():
    s = tms.build_shipment(row(), {"jobValue": 100}, today=TODAY)
    assert s.reference_number == "111135A"
    assert s.extra["mw_id"] == "111145"
    assert s.extra["file_number"] == "111135"
    assert s.extra["is_sequel"] is True


# ── §3: merge the list row's dates with the detail's ─────────────────────────
def test_a_date_on_the_list_row_is_read_when_the_detail_omits_it():
    """The bug: `_activity_dates(detail) or _activity_dates(row)` — the left
    side is always a callable, so the row was never consulted. On this feed the
    list row carries pack, uplift and delivery, so dates were being thrown away
    and the files they belong to could never be planned."""
    r = row(**ad(created="2026-09-15", uplift="2026-09-25", delivery="2026-10-02"))
    stage, _flags, dates = tms.stage_for(r, {"jobValue": 1}, TODAY)
    assert dates["uplift"] == dt.date(2026, 9, 25)
    assert dates["delivery"] == dt.date(2026, 10, 2)
    assert stage is Stage.DELIVERED


def test_the_detail_still_wins_where_both_have_a_date():
    r = row(**ad(created="2026-09-15", uplift="2026-09-25"))
    d = {"activityDates": {"uplift": {"date": "2026-09-28"}}}
    _stage, _flags, dates = tms.stage_for(r, d, TODAY)
    assert dates["uplift"] == dt.date(2026, 9, 28)


# ── §4: old files that are still moving ──────────────────────────────────────
WINDOW = TODAY - dt.timedelta(days=150)


def test_a_file_opened_in_the_window_is_kept():
    assert tms.keep_active(row(**ad(created="2026-09-15")), WINDOW) == "in window"


def test_an_old_file_with_an_old_collect_date_is_dropped():
    assert tms.keep_active(row(**ad(created="2025-01-10", uplift="2025-02-01")), WINDOW) == ""


def test_an_old_file_that_collects_inside_the_window_is_kept():
    """Moveware's Removals report selects by collect date whatever year the
    file was opened. A window keyed on 'opened recently' misses exactly the
    job that has been sitting around longest — the one most likely to slip."""
    r = row(**ad(created="2024-11-02", uplift="2026-09-30"))
    assert tms.keep_active(r, WINDOW) == "old file still moving"


def test_a_delivery_date_inside_the_window_also_keeps_an_old_file():
    r = row(**ad(created="2024-11-02", delivery="2026-10-01"))
    assert tms.keep_active(r, WINDOW) == "old file still moving"


def test_a_row_with_no_opened_date_is_kept_rather_than_lost_in_silence():
    assert tms.keep_active({"id": "1"}, WINDOW) == "no opened date"


def test_selecting_by_collect_date_alone_would_empty_this_tenants_board():
    """Thelsa's twist on rule 2: uplift is blank on nearly every row because
    the coordinators do not record it. The union keeps those files visible;
    replacing the window with uplift would have hidden the whole feed."""
    r = row(**ad(created="2026-09-15"))          # no uplift at all
    assert tms.keep_active(r, WINDOW) == "in window"


# ── §7: the rep is the rep ───────────────────────────────────────────────────
def test_the_sales_rep_comes_from_its_own_role_and_nothing_else():
    d = {"roles": {"salesRepresentative": {"entity": {"name": "Ana Ruiz"}},
                   "moveManager": {"entity": {"name": "Someone Else"}}}}
    s = tms.build_shipment(row(), d, today=TODAY)
    assert s.extra["sales_rep"] == "Ana Ruiz"


def test_a_job_with_no_rep_gets_no_stand_in():
    s = tms.build_shipment(row(), {"roles": {"moveManager": {"entity": {"name": "X"}}}}, today=TODAY)
    assert s.extra["sales_rep"] == ""


# ── §5.3: branch comes off the list row ──────────────────────────────────────
def test_the_branch_code_is_read_from_the_list_row_when_the_detail_is_silent():
    s = tms.build_shipment(row(), {}, today=TODAY)
    assert s.extra["branch_code"] == "SLU"
