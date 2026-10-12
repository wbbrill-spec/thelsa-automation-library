"""
payments.py — which invoices have been collected, from Finance's payments report.

The report is CONTPAQ i's "Análisis de IVA de pagos de clientes
(cronológicamente)", one workbook per company, one sheet per currency:

    CONTPAQ i            <company>
    Moneda: <currency>   ANÁLISIS DE IVA DE PAGOS DE CLIENTES (CRONOLÓGICAMENTE)
                         Del: 01/ENE/2026 al: 30/SEP/2026
    Impuesto a considerar: I.V.A.
        PAGO                                             FACTURA
    Fecha  Serie  Folio  Código Cliente  Cliente  Concepto  Importe pagado  .  Serie  Folio  Total
    …one row per payment applied to an invoice…
                                                  <sum>                               <sum>

Measured on the report Lupita sent on 12 Oct 2026 (Thelsa Relocation Solutions,
1 Jan to 30 Sep 2026). Columns are found by the words of the header row, so a
sheet with a column more or less is still read.

The report names customers and bank accounts. None of that is kept: what leaves
this module is the date, the invoice series and folio, the amount and the
currency of each payment. The company named at the top is Thelsa's own.

The amounts paid include IVA and the invoice totals printed in the report are
not reliable (the same folio shows different totals on different rows), so
whether an invoice is collected in full is decided against Finance's own
figure for that invoice: see `collected` and report.auto_payments.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import math
import re
import unicodedata
import warnings
from dataclasses import asdict, dataclass

import openpyxl

MONTHS_ES = {"ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6, "JUL": 7, "AGO": 8, "SEP": 9, "OCT": 10,
             "NOV": 11, "DIC": 12}
CURRENCIES = {"DOLAR AMERICANO": "USD", "PESO MEXICANO": "MXN"}
# Thelsa's own companies, as CONTPAQ prints them at the top of the report.
COMPANIES = {"THELSA RELOCATION SOLUTIONS": "TRS", "THELSA MOBILITY SOLUTIONS": "TMS",
             "THELSA MOBILITY SOLUTIONS LLC": "TMS LLC", "THELSA INTERNATIONAL MOVING": "TIM"}
_SERIES = re.compile(r"^[A-Z][A-Z\-]{0,11}$")


class PaymentReportError(ValueError):
    """The workbook is not the payments report, or a cell cannot be read.
    The messages never carry text from the workbook."""

    def __init__(self, message: str, es: str | None = None):
        super().__init__(message)
        self.es = es or message


def _bad(en: str, es: str):
    raise PaymentReportError(en, es)


def _plain(v) -> str:
    t = unicodedata.normalize("NFKD", str(v or ""))
    return "".join(c for c in t if not unicodedata.combining(c)).upper().strip()


@dataclass(frozen=True)
class Payment:
    date: str            # ISO date the payment was applied, '' if not readable
    currency: str        # USD | MXN: the currency of the sheet
    series: str          # of the invoice paid
    folio: str
    paid: float          # amount applied to the invoice, IVA included
    sheet: int           # 1-based
    row: int


def _date(v):
    if isinstance(v, dt.datetime):
        return v.date().isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 20000 < v < 80000:
        return (dt.date(1899, 12, 30) + dt.timedelta(days=int(v))).isoformat()     # an Excel day number
    return ""


def _period(text: str) -> tuple:
    out = []
    for m in re.finditer(r"(\d{1,2})/([A-Z]{3})/(\d{4})", _plain(text)):
        try:
            out.append(dt.date(int(m.group(3)), MONTHS_ES[m.group(2)], int(m.group(1))).isoformat())
        except (KeyError, ValueError):
            out.append("")
    return (out + ["", ""])[:2]


def load(src) -> tuple:
    """(payments, info). info = {"company": 'TRS' … or '' when not known, "key":
    what tells this company's report from another's, "from", "to": the report's
    own period as ISO dates, "currencies": […], "paid": {currency: sum}}."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            wb = openpyxl.load_workbook(src, data_only=True)
    except Exception:
        _bad("the file could not be opened as an Excel workbook", "el archivo no se pudo abrir como libro de Excel")
    out, info = [], {"company": "", "key": "", "from": "", "to": "", "currencies": [], "paid": {}}
    for si, ws in enumerate(wb.worksheets, start=1):
        rows = [tuple(r) for r in ws.iter_rows(values_only=True)]
        head = next((i for i, r in enumerate(rows)
                     if [_plain(c) for c in r if c is not None][:3] == ["FECHA", "SERIE", "FOLIO"]), None)
        if head is None:
            continue                                         # not a payments sheet
        top = [str(c) for r in rows[:head] for c in r if isinstance(c, str)]
        if not any("PAGOS DE CLIENTES" in _plain(c) for c in top):
            continue
        ccy = next((CURRENCIES[k] for c in top for k in CURRENCIES if k in _plain(c) and "MONEDA" in _plain(c)), "")
        if not ccy:
            _bad(f"sheet {si}: the currency of the report is not US dollars or Mexican pesos",
                 f"hoja {si}: la moneda del reporte no es dólar americano ni peso mexicano")
        company = next((COMPANIES[_plain(c)] for c in top if _plain(c) in COMPANIES), "")
        # The company is printed at the end of the first line. One this module does not know is kept as a short
        # fingerprint of its name, not as text: enough to tell two companies' reports apart.
        first = [c for c in rows[0] if isinstance(c, str) and c.strip()] if rows else []
        raw = _plain(first[-1]) if len(first) > 1 else ""
        key = company or ("co-" + hashlib.sha256(raw.encode()).hexdigest()[:8] if raw else "?")
        if info.get("key") and key != info["key"]:
            _bad("the sheets of the workbook are of different companies", "las hojas del libro son de empresas distintas")
        info["company"], info["key"] = company, key
        frm, to = next((_period(c) for c in top if _plain(c).startswith("DEL:")), ("", ""))
        info["from"], info["to"] = info["from"] or frm, max(info["to"], to)
        names = [_plain(c) for c in rows[head]]
        try:
            c_date, c_paid = names.index("FECHA"), names.index("IMPORTE PAGADO")
            c_series = names.index("SERIE", c_paid)            # the invoice's series and folio come after the amount
            c_folio = names.index("FOLIO", c_paid)
        except ValueError:
            _bad(f"sheet {si}: the columns of the payments report were not found",
                 f"hoja {si}: no se encontraron las columnas del reporte de pagos")
        total_printed, total = None, 0.0
        for i, r in enumerate(rows[head + 1:], start=head + 2):
            cell = lambda j: r[j] if j < len(r) else None
            amount, series, folio = cell(c_paid), cell(c_series), cell(c_folio)
            if amount is None and series is None and folio is None:
                continue
            if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount):
                _bad(f"sheet {si}, row {i}: the amount paid is not a number",
                     f"hoja {si}, fila {i}: el importe pagado no es un número")
            if series is None and folio is None and cell(c_date) is None:
                total_printed = float(amount)               # the sheet's own sum
                continue
            s_ = _plain(series)
            f_ = str(int(folio)) if isinstance(folio, (int, float)) and not isinstance(folio, bool) \
                and float(folio) == int(folio) and folio > 0 else (str(folio).strip() if isinstance(folio, str) else "")
            if not _SERIES.match(s_) or not f_.isdigit():
                _bad(f"sheet {si}, row {i}: the invoice series or folio cannot be read",
                     f"hoja {si}, fila {i}: no se pudo leer la serie o el folio de la factura")
            out.append(Payment(date=_date(cell(c_date)), currency=ccy, series=s_, folio=f_.lstrip("0") or "0",
                               paid=float(amount), sheet=si, row=i))
            total += float(amount)
        if total_printed is not None and abs(total_printed - total) > 0.05:
            _bad(f"sheet {si}: the payments add up to {total:,.2f} but the sheet's own total is {total_printed:,.2f}",
                 f"hoja {si}: los pagos suman {total:,.2f} pero el total de la hoja es {total_printed:,.2f}")
        info["currencies"].append(ccy)
        info["paid"][ccy] = round(info["paid"].get(ccy, 0.0) + total, 2)
    if not info["currencies"]:
        _bad("this does not look like CONTPAQ's report of customer payments (Análisis de IVA de pagos de clientes)",
             "no parece el reporte de pagos de clientes de CONTPAQ (Análisis de IVA de pagos de clientes)")
    return out, info


def to_records(payments, info) -> dict:
    return {"payments": [asdict(p) for p in payments], "info": dict(info)}


def from_records(doc) -> tuple:
    try:
        ps = [Payment(**r) for r in doc["payments"]]
        info = {"company": str(doc["info"].get("company", "")),
                "key": str(doc["info"].get("key", "") or doc["info"].get("company", "") or "?"),
                "from": str(doc["info"].get("from", "")),
                "to": str(doc["info"].get("to", "")), "currencies": list(doc["info"].get("currencies", [])),
                "paid": dict(doc["info"].get("paid", {}))}
        ok = all((p.date == "" or dt.date.fromisoformat(p.date)) and p.currency in ("USD", "MXN")
                 and _SERIES.match(p.series) and p.folio.isdigit() and isinstance(p.sheet, int) and isinstance(p.row, int)
                 and isinstance(p.paid, (int, float)) and not isinstance(p.paid, bool) and math.isfinite(p.paid)
                 for p in ps)
        ok = ok and info["company"] in set(COMPANIES.values()) | {""}
        ok = ok and bool(re.fullmatch(r"\?|co-[0-9a-f]{8}|[A-Z ]{2,8}", info["key"]))
        ok = ok and all(v == "" or dt.date.fromisoformat(v) for v in (info["from"], info["to"]))
        ok = ok and set(info["currencies"]) <= {"USD", "MXN"}
    except Exception:
        ok = False
    if not ok:
        _bad("the stored payments report could not be read back", "no se pudo leer el reporte de pagos guardado")
    return ps, info


def collected(payments) -> dict:
    """{(series, folio): {"MXN": pesos paid, "USD": dollars paid, "last": ISO
    date of the latest payment, "n": payments}} — everything applied to each invoice."""
    out = {}
    for p in payments or []:
        d = out.setdefault((p.series, p.folio), {"MXN": 0.0, "USD": 0.0, "last": "", "n": 0})
        d[p.currency] += p.paid
        d["last"] = max(d["last"], p.date)
        d["n"] += 1
    return out
