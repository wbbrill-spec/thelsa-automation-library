"""
Suggested replies with Claude. Draft-only: the text is shown to the user and,
if they choose, saved to their Outlook Drafts. Nothing is ever sent.

ANTHROPIC_API_KEY must be set. ASSISTANT_DRAFT_MODEL overrides the model.
"""

import os

import requests

MODEL = os.environ.get("ASSISTANT_DRAFT_MODEL", "claude-sonnet-4-5")


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def suggest_reply(user_name: str, sender: str, subject: str, body: str) -> str:
    if not enabled():
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    prompt = (
        f"You are drafting an email reply on behalf of {user_name}, who works at Thelsa "
        "(international and domestic moving, Mexico). Write a short, professional reply to "
        "the email below. Reply in the same language as the email (Spanish or English). "
        "Do not invent facts, prices, dates or commitments — if something needs confirming, "
        "say you'll confirm. Output only the reply body, no subject line, no signature block.\n\n"
        f"From: {sender}\nSubject: {subject}\n\n{body[:6000]}"
    )
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                 "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": MODEL, "max_tokens": 600,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=60)
    r.raise_for_status()
    parts = r.json().get("content") or []
    return "".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()


def _ask(prompt: str, max_tokens: int = 700) -> str:
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                 "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": MODEL, "max_tokens": max_tokens,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=60)
    r.raise_for_status()
    parts = r.json().get("content") or []
    return "".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()


def followup_prompt(user_name: str, draft: dict, title: str = "") -> str:
    """The instruction for a follow-up written ONLY from the to-do's facts."""
    import json as _json
    ch = (draft or {}).get("channel") or "email"
    lang = "Spanish (Mexico)" if (draft or {}).get("language") == "es" else "English"
    facts = _json.dumps((draft or {}).get("facts") or {}, ensure_ascii=False, default=str)
    kind = ("a short WhatsApp message (2-4 sentences, no subject, no signature, friendly, no emojis)"
            if ch == "whatsapp" else
            "a short professional email. First line: 'Subject: ...'; then a blank line; then the body. "
            "No signature block (Outlook adds it)")
    return (
        f"You are drafting {kind} for {user_name}, who works at a moving company "
        "(international and domestic moves). It is a follow-up to get a to-do done.\n"
        f"Write it in {lang}.\n"
        f"Purpose: {(draft or {}).get('purpose') or title}\n"
        f"To-do: {title}\n"
        f"Facts (the ONLY facts you may use): {facts}\n"
        "Rules: never invent prices, dates, amounts, names, reference numbers or promises that are "
        "not in the facts; if something is unknown, ask for it or leave it out. Keep it polite and "
        "specific; mention the file / quote / invoice number when the facts give one. Output only "
        "the message.")


def draft_followup(user_name: str, draft: dict, title: str = "") -> tuple:
    """(subject, body) for a product to-do. Draft only: nothing is sent."""
    if not enabled():
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    text = _ask(followup_prompt(user_name, draft, title))
    subject = ""
    if (draft or {}).get("channel") != "whatsapp":
        lines = text.splitlines()
        if lines and lines[0].lower().startswith(("subject:", "asunto:")):
            subject = lines[0].split(":", 1)[1].strip()
            text = "\n".join(lines[1:]).strip()
    return subject or title[:120], text
