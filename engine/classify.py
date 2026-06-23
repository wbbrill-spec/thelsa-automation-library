"""
classify.py — tunable inbound classifier driven by the signal_phrases tab.

Pure functions (no network) so they're unit-testable. The engine loads the
phrase config from the workbook and passes it in.
"""
import re

# precedence: an unsubscribe always wins; then booking; then rate request.
PRECEDENCE = ["unsubscribe", "booking", "rate_request"]

_BOUNCE_SENDERS = ("mailer-daemon", "postmaster", "microsoftexchange")


def phrases_by_category(signal_rows):
    """signal_rows: list of dicts (category, phrase, active) -> {cat: [phrase,...]}."""
    out = {}
    for r in signal_rows:
        if str(r.get("active", "")).strip().upper() in ("TRUE", "1", "YES"):
            out.setdefault(r.get("category", "").strip(), []).append(r.get("phrase", "").strip().lower())
    return out


def classify(subject, body, phrases):
    """Return 'unsubscribe' | 'booking' | 'rate_request' | 'other'."""
    text = f"{subject or ''}\n{body or ''}".lower()
    for cat in PRECEDENCE:
        for ph in phrases.get(cat, []):
            if ph and ph in text:
                return cat
    return "other"


def is_bounce(from_addr):
    a = (from_addr or "").lower()
    return any(tok in a for tok in _BOUNCE_SENDERS)


def is_original_thread(subject):
    """A rate request counts only if it's the first message of a new thread,
    i.e. the subject is not a reply/forward of our outreach."""
    s = (subject or "").strip().lower()
    return not s.startswith(("re:", "fw:", "fwd:"))


def domain_of(email):
    return (email or "").split("@")[-1].strip().lower()
