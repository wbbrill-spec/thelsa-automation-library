"""The settings screen: only an admin saves, typed values are read strictly,
nothing that does not add up is saved, every change is kept in the history,
and the report uses what is saved."""
import pytest

from commissions import engine as E
from commissions import settings_store as SS
from commissions import store
from commissions import web as W
from test_commissions_web import LUPITA, _lines_of, client, login, upload, xlsx  # noqa: F401

BILL = "bbrill@thelsa.com"


def form(**over):
    s = SS.defaults()
    f = {"csrf": "tok", "rate": "3", "w_invoicing": "60", "w_margin": "30", "w_discipline": "10", "target_margin": "30",
         "sales_share": str(round(s.sales_share * 100, 6)), "usd_mxn": "16.50"}
    for g, people in (("sales", s.sales_people), ("admin", s.admin_people)):
        for i, (n, p) in enumerate(people):
            f[f"{g}_name_{i}"], f[f"{g}_share_{i}"] = n, f"{p * 100:g}"
    for m, v in s.budgets.items():
        f[f"bud_{m}"] = f"{v:,.2f}"
    f.update(over)
    return f


def test_the_defaults_round_trip_through_the_form():
    s = SS.parse(form())
    d = SS.defaults()
    assert SS.changes(d, s) == [] and s == d.check()
    assert SS.from_doc(SS.to_doc(s)) == s and SS.from_doc(None) == d


@pytest.mark.parametrize("over", [{"rate": "3,5"}, {"rate": "abc"}, {"w_margin": "40"}, {"usd_mxn": "0"},
                                  {"usd_mxn": "16,50"}, {"bud_2026-06": "1.234.567,00"}, {"bud_2026-06": "-5"},
                                  {"sales_share_1": "40"}, {"admin_name_0": ""}, {"admin_name_1": "MC 1 Stephanie"},
                                  {"rate": "150"}])
def test_anything_not_plainly_typed_or_not_adding_up_is_refused(over):
    with pytest.raises(SS.SettingsFormError) as e:
        SS.parse(form(**over))
    assert e.value.es


def test_lupita_rogelio_and_admins_save_others_cannot_and_the_report_uses_it(client, tmp_path):
    login(client)
    upload(client, xlsx(tmp_path))
    before = _lines_of(client)["total_all"]["total"]
    login(client, "mariotorres@thelsa.com")
    page = client.get("/commissions").data.decode()
    assert 'id="settings"' in page and "Save settings" not in page          # Mario sees, cannot save
    client.post("/commissions/settings", data=form(rate="4"))
    assert store.get("settings")[0] is None
    for who in (LUPITA, "rogeliofranco@thelsa.com"):
        login(client, who)
        assert "Save settings" in client.get("/commissions").data.decode()
        client.post("/commissions/settings", data=form(rate="3.5"))
        assert W.settings().rate == pytest.approx(0.035) and store.get("settings_history")[0][-1]["by"] == who
        client.post("/commissions/settings", data={"csrf": "tok", "action": "reset"})
    login(client, BILL)
    assert "Save settings" in client.get("/commissions").data.decode()
    assert client.post("/commissions/settings", data=form(rate="4", csrf="bad")).status_code == 400
    client.post("/commissions/settings", data=form(rate="4"))
    assert W.settings().rate == pytest.approx(0.04)
    assert _lines_of(client)["total_all"]["total"] == pytest.approx(before * 4 / 3)
    hist = store.get("settings_history")[0]
    assert hist[-1]["by"] == BILL and hist[-1]["changes"] == ["rate: 0.03 -> 0.04"] and len(hist) == 5
    client.post("/commissions/settings", data=form(w_margin="40"))          # refused: weights add to 110
    assert W.settings().rate == pytest.approx(0.04) and "Not saved" in client.get("/commissions").data.decode()
    client.post("/commissions/settings", data={"csrf": "tok", "action": "reset"})
    assert W.settings() == SS.defaults() and _lines_of(client)["total_all"]["total"] == pytest.approx(before)


def test_a_2027_budget_can_be_added_and_a_month_without_one_is_not_calculated():
    s = SS.parse(form(**{"bud_2027-01": "2,000,000.00", "bud_2026-06": ""}))
    assert s.budgets["2027-01"] == 2_000_000.0 and "2026-06" not in s.budgets


def test_stored_settings_that_do_not_add_up_are_not_used(client):
    store.put("settings", dict(SS.to_doc(SS.defaults()), w_margin=0.5))
    assert W.settings() == SS.defaults()
    assert W._settings_state()["bad"] is True


def test_renamed_people_flow_into_the_distribution():
    s = SS.parse(form(sales_name_0="KAR 1 New Person"))
    assert s.sales_people[0] == ("KAR 1 New Person", 0.5)
    assert isinstance(s, E.Settings)


def test_equal_thirds_are_accepted_and_the_dollar_rate_is_kept_exactly():
    f = form()
    for i in range(6):
        f.pop(f"admin_name_{i}", None), f.pop(f"admin_share_{i}", None)
    f.update({"admin_name_0": "A", "admin_share_0": "33.3333", "admin_name_1": "B", "admin_share_1": "33.3333",
              "admin_name_2": "C", "admin_share_2": "33.3333"})
    s = SS.parse(f)
    assert sum(p for _, p in s.admin_people) == pytest.approx(1.0, abs=1e-12)
    cur = SS.parse(form(usd_mxn="16.5025"))
    assert SS.parse(form(usd_mxn="16.5025", rate="4"), cur).usd_mxn == 16.5025
