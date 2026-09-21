"""
Mail triage rules — provider-neutral.

Provider adapters (Graph, Gmail) turn raw messages into a common "message" dict:
    {id, from_name, from_addr, subject, snippet, url, received_at (datetime),
     is_read, is_flagged}
and triage() turns those into dashboard items:
    needs_reply — unread, from a real person (not an automated sender)
    flagged     — flagged / starred for follow-up
Same rules as the original on-demand /assistant page.
"""

import datetime as _dt

AUTOMATED_HINTS = (
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply",
    "notifications", "notification", "notify", "mailer", "mailer-daemon",
    "postmaster", "bounce", "bounces", "automated", "auto-confirm",
    "alerts", "alert@", "updates@", "news@", "newsletter", "marketing@",
    "billing@", "receipts@", "support@microsoft", "account-security-noreply",
)


def is_automated(addr: str) -> bool:
    a = (addr or "").lower()
    return any(h in a for h in AUTOMATED_HINTS)


def _parse_iso(iso: str):
    if not iso:
        return None
    try:
        return _dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def normalize_graph(m: dict) -> dict:
    """Microsoft Graph message → common message dict."""
    frm = (m.get("from") or {}).get("emailAddress") or {}
    return {
        "id": m.get("id"),
        "from_name": frm.get("name") or frm.get("address") or "Unknown",
        "from_addr": (frm.get("address") or "").lower(),
        "subject": m.get("subject") or "(no subject)",
        "snippet": (m.get("bodyPreview") or "").strip()[:500],
        "url": m.get("webLink"),
        "received_at": _parse_iso(m.get("receivedDateTime")),
        "is_read": bool(m.get("isRead")),
        "is_flagged": (m.get("flag") or {}).get("flagStatus") == "flagged",
        "importance": (m.get("importance") or "normal").lower(),
    }


def triage(messages: list, own_address: str = None) -> list:
    """Common message dicts → dashboard item dicts (for db.replace_items)."""
    own = (own_address or "").lower()
    out = []
    for m in messages:
        if not m.get("id"):
            continue
        base = {k: m.get(k) for k in ("from_name", "from_addr", "subject", "snippet",
                                      "url", "received_at")}
        base["external_id"] = m["id"]
        base["meta"] = {"importance": m.get("importance") or "normal"}
        if m.get("is_flagged"):
            out.append({**base, "kind": "flagged"})
        if (not m.get("is_read") and not is_automated(m.get("from_addr"))
                and m.get("from_addr") != own):
            out.append({**base, "kind": "needs_reply"})
    return out
