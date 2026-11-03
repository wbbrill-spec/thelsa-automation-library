"""FAIM MS1.4 documents found in email (doc_check)."""
import datetime as dt

import doc_check as dc

TODAY = dt.date(2026, 11, 2)


def test_documents_are_recognised_by_attachment_name_and_text():
    assert dc.classify("HBL 110822 MAEU.pdf") == {"bol"}
    assert dc.classify("Inventario firmado 110822.pdf") == {"inventory"}
    assert dc.classify("AWB 176-12345678 for 111046") == {"bol"}
    assert dc.classify("Certificado de seguro - póliza 4411") == {"insurance"}
    assert dc.classify("Pre-alert 110900 / shipping instructions") == {"instructions"}
    assert dc.classify("Shipment 110900 has been delivered, no exceptions") == {"delivered"}
    assert dc.classify("Invoice 110900") == set()
    assert dc.classify("Podemos confirmar mañana") == set()          # 'pod' inside a word is not a POD


def test_what_a_file_needs_depends_on_type_and_how_far_it_went():
    load, deliv = dt.date(2026, 9, 1), dt.date(2026, 10, 1)
    assert dc.required_docs("EXP", True, load, deliv, TODAY) == ["instructions", "bol", "insurance", "delivered", "inventory"]
    assert dc.required_docs("EXP", False, load, deliv, TODAY) == ["instructions", "bol", "delivered", "inventory"]
    assert dc.required_docs("EXA", True, load, None, TODAY) == ["instructions", "bol", "inventory"]   # ends at origin
    assert dc.required_docs("IMA", False, None, deliv, TODAY) == ["delivered", "inventory"]          # no load date known
    assert dc.required_docs("EXP", False, load, None, TODAY) == ["instructions", "bol"]              # not delivered yet
    assert dc.required_docs("DIN", True, load, deliv, TODAY) == []                                   # domestic: not MS1.4
    # grace: a bill of lading isn't missing the day after loading
    assert "bol" not in dc.required_docs("EXP", False, TODAY - dt.timedelta(days=2), None, TODAY)


def test_only_emails_naming_the_file_count_and_attachments_win(monkeypatch):
    msgs = [
        {"subject": "Docs 110822", "bodyPreview": "see attached", "receivedDateTime": "2026-09-05T10:00:00Z",
         "webLink": "L1", "_attachments": ["HBL MAEU 110822.pdf"]},
        {"subject": "Re: something else", "bodyPreview": "bill of lading attached", "receivedDateTime": "2026-09-01T10:00:00Z",
         "webLink": "L2", "_attachments": ["BL.pdf"]},                                   # never names 110822
        {"subject": "110822 delivered", "bodyPreview": "will be delivered next week",
         "receivedDateTime": "2026-09-20T10:00:00Z", "webLink": "L3", "_attachments": []},  # before delivery
        {"subject": "110822 delivered", "bodyPreview": "delivered today, no exceptions",
         "receivedDateTime": "2026-10-01T18:00:00Z", "webLink": "L4", "_attachments": []},
    ]
    monkeypatch.setattr(dc, "_search", lambda mbx, number, app_h, diag: msgs)
    found = dc.docs_for_file({"file": "110822", "coord_email": "sarareyes@thelsa.com",
                              "delivery_day": "2026-10-01"}, None, {"_delegated": {}})
    assert found["bol"]["link"] == "L1" and found["bol"]["attachment"] == "HBL MAEU 110822.pdf"
    assert found["delivered"]["link"] == "L4"


def test_summary_counts_needed_and_found():
    results = [{"coord": "Sara", "needed": ["bol", "inventory"], "found": {"bol": {}}, "missing": ["inventory"]},
               {"coord": "Sara", "needed": ["bol"], "found": {"bol": {}}, "missing": []},
               {"coord": "Eli", "needed": [], "found": {}, "missing": []}]
    sm = dc.summary(results)
    assert sm["by_doc"]["bol"] == {"needed": 2, "found": 2}
    assert sm["by_doc"]["inventory"] == {"needed": 1, "found": 0}
    assert sm["files_checked"] == 2 and sm["files_complete"] == 1
    assert sm["by_coord"]["Sara"] == {"needed": 3, "found": 2, "files": 2}
