"""
underbilling.py — detect approved-but-unbilled additional charges (revenue leakage).

The core of the Thelsa audit's real purpose. A move coordinator emails the client
or booking agent the "FINAL CHARGES" for a move — the MoveWare job number is in the
SUBJECT, the amounts are in the BODY — and the client approves. Those approved
charges must then be invoiced in MoveWare. UNDER-billing is when approved charges
were never invoiced (or invoiced for less). This module:

  1. parse_charges_email()  — pull job#, charge lines, total, currency from a
                              "FINAL CHARGES" message.
  2. detect_approval()      — did the client approve (English or Spanish)?
  3. reconcile()            — aggregate approved charges per job and compare to the
                              MoveWare invoiced total; flag the gap.

Parsing is deliberately conservative: amounts must carry a currency token (so
weights/volumes/phone numbers are never mistaken for money), and only the part of
a message ABOVE the quoted history is read (so prior charges in the thread aren't
double-counted).
"""
from __future__ import annotations

import os
import re
import threading
import time

# MoveWare TMS job ids run ~100001–111xxx: 6 digits starting 10 or 11. This
# excludes agent references like 6573212 (7 digits) or 811556 (starts 81).
_JOB_RE = re.compile(r"\b(1[01]\d{4})\b")

# A money amount that carries a currency token on either side. Requiring the token
# is what keeps weights ("30.25 cbm"), counts, and phone numbers out.
_AMT_RE = re.compile(
    r"(?:USD|US\$|MXN|EUR|\$|€)\s*([0-9][0-9.,]*)"      # USD 1,338.21 / $4,949.00
    r"|([0-9][0-9.,]*)\s*(?:USD|MXN|EUR)",              # 1.338,21 USD
    re.IGNORECASE,
)
_CCY_RE = re.compile(r"\b(USD|US\$|MXN|EUR)\b|\$|€", re.IGNORECASE)

FINAL_CHARGES_MARKERS = ("final charges", "final charge", "cargos finales", "cargo final")
# Where quoted history starts — stop reading the message body here.
_QUOTE_MARKERS = ("\nDe:", "\nFrom:", "-----Original", "________________",
                  "\nEnviado el:", "\nSent:", "\nOn ", "> ")
# Client approval, EN + ES. Kept phrase-level to avoid false hits on "not approved".
APPROVAL_MARKERS = (
    "approved", "is approved", "please proceed", "proceed with your billing",
    "go ahead", "you may proceed", "confirmed, please",
    "de acuerdo", "proceder", "por favor proceder", "autorizo", "autorizado",
    "aprobado", "quedo de acuerdo", "adelante con", "confirmo",
)
_NEGATION = ("not approved", "no aprobado", "cannot approve", "no autorizo",
             "before approving", "antes de aprobar", "pending approval", "not yet approved")


def _num(s: str):
    """Parse a money string that may be US ('1,042.75') or EU ('1.338,21') format."""
    s = s.strip().rstrip(".,")
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):        # comma is the decimal sep (EU)
            s = s.replace(".", "").replace(",", ".")
        else:                                   # dot is the decimal sep (US)
            s = s.replace(",", "")
    elif "," in s:
        # comma only: decimal if exactly two trailing digits, else a thousands sep
        s = s.replace(",", ".") if re.search(r",\d{2}$", s) else s.replace(",", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v > 0 else None


def _body_before_quote(body: str) -> str:
    """Return only the top of the message, before any quoted reply history."""
    cut = len(body)
    for m in _QUOTE_MARKERS:
        i = body.find(m)
        if 0 <= i < cut:
            cut = i
    return body[:cut]


def _currency(text: str) -> str | None:
    m = _CCY_RE.search(text or "")
    if not m:
        return None
    tok = m.group(0).upper()
    if tok in ("$", "US$"):
        return "USD"
    if tok == "€":
        return "EUR"
    return tok


def extract_job(subject: str, known_jobs=None):
    """First MoveWare-shaped job id in the subject; if `known_jobs` given, prefer a
    match in that set (disambiguates when several 6-digit numbers appear)."""
    ids = _JOB_RE.findall(subject or "")
    if not ids:
        return None
    if known_jobs:
        for j in ids:
            if j in known_jobs:
                return j
    return ids[0]


def is_final_charges(subject: str) -> bool:
    s = (subject or "").lower()
    return any(m in s for m in FINAL_CHARGES_MARKERS)


def detect_approval(text: str) -> bool:
    """True if the text reads as a client approval (and not a negation)."""
    t = (text or "").lower()
    if any(n in t for n in _NEGATION):
        # A negation nearby suppresses a bare 'approve' — conservative: only approve
        # if there's an approval phrase AND no negation phrase.
        return False
    return any(m in t for m in APPROVAL_MARKERS)


def parse_charges_email(subject: str, body: str, sender: str = "", date=None,
                        known_jobs=None) -> dict | None:
    """Parse one 'FINAL CHARGES' message into a finding, or None if it isn't one /
    has no job number / no amounts."""
    if not is_final_charges(subject):
        return None
    job = extract_job(subject, known_jobs)
    if not job:
        return None
    top = _body_before_quote(body or "")
    amounts = []
    for m in _AMT_RE.finditer(top):
        v = _num(m.group(1) or m.group(2) or "")
        if v is not None:
            amounts.append(round(v, 2))
    if not amounts:
        return None
    return {
        "job": job,
        "coordinator": (sender or "").lower(),
        "subject": subject,
        "charges": amounts,
        "charged_total": round(sum(amounts), 2),
        "currency": _currency(top) or "USD",
        "date": date,
    }


def reconcile(findings, invoiced_by_job, tol: float = 1.0):
    """Aggregate approved charge-findings per job and compare to the MoveWare
    invoiced total. Returns under-billing rows (approved_total − invoiced > tol),
    most-under-billed first.

    findings: list of parse_charges_email() dicts, each with an `approved` bool
              (set by the caller from the thread) — unapproved ones are ignored.
    invoiced_by_job: {job_id: {"inv_amt": float, "currency": str|None,
                               "client": str|None, "coordinator": str|None}}
    """
    by_job = {}
    for f in findings:
        if not f or not f.get("approved"):
            continue
        j = f["job"]
        e = by_job.setdefault(j, {"job": j, "approved_total": 0.0, "currency": f.get("currency"),
                                  "coordinator": f.get("coordinator"), "subjects": []})
        e["approved_total"] += f.get("charged_total", 0.0)
        e["subjects"].append(f.get("subject", ""))

    rows = []
    for j, e in by_job.items():
        inv = invoiced_by_job.get(j) or {}
        inv_amt = round(float(inv.get("inv_amt") or 0.0), 2)
        approved = round(e["approved_total"], 2)
        gap = round(approved - inv_amt, 2)
        inv_ccy = (inv.get("currency") or "").upper() or None
        ccy_match = (inv_ccy is None) or (inv_ccy == (e["currency"] or "").upper())
        if gap > tol:
            rows.append({
                "job": j,
                "coordinator": e.get("coordinator") or inv.get("coordinator"),
                "client": inv.get("client"),
                "approved_total": approved,
                "invoiced": inv_amt,
                "gap": gap,
                "currency": e["currency"],
                "invoiced_currency": inv_ccy,
                "currency_match": ccy_match,
                "subjects": e["subjects"],
            })
    # Currency-mismatched rows are flagged for manual review (not a confident gap).
    rows.sort(key=lambda r: (r["currency_match"] is True, r["gap"]), reverse=True)
    return rows


# ── Orchestration: turn coordinator emails into under-billing rows ───────────
def build_findings(messages, thread_fetcher=None, known_jobs=None):
    """From raw 'FINAL CHARGES' messages, build findings with an `approved` flag.
    A finding is approved if the charges message itself reads as approved, or any
    message in its thread does (via `thread_fetcher(mailbox, conversationId)`)."""
    findings = []
    for m in messages:
        f = parse_charges_email(m.get("subject", ""), m.get("body", ""),
                                sender=m.get("sender", ""), date=m.get("date"),
                                known_jobs=known_jobs)
        if not f:
            continue
        approved = detect_approval(m.get("body", ""))
        if not approved and thread_fetcher:
            try:
                for txt in thread_fetcher(m.get("mailbox", ""), m.get("conversationId")):
                    if detect_approval(txt):
                        approved = True
                        break
            except Exception:
                pass
        f["approved"] = approved
        findings.append(f)
    return findings


# Cached scan result the dashboard reads.
_SCAN = {"rows": [], "scanned_at": None, "n_findings": 0, "n_approved": 0,
         "have_creds": False, "error": None}
_SCAN_LOCK = threading.Lock()
_SCAN_THREAD = None
_SCAN_INTERVAL = int(os.environ.get("UNDERBILLING_SCAN_SECONDS", 6 * 3600))


def scan_live():
    """Read coordinator FINAL CHARGES mail, reconcile against the MoveWare invoiced
    totals already cached by mw_live, and store under-billing rows. No-op (records
    have_creds=False) until the Azure app secret is configured."""
    try:
        import ms_graph
        import mw_live
    except Exception as e:
        with _SCAN_LOCK:
            _SCAN["error"] = f"import: {e}"
        return _SCAN
    with _SCAN_LOCK:
        _SCAN["have_creds"] = ms_graph.have_ms_creds()
    if not ms_graph.have_ms_creds():
        return _SCAN
    try:
        audited = {str(m.get("job")): m for m in mw_live.audited_files() if m.get("job")}
        known = set(audited.keys())
        msgs = ms_graph.fetch_final_charges()
        findings = build_findings(msgs, ms_graph.fetch_thread_texts, known_jobs=known)
        invoiced = {j: {"inv_amt": m.get("inv_amt", 0.0), "currency": None,
                        "client": m.get("client"), "coordinator": m.get("coordinator")}
                    for j, m in audited.items()}
        rows = reconcile(findings, invoiced)
        with _SCAN_LOCK:
            _SCAN.update({"rows": rows, "scanned_at": time.time(),
                          "n_findings": len(findings),
                          "n_approved": sum(1 for f in findings if f.get("approved")),
                          "error": None})
    except Exception as e:
        with _SCAN_LOCK:
            _SCAN["error"] = str(e)
    return _SCAN


def _scan_loop():
    while True:
        scan_live()
        time.sleep(_SCAN_INTERVAL)


def ensure_scanner():
    """Start the background under-billing scan if the Azure app secret is set.
    Safe/no-op without creds, so it costs nothing until Cesar's secret lands."""
    global _SCAN_THREAD
    try:
        import ms_graph
        if not ms_graph.have_ms_creds():
            return
    except Exception:
        return
    with _SCAN_LOCK:
        if _SCAN_THREAD is not None and _SCAN_THREAD.is_alive():
            return
        _SCAN_THREAD = threading.Thread(target=_scan_loop, daemon=True, name="underbilling-scan")
        _SCAN_THREAD.start()


def get_underbilling():
    with _SCAN_LOCK:
        return dict(_SCAN)
