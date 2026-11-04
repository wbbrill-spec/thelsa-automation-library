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

import tenant as _t

# Every company-specific value comes from tenants/<id>.json (multi-company, 4 Nov 2026).
DEFAULT_CC = _t.get("people.alert_cc", "")



# Bill, 21 Oct 2026: every email the app sends carries the app's name in the
# subject line, so people know where it came from. APP_EMAIL_NAME overrides.
def app_subject(subject: str) -> str:
    name = os.environ.get("APP_EMAIL_NAME", _t.get("app_name", "Audit Assistant")).strip()
    tag = f"[{name}]"
    return subject if not name or subject.startswith(tag) else f"{tag} {subject}"

def _cc_list():
    raw = os.environ.get("AUDIT_ALERT_CC", DEFAULT_CC)
    return [a.strip() for a in raw.split(",") if a.strip()]


def _fallback_inbox():
    return (os.environ.get("AUDIT_ALERT_FALLBACK")
            or os.environ.get("ALERT_EMAIL")
            or _t.get("people.sender_mailbox", "")).strip()


# Coordinators whose address was confirmed from mail they sent themselves
# (19 Oct 2026). AUDIT_COORDINATOR_EMAILS adds to / overrides these.
KNOWN_COORDINATORS = dict(_t.get("people.coordinators", {}))
# No longer with Thelsa (Bill, 30 Oct 2026): never emailed. Other unknown codes
# are probably ex-staff too and go to the fallback inbox, never guessed.
FORMER_STAFF = set(_t.get("people.former_staff", []))


def _coordinator_map():
    """KNOWN_COORDINATORS plus AUDIT_COORDINATOR_EMAILS (JSON or
    'Name:email,...'). Lower-keyed."""
    raw = (os.environ.get("AUDIT_COORDINATOR_EMAILS") or "").strip()
    out = dict(KNOWN_COORDINATORS)
    if not raw:
        return out
    if raw.startswith("{"):
        try:
            for k, v in json.loads(raw).items():
                if k and v:
                    out[str(k).strip().lower()] = str(v).strip()
        except (ValueError, TypeError):
            return dict(KNOWN_COORDINATORS)
    else:
        for pair in raw.split(","):
            if ":" in pair:
                k, v = pair.split(":", 1)
                if k.strip() and v.strip():
                    out[k.strip().lower()] = v.strip()
    return out


def _fold(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return "".join(ch for ch in s if ch.isalnum() or ch == " ")


def name_matches(name, email) -> bool:
    """Does this address belong to this person? True when the mailbox part holds
    the first name and at least one surname (sarareyes@ ↔ "Sara Reyes",
    maria.gonzalez@ ↔ "Maria Gonzalez"), accents ignored.

    Why (19 Oct 2026): on live V2 files the coordinator NAME comes from Moveware's
    coordinator role while the EMAIL can come from any @thelsa.com address on any
    role. Sara's, Stephanie's and Elizabeth's missing-date lists all went to
    Edgar Espino. An address that does not match the name is never used."""
    local = _fold((email or "").split("@")[0]).replace(" ", "")
    parts = [p for p in _fold(name).split() if len(p) > 1]
    if not local or not parts:
        return False
    return parts[0] in local and (len(parts) == 1 or any(p in local for p in parts[1:]))


def resolve_email(coordinator, direct_email=None):
    """Return (to_email, resolved: bool) for a coordinator.

    Prefers the AUDIT_COORDINATOR_EMAILS override map, then the email carried on
    the file itself — only when it belongs to that coordinator (name_matches) or
    no name is known; else the fallback inbox so a person assigns it. Never a
    guessed or someone else's address.
    """
    direct = (direct_email or "").strip()
    name = (coordinator or "").strip()
    if name.lower() in FORMER_STAFF or direct.lower() in FORMER_STAFF:
        return _fallback_inbox(), False
    mapped = _coordinator_map().get(name.lower()) if name else None
    if mapped:
        return mapped, True
    if "@" in direct and (not name or name_matches(name, direct)):
        return direct, True
    email = None
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
            "subject": app_subject(subject), "body": _body_for(coord, cfiles, resolved),
            "file_count": n, "total": total,
        })
    return alerts


# ── "Ready to invoice" alerts ────────────────────────────────────────────────
# When the audit finds files that can be billed (packed/delivered) but are not
# yet invoiced, the coordinator is alerted, cc the supervisor AND bbrill. Same
# draft-only, gated discipline as the discrepancy alerts above.
# Ready-to-invoice alerts go to the team that ISSUES invoices, not to the
# coordinators or sales (Lupita, 2 Nov 2026: "All alerts for all services ready to
# be invoiced should go to Maria Fernanda Carrasco or Monica Escalante, in copy my
# email. We are the responsible for issuing the invoice, not the sales or the MC").
# One email per run listing every file, each with its coordinator.
INVOICE_TO = ",".join(_t.get("invoicing.team_to", []))
INVOICE_TO_NAMES = _t.get("invoicing.team_names", "team")
INVOICE_TO_NAMES_ES = _t.get("invoicing.team_names_es", INVOICE_TO_NAMES)
INVOICE_CC = ",".join(_t.get("invoicing.cc", []))
# US Embassy / Consulate files: Edgar asks Fer and Monica for the invoice (Lupita,
# 3 Nov 2026: "Edgar Espino is responsible to request the invoice to Monica and
# Fernanda"). They go to him in their own email, not on the invoicing team's list.
INVOICE_EMBASSY_TO = ",".join(_t.get("invoicing.embassy_to", []))
INVOICE_EMBASSY_NAMES = _t.get("invoicing.embassy_names", "")


def _invoice_cc_list():
    raw = os.environ.get("INVOICE_ALERT_CC", INVOICE_CC)
    return [a.strip() for a in raw.split(",") if a.strip()]


# Moveware shows some users by their login (Bill, 3 Nov 2026: "coordinator
# Edgarespino" in the alerts). The name people know them by:
DISPLAY_NAMES = dict(_t.get("people.display_names", {}))


def display_coordinator(name: str) -> str:
    n = (name or "").strip()
    if not n:
        return ""
    shown = DISPLAY_NAMES.get(n.lower(), n)
    if n.lower() in FORMER_STAFF:
        shown += " (no longer at TMS: please reassign the file in Moveware)"
    return shown


# Files the team said NOT to bill yet. Each comes back on the list after its
# review date, so a hold is never forgotten. INVOICE_HOLDS (JSON
# {"job": {"reason": "...", "until": "YYYY-MM-DD"}}) adds to / overrides these.
INVOICE_HOLDS = dict(_t.get("invoicing.holds", {}))


def invoice_hold(job, today=None):
    """The hold reason if this file must not be flagged today, else ''."""
    import datetime as _dt
    holds = dict(INVOICE_HOLDS)
    raw = (os.environ.get("INVOICE_HOLDS") or "").strip()
    if raw:
        try:
            holds.update({str(k).strip().upper(): v for k, v in json.loads(raw).items() if isinstance(v, dict)})
        except (ValueError, TypeError, AttributeError):
            pass
    h = holds.get(str(job or "").strip().upper())
    if not h:
        return ""
    today = today or _dt.date.today()
    try:
        if h.get("until") and _dt.date.fromisoformat(h["until"]) <= today:
            return ""
    except ValueError:
        pass
    return str(h.get("reason") or "on hold")


def _invoice_to_list():
    raw = os.environ.get("INVOICE_ALERT_TO", INVOICE_TO)
    return [a.strip() for a in raw.split(",") if a.strip()]


def _file_lines(rows):
    out = []
    for r in rows:
        tag = " [US EMBASSY]" if r.get("embassy") else ""
        when = (f"packed {r['pack']}" if r.get("pack")
                else (f"delivered {r['delivery']}" if r.get("delivery") else ""))
        coord = f"; coordinator {display_coordinator(r['coordinator'])}" if r.get("coordinator") else ""
        out.append(f"  • Job {r['job']} — {r.get('client','')}{tag}  "
                   f"(value {_fmt(r.get('value', 0))}{' ' + r['currency'] if r.get('currency') else ''}{'; ' + when if when else ''}{coord})")
    return out


def _invoice_body(coordinator, rows, resolved, followup=False, coordinator_es=None):
    """Bilingual (English + Spanish) alert explaining WHY the file was flagged and
    WHAT to do. Sent from bbrill@thelsa.com; a human can reply to the thread.
    When `followup` is set, it is a 48-hour reminder for files still not invoiced."""
    total = _fmt(sum(r.get("value", 0) for r in rows))
    files = _file_lines(rows)
    en_reminder = ([
        "FOLLOW-UP / REMINDER: the file(s) below were flagged earlier and are STILL "
        "not invoiced after 48 hours. Please issue the invoice(s) or reply with "
        "the reason for the hold.", "",
    ] if followup else [])
    es_reminder = ([
        "SEGUIMIENTO / RECORDATORIO: el/los expediente(s) de abajo se marcaron antes "
        "y AÚN no están facturados después de 48 horas. Por favor emite la(s) "
        "factura(s) o responde con el motivo de la espera.", "",
    ] if followup else [])
    en = [
        f"Hi {coordinator or 'team'},",
        "",
        *en_reminder,
        "The automated Move-File Audit flagged the following move(s) as READY TO "
        "INVOICE: the move has happened (loaded, or delivered for inbound moves), but "
        "no invoice shows in Moveware or in Finance's register yet. (US Embassy files "
        "are only flagged once they have been DELIVERED.)",
        "",
        "Please issue the invoice(s), or reply to this email if a file is on hold or "
        "should not be billed yet. Each file shows its coordinator, in case you need "
        "details.",
        "",
        "Files ready to invoice:",
        *files,
        "",
        f"Total ready to invoice: {total}",
    ]
    es = [
        f"Hola {coordinator_es or coordinator or 'equipo'},",
        "",
        *es_reminder,
        "La auditoría automática de expedientes marcó la(s) siguiente(s) mudanza(s) "
        "como LISTA(S) PARA FACTURAR: la mudanza ya se realizó (cargada, o entregada "
        "si es de importación), pero aún no aparece factura en Moveware ni en el "
        "registro de Finanzas. (Los expedientes de la Embajada de EE. UU. se marcan "
        "solo una vez ENTREGADOS.)",
        "",
        "Por favor emite la(s) factura(s), o responde a este correo si algún "
        "expediente está en espera o no debe facturarse todavía. Cada expediente "
        "indica su coordinador, por si necesitas detalles.",
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


def _embassy_body(rows, followup=False):
    """Bilingual note to Edgar: US Embassy / Consulate files delivered and not yet
    invoiced; he sends the billing instructions to Fer and Monica."""
    total = _fmt(sum(r.get("value", 0) for r in rows))
    files = _file_lines(rows)
    rem_en = (["REMINDER: these were flagged earlier and are still not invoiced.", ""] if followup else [])
    rem_es = (["RECORDATORIO: estos expedientes se marcaron antes y aún no están facturados.", ""] if followup else [])
    en = ["Hi Edgar,", "", *rem_en,
          "These US Embassy / Consulate moves have been delivered, but no invoice shows in Moveware "
          "or in Finance's register yet.", "",
          "Could you please send the billing instructions to Fer and Monica (or reply if a file is on "
          "hold or should not be billed yet)?", "",
          "US Embassy / Consulate files ready to invoice:", *files, "",
          f"Total: {total}"]
    es = ["Hola Edgar,", "", *rem_es,
          "Estas mudanzas de la Embajada / Consulado de EE. UU. ya se entregaron, pero aún no aparece "
          "factura en Moveware ni en el registro de Finanzas.", "",
          "¿Podrías enviar las instrucciones de facturación a Fer y Mónica (o responder si algún "
          "expediente está en espera o no debe facturarse todavía)?", "",
          "Expedientes de Embajada / Consulado listos para facturar:", *files, "",
          f"Total: {total}"]
    return "\n".join(["\n".join(en), "", "— — — — —", "", "\n".join(es), "",
                      "— Thelsa Automation Library · Move-File Audit"])


def build_invoice_alerts(worklist):
    """Files ready to invoice, highest value first, each with its coordinator:
      * US Embassy / Consulate files -> ONE email to Edgar (he requests the invoice
        from Fer and Monica; Lupita, 3 Nov 2026), Lupita in copy;
      * every other file -> ONE email to the invoicing team (Fer and Monica,
        Lupita in copy — Lupita, 2 Nov 2026).
    Pure — creates nothing. Returns a list of 0, 1 or 2 alerts."""
    rows = sorted(list(worklist or []), key=lambda r: -r.get("value", 0))
    alerts = []
    embassy = [r for r in rows if r.get("embassy")]
    team = [r for r in rows if not r.get("embassy")]
    if team:
        total = round(sum(r.get("value", 0) for r in team), 2)
        n = len(team)
        followup = any(r.get("_followup") for r in team)
        tag = "[To Invoice – REMINDER]" if followup else "[To Invoice]"
        subject = f"{tag} {n} file{'s' if n != 1 else ''} ready to bill ({_fmt(total)})"
        names = os.environ.get("INVOICE_ALERT_TO_NAMES", INVOICE_TO_NAMES)
        names_es = os.environ.get("INVOICE_ALERT_TO_NAMES_ES", INVOICE_TO_NAMES_ES)
        alerts.append({
            "coordinator": "Invoicing team", "to": _invoice_to_list(), "cc": _invoice_cc_list(),
            "resolved": True, "subject": app_subject(subject),
            "body": _invoice_body(names, team, True, followup, coordinator_es=names_es),
            "file_count": n, "total": total, "followup": followup, "jobs": [r.get("job") for r in team],
        })
    if embassy:
        total = round(sum(r.get("value", 0) for r in embassy), 2)
        n = len(embassy)
        followup = any(r.get("_followup") for r in embassy)
        tag = "[To Invoice – US Embassy – REMINDER]" if followup else "[To Invoice – US Embassy]"
        subject = f"{tag} {n} file{'s' if n != 1 else ''} ready to bill ({_fmt(total)})"
        to = [a.strip() for a in os.environ.get("INVOICE_EMBASSY_TO", INVOICE_EMBASSY_TO).split(",") if a.strip()]
        alerts.append({
            "coordinator": "US Embassy (Edgar)", "to": to, "cc": _invoice_cc_list(),
            "resolved": True, "subject": app_subject(subject),
            "body": _embassy_body(embassy, followup),
            "file_count": n, "total": total, "followup": followup, "jobs": [r.get("job") for r in embassy],
        })
    return alerts


def send_enabled():
    """Actually SEND (vs draft) only when explicitly turned on — a second switch
    on top of AUDIT_ALERTS_ENABLED so sending is never the accidental default."""
    return alerts_enabled() and os.environ.get("INVOICE_ALERTS_SEND", "1") == "1"


def _sender():
    return (os.environ.get("GRAPH_SENDER") or os.environ.get("ALERT_EMAIL")
            or _t.get("people.sender_mailbox", "")).strip()


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
    """Alert the invoicing team (Fer and Monica, Lupita in copy) about files ready
    to invoice — SEND or DRAFT.

    Gating (nothing goes out unless all hold):
      • AUDIT_ALERTS_ENABLED=1 and DRY_RUN!=1   (master switch)
      • live data only (never off the demo dataset)
      • to SEND (not draft): INVOICE_ALERTS_SEND=1  — otherwise a draft is created
    Safeguards: de-dupes by job (a file is not re-alerted within
    INVOICE_ALERTS_REDISPATCH_DAYS, default 7) and caps emails per run
    (INVOICE_ALERTS_MAX, default 25). Sends from GRAPH_SENDER (default
    bbrill@thelsa.com), to INVOICE_ALERT_TO, cc INVOICE_ALERT_CC (maria.gonzalez).
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

    # Only booked files from the last 12 months, with a value to bill.
    worklist = [r for r in (worklist or []) if recent_booked(r) and (r.get("value") or 0) > 0]
    # Files the team put on hold are left out until their review date.
    held = [r for r in worklist if invoice_hold(r.get("job"))]
    status["on_hold"] = [{"job": r.get("job"), "reason": invoice_hold(r.get("job"))} for r in held]
    worklist = [r for r in worklist if not invoice_hold(r.get("job"))]
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
            sent_jobs = {str(j) for j in (a.get("jobs") or [])}
            for r in fresh:
                if sent_jobs and str(r.get("job")) not in sent_jobs:
                    continue
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
    # Ready-to-invoice alerts ON again (Bill, 2 Nov 2026), now addressed to the
    # invoicing team (Fer, Monica; Lupita cc) and checked against Finance's invoice
    # register, which fixed the 30 Oct problem (110887 emailed as unbilled though
    # Finance had invoiced it). AUDIT_ALERTS_ENABLED=0 turns them off.
    return (os.environ.get("AUDIT_ALERTS_ENABLED", "1") == "1"
            and os.environ.get("DRY_RUN", "0") != "1")


def cost_alerts_enabled():
    # Cost-problem emails to coordinators stay OFF until their data is verified
    # (Bill, 30 Oct 2026). Their own switch, so turning invoice alerts on does not
    # turn these on: COST_ALERTS_ENABLED=1.
    return alerts_enabled() and os.environ.get("COST_ALERTS_ENABLED", "0") == "1"


def _d(v):
    import datetime as _dt
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    try:
        return _dt.date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def recent_booked(f, today=None) -> bool:
    """Emails cover only BOOKED files (status W) whose pack or delivery date falls
    in the last ALERTS_WINDOW_DAYS (default 365) — Bill, 30 Oct 2026: the first
    run listed files from 2021-22 and a quote that was never booked."""
    import datetime as _dt
    today = today or _dt.date.today()
    try:
        days = max(1, int(os.environ.get("ALERTS_WINDOW_DAYS", "365")))
    except ValueError:
        days = 365
    if str(f.get("status") or "").strip().upper() != "W":
        return False
    start = today - _dt.timedelta(days=days)
    return any(d and start <= d <= today for d in (_d(f.get("pack")), _d(f.get("delivery"))))


def daily_due(key: str, now=None) -> bool:
    """Once per weekday, in the morning window (Mexico City, ALERTS_HOUR default 8,
    for ALERTS_WINDOW_H default 4 h) — never at night after a restart."""
    import datetime as _dt
    mx = _dt.timezone(_dt.timedelta(hours=-6))
    now = now or _dt.datetime.now(mx)
    try:
        hour = int(os.environ.get("ALERTS_HOUR", "8"))
        window = max(1, int(os.environ.get("ALERTS_WINDOW_H", "4")))
    except ValueError:
        hour, window = 8, 4
    last = (_load_state().get("_daily") or {}).get(key)
    return now.weekday() < 5 and hour <= now.hour < hour + window and last != now.date().isoformat()


def mark_daily(key: str, now=None) -> None:
    import datetime as _dt
    mx = _dt.timezone(_dt.timedelta(hours=-6))
    now = now or _dt.datetime.now(mx)
    st = _load_state()
    st.setdefault("_daily", {})[key] = now.date().isoformat()
    _save_state(st)


def send_cost_alerts(files, live=False):
    """SEND one cost-discrepancy email per coordinator (Bill, 30 Oct 2026). A
    coordinator is emailed again only when their list of files changes, at most
    weekly otherwise (COST_ALERTS_REPEAT_DAYS, default 7). Capped per run."""
    import datetime as _dt
    import hashlib
    alerts = build_alerts(files)
    status = {"enabled": cost_alerts_enabled(), "live": bool(live), "alert_count": len(alerts),
              "sent": [], "skipped": 0, "skipped_reason": None}
    if not alerts:
        status["skipped_reason"] = "no discrepancies"
        return status
    if not live:
        status["skipped_reason"] = "demo data — never off non-live data"
        return status
    if not cost_alerts_enabled():
        status["skipped_reason"] = "cost alerts off (COST_ALERTS_ENABLED != 1)"
        return status
    try:
        repeat = max(1, int(os.environ.get("COST_ALERTS_REPEAT_DAYS", "7")))
    except ValueError:
        repeat = 7
    now = _dt.datetime.now()
    state = _load_state()
    seen = state.setdefault("_cost", {})
    try:
        from engine.mailer import GraphMailer
        mailer = GraphMailer(mailbox=_sender())
    except Exception as e:
        status["skipped_reason"] = f"mailer unavailable: {e}"
        return status
    n = 0
    for a in alerts:
        sig = hashlib.sha1(a["body"].encode()).hexdigest()[:16]
        prev = seen.get(a["coordinator"]) or {}
        try:
            age = (now - _dt.datetime.fromisoformat(prev["at"])).days if prev.get("at") else None
        except Exception:
            age = None
        if prev.get("sig") == sig and age is not None and age < repeat:
            status["skipped"] += 1
            continue
        if n >= _max_per_run():
            status["skipped"] += 1
            continue
        rec = {"coordinator": a["coordinator"], "to": a["to"]}
        try:
            mailer.send_mail(a["to"], a["subject"], a["body"], cc=a["cc"])
            rec["ok"] = True
            seen[a["coordinator"]] = {"sig": sig, "at": now.isoformat()}
            n += 1
        except Exception as e:
            rec["ok"] = False
            rec["error"] = str(e)[:200]
        status["sent"].append(rec)
    _save_state(state)
    return status


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
