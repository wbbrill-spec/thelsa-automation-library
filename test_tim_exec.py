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


def test_route_requires_login():
    from flask import Flask
    app = Flask(__name__)
    app.secret_key = "x"
    app.add_url_rule("/login", "login", lambda: "login")
    app.register_blueprint(T.tim_exec_bp)
    c = app.test_client()
    assert c.get("/tim-exec").status_code == 302
    with c.session_transaction() as s:
        s["user_email"] = "bbrill@thelsa.com"
    r = c.get("/tim-exec")
    assert r.status_code == 200 and b"TIM Executive Dashboard" in r.data
