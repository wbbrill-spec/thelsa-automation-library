"""4 Nov 2026 (Bill): the Assistant listed every email he received, even ones
not sent to him, and put colleagues' file updates on his list as his own.
  * mail: only messages with the user in the To line become reply / chaser to-dos
  * files: a watched colleague's to-dos are tagged team=<colleague>, shown apart"""
import datetime as dt
import types, sys

from assistant import moveware, triage


def _g(mid, to, cc=(), frm="ana@client.com"):
    return {"id": mid, "from": {"emailAddress": {"name": "Ana", "address": frm}},
            "toRecipients": [{"emailAddress": {"address": a}} for a in to],
            "ccRecipients": [{"emailAddress": {"address": a}} for a in cc],
            "subject": "Any update on the invoice?", "bodyPreview": "p", "webLink": "w",
            "receivedDateTime": "2026-11-03T12:00:00Z", "isRead": False,
            "flag": {"flagStatus": "notFlagged"}, "conversationId": "C" + mid}


ME = {"bbrill@thelsa.com", "bill.brill@thelsa.com"}


def test_only_mail_to_me_needs_a_reply():
    msgs = [triage.normalize_graph(m) for m in [
        _g("1", ["bbrill@thelsa.com"]),                       # to me
        _g("2", ["sarareyes@thelsa.com"], cc=["bbrill@thelsa.com"]),   # cc only
        _g("3", ["operaciones@thelsa.com"]),                  # group / distribution list
        _g("4", ["Bill.Brill@thelsa.com"]),                   # my alias, any case
        _g("5", ["x@thelsa.com", "bbrill@thelsa.com"]),       # one of several To
    ]]
    got = sorted(i["external_id"] for i in triage.triage(msgs, ME) if i["kind"] == "needs_reply")
    assert got == ["1", "4", "5"]
    ch = sorted(i["external_id"] for i in triage.chasers(msgs, [], ME))
    assert ch == ["1", "4", "5"]


def test_flagged_mail_stays_even_when_cc():
    m = _g("9", ["other@thelsa.com"], cc=["bbrill@thelsa.com"])
    m["flag"] = {"flagStatus": "flagged"}
    assert [i["kind"] for i in triage.triage([triage.normalize_graph(m)], ME)] == ["flagged"]


def test_mail_without_recipient_data_is_not_dropped():
    m = _g("7", [])
    del m["toRecipients"]
    assert [i["kind"] for i in triage.triage([triage.normalize_graph(m)], ME)] == ["needs_reply"]


def test_watched_colleagues_files_are_tagged_team_not_mine(monkeypatch):
    def f(job, coord):
        return {"job": job, "coordinator_email": coord, "status": "W",
                "pack": dt.date.today() - dt.timedelta(days=3), "invoiced": False, "act_wt": 5}
    monkeypatch.setitem(sys.modules, "mw_live", types.SimpleNamespace(
        have_creds=lambda: True, ensure_auditor=lambda: None,
        audited_in_window=lambda: [f("1", "bbrill@thelsa.com"), f("2", "sarareyes@thelsa.com"),
                                   f("3", "nobody@thelsa.com")]))
    bill = {"email": "bbrill@thelsa.com", "moveware_email": None,
            "mw_watch": "sarareyes@thelsa.com, bbrill@thelsa.com"}
    items = moveware.tasks_for_user(bill)
    mine = {i["meta"]["job"] for i in items if not i["meta"].get("team")}
    team = {(i["meta"]["job"], i["meta"]["team"]) for i in items if i["meta"].get("team")}
    assert mine == {"1"}
    assert team == {("2", "sarareyes@thelsa.com")}
    assert moveware.team_emails(bill) == ["sarareyes@thelsa.com"]     # own address never "team"


def test_dashboard_counts_only_my_own(client=None):
    from assistant import web
    rows = [{"source": "auditrack", "tier": "urgent", "seen": False,
             "meta": {"team": "sarareyes@thelsa.com", "job": "2"}},
            {"source": "auditrack", "tier": "urgent", "seen": False, "meta": {"job": "1"}}]
    g = web.group_team([r for r in rows if r["meta"].get("team")])
    assert g[0]["email"] == "sarareyes@thelsa.com" and g[0]["n"] == 1
