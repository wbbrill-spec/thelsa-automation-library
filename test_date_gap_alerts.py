"""Missing Moveware key dates: which files count, what each coordinator is told,
and that nothing goes out unless switched on, on a full read, once a day."""
import datetime as dt

import pytest

import date_gap_alerts as dga
import faim_web as fw

TODAY = dt.date(2026, 10, 16)


def rec(job, typ="EXP", status="W", pack=None, delivery=None, coord="Stephanie", email="stephaniebarraza@thelsa.com",
        booked=None):
    return {"job": job, "status": status, "job_type": typ, "pack": pack, "delivery": delivery,
            "mw_coordinator": coord, "coordinator_email": email, "booked": booked, "survey": "2026-09-01"}


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    for k in ("DATE_ALERTS_ENABLED", "DATE_ALERTS_SEND", "DATE_ALERTS_AUTO", "DRY_RUN", "DATE_ALERT_CC",
              "AUDIT_COORDINATOR_EMAILS", "DATE_ALERTS_HOUR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(dga, "_state_path", lambda: str(tmp_path / "state.json"))


def gaps(files, sit=None):
    return fw.faim_snapshot(files, today=TODAY, sit=sit or {})["date_gaps"]


def test_which_files_have_a_gap_follows_the_agreed_rules():
    g = gaps([
        rec("111001", "EXP", pack="2026-09-10", delivery="2026-10-01"),          # complete
        rec("111002", "EXP", pack="2026-09-10"),                                 # no delivery
        rec("111003", "IMA", delivery="2026-09-20"),                             # agent import: no pack due
        rec("111004", "EXA", pack="2026-09-15"),                                 # agent export: no delivery due
        rec("111005", "IMP", delivery="2026-09-20"),                             # TMS import: pack due
        rec("111006", "EXP", booked="2026-09-01", coord="Elizabeth", email=""),  # booked, no dates at all
        rec("111007", "EXP", status="P", booked="2026-09-01"),                   # quote, not booked: not chased
        rec("111008", "EXP", booked="2024-01-05"),                               # won long ago: outside the window
        rec("111009", "EXP", status="C", pack="2026-09-10"),                     # cancelled
    ])
    by = {x["file"]: x for x in g}
    assert set(by) == {"111002", "111005", "111006"}
    assert by["111002"]["missing"] == ["delivery"] and by["111005"]["missing"] == ["pack"]
    assert by["111006"]["no_dates"] and by["111006"]["missing"] == ["pack", "delivery"]


def test_sit_plan_day_is_shown_but_stays_a_gap():
    g = gaps([rec("111002", "EXP", pack="2026-09-10")], sit={"111002": {"delivery": dt.date(2026, 10, 2)}})
    assert g[0]["missing"] == ["delivery"] and g[0]["sit_delivery"] == "2026-10-02"
    body = dga.build(g)["coordinators"][0]["body"]
    assert "Plan de Viajes: 2026-10-02" in body and "SIT plan: 2026-10-02" in body


def test_one_email_per_coordinator_cc_key_users_and_a_summary_for_bill():
    g = gaps([rec("111002", "EXP", pack="2026-09-10"), rec("111005", "IMP", delivery="2026-09-20"),
              rec("111006", "EXP", booked="2026-09-01", coord="Elizabeth", email="")])
    plan = dga.build(g)
    by = {a["coordinator"]: a for a in plan["coordinators"]}
    assert by["Stephanie"]["to"] == "stephaniebarraza@thelsa.com" and by["Stephanie"]["files"] == 2
    assert by["Stephanie"]["cc"] == ["maria.gonzalez@thelsa.com", "sarareyes@thelsa.com"]
    assert not by["Elizabeth"]["resolved"] and "asignar manualmente" in by["Elizabeth"]["body"]
    assert "SIN NINGUNA fecha clave" in by["Elizabeth"]["body"] and "sin excepciones" in by["Stephanie"]["body"].lower()
    s = plan["summary"]
    assert s["to"] == "bill.brill@inflectionpointnow.com" and s["files"] == 3 and "Stephanie: 2" in s["body"]


class Mailer:
    sent, drafts = [], []

    def __init__(self, mailbox=None):
        pass

    def send_mail(self, to, subject, body, cc=None):
        Mailer.sent.append(to)

    def create_draft(self, to, subject, body, cc=None, folder=None):
        Mailer.drafts.append(to)
        return {"id": "x"}


@pytest.fixture
def mailer(monkeypatch):
    import engine.mailer as m
    Mailer.sent, Mailer.drafts = [], []
    monkeypatch.setattr(m, "GraphMailer", Mailer)
    return Mailer


def test_nothing_goes_out_unless_on_live_complete_and_once_a_day(monkeypatch, mailer):
    g = gaps([rec("111002", "EXP", pack="2026-09-10")])
    now = dt.datetime(2026, 10, 19, 9, 0, tzinfo=dga.MEXICO)
    assert dga.dispatch(g, live=True, complete=True, now=now)["skipped_reason"].startswith("DATE_ALERTS_ENABLED")
    monkeypatch.setenv("DATE_ALERTS_ENABLED", "1")
    assert dga.dispatch(g, live=False, complete=True, now=now)["skipped_reason"] == "not live Moveware data"
    assert "still being read" in dga.dispatch(g, live=True, complete=False, now=now)["skipped_reason"]
    st = dga.dispatch(g, live=True, complete=True, now=now)
    assert st["mode"] == "draft" and len(mailer.drafts) == 2 and not mailer.sent        # coordinator + summary
    assert dga.dispatch(g, live=True, complete=True, now=now)["skipped_reason"] == "already alerted today"
    monkeypatch.setenv("DATE_ALERTS_SEND", "1")
    st = dga.dispatch(g, live=True, complete=True, now=now + dt.timedelta(days=1))
    assert st["mode"] == "send" and len(mailer.sent) == 2


def test_the_keeper_runs_it_once_a_weekday_from_the_set_hour(monkeypatch):
    mon9 = dt.datetime(2026, 10, 19, 9, 0, tzinfo=dga.MEXICO)
    assert not dga.due_now(mon9)
    monkeypatch.setenv("DATE_ALERTS_AUTO", "1")
    assert dga.due_now(mon9) and not dga.due_now(mon9.replace(hour=7))
    assert not dga.due_now(dt.datetime(2026, 10, 18, 9, 0, tzinfo=dga.MEXICO))          # Sunday
    dga._save_state({"(summary)": "2026-10-19"})
    assert not dga.due_now(mon9)


def test_a_date_typed_in_the_wrong_year_is_listed_to_be_corrected():
    g = gaps([rec("110801", "IMP", pack="2026-08-30", delivery="2020-09-02", booked="2026-08-01"),
              rec("110802", "IMP", pack="2026-08-30", delivery="2026-09-02", booked="2026-08-01")])
    assert [x["file"] for x in g] == ["110801"] and g[0]["wrong_year"] == ["delivery"]
    body = dga.build(g)["coordinators"][0]["body"]
    assert "año equivocado" in body and "2020-09-02" in body
