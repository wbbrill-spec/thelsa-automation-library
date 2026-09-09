"""
alerts.py — Per-person outstanding-item alerts for cross-border shipments (spec §8).

Turns the dashboard's "Needs attention" list into ONE review-ready email per
responsible person: their shipments that are on hold, awaiting payment, with the
customer unresponsive, running out of delivery window, missing documents or a
certificate, or stalled with no checklist progress.

SAFETY — drafts only, never sends
---------------------------------
Same discipline as `coordinator_alerts.py` and the suggested-load email: the
Graph app has Mail.ReadWrite but not Mail.Send, and this module only ever creates
DRAFTS in the Thelsa mailbox for a human to review. It never writes to ClickUp or
Moveware, and it never emails a guessed address — an owner whose email is not
configured is routed to the fallback inbox with a note asking a human to route it.

Who owns a shipment (Bill's rule, 2026-09-09)
---------------------------------------------
Ownership follows the SOURCE SYSTEM, not the per-record assignee: every TIM
(ClickUp) file belongs to Fernanda Mora and every TMS (Moveware) file belongs to
Sara Reyes. ClickUp subtask assignees and Moveware's moveManager are no longer
used to route — ClickUp assignees are mostly blank, and one owner per system is
how the team actually works. Both addresses are built in below, so nothing needs
configuring; CB_ALERT_TIM_OWNER / CB_ALERT_TMS_OWNER change the owner, and
CB_ALERT_FOLDER_OWNERS can hand one agent folder to someone else.

Config (all optional; nothing here is a secret)
-----------------------------------------------
  CB_ALERT_EMAILS          name → email map, JSON ({"Fernanda Mora":"…"}) or
                           "Fernanda Mora:f@thelsa.com,Sara Reyes:s@thelsa.com".
  CB_ALERT_TIM_OWNER       who owns ClickUp files (default "Fernanda Mora").
  CB_ALERT_TMS_OWNER       who owns Moveware files (default "Sara Reyes").
  CB_ALERT_FOLDER_OWNERS   agent/folder → owner name, same two formats; overrides
                           the TIM owner for that folder only.
  CB_ALERT_CC              cc address(es), comma-separated (default: none).
  CB_ALERT_FALLBACK        where alerts for an unresolved owner go
                           (default ALERT_EMAIL, else bbrill@thelsa.com).
  CB_ALERTS_ENABLED=1      let the daily scheduler create drafts (off by default;
                           the dashboard button always works on demand).
  CB_ALERTS_HOUR           scheduler hour, server local time (default 8).
  CB_ALERTS_DRAFT_FOLDER   mailbox folder for the drafts (optional).
  CB_ALERTS_MAX            cap drafts per run (default 25).
  CB_ALERT_REPEAT_HOURS    don't re-alert the same shipment within N hours
                           (default 72). Set 0 to alert every run.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from typing import Optional

from .models import TIM_DELIVERY_WINDOW_DAYS, Shipment, Source, norm_text

SITE = "https://thelsa.inflectionpointnow.com/crossborder"
TIM_OWNER = "Fernanda Mora"      # every ClickUp file
TMS_OWNER = "Sara Reyes"         # every Moveware file
DEFAULT_OWNER = TIM_OWNER        # back-compat alias

# Confirmed with Bill 2026-09-09 from the Thelsa directory. Note there is also a
# Fernanda Viana at SIT Spain (an external agent) — not this person, and never a
# recipient of internal shipment lists. CB_ALERT_EMAILS overrides these.
KNOWN_EMAILS: dict[str, str] = {
    "fernanda mora": "fernandamora@thelsa.com",
    "sara reyes": "sarareyes@thelsa.com",
}

# Priority order — the most urgent reason a shipment appears in someone's list.
ALERT_FLAGS = [
    "on_hold", "payment_pending", "unresponsive", "window_risk",
    "docs_incomplete", "docs_pending", "certificate_pending", "visa_pending",
    "stalled",
]

# (Spanish, English) label for each reason.
FLAG_LABEL: dict[str, tuple[str, str]] = {
    "on_hold": ("En espera / detenido", "On hold"),
    "payment_pending": ("Pago pendiente", "Payment pending"),
    "unresponsive": ("Cliente no responde", "Customer unresponsive"),
    "window_risk": ("Ventana de entrega por vencer", "Delivery window closing"),
    "docs_incomplete": ("Documentos incompletos", "Documents incomplete"),
    "docs_pending": ("Documentos pendientes", "Documents pending"),
    "certificate_pending": ("Certificado de menaje pendiente", "Certificate pending"),
    "visa_pending": ("Visa pendiente", "Visa pending"),
    "stalled": ("Sin avance en la lista de pasos", "No checklist progress"),
}


# ── config helpers ───────────────────────────────────────────────────────────
def _env_map(name: str) -> dict:
    """Parse a name→value map from JSON or 'Key:value,Key:value'. Lower-keyed."""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return {}
    out: dict = {}
    if raw.startswith("{"):
        try:
            for k, v in json.loads(raw).items():
                if k and v:
                    out[norm_text(k)] = str(v).strip()
        except (ValueError, TypeError, AttributeError):
            return {}
        return out
    for pair in raw.split(","):
        if ":" in pair:
            k, v = pair.split(":", 1)
            if k.strip() and v.strip():
                out[norm_text(k)] = v.strip()
    return out


def _cc_list() -> list[str]:
    raw = os.environ.get("CB_ALERT_CC", "") or ""
    return [a.strip() for a in raw.replace(";", ",").split(",") if a.strip()]


def _fallback_inbox() -> str:
    return (os.environ.get("CB_ALERT_FALLBACK")
            or os.environ.get("ALERT_EMAIL")
            or "bbrill@thelsa.com").strip()


def owner_name_for_source(source: Source) -> str:
    """The one person who owns every file in a source system."""
    if source is Source.TMS:
        return (os.environ.get("CB_ALERT_TMS_OWNER") or TMS_OWNER).strip()
    return (os.environ.get("CB_ALERT_TIM_OWNER")
            or os.environ.get("CB_ALERT_DEFAULT_OWNER") or TIM_OWNER).strip()


def _repeat_hours() -> int:
    try:
        return max(0, int(os.environ.get("CB_ALERT_REPEAT_HOURS", "72") or 72))
    except ValueError:
        return 72


def _window_risk_days() -> int:
    try:
        return max(0, int(os.environ.get("PLAN_WINDOW_RISK_DAYS", "7") or 7))
    except ValueError:
        return 7


# ── ownership ────────────────────────────────────────────────────────────────
def owner_for(s: Shipment) -> tuple[str, str, bool]:
    """(owner name, email, resolved).

    Ownership is by source system — TIM → the TIM owner, TMS → the TMS owner —
    with CB_ALERT_FOLDER_OWNERS able to hand one agent folder to someone else.
    The address comes from CB_ALERT_EMAILS, or from Moveware itself when the
    owner is the job's own move manager. An owner with no address on file is
    routed to the fallback inbox (resolved=False) — never a guessed address.
    """
    emails = _env_map("CB_ALERT_EMAILS")

    name = ""
    if s.source is Source.TIM:
        name = _env_map("CB_ALERT_FOLDER_OWNERS").get(norm_text(s.agent)) or ""
    name = name or owner_name_for_source(s.source)

    email = emails.get(norm_text(name)) or KNOWN_EMAILS.get(norm_text(name))
    if email:
        return name, email, True

    # Moveware carries each job's move manager; use that address only when it is
    # the same person the file is assigned to.
    if s.source is Source.TMS:
        direct = str((s.extra or {}).get("coordinator_email") or "").strip()
        manager = next((str(a or "").strip() for a in (s.assignees or []) if a), "")
        if "@" in direct and norm_text(manager) == norm_text(name):
            return name, direct, True

    return name, _fallback_inbox(), False


# ── which shipments need attention ───────────────────────────────────────────
def deadline_for(s: Shipment) -> Optional[dt.date]:
    """The date the shipment must be delivered by — the same rule the
    consolidation engine plans against (engine.make_item)."""
    if s.source is Source.TIM:
        ready = s.milestones.get("green_light") or s.ready_date
        return (ready + dt.timedelta(days=TIM_DELIVERY_WINDOW_DAYS)) if ready else None
    return s.delivery_date


def issues_for(s: Shipment, today: Optional[dt.date] = None) -> list[str]:
    """The alert reasons carried by one open shipment, most urgent first."""
    if not s.is_open:
        return []
    today = today or dt.date.today()
    flags = set(s.status_flags or [])
    dl = deadline_for(s)
    if dl and (dl - today).days <= _window_risk_days():
        flags.add("window_risk")
    return [f for f in ALERT_FLAGS if f in flags]


def _row(s: Shipment, issues: list[str], today: dt.date) -> dict:
    dl = deadline_for(s)
    return {
        "id": s.id,
        "source": s.source.value,
        "customer": s.customer_name,
        "agent": s.agent,
        "reference": s.reference_number,
        "destination": s.destination or "",
        "hub": s.destination_hub.value,
        "stage": s.stage.value,
        "step": s.current_step or s.source_status or "",
        "issues": issues,
        "top_issue": issues[0] if issues else "",
        "days_since_progress": s.days_since_progress,
        "deadline": dl.isoformat() if dl else None,
        "days_left": (dl - today).days if dl else None,
        "m3": s.planning_m3 or None,
        "url": s.url,
    }


# ── email body ───────────────────────────────────────────────────────────────
def _detail(r: dict, lang: str) -> str:
    """The one-line 'why' after a shipment, in the requested language."""
    top = r["top_issue"]
    if top == "window_risk" and r.get("days_left") is not None:
        d = r["days_left"]
        if lang == "es":
            return (f"vence {r['deadline']} ({d} días)" if d >= 0
                    else f"venció {r['deadline']} (hace {abs(d)} días)")
        return (f"due {r['deadline']} ({d} days)" if d >= 0
                else f"overdue since {r['deadline']} ({abs(d)} days)")
    if top == "stalled" and r.get("days_since_progress") is not None:
        return (f"{r['days_since_progress']} días sin avance" if lang == "es"
                else f"{r['days_since_progress']} days without progress")
    return r["step"] or ""


def _lines(rows: list[dict], lang: str) -> list[str]:
    out = []
    for r in rows:
        labels = " · ".join(FLAG_LABEL.get(f, (f, f))[0 if lang == "es" else 1]
                            for f in r["issues"])
        head = f"  • [{r['source']}] {r['customer']}"
        if r["reference"]:
            head += f" — {r['reference']}"
        if r["agent"]:
            head += f" ({r['agent']})"
        out.append(head)
        detail = _detail(r, lang)
        out.append(f"      {labels}" + (f" — {detail}" if detail else ""))
        if r["url"]:
            out.append(f"      {r['url']}")
    return out


def alert_body(owner: str, rows: list[dict], resolved: bool, site: str = SITE) -> str:
    """Spanish first, English below — one alert for one person's shipments."""
    n = len(rows)
    es = [
        f"Hola {owner or 'equipo'},",
        "",
        f"El tablero de embarques transfronterizos marcó {n} embarque"
        f"{'s' if n != 1 else ''} a tu nombre que necesita"
        f"{'n' if n != 1 else ''} atención. Por favor revísalos y actualiza "
        "ClickUp / Moveware; este correo es informativo y no reserva ni cancela nada.",
        "",
        "Embarques que requieren atención:",
        *_lines(rows, "es"),
        "",
        f"Tablero en vivo: {site}",
    ]
    en = [
        f"Hi {owner or 'team'},",
        "",
        f"The cross-border board flagged {n} shipment{'s' if n != 1 else ''} "
        f"assigned to you that need{'' if n != 1 else 's'} attention. Please "
        "review and update ClickUp / Moveware; this email is informational and "
        "books or cancels nothing.",
        "",
        "Shipments needing attention:",
        *_lines(rows, "en"),
        "",
        f"Live board: {site}",
    ]
    parts = ["\n".join(es), "", "— — — — —", "", "\n".join(en), ""]
    if not resolved:
        parts.append("[No email on file for this person — routed here for manual "
                     "assignment. Add them to CB_ALERT_EMAILS.]")
        parts.append("")
    parts.append("— Thelsa Automation Library · Cross-Border Dashboard")
    return "\n".join(parts)


# ── building the alerts ──────────────────────────────────────────────────────
def build_alerts(shipments: list[Shipment], today: Optional[dt.date] = None) -> list[dict]:
    """Group every open shipment that needs attention by its owner. Pure —
    creates nothing, writes nothing."""
    today = today or dt.date.today()
    by_owner: dict[str, dict] = {}
    for s in shipments or []:
        issues = issues_for(s, today)
        if not issues:
            continue
        name, email, resolved = owner_for(s)
        key = name or "Unassigned"
        b = by_owner.setdefault(key, {"owner": key, "to": email, "resolved": resolved,
                                      "rows": []})
        if resolved and not b["resolved"]:          # a resolved address wins
            b["to"], b["resolved"] = email, True
        b["rows"].append(_row(s, issues, today))

    order = {f: i for i, f in enumerate(ALERT_FLAGS)}
    cc = _cc_list()
    alerts = []
    for b in sorted(by_owner.values(), key=lambda x: (-len(x["rows"]), x["owner"])):
        rows = sorted(b["rows"], key=lambda r: (order.get(r["top_issue"], 99),
                                                r["days_left"] if r["days_left"] is not None else 9999,
                                                r["customer"]))
        n = len(rows)
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["top_issue"]] = counts.get(r["top_issue"], 0) + 1
        head = ", ".join(f"{v} {FLAG_LABEL.get(k, (k, k))[1].lower()}"
                         for k, v in sorted(counts.items(), key=lambda kv: order.get(kv[0], 99)))
        subject = (f"[Transfronterizo] {n} embarque{'s' if n != 1 else ''} "
                   f"requiere{'n' if n != 1 else ''} atención — {b['owner']} / "
                   f"{n} shipment{'s' if n != 1 else ''} "
                   f"need{'' if n != 1 else 's'} attention ({head})")
        alerts.append({
            "owner": b["owner"], "to": b["to"], "cc": cc, "resolved": b["resolved"],
            "subject": subject, "body": alert_body(b["owner"], rows, b["resolved"]),
            "shipment_count": n, "by_issue": counts, "shipments": rows,
        })
    return alerts


# ── de-dupe state (scheduler only) ───────────────────────────────────────────
def _state_path() -> str:
    for c in ("/var/data", "/data"):
        if os.path.isdir(c) and os.access(c, os.W_OK):
            return os.path.join(c, "crossborder_alert_state.json")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "crossborder_alert_state.json")


def _load_state() -> dict:
    try:
        with open(_state_path()) as f:
            return json.load(f) or {}
    except Exception:  # noqa: BLE001
        return {}


def _save_state(state: dict) -> None:
    try:
        p = _state_path()
        with open(p + ".tmp", "w") as f:
            json.dump(state, f)
        os.replace(p + ".tmp", p)
    except Exception:  # noqa: BLE001
        pass


def due_shipments(shipments: list[Shipment], state: dict, now: dt.datetime,
                  today: Optional[dt.date] = None) -> list[Shipment]:
    """Drop shipments alerted within CB_ALERT_REPEAT_HOURS whose reasons have not
    changed — a coordinator should not get the same list every morning."""
    hours = _repeat_hours()
    if not hours:
        return list(shipments or [])
    out = []
    for s in shipments or []:
        issues = issues_for(s, today)
        if not issues:
            continue
        e = state.get(s.id)
        if not isinstance(e, dict) or not e.get("last"):
            out.append(s)
            continue
        if e.get("issues") != issues:          # something changed — tell them
            out.append(s)
            continue
        try:
            last = dt.datetime.fromisoformat(e["last"])
        except (ValueError, TypeError):
            out.append(s)
            continue
        if (now - last).total_seconds() / 3600.0 >= hours:
            out.append(s)
    return out


def alerts_enabled() -> bool:
    return (os.environ.get("CB_ALERTS_ENABLED") == "1"
            and os.environ.get("DRY_RUN", "0") != "1")


def _max_per_run() -> int:
    try:
        return max(1, int(os.environ.get("CB_ALERTS_MAX", "25") or 25))
    except ValueError:
        return 25


def create_drafts(shipments: list[Shipment], actor: str = "scheduler",
                  respect_state: bool = True, today: Optional[dt.date] = None) -> dict:
    """Create one Graph DRAFT per owner. Never sends, never writes to ClickUp or
    Moveware. `respect_state=False` is the on-demand dashboard button: a human
    asked for it, so nothing is suppressed and the de-dupe clock is not touched."""
    now = dt.datetime.now()
    status: dict = {"actor": actor, "enabled": alerts_enabled(), "mode": "draft",
                    "drafts": [], "alert_count": 0, "shipments_flagged": 0,
                    "suppressed": 0, "skipped_reason": None,
                    "at": now.isoformat(timespec="seconds")}
    if not shipments:
        status["skipped_reason"] = "no shipments loaded yet"
        return status

    flagged = [s for s in shipments if issues_for(s, today)]
    status["shipments_flagged"] = len(flagged)
    state = _load_state() if respect_state else {}
    fresh = due_shipments(flagged, state, now, today) if respect_state else flagged
    status["suppressed"] = len(flagged) - len(fresh)

    alerts = build_alerts(fresh, today)
    status["alert_count"] = len(alerts)
    if not alerts:
        status["skipped_reason"] = ("nothing needs attention" if not flagged
                                    else f"all {len(flagged)} flagged shipments were "
                                         f"alerted within the last {_repeat_hours()}h")
        return status
    alerts = alerts[:_max_per_run()]

    try:
        from engine.mailer import GraphMailer     # the library's app-only Graph adapter
        mailer = GraphMailer(mailbox=(os.environ.get("GRAPH_SENDER") or "bbrill@thelsa.com"))
    except Exception as exc:  # noqa: BLE001
        status["skipped_reason"] = f"mailer unavailable: {type(exc).__name__}: {exc}"
        return status

    folder = os.environ.get("CB_ALERTS_DRAFT_FOLDER") or None
    for a in alerts:
        rec = {"owner": a["owner"], "to": a["to"], "resolved": a["resolved"],
               "shipments": a["shipment_count"]}
        try:
            d = mailer.create_draft(a["to"], a["subject"], a["body"], cc=a["cc"],
                                    folder=folder)
            rec.update(ok=True, id=d.get("id", ""), folder=d.get("folder", ""),
                       webLink=d.get("webLink", ""))
            if respect_state:
                for r in a["shipments"]:
                    state[r["id"]] = {"last": now.isoformat(), "issues": r["issues"]}
        except Exception as exc:  # noqa: BLE001
            rec.update(ok=False, error=f"{type(exc).__name__}: {exc}")
        status["drafts"].append(rec)
    if respect_state:
        _save_state(state)
    return status
