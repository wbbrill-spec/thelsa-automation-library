"""TIM Executive Dashboard: the embedded close must tie out line by line."""
import json

import pytest

import tim_exec as T

ADD = {
    "total_rev": ["direct_rev", "other_rev"],
    "total_cos": ["direct_cost", "op_exp", "admin_fac", "other_cost"],
    "total_exp": ["admin_exp", "selling_exp", "other_exp", "corp_exp"],
}


def _cases():
    for u, pers in T.CLOSE["units"].items():
        for per, lines in pers.items():
            for col in range(3):
                yield u, per, col, lines


@pytest.mark.parametrize("u,per,col,L", list(_cases()))
def test_statement_ties_out(u, per, col, L):
    v = lambda k: L[k][col]
    assert set(L) == set(T.KEYS)
    for tot, parts in ADD.items():
        assert abs(v(tot) - sum(v(p) for p in parts)) <= 2, (u, per, col, tot)
    assert abs(v("direct_margin") - (v("total_rev") - v("total_cos"))) <= 2
    assert abs(v("op_profit") - (v("direct_margin") - v("total_exp"))) <= 2
    assert abs(v("net") - (v("op_profit") - v("cif") - v("taxes"))) <= 2


def test_total_is_sum_of_units():
    units, C = T.with_total(), T.CLOSE["units"]
    for per in ("month", "ytd"):
        for k in ("total_rev", "net"):
            for i in range(3):
                assert units["total"][per][k][i] == C["cons"][per][k][i] + C["imp"][per][k][i]


def test_october_statement_values():
    # spot checks against Rogelio's Est Financ TIM 2610 (12 Nov 2026)
    C = T.CLOSE["units"]
    assert T.CLOSE["period_en"] == "October 2026"
    assert C["cons"]["month"]["net"] == [87845, 13582, 6104]
    assert C["cons"]["ytd"]["net"] == [383820, 180584, 164665]
    assert C["imp"]["month"]["total_rev"] == [25949, 40625, 5883]
    assert C["imp"]["ytd"]["net"] == [-98277, -75612, -80101]


def test_page_embeds_data():
    page = T.render_page()
    assert "/*__DATA__*/null" not in page
    blob = page.split("var D = ", 1)[1].split(";\n", 1)[0]
    data = json.loads(blob)
    assert set(data["units"]) == {"total", "cons", "imp"}


def _client():
    from flask import Flask
    app = Flask(__name__)
    app.secret_key = "x"
    app.add_url_rule("/login", "login", lambda: "login")
    app.register_blueprint(T.tim_exec_bp)
    return app.test_client()


def _as(c, email):
    with c.session_transaction() as s:
        s["user_email"] = email


@pytest.fixture(autouse=True)
def _no_override(monkeypatch):
    monkeypatch.delenv("TIM_EXEC_USERS", raising=False)


def test_route_requires_login():
    assert _client().get("/tim-exec").status_code == 302


@pytest.mark.parametrize("email", T.DEFAULT_USERS)
def test_each_named_person_can_open_it(email):
    c = _client()
    _as(c, "  " + email.upper() + " ")
    r = c.get("/tim-exec")
    assert r.status_code == 200 and b"TIM Executive Dashboard" in r.data


@pytest.mark.parametrize("email", ["mariotorres@thelsa.com", "s.garzapadilla@thelsa.com",
                                   "maria.gonzalez@thelsa.com", "someone@thelsa.com", "x"])
def test_anyone_else_gets_not_found(email):
    c = _client()
    _as(c, email)
    r = c.get("/tim-exec")
    assert r.status_code == 404 and b"295" not in r.data


def test_default_list_is_the_seven_people():
    assert set(T.DEFAULT_USERS) == {
        "gustavogonzalez@thelsa.com", "erickamartinez@thelsa.com", "dianaaguirre@thelsa.com",
        "fernandamora@thelsa.com", "rogeliofranco@thelsa.com", "sergiogarza@thelsa.com",
        "bbrill@thelsa.com", "bill.brill@inflectionpointnow.com"}


def test_render_setting_overrides_the_list(monkeypatch):
    monkeypatch.setenv("TIM_EXEC_USERS", "new.person@thelsa.com; other@thelsa.com")
    c = _client()
    _as(c, "new.person@thelsa.com")
    assert c.get("/tim-exec").status_code == 200
    _as(c, "gustavogonzalez@thelsa.com")
    assert c.get("/tim-exec").status_code == 404


def _home():
    from pathlib import Path
    return Path(__file__).with_name("index.html").read_text()


def test_home_tile_only_for_named_people():
    home = _home()
    assert 'href="/tim-exec"' in home
    for email in T.DEFAULT_USERS:
        assert 'href="/tim-exec"' in T.home_tile(home, email)
    for email in ("mariotorres@thelsa.com", "", None):
        page = T.home_tile(home, email)
        assert "/tim-exec" not in page and "TIM Executive" not in page
        assert "TIM_EXEC_TILE" not in page
        assert "TMS Executive Dashboard" in page
        assert page.count("<div") == page.count("</div>")


# ── Latest revenue (sales report) ──────────────────────────────────────────────
@pytest.mark.parametrize("per", ["month", "ytd"])
@pytest.mark.parametrize("cur", ["usd", "mxn"])
def test_sales_lines_tie_to_reports_tim_total(per, cur):
    # the sales report's own lines add up to its own TIM total
    L, stated = T.SALES["lines"], T.SALES["stated_tim"][per][cur]
    for i in range(2):
        assert abs(sum(L[m][per][cur][i] for m in ("mud", "com", "imp")) - stated[i]) <= 0.15, (per, cur, i)


def test_unit_totals_come_from_statement_when_it_covers_the_month():
    assert T.statement_covers_sales()
    u, W = T.sales_units(), T.with_total()
    for unit in ("total", "cons", "imp"):
        for per in ("month", "ytd"):
            rev = W[unit][per]["total_rev"]
            assert u[unit][per]["usd"] == [round(rev[0] / 1000, 2), round(rev[1] / 1000, 2)]
            assert u[unit][per]["py_usd"] == round(rev[2] / 1000, 2)
    # October itself: statement and sales report agree (to rounding)
    for unit, lines in (("cons", ("mud", "com")), ("imp", ("imp",))):
        sales_month = sum(T.SALES["lines"][m]["month"]["usd"][0] for m in lines)
        assert abs(u[unit]["month"]["usd"][0] - sales_month) <= 0.1, unit


def test_sales_budget_ties_to_close_budget():
    # the same 2026 plan in both reports, month and year to date
    for unit, lines in (("cons", ("mud", "com")), ("imp", ("imp",))):
        for per in ("month", "ytd"):
            sales_bp = sum(T.SALES["lines"][m][per]["usd"][1] for m in lines)
            assert abs(sales_bp - T.CLOSE["units"][unit][per]["total_rev"][1] / 1000) <= 0.2, (unit, per)


def test_page_shows_both_periods():
    page = T.render_page()
    blob = json.loads(page.split("var D = ", 1)[1].split(";\n", 1)[0])
    assert blob["sales"]["period_en"] == "October 2026"
    assert blob["meta"]["period_en"] == "October 2026"
    assert blob["sales"]["official"] is True
    assert 'id="salesKpis"' in page and 'id="kpis"' in page


# ── Scorecard and 2026 budget ──────────────────────────────────────────────────
def _sc(k):
    return next(l["v"] for l in T.SCORECARD["lines"] if l["k"] == k)


def test_scorecard_adds_up():
    st = T.SCORECARD["stated"]
    for i in range(3):
        mud = sum(_sc(k)[i] for k in ("agentes", "texas", "resto"))
        assert abs(mud - st["Mudanzas"][i]) <= 0.02
        assert abs(st["Mudanzas"][i] + _sc("comercial")[i] + _sc("imp")[i] - st["Total"][i]) <= 0.02


def test_scorecard_matches_sales_report():
    L = T.SALES["lines"]
    assert abs(T.SCORECARD["stated"]["Mudanzas"][0] - L["mud"]["month"]["usd"][0]) <= 0.1
    assert abs(_sc("comercial")[0] - L["com"]["month"]["usd"][0]) <= 0.1
    assert abs(_sc("imp")[0] - L["imp"]["month"]["usd"][0]) <= 0.1
    assert abs(T.SCORECARD["stated"]["Total"][1] - T.SALES["stated_tim"]["month"]["usd"][1]) <= 0.1


def test_budget_ties_to_every_other_source():
    b, bu = T.BUDGET_2026, T.budget_by_unit()
    assert abs(sum(bu["total"]) - b["stated_total_year"]) <= 0.05
    # October budget = scorecard budget, line by line
    assert abs(b["agents"][9] / 1000 - _sc("agentes")[1]) <= 0.01
    assert abs(b["texas"][9] / 1000 - _sc("texas")[1]) <= 0.01
    assert abs(b["resto_usa"][9] / 1000 - _sc("resto")[1]) <= 0.01
    assert abs(b["commercial"][9] / 1000 - _sc("comercial")[1]) <= 0.01
    assert abs(b["furniture"][9] / 1000 - _sc("imp")[1]) <= 0.01
    # January–<statement month> budget = the statement's YTD budget, per unit
    m = T.CLOSE["month"]
    for unit in ("cons", "imp"):
        assert abs(sum(bu[unit][:m]) - T.CLOSE["units"][unit]["ytd"]["total_rev"][1]) <= 1, unit
        assert abs(bu[unit][m - 1] - T.CLOSE["units"][unit]["month"]["total_rev"][1]) <= 1, unit
    # January–October budget = the sales report's YTD budget
    assert abs(sum(bu["total"][:10]) / 1000 - T.SALES["stated_tim"]["ytd"]["usd"][1]) <= 0.1


def test_full_year_math():
    F = T.full_year()
    assert F["total"]["year"] == 2489.3 and F["total"]["rest_budget"] == 340.0
    assert F["total"]["actual"] == 2169.61 and F["total"]["needed"] == 319.7   # official statement YTD
    assert abs(F["cons"]["needed"] + F["imp"]["needed"] - F["total"]["needed"]) <= 0.2
