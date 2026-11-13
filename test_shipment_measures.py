"""Estimated (survey) vs actual (shipping documents) volume and weight."""
import datetime as dt

import shipment_measures as sm


def test_weights_and_volumes_are_read_with_their_units():
    f = sm.find_measures
    assert f("Gross weight: 2,345.50 KGS  Volume: 30.5 CBM") == {"kg": (2345.5, 3), "m3": (30.5, 0)}
    assert f("PESO BRUTO 1.250 kg")["kg"] == (1250.0, 3)          # Mexican thousands dot
    assert f("Net weight 4.200,5 kgs")["kg"] == (4200.5, 2)
    assert f("volumen 12,5 m3")["m3"] == (12.5, 0)                 # decimal comma
    assert f("G.W. (KGS): 2,345")["kg"] == (2345.0, 3)
    assert f("CBM: 30.5")["m3"] == (30.5, 0)
    assert f("2,345 kgs gross")["kg"] == (2345.0, 3)


def test_pounds_and_cubic_feet_are_converted():
    out = sm.find_measures("Total 1,050 cu ft  weight 7,000 lbs")
    assert out["kg"][0] == round(7000 * sm.KG_PER_LB, 1)
    assert out["m3"][0] == round(1050 * sm.M3_PER_FT3, 2)


def test_gross_beats_net_and_numbers_without_a_unit_do_not_count():
    assert sm.find_measures("peso neto 3500 kg peso bruto 3800 kg")["kg"] == (3800.0, 3)
    assert sm.find_measures("40 HC container, call 555 1234, file 110895") == {}
    assert sm.find_measures("weight 3 kg sample") == {}            # outside the sane range


def test_moveware_measure_record_is_the_estimate():
    items = [{"category": "Estimate", "density": 6.5,
              "volume": {"gross": {"m3": 64, "f3": 2260}, "net": {"m3": 60}},
              "weight": {"gross": {"kg": 6664}}}]
    out = sm.parse_measures(items)
    assert out["est"]["m3"] == 64 and out["est"]["kg"] == 6664 and "act" not in out
    out = sm.parse_measures([{"category": "Actual", "volume": {"net": {"f3": 1000}}, "weight": {"net": {"lb": 1000}}}])
    assert out["act"]["m3"] == round(1000 * sm.M3_PER_FT3, 2) and out["act"]["kg"] == round(1000 * sm.KG_PER_LB, 1)


def test_compare_and_summary():
    assert sm.compare({"m3": 50, "kg": 5000}, {"m3": 55, "kg": 4000}) == {"m3_diff_pct": 10.0, "kg_diff_pct": -20.0}
    assert sm.compare({"m3": 50}, {}) == {}
    rs = [{"coord": "Ana", "m3_diff_pct": 5.0, "kg_diff_pct": -20.0, "est": {"m3": 1}, "act": {"m3": 1}},
          {"coord": "Ana", "m3_diff_pct": 30.0, "est": {"m3": 1}, "act": {"m3": 1}},
          {"coord": "Luis", "est": {"m3": 1}, "act": {}}]
    s = sm.summary(rs)
    assert s["files_checked"] == 3 and s["compared"] == 2 and s["with_actual"] == 2
    assert s["volume"] == {"files": 2, "avg_abs_pct": 17.5, "median_pct": 17.5, "within_pct": 50,
                           "under_estimated": 1, "over_estimated": 0}
    assert s["by_coord"]["Luis"]["volume"] == {"files": 0}
    assert s["by_coord"]["Ana"]["weight"]["over_estimated"] == 1


def test_only_loaded_files_with_a_moveware_id_are_checked():
    today = dt.date(2026, 11, 13)
    rows = [{"file": "1", "job_id": 11, "load_day": "2026-11-01"},
            {"file": "2", "job_id": 12, "load_day": "2026-12-01"},          # not loaded yet
            {"file": "3", "job_id": None, "load_day": "2026-11-01"},        # no Moveware id
            {"file": "4", "job_id": 14, "load_day": "", "delivery_day": "2026-11-10"}]
    assert [r["file"] for r in sm.rows_for(rows, today)] == ["1", "4"]


def test_actual_comes_from_the_best_document(monkeypatch):
    import doc_check as dc
    import survey_finder as sf
    msgs = [{"id": "a", "subject": "110895 BL draft", "receivedDateTime": "2026-09-01T10:00:00Z",
             "bodyPreview": "file 110895", "webLink": "https://x/a", "hasAttachments": True,
             "_attachments": ["HBL 110895.pdf"]},
            {"id": "b", "subject": "110895 certificado de peso", "receivedDateTime": "2026-08-30T10:00:00Z",
             "bodyPreview": "110895", "webLink": "https://x/b", "hasAttachments": True,
             "_attachments": ["boleta de bascula.pdf"]},
            {"id": "c", "subject": "Hello", "receivedDateTime": "2026-09-02T10:00:00Z",
             "bodyPreview": "another file", "webLink": "", "hasAttachments": False, "_attachments": []}]
    monkeypatch.setattr(dc, "_search", lambda mbx, n, h, d: msgs)
    monkeypatch.setattr(sf, "_mailboxes_for", lambda e: ["coord@x.com"])
    monkeypatch.setattr(sf, "_auth_for", lambda mbx, h, dl, d: ({"Authorization": "t"}, "https://g/messages"))
    monkeypatch.setattr(sm, "_attachments", lambda url, mid, hh: [{"id": mid + "1", "name": (
        "HBL 110895.pdf" if mid == "a" else "boleta de bascula.pdf"), "contentType": "application/pdf"}])
    monkeypatch.setattr(sm, "_attachment_bytes", lambda url, mid, aid, hh: aid.encode())
    texts = {"a1": "Gross weight 7,100 kgs  Measurement 66 CBM", "b1": "PESO BRUTO 6.900 KG"}
    monkeypatch.setattr(sm, "pdf_text", lambda data: texts[data.decode()])
    monkeypatch.setattr(sm, "_body_text", lambda url, mid, hh: "")
    diag = {}
    got = sm.actual_from_email({"file": "110895", "coord_email": "coord@x.com"}, {"m3": 64, "kg": 6664},
                               None, diag)
    assert got["kg"]["value"] == 6900.0 and got["kg"]["kind"] == "weight"      # certificate beats the BL
    assert got["m3"]["value"] == 66.0 and got["m3"]["kind"] == "bol"
    assert got["kg"]["attachment"] == "boleta de bascula.pdf" and diag["pdfs"] == 2


def test_a_figure_far_from_the_estimate_is_left_out(monkeypatch):
    import doc_check as dc
    import survey_finder as sf
    msg = {"id": "a", "subject": "110895 bill of lading", "receivedDateTime": "2026-09-01T10:00:00Z", "bodyPreview": "110895",
           "webLink": "", "hasAttachments": False, "_attachments": []}
    monkeypatch.setattr(dc, "_search", lambda mbx, n, h, d: [msg])
    monkeypatch.setattr(sf, "_mailboxes_for", lambda e: ["coord@x.com"])
    monkeypatch.setattr(sf, "_auth_for", lambda mbx, h, dl, d: ({"Authorization": "t"}, "https://g"))
    monkeypatch.setattr(sm, "_body_text", lambda url, mid, hh: "container tare weight 28,000 kg; volume 67 cbm")
    diag = {}
    got = sm.actual_from_email({"file": "110895"}, {"m3": 64, "kg": 2000}, None, diag)
    assert "kg" not in got and got["m3"]["value"] == 67.0 and diag["implausible"] == 1


def test_pdf_text_of_something_that_is_not_a_pdf_is_empty():
    assert sm.pdf_text(b"not a pdf") == "" and sm.pdf_text(b"") == ""


def test_run_uses_the_audit_estimate_without_another_moveware_call(monkeypatch):
    import ms_graph
    import survey_finder as sf
    monkeypatch.setattr(sm, "_js", None)
    monkeypatch.setattr(ms_graph, "_get_token", lambda: None)
    monkeypatch.setattr(sf, "delegated_mailboxes", lambda: {})
    called = []
    monkeypatch.setattr(sm, "moveware_measures", lambda jid: called.append(jid) or {"est": {"m3": 10, "kg": 1000}})
    sm.run([{"file": "1", "job_id": 1, "coord": "Ana", "est_m3": 20, "est_kg": 2000},
            {"file": "2", "job_id": 2, "coord": "Ana"}])
    st = sm.state()
    assert called == [2] and not st["running"] and st["error"] is None
    assert [r["est"] for r in st["results"]] == [{"m3": 20, "kg": 2000}, {"m3": 10, "kg": 1000}]
