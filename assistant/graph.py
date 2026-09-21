"""
Microsoft Graph for the assistant — per-user, delegated, refreshable.

At "Connect mailbox" time the library's Microsoft sign-in hands us the MSAL token
cache (which holds the user's refresh token). We store that cache ENCRYPTED in
asst_connections and, on every scan, rebuild an MSAL client from it, silently
get a fresh access token, and write the rotated cache back.

Scopes: ASSISTANT_MS_SCOPES (default "User.Read Mail.Read"). Add Mail.ReadWrite
once IT has admin-consented it, to enable "Save draft to Outlook".
offline_access (→ refresh token) is added by MSAL automatically.
"""

import datetime as _dt
import os

import requests

from . import db

GRAPH = "https://graph.microsoft.com/v1.0"
LOOKBACK_DAYS = int(os.environ.get("ASSISTANT_LOOKBACK_DAYS", "7"))
MAX_MESSAGES = int(os.environ.get("ASSISTANT_MAX_MESSAGES", "50"))


class ReconnectNeeded(RuntimeError):
    """Stored credential no longer works — user must reconnect."""


def scopes() -> list:
    raw = os.environ.get("ASSISTANT_MS_SCOPES", "User.Read Mail.Read")
    return [s for s in raw.replace(",", " ").split() if s]


def can_write_drafts() -> bool:
    return "Mail.ReadWrite" in scopes()


def _app(cache=None):
    import msal
    tenant = os.environ.get("MS_TENANT_ID", "").strip()
    return msal.ConfidentialClientApplication(
        os.environ.get("MS_CLIENT_ID", "").strip(),
        authority=f"https://login.microsoftonline.com/{tenant}",
        client_credential=os.environ.get("MS_CLIENT_SECRET", "").strip(),
        token_cache=cache,
    )


def new_cache():
    import msal
    return msal.SerializableTokenCache()


def store_cache(user_id: str, cache, account_email: str):
    db.save_connection(user_id, "microsoft", cache.serialize(), account_email=account_email,
                       scopes=" ".join(scopes()))


def access_token(user_id: str) -> str:
    """Fresh access token for this user's mailbox (refreshing + persisting as needed)."""
    import msal
    raw = db.get_secret(user_id, "microsoft")
    if not raw:
        raise ReconnectNeeded("Mailbox not connected")
    cache = msal.SerializableTokenCache()
    cache.deserialize(raw)
    app = _app(cache)
    accounts = app.get_accounts()
    if not accounts:
        raise ReconnectNeeded("No account in stored credential")
    result = app.acquire_token_silent(scopes(), account=accounts[0])
    if cache.has_state_changed:
        db.update_secret(user_id, "microsoft", cache.serialize())
    if not result or "access_token" not in result:
        err = (result or {}).get("error_description") or (result or {}).get("error") or "refresh failed"
        raise ReconnectNeeded(err)
    return result["access_token"]


def _get(token, path, params=None):
    r = requests.get(GRAPH + path, headers={"Authorization": f"Bearer {token}"},
                     params=params, timeout=30)
    if r.status_code == 401:
        raise ReconnectNeeded("Graph rejected the token (401)")
    r.raise_for_status()
    return r.json()


def fetch_inbox(user_id: str) -> list:
    token = access_token(user_id)
    since = (_dt.datetime.now(_dt.timezone.utc)
             - _dt.timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = _get(token, "/me/mailFolders/inbox/messages", {
        "$select": "id,subject,from,receivedDateTime,isRead,webLink,bodyPreview,flag,importance",
        "$top": str(MAX_MESSAGES),
        "$orderby": "receivedDateTime desc",
        "$filter": f"receivedDateTime ge {since}",
    })
    return data.get("value", [])


def get_message_text(user_id: str, message_id: str) -> str:
    """Full body (text) of one message — only fetched on demand for drafting."""
    token = access_token(user_id)
    r = requests.get(f"{GRAPH}/me/messages/{message_id}",
                     headers={"Authorization": f"Bearer {token}",
                              "Prefer": 'outlook.body-content-type="text"'},
                     params={"$select": "subject,from,body,receivedDateTime"}, timeout=30)
    if r.status_code == 401:
        raise ReconnectNeeded("Graph rejected the token (401)")
    r.raise_for_status()
    return ((r.json().get("body") or {}).get("content") or "")[:8000]


def create_reply_draft(user_id: str, message_id: str, body_text: str) -> dict:
    """Create a reply DRAFT in the user's Outlook Drafts. Never sends."""
    token = access_token(user_id)
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = requests.post(f"{GRAPH}/me/messages/{message_id}/createReply", headers=h,
                      json={"comment": body_text}, timeout=30)
    if r.status_code in (401, 403):
        raise PermissionError("Your mailbox connection doesn't allow creating drafts yet "
                              "(Mail.ReadWrite not granted).")
    r.raise_for_status()
    d = r.json()
    return {"id": d.get("id"), "webLink": d.get("webLink")}
