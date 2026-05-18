# Thelsa Automation Library — Render Deployment Guide

## What changed
The Library no longer launches sub-apps as subprocesses or proxies traffic to them.
Each automation now lives as its own independent Render service (always-on, no laptops needed).
The Library is just the landing page + Google OAuth gate.

---

## Step 1 — Encode your Google credentials for Render

Run this once in Terminal from the Library folder:

```bash
python3 -c "
import base64, pathlib
print(base64.b64encode(pathlib.Path('web_credentials.json').read_bytes()).decode())
"
```

Copy the output string — you'll paste it into Render as `GOOGLE_CREDENTIALS_B64`.

---

## Step 2 — Create GitHub repos

You need two new GitHub repos (both projects currently have no git remote).

### Thelsa Automation Library

```bash
cd "/Users/williambrill/Documents/Claude/Projects/Thelsa Automation Library"
git init
git add .
git commit -m "Initial commit — refactored for Render deployment"
```

Then on github.com → New repository → name it `thelsa-automation-library` → Create.

```bash
git remote add origin https://github.com/<your-username>/thelsa-automation-library.git
git branch -M main
git push -u origin main
```

### TMS Corp Lead Gen Engine

```bash
cd "/Users/williambrill/Documents/Claude/Projects/TMS Corp Lead Gen Engine"
git init
git add .
git commit -m "Initial commit — Render deployment files added"
git remote add origin https://github.com/<your-username>/thelsa-lead-gen.git
git branch -M main
git push -u origin main
```

---

## Step 3 — Deploy on Render

Go to https://dashboard.render.com → New → Web Service.

### Deploy: Thelsa Automation Library
1. Connect the `thelsa-automation-library` GitHub repo.
2. Render detects `render.yaml` — confirm settings.
3. Under Environment Variables, add:
   - `GOOGLE_CREDENTIALS_B64` → paste the string from Step 1
4. Click **Create Web Service**.
5. Note the assigned URL — it will be something like `https://thelsa-automation-library.onrender.com`

### Deploy: TMS Lead Gen Engine
1. Connect the `thelsa-lead-gen` GitHub repo.
2. Render detects `render.yaml`.
3. Under Environment Variables, add:
   - `ANTHROPIC_API_KEY`
   - `SENDGRID_API_KEY`
   - `AGENT_EMAIL`
   - `AGENT_NAME`
   - `AGENT_TITLE`
   - `AGENCY_ADDRESS`
4. Click **Create Web Service**.

---

## Step 4 — Add Render URLs to Google OAuth

Go to https://console.cloud.google.com → APIs & Services → Credentials →
click your OAuth 2.0 Client ID.

Under **Authorised redirect URIs**, add:

```
https://thelsa-automation-library.onrender.com/auth/callback
```

(Also add the Lead Gen URL if it has its own OAuth flow later.)

Click **Save**.

---

## Step 5 — Update OAUTH_REDIRECT_URI in Render

In the Automation Library Render service → Environment:
- Set `OAUTH_REDIRECT_URI` to `https://thelsa-automation-library.onrender.com/auth/callback`
- Set `LEAD_GEN_URL` to the actual Lead Gen Render URL from Step 3

Trigger a manual redeploy after updating env vars.

---

## Done ✓

The Library will be live at its Render URL. Share that URL with the team.
No laptop required. No ngrok. No insomnia.

Any push to the GitHub `main` branch triggers an automatic redeploy on Render.
