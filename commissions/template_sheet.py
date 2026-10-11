"""
template_sheet.py — a month of commission in the layout of Finance's own
workbook (COMMISSION CALCULATION 2026.xlsx, Rogelio's template).

One sheet, the same blocks and columns as the template:

    A–J   BOOKED QUOTE   month, file, status, sale price, costing, margin, %,
                         budget, budget achievement, sales reach
    K–O   REAL           billed, actual cost, provision, gross margin, %
    P     payment status
    Q–U   commission     invoicing part, achievement, margin part, discipline, total
    W–AA  distribution   per line (paid lines only) and by role under the table

Every calculated cell is a live formula, and they are the template's formulas,
so Finance can follow each number. Two things are done more safely than in the
template, without changing any result: a line with nothing billed shows a blank
margin % instead of #DIV/0!, and the split between Sales and Adm is a formula
on every line (zero unless the line is marked Paid) instead of being typed in
on the paid lines only.

The sheet needs three things the Finance margin report does not have: the
quoted sale price and costing of each file (BOOKED QUOTE), and whether each
invoice is collected (payment status). Cells with nothing to show are left
empty, never filled with a guess. A billed file with no cost in the books yet
shows "pending" in Actual Cost and has no gross margin part until the cost is
posted (the engine's rule; the template has no such case).
"""
from __future__ import annotations

from dataclasses import dataclass

from openpyxl.comments import Comment
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as L

from . import engine as E

PENDING = "pending"        # written in Actual Cost when a billed file has no cost yet
MONEY = '#,##0.00;(#,##0.00);"-"'
PCT = '0.0%;(0.0%);"-"'
_thin = Side(style="thin", color="BFBFBF")
_box = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)
_FILL = {"quote": "D9E1F2", "real": "FFF2CC", "comm": "99FF66", "dist": "6699FF", "input": "FFFF00"}
HEAD = ["Month", "File", "Status", "Sale prices", "Costing (quote)", "Margin (quote)", "% Margin (quote)",
        "Budget per month", "% of Budget achievement", "% of Sales reach", "Billed", "Actual Cost", "Provision",
        "Gross margin", "% gross margin", "Payment status", "% Sales/Billed", "% of Margin gross achievement",
        "Gross Margin", "Discipline and behavior", "Total Commission"]
DIST_HEAD = ["Role and Name", "Commission by role", "Sales", "Adm, MC and Buyer", "Total Commission"]


@dataclass(frozen=True)
class Row:
    """One line of the sheet. Leave out what is not known."""
    file: str
    month: str = ""                    # shown in column A ("August", "Credit Note", …)
    status: str = ""                   # "Ganado" in the template
    sale_price: float | None = None    # quoted sale price of a file booked in the month
    quote_cost: float | None = None
    billed: float | None = None
    cost: float | None = None
    provision: float | None = None
    payment: str = ""                  # "Paid", "Pending" or "" (not known)
    cost_pending: bool = False         # billed, and no cost in the books yet: the margin part waits


def _fill(hex_):
    return PatternFill("solid", fgColor=hex_)


def write(ws, rows, s: E.Settings, budget: float, bookings: float | None = None, notes=()) -> dict:
    """Fill `ws`. `budget` is the month's booking budget. Sales reach comes from
    the sale prices on the sheet, as in the template; when those are not on the
    sheet, give the month's `bookings` and that figure is used instead (in a
    yellow cell someone can overtype). Returns where things are, for checking."""
    s.check()
    rows = list(rows)
    first, last = 4, 3 + max(len(rows), 1)
    tot = last + 1
    tgt = tot + 2                       # MAIN TARGETS block, as two rows under the total
    bold, base = Font(name="Calibri", size=11, bold=True), Font(name="Calibri", size=11)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # ── titles and headers (rows 1–3) ───────────────────────────────────────
    for rng, text in (("Q1:U1", "GLOBAL COMMISSION"), ("A2:J2", "BOOKED QUOTE"), ("K2:O2", "REAL"),
                      ("W2:AA2", "COMMISSION DISTRIBUTION")):
        ws.merge_cells(rng)
        c = ws[rng.split(":")[0]]
        c.value, c.font, c.alignment = text, bold, center
    ws.merge_cells("Q2:U2")
    ws["Q2"].value, ws["Q2"].number_format, ws["Q2"].font, ws["Q2"].alignment = s.rate, "0%", bold, center
    for j, h in enumerate(HEAD, start=1):
        c = ws.cell(row=3, column=j, value=h)
        c.font, c.alignment, c.border = bold, center, _box
        c.fill = _fill(_FILL["quote"] if j <= 10 else _FILL["real"] if j <= 15 else _FILL["comm"]) if j != 16 else PatternFill()
    for j, h in enumerate(DIST_HEAD, start=23):
        c = ws.cell(row=3, column=j, value=h)
        c.font, c.alignment, c.border, c.fill = bold, center, _box, _fill(_FILL["dist"])
    ws.row_dimensions[3].height = 43.2

    RATE, WI, WM, WD = "$Q$2", f"$D${tgt + 1}", f"$D${tgt + 2}", f"$D${tgt + 3}"
    # The template uses one cell (30 %) both as the weight of the margin part and
    # as the margin a file should reach. If the two are ever set differently,
    # the target gets a cell of its own so the sheet still says what is paid.
    same_target = abs(s.target_margin - s.w_margin) < 1e-12
    TARGET = WM if same_target else f"$F${tgt + 2}"
    # ── the lines ────────────────────────────────────────────────────────────
    for i, x in enumerate(rows):
        r = first + i
        vals = {1: x.month or None, 2: x.file, 3: x.status or None, 4: x.sale_price, 5: x.quote_cost,
                11: x.billed, 12: PENDING if x.cost_pending else x.cost, 13: x.provision, 16: x.payment or None}
        for col, v in vals.items():
            ws.cell(row=r, column=col, value=v)
        f = {6: f'=IF(AND(D{r}="",E{r}=""),"",D{r}-E{r})',
             7: f'=IF(OR(D{r}="",D{r}=0),"",F{r}/D{r})',
             10: f"=$I${tot}*{WI}",
             14: f'=IF(OR(ISTEXT(L{r}),AND(K{r}="",L{r}="",M{r}="")),"",K{r}-L{r}-M{r})',
             15: f'=IF(N{r}="","",IF(K{r}=0,IF(N{r}=0,"",-1),N{r}/K{r}))',
             17: f"=(K{r}*{RATE})*J{r}",
             18: f'=IF(O{r}="","",O{r}/{TARGET})',
             19: f'=IF(OR(R{r}="",K{r}=0),0,((K{r}*{RATE})*{WM})*R{r})',
             20: f"=((K{r}*{RATE})*{WD})",
             21: f"=Q{r}+S{r}+T{r}",
             25: f'=IF(P{r}="Paid",U{r}*{s.sales_share!r},0)',
             26: f'=IF(P{r}="Paid",U{r}*{s.admin_share!r},0)',
             27: f"=SUM(Y{r}:Z{r})"}
        for col, v in f.items():
            ws.cell(row=r, column=col, value=v)
        for col in range(1, 28):
            if col in (22, 23, 24):
                continue
            c = ws.cell(row=r, column=col)
            c.font, c.border = (bold if col == 27 else base), _box
            if col in (4, 5, 6, 11, 12, 13, 14, 17, 19, 20, 21, 25, 26, 27):
                c.number_format = MONEY
            elif col in (7, 10, 15, 18):
                c.number_format = "0.00%" if col == 10 else PCT
            elif col == 2:
                c.alignment = Alignment(horizontal="center")
            if col == 12 and x.cost_pending:
                c.alignment, c.font = Alignment(horizontal="right"), Font(name="Calibri", size=11, italic=True, color="7F7F7F")
    # ── totals ───────────────────────────────────────────────────────────────
    ws.cell(row=tot, column=1, value="Total Bookings").font = base
    sums = {4: "D", 5: "E", 6: "F", 11: "K", 12: "L", 14: "N", 17: "Q", 19: "S", 20: "T", 21: "U", 25: "Y",
            26: "Z", 27: "AA"}
    for col, letter in sums.items():
        ws.cell(row=tot, column=col, value=f"=SUM({letter}{first}:{letter}{last})")
    if bookings is not None:           # sale prices are not on the sheet: the month's bookings are typed in
        c = ws.cell(row=tot, column=4, value=float(bookings))
        c.fill = _fill(_FILL["input"])
        c.comment = Comment("Bookings of the month (sale price of the files booked). The sale prices are not "
                            "listed above, so the total is typed here. Change it and the sheet recalculates.",
                            "Commission report")
    ws.cell(row=tot, column=7, value=f'=IF(D{tot}=0,"",F{tot}/D{tot})')
    ws.cell(row=tot, column=8, value=float(budget))
    ws.cell(row=tot, column=9, value=f"=IF(H{tot}=0,0,D{tot}/H{tot})")
    ws.cell(row=tot, column=15, value=f'=IF(K{tot}=0,"",N{tot}/K{tot})')
    for col in list(sums) + [7, 8, 9, 15]:
        c = ws.cell(row=tot, column=col)
        c.font, c.border = bold, _box
        c.number_format = "0%" if col == 9 else PCT if col in (7, 15) else MONEY
    # ── main targets ─────────────────────────────────────────────────────────
    ws.merge_cells(f"C{tgt}:D{tgt}")
    c = ws[f"C{tgt}"]
    c.value, c.font, c.fill, c.alignment = "MAIN TARGETS", bold, _fill(_FILL["input"]), center
    for k, (label, v) in enumerate((("INVOICING", s.w_invoicing), ("GROSS MARGIN", s.w_margin),
                                    ("DISCIPLINE AND BEHAVIOR", s.w_discipline)), start=1):
        ws.merge_cells(f"B{tgt + k}:C{tgt + k}")
        a = ws[f"B{tgt + k}"]
        a.value, a.font, a.alignment = label, bold, Alignment(horizontal="right")
        d = ws[f"D{tgt + k}"]
        d.value, d.font, d.number_format = v, bold, "0%"
    if not same_target:
        ws[f"E{tgt + 2}"].value, ws[f"E{tgt + 2}"].font = "target margin", base
        ws[f"F{tgt + 2}"].value, ws[f"F{tgt + 2}"].number_format, ws[f"F{tgt + 2}"].font = s.target_margin, "0%", bold
    d = ws[f"D{tgt + 4}"]
    d.value, d.font, d.number_format, d.fill = f"=SUM(D{tgt + 1}:D{tgt + 3})", bold, "0%", _fill(_FILL["input"])
    note_row = tgt + 6
    ws[f"B{note_row}"].value = "Commission is paid only if the invoice has been collected."
    ws[f"B{note_row}"].font = Font(name="Calibri", size=12, bold=True, color="FF0000")
    for k, text in enumerate(notes, start=1):
        ws[f"B{note_row + k}"].value = text
        ws[f"B{note_row + k}"].font = Font(name="Calibri", size=10, italic=True)
    # ── distribution by role ─────────────────────────────────────────────────
    r = tgt
    sales_first = r
    for name, share in s.sales_people:
        ws.cell(row=r, column=23, value=name).font = base
        ws.cell(row=r, column=24, value=share).number_format = "0.0%"
        c = ws.cell(row=r, column=25, value=f"=$Y${tot}*X{r}")
        c.number_format = MONEY
        r += 1
    c = ws.cell(row=r, column=24, value=f"=SUM(X{sales_first}:X{r - 1})")
    c.number_format, c.font = "0%", bold
    c = ws.cell(row=r, column=25, value=f"=SUM(Y{sales_first}:Y{r - 1})")
    c.number_format, c.font = MONEY, bold
    sales_total = r
    r += 1
    adm_first = r
    for name, share in s.admin_people:
        ws.cell(row=r, column=23, value=name).font = base
        ws.cell(row=r, column=24, value=share).number_format = "0.0%"
        c = ws.cell(row=r, column=26, value=f"=X{r}*$Z${tot}")
        c.number_format = MONEY
        r += 1
    c = ws.cell(row=r, column=26, value=f"=SUM(Z{adm_first}:Z{r - 1})")
    c.number_format, c.font = MONEY, bold
    c = ws.cell(row=r, column=27, value=f"=(Z{r}+Y{sales_total})-AA{tot}")
    c.number_format, c.font = MONEY, bold
    c.comment = Comment("Control: what the roles receive minus the total paid out. Must be zero.", "Commission report")
    # ── looks ────────────────────────────────────────────────────────────────
    rng = f"P{first}:P{last}"
    ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=['"Paid"'], fill=_fill("C6EFCE"),
                                                  font=Font(color="006100")))
    ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=['"Pending"'], fill=_fill("FFC7CE"),
                                                  font=Font(color="9C0006")))
    widths = {"A": 13.4, "B": 12, "C": 14, "D": 13, "E": 14.3, "F": 13, "G": 10, "H": 14, "I": 12.2, "J": 10,
              "K": 13, "L": 13, "M": 10, "N": 13, "O": 9, "P": 11, "Q": 12, "R": 13.8, "S": 11.1, "T": 12,
              "U": 12, "V": 1.7, "W": 32, "X": 12, "Y": 13, "Z": 13, "AA": 13}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "C4"
    return {"first": first, "last": last, "total": tot, "targets": tgt, "sales_total": sales_total,
            "adm_first": adm_first, "adm_total": r}


# The template's own example, as inputs. Building the sheet from these and
# recalculating must give the template's numbers (see test_commissions_template).
SAMPLE_BUDGET = 500_000.0
SAMPLE_ROWS = (
    Row("111000", "Agosto", "Ganado", 85_000, 60_000, 85_000, 59_500, None, "Paid"),
    Row("111001", "", "Ganado", 85_000, 60_000, 86_500, 58_000, None, "Pending"),
    Row("111002", "", "Ganado", 85_000, 60_000),
    Row("111003", "", "Ganado", 500_000, 60_000, 42_500, 29_000, None, "Paid"),
    Row("110948", "", "Ganado", None, None, None, 8_000),
    Row("110950", "Credit Note", "Ganado", None, None, -5_000, None, None, "Paid"),
    Row("111004", "", "Ganado", 85_000, 60_000),
)


def rows_for_month(report: dict, month: str) -> list:
    """The lines of one invoice month of `report.build(...)` as sheet rows. Only
    the REAL block can be filled from Finance's margin report: quoted prices,
    status and payment status are not in it."""
    names = ("January February March April May June July August September October November December").split()
    out = [Row(file=x["job"], billed=x["billed"], cost=None if not x["cost_posted"] else x["cost"],
               cost_pending=not x["cost_posted"])
           for x in report["lines"] if x["month"] == month]
    if out:                            # the month is written once, on the first line, as in the template
        first = out[0]
        out[0] = Row(first.file, names[int(month[5:7]) - 1], first.status, first.sale_price, first.quote_cost,
                     first.billed, first.cost, first.provision, first.payment, first.cost_pending)
    return out
