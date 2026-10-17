"""Missing Moveware key dates on the coordinator's assistant dashboard.

Same rows as the daily missing-dates email (faim_web date_gaps), so the email
and the dashboard can never disagree.
"""
from assistant import i18n, moveware, priority

GAPS = [
    {"file": "412042", "type": "XP", "coord": "Sara", "coord_email": "SaraReyes@thelsa.com",
     "missing": ["pack", "delivery"], "sit_pack": "2026-10-20", "sit_delivery": ""},
    {"file": "412050", "type": "EXA", "coord": "Sara", "coord_email": "sarareyes@thelsa.com",
     "missing": ["pack"], "no_dates": True, "booked": "2026-09-30"},
    {"file": "412060", "type": "IMP", "coord": "Sara", "coord_email": "sarareyes@thelsa.com",
     "missing": [], "wrong_year": ["delivery"], "dates": {"delivery": "2020-11-03"}},
    {"file": "499999", "type": "XP", "coord": "Other", "coord_email": "other@thelsa.com",
     "missing": ["pack"]},
    {"file": "499998", "type": "XP", "coord": "Unassigned", "coord_email": "", "missing": ["pack"]},
]
SARA = {"sarareyes@thelsa.com"}


def test_only_the_coordinators_own_gaps():
    items = moveware.tasks_for_date_gaps(GAPS, SARA)
    assert sorted(i["meta"]["job"] for i in items) == ["412042", "412050", "412060"]
    assert {i["kind"] for i in items} == {"enter_dates"}
    assert {i["external_id"] for i in items} == {"412042:dates", "412050:dates", "412060:dates"}


def test_watch_list_sees_coordinators_gaps():
    items = moveware.tasks_for_date_gaps(GAPS, SARA | {"other@thelsa.com"})
    assert "499999" in {i["meta"]["job"] for i in items}


def test_no_email_goes_nowhere_on_the_dashboard():
    # No coordinator email: the daily email routes it to the fallback inbox.
    assert not [i for i in moveware.tasks_for_date_gaps(GAPS, {""}) if i["meta"]["job"] == "499998"]


def test_text_both_languages():
    items = {i["meta"]["job"]: i for i in moveware.tasks_for_date_gaps(GAPS, SARA)}
    s, n = i18n.item_text(items["412042"], "es")
    assert "412042" in s and "Plan de Viajes: 2026-10-20" in n and "fecha de entrega" in n
    s, n = i18n.item_text(items["412042"], "en")
    assert s.startswith("Enter Moveware dates") and "SIT plan: 2026-10-20" in n
    s, n = i18n.item_text(items["412050"], "es")
    assert "SIN NINGUNA" in n and "entrega" not in n          # EXA: no delivery expected
    s, n = i18n.item_text(items["412060"], "en")
    assert s.startswith("Fix Moveware date") and "2020-11-03" in n


def test_always_urgent_even_when_seen():
    for it in moveware.tasks_for_date_gaps(GAPS, SARA):
        for seen in (False, True):
            sc = priority.score({**it, "seen": seen})
            assert priority.tier(sc) == "urgent"
    worst = [i for i in moveware.tasks_for_date_gaps(GAPS, SARA) if i["meta"]["no_dates"]][0]
    plain = [i for i in moveware.tasks_for_date_gaps(GAPS, SARA) if i["meta"]["job"] == "412042"][0]
    assert priority.score(worst) > priority.score(plain)


def test_half_read_book_keeps_previous_items(monkeypatch):
    prev = [{"kind": "enter_dates", "external_id": "1:dates", "from_name": "Moveware", "from_addr": None,
             "subject": "s", "snippet": "n", "url": None, "received_at": None, "meta": '{"job": "1"}'},
            {"kind": "invoice_file", "external_id": "2:invoice", "meta": "{}"}]
    from assistant import db
    monkeypatch.setattr(moveware, "_date_gaps", lambda: (GAPS, False))
    monkeypatch.setattr(db, "list_items", lambda uid, src: prev)
    out = moveware._gap_items({"id": "u1"}, SARA)
    assert [i["external_id"] for i in out] == ["1:dates"] and out[0]["meta"] == {"job": "1"}


def test_full_read_replaces(monkeypatch):
    monkeypatch.setattr(moveware, "_date_gaps", lambda: (GAPS, True))
    out = moveware._gap_items({"id": "u1"}, SARA)
    assert len(out) == 3


def test_book_unreadable_keeps_previous(monkeypatch):
    from assistant import db
    monkeypatch.setattr(moveware, "_date_gaps", lambda: (None, False))
    monkeypatch.setattr(db, "list_items", lambda uid, src: [])
    assert moveware._gap_items({"id": "u1"}, SARA) == []
