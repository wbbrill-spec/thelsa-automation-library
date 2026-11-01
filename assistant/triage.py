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
        "conversation_id": m.get("conversationId") or "",
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



# ── Chasers waiting on you ────────────────────────────────────────────────────
# Every case found in Bill's mail on 1 Nov 2026 had the other side asking again —
# "any update?", "following up", "favor de confirmar", "sigo esperando" — and a
# chaser that has been READ but not answered vanished from the old "unread" rule.
# A chaser stays on the dashboard, read or not, until the user writes in that
# conversation after it.
CHASER_HINTS = (
    "any update", "any news", "please update", "update us", "following up", "follow up", "follow-up", "still waiting", "gentle reminder",
    "kind reminder", "friendly reminder", "reminder", "second request", "2nd request",
    "as per my previous", "as per my last", "per my last email", "have you had a chance",
    "please advise", "kindly advise", "please confirm", "urgent", "awaiting your",
    "waiting for your", "chasing", "no response", "haven't heard", "have not heard",
    "seguimiento", "recordatorio", "sigo esperando", "seguimos esperando", "quedo en espera",
    "favor de confirmar", "me puedes confirmar", "pueden confirmar", "alguna novedad", "alguna noticia", "algún avance", "algun avance",
    "alguna actualización", "alguna actualizacion", "urgente", "sin respuesta",
    "no he recibido", "no hemos recibido", "segundo aviso", "reenvío", "reenvio",
)


def is_chaser(subject: str, preview: str) -> bool:
    text = f"{subject or ''} {preview or ''}".lower()
    return any(h in text for h in CHASER_HINTS)


def chasers(messages: list, sent: list, own_address: str = None) -> list:
    """Chasers from real people that the user has not answered since.

    `messages`: normalized inbox messages (with conversation_id); `sent`: the
    user's sent mail [{conversationId, sentDateTime}]. One item per
    conversation (the latest chaser), so a thread chased three times is one
    to-do that says so."""
    own = (own_address or "").lower()
    last_sent = {}
    for s_ in sent or []:
        cid, when = s_.get("conversationId"), _parse_iso(s_.get("sentDateTime"))
        if cid and when and (cid not in last_sent or when > last_sent[cid]):
            last_sent[cid] = when
    per_conv = {}
    for m in messages:
        if not m.get("id") or not m.get("received_at") or is_automated(m.get("from_addr")) \
                or m.get("from_addr") == own or not is_chaser(m.get("subject"), m.get("snippet")):
            continue
        cid = m.get("conversation_id") or m["id"]
        replied = last_sent.get(cid)
        if replied and replied >= m["received_at"]:
            continue
        per_conv.setdefault(cid, []).append(m)
    out = []
    for cid, ms in per_conv.items():
        ms.sort(key=lambda x: x["received_at"])
        m = ms[-1]
        base = {k: m.get(k) for k in ("from_name", "from_addr", "subject", "snippet", "url", "received_at")}
        out.append({**base, "external_id": m["id"], "kind": "chased",
                    "meta": {"importance": m.get("importance") or "normal", "chases": len(ms)}})
    return out
