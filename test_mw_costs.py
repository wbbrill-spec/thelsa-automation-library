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
    assert [o["id"] for o in mc.won_options([{"id": 1, "status": "P"}, {"id": 2, "status": "L"}])] == [1]


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
