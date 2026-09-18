"""
Thelsa Automation Library — Dashboard
Serves the library landing page and authenticates team members via
Google OAuth *and* Microsoft (Entra) sign-in.
Access is restricted to an allow-list of email domains (thelsa.com +
inflectionpointnow.com by default).

Each automation tool runs as its own independent Render service; this app
just links out to them.

Local dev : python3 app.py → http://localhost:5000
Production: deployed to Render — always-on, no ngrok required.

Required environment variables (set in Render dashboard):
  FLASK_SECRET_KEY      — random secret, use Render's "generate" button
  OAUTH_REDIRECT_URI    — https://<your-service>.onrender.com/auth/callback   (Google)
  GOOGLE_CREDENTIALS_B64 — base64-encoded contents of web_credentials.json
  RATE_ENGINE_URL       — URL of the OA-DA Rate Engine Render service
  LEAD_GEN_URL          — URL of the TMS Lead Gen Engine Render service

Optional (enables "Sign in with Microsoft" — needed for @thelsa.com / M365 users):
  MS_CLIENT_ID          — Entra app (client) ID
  MS_CLIENT_SECRET      — Entra app client secret
  MS_TENANT_ID          — Entra directory (tenant) ID
  MS_REDIRECT_URI       — https://<host>/auth/callback/microsoft (must match Entra registration)

Optional access control:
  ALLOWED_EMAIL_DOMAINS — comma-separated (default: "thelsa.com,inflectionpointnow.com")
"""

import base64
import functools
import hashlib
import json
import logging
import os
import re
import secrets
from datetime import timedelta
from pathlib import Path

from flask import Flask, redirect, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

# ── App setup ──────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder="static", static_url_path="/static")
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "thelsa-lib-change-me-in-production")
app.permanent_session_lifetime = timedelta(days=7)

# Trust reverse-proxy headers from Render (and ngrok for local testing)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Allow OAuth over plain HTTP only in local dev (Render always uses HTTPS)
if os.environ.get("FLASK_ENV") == "development":
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

BASE = Path(__file__).resolve().parent
TOKEN_DIR = BASE / "data" / "tokens"
TOKEN_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ── Access control (email-domain allow-list) ───────────────────────────────────
ALLOWED_DOMAINS = {
    d.strip().lower()
    for d in os.environ.get(
        "ALLOWED_EMAIL_DOMAINS", "thelsa.com,inflectionpointnow.com"
    ).split(",")
    if d.strip()
}


def _email_allowed(email: str) -> bool:
    """True only if the email's domain is on the allow-list."""
    email = (email or "").lower().strip()
    return "@" in email and email.rsplit("@", 1)[1] in ALLOWED_DOMAINS


def _deny_page(email: str):
    """Signed-in but not authorized — clear session and show a clear message."""
    session.clear()
    allowed = " and ".join(f"@{d}" for d in sorted(ALLOWED_DOMAINS))
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Access restricted — Thelsa Automation Library</title>
<style>
  body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
       background:#f5f6f8;color:#1a1a2e;display:flex;align-items:center;justify-content:center;
       min-height:100vh;padding:24px;}}
  .box{{background:#fff;max-width:440px;width:100%;border-radius:14px;padding:36px 32px;
       box-shadow:0 8px 30px rgba(0,0,0,.08);text-align:center;}}
  img{{height:40px;margin-bottom:18px;}}
  h1{{font-size:20px;margin:0 0 10px;color:#c0392b;}}
  p{{font-size:15px;line-height:1.5;color:#444;margin:0 0 8px;}}
  .email{{font-weight:600;color:#1a1a2e;}}
  a{{display:inline-block;margin-top:18px;color:#1967d2;text-decoration:none;font-weight:600;}}
</style></head><body>
  <div class="box">
    <img src="/static/thelsa_logo.png" alt="Thelsa">
    <h1>Access restricted</h1>
    <p>The Thelsa Automation Library is limited to {allowed} accounts.</p>
    <p>You're signed in as <span class="email">{email}</span>, which isn't authorized.</p>
    <a href="/logout">Try a different account</a>
  </div></body></html>"""
    return html, 403


# ── Microsoft (Entra) sign-in config ───────────────────────────────────────────
MS_CLIENT_ID = os.environ.get("MS_CLIENT_ID", "").strip()
MS_CLIENT_SECRET = os.environ.get("MS_CLIENT_SECRET", "").strip()
MS_TENANT_ID = os.environ.get("MS_TENANT_ID", "").strip()
MS_REDIRECT_URI = os.environ.get("MS_REDIRECT_URI", "").strip()
MS_AUTHORITY = f"https://login.microsoftonline.com/{MS_TENANT_ID}" if MS_TENANT_ID else ""
MS_SCOPES = ["User.Read"]  # openid/profile/email are added automatically by MSAL


def microsoft_enabled() -> bool:
    return bool(MS_CLIENT_ID and MS_CLIENT_SECRET and MS_TENANT_ID and MS_REDIRECT_URI)


def _msal_app():
    import msal
    return msal.ConfidentialClientApplication(
        MS_CLIENT_ID, authority=MS_AUTHORITY, client_credential=MS_CLIENT_SECRET
    )


# ── Google OAuth helpers ───────────────────────────────────────────────────────
GMAIL_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]


def _load_client_config() -> dict:
    """Load Google OAuth client config.

    Priority:
      1. GOOGLE_CREDENTIALS_B64 env var (base64-encoded JSON) — used on Render
         so the secret never touches the repo.
      2. web_credentials.json in the project root — used in local dev.
    """
    creds_b64 = os.environ.get("GOOGLE_CREDENTIALS_B64", "").strip()
    if creds_b64:
        return json.loads(base64.b64decode(creds_b64).decode())
    path = BASE / "web_credentials.json"
    if path.exists():
        return json.loads(path.read_text())
    raise RuntimeError(
        "No Google OAuth credentials found.\n"
        "  Local dev : place web_credentials.json in the project root.\n"
        "  Render    : set GOOGLE_CREDENTIALS_B64 to base64-encoded JSON.\n"
        "  Encode    : python3 -c \"import base64,pathlib; "
        "print(base64.b64encode(pathlib.Path('web_credentials.json').read_bytes()).decode())\""
    )


def _callback_uri() -> str:
    """Return the Google OAuth callback URI."""
    override = os.environ.get("OAUTH_REDIRECT_URI", "").strip()
    if override:
        return override
    proto = request.headers.get("X-Forwarded-Proto", "http")
    host = request.headers.get("X-Forwarded-Host", request.host)
    return f"{proto}://{host}/auth/callback"


def _make_flow(redirect_uri: str):
    from google_auth_oauthlib.flow import Flow
    return Flow.from_client_config(
        _load_client_config(), scopes=GMAIL_SCOPES, redirect_uri=redirect_uri
    )


def _token_path(email: str) -> Path:
    safe = re.sub(r"[^a-z0-9]", "_", email.lower())
    return TOKEN_DIR / f"{safe}.json"


def _save_token(email: str, credentials) -> None:
    data = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": list(credentials.scopes or []),
    }
    _token_path(email).write_text(json.dumps(data))


def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_email"):
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return decorated


# ── Login: provider choice page ────────────────────────────────────────────────
def _login_page(next_url: str) -> str:
    q = f"?next={next_url}" if next_url else ""
    ms_button = ""
    if microsoft_enabled():
        ms_button = f"""
        <a class="btn btn-ms" href="/login/microsoft{q}">
          <span class="ms-logo" aria-hidden="true">
            <span style="background:#f25022"></span><span style="background:#7fba00"></span>
            <span style="background:#00a4ef"></span><span style="background:#ffb900"></span>
          </span>
          Sign in with Microsoft
        </a>"""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in — Thelsa Automation Library</title>
<style>
  body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
       background:#f5f6f8;color:#1a1a2e;display:flex;align-items:center;justify-content:center;
       min-height:100vh;padding:24px;}}
  .box{{background:#fff;max-width:400px;width:100%;border-radius:14px;padding:40px 32px;
       box-shadow:0 8px 30px rgba(0,0,0,.08);text-align:center;}}
  img.logo{{height:44px;margin-bottom:22px;}}
  h1{{font-size:19px;margin:0 0 6px;}}
  p.sub{{font-size:14px;color:#666;margin:0 0 28px;}}
  .btn{{display:flex;align-items:center;justify-content:center;gap:10px;width:100%;
       box-sizing:border-box;padding:12px 16px;margin:0 0 12px;border-radius:10px;
       border:1px solid #e0e0e0;background:#fff;color:#1a1a2e;font-size:15px;font-weight:600;
       text-decoration:none;transition:background .15s,box-shadow .15s;}}
  .btn:hover{{background:#fafafa;box-shadow:0 2px 8px rgba(0,0,0,.06);}}
  .btn-google img{{height:18px;}}
  .ms-logo{{display:grid;grid-template-columns:9px 9px;grid-gap:2px;}}
  .ms-logo span{{width:9px;height:9px;display:block;}}
  .note{{font-size:12px;color:#999;margin-top:20px;line-height:1.5;}}
</style></head><body>
  <div class="box">
    <img class="logo" src="/static/thelsa_logo.png" alt="Thelsa">
    <h1>Automation Library</h1>
    <p class="sub">Sign in to continue</p>
    <a class="btn btn-google" href="/login/google{q}">
      <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
        <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
        <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
        <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>
        <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
      </svg>
      Sign in with Google
    </a>
    {ms_button}
    <p class="note">Access is limited to authorized Thelsa and InflectionPoint accounts.</p>
  </div></body></html>"""


# ── Auth routes ────────────────────────────────────────────────────────────────
@app.route("/login")
def login():
    """Show the provider-choice page (Google + Microsoft when configured)."""
    next_url = request.args.get("next", url_for("index"))
    return _login_page(next_url)


@app.route("/login/google")
def login_google():
    session["oauth_next"] = request.args.get("next", url_for("index"))
    cb = _callback_uri()
    flow = _make_flow(cb)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        code_challenge=code_challenge,
        code_challenge_method="S256",
    )
    session["oauth_state"] = state
    session["oauth_code_verifier"] = code_verifier
    return redirect(auth_url)


@app.route("/auth/callback")
def auth_callback():
    flow = _make_flow(_callback_uri())
    try:
        flow.fetch_token(
            authorization_response=request.url,
            code_verifier=session.get("oauth_code_verifier", ""),
        )
    except Exception as exc:
        return f"OAuth token exchange failed: {exc}", 400

    creds = flow.credentials
    try:
        from googleapiclient.discovery import build
        svc = build("oauth2", "v2", credentials=creds, cache_discovery=False)
        info = svc.userinfo().get().execute()
    except Exception as exc:
        return f"Failed to fetch user info: {exc}", 400

    email = (info.get("email") or "").lower()
    if not _email_allowed(email):
        logger.info(f"Denied (Google, domain not allowed): {email}")
        return _deny_page(email)

    session["user_email"] = email
    session["user_name"] = info.get("name", email)
    session["auth_provider"] = "google"
    session.permanent = True
    _save_token(email, creds)
    logger.info(f"Login (Google): {email}")
    return redirect(session.pop("oauth_next", url_for("index")))


@app.route("/login/microsoft")
def login_microsoft():
    if not microsoft_enabled():
        return "Microsoft sign-in is not configured on this deployment.", 503
    session["oauth_next"] = request.args.get("next", url_for("index"))
    state = secrets.token_urlsafe(24)
    session["ms_state"] = state
    auth_url = _msal_app().get_authorization_request_url(
        MS_SCOPES,
        state=state,
        redirect_uri=MS_REDIRECT_URI,
        prompt="select_account",
    )
    return redirect(auth_url)


@app.route("/auth/callback/microsoft")
def auth_callback_microsoft():
    if not microsoft_enabled():
        return "Microsoft sign-in is not configured on this deployment.", 503
    if request.args.get("state") != session.get("ms_state"):
        return "Invalid state — please try signing in again.", 400
    if request.args.get("error"):
        return (f"Microsoft sign-in failed: "
                f"{request.args.get('error_description', request.args.get('error'))}"), 400
    code = request.args.get("code")
    if not code:
        return "Microsoft sign-in failed: no authorization code returned.", 400

    result = _msal_app().acquire_token_by_authorization_code(
        code, scopes=MS_SCOPES, redirect_uri=MS_REDIRECT_URI
    )
    if "error" in result:
        return (f"Microsoft token exchange failed: "
                f"{result.get('error_description', result.get('error'))}"), 400

    claims = result.get("id_token_claims", {}) or {}
    email = (claims.get("preferred_username") or claims.get("email") or "").lower()
    name = claims.get("name", email)

    if not _email_allowed(email):
        logger.info(f"Denied (Microsoft, domain not allowed): {email}")
        return _deny_page(email)

    session["user_email"] = email
    session["user_name"] = name
    session["auth_provider"] = "microsoft"
    session.permanent = True
    logger.info(f"Login (Microsoft): {email}")
    return redirect(session.pop("oauth_next", url_for("index")))


@app.route("/logout")
def logout():
    logger.info(f"Logout: {session.get('user_email')}")
    session.clear()
    return redirect(url_for("login"))


# ── Main routes ────────────────────────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    html = (BASE / "index.html").read_text()
    name = session.get("user_name", "")
    email = session.get("user_email", "")
    html = html.replace("{{USER_NAME}}", name).replace("{{USER_EMAIL}}", email)
    return html


@app.route("/launch/<key>")
@login_required
def launch(key):
    """Redirect to the sub-app's Render URL. The sub-app handles its own auth."""
    url = RENDER_URLS.get(key)
    if not url:
        return f"Unknown automation: {key}", 404
    return redirect(url)


@app.route("/run/<key>")
@login_required
def run_now(key):
    """'Run Now' button — triggers the sub-app pipeline, then shows its dashboard."""
    url = RENDER_URLS.get(key)
    if not url:
        return f"Unknown automation: {key}", 404
    # For the lead-gen engine, hit /trigger to start the pipeline
    if key == "lead-gen":
        return redirect(f"{url}/trigger")
    return redirect(url)


# ── Sub-app Render URLs ─────────────────────────────────────────────────────────
# Override with RATE_ENGINE_URL / LEAD_GEN_URL env vars in Render dashboard.
RENDER_URLS = {
    "rate-engine": os.environ.get(
        "RATE_ENGINE_URL", "https://thelsa-rate-engine.onrender.com"
    ),
    "lead-gen": os.environ.get(
        "LEAD_GEN_URL", "https://thelsa-lead-gen.onrender.com"
    ),
    "cross-border-engine": os.environ.get(
        "CROSS_BORDER_ENGINE_URL", "https://thelsa-cross-border-engine.onrender.com"
    ),
}


# ── Health / keep-alive ─────────────────────────────────────────────────────────
@app.route("/health")
def health():
    """Unauthenticated health-check used by keep-alive cron to prevent cold starts."""
    return {"status": "ok"}, 200


@app.route("/ping")
def ping():
    """Alias of /health for compatibility with UptimeRobot / external monitors."""
    return "pong", 200


# ── Blueprints (sub-app dashboards mounted in-process) ──────────────────────────
# Email Campaigns dashboard
from campaigns import campaigns_bp
app.register_blueprint(campaigns_bp)
# Engine HTTP triggers (test draft + scheduled draft/monitor)
from engine_web import engine_bp
app.register_blueprint(engine_bp)
# Move-File Cost & Profit Audit dashboard
from audit_web import audit_bp
app.register_blueprint(audit_bp)
# FAIM Move-File Quality Audit dashboard (in-app, reuses the library Google login)
from faim_web import faim_bp
app.register_blueprint(faim_bp)
# Cross-Border Shipment Dashboard (TIM/ClickUp + TMS/Moveware, unified)
from crossborder.web import crossborder_bp
app.register_blueprint(crossborder_bp)


# ── Entry point ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # Allow plain HTTP only for local dev
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    print(f"\n Thelsa Automation Library — http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
