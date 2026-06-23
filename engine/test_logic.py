"""
test_logic.py — unit tests for the pure engine logic (no network/deps).
Run:  python -m engine.test_logic
"""
from datetime import date

from . import planning, classify, config


def test_business_days():
    # Fri 2026-06-26 + 1 business day = Mon 2026-06-29
    assert planning.add_business_days(date(2026, 6, 26), 1) == date(2026, 6, 29)
    # Tue 2026-06-23 + 5 business days = Tue 2026-06-30
    assert planning.add_business_days(date(2026, 6, 23), 5) == date(2026, 6, 30)
    assert planning.add_business_days(date(2026, 6, 23), 0) == date(2026, 6, 23)


def test_due():
    today = date(2026, 6, 23)
    assert planning.is_due("", today) is True
    assert planning.is_due("2026-06-23", today) is True
    assert planning.is_due("2026-06-20", today) is True
    assert planning.is_due("2026-06-25", today) is False


def test_suppression_and_screen():
    se = {"info@blocked.com"}
    sd = {"competitor.com"}
    assert planning.is_suppressed("info@blocked.com", se, sd) is True
    assert planning.is_suppressed("anyone@competitor.com", se, sd) is True
    assert planning.is_suppressed("ok@good.com", se, sd) is False
    stop = config.STOP_STAGES
    assert planning.screen_reason({"stage": "replied", "email": "a@b.com"}, se, sd, stop) == "stage=replied"
    assert planning.screen_reason({"stage": "queued", "email": "info@blocked.com"}, se, sd, stop) == "suppressed"
    assert planning.screen_reason({"stage": "queued", "email": "ok@good.com"}, se, sd, stop) is None


def test_next_step():
    gaps = [0, 5, 7]
    nxt, nad = planning.next_step_after_send(1, date(2026, 6, 23), gaps)  # +5 bd
    assert nxt == 2 and nad == "2026-06-30"
    nxt, nad = planning.next_step_after_send(3, date(2026, 6, 23), gaps)  # no step 4
    assert nxt is None and nad is None


def test_classify():
    rows = [
        {"category": "booking", "phrase": "nomination", "active": "TRUE"},
        {"category": "unsubscribe", "phrase": "unsubscribe", "active": "TRUE"},
        {"category": "rate_request", "phrase": "please quote", "active": "TRUE"},
        {"category": "rate_request", "phrase": "inactive one", "active": "FALSE"},
    ]
    ph = classify.phrases_by_category(rows)
    assert classify.classify("Re: rates", "Please quote our move to Mexico", ph) == "rate_request"
    assert classify.classify("Nomination", "you are nominated", ph) == "booking"
    assert classify.classify("stop", "please unsubscribe me", ph) == "unsubscribe"
    # precedence: unsubscribe beats booking
    assert classify.classify("nomination", "unsubscribe", ph) == "unsubscribe"
    # inactive phrase ignored
    assert classify.classify("x", "inactive one", ph) == "other"


def test_bounce_and_thread():
    assert classify.is_bounce("MAILER-DAEMON@thelsa.com") is True
    assert classify.is_bounce("postmaster@outlook.com") is True
    assert classify.is_bounce("agent@mover.com") is False
    assert classify.is_original_thread("We need a quote") is True
    assert classify.is_original_thread("Re: Your Mexico partner") is False
    assert classify.is_original_thread("FWD: hi") is False


def test_parse_gaps():
    assert config.parse_gaps("0,5,7") == [0, 5, 7]
    assert config.parse_gaps("") == [0, 5, 7]
    assert config.parse_gaps(" 0 , 3 ") == [0, 3]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print(f"\nAll {len(fns)} test groups passed.")
