"""
invoices.py — Finance's register of invoices issued ("FACTURADO ACUMULADO YTD").

One sheet, one row per invoice and file, with a heading row:

    Cía  Mes  Tipo  EXPEDIENTE  Ce.Co  Fecha  Serie  Folio  FACTURA  Razón Social
    Total Factura  Factura que cancela  Subtotal EUR  Subtotal USD  Subtotal MX
    FILE2  Moneda  TC EUR  TC MX  TC USD  Total MX  Total USD  VENDEDOR  ADM. DE VENTAS

Measured on the register Lupita sent on 12 Oct 2026 (347 rows, 5 Jan to 30 Sep
2026). It is the list Finance's margin report is built from: the two agree row
for row. What the register adds for the commission report:

  * the invoice's real series. The margin report often writes the type
    (CORP-PART, AGENTE) where the series goes, and a payment can only be
    matched to an invoice by its real series and folio;
  * the company that issued each invoice, which is the company whose payments
    report shows its collection;
  * the invoice total as issued, in the invoice's own currency, and the
    exchange rate Finance used.

The register names customers and staff. None of that is kept: what leaves this
module is codes, numbers and dates. A file number that is not a file number
(Finance sometimes writes something else in that cell) is left blank.
"""
from __future__ import annotations

import datetime as dt
import math
import re
import unicodedata
import warnings
from dataclasses import asdict, dataclass

import openpyxl

COMPANY = {"LLC": "TMS LLC", "SA": "TMS SA", "TRS": "TRS", "TIM": "TIM"}      # as Finance's margin report names them
CURRENCY = {"USD": "USD", "MXP": "MXN", "MXN": "MXN", "EUR": "EUR"}
RATE_BAND = (10.0, 30.0)
_SERIES = re.compile(r"^[A-Z][A-Z0-9\-]{0,11}$")
_KIND = re.compile(r"^[A-Z][A-Z0-9\-]{1,9}$")
_JOB = re.compile(r"^\d{5,7}[A-Z]{0,2}$")
_REF = re.compile(r"^([A-Z][A-Z\-]{0,11}?)[ -]?0*(\d{1,9})$")
NEEDED = ("CIA", "EXPEDIENTE", "FECHA", "SERIE", "FOLIO", "TOTAL FACTURA", "SUBTOTAL USD", "SUBTOTAL MX", "MONEDA")


class InvoiceRegisterError(ValueError):
    """The workbook is not the invoice register, or a cell cannot be read.
    The messages never carry text from the workbook."""

    def __init__(self, message: str, es: str | None = None):
        super().__init__(message)
        self.es = es or message


def _bad(en: str, es: str):
    raise InvoiceRegisterError(en, es)


def _plain(v) -> str:
    t = unicodedata.normalize("NFKD", str(v if v is not None else ""))
    return " ".join("".join(c for c in t if not unicodedata.combining(c)).upper().split())


@dataclass(frozen=True)
class Invoice:
    company: str         # TMS LLC | TMS SA | TRS | TIM | '' (not one this module knows)
    kind: str            # Finance's type code: CORP, PART, AGTS, DIPLOM … ('' when not a code)
    job: str             # the file, '' when the cell does not hold a file number
    date: str            # ISO
    series: str
    folio: str
    currency: str        # USD | MXN | EUR | ''
    total: float         # "Total Factura", in the invoice's own currency
    subtotal: float      # without IVA, in the invoice's own currency
    pesos: float         # "Subtotal MX": without IVA, in pesos at Finance's rate
    rate: float | None   # "TC USD": pesos per dollar Finance used, None when not a rate
    cancels: str         # the document this one cancels, e.g. "TMS 1148"; '' when none or not readable
    row: int


def _number(v, row: int, what: str, what_es: str) -> float:
    if v is None or (isinstance(v, str) and not v.strip()):
        return 0.0
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        _bad(f"row {row}: {what} is not a number", f"fila {row}: {what_es} no es un número")
    return float(v)


def _date(v) -> str:
    if isinstance(v, dt.datetime):
        return v.date().isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 20000 < v < 80000:
        return (dt.date(1899, 12, 30) + dt.timedelta(days=int(v))).isoformat()
    return ""


def _job(v) -> str:
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and float(v) == int(v):
        v = str(int(v))
    t = _plain(v)
    return t if _JOB.match(t) else ""


def _folio(v) -> str:
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and float(v) == int(v) and v > 0:
        return str(int(v))
    t = str(v).strip() if isinstance(v, str) else ""
    return (t.lstrip("0") or "0") if t.isdigit() else ""


def _cancels(v) -> str:
    m = _REF.match(_plain(v).replace(" ", "")) if v is not None else None
    return f"{m.group(1)} {m.group(2)}" if m else ""


def load(src) -> tuple:
    """(invoices, info). info = {"rows", "invoices": distinct numbers, "from",
    "to": first and last invoice date, "companies": {company: rows}}."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            wb = openpyxl.load_workbook(src, data_only=True)
    except Exception:
        _bad("the file could not be opened as an Excel workbook", "el archivo no se pudo abrir como libro de Excel")
    for ws in wb.worksheets:
        rows = [tuple(r) for r in ws.iter_rows(values_only=True)]
        head = next((i for i, r in enumerate(rows[:30]) if set(NEEDED) <= {_plain(c) for c in r if c is not None}), None)
        if head is not None:
            break
    else:
        _bad("this does not look like Finance's register of invoices issued (FACTURADO ACUMULADO)",
             "no parece el registro de facturas emitidas de Finanzas (FACTURADO ACUMULADO)")
    names = [_plain(c) for c in rows[head]]
    at = {n: names.index(n) for n in NEEDED}
    opt = {n: names.index(n) if n in names else None for n in ("TIPO", "FACTURA QUE CANCELA", "SUBTOTAL EUR", "TC USD")}
    out = []
    for i, r in enumerate(rows[head + 1:], start=head + 2):
        cell = lambda j: r[j] if j is not None and j < len(r) else None
        if all(cell(at[n]) is None for n in ("SERIE", "FOLIO", "TOTAL FACTURA", "EXPEDIENTE")):
            continue                                              # a blank row, or a note under the list
        series, folio, date = _plain(cell(at["SERIE"])), _folio(cell(at["FOLIO"])), _date(cell(at["FECHA"]))
        if not _SERIES.match(series) or not folio:
            _bad(f"row {i}: the invoice series or folio cannot be read",
                 f"fila {i}: no se pudo leer la serie o el folio de la factura")
        if not date:
            _bad(f"row {i}: the invoice date cannot be read", f"fila {i}: no se pudo leer la fecha de la factura")
        ccy = CURRENCY.get(_plain(cell(at["MONEDA"])), "")
        sub = {"USD": at["SUBTOTAL USD"], "MXN": at["SUBTOTAL MX"], "EUR": opt["SUBTOTAL EUR"]}.get(ccy)
        rate = cell(opt["TC USD"])
        rate = float(rate) if isinstance(rate, (int, float)) and not isinstance(rate, bool) \
            and RATE_BAND[0] <= rate <= RATE_BAND[1] else None
        kind = _plain(cell(opt["TIPO"]))
        out.append(Invoice(
            company=COMPANY.get(_plain(cell(at["CIA"])), ""), kind=kind if _KIND.match(kind) else "",
            job=_job(cell(at["EXPEDIENTE"])), date=date, series=series, folio=folio, currency=ccy,
            total=_number(cell(at["TOTAL FACTURA"]), i, "the invoice total", "el total de la factura"),
            subtotal=_number(cell(sub), i, "the subtotal", "el subtotal"),
            pesos=_number(cell(at["SUBTOTAL MX"]), i, "the subtotal in pesos", "el subtotal en pesos"),
            rate=rate, cancels=_cancels(cell(opt["FACTURA QUE CANCELA"])), row=i))
    if not out:
        _bad("the register has no invoices", "el registro no tiene facturas")
    return out, _info(out)


def _info(invoices) -> dict:
    companies = {}
    for x in invoices:
        companies[x.company or "?"] = companies.get(x.company or "?", 0) + 1
    return {"rows": len(invoices), "invoices": len({(x.company, x.series, x.folio) for x in invoices}),
            "from": min(x.date for x in invoices), "to": max(x.date for x in invoices), "companies": companies}


def to_records(invoices, info=None) -> dict:
    return {"invoices": [asdict(x) for x in invoices]}


def from_records(doc) -> tuple:
    try:
        out = [Invoice(**r) for r in doc["invoices"]]
        num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        ok = bool(out) and all(
            x.company in set(COMPANY.values()) | {""} and (x.kind == "" or _KIND.match(x.kind))
            and (x.job == "" or _JOB.match(x.job)) and dt.date.fromisoformat(x.date) and _SERIES.match(x.series)
            and x.folio.isdigit() and x.currency in ("USD", "MXN", "EUR", "") and num(x.total) and num(x.subtotal)
            and num(x.pesos) and (x.rate is None or num(x.rate)) and (x.cancels == "" or _REF.match(x.cancels.replace(" ", "")))
            and isinstance(x.row, int) for x in out)
    except Exception:
        ok = False
    if not ok:
        _bad("the stored invoice register could not be read back", "no se pudo leer el registro de facturas guardado")
    return out, _info(out)


def by_number(invoices) -> dict:
    """{(series, folio): [invoice, …]} — each invoice once per company that
    issued that number, its rows added up (an invoice for several files has one
    row per file). invoice = {"company", "currency", "total", "subtotal",
    "pesos", "rate", "date", "jobs", "kinds"}."""
    out = {}
    for x in invoices or []:
        same = out.setdefault((x.series, x.folio), [])
        d = next((d for d in same if d["company"] == x.company), None)
        if d is None:
            d = {"company": x.company, "currency": x.currency, "total": 0.0, "subtotal": 0.0, "pesos": 0.0,
                 "rate": x.rate, "date": x.date, "jobs": [], "kinds": []}
            same.append(d)
        d["total"] += x.total
        d["subtotal"] += x.subtotal
        d["pesos"] += x.pesos
        d["date"] = min(d["date"], x.date)
        if x.job and x.job not in d["jobs"]:
            d["jobs"].append(x.job)
        if x.kind and x.kind not in d["kinds"]:
            d["kinds"].append(x.kind)
    return out


def real_series(invoices) -> dict:
    """{(folio, ISO date): {series: {"company", "jobs"}}} — to find the real
    series of an invoice the margin report shows under its type."""
    out = {}
    for x in invoices or []:
        d = out.setdefault((x.folio, x.date), {}).setdefault(x.series, {"company": x.company, "jobs": set()})
        if x.job:
            d["jobs"].add(x.job)
    return out
