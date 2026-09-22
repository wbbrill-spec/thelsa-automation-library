"""Fernanda's real consolidation note (ClickUp, 22 Sep 2026).

The note she actually wrote, on the list "Ana Saldivar - GoArmstrong - …",
as a COMMENT ON STEP 5 ("Confirmar Recepción en Bodega"):

    importación junto con:
     jorge loredo, sandra guadalupe, carol ann ashworth, christian latino,
     orlando perez, daniel moya, giovanna bartel

Two things about that shape drive the code under test:
  1. it is on a task, not on the list;
  2. she writes it ONCE and names the others, who carry nothing at all.
"""
import datetime as dt

import pytest

from crossborder import engine, grouping, tim
from crossborder.models import Hub, Shipment, Source, Stage

TODAY = dt.date(2026, 9, 22)
REAL_NOTE = ("importación junto con:\n jorge loredo, sandra guadalupe, carol ann ashworth, "
             "christian latino, orlando perez, daniel moya, giovanna bartel")
OTHERS = ["Jorge Loredo", "Sandra Guadalupe", "Carol Ann Ashworth", "Christian Latino",
          "Orlando Perez", "Daniel Moya", "Giovanna Bartel"]


def ship(i, customer, *, source=Source.TIM, ref="", m3=8.0, stage=Stage.TO_BORDER,
         hub=Hub.MEXICO_CITY, note=None):
    s = Shipment(id=f"{source.value}:{i}", source=source, source_ref=str(i),
                 customer_name=customer, agent="GoArmstrong", reference_number=ref,
                 destination=hub.value, destination_hub=hub, stage=stage, volume_m3=m3,
                 ready_date=dt.date(2026, 9, 18),
                 milestones={"green_light": dt.date(2026, 9, 10)},
                 extra={"direction": "import", "method": "ROAD", "service": "LTL"})
    if note:
        grouping.apply_note(s, [note])
    return s


def test_her_note_is_read_and_every_name_survives():
    """Seven names. The old six-name cap dropped Giovanna Bartel silently."""
    p = grouping.parse_note(REAL_NOTE)
    assert p["grouped"] and not p["do_not_consolidate"]
    assert [n.lower() for n in p["with"]] == [
        "jorge loredo", "sandra guadalupe", "carol ann ashworth", "christian latino",
        "orlando perez", "daniel moya", "giovanna bartel"]


def test_the_note_is_found_on_a_task_comment_not_just_the_list():
    steps = [{"id": "t5", "name": "5.- Confirmar Recepción en Bodega", "parent": None},
             {"id": "t7", "name": "7.- Programar Importación y Cruce Fronterizo", "parent": None},
             {"id": "t1", "name": "1.- Presentarse con el Cliente", "parent": None}]
    picked = [t["id"] for t in tim.note_candidate_tasks(steps, "7.- Programar Importación y Cruce Fronterizo")]
    assert "t5" in picked and "t7" in picked and "t1" not in picked
    texts = grouping.note_texts({}, [{"id": "c1", "comment_text": REAL_NOTE}])
    assert grouping.parse_note(texts[0])["grouped"]


def test_the_seven_named_files_are_pulled_onto_the_same_truck():
    """She writes one note; the other seven files carry nothing at all.

    If the dashboard only grouped files that have a note, those seven would
    stay in the suggestions and the planner would propose a second truck for
    freight that is already on the first.
    """
    anchor = ship(1, "Ana Saldivar", ref="121722", note=REAL_NOTE)
    others = [ship(10 + n, name, ref=f"13000{n}") for n, name in enumerate(OTHERS)]
    unrelated = ship(99, "Zulema Ortiz", ref="140001")
    ships = [anchor] + others + [unrelated]

    diag = grouping.resolve_groups(ships)
    assert diag["notes"] == 1 and diag["groups"] == 1
    assert diag["matched"] == 7 and diag["unmatched"] == []
    assert diag["members"] == 8 and diag["linked"] == 7

    # every named file is now grouped, and says who with
    for s in [anchor] + others:
        assert s.is_grouped, s.customer_name
    assert not unrelated.is_grouped
    assert "TIM - 130000" in anchor.consolidated_with
    assert "TIM - 121722" in others[0].consolidated_with
    assert len(anchor.consolidated_with) == 7
    # a file named by somebody else's note says whose
    assert "Ana Saldivar" in others[0].consolidation["source"]


def test_a_moveware_file_named_in_a_clickup_note_is_pulled_in_too():
    """The cross-silo win: Sara's file joins Fernanda's truck."""
    anchor = ship(1, "Ana Saldivar", ref="121722", note="consolidado con Jorge Loredo")
    tms_file = ship(2, "Jorge Loredo", source=Source.TMS, ref="110719")
    grouping.resolve_groups([anchor, tms_file])
    assert tms_file.is_grouped
    assert anchor.consolidated_with == ["TMS - 110719"]
    assert tms_file.consolidated_with == ["TIM - 121722"]


def test_a_partial_name_still_matches_the_full_customer_name():
    anchor = ship(1, "Ana Saldivar", note="consolidado con jorge loredo")
    full = ship(2, "Jorge Loredo Martinez", ref="130044")
    grouping.resolve_groups([anchor, full])
    assert full.is_grouped and anchor.consolidated_with == ["TIM - 130044"]


def test_a_name_that_matches_nothing_is_reported_not_swallowed():
    """An unmatched name is the one thing worth interrupting somebody about:
    either that file is not in ClickUp or Moveware yet, or it is spelled
    differently. It must never be silently dropped."""
    anchor = ship(1, "Ana Saldivar", note="consolidado con Fantasma Inexistente")
    diag = grouping.resolve_groups([anchor, ship(2, "Zulema Ortiz")])
    assert diag["matched"] == 0 and len(diag["unmatched"]) == 1
    assert "Fantasma Inexistente" in diag["unmatched"][0]
    assert "best 0." in diag["unmatched"][0]          # says how close it got


def test_a_name_list_stops_at_the_words_around_it():
    """"con Ana Ruiz que sale el martes" is one name, not a sentence."""
    p = grouping.parse_note("consolidado con Ana Ruiz que sale el martes")
    assert p["with"] == ["Ana Ruiz"]


def test_two_notes_naming_each_other_are_one_truck_not_two():
    a = ship(1, "Ana Saldivar", note="consolidado con Jorge Loredo")
    b = ship(2, "Jorge Loredo", note="consolidado con Ana Saldivar")
    diag = grouping.resolve_groups([a, b])
    assert diag["groups"] == 1 and diag["members"] == 2
    assert grouping.group_key(a) == grouping.group_key(b)


def test_the_whole_group_leaves_the_suggestions_and_shows_as_one_truck():
    anchor = ship(1, "Ana Saldivar", ref="121722", note=REAL_NOTE)
    others = [ship(10 + n, name, ref=f"13000{n}") for n, name in enumerate(OTHERS)]
    spare = ship(99, "Zulema Ortiz", ref="140001")
    ships = [anchor] + others + [spare]
    grouping.resolve_groups(ships)
    p = engine.plan(ships, today=TODAY)

    planned = {s["id"] for l in p["loads"] for s in l["shipments"]}
    assert planned == {"TIM:99"}                       # only the ungrouped file
    assert len(p["groups"]) == 1
    g = p["groups"][0]
    assert g["customers"] == 8
    assert {m["label"] for m in g["members"]} >= {"TIM - 121722", "TIM - 130000"}
    assert [c["customer"] for c in g["could_join"]] == ["Zulema Ortiz"]
    # the file carrying the note is marked as such
    assert sum(1 for m in g["members"] if m["carrier"]) == 1


def test_an_unmatched_name_reaches_the_advice_line():
    anchor = ship(1, "Ana Saldivar", ref="121722",
                  note="consolidado con Jorge Loredo, Fantasma Inexistente")
    jorge = ship(2, "Jorge Loredo", ref="130044")
    ships = [anchor, jorge]
    grouping.resolve_groups(ships)
    g = engine.plan(ships, today=TODAY)["groups"][0]
    assert g["unmatched"] == ["Fantasma Inexistente"]
    assert "not found on the board" in g["advice"]
