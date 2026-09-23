"""
The three facts the team agreed on 23 Sep to write down in words, and the
customs-clearance rule that finally lets a file leave stage 1.

Every case here is phrased the way a coordinator would actually type it —
mixed Spanish and English, accents dropped, no fixed format — because that is
what the crew notes and ClickUp comments really look like.
"""
import datetime as dt

from crossborder import markers
from crossborder.models import Shipment, Source, Stage

TODAY = dt.date(2026, 9, 23)


def ship(source=Source.TMS, **kw):
    base = dict(id="TMS:1", source=source, source_ref="1")
    base.update(kw)
    return Shipment(**base)


# ── port of entry (D16 / D17) ────────────────────────────────────────────────
def test_the_label_the_team_was_asked_to_use():
    assert markers.port_of_entry("port of entry: McAllen") == "McAllen"
    assert markers.port_of_entry("Port of Entry - Laredo") == "Laredo"


def test_the_way_people_actually_write_it():
    assert markers.port_of_entry("cruce por Laredo, entrega en CDMX") == "Laredo"
    assert markers.port_of_entry("puerto de entrada: mcallen") == "McAllen"
    assert markers.port_of_entry("despacho en Nuevo Laredo") == "Laredo"


def test_the_mexican_side_of_the_crossing_means_the_same_trailer():
    """Reynosa is the far side of the McAllen bridge — same freight, same
    trailer. Splitting them would invent a lane that does not exist."""
    assert markers.port_of_entry("cruce por Reynosa") == "McAllen"
    assert markers.port_of_entry("aduana Hidalgo") == "McAllen"


def test_a_customer_city_in_a_long_note_does_not_reassign_the_file():
    note = ("consolidado con Maria Gonzalez, Juan Perez, la familia Ruiz que "
            "viene de Laredo Texas y tres mas, sale el viernes de la bodega "
            "rumbo a Monterrey con entrega en Guadalajara la semana siguiente")
    assert markers.port_of_entry(note) == ""


def test_silence_is_not_a_port():
    assert markers.port_of_entry("") == ""
    assert markers.port_of_entry("empaque programado para el jueves") == ""


def test_tim_files_are_mcallen_because_fernanda_never_uses_laredo():
    s = ship(source=Source.TIM, id="TIM:9")
    assert s.port_of_entry == "McAllen"
    assert s.port_is_assumed is True


def test_a_tms_file_claims_no_port_until_somebody_writes_one():
    """The switch to McAllen is the thing being measured. Assuming it happened
    would measure nothing."""
    s = ship()
    assert s.port_of_entry == ""
    s.extra = {"port_of_entry": "McAllen"}
    assert s.port_of_entry == "McAllen"
    assert s.port_is_assumed is False


# ── door to door (D22) ───────────────────────────────────────────────────────
def test_the_marker_fernanda_agreed_to_write():
    assert markers.door_to_door("this is a door-to-door service") is True
    assert markers.door_to_door("servicio puerta a puerta") is True


def test_a_denial_is_read_as_a_denial():
    assert markers.door_to_door("no es puerta a puerta") is False
    assert markers.door_to_door("not door to door, delivers to our warehouse") is False


def test_saying_nothing_stays_unknown_rather_than_becoming_false():
    """The rule this replaces guessed from the checklist length and was wrong.
    Unknown has to stay unknown."""
    assert markers.door_to_door("empaque el martes") is None


def test_the_shipment_honours_the_written_marker():
    s = ship()
    assert s.is_door_to_door is False
    markers.apply_markers(s, ["servicio puerta a puerta"])
    assert s.is_door_to_door is True


# ── load type (D22) ──────────────────────────────────────────────────────────
def test_load_type_is_read_from_the_words():
    assert markers.load_type("3 lift vans") == "liftvan"
    assert markers.load_type("2 U-Boxes") == "ubox"
    assert markers.load_type("carga suelta") == "loose"
    assert markers.load_type("loose loaded") == "loose"


def test_counts_on_the_file_beat_anything_written_in_a_note():
    s = ship(u_boxes=2)
    markers.apply_markers(s, ["loose load"])
    assert s.load_type == "ubox"


def test_a_fifteen_cubic_metre_shipment_is_not_guessed():
    """It could be any of the three. Fernanda said so; the board does not
    pretend otherwise."""
    s = ship(volume_m3=15.0)
    assert s.load_type == ""


# ── the crossing rule (D18) ──────────────────────────────────────────────────
def test_a_clearance_date_in_the_past_means_it_has_crossed():
    """Bill's ruling. This is the single change that lets stage 2 exist."""
    s = ship(stage=Stage.TO_BORDER, clearance_date=dt.date(2026, 9, 1))
    assert s.has_crossed is True


def test_a_clearance_date_in_the_future_does_not():
    s = ship(stage=Stage.TO_BORDER, clearance_date=dt.date(2099, 1, 1))
    assert s.has_crossed is False


def test_no_clearance_date_falls_back_to_the_stage():
    s = ship(stage=Stage.TO_BORDER)
    assert s.has_crossed is False
    assert ship(stage=Stage.AT_HUB).has_crossed is True


def test_all_three_markers_come_out_of_one_crew_note():
    s = ship()
    markers.apply_markers(
        s, ["port of entry: McAllen | puerta a puerta | 3 lift vans"])
    assert s.port_of_entry == "McAllen"
    assert s.is_door_to_door is True
    assert s.load_type == "liftvan"


def test_applying_markers_leaves_everything_else_alone():
    s = ship(extra={"note": "keep me", "folder": "Impo"})
    markers.apply_markers(s, ["port of entry: Laredo"])
    assert s.extra["note"] == "keep me"
    assert s.extra["folder"] == "Impo"
    assert s.extra["port_of_entry"] == "Laredo"
