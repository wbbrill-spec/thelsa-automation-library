"""
engine.py — the commission calculation.

The reference is Finance's own workbook, COMMISSION CALCULATION 2026.xlsx
(Rogelio's template, sent by Lupita on 28 Sep 2026). Every formula here is the
workbook's formula, cell for cell, and test_commissions_engine.py reproduces
every row, total and distribution of that workbook. If a rule here and the
workbook ever disagree, the workbook wins and the tests say so.

    sales reach      J = (bookings in the month / monthly budget) x invoicing weight
    gross margin     N = billed - actual cost - provision
    margin %         O = N / billed          (billed = 0  ->  -100 %)
    achievement      R = O / target margin
    invoicing part   Q = billed x rate x J
    margin part      S = billed x rate x margin weight x R
    discipline part  T = billed x rate x discipline weight
    total            U = Q + S + T

Three things that are easy to "simplify" and must not be:

  * The margin part is NOT "gross margin x rate x 30 %". The 30 % appears twice
    in the workbook, once as the weight and once as the target margin, and the
    two cancel: file 111000 pays 765.00, not 229.50. Weight and target are
    separate settings here so that changing one cannot silently change the other.
  * A line with nothing billed pays nothing, even when it carries cost. The
    margin part is multiplied by billed, so cost with no billing gives zero, not
    a negative (workbook row 110948).
  * There is no cap. Bookings above budget pay above 100 %, and a file that
    loses money gives a negative margin part that reduces its total (confirmed
    by Lupita, 7 Oct 2026).

Money is kept at full precision and rounded only for display, as Excel does.
"""
from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, field

_TOL = 1e-9

# Every way Finance writes a currency (accents and case ignored). One list for
# the whole report, so a label is never read one way here and another there.
_CCY = {"USD": "USD", "US$": "USD", "DLS": "USD", "DOLAR": "USD", "DOLARES": "USD",
        "DOLAR AMERICANO": "USD", "DOLARES AMERICANOS": "USD", "US DOLLAR": "USD",
        "MXN": "MXN", "MN": "MXN", "M.N.": "MXN", "PESO": "MXN", "PESOS": "MXN",
        "PESO MEXICANO": "MXN", "PESOS MEXICANOS": "MXN",
        "EUR": "EUR", "EURO": "EUR", "EUROS": "EUR"}


def currency_code(label) -> str:
    """'USD', 'MXN' or 'EUR' for a currency as Finance writes it; '' if not known."""
    t = "".join(c for c in unicodedata.normalize("NFKD", str(label or "")) if not unicodedata.combining(c))
    return _CCY.get(t.upper().strip(), "")


def _finite(*values) -> bool:
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)


class SettingsError(ValueError):
    """The settings do not add up. Nothing is calculated on bad settings."""


@dataclass(frozen=True)
class Settings:
    rate: float = 0.03                     # global commission  (workbook Q2)
    w_invoicing: float = 0.60              # D14
    w_margin: float = 0.30                 # D15, as the weight
    w_discipline: float = 0.10             # D16
    target_margin: float = 0.30            # D15, as the margin a file should make
    usd_mxn: float = 16.5                  # internal rate Rogelio gave TMS (see sales_fx)
    # How a sale invoiced in dollars becomes pesos:
    #   "spot"      the rate of the day of the invoice, i.e. the peso amount
    #               Finance books. Sales and costs are then on the same footing
    #               and the margin is the one in Finance's report (Bill, 8 Oct 2026:
    #               "16.5 will throw off the profitability calculations").
    #   "internal"  the fixed rate usd_mxn (Lupita's answer of 7 Oct 2026).
    # usd_mxn is still used for bookings entered in dollars, which have no invoice.
    sales_fx: str = "spot"
    sales_share: float = 2 / 3             # Y = U/3*2
    admin_share: float = 1 / 3             # Z = U/3*1
    sales_people: tuple = (("KAR 1 Pablo", 0.5), ("KAR 2 Edwin", 0.5))
    admin_people: tuple = (
        ("Buyer and Cost Manager (Lupita)", 0.358),
        ("Logistics Supervisor (Sara Reyes)", 0.21),
        ("MC 1 Stephanie", 0.108),
        ("MC 2 Elizabeth", 0.108),
        ("Adm. 1 Fernanda", 0.108),
        ("Adm. 2 Monica", 0.108),
    )
    # month ("2026-05") -> booking budget in MXN
    budgets: dict = field(default_factory=dict)
    # The margin part is paid on actual cost, so a file with no cost posted yet
    # has no margin part to pay (Bill, 7 Oct 2026). Off = calculate regardless.
    margin_waits_for_cost: bool = True

    def check(self) -> "Settings":
        def near(a, b):
            return abs(a - b) < 1e-6
        for group in (self.sales_people, self.admin_people):
            if not isinstance(group, (tuple, list)) or any(
                    not isinstance(x, (tuple, list)) or len(x) != 2 for x in group):
                raise SettingsError("each distribution must be a list of (name, share)")
        numbers = [self.rate, self.w_invoicing, self.w_margin, self.w_discipline, self.target_margin,
                   self.usd_mxn, self.sales_share, self.admin_share]
        numbers += [p for _, p in list(self.sales_people) + list(self.admin_people)]
        numbers += list(self.budgets.values())
        if not _finite(*numbers):
            raise SettingsError("every rate, weight, share and budget must be a number")
        if self.sales_fx not in ("spot", "internal"):
            raise SettingsError('sales_fx must be "spot" or "internal"')
        if self.rate > 1 or self.target_margin > 1:
            raise SettingsError("the commission rate and the target margin are percentages, at most 100 %")
        if not near(self.w_invoicing + self.w_margin + self.w_discipline, 1.0):
            raise SettingsError("the three weights must add up to 100 %")
        if not near(self.sales_share + self.admin_share, 1.0):
            raise SettingsError("the Sales and Admin shares must add up to 100 %")
        if not near(sum(p for _, p in self.sales_people), 1.0):
            raise SettingsError("the Sales distribution must add up to 100 %")
        if not near(sum(p for _, p in self.admin_people), 1.0):
            raise SettingsError("the Admin distribution must add up to 100 %")
        if self.rate <= 0 or self.target_margin <= 0 or self.usd_mxn <= 0:
            raise SettingsError("rate, target margin and exchange rate must be above zero")
        if min(self.w_invoicing, self.w_margin, self.w_discipline, self.sales_share, self.admin_share) < 0:
            raise SettingsError("a weight or a share cannot be negative")
        people = list(self.sales_people) + list(self.admin_people)
        if any(p < 0 for _, p in people):
            raise SettingsError("a person's share cannot be negative")
        names = [str(n).strip().lower() for n, _ in people]
        if any(not n for n in names):
            raise SettingsError("every person in the distribution needs a name")
        if len(set(names)) != len(names):
            # two lines with one name would be paid as one line: money would vanish
            raise SettingsError("the same name appears twice in the distribution")
        return self


# 2026 booking budget, MXN, as sent by Lupita on 7 Oct 2026. Corporate + Private
# only: the Diplomatic line is not part of this report.
BUDGET_2026 = {
    "2026-01": 461_225.74 + 560_914.82, "2026-02": 791_582.67 + 894_320.44,
    "2026-03": 1_093_662.68 + 1_042_447.00, "2026-04": 1_135_452.02 + 1_029_296.29,
    "2026-05": 928_135.00 + 1_209_654.17, "2026-06": 1_903_929.52 + 1_650_125.28,
    "2026-07": 3_052_488.88 + 1_698_174.55, "2026-08": 2_434_877.76 + 1_955_911.89,
    "2026-09": 2_027_284.77 + 1_184_801.00, "2026-10": 1_063_245.86 + 1_101_074.74,
    "2026-11": 1_241_324.74 + 945_316.97, "2026-12": 1_371_324.75 + 903_428.55,
}


@dataclass(frozen=True)
class Line:
    """One row of the report: a job (lot letter included) in an invoice month."""
    job: str
    billed: float = 0.0            # net of credit notes, MXN
    cost: float = 0.0              # actual cost, MXN
    provision: float = 0.0
    paid: bool | None = None       # True collected, False pending, None unknown
    cost_posted: bool = True       # False = no cost in the books yet
    note: str = ""


@dataclass(frozen=True)
class LineResult:
    line: Line
    gross_margin: float
    margin_pct: float | None       # None when nothing is billed and nothing spent
    invoicing: float
    margin: float
    discipline: float
    margin_pending: bool           # margin part withheld: cost not posted

    @property
    def total(self) -> float:
        return self.invoicing + self.margin + self.discipline

    @property
    def payable(self) -> bool:
        """Commission is paid only once the invoice has been collected."""
        return self.line.paid is True


def sales_reach(bookings: float, budget: float, s: Settings) -> float:
    """Workbook J: budget achievement times the invoicing weight. No cap."""
    if not budget or not _finite(budget) or budget <= 0:
        raise SettingsError("no booking budget for this month")
    if not _finite(bookings):
        raise SettingsError("bookings for the month are not a number")
    return (bookings / budget) * s.w_invoicing


def calc_line(line: Line, reach: float, s: Settings) -> LineResult:
    k = float(line.billed or 0.0)
    cost = float(line.cost or 0.0)
    prov = float(line.provision or 0.0)
    if not _finite(k, cost, prov, reach):
        raise ValueError(f"job {line.job}: an amount on the line is not a number")
    gm = k - cost - prov                                        # N
    if abs(k) > _TOL:
        pct = gm / k                                            # O
    elif abs(gm) > _TOL:
        pct = -1.0                                              # O8: IF(K=0,-100%,…)
    else:
        pct = None
    base = k * s.rate                                           # K x Q2
    invoicing = base * reach                                    # Q
    discipline = base * s.w_discipline                          # T
    pending = bool(s.margin_waits_for_cost and not line.cost_posted and abs(k) > _TOL)
    if pending or pct is None:
        margin = 0.0
    else:
        margin = base * s.w_margin * (pct / s.target_margin)    # S = K*Q2*D15*R
    # "+ 0.0" turns a negative zero into zero, so no line ever prints as -0.00
    return LineResult(line, gm + 0.0, pct, invoicing + 0.0, margin + 0.0, discipline + 0.0, pending)


@dataclass(frozen=True)
class MonthResult:
    month: str
    bookings: float
    budget: float
    reach: float
    lines: tuple

    def _sum(self, attr, only_payable=False):
        return sum(getattr(r, attr) for r in self.lines if r.payable or not only_payable)

    @property
    def billed(self):
        return sum(r.line.billed or 0.0 for r in self.lines)

    @property
    def total(self):
        """Everything calculated, collected or not (workbook U11)."""
        return self._sum("total")

    @property
    def payable_total(self):
        """Only the lines whose invoice is collected (workbook AA11)."""
        return self._sum("total", only_payable=True)

    @property
    def held_total(self):
        return self.total - self.payable_total

    def distribution(self, s: Settings, basis: float | None = None) -> dict:
        """Who gets what. `basis` defaults to the collected total; pass
        `self.total` to see the split if every invoice were collected."""
        s.check()
        u = self.payable_total if basis is None else basis
        sales, admin = u * s.sales_share, u * s.admin_share
        out = {"sales_pool": sales, "admin_pool": admin, "people": {}}
        for name, share in s.sales_people:
            out["people"][name] = sales * share + 0.0
        for name, share in s.admin_people:
            out["people"][name] = admin * share + 0.0
        # Workbook AA22, must be 0. Taken from what each person actually gets,
        # so a peso that reaches nobody shows here.
        out["control"] = sum(out["people"].values()) - u + 0.0
        return out


def calc_month(month: str, lines, bookings: float, s: Settings,
               budget: float | None = None) -> MonthResult:
    s.check()
    b = s.budgets.get(month) if budget is None else budget
    reach = sales_reach(bookings, b, s)
    return MonthResult(month, bookings, b, reach, tuple(calc_line(x, reach, s) for x in lines))


def to_mxn(amount: float, currency: str, s: Settings, usd_equivalent: float | None = None) -> float:
    """An amount with no invoice behind it (a booking, a quoted price) in pesos.
    Pesos stay as they are; US dollars use the internal rate; any other
    currency uses a US-dollar equivalent at the internal rate. Invoiced sales
    do not come through here: see finance.billed_for_commission."""
    c = currency_code(currency)
    if c == "MXN":
        return float(amount)
    if c == "USD":
        return float(amount) * s.usd_mxn
    if not c:
        raise SettingsError("the currency is not one this report knows")
    if usd_equivalent is None:
        raise SettingsError(f"no US-dollar equivalent to convert {c} to pesos")
    return float(usd_equivalent) * s.usd_mxn
