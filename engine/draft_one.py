# engine/draft_one.py — create lead-gen draft(s) in a chosen rep mailbox.
# Usage: python -m engine.draft_one path/to/payload.json
# payload.json: {"mailbox": "...", "to": "...", "subject": "...", "body": "..."}
#   (or a JSON list of such objects to create several at once)
import json
import sys

from . import config
from .mailer import GraphMailer


def _create(item):
    mailbox = (item.get("mailbox") or "").strip()
    to = (item.get("to") or "").strip()
    subject = (item.get("subject") or "").strip()
    body = item.get("body") or ""
    if mailbox.lower() not in config.ALLOWED_MAILBOXES:
        return {"ok": False, "error": f"mailbox not allowed: {mailbox}", "to": to}
    if not (to and subject and body):
        return {"ok": False, "error": "missing to/subject/body", "to": to}
    try:
        d = GraphMailer(mailbox).create_draft(to, subject, body)
        return {"ok": True, "mailbox": mailbox, "to": to, "draft": d}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "mailbox": mailbox, "to": to}


def main(path):
    with open(path) as fh:
        payload = json.load(fh)
    items = payload if isinstance(payload, list) else [payload]
    results = [_create(it) for it in items]
    print(json.dumps(results, indent=2))
    return results


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m engine.draft_one <payload.json>")
    main(sys.argv[1])
