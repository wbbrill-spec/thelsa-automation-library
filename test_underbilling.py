"""
Tests for the under-billing detector, built against the real coordinator emails
seen in the Thelsa mailbox (jobs 110881, 110919).
"""
import underbilling as ub


# ── amount parsing (mixed US and EU number formats appear in one email) ──────
def test_num_us_and_eu_formats():
    assert ub._num("1,042.75") == 1042.75      # US: comma thousands, dot decimal
    assert ub._num("1.338,21") == 1338.21      # EU: dot thousands, comma decimal
    assert ub._num("4,949.00") == 4949.0
    assert ub._num("153.60") == 153.6
    assert ub._num("8,600.00") == 8600.0
    assert ub._num("1850") == 1850.0
    assert ub._num("0") is None                # zero/невalid -> None


def test_job_extraction_ignores_agent_refs():
    subj = "FINAL CHARGES |Packing report | Chris Djumaa Nsiah - HSBC / Sirva Moving - [6573212] | 110881"
    assert ub.extract_job(subj) == "110881"    # not 6573212
    subj2 = "BL Draft | ... / 811556 / 110886 / luz verde despacho"
    assert ub.extract_job(subj2) == "110886"   # not 811556
    assert ub.extract_job("no job here") is None


def test_prefer_known_job():
    # if several MoveWare-shaped ids appear, prefer one in the known set
    subj = "FINAL CHARGES 110001 vs 110886"
    assert ub.extract_job(subj, known_jobs={"110886"}) == "110886"


# ── approval detection (EN + ES, with negation guard) ───────────────────────
def test_detect_approval_english():
    assert ub.detect_approval("The Air shipment is approved") is True
    assert ub.detect_approval("Please proceed with your billing to Allied Intl.") is True


def test_detect_approval_spanish():
    assert ub.detect_approval("Si por favor proceder, el 13 de julio...") is True
    assert ub.detect_approval("Quedo de acuerdo, adelante con el despacho") is True


def test_negation_blocks_false_approval():
    # the real 110881 line: air approved but sea NOT yet
    assert ub.detect_approval(
        "I must have the net weight of the Sea shipment though before approving it") is False
    assert ub.detect_approval("This is not approved yet") is False


# ── parse a real 'FINAL CHARGES' message ────────────────────────────────────
AIR_BODY = (
    "Dear Jamie\n\nPlease find below final charges for this move:\n\nAIR\n"
    "  * OA SERVICES = USD 1.338,21\n  * Freight Air = USD 1,042.75\n\n"
    "Looking forward to your approval.\n\nMaria Carrasco\nBilling Coordinator\n"
    "De: Stephanie Barraza ... (quoted history with USD 9,999.99 that must be ignored)\n"
)

SEA_BODY = (
    "Dear Don\n\nPlease find below final charges for this move:\n\n"
    "  * Origin service USD 8,482.24\n  * Crating USD 153.60\n"
    "  * Freight Exclusive Inland - International USD 8,600.00\n"
    "  * Shuttle service at origin city USD 1,850.00\n\nLooking forward to your approval.\n"
)


def test_parse_air_charges_mixed_formats():
    f = ub.parse_charges_email(
        "FINAL CHARGES | Chris Djumaa Nsiah - Sirva - [6573212] | 110881",
        AIR_BODY, sender="mariacarrasco@thelsa.com")
    assert f is not None
    assert f["job"] == "110881"
    assert f["currency"] == "USD"
    assert sorted(f["charges"]) == [1042.75, 1338.21]     # 9,999.99 in quote ignored
    assert f["charged_total"] == 2380.96


def test_parse_sea_charges():
    f = ub.parse_charges_email(
        "FINAL CHARGES | Giovanni Moratalla - Jabil / 110919", SEA_BODY,
        sender="mariacarrasco@thelsa.com")
    assert f["job"] == "110919"
    assert f["charged_total"] == round(8482.24 + 153.60 + 8600.00 + 1850.00, 2)


def test_non_final_charges_returns_none():
    assert ub.parse_charges_email("Packing report | 110881", "some body USD 100") is None


def test_final_charges_without_job_returns_none():
    assert ub.parse_charges_email("FINAL CHARGES for the move", "USD 500") is None


# ── reconcile approved charges vs MoveWare invoiced ─────────────────────────
def test_reconcile_flags_underbilled_job():
    findings = [
        {"job": "110881", "charged_total": 2380.96, "currency": "USD",
         "coordinator": "mariacarrasco@thelsa.com", "subject": "AIR 110881", "approved": True},
        {"job": "110881", "charged_total": 14082.84, "currency": "USD",
         "coordinator": "mariacarrasco@thelsa.com", "subject": "SEA 110881", "approved": True},
    ]
    invoiced = {"110881": {"inv_amt": 2380.96, "currency": "USD",
                           "client": "Jamie.Poteat@sirva.com"}}
    rows = ub.reconcile(findings, invoiced)
    assert len(rows) == 1
    r = rows[0]
    assert r["job"] == "110881"
    assert r["approved_total"] == round(2380.96 + 14082.84, 2)
    assert r["invoiced"] == 2380.96
    assert r["gap"] == 14082.84          # the SEA charges never made it onto the invoice
    assert r["currency_match"] is True


def test_reconcile_fully_billed_job_not_flagged():
    findings = [{"job": "110900", "charged_total": 5000.0, "currency": "USD",
                 "subject": "x", "approved": True}]
    invoiced = {"110900": {"inv_amt": 5000.0, "currency": "USD"}}
    assert ub.reconcile(findings, invoiced) == []


def test_reconcile_ignores_unapproved():
    findings = [{"job": "110901", "charged_total": 9000.0, "currency": "USD",
                 "subject": "x", "approved": False}]
    invoiced = {"110901": {"inv_amt": 0.0, "currency": "USD"}}
    assert ub.reconcile(findings, invoiced) == []   # not approved -> not our problem


def test_reconcile_flags_currency_mismatch_for_review():
    findings = [{"job": "110902", "charged_total": 3000.0, "currency": "USD",
                 "subject": "x", "approved": True}]
    invoiced = {"110902": {"inv_amt": 100.0, "currency": "MXN"}}
    rows = ub.reconcile(findings, invoiced)
    assert len(rows) == 1
    assert rows[0]["currency_match"] is False        # flagged, needs manual FX check


# ── build_findings: approval detected in the thread, not just the charges msg ─
def test_build_findings_approval_from_thread():
    messages = [{
        "subject": "FINAL CHARGES | Sirva | 110881", "body": "final charges\nOA USD 2,380.96\nLooking forward to your approval.",
        "sender": "mariacarrasco@thelsa.com", "mailbox": "mariacarrasco@thelsa.com",
        "conversationId": "c1", "date": "2026-08-19",
    }]
    # charges message itself is NOT an approval; the client's reply is.
    def fetch(mbx, cid):
        return ["Hello, the Air shipment is approved. Please invoice."] if cid == "c1" else []
    findings = ub.build_findings(messages, thread_fetcher=fetch)
    assert len(findings) == 1
    assert findings[0]["approved"] is True
    assert findings[0]["job"] == "110881"


def test_build_findings_no_approval_stays_false():
    messages = [{
        "subject": "FINAL CHARGES | 110882", "body": "final charges\nOA USD 500.00\nawaiting your approval",
        "sender": "x@thelsa.com", "mailbox": "x@thelsa.com", "conversationId": "c2",
    }]
    findings = ub.build_findings(messages, thread_fetcher=lambda m, c: ["still reviewing, not yet approved"])
    assert findings[0]["approved"] is False


def test_build_findings_skips_non_charges():
    messages = [{"subject": "Packing report | 110883", "body": "docs attached",
                 "sender": "x@thelsa.com", "mailbox": "x@thelsa.com", "conversationId": "c3"}]
    assert ub.build_findings(messages, thread_fetcher=lambda m, c: []) == []
