# Adding a new company

> **Start here (4 Nov 2026): one intake for all three apps.** Fill the company in once in QuoteRelay's
> onboarding wizard (mattapp.inflectionpointnow.com/admin/onboard): sections 1–4 for QuoteRelay,
> 5 "Operations & AuditTrack" and 6 "AI Assistant". The company's copy of this app then reads its
> settings from there: set `COMPANY_SETTINGS_URL=https://mattapp.inflectionpointnow.com/api/company/settings?t=<id>`
> and the company's token (`ASSISTANT_FEED_TOKEN_QUOTERELAY` here = `ASSISTANT_FEED_TOKEN_<ID>` on QuoteRelay).
> The settings files below are then only a fallback (the wizard can also download them).
> Passwords and keys never go in the form: the wizard's one-time secure link collects them.


Each company runs as its own copy of this app, from this same code, on its own
Render server, with its own disk and its own database. One company's data,
mailboxes and memory never touch another's.

## 1. Settings file (Claude does this)
Copy `tenants/example.json` to `tenants/<id>.json` (id = short lower-case name,
e.g. `acme`) and fill in:
- `brand`: company name, product name, logo (optional)
- `login.allowed_domains`: the company's email domain(s) + inflectionpointnow.com
- `moveware`: their Moveware company segment and host; who the diary write-back signs as
- `people`: coordinators (name -> email, Moveware codes), sales mailboxes,
  operations mailboxes, who gets copied on alerts, sender mailbox, former staff
- `invoicing`: who gets "ready to invoice" alerts, the subject of Finance's
  invoice-report email, holds
- `files`: test files to ignore, status codes, agent move types
- `features`: turn on only what the company has. Thelsa-only tools
  (`crossborder`, `tim`, `commission_report`, `campaigns`, `engine`,
  `rate_engine`, `embassy_rules`, `plan_de_viajes`) stay **false**.

Then run the tests: `test_tenant.py` checks the file has every required setting,
and `test_multicompany.py` must pass with the new company too.

## 2. What the company provides (Bill collects)
- Moveware API access for their company (user, password / key, company segment)
- Microsoft 365: their IT approves the app for reading mail (same steps as César
  did for Thelsa) and for sending from the sender mailbox
- The people list: coordinators, sales, invoicing team, managers

## 3. Render (Bill clicks; about $25/month)
1. Render → **New** → **Postgres** (or a new database on the existing instance) → copy its URL.
2. Render → **New** → **Web Service** → this same GitHub repo.
   - Name: `audit-<id>`
   - Compute plan: 1 CPU / 2 GB
   - Start command: `gunicorn app:app`  (settings come from gunicorn.conf.py)
   - Add a **Disk**: mount path `/var/data`, 5 GB
3. Environment variables:
   - `TENANT` = `<id>`
   - `DATABASE_URL` = the new database's URL (never Thelsa's — the app refuses to start on another company's database)
   - `FLASK_SECRET_KEY` = generate
   - Moveware: `MOVEWARE_USERNAME`, `MOVEWARE_PASSWORD` (and any key) for that company
   - Microsoft: `MS_CLIENT_ID`, `MS_CLIENT_SECRET`, `MS_TENANT_ID` for that company's Microsoft 365
   - `OAUTH_REDIRECT_URI` = `https://<their address>/auth/callback`
   - `AUDIT_ALERTS_ENABLED` = `0` until the first week's results are checked
4. Custom domain (optional): e.g. `acme.inflectionpointnow.com`.

## 4. First week
- Alerts stay off (`AUDIT_ALERTS_ENABLED=0`) while the audit fills and the team
  connects their mailboxes; Bill checks the dashboard with the company's lead.
- /healthz shows memory and restarts; crash emails go to `people.admin_alerts_to`.
- Then turn alerts on.
