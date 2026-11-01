"""Chasers waiting on the user stay on the dashboard, read or not, until answered."""
import datetime as dt

from assistant import i18n, priority, triage

NOW = dt.datetime(2026, 11, 1, 12, tzinfo=dt.timezone.utc)


def msg(i, subj, preview="", ago_h=30, frm="agent@fink.com.br", cid="C1", read=True):
    return {"id": i, "from_name": "Aline", "from_addr": frm, "subject": subj, "snippet": preview,
            "url": "https://outlook/x", "received_at": NOW - dt.timedelta(hours=ago_h),
            "is_read": read, "is_flagged": False, "conversation_id": cid}


def test_read_chaser_unanswered_is_flagged_once_per_conversation():
    ms = [msg("m1", "Packing list", "Any news about the packing list in English?", 50),
          msg("m2", "RE: Packing list", "Please update us urgently", 20),
          msg("m3", "Newsletter", "Following up on our offer", 10, frm="news@shop.com", cid="C2"),
          msg("m4", "Lunch?", "see you", 5, cid="C3")]
    out = triage.chasers(ms, [], "stephanie@thelsa.com")
    assert [o["external_id"] for o in out] == ["m2"] and out[0]["meta"]["chases"] == 2


def test_answered_after_the_chaser_clears_it():
    ms = [msg("m1", "Payment", "Favor de confirmar si recibieron el pago", 30)]
    sent_before = [{"conversationId": "C1", "sentDateTime": (NOW - dt.timedelta(hours=40)).isoformat()}]
    sent_after = [{"conversationId": "C1", "sentDateTime": (NOW - dt.timedelta(hours=2)).isoformat()}]
    assert len(triage.chasers(ms, sent_before, "me@thelsa.com")) == 1
    assert triage.chasers(ms, sent_after, "me@thelsa.com") == []


def test_chaser_is_urgent_and_labelled():
    it = triage.chasers([msg("m1", "Any update?", "", 30)], [], "me@thelsa.com")[0]
    s = priority.score({**it, "seen": True}, now=NOW)
    assert priority.tier(s) == "urgent"
    assert i18n.kind_label("chased", "es") == "Te están dando seguimiento"
