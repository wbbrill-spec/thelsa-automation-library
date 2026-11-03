import mw_costs as mc

# File 111136, option 1, first charge line as Moveware returned it on 3 Nov 2026.
CHARGE = {"id": 591417, "currency": "MXN", "description": "Int. Move", "valueExclusive": 106001.3,
          "valueEstimateExclusive": 83681.0, "valueActualExclusive": 58000.0, "supplierCode": "",
          "purchase": "", "dateModified": "2026-11-02T19:03:50.890-00:00"}
PENDING = {"id": 2, "currency": "MXN", "description": "Customs", "valueExclusive": 4350.0,
           "valueEstimateExclusive": 3000.0, "valueActualExclusive": 0.0}
EMPTY = {"id": 3, "currency": "MXN", "description": "x", "valueExclusive": 0, "valueEstimateExclusive": 0,
         "valueActualExclusive": 0}


def test_lines_and_file_totals_from_charges():
    lines = mc.lines_from_charges([CHARGE, PENDING, EMPTY])
    assert len(lines) == 2                                     # all-zero line left out
    e = mc.summarize_file("111136", ["111136"], lines)
    assert (e["sell"], e["est"], e["act"]) == (110351.3, 86681.0, 58000.0)
    assert e["lines_with_act"] == 1 and e["lines_pending"] == 1 and e["est_pending"] == 3000.0
    assert e["cost_best"] == 61000.0 and e["profit_best"] == 49351.3 and not e["complete"]
    assert e["currency"] == "MXN" and e["source"] == "moveware_api"


def test_only_won_options_count():
    opts = [{"id": 1, "costStatus": "W"}, {"id": 2, "costStatus": "C"}, {"id": 3, "costStatus": "W"}]
    assert [o["id"] for o in mc.won_options(opts)] == [1, 3]
    assert [o["id"] for o in mc.won_options([{"id": 1}, {"id": 2}])] == [1, 2]       # no status: all
    assert mc.won_options([{"id": 1, "status": "P"}, {"id": 2, "status": "L"}]) == []


def test_customer_name_is_scrubbed_from_line_descriptions():
    lines = mc.lines_from_charges([dict(CHARGE, description="Int. Move Smith")], scrub=lambda t: t.replace("Smith", "").strip())
    assert lines[0]["desc"] == "Int. Move"


def test_compare_with_the_costing_sheet():
    api = {"1": {"est": 100.0, "act": 50.0}, "2": {"est": 10.0, "act": 0.0}, "3": {"est": 5.0, "act": 5.0}}
    sheet = {"1": {"est": 100.5, "act": 50.0}, "2": {"est": 10.0, "act": 9.0}, "4": {"est": 1.0, "act": 1.0}}
    got = mc.compare(api, sheet)
    assert got["counts"] == {"both": 2, "est_match": 2, "act_match": 1, "api_only": 1, "sheet_only": 1}
    assert got["differences"][0]["job"] == "2"


def test_merged_prefers_the_api_where_it_has_the_file(monkeypatch):
    monkeypatch.setattr(mc, "costs", lambda: {"1": {"lines": 3, "source": "moveware_api"}, "9": {"lines": 0}})
    got = mc.merged({"1": {"source": "sheet"}, "2": {"source": "sheet"}})
    assert got["1"]["source"] == "moveware_api" and got["2"]["source"] == "sheet" and "9" not in got


def _ln(sell, est, act):
    return {"id": 0, "desc": "", "sell": sell, "est": est, "act": act, "currency": "MXN"}


def test_a_first_line_that_repeats_the_total_is_dropped():
    # 111136 as Moveware returned it on 3 Nov 2026 (option 57328)
    rest = [_ln(4350, 4350, 0), _ln(7665, 7665, 0), _ln(67280, 67280, 58000), _ln(1450, 1450, 0), _ln(25256.3, 2936, 0)]
    lines = [_ln(106001.3, 83681.0, 58000.0)] + rest
    assert mc.drop_total_line(lines, 106001.3) == rest
    # 111108: the first line is a real line (the option total is the sum WITHOUT it is
    # not the case here: all lines together make the option value)
    real = [_ln(100, 80, 0), _ln(50, 40, 0)]
    assert mc.drop_total_line(real, 150) == real


def test_quoted_alternatives_never_count():
    assert mc.won_options([{"id": 1, "status": "Q"}, {"id": 2, "status": "C"}]) == []
    assert [o["id"] for o in mc.won_options([{"id": 1, "status": "W"}, {"id": 2, "status": "Q"}])] == [1]


def test_report_text_reads_plainly():
    api = {"1": mc.summarize_file("1", ["1"], [_ln(100, 80, 70)]), "2": mc.summarize_file("2", ["2"], [_ln(50, 40, 0)])}
    sheet = {"1": {"est": 80.0, "act": 60.0}}
    t = mc.report_text(api, sheet)
    assert "Files read from the API: 2" in t and "actual cost agrees: 0 of 1" in t and "1: est 80.00 vs 80.00 | act 70.00 vs 60.00" in t


def test_a_build_saved_by_older_rules_is_not_reused(tmp_path, monkeypatch):
    import json
    p = tmp_path / "mw_costs.json"
    p.write_text(json.dumps({"built_at": 1, "files": {"1": {"lines": 1}}}))        # no rules version
    monkeypatch.setattr(mc, "_path", lambda: str(p))
    monkeypatch.setitem(mc._STATE, "files", {})
    mc.load_saved()
    assert mc._STATE["files"] == {}
    p.write_text(json.dumps({"built_at": 1, "rules": mc.RULES_VERSION, "files": {"1": {"lines": 1}}}))
    mc.load_saved()
    assert mc._STATE["files"] == {"1": {"lines": 1}}
    monkeypatch.setitem(mc._STATE, "files", {})


def test_file_currency_is_the_options_currency():
    lines = [dict(_ln(100, 80, 0), currency="USD", option_currency="MXN"), dict(_ln(50, 40, 0), currency="MXN", option_currency="MXN")]
    assert mc.summarize_file("1", ["1"], lines)["currency"] == "MXN"


def test_a_total_line_anywhere_in_the_option_is_dropped():
    rest = [_ln(4350, 4350, 0), _ln(67280, 67280, 58000), _ln(34371.3, 12051, 0)]
    total = _ln(106001.3, 83681.0, 58000.0)
    assert mc.drop_total_line(rest[:2] + [total] + rest[2:], 106001.3) == rest


def test_the_same_job_or_charge_line_is_counted_once(monkeypatch):
    calls = []
    def fake_read(jid, client=""):
        calls.append(jid)
        return [{"id": 7, "desc": "", "sell": 10.0, "est": 8.0, "act": 8.0, "est_inc": 8.0, "currency": "MXN"}]
    monkeypatch.setattr(mc, "read_file", fake_read)
    monkeypatch.setattr(mc, "_reported", lambda: True)
    monkeypatch.setattr(mc, "_path", lambda: "/nonexistent/mw_costs.json")
    out = mc.build([{"job": "110611", "job_id": 1}, {"job": "110611", "job_id": 1},
                    {"job": "110611A", "job_id": 2}], pause=0)
    assert calls == [1, 2]                               # job 1 read once
    assert out["110611"]["act"] == 8.0                    # charge 7 counted once
    monkeypatch.setitem(mc._STATE, "files", {})


def test_estimate_matches_the_sheet_with_or_without_iva():
    api = {"1": {"est": 80160.0, "est_inc": 92985.6, "act": 0.0}}
    sheet = {"1": {"est": 92985.6, "act": 0.0}}
    assert mc.compare(api, sheet)["counts"]["est_match"] == 1


def test_the_report_is_sent_once_per_rules_version(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "_path", lambda: str(tmp_path / "mw_costs.json"))
    assert not mc._reported()
    mc._mark_reported()
    assert mc._reported()
    assert mc.report({}) is False                        # already sent: nothing goes out


def test_a_marker_left_by_a_dead_process_does_not_block(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "_path", lambda: str(tmp_path / "mw_costs.json"))
    (tmp_path / "mw_costs.json.building").write_text("999999 1")      # no such process
    assert mc._building_elsewhere() is False
    mc._mark_building()                                                 # this process
    assert mc._building_elsewhere() is False


def test_a_failed_send_is_retried_later(tmp_path, monkeypatch):
    import engine.mailer as em
    monkeypatch.setattr(mc, "_path", lambda: str(tmp_path / "mw_costs.json"))
    class Boom:
        def __init__(self, mailbox=None): pass
        def send_mail(self, *a, **k): raise RuntimeError("graph down")
    monkeypatch.setattr(em, "GraphMailer", Boom)
    monkeypatch.setattr("costing_sheet.costs", lambda: {})
    assert mc.report({}) is False
    assert not mc._reported()                                           # claim released
