import json
import os
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

app = Flask(__name__)

SITE_NAME = os.getenv("SITE_NAME", "Internet Booty")
PRIZE_AMOUNT = os.getenv("PRIZE_AMOUNT", "$5,000")
LAUNCH_AT = os.getenv("LAUNCH_AT", "2026-10-14T18:24:00+00:00")
EARLY_PRICE = 10
REGULAR_PRICE = 20

app.secret_key = os.getenv("SECRET_KEY", secrets.token_urlsafe(48))
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("FLASK_DEBUG") != "1",
)

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_SEGMENT_ID = os.getenv("RESEND_SEGMENT_ID", "")
RESEND_TOPIC_ID = os.getenv("RESEND_TOPIC_ID", "")
RESEND_FROM = os.getenv("RESEND_FROM", "Internet Booty <crew@internetbooty.com>")

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,24}$")


def launch_datetime():
    value = LAUNCH_AT.replace("Z", "+00:00")
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def hunt_is_open():
    return datetime.now(timezone.utc) >= launch_datetime()


def hunt_gate(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not hunt_is_open():
            return redirect(url_for("home", locked="1"), code=302)
        return view(*args, **kwargs)
    return wrapped


def csrf_token():
    token = session.get("_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf"] = token
    return token


def valid_csrf():
    sent = request.form.get("_csrf", "")
    expected = session.get("_csrf", "")
    return bool(sent and expected and secrets.compare_digest(sent, expected))


def resend_request(method, path, payload=None):
    if not RESEND_API_KEY:
        raise RuntimeError("Email service is not configured.")

    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.resend.com{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "InternetBooty/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = {"message": raw or str(exc)}
        return exc.code, detail


def get_contact(email):
    encoded = urllib.parse.quote(email, safe="")
    status, data = resend_request("GET", f"/contacts/{encoded}")
    if status == 200:
        return data
    if status == 404:
        return None
    raise RuntimeError(data.get("message", "Unable to read account."))


def upsert_early_contact(email, username):
    properties = {
        "username": username,
        "internetbooty_access": "early_10_reserved",
        "registration_source": "captains_mark",
    }
    payload = {
        "email": email,
        "unsubscribed": False,
        "properties": properties,
    }
    if RESEND_SEGMENT_ID:
        payload["segments"] = [{"id": RESEND_SEGMENT_ID}]
    if RESEND_TOPIC_ID:
        payload["topics"] = [{"id": RESEND_TOPIC_ID, "subscription": "opt_in"}]

    status, data = resend_request("POST", "/contacts", payload)
    if status in (200, 201):
        return data

    if status == 409:
        encoded = urllib.parse.quote(email, safe="")
        status, data = resend_request(
            "PATCH",
            f"/contacts/{encoded}",
            {"unsubscribed": False, "properties": properties},
        )
        if status not in (200, 201):
            raise RuntimeError(data.get("message", "Unable to update account."))

        if RESEND_SEGMENT_ID:
            resend_request("POST", f"/contacts/{encoded}/segments/{RESEND_SEGMENT_ID}")
        if RESEND_TOPIC_ID:
            resend_request(
                "PATCH",
                f"/contacts/{encoded}/topics",
                {"topics": [{"id": RESEND_TOPIC_ID, "subscription": "opt_in"}]},
            )
        return data

    raise RuntimeError(data.get("message", "Unable to create account."))


def send_login_link(email):
    serializer = URLSafeTimedSerializer(app.secret_key)
    token = serializer.dumps(email, salt="crew-login")
    link = url_for("crew_magic", token=token, _external=True, _scheme="https")
    html = f"""
    <div style="font-family:Georgia,serif;background:#061018;color:#f5ecd8;padding:34px">
      <div style="max-width:560px;margin:auto;border:1px solid #b98c3a;padding:30px;background:#091821">
        <div style="font-size:12px;letter-spacing:3px;color:#d6a84c">INTERNET BOOTY</div>
        <h1 style="font-weight:400;color:#f2d486">Your crew sign-in link</h1>
        <p style="line-height:1.7;color:#d1d5d2">Use the button below to return to your Early Crew account. This link expires in 30 minutes.</p>
        <p style="margin:28px 0"><a href="{link}" style="background:#d6a84c;color:#071018;padding:14px 20px;text-decoration:none;font-weight:bold">BOARD THE SHIP →</a></p>
        <p style="font-size:12px;color:#89979a">If you did not request this, you can ignore this email.</p>
      </div>
    </div>
    """
    payload = {
        "from": RESEND_FROM,
        "to": [email],
        "subject": "Your Internet Booty crew sign-in link",
        "html": html,
        "text": f"Sign in to Internet Booty: {link}\n\nThis link expires in 30 minutes.",
    }
    status, data = resend_request("POST", "/emails", payload)
    if status not in (200, 201):
        raise RuntimeError(data.get("message", "Unable to send sign-in email."))


@app.context_processor
def inject_globals():
    return {
        "site_name": SITE_NAME,
        "prize_amount": PRIZE_AMOUNT,
        "launch_at": launch_datetime().isoformat(),
        "hunt_open": hunt_is_open(),
        "csrf_token": csrf_token,
        "early_price": EARLY_PRICE,
        "regular_price": REGULAR_PRICE,
    }


@app.get("/")
def home():
    return render_template("index.html", locked=request.args.get("locked") == "1")


@app.route("/captains-mark", methods=["GET", "POST"])
def early_access():
    if request.method == "POST":
        if not valid_csrf():
            abort(400)

        if request.form.get("website"):
            return redirect(url_for("early_access"))

        email = request.form.get("email", "").strip().lower()
        username = request.form.get("username", "").strip()
        consent = request.form.get("consent") == "yes"

        if not EMAIL_RE.match(email):
            flash("Enter a valid email address.", "error")
        elif not USERNAME_RE.match(username):
            flash("Pirate name must be 3–24 characters using letters, numbers, _ or -.", "error")
        elif not consent:
            flash("You need to opt into Early Crew emails to reserve early access.", "error")
        else:
            try:
                upsert_early_contact(email, username)
                session["crew_email"] = email
                session["crew_username"] = username
                session["early_registered"] = True
                flash("Your Early Crew account is aboard. Your $10 rate is reserved.", "success")
                return redirect(url_for("crew_account"))
            except Exception:
                app.logger.exception("Early access registration failed")
                flash("The signal was lost. Try again in a moment.", "error")

    return render_template("early_access.html")


@app.get("/crew")
def crew_account():
    email = session.get("crew_email")
    if not email:
        return redirect(url_for("crew_signin"))
    username = session.get("crew_username", "Crewmate")
    return render_template("crew.html", email=email, username=username)


@app.route("/crew/sign-in", methods=["GET", "POST"])
def crew_signin():
    if request.method == "POST":
        if not valid_csrf():
            abort(400)
        email = request.form.get("email", "").strip().lower()
        if not EMAIL_RE.match(email):
            flash("Enter the email used for your Early Crew account.", "error")
        else:
            try:
                contact = get_contact(email)
                access = (contact or {}).get("properties", {}).get("internetbooty_access")
                if contact and access:
                    send_login_link(email)
                # Always show the same response to avoid revealing registered emails.
                flash("If that address is aboard, a sign-in link is on its way.", "success")
            except Exception:
                app.logger.exception("Magic-link sign in failed")
                flash("Sign-in email is temporarily unavailable.", "error")
    return render_template("signin.html")


@app.get("/crew/magic/<token>")
def crew_magic(token):
    serializer = URLSafeTimedSerializer(app.secret_key)
    try:
        email = serializer.loads(token, salt="crew-login", max_age=1800)
    except (BadSignature, SignatureExpired):
        flash("That sign-in link has expired. Request another one.", "error")
        return redirect(url_for("crew_signin"))

    try:
        contact = get_contact(email)
    except Exception:
        contact = None

    if not contact:
        flash("We could not find that crew account.", "error")
        return redirect(url_for("crew_signin"))

    session["crew_email"] = email
    session["crew_username"] = contact.get("properties", {}).get("username") or "Crewmate"
    session["early_registered"] = True
    return redirect(url_for("crew_account"))


@app.post("/crew/logout")
def crew_logout():
    if not valid_csrf():
        abort(400)
    session.pop("crew_email", None)
    session.pop("crew_username", None)
    session.pop("early_registered", None)
    return redirect(url_for("home"))


@app.get("/hunt")
@hunt_gate
def hunt():
    return render_template("open.html", page_title="The Hunt", heading="The vault is open.")


@app.get("/map")
@hunt_gate
def map_page():
    return render_template("open.html", page_title="The Map", heading="The map has awakened.")


@app.get("/puzzles")
@hunt_gate
def puzzles():
    return render_template("open.html", page_title="Puzzle Rooms", heading="The first room awaits.")


@app.get("/archive")
@hunt_gate
def archive():
    return render_template("open.html", page_title="The Archive", heading="The archive is unsealed.")


@app.get("/api/status")
def status():
    now = datetime.now(timezone.utc)
    launch = launch_datetime()
    return jsonify({
        "open": now >= launch,
        "launchAt": launch.isoformat(),
        "secondsRemaining": max(0, int((launch - now).total_seconds())),
        "prize": PRIZE_AMOUNT,
    })


@app.get("/health")
def health():
    return {"status": "ok"}, 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG") == "1")
