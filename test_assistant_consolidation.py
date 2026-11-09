"""Daily consolidation reminder (Bill, 9 Nov 2026): Elizabeth, Sara, Fernanda,
Lupita, Mario and Gustavo are reminded every day to check the consolidation
dashboard; the to-do is worded from the dashboard's own plan."""
import datetime as dt

from assistant import consolidation as C, todo

D = dt.date(2026, 11, 10)
LISTED = {"elizabethhernandez@thelsa.com", "sarareyes@thelsa.com", "fernandamora@thelsa.com",
          "maria.gonzalez@thelsa.com", "mariotorres@thelsa.com", "gustavogonzalez@thelsa.com"}


def test_the_six_people_are_listed_and_it_is_on():
    todo._CACHE.clear()
    assert set(todo.people("consolidation")) == LISTED
    assert C.enabled()


def test_worded_from_the_plan_with_opportunities():
    todo._CACHE.clear()
    p = {"opportunities": [{"lane": "LRD→MEX", "spare_m3": 40.2, "coming": [{"id": "a"}, {"id": "b"}]},
                           {"lane": "LRD→GDL", "spare_m3": 20, "coming": []}],
         "summary": {"light": 2}, "ready": 7, "coming": 3}
    it = C.item_from_plan(p, D)
    assert it["id"] == "consolidate:2026-11-10" and set(it["team"]) == LISTED
    assert "2 trailer(s)" in it["title"]["en"] and "60 m³" in it["title"]["en"]
    assert "2 shipment(s)" in it["detail"]["en"] and "LRD→MEX" in it["detail"]["en"]
    assert it["urgency"] == 74 and it["link"].endswith("/crossborder")


def test_still_reminds_when_nothing_to_combine():
    it = C.item_from_plan({"ready": 4, "coming": 1, "summary": {}}, D)
    assert it["title"]["en"].startswith("Check the consolidation dashboard") and it["urgency"] == 48


def test_only_the_listed_people_get_it():
    todo._CACHE.clear()
    parsed = todo.parse_feed(C.PRODUCT, todo.feed_payload(C.PRODUCT, [C.item_from_plan({}, D)]))
    assert parsed["items"], parsed
    assert len(todo.for_user(parsed["items"], {"sarareyes@thelsa.com"})) == 1
    assert len(todo.for_user(parsed["items"], {"mariotorres@thelsa.com"})) == 1
    assert todo.for_user(parsed["items"], {"bbrill@thelsa.com"}) == []
    assert todo.for_user(parsed["items"], {"pablomartinez@thelsa.com"}) == []


def test_a_new_one_every_day():
    assert C.item_from_plan({}, D)["id"] != C.item_from_plan({}, D + dt.timedelta(days=1))["id"]
