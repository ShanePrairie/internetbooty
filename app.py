import os
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, redirect, render_template, request, url_for, jsonify

app = Flask(__name__)

SITE_NAME = os.getenv("SITE_NAME", "Internet Booty")
PRIZE_AMOUNT = os.getenv("PRIZE_AMOUNT", "$5,000")
# Default: exactly 20 days from the date this build was created.
LAUNCH_AT = os.getenv("LAUNCH_AT", "2026-10-14T18:24:00+00:00")


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


@app.context_processor
def inject_globals():
    return {
        "site_name": SITE_NAME,
        "prize_amount": PRIZE_AMOUNT,
        "launch_at": launch_datetime().isoformat(),
        "hunt_open": hunt_is_open(),
    }


@app.get("/")
def home():
    return render_template("index.html", locked=request.args.get("locked") == "1")


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
