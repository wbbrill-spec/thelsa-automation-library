"""Offline tests for the per-person alerts (crossborder/alerts.py)."""
import datetime as dt

import pytest

from crossborder import alerts
from crossborder.models import Hub, Shipment, Source, Stage

TODAY = dt.date(2026, 9, 9)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("CB_ALERT_EMAILS", "CB_ALERT_FOLDER_OWNERS", "CB_ALERT_DEFAULT_OWNER",
              "CB_ALERT_TIM_OWNER", "CB_ALERT_TMS_OWNER",
              "CB_ALERT_CC", "CB_ALERT_FALLBACK", "ALERT_EMAIL", "CB_ALERTS_ENABLED",
              "CB_ALERT_REPEAT_HOURS", "PLAN_WINDOW_RISK_DAYS", "DRY_RUN"):
        monkeypatch.delenv(k, raising=False)


def tim(i, customer, *, flags=(), assignees=(), agent="UHaul", stage=Stage.DOCS_PENDING,
        green_light=None, days_since=None):
    return Shipment(id=f"TIM:{i}", source=Source.TIM, source_ref=str(i), reference_number=f"12{i}",
                    customer_name=customer, agent=agent, destination_hub=Hub.MONTERREY, stage=stage,
                    status_flags=list(flags), assignees=list(assignees),
                    milestones={"green_light": green_light} if green_light else {},
                    current_step="3.- Documentos Completos", days_since_progress=days_since,
                    url=f"https://app.clickup.com/x/v/li/{i}")


def tms(i, customer, *, flags=(), manager=None, email=None, delivery=None, stage=Stage.TO_BORDER):
    return Shipment(id=f"TMS:{i}", source=Source.TMS, source_ref=str(i), customer_name=customer,
                    agent="Allied", destination_hub=Hub.MEXICO_CITY, stage=stage,
                    status_flags=list(flags), assignees=[manager] if manager else [],
                    delivery_date=delivery, extra={"coordinator_email": email} if email else {})


# ── which shipments are flagged ──────────────────────────────────────────────
def test_issues_are_ordered_and_window_risk_is_computed():
    s = tim(1, "Ana", flags=["stalled", "docs_incomplete"], days_since=12,
            green_light=dt.date(2026, 8, 20))          # +30d = 2026-09-19, 10 days out
    assert alerts.issues_for(s, TODAY) == ["docs_incomplete", "stalled"]

    risky = tim(2, "Beto", flags=["stalled"], green_light=dt.date(2026, 8, 14))  # deadline 09-13
    assert alerts.issues_for(risky, TODAY)[0] == "window_risk"
    assert alerts.deadline_for(risky) == dt.date(2026, 9, 13)


def test_closed_and_clean_shipments_are_never_alerted():
    assert alerts.issues_for(tim(3, "Cerrado", flags=["stalled"], stage=Stage.CLOSED), TODAY) == []
    assert alerts.issues_for(tim(4, "Sano"), TODAY) == []


def test_tms_window_risk_uses_the_delivery_date():
    assert alerts.issues_for(tms(5, "Dora", delivery=dt.date(2026, 9, 12)), TODAY) == ["window_risk"]
    assert alerts.issues_for(tms(6, "Elsa", delivery=dt.date(2026, 11, 1)), TODAY) == []


# ── ownership ────────────────────────────────────────────────────────────────
def test_ownership_follows_the_source_system():
    """Bill's rule: every ClickUp file is Fernanda Mora's, every Moveware file is
    Sara's — regardless of who is assigned on the record."""
    assert alerts.owner_for(tim(7, "Sin dueño", flags=["stalled"])) == (
        "Fernanda Mora", "fernandamora@thelsa.com", True)
    assert alerts.owner_for(tms(8, "x")) == ("Sara Reyes", "sarareyes@thelsa.com", True)


def test_record_assignees_no_longer_route(monkeypatch):
    monkeypatch.setenv("CB_ALERT_EMAILS", '{"Erica":"erica@thelsa.com"}')
    # a ClickUp assignee does not take the file away from the TIM owner
    assert alerts.owner_for(tim(9, "x", assignees=["Erica"]))[0] == "Fernanda Mora"
    # nor does a Moveware move manager who is not the TMS owner
    assert alerts.owner_for(tms(10, "x", manager="Erica", email="erica@thelsa.com"))[0] == "Sara Reyes"


def test_moveware_supplies_the_address_when_the_manager_is_the_owner(monkeypatch):
    monkeypatch.setenv("CB_ALERT_TMS_OWNER", "Elizabeth Hernandez")
    assert alerts.owner_for(tms(11, "x", manager="Elizabeth Hernandez",
                                email="elizabethhernandez@thelsa.com")) == (
        "Elizabeth Hernandez", "elizabethhernandez@thelsa.com", True)


def test_owners_and_folder_overrides_are_configurable(monkeypatch):
    monkeypatch.setenv("CB_ALERT_TIM_OWNER", "Diana Aguirre")
    monkeypatch.setenv("CB_ALERT_EMAILS", "Diana Aguirre:dianaaguirre@thelsa.com")
    assert alerts.owner_for(tim(12, "x")) == ("Diana Aguirre", "dianaaguirre@thelsa.com", True)
    monkeypatch.setenv("CB_ALERT_FOLDER_OWNERS", "UHaul:Gustavo")
    assert alerts.owner_for(tim(13, "x", agent="UHaul"))[0] == "Gustavo"
    assert alerts.owner_for(tim(14, "x", agent="Logicstics"))[0] == "Diana Aguirre"


def test_unknown_owner_goes_to_the_fallback_inbox_never_a_guess(monkeypatch):
    monkeypatch.setenv("CB_ALERT_TIM_OWNER", "Somebody New")
    assert alerts.owner_for(tim(15, "x")) == ("Somebody New", "bbrill@thelsa.com", False)
    monkeypatch.setenv("CB_ALERT_FALLBACK", "gustavo@thelsa.com")
    assert alerts.owner_for(tim(16, "x"))[1] == "gustavo@thelsa.com"


# ── grouping and the email ───────────────────────────────────────────────────
def test_build_alerts_groups_by_owner_and_sorts_by_urgency():
    ships = [
        tim(20, "Stalled one", flags=["stalled"], assignees=["Diana"], days_since=9),
        tim(21, "On hold", flags=["on_hold"]),
        tim(22, "Clean"),
        tms(23, "Late", manager="Erica", delivery=dt.date(2026, 9, 10)),
    ]
    out = alerts.build_alerts(ships, TODAY)
    assert [a["owner"] for a in out] == ["Fernanda Mora", "Sara Reyes"]   # busiest first
    fer = out[0]
    assert fer["to"] == "fernandamora@thelsa.com" and fer["resolved"]
    assert fer["shipment_count"] == 2                             # the clean one is out
    assert [r["customer"] for r in fer["shipments"]] == ["On hold", "Stalled one"]
    assert "on hold" in fer["subject"].lower()
    body = fer["body"]
    assert body.index("Hola Fernanda Mora") < body.index("Hi Fernanda Mora")   # Spanish first
    assert "En espera / detenido" in body and "On hold" in body
    assert "9 días sin avance" in body and "9 days without progress" in body
    assert "https://app.clickup.com/x/v/li/20" in body
    assert "CB_ALERT_EMAILS" not in body                          # resolved → no routing note
    # the Moveware file went to Sara even though Erica is its move manager
    assert out[1]["to"] == "sarareyes@thelsa.com"


def test_subject_agrees_in_number():
    one = alerts.build_alerts([tim(24, "Uno", flags=["on_hold"])], TODAY)[0]["subject"]
    assert "1 embarque requiere atención" in one and "1 shipment needs attention" in one
    two = alerts.build_alerts([tim(25, "Uno", flags=["on_hold"]),
                               tim(26, "Dos", flags=["on_hold"])], TODAY)[0]["subject"]
    assert "2 embarques requieren atención" in two and "2 shipments need attention" in two


def test_unresolved_owner_gets_a_routing_note_and_the_fallback_inbox(monkeypatch):
    monkeypatch.setenv("CB_ALERT_TIM_OWNER", "Somebody New")
    a = alerts.build_alerts([tim(30, "Nadie", flags=["on_hold"])], TODAY)[0]
    assert a["to"] == "bbrill@thelsa.com" and not a["resolved"]
    assert "CB_ALERT_EMAILS" in a["body"]


def test_cc_list_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("CB_ALERT_CC", "gustavo@thelsa.com, bbrill@thelsa.com")
    a = alerts.build_alerts([tim(31, "x", flags=["on_hold"])], TODAY)[0]
    assert a["cc"] == ["gustavo@thelsa.com", "bbrill@thelsa.com"]


def test_no_flags_means_no_alerts():
    assert alerts.build_alerts([tim(32, "Sano"), tms(33, "Sano2")], TODAY) == []


# ── de-dupe ──────────────────────────────────────────────────────────────────
def test_due_shipments_suppresses_repeats_but_not_changes():
    now = dt.datetime(2026, 9, 9, 8, 0)
    s = tim(40, "Ana", flags=["stalled"], days_since=9)
    state = {s.id: {"last": (now - dt.timedelta(hours=10)).isoformat(), "issues": ["stalled"]}}
    assert alerts.due_shipments([s], state, now, TODAY) == []

    old = {s.id: {"last": (now - dt.timedelta(hours=80)).isoformat(), "issues": ["stalled"]}}
    assert alerts.due_shipments([s], old, now, TODAY) == [s]

    changed = {s.id: {"last": (now - dt.timedelta(hours=1)).isoformat(), "issues": ["docs_incomplete"]}}
    assert alerts.due_shipments([s], changed, now, TODAY) == [s]

    assert alerts.due_shipments([s], {}, now, TODAY) == [s]


def test_repeat_hours_zero_disables_suppression(monkeypatch):
    monkeypatch.setenv("CB_ALERT_REPEAT_HOURS", "0")
    now = dt.datetime(2026, 9, 9, 8, 0)
    s = tim(41, "Ana", flags=["stalled"])
    state = {s.id: {"last": now.isoformat(), "issues": ["stalled"]}}
    assert alerts.due_shipments([s], state, now, TODAY) == [s]


# ── drafting is gated and never sends ────────────────────────────────────────
def test_create_drafts_reports_when_nothing_needs_attention():
    out = alerts.create_drafts([tim(50, "Sano")], actor="test", today=TODAY)
    assert out["alert_count"] == 0 and out["skipped_reason"] == "nothing needs attention"
    assert out["drafts"] == []


def test_create_drafts_uses_the_mailer_and_only_ever_drafts(monkeypatch):
    made = []

    class FakeMailer:
        def __init__(self, mailbox=None):
            self.mailbox = mailbox

        def create_draft(self, to, subject, body, cc=None, folder=None):
            made.append({"to": to, "subject": subject, "cc": cc, "folder": folder})
            return {"id": "d1", "folder": folder or "Drafts", "webLink": "https://x"}

        def send_mail(self, *a, **k):                      # pragma: no cover
            raise AssertionError("alerts must never send")

    import sys
    import types
    pkg = types.ModuleType("engine")
    pkg.__path__ = []
    mod = types.ModuleType("engine.mailer")
    mod.GraphMailer = FakeMailer
    monkeypatch.setitem(sys.modules, "engine", pkg)
    monkeypatch.setitem(sys.modules, "engine.mailer", mod)
    monkeypatch.setattr(alerts, "_load_state", lambda: {})
    monkeypatch.setattr(alerts, "_save_state", lambda s: None)

    out = alerts.create_drafts([tim(51, "Ana", flags=["on_hold"], assignees=["Diana"])],
                               actor="bill", today=TODAY)
    assert out["alert_count"] == 1 and out["drafts"][0]["ok"] and out["mode"] == "draft"
    assert made and made[0]["to"] == "fernandamora@thelsa.com"


def test_alerts_enabled_switch(monkeypatch):
    assert not alerts.alerts_enabled()
    monkeypatch.setenv("CB_ALERTS_ENABLED", "1")
    assert alerts.alerts_enabled()
    monkeypatch.setenv("DRY_RUN", "1")
    assert not alerts.alerts_enabled()
