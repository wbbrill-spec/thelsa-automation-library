"""Picking the right week tab out of Gustavo's Concentrado Remisiones workbook.

Graph access was unblocked on 2026-09-15 and the dashboard immediately reported
a healthy read — 190 KB, 38 worksheets, source "graph" — merging 0 rows, because
latest_week() took the workbook's LAST sheet and the workbook ends with an empty
scratch tab named "Hoja1". A clean read that silently contributes nothing is the
failure mode worth testing for.

Tab names below are the real ones from the live workbook.
"""
import datetime as dt

from crossborder.remisiones import latest_week, week_end_date

TODAY = dt.date(2026, 9, 15)


# ── tab names ────────────────────────────────────────────────────────────────


def test_every_tab_spelling_in_the_real_workbook_parses():
    """Four different patterns are in use; none of them is a typo to fix."""
    assert week_end_date("4 al 10 ene", 2026) == dt.date(2026, 1, 10)
    assert week_end_date("25 ene al 31 ene", 2026) == dt.date(2026, 1, 31)
    assert week_end_date("22 FEB 28 FEB", 2026) == dt.date(2026, 2, 28)
    assert week_end_date("1 feb al 7 feb", 2026) == dt.date(2026, 2, 7)


def test_a_week_straddling_two_months_ends_in_the_second():
    assert week_end_date("26 abr al 2 may", 2026) == dt.date(2026, 5, 2)


def test_a_non_week_tab_is_not_a_week():
    for t in ("Hoja1", "Hoja 2", "Resumen", "", "TOTALES"):
        assert week_end_date(t, 2026) is None


def test_an_impossible_date_is_rejected_rather_than_raising():
    assert week_end_date("29 al 31 feb", 2026) is None


# ── picking the tab ──────────────────────────────────────────────────────────


def test_the_scratch_tab_never_wins():
    """The actual bug: 'Hoja1' sorted last and had no rows."""
    sheets = {"6 al 12 sep": ["a"], "13 al 19 sep": ["b", "c"], "Hoja1": []}
    assert latest_week(sheets, TODAY) == "13 al 19 sep"


def test_the_current_week_wins_over_earlier_ones():
    sheets = {"30 ago al 5 sep": ["a"], "6 al 12 sep": ["b"], "13 al 19 sep": ["c"]}
    assert latest_week(sheets, TODAY) == "13 al 19 sep"


def test_an_empty_tab_rolled_forward_early_does_not_win():
    """Next week's tab often exists before anyone has typed into it."""
    sheets = {"13 al 19 sep": ["c"], "20 al 26 sep": []}
    assert latest_week(sheets, TODAY) == "13 al 19 sep"


def test_a_far_future_tab_does_not_win():
    sheets = {"13 al 19 sep": ["c"], "27 dic al 2 ene": ["x"]}
    assert latest_week(sheets, TODAY) == "13 al 19 sep"


def test_it_falls_back_to_the_last_tab_with_rows():
    sheets = {"Hoja1": [], "Resumen": ["x"]}
    assert latest_week(sheets, TODAY) == "Resumen"


def test_it_never_raises_on_an_all_empty_workbook():
    assert latest_week({"Hoja1": []}, TODAY) == "Hoja1"
