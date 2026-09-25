import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, Response, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
import redis

app = Flask(__name__)

SITE_NAME = os.getenv("SITE_NAME", "Internet Booty")
PRIZE_AMOUNT = os.getenv("PRIZE_AMOUNT", "$5,000")
LAUNCH_AT = os.getenv("LAUNCH_AT", "2026-10-14T18:24:00+00:00")
HUNT_ENABLED = os.getenv("HUNT_ENABLED", "0") == "1"

EARLY_PRICE = 10
REGULAR_PRICE = 20
EARLY_PRICE_CENTS = int(os.getenv("EARLY_PRICE_CENTS", "1000"))
REGULAR_PRICE_CENTS = int(os.getenv("REGULAR_PRICE_CENTS", "2000"))

IS_DEBUG = os.getenv("FLASK_DEBUG") == "1"
SECRET_KEY = os.getenv("SECRET_KEY", "")
if not SECRET_KEY and not IS_DEBUG:
    raise RuntimeError("SECRET_KEY must be configured in production.")

app.secret_key = SECRET_KEY or secrets.token_urlsafe(48)
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://internetbooty.onrender.com").rstrip("/")
trusted_hosts = ["internetbooty.com", "www.internetbooty.com", "internetbooty.onrender.com", "internetbooty", "localhost", "127.0.0.1"]

app.config.update(
    SESSION_COOKIE_NAME="__Host-internetbooty",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=not IS_DEBUG,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    SESSION_REFRESH_EACH_REQUEST=False,
    MAX_CONTENT_LENGTH=64 * 1024,
    TRUSTED_HOSTS=trusted_hosts,
)

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_SEGMENT_ID = os.getenv("RESEND_SEGMENT_ID", "")
RESEND_WAITLIST_SEGMENT_ID = os.getenv("RESEND_WAITLIST_SEGMENT_ID", "")
RESEND_TOPIC_ID = os.getenv("RESEND_TOPIC_ID", "")
RESEND_FROM = os.getenv("RESEND_FROM", "Internet Booty <crew@internetbooty.com>")

REDIS_URL = os.getenv("REDIS_URL", "")
CHAT_KEY = "internetbooty:crew:worldchat"
CHAT_LIMIT = 150
CHAT_REDIS = redis.Redis.from_url(
    REDIS_URL,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
    health_check_interval=30,
) if REDIS_URL else None

SQUARE_ACCESS_TOKEN = os.getenv("SQUARE_ACCESS_TOKEN", "")
SQUARE_LOCATION_ID = os.getenv("SQUARE_LOCATION_ID", "")
SQUARE_API_VERSION = os.getenv("SQUARE_API_VERSION", "2026-09-16")
SQUARE_BASE_URL = os.getenv("SQUARE_BASE_URL", "https://connect.squareup.com")
SQUARE_WEBHOOK_SIGNATURE_KEY = os.getenv("SQUARE_WEBHOOK_SIGNATURE_KEY", "")
SQUARE_WEBHOOK_NOTIFICATION_URL = os.getenv(
    "SQUARE_WEBHOOK_NOTIFICATION_URL",
    "https://internetbooty.onrender.com/webhooks/square",
)

GUIDES = {
    "how-online-treasure-hunts-work": {
        "title": "How Online Treasure Hunts Work",
        "description": "A practical guide to online treasure hunts, puzzle paths, ciphers, clue chains, and what makes a fair digital hunt.",
        "eyebrow": "FIELD NOTES 01",
        "heading": "How online treasure hunts actually work.",
        "intro": "Online treasure hunts turn the internet into a puzzle board. Instead of following a physical map, players move through clues, documents, symbols, ciphers, websites, and research until one final answer emerges.",
        "sections": [
            {
                "title": "A trail, not a trivia quiz",
                "body": "The strongest hunts are built as chains. One solved clue reveals the next place to look, the next decoding method, or the next piece of context. That structure matters because it rewards observation and reasoning rather than random guessing. A clue might begin as a line of text, point toward a public source, hide a pattern in a page, and eventually produce a word or phrase that unlocks the next stage. Good hunts also give every player the same underlying path, even when different solvers reach the answer in different ways."
            },
            {
                "title": "Common building blocks",
                "body": "Puzzle hunts often mix several forms of reasoning: substitution ciphers, acrostics, wordplay, coordinates, book ciphers, metadata, visual patterns, number systems, and research clues. The challenge is rarely knowing every technique in advance. It is noticing what kind of problem you are looking at. Repetition may suggest a cipher. Strange capitalization may suggest hidden text. A suspicious quotation may point to a source. The skill is learning to recognize signals without forcing every clue into the same method."
            },
            {
                "title": "Fairness matters",
                "body": "A well-designed online hunt should not require privileged access, secret personal relationships, or information available only to one player. The puzzle itself should contain enough structure to lead a careful solver forward. Timing also matters. If a hunt is competitive, launch times, answer submission, and winner verification need to be handled consistently so the competition is based on solving rather than technical quirks."
            },
            {
                "title": "The First Vault",
                "body": "Internet Booty is built around that style of hunt: layered clues, ciphers, hidden patterns, and a final solution. The First Vault is scheduled to open after the public countdown. Until then, the homepage is intentionally part atmosphere and part puzzle. People who pay close attention may notice that not every path announces itself."
            }
        ]
    },
    "puzzle-hunt-strategy": {
        "title": "Puzzle Hunt Strategy: Solve Layered Riddles Faster",
        "description": "A practical puzzle-hunt strategy guide covering clue triage, pattern recognition, note-taking, dead ends, and verification.",
        "eyebrow": "FIELD NOTES 02",
        "heading": "A better way to attack a puzzle hunt.",
        "intro": "Speed in a puzzle hunt is usually less about knowing obscure facts and more about staying organized, testing ideas cheaply, and abandoning bad assumptions before they consume an hour.",
        "sections": [
            {
                "title": "Start by inventorying the clue",
                "body": "Before decoding anything, list what is unusual. Count lines, words, letters, repeated symbols, capitalization, punctuation, spacing, dates, numbers, and source references. Many puzzles become harder because solvers begin transforming the clue before understanding what makes it distinctive. A thirty-second inventory gives you a baseline and keeps you from overlooking the most deliberate feature."
            },
            {
                "title": "Test cheap hypotheses first",
                "body": "Try methods in increasing order of cost. Acrostics, first letters, last letters, obvious Caesar shifts, simple indexing, and direct references are quick to test. Complex ciphers, broad web research, or brute force should come later unless the clue strongly points there. This approach protects your time and makes false starts easier to discard."
            },
            {
                "title": "Keep a solve log",
                "body": "Write down what you tried, what output you got, and why you rejected it. Puzzle hunts often reuse information from earlier steps, and a failed idea can become useful after a later clue changes the context. A solve log also prevents teams from repeating the same dead end. Screenshots, copied text, timestamps, and short notes are enough."
            },
            {
                "title": "Demand confirmation",
                "body": "A good solve usually confirms itself. The output should look intentional, connect naturally to the next clue, or explain why a strange feature existed. If a method produces gibberish that requires several extra assumptions, treat it as weak evidence. The best competitive habit is not solving fast; it is recognizing when you have actually solved something."
            }
        ]
    },
    "cipher-solving-basics": {
        "title": "Cipher Solving Basics for Online Puzzle Hunts",
        "description": "Learn the most useful beginner cipher techniques for online puzzle hunts, including Caesar shifts, substitution, indexing, and transposition.",
        "eyebrow": "FIELD NOTES 03",
        "heading": "Cipher basics every treasure hunter should know.",
        "intro": "You do not need to memorize hundreds of historical ciphers to be dangerous in a puzzle hunt. A small toolkit covers a surprising amount of territory.",
        "sections": [
            {
                "title": "Caesar and ROT shifts",
                "body": "A Caesar cipher moves every letter by the same number of positions in the alphabet. ROT13 is the familiar version that shifts by thirteen. If text looks almost language-like or a clue contains a strong number, a shift is cheap to test. Watch for wraparound from Z back to A, and remember that the number may come from somewhere else in the puzzle rather than being stated directly."
            },
            {
                "title": "Substitution ciphers",
                "body": "In a simple substitution cipher, each plaintext letter is consistently replaced by another symbol or letter. Frequency helps: E is common in English, one-letter words are often A or I, and repeated letter patterns preserve repeated structures. In puzzle hunts, however, the key is frequently hinted by the clue, so do not assume you must solve every substitution from frequency alone."
            },
            {
                "title": "Indexing",
                "body": "Indexing is one of the most common puzzle-hunt mechanisms. A sequence of numbers may tell you which letter to take from each word or line. For example, a 3 beside a word can mean take its third letter. Always check whether the clue suggests one-based indexing, zero-based indexing, line numbers, word numbers, or letter positions."
            },
            {
                "title": "Transposition and ordering",
                "body": "Sometimes the letters are correct but arranged in the wrong order. Columns may need to be read vertically, rows reversed, chunks reordered, or text placed into a grid. If the clue's character count factors neatly into a rectangle, or the formatting looks unnaturally regular, test a transposition before reaching for a more exotic cipher."
            }
        ]
    },
    "what-makes-a-great-online-treasure-hunt": {
        "title": "What Makes a Great Online Treasure Hunt?",
        "description": "The design principles behind memorable online treasure hunts: discovery, fairness, escalating puzzles, atmosphere, and a satisfying finish.",
        "eyebrow": "FIELD NOTES 04",
        "heading": "What separates a hunt from a pile of riddles?",
        "intro": "A memorable treasure hunt creates the feeling that the player is discovering a hidden system. The puzzles matter, but pacing, atmosphere, and trust are what make people keep searching.",
        "sections": [
            {
                "title": "Discovery before explanation",
                "body": "Treasure hunts are strongest when the player notices something before the site tells them what it means. A strange mark, an odd sentence, a repeated symbol, or a page that feels slightly too deliberate can create the first spark. That moment turns browsing into investigation. The trick is making hidden details discoverable enough that attentive people can find them without making the answer obvious."
            },
            {
                "title": "Escalation",
                "body": "Early puzzles should teach the language of the hunt. Later puzzles can combine techniques, require research, or connect information that originally seemed unrelated. This creates a sense of progression. Difficulty should rise because the player has learned more, not because the clues become arbitrary."
            },
            {
                "title": "Atmosphere with purpose",
                "body": "Visual design, story, sound, and writing can make a puzzle feel larger than the mechanism underneath it. The best atmosphere also carries information. Decorative details may hint at a theme, suggest a method, or reinforce the world of the hunt. When every element feels potentially meaningful, players naturally become more observant."
            },
            {
                "title": "A finish that feels earned",
                "body": "The final solve should connect to what came before. A satisfying ending gives players the sense that the path was visible in retrospect, even if it was difficult in the moment. That is the standard behind The First Vault: a trail built to reward attention, persistence, and reasoning rather than pure guessing."
            }
        ]
    }
}


SENSITIVE_PATH_PREFIXES = (
    "/crew",
    "/captains-mark",
    "/hunt",
    "/map",
    "/puzzles",
    "/archive",
)
RATE_BUCKETS = {}


def client_fingerprint():
    forwarded = request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For", "")
    ip = forwarded.split(",", 1)[0].strip() if forwarded else (request.remote_addr or "unknown")
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()[:24]


def enforce_rate_limit(scope, limit, window_seconds, subject=""):
    now = time.monotonic()
    key = f"{scope}:{client_fingerprint()}:{subject}"
    bucket = RATE_BUCKETS.setdefault(key, [])
    cutoff = now - window_seconds
    bucket[:] = [stamp for stamp in bucket if stamp > cutoff]
    if len(bucket) >= limit:
        abort(429)
    bucket.append(now)

    if len(RATE_BUCKETS) > 5000:
        stale_before = now - 3600
        for old_key in list(RATE_BUCKETS):
            RATE_BUCKETS[old_key] = [stamp for stamp in RATE_BUCKETS[old_key] if stamp > stale_before]
            if not RATE_BUCKETS[old_key]:
                RATE_BUCKETS.pop(old_key, None)


def public_url(path):
    return f"{PUBLIC_BASE_URL}{path}"


@app.before_request
def prepare_security_context():
    g.csp_nonce = secrets.token_urlsafe(18)


@app.after_request
def apply_security_headers(response):
    nonce = getattr(g, "csp_nonce", "")
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}' https://www.googletagmanager.com; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https://images.stockcake.com https://www.google-analytics.com https://www.googletagmanager.com; "
        "connect-src 'self' https://www.google-analytics.com https://region1.google-analytics.com https://www.googletagmanager.com; "
        "font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; "
        "form-action 'self' https://square.link https://checkout.square.site; upgrade-insecure-requests"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"

    host = (request.environ.get("HTTP_HOST") or "").split(":", 1)[0].lower()
    if host in {"internetbooty.com", "www.internetbooty.com"}:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"

    if request.path.startswith(SENSITIVE_PATH_PREFIXES):
        response.headers["Cache-Control"] = "no-store, private, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet"

    return response


EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,24}$")

USERNAME_LEET_MAP = str.maketrans({
    "0": "o",
    "1": "i",
    "3": "e",
    "4": "a",
    "5": "s",
    "7": "t",
    "8": "b",
    "9": "g",
})
USERNAME_BLOCKED_WORDS = {
    "fuck", "fucker", "fucking", "motherfucker",
    "shit", "shithead", "bullshit",
    "bitch", "cunt",
    "asshole", "arsehole",
    "whore", "slut", "pussy",
    "dick", "dickhead", "cock", "cocksucker",
    "nigger", "nigga", "faggot", "fag",
    "retard", "retarded",
    "chink", "spic", "kike", "wetback",
}
USERNAME_BLOCKED_PATTERNS = (
    re.compile(r"fuck"),
    re.compile(r"motherfucker"),
    re.compile(r"cunt"),
    re.compile(r"asshole"),
    re.compile(r"whore"),
    re.compile(r"slut"),
    re.compile(r"pussy"),
    re.compile(r"nigg(?:er|a)"),
    re.compile(r"fagg?ot"),
    re.compile(r"retard"),
    re.compile(r"chink"),
    re.compile(r"spic"),
    re.compile(r"kike"),
    re.compile(r"wetback"),
    re.compile(r"(?:dick|cock)(?:head|face|wad|bag|sucker|hole)"),
)


def username_is_allowed(username):
    lowered = username.lower().translate(USERNAME_LEET_MAP)
    tokens = [token for token in re.split(r"[_-]+", lowered) if token]
    compact = re.sub(r"[_-]+", "", lowered)

    if any(token in USERNAME_BLOCKED_WORDS for token in tokens):
        return False
    if compact in USERNAME_BLOCKED_WORDS:
        return False
    return not any(pattern.search(compact) for pattern in USERNAME_BLOCKED_PATTERNS)



def launch_datetime():
    value = LAUNCH_AT.replace("Z", "+00:00")
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def hunt_is_open():
    return HUNT_ENABLED and datetime.now(timezone.utc) >= launch_datetime()


def clear_auth_session():
    for key in (
        "crew_email",
        "crew_username",
        "crew_amount_paid",
        "early_registered",
        "auth_at",
        "paid_verified_at",
    ):
        session.pop(key, None)


def paid_session_valid(max_verification_age=300):
    email = session.get("crew_email")
    auth_at = session.get("auth_at")
    if not email or not auth_at:
        return False

    now = int(time.time())
    if now - int(auth_at) > 12 * 3600:
        clear_auth_session()
        return False

    last_verified = int(session.get("paid_verified_at") or 0)
    if now - last_verified <= max_verification_age:
        return True

    try:
        contact = get_contact(email)
    except Exception:
        app.logger.exception("Paid entitlement revalidation failed")
        return False

    if not contact_is_paid(contact):
        clear_auth_session()
        return False

    session["crew_username"] = contact_property(contact, "username") or session.get("crew_username") or "Crewmate"
    session["paid_verified_at"] = now
    return True


def hunt_gate(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not hunt_is_open():
            return redirect(url_for("home", locked="1"), code=302)
        if not paid_session_valid():
            flash("Paid crew access is required to enter the hunt.", "error")
            return redirect(url_for("crew_signin"), code=302)
        return view(*args, **kwargs)
    return wrapped


def crew_gate(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not paid_session_valid():
            return redirect(url_for("crew_signin"), code=302)
        return view(*args, **kwargs)
    return wrapped


def csrf_token():
    token = session.get("_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf"] = token
    return token


def valid_csrf():
    sent = request.form.get("_csrf", "") or request.headers.get("X-CSRF-Token", "")
    expected = session.get("_csrf", "")
    return bool(sent and expected and secrets.compare_digest(sent, expected))


def get_chat_messages():
    if not CHAT_REDIS:
        return []
    raw = CHAT_REDIS.lrange(CHAT_KEY, -CHAT_LIMIT, -1)
    messages = []
    for item in raw:
        try:
            messages.append(json.loads(item))
        except (TypeError, json.JSONDecodeError):
            continue
    return messages


def append_chat_message(username, text):
    if not CHAT_REDIS:
        raise RuntimeError("World chat is not configured.")

    message = {
        "id": secrets.token_hex(8),
        "username": username,
        "text": text,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    pipe = CHAT_REDIS.pipeline()
    pipe.rpush(CHAT_KEY, json.dumps(message, separators=(",", ":")))
    pipe.ltrim(CHAT_KEY, -CHAT_LIMIT, -1)
    pipe.expire(CHAT_KEY, 60 * 60 * 24 * 7)
    pipe.execute()
    return message


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


def add_launch_watch_contact(email):
    payload = {
        "email": email,
        "unsubscribed": False,
        "properties": {"registration_source": "launch_watch"},
    }
    if RESEND_WAITLIST_SEGMENT_ID:
        payload["segments"] = [{"id": RESEND_WAITLIST_SEGMENT_ID}]
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
            {"unsubscribed": False, "properties": {"registration_source": "launch_watch"}},
        )
        if status not in (200, 201):
            raise RuntimeError(data.get("message", "Unable to join launch watch."))
        if RESEND_WAITLIST_SEGMENT_ID:
            resend_request("POST", f"/contacts/{encoded}/segments/{RESEND_WAITLIST_SEGMENT_ID}")
        if RESEND_TOPIC_ID:
            resend_request(
                "PATCH",
                f"/contacts/{encoded}/topics",
                {"topics": [{"id": RESEND_TOPIC_ID, "subscription": "opt_in"}]},
            )
        return data

    raise RuntimeError(data.get("message", "Unable to join launch watch."))


def contact_property(contact, key, default=None):
    value = ((contact or {}).get("properties") or {}).get(key, default)
    if isinstance(value, dict) and "value" in value:
        return value.get("value", default)
    return value


def contact_is_paid(contact):
    return (
        contact_property(contact, "entry_status") == "paid"
        and bool(contact_property(contact, "square_payment_id"))
    )


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
    session.clear()
    session.permanent = True
    session["crew_email"] = email
    session["crew_username"] = username
    session["crew_amount_paid"] = int(amount_cents) / 100
    session["early_registered"] = int(amount_cents) == EARLY_PRICE_CENTS
    session["auth_at"] = int(time.time())
    session["paid_verified_at"] = int(time.time())
    csrf_token()


def send_welcome_email(email, username, amount_cents):
    amount = int(amount_cents) / 100
    link = public_url(url_for("crew_signin"))
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
    nonce = secrets.token_urlsafe(24)
    encoded = urllib.parse.quote(email, safe="")
    status, data = resend_request(
        "PATCH",
        f"/contacts/{encoded}",
        {"properties": {"login_nonce": nonce}},
    )
    if status not in (200, 201):
        raise RuntimeError(data.get("message", "Unable to prepare sign-in link."))

    serializer = URLSafeTimedSerializer(app.secret_key)
    token = serializer.dumps({"email": email, "nonce": nonce}, salt="crew-login")
    link = public_url(url_for("crew_magic", token=token))
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
    return_url = public_url(url_for("square_return"))
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

    total_money = order.get("total_money") or {}
    total = total_money.get("amount")
    if total_money.get("currency") != "USD":
        raise RuntimeError("Completed Square order currency was not USD.")
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

    existing = get_contact(email)
    if contact_is_paid(existing) and contact_property(existing, "square_payment_id") == payment_id:
        return email, contact_property(existing, "username") or username, amount_cents

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
        "csp_nonce": getattr(g, "csp_nonce", ""),
    }


@app.get("/guides/<slug>")
def guide(slug):
    item = GUIDES.get(slug)
    if not item:
        abort(404)
    return render_template("guide.html", guide=item, slug=slug)


@app.get("/")
def home():
    return render_template("index.html", locked=request.args.get("locked") == "1")


@app.post("/launch-watch")
def launch_watch():
    enforce_rate_limit("launch-watch", 6, 3600)
    if not valid_csrf():
        abort(400)
    if request.form.get("website"):
        return redirect(url_for("home"))

    email = request.form.get("email", "").strip().lower()
    if not EMAIL_RE.match(email):
        flash("Enter a valid email to get the launch signal.", "error")
        return redirect(url_for("home", watch="1") + "#launch-watch")

    try:
        add_launch_watch_contact(email)
        flash("Signal locked in. We'll email you when the vault moves.", "success")
    except Exception:
        app.logger.exception("Launch watch signup failed")
        flash("The signal was lost. Try again in a moment.", "error")
    return redirect(url_for("home", watch="1") + "#launch-watch")


@app.get("/robots.txt")
def robots():
    return Response(
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /crew/\n"
        "Disallow: /captains-mark\n"
        "Disallow: /api/\n"
        "Disallow: /hunt\n"
        "Disallow: /map\n"
        "Disallow: /puzzles\n"
        "Disallow: /archive\n"
        "Disallow: /webhooks/\n"
        "Sitemap: https://internetbooty.com/sitemap.xml\n",
        mimetype="text/plain",
    )


@app.get("/sitemap.xml")
def sitemap():
    lastmod = datetime.now(timezone.utc).date().isoformat()
    urls = [
        ("https://internetbooty.com/", "daily", "1.0"),
        *[(f"https://internetbooty.com/guides/{slug}", "monthly", "0.7") for slug in GUIDES],
    ]
    entries = "".join(
        f"""  <url>
    <loc>{url}</loc>
    <lastmod>{lastmod}</lastmod>
    <changefreq>{changefreq}</changefreq>
    <priority>{priority}</priority>
  </url>
"""
        for url, changefreq, priority in urls
    )
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{entries}</urlset>
"""
    return Response(body, mimetype="application/xml")


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
        elif not username_is_allowed(username):
            flash("That pirate name isn't allowed. Pick a cleaner one and try again.", "error")
        elif not consent:
            flash("You need to opt into Early Crew emails to continue.", "error")
        else:
            try:
                enforce_rate_limit("early-checkout-ip", 12, 600)
                enforce_rate_limit("early-checkout-email", 6, 600, hashlib.sha256(email.encode("utf-8")).hexdigest()[:16])
                existing = get_contact(email)
                if contact_is_paid(existing):
                    amount = int(float(contact_property(existing, "amount_paid", EARLY_PRICE)) * 100)
                    login_crew(email, contact_property(existing, "username") or username, amount)
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

    order_id = (session.get("pending_square_order_id") or request.args.get("orderId", "")).strip()
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

    return render_template("payment_pending.html", token=token, order_id=order_id)


@app.get("/crew/payment-status")
def square_payment_status():
    enforce_rate_limit("payment-status", 45, 60)
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

    order_id = (session.get("pending_square_order_id") or request.args.get("orderId", "")).strip()
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
        amount_money = payment.get("amount_money") or {}
        actual = int(amount_money.get("amount") or 0)
        if amount_money.get("currency") != "USD":
            app.logger.warning("Rejected non-USD Square payment for Internet Booty entry")
            return "", 200
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


@app.get("/crew/chat")
@crew_gate
def crew_chat():
    return render_template(
        "chat.html",
        username=session.get("crew_username", "Crewmate"),
    )


@app.get("/api/crew/chat")
@crew_gate
def crew_chat_messages():
    try:
        messages = get_chat_messages()
    except redis.RedisError:
        app.logger.exception("Crew world chat read failed")
        return jsonify({"messages": [], "offline": True}), 503
    return jsonify({"messages": messages, "offline": False})


@app.post("/api/crew/chat")
@crew_gate
def crew_chat_post():
    enforce_rate_limit("crew-world-chat", 8, 30, session.get("crew_email", ""))
    if not valid_csrf():
        abort(400)

    data = request.get_json(silent=True) or {}
    text_value = str(data.get("text", "")).strip()
    text_value = re.sub(r"\s+", " ", text_value)

    if not text_value:
        return jsonify({"error": "Say something first."}), 400
    if len(text_value) > 220:
        return jsonify({"error": "Keep messages under 220 characters."}), 400

    username = session.get("crew_username", "Crewmate")
    try:
        message = append_chat_message(username, text_value)
    except redis.RedisError:
        app.logger.exception("Crew world chat write failed")
        return jsonify({"error": "World chat is temporarily offline."}), 503

    return jsonify({"message": message}), 201


@app.get("/crew")
@crew_gate
def crew_account():
    email = session.get("crew_email")
    username = session.get("crew_username", "Crewmate")
    amount_paid = session.get("crew_amount_paid", EARLY_PRICE)
    return render_template("crew.html", email=email, username=username, amount_paid=amount_paid)


@app.route("/crew/sign-in", methods=["GET", "POST"])
def crew_signin():
    if request.method == "POST":
        if not valid_csrf():
            abort(400)
        email = request.form.get("email", "").strip().lower()
        enforce_rate_limit("crew-signin-ip", 12, 600)
        enforce_rate_limit("crew-signin-email", 6, 600, hashlib.sha256(email.encode("utf-8")).hexdigest()[:16])
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
    enforce_rate_limit("crew-magic", 20, 600)
    serializer = URLSafeTimedSerializer(app.secret_key)
    try:
        payload = serializer.loads(token, salt="crew-login", max_age=1800)
        email = payload.get("email", "")
        nonce = payload.get("nonce", "")
        if not email or not nonce:
            raise BadSignature("Missing token fields")
    except (BadSignature, SignatureExpired, AttributeError):
        flash("That sign-in link has expired or was already replaced.", "error")
        return redirect(url_for("crew_signin"))

    try:
        contact = get_contact(email)
    except Exception:
        contact = None

    stored_nonce = contact_property(contact, "login_nonce") or ""
    if (
        not contact
        or not contact_is_paid(contact)
        or not stored_nonce
        or not secrets.compare_digest(str(stored_nonce), str(nonce))
    ):
        flash("That sign-in link is invalid or has already been used.", "error")
        return redirect(url_for("crew_signin"))

    encoded = urllib.parse.quote(email, safe="")
    status, _ = resend_request(
        "PATCH",
        f"/contacts/{encoded}",
        {"properties": {"login_nonce": ""}},
    )
    if status not in (200, 201):
        flash("Sign-in could not be completed securely. Request a new link.", "error")
        return redirect(url_for("crew_signin"))

    amount = int(float(contact_property(contact, "amount_paid", EARLY_PRICE)) * 100)
    login_crew(email, contact_property(contact, "username") or "Crewmate", amount)
    return redirect(url_for("crew_account"))


@app.post("/crew/logout")
def crew_logout():
    if not valid_csrf():
        abort(400)
    session.clear()
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
        "open": hunt_is_open(),
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
