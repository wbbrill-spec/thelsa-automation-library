"""
sheets.py — service-account read/write layer over the master workbook.

read_tab() returns rows as dicts, each carrying its 1-based sheet row number in
'_row' so callers can update specific cells. All writes go through the Sheets
API using the service account (GOOGLE_SA_B64), which has Editor on the workbook.
"""
import base64
import json
import os
from datetime import datetime, timezone

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from . import config

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_svc_cache = None


def _svc():
    global _svc_cache
    if _svc_cache is None:
        b64 = os.environ["GOOGLE_SA_B64"]
        info = json.loads(base64.b64decode(b64).decode())
        creds = Credentials.from_service_account_info(info, scopes=_SCOPES)
        _svc_cache = build("sheets", "v4", credentials=creds, cache_discovery=False).spreadsheets()
    return _svc_cache


def read_tab(tab):
    """Return list[dict]; each dict has the header keys plus '_row' (1-based)."""
    resp = _svc().values().get(spreadsheetId=config.SHEET_ID, range=f"'{tab}'!A1:ZZ").execute()
    rows = resp.get("values", [])
    if not rows:
        return []
    hdr = rows[0]
    out = []
    for i, r in enumerate(rows[1:], start=2):
        r = r + [""] * (len(hdr) - len(r))
        d = {hdr[j]: r[j] for j in range(len(hdr))}
        d["_row"] = i
        out.append(d)
    return out


def _header(tab):
    resp = _svc().values().get(spreadsheetId=config.SHEET_ID, range=f"'{tab}'!1:1").execute()
    return (resp.get("values", [[]]) or [[]])[0]


def _col_letter(n):  # 0-based -> A, B, ...
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def update_fields(tab, row, fields: dict):
    """Update named columns in a given 1-based row. fields = {col_name: value}."""
    hdr = _header(tab)
    data = []
    for name, val in fields.items():
        if name in hdr:
            c = _col_letter(hdr.index(name))
            data.append({"range": f"'{tab}'!{c}{row}", "values": [[val]]})
    if data:
        _svc().values().batchUpdate(
            spreadsheetId=config.SHEET_ID,
            body={"valueInputOption": "RAW", "data": data}).execute()


def append_row(tab, values_by_name: dict):
    """Append a row, placing values under the matching header columns."""
    hdr = _header(tab)
    row = [values_by_name.get(h, "") for h in hdr]
    _svc().values().append(
        spreadsheetId=config.SHEET_ID, range=f"'{tab}'!A1",
        valueInputOption="RAW", insertDataOption="INSERT_ROWS",
        body={"values": [row]}).execute()


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── convenience writers ──────────────────────────────────────────────────────
def log_activity(campaign_id, email, event_type, detail="", message_id="", content_version=""):
    append_row("activity_log", {
        "timestamp": now_iso(), "campaign_id": campaign_id, "email": email,
        "event_type": event_type, "detail": detail, "message_id": message_id,
        "content_version": content_version})


def log_run(run_id, campaign_id, task, status, counts="", error=""):
    append_row("runs", {
        "run_id": run_id, "timestamp": now_iso(), "campaign_id": campaign_id,
        "task": task, "status": status, "counts": counts, "error": error})


def processed_message_ids():
    """Set of message ids already in activity_log (idempotency ledger)."""
    return {r.get("message_id") for r in read_tab("activity_log") if r.get("message_id")}
