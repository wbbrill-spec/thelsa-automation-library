"""
signoff.py — pay runs: how a commission gets from "calculated" to "paid".

A pay run is one batch of commission to pay. The chain (Lupita, 7 Oct):

    draft      worked out live from the report: every new-calculation line that
               is Paid (invoice collected), less whatever earlier runs already
               paid on that line
    submitted  Lupita has reviewed the run and sends it to Mario. Its lines and
               amounts are frozen from this moment
    approved   Mario approves it. From now on its amounts count as paid out, so
               the next draft only carries what is new
    paid       Rogelio (Finance) records that the people were paid
    returned   Mario sent it back (with a note); it counts for nothing
    withdrawn  Lupita took it back before approval; it counts for nothing
    cancelled  an approved run that was not paid, cancelled by an admin

Why a run and not a month: an invoice of June may be collected in October, and
a June line paid without its margin part (cost not posted yet) gets the margin
part when the cost arrives. A run pays, for each line (invoice month + file),
the line's commission today minus what approved runs already paid on it. So a
late collection, a late margin part, or a later credit note on a paid line all
land in the next run, once, and nothing is paid twice.

Each step is recorded with who and when. Amounts per person are rounded to the
cent (half up) for the payment instruction; the control line shows any cent
left over by rounding. No customer data: file numbers and amounts only.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from decimal import ROUND_HALF_UP, Decimal

from . import engine as E

COUNTED = ("approved", "paid")                   # runs whose amounts are paid out
OPEN = "submitted"
ADMINS = ("bbrill@thelsa.com", "bill.brill@inflectionpointnow.com")
ROLE_DEFAULTS = {"review": ("maria.gonzalez@thelsa.com",), "approve": ("mariotorres@thelsa.com",),
                 "finance": ("rogeliofranco@thelsa.com",)}
ROLE_ENV = {"review": "COMMISSION_REVIEWERS", "approve": "COMMISSION_APPROVERS", "finance": "COMMISSION_FINANCE"}
# which role may take which step
STEP_ROLE = {"submit": "review", "withdraw": "review", "approve": "approve", "return": "approve",
             "pay": "finance", "cancel": None}
STATE_ES = {"draft": "borrador", "submitted": "enviado a aprobación", "approved": "aprobado", "paid": "pagado",
            "returned": "devuelto", "withdrawn": "retirado", "cancelled": "cancelado"}
STEP_ES = {"submit": "revisado y enviado", "withdraw": "retirado", "approve": "aprobado", "return": "devuelto",
           "pay": "pagado", "cancel": "cancelado"}
CENT = Decimal("0.01")


class SignOffError(ValueError):
    def __init__(self, en: str, es: str):
        super().__init__(en)
        self.es = es


def _emails(raw: str) -> set:
    return {e.strip().lower() for e in (raw or "").replace(";", ",").split(",") if e.strip()}


def role_members(role: str) -> set:
    return _emails(os.environ.get(ROLE_ENV[role], "")) or set(ROLE_DEFAULTS[role])


def is_admin(email: str) -> bool:
    return (email or "").lower() in (_emails(os.environ.get("COMMISSION_ADMINS", "")) or set(ADMINS))


def roles_of(email: str) -> list:
    email = (email or "").lower()
    return [r for r in ROLE_ENV if email in role_members(r)] + (["admin"] if is_admin(email) else [])


def may(email: str, step: str) -> bool:
    """An admin may take any step (recorded as such); everyone else only their own."""
    role = STEP_ROLE[step]
    return is_admin(email) or (role is not None and (email or "").lower() in role_members(role))


def cents(v) -> Decimal:
    return Decimal(str(v)).quantize(CENT, rounding=ROUND_HALF_UP)


def paid_so_far(runs) -> dict:
    """(month, job) -> commission already paid out by approved and paid runs."""
    out = {}
    for r in runs or ():
        if r.get("state") in COUNTED:
            for ln in r.get("lines", ()):
                k = (ln["month"], ln["job"])
                out[k] = out.get(k, 0.0) + float(ln["due"])
    return out


def draft(report: dict, runs, s: E.Settings, new_from: str) -> dict:
    """What a run submitted now would pay. `report` = report.build(...)."""
    before = paid_so_far(runs)
    lines, warnings = [], []
    seen = set()
    for r in report.get("lines", ()):
        if r["month"] < new_from:
            continue
        k = (r["month"], r["job"])
        seen.add(k)
        prior = before.get(k, 0.0)
        if r.get("payment") != "Paid":
            if abs(prior) >= 0.005:
                warnings.append({"month": r["month"], "job": r["job"], "paid": prior,
                                 "en": "paid in an earlier run, no longer marked Paid; nothing is taken back",
                                 "es": "pagada en una corrida anterior y ya no marcada como pagada; no se descuenta"})
            continue
        due = float(r["total"]) - prior
        if abs(due) >= 0.005:
            lines.append({"month": r["month"], "job": r["job"], "total": float(r["total"]), "before": prior,
                          "due": due, "adjust": abs(prior) >= 0.005})
    for k, prior in sorted(before.items()):
        if k not in seen and abs(prior) >= 0.005:
            warnings.append({"month": k[0], "job": k[1], "paid": prior,
                             "en": "paid in an earlier run, no longer in the report; nothing is taken back",
                             "es": "pagada en una corrida anterior y ya no está en el reporte; no se descuenta"})
    lines.sort(key=lambda x: (x["month"], x["job"]))
    total = sum(x["due"] for x in lines)
    return {"lines": lines, "due": total, "people": split(total, s), "warnings": warnings,
            "months": sorted({x["month"] for x in lines}), "fingerprint": fingerprint(lines)}


def split(total: float, s: E.Settings) -> list:
    """Each person's amount of `total`, rounded to the cent; the last row is the control."""
    rows = [(n, "Sales", s.sales_share, p) for n, p in s.sales_people] + \
           [(n, "Adm, MC and Buyer", s.admin_share, p) for n, p in s.admin_people]
    return [{"name": n, "group": g, "share": gs * p, "amount": float(cents(total * gs * p))} for n, g, gs, p in rows]


def control(run_or_draft) -> float:
    """Sum paid to people minus the run's total, both to the cent. Rounding only."""
    return float(sum(cents(p["amount"]) for p in run_or_draft["people"]) - cents(run_or_draft["due"]))


def fingerprint(lines) -> str:
    body = json.dumps([[x["month"], x["job"], round(x["due"], 2)] for x in lines], separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _log(run, step, email, note=""):
    run["log"].append({"step": step, "by": email, "at": _now(), "note": note.strip()[:500],
                       "as_admin": not (STEP_ROLE[step] and email in role_members(STEP_ROLE[step])) and is_admin(email)})


def find(runs, run_id: int) -> dict:
    for r in runs:
        if r["id"] == run_id:
            return r
    raise SignOffError("That pay run does not exist.", "Esa corrida de pago no existe.")


def act(runs: list, step: str, email: str, s: E.Settings, current: dict | None = None, run_id: int | None = None,
        note: str = "", paid_on: str = "", expect: str = "") -> dict:
    """Take one step. Changes `runs` in place and returns the run. `current` is
    the live draft (needed to submit, and to say at approval what changed)."""
    email = (email or "").lower()
    if step not in STEP_ROLE:
        raise SignOffError("Unknown step.", "Paso desconocido.")
    if not may(email, step):
        raise SignOffError("You are not the person who takes this step.", "Usted no es quien realiza este paso.")
    if step == "submit":
        if any(r["state"] == OPEN for r in runs):
            raise SignOffError("A pay run is already waiting for approval.", "Ya hay una corrida esperando aprobación.")
        if current is None or not current["lines"]:
            raise SignOffError("There is nothing new to pay.", "No hay nada nuevo por pagar.")
        if expect and expect != current["fingerprint"]:
            raise SignOffError("The figures changed while you were looking. Reload the page and review again.",
                               "Las cifras cambiaron mientras revisaba. Recargue la página y revise de nuevo.")
        if current["due"] <= 0:
            raise SignOffError("The run adds up to zero or less; there is nothing to pay.",
                               "La corrida suma cero o menos; no hay nada que pagar.")
        run = {"id": max((r["id"] for r in runs), default=0) + 1, "state": OPEN, "created": _now(),
               "lines": current["lines"], "due": current["due"], "people": current["people"],
               "months": current["months"], "fingerprint": current["fingerprint"], "log": []}
        _log(run, "submit", email, note)
        runs.append(run)
        return run
    run = find(runs, int(run_id or 0))
    need = {"withdraw": (OPEN,), "approve": (OPEN,), "return": (OPEN,), "pay": ("approved",), "cancel": ("approved",)}
    if run["state"] not in need[step]:
        raise SignOffError(f"Run {run['id']} is {run['state']}; it cannot be {step}d now.",
                           f"La corrida {run['id']} está {STATE_ES[run['state']]}; no se puede hacer ese paso ahora.")
    if step == "return" and not note.strip():
        raise SignOffError("Say why the run is returned.", "Indique por qué se devuelve la corrida.")
    if step == "cancel" and not note.strip():
        raise SignOffError("Say why the run is cancelled.", "Indique por qué se cancela la corrida.")
    if step == "approve" and current is not None:
        mine = {(x["month"], x["job"]): x["due"] for x in run["lines"]}
        now = {(x["month"], x["job"]): x["due"] for x in current["lines"]}
        moved = sum(now.get(k, 0.0) - v for k, v in mine.items())
        extra = sum(v for k, v in now.items() if k not in mine)
        if abs(moved) >= 0.005 or abs(extra) >= 0.005:
            run["changed_at_approval"] = {"on_these_lines": moved, "new_lines": extra}
    if step == "pay":
        try:
            day = dt.date.fromisoformat(paid_on) if paid_on else dt.date.today()
        except ValueError:
            raise SignOffError("The payment date must be a date.", "La fecha de pago debe ser una fecha.") from None
        run["paid_on"] = day.isoformat()
    run["state"] = {"withdraw": "withdrawn", "approve": "approved", "return": "returned", "pay": "paid",
                    "cancel": "cancelled"}[step]
    _log(run, step, email, note)
    return run


def instruction_book(run: dict, out=None):
    """The payment instruction for Finance: what each person is paid, the
    approval trail, and the lines behind it. Bilingual headings."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    bold = Font(bold=True)
    head = PatternFill("solid", fgColor="6699FF")
    wb = Workbook()
    ws = wb.active
    ws.title = "Payment instruction"
    ws["A1"].value = f"Commission pay run {run['id']} / Corrida de comisiones {run['id']}"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"].value = (f"Invoice months covered / Meses de factura: {', '.join(run.get('months', []))}. "
                      f"State / Estado: {run['state']} / {STATE_ES[run['state']]}. Amounts in MXN.")
    r = 4
    for c, h in enumerate(["Person / Persona", "Group / Grupo", "Share / Parte", "Amount MXN / Importe MXN"], 1):
        cell = ws.cell(row=r, column=c, value=h)
        cell.font, cell.fill = bold, head
    for p in run["people"]:
        r += 1
        ws.cell(row=r, column=1, value=p["name"])
        ws.cell(row=r, column=2, value=p["group"])
        ws.cell(row=r, column=3, value=p["share"]).number_format = "0.00%"
        ws.cell(row=r, column=4, value=p["amount"]).number_format = "#,##0.00"
    r += 1
    ws.cell(row=r, column=1, value="Total to pay / Total a pagar").font = bold
    c = ws.cell(row=r, column=4, value=f"=SUM(D5:D{r - 1})")
    c.font, c.number_format = bold, "#,##0.00"
    r += 1
    ws.cell(row=r, column=1, value="Run total / Total de la corrida")
    ws.cell(row=r, column=4, value=float(cents(run["due"]))).number_format = "#,##0.00"
    r += 1
    ws.cell(row=r, column=1, value="Rounding difference / Diferencia por redondeo")
    ws.cell(row=r, column=4, value=f"=D{r - 2}-D{r - 1}").number_format = "#,##0.00"
    r += 2
    ws.cell(row=r, column=1, value="Approval trail / Historial de aprobación").font = bold
    for e in run["log"]:
        r += 1
        ws.cell(row=r, column=1, value=f"{e['step']} / {STEP_ES[e['step']]}")
        ws.cell(row=r, column=2, value=e["by"] + (" (admin)" if e.get("as_admin") else ""))
        ws.cell(row=r, column=3, value=e["at"])
        ws.cell(row=r, column=4, value=e.get("note", ""))
    if run.get("paid_on"):
        r += 1
        ws.cell(row=r, column=1, value=f"Paid on / Pagado el: {run['paid_on']}")
    for col, w in zip("ABCD", (40, 22, 26, 22)):
        ws.column_dimensions[col].width = w
    lw = wb.create_sheet("Lines")
    for c, h in enumerate(["Invoice month / Mes de factura", "File / Expediente", "Line commission today / Comisión de la línea",
                           "Paid in earlier runs / Pagado antes", "Paid in this run / Pagado en esta corrida"], 1):
        cell = lw.cell(row=1, column=c, value=h)
        cell.font, cell.fill = bold, head
        cell.alignment = Alignment(wrap_text=True)
    for i, x in enumerate(run["lines"], start=2):
        lw.cell(row=i, column=1, value=x["month"])
        lw.cell(row=i, column=2, value=x["job"])
        for col, key in ((3, "total"), (4, "before"), (5, "due")):
            lw.cell(row=i, column=col, value=round(x[key], 2)).number_format = "#,##0.00;(#,##0.00)"
    n = len(run["lines"]) + 2
    lw.cell(row=n, column=2, value="Total").font = bold
    c = lw.cell(row=n, column=5, value=f"=SUM(E2:E{n - 1})")
    c.font, c.number_format = bold, "#,##0.00;(#,##0.00)"
    for col, w in zip("ABCDE", (16, 14, 20, 18, 20)):
        lw.column_dimensions[col].width = w
    if out is not None:
        wb.save(out)
    return wb
