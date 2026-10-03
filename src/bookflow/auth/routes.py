"""Admin login and logout routes."""

from __future__ import annotations

from flask import (
    Blueprint,
    Response,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from bookflow.auth.decorators import require_csrf
from bookflow.auth.service import (
    ADMIN_SESSION_KEY,
    ensure_csrf_token,
    safe_next_target,
)

bp = Blueprint("auth", __name__)

bp.before_request(require_csrf)


@bp.get("/admin/login")
def login() -> str | Response:
    if session.get(ADMIN_SESSION_KEY):
        return redirect(url_for("admin.dashboard"))
    return render_template("login.html", next_target=request.args.get("next", ""))


@bp.post("/admin/login")
def login_post() -> str | Response | tuple[str, int]:
    limiter = current_app.extensions["login_rate_limiter"]
    verifier = current_app.extensions["password_verifier"]
    username = request.form.get("username", "")
    password = request.form.get("password", "")
    next_target = request.form.get("next", "")
    key = (request.remote_addr or "unknown", username)

    if limiter.is_blocked(key):
        return (
            render_template(
                "login.html",
                error="Too many failed attempts. Please try again later.",
                next_target=next_target,
            ),
            429,
        )

    if not verifier.configured:
        return (
            render_template(
                "login.html",
                error="The admin password is not configured. Set OPDS_ADMIN_PASSWORD.",
                next_target=next_target,
            ),
            503,
        )

    if verifier.verify(username, password):
        limiter.reset(key)
        session.clear()
        session[ADMIN_SESSION_KEY] = True
        session.permanent = True
        ensure_csrf_token()
        target = safe_next_target(next_target) or url_for("admin.dashboard")
        return redirect(target)

    limiter.record_failure(key)
    return (
        render_template(
            "login.html",
            error="Invalid username or password.",
            next_target=next_target,
        ),
        401,
    )


@bp.post("/admin/logout")
def logout() -> Response:
    session.clear()
    return redirect(url_for("auth.login"))
