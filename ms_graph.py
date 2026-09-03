"""
ms_graph.py — read the TMS coordinators' mailboxes via Microsoft Graph (app-only).

Production path for the under-billing detector. Authenticates as the Azure app
Cesar registered ("Thelsa AI Personal Assistant"), which has application
Mail.ReadWrite scoped by RBAC to the AI-Assistant-Users group (the 20 mailboxes).
We read only the 12 TMS coordinator mailboxes and only "FINAL CHARGES" threads.

Credential (app-only, client_credentials). No secret is ever hard-coded. It is
resolved at import from env, preferring a dedicated app but FALLING BACK to the
engine's existing Graph credential so nothing new needs configuring:
    1. MS_TENANT_ID / MS_CLIENT_ID / MS_CLIENT_SECRET   (dedicated app, if secret set)
    2. GRAPH_TENANT_ID / GRAPH_CLIENT_ID / GRAPH_CLIENT_SECRET   (the SAME app-only
       credential engine/mailer.py already uses to read/write employee mailboxes)

Everything degrades gracefully: with no secret in either set, have_ms_creds() is
False and the fetch functions return [] so the dashboard shows "waiting for access".
"""
from __future__ import annotations

import os
import time

import requests

MS_TENANT_DEFAULT = "b054f5d3-0c08-46c0-8e80-cc38cbd9e58e"
MS_CLIENT_DEFAULT = "8210a2b4-4bc5-4eef-abbc-03ac092ff11f"


def _resolve_creds():
    """Pick the app-only Graph credential to read coordinator mailboxes with.

    Preference order:
      1. Dedicated MS_* vars (the "Thelsa AI Personal Assistant" app), if a secret
         is set — tenant/client fall back to the known app defaults.
      2. The engine's EXISTING app-only credential (GRAPH_TENANT_ID / GRAPH_CLIENT_ID
         / GRAPH_CLIENT_SECRET) — the same app the mailer / lead-gen already use to
         read employee mailboxes. Reusing it means no separate secret to configure.
    Each option is a MATCHED triplet — we never mix a secret from one app with the
    client id of another.
    """
    if os.environ.get("MS_CLIENT_SECRET"):
        return (os.environ.get("MS_TENANT_ID", MS_TENANT_DEFAULT).strip(),
                os.environ.get("MS_CLIENT_ID", MS_CLIENT_DEFAULT).strip(),
                os.environ["MS_CLIENT_SECRET"].strip())
    if os.environ.get("GRAPH_CLIENT_SECRET"):
        return (os.environ.get("GRAPH_TENANT_ID", "").strip(),
                os.environ.get("GRAPH_CLIENT_ID", "").strip(),
                os.environ.get("GRAPH_CLIENT_SECRET", "").strip())
    return (os.environ.get("MS_TENANT_ID", MS_TENANT_DEFAULT).strip(),
            os.environ.get("MS_CLIENT_ID", MS_CLIENT_DEFAULT).strip(),
            "")


MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET = _resolve_creds()

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


# Diagnostics from the most recent fetch, surfaced on the dashboard so a silent
# 0-result is explainable (token failure vs per-mailbox 403 vs simply no matches).
LAST_DIAG = {"mailboxes": 0, "ok": 0, "forbidden": 0, "notfound": 0,
             "other": 0, "hits": 0, "token_ok": None, "first_error": None}


def _short(r) -> str:
    try:
        j = r.json()
        return (((j.get("error") or {}).get("message")) or r.text or "")[:140]
    except Exception:
        return (r.text or "")[:140]


def fetch_final_charges(days: int = 400, per_mailbox: int = 50) -> list[dict]:
    """Return recent 'FINAL CHARGES' / 'CARGOS FINALES' messages across the 12 TMS
    coordinator mailboxes: {subject, body, sender, date, conversationId, mailbox}.
    Records per-mailbox outcome in LAST_DIAG. [] if no creds/token."""
    global LAST_DIAG
    diag = {"mailboxes": len(TMS_COORDINATORS), "ok": 0, "forbidden": 0,
            "notfound": 0, "other": 0, "hits": 0, "token_ok": False, "first_error": None}
    tok = _get_token()
    diag["token_ok"] = bool(tok)
    if not tok:
        diag["first_error"] = "token request failed — check the GRAPH_* / MS_* tenant, client id and secret"
        LAST_DIAG = diag
        return []
    h = {"Authorization": f"Bearer {tok}", "ConsistencyLevel": "eventual"}
    out = []
    for mbx in TMS_COORDINATORS:
        params = {
            "$search": '"subject:FINAL CHARGES" OR "subject:CARGOS FINALES"',
            "$select": "subject,from,receivedDateTime,conversationId,body,bodyPreview",
            "$top": str(per_mailbox),
        }
        try:
            r = requests.get(f"{GRAPH}/users/{mbx}/messages", headers=h,
                             params=params, timeout=_TIMEOUT)
        except Exception as e:
            diag["other"] += 1
            diag["first_error"] = diag["first_error"] or f"{mbx}: {e}"
            continue
        if r.status_code == 200:
            diag["ok"] += 1
            vals = r.json().get("value", [])
            diag["hits"] += len(vals)
            for m in vals:
                out.append({
                    "subject": m.get("subject", ""),
                    "body": _plain(m),
                    "sender": ((m.get("from") or {}).get("emailAddress") or {}).get("address", mbx),
                    "date": m.get("receivedDateTime"),
                    "conversationId": m.get("conversationId"),
                    "mailbox": mbx,
                })
        elif r.status_code in (401, 403):
            diag["forbidden"] += 1
            diag["first_error"] = diag["first_error"] or f"{mbx}: {r.status_code} {_short(r)}"
        elif r.status_code == 404:
            diag["notfound"] += 1
            diag["first_error"] = diag["first_error"] or f"{mbx}: 404 mailbox not found"
        else:
            diag["other"] += 1
            diag["first_error"] = diag["first_error"] or f"{mbx}: {r.status_code} {_short(r)}"
    LAST_DIAG = diag
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
