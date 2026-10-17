"""
date_gap_alerts.py — every coordinator, every day: the files where a key date
is missing in Moveware, until it is entered.

Bill, 16 Oct 2026, after the Moveware training with Jose Rivas: "The team needs
to enter the dates — zero exceptions. We will flag gaps in their data entry and
enforce this immediately." Jose's advice, backed by Bill: enter the planned
dates (Load / Pack, Delivery) as soon as they are known and correct them when
they happen; Moveware then schedules the reminders that hang off them.

WHAT COUNTS AS A GAP (the FAIM audit's own rules, faim_web.faim_snapshot)
  - pack date missing on a booked move, except agent imports (IMA): Thelsa does
    not pack those (Lupita, 16 Oct: "IMA ... no nos interesa cuando se empaca");
  - delivery date missing, except agent exports (EXA): Thelsa's part ends at
    origin;
  - a booked file (status W, won in the last 12 months) with no key date at all;
  - a key date in the wrong year (more than a year before the file was won, or
    more than 18 months ahead: 2020 typed for 2026 on three files on 15 Oct).
  Where SIT's Plan de Viajes has the day, the email says so, so the coordinator
  can copy it — but it stays a gap until it is in Moveware.

WHO GETS WHAT
  - one email per coordinator listing their files (to the email Moveware carries
    on the file, else AUDIT_COORDINATOR_EMAILS, else the fallback inbox — never
    a guessed address), cc the key users Lupita and Sara (DATE_ALERT_CC);
  - one summary for Bill (DATE_ALERT_SUMMARY_TO, default his automation address),
    cc the same key users: gaps per coordinator, so the follow-up is visible.
  - Spanish first, English below.

SAFETY — nothing goes out by accident
  DATE_ALERTS_ENABLED=1 and DRY_RUN!=1   master switch (else: preview only)
  DATE_ALERTS_SEND=1                     actually SEND; otherwise DRAFTS in the
                                         "Audit Alert Drafts" folder
  live data only, and only once the file book has been read in full (a half-read
  book would accuse people of gaps that are not there)
  once per coordinator per day (state file next to the audit cache);
  DATE_ALERTS_MAX caps emails per run (default 30).
  DATE_ALERTS_AUTO=1 lets the FAIM keeper run it by itself once a day, from
  DATE_ALERTS_HOUR (Mexico City time, default 8), only within DATE_ALERTS_WINDOW_H
  hours of it (default 4), so a restart never mails the team at night.
"""
from __future__ import annotations

import datetime as dt
import json
import os

import coordinator_alerts as ca

MEXICO = dt.timezone(dt.timedelta(hours=-6))
DEFAULT_CC = "maria.gonzalez@thelsa.com,sarareyes@thelsa.com"
DEFAULT_SUMMARY_TO = "bill.brill@inflectionpointnow.com"
WORD_ES = {"pack": "fecha de empaque / carga", "delivery": "fecha de entrega"}
WORD_EN = {"pack": "pack / load date", "delivery": "delivery date"}


def _list(name, default):
    return [a.strip() for a in os.environ.get(name, default).split(",") if a.strip()]


def enabled() -> bool:
    return os.environ.get("DATE_ALERTS_ENABLED") == "1" and os.environ.get("DRY_RUN", "0") != "1"


def send_enabled() -> bool:
    return enabled() and os.environ.get("DATE_ALERTS_SEND") == "1"


def _max() -> int:
    try:
        return max(1, int(os.environ.get("DATE_ALERTS_MAX", "30")))
    except ValueError:
        return 30


def _state_path() -> str:
    return os.path.join(os.path.dirname(ca._state_path()), "date_gap_alert_state.json")


def _load_state() -> dict:
    try:
        with open(_state_path()) as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        with open(_state_path(), "w") as fh:
            json.dump(state, fh)
    except Exception:
        pass


def _line(g) -> str:
    if g.get("wrong_year"):
        es = ", ".join(f"{WORD_ES[k]} {g['dates'][k]}" for k in g["wrong_year"])
        en = ", ".join(f"{WORD_EN[k]} {g['dates'][k]}" for k in g["wrong_year"])
        return (f"• {g['file']} ({g['type'] or 's/tipo'}): fecha con año equivocado: {es}\n"
                f"  {g['file']}: date in the wrong year: {en}")
    if g.get("no_dates"):
        es = f"• {g['file']} ({g['type'] or 's/tipo'}): ganado el {g.get('booked', '')} y SIN NINGUNA fecha clave"
        en = f"  {g['file']}: won {g.get('booked', '')}, no key date at all"
        return es + "\n" + en
    es_bits, en_bits = [], []
    for k in g["missing"]:
        hint = g.get(f"sit_{k}") or ""
        es_bits.append(WORD_ES[k] + (f" (Plan de Viajes: {hint})" if hint else ""))
        en_bits.append(WORD_EN[k] + (f" (SIT plan: {hint})" if hint else ""))
    return (f"• {g['file']} ({g['type'] or 's/tipo'}): falta {', '.join(es_bits)}\n"
            f"  {g['file']}: missing {', '.join(en_bits)}")


def build(date_gaps) -> dict:
    """{"coordinators": [alert...], "summary": alert | None} from faim_snapshot's
    "date_gaps". Each alert: to, cc, subject, body, coordinator, files."""
    by = {}
    for g in date_gaps or []:
        by.setdefault(g.get("coord") or "Unassigned", []).append(g)
    cc = _list("DATE_ALERT_CC", DEFAULT_CC)
    out = []
    for coord in sorted(by, key=str.lower):
        files = by[coord]
        direct = next((g.get("coord_email") for g in files if g.get("coord_email")), "")
        to, resolved = ca.resolve_email(None if coord == "Unassigned" else coord, direct)
        n = len(files)
        body = [f"Hola {coord if coord != 'Unassigned' else 'equipo'},", "",
                f"En Moveware faltan fechas clave en {n} expediente{'s' if n != 1 else ''} a tu cargo. "
                "Por favor captúralas hoy. Si la fecha todavía no ocurre, captura la fecha planeada y "
                "corrígela cuando suceda: así Moveware genera los recordatorios y los reportes salen completos.",
                "Regla: sin excepciones. Este aviso se repite cada día mientras falte alguna fecha.", ""]
        body += [_line(g) for g in files]
        body += ["", "—", f"Hi {coord if coord != 'Unassigned' else 'team'}, Moveware is missing key dates on "
                 f"the {n} file(s) above. Please enter them today; if a date is still ahead, enter the planned "
                 "date and correct it when it happens. This reminder repeats daily until every date is in.", ""]
        if not resolved:
            body.append("[Sin correo del coordinador en el expediente: asignar manualmente. / Coordinator "
                        "email not on file: routed here for manual assignment.]")
        body.append("— Thelsa Automation Library · Auditoría FAIM / FAIM audit")
        out.append({"coordinator": coord, "to": to, "cc": cc, "files": n, "resolved": resolved,
                    "subject": f"Fechas faltantes en Moveware: {n} expediente{'s' if n != 1 else ''} / "
                               f"Missing Moveware dates: {n} file{'s' if n != 1 else ''}",
                    "body": "\n".join(body)})
    summary = None
    if out:
        total = sum(a["files"] for a in out)
        rows = [f"• {a['coordinator']}: {a['files']}" + ("" if a["resolved"] else " (sin correo / no email)")
                for a in sorted(out, key=lambda a: -a["files"])]
        no_dates = sum(1 for g in date_gaps if g.get("no_dates"))
        summary = {"coordinator": "(summary)", "to": os.environ.get("DATE_ALERT_SUMMARY_TO", DEFAULT_SUMMARY_TO),
                   "cc": cc, "files": total, "resolved": True,
                   "subject": f"Fechas faltantes en Moveware: {total} expedientes, {len(out)} coordinadores / "
                              f"Missing Moveware dates: {total} files",
                   "body": "\n".join(["Expedientes con fechas clave faltantes, por coordinador / Files missing key "
                                      "dates, by coordinator:", ""] + rows +
                                     ["", f"De ellos, {no_dates} ganados sin ninguna fecha / of which {no_dates} "
                                      "booked with no key date at all.", "",
                                      "Cada coordinador recibió su lista. / Each coordinator was sent their list.",
                                      "— Thelsa Automation Library · Auditoría FAIM"])}
    return {"coordinators": out, "summary": summary}


def dispatch(date_gaps, live: bool, complete: bool, send=None, now=None) -> dict:
    """Draft or send today's alerts. Returns what happened (no file contents)."""
    now = now or dt.datetime.now(MEXICO)
    today = now.date().isoformat()
    will_send = send_enabled() if send is None else bool(send)
    plan = build(date_gaps)
    status = {"enabled": enabled(), "mode": "send" if will_send else "draft", "live": bool(live),
              "complete": bool(complete), "coordinators": len(plan["coordinators"]),
              "files": sum(a["files"] for a in plan["coordinators"]), "done": [], "skipped_reason": None}
    if not live:
        status["skipped_reason"] = "not live Moveware data"
        return status
    if not complete:
        status["skipped_reason"] = "the file book is still being read; alerts wait for a full read"
        return status
    if not enabled():
        status["skipped_reason"] = "DATE_ALERTS_ENABLED != 1 (or DRY_RUN=1): preview only"
        return status
    state = _load_state()
    todo = [a for a in plan["coordinators"] if state.get(a["coordinator"]) != today]
    if plan["summary"] and state.get("(summary)") != today:
        todo.append(plan["summary"])
    if not todo:
        status["skipped_reason"] = "already alerted today"
        return status
    try:
        from engine.mailer import GraphMailer
        mailer = GraphMailer(mailbox=ca._sender())
    except Exception as e:                          # noqa: BLE001
        status["skipped_reason"] = f"mailer unavailable: {e}"
        return status
    for a in todo[:_max()]:
        rec = {"coordinator": a["coordinator"], "to": a["to"], "files": a["files"]}
        try:
            if will_send:
                mailer.send_mail(a["to"], a["subject"], a["body"], cc=a["cc"])
            else:
                mailer.create_draft(a["to"], a["subject"], a["body"], cc=a["cc"], folder=ca._draft_folder())
            rec["ok"] = True
            state[a["coordinator"]] = today
        except Exception as e:                      # noqa: BLE001
            rec["ok"], rec["error"] = False, str(e)[:200]
        status["done"].append(rec)
    _save_state(state)
    return status


def due_now(now=None) -> bool:
    """For the keeper: once a day, from DATE_ALERTS_HOUR (Mexico City time)."""
    if os.environ.get("DATE_ALERTS_AUTO") != "1":
        return False
    now = now or dt.datetime.now(MEXICO)
    try:
        hour = int(os.environ.get("DATE_ALERTS_HOUR", "8"))
    except ValueError:
        hour = 8
    try:
        window = max(1, int(os.environ.get("DATE_ALERTS_WINDOW_H", "4")))
    except ValueError:
        window = 4
    # Only inside the morning window: a restart or a late full read of the file
    # book must not email the whole team at night.
    return (now.weekday() < 5 and hour <= now.hour < hour + window
            and _load_state().get("(summary)") != now.date().isoformat())
