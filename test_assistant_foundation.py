"""Foundation tests: encrypted vault, per-user isolation, item upserts, triage."""

import datetime as dt

import pytest
from cryptography.fernet import Fernet

from assistant import db, triage, vault


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch, tmp_path):
    monkeypatch.setenv("TOKEN_ENC_KEY", Fernet.generate_key().decode())
    db.reset_engine_for_tests(f"sqlite:///{tmp_path}/t.db")
    yield


def _item(eid, kind="needs_reply", subj="Hi"):
    return {"external_id": eid, "kind": kind, "from_name": "Ana", "from_addr": "ana@x.com",
            "subject": subj, "snippet": "...", "url": "https://o/1",
            "received_at": dt.datetime(2026, 9, 21, 12, tzinfo=dt.timezone.utc)}


# ── Vault ──
def test_vault_roundtrip_and_ciphertext_only():
    ct = vault.encrypt("refresh-token-abc")
    assert b"refresh-token-abc" not in ct
    assert vault.decrypt(ct) == "refresh-token-abc"


def test_vault_missing_key(monkeypatch):
    monkeypatch.delenv("TOKEN_ENC_KEY")
    assert not vault.is_configured()
    with pytest.raises(vault.VaultError):
        vault.encrypt("x")


def test_vault_rotation(monkeypatch):
    old = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENC_KEY", old)
    ct = vault.encrypt("secret")
    new = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENC_KEY", f"{new},{old}")
    assert vault.decrypt(ct) == "secret"          # old data still readable
    rotated = vault.rotate(ct)
    monkeypatch.setenv("TOKEN_ENC_KEY", new)       # old key retired
    assert vault.decrypt(rotated) == "secret"
    with pytest.raises(vault.VaultError):
        vault.decrypt(ct)


def test_connection_stored_encrypted():
    u = db.upsert_user("bill@thelsa.com", "Bill")
    db.save_connection(u["id"], "microsoft", '{"cache":"TOKENS"}', "bill@thelsa.com")
    row = db.get_connection(u["id"], "microsoft")
    assert b"TOKENS" not in bytes(row["secret_enc"])
    assert db.get_secret(u["id"], "microsoft") == '{"cache":"TOKENS"}'


# ── Isolation ──
def test_users_cannot_see_each_others_items_or_drafts():
    a = db.upsert_user("a@thelsa.com")
    b = db.upsert_user("b@thelsa.com")
    db.replace_items(a["id"], "microsoft", [_item("m1")])
    db.replace_items(b["id"], "microsoft", [_item("m2")])
    assert [i["external_id"] for i in db.list_items(a["id"])] == ["m1"]
    a_item = db.list_items(a["id"])[0]
    assert db.get_item(b["id"], a_item["id"]) is None
    with pytest.raises(PermissionError):
        db.save_draft(b["id"], a_item["id"], "hijack")
    db.mark_seen(b["id"], a_item["id"])            # silently no-op for wrong user
    assert db.get_item(a["id"], a_item["id"])["seen"] is False


# ── Items lifecycle ──
def test_replace_items_keeps_seen_and_drops_stale():
    u = db.upsert_user("u@thelsa.com")
    db.replace_items(u["id"], "microsoft", [_item("m1"), _item("m2")])
    m1 = [i for i in db.list_items(u["id"]) if i["external_id"] == "m1"][0]
    db.mark_seen(u["id"], m1["id"])
    db.save_draft(u["id"], m1["id"], "Thanks!")
    db.replace_items(u["id"], "microsoft", [_item("m1", subj="Hi again")])
    rows = db.list_items(u["id"])
    assert len(rows) == 1 and rows[0]["seen"] is True and rows[0]["subject"] == "Hi again"
    assert len(db.list_drafts(u["id"])) == 1
    assert "microsoft" in db.sync_times(u["id"])


def test_disconnect_deletes_tokens_items_and_drafts():
    u = db.upsert_user("u@thelsa.com")
    db.save_connection(u["id"], "microsoft", "cache")
    db.replace_items(u["id"], "microsoft", [_item("m1")])
    db.save_draft(u["id"], db.list_items(u["id"])[0]["id"], "x")
    db.disconnect(u["id"], "microsoft")
    assert db.get_connection(u["id"], "microsoft") is None
    assert db.list_items(u["id"]) == [] and db.list_drafts(u["id"]) == []


def test_deactivate_purges_everything_and_skips_scan():
    u = db.upsert_user("u@thelsa.com")
    db.save_connection(u["id"], "microsoft", "cache")
    assert [r["email"] for r in db.users_to_scan("microsoft")] == ["u@thelsa.com"]
    db.set_active(u["id"], False)
    assert db.users_to_scan("microsoft") == []
    assert db.get_connection(u["id"], "microsoft") is None


def test_database_url_translation(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h/db")
    assert db._db_url() == "postgresql+psycopg://u:p@h/db"


# ── Triage ──
def _graph(mid, addr, read=False, flagged=False):
    return {"id": mid, "from": {"emailAddress": {"name": "N", "address": addr}},
            "subject": "S", "bodyPreview": "p", "webLink": "w",
            "receivedDateTime": "2026-09-21T12:00:00Z", "isRead": read,
            "flag": {"flagStatus": "flagged" if flagged else "notFlagged"}}


def test_triage_rules():
    msgs = [triage.normalize_graph(m) for m in [
        _graph("1", "ana@client.com"),                      # needs reply
        _graph("2", "no-reply@service.com"),                # automated → skip
        _graph("3", "bob@client.com", read=True),           # read → skip
        _graph("4", "carl@client.com", read=True, flagged=True),  # flagged
        _graph("5", "me@thelsa.com"),                       # own mail → skip
    ]]
    got = sorted((i["external_id"], i["kind"]) for i in triage.triage(msgs, "me@thelsa.com"))
    assert got == [("1", "needs_reply"), ("4", "flagged")]
    assert msgs[0]["received_at"].tzinfo is not None


def test_vault_accepts_any_long_random_string(monkeypatch):
    monkeypatch.setenv("TOKEN_ENC_KEY", "x7Hq2LmP9vR4tY8wZ1aB3cD5eF6gJ0kN")
    assert vault.decrypt(vault.encrypt("hello")) == "hello"
    monkeypatch.setenv("TOKEN_ENC_KEY", "short")
    assert not vault.is_configured()


def test_missing_columns_are_added(tmp_path):
    import sqlalchemy as sa
    url = f"sqlite:///{tmp_path}/old.db"
    old = sa.create_engine(url)
    with old.begin() as c:
        c.execute(sa.text("CREATE TABLE asst_users (id VARCHAR(36) PRIMARY KEY, email VARCHAR(320), "
                          "name VARCHAR(200), role VARCHAR(20), active BOOLEAN, created_at TIMESTAMP)"))
        c.execute(sa.text("INSERT INTO asst_users VALUES ('1','old@thelsa.com','Old','user',1,NULL)"))
    db.reset_engine_for_tests(url)
    u = db.get_user_by_email("old@thelsa.com")
    assert u["tim_scope"] == "assigned" and u["whatsapp_opt_in"] in (False, 0)
