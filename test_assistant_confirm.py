"""'Did it happen?' prompts and unassigned-file routing on the assistant dashboard."""
import datetime as dt
import time

import pytest

from assistant import i18n, moveware, mw_confirm, priority

TODAY = dt.date(2026, 10, 20)


def _f(job, pack=None, delivery=None, status="W", jt="XP", jid=None):
    return {"job": job, "job_id": jid or job, "status": status, "job_type": jt,
            "pack": pack, "delivery": delivery}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    mw_confirm._CACHE.clear()
    monkeypatch.setattr(mw_confirm, "refresh", lambda pairs, wait=False: True)
    yield
    mw_confirm._CACHE.clear()


def _cache(job, **kw):
    mw_confirm._CACHE[job] = {"load": None, "pack": None, "delivery": None, "has_load": True,
                              "at": time.time(), **kw}


def test_due_respects_window_status_and_job_type():
    files = [_f("1", pack="2026-10-18"),                       # due (load)
             _f("2", pack="2026-10-20"),                       # today: not yet
             _f("3", pack="2026-09-01"),                       # beyond look-back
             _f("4", pack="2026-10-18", status="P"),           # quote, not booked
             _f("5", delivery="2026-10-19", jt="EXA"),         # no delivery on EXA
             _f("6", pack="2026-10-19", jt="IMA2"),            # no load on IMA
             _f("7", delivery="2026-10-19", jt="IMA")]         # delivery on IMA: due
    got = {(j, ms) for j, _id, ms, _d, _t in mw_confirm.due(files, TODAY)}
    assert got == {("1", "load"), ("7", "delivery")}


def test_unread_diary_gives_no_item_but_queues_a_read(monkeypatch):
    asked = []
    monkeypatch.setattr(mw_confirm, "refresh", lambda pairs, wait=False: asked.extend(pairs))
    assert mw_confirm.items([_f("1", pack="2026-10-18")], TODAY) == []
    assert asked == [("1", "1")]


def test_open_action_flags_completed_clears():
    _cache("1")                                         # load not completed
    _cache("2", load="2026-10-18")                      # completed
    _cache("3", pack="2026-10-17", has_load=False)      # no Load action: completed Pack stands in
    files = [_f("1", pack="2026-10-18"), _f("2", pack="2026-10-18"), _f("3", pack="2026-10-17")]
    items = mw_confirm.items(files, TODAY)
    assert [i["meta"]["job"] for i in items] == ["1"]
    it = items[0]
    assert it["kind"] == "confirm_done" and it["external_id"] == "1:confirm:load"
    assert it["meta"]["days"] == 2


def test_completed_pack_does_not_count_when_load_exists():
    _cache("1", pack="2026-10-17", has_load=True)
    assert len(mw_confirm.items([_f("1", pack="2026-10-18")], TODAY)) == 1


def test_confirm_text_and_urgency():
    _cache("9")
    it = mw_confirm.items([_f("9", delivery="2026-10-12", jt="IMP")], TODAY)[0]
    s, n = i18n.item_text(it, "es")
    assert "entrega" in s and "2026-10-12" in n
    s, n = i18n.item_text(it, "en")
    assert s.startswith("Did the delivery happen?")
    assert priority.tier(priority.score({**it, "seen": True})) == "urgent"   # 8 days old
    young = {**it, "meta": {**it["meta"], "days": 1}}
    assert priority.tier(priority.score(young)) == "today"


GAPS = [{"file": "500", "type": "XP", "coord": "Unassigned", "coord_email": "", "missing": ["pack"]},
        {"file": "501", "type": "XP", "coord": "Sara", "coord_email": "sarareyes@thelsa.com",
         "missing": ["pack"]}]


def test_unassigned_items():
    items = moveware.tasks_for_unassigned(GAPS)
    assert [i["meta"]["job"] for i in items] == ["500"]
    s, n = i18n.item_text(items[0], "es")
    assert s.startswith("Asignar coordinador")
    assert priority.tier(priority.score(items[0])) == "urgent"


def test_unassigned_only_for_key_users(monkeypatch):
    monkeypatch.setattr(moveware, "_date_gaps", lambda: (GAPS, True))
    monkeypatch.delenv("DATE_ALERT_CC", raising=False)
    lupita = moveware._gap_items({"id": "u", "email": "Maria.Gonzalez@thelsa.com"},
                                 {"maria.gonzalez@thelsa.com"})
    assert {i["kind"] for i in lupita} == {"assign_coord"}
    other = moveware._gap_items({"id": "u", "email": "x@thelsa.com"}, {"x@thelsa.com"})
    assert other == []


def test_prewarm_reads_all_due_files(monkeypatch):
    import mw_live
    monkeypatch.setattr(mw_live, "have_creds", lambda: True)
    monkeypatch.setattr(mw_live, "audited_in_window",
                        lambda: [_f("1", pack=(dt.date.today() - dt.timedelta(days=2)).isoformat()),
                                 _f("2", pack=(dt.date.today() - dt.timedelta(days=2)).isoformat())])
    _cache("2")
    got = []
    monkeypatch.setattr(mw_confirm, "refresh", lambda pairs, wait=False: got.extend(pairs))
    assert mw_confirm.prewarm() == 1 and got == [("1", "1")]
