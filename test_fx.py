import json
import fx


def _stub(monkeypatch, tmp_path, got=None):
    monkeypatch.setenv("FX_CACHE_PATH", str(tmp_path / "fx.json"))
    monkeypatch.setattr(fx, "_fetch_yahoo", lambda codes: dict(got or {}))
    fx._mem.clear()


def test_normalize_iso_symbols_and_spanish():
    assert fx.normalize("usd") == "USD"
    assert fx.normalize("Dólar Americano") == "USD"
    assert fx.normalize("Peso Mexicano") == "MXN"
    assert fx.normalize("MN") == "MXN"
    assert fx.normalize("€") == "EUR"
    assert fx.normalize({"code": "mxn"}) == "MXN"
    assert fx.normalize("", "MXN") == "MXN"
    assert fx.normalize(None) is None


def test_convert_uses_yahoo_rate(monkeypatch, tmp_path):
    _stub(monkeypatch, tmp_path, {"MXN": 20.0})
    assert fx.rates()["source"] == "yahoo"
    assert fx.convert(200, "MXN", "USD") == 10.0
    assert fx.convert(10, "USD", "MXN") == 200.0
    assert fx.convert(5, "USD", "USD") == 5.0


def test_fallback_when_yahoo_down(monkeypatch, tmp_path):
    _stub(monkeypatch, tmp_path, {})
    r = fx.rates()
    assert r["source"] == "fallback"
    assert r["per_usd"]["MXN"] == fx.FALLBACK_PER_USD["MXN"]
    assert not (tmp_path / "fx.json").exists()   # fallback never persisted


def test_fetches_once_per_day_and_persists(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv("FX_CACHE_PATH", str(tmp_path / "fx.json"))
    monkeypatch.setattr(fx, "_fetch_yahoo", lambda c: calls.append(1) or {"MXN": 19.0})
    fx._mem.clear()
    fx.rates(); fx.rates(); fx.rate("MXN", "USD")
    assert len(calls) == 1
    assert json.load(open(tmp_path / "fx.json"))["per_usd"]["MXN"] == 19.0
    fx._mem.clear()
    fx.rates()                      # served from disk, no new fetch
    assert len(calls) == 1


def test_unknown_currency_left_as_booked(monkeypatch, tmp_path):
    _stub(monkeypatch, tmp_path, {"MXN": 20.0})
    assert fx.convert(100, "XYZ", "USD") == 100.0
