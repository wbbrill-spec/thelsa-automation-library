"""
Multi-tenant data layer for the AI Assistant.

Every read/write function takes a user_id and filters on it — a user can never
reach another user's rows through this module. Web routes must pass the id of
the SIGNED-IN user (from the session), never an id taken from the request.

DATABASE_URL: Render's Postgres "Internal Database URL". Falls back to a local
SQLite file for development and tests.
"""

import datetime as _dt
import hashlib
import json
import os
import secrets
import uuid

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Integer, LargeBinary,
                        MetaData, String, Table, Text, UniqueConstraint, and_,
                        create_engine, delete, insert, select, update)

from . import vault

metadata = MetaData()


def _uuid() -> str:
    return str(uuid.uuid4())


def now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


users = Table(
    "asst_users", metadata,
    Column("id", String(36), primary_key=True, default=_uuid),
    Column("email", String(320), unique=True, nullable=False),
    Column("name", String(200)),
    Column("role", String(20), nullable=False, default="user"),      # user | admin
    Column("active", Boolean, nullable=False, default=True),
    Column("consent_at", DateTime(timezone=True)),                    # accepted data notice
    Column("whatsapp_opt_in", Boolean, nullable=False, default=False),
    Column("moveware_email", String(320)),                           # override if Moveware uses another address
    Column("tim_scope", String(20), nullable=False, default="assigned"),  # ClickUp/TIM: all | assigned | none
    Column("lang", String(5)),                                       # en | es (None = follow browser)
    Column("created_at", DateTime(timezone=True), default=now),
)

connections = Table(
    "asst_connections", metadata,
    Column("id", String(36), primary_key=True, default=_uuid),
    Column("user_id", String(36), ForeignKey("asst_users.id", ondelete="CASCADE"), nullable=False),
    Column("provider", String(20), nullable=False),          # microsoft | google | whatsapp
    Column("account_email", String(320)),
    Column("secret_enc", LargeBinary),                       # encrypted token cache / sync-key hash
    Column("scopes", Text),
    Column("status", String(20), nullable=False, default="connected"),  # connected | error | revoked
    Column("last_error", Text),
    Column("connected_at", DateTime(timezone=True), default=now),
    Column("last_ok_at", DateTime(timezone=True)),
    UniqueConstraint("user_id", "provider", name="uq_conn_user_provider"),
)

items = Table(
    "asst_items", metadata,
    Column("id", String(36), primary_key=True, default=_uuid),
    Column("user_id", String(36), ForeignKey("asst_users.id", ondelete="CASCADE"), nullable=False),
    Column("source", String(20), nullable=False),            # microsoft | google | whatsapp
    Column("kind", String(20), nullable=False),              # needs_reply | flagged | waiting_on
    Column("external_id", String(512), nullable=False),
    Column("from_name", String(300)),
    Column("from_addr", String(320)),
    Column("subject", Text),
    Column("snippet", Text),
    Column("url", Text),
    Column("received_at", DateTime(timezone=True)),
    Column("meta", Text),                                    # JSON: importance, job, value, due date…
    Column("seen", Boolean, nullable=False, default=False),
    Column("updated_at", DateTime(timezone=True), default=now),
    UniqueConstraint("user_id", "source", "external_id", "kind", name="uq_item"),
)

drafts = Table(
    "asst_drafts", metadata,
    Column("id", String(36), primary_key=True, default=_uuid),
    Column("user_id", String(36), ForeignKey("asst_users.id", ondelete="CASCADE"), nullable=False),
    Column("item_id", String(36), ForeignKey("asst_items.id", ondelete="CASCADE"), nullable=False),
    Column("body", Text, nullable=False),
    Column("status", String(20), nullable=False, default="suggested"),  # suggested | saved | dismissed
    Column("provider_draft_id", String(512)),                # id of the draft in Outlook/Gmail
    Column("created_at", DateTime(timezone=True), default=now),
)

source_sync = Table(                                           # "as of" timestamp per user+source
    "asst_source_sync", metadata,
    Column("user_id", String(36), ForeignKey("asst_users.id", ondelete="CASCADE"), primary_key=True),
    Column("source", String(20), primary_key=True),
    Column("synced_at", DateTime(timezone=True), nullable=False),
)

runs = Table(
    "asst_runs", metadata,
    Column("id", String(36), primary_key=True, default=_uuid),
    Column("trigger", String(20), nullable=False, default="schedule"),  # schedule | manual
    Column("started_at", DateTime(timezone=True), default=now),
    Column("finished_at", DateTime(timezone=True)),
    Column("users_scanned", Integer, default=0),
    Column("errors", Integer, default=0),
    Column("note", Text),
)


# ── Engine ─────────────────────────────────────────────────────────────────────
_engine = None


def _db_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return "sqlite:///" + os.path.join(os.path.dirname(__file__), "..", "data", "assistant.db")
    # Render hands out postgres:// — SQLAlchemy + psycopg3 want postgresql+psycopg://
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def engine():
    global _engine
    if _engine is None:
        url = _db_url()
        if url.startswith("sqlite:///"):
            os.makedirs(os.path.dirname(url[len("sqlite:///"):]) or ".", exist_ok=True)
        _engine = create_engine(url, pool_pre_ping=True, future=True)
        metadata.create_all(_engine)
        _add_missing_columns(_engine)
    return _engine


def _add_missing_columns(eng):
    """Tiny forward-only migration: add columns introduced after a table was
    first created (create_all never alters existing tables). Nullable-safe:
    columns are added with their Python default applied to existing rows."""
    from sqlalchemy import inspect, text
    insp = inspect(eng)
    for table in metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in have:
                continue
            ddl_type = col.type.compile(dialect=eng.dialect)
            default = getattr(col.default, "arg", None)
            clause = ""
            if isinstance(default, bool):
                clause = f" DEFAULT {'TRUE' if default else 'FALSE'}"
            elif isinstance(default, str):
                clause = " DEFAULT '" + default.replace("'", "''") + "'"
            with eng.begin() as c:
                c.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl_type}{clause}'))


def reset_engine_for_tests(url: str):
    global _engine
    _engine = create_engine(url, future=True)
    metadata.create_all(_engine)
    _add_missing_columns(_engine)
    return _engine


# ── Users ──────────────────────────────────────────────────────────────────────
def get_user_by_email(email: str):
    with engine().connect() as c:
        return c.execute(select(users).where(users.c.email == email.lower().strip())).mappings().first()


def get_user(user_id: str):
    with engine().connect() as c:
        return c.execute(select(users).where(users.c.id == user_id)).mappings().first()


def upsert_user(email: str, name: str = None, role: str = None):
    """Create the user on first sign-in (or when an admin adds them)."""
    email = email.lower().strip()
    existing = get_user_by_email(email)
    with engine().begin() as c:
        if existing:
            vals = {}
            if name and name != existing["name"]:
                vals["name"] = name
            if role and role != existing["role"]:
                vals["role"] = role
            if vals:
                c.execute(update(users).where(users.c.id == existing["id"]).values(**vals))
        else:
            c.execute(insert(users).values(id=_uuid(), email=email, name=name or email,
                                           role=role or "user", created_at=now()))
    return get_user_by_email(email)


def set_consent(user_id: str, whatsapp_opt_in: bool = None):
    vals = {"consent_at": now()}
    if whatsapp_opt_in is not None:
        vals["whatsapp_opt_in"] = bool(whatsapp_opt_in)
    with engine().begin() as c:
        c.execute(update(users).where(users.c.id == user_id).values(**vals))


def set_active(user_id: str, active: bool):
    with engine().begin() as c:
        c.execute(update(users).where(users.c.id == user_id).values(active=active))
    if not active:
        purge_user_data(user_id)


def set_moveware_email(user_id: str, email: str):
    with engine().begin() as c:
        c.execute(update(users).where(users.c.id == user_id)
                  .values(moveware_email=(email or "").strip().lower() or None))


def set_tim_scope(user_id: str, scope: str):
    if scope not in ("all", "assigned", "none"):
        raise ValueError(scope)
    with engine().begin() as c:
        c.execute(update(users).where(users.c.id == user_id).values(tim_scope=scope))


def set_lang(user_id: str, lang: str):
    if lang not in ("en", "es"):
        raise ValueError(lang)
    with engine().begin() as c:
        c.execute(update(users).where(users.c.id == user_id).values(lang=lang))


def list_users():
    with engine().connect() as c:
        return list(c.execute(select(users).order_by(users.c.email)).mappings())


# ── Connections (encrypted) ────────────────────────────────────────────────────
def save_connection(user_id: str, provider: str, secret: str, account_email: str = None,
                    scopes: str = None):
    """Store/replace a user's credential for one provider — encrypted before insert."""
    enc = vault.encrypt(secret)
    with engine().begin() as c:
        c.execute(delete(connections).where(and_(connections.c.user_id == user_id,
                                                 connections.c.provider == provider)))
        c.execute(insert(connections).values(
            id=_uuid(), user_id=user_id, provider=provider, account_email=account_email,
            secret_enc=enc, scopes=scopes, status="connected", connected_at=now()))


def get_connection(user_id: str, provider: str):
    with engine().connect() as c:
        return c.execute(select(connections).where(and_(
            connections.c.user_id == user_id, connections.c.provider == provider))).mappings().first()


def get_secret(user_id: str, provider: str):
    row = get_connection(user_id, provider)
    if not row or not row["secret_enc"] or row["status"] == "revoked":
        return None
    return vault.decrypt(row["secret_enc"])


def update_secret(user_id: str, provider: str, secret: str):
    """Write back a refreshed token cache (tokens rotate on every refresh)."""
    with engine().begin() as c:
        c.execute(update(connections).where(and_(
            connections.c.user_id == user_id, connections.c.provider == provider))
            .values(secret_enc=vault.encrypt(secret)))


def mark_connection(user_id: str, provider: str, ok: bool, error: str = None):
    vals = {"status": "connected", "last_ok_at": now(), "last_error": None} if ok \
        else {"status": "error", "last_error": (error or "")[:2000]}
    with engine().begin() as c:
        c.execute(update(connections).where(and_(
            connections.c.user_id == user_id, connections.c.provider == provider)).values(**vals))


def disconnect(user_id: str, provider: str):
    """Delete the credential AND everything derived from it, immediately."""
    with engine().begin() as c:
        c.execute(delete(connections).where(and_(connections.c.user_id == user_id,
                                                 connections.c.provider == provider)))
        item_ids = select(items.c.id).where(and_(items.c.user_id == user_id, items.c.source == provider))
        c.execute(delete(drafts).where(and_(drafts.c.user_id == user_id, drafts.c.item_id.in_(item_ids))))
        c.execute(delete(items).where(and_(items.c.user_id == user_id, items.c.source == provider)))
        c.execute(delete(source_sync).where(and_(source_sync.c.user_id == user_id,
                                                 source_sync.c.source == provider)))


def purge_user_data(user_id: str):
    """Offboarding: remove all credentials, items and drafts for a user."""
    with engine().begin() as c:
        for t in (drafts, items, source_sync, connections):
            c.execute(delete(t).where(t.c.user_id == user_id))


def users_to_scan(provider: str):
    """Active users with a working connection for this provider (worker use)."""
    with engine().connect() as c:
        q = (select(users.c.id, users.c.email, users.c.name)
             .select_from(users.join(connections, connections.c.user_id == users.c.id))
             .where(and_(users.c.active.is_(True), connections.c.provider == provider,
                         connections.c.status != "revoked")))
        return list(c.execute(q).mappings())


# ── WhatsApp sync key (browser extension → server) ──────────────────────────
def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_whatsapp_key(user_id: str) -> str:
    """Issue a fresh sync key. Only its hash is stored; the key is shown once."""
    key = "wa_" + secrets.token_urlsafe(32)
    with engine().begin() as c:
        c.execute(delete(connections).where(and_(connections.c.user_id == user_id,
                                                 connections.c.provider == "whatsapp")))
        c.execute(insert(connections).values(
            id=_uuid(), user_id=user_id, provider="whatsapp", account_email=None,
            secret_enc=_hash_key(key).encode(), status="connected", connected_at=now()))
    return key


def user_for_whatsapp_key(key: str):
    if not key or not key.startswith("wa_"):
        return None
    h = _hash_key(key).encode()
    with engine().connect() as c:
        row = c.execute(select(connections.c.user_id, connections.c.secret_enc).where(
            connections.c.provider == "whatsapp")).mappings().all()
    for r in row:
        if r["secret_enc"] and secrets.compare_digest(bytes(r["secret_enc"]), h):
            u = get_user(r["user_id"])
            return u if (u and u["active"] and u["whatsapp_opt_in"]) else None
    return None


# ── Items ──────────────────────────────────────────────────────────────────────
def replace_items(user_id: str, source: str, new_items: list):
    """Make this user's items for this source exactly new_items.

    Keeps the "seen" flag (and any drafts) for items that are still present,
    drops items that no longer qualify, and stamps the source's "as of" time.
    """
    with engine().begin() as c:
        existing = {(r["external_id"], r["kind"]): r for r in c.execute(
            select(items).where(and_(items.c.user_id == user_id, items.c.source == source))).mappings()}
        keep = set()
        for it in new_items:
            key = (it["external_id"], it["kind"])
            keep.add(key)
            fields = {k: it.get(k) for k in ("from_name", "from_addr", "subject", "snippet",
                                             "url", "received_at")}
            fields["meta"] = json.dumps(it.get("meta") or {}, default=str)
            if key in existing:
                c.execute(update(items).where(items.c.id == existing[key]["id"])
                          .values(**fields, updated_at=now()))
            else:
                c.execute(insert(items).values(id=_uuid(), user_id=user_id, source=source,
                                               kind=it["kind"], external_id=it["external_id"],
                                               updated_at=now(), **fields))
        stale = [r["id"] for k, r in existing.items() if k not in keep]
        if stale:
            c.execute(delete(drafts).where(and_(drafts.c.user_id == user_id, drafts.c.item_id.in_(stale))))
            c.execute(delete(items).where(and_(items.c.user_id == user_id, items.c.id.in_(stale))))
        _stamp_sync(c, user_id, source)


def _stamp_sync(c, user_id, source):
    c.execute(delete(source_sync).where(and_(source_sync.c.user_id == user_id,
                                             source_sync.c.source == source)))
    c.execute(insert(source_sync).values(user_id=user_id, source=source, synced_at=now()))


def list_items(user_id: str, source: str = None):
    with engine().connect() as c:
        q = select(items).where(items.c.user_id == user_id)
        if source:
            q = q.where(items.c.source == source)
        return list(c.execute(q.order_by(items.c.received_at.desc())).mappings())


def get_item(user_id: str, item_id: str):
    with engine().connect() as c:
        return c.execute(select(items).where(and_(items.c.user_id == user_id,
                                                  items.c.id == item_id))).mappings().first()


def mark_seen(user_id: str, item_id: str, seen: bool = True):
    with engine().begin() as c:
        c.execute(update(items).where(and_(items.c.user_id == user_id, items.c.id == item_id))
                  .values(seen=seen))


def sync_times(user_id: str) -> dict:
    with engine().connect() as c:
        return {r["source"]: r["synced_at"] for r in c.execute(
            select(source_sync).where(source_sync.c.user_id == user_id)).mappings()}


# ── Drafts ─────────────────────────────────────────────────────────────────────
def save_draft(user_id: str, item_id: str, body: str, status: str = "suggested",
               provider_draft_id: str = None) -> str:
    if not get_item(user_id, item_id):
        raise PermissionError("Item does not belong to this user")
    did = _uuid()
    with engine().begin() as c:
        c.execute(insert(drafts).values(id=did, user_id=user_id, item_id=item_id, body=body,
                                        status=status, provider_draft_id=provider_draft_id,
                                        created_at=now()))
    return did


def list_drafts(user_id: str, item_id: str = None):
    with engine().connect() as c:
        q = select(drafts).where(drafts.c.user_id == user_id)
        if item_id:
            q = q.where(drafts.c.item_id == item_id)
        return list(c.execute(q.order_by(drafts.c.created_at.desc())).mappings())


def update_draft(user_id: str, draft_id: str, **vals):
    allowed = {k: v for k, v in vals.items() if k in ("body", "status", "provider_draft_id")}
    with engine().begin() as c:
        c.execute(update(drafts).where(and_(drafts.c.user_id == user_id, drafts.c.id == draft_id))
                  .values(**allowed))


# ── Runs (audit / health) ──────────────────────────────────────────────────────
def start_run(trigger: str = "schedule") -> str:
    rid = _uuid()
    with engine().begin() as c:
        c.execute(insert(runs).values(id=rid, trigger=trigger, started_at=now()))
    return rid


def finish_run(run_id: str, users_scanned: int, errors: int, note: str = None):
    with engine().begin() as c:
        c.execute(update(runs).where(runs.c.id == run_id).values(
            finished_at=now(), users_scanned=users_scanned, errors=errors, note=note))


def recent_runs(limit: int = 20):
    with engine().connect() as c:
        return list(c.execute(select(runs).order_by(runs.c.started_at.desc()).limit(limit)).mappings())


def connection_health():
    """Admin view: every user with each provider's status and last good scan."""
    with engine().connect() as c:
        q = (select(users.c.id, users.c.email, users.c.name, users.c.active, users.c.role,
                    connections.c.provider, connections.c.status, connections.c.last_ok_at,
                    connections.c.last_error)
             .select_from(users.outerjoin(connections, connections.c.user_id == users.c.id))
             .order_by(users.c.email))
        return list(c.execute(q).mappings())
