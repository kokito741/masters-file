from functools import wraps
from urllib.parse import urlencode

import requests
from flask import redirect, request, session, url_for


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("access_token"):
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)

    return wrapped


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("access_token"):
            return redirect(url_for("login"))
        if session.get("role") != "Admin":
            return {"error": "Forbidden"}, 403
        return view_func(*args, **kwargs)

    return wrapped


def build_authorize_url(config, state):
    params = {
        "client_id": config.KEYROCK_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": config.CALLBACK_URL,
        "state": state,
    }
    return f"{config.KEYROCK_PUBLIC_URL.rstrip('/')}/oauth2/authorize?{urlencode(params)}"


def exchange_code_for_token(config, code):
    token_url = f"{config.KEYROCK_URL.rstrip('/')}/oauth2/token"
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.CALLBACK_URL,
    }
    response = requests.post(
        token_url,
        data=data,
        auth=(config.KEYROCK_CLIENT_ID, config.KEYROCK_CLIENT_SECRET),
        timeout=config.REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def _extract_role_name(item):
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return item.get("name") or item.get("role") or ""
    return ""


def infer_role_from_userinfo(userinfo):
    role_sources = []
    for key in ("roles", "organizations", "apps", "applications"):
        value = userinfo.get(key)
        if isinstance(value, list):
            role_sources.extend(value)

    for parent in (userinfo.get("app_organizations"), userinfo.get("organizations_info")):
        if isinstance(parent, list):
            for entry in parent:
                if isinstance(entry, dict):
                    roles = entry.get("roles")
                    if isinstance(roles, list):
                        role_sources.extend(roles)

    normalized = {(_extract_role_name(item) or "").strip().lower() for item in role_sources}
    if "admin" in normalized:
        return "Admin"
    return "Viewer"


def fetch_user_info(config, access_token):
    user_url = f"{config.KEYROCK_URL.rstrip('/')}/user"
    response = requests.get(
        user_url,
        params={"access_token": access_token},
        timeout=config.REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def logout_session():
    session.clear()


def callback_error_response(error_text):
    if request.accept_mimetypes.best == "application/json":
        return {"error": error_text}, 400
    # Show the reason rather than bouncing to /login, which produces an
    # invisible redirect loop when the callback fails.
    return f"<h3>Login failed</h3><pre>{error_text}</pre>", 400
