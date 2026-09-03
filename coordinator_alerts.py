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


# ── "Ready to invoice" alerts ────────────────────────────────────────────────
# When the audit finds files that can be billed (packed/delivered) but are not
# yet invoiced, the coordinator is alerted, cc the supervisor AND bbrill. Same
# draft-only, gated discipline as the discrepancy alerts above.
INVOICE_CC = "maria.gonzalez@thelsa.com,bbrill@thelsa.com"


def _invoice_cc_list():
    raw = os.environ.get("INVOICE_ALERT_CC", INVOICE_CC)
    return [a.strip() for a in raw.split(",") if a.strip()]


def _file_lines(rows):
    out = []
    for r in rows:
        tag = " [US EMBASSY]" if r.get("embassy") else ""
        when = (f"packed {r['pack']}" if r.get("pack")
                else (f"delivered {r['delivery']}" if r.get("delivery") else ""))
        out.append(f"  • Job {r['job']} — {r.get('client','')}{tag}  "
                   f"(value {_fmt(r.get('value', 0))}{'; ' + when if when else ''})")
    return out


def _invoice_body(coordinator, rows, resolved, followup=False):
    """Bilingual (English + Spanish) alert explaining WHY the file was flagged and
    WHAT to do. Sent from bbrill@thelsa.com; a human can reply to the thread.
    When `followup` is set, it is a 48-hour reminder for files still not invoiced."""
    total = _fmt(sum(r.get("value", 0) for r in rows))
    files = _file_lines(rows)
    en_reminder = ([
        "FOLLOW-UP / REMINDER: the file(s) below were flagged earlier and are STILL "
        "not invoiced in Moveware after 48 hours. Please action them or reply with "
        "the reason for the hold.", "",
    ] if followup else [])
    es_reminder = ([
        "SEGUIMIENTO / RECORDATORIO: el/los expediente(s) de abajo se marcaron antes "
        "y AÚN no están facturados en Moveware después de 48 horas. Por favor "
        "atiéndelos o responde con el motivo de la espera.", "",
    ] if followup else [])
    en = [
        f"Hi {coordinator or 'team'},",
        "",
        *en_reminder,
        "The automated Move-File Audit flagged the following move(s) as READY TO "
        "INVOICE: they have a pack or delivery date that has already passed, but no "
        "invoice has been raised in Moveware yet. (US Embassy files are only flagged "
        "once they have been DELIVERED.)",
        "",
        "Please raise the invoice(s) in Moveware, or reply to this email if a file "
        "is on hold or should not be billed yet.",
        "",
        "Files ready to invoice:",
        *files,
        "",
        f"Total ready to invoice: {total}",
    ]
    es = [
        f"Hola {coordinator or 'equipo'},",
        "",
        *es_reminder,
        "La auditoría automática de expedientes marcó la(s) siguiente(s) mudanza(s) "
        "como LISTA(S) PARA FACTURAR: ya tienen fecha de empaque o de entrega "
        "cumplida, pero aún no se ha generado la factura en Moveware. (Los "
        "expedientes de la Embajada de EE. UU. se marcan solo una vez ENTREGADOS.)",
        "",
        "Por favor genera la(s) factura(s) en Moveware, o responde a este correo si "
        "algún expediente está en espera o no debe facturarse todavía.",
        "",
        "Expedientes listos para facturar:",
        *files,
        "",
        f"Total listo para facturar: {total}",
    ]
    parts = ["\n".join(en), "", "— — — — —", "", "\n".join(es), ""]
    if not resolved:
        parts.append("[Coordinator email not on file — routed here for manual "
                     "assignment. Add them to AUDIT_COORDINATOR_EMAILS.]")
        parts.append("")
    parts.append("— Thelsa Automation Library · Move-File Audit")
    return "\n".join(parts)


def build_invoice_alerts(worklist):
    """Group 'ready to invoice' worklist rows (from compute_metrics) by
    coordinator into draft payloads. Pure — creates nothing."""
    by_coord = {}
    for r in (worklist or []):
        by_coord.setdefault(r.get("coordinator") or "Unassigned", []).append(r)
    cc = _invoice_cc_list()
    alerts = []
    for coord, rows in sorted(by_coord.items(),
                              key=lambda kv: -sum(r.get("value", 0) for r in kv[1])):
        rows.sort(key=lambda r: -r.get("value", 0))
        to_email, resolved = resolve_email(coord)
        total = round(sum(r.get("value", 0) for r in rows), 2)
        n = len(rows)
        followup = any(r.get("_followup") for r in rows)
        tag = "[To Invoice – REMINDER]" if followup else "[To Invoice]"
        subject = (f"{tag} {n} file{'s' if n != 1 else ''} ready to bill — "
                   f"{coord} ({_fmt(total)})")
        alerts.append({
            "coordinator": coord, "to": to_email, "cc": cc, "resolved": resolved,
            "subject": subject, "body": _invoice_body(coord, rows, resolved, followup),
            "file_count": n, "total": total, "followup": followup,
        })
    return alerts


def send_enabled():
    """Actually SEND (vs draft) only when explicitly turned on — a second switch
    on top of AUDIT_ALERTS_ENABLED so sending is never the accidental default."""
    return alerts_enabled() and os.environ.get("INVOICE_ALERTS_SEND") == "1"


def _sender():
    return (os.environ.get("GRAPH_SENDER") or os.environ.get("ALERT_EMAIL")
            or "bbrill@thelsa.com").strip()


def _draft_folder():
    # Drafts land in this mailbox folder (Bill created "Audit Alert Drafts").
    return os.environ.get("INVOICE_ALERTS_DRAFT_FOLDER", "Audit Alert Drafts")


def _max_per_run():
    try:
        return max(1, int(os.environ.get("INVOICE_ALERTS_MAX", "25")))
    except ValueError:
        return 25


def _followup_hours():
    try:
        return max(1, int(os.environ.get("INVOICE_ALERTS_FOLLOWUP_HOURS", "48")))
    except ValueError:
        return 48


def _state_path():
    # Persist which jobs were already alerted (and when) next to the audit cache,
    # so a coordinator is not re-emailed about the same file on every scan.
    base = os.environ.get("AUDIT_CACHE_PATH")
    if base:
        d = os.path.dirname(base)
    else:
        for c in ("/var/data", "/data"):
            if os.path.isdir(c) and os.access(c, os.W_OK):
                d = c
                break
        else:
            d = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(d, "invoice_alert_state.json")


def _load_state():
    try:
        with open(_state_path()) as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_state(state):
    try:
        p = _state_path()
        with open(p + ".tmp", "w") as f:
            json.dump(state, f)
        os.replace(p + ".tmp", p)
    except Exception:
        pass


def _entry(state, job):
    """State entry for a job: {'last': iso-datetime, 'count': int}. Tolerates the
    older date-string format."""
    e = state.get(str(job))
    if isinstance(e, dict):
        return e
    if isinstance(e, str):
        return {"last": e, "count": 1}
    return None


def _due(job, state, now):
    """Alert this job now if it was never alerted, or if the last alert was ≥ the
    follow-up window ago (48h) and it's STILL on the ready-to-invoice list (a file
    that got invoiced simply drops off the list, so it stops here on its own)."""
    import datetime as _dt
    e = _entry(state, job)
    if not e or not e.get("last"):
        return True, False               # first alert, not a follow-up
    try:
        last = _dt.datetime.fromisoformat(e["last"])
    except Exception:
        return True, False
    hours = (now - last).total_seconds() / 3600.0
    return (hours >= _followup_hours()), True   # due => this is a follow-up


def dispatch_invoice_alerts(worklist, live=False, send=None):
    """Alert coordinators about files ready to invoice — SEND or DRAFT.

    Gating (nothing goes out unless all hold):
      • AUDIT_ALERTS_ENABLED=1 and DRY_RUN!=1   (master switch)
      • live data only (never off the demo dataset)
      • to SEND (not draft): INVOICE_ALERTS_SEND=1  — otherwise a draft is created
    Safeguards: de-dupes by job (a file is not re-alerted within
    INVOICE_ALERTS_REDISPATCH_DAYS, default 7) and caps emails per run
    (INVOICE_ALERTS_MAX, default 25). Sends from GRAPH_SENDER (default
    bbrill@thelsa.com), cc maria.gonzalez + bbrill.
    """
    import datetime as _dt
    now = _dt.datetime.now()
    will_send = send_enabled() if send is None else bool(send)
    status = {"enabled": alerts_enabled(), "send": will_send, "live": bool(live),
              "mode": "send" if will_send else "draft", "sent": [], "drafts": [],
              "skipped": 0, "followups": 0, "skipped_reason": None}

    if not live:
        status["skipped_reason"] = "demo data — alerting disabled off non-live data"
        return status
    if not alerts_enabled():
        status["skipped_reason"] = "AUDIT_ALERTS_ENABLED != 1 (or DRY_RUN=1)"
        return status

    # First alert immediately; then follow up only every 48h while still unbilled.
    state = _load_state()
    fresh = []
    for r in (worklist or []):
        due, is_followup = _due(r.get("job"), state, now)
        if due:
            r = dict(r, _followup=is_followup)
            fresh.append(r)
            if is_followup:
                status["followups"] += 1
    status["skipped"] = len(worklist or []) - len(fresh)
    alerts = build_invoice_alerts(fresh)
    status["alert_count"] = len(alerts)
    if not alerts:
        status["skipped_reason"] = "nothing due (all alerted within the last 48h or none ready)"
        return status
    alerts = alerts[:_max_per_run()]

    try:
        from engine.mailer import GraphMailer
        mailer = GraphMailer(mailbox=_sender())
    except Exception as e:
        status["skipped_reason"] = f"mailer unavailable: {e}"
        return status

    for a in alerts:
        rec = {"coordinator": a["coordinator"], "to": a["to"], "files": a["file_count"],
               "total": a["total"]}
        try:
            if will_send:
                mailer.send_mail(a["to"], a["subject"], a["body"], cc=a["cc"])
                rec["ok"] = True
                status["sent"].append(rec)
            else:
                d = mailer.create_draft(a["to"], a["subject"], a["body"], cc=a["cc"],
                                        folder=_draft_folder())
                rec["ok"] = True
                rec["id"] = d.get("id", "")
                rec["folder"] = d.get("folder", "")
                status["drafts"].append(rec)
            # Mark this coordinator's jobs alerted (bump count) so the next touch
            # is a 48h follow-up, not an immediate re-send.
            for r in fresh:
                if (r.get("coordinator") or "Unassigned") == a["coordinator"]:
                    prev = _entry(state, r.get("job")) or {"count": 0}
                    state[str(r.get("job"))] = {"last": now.isoformat(),
                                                "count": int(prev.get("count", 0)) + 1}
        except Exception as e:
            rec["ok"] = False
            rec["error"] = str(e)
            (status["sent"] if will_send else status["drafts"]).append(rec)
    _save_state(state)
    return status


def create_invoice_drafts(worklist, live=False):
    """Back-compat: DRAFT one alert per coordinator (never sends)."""
    return dispatch_invoice_alerts(worklist, live=live, send=False)


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
