"""
store.py — where the commission report keeps what people give it.

Three small documents, each stored whole under a key:

    finance_report   the lines read from Finance's margin report (no names)
    bookings         bookings per month, as typed in
    uploads          who uploaded what and when

They go in the application's database (Render Postgres, DATABASE_URL), in a
table of their own, so they survive a redeploy. Without a database they go in a
local SQLite file, which works but is lost when the service is rebuilt;
`durable()` says which, and the page tells the user.

Finance's workbook itself is never stored: it carries customer names.
"""
from __future__ import annotations

import datetime as dt
import json
import os

from sqlalchemy import Column, DateTime, MetaData, String, Table, Text, create_engine, insert, select, update

_meta = MetaData()
_docs = Table(
    "commission_docs", _meta,
    Column("key", String(80), primary_key=True),
    Column("body", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("updated_by", String(320), nullable=False, default=""),
)
_engine = None


def _url() -> str:
    url = (os.environ.get("COMMISSION_DATABASE_URL") or os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
        return "sqlite:///" + os.path.abspath(os.path.join(base, "commissions.db"))
    for prefix in ("postgres://", "postgresql://"):       # Render hands out postgres://
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def durable() -> bool:
    """True when the data is in a real database and survives a redeploy."""
    return not _url().startswith("sqlite:")


def engine():
    global _engine
    if _engine is None:
        url = _url()
        if url.startswith("sqlite:///"):
            os.makedirs(os.path.dirname(url[len("sqlite:///"):]) or ".", exist_ok=True)
        _engine = create_engine(url, pool_pre_ping=True, future=True)
        _meta.create_all(_engine)
    return _engine


def reset_for_tests() -> None:
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def get(key: str, default=None):
    """(document, {"at": datetime, "by": email}) or (default, None)."""
    with engine().connect() as c:
        row = c.execute(select(_docs.c.body, _docs.c.updated_at, _docs.c.updated_by)
                        .where(_docs.c.key == key)).first()
    if row is None:
        return default, None
    return json.loads(row[0]), {"at": row[1], "by": row[2]}


def put(key: str, doc, by: str = "") -> None:
    body = json.dumps(doc, separators=(",", ":"), allow_nan=False)
    now = dt.datetime.now(dt.timezone.utc)
    with engine().begin() as c:
        done = c.execute(update(_docs).where(_docs.c.key == key)
                         .values(body=body, updated_at=now, updated_by=by)).rowcount
        if not done:
            c.execute(insert(_docs).values(key=key, body=body, updated_at=now, updated_by=by))
