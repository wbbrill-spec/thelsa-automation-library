import mw_probe as mp


def test_shape_hides_text_but_keeps_numbers_dates_and_codes():
    got = mp.shape({"creditor": "Transportes Pérez SA", "cost": 1200.5, "date": "2026-09-01", "code": "TRS",
                    "lines": [{"desc": "Freight", "actualCost": 900}]})
    assert got == {"creditor": "text(20)", "cost": 1200.5, "date": "2026-09-01", "code": "TRS",
                   "lines": {"count": 1, "first": [{"desc": "text(7)", "actualCost": 900}]}}


def test_money_fields_are_found_at_any_depth():
    body = {"charges": [{"sell": 100, "estimatedCost": 60, "actualCost": 75, "name": "x", "isTaxed": True}]}
    assert mp.cost_fields(body) == {"charges[0].sell": 100, "charges[0].estimatedCost": 60,
                                    "charges[0].actualCost": 75}


def test_summary_lists_cost_paths_and_what_answered():
    res = {"at": "2026-11-03T07:00:00Z", "base": "host/64000/api",
           "spec": {"spec_url": "/64000/api/swagger/v1/swagger.json", "n_paths": 2,
                    "cost_paths": [{"path": "/jobs/{id}/costs", "methods": ["GET"]}], "tried": []},
           "top": [{"path": "/costs", "status": 200, "money_fields": {"amount": 1}}],
           "jobs": [{"job": "110822", "type": "IMP", "job_id": 110822,
                     "paths": [{"path": "/jobs/{id}/costs", "status": 200, "money_fields": {"actualCost": 5}},
                               {"path": "/jobs/{id}/pos", "status": 404}]}]}
    t = mp.summary_text(res)
    assert "GET  /jobs/{id}/costs" in t and "/jobs/{id}/costs: 1 money fields" in t and "404 /jobs/{id}/pos" in t
