"""
mailer.py — Microsoft Graph adapter for the bbrill@thelsa.com mailbox.

App-only auth (client credentials). Used to:
  - create_draft()  : create a draft for Bill to review + send manually
  - list_sent()     : reconcile what's already been sent (Sent Items)
  - scan_inbox()    : read recent inbound mail for the monitor task

Requires the Entra app registration with Graph Mail.ReadWrite (application),
admin-consented and scoped to only MAILBOX. No Mail.Send is used — the engine
never sends, it only drafts.
"""
import os

import requests

from . import config

GRAPH = "https://graph.microsoft.com/v1.0"


def render_body(template_body):
    """Templates store paragraph breaks as the literal two chars '\\n'. Convert
    them to real newlines for the draft."""
    return (template_body or "").replace("\\n", "\n")


class GraphMailer:
    def __init__(self):
        # .strip() guards against a stray space/newline pasted into the env var.
        self.tenant = os.environ["GRAPH_TENANT_ID"].strip()
        self.client_id = os.environ["GRAPH_CLIENT_ID"].strip()
        self.secret = os.environ["GRAPH_CLIENT_SECRET"].strip()
        self.mailbox = config.MAILBOX
        self._tok = None

    def _token(self):
        if self._tok:
            return self._tok
        r = requests.post(
            f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/token",
            data={"client_id": self.client_id, "client_secret": self.secret,
                  "grant_type": "client_credentials",
                  "scope": "https://graph.microsoft.com/.default"}, timeout=30)
        if r.status_code != 200:
            # Surface Microsoft's exact reason (AADSTS code) instead of a bare 401.
            detail = ""
            try:
                j = r.json()
                detail = j.get("error_description", "") or j.get("error", "")
            except Exception:
                detail = r.text[:600]
            raise RuntimeError(f"Microsoft token request failed [{r.status_code}]: "
                               f"{detail.splitlines()[0] if detail else r.text[:300]}")
        self._tok = r.json()["access_token"]
        return self._tok

    def _h(self):
        return {"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"}

    def create_draft(self, to_email, subject, body_text):
        """Create a draft in the mailbox. Returns {'id', 'webLink'}."""
        msg = {
            "subject": subject,
            "body": {"contentType": "Text", "content": render_body(body_text)},
            "toRecipients": [{"emailAddress": {"address": to_email}}],
        }
        r = requests.post(f"{GRAPH}/users/{self.mailbox}/messages",
                          headers=self._h(), json=msg, timeout=30)
        if r.status_code >= 300:
            raise RuntimeError(f"Graph create_draft failed [{r.status_code}]: {r.text[:400]}")
        d = r.json()
        return {"id": d.get("id", ""), "webLink": d.get("webLink", "")}

    def list_sent(self, since_iso):
        """Recipient addresses we've sent to since `since_iso` (Sent Items)."""
        url = (f"{GRAPH}/users/{self.mailbox}/mailFolders/SentItems/messages"
               f"?$select=toRecipients,sentDateTime&$top=200"
               f"&$filter=sentDateTime ge {since_iso}")
        out = []
        while url:
            r = requests.get(url, headers=self._h(), timeout=30)
            r.raise_for_status()
            j = r.json()
            for m in j.get("value", []):
                for rcpt in m.get("toRecipients", []):
                    a = rcpt.get("emailAddress", {}).get("address", "").lower()
                    if a:
                        out.append(a)
            url = j.get("@odata.nextLink")
        return out

    def scan_inbox(self, since_iso):
        """Recent inbound messages. Returns list of dicts:
        {message_id, from, subject, body, received}."""
        url = (f"{GRAPH}/users/{self.mailbox}/mailFolders/Inbox/messages"
               f"?$select=internetMessageId,from,subject,bodyPreview,receivedDateTime"
               f"&$top=100&$orderby=receivedDateTime desc"
               f"&$filter=receivedDateTime ge {since_iso}")
        out = []
        r = requests.get(url, headers=self._h(), timeout=30)
        r.raise_for_status()
        for m in r.json().get("value", []):
            out.append({
                "message_id": m.get("internetMessageId", ""),
                "from": m.get("from", {}).get("emailAddress", {}).get("address", "").lower(),
                "subject": m.get("subject", ""),
                "body": m.get("bodyPreview", ""),
                "received": m.get("receivedDateTime", ""),
            })
        return out
