import importlib.util
import logging
from pathlib import Path
from typing import List
from urllib.parse import quote

from fastapi import FastAPI, Request, Form, Header
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from jinja2 import Environment, FileSystemLoader

from src.config import SESSION_SECRET_KEY, GOOGLE_CLIENT_ID, CRON_SECRET, PROJECT_ROOT, ADMIN_EMAIL
from src.database import (
    add_subscription,
    count_all_digests,
    deactivate_subscription,
    dismiss_newsletter,
    get_active_subscriptions,
    get_admin_user_stats,
    get_all_subscriptions,
    get_digest_by_id,
    get_digests_for_user,
    get_dismissed_sender_emails,
    get_user_by_id,
    init_db,
    update_subscription_status,
)
from src.web.gmail_client import (
    get_authorization_url,
    exchange_code,
    fetch_recent_emails,
    detect_newsletters,
    get_user_email,
)
from src.web.token_storage import save_user_tokens, get_user_id_by_email, get_all_users_with_tokens

logger = logging.getLogger(__name__)

app = FastAPI(title="Newsletter Digest")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET_KEY)

init_db()

templates_dir = Path(__file__).parent / "templates"
jinja_env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_user_email(request: Request):
    """Return the user email from the session, or None."""
    return request.session.get("user_email")


def _require_auth(request: Request):
    """Return user_email if authenticated, otherwise a redirect response."""
    email = _get_user_email(request)
    if not email:
        return None, RedirectResponse("/?error=Please+connect+your+Gmail+account+first")
    return email, None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    """Landing page with 'Connect with Gmail' button."""
    error = request.query_params.get("error")
    success = request.query_params.get("success")
    template = jinja_env.get_template("landing.html")
    return template.render(error=error, success=success)


@app.get("/auth/google")
async def auth_google():
    """Redirect the user to the Google OAuth consent screen."""
    if not GOOGLE_CLIENT_ID:
        return RedirectResponse("/?error=Google+OAuth+credentials+not+configured")
    url = get_authorization_url()
    return RedirectResponse(url)


@app.get("/auth/callback")
async def auth_callback(request: Request):
    """Handle the OAuth callback from Google."""
    error = request.query_params.get("error")
    if error:
        return RedirectResponse("/?error={}".format(error))

    code = request.query_params.get("code")
    if not code:
        return RedirectResponse("/?error=No+authorization+code+received")

    creds_data = exchange_code(code)
    user_email = get_user_email(creds_data)
    save_user_tokens(user_email, creds_data)
    request.session["google_creds"] = creds_data
    request.session["user_email"] = user_email
    return RedirectResponse("/dashboard")


@app.get("/newsletters", response_class=HTMLResponse)
async def newsletters(request: Request):
    """Show subscribed and detected newsletters."""
    creds_data = request.session.get("google_creds")
    if not creds_data:
        return RedirectResponse("/?error=Please+connect+your+Gmail+account+first")

    user_email = request.session.get("user_email")
    if not user_email:
        user_email = get_user_email(creds_data)
        request.session["user_email"] = user_email

    user_id = get_user_id_by_email(user_email) or 1

    # Current active subscriptions
    subscriptions = get_active_subscriptions(user_id)
    subscribed_emails = {sub.sender_email for sub in subscriptions}

    # Dismissed newsletters (hidden from detected list)
    dismissed_emails = get_dismissed_sender_emails(user_id)

    # Detect newsletters from inbox, filtering self-emails and digest subjects
    emails = fetch_recent_emails(creds_data, max_results=100)
    all_detected = detect_newsletters(emails, user_email=user_email)

    # Only show detected newsletters not already subscribed and not dismissed
    detected = [
        nl for nl in all_detected
        if nl["sender_email"] not in subscribed_emails
        and nl["sender_email"] not in dismissed_emails
    ]

    template = jinja_env.get_template("newsletters.html")
    return template.render(subscriptions=subscriptions, detected=detected)


@app.post("/newsletters/save")
async def save_newsletters(request: Request):
    """Save newsletter subscription changes."""
    creds_data = request.session.get("google_creds")
    if not creds_data:
        return RedirectResponse("/?error=Please+connect+your+Gmail+account+first", status_code=303)

    user_email = request.session.get("user_email")
    if not user_email:
        user_email = get_user_email(creds_data)
        request.session["user_email"] = user_email

    user_id = get_user_id_by_email(user_email) or 1

    form_data = await request.form()

    # Existing subscriptions: all_sub_ids lists every subscription shown on page;
    # keep_sub lists only the checked ones. Unchecked → deactivate.
    all_sub_ids = set(form_data.getlist("all_sub_ids"))
    kept_sub_ids = set(form_data.getlist("keep_sub"))
    for sub_id_str in all_sub_ids:
        if sub_id_str not in kept_sub_ids:
            update_subscription_status(int(sub_id_str), False)

    # Detected newsletters: checked → add subscription
    sender_names = {
        k.removeprefix("sender_name_"): v
        for k, v in form_data.items()
        if k.startswith("sender_name_")
    }
    added = 0
    for sender_email in dict.fromkeys(form_data.getlist("add_detected")):
        sender_name = sender_names.get(sender_email, sender_email)
        add_subscription(sender_email=sender_email, sender_name=sender_name, user_id=user_id)
        added += 1

    msg = quote("Subscriptions updated!")
    return RedirectResponse("/dashboard?success={}".format(msg), status_code=303)


@app.post("/newsletters/dismiss")
async def dismiss_newsletter_route(request: Request):
    """Dismiss a detected newsletter so it no longer appears in the list."""
    creds_data = request.session.get("google_creds")
    if not creds_data:
        return RedirectResponse("/?error=Please+connect+your+Gmail+account+first", status_code=303)

    user_email = request.session.get("user_email")
    user_id = (get_user_id_by_email(user_email) or 1) if user_email else 1

    form_data = await request.form()
    sender_email = form_data.get("sender_email")
    if sender_email:
        dismiss_newsletter(sender_email, user_id)

    return RedirectResponse("/newsletters", status_code=303)


# ---------------------------------------------------------------------------
# Dashboard routes
# ---------------------------------------------------------------------------

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard showing recent digests."""
    user_email, redirect = _require_auth(request)
    if redirect:
        return redirect

    success = request.query_params.get("success")
    digests = get_digests_for_user(user_email, limit=30)
    template = jinja_env.get_template("dashboard.html")
    return template.render(
        user_email=user_email,
        digests=digests,
        success=success,
    )


@app.get("/dashboard/digest/{digest_id}", response_class=HTMLResponse)
async def view_digest(request: Request, digest_id: int):
    """View a specific past digest."""
    user_email, redirect = _require_auth(request)
    if redirect:
        return redirect

    digest = get_digest_by_id(digest_id)
    if not digest or digest["user_email"] != user_email:
        return RedirectResponse("/dashboard")

    template = jinja_env.get_template("digest_view.html")
    return template.render(digest=digest)


@app.get("/dashboard/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Manage subscriptions and delivery preferences."""
    user_email, redirect = _require_auth(request)
    if redirect:
        return redirect

    user_id = get_user_id_by_email(user_email) or 1
    subscriptions = get_all_subscriptions(user_id)
    success = request.query_params.get("success")
    template = jinja_env.get_template("settings.html")
    return template.render(
        user_email=user_email,
        subscriptions=subscriptions,
        success=success,
    )


@app.post("/dashboard/settings", response_class=HTMLResponse)
async def save_settings(request: Request):
    """Save subscription and delivery settings."""
    user_email, redirect = _require_auth(request)
    if redirect:
        return redirect

    user_id = get_user_id_by_email(user_email) or 1
    form_data = await request.form()

    # Active subscription IDs come back as a list of checked checkboxes
    active_ids = set(form_data.getlist("active_subscriptions"))

    # Update each subscription's active status
    all_subs = get_all_subscriptions(user_id)
    for sub in all_subs:
        should_be_active = str(sub.id) in active_ids
        if sub.is_active != should_be_active:
            update_subscription_status(sub.id, should_be_active)

    msg = quote("Settings saved!")
    return RedirectResponse("/dashboard/settings?success={}".format(msg), status_code=303)


# ---------------------------------------------------------------------------
# Static / informational pages
# ---------------------------------------------------------------------------

@app.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    """Privacy policy page."""
    template = jinja_env.get_template("privacy.html")
    return template.render()


# ---------------------------------------------------------------------------
# Admin dashboard
# ---------------------------------------------------------------------------

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    """Admin dashboard — only accessible to the configured ADMIN_EMAIL."""
    user_email, redirect = _require_auth(request)
    if redirect:
        return redirect

    if not ADMIN_EMAIL or user_email != ADMIN_EMAIL:
        return RedirectResponse("/dashboard")

    users = get_admin_user_stats()
    for u in users:
        u["signup_date"] = str(u.get("created_at") or "")[:10] or "—"
        u["last_digest"] = str(u.get("last_digest_date") or "")[:10] or "—"

    total_subscriptions = sum(u.get("subscription_count") or 0 for u in users)
    total_digests = count_all_digests()

    template = jinja_env.get_template("admin.html")
    return template.render(
        admin_email=user_email,
        users=users,
        total_subscriptions=total_subscriptions,
        total_digests=total_digests,
    )


@app.get("/admin/user/{user_id}", response_class=HTMLResponse)
async def admin_user_detail(request: Request, user_id: int):
    """Admin view of a single user's subscriptions."""
    user_email, redirect = _require_auth(request)
    if redirect:
        return redirect

    if not ADMIN_EMAIL or user_email != ADMIN_EMAIL:
        return RedirectResponse("/dashboard")

    user = get_user_by_id(user_id)
    if not user:
        return RedirectResponse("/admin")

    user["signup_date"] = str(user.get("created_at") or "")[:10] or "—"
    subscriptions = get_all_subscriptions(user_id)
    digests = get_digests_for_user(user["email"], limit=10)

    template = jinja_env.get_template("admin_user.html")
    return template.render(
        admin_email=user_email,
        user=user,
        subscriptions=subscriptions,
        digests=digests,
    )


# ---------------------------------------------------------------------------
# Scheduled job API
# ---------------------------------------------------------------------------


def _load_run_daily():
    """Lazily import the run() function from scripts/run_daily.py."""
    spec = importlib.util.spec_from_file_location(
        "run_daily", str(PROJECT_ROOT / "scripts" / "run_daily.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run


@app.post("/api/run-digest")
async def run_digest(request: Request, x_cron_secret: str = Header(None)):
    """Trigger the digest pipeline for all users with stored OAuth tokens.

    Protected by a shared secret passed in the ``X-Cron-Secret`` header.
    Intended to be called by Railway cron or an equivalent scheduler.
    """
    if not CRON_SECRET:
        return JSONResponse(
            {"error": "CRON_SECRET is not configured on the server"},
            status_code=500,
        )

    if x_cron_secret != CRON_SECRET:
        return JSONResponse({"error": "Invalid or missing X-Cron-Secret"}, status_code=401)

    hours = int(request.query_params.get("hours", 24))

    users = get_all_users_with_tokens()
    if not users:
        return JSONResponse({"status": "ok", "message": "No users with OAuth tokens found", "results": []})

    run_pipeline = _load_run_daily()

    results = []
    for user_email in users:
        try:
            logger.info("Running digest pipeline for %s", user_email)
            run_pipeline(dry_run=False, hours=hours, force=False, user=user_email)
            results.append({"user": user_email, "status": "success"})
        except Exception as e:
            logger.error("Digest pipeline failed for %s: %s", user_email, e)
            results.append({"user": user_email, "status": "error", "error": str(e)})

    succeeded = sum(1 for r in results if r["status"] == "success")
    failed = sum(1 for r in results if r["status"] == "error")

    return JSONResponse({
        "status": "ok",
        "users_processed": len(results),
        "succeeded": succeeded,
        "failed": failed,
        "results": results,
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.web.app:app", host="127.0.0.1", port=8000, reload=True)
