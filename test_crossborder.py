"""Offline tests for the cross-border unified model + ClickUp normalizer."""
import datetime as dt

from crossborder import clickup
from crossborder.models import (
    Hub, Stage, hub_for_destination, parse_date, volume_to_m3,
)

FIELDS = [
    {"id": "f-ref", "name": "Reference Number", "type": "short_text"},
    {"id": "f-agent", "name": "Agent", "type": "short_text"},
    {"id": "f-orig", "name": "Origin", "type": "short_text"},
    {"id": "f-dest", "name": "Destination", "type": "location"},
    {"id": "f-vol", "name": "Volume (cuft)", "type": "number"},
    {"id": "f-wt", "name": "Weight", "type": "number"},
    {"id": "f-lv", "name": "# Lift Vans", "type": "number"},
    {"id": "f-ub", "name": "# U-Boxes", "type": "number"},
    {"id": "f-loc", "name": "Current Location / Warehouse", "type": "drop_down",
     "type_config": {"options": [{"id": "o1", "name": "McAllen", "orderindex": 0},
                                 {"id": "o2", "name": "Monterrey", "orderindex": 1}]}},
    {"id": "f-clr", "name": "Estimated Clearance Date", "type": "date"},
    {"id": "f-rdy", "name": "Ready Date", "type": "date"},
    {"id": "f-dlv", "name": "Delivery Date", "type": "date"},
]

TASK = {
    "id": "abc123",
    "name": "Garcia, Maria",
    "status": {"status": "In Transit to Border"},
    "date_updated": "1788000000000",
    "due_date": None,
    "start_date": None,
    "url": "https://app.clickup.com/t/abc123",
    "assignees": [{"username": "Fernanda"}],
    "tags": [{"name": "on hold"}],
    "custom_fields": [
        {"id": "f-ref", "type": "short_text", "value": "TIM-2026-041"},
        {"id": "f-agent", "type": "short_text", "value": "Allied Intl"},
        {"id": "f-orig", "type": "short_text", "value": "McAllen"},
        {"id": "f-dest", "type": "location", "value": {"formatted_address": "Zapopan, Jal., Mexico"}},
        {"id": "f-vol", "type": "number", "value": "706"},
        {"id": "f-wt", "type": "number", "value": "1200"},
        {"id": "f-lv", "type": "number", "value": "2"},
        {"id": "f-ub", "type": "number", "value": "1"},
        {"id": "f-loc", "type": "drop_down", "value": 1,
         "type_config": FIELDS[8]["type_config"]},
        {"id": "f-clr", "type": "date", "value": "1788825600000"},
        {"id": "f-dlv", "type": "date", "value": "1789948800000"},
    ],
}


def test_field_map_auto_matches_spec_fields():
    fmap = clickup.build_field_map(FIELDS)
    assert fmap["reference_number"]["id"] == "f-ref"
    assert fmap["destination"]["id"] == "f-dest"
    assert fmap["lift_vans"]["id"] == "f-lv"
    assert fmap["u_boxes"]["id"] == "f-ub"
    assert fmap["current_location"]["id"] == "f-loc"
    assert fmap["clearance_date"]["id"] == "f-clr"
    assert "customer_name" not in fmap          # falls back to task name


def test_task_normalizes_to_shipment():
    s = clickup.task_to_shipment(TASK, clickup.build_field_map(FIELDS))
    assert s.id == "TIM:abc123" and s.source.value == "TIM"
    assert s.reference_number == "TIM-2026-041"
    assert s.customer_name == "Garcia, Maria"
    assert s.destination_hub is Hub.GUADALAJARA
    assert s.current_location == "Monterrey"
    assert s.volume_m3 == 19.99                  # 706 cuft → m³
    assert s.lift_vans == 2 and s.u_boxes == 1
    assert s.lift_van_equivalents == 3.3
    assert s.stage is Stage.TO_BORDER
    assert s.status_flags == ["on_hold"]
    assert s.clearance_date == dt.date(2026, 9, 8)
    assert s.delivery_date == dt.date(2026, 9, 21)
    assert s.assignees == ["Fernanda"]
    d = s.to_dict()
    assert d["stage"] == "in_transit_to_border" and d["is_open"] is True


def test_stage_mapping_and_unknowns():
    assert clickup.stage_for_status("Docs Pending") is Stage.DOCS_PENDING
    assert clickup.stage_for_status("GREEN LIGHT ✅") is Stage.GREEN_LIGHT
    assert clickup.stage_for_status("Customs / Aduana") is Stage.CUSTOMS
    assert clickup.stage_for_status("Out for Delivery") is Stage.OUT_FOR_DELIVERY
    assert clickup.stage_for_status("Delivered") is Stage.DELIVERED
    assert clickup.stage_for_status("Something Odd") is Stage.UNKNOWN


def test_stage_override_env(monkeypatch):
    monkeypatch.setenv("CLICKUP_STAGE_MAP", '{"Something Odd": "at_hub"}')
    assert clickup.stage_for_status("Something Odd") is Stage.AT_HUB


def test_hub_lookup():
    assert hub_for_destination("CDMX") is Hub.MEXICO_CITY
    assert hub_for_destination("San Pedro Garza García, NL") is Hub.MONTERREY
    assert hub_for_destination("Cancún, Q. Roo") is Hub.MERIDA
    assert hub_for_destination("Querétaro") is Hub.QUERETARO
    assert hub_for_destination("Ciudad Juárez") is Hub.TORREON
    assert hub_for_destination("Nowhere Special") is Hub.UNKNOWN
    assert hub_for_destination("") is Hub.UNKNOWN


def test_unit_helpers():
    assert volume_to_m3("35.3 cuft") == 1.0
    assert volume_to_m3("12.5") == 12.5
    assert volume_to_m3(None) is None
    assert parse_date("1788825600000") == dt.date(2026, 9, 8)
    assert parse_date("2026-09-08T10:00:00Z") == dt.date(2026, 9, 8)
    assert parse_date("") is None


def test_webhook_signature():
    body = b'{"event":"taskUpdated"}'
    import hashlib
    import hmac
    sig = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert clickup.verify_signature(body, sig, "s3cret")
    assert not clickup.verify_signature(body, "bad", "s3cret")
    assert not clickup.verify_signature(body, sig, "")
