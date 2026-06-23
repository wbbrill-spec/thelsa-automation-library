"""
test_draft.py — one-shot connectivity test for the Microsoft Graph mail link.

Creates ONE test draft in the mailbox and prints the result. Touches no campaign
data (no workbook writes). Use this the moment the Graph credential is in place to
confirm auth + Mail.ReadWrite + that the draft lands in bbrill@'s Outlook Drafts.

    TEST_TO=bbrill@thelsa.com python -m engine.test_draft

Then open Outlook → Drafts, confirm the test message, and delete it.
"""
import os

from .mailer import GraphMailer


def main():
    to = os.environ.get("TEST_TO", os.environ.get("MAILBOX", "bbrill@thelsa.com"))
    m = GraphMailer()
    d = m.create_draft(
        to,
        "[TEST] Thelsa outreach engine — connectivity check",
        "This is a test draft created by the agent-outreach engine to confirm "
        "Microsoft Graph access (Mail.ReadWrite) to this mailbox.\n\n"
        "If you can see this draft in Outlook, the connection works. "
        "You can delete it. No campaign emails have been created or sent.")
    print("OK — draft created:", d)


if __name__ == "__main__":
    main()
