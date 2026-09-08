"""Offline tests for the Moveware/TMS reader (crossborder/tms.py)."""
import datetime as dt

from crossborder import tms
from crossborder.models import Hub, Stage

TODAY = dt.date(2026, 9, 8)

ROW = {"id": "110991", "name": "Eduardo Puentes / Marcelina Cid", "status": "W", "jobType": "IMA", "method": "Road",
       "origin": "US", "destination": "MX", "created": "2026-08-20T00:00:00+10:00", "lastUpdated": "2026-09-05T03:00:00+10:00",
       "jobDate": "", "salesRep": "GG", "moveManager": "Fernanda", "uplift": "2026-09-01", "delivery": "2026-09-20"}
DETAIL = {"id": "110991", "branchCode": "MTY", "branchName": "Thelsa Monterrey", "jobStatus": {"code": "W", "text": "Booked"},
          "currency": "USD", "billing": {"name": "EMBAJADA DE ESTADOS UNIDOS DE NORTEAMERICA"},
          "moveManager": {"code": "FER", "name": "Fernanda Lopez", "email": "fernanda@thelsa.com"},
          "upliftStart": {"dateTime": "2026-09-01T00:00:00+10:00"}, "deliveryStart": {"dateTime": ""},
          "customerType": {"code": "", "text": "Corporate"}, "method": {"code": "ROAD", "text": "ROAD"},
          "service": {"code": "LCL", "text": "LCL"}, "isClosed": "N",
          "locations": {"destination": {"address": {"suburb": "Guadalajara", "state": "Jalisco", "countryISO2": "MX",
                                                    "formattedAddress": "Guadalajara Jalisco Mexico"},
                                        "port": {"code": "MXVER", "name": "Veracruz"},
                                        "agent": {"name": "Thelsa Mobility Solutions"}},
                        "origin": {"address": {"suburb": "McAllen", "state": "TX", "countryISO2": "US"},
                                   "port": {"code": "", "name": ""}}},
          "measurements": [{"type": "items", "value": "42"}, {"type": "volumeNett", "uom": "m", "value": "18.5"},
                           {"type": "volumeNett", "uom": "ft", "value": "653"},
                           {"type": "volumeGross", "uom": "m", "value": "23.1"}, {"type": "weightNett", "uom": "lb", "value": "3527"},
                           {"type": "weightNett", "uom": "kg", "value": "1600"}, {"type": "actualWeight", "uom": "kg", "value": "5333"},
                           {"type": "actualWeight", "uom": "lb", "value": "5333"}],
          "notes": [{"comment": "Pendiente dirección de entrega.", "type": "crewNote"}, {"type": "internalNote"}],
          "extras": [{"field": "dtpacking", "value": "2026-09-01"}, {"field": "dtdelivery", "value": "2026-09-20"},
                     {"field": "dtopscomplete", "value": ""}, {"field": "loadtype", "value": "Loose"},
                     {"field": "debtortype", "value": "Agent"}, {"field": "revenue", "value": "7504.68"}]}


def test_direction_and_country():
    assert tms.direction({"origin": "US", "destination": "MX"}) == "import"
    assert tms.direction({"origin": "MX", "destination": "US"}) == "export"
    assert tms.direction({"origin": "US", "destination": "US"}) is None
    assert tms.direction({"origin": "SG", "destination": "MX"}) is None
    assert tms._country({"country": "mx"}) == "MX"


def test_build_shipment_in_transit():
    s = tms.build_shipment(ROW, DETAIL, today=TODAY)
    assert s.id == "TMS:110991" and s.source.value == "TMS" and s.reference_number == "110991"
    assert s.customer_name == "Eduardo Puentes / Marcelina Cid" and s.agent.startswith("EMBAJADA")   # payer is the agent
    assert s.destination == "Guadalajara, Jalisco" and s.destination_hub is Hub.GUADALAJARA
    assert s.origin == "McAllen, TX"
    assert s.volume_m3 == 18.5 and s.weight == 1600.0        # weightNett kg beats derived actualWeight
    assert s.extra["sale_value"] == 7504.68 and s.extra["service"] == "LCL" and s.extra["note"].startswith("Pendiente")
    assert s.extra["destination_port"] == "Veracruz" and s.extra["payer"].startswith("EMBAJADA")
    assert s.stage is Stage.TO_BORDER and s.ready_date == dt.date(2026, 9, 1) and s.delivery_date == dt.date(2026, 9, 20)
    assert s.assignees == ["Fernanda Lopez"] and s.extra["coordinator_email"] == "fernanda@thelsa.com"
    assert s.extra["direction"] == "import" and s.extra["items"] == 42 and s.extra["load_type"] == "Loose"
    assert "stalled" not in s.status_flags          # updated 3 days ago
    d = s.to_dict()
    assert d["milestones"]["uplift"] == "2026-09-01" and d["is_open"]


def test_stage_rules():
    booked = tms.build_shipment({**ROW, "uplift": "2026-09-15", "lastUpdated": "2026-08-01"}, {**DETAIL, "upliftStart": {"dateTime": "2026-09-15T00:00:00+10:00"}, "extras": []}, today=TODAY)
    assert booked.stage is Stage.BOOKED and "stalled" in booked.status_flags
    ofd = tms.build_shipment(ROW, {**DETAIL, "extras": [{"field": "dtdelivery", "value": "2026-09-09"}]}, today=TODAY)
    assert ofd.stage is Stage.OUT_FOR_DELIVERY
    delivered = tms.build_shipment(ROW, {**DETAIL, "extras": [{"field": "dtdelivery", "value": "2026-09-03"}]}, today=TODAY)
    assert delivered.stage is Stage.DELIVERED and delivered.milestones["delivered"] == dt.date(2026, 9, 3)
    closed = tms.build_shipment(ROW, {**DETAIL, "extras": [{"field": "dtopscomplete", "value": "2026-08-01"}]}, today=TODAY)
    assert closed.stage is Stage.CLOSED and not closed.is_open
    lost = tms.build_shipment({**ROW, "status": "L"}, None, today=TODAY)
    assert lost.stage is Stage.CLOSED
    flagged_closed = tms.build_shipment(ROW, {**DETAIL, "isClosed": "Y"}, today=TODAY)
    assert flagged_closed.stage is Stage.CLOSED
    delivered2 = tms.build_shipment(ROW, {**DETAIL, "deliveryStart": {"dateTime": "2026-09-06T00:00+10:00"}, "extras": []}, today=TODAY)
    assert delivered2.stage is Stage.DELIVERED
    bare = tms.build_shipment({**ROW, "uplift": "", "delivery": ""}, None, today=TODAY)
    assert bare.stage is Stage.BOOKED and bare.customer_name == "Eduardo Puentes / Marcelina Cid" and bare.destination == "MX"
    assert bare.agent == "TMS"


def test_measurements_prefer_nett_and_convert_units():
    m = tms._measurements({"measurements": [{"type": "volumeGross", "uom": "ft", "value": "706"},
                                            {"type": "weightNett", "uom": "lb", "value": "2204"}]})
    assert m["volume_m3"] == 19.99 and m["weight_kg"] == 999.7
    m2 = tms._measurements({"measurements": [{"type": "actualWeight", "uom": "kg", "value": "5333"},
                                             {"type": "weightGross", "uom": "kg", "value": "3400"}]})
    assert m2["weight_kg"] == 5333.0
    assert tms._measurements({})["volume_m3"] is None


def test_walk_slices_filters_and_details():
    class Fake:
        env = "test"; base_url = "x"; requests_made = 0; errors = []
        calls = []
        def jobs(self, page=1, limit=50, **f):
            self.calls.append((page, f))
            if page > 1:
                return []
            return [ROW, {**ROW, "id": "2", "origin": "US", "destination": "US"},
                    {**ROW, "id": "3", "origin": "MX", "destination": "US", "status": "L"},
                    {**ROW, "id": "4", "origin": "MX", "destination": "CA"}]
        def job(self, jid):
            return DETAIL if jid == "110991" else {}
    ships, diag = tms.fetch_tms_shipments(Fake(), days=20, slice_days=10, today=TODAY)
    assert diag["rows_seen"] == 4 and diag["cross_border"] == 2 and diag["by_direction"] == {"import": 1, "export": 1}
    assert diag["by_lane"]["US→MX"] == 1 and diag["by_status"] == {"W": 3, "L": 1}
    assert len(diag["slices"]) == 2 and diag["slices"][0]["from"] == "2026-08-29"
    assert sorted(s.source_ref for s in ships) == ["110991", "4"]
    ships.sort(key=lambda s: s.source_ref)
    assert ships[0].destination_hub is Hub.GUADALAJARA and ships[1].destination_hub is Hub.UNKNOWN
    assert diag["details_fetched"] == 1 and "extras_fields" in diag
