"""ClickUp references starting "AA" are U-Box jobs (Bill, 2026-09-21).

U-Box: exterior 96" × 60" × 90", usable 257 cu ft / 7.3 m³, max contents 2,000 lb.
"""
import datetime as dt

import pytest

from crossborder import engine, models, tim
from crossborder.models import Hub, Shipment, Source, Stage

TODAY = dt.date(2026, 9, 21)


def job(ref, m3=None, ub=None):
    return Shipment(id=f"TIM:{ref}", source=Source.TIM, source_ref=ref, reference_number=ref,
                    customer_name="C", destination="Monterrey", destination_hub=Hub.MONTERREY,
                    stage=Stage.TO_BORDER, volume_m3=m3, u_boxes=ub,
                    milestones={"green_light": dt.date(2026, 9, 15)})


def test_ubox_constants_match_the_spec():
    assert (models.U_BOX_M3, models.U_BOX_CUFT, models.U_BOX_MAX_LB) == (7.3, 257, 2000)
    assert models.U_BOX_EXTERIOR_IN == (96, 60, 90)


@pytest.mark.parametrize("ref,expect", [
    ("AA123456", True), ("aa-99", True), (" AA77 ", True),
    ("121722", False), ("TIM-48007-26", False), ("BAA1", False), ("", False),
])
def test_aa_reference_marks_a_ubox_job(ref, expect):
    assert job(ref).is_ubox_job is expect


def test_reference_comes_through_from_the_clickup_list_name():
    meta = tim.parse_list_name("Rosa Molina - UHaul - AA123456")
    assert meta["reference"] == "AA123456" and job(meta["reference"]).is_ubox_job


@pytest.mark.parametrize("m3,ub,boxes", [
    (None, 3, 3),        # explicit count wins
    (14.6, None, 2),     # 2 × 7.3
    (7.0, None, 1),
    (3.0, None, 1),      # a U-Box job is at least one box
    (None, None, 0),     # unknown — never guessed
])
def test_u_boxes_planned(m3, ub, boxes):
    assert job("AA1", m3=m3, ub=ub).u_boxes_planned == boxes


def test_non_aa_job_without_count_is_not_a_ubox_job():
    assert job("121722", m3=14.6).u_boxes_planned == 0


def test_engine_names_the_ubox_count():
    it = engine.make_item(job("AA1", m3=14.6), TODAY)
    assert any("U-Box job — 2 U-Boxes" in r for r in it.reasons)


def test_engine_says_why_an_unsized_ubox_job_cannot_be_planned():
    it = engine.make_item(job("AA1"), TODAY)
    assert not it.sized
    assert any("U-Box job (AA reference)" in r for r in it.reasons)


def test_to_dict_exposes_ubox_fields():
    d = job("AA1", ub=2).to_dict()
    assert d["is_ubox_job"] is True and d["u_boxes_planned"] == 2


def test_load_rows_carry_the_ubox_flag():
    p = engine.plan([job("AA1", ub=2)], today=TODAY)
    row = p["loads"][0]["shipments"][0]
    assert row["ubox"] is True and row["u_boxes"] == 2
