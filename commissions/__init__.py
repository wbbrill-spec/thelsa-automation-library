"""TMS commission report — Corporate accounts and Private customers.

engine.py          the calculation, exactly as Finance's workbook does it
finance.py         reader for Finance's gross-margin report (Margen x Expediente)
moveware.py        bookings per month and job type, from records the Moveware reader already holds
report.py          the report as numbers: months, totals, distribution, lines, points to look at
store.py           where uploaded lines and typed-in bookings are kept
web.py             the /commissions pages (restricted)
trial_workbook.py  builds and checks the Excel workbook

Nothing in this package talks to Moveware.
"""
