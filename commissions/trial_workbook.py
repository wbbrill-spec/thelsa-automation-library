"""
trial_workbook.py — the trial commission workbook, built from Finance's margin report.

    python -m commissions.trial_workbook "<Margen x Expediente.xlsx>" "<out.xlsx>"
    python -m commissions.trial_workbook --verify "<Margen x Expediente.xlsx>" "<out.xlsx>"

The workbook carries live formulas (Settings, Lines, Summary, Checks) so Finance
can change a setting, type the real bookings or mark a line Paid and see the
result. `--verify` reads a recalculated copy back and compares every line and
every monthly total with the engine; it must report zero mismatches before the
workbook goes to anyone. Job numbers and amounts only: no customer names.

Not used by the web application. A person runs it.
"""
import sys
from collections import defaultdict

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as L

from . import engine as E
from . import finance as F



def build(src, out, lines=None, bookings=None):
    """Write the workbook to `out` (a path or a file-like object). Give either
    the path of Finance's workbook as `src`, or lines already read as `lines`.
    `bookings` is {month: amount}; months without one keep the placeholder."""
    from .report import LABELS, clean_bookings
    bookings = clean_bookings(bookings)
    S = E.Settings(budgets=dict(E.BUDGET_2026))
    if lines is None:
        lines = F.load_base(src)
    by_month = F.commission_lines(lines, S)
    jobs = F.jobs(lines, S)
    probs = F.problems(lines, S)
    MONTHS = [f"2026-{m:02d}" for m in range(1, 13)]
    MNAME = dict(zip(MONTHS, "January February March April May June July August September October November December".split()))
    RULES = {**{m: "Old calculation (already paid)" for m in MONTHS[:3]}, MONTHS[3]: "To confirm with Lupita",
             **{m: "New calculation" for m in MONTHS[4:9]}, **{m: "No data yet" for m in MONTHS[9:]}}
    BUD = {  # corporate, private — Lupita, 7 Oct 2026
        "2026-01": (461225.74, 560914.82), "2026-02": (791582.67, 894320.44), "2026-03": (1093662.68, 1042447.00),
        "2026-04": (1135452.02, 1029296.29), "2026-05": (928135.00, 1209654.17), "2026-06": (1903929.52, 1650125.28),
        "2026-07": (3052488.88, 1698174.55), "2026-08": (2434877.76, 1955911.89), "2026-09": (2027284.77, 1184801.00),
        "2026-10": (1063245.86, 1101074.74), "2026-11": (1241324.74, 945316.97), "2026-12": (1371324.75, 903428.55)}

    ARIAL = "Arial"
    f_base = Font(name=ARIAL, size=10)
    f_bold = Font(name=ARIAL, size=10, bold=True)
    f_title = Font(name=ARIAL, size=14, bold=True)
    f_input = Font(name=ARIAL, size=10, color="0000FF")
    f_head = Font(name=ARIAL, size=10, bold=True, color="FFFFFF")
    f_link = Font(name=ARIAL, size=10, color="008000")
    f_warn = Font(name=ARIAL, size=10, bold=True, color="C00000")
    fill_head = PatternFill("solid", fgColor="1F3864")
    fill_in = PatternFill("solid", fgColor="FFFF00")
    fill_sub = PatternFill("solid", fgColor="D9E1F2")
    fill_warn = PatternFill("solid", fgColor="FCE4D6")
    thin = Side(style="thin", color="BFBFBF")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    MONEY = '#,##0.00;(#,##0.00);"-"'
    MONEY0 = '#,##0;(#,##0);"-"'
    PCT = '0.0%;(0.0%);"-"'
    wrap = Alignment(wrap_text=True, vertical="top")

    wb = Workbook()

    # ───────────────────────────── Settings ─────────────────────────────
    st = wb.active
    st.title = "Settings"
    st["A1"] = "Settings"; st["A1"].font = f_title
    st["A2"] = "Blue = a number typed in. Yellow = please fill in or correct. Black = calculated."; st["A2"].font = f_base
    rows = [("Global commission", S.rate, PCT, "Workbook COMMISSION CALCULATION 2026, cell Q2. Confirmed by Lupita 7 Oct 2026."),
            ("Weight: invoicing", S.w_invoicing, PCT, "Cell D14. Confirmed 7 Oct 2026."),
            ("Weight: gross margin", S.w_margin, PCT, "Cell D15, used as the weight."),
            ("Weight: discipline and behavior", S.w_discipline, PCT, "Cell D16."),
            ("Target gross margin", S.target_margin, PCT, "Cell D15, used as the target a file should reach. Kept separate from the weight."),
            ("Exchange rate USD to MXN", S.usd_mxn, "0.00", "Internal rate Rogelio gave TMS (Bill, 7 Oct 2026). Applied to invoices in US dollars."),
            ("Share to Sales", S.sales_share, "0.0000%", "Workbook: U/3*2."),
            ("Share to Adm, MC and Buyer", S.admin_share, "0.0000%", "Workbook: U/3*1.")]
    st["A4"], st["B4"], st["C4"] = "Rule", "Value", "Source"
    for c in "ABC":
        st[f"{c}4"].font = f_head; st[f"{c}4"].fill = fill_head
    for i, (name, val, fmt, srcnote) in enumerate(rows, start=5):
        st[f"A{i}"] = name; st[f"B{i}"] = val; st[f"C{i}"] = srcnote
        st[f"A{i}"].font = f_base; st[f"B{i}"].font = f_input; st[f"B{i}"].number_format = fmt; st[f"C{i}"].font = f_base
    R_RATE, R_WI, R_WM, R_WD, R_TGT, R_FX, R_SS, R_AS = ("Settings!$B$5", "Settings!$B$6", "Settings!$B$7", "Settings!$B$8",
                                                         "Settings!$B$9", "Settings!$B$10", "Settings!$B$11", "Settings!$B$12")
    st["A13"] = "Weights add up to"; st["B13"] = "=B6+B7+B8"; st["B13"].number_format = PCT
    st["A13"].font = f_base; st["B13"].font = f_base

    # monthly budget and bookings
    H0 = 16
    st[f"A{H0-1}"] = "Monthly booking budget and bookings (MXN)"; st[f"A{H0-1}"].font = f_bold
    heads = ["Month", "Month name", "Budget Corporate", "Budget Private", "Budget used", "Bookings in the month",
             "Budget achievement", "Sales reach", "Rules that apply"]
    for j, h in enumerate(heads, start=1):
        c = st.cell(row=H0, column=j, value=h); c.font = f_head; c.fill = fill_head; c.alignment = wrap
    for i, m in enumerate(MONTHS, start=H0 + 1):
        st.cell(row=i, column=1, value=m).font = f_base
        st.cell(row=i, column=2, value=MNAME[m]).font = f_base
        for col, v in ((3, BUD[m][0]), (4, BUD[m][1])):
            c = st.cell(row=i, column=col, value=v); c.font = f_input; c.number_format = MONEY
        c = st.cell(row=i, column=5, value=f"=C{i}+D{i}"); c.font = f_base; c.number_format = MONEY
        c = st.cell(row=i, column=6, value=bookings.get(m, f"=E{i}")); c.font = f_input; c.number_format = MONEY
        if m not in bookings:
            c.fill = fill_in
        c = st.cell(row=i, column=7, value=f"=IF(E{i}=0,0,F{i}/E{i})"); c.font = f_base; c.number_format = PCT
        c = st.cell(row=i, column=8, value=f"=G{i}*$B$6"); c.font = f_base; c.number_format = "0.0000"
        st.cell(row=i, column=9, value=RULES[m]).font = f_base
    MFIRST, MLAST = H0 + 1, H0 + 12
    st[f"F{H0}"].comment = Comment(
        "PLACEHOLDER. Real bookings (sale prices of the files booked in the month) are not available yet, "
        "so each month is set equal to its budget, which gives a sales reach of 0.60. "
        "Type the real bookings over these cells and everything recalculates.", "Trial")
    st[f"C{H0}"].comment = Comment("Source: Lupita's email of 7 Oct 2026, 'Budget Corporate business 2026' and "
                                   "'Budget Private business 2026'. The Diplomatic line is left out: Embassy business is "
                                   "not part of this report.", "Trial")
    MRNG = f"Settings!$A${MFIRST}:$A${MLAST}"
    REACH = f"Settings!$H${MFIRST}:$H${MLAST}"

    # distribution
    D0 = MLAST + 3
    st[f"A{D0-1}"] = "Distribution of each paid commission"; st[f"A{D0-1}"].font = f_bold
    for j, h in enumerate(["Role and name", "Group", "Share of the group"], start=1):
        c = st.cell(row=D0, column=j, value=h); c.font = f_head; c.fill = fill_head
    people = [(n, "Sales", p) for n, p in S.sales_people] + [(n, "Adm, MC and Buyer", p) for n, p in S.admin_people]
    for i, (n, grp, p) in enumerate(people, start=D0 + 1):
        st.cell(row=i, column=1, value=n).font = f_base
        st.cell(row=i, column=2, value=grp).font = f_base
        c = st.cell(row=i, column=3, value=p); c.font = f_input; c.number_format = PCT
    PFIRST, PLAST = D0 + 1, D0 + len(people)
    st[f"A{PLAST+1}"] = "Names: Buyer and Cost Manager and Logistics Supervisor as given by Lupita, 7 Oct 2026."
    st[f"A{PLAST+1}"].font = Font(name=ARIAL, size=9, italic=True)
    for col, w in zip("ABCDEFGHI", (34, 16, 18, 18, 18, 22, 14, 12, 34)):
        st.column_dimensions[col].width = w
    st.column_dimensions["C"].width = 22

    # ───────────────────────────── Lines ─────────────────────────────
    ln = wb.create_sheet("Lines")
    ln["A1"] = "Commission lines: one per job per invoice month (Corporate and Private only)"; ln["A1"].font = f_title
    ln["A2"] = ("TRIAL. Billed and cost come from Finance's margin report to 30 Sep 2026. Sales reach is a placeholder "
                "and no invoice is marked as collected, so nothing here is payable yet.")
    ln["A2"].font = f_warn
    HR = 4
    cols = ["Month", "File", "Billed, Finance rate (MXN)", "Billed for commission (MXN)", "Actual cost (MXN)", "Provision",
            "Gross margin", "% gross margin", "Cost posted?", "Payment status", "Sales reach", "Invoicing part",
            "Gross margin part", "Discipline part", "Total commission", "Sales (2/3)", "Adm, MC and Buyer (1/3)", "Note"]
    for j, h in enumerate(cols, start=1):
        c = ln.cell(row=HR, column=j, value=h); c.font = f_head; c.fill = fill_head; c.alignment = wrap; c.border = box
    ln.row_dimensions[HR].height = 44
    ln[f"D{HR}"].comment = Comment("US-dollar invoices at the internal rate on the Settings sheet (16.5); peso invoices as "
                                   "invoiced. Net of credit notes dated in the same month.", "Trial")
    ln[f"E{HR}"].comment = Comment("All supplier and TRS-to-TMS cost posted for the job up to 30 Sep 2026, laid on the "
                                   "job's invoice months in proportion to what was billed in each. A month where credit "
                                   "notes exceed invoices carries no cost, so no month ever carries more cost than the "
                                   "job has.", "Trial")
    ln[f"J{HR}"].comment = Comment("Type Paid when the invoice has been collected, Pending when not. Blank = not known. "
                                   "Only Paid lines are distributed.", "Trial")
    ln[f"I{HR}"].comment = Comment("No = Finance has no cost for this job yet. Its gross margin part is not calculated "
                                   "until the cost is posted.", "Trial")
    r = HR
    fin_by = {(j["job"], m): v for (job, t), j in jobs.items() if t == F.IN_SCOPE for m, v in j["by_month_fin"].items()}
    unstated = {p["job"] for p in probs if p["kind"] in ("currency_missing", "no_dollar_amount")
                and "Finance's peso amount used" in p["detail"]}
    cn_wrong = {p["job"] for p in probs if p["kind"] == "credit_note_amount"}
    fx_wrong = {p["job"] for p in probs if p["kind"] == "fx_outlier"}
    two_types = {p["job"] for p in probs if p["kind"] == "type_conflict"}
    for m in sorted(by_month):
        for x in sorted(by_month[m], key=lambda z: z.job):
            r += 1
            notes = []
            if x.note:
                notes.append("Billed to the Embassy but typed CORP-PART: confirm in or out")
            if not x.cost_posted:
                notes.append("No cost posted yet: margin part waits")
            if x.job in unstated:
                notes.append("Invoice currency not known: Finance's peso amount used")
            if x.job in cn_wrong:
                notes.append("A credit note on this job has the wrong amount (see Checks): billed is understated")
            if x.job in fx_wrong:
                notes.append("Exchange rate mistyped in Finance's report (see Checks); the commission uses 16.5 and is not affected")
            if x.job in two_types:
                notes.append("Finance typed this job two ways (see Checks)")
            if x.billed < 0:
                notes.append("Credit notes exceed invoices this month")
            flagged = bool(x.note or not x.cost_posted or x.job in unstated | cn_wrong | fx_wrong | two_types)
            vals = [m, x.job, fin_by.get((x.job, m), 0.0), x.billed, x.cost, None,
                    f"=D{r}-E{r}-F{r}", f'=IF(D{r}=0,IF(G{r}=0,"",-1),G{r}/D{r})', "Yes" if x.cost_posted else "No", None,
                    f"=INDEX({REACH},MATCH(A{r},{MRNG},0))", f"=D{r}*{R_RATE}*K{r}",
                    f'=IF(I{r}="No",0,IF(D{r}=0,0,D{r}*{R_RATE}*{R_WM}*(H{r}/{R_TGT})))',
                    f"=D{r}*{R_RATE}*{R_WD}", f"=L{r}+M{r}+N{r}",
                    f'=IF(J{r}="Paid",O{r}*{R_SS},0)', f'=IF(J{r}="Paid",O{r}*{R_AS},0)', "; ".join(notes)]
            for j, v in enumerate(vals, start=1):
                c = ln.cell(row=r, column=j, value=v); c.font = f_base; c.border = box
                if j in (3, 4, 5):
                    c.font = f_input; c.number_format = MONEY
                elif j in (6,):
                    c.fill = fill_in; c.number_format = MONEY
                elif j in (7, 12, 13, 14, 15, 16, 17):
                    c.number_format = MONEY
                elif j == 8:
                    c.number_format = PCT
                elif j == 10:
                    c.fill = fill_in
                elif j == 11:
                    c.number_format = "0.0000"; c.font = f_link
            if flagged:
                ln.cell(row=r, column=18).fill = fill_warn
    LFIRST, LLAST = HR + 1, r
    tr = LLAST + 1
    ln.cell(row=tr, column=1, value="Total").font = f_bold
    for j in (3, 4, 5, 7, 12, 13, 14, 15, 16, 17):
        c = ln.cell(row=tr, column=j, value=f"=SUM({L(j)}{LFIRST}:{L(j)}{LLAST})"); c.font = f_bold; c.number_format = MONEY; c.fill = fill_sub
    c = ln.cell(row=tr, column=8, value=f"=IF(D{tr}=0,0,G{tr}/D{tr})"); c.font = f_bold; c.number_format = PCT; c.fill = fill_sub
    ln.freeze_panes = ln[f"C{HR+1}"]
    ln.auto_filter.ref = f"A{HR}:{L(len(cols))}{LLAST}"
    for j, w in enumerate((10, 10, 16, 16, 15, 11, 15, 10, 9, 11, 9, 13, 13, 12, 14, 13, 14, 58), start=1):
        ln.column_dimensions[L(j)].width = w


    def rng(col):
        return f"Lines!${col}${LFIRST}:${col}${LLAST}"


    # ───────────────────────────── Summary ─────────────────────────────
    sm = wb.create_sheet("Summary", 0)
    sm["A1"] = "TMS commission report: trial run, January to September 2026"; sm["A1"].font = f_title
    sm["A2"] = "Corporate accounts and Private customers only. US Embassy and agent business are not included."; sm["A2"].font = f_base
    sm["A3"] = "TRIAL, NOT FOR PAYMENT. Three inputs are still missing: bookings per month, which invoices are collected, and the costs not posted yet."
    sm["A3"].font = f_warn
    SH = 5
    heads = ["Month", "Rules that apply", "Lines", "Billed for commission", "Actual cost", "Gross margin", "% gross margin",
             "Sales reach", "Invoicing part", "Gross margin part", "Discipline part", "Total calculated",
             "Of which payable (collected)", "Held (not collected or not known)", "Lines waiting for cost", "Billed on those lines"]
    for j, h in enumerate(heads, start=1):
        c = sm.cell(row=SH, column=j, value=h); c.font = f_head; c.fill = fill_head; c.alignment = wrap; c.border = box
    sm.row_dimensions[SH].height = 44
    data_months = MONTHS[:9]
    for i, m in enumerate(data_months, start=SH + 1):
        srow = MFIRST + MONTHS.index(m)
        vals = [m, f"=Settings!I{srow}", f'=COUNTIF({rng("A")},A{i})', f'=SUMIFS({rng("D")},{rng("A")},A{i})',
                f'=SUMIFS({rng("E")},{rng("A")},A{i})', f'=SUMIFS({rng("G")},{rng("A")},A{i})',
                f"=IF(D{i}=0,0,F{i}/D{i})", f"=Settings!H{srow}", f'=SUMIFS({rng("L")},{rng("A")},A{i})',
                f'=SUMIFS({rng("M")},{rng("A")},A{i})', f'=SUMIFS({rng("N")},{rng("A")},A{i})',
                f'=SUMIFS({rng("O")},{rng("A")},A{i})',
                f'=SUMIFS({rng("P")},{rng("A")},A{i})+SUMIFS({rng("Q")},{rng("A")},A{i})', f"=L{i}-M{i}",
                f'=COUNTIFS({rng("A")},A{i},{rng("I")},"No")', f'=SUMIFS({rng("D")},{rng("A")},A{i},{rng("I")},"No")']
        for j, v in enumerate(vals, start=1):
            c = sm.cell(row=i, column=j, value=v); c.font = f_base; c.border = box
            if j in (4, 5, 6, 9, 10, 11, 12, 13, 14, 16):
                c.number_format = MONEY0
            elif j == 7:
                c.number_format = PCT
            elif j == 8:
                c.number_format = "0.0000"; c.font = f_link
            elif j == 2:
                c.font = f_link
    SF, SL = SH + 1, SH + len(data_months)
    NEW_F = SH + 1 + 4          # May
    for label, a, b, rr in (("Total January to September", SF, SL, SL + 1), ("New calculation only: May to September", NEW_F, SL, SL + 2)):
        sm.cell(row=rr, column=1, value=label).font = f_bold
        for j in (3, 4, 5, 6, 9, 10, 11, 12, 13, 14, 15, 16):
            c = sm.cell(row=rr, column=j, value=f"=SUM({L(j)}{a}:{L(j)}{b})"); c.font = f_bold; c.fill = fill_sub
            c.number_format = MONEY0 if j not in (3, 15) else "0"
        c = sm.cell(row=rr, column=7, value=f"=IF(D{rr}=0,0,F{rr}/D{rr})"); c.font = f_bold; c.number_format = PCT; c.fill = fill_sub
    TOT_ALL, TOT_NEW = SL + 1, SL + 2

    # distribution
    DH = SL + 5
    sm.cell(row=DH - 1, column=1, value="Distribution, May to September (new calculation)").font = f_bold
    for j, h in enumerate(["Role and name", "Group", "Share of the group", "If every invoice were collected", "Payable now (lines marked Paid)"], start=1):
        c = sm.cell(row=DH, column=j, value=h); c.font = f_head; c.fill = fill_head; c.alignment = wrap; c.border = box
    for k, (n, grp, p) in enumerate(people):
        i = DH + 1 + k
        prow = PFIRST + k
        share = R_SS if grp == "Sales" else R_AS
        vals = [f"=Settings!A{prow}", f"=Settings!B{prow}", f"=Settings!C{prow}", f"=$L${TOT_NEW}*{share}*C{i}", f"=$M${TOT_NEW}*{share}*C{i}"]
        for j, v in enumerate(vals, start=1):
            c = sm.cell(row=i, column=j, value=v); c.font = f_link if j <= 3 else f_base; c.border = box
            if j == 3:
                c.number_format = PCT
            elif j > 3:
                c.number_format = MONEY
    DL = DH + len(people)
    sm.cell(row=DL + 1, column=1, value="Total").font = f_bold
    for j in (4, 5):
        c = sm.cell(row=DL + 1, column=j, value=f"=SUM({L(j)}{DH+1}:{L(j)}{DL})"); c.font = f_bold; c.number_format = MONEY; c.fill = fill_sub
    sm.cell(row=DL + 2, column=1, value="Control (must be zero)").font = f_base
    c = sm.cell(row=DL + 2, column=4, value=f"=D{DL+1}-L{TOT_NEW}"); c.number_format = MONEY; c.font = f_base
    c = sm.cell(row=DL + 2, column=5, value=f"=E{DL+1}-M{TOT_NEW}"); c.number_format = MONEY; c.font = f_base

    NH = DL + 5
    n_jobs = sum(1 for (job, t) in jobs if t == F.IN_SCOPE)
    notes = [
        f"What is real: billed amounts, costs, file numbers and file types, from Finance's margin report to 30 Sep 2026 ({n_jobs} Corporate/Private jobs).",
        "What is a placeholder: bookings per month are set equal to the budget (Settings sheet, yellow cells), so sales reach shows 0.60 in every month.",
        "What is unknown: which invoices are collected. Until a line is marked Paid on the Lines sheet, it is held and nothing is distributed.",
        "Gross margin part: calculated on actual cost. Lines whose job has no cost posted yet show zero and are counted in the last two columns.",
        "Costs arrive about two months after the invoice, so August and September margins are overstated and will fall as costs are posted.",
        "A job's cost is laid on its invoice months in proportion to what was billed. When more cost arrives later, earlier months of that job change too.",
        "A credit note takes back all three parts of the commission. A job cancelled in full pays nothing overall.",
        "The Checks sheet lists every point in Finance's report that changes a number here. Start with the first lines: a credit note with a wrong amount and a mistyped exchange rate.",
        "US-dollar invoices are converted at 16.5; costs are in pesos as Finance booked them, at the rate of each day.",
        "January to March were paid on the old calculation and April is to be confirmed; they are shown for completeness only.",
        "Formulas follow Rogelio's workbook COMMISSION CALCULATION 2026 cell for cell, and reproduce its sample (file 111000 = 3,590.40).",
    ]
    sm.cell(row=NH - 1, column=1, value="How to read this trial").font = f_bold
    for k, t in enumerate(notes):
        c = sm.cell(row=NH + k, column=1, value=t); c.font = f_base
    for j, w in enumerate((38, 30, 18, 20, 18, 16, 12, 10, 14, 14, 13, 15, 15, 16, 12, 16), start=1):
        sm.column_dimensions[L(j)].width = w
    sm.freeze_panes = sm[f"A{SH+1}"]

    # ───────────────────────────── Checks ─────────────────────────────
    ck = wb.create_sheet("Checks")
    ck["A1"] = "Points in Finance's report to look at"; ck["A1"].font = f_title
    ck["A2"] = "File numbers and amounts only. No customer names are carried into this workbook."; ck["A2"].font = f_base
    LABEL = LABELS
    ORDER = list(LABEL)
    ORDER += sorted({p["kind"] for p in probs} - set(ORDER))
    for j, h in enumerate(["Point", "File", "Detail", "Amount (MXN)"], start=1):
        c = ck.cell(row=4, column=j, value=h); c.font = f_head; c.fill = fill_head; c.border = box
    rr = 4
    for kind in ORDER:
        for p in sorted((p for p in probs if p["kind"] == kind), key=lambda z: (z["job"], z["detail"])):
            rr += 1
            amt = p.get("amount_mxn", p.get("understated_mxn"))
            for j, v in enumerate([LABEL.get(kind, kind), p["job"], p["detail"], amt], start=1):
                c = ck.cell(row=rr, column=j, value=v); c.font = f_base; c.border = box
                if j == 4:
                    c.number_format = MONEY
    for col, w in zip("ABCD", (40, 12, 78, 16)):
        ck.column_dimensions[col].width = w
    ck.freeze_panes = "A5"

    wb.save(out)
    return {"lines": LLAST - LFIRST + 1, "checks": rr - 4}


def verify(src, out):
    import openpyxl
    s=E.Settings(budgets=dict(E.BUDGET_2026))
    cl=F.commission_lines(F.load_base(src), s)
    wb=openpyxl.load_workbook(out, data_only=True); ln=wb['Lines']; sm=wb['Summary']
    exp={}
    for m, ls in cl.items():
        res=E.calc_month(m, ls, s.budgets[m], s)      # bookings = budget -> reach .6
        assert abs(res.reach-0.6)<1e-12
        for r in res.lines: exp[(m, r.line.job)]=r
    bad=0; n=0
    for row in ln.iter_rows(min_row=5, values_only=True):
        if not row[0] or row[0]=='Total': continue
        r=exp.pop((row[0], row[1])); n+=1
        for got, want, name in ((row[3], r.line.billed,'billed'),(row[4], r.line.cost,'cost'),(row[6], r.gross_margin,'gm'),(row[11], r.invoicing,'inv'),(row[12], r.margin,'margin'),(row[13], r.discipline,'disc'),(row[14], r.total,'total'),(row[15] or 0,0,'sales paid'),(row[16] or 0,0,'adm paid')):
            if abs((got or 0)-want)>1e-6: bad+=1; print("MISMATCH", row[0], row[1], name, got, want)
        if (row[8]=="Yes")!=r.line.cost_posted: bad+=1; print("cost posted flag", row[1])
    print("lines checked", n, "left over in engine", len(exp), "mismatches", bad)
    # summary rows
    tot={}
    for m, ls in cl.items():
        res=E.calc_month(m, ls, s.budgets[m], s); tot[m]=res
    for row in sm.iter_rows(min_row=6, max_row=14, values_only=True):
        res=tot[row[0]]
        chk=[(row[2],len(res.lines)),(row[3],res.billed),(row[8],sum(x.invoicing for x in res.lines)),(row[9],sum(x.margin for x in res.lines)),(row[10],sum(x.discipline for x in res.lines)),(row[11],res.total),(row[12],0),(row[13],res.total),(row[14],sum(1 for x in res.lines if not x.line.cost_posted))]
        for got,want in chk:
            if abs((got or 0)-want)>1e-6: bad+=1; print("SUMMARY MISMATCH", row[0], got, want)
        print(row[0], row[1], "| lines", row[2], "| billed %.0f | gm%% %.1f%% | inv %.0f | margin %.0f | disc %.0f | total %.0f | waiting cost %s (%.0f)" % (row[3], 100*row[6], row[8], row[9], row[10], row[11], row[14], row[15] or 0))
    for rr in (15,16):
        row=[c.value for c in sm[rr]]; print(row[0], "| lines", row[2], "| billed %.0f | cost %.0f | gm %.0f (%.1f%%) | inv %.0f | margin %.0f | disc %.0f | TOTAL %.2f | payable %.2f | held %.2f | waiting %s / %.0f" % (row[3],row[4],row[5],100*row[6],row[8],row[9],row[10],row[11],row[12],row[13],row[14],row[15]))
    new=sum(tot[m].total for m in ('2026-05','2026-06','2026-07','2026-08','2026-09'))
    print("engine May-Sep total %.2f" % new, "sheet", sm['L16'].value)
    print("distribution if collected:"); 
    for rr in range(20, 30):
        row=[c.value for c in sm[rr]]
        if row[0]: print("  ", row[0], row[1], row[2], row[3], row[4])
    print("mismatches total:", bad)
    ok = bad == 0
    # formula errors scan
    wb2=openpyxl.load_workbook(out, data_only=True)
    errs=[(ws.title,c.coordinate,c.value) for ws in wb2 for r in ws.iter_rows() for c in r if isinstance(c.value,str) and c.value.startswith('#')]
    print("error cells:", errs[:5], len(errs))
    return ok and not errs


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--verify"]
    if len(args) != 2:
        sys.exit(__doc__)
    if "--verify" in sys.argv:
        sys.exit(0 if verify(*args) else 1)
    print(build(*args))
