"""
ms_graph.py — read the TMS coordinators' mailboxes via Microsoft Graph (app-only).

Production path for the under-billing detector. Authenticates as the Azure app
Cesar registered ("Thelsa AI Personal Assistant"), which has application
Mail.ReadWrite scoped by RBAC to the AI-Assistant-Users group (the 20 mailboxes).
We read only the 12 TMS coordinator mailboxes and only "FINAL CHARGES" threads.

Config via env (tenant + client id default to the known app; the SECRET must be
supplied and is never hard-coded):
    MS_TENANT_ID       (default: the Thelsa tenant)
    MS_CLIENT_ID       (default: the Thelsa AI Personal Assistant app)
    MS_CLIENT_SECRET   (REQUIRED — from Bill's Bitwarden vault; set on Render)

Everything degrades gracefully: with no secret, have_ms_creds() is False and the
fetch functions return [] so the dashboard simply shows "waiting for access".
"""
from __future__ import annotations

import os
import time

import requests

MS_TENANT_ID = os.environ.get("MS_TENANT_ID", "b054f5d3-0c08-46c0-8e80-cc38cbd9e58e")
MS_CLIENT_ID = os.environ.get("MS_CLIENT_ID", "8210a2b4-4bc5-4eef-abbc-03ac092ff11f")
MS_CLIENT_SECRET = os.environ.get("MS_CLIENT_SECRET", "")

GRAPH = "https://graph.microsoft.com/v1.0"
_TIMEOUT = 20

# The 12 TMS coordinators (TMS shipments run in MoveWare = company 64000). TIM
# coordinators run in ClickUp and are intentionally excluded here.
TMS_COORDINATORS = [
    "sarareyes@thelsa.com", "edwuinlopez@thelsa.com", "edgarespino@thelsa.com",
    "elizabethhernandez@thelsa.com", "wendyhernandez@thelsa.com",
    "monicaescalante@thelsa.com", "pablomartinez@thelsa.com",
    "stephaniebarraza@thelsa.com", "maria.gonzalez@thelsa.com",
    "mariacarrasco@thelsa.com", "guillermomonroy@thelsa.com", "victorjasso@thelsa.com",
]

_token = {"value": None, "exp": 0}


def have_ms_creds() -> bool:
    return bool(MS_CLIENT_SECRET and MS_TENANT_ID and MS_CLIENT_ID)


def _get_token() -> str | None:
    if not have_ms_creds():
        return None
    if _token["value"] and time.time() < _token["exp"] - 60:
        return _token["value"]
    url = f"https://login.microsoftonline.com/{MS_TENANT_ID}/oauth2/v2.0/token"
    data = {
        "client_id": MS_CLIENT_ID,
        "client_secret": MS_CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }
    try:
        r = requests.post(url, data=data, timeout=_TIMEOUT)
        r.raise_for_status()
        j = r.json()
    except Exception:
        return None
    _token["value"] = j.get("access_token")
    _token["exp"] = time.time() + int(j.get("expires_in", 3000))
    return _token["value"]


def _headers():
    tok = _get_token()
    return {"Authorization": f"Bearer {tok}"} if tok else None


def _plain(msg: dict) -> str:
    body = (msg.get("body") or {})
    content = body.get("content") or msg.get("bodyPreview") or ""
    if (body.get("contentType") or "").lower() == "html":
        # light HTML strip — enough for amount/approval parsing
        import re
        content = re.sub(r"<[^>]+>", " ", content)
        content = re.sub(r"&nbsp;|&#160;", " ", content)
        content = re.sub(r"&amp;", "&", content)
    return content


def fetch_final_charges(days: int = 400, per_mailbox: int = 50) -> list[dict]:
    """Return recent 'FINAL CHARGES' messages across the 12 TMS coordinator
    mailboxes: {subject, body, sender, date, conversationId, mailbox}. [] if no creds."""
    h = _headers()
    if not h:
        return []
    out = []
    for mbx in TMS_COORDINATORS:
        # $search on subject; Graph requires ConsistencyLevel:eventual for $search
        params = {
            "$search": '"subject:FINAL CHARGES"',
            "$select": "subject,from,receivedDateTime,conversationId,body,bodyPreview",
            "$top": str(per_mailbox),
        }
        try:
            r = requests.get(f"{GRAPH}/users/{mbx}/messages",
                             headers={**h, "ConsistencyLevel": "eventual"},
                             params=params, timeout=_TIMEOUT)
            if r.status_code != 200:
                continue
            for m in r.json().get("value", []):
                out.append({
                    "subject": m.get("subject", ""),
                    "body": _plain(m),
                    "sender": ((m.get("from") or {}).get("emailAddress") or {}).get("address", mbx),
                    "date": m.get("receivedDateTime"),
                    "conversationId": m.get("conversationId"),
                    "mailbox": mbx,
                })
        except Exception:
            continue
    return out


def fetch_thread_texts(mailbox: str, conversation_id: str, limit: int = 25) -> list[str]:
    """Bodies of every message in a conversation (to look for the client approval)."""
    h = _headers()
    if not h or not conversation_id:
        return []
    # escape single quotes for OData
    cid = conversation_id.replace("'", "''")
    params = {
        "$filter": f"conversationId eq '{cid}'",
        "$select": "body,bodyPreview,from,receivedDateTime",
        "$top": str(limit),
    }
    try:
        r = requests.get(f"{GRAPH}/users/{mailbox}/messages",
                         headers=h, params=params, timeout=_TIMEOUT)
        if r.status_code != 200:
            return []
        return [_plain(m) for m in r.json().get("value", [])]
    except Exception:
        return []
