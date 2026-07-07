"""
ai.py — optional AI personalization for draft bodies.

Given the approved email body, produce a lightly reworded, per-recipient version
so each draft is unique (which materially reduces spam-filter fingerprinting) while
saying the *same thing*. It is deliberately conservative: it must not invent claims,
must keep every service/bullet, and must keep the greeting, signature, the
rates@thelsa.com CTA, the phone number, and the unsubscribe line intact.

If ANTHROPIC_API_KEY is unset or the call fails, callers fall back to the plain
templated body — drafting never breaks because of this module.
"""
import os

import requests

API_URL = "https://api.anthropic.com/v1/messages"

_SYSTEM = (
    "You lightly reword a fixed B2B outreach email so each recipient receives a "
    "slightly unique version, purely to avoid spam-filter fingerprinting from "
    "identical bulk sends. STRICT RULES:\n"
    "- Preserve the exact meaning, offer, and every fact. Invent nothing: no new "
    "claims, prices, promises, dates, or credentials.\n"
    "- Keep every bullet point and all listed services, and keep the plain-text "
    "structure and a very similar length.\n"
    "- Keep these EXACTLY as given, verbatim: the first greeting line, the entire "
    "signature block, the email address rates@thelsa.com, the phone number, and the "
    "final unsubscribe sentence.\n"
    "- Only lightly vary the connective prose (synonyms, minor sentence reordering).\n"
    "VOICE: keep the sender's tone — warm but direct and businesslike, courteous, "
    "plain-spoken, short paragraphs, no marketing hype and no exclamation points.\n"
    "Return ONLY the rewritten email body — no preamble, no quotes, no commentary."
)


def available():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def personalize(body, contact, timeout=30):
    """Return a lightly personalized body, or raise on failure (caller falls back)."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return body
    model = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")
    company = (contact.get("company") or "").strip() or "unknown"
    country = (contact.get("country") or "").strip() or "unknown"
    city = (contact.get("city") or "").strip() or "unknown"
    context = (f"Recipient — company: {company}; country: {country}; city: {city}. "
               "You may nod subtly to their region in the opening if it fits, but do "
               "not state anything you were not given.")
    user = f"{context}\n\nEmail to lightly reword (return the body only):\n\n{body}"
    r = requests.post(API_URL, timeout=timeout,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": model, "max_tokens": 1400, "temperature": 0.7,
              "system": _SYSTEM,
              "messages": [{"role": "user", "content": user}]})
    if r.status_code >= 300:
        raise RuntimeError(f"Anthropic error [{r.status_code}]: {r.text[:200]}")
    data = r.json()
    out = "".join(b.get("text", "") for b in data.get("content", [])
                  if b.get("type") == "text").strip()
    return out or body
