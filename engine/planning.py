"""
planning.py — pure scheduling + screening logic (no network; unit-tested).
"""
from datetime import date, timedelta


def parse_date(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def add_business_days(start, n):
    """Add n business days (Mon-Fri) to a date."""
    d = start
    step = 1 if n >= 0 else -1
    remaining = abs(n)
    while remaining > 0:
        d = d + timedelta(days=step)
        if d.weekday() < 5:  # 0-4 = Mon-Fri
            remaining -= 1
    return d


def is_due(next_action_date, today):
    """True if a step is due (no date set = due now)."""
    nad = parse_date(next_action_date)
    return nad is None or nad <= today


def is_suppressed(email, supp_emails, supp_domains):
    e = (email or "").lower()
    dom = e.split("@")[-1]
    return e in supp_emails or dom in supp_domains


def screen_reason(member, supp_emails, supp_domains, stop_stages):
    """Return a reason string if the member is NOT eligible to draft, else None."""
    stage = (member.get("stage") or "").strip().lower()
    if stage in stop_stages:
        return f"stage={stage}"
    if is_suppressed(member.get("email"), supp_emails, supp_domains):
        return "suppressed"
    return None


def next_step_after_send(step, sent_date, gaps):
    """Given the step just sent and its send date, return (next_step, next_action_date)
    or (None, None) if the sequence is finished. gaps[i] = business-day gap BEFORE
    step i+1 (gaps[0] applies to step 1; the gap before step k is gaps[k-1])."""
    nxt = step + 1
    if nxt > len(gaps):
        return None, None
    gap = gaps[nxt - 1]
    return nxt, add_business_days(sent_date, gap).isoformat()
