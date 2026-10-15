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
    units = T.with_total()
    assert units["total"]["ytd"]["net"][0] == 295975 - 98610
    assert units["total"]["month"]["total_rev"][0] == 244738 + 6757


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
