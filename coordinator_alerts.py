"""
coordinator_alerts.py — Revenue/cost discrepancy alerts for move coordinators.

When the Move-File Cost & Profit Audit finds that a file's revenue or cost
figures don't reconcile (see audit_web.check_calculations), the responsible move
coordinator should be told. This module turns those per-file flags into one
review-ready email DRAFT per coordinator, cc'd to a supervisor.

SAFETY — draft only, never send
-------------------------------
Consistent with engine/mailer.py and engine/alerts.py, the Graph app has
Mail.ReadWrite but NOT Mail.Send: this module only ever creates DRAFTS in the
bbrill@thelsa.com mailbox for a human to review and send. Creating drafts is
further gated so nothing is written unless explicitly enabled:

  AUDIT_ALERTS_ENABLED = "1"   # must be set to create drafts at all
  DRY_RUN              != "1"   # DRY_RUN=1 forces preview-only
  live data only               # never draft off the demo dataset

Config
------
  AUDIT_ALERT_CC          cc address(es), comma-separated
                          (default: maria.gonzalez@thelsa.com)
  AUDIT_COORDINATOR_EMAILS  name->email map, either JSON
                          ({"Marla":"marla@thelsa.com"}) or
                          "Marla:marla@thelsa.com,Fernanda:fernanda@thelsa.com"
  AUDIT_ALERT_FALLBACK    where alerts for an UNRESOLVED coordinator go so a
                          human can route them (default: ALERT_EMAIL or
                          bbrill@thelsa.com). We never email a guessed address.
"""
import json
import os

DEFAULT_CC = "maria.gonzalez@thelsa.com"


def _cc_list():
    raw = os.environ.get("AUDIT_ALERT_CC", DEFAULT_CC)
    return [a.strip() for a in raw.split(",") if a.strip()]


def _fallback_inbox():
    return (os.environ.get("AUDIT_ALERT_FALLBACK")
            or os.environ.get("ALERT_EMAIL")
            or "bbrill@thelsa.com").strip()


def _coordinator_map():
    """Parse AUDIT_COORDINATOR_EMAILS (JSON or 'Name:email,...'). Lower-keyed."""
    raw = (os.environ.get("AUDIT_COORDINATOR_EMAILS") or "").strip()
    if not raw:
        return {}
    out = {}
    if raw.startswith("{"):
        try:
            for k, v in json.loads(raw).items():
                if k and v:
                    out[str(k).strip().lower()] = str(v).strip()
        except (ValueError, TypeError):
            return {}
    else:
        for pair in raw.split(","):
            if ":" in pair:
                k, v = pair.split(":", 1)
                if k.strip() and v.strip():
                    out[k.strip().lower()] = v.strip()
    return out


def resolve_email(coordinator):
    """Return (to_email, resolved: bool) for a coordinator name.

    Resolved from AUDIT_COORDINATOR_EMAILS; if unknown, routes to the fallback
    inbox so a person assigns it — we never send to a guessed address.
    """
    name = (coordinator or "").strip()
    email = _coordinator_map().get(name.lower())
    if email:
        return email, True
    return _fallback_inbox(), False


def _fmt(n):
    return f"{n:,.2f}"


def _body_for(coordinator, files, resolved):
    lines = [
        f"Hi {coordinator or 'team'},",
        "",
        "The automated Move-File Cost & Profit Audit found revenue/cost figures "
        "on the following active file(s) that don't reconcile. Please review and "
        "correct in Moveware.",
        "",
    ]
    for f in files:
        lines.append(f"• Job {f['job']} — {f.get('client','')}  "
                     f"(discrepancy {_fmt(f.get('disc_value',0))})")
        for g in f.get("disc_flags", []):
            lines.append(f"    - {g['label']}: expected {_fmt(g['expected'])}, "
                         f"found {_fmt(g['found'])} (diff {_fmt(g['diff'])})")
    lines += [
        "",
        f"Total across your file(s): {_fmt(sum(f.get('disc_value', 0) for f in files))}",
        "",
    ]
    if not resolved:
        lines.append("[Coordinator email not on file — routed here for manual "
                     "assignment. Add them to AUDIT_COORDINATOR_EMAILS.]")
        lines.append("")
    lines.append("— Thelsa Automation Library · Move-File Cost & Profit Audit")
    return "\n".join(lines)


def build_alerts(files):
    """Group active flagged files by coordinator into alert payloads.

    Returns a list of dicts: {coordinator, to, cc, resolved, subject, body,
    file_count, total}. Pure — creates nothing.
    """
    by_coord = {}
    for f in files:
        if f.get("disc_value", 0) > 0 and f.get("stage") != "closed":
            by_coord.setdefault(f.get("coordinator") or "Unassigned", []).append(f)

    alerts = []
    cc = _cc_list()
    for coord, cfiles in sorted(by_coord.items(),
                                key=lambda kv: -sum(f.get("disc_value", 0) for f in kv[1])):
        cfiles.sort(key=lambda f: -f.get("disc_value", 0))
        to_email, resolved = resolve_email(coord)
        total = round(sum(f.get("disc_value", 0) for f in cfiles), 2)
        n = len(cfiles)
        subject = (f"[Cost Audit] {n} file{'s' if n != 1 else ''} with revenue/cost "
                   f"discrepancies — {coord} ({_fmt(total)})")
        alerts.append({
            "coordinator": coord, "to": to_email, "cc": cc, "resolved": resolved,
            "subject": subject, "body": _body_for(coord, cfiles, resolved),
            "file_count": n, "total": total,
        })
    return alerts


def alerts_enabled():
    return (os.environ.get("AUDIT_ALERTS_ENABLED") == "1"
            and os.environ.get("DRY_RUN", "0") != "1")


def create_drafts(files, live=False):
    """Create one Graph DRAFT per coordinator alert. Draft only — never sends.

    Guarded: returns without writing anything unless alerts are enabled AND the
    data is live (never draft off demo data). Returns a status dict.
    """
    alerts = build_alerts(files)
    status = {
        "enabled": alerts_enabled(), "live": bool(live),
        "alert_count": len(alerts), "drafts": [], "skipped_reason": None,
    }
    if not alerts:
        status["skipped_reason"] = "no discrepancies"
        return status
    if not live:
        status["skipped_reason"] = "demo data — drafting disabled off non-live data"
        return status
    if not alerts_enabled():
        status["skipped_reason"] = "AUDIT_ALERTS_ENABLED != 1 (or DRY_RUN=1)"
        return status
    try:
        from engine.mailer import GraphMailer
        mailer = GraphMailer()
    except Exception as e:  # missing Graph config / deps
        status["skipped_reason"] = f"mailer unavailable: {e}"
        return status
    for a in alerts:
        try:
            d = mailer.create_draft(a["to"], a["subject"], a["body"], cc=a["cc"])
            status["drafts"].append({"coordinator": a["coordinator"], "to": a["to"],
                                     "id": d.get("id", ""), "ok": True})
        except Exception as e:
            status["drafts"].append({"coordinator": a["coordinator"], "to": a["to"],
                                     "ok": False, "error": str(e)})
    return status
