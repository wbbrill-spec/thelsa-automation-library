"""
quotes.py — the booked quote of each file, read from two Moveware reports.

    Removals Summary     one row per job won: move type, branch, customer type,
                         date won, value excluding and including IVA
    Move Costing Sheet   the cost options of each job: quoted cost, margin and
                         sale price, and the actual costs loaded so far

Both are print-style exports with no column headings, so cells are found by
their position and by the fixed words Moveware prints ("Branch:", "Removal
Plan", "Cost Option", "Option Total"). The positions were measured on the
reports Lupita sent on 9 Oct 2026. A file that does not have this shape is
refused with a message; nothing is guessed.

Both reports carry customer names, addresses and e-mail addresses. None of
that is kept, or put in a message: what leaves this module is job numbers,
codes, dates and amounts.

How the booked quote of a job is taken (Lupita, 9–12 Oct 2026):
  * only cost options with Cost Status W count; C is cancelled, L is lost;
  * a job can have two or more W options (air, extras): they are added up;
  * the lines printed below "Option Total" are extras told to the customer when
    selling, and are part of the quote;
  * the sale price is the value EXCLUDING IVA of the Removals Summary;
  * the currency is the branch: a branch ending in U is US dollars, in M pesos.
"""
from __future__ import annotations

import datetime as dt
import math
import re
import warnings
from dataclasses import asdict, dataclass

import openpyxl

from . import engine as E

EPS = 0.005
JOB = re.compile(r"^\d{5,7}[A-Z]{0,2}$")
CUSTOMER_TYPES = ("Company", "Private", "Diplomatic", "Agent", "Blank")
# Move types of agent business: never Corporate or Private (Lupita, 12 Oct 2026).
AGENT_MOVE_TYPES = ("IMA", "EXA")
# Jobs on a branch whose letter says the wrong currency, each checked against
# what Finance invoiced. Remove an entry once the branch is corrected in Moveware.
#   110766  on MEU (dollars); every amount is pesos: Finance invoiced it in
#           pesos for the same figures. Reported to Thelsa on 12 Oct 2026.
#   110517  on MEU; sale price 291,817.39 and Finance's invoices 291,817.74 pesos.
#   110633  on MEU; sold and invoiced in Canadian dollars (CAD 19,300.82; Lupita,
#           12 Oct 2026). There is no rate for that currency here, so its quote
#           is left out and the page says why.
BRANCH_CURRENCY = {"110766": "MXN", "110517": "MXN", "110633": "CAD"}


class QuoteReportError(ValueError):
    """The workbook is not the report it should be, or a cell cannot be read.
    The messages never carry text from the workbook."""

    def __init__(self, message: str, es: str | None = None):
        super().__init__(message)
        self.es = es or message


def _bad(en: str, es: str):
    raise QuoteReportError(en, es)


def _num(v, row: int, what: str, what_es: str):
    """A number, or None for an empty cell. Anything else stops the load."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        _bad(f"row {row}: the {what} is not a number", f"fila {row}: {what_es} no es un número")
    return float(v)


def _rows(src):
    try:
        with warnings.catch_warnings():                      # Moveware's exports carry no default style
            warnings.simplefilter("ignore")
            wb = openpyxl.load_workbook(src, data_only=True)
    except QuoteReportError:
        raise
    except Exception:
        _bad("the file could not be opened as an Excel workbook", "el archivo no se pudo abrir como libro de Excel")
    ws = wb.worksheets[0]
    rows = [tuple(r) for r in ws.iter_rows(values_only=True)]
    wb.close()
    return rows


def _cell(row, i):
    return row[i] if i < len(row) else None


# ── Removals Summary ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Removal:
    job: str
    move_type: str              # IMP, EXP, IMA, DIN …
    branch: str                 # MEU, MEM … the last letter is the currency
    customer_type: str          # Company | Private | Diplomatic | Agent | Blank | ?
    won: str                    # ISO date the job was won, '' if not readable
    status: str                 # W
    value_ex: float             # sale price excluding IVA, in the branch's currency
    value_inc: float            # including IVA
    row: int


_GROUPS = ("Branch", "Company", "Customer", "Customer Type", "Date")


def load_removals(src) -> tuple:
    """(jobs, control) from the Moveware Removals Summary.

    control = {"listed": jobs listed, "listed_ex": their value, "total_files",
    "total_ex": what the report's own branch totals say}."""
    rows = _rows(src)
    ctx, out, seen = {}, [], {}
    total_files, total_ex, branches = 0, 0.0, 0
    for i, r in enumerate(rows, start=1):
        a = _cell(r, 0)
        if not isinstance(a, str) or not a.strip():
            continue
        a = a.strip()
        if JOB.match(a):
            if "Branch" not in ctx or "Date" not in ctx:
                _bad(f"row {i}: a job is listed before its branch and date groups",
                     f"fila {i}: hay un expediente antes de sus grupos de sucursal y fecha")
            move = str(_cell(r, 1) or "").strip().upper()
            branch = str(_cell(r, 10) or "").strip().upper()
            status = str(_cell(r, 14) or "").strip().upper()
            if not re.fullmatch(r"[A-Z]{3}", move):
                _bad(f"row {i}: the move type is not a three-letter code",
                     f"fila {i}: el tipo de mudanza no es un código de tres letras")
            if not re.fullmatch(r"[A-Z]{2,4}", branch) or branch != ctx["Branch"]:
                _bad(f"row {i}: the branch of the job does not match its branch group",
                     f"fila {i}: la sucursal del expediente no coincide con su grupo de sucursal")
            if not re.fullmatch(r"[A-Z]", status):
                _bad(f"row {i}: the status is not a single letter", f"fila {i}: el estatus no es una sola letra")
            ex = _num(_cell(r, 17), i, "value excluding tax", "el valor sin impuestos") or 0.0
            inc = _num(_cell(r, 18), i, "value including tax", "el valor con impuestos") or 0.0
            if ex < 0 or inc < 0:
                _bad(f"row {i}: a negative value", f"fila {i}: un valor negativo")
            if a in seen:
                _bad(f"rows {seen[a]} and {i}: job {a} is listed twice",
                     f"filas {seen[a]} y {i}: el expediente {a} aparece dos veces")
            seen[a] = i
            ct = ctx.get("Customer Type", "Blank")
            out.append(Removal(job=a, move_type=move, branch=branch,
                               customer_type=ct if ct in CUSTOMER_TYPES else "?",
                               won=ctx["Date"], status=status, value_ex=ex, value_inc=inc, row=i))
        elif ":" in a:
            key, value = (x.strip() for x in a.split(":", 1))
            if key not in _GROUPS:
                continue                                    # "Grand Total: …" and anything else
            for lower in _GROUPS[_GROUPS.index(key) + 1:]:
                ctx.pop(lower, None)
            if key == "Branch":
                if not re.fullmatch(r"[A-Z]{2,4}", value.upper()):
                    _bad(f"row {i}: the branch is not a short code", f"fila {i}: la sucursal no es un código corto")
                ctx[key] = value.upper()
                branches += 1
                total_files += int(_num(_cell(r, 1), i, "number of jobs of the branch",
                                        "el número de expedientes de la sucursal") or 0)
                total_ex += _num(_cell(r, 6), i, "value of the branch", "el valor de la sucursal") or 0.0
            elif key == "Date":
                m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})", value)
                try:
                    y = int(m.group(3))
                    ctx[key] = dt.date(y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1))).isoformat()
                except (AttributeError, ValueError):
                    ctx[key] = ""                            # listed by `problems`
            elif key == "Customer Type":
                ctx[key] = value if value in CUSTOMER_TYPES else "?"
            else:
                ctx[key] = "x"                               # a name: only that the group exists is kept
    if not branches or not out:
        _bad("this does not look like the Moveware Removals Summary: no branch groups with jobs were found",
             "no parece el reporte Removals Summary de Moveware: no se encontraron grupos de sucursal con expedientes")
    control = {"listed": len(out), "listed_ex": round(sum(x.value_ex for x in out), 2),
               "total_files": total_files, "total_ex": round(total_ex, 2)}
    return out, control


# ── Move Costing Sheet ───────────────────────────────────────────────────────
@dataclass(frozen=True)
class CostOption:
    no: str
    status: str                 # W | C | L
    cost: float                 # quoted cost: "Option Total" plus the lines below it
    sell: float                 # quoted sale price, as loaded (may include IVA)
    lines: int                  # cost lines of the option, above and below the total
    loaded: int                 # of them, lines with an actual cost
    actual: float               # actual cost loaded so far
    last_actual: str            # ISO date of the latest actual cost, '' if none dated
    total_off: bool             # "Option Total" is not the sum of the lines above it


@dataclass(frozen=True)
class Costing:
    job: str
    move_type: str
    client_type: str
    options: tuple
    row: int


def load_costing(src) -> tuple:
    """(jobs, period) from the Moveware Move Costing Sheet. period = the report's
    own title line: {"won_from", "won_to"} as ISO dates, '' where not readable."""
    rows = _rows(src)
    title = str(_cell(rows[0], 0) or "") if rows else ""
    period = {"won_from": "", "won_to": ""}
    for key, label in (("won_from", "Won From"), ("won_to", "Won To")):
        m = re.search(label + r":\s*(\d{1,2})/(\d{1,2})/(\d{2})\b", title)
        if m:
            try:
                period[key] = dt.date(2000 + int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
            except ValueError:
                pass
    jobs, seen = [], {}
    cur = None           # [job, move, client type, options, row]
    opt = None           # the option being read
    phase = ""

    def close_option(at: int):
        nonlocal opt
        if opt is None:
            return
        if opt["totals"] != 1:
            _bad(f"row {opt['row']}: the cost option has {opt['totals']} \"Option Total\" lines instead of one",
                 f"fila {opt['row']}: la opción de costo tiene {opt['totals']} líneas \"Option Total\" en lugar de una")
        cur[3].append(CostOption(
            no=opt["no"], status=opt["status"], cost=round(opt["total_cost"] + opt["extra_cost"], 2),
            sell=round(opt["total_sell"] + opt["extra_sell"], 2), lines=opt["lines"], loaded=opt["loaded"],
            actual=round(opt["actual"], 2), last_actual=opt["last"],
            total_off=abs(opt["line_cost"] - opt["total_cost"]) > 0.05))
        opt = None

    def close_job():
        nonlocal cur
        if cur is None:
            return
        jobs.append(Costing(job=cur[0], move_type=cur[1], client_type=cur[2], options=tuple(cur[3]), row=cur[4]))
        cur = None

    for i, r in enumerate(rows[1:], start=2):
        a = _cell(r, 0)
        if a is None or (isinstance(a, str) and not a.strip()):
            continue
        a = a.strip() if isinstance(a, str) else a
        if a == "Removal Plan":
            close_option(i)
            close_job()
            job = str(_cell(r, 2) or "").strip().upper()
            if not JOB.match(job):
                _bad(f"row {i}: the removal plan has no job number", f"fila {i}: el plan de mudanza no tiene número de expediente")
            if job in seen:
                _bad(f"rows {seen[job]} and {i}: job {job} is listed twice",
                     f"filas {seen[job]} y {i}: el expediente {job} aparece dos veces")
            seen[job] = i
            move = str(_cell(r, 11) or "").strip().upper()
            ct = ""
            for c in r[12:]:
                if isinstance(c, str) and c.strip().startswith("Client Type"):
                    ct = c.split(":", 1)[1].strip() if ":" in c else ""
            cur = [job, move if re.fullmatch(r"[A-Z]{3}", move) else "?",
                   ct if ct in CUSTOMER_TYPES else ("Blank" if not ct else "?"), [], i]
        elif a == "Cost Option":
            if cur is None:
                _bad(f"row {i}: a cost option before any removal plan", f"fila {i}: una opción de costo antes de un plan de mudanza")
            close_option(i)
            status = str(_cell(r, 8) or "").strip().upper()
            if not re.fullmatch(r"[A-Z]", status):
                _bad(f"row {i}: the cost status is not a single letter", f"fila {i}: el estatus de costo no es una sola letra")
            no = str(_cell(r, 2) or "").strip()
            opt = dict(no=no if re.fullmatch(r"\d{1,3}", no) else "?", status=status, row=i, totals=0,
                       total_cost=0.0, total_sell=0.0, line_cost=0.0, extra_cost=0.0, extra_sell=0.0,
                       lines=0, loaded=0, actual=0.0, last="")
            phase = "lines"
        elif a in ("ESTIMATED COSTS", "Profit Margin"):
            continue
        elif a == "Option Total":
            if opt is None:
                _bad(f"row {i}: an option total outside a cost option", f"fila {i}: un total de opción fuera de una opción de costo")
            opt["totals"] += 1
            opt["total_cost"] = _num(_cell(r, 3), i, "quoted cost", "el costo cotizado") or 0.0
            opt["total_sell"] = _num(_cell(r, 6), i, "quoted sale price", "el precio de venta cotizado") or 0.0
            phase = "after"
        else:
            if opt is None:
                if cur is None:
                    continue                                 # text before the first job
                _bad(f"row {i}: a cost line outside a cost option", f"fila {i}: una línea de costo fuera de una opción de costo")
            cost = _num(_cell(r, 3), i, "quoted cost", "el costo cotizado")
            sell = _num(_cell(r, 6), i, "quoted sale price", "el precio de venta cotizado")
            act = _num(_cell(r, 8), i, "actual cost", "el costo real")
            opt["lines"] += 1
            if phase == "lines":
                opt["line_cost"] += cost or 0.0
            else:
                opt["extra_cost"] += cost or 0.0
                opt["extra_sell"] += sell or 0.0
            if act is not None:
                opt["loaded"] += 1
                opt["actual"] += act
                d = _cell(r, 9)
                if isinstance(d, (dt.datetime, dt.date)):
                    iso = (d.date() if isinstance(d, dt.datetime) else d).isoformat()
                    opt["last"] = max(opt["last"], iso)
    close_option(len(rows) + 1)
    close_job()
    if not jobs or not any(j.options for j in jobs):
        _bad("this does not look like the Moveware Move Costing Sheet: no removal plans with cost options were found",
             "no parece el reporte Move Costing Sheet de Moveware: no se encontraron planes de mudanza con opciones de costo")
    return jobs, period


# ── storing what was read (no names in it) ───────────────────────────────────
def removals_to_records(jobs, control) -> dict:
    return {"jobs": [asdict(x) for x in jobs], "control": dict(control)}


def removals_from_records(doc) -> tuple:
    try:
        jobs = [Removal(**r) for r in doc["jobs"]]
        control = {k: doc["control"][k] for k in ("listed", "listed_ex", "total_files", "total_ex")}
        ok = all(JOB.match(x.job) and re.fullmatch(r"[A-Z]{3}", x.move_type) and re.fullmatch(r"[A-Z]{2,4}", x.branch)
                 and x.customer_type in CUSTOMER_TYPES + ("?",) and re.fullmatch(r"[A-Z]", x.status)
                 and (x.won == "" or dt.date.fromisoformat(x.won)) and isinstance(x.row, int)
                 and E._finite(x.value_ex, x.value_inc) and x.value_ex >= 0 and x.value_inc >= 0 for x in jobs)
        ok = ok and E._finite(*control.values()) and len({x.job for x in jobs}) == len(jobs)
    except Exception:
        ok = False
    if not ok:
        _bad("the stored Removals Summary could not be read back", "no se pudo leer el Removals Summary guardado")
    return jobs, control


def costing_to_records(jobs, period) -> dict:
    return {"jobs": [asdict(x) for x in jobs], "period": dict(period)}


def costing_from_records(doc) -> tuple:
    try:
        jobs = [Costing(job=r["job"], move_type=r["move_type"], client_type=r["client_type"], row=r["row"],
                        options=tuple(CostOption(**o) for o in r["options"])) for r in doc["jobs"]]
        period = {k: str(doc["period"].get(k, "")) for k in ("won_from", "won_to")}
        ok = all(JOB.match(j.job) and (j.move_type == "?" or re.fullmatch(r"[A-Z]{3}", j.move_type))
                 and j.client_type in CUSTOMER_TYPES + ("?",) and isinstance(j.row, int)
                 and all(re.fullmatch(r"\d{1,3}|\?", o.no) and re.fullmatch(r"[A-Z]", o.status)
                         and E._finite(o.cost, o.sell, o.actual) and isinstance(o.lines, int)
                         and isinstance(o.loaded, int) and isinstance(o.total_off, bool)
                         and (o.last_actual == "" or dt.date.fromisoformat(o.last_actual)) for o in j.options)
                 for j in jobs)
        ok = ok and len({j.job for j in jobs}) == len(jobs)
        ok = ok and all(v == "" or dt.date.fromisoformat(v) for v in period.values())
    except Exception:
        ok = False
    if not ok:
        _bad("the stored Move Costing Sheet could not be read back", "no se pudo leer el Move Costing Sheet guardado")
    return jobs, period


# ── the booked quote of each job ─────────────────────────────────────────────
def currency_of(job: str, branch: str, pesos=()) -> str:
    """'USD', 'MXN', or '' when the branch does not say."""
    if job in BRANCH_CURRENCY:
        return BRANCH_CURRENCY[job]
    if job in pesos:
        return "MXN"
    return {"U": "USD", "M": "MXN"}.get(branch[-1:] if branch else "", "")


def looks_like_pesos(removals, billed_pesos: dict) -> set:
    """Jobs on a dollar branch whose sale price is, to within 1 %, what Finance
    invoiced for them in pesos. A dollar price cannot equal the peso invoices,
    so such a job was loaded in pesos on the wrong branch. `billed_pesos` =
    {job: Finance's invoiced pesos}. Jobs already in BRANCH_CURRENCY are left out."""
    out = set()
    for x in removals or []:
        b = billed_pesos.get(x.job)
        if x.job in BRANCH_CURRENCY or x.branch[-1:] != "U" or not b or b <= EPS:
            continue
        if any(v > EPS and abs(v / b - 1) <= 0.01 for v in (x.value_ex, x.value_inc)):
            out.add(x.job)
    return out


def book(removals=None, costings=None, s: E.Settings | None = None, pesos=()) -> dict:
    """{job: quote} in pesos, for every job either report knows.

    quote = {"status": 'W'…, "sale": sale price excluding IVA or None,
             "cost": quoted cost or None, "currency", "won" (ISO or ''),
             "customer_type", "move_type", "with_tax": loaded with IVA in
             Moveware, "sale_from": 'removals' | 'costing' | '', "w_options"}.
    US dollars are taken at the internal rate, like invoiced dollar sales.
    `pesos` = jobs on a dollar branch to read as pesos (see looks_like_pesos)."""
    s = s or E.Settings()
    rem = {x.job: x for x in (removals or [])}
    cst = {x.job: x for x in (costings or [])}
    out = {}
    for job in sorted(set(rem) | set(cst)):
        r, c = rem.get(job), cst.get(job)
        ccy = currency_of(job, r.branch if r else "", pesos)
        rate = {"USD": s.usd_mxn, "MXN": 1.0}.get(ccy)
        w = [o for o in c.options if o.status == "W"] if c else []
        sale, sale_from = None, ""
        if r and r.value_ex > EPS:
            sale, sale_from = r.value_ex, "removals"
        elif w and sum(o.sell for o in w) > EPS:
            sale, sale_from = sum(o.sell for o in w), "costing"
        cost = sum(o.cost for o in w) if w else None
        out[job] = {
            "status": (r.status if r else ("W" if w else "")),
            "sale": None if (sale is None or rate is None) else sale * rate,
            "cost": None if (cost is None or rate is None) else cost * rate,
            "currency": ccy, "won": r.won if r else "",
            "customer_type": r.customer_type if r else (c.client_type if c else "?"),
            "move_type": r.move_type if r else (c.move_type if c else "?"),
            "with_tax": bool(r and r.value_inc > r.value_ex * 1.005 + EPS) or sale_from == "costing",
            "sale_from": sale_from, "w_options": len(w),
            "branch_fixed": job in BRANCH_CURRENCY or job in pesos, "total_off": any(o.total_off for o in w)}
    return out


def in_scope_by_moveware(q: dict) -> bool:
    """Corporate or Private by Moveware's own marks: not an agent move type,
    and customer type Company or Private."""
    return q["move_type"] not in AGENT_MOVE_TYPES and q["customer_type"] in ("Company", "Private")


def won_by_month(quotes: dict, also=()) -> dict:
    """{month: sale prices of the Corporate and Private jobs won in it}. `also`
    names jobs to count although Moveware's customer type is blank."""
    out = {}
    for job, q in quotes.items():
        if q["sale"] is None or not q["won"]:
            continue
        if in_scope_by_moveware(q) or (job in also and q["move_type"] not in AGENT_MOVE_TYPES):
            out[q["won"][:7]] = out.get(q["won"][:7], 0.0) + q["sale"]
    return out


def problems(removals=None, control=None, costings=None, pesos=()) -> list:
    """Points in the two reports themselves. Each is {"kind", "job", "detail",
    "detail_es"}; no text from the workbooks."""
    out = []

    def add(kind, job, en, es, amount=None):
        out.append({"kind": kind, "job": job, "detail": en, "detail_es": es,
                    **({"amount_mxn": round(amount, 2)} if amount is not None else {})})
    if control and (control["total_files"] != control["listed"]
                    or abs(control["total_ex"] - control["listed_ex"]) > 1.0):
        add("quote_totals", "",
            f"Removals Summary: its own totals show {control['total_files']} jobs and {control['total_ex']:,.2f}; "
            f"{control['listed']} jobs and {control['listed_ex']:,.2f} are listed",
            f"Removals Summary: sus propios totales muestran {control['total_files']} expedientes y "
            f"{control['total_ex']:,.2f}; aparecen {control['listed']} expedientes y {control['listed_ex']:,.2f}")
    for x in removals or []:
        if not x.won:
            add("quote_no_date", x.job, f"row {x.row} of the Removals Summary: the date won could not be read",
                f"fila {x.row} del Removals Summary: no se pudo leer la fecha de ganado")
        if x.job in BRANCH_CURRENCY:
            c = BRANCH_CURRENCY[x.job]
            if c in ("USD", "MXN"):
                add("quote_branch_currency", x.job,
                    f"branch {x.branch} says another currency but the amounts are {c}; read as {c} until the "
                    "branch is corrected in Moveware",
                    f"la sucursal {x.branch} indica otra moneda pero los importes están en {c}; se leen como {c} "
                    "hasta que se corrija la sucursal en Moveware")
            else:
                add("quote_branch_currency", x.job,
                    f"the amounts are in {c}, which this report has no rate for; the quote of this file is left out",
                    f"los importes están en {c}, moneda para la que este reporte no tiene tipo de cambio; la "
                    "cotización de este expediente se deja fuera")
        elif x.job in pesos:
            add("quote_branch_currency", x.job,
                f"branch {x.branch} says US dollars, but the sale price equals what Finance invoiced in pesos; "
                "read as pesos",
                f"la sucursal {x.branch} indica dólares, pero el precio de venta es igual a lo que Finanzas facturó "
                "en pesos; se lee como pesos")
        elif not currency_of(x.job, x.branch):
            add("quote_branch_currency", x.job, f"branch {x.branch} does not end in U or M: currency not known",
                f"la sucursal {x.branch} no termina en U ni en M: no se conoce la moneda")
    for j in costings or []:
        for o in j.options:
            if o.status == "W" and o.total_off:
                add("quote_total_off", j.job,
                    f"row {j.row} of the Move Costing Sheet, option {o.no}: \"Option Total\" is not the sum of its lines",
                    f"fila {j.row} del Move Costing Sheet, opción {o.no}: \"Option Total\" no es la suma de sus líneas")
    return out
