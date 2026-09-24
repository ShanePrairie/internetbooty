import base64
import hashlib
import hmac
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
EARLY_PRICE_CENTS = int(os.getenv("EARLY_PRICE_CENTS", "1000"))
REGULAR_PRICE_CENTS = int(os.getenv("REGULAR_PRICE_CENTS", "2000"))

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

SQUARE_ACCESS_TOKEN = os.getenv("SQUARE_ACCESS_TOKEN", "")
SQUARE_LOCATION_ID = os.getenv("SQUARE_LOCATION_ID", "")
SQUARE_API_VERSION = os.getenv("SQUARE_API_VERSION", "2026-09-16")
SQUARE_BASE_URL = os.getenv("SQUARE_BASE_URL", "https://connect.squareup.com")
SQUARE_WEBHOOK_SIGNATURE_KEY = os.getenv("SQUARE_WEBHOOK_SIGNATURE_KEY", "")
SQUARE_WEBHOOK_NOTIFICATION_URL = os.getenv(
    "SQUARE_WEBHOOK_NOTIFICATION_URL",
    "https://internetbooty.onrender.com/webhooks/square",
)

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


def registration_serializer():
    return URLSafeTimedSerializer(app.secret_key)


def make_registration_token(email, username, amount_cents):
    return registration_serializer().dumps(
        {
            "email": email,
            "username": username,
            "amount": int(amount_cents),
            "tier": "early" if int(amount_cents) == EARLY_PRICE_CENTS else "standard",
        },
        salt="square-entry",
    )


def read_registration_token(token, max_age=7 * 24 * 3600):
    return registration_serializer().loads(token, salt="square-entry", max_age=max_age)


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


def contact_is_paid(contact):
    props = (contact or {}).get("properties", {})
    return props.get("entry_status") == "paid" and bool(props.get("square_payment_id"))


def activate_contact(email, username, payment_id, amount_cents, paid_at):
    tier = "early_10_paid" if int(amount_cents) == EARLY_PRICE_CENTS else "standard_20_paid"
    properties = {
        "username": username,
        "internetbooty_access": tier,
        "registration_source": "captains_mark" if tier.startswith("early") else "standard_checkout",
        "square_payment_id": payment_id,
        "amount_paid": int(amount_cents) / 100,
        "paid_at": paid_at,
        "entry_status": "paid",
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
            raise RuntimeError(data.get("message", "Unable to activate account."))

        if RESEND_SEGMENT_ID:
            resend_request("POST", f"/contacts/{encoded}/segments/{RESEND_SEGMENT_ID}")
        if RESEND_TOPIC_ID:
            resend_request(
                "PATCH",
                f"/contacts/{encoded}/topics",
                {"topics": [{"id": RESEND_TOPIC_ID, "subscription": "opt_in"}]},
            )
        return data

    raise RuntimeError(data.get("message", "Unable to activate account."))


def login_crew(email, username, amount_cents=EARLY_PRICE_CENTS):
    session["crew_email"] = email
    session["crew_username"] = username
    session["crew_amount_paid"] = int(amount_cents) / 100
    session["early_registered"] = int(amount_cents) == EARLY_PRICE_CENTS


def send_welcome_email(email, username, amount_cents):
    amount = int(amount_cents) / 100
    link = url_for("crew_signin", _external=True, _scheme="https")
    html = f"""
    <div style="font-family:Georgia,serif;background:#061018;color:#f5ecd8;padding:34px">
      <div style="max-width:560px;margin:auto;border:1px solid #b98c3a;padding:30px;background:#091821">
        <div style="font-size:12px;letter-spacing:3px;color:#d6a84c">INTERNET BOOTY</div>
        <h1 style="font-weight:400;color:#f2d486">You're officially aboard.</h1>
        <p style="line-height:1.7;color:#d1d5d2">Welcome, {username}. Your &#36;{amount:.0f} entry has been confirmed and your crew account is active.</p>
        <p style="margin:28px 0"><a href="{link}" style="background:#d6a84c;color:#071018;padding:14px 20px;text-decoration:none;font-weight:bold">OPEN YOUR ACCOUNT →</a></p>
        <p style="font-size:12px;color:#89979a">We'll use this address for account messages and hunt updates you opted into.</p>
      </div>
    </div>
    """
    payload = {
        "from": RESEND_FROM,
        "to": [email],
        "subject": "You're aboard — Internet Booty entry confirmed",
        "html": html,
        "text": f"Welcome, {username}. Your USD {amount:.0f} Internet Booty entry is confirmed. Account sign-in: {link}",
    }
    status, data = resend_request("POST", "/emails", payload)
    if status not in (200, 201):
        raise RuntimeError(data.get("message", "Unable to send confirmation email."))


def send_login_link(email):
    serializer = URLSafeTimedSerializer(app.secret_key)
    token = serializer.dumps(email, salt="crew-login")
    link = url_for("crew_magic", token=token, _external=True, _scheme="https")
    html = f"""
    <div style="font-family:Georgia,serif;background:#061018;color:#f5ecd8;padding:34px">
      <div style="max-width:560px;margin:auto;border:1px solid #b98c3a;padding:30px;background:#091821">
        <div style="font-size:12px;letter-spacing:3px;color:#d6a84c">INTERNET BOOTY</div>
        <h1 style="font-weight:400;color:#f2d486">Your crew sign-in link</h1>
        <p style="line-height:1.7;color:#d1d5d2">Use the button below to return to your account. This link expires in 30 minutes.</p>
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


def square_request(method, path, payload=None):
    if not SQUARE_ACCESS_TOKEN:
        raise RuntimeError("Square checkout is not configured.")

    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{SQUARE_BASE_URL}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {SQUARE_ACCESS_TOKEN}",
            "Square-Version": SQUARE_API_VERSION,
            "Content-Type": "application/json",
            "User-Agent": "InternetBooty/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = {"errors": [{"detail": raw or str(exc)}]}
        return exc.code, detail


def square_error_message(data):
    errors = data.get("errors") or []
    if errors:
        return errors[0].get("detail") or errors[0].get("code") or "Square request failed."
    return "Square request failed."


def square_location_id():
    if SQUARE_LOCATION_ID:
        return SQUARE_LOCATION_ID

    status, data = square_request("GET", "/v2/locations")
    if status != 200:
        raise RuntimeError(square_error_message(data))

    locations = data.get("locations") or []
    active = [loc for loc in locations if loc.get("status") == "ACTIVE"]
    if not active:
        raise RuntimeError("No active Square location is available.")
    return active[0]["id"]


def create_square_checkout(email, username, amount_cents):
    token = make_registration_token(email, username, amount_cents)
    location_id = square_location_id()
    return_url = url_for("square_return", _external=True, _scheme="https")
    redirect_url = f"{return_url}?token={urllib.parse.quote(token, safe='')}"

    payload = {
        "idempotency_key": secrets.token_hex(24),
        "description": "Internet Booty access",
        "quick_pay": {
            "name": "Internet Booty — The First Vault Early Crew",
            "price_money": {"amount": int(amount_cents), "currency": "USD"},
            "location_id": location_id,
        },
        "checkout_options": {
            "redirect_url": redirect_url,
            "ask_for_shipping_address": False,
        },
        "pre_populated_data": {
            "buyer_email": email,
        },
        "payment_note": f"IBENTRY:{token}",
    }

    status, data = square_request("POST", "/v2/online-checkout/payment-links", payload)
    if status not in (200, 201):
        raise RuntimeError(square_error_message(data))

    link = data.get("payment_link") or {}
    if not link.get("url") or not link.get("order_id"):
        raise RuntimeError("Square did not return a checkout link.")

    session["pending_entry_token"] = token
    session["pending_square_order_id"] = link["order_id"]
    session["pending_square_link_id"] = link.get("id")
    return link["url"]


def square_order(order_id):
    status, data = square_request("GET", f"/v2/orders/{urllib.parse.quote(order_id, safe='')}")
    if status != 200:
        raise RuntimeError(square_error_message(data))
    return data.get("order") or {}


def completed_order_details(order, expected_amount):
    if order.get("state") != "COMPLETED":
        return None

    total = ((order.get("total_money") or {}).get("amount"))
    if int(total or 0) != int(expected_amount):
        raise RuntimeError("Completed Square order amount did not match the expected entry price.")

    tenders = order.get("tenders") or []
    payment_id = "square-order-" + str(order.get("id", "unknown"))
    if tenders:
        payment_id = tenders[0].get("payment_id") or tenders[0].get("id") or payment_id

    paid_at = order.get("closed_at") or order.get("updated_at") or datetime.now(timezone.utc).isoformat()
    return payment_id, paid_at


def activate_from_registration(registration, payment_id, paid_at):
    email = registration["email"]
    username = registration["username"]
    amount_cents = int(registration["amount"])
    activate_contact(email, username, payment_id, amount_cents, paid_at)
    try:
        send_welcome_email(email, username, amount_cents)
    except Exception:
        app.logger.exception("Paid account activated but welcome email failed")
    return email, username, amount_cents


def square_signature_valid(raw_body):
    if not SQUARE_WEBHOOK_SIGNATURE_KEY:
        return False
    provided = request.headers.get("x-square-hmacsha256-signature", "")
    message = SQUARE_WEBHOOK_NOTIFICATION_URL.encode("utf-8") + raw_body
    digest = hmac.new(
        SQUARE_WEBHOOK_SIGNATURE_KEY.encode("utf-8"),
        message,
        hashlib.sha256,
    ).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return bool(provided and hmac.compare_digest(provided, expected))


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
            flash("You need to opt into Early Crew emails to continue.", "error")
        else:
            try:
                existing = get_contact(email)
                if contact_is_paid(existing):
                    amount = int(float((existing.get("properties") or {}).get("amount_paid", EARLY_PRICE)) * 100)
                    login_crew(email, (existing.get("properties") or {}).get("username") or username, amount)
                    flash("You're already aboard. No second payment was taken.", "success")
                    return redirect(url_for("crew_account"))

                checkout_url = create_square_checkout(email, username, EARLY_PRICE_CENTS)
                return redirect(checkout_url, code=303)
            except Exception:
                app.logger.exception("Square checkout creation failed")
                flash("Secure checkout is temporarily unavailable. Try again shortly.", "error")

    return render_template("early_access.html")


@app.get("/crew/payment-return")
def square_return():
    token = request.args.get("token", "")
    if not token:
        return redirect(url_for("early_access"))

    try:
        registration = read_registration_token(token)
    except (BadSignature, SignatureExpired):
        flash("That checkout session is no longer valid.", "error")
        return redirect(url_for("early_access"))

    try:
        contact = get_contact(registration["email"])
        if contact_is_paid(contact):
            props = contact.get("properties") or {}
            amount = int(float(props.get("amount_paid", registration["amount"] / 100)) * 100)
            login_crew(registration["email"], props.get("username") or registration["username"], amount)
            return redirect(url_for("crew_account"))
    except Exception:
        app.logger.exception("Unable to read paid account after Square redirect")

    order_id = session.get("pending_square_order_id")
    if order_id:
        try:
            order = square_order(order_id)
            details = completed_order_details(order, registration["amount"])
            if details:
                payment_id, paid_at = details
                email, username, amount = activate_from_registration(registration, payment_id, paid_at)
                login_crew(email, username, amount)
                session.pop("pending_square_order_id", None)
                session.pop("pending_square_link_id", None)
                session.pop("pending_entry_token", None)
                flash("Payment confirmed. Welcome aboard.", "success")
                return redirect(url_for("crew_account"))
        except Exception:
            app.logger.exception("Square payment return verification failed")

    return render_template("payment_pending.html", token=token)


@app.get("/crew/payment-status")
def square_payment_status():
    token = request.args.get("token", "")
    if not token:
        return jsonify({"paid": False}), 400

    try:
        registration = read_registration_token(token)
    except (BadSignature, SignatureExpired):
        return jsonify({"paid": False, "expired": True}), 400

    try:
        contact = get_contact(registration["email"])
        if contact_is_paid(contact):
            return jsonify({"paid": True})
    except Exception:
        app.logger.exception("Payment status contact lookup failed")

    order_id = session.get("pending_square_order_id")
    if not order_id:
        return jsonify({"paid": False})

    try:
        order = square_order(order_id)
        details = completed_order_details(order, registration["amount"])
        if details:
            payment_id, paid_at = details
            activate_from_registration(registration, payment_id, paid_at)
            return jsonify({"paid": True})
    except Exception:
        app.logger.exception("Payment status Square verification failed")

    return jsonify({"paid": False})


@app.post("/webhooks/square")
def square_webhook():
    raw_body = request.get_data(cache=True)

    if not square_signature_valid(raw_body):
        return jsonify({"error": "invalid signature"}), 403

    event = request.get_json(silent=True) or {}
    if event.get("type") not in {"payment.created", "payment.updated"}:
        return "", 204

    payment = (((event.get("data") or {}).get("object") or {}).get("payment") or {})
    if payment.get("status") != "COMPLETED":
        return "", 200

    note = payment.get("note") or ""
    if not note.startswith("IBENTRY:"):
        return "", 200

    token = note.split("IBENTRY:", 1)[1].strip()
    try:
        registration = read_registration_token(token)
        expected = int(registration["amount"])
        actual = int(((payment.get("amount_money") or {}).get("amount")) or 0)
        if actual != expected:
            app.logger.warning("Square payment amount mismatch for Internet Booty entry")
            return "", 200

        paid_at = payment.get("updated_at") or payment.get("created_at") or datetime.now(timezone.utc).isoformat()
        activate_from_registration(registration, payment.get("id", "square-payment"), paid_at)
    except (BadSignature, SignatureExpired):
        app.logger.warning("Rejected invalid registration token from Square payment note")
    except Exception:
        app.logger.exception("Square webhook account activation failed")
        return jsonify({"error": "activation failed"}), 500

    return "", 200


@app.get("/crew")
def crew_account():
    email = session.get("crew_email")
    if not email:
        return redirect(url_for("crew_signin"))
    username = session.get("crew_username", "Crewmate")
    amount_paid = session.get("crew_amount_paid", EARLY_PRICE)
    return render_template("crew.html", email=email, username=username, amount_paid=amount_paid)


@app.route("/crew/sign-in", methods=["GET", "POST"])
def crew_signin():
    if request.method == "POST":
        if not valid_csrf():
            abort(400)
        email = request.form.get("email", "").strip().lower()
        if not EMAIL_RE.match(email):
            flash("Enter the email used for your crew account.", "error")
        else:
            try:
                contact = get_contact(email)
                if contact_is_paid(contact):
                    send_login_link(email)
                flash("If that paid account exists, a sign-in link is on its way.", "success")
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

    if not contact or not contact_is_paid(contact):
        flash("We could not find an active paid crew account.", "error")
        return redirect(url_for("crew_signin"))

    props = contact.get("properties") or {}
    amount = int(float(props.get("amount_paid", EARLY_PRICE)) * 100)
    login_crew(email, props.get("username") or "Crewmate", amount)
    return redirect(url_for("crew_account"))


@app.post("/crew/logout")
def crew_logout():
    if not valid_csrf():
        abort(400)
    for key in ("crew_email", "crew_username", "crew_amount_paid", "early_registered"):
        session.pop(key, None)
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
        "payments": "square",
    })


@app.get("/health")
def health():
    return {
        "status": "ok",
        "squareConfigured": bool(SQUARE_ACCESS_TOKEN),
        "squareWebhookConfigured": bool(SQUARE_WEBHOOK_SIGNATURE_KEY),
    }, 200



if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG") == "1")
