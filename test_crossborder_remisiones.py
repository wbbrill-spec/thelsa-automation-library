"""Offline tests for the Remisiones workbook reader (crossborder/remisiones.py)."""
import datetime as dt

from crossborder import remisiones as R, tim
from crossborder.models import Hub, Shipment, Source, Stage

EXTRACT = """Workbook: 2 worksheets.

## Sheet: 1 feb al 7 feb — 10 rows × 12 columns (B2:M11)
\tREMISIONES PENDIENTES\t\t\t\t\t\t\t\t\t\t
# ID\tNOMBRE - USUARIO\tAGENTE - CLIENTE\tFECHA\tVOLUMEN\tCONCEPTO\tVENTA\tREFERENCIA\tTIPO\tORIGEN\tDESTINO\tSTATUS
\tOld Person\tUHaul\t\t1 ubox\t\t $2.000,00 \t111111\tAgente\tMcAllen, TX\tCDMX\tPor recibir en bodega
\t\t\t\t\t\t $2.000,00 \t\t\t\t\t
Formulas:
H5: =SUM(H4:H4)

## Sheet: 26 abr al 2 may — 20 rows × 12 columns (B2:M21)
\tREMISIONES PENDIENTES\t\t\t\t\t\t\t\t\t\t
# ID\tNOMBRE - USUARIO\tAGENTE - CLIENTE\tFECHA\tVOLUMEN\tCONCEPTO\tVENTA\tREFERENCIA\tTIPO\tORIGEN\tDESTINO\tSTATUS
\tYanagisawa Nobuhiro\tDewitt\t\t1 van\t\t $2.928,00 \t136962\tAgente\tMcAllen, TX\tCDMX\tLlega el lunes 13 a McAllen
\ta4s\t\t\t\t\t500\t\t\t\t\t
Señor\tBrooks Bridges\tUHaul\t\t1 ubox\t\t $2.037,80 \t123614\tAgente\tMcAllen, TX\tIxtapa, Jalisco\tPor recibir en bodega
\tJacques Maurin\t\t\t160 m3\t\t $16.300,28 \tTIM-12702-26\tParticular\tMcAllen, TX\tSan Pedro Garza Garcia\tPendiente de pago
\tBlake Burgess\tPART\t\t1060CFT\t\t $9.495,00 \tTIM-18703-26\t\tTACOMA WA\tZAPOPAN \t
[1 empty row]
\t\t\t\t\t\t $71.966,16 \t\t\t\t\t
\tALMACENAJES MENSUALES\t\t\t\t\t\t\t\t\t\t
\tTOCA\t\tComercial\t\t\t $4.740,00 \t\t\t\t\t
\t\t\t\t\tTOTAL\t $6.592,12 \t\t\t\t\t
\tConfirmados pendientes\t\t\t\t\t\t\t\t\t\t
\tNOMBRE - USUARIO\tAGENTE - CLIENTE\tFECHA\tVOLUMEN\tCONCEPTO\tVENTA\tREFERENCIA\tTIPO\tORIGEN\tDESTINO\tSTATUS
\tWilliam Cassidy\tLogicstics\t\t167\t\t $2.028,00 \tMX133662 / LOGIC-24716\tAgente\tPharr, TX\tCDMX\tPor recibir en bodega, en almacenaje por 2 meses
\tRosa Molina\tUHaul\t\t\t\t\t121722\tAgente\tMcAllen, TX\tBarranca Honda, Morelos\tPor recibir en bodega
\tKELLY KATHLEEN WANIKA\t\t\t3 uboxes\t\t $4.945,00 \t127319A\tAgente\tMcAllen, TX\tQueretaro\tPor recibir en bodega
\tEntregas\t\t\t\t\t $80.307,65 \t\t\t\t\t
\tNOMBRE - USUARIO\tAGENTE - CLIENTE\tDestino\t\tFECHA ENTREGA\tVOLUMEN\tCOMENTARIOS\t\tINVOICE ENVIADO\t\t
Formulas:
H22: =SUM(H4:H20)
"""


def test_parse_values():
    assert R.parse_sale(" $2.478,00 ") == 2478.0
    assert R.parse_sale("2189,43") == 2189.43
    assert R.parse_sale("$41.625,82") == 41625.82
    assert R.parse_sale(2478) == 2478.0
    assert R.parse_sale(" $-   ") is None
    assert R.parse_volume("1 van") == {"lift_vans": 1, "u_boxes": None, "volume_m3": None, "vehicles": None}
    assert R.parse_volume("1/2 LVS")["lift_vans"] == 0.5 and R.parse_volume("1,5 ubox")["u_boxes"] == 1.5
    assert R.parse_volume("13,65cdm")["volume_m3"] == 13.65 and R.parse_volume("1 carro")["vehicles"] == 1
    assert R.parse_volume("3 uboxes")["u_boxes"] == 3 and R.parse_volume("1 Ubox")["u_boxes"] == 1
    assert R.parse_volume("423cuft")["volume_m3"] == 11.98
    assert R.parse_volume("160 m3")["volume_m3"] == 160.0 and R.parse_volume("40M3")["volume_m3"] == 40.0
    assert R.parse_volume("450 o 520")["volume_m3"] == 12.74          # first number, cuft
    assert R.parse_volume("")["volume_m3"] is None
    assert R.status_flags("Pendiente de pago") == ["payment_pending"]
    assert R.status_flags("Por recibir en bodega, en almacenaje por 2 meses") == ["in_storage"]
    assert R.status_flags("On Hold") == ["on_hold"]


def test_parse_extract_blocks_and_rows():
    sheets = R.parse_text_extract(EXTRACT)
    assert list(sheets) == ["1 feb al 7 feb", "26 abr al 2 may"]
    assert R.latest_week(sheets) == "26 abr al 2 may"
    wk = sheets["26 abr al 2 may"]
    blocks = [(r.block, r.name) for r in wk]
    assert ("pending", "Yanagisawa Nobuhiro") in blocks and ("pending", "Brooks Bridges") in blocks
    assert ("storage", "TOCA") in blocks and ("confirmed", "Rosa Molina") in blocks
    assert not any(r.name in ("a4s", "TOTAL") for r in wk)          # artifacts skipped
    assert len([r for r in wk if r.block == "pending"]) == 4
    jm = next(r for r in wk if r.name == "Jacques Maurin")
    assert jm.volume_m3 == 160.0 and jm.sale == 16300.28 and jm.hub is Hub.MONTERREY
    assert jm.flags == ["payment_pending"] and jm.type == "Particular"
    bb = next(r for r in wk if r.name == "Blake Burgess")
    assert bb.volume_m3 == 30.02 and bb.hub is Hub.GUADALAJARA and bb.origin == "TACOMA WA"
    kw = next(r for r in wk if r.name == "KELLY KATHLEEN WANIKA")
    assert kw.u_boxes == 3 and kw.hub is Hub.QUERETARO


def _ship(sid, customer, ref, agent="UHaul"):
    return Shipment(id=f"TIM:{sid}", source=Source.TIM, source_ref=sid, customer_name=customer,
                    reference_number=ref, agent=agent, stage=Stage.TO_BORDER)


def test_match_and_enrich():
    rows = R.parse_text_extract(EXTRACT)["26 abr al 2 may"]
    ships = [
        _ship("1", "Rosa Molina", "121722"),
        _ship("2", "William Cassidy", "MX133662 / LOGIC-24716", "Logicstics"),
        _ship("3", "Kelly Kathleen Wanika", ""),                 # no ref → name match
        _ship("4", "Somebody Else", "999999"),                   # no match
    ]
    m = R.match_rows_to_shipments(rows, ships)
    assert set(m["matches"]) == {"TIM:1", "TIM:2", "TIM:3"}
    assert m["how"]["TIM:2"] == "reference" and m["how"]["TIM:3"].startswith("name")
    assert m["diag"]["matched"] == 3 and "Somebody Else (999999)" in m["diag"]["unmatched_shipments"]
    R.enrich_shipment(ships[1], m["matches"]["TIM:2"])
    assert ships[1].destination == "CDMX" and ships[1].destination_hub is Hub.MEXICO_CITY
    assert ships[1].volume_m3 == 4.73 and ships[1].origin == "Pharr, TX"
    assert ships[1].extra["sale_value"] == 2028.0 and "in_storage" in ships[1].status_flags
    d = ships[1].to_dict()
    assert d["extra"]["remisiones_block"] == "confirmed" and d["destination_hub"] == "Mexico City"
    R.enrich_shipment(ships[2], m["matches"]["TIM:3"])
    assert ships[2].u_boxes == 3 and ships[2].lift_van_equivalents == 3.9


def test_reference_collision_goes_to_closest_name_and_storage_rows_never_match():
    rows = R.parse_text_extract(EXTRACT)["26 abr al 2 may"]
    a = _ship("a", "Armando Hernandez", "121722")          # wrong list tagged with Rosa's ref
    b = _ship("b", "Rosa Molina", "121722")
    c = _ship("c", "TOCA", "")                             # storage account, not a shipment
    m = R.match_rows_to_shipments(rows, [a, b, c])
    assert list(m["matches"]) == ["TIM:b"] and "collision" in m["how"]["TIM:b"]
    assert m["diag"]["collisions"] == ["TIM:b"]
    assert "TOCA ()" in m["diag"]["unmatched_shipments"]
