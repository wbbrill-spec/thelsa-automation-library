"""AI Assistant web tests: isolation, consent, ranking, Moveware to-dos, WhatsApp, admin."""

import datetime as dt
import os
import re

os.environ.setdefault("ASSISTANT_SCHEDULER", "0")
os.environ.setdefault("ENABLE_SCHEDULER", "0")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

import pytest
from cryptography.fernet import Fernet

from assistant import db, moveware, priority


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("TOKEN_ENC_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ASSISTANT_ADMINS", "boss@thelsa.com")
    db.reset_engine_for_tests(f"sqlite:///{tmp_path}/w.db")
    import app as appmod
    appmod.app.config["TESTING"] = True
    with appmod.app.test_client() as c:
        yield c


def login(c, email, name="Test User"):
    with c.session_transaction() as s:
        s.clear()
        s["user_email"] = email
        s["user_name"] = name
        s["asst_csrf"] = "tok"


def consent(email):
    u = db.upsert_user(email)
    db.set_consent(u["id"])
    return db.get_user_by_email(email)


def mail_item(eid, subj, hours_ago=1, kind="needs_reply"):
    return {"external_id": eid, "kind": kind, "from_name": "Ana", "from_addr": "ana@client.com",
            "subject": subj, "snippet": "hola", "url": "https://outlook/1",
            "received_at": dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)}


def test_requires_login(client):
    r = client.get("/assistant")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_first_visit_shows_consent(client):
    login(client, "ana@thelsa.com")
    r = client.get("/assistant")
    assert b"I agree" in r.data
    r = client.post("/assistant/consent", data={"csrf": "tok", "agree": "on"})
    assert r.headers["Location"].endswith("/assistant/connect/microsoft")
    assert db.get_user_by_email("ana@thelsa.com")["consent_at"] is not None


def test_csrf_required(client):
    login(client, "ana@thelsa.com")
    r = client.post("/assistant/consent", data={"agree": "on"})
    assert r.status_code == 400


def test_each_user_sees_only_their_own_dashboard(client):
    a, b = consent("a@thelsa.com"), consent("b@thelsa.com")
    db.replace_items(a["id"], "microsoft", [mail_item("m1", "SECRET-FOR-A")])
    db.replace_items(b["id"], "microsoft", [mail_item("m2", "SECRET-FOR-B")])
    login(client, "b@thelsa.com")
    page = client.get("/assistant").data
    assert b"SECRET-FOR-B" in page and b"SECRET-FOR-A" not in page
    # B tries to act on A's item by id → no effect
    a_item = db.list_items(a["id"])[0]
    client.post(f"/assistant/item/{a_item['id']}/seen", data={"csrf": "tok", "seen": "1"})
    assert db.get_item(a["id"], a_item["id"])["seen"] is False
    r = client.post(f"/assistant/item/{a_item['id']}/draft", data={"csrf": "tok"})
    assert r.status_code == 404


def test_dashboard_is_ordered_by_urgency(client):
    u = consent("c@thelsa.com")
    db.replace_items(u["id"], "microsoft", [mail_item("m1", "RECENT-EMAIL", hours_ago=1)])
    today = dt.date.today()
    db.replace_items(u["id"], "moveware", moveware.tasks_for_files([
        {"job": "J100", "client": "Perez", "status": "W", "pack": today - dt.timedelta(days=12),
         "invoiced": False, "sell": 9000, "act_wt": 1200}]))
    login(client, "c@thelsa.com")
    page = client.get("/assistant").data.decode()
    assert page.index("Invoice file J100") < page.index("RECENT-EMAIL")
    assert "Urgent" in page


def test_admin_is_admin_only(client):
    consent("user@thelsa.com")
    login(client, "user@thelsa.com")
    assert client.get("/assistant/admin").status_code == 403
    login(client, "boss@thelsa.com")
    r = client.get("/assistant/admin")
    assert r.status_code == 200 and b"user@thelsa.com" in r.data
    assert b"hola" not in r.data                          # never shows mail content


def test_admin_deactivate_purges(client):
    u = consent("gone@thelsa.com")
    db.save_connection(u["id"], "microsoft", "cache")
    db.replace_items(u["id"], "microsoft", [mail_item("m1", "x")])
    login(client, "boss@thelsa.com")
    client.get("/assistant/admin")
    client.post(f"/assistant/admin/user/{u['id']}/active", data={"csrf": "tok", "active": "0"})
    assert db.get_connection(u["id"], "microsoft") is None and db.list_items(u["id"]) == []
    login(client, "gone@thelsa.com")
    assert client.get("/assistant").status_code == 403


def test_whatsapp_sync_flow(client):
    login(client, "w@thelsa.com")
    u = consent("w@thelsa.com")
    client.post("/assistant/whatsapp/optin", data={"csrf": "tok", "agree": "on"})
    client.post("/assistant/whatsapp/key", data={"csrf": "tok"})
    page = client.get("/assistant/whatsapp").data.decode()
    key = re.search(r"(wa_[A-Za-z0-9_\-]+)", page).group(1)
    assert client.post("/api/assistant/whatsapp/sync", json={"chats": []},
                       headers={"Authorization": "Bearer wa_wrong"}).status_code == 401
    r = client.post("/api/assistant/whatsapp/sync", headers={"Authorization": f"Bearer {key}"},
                    json={"chats": [{"name": "Cliente Lopez", "unread": 3, "preview": "¿Ya llegó?"},
                                    {"name": "Read chat", "unread": 0}]})
    assert r.json["items"] == 1
    assert b"Cliente Lopez" in client.get("/assistant").data
    z = client.get("/assistant/whatsapp/extension.zip")
    assert z.status_code == 200 and z.data[:2] == b"PK"
    # turning it off deletes the data and invalidates the key
    client.post("/assistant/disconnect/whatsapp", data={"csrf": "tok"})
    assert db.list_items(u["id"], "whatsapp") == []
    assert client.post("/api/assistant/whatsapp/sync", headers={"Authorization": f"Bearer {key}"},
                       json={"chats": []}).status_code == 401


def _wa_setup(client, monkeypatch, chats, verdicts=None, watch=None):
    """Opt a user into WhatsApp, POST a chat list, return the resulting items."""
    from assistant import wa_triage
    wa_triage.reset_cache_for_tests()
    monkeypatch.setattr(wa_triage, "classify", lambda cs: verdicts or {})
    login(client, "wa@thelsa.com")
    u = consent("wa@thelsa.com")
    if watch is not None:
        db.set_wa_watch(u["id"], watch)
    client.post("/assistant/whatsapp/optin", data={"csrf": "tok", "agree": "on"})
    client.post("/assistant/whatsapp/key", data={"csrf": "tok"})
    page = client.get("/assistant/whatsapp").data.decode()
    key = re.search(r"(wa_[A-Za-z0-9_\-]+)", page).group(1)
    r = client.post("/api/assistant/whatsapp/sync",
                    headers={"Authorization": f"Bearer {key}"}, json={"chats": chats})
    assert r.status_code == 200
    return u, db.list_items(u["id"], "whatsapp")


CHATS = [{"name": "Cliente Lopez", "unread": 2, "preview": "¿Ya salió el embarque?"},
         {"name": "Mamá", "unread": 5, "preview": "no olvides la cena"},
         {"name": "Thelsa Operaciones", "unread": 1, "preview": "falta la carta de encargo"}]


def test_whatsapp_drops_personal_chats_and_uses_the_action_line(client, monkeypatch):
    from assistant import wa_triage
    verdicts = {
        wa_triage.key("Cliente Lopez", "¿Ya salió el embarque?"):
            {"work": True, "action": "Confirmar fecha de salida del embarque"},
        wa_triage.key("Mamá", "no olvides la cena"): {"work": False, "action": ""},
        wa_triage.key("Thelsa Operaciones", "falta la carta de encargo"):
            {"work": True, "action": ""},
    }
    u, items = _wa_setup(client, monkeypatch, CHATS, verdicts)
    assert sorted(i["from_name"] for i in items) == ["Cliente Lopez", "Thelsa Operaciones"]
    by_name = {i["from_name"]: i for i in items}
    assert by_name["Cliente Lopez"]["snippet"] == "Confirmar fecha de salida del embarque"
    # no action line from the model -> the raw preview is kept, nothing is lost
    assert by_name["Thelsa Operaciones"]["snippet"] == "falta la carta de encargo"


def test_whatsapp_keeps_everything_when_triage_is_unavailable(client, monkeypatch):
    u, items = _wa_setup(client, monkeypatch, CHATS, verdicts={})
    assert len(items) == 3                       # no API key / API failed -> nothing dropped
    assert {i["snippet"] for i in items} >= {"no olvides la cena"}


def test_whatsapp_watch_list_runs_before_the_ai(client, monkeypatch):
    u, items = _wa_setup(client, monkeypatch, CHATS, verdicts={}, watch="thelsa, cliente")
    assert sorted(i["from_name"] for i in items) == ["Cliente Lopez", "Thelsa Operaciones"]


def test_whatsapp_watch_matching_is_accent_and_case_insensitive():
    from assistant import wa_triage
    assert wa_triage.passes_watch("Logística TIM", "logistica")
    assert wa_triage.passes_watch("Thelsa Operaciones", "THELSA")
    assert not wa_triage.passes_watch("Mamá", "thelsa")
    assert wa_triage.passes_watch("Mamá", "")          # empty watch list keeps everything
    assert wa_triage.passes_watch("Mamá", None)


def test_whatsapp_triage_is_off_without_an_api_key(monkeypatch):
    from assistant import wa_triage
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert wa_triage.enabled() is False
    assert wa_triage.classify(CHATS) == {}          # never calls out, never drops


def test_whatsapp_triage_parses_the_model_reply_and_caches_it(monkeypatch):
    from assistant import wa_triage
    wa_triage.reset_cache_for_tests()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    calls = []

    class Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"content": [{"type": "text", "text":
                    'Here you go:\n[{"n":1,"work":true,"action":"Confirmar salida"},'
                    '{"n":2,"work":false,"action":""}]'}]}

    monkeypatch.setattr(wa_triage.requests, "post", lambda *a, **k: (calls.append(1), Resp())[1])
    chats = [{"name": "Cliente Lopez", "unread": 1, "preview": "¿ya salió?"},
             {"name": "Mamá", "unread": 1, "preview": "la cena"}]
    v = wa_triage.classify(chats)
    assert v[wa_triage.key("Cliente Lopez", "¿ya salió?")] == {"work": True,
                                                              "action": "Confirmar salida"}
    assert v[wa_triage.key("Mamá", "la cena")]["work"] is False
    wa_triage.classify(chats)                      # unchanged chats -> served from cache
    assert len(calls) == 1


def test_whatsapp_triage_keeps_chats_when_the_api_errors(monkeypatch):
    from assistant import wa_triage
    wa_triage.reset_cache_for_tests()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def boom(*a, **k):
        raise RuntimeError("503 from Anthropic")

    monkeypatch.setattr(wa_triage.requests, "post", boom)
    assert wa_triage.classify([{"name": "Cliente", "unread": 1, "preview": "hola"}]) == {}


def test_admin_sets_whatsapp_watch_list(client):
    u = consent("wa2@thelsa.com")
    login(client, "boss@thelsa.com")
    client.get("/assistant/admin")
    client.post(f"/assistant/admin/user/{u['id']}/wa",
                data={"csrf": "tok", "wa": "Thelsa; TIM , thelsa"})
    assert db.get_user(u["id"])["wa_watch"] == "Thelsa, TIM"      # de-duped, order kept
    client.post(f"/assistant/admin/user/{u['id']}/wa", data={"csrf": "tok", "wa": ""})
    assert db.get_user(u["id"])["wa_watch"] is None


def test_moveware_rules():
    today = dt.date(2026, 9, 21)
    files = [
        {"job": "A", "client": "Uno", "status": "W", "pack": dt.date(2026, 9, 10),
         "invoiced": False, "sell": 5000, "act_wt": None},                     # invoice + docs
        {"job": "B", "client": "Dos", "status": "W", "pack": dt.date(2026, 9, 25),
         "invoiced": False, "declared": None, "ins": None},                      # request docs
        {"job": "C", "client": "Embajada de Estados Unidos", "status": "W", "is_embassy": True,
         "pack": dt.date(2026, 9, 10), "delivery": dt.date(2026, 10, 5),
         "invoiced": False, "act_wt": 900},                                      # not yet billable
        {"job": "D", "client": "Cuatro", "status": "C", "pack": dt.date(2026, 9, 1),
         "invoiced": False},                                                     # cancelled
        {"job": "E", "client": "Cinco", "status": "W", "pack": dt.date(2026, 9, 1),
         "invoiced": True, "act_wt": 100},                                       # all done
    ]
    got = sorted((t["external_id"]) for t in moveware.tasks_for_files(files, today=today))
    assert got == ["A:docs", "A:invoice", "B:request"]
    charges = moveware.tasks_for_underbilling([{"job": "F", "approved_total": 800, "invoiced": 200}])
    assert charges[0]["kind"] == "invoice_charge" and charges[0]["meta"]["value"] == 600


def test_moveware_matches_coordinator_email(monkeypatch):
    import types, sys
    fake = types.SimpleNamespace(
        have_creds=lambda: True, ensure_auditor=lambda: None,
        audited_in_window=lambda: [
            {"job": "1", "coordinator_email": "Ana@Thelsa.com", "status": "W",
             "pack": dt.date.today() - dt.timedelta(days=3), "invoiced": False, "act_wt": 5},
            {"job": "2", "coordinator_email": "other@thelsa.com", "status": "W",
             "pack": dt.date.today() - dt.timedelta(days=3), "invoiced": False, "act_wt": 5}])
    monkeypatch.setitem(sys.modules, "mw_live", fake)
    items = moveware.tasks_for_user({"email": "ana@thelsa.com", "moveware_email": None})
    assert [i["meta"]["job"] for i in items] == ["1"]


def test_moveware_watch_list_adds_other_coordinators(monkeypatch):
    import types, sys
    def f(job, coord):
        return {"job": job, "coordinator_email": coord, "status": "W",
                "pack": dt.date.today() - dt.timedelta(days=3), "invoiced": False, "act_wt": 5}
    fake = types.SimpleNamespace(
        have_creds=lambda: True, ensure_auditor=lambda: None,
        audited_in_window=lambda: [f("1", "Stephanie.Barraza@Thelsa.com"),
                                   f("2", "elizabethhernandez@thelsa.com"),
                                   f("3", "sarareyes@thelsa.com"),
                                   f("4", "someoneelse@thelsa.com")])
    monkeypatch.setitem(sys.modules, "mw_live", fake)
    memo = {"email": "guillermomonroy@thelsa.com", "moveware_email": None,
            "mw_watch": "stephanie.barraza@thelsa.com, elizabethhernandez@thelsa.com; "
                        "sarareyes@thelsa.com"}
    jobs = sorted(i["meta"]["job"] for i in moveware.tasks_for_user(memo))
    assert jobs == ["1", "2", "3"]                     # not "4"
    # no watch list → only their own files, which is none of these
    assert moveware.tasks_for_user({**memo, "mw_watch": None}) == []


def test_watched_emails_normalizes():
    got = moveware.watched_emails({"email": "Memo@Thelsa.com", "moveware_email": "memo.m@thelsa.com",
                                   "mw_watch": " A@x.com ,a@x.com;\nB@x.com , not-an-email "})
    assert got == {"memo@thelsa.com", "memo.m@thelsa.com", "a@x.com", "b@x.com"}


def test_admin_sets_moveware_watch_list(client):
    u = consent("memo@thelsa.com")
    login(client, "boss@thelsa.com")
    client.get("/assistant/admin")
    client.post(f"/assistant/admin/user/{u['id']}/mwwatch",
                data={"csrf": "tok", "watch": "Stephanie.Barraza@thelsa.com, sarareyes@thelsa.com"})
    assert db.get_user(u["id"])["mw_watch"] == "stephanie.barraza@thelsa.com,sarareyes@thelsa.com"
    client.post(f"/assistant/admin/user/{u['id']}/mwwatch", data={"csrf": "tok", "watch": ""})
    assert db.get_user(u["id"])["mw_watch"] is None


def test_priority_tiers():
    now = dt.datetime(2026, 9, 21, 12, tzinfo=dt.timezone.utc)
    ranked = priority.rank([
        {"kind": "needs_reply", "received_at": now - dt.timedelta(hours=1), "meta": "{}"},
        {"kind": "invoice_file", "meta": {"days": 15, "value": 8000}},
        {"kind": "request_docs", "meta": {"days_left": 9}},
    ], now=now)
    assert [r["kind"] for r in ranked] == ["invoice_file", "needs_reply", "request_docs"]
    assert ranked[0]["tier"] == "urgent"


def test_clickup_tim_tasks_and_scope():
    import types
    from assistant import clickup_tim
    S = lambda **k: types.SimpleNamespace(**{"agent": "UHaul", "status_flags": [], "steps_done": 2,
                                             "steps_total": 13, "url": "https://app.clickup.com/x",
                                             "last_progress_at": dt.date(2026, 9, 10), **k})
    ships = [
        S(source_ref="1", customer_name="Rosa Molina", reference_number="121722",
          stage=types.SimpleNamespace(value="docs_pending"), current_step="2 Solicitar Documentos",
          days_since_progress=9, assignees=["Fernanda Mora"]),
        S(source_ref="2", customer_name="Louise P", reference_number="MX1",
          stage=types.SimpleNamespace(value="customs_clearance"), current_step="7 Programar Importación",
          days_since_progress=2, assignees=[]),
        S(source_ref="3", customer_name="Done Guy", reference_number="X",
          stage=types.SimpleNamespace(value="delivered"), current_step="", days_since_progress=1,
          assignees=[]),
    ]
    fer = {"email": "fmora@thelsa.com", "name": "Fernanda Mora", "tim_scope": "assigned"}
    got = clickup_tim.tasks_for_shipments(ships, fer)
    assert [(t["external_id"], t["kind"]) for t in got] == [("tim:1", "tim_docs")]
    everything = clickup_tim.tasks_for_shipments(ships, {**fer, "tim_scope": "all"})
    assert sorted(t["external_id"] for t in everything) == ["tim:1", "tim:2"]
    assert clickup_tim.tasks_for_shipments(ships, {**fer, "tim_scope": "none"}) == []
    ranked = priority.rank(everything)
    assert ranked[0]["kind"] == "tim_docs" and ranked[0]["tier"] in ("urgent", "today")


def test_admin_sets_tim_scope(client):
    u = consent("fer@thelsa.com")
    login(client, "boss@thelsa.com")
    client.get("/assistant/admin")
    client.post(f"/assistant/admin/user/{u['id']}/tim", data={"csrf": "tok", "scope": "all"})
    assert db.get_user(u["id"])["tim_scope"] == "all"


def test_language_toggle_and_spanish_items(client):
    u = consent("es@thelsa.com")
    today = dt.date.today()
    db.replace_items(u["id"], "moveware", moveware.tasks_for_files([
        {"job": "J200", "client": "Lopez", "status": "W", "pack": today - dt.timedelta(days=5),
         "invoiced": False, "sell": 3000, "act_wt": 800}]))
    login(client, "es@thelsa.com")
    page = client.get("/assistant", headers={"Accept-Language": "en-US"}).data.decode()
    assert "Invoice file J200" in page and "Refresh now" in page
    r = client.get("/assistant/lang/es?next=/assistant")
    assert r.status_code == 302 and db.get_user(u["id"])["lang"] == "es"
    page = client.get("/assistant", headers={"Accept-Language": "en-US"}).data.decode()
    assert "Facturar expediente J200" in page and "Actualizar ahora" in page
    assert "Invoice file J200" not in page
    # open-redirect guard
    r = client.get("/assistant/lang/en?next=https://evil.example")
    assert r.headers["Location"].endswith("/assistant")


def test_browser_language_is_the_default(client):
    consent("nuevo@thelsa.com")
    login(client, "nuevo@thelsa.com")
    page = client.get("/assistant", headers={"Accept-Language": "es-MX,es;q=0.9"}).data.decode()
    assert "Todo lo que necesita de ti" in page


def test_sources_without_items_are_hidden(client):
    u = consent("mario@thelsa.com")
    db.replace_items(u["id"], "microsoft", [mail_item("m1", "hola")])
    login(client, "mario@thelsa.com")
    page = client.get("/assistant").data.decode()
    assert "Moveware to-dos" not in page and 'data-f="moveware"' not in page
    assert 'data-f="clickup"' not in page


def test_spanish_consent_and_whatsapp_pages(client):
    u = db.upsert_user("sara@thelsa.com")
    db.set_lang(u["id"], "es")
    login(client, "sara@thelsa.com")
    assert "Acepto que el asistente" in client.get("/assistant").data.decode()
    db.set_consent(u["id"])
    assert "Activar WhatsApp" in client.get("/assistant/whatsapp").data.decode()
