"""The shared to-do format, AuditTrack's to-dos, and the reader for the separate
products (QuoteRelay, WhatsApp leads): routing, last-good-list, privacy."""
import datetime as dt
import json

import pytest

from assistant import auditrack, db, feeds, i18n, priority, todo


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db.reset_engine_for_tests(f"sqlite:///{tmp_path}/p.db")
    monkeypatch.setattr(todo, "_CACHE", {"thelsa": {
        "people": {"key_users": ["lupita@thelsa.com", "sarareyes@thelsa.com"],
                   "billing": ["lupita@thelsa.com"], "procurement": ["proc@thelsa.com"],
                   "collections": []},
        "drafts": {"invoice_request_to": "lupita@thelsa.com"},
        "auditrack": {"enabled": True, "open_invoice_min_days": 30},
        "feeds": [{"id": "quoterelay", "name": "QuoteRelay",
                   "url": "https://matt.example/api/assistant/tasks?t=thelsa",
                   "token_env": "TOK_QR"},
                  {"id": "wa_leads", "name": "WhatsApp", "url": "http://not-https.example",
                   "token_env": "TOK_WA"}]}})
    monkeypatch.delenv("ASSISTANT_TENANT", raising=False)


def item(**kw):
    base = {"id": "q1", "kind": "quote_followup", "title": {"en": "Follow up quote", "es": "Seguimiento"},
            "detail": {"en": "No reply", "es": "Sin respuesta"}, "urgency": 70}
    base.update(kw)
    return base


# ── format ────────────────────────────────────────────────────────────────────
def test_parse_feed_checks_contract_and_drops_bad_rows():
    bad = todo.parse_feed("quoterelay", {"contract": "v0", "items": []})
    assert not bad["ok"]
    p = todo.parse_feed("quoterelay", todo.feed_payload("quoterelay", [
        item(), item(),                                   # duplicate id
        {"id": "x", "kind": "k"},                         # no title
        item(id="q2", kind="Quote Follow-Up!!", link="javascript:alert(1)", urgency=500),
    ]))
    assert p["ok"] and p["rejected"] == 2
    q2 = [i for i in p["items"] if i["id"] == "q2"][0]
    assert q2["kind"] == "quotefollowup" and q2["link"] == "" and q2["urgency"] == 100


def test_owner_must_be_their_own_address():
    assert todo.owner_ok("Sara Reyes", "sarareyes@thelsa.com")
    assert not todo.owner_ok("Sara Reyes", "edgarespino@thelsa.com")      # the 19 Oct lesson
    assert todo.owner_ok("", "maurice@thelsa.com")                         # read from their own mailbox
    it = todo.clean_item("q", item(owner_name="Sara Reyes", owner_email="edgarespino@thelsa.com"))
    assert todo.owners(it) == {"lupita@thelsa.com", "sarareyes@thelsa.com"}   # unowned → key users


def test_role_reaches_company_people_never_another_person():
    it = todo.clean_item("q", item(role="procurement"))
    assert todo.owners(it) == {"proc@thelsa.com"}
    it = todo.clean_item("q", item(role="collections"))                  # nobody named → key users
    assert todo.owners(it) == {"lupita@thelsa.com", "sarareyes@thelsa.com"}


def test_for_user_and_text_and_rank():
    it = todo.clean_item("quoterelay", item(owner_email="maurice@thelsa.com", file="110873A",
                                            customer="Agent Co", sticky=True, urgency=80))
    rows = todo.for_user([it], {"maurice@thelsa.com"})
    assert len(rows) == 1 and todo.for_user([it], {"other@thelsa.com"}) == []
    r = rows[0]
    assert r["meta"]["job"] == "110873A" and r["from_name"] == "Agent Co"
    assert i18n.item_text(r, "es") == ("Seguimiento", "Sin respuesta")
    assert priority.score({**r, "seen": True}) == 80                     # sticky: opening doesn't lower it
    r["meta"]["sticky"] = False
    assert priority.score({**r, "seen": True}) == 60


def test_base_file_keeps_sequels_grouped():
    assert todo.base_file("110873A") == "110873" and todo.base_file("110873") == "110873"
    assert todo.base_file("ABC-1") == "ABC-1"


# ── feeds ─────────────────────────────────────────────────────────────────────
class _Resp:
    def __init__(self, code, payload):
        self.status_code, self._p = code, payload

    def json(self):
        if isinstance(self._p, Exception):
            raise self._p
        return self._p


def test_feed_needs_https_and_token(monkeypatch):
    cfg = feeds.configured()
    assert [f["id"] for f in cfg] == ["quoterelay"]                      # http feed refused
    monkeypatch.delenv("TOK_QR", raising=False)
    r = feeds.refresh_all()
    assert "waiting for its token" in r["quoterelay"]
    assert db.get_feed("quoterelay") == (None, None)


def test_good_read_saved_failed_read_keeps_last_good(monkeypatch):
    monkeypatch.setenv("TOK_QR", "secret")
    calls = []

    def ok_get(url, timeout, headers):
        calls.append(headers["Authorization"])
        return _Resp(200, todo.feed_payload("quoterelay", [item(owner_email="maurice@thelsa.com")]))
    monkeypatch.setattr(feeds.requests, "get", ok_get)
    assert feeds.refresh_all()["quoterelay"] == "1 to-dos" and calls == ["Bearer secret"]
    monkeypatch.setattr(feeds.requests, "get", lambda *a, **k: _Resp(500, {}))
    assert feeds.refresh_all()["quoterelay"] == "HTTP 500"
    items, _ = db.get_feed("quoterelay")
    assert len(items) == 1                                               # last good list kept
    monkeypatch.setattr(feeds.requests, "get", lambda *a, **k: _Resp(
        200, todo.feed_payload("quoterelay", [], complete=False)))
    assert "partial" in feeds.refresh_all()["quoterelay"]
    assert len(db.get_feed("quoterelay")[0]) == 1
    got = feeds.items_for({}, {"maurice@thelsa.com"})
    assert list(got) == ["quoterelay"] and len(got["quoterelay"]) == 1
    h = feeds.health()[0]
    assert h["count"] == 1 and h["ok"] is False and "partial" in h["error"]


# ── AuditTrack builders ───────────────────────────────────────────────────────
DIR = {"stephanie barraza": "stephaniebarraza@thelsa.com"}


def test_invoice_items_use_auditracks_worklist_and_verified_owner():
    wl = [{"job": "111154", "client": "ACME", "coordinator": "Stephanie Barraza",
           "coordinator_email": "edgarespino@thelsa.com", "value": 1000, "currency": "USD",
           "orig_currency": "MXN", "orig_value": 17880, "draft": True, "embassy": False,
           "pack": (dt.date.today() - dt.timedelta(days=5)).isoformat(), "delivery": None}]
    it = auditrack.invoice_items(wl, DIR, "lupita@thelsa.com")[0]
    assert it["owner_email"] == "stephaniebarraza@thelsa.com"            # Edgar's address rejected
    assert "17,880 MXN" in it["detail"]["en"] and "draft invoice is ready" in it["detail"]["en"]
    assert it["draft"]["to"] == "lupita@thelsa.com" and it["sticky"]
    assert auditrack.invoice_items(wl, DIR, "")[0]["draft"] is None


def test_other_auditrack_items():
    files = [{"job": "110001", "disc_value": 250, "stage": "gap_flagged", "coordinator": "Stephanie Barraza",
              "coordinator_email": "stephaniebarraza@thelsa.com", "currency": "USD"},
             {"job": "110002", "disc_value": 0}, {"job": "110003", "disc_value": 9, "stage": "closed"}]
    assert [i["file"] for i in auditrack.cost_items(files, DIR)] == ["110001"]
    pr = auditrack.price_items([{"job": "111154", "moveware": 17880, "booked": 9790.74, "currency": "MXN",
                                 "difference": 8089.26, "coordinator": "Stephanie Barraza"}], DIR)
    assert pr[0]["owner_email"] == "stephaniebarraza@thelsa.com"
    oi = auditrack.open_invoice_items(
        [{"company": "TMS", "series": "A", "folio": "77", "date": "2026-09-01", "currency": "MXN",
          "total": 5000, "paid_share": 0.4, "jobs": ["110001"], "days": 61},
         {"company": "TMS", "series": "A", "folio": "78", "days": 5}],
        {"110001": ("Stephanie Barraza", "stephaniebarraza@thelsa.com")}, 30)
    assert len(oi) == 1 and oi[0]["owner_email"] == "stephaniebarraza@thelsa.com"
    assert oi[0]["draft"]["to"] == "" and "40%" in oi[0]["detail"]["en"]
    sup = auditrack.supplier_items([{"check": "invoice_missing", "job": "110001", "line": "Customs",
                                     "creditor": "Agente X", "delivered": "2026-08-01", "days": 92}],
                                   {"invoice_missing": ("Supplier invoice missing", "Falta factura")})
    assert sup[0]["role"] == "procurement" and sup[0]["draft"]["facts"]["supplier"] == "Agente X"
    pl = auditrack.plan_items([{"action": "review", "job": "110862", "milestone": "pack",
                                "mw_date": "2026-10-01", "plan_date": "2026-10-03",
                                "coordinator": "Stephanie Barraza", "activity_id": 5},
                               {"action": "update", "job": "1"}], DIR)
    assert len(pl) == 1 and pl[0]["kind"] == "plan_review"


def test_auditrack_refresh_keeps_last_list_on_partial_read(monkeypatch):
    monkeypatch.setattr(auditrack, "build", lambda: ([item(id="inv:1", kind="invoice_ready",
                                                           owner_email="maurice@thelsa.com")], True))
    assert auditrack.refresh() == "1 to-dos"
    monkeypatch.setattr(auditrack, "build", lambda: ([], False))
    assert auditrack.refresh() == "partial"
    assert len(auditrack.items_for({"maurice@thelsa.com"})) == 1


def test_no_customer_data_in_admin_health(monkeypatch):
    monkeypatch.setattr(auditrack, "build", lambda: ([item(id="inv:1", customer="Secret Customer SA",
                                                           owner_email="m@thelsa.com")], True))
    auditrack.refresh()
    assert "Secret Customer" not in json.dumps(feeds.health(), default=str)


def test_auditrack_build_runs_on_real_calculations(monkeypatch):
    """The whole path through AuditTrack's own reconcile / compute_metrics."""
    import mw_live
    today = dt.date.today()
    recs = [{"job": "111501", "job_id": 9, "status": "W", "job_type": "EXP", "client": "Cliente Uno",
             "pack": today - dt.timedelta(days=10), "delivery": None,
             "booked": today - dt.timedelta(days=40), "sell": 2500, "est": 0, "act": 0,
             "inv_amt": 0, "invoiced": False, "inv_list": [], "currency": "USD",
             "coordinator": "Sara Reyes", "mw_coordinator": "Sara Reyes",
             "coordinator_email": "sarareyes@thelsa.com", "q_lines": [], "i_lines": [], "sel_lines": [],
             "mode": "sea", "branch": "MTY", "is_embassy": False, "declared": 0, "ins": 0, "act_wt": 0,
             "est_wt": 0, "est_vol": 0, "rev_reported": 2500, "rev_lines": 0, "n_rev_lines": 0,
             "cost_lines": 0, "agent": None, "created": today - dt.timedelta(days=60),
             "survey": None, "detail_ok": True, "number": "111501", "inv_net_by_ccy": {}}]
    monkeypatch.setattr(mw_live, "have_creds", lambda: True)
    monkeypatch.setattr(mw_live, "audit_progress", lambda: {"window_complete": True})
    monkeypatch.setattr(mw_live, "audited_files", lambda: recs)
    monkeypatch.setattr(mw_live, "audited_in_window", lambda: recs)
    items, complete = auditrack.build()
    assert complete
    inv = [i for i in items if i["kind"] == "invoice_ready"]
    assert inv and inv[0]["owner_email"] == "sarareyes@thelsa.com" and inv[0]["file"] == "111501"


def test_product_team_used_when_nobody_named():
    it = todo.clean_item("wa_leads", item(role="advisor", team=["team@thelsa.com", "not-an-address"]))
    assert todo.owners(it) == {"team@thelsa.com"}


def test_part_that_cannot_be_read_keeps_its_last_list(monkeypatch):
    first = [item(id="openinv:A1", kind="open_invoice", owner_email="m@thelsa.com"),
             item(id="inv:9", kind="invoice_ready", owner_email="m@thelsa.com")]
    monkeypatch.setattr(auditrack, "build", lambda: (first, True))
    auditrack.BUILD_FAILED.clear()
    auditrack.refresh()

    def second():
        auditrack.BUILD_FAILED.clear()
        auditrack.BUILD_FAILED.add("open_invoice")          # Finance report unreadable this time
        return [item(id="inv:9", kind="invoice_ready", owner_email="m@thelsa.com")], True
    monkeypatch.setattr(auditrack, "build", second)
    assert "kept from the last read" in auditrack.refresh()
    kinds = sorted(i["kind"] for i in db.get_feed("auditrack")[0])
    assert kinds == ["invoice_ready", "open_invoice"]
    auditrack.BUILD_FAILED.clear()


def test_feed_for_another_company_is_refused():
    p = todo.parse_feed("quoterelay", todo.feed_payload("quoterelay", [item()], tenant="classicmoving"))
    assert not p["ok"] and "classicmoving" in p["error"]
