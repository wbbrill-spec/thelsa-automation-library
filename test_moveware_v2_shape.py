"""V2 changed the job row's shape, not just the endpoint.

Measured against the live feed on 2026-09-14, three things moved at once and
each one independently zeroed the cross-border count:

  1. countries are spelled out in full ("United States"), where v1 sent "US".
     The old helper truncated to two letters, so "United States" became "UN"
     and matched nothing.
  2. origin/destination moved under addresses{}; the top-level keys are gone.
  3. the modified timestamp is dateModified, not lastUpdated.

These tests use a real V2 row, trimmed.
"""
import datetime as dt

from crossborder import tms

# A genuine row from /jobs?limit=1&status=W on the live V2 feed, trimmed.
V2_ROW = {
    "id": 111136,
    "status": "W",
    "dateModified": "2026-09-14T16:03:01.235-00:00",
    "activityDates": {"created": {"date": "2026-09-14"}, "uplift": {"date": None}},
    "addresses": {
        "origin": {"city": "", "country": "Mexico", "state": "Jalisco",
                   "fullAddress": "Colón #255a, Ciudad Guzmán, Jalisco, México"},
        "destination": {"city": "Brownsville", "country": "United States",
                        "state": "Texas", "fullAddress": "Brownsville Texas United States"},
    },
}

V1_ROW = {"id": 90001, "status": "W", "lastUpdated": "2026-09-01T00:00:00Z",
          "origin": "US", "destination": "MX"}


# ── country names ────────────────────────────────────────────────────────────


def test_full_country_names_resolve_to_iso_codes():
    assert tms._country("United States") == "US"
    assert tms._country("Mexico") == "MX"
    assert tms._country("Canada") == "CA"


def test_a_truncated_name_is_never_returned():
    """'United States'[:2] == 'UN' was the bug. It must not come back."""
    for name in ("United States", "Mexico", "Canada", "Brazil", "Germany"):
        assert len(tms._country(name)) == 2
        assert tms._country(name) not in ("UN", "ME", "CA" if name != "Canada" else "")


def test_two_letter_codes_still_work():
    assert tms._country("US") == "US"
    assert tms._country("MX") == "MX"


def test_an_address_object_resolves_through_its_country():
    assert tms._country({"country": "United States", "city": "Brownsville"}) == "US"


def test_accented_spanish_names_resolve():
    assert tms._country("México") == "MX"
    assert tms._country("Estados Unidos") == "US"


# ── row shape ────────────────────────────────────────────────────────────────


def test_v2_endpoints_are_read_from_the_addresses_object():
    o, d = tms._endpoints(V2_ROW)
    assert tms._country(o) == "MX" and tms._country(d) == "US"


def test_v1_endpoints_still_work():
    o, d = tms._endpoints(V1_ROW)
    assert tms._country(o) == "US" and tms._country(d) == "MX"


def test_a_real_v2_row_is_recognised_as_cross_border():
    """This is the whole point: before the fix this returned None and the
    dashboard reported 0 cross-border jobs on a feed full of them."""
    assert tms.direction(V2_ROW) == "export"   # Mexico → United States


def test_a_v1_row_is_still_recognised():
    assert tms.direction(V1_ROW) == "import"


def test_a_domestic_row_is_not_cross_border():
    row = {"addresses": {"origin": {"country": "Mexico"},
                         "destination": {"country": "Mexico"}}}
    assert tms.direction(row) is None


def test_created_date_is_read_from_activity_dates():
    assert tms._created(V2_ROW) == dt.date(2026, 9, 14)


def test_created_date_survives_a_missing_block():
    assert tms._created({"id": 1}) is None
    assert tms._created({"activityDates": {"created": {"date": None}}}) is None


# ── the walk ─────────────────────────────────────────────────────────────────

TODAY = dt.date(2026, 9, 14)


def _row(i, created, o="Mexico", d="United States"):
    return {"id": str(i), "status": "W", "dateModified": "2026-09-01T00:00:00Z",
            "activityDates": {"created": {"date": created}},
            "addresses": {"origin": {"country": o}, "destination": {"country": d}}}


class _Erratic:
    """V2 returns erratic page sizes: 18, then 15, then 3, then 12, then 7.
    Nothing about a short page means the feed has ended."""
    env = "test"; base_url = "x"; requests_made = 0; errors = []
    SIZES = [18, 15, 3, 12, 7]

    def __init__(self):
        self.pages = []

    def jobs(self, page=1, limit=50, **f):
        self.pages.append((page, limit, f.get("status")))
        if page > len(self.SIZES):
            return []
        start = sum(self.SIZES[:page - 1])
        return [_row(start + i, (TODAY - dt.timedelta(days=start + i)).isoformat())
                for i in range(self.SIZES[page - 1])]

    def job(self, jid):
        return {}


def test_a_short_page_does_not_end_the_walk():
    """The regression that capped the board at one page: breaking on
    len(rows) < limit stopped after page 1, because V2 never fills a page."""
    c = _Erratic()
    _s, d = tms.fetch_tms_shipments(c, days=150, details=False, today=TODAY)
    assert d["rows_seen"] == sum(_Erratic.SIZES), "every page must be collected"
    assert d["pages_walked"] > 1
    assert d["stopped_because"] == "end of feed"


def test_the_walk_asks_for_a_limit_the_server_honours():
    c = _Erratic()
    tms.fetch_tms_shipments(c, days=150, details=False, today=TODAY)
    page, limit, status = c.pages[0]
    assert limit <= 10, ("at limit=18 and above, V2 returns page 1 again for "
                         "every page — the walk would never see past 18 jobs")
    assert status == "W", "filter to Won jobs server-side"


def test_the_walk_sends_no_date_filter():
    c = _Erratic()
    tms.fetch_tms_shipments(c, days=150, details=False, today=TODAY)
    for _page, _limit, _status in c.pages:
        pass
    assert all("created" not in str(p) for p in c.pages)


class _Stuck(_Erratic):
    """If `page` ever breaks the way `offset` already has, every page is page 1."""
    def jobs(self, page=1, limit=50, **f):
        self.pages.append((page, limit, f.get("status")))
        return [_row(i, (TODAY - dt.timedelta(days=i)).isoformat()) for i in range(5)]


def test_a_feed_that_repeats_itself_is_noticed_quickly():
    c = _Stuck()
    _s, d = tms.fetch_tms_shipments(c, days=150, details=False, today=TODAY)
    assert d["stopped_because"] == "pages stopped yielding new jobs"
    assert d["pages_walked"] <= 5, "must not re-read the same page 40 times"
    assert d["rows_seen"] == 5


def test_jobs_older_than_the_window_are_dropped():
    class Old(_Erratic):
        def jobs(self, page=1, limit=50, **f):
            if page > 1:
                return []
            return [_row(1, "2026-09-10"), _row(2, "2020-01-01")]
    _s, d = tms.fetch_tms_shipments(Old(), days=30, details=False, today=TODAY)
    assert d["rows_seen"] == 1, "the 2020 job is outside a 30-day window"


def test_the_client_sends_page_not_just_offset():
    """V2 ignores `offset`. The walk was fixed to page, but the query string was
    still spelling it `offset=`, so every page came back as page 1 — the walk
    stalled at 10 jobs with the diagnostics insisting it had walked 4 pages."""
    seen = {}

    class Cap(tms.MovewareClient):
        def __init__(self):
            self.base_url = "x"; self.requests_made = 0; self.errors = []
        def get(self, path, timeout=12):
            seen["path"] = path
            return {"jobs": []}

    Cap().jobs(page=3, limit=10, status="W")
    assert "page=3" in seen["path"], "V2 pages on `page`"
    assert "offset=3" in seen["path"], "v1 pages on `offset` — keep the rollback working"
    assert "limit=10" in seen["path"] and "status=W" in seen["path"]


# ── the detail payload ───────────────────────────────────────────────────────
# I claimed V2 "arrives with no volume and no uplift date". That was wrong: both
# are on the job, in V2's shape, and the v1 mapper was reading v1's field names.
# A real job (111025) pinned below so that claim can't be made again.

V2_DETAIL = {
    "id": 111025, "status": "W", "method": "ROAD", "service": "FTL",
    "loadType": "Loose",
    "activityDates": {
        "booked": {"date": "2026-07-28"}, "created": {"date": "2026-07-28"},
        "followup": {"date": "2026-07-31"}, "pack": {"date": "2026-08-20"},
        # uplift is EMPTY on real jobs — pack carries the move-out date.
        "uplift": {"date": None, "time": ""},
        "delivery": {"date": None, "time": ""}, "unpack": {"date": None},
        "estimatedDelivery": {"date": None}, "estimatedMove": {"date": None},
    },
    "measures": [{
        "volume": {"net": {"m3": 35, "f3": 1237}, "gross": {"m3": 35, "f3": 1237}},
        "weight": {"net": {"kg": 3644, "lb": 8034}, "gross": {"kg": 3644, "lb": 8034}},
    }],
    "addresses": {"origin": {"country": "Mexico", "city": "Guadalajara",
                             "state": "Jalisco"},
                  "destination": {"country": "United States", "city": "Brownsville",
                                  "state": "Texas"}},
}
V2_LIST_ROW = {"id": 111025, "status": "W", "addresses": V2_DETAIL["addresses"],
               "activityDates": {"created": {"date": "2026-07-28"}}}


def test_volume_comes_off_the_v2_measures_block():
    m = tms._measurements(V2_DETAIL)
    assert m["volume_m3"] == 35.0
    assert m["weight_kg"] == 3644.0


def test_the_metric_figure_is_used_not_the_converted_imperial_one():
    """1237 f3 / 35.3147 is 35.03 — close, but Moveware derives f3 and it can go
    stale, so the m3 field wins outright."""
    d = {"measures": [{"volume": {"net": {"m3": 35, "f3": 9999}}}]}
    assert tms._measurements(d)["volume_m3"] == 35.0


def test_imperial_is_converted_when_metric_is_absent():
    d = {"measures": [{"volume": {"net": {"f3": 1237}},
                       "weight": {"net": {"lb": 8034}}}]}
    m = tms._measurements(d)
    assert 34.9 < m["volume_m3"] < 35.1
    assert 3640 < m["weight_kg"] < 3650


def test_v1_measurements_still_parse():
    d = {"measurements": [{"type": "volumeNett", "uom": "m3", "value": 12},
                          {"type": "weightNett", "uom": "kg", "value": 900}]}
    m = tms._measurements(d)
    assert m["volume_m3"] == 12.0 and m["weight_kg"] == 900.0


def test_pack_is_the_move_out_date_when_uplift_is_empty():
    """The mapper read only upliftStart, which V2 never populates — so every job
    looked undated and the consolidation engine had nothing to plan."""
    _st, _fl, dates = tms.stage_for(V2_LIST_ROW, V2_DETAIL, dt.date(2026, 9, 15))
    assert dates["uplift"] == dt.date(2026, 8, 20)


def test_a_real_v2_job_is_fully_plannable():
    s = tms.build_shipment(V2_LIST_ROW, V2_DETAIL, today=dt.date(2026, 9, 15))
    assert s.volume_m3 == 35.0
    assert s.weight == 3644.0
    assert s.ready_date == dt.date(2026, 8, 20)
    assert s.planning_m3 == 35.0, "the engine plans on this — 0 means unplannable"
    assert s.extra["service"] == "FTL"
    assert s.milestones.get("booked") == dt.date(2026, 7, 28)


# ── addresses drive the hub, not just a label ────────────────────────────────


def test_destination_text_comes_from_the_addresses_block():
    s = tms.build_shipment(V2_LIST_ROW, V2_DETAIL, today=dt.date(2026, 9, 15))
    assert s.destination == "Brownsville, Texas"
    assert s.origin == "Guadalajara, Jalisco"


def test_an_import_resolves_its_hub():
    """v1 read `locations`; V2 sends `addresses`. With the destination text
    empty the hub lookup had nothing to match, so every TMS import landed in
    'Unassigned hub' and the lanes read 'Export → ?'."""
    from crossborder.models import Hub
    addr = {"origin": {"country": "United States", "city": "Houston", "state": "Texas"},
            "destination": {"country": "Mexico", "city": "Zapopan", "state": "Jalisco"}}
    detail = {**V2_DETAIL, "addresses": addr}
    row = {**V2_LIST_ROW, "addresses": addr}
    s = tms.build_shipment(row, detail, today=dt.date(2026, 9, 15))
    assert s.destination == "Zapopan, Jalisco"
    assert s.destination_hub is Hub.GUADALAJARA


def test_an_export_lane_names_the_state():
    from crossborder import engine
    s = tms.build_shipment(V2_LIST_ROW, V2_DETAIL, today=dt.date(2026, 9, 15))
    assert engine.lane_for(s) == ("Export → Texas", "Texas")


def test_v1_locations_still_win_when_present():
    detail = {**V2_DETAIL,
              "locations": {"destination": {"city": "Monterrey", "state": "Nuevo Leon"}}}
    s = tms.build_shipment(V2_LIST_ROW, detail, today=dt.date(2026, 9, 15))
    assert s.destination == "Monterrey, Nuevo Leon"


# ── ports and agents ─────────────────────────────────────────────────────────


def test_a_string_port_does_not_take_the_pull_down():
    """v1 sent {"name": ...}; V2 sends "USBOS". Calling .get() on that string
    raised AttributeError inside the walk and emptied the whole TMS half of the
    board — and it only surfaced once addresses started resolving at all."""
    addr = {"origin": {"country": "United States", "iso2country": "US",
                       "city": "Boston", "state": "Massachusetts", "port": "USBOS"},
            "destination": {"country": "Mexico", "iso2country": "MX",
                            "city": "Veracruz", "state": "Veracruz", "port": "MXVER"}}
    detail = {**V2_DETAIL, "addresses": addr}
    row = {**V2_LIST_ROW, "addresses": addr}
    s = tms.build_shipment(row, detail, today=dt.date(2026, 9, 15))
    assert s.extra["origin_port"] == "USBOS"
    assert s.extra["destination_port"] == "MXVER"


def test_a_v1_object_port_still_reads():
    assert tms._named({"name": "Port of Houston"}) == "Port of Houston"
    assert tms._named("USBOS") == "USBOS"
    assert tms._named(None) == ""


def test_iso2country_is_preferred_over_the_spelled_out_name():
    """No name table needed when the API hands over the code."""
    assert tms._country({"country": "United States of Nowhere", "iso2country": "US"}) == "US"


def test_country_codes_reach_the_extra_block():
    s = tms.build_shipment(V2_LIST_ROW, V2_DETAIL, today=dt.date(2026, 9, 15))
    assert s.extra["origin_country"] == "MX"
    assert s.extra["destination_country"] == "US"
