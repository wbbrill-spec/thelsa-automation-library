"""Finding a Moveware job by the number staff use — lots included.

From the Moveware Data Capture Guide (§2, §5.1, §5.4): a lot such as 110771A is
its own job, the API opens it by an internal id that is a DIFFERENT number
(110771A is id 110782), the list hides jobs, `?number=` misses some, and no
server filter is believed until its rows are checked in our own code.

These tests pin the behaviours most likely to be "simplified" later:
  * a lot is never opened by typing its staff number into /jobs/{…}
  * an internal id is never mistaken for a job number
  * a row a filter returns counts only if its own number matches
  * the id-by-id search finds what the list hides, remembers what it opened,
    and only says "does not exist" when it really reached the newest job
  * a failed call is never recorded as "no such job"
  * nothing a lookup returns carries a customer name, an address or money
"""
import json
import urllib.error

import pytest
from flask import Flask

import audit_web
import mw_live
import mw_lots

TOP = 110900                      # newest job id in the fake database
LOTS = {110782: "110771A", 110830: "110771B"}


def _job(jid):
    num = LOTS.get(jid, str(jid))
    return {
        "id": jid, "number": num, "numberOnly": num, "status": "C" if num == "110771B" else "W",
        "branchCode": "MEU", "type": "IMP", "jobValue": 152488.77, "currency": "MXN",
        "firstName": "Jane", "lastName": "Roe", "name": "Roe, Jane",
        "activityDates": {"created": {"date": "2026-07-01"}, "booked": {"date": "2026-07-06"},
                          "pack": {"date": "2026-07-27"}, "uplift": {"date": None}},
        "addresses": {"origin": {"street": "1 Secret Street", "city": "Monterrey"}},
        "roles": {"billTo": {"entity": {"name": "U.S. Embassy Mexico City"}},
                  "salesRepresentative": {"firstName": "Edgar", "lastName": "Espino"}},
    }


class FakeMoveware:
    """Stands in for mw_live._get_timed. `number_filter` is how the server
    treats ?number= : 'works', 'ignored' (returns an unrelated page) or 'hidden'
    (returns nothing for jobs the list hides)."""

    def __init__(self, number_filter="ignored", top=TOP, broken=(), wrong=None):
        self.number_filter = number_filter
        self.top = top
        self.broken = set(broken)          # ids whose call times out
        self.wrong = wrong or {}           # id -> a different job comes back
        self.calls = []

    def __call__(self, path, timeout):
        self.calls.append(path)
        if path.startswith("/jobs?"):
            q = dict(kv.split("=", 1) for kv in path.split("?", 1)[1].split("&"))
            if self.number_filter == "works" and "number" in q:
                hits = [j for j in range(110771, self.top + 1)
                        if LOTS.get(j, str(j)) == q["number"]]
                return {"jobs": [_job(j) for j in hits]}
            if self.number_filter == "hidden":
                return {"jobs": []}
            return {"jobs": [_job(j) for j in range(100001, 100011)]}   # ignored
        jid = int(path.rsplit("/", 1)[1]) if path.rsplit("/", 1)[1].isdigit() else None
        if jid is None:
            raise urllib.error.HTTPError(path, 404, "Not Found", {}, None)
        if jid in self.broken:
            raise TimeoutError("hard deadline exceeded")
        if jid in self.wrong:
            return _job(self.wrong[jid])
        if jid < 100001 or jid > self.top:
            raise urllib.error.HTTPError(path, 404, "Not Found", {}, None)
        return _job(jid)

    def opened(self):
        return [p for p in self.calls if not p.startswith("/jobs?")]


@pytest.fixture
def mw(monkeypatch, tmp_path):
    def install(**kw):
        fake = FakeMoveware(**kw)
        monkeypatch.setattr(mw_live, "_get_timed", fake)
        return fake
    monkeypatch.setattr(mw_live, "have_creds", lambda: True)
    monkeypatch.setattr(mw_live, "audited_files", lambda: [])
    monkeypatch.setattr(mw_lots, "_dir", lambda: str(tmp_path))
    monkeypatch.delenv("MW_LOT_INDEX_PATH", raising=False)
    monkeypatch.setattr(mw_lots, "_INDEX", {"loaded": False, "ids": {}})
    monkeypatch.setattr(mw_lots, "_SCANS", {})
    return install


def _run_search(number, **kw):
    """Run the background search and wait for it."""
    started = mw_lots.start_search(number, **kw)
    assert started["status"] == "started"
    mw_lots._SCANS[mw_lots.normalize(number)].join(30)
    return mw_lots.search_result(number)


# ── job numbers ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("typed,want", [
    ("110771A", ("110771", "A")), ("110771a", ("110771", "A")),
    ("110771-A", ("110771", "A")), ("110771 a", ("110771", "A")),
    ("110771/A", ("110771", "A")), (" 110771 ", ("110771", "")),
    ("412042W", ("412042", "W")),
])
def test_staff_spellings_of_a_lot_number(typed, want):
    assert mw_lots.parse(typed) == want


@pytest.mark.parametrize("junk", ["", None, "abc", "110771ABC", "12", "110771A1", "1; drop"])
def test_junk_is_not_a_job_number(junk):
    assert mw_lots.parse(junk) is None
    assert mw_lots.find(junk)["status"] == "invalid"


def test_file_and_lot():
    assert mw_lots.base_number("110771A") == "110771"
    assert mw_lots.is_lot("110771A") and not mw_lots.is_lot("110771")


def test_database_label_comes_from_the_url_in_use(monkeypatch):
    """Never label a page TEST while it reads live (guide §1)."""
    assert mw_lots.database() == "live"
    monkeypatch.setattr(mw_live, "BASE_URL", "https://rest.moveware-test.app/08800/api")
    assert mw_lots.database() == "test"


# ── the quick checks ─────────────────────────────────────────────────────────
def test_a_plain_number_opens_directly(mw):
    fake = mw()
    out = mw_lots.find("110845")
    assert out["status"] == "found" and out["found_by"] == "opened directly by number"
    assert out["job"]["number"] == "110845" and out["job"]["id"] == "110845"
    assert fake.opened() == ["/jobs/110845"]


def test_a_lot_is_never_opened_by_its_staff_number(mw):
    """/jobs/110771A does not exist; /jobs/110771 is the wrong job."""
    fake = mw(number_filter="works")
    out = mw_lots.find("110771A")
    assert out["status"] == "found"
    assert out["job"]["number"] == "110771A"
    assert out["job"]["id"] == "110782"            # the id is a different number
    assert out["job"]["file"] == "110771" and out["job"]["lot"] == "A"
    assert "/jobs/110771A" not in fake.calls
    assert fake.opened() == ["/jobs/110782"]       # confirmed before it is believed


def test_an_ignored_filter_cannot_produce_a_match(mw):
    """The server accepts ?number= and returns an unrelated page. Rows count
    only if their own number is the one asked for (guide §5.4)."""
    mw(number_filter="ignored")
    out = mw_lots.find("110771A")
    assert out["status"] == "not_found_yet"
    steps = {t["step"]: t for t in out["tried"]}
    assert steps["list ?number=110771A"]["rows"] == 10
    assert steps["list ?number=110771A"]["rows_on_this_file"] == 0


def test_an_internal_id_is_not_a_job_number(mw):
    """Id 110782 exists, but it is job 110771A. Nobody has a job '110782'."""
    mw(number_filter="hidden")
    out = mw_lots.find("110782")
    assert out["status"] == "not_found_yet"
    # …and what was learned on the way is kept: the next lookup of the lot is free.
    assert mw_lots._known_id("110771A") == "110782"


def test_a_different_job_coming_back_is_not_accepted(mw):
    """Guide §10: trust a detail only if it echoes the id that was asked for."""
    mw(number_filter="hidden", wrong={110845: 110846})
    assert mw_lots.find("110845")["status"] == "not_found_yet"


def test_the_audit_cache_answers_without_a_list_call(mw, monkeypatch):
    fake = mw()
    monkeypatch.setattr(mw_live, "audited_files",
                        lambda: [{"job": "110771A", "number": "110771A", "job_id": "110782"}])
    out = mw_lots.find("110771A")
    assert out["status"] == "found" and out["found_by"] == "audit dashboard cache"
    assert fake.calls == ["/jobs/110782"]


def test_no_credentials_is_reported_not_guessed(mw, monkeypatch):
    mw()
    monkeypatch.setattr(mw_live, "have_creds", lambda: False)
    assert mw_lots.find("110771A")["status"] == "unavailable"


# ── the id-by-id search ──────────────────────────────────────────────────────
def test_the_search_finds_a_lot_the_list_hides(mw):
    fake = mw(number_filter="hidden")
    assert mw_lots.find("110771A")["status"] == "not_found_yet"
    res = _run_search("110771A")
    assert res["status"] == "found" and res["complete"] is True
    assert res["found"]["number"] == "110771A" and res["found"]["id"] == "110782"
    assert res["from_id"] == 110772                 # a lot's id is above its file
    assert res["stopped_because"] == "found"
    assert "/jobs/110771A" not in fake.calls


def test_what_the_search_opened_is_remembered(mw, tmp_path):
    mw(number_filter="hidden")
    _run_search("110771A")
    saved = json.loads((tmp_path / "mw_number_index_live.json").read_text())
    assert saved["ids"]["110782"] == "110771A"
    # A fresh process (empty memory) answers from the file with one confirming call.
    mw_lots._INDEX.update(loaded=False, ids={})
    fake = mw(number_filter="hidden")
    out = mw_lots.find("110771A")
    assert out["status"] == "found" and out["found_by"] == "remembered index"
    assert fake.calls == ["/jobs/110782"]


def test_does_not_exist_only_after_reaching_the_newest_job(mw):
    mw(number_filter="hidden")
    res = _run_search("110771C", limit=5000)
    assert res["status"] == "does_not_exist" and res["complete"] is True
    assert res["stopped_because"] == "reached the newest job in the database"
    assert res["lots_found"] == ["110771A", "110771B"]     # seen on the way


def test_a_capped_search_does_not_claim_the_job_is_missing(mw):
    mw(number_filter="hidden")
    res = _run_search("110771B", limit=20)          # the lot is 59 ids up
    assert res["status"] == "not_found" and res["complete"] is False
    assert res["stopped_because"] == "search limit reached"
    assert "Not proof" in res["note"]
    # Running it again continues past the ids already opened.
    res = _run_search("110771B", limit=60)
    assert res["status"] == "found" and res["ids_skipped_known"] == 20


def test_a_failed_call_is_not_recorded_as_no_job(mw):
    mw(number_filter="hidden", broken={110782})
    res = _run_search("110771A", limit=5000)
    assert res["status"] == "not_found" and res["complete"] is False
    assert res["errors"] == 1
    assert "110782" not in mw_lots._INDEX["ids"]    # still unknown, will be retried


def test_empty_ids_past_the_newest_job_are_not_remembered(mw):
    """Past the top, 'no job' only means 'not created yet'."""
    mw(number_filter="hidden", top=110800)
    _run_search("110771C", limit=5000)
    assert mw_lots._INDEX["ids"].get("110800") == "110800"
    assert "110801" not in mw_lots._INDEX["ids"]
    # A job created later is therefore still found.
    mw(number_filter="hidden", top=110900)
    assert _run_search("110771B", limit=5000)["status"] == "found"


def test_every_lot_on_a_file(mw):
    mw(number_filter="hidden")
    res = _run_search("110771", every_lot=True, limit=5000)
    assert res["lots_found"] == ["110771A", "110771B"]
    assert res["complete"] is True
    assert [r["number"] for r in mw_lots.known_on_file("110771")] == ["110771A", "110771B"]


def test_each_lot_keeps_its_own_status(mw):
    """Guide §2: never copy the file's status or dates onto a lot."""
    mw(number_filter="works")
    assert mw_lots.find("110771A")["job"]["status"] == "W"
    assert mw_lots.find("110771B")["job"]["status"] == "C"


def test_an_interrupted_search_says_so(mw, tmp_path):
    mw()
    (tmp_path / "mw_lot_search_live_110771A.json").write_text(
        json.dumps({"number": "110771A", "status": "running", "updated_at": 1}))
    assert mw_lots.search_result("110771A")["status"] == "interrupted"
    assert mw_lots.search_result("110999Z")["status"] == "no_search"


# ── nothing private leaves a lookup (guide §12) ──────────────────────────────
def test_lookups_carry_no_names_addresses_or_money(mw):
    mw(number_filter="hidden")
    blob = json.dumps([mw_lots.find("110845"), _run_search("110771A"),
                       mw_lots.find("110771A")])
    for private in ("Roe", "Jane", "Secret Street", "Monterrey", "152488", "MXN",
                    "Espino", "Embassy Mexico"):
        assert private not in blob
    assert mw_lots.find("110845")["job"]["embassy"] is True     # a yes/no only


def test_the_module_cannot_write_to_moveware():
    """Read-only by construction: the only way out is mw_live's GET fetch."""
    src = open(mw_lots.__file__).read()
    for verb in ("POST", "PUT", "PATCH", "DELETE", "urlopen", "requests."):
        assert verb not in src
    assert 'method="GET"' in open(mw_live.__file__).read()


# ── the routes ───────────────────────────────────────────────────────────────
@pytest.fixture
def client():
    app = Flask(__name__)
    app.secret_key = "test"
    app.add_url_rule("/login", "login", lambda: "login")
    app.register_blueprint(audit_web.audit_bp)
    with app.test_client() as c:
        yield c


def _login(c):
    with c.session_transaction() as s:
        s["user_email"] = "maria.gonzalez@thelsa.com"


def test_lot_route_needs_a_login(client, mw):
    mw()
    assert client.get("/audit/lot?number=110771A").status_code == 302


def test_lot_route_finds_then_falls_back_to_the_search(client, mw):
    mw(number_filter="hidden")
    _login(client)
    body = client.get("/audit/lot?number=110771-a").get_json()
    assert body["status"] == "not_found_yet"
    assert body["search"]["status"] == "started"
    assert body["collect"] == "/audit/lot?number=110771A&result=1"
    mw_lots._SCANS["110771A"].join(30)
    got = client.get(body["collect"]).get_json()
    assert got["status"] == "found" and got["found"]["id"] == "110782"
    assert client.get("/audit/lot?number=110771A").get_json()["found_by"] == "remembered index"
    assert "error" in client.get("/audit/lot?number=nonsense").get_json()


def test_v2probe_takes_the_number_staff_use(client, mw):
    fake = mw(number_filter="works")
    _login(client)
    body = client.get("/audit/v2probe?number=110771A").get_json()
    assert body["id"] == "110782" and body["number"] == "110771A"
    assert "/jobs/110782/invoices" in fake.calls
    # A lot typed into ?id= is treated as a number, not sent to /jobs/110771A.
    assert client.get("/audit/v2probe?id=110771A").get_json()["id"] == "110782"
    assert not any("110771A/" in p or p.endswith("/110771A") for p in fake.calls)
    # A plain ?id= is still Moveware's internal id — and says which job it is.
    assert client.get("/audit/v2probe?id=110782").get_json()["number"] == "110771A"


def test_v2probe_does_not_guess_when_the_lot_is_not_found(client, mw):
    fake = mw(number_filter="hidden")
    _login(client)
    body = client.get("/audit/v2probe?number=110771A").get_json()
    assert "error" in body and body["next"] == "/audit/lot?number=110771A"
    assert not any("/invoices" in p for p in fake.calls)
