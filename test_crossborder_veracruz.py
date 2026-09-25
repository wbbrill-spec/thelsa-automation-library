"""
Veracruz — the sea gate (Bill, 25 Sep 2026).

"All sea freight shipments go in or out of Veracruz. Very few exceptions."

That sentence is what makes this cheap. The port of entry is nowhere in a
Moveware job: a container cleared at Veracruz and delivered to Guadalajara
records Guadalajara. But if sea freight IS Veracruz, the shipping method
identifies the port, and the shipping method is on the list row — so the feed
widens without the walk getting any longer.

Two things were also simply wrong before this:
  • Veracruz sat in the Mexico City keyword list, so Veracruz freight and the
    TRS trucks serving it were reported as Mexico City. Three live trucks and
    176 m³ of "Mexico City" spare were Veracruz's.
  • Sea jobs never reached the board at all — the Moveware reader kept only
    US↔MX, so Malaysia→Mexico and Mexico→Brazil were dropped before detail.
"""
import datetime as dt

from crossborder import engine
from crossborder.models import Hub, Leg, Shipment, Source, hub_for_destination
from crossborder.tms import is_sea_freight, sea_direction, veracruz_leg

TODAY = dt.date(2026, 9, 25)


def row(method="Sea", service="FCL 20", type_="IMP", origin="Malaysia", dest="Mexico"):
    return {"id": "1", "method": method, "service": service, "type": type_,
            "addresses": {"origin": {"country": origin, "city": ""},
                          "destination": {"country": dest, "city": ""}}}


def ship(**kw):
    base = dict(id="TMS:1", source=Source.TMS, source_ref="1")
    base.update(kw)
    return Shipment(**base)


# ── the hub, which was wrong ─────────────────────────────────────────────────
def test_veracruz_is_its_own_hub_not_mexico_city():
    """The San Luis Potosí mistake, repeated and now corrected."""
    assert hub_for_destination("Veracruz, Ver.") is Hub.VERACRUZ
    assert hub_for_destination("BODEGA THELSA VERACRUZ") is Hub.VERACRUZ
    assert hub_for_destination("Boca del Rio") is Hub.VERACRUZ
    assert hub_for_destination("Coatzacoalcos") is Hub.VERACRUZ


def test_mexico_city_keeps_what_is_actually_its_own():
    assert hub_for_destination("CDMX") is Hub.MEXICO_CITY
    assert hub_for_destination("Puebla") is Hub.MEXICO_CITY
    assert hub_for_destination("Cuernavaca, Morelos") is Hub.MEXICO_CITY


def test_a_borough_seen_on_a_live_veracruz_trip_no_longer_falls_through():
    """"BODEGA THELSA VERACRUZ → CUAJIMALPA", 50 m³, was landing in Unknown."""
    assert hub_for_destination("CUAJIMALPA") is Hub.MEXICO_CITY


def test_the_far_ends_of_the_state_are_not_claimed_blindly():
    """Veracruz state is long and thin; the port warehouse does not serve all
    of it. Better Unknown than a confident wrong hub."""
    assert hub_for_destination("Panuco") is Hub.UNKNOWN


# ── recognising sea freight ──────────────────────────────────────────────────
def test_a_container_is_sea_freight():
    for svc in ("FCL 20", "FCL40", "FCL40+40HC", "LCL", "Container 40"):
        assert is_sea_freight(row(service=svc)) is True


def test_air_freight_is_not_sea_even_when_the_method_says_sea():
    """Two of eight live rows said method Sea and service Air. Method alone
    over-captures, so the container wins."""
    assert is_sea_freight(row(method="Sea", service="Air")) is False
    assert is_sea_freight(row(method="SEA", service="Air")) is False


def test_road_freight_is_not_sea():
    assert is_sea_freight(row(method="Road", service="")) is False


def test_method_alone_still_counts_when_service_is_silent():
    assert is_sea_freight(row(method="Sea", service="")) is True


# ── which way through the port ───────────────────────────────────────────────
def test_moveware_type_code_decides_the_direction():
    assert sea_direction(row(type_="IMP")) == "import"
    assert sea_direction(row(type_="IMA")) == "import"
    assert sea_direction(row(type_="EXP")) == "export"


def test_countries_decide_when_the_type_code_is_blank():
    assert sea_direction(row(type_="", origin="United Kingdom", dest="Mexico")) == "import"
    assert sea_direction(row(type_="", origin="Mexico", dest="Brazil")) == "export"


def test_an_arriving_container_becomes_a_truck_out_of_the_port():
    assert veracruz_leg(row(type_="IMP")) == "veracruz_out"


def test_a_departing_container_becomes_a_truck_into_the_port():
    assert veracruz_leg(row(type_="EXP")) == "veracruz_in"


def test_road_freight_creates_no_veracruz_leg():
    assert veracruz_leg(row(method="Road", service="")) is None


# ── how it plans ─────────────────────────────────────────────────────────────
def test_sea_freight_is_planned_on_its_own_leg_not_the_land_crossing():
    s = ship(extra={"veracruz_leg": "veracruz_out", "is_sea": True},
             destination_hub=Hub.GUADALAJARA)
    assert engine.leg_for(s) is Leg.PORT


def test_freight_leaving_the_country_shares_one_lane_into_the_port():
    """Same idea as the McAllen trailer: everything meeting the vessel travels
    together, whatever city it started in."""
    a = ship(extra={"veracruz_leg": "veracruz_in", "is_sea": True}, destination_hub=Hub.VERACRUZ)
    b = ship(id="TMS:2", source_ref="2", extra={"veracruz_leg": "veracruz_in", "is_sea": True},
             destination_hub=Hub.VERACRUZ, origin="Monterrey")
    assert engine.lane_for(a)[0] == engine.lane_for(b)[0]
    assert "Veracruz" in engine.lane_for(a)[0]


def test_freight_arriving_splits_by_the_city_it_is_going_to():
    gdl = ship(extra={"veracruz_leg": "veracruz_out", "is_sea": True}, destination_hub=Hub.GUADALAJARA)
    cdmx = ship(id="TMS:3", source_ref="3", extra={"veracruz_leg": "veracruz_out", "is_sea": True},
                destination_hub=Hub.MEXICO_CITY)
    assert engine.lane_for(gdl)[0] != engine.lane_for(cdmx)[0]
    assert engine.lane_for(gdl)[0].startswith("Veracruz →")


def test_a_sea_shipment_never_lands_on_the_mcallen_crossing_lane():
    """The bug this guards: sea freight inheriting the land-border plan."""
    s = ship(extra={"veracruz_leg": "veracruz_out", "is_sea": True}, destination_hub=Hub.MEXICO_CITY)
    lane, _region, leg = engine.lane_for(s)
    assert leg is Leg.PORT
    assert "McAllen" not in lane


def test_land_freight_is_untouched_by_any_of_this():
    s = ship(extra={"direction": "import"}, destination_hub=Hub.MEXICO_CITY)
    assert engine.leg_for(s) is Leg.CROSSING
    assert "McAllen" in engine.lane_for(s)[0]


# ── the screen, which was silently discarding all of it ──────────────────────
def test_a_sea_container_is_planned_because_its_inland_leg_is_a_truck():
    """The bug that made this land as 45 shipments and 0 loads: screen()
    rejected anything whose method was not road, so every container was
    discarded as 'not truck freight'. Correct before — a container is not a
    truck — and exactly wrong once the port leg is the thing being planned."""
    s = ship(stage=__import__("crossborder.models", fromlist=["x"]).Stage.BOOKED,
             volume_m3=42.0, destination_hub=Hub.VERACRUZ,
             extra={"veracruz_leg": "veracruz_in", "is_sea": True,
                    "method": "SEA", "service": "FCL40"})
    assert engine.screen(s) == ""


def test_air_freight_is_still_not_planned():
    from crossborder.models import Stage
    s = ship(stage=Stage.BOOKED, volume_m3=10.0, destination_hub=Hub.MEXICO_CITY,
             extra={"method": "AIR", "service": "Air"})
    assert "not truck freight" in engine.screen(s)


def test_road_freight_is_screened_exactly_as_before():
    from crossborder.models import Stage
    s = ship(stage=Stage.BOOKED, volume_m3=10.0, destination_hub=Hub.MEXICO_CITY,
             extra={"direction": "import", "method": "", "service": ""})
    assert engine.screen(s) == ""
