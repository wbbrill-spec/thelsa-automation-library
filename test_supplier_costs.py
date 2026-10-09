"""Supplier cost detector: extra costs quoted in email + CFDI supplier invoices."""
import supplier_costs as sc

# Real wording from a TMS thread (file 110886, Oct 2026), names removed.
INSA = ("Hola Conzuelo Te comento que intentamos levantar un reporte con la naviera pero ya no fue posible. "
        "Por favor déjame saber si puedo proceder con la facturación de los cargos adicionales "
        "• Demoras: 2,040 USD + 15% finance fee • Almacenajes: $3,690 + 15% finance fee "
        "• Roll de buque: $300 USD + 15% finance fee De: Someone < a@b.com > Enviado el: jueves, 1 de octubre "
        "• Almacenajes: $9,999 USD")

CFDI = b"""<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital"
  Version="4.0" Serie="A" Folio="1234" Fecha="2026-06-12T10:00:00" Moneda="USD" TipoCambio="17.20"
  SubTotal="1500.00" Total="1740.00" TipoDeComprobante="I">
  <cfdi:Emisor Rfc="TRA010101AAA" Nombre="TRANSPORTES EJEMPLO SA DE CV"/>
  <cfdi:Receptor Rfc="TMS020202BBB" Nombre="THELSA"/>
  <cfdi:Conceptos>
    <cfdi:Concepto Descripcion="FLETE TERRESTRE EXP 110886 VERACRUZ" Importe="1500.00"/>
  </cfdi:Conceptos>
  <cfdi:Impuestos TotalImpuestosTrasladados="240.00"/>
  <cfdi:Complemento><tfd:TimbreFiscalDigital UUID="abcd-1234"/></cfdi:Complemento>
</cfdi:Comprobante>"""


def test_top_of_message_drops_quoted_history():
    top = sc.top_of_message(INSA)
    assert "Roll de buque" in top and "9,999" not in top


def test_extra_cost_lines_with_fee():
    lines = sc.parse_extra_costs(sc.top_of_message(INSA))
    got = {(l["kind"], l["amount"], l["currency"], l["fee_pct"]) for l in lines}
    assert ("demurrage", 2040.0, "USD", 15.0) in got
    assert ("storage", 3690.0, "USD", 15.0) in got
    assert ("roll / rebooking", 300.0, "USD", 15.0) in got
    dem = [l for l in lines if l["kind"] == "demurrage"][0]
    assert dem["amount_with_fee"] == 2346.0


def test_no_cost_without_keyword_or_currency():
    assert sc.parse_extra_costs("Please call me at 81 8220 3550, volume 30.25 cbm") == []
    assert sc.parse_extra_costs("Almacenaje pendiente de confirmar") == []


def test_find_files():
    s = "declaracion | Sr. X / 811556 / 110886 / luz verde; also 110779A and 1108861"
    assert sc.find_files(s) == ["110886", "110779A"]
    assert sc.find_files(s, known={"110779"}) == ["110779A"]


def test_parse_cfdi():
    c = sc.parse_cfdi(CFDI, "factura.xml")
    assert c["supplier"].startswith("TRANSPORTES") and c["rfc"] == "TRA010101AAA"
    assert c["currency"] == "USD" and c["fx"] == 17.2
    assert c["net"] == 1500.0 and c["total"] == 1740.0 and c["tax"] == 240.0
    assert c["files"] == ["110886"] and c["uuid"] == "ABCD-1234"
    assert sc.parse_cfdi(b"<nota/>") is None and sc.parse_cfdi(b"not xml") is None


def test_rows_from_message_and_queue():
    msg = {"subject": "RE: Sr. X / 811556 / 110886 / luz verde", "body": INSA,
           "sender": "asis@insa.com.ec", "date": "2026-10-07T17:11:16Z",
           "mailbox": "stephaniebarraza@thelsa.com",
           "attachments": [{"name": "A1234.xml", "bytes": CFDI}, {"name": "x.pdf", "bytes": b"%PDF"}]}
    rows = sc.rows_from_message(msg)
    cf = [r for r in rows if r["source"].startswith("supplier invoice")]
    em = [r for r in rows if r["source"].startswith("extra cost")]
    assert len(cf) == 1 and cf[0]["file"] == "110886" and cf[0]["amount"] == 1500.0 and cf[0]["status"] == "to post"
    assert len(em) == 3 and all(r["file"] == "110886" and r["status"] == "to post" for r in em)
    q = sc.merge(rows)
    first = next(iter(q))
    q[first]["status"] = "posted"
    q2 = sc.merge(sc.rows_from_message(msg), q)        # re-scan never resets a person's status
    assert q2[first]["status"] == "posted" and len(q2) == 4
    s = sc.summarize(q2)
    assert s["n_open"] == 3 and s["n_files"] == 1


def test_internal_sender_needs_confirmation():
    msg = {"subject": "110900 | costs", "body": "Almacenaje adicional: USD 450", "sender": "x@thelsa.com",
           "date": "2026-10-01", "mailbox": "x@thelsa.com"}
    rows = sc.rows_from_message(msg)
    assert rows[0]["status"] == "to confirm"


def test_credit_note_cfdi_is_negative():
    msg = {"subject": "NC 110886", "body": "", "sender": "s@prov.mx", "date": "2026-06-20", "mailbox": "m",
           "attachments": [{"name": "nc.xml", "bytes": CFDI.replace(b'TipoDeComprobante="I"', b'TipoDeComprobante="E"')}]}
    assert sc.rows_from_message(msg)[0]["amount"] == -1500.0


def test_no_creds_is_a_quiet_noop(monkeypatch):
    import ms_graph
    monkeypatch.setattr(ms_graph, "have_ms_creds", lambda: False)
    assert sc.scan_live()["have_creds"] is False
