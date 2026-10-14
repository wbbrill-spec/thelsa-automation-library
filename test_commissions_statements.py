"""
The statement per person: what each person gets in each new-calculation month,
on the page (report["people"][i]["months"]) and on the "By person" sheet of the
Excel download, whose payable cells link to the split by role of each month's
sheet.
"""
import pytest

from commissions import report as R
from commissions import template_sheet as T
from test_commissions_finance import load  # noqa: F401  (fixture)
from test_commissions_payments import PAID, S, P, _fin, report


@pytest.fixture
def rep(load, tmp_path):
    lines = _fin(load)
    ps, info = P.load(report(tmp_path, mxp=[p for p in PAID if p[9] != 4], usd=[p for p in PAID if p[9] == 4]))
    return R.build(lines, S, {}, collected=ps, collected_reach=R.payment_reach([(ps, info)]))


def test_each_person_has_one_row_per_new_calculation_month_adding_up_to_their_total(rep):
    new = [m["month"] for m in rep["months"] if m["new"]]
    assert new and all(m >= R.NEW_FROM for m in new)
    assert rep["total_new"]["payable"] > 0
    for p in rep["people"]:
        assert [x["month"] for x in p["months"]] == new
        for key in ("payable", "if_collected"):
            assert sum(x[key] for x in p["months"]) == pytest.approx(p[key])
            assert sum(x["usd"][key] for x in p["months"]) == pytest.approx(p["usd"][key])
        for x in p["months"]:
            assert x["held"] == pytest.approx(x["if_collected"] - x["payable"])
    # everyone together gets exactly the month's payable
    for i, m in enumerate(new):
        month = next(r for r in rep["months"] if r["month"] == m)
        assert sum(p["months"][i]["payable"] for p in rep["people"]) == pytest.approx(month["payable"])


def test_by_person_sheet_links_each_amount_to_the_split_under_its_months_sheet(rep):
    wb = T.build_book(rep, S)
    assert wb.sheetnames[-1] == T.PERSON_SHEET and wb.active.title != T.PERSON_SHEET
    ws = wb[T.PERSON_SHEET]
    new = [T.sheet_name(m["month"]) for m in rep["months"] if m["new"]]
    assert [ws.cell(row=5, column=3 + j).value for j in range(len(new))] == new
    names = [n for n, _ in S.sales_people] + [n for n, _ in S.admin_people]
    assert [ws.cell(row=6 + i, column=1).value for i in range(len(names))] == names
    month_ws = wb[new[0]]
    for i, name in enumerate(names):
        link = ws.cell(row=6 + i, column=3).value
        sheet, cell = link[1:].split("!")
        assert sheet == f"'{new[0]}'"
        col = "Y" if i < len(S.sales_people) else "Z"
        assert cell.startswith(col)
        assert month_ws[f"W{cell[1:]}"].value == name          # the link lands on that person's own row
    # second block: the calculated figure, as a number
    top = 6 + len(names) + 3
    assert ws.cell(row=top, column=1).value.startswith("If every invoice were collected")
    first = rep["people"][0]["months"][0]["if_collected"]
    assert ws.cell(row=top + 2, column=3).value == pytest.approx(round(first, 2))


def test_no_new_month_means_no_by_person_sheet():
    wb = T.build_book({"months": [], "lines": []}, S)
    assert T.PERSON_SHEET not in wb.sheetnames
