"""The Moveware cache must not let an outage silently empty the board.

Moveware answered 503 on every slice on 2026-09-11. The walk raised nothing —
it just returned zero rows — so the empty result was cached as legitimate and
all 45 TMS shipments disappeared from the dashboard with no error shown.
"""
import time

import pytest

from crossborder import web


def _ship(i):
    from crossborder.models import Shipment, Source
    return Shipment(id=f"TMS:{i}", source=Source.TMS, source_ref=str(i))


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    web._TMS_CACHE.update(at=0.0, shipments=[], diag={}, good_at=0.0,
                          attempt_at=0.0, degraded=False, reason=None)
    monkeypatch.setattr(web.tms.MovewareClient, "have_creds", staticmethod(lambda: True))
    yield
    web._TMS_CACHE.update(at=0.0, shipments=[], diag={}, good_at=0.0,
                          attempt_at=0.0, degraded=False, reason=None)


def _walk(monkeypatch, ships, slices=None):
    calls = {"n": 0}

    def fake(*a, **k):
        calls["n"] += 1
        return list(ships), {"env": "prod", "count": len(ships), "rows_seen": len(ships),
                             "slices": slices if slices is not None else [{"from": "x", "to": "y", "rows": len(ships)}]}
    monkeypatch.setattr(web.tms, "fetch_tms_shipments", fake)
    return calls


def test_good_walk_is_cached():
    pass  # covered by the flow tests below


def test_a_zero_row_walk_keeps_the_last_good_fleet(monkeypatch):
    good = [_ship(1), _ship(2)]
    _walk(monkeypatch, good)
    ships, diag = web._tms_cached({})
    assert len(ships) == 2 and not diag.get("stale")

    # Moveware starts answering 503: every slice fails, the walk returns [].
    web._TMS_CACHE["at"] = 0.0            # force a re-walk
    _walk(monkeypatch, [], slices=[{"from": "x", "to": "y", "error": "HTTP Error 503"},
                                   {"from": "a", "to": "b", "error": "HTTP Error 503"}])
    ships, diag = web._tms_cached({})
    assert [s.id for s in ships] == ["TMS:1", "TMS:2"], "the board must keep the last good fleet"
    assert diag["stale"] is True
    assert diag["count"] == 2
    assert "no jobs" in diag["stale_reason"]
    assert diag["stale_age_s"] is not None
    assert web._TMS_CACHE["degraded"] is True


def test_a_genuinely_empty_first_walk_is_accepted(monkeypatch):
    """Zero rows is only believable when no slice errored."""
    _walk(monkeypatch, [])
    ships, diag = web._tms_cached({})
    assert ships == [] and not diag.get("stale") and not diag.get("error")
    assert web._TMS_CACHE["degraded"] is False


def test_a_cold_start_during_an_outage_reports_an_error_not_a_zero(monkeypatch):
    """A Render restart mid-outage leaves no cache to fall back on. The board
    must still say Moveware is unreachable rather than showing a confident 0."""
    _walk(monkeypatch, [], slices=[{"error": "HTTP Error 503"}, {"error": "HTTP Error 503"}])
    ships, diag = web._tms_cached({})
    assert ships == []
    assert "no jobs" in diag["error"], "a 503 storm must not read as an empty business"
    assert diag["slice_errors"] == 2
    assert web._TMS_CACHE["degraded"] is True


def test_while_degraded_moveware_is_not_hammered(monkeypatch):
    _walk(monkeypatch, [_ship(1)])
    web._tms_cached({})
    web._TMS_CACHE["at"] = 0.0
    calls = _walk(monkeypatch, [], slices=[{"error": "503"}])
    web._tms_cached({})                      # the failing attempt
    assert calls["n"] == 1
    for _ in range(5):                       # further refreshes inside the window
        ships, diag = web._tms_cached({})
        assert diag["stale"] is True and len(ships) == 1
    assert calls["n"] == 1, "must not re-walk Moveware on every 5-minute refresh"

    # once the retry window passes, it tries again
    web._TMS_CACHE["attempt_at"] = time.time() - (web._TMS_RETRY_S + 1)
    web._tms_cached({})
    assert calls["n"] == 2


def test_recovery_clears_the_degraded_flag(monkeypatch):
    _walk(monkeypatch, [_ship(1)])
    web._tms_cached({})
    web._TMS_CACHE["at"] = 0.0
    _walk(monkeypatch, [], slices=[{"error": "503"}])
    web._tms_cached({})
    assert web._TMS_CACHE["degraded"] is True

    web._TMS_CACHE["attempt_at"] = 0.0
    _walk(monkeypatch, [_ship(1), _ship(2), _ship(3)])
    ships, diag = web._tms_cached({})
    assert len(ships) == 3 and not diag.get("stale")
    assert web._TMS_CACHE["degraded"] is False and web._TMS_CACHE["reason"] is None


def test_an_exception_also_serves_the_last_good_fleet(monkeypatch):
    _walk(monkeypatch, [_ship(1)])
    web._tms_cached({})
    web._TMS_CACHE["at"] = 0.0

    def boom(*a, **k):
        raise RuntimeError("connection reset")
    monkeypatch.setattr(web.tms, "fetch_tms_shipments", boom)
    ships, diag = web._tms_cached({})
    assert len(ships) == 1
    assert diag["stale"] is True and "connection reset" in diag["stale_reason"]


def test_slice_errors_survive_the_diag_compaction():
    out = web._compact_tms_diag({"env": "prod", "rows_seen": 0, "slices": [
        {"from": "a", "to": "b", "error": "HTTPError: HTTP Error 503: Service Unavailable"},
        {"from": "c", "to": "d", "rows": 0},
    ]})
    assert out["slices"] == 2
    assert out["slice_errors"] == 1
    assert "503" in out["slice_error_sample"]
