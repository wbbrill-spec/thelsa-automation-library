"""
finance.py — reader for Finance's gross-margin report.

The report is the workbook Lupita keeps with Finance ("Margen x Expediente
…xlsx", first received 7 Oct 2026). Its `Base` sheet is one line per accounting
document — a sales invoice or credit note, a supplier cost, or a TRS-to-TMS
intercompany charge — with the job number and Finance's own type for the job.

What this module knows about that sheet, all measured on the September 2026
report rather than assumed:

  * The type is one of AGENTE, CORP-PART, DIPLOM. Corporate and Private are a
    single type. Only CORP-PART is in scope for commission.
  * The type belongs to the job, lot letter included. 110771 and 110771A are
    AGENTE while 110771B is CORP-PART. Never roll a lot up to its file.
  * "Neto Dólar Americano" is the US-dollar EQUIVALENT of every line, including
    peso invoices. "Moneda" is the invoice's real currency and is sometimes
    blank; "Venta Neta" is always pesos at Finance's rate for that day.
  * The embassy cannot be recognised by who was billed (diplomatic jobs are
    billed to individuals too). The bill-to is read only to raise a flag.
  * The last line of the sheet is a grand total, not a document.

Two promises this module keeps:

  * No customer name leaves it. The bill-to, the free-text comment, anything
    typed where a job number belongs and any type that is not one of the three
    are read and dropped; only job numbers, document references, dates, codes
    and amounts are kept.
  * No money disappears quietly. An amount that cannot be read stops the load.
    Cost that ends up not counted (nothing billed, a fully cancelled job) and
    every guess about a currency is listed by `problems`.

For the commission, a sale invoiced in dollars counts at the peso amount Finance
booked, the rate of the day of the invoice (Settings.sales_fx = "spot", Bill's
decision of 8 Oct 2026), so sales and costs are on the same footing. The other
setting, "internal", converts dollars at the fixed rate in Settings (16.5).
"""
from __future__ import annotations

import datetime as dt
import math
import re
import statistics
import unicodedata
from collections import defaultdict
from dataclasses import dataclass

from . import engine as E

IN_SCOPE = "CORP-PART"
TYPES = ("AGENTE", "CORP-PART", "DIPLOM")
EPS = 0.005                      # half a cent: below this an amount is nothing
NET_ZERO = 1.0                   # a job whose billing nets to under one peso is cancelled; the cents are rounding
RATE_BAND = (10.0, 30.0)         # pesos per dollar outside this is a typing slip, not a rate
_JOB = re.compile(r"^\d{6}[A-Z]{0,2}$")
_JOBISH = re.compile(r"^\d{5,7}[A-Z]{0,2}$")          # a mistyped number, still not a name
# Document series Finance uses (measured on the September 2026 report). Anything
# else typed in that column is not kept: it could be a name.
_SERIES_WORDS = "CORP-PART|AGENTE|DIPLOM|AGTS|CORP|PART|TMS"
_SERIES = re.compile(rf"^(NC)?({_SERIES_WORDS})$")
_REF = re.compile(rf"^((?:NC)?(?:{_SERIES_WORDS}))[ -]?0*(\d{{1,9}})$")   # "NCCORP 15", "TMS1128", "corp-125"
_AMOUNT = re.compile(r"^(\d{1,3}(,\d{3})+|\d+)(\.\d+)?$")
_EMBASSY = re.compile(r"EMBAJADA|EMBASSY|CONSULAD|CONSULATE", re.I)
_ENTITY = {"LLC": "TMS LLC", "SA": "TMS SA"}
_ENTITIES = {"TMS LLC", "TMS SA", "TRS", "TIM", ""}

COLUMNS = ("Empresa", "Tipo", "Expediente", "Expediente 2", "Fecha", "Serie", "Folio",
           "Razon Social", "Factura que Cancela", "Neto Dólar Americano", "Moneda",
           "Tipo de Cambio", "Venta Neta", "Costo Proveedores", "Servs TRS a TMS", "COMENTARIOS")


class FinanceReportError(ValueError):
    """The workbook cannot be read safely. Nothing is calculated from it."""


@dataclass(frozen=True)
class FinLine:
    row: int                    # spreadsheet row, for tracing a number back
    type: str
    job: str                    # as written by Finance, lot letter included
    file: str                   # the base file
    date: dt.date | None
    entity: str
    series: str
    folio: str
    currency: str               # USD | MXN | EUR | '' (not known)
    currency_source: str        # stated | same invoice | amounts | unknown label | not stated
    fx: float | None
    usd_equiv: float | None
    net_sales_mxn: float        # Finance's pesos (0 when the line is not a sale)
    supplier_cost_mxn: float
    interco_cost_mxn: float
    cancels: str                # document this credit note cancels, e.g. "CORP 125"
    has_comment: bool           # Finance wrote a note on the line (text not kept)
    embassy_billed: bool        # a flag only; the name itself is not kept
    cancels_unread: bool = False   # something is written as the cancelled document, but not a reference

    @property
    def is_sale(self) -> bool:
        return abs(self.net_sales_mxn) >= EPS

    @property
    def cost_mxn(self) -> float:
        return self.supplier_cost_mxn + self.interco_cost_mxn

    @property
    def kind(self) -> str:
        if self.is_sale:
            return "sale"
        if self.supplier_cost_mxn:
            return "supplier_cost"
        return "interco_cost" if self.interco_cost_mxn else "empty"

    @property
    def month(self) -> str:
        return self.date.strftime("%Y-%m") if self.date else ""

    @property
    def job_ok(self) -> bool:
        return bool(_JOB.match(self.job))


# ── reading cells ────────────────────────────────────────────────────────────
def _plain(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _num(v, row: int, col: str, money: bool = True):
    """A number cell. Blank is None. Text that is plainly an amount ("(17,500.00)",
    "$5,000.00", "2,500.00-") is read as one. Anything else stops the load: an
    invoice or a credit note that silently reads as nothing, or as a thousand
    times too little, is worse than no report. So "1.234,56", "1234,56", "nan"
    and, in a money column, "100.000" (a hundred, or a hundred thousand?) are
    all refused."""
    def stop(why):
        raise FinanceReportError(f"row {row}, column {col!r}: {why}") from None
    if v is None:
        return None
    if isinstance(v, bool):
        stop("the cell holds true/false, not an amount")
    if isinstance(v, (int, float)):
        if not math.isfinite(v):
            stop("the cell does not hold a finite number")
        return float(v)
    t = str(v).strip()
    if t in ("", "-"):
        return None
    neg = (t.startswith("(") and t.endswith(")")) or t.endswith("-") or t.startswith("-")
    core = t.strip("()-").replace("$", "").replace(" ", "")
    if not _AMOUNT.match(core):
        stop("the cell holds text, not an amount")
    if money and "," not in core and re.search(r"\.\d{3}$", core):
        stop("the amount is written with three decimals and could be read two ways")
    x = float(core.replace(",", ""))
    return -x if neg else x


def _folio(v) -> str:
    """A document number: digits, leading zeros dropped. Anything else is not kept."""
    t = _text(v)
    if not t:
        return ""
    return (t.lstrip("0") or "0") if t.isdigit() else "?"


def _currency(label: str) -> tuple:
    """('USD'|'MXN'|'EUR'|'', source)."""
    if not _plain(label).strip():
        return "", "not stated"
    code = E.currency_code(label)
    return (code, "stated") if code else ("", "unknown label")


def _two_decimals(x: float) -> bool:
    return abs(round(x, 2) - x) < 5e-7


def _from_amounts(usd_equiv, mxn) -> str:
    """An invoice is issued to the cent in its own currency, so the side that
    is a clean two-decimal amount is the invoice currency. A tie says nothing.

    Measured on the 324 sales lines of the September 2026 report whose currency
    Finance did state: the rule named the right currency on 288, gave no answer
    on 36 and was never wrong. "Neither side is to the cent" does NOT mean a
    third currency: five peso invoices and one dollar invoice look like that."""
    u = usd_equiv is not None and _two_decimals(usd_equiv)
    m = mxn is not None and _two_decimals(mxn)
    if u and not m:
        return "USD"
    if m and not u:
        return "MXN"
    return ""


def _doc(r) -> bool:
    """The line carries a readable document reference and a date."""
    return r["series"] not in ("", "?") and r["folio"] not in ("", "?") and r["date"] is not None


def load_base(path, sheet: str = "Base") -> list:
    """Read the `Base` sheet into FinLine records. Raises FinanceReportError if
    the columns are not the ones this reader knows or a money cell is unreadable."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    if sheet not in wb.sheetnames:
        raise FinanceReportError(f"no sheet named {sheet!r} in the workbook ({len(wb.sheetnames)} sheets)")
    ws = wb[sheet]
    header, raw = None, []
    run = [0.0, 0.0, 0.0]                              # sales, supplier cost, TRS cost read so far
    for i, row in enumerate(ws.iter_rows(values_only=True), start=1):
        cells = [_text(c) for c in row]
        if header is None:
            if "Expediente" in cells and "Venta Neta" in cells:
                missing = [c for c in COLUMNS if c not in cells]
                if missing:
                    raise FinanceReportError(f"columns missing from {sheet!r}: {missing}")
                header = {c: cells.index(c) for c in COLUMNS}
            elif i > 20:
                break
            continue
        g = lambda name: row[header[name]] if header[name] < len(row) else None  # noqa: E731
        typ = _text(g("Tipo")).upper()
        job = _text(g("Expediente")).upper().replace(" ", "")
        sales = _num(g("Venta Neta"), i, "Venta Neta") or 0.0
        sup = _num(g("Costo Proveedores"), i, "Costo Proveedores") or 0.0
        ic = _num(g("Servs TRS a TMS"), i, "Servs TRS a TMS") or 0.0
        usd = _num(g("Neto Dólar Americano"), i, "Neto Dólar Americano")
        if not typ and not job:
            # No type and no job: a blank line, or a total. A total must equal
            # the lines above it; that is also the check that nothing was lost
            # on the way. Money on any other such row has no job to go to, and
            # the report is not used until someone has looked at it.
            if max(abs(sales), abs(sup), abs(ic), abs(usd or 0.0)) < EPS:
                continue
            if all(abs(a - b) <= 1.0 for a, b in zip((sales, sup, ic), run)):
                continue
            raise FinanceReportError(f"row {i} has amounts but no type and no job number, "
                                     "and they are not the total of the lines above")
        run[0] += sales
        run[1] += sup
        run[2] += ic
        if not _JOBISH.match(job):
            # Something other than a job number was typed here (a name, once). It
            # is not kept; the line stays, so its money is still counted and seen.
            job = f"?ROW{i}"
        file2 = _text(g("Expediente 2")).upper().replace(" ", "")
        file_ = file2 if re.fullmatch(r"\d{6}", file2) else re.sub(r"[A-Z]+$", "", job)
        d = g("Fecha")
        date = d.date() if isinstance(d, dt.datetime) else (d if isinstance(d, dt.date) else None)
        ccy, source = _currency(_text(g("Moneda")))
        ent = _text(g("Empresa")).upper()
        ent = _ENTITY.get(ent, ent)
        cref = _plain(_text(g("Factura que Cancela"))).upper()
        ref = _REF.match(cref)
        series = _plain(_text(g("Serie"))).upper()
        raw.append(dict(
            row=i, type=typ if typ in TYPES else "?", job=job, file=file_, date=date,
            entity=ent if ent in _ENTITIES else "OTHER",
            series=series if _SERIES.match(series) else ("?" if series else ""),
            folio=_folio(g("Folio")),
            currency=ccy, currency_source=source,
            fx=_num(g("Tipo de Cambio"), i, "Tipo de Cambio", money=False),
            usd_equiv=usd, net_sales_mxn=sales, supplier_cost_mxn=sup, interco_cost_mxn=ic,
            cancels=f"{ref.group(1)} {ref.group(2)}" if ref else "",
            has_comment=bool(_text(g("COMENTARIOS"))),
            embassy_billed=bool(_EMBASSY.search(_text(g("Razon Social")))),
            cancels_unread=bool(cref) and not ref))
    wb.close()
    if header is None:
        raise FinanceReportError(f"no header row found in {sheet!r}")
    if not raw:
        raise FinanceReportError(f"{sheet!r} has a header but no lines")
    # A sale with no currency: take it from another line of the same invoice
    # (same series, number and date; one invoice can be split over two jobs, as
    # CORP 129 was over 110611 and 110612), else from the amounts themselves.
    # Whatever is decided is reported.
    stated = defaultdict(set)
    for r in raw:
        if abs(r["net_sales_mxn"]) >= EPS and r["currency"] and _doc(r):
            stated[(r["series"], r["folio"], r["date"])].add(r["currency"])
    for r in raw:
        if abs(r["net_sales_mxn"]) >= EPS and not r["currency"] and r["currency_source"] == "not stated":
            same = stated.get((r["series"], r["folio"], r["date"]), ()) if _doc(r) else ()
            if len(same) == 1:
                r["currency"], r["currency_source"] = next(iter(same)), "same invoice"
            else:
                guess = _from_amounts(r["usd_equiv"], r["net_sales_mxn"])
                if guess:
                    r["currency"], r["currency_source"] = guess, "amounts"
    return [FinLine(**r) for r in raw]


# ── keeping the lines between visits ─────────────────────────────────────────
def to_records(lines) -> list:
    """The lines as plain dictionaries (dates as text), for storage. Nothing
    here was ever a customer name, so nothing stored can be one."""
    out = []
    for ln in lines:
        d = {k: getattr(ln, k) for k in ln.__dataclass_fields__}
        d["date"] = ln.date.isoformat() if ln.date else None
        out.append(d)
    return out


def from_records(records) -> list:
    """Back from storage. Every field is checked again on the way in, so a
    stored document that was tampered with cannot bring text or a bad number
    back into the report."""
    out = []
    for i, d in enumerate(records):
        try:
            d = dict(d)
            d["date"] = dt.date.fromisoformat(d["date"]) if d.get("date") else None
            ln = FinLine(**d)
            ok = (isinstance(ln.row, int) and (ln.type in TYPES or ln.type == "?")
                  and (_JOBISH.match(ln.job) or re.fullmatch(r"\?ROW\d+", ln.job))
                  and (_JOBISH.match(ln.file) or re.fullmatch(r"\?ROW\d+|\d{5,7}", ln.file))
                  and ln.entity in _ENTITIES | {"OTHER"}
                  and (ln.series in ("", "?") or _SERIES.match(ln.series))
                  and (ln.folio in ("", "?") or ln.folio.isdigit())
                  and ln.currency in ("USD", "MXN", "EUR", "")
                  and ln.currency_source in ("stated", "same invoice", "amounts", "unknown label", "not stated")
                  and (ln.cancels == "" or _REF.match(ln.cancels))
                  and all(isinstance(getattr(ln, k), bool) for k in ("has_comment", "embassy_billed", "cancels_unread"))
                  and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                          for v in (ln.net_sales_mxn, ln.supplier_cost_mxn, ln.interco_cost_mxn))
                  and all(v is None or (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v))
                          for v in (ln.fx, ln.usd_equiv)))
        except (TypeError, ValueError, KeyError):
            ok = False
        if not ok:
            raise FinanceReportError(f"stored line {i + 1} is not a line this reader wrote")
        out.append(ln)
    return out


# ── amounts for the commission ───────────────────────────────────────────────
def _dollars_usable(line: FinLine) -> bool:
    """The dollar amount is there, is not zero and has the sign of the pesos."""
    u = line.usd_equiv
    return u is not None and abs(u) >= EPS and (u > 0) == (line.net_sales_mxn > 0)


def billed_for_commission(line: FinLine, s: E.Settings) -> float:
    """A sale in pesos as the commission sees it.

    sales_fx "spot": Finance's peso amount, whatever the currency.
    sales_fx "internal": US dollars at the internal rate, pesos untouched,
    other currencies through Finance's US-dollar equivalent; where the currency
    or the dollar amount is not known, or the dollar amount is zero or has the
    wrong sign, Finance's own peso figure is used and `problems` says so."""
    if not line.is_sale:
        return 0.0
    if s.sales_fx == "spot":
        return line.net_sales_mxn
    if line.currency in ("USD", "EUR") and _dollars_usable(line):
        return line.usd_equiv * s.usd_mxn
    return line.net_sales_mxn


def jobs(lines, s: E.Settings | None = None) -> dict:
    """One record per (job, type), lot letter kept. Totals for the whole report."""
    s = s or E.Settings()
    out = {}
    for ln in lines:
        j = out.setdefault((ln.job, ln.type), {
            "job": ln.job, "file": ln.file, "type": ln.type, "billed_fin_mxn": 0.0, "billed_mxn": 0.0,
            "supplier_cost_mxn": 0.0, "interco_cost_mxn": 0.0, "cost_mxn": 0.0,
            "first_invoice": None, "last_invoice": None, "last_cost": None,
            "invoices": 0, "credit_notes": 0, "embassy_billed": False, "undated_billed_mxn": 0.0,
            "own_dollars": 0.0, "own_pesos": 0.0,          # dated net, each invoice in its own currency
            "by_month": defaultdict(float), "by_month_fin": defaultdict(float)})
        if ln.is_sale:
            amt = billed_for_commission(ln, s)
            j["billed_fin_mxn"] += ln.net_sales_mxn
            j["billed_mxn"] += amt
            j["credit_notes" if ln.net_sales_mxn < 0 else "invoices"] += 1
            j["embassy_billed"] = j["embassy_billed"] or ln.embassy_billed
            if ln.date:
                if ln.currency in ("USD", "EUR") and _dollars_usable(ln):
                    j["own_dollars"] += ln.usd_equiv
                else:
                    j["own_pesos"] += ln.net_sales_mxn
                j["by_month"][ln.month] += amt
                j["by_month_fin"][ln.month] += ln.net_sales_mxn
                j["first_invoice"] = min(j["first_invoice"] or ln.date, ln.date)
                j["last_invoice"] = max(j["last_invoice"] or ln.date, ln.date)
            else:
                j["undated_billed_mxn"] += amt
        if abs(ln.cost_mxn) >= EPS or ln.supplier_cost_mxn or ln.interco_cost_mxn:   # whatever else is on the row
            j["supplier_cost_mxn"] += ln.supplier_cost_mxn
            j["interco_cost_mxn"] += ln.interco_cost_mxn
            j["cost_mxn"] += ln.cost_mxn
            if ln.date:
                j["last_cost"] = max(j["last_cost"] or ln.date, ln.date)
    return out


def _spread(j: dict) -> tuple:
    """How one job's cost is laid on its invoice months.

    Returns ({month: (billed, cost)}, cost_used, why_not_used).

    Cost goes only to months with a positive net billing, in proportion to it,
    so no month ever carries more cost than the job has and a credit-note month
    carries none. The job's margin part still adds up to (billed - cost) x rate,
    because that part is linear in billed minus cost.

    A job whose billing nets to nothing in the report (fully cancelled, give or
    take the cents that rounding leaves behind, or cost with nothing billed)
    gets no cost on any line: nothing billed pays nothing,
    as in the workbook. A job with credit notes only has nowhere to put cost
    either. In both cases the cost is reported, not hidden.
    """
    months = {m: b for m, b in sorted(j["by_month"].items()) if abs(b) >= EPS}
    net = sum(months.values())
    pos = sum(b for b in months.values() if b > 0)
    cost = j["cost_mxn"]
    # Cancelled in full: nothing left in the currency each invoice was issued
    # in. In pesos a dollar invoice and its credit note seldom cancel exactly,
    # because each is booked at its own day's rate; that exchange difference
    # stays on the lines, but it must not make a cancelled job carry its cost.
    cancelled = abs(j["own_dollars"]) < 0.01 and abs(j["own_pesos"]) < NET_ZERO
    if abs(net) < NET_ZERO or cancelled:
        billed_at_all = j["invoices"] or j["credit_notes"]
        return ({m: (b, 0.0) for m, b in months.items()}, 0.0,
                "billing nets to zero in the report" if billed_at_all else "nothing billed in the report")
    if pos <= 0:
        return {m: (b, 0.0) for m, b in months.items()}, 0.0, "credit notes only in the report"
    return {m: (b, cost * b / pos if b > 0 else 0.0) for m, b in months.items()}, cost, ""


def commission_lines(lines, s: E.Settings | None = None, type_: str = IN_SCOPE) -> dict:
    """Engine input: {month: [engine.Line, …]} for one Finance type.

    One line per job per invoice month, billed net of that month's credit
    notes, cost laid on as `_spread` describes. A billed job with no cost
    posted yet is marked so the engine holds its margin part; that applies to
    all its lines alike, so an invoice and its later credit note stay in step.
    """
    s = s or E.Settings()
    out = defaultdict(list)
    for (job, typ), j in sorted(jobs(lines, s).items()):
        if typ != type_:
            continue
        spread, _used, _why = _spread(j)
        net = sum(b for b, _ in spread.values())
        waiting = abs(j["cost_mxn"]) < EPS and net >= NET_ZERO and not _why   # billed, and no cost in the books yet
        for month, (billed, cost) in spread.items():
            out[month].append(E.Line(job=job, billed=billed, cost=cost, paid=None, cost_posted=not waiting,
                                     note="billed to the Embassy" if j["embassy_billed"] else ""))
    return dict(out)


# ── what a person should look at ─────────────────────────────────────────────
def problems(lines, s: E.Settings | None = None) -> list:
    """Everything in the report to look at before its numbers are relied on.
    Job numbers, document references and amounts only."""
    s = s or E.Settings()
    spot = s.sales_fx == "spot"          # the currency of a sale then changes nothing
    out = []

    def add(kind, job, detail, **more):
        out.append({"kind": kind, "job": job, "detail": detail, **more})

    for ln in lines:
        if not ln.job_ok:
            add("bad_job_number", ln.job,
                f"row {ln.row}: the job number is not six digits plus an optional lot letter")
        if ln.type not in TYPES:
            add("unknown_type", ln.job, f"row {ln.row}: type is not one of {', '.join(TYPES)}")
        if ln.date is None:
            add("no_date", ln.job, f"row {ln.row}: no date" + (", so the sale is in no month" if ln.is_sale else ""),
                **({"amount_mxn": round(ln.net_sales_mxn, 2)} if ln.is_sale else {}))
        if ln.is_sale and (ln.supplier_cost_mxn or ln.interco_cost_mxn):
            add("sale_and_cost_on_one_row", ln.job, f"row {ln.row}: both counted",
                amount_mxn=round(ln.cost_mxn, 2))
        if ln.is_sale and ln.currency_source != "stated" and not spot:
            how = {"same invoice": f"read as {ln.currency} from another line of the same invoice",
                   "amounts": f"read as {ln.currency} from the amounts",
                   "unknown label": "the currency written is not one this reader knows; Finance's peso amount used",
                   "not stated": "could not be worked out; Finance's peso amount used"}[ln.currency_source]
            add("currency_missing", ln.job, f"row {ln.row}: currency not stated, {how}",
                amount_mxn=round(ln.net_sales_mxn, 2))
        if ln.is_sale and ln.currency in ("USD", "EUR"):
            if not _dollars_usable(ln):
                what = ("no dollar amount" if ln.usd_equiv is None else
                        "a dollar amount of zero" if abs(ln.usd_equiv) < EPS else
                        "a dollar amount with the opposite sign to the pesos")
                if not spot:
                    add("no_dollar_amount", ln.job, f"row {ln.row}: {ln.currency} invoice with {what}; "
                        "Finance's peso amount used", amount_mxn=round(ln.net_sales_mxn, 2))
                elif ln.usd_equiv is not None:            # the pesos are used; one of the two is still wrong
                    add("dollar_amount_mismatch", ln.job, f"row {ln.row}: {ln.currency} invoice with {what}; "
                        "the commission uses the pesos", amount_mxn=round(ln.net_sales_mxn, 2))
            else:
                implied = ln.net_sales_mxn / ln.usd_equiv
                off_rate = bool(ln.fx) and abs(implied - ln.fx) / ln.fx > 0.02
                if off_rate or not RATE_BAND[0] <= implied <= RATE_BAND[1]:
                    add("dollar_amount_mismatch", ln.job, f"row {ln.row}: pesos divided by dollars gives "
                        f"{implied:,.4f}" + (f" against a rate of {ln.fx:,.4f} on the line" if ln.fx else "")
                        + "; one of the two amounts is wrong and the commission uses the "
                        + ("pesos" if spot else "dollars"),
                        amount_mxn=round(ln.net_sales_mxn, 2))
        if not ln.is_sale and abs(ln.cost_mxn) < EPS and not (ln.supplier_cost_mxn or ln.interco_cost_mxn):
            add("empty_line", ln.job, f"row {ln.row}: no peso amount on the line"
                + (f", but a dollar amount of {ln.usd_equiv:,.2f}" if ln.usd_equiv else "")
                + " (a formula that was never calculated reads like this too)")
        if ln.is_sale and ln.net_sales_mxn < 0 and ln.cancels_unread:
            add("credit_reference_unreadable", ln.job, f"row {ln.row}: the document this credit note cancels "
                "is not written as a series and a number, so it could not be compared",
                amount_mxn=round(ln.net_sales_mxn, 2))
        if ln.is_sale and ln.embassy_billed and ln.type == IN_SCOPE:
            add("embassy_billed_in_scope", ln.job, f"row {ln.row}: billed to the Embassy but typed {IN_SCOPE}",
                amount_mxn=round(ln.net_sales_mxn, 2))
    # a date far from the rest of the report (2062 for 2026)
    years = [ln.date.year for ln in lines if ln.date]
    if years:
        usual = statistics.mode(years)
        for ln in lines:
            if ln.date and abs(ln.date.year - usual) > 1:
                add("date_out_of_range", ln.job, f"row {ln.row}: dated {ln.date.isoformat()} in a report for {usual}",
                    **({"amount_mxn": round(ln.net_sales_mxn, 2)} if ln.is_sale else {}))
    # the same document on the same job twice, to the cent
    seen = defaultdict(list)
    for ln in lines:
        if ln.folio not in ("", "?") and (ln.is_sale or ln.cost_mxn):
            seen[(ln.job, ln.type, ln.date, ln.series, ln.folio, round(ln.usd_equiv or 0.0, 2),
                  round(ln.net_sales_mxn, 2), round(ln.supplier_cost_mxn, 2), round(ln.interco_cost_mxn, 2))].append(ln)
    for same in seen.values():
        if len(same) > 1:
            add("duplicate_line", same[0].job, "rows " + ", ".join(str(x.row) for x in same)
                + " are the same document for the same amount; both are counted",
                amount_mxn=round(same[0].net_sales_mxn or same[0].cost_mxn, 2))
    # exchange rates far from the rest of their month
    by_month = defaultdict(list)
    for ln in lines:
        if ln.is_sale and ln.currency == "USD" and ln.fx and ln.currency_source == "stated":
            by_month[ln.month].append(ln)
    for rows in by_month.values():
        med = statistics.median(r.fx for r in rows)
        for r in rows:
            if abs(r.fx - med) / med > 0.10:
                add("fx_outlier", r.job, f"row {r.row}: rate {r.fx} against {med:.4f} for the month"
                    + ("; Finance's peso amount, and so the billed amount here, is too low by the amount shown"
                       if spot and r.fx < med else ""),
                    understated_mxn=round((med - r.fx) * (r.usd_equiv or 0), 2))
    # A credit note can be for part of its invoice, never for more. Amounts are
    # compared in the invoice's own currency. A note that names the wrong
    # invoice but matches another invoice of the job is a slip in the reference,
    # not in the money, and is left alone.
    def own(x):
        return x.usd_equiv if (x.currency in ("USD", "EUR") and x.usd_equiv is not None) else x.net_sales_mxn

    invoices = defaultdict(list)
    for ln in lines:
        if ln.is_sale and ln.net_sales_mxn > 0:
            invoices[ln.job].append(ln)
    for ln in lines:
        if not (ln.is_sale and ln.net_sales_mxn < 0):
            continue
        digits = re.sub(r"\D", "", ln.job)
        if (ln.job_ok and ln.usd_equiv is not None and ln.usd_equiv == round(ln.usd_equiv)
                and abs(abs(ln.usd_equiv) - float(digits)) < 0.005):
            named = [t for t in invoices[ln.job] if ln.cancels and f"{t.series} {t.folio}" == ln.cancels]
            should = f"; {ln.cancels}, which it cancels, is for {sum(own(t) for t in named):,.2f}" if named else ""
            add("credit_note_amount", ln.job,
                f"row {ln.row}: the credit note's dollar amount is the job number itself{should}",
                amount_mxn=round(ln.net_sales_mxn, 2))
            continue
        if not ln.cancels:
            continue
        named = [t for t in invoices[ln.job] if f"{t.series} {t.folio}" == ln.cancels and t.currency == ln.currency]
        if not named:
            continue                                      # invoice from an earlier period: nothing to compare
        amount, limit = abs(own(ln)), sum(own(t) for t in named)
        if amount <= limit + max(1.0, 0.005 * limit):
            continue
        if any(abs(own(t) - amount) <= max(1.0, 0.005 * amount) for t in invoices[ln.job] if t.currency == ln.currency):
            continue
        add("credit_note_amount", ln.job, f"row {ln.row}: credit note for {amount:,.2f} {ln.currency or 'pesos'} "
            f"cancels {ln.cancels}, which is for {limit:,.2f}", amount_mxn=round(ln.net_sales_mxn, 2))
    # one job, more than one type
    types = defaultdict(set)
    for ln in lines:
        types[ln.job].add(ln.type)
    for job, ts in sorted(types.items()):
        if len(ts) > 1:
            add("type_conflict", job, "typed " + " and ".join(sorted(ts)))
    # in-scope jobs: billed with no cost, and cost that is not counted
    for j in jobs(lines, s).values():
        if j["type"] != IN_SCOPE:
            continue
        _spread_, used, why = _spread(j)
        net = sum(b for b, _ in _spread_.values())
        if net >= NET_ZERO and abs(j["cost_mxn"]) < EPS and not why:
            add("no_cost_posted", j["job"], "billed, no cost in the report yet", amount_mxn=round(net, 2))
        if j["cost_mxn"] <= -EPS:
            add("negative_cost", j["job"], "the job's cost in the report is negative (supplier credits "
                "larger than its costs); it is counted as it stands", amount_mxn=round(j["cost_mxn"], 2))
        if abs(j["cost_mxn"]) >= EPS and not used:
            add("cost_without_sales" if why.startswith("nothing") else "cost_not_counted", j["job"],
                f"cost in the report is not counted: {why}", amount_mxn=round(j["cost_mxn"], 2))
    return out
