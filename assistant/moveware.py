"""
Moveware to-dos for customer-service coordinators.

Reads the move files the library's background Moveware auditor (mw_live) has
already loaded — no extra Moveware API calls — keeps only the files whose
coordinator email matches this user, and turns each file's state into concrete
actions:

  invoice_file    Move happened (packed, or delivered for US Embassy/Consulate
                  files) and the file has NOT been invoiced.
  invoice_charge  Additional charges were approved by email but not invoiced
                  (from the under-billing scanner).
  upload_docs     File is packed but has no actual weight in Moveware yet —
                  get the certified weight ticket + packing list from SIT and
                  upload/send them.
  request_docs    Pack date is within REQUEST_DOCS_DAYS and the file has no
                  declared value / insurance on it — request the valued
                  inventory / insurance form from the customer.
  confirm_done    A planned Load / Delivery date has passed and the action is
                  still open in Moveware: mark it completed or fix the date
                  (assistant/mw_confirm.py).
  assign_coord    A booked file with a missing key date has NO coordinator, so
                  nobody's dashboard or email can carry it. Shown to the key
                  users (DATE_ALERT_CC: Lupita, Sara) to assign one.
  enter_dates     A key date (Load/Pack, Delivery) is missing or typed in the
                  wrong year in Moveware. Same list, same rules as the daily
                  missing-dates email (faim_web date_gaps → date_gap_alerts), so
                  the dashboard and the email can never disagree. Always Urgent,
                  even once seen: it stays at the top until the date is entered
                  (Bill, 16 Oct 2026: data entry is not optional).

Rules are deliberately simple and each is one function, so they can be tuned.
"""

import datetime as _dt
import os

REQUEST_DOCS_DAYS = int(os.environ.get("ASSISTANT_REQUEST_DOCS_DAYS", "10"))
MW_JOB_URL = os.environ.get("ASSISTANT_MW_JOB_URL", "")   # e.g. https://…/job/{job}


def _today():
    return _dt.date.today()


def _as_date(v):
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    if isinstance(v, str) and v:
        try:
            return _dt.date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def _money(v) -> str:
    return "$" + f"{float(v or 0):,.0f}"


def _url(job):
    return MW_JOB_URL.format(job=job) if MW_JOB_URL else None


def _dt_utc(d):
    return _dt.datetime(d.year, d.month, d.day, 12, tzinfo=_dt.timezone.utc) if d else None


def tasks_for_files(files: list, today=None) -> list:
    """Coordinator's files (mw_live dicts) → dashboard item dicts."""
    today = today or _today()
    out = []
    for f in files:
        job = str(f.get("job") or "")
        if not job:
            continue
        status = (f.get("status") or "").upper()
        if status in ("C", "L", "X"):          # cancelled / lead / lost
            continue
        client = f.get("client") or "Customer"
        pack, delivery = _as_date(f.get("pack")), _as_date(f.get("delivery"))
        packed = bool(pack and pack <= today)
        delivered = bool(delivery and delivery <= today)
        value = f.get("inv_amt") if f.get("invoiced") else (f.get("sell") or 0)
        base = {"from_name": client, "from_addr": None, "url": _url(job)}

        # 1. Invoice the file
        billable = delivered if f.get("is_embassy") else (packed or delivered)
        if billable and not f.get("invoiced"):
            since = delivery if (f.get("is_embassy") or (delivered and not packed)) else pack
            days = (today - since).days if since else 0
            why = "delivered" if f.get("is_embassy") else "packed"
            out.append({**base, "kind": "invoice_file", "external_id": f"{job}:invoice",
                        "subject": f"Invoice file {job} — {client}",
                        "snippet": f"Move {why} {days} day(s) ago and not invoiced yet"
                                   + (f" · {_money(value)} to bill" if value else "")
                                   + (" · US Embassy/Consulate (bill after delivery)"
                                      if f.get("is_embassy") else ""),
                        "received_at": _dt_utc(since),
                        "meta": {"job": job, "days": days, "value": value, "due": since,
                                 "client": client, "embassy": bool(f.get("is_embassy"))}})

        # 2. Upload / send the weight ticket + packing list
        if packed and not f.get("act_wt"):
            days = (today - pack).days
            out.append({**base, "kind": "upload_docs", "external_id": f"{job}:docs",
                        "subject": f"Send weight ticket & packing list — {job} {client}",
                        "snippet": f"Packed {days} day(s) ago; no actual weight in Moveware. "
                                   "Download the certified weight ticket + packing list from SIT "
                                   "and upload them.",
                        "received_at": _dt_utc(pack),
                        "meta": {"job": job, "days": days, "due": pack, "client": client}})

        # 3. Request documents before the pack
        if pack and today <= pack <= today + _dt.timedelta(days=REQUEST_DOCS_DAYS) \
                and not f.get("declared") and not f.get("ins"):
            left = (pack - today).days
            out.append({**base, "kind": "request_docs", "external_id": f"{job}:request",
                        "subject": f"Request valued inventory / insurance form — {job} {client}",
                        "snippet": f"Pack in {left} day(s) and no declared value or insurance "
                                   "on file.",
                        "received_at": _dt_utc(pack),
                        "meta": {"job": job, "days_left": left, "due": pack, "client": client}})
    return out


def tasks_for_underbilling(rows: list) -> list:
    out = []
    for r in rows:
        job = str(r.get("job") or "")
        approved = float(r.get("approved_total") or 0)
        invoiced = float(r.get("invoiced") or 0)
        gap = round(approved - invoiced, 2)
        if not job or gap <= 1:
            continue
        out.append({"kind": "invoice_charge", "external_id": f"{job}:charge",
                    "from_name": r.get("client") or "Customer", "from_addr": None,
                    "subject": f"Invoice approved extra charges — {job}",
                    "snippet": f"{_money(approved)} approved by email, {_money(invoiced)} invoiced "
                               f"({_money(gap)} not billed)",
                    "url": _url(job), "received_at": None,
                    "meta": {"job": job, "value": gap, "approved": approved,
                             "invoiced": invoiced}})
    return out


def tasks_for_date_gaps(gaps: list, emails) -> list:
    """faim_web date_gaps rows whose coordinator is one of `emails` → items.

    A gap row without a coordinator email cannot be placed on anyone's
    dashboard; the daily email routes those to the fallback inbox instead."""
    out = []
    for g in gaps or []:
        job = str(g.get("file") or "")
        if not job or not (g.get("coord_email") or "").strip() or not _matches(g.get("coord_email"), emails):
            continue
        meta = {"job": job, "type": g.get("type") or "", "missing": list(g.get("missing") or []),
                "wrong_year": list(g.get("wrong_year") or []), "dates": dict(g.get("dates") or {}),
                "no_dates": bool(g.get("no_dates")), "booked": g.get("booked") or "",
                "sit_pack": g.get("sit_pack") or "", "sit_delivery": g.get("sit_delivery") or ""}
        item = {"kind": "enter_dates", "external_id": f"{job}:dates", "from_name": "Moveware",
                "from_addr": None, "url": _url(job), "received_at": None, "meta": meta}
        from . import i18n
        item["subject"], item["snippet"] = i18n.item_text(item, "en")
        out.append(item)
    return out


def key_users() -> set:
    """Moveware key users (Lupita, Sara) — same list the missing-dates email cc's."""
    import date_gap_alerts as dga
    return {e.lower() for e in dga._list("DATE_ALERT_CC", dga.DEFAULT_CC)}


def tasks_for_unassigned(gaps: list) -> list:
    """Gap rows with no coordinator email → 'assign a coordinator' items."""
    out = []
    for g in gaps or []:
        job = str(g.get("file") or "")
        if not job or (g.get("coord_email") or "").strip():
            continue
        meta = {"job": job, "type": g.get("type") or "", "missing": list(g.get("missing") or []),
                "no_dates": bool(g.get("no_dates")), "coord": g.get("coord") or ""}
        item = {"kind": "assign_coord", "external_id": f"{job}:assign", "from_name": "Moveware",
                "from_addr": None, "url": _url(job), "received_at": None, "meta": meta}
        from . import i18n
        item["subject"], item["snippet"] = i18n.item_text(item, "en")
        out.append(item)
    return out


def _date_gaps():
    """(gaps, complete) from the same source as the daily missing-dates email.
    Returns (None, False) when the shared file book is not readable."""
    try:
        import faim_web
        gaps, live, complete = faim_web._date_gaps_now()
    except Exception:
        return None, False
    if not live:
        return None, False
    return gaps, bool(complete)


def _matches(addr, emails):
    return (addr or "").strip().lower() in emails


def wants_file(f, user, emails=None) -> bool:
    """Does this Moveware file belong on this user's dashboard?

    Two independent reasons, either is enough:
      * the file's coordinator is the user, their Moveware override, or someone
        on their watch list (users.mw_watch);
      * the file is a US Embassy / Consulate booking and the user follows all of
        those (users.mw_embassy) — for the documentation side, which works every
        embassy file regardless of whose coordinator name is on it.
    """
    if emails is None:
        emails = watched_emails(user)
    if _matches(f.get("coordinator_email"), emails):
        return True
    return bool(user.get("mw_embassy")) and bool(f.get("is_embassy"))


def watched_emails(user) -> set:
    """Every coordinator address whose files this user should see: their own,
    their Moveware override, plus anyone on their watch list (users.mw_watch).
    A manager can therefore follow their coordinators' files without being the
    coordinator on any of them."""
    emails = {(user.get("email") or "").strip().lower()}
    if user.get("moveware_email"):
        emails.add(user["moveware_email"].strip().lower())
    watch = user.get("mw_watch")
    if watch:
        parts = watch.replace(";", ",").split(",") if isinstance(watch, str) else watch
        emails.update(e for e in (str(p).strip().lower() for p in parts) if "@" in e)
    emails.discard("")
    return emails


def tasks_for_user(user) -> list:
    """All Moveware to-dos for one user. Returns None if Moveware data isn't
    loaded yet (so the caller keeps the previous items instead of wiping them)."""
    try:
        import mw_live
    except Exception:
        return None
    if not mw_live.have_creds():
        return None
    mw_live.ensure_auditor()
    files = mw_live.audited_in_window()
    if not files:
        return None
    emails = watched_emails(user)
    mine = [f for f in files if wants_file(f, user, emails)]
    items = tasks_for_files(mine)
    # "Invoice this file" now comes from AuditTrack's own worklist (booked moves
    # only, drafts not counted, Embassy files after delivery, Finance's invoice
    # register) so the dashboard matches AuditTrack and its emails exactly.
    try:
        from . import auditrack, db as _db
        if auditrack.enabled() and _db.get_feed(auditrack.PRODUCT)[0] is not None:
            items = [i for i in items if i["kind"] != "invoice_file"]
    except Exception:
        pass
    try:
        import underbilling
        rows = (underbilling.get_underbilling() or {}).get("rows") or []
        items += tasks_for_underbilling([r for r in rows if _matches(r.get("coordinator"), emails)])
    except Exception:
        pass
    items += _gap_items(user, emails)
    try:
        from . import mw_confirm
        items += mw_confirm.items(mine)
    except Exception:
        pass
    return items


def _gap_items(user, emails) -> list:
    """Missing-date items. Only a FULL read of the file book may change them:
    a half-read book would accuse people of gaps that are not there, so while
    it is still being read the previous gap items are carried over unchanged."""
    gaps, complete = _date_gaps()
    if gaps is not None and complete:
        out = tasks_for_date_gaps(gaps, emails)
        try:
            if (user.get("email") or "").strip().lower() in key_users():
                out += tasks_for_unassigned(gaps)
        except Exception:
            pass
        return out
    try:
        from . import db
        import json
        prev = []
        for r in db.list_items(user["id"], "moveware"):
            if r["kind"] not in ("enter_dates", "assign_coord"):
                continue
            d = dict(r)
            m = d.get("meta")
            d["meta"] = m if isinstance(m, dict) else json.loads(m or "{}")
            prev.append({k: d.get(k) for k in ("kind", "external_id", "from_name", "from_addr",
                                               "subject", "snippet", "url", "received_at", "meta")})
        return prev
    except Exception:
        return []
