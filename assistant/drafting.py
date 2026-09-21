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
