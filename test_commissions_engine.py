"""The commission engine must reproduce Finance's workbook to the cent.

Reference: COMMISSION CALCULATION 2026.xlsx (Rogelio's template, the sample
Lupita sent on 28 Sep 2026). Every expected number below is a cell of that
workbook. If one of these tests fails, the engine is wrong — do not adjust the
expected values.
"""
import pytest

from commissions import engine as E

S = E.Settings(budgets={"sample": 500_000.0}, margin_waits_for_cost=False)
BOOKINGS = 840_000.0            # D11: sale prices booked in the month

# job, billed K, cost L, paid P      -> expected Q, S, T, U
SAMPLE = [
    ("111000", 85_000, 59_500, True, 2570.40, 765.00, 255.00, 3590.40),
    ("111001", 86_500, 58_000, False, 2615.76, 855.00, 259.50, 3730.26),
    ("111002", 0, 0, None, 0, 0, 0, 0),            # booked, not billed
    ("111003", 42_500, 29_000, True, 1285.20, 405.00, 127.50, 1817.70),
    ("110948", 0, 8_000, None, 0, 0, 0, 0),        # cost, no billing
    ("110950", -5_000, 0, True, -151.20, -150.00, -15.00, -316.20),   # credit note
    ("111004", 0, 0, None, 0, 0, 0, 0),
]


def _month():
    lines = [E.Line(job=j, billed=k, cost=c, paid=p) for j, k, c, p, *_ in SAMPLE]
    return E.calc_month("sample", lines, BOOKINGS, S)


def test_sales_reach_is_budget_achievement_times_the_invoicing_weight():
    assert E.sales_reach(BOOKINGS, 500_000, S) == pytest.approx(1.008)     # J4
    assert _month().reach == pytest.approx(1.008)


def test_no_cap_on_sales_reach():
    """Confirmed by Lupita, 7 Oct 2026: no maximum."""
    assert E.sales_reach(5_000_000, 500_000, S) == pytest.approx(6.0)


@pytest.mark.parametrize("row", SAMPLE, ids=[r[0] for r in SAMPLE])
def test_every_row_of_the_sample(row):
    job, k, c, p, q, s_, t, u = row
    r = E.calc_line(E.Line(job=job, billed=k, cost=c, paid=p), 1.008, S)
    assert r.invoicing == pytest.approx(q, abs=0.005)
    assert r.margin == pytest.approx(s_, abs=0.005)
    assert r.discipline == pytest.approx(t, abs=0.005)
    assert r.total == pytest.approx(u, abs=0.005)


def test_margin_part_is_765_not_229_50():
    """The 30 % is in the workbook twice (weight and target) and cancels.
    'Gross margin x 3 % x 30 %' gives 229.50 and is NOT what the workbook pays."""
    r = E.calc_line(E.Line("111000", billed=85_000, cost=59_500), 1.008, S)
    assert r.gross_margin == 25_500 and r.margin_pct == pytest.approx(0.30)
    assert r.margin == pytest.approx(765.00)
    assert r.margin != pytest.approx(229.50)


def test_cost_with_no_billing_pays_zero_not_a_negative():
    r = E.calc_line(E.Line("110948", billed=0, cost=8_000), 1.008, S)
    assert r.gross_margin == -8_000 and r.margin_pct == -1.0      # O8 = -100 %
    assert r.margin == 0 and r.total == 0


def test_a_loss_making_file_reduces_its_total():
    """Intended (Lupita, 7 Oct): a file sold badly must be compensated."""
    r = E.calc_line(E.Line("x", billed=100_000, cost=130_000), 0.6, S)
    assert r.margin == pytest.approx(-900.0)                      # -30,000 x 3 %
    assert r.total == pytest.approx(1800 - 900 + 300)


def test_month_totals_match_row_11():
    m = _month()
    assert m.billed == pytest.approx(209_000)                     # K11
    assert sum(r.invoicing for r in m.lines) == pytest.approx(6320.16)   # Q11
    assert sum(r.margin for r in m.lines) == pytest.approx(1875.00)      # S11
    assert sum(r.discipline for r in m.lines) == pytest.approx(627.00)   # T11
    assert m.total == pytest.approx(8822.16)                      # U11


def test_only_collected_invoices_are_paid():
    m = _month()
    assert m.payable_total == pytest.approx(5091.90)              # AA11
    assert m.held_total == pytest.approx(3730.26)                 # 111001, Pending


def test_distribution_matches_the_workbook():
    d = _month().distribution(S)
    assert d["sales_pool"] == pytest.approx(3394.60)              # Y11
    assert d["admin_pool"] == pytest.approx(1697.30)              # Z11
    p = d["people"]
    assert p["KAR 1 Pablo"] == pytest.approx(1697.30)             # Y13
    assert p["KAR 2 Edwin"] == pytest.approx(1697.30)             # Y14
    assert p["Buyer and Cost Manager (Lupita)"] == pytest.approx(607.6334)      # Z16
    assert p["Logistics Supervisor (Sara Reyes)"] == pytest.approx(356.433)     # Z17
    for who in ("MC 1 Stephanie", "MC 2 Elizabeth", "Adm. 1 Fernanda", "Adm. 2 Monica"):
        assert p[who] == pytest.approx(183.3084)                  # Z18:Z21
    assert sum(p.values()) == pytest.approx(5091.90)
    assert d["control"] == pytest.approx(0)                       # AA22


def test_weight_and_target_margin_are_separate_settings():
    """Raising the target must not change the weight, and the reverse."""
    line = E.Line("x", billed=100_000, cost=70_000)               # 30 % margin
    base = E.calc_line(line, 0.6, S).margin                       # 900
    harder = E.Settings(target_margin=0.40, budgets={}, margin_waits_for_cost=False)
    assert E.calc_line(line, 0.6, harder).margin == pytest.approx(base * 0.30 / 0.40)
    assert E.calc_line(line, 0.6, harder).discipline == E.calc_line(line, 0.6, S).discipline


def test_margin_part_waits_for_actual_cost():
    """Bill, 7 Oct: the margin part is paid on actual cost. A billed file with
    no cost posted has no margin part yet; the other two parts still stand."""
    s = E.Settings(budgets={})
    r = E.calc_line(E.Line("x", billed=100_000, cost=0, cost_posted=False), 0.6, s)
    assert r.margin_pending and r.margin == 0
    assert r.invoicing == pytest.approx(1800) and r.discipline == pytest.approx(300)
    done = E.calc_line(E.Line("x", billed=100_000, cost=70_000, cost_posted=True), 0.6, s)
    assert not done.margin_pending and done.margin == pytest.approx(900)


def test_unknown_payment_status_is_never_payable():
    r = E.calc_line(E.Line("x", billed=1000, cost=500, paid=None), 0.6, S)
    assert r.payable is False


def test_bad_settings_stop_the_calculation():
    for bad in (dict(w_invoicing=0.7), dict(sales_share=0.5),
                dict(admin_people=(("a", 0.5),)), dict(rate=0)):
        with pytest.raises(E.SettingsError):
            E.Settings(budgets={"m": 1.0}, **bad).check()
    with pytest.raises(E.SettingsError):
        E.calc_month("2031-01", [], 1000, E.Settings(budgets={}))  # no budget for the month


def test_a_name_twice_in_the_distribution_is_refused():
    """Two lines with one name would be paid as one and a share would vanish."""
    twice = (("KAR 1 Pablo", 0.5), ("kar 1 pablo ", 0.5))
    with pytest.raises(E.SettingsError):
        E.Settings(sales_people=twice).check()
    both_pools = (("KAR 1 Pablo", 0.358), ("b", 0.21), ("c", 0.108), ("d", 0.108), ("e", 0.108), ("f", 0.108))
    with pytest.raises(E.SettingsError):
        E.Settings(admin_people=both_pools).check()


def test_negative_weights_and_shares_are_refused():
    for bad in (dict(w_invoicing=1.2, w_margin=-0.3, w_discipline=0.1),
                dict(sales_share=1.5, admin_share=-0.5),
                dict(sales_people=(("a", 1.5), ("b", -0.5)))):
        with pytest.raises(E.SettingsError):
            E.Settings(**bad).check()


def test_month_and_distribution_both_check_the_settings():
    bad = E.Settings(budgets={"m": 1000.0}, w_invoicing=0.7)
    with pytest.raises(E.SettingsError):
        E.calc_month("m", [E.Line("x", billed=100)], 1000, bad)
    good = E.calc_month("m", [E.Line("x", billed=100, paid=True)], 1000, E.Settings(budgets={"m": 1000.0}))
    with pytest.raises(E.SettingsError):
        good.distribution(E.Settings(sales_people=(("a", 0.5), ("a", 0.5))))


def test_control_is_what_people_get_against_what_is_paid():
    d = _month().distribution(S)
    assert d["control"] == pytest.approx(0, abs=1e-9)
    assert sum(d["people"].values()) == pytest.approx(_month().payable_total)


def test_nothing_prints_as_minus_zero():
    r = E.calc_line(E.Line("x", billed=0, cost=0), 1.008, S)
    for v in (r.gross_margin, r.invoicing, r.margin, r.discipline, r.total):
        assert str(v) == "0.0"
    r = E.calc_line(E.Line("x", billed=-0.0, cost=0.0), 0.6, S)
    assert "-0.0" not in repr((r.gross_margin, r.invoicing, r.margin, r.discipline))


def test_budget_2026_is_corporate_plus_private():
    assert E.BUDGET_2026["2026-05"] == pytest.approx(2_137_789.17)
    assert sum(E.BUDGET_2026.values()) == pytest.approx(31_680_000.0, abs=0.5)
    assert len(E.BUDGET_2026) == 12


def test_exchange_rate_is_the_internal_16_5():
    assert E.to_mxn(1000, "Dólar Americano", S) == pytest.approx(16_500)
    assert E.to_mxn(1000, "Peso Mexicano", S) == 1000
    assert E.to_mxn(900, "Euros", S, usd_equivalent=1000) == pytest.approx(16_500)
    with pytest.raises(E.SettingsError):
        E.to_mxn(900, "Euros", S)


def test_every_spelling_of_a_currency_is_read_the_same_way():
    """A peso label must never be taken for a foreign currency because of its spelling."""
    for label in ("Peso", "pesos mexicanos", "M.N.", "MN", "MXN", " Peso Mexicano "):
        assert E.to_mxn(1000, label, S, usd_equivalent=50) == 1000
    for label in ("Dolares", "US$", "dólares americanos", "USD", "DLS"):
        assert E.to_mxn(1000, label, S, usd_equivalent=50) == pytest.approx(16_500)
    for unknown in ("", None, "Libra", "CAD"):
        with pytest.raises(E.SettingsError):
            E.to_mxn(1000, unknown, S, usd_equivalent=50)


def test_provision_is_taken_off_the_margin():
    r = E.calc_line(E.Line("x", billed=100_000, cost=60_000, provision=10_000), 0.6, S)
    assert r.gross_margin == 30_000 and r.margin == pytest.approx(900)
    assert E.calc_line(E.Line("x", billed=100_000, cost=60_000), 0.6, S).margin == pytest.approx(1200)


def test_distribution_on_another_basis_and_with_other_shares():
    m = _month()
    d = m.distribution(S, basis=m.total)                           # as if every invoice were collected
    assert d["sales_pool"] == pytest.approx(8822.16 * 2 / 3) and sum(d["people"].values()) == pytest.approx(8822.16)
    other = E.Settings(sales_share=0.5, admin_share=0.5, sales_people=(("a", 0.25), ("b", 0.75)),
                       admin_people=(("c", 1.0),))
    d = m.distribution(other, basis=1000)
    assert d["people"] == {"a": pytest.approx(125), "b": pytest.approx(375), "c": pytest.approx(500)}
    assert d["control"] == pytest.approx(0)


def test_a_budget_given_for_the_run_replaces_the_one_in_settings():
    s = E.Settings(budgets={"m": 1000.0})
    assert E.calc_month("m", [], 500, s).reach == pytest.approx(0.3)
    assert E.calc_month("m", [], 500, s, budget=250).reach == pytest.approx(1.2)


def test_settings_that_are_not_numbers_or_not_percentages_are_refused():
    nan, inf = float("nan"), float("inf")
    for bad in (dict(rate=nan), dict(rate=inf), dict(rate=5.0), dict(target_margin=0), dict(target_margin=nan),
                dict(target_margin=30), dict(usd_mxn=inf), dict(budgets={"m": nan}), dict(budgets={"m": "1000"}),
                dict(sales_people=(("a", nan), ("b", 0.5))),
                dict(sales_people=(p for p in (("a", 0.5), ("b", 0.5)))),     # used up after one reading
                dict(admin_people=("Lupita", 1.0))):
        with pytest.raises(E.SettingsError):
            E.Settings(**bad).check()
    with pytest.raises(E.SettingsError):
        E.sales_reach(nan, 1000, S)
    with pytest.raises(E.SettingsError):
        E.sales_reach(1000, nan, S)


def test_an_amount_that_is_not_a_number_stops_the_line():
    for bad in (dict(billed=float("nan")), dict(billed=1000, cost=float("inf")),
                dict(billed=1000, provision=float("nan"))):
        with pytest.raises(ValueError):
            E.calc_line(E.Line("x", **bad), 0.6, S)
