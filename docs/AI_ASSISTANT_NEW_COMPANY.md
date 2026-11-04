# AI Assistant: adding a company (one copy per company)

> **Start here (4 Nov 2026): one intake for all three apps.** Fill the company in once in QuoteRelay's
> onboarding wizard (mattapp.inflectionpointnow.com/admin/onboard): sections 1–4 for QuoteRelay,
> 5 "Operations & AuditTrack" and 6 "AI Assistant". The company's copy of this app then reads its
> settings from there: set `COMPANY_SETTINGS_URL=https://mattapp.inflectionpointnow.com/api/company/settings?t=<id>`
> and the company's token (`ASSISTANT_FEED_TOKEN_QUOTERELAY` here = `ASSISTANT_FEED_TOKEN_<ID>` on QuoteRelay).
> The settings files below are then only a fallback (the wizard can also download them).
> Passwords and keys never go in the form: the wizard's one-time secure link collects them.


Each company gets its own copy of the AI Assistant: its own web address, its own
database and its own Microsoft sign-in. Nothing is shared between companies.
The code is the same for every copy; what differs is one settings file and the
copy's Render environment.

## 1. Bill decides (no code)
- Company id (lower case, e.g. `greatermoving`) and display name, logo file
- Staff email domain(s) and who the admins are
- Key users (see every to-do nobody owns) and billing / procurement contacts
- Which products to connect: QuoteRelay, WhatsApp AI Assistant, Moveware/AuditTrack
- Web address, e.g. `assistant.greatermoving.com` or `greatermoving.inflectionpointnow.com`

## 2. Settings files (Claude)
Two files per company, both named `<id>.json`:
- `tenants/<id>.json` — the library: Moveware company, coordinators, alerts, invoicing
  routing, file rules (template: `tenants/example.json`)
- `assistant/tenants/<id>.json` — the AI Assistant: name, logo, staff domains, admins,
  key users, connected products (template: `assistant/tenants/_template.json`)

Add the logo to `static/`, run the tests, push.

## 3. Company's IT (one-time, ~10 minutes)
In their Microsoft Entra admin center, register a web app ("<Company> AI Assistant"):
- Redirect URI: `https://<web address>/auth/callback/microsoft`
- Delegated permissions: User.Read, Mail.Read, offline_access (Mail.ReadWrite later, for drafts)
- Grant admin consent; send Bill the Application (client) ID, Directory (tenant) ID and a client secret
  (the secret goes straight into Render, never into email threads or this repo)

## 4. Render (Bill clicks, Claude can drive the browser)
- New Postgres database: `<id>-assistant-db`
- New Web Service from this repo, same build/start commands as `thelsa-automation-library`
- Environment:
  - `TENANT=<id>` (selects both settings files)
  - `DATABASE_URL` = the new database's internal URL
  - `MS_CLIENT_ID`, `MS_TENANT_ID`, `MS_CLIENT_SECRET`, `MS_REDIRECT_URI` from their IT
  - `TOKEN_ENC_KEY` = a NEW random key (never reuse Thelsa's)
  - `ASSISTANT_FEED_TOKEN_QUOTERELAY` = a NEW token, also set as `ASSISTANT_FEED_TOKEN_<ID>` on the MATT app
  - Leave every Moveware / Thelsa variable out unless that company uses Moveware
- Custom domain → DNS record at the company's (or InflectionPoint's) DNS host

## 5. Connect products
- QuoteRelay: tenant `<id>` must exist on MATT; set its `ASSISTANT_FEED_TOKEN_<ID>`
- WhatsApp AI Assistant: add `assistant_feed` to that company's tenant yaml with its own token variable
- Moveware/AuditTrack: only with that company's Moveware credentials; then set `"auditrack": {"enabled": true}`

## 6. Check
- Sign in as Bill → admin page shows the company name and logo, products "connected"
- Add one real user, connect their mailbox, press Refresh: only mail with them in the To line,
  only their own to-dos
