"""NAS Control Panel - core app: login, lockout, layout and tool registry."""
import datetime
import hmac
import importlib
import os
import secrets
import time

from flask import (Flask, abort, flash, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import core

# ---- Tools (pages). Add new modules here. ----------------------------------
TOOL_MODULES = ["tools.shares", "tools.mounts", "tools.apps"]

MAX_FAILS = 5          # wrong passwords...
WINDOW = 10 * 60       # ...within 10 minutes...
LOCKOUT = 10 * 60      # ...locks the panel for 10 minutes
MIN_PW = 8

app = Flask(__name__)


def _secret_key():
    p = os.path.join(core.DATA, "secret.key")
    if not os.path.exists(p):
        core.write_private(p, secrets.token_hex(32))
    return open(p).read().strip()


app.config.update(
    SECRET_KEY=_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_NAME="naspanel",
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(hours=12),
)

TOOLS = []
for m in TOOL_MODULES:
    mod = importlib.import_module(m)
    app.register_blueprint(mod.bp)
    TOOLS.append(mod.TOOL)


# ---- Auth state --------------------------------------------------------------
def auth():
    return core.load("auth.json", {})


def setup_code():
    """One-time code needed to set the first password (shown in the container log)."""
    p = os.path.join(core.DATA, "setup-code.txt")
    if not os.path.exists(p):
        core.write_private(p, secrets.token_hex(4).upper())
    return open(p).read().strip()


if not auth().get("hash"):
    print(f"*** NAS PANEL SETUP CODE: {setup_code()} ***", flush=True)


def client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()


def locked_for():
    """Seconds left on a lockout (0 if not locked)."""
    return max(0, int(auth().get("locked_until", 0) - time.time()))


def check_secret(ok, what):
    """Record a password/code attempt; lock after MAX_FAILS within WINDOW. Returns ok."""
    with core.LOCK:
        st = auth()
        now = time.time()
        if ok:
            st["fails"] = []
        else:
            st["fails"] = [t for t in st.get("fails", []) if now - t < WINDOW] + [now]
            core.log(f"Wrong {what} ({len(st['fails'])}/{MAX_FAILS})", client_ip())
            if len(st["fails"]) >= MAX_FAILS:
                st["locked_until"] = now + LOCKOUT
                st["fails"] = []
                core.log(f"LOCKED for {LOCKOUT // 60} minutes after {MAX_FAILS} wrong attempts", client_ip())
        core.save("auth.json", st)
    return ok


def lock_message():
    s = locked_for()
    return f"Too many wrong attempts. Locked - try again in {s // 60 + 1} minute(s)."


# ---- Request guard -------------------------------------------------------------
PUBLIC = {"login", "setup", "static"}


@app.before_request
def guard():
    if request.endpoint in PUBLIC:
        return None
    if not auth().get("hash"):
        return redirect(url_for("setup"))
    if not session.get("ok"):
        return redirect(url_for("login", next=request.path))
    if session.get("pwv") != auth().get("pwv"):      # password changed elsewhere
        session.clear()
        return redirect(url_for("login"))
    if request.method == "POST" and not hmac.compare_digest(
            request.form.get("csrf", ""), session.get("csrf", "")):
        abort(400)
    return None


@app.after_request
def headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.context_processor
def inject():
    return {"TOOLS": TOOLS, "csrf": session.get("csrf", ""), "now": datetime.datetime.now()}


def start_session():
    session.clear()
    session.permanent = True
    session["ok"] = True
    session["csrf"] = secrets.token_hex(16)
    session["pwv"] = auth().get("pwv")


def set_password(pw):
    with core.LOCK:
        st = auth()
        st["hash"] = generate_password_hash(pw)
        st["pwv"] = secrets.token_hex(4)
        st["fails"] = []
        core.save("auth.json", st)


# ---- Routes --------------------------------------------------------------------
@app.route("/setup", methods=["GET", "POST"])
def setup():
    if auth().get("hash"):
        return redirect(url_for("login"))
    if request.method == "POST":
        if locked_for():
            flash(lock_message(), "err")
        elif not check_secret(hmac.compare_digest(
                request.form.get("code", "").strip().upper(), setup_code()), "setup code"):
            flash("Wrong setup code.", "err")
        else:
            pw, pw2 = request.form.get("pw", ""), request.form.get("pw2", "")
            if len(pw) < MIN_PW:
                flash(f"Password must be at least {MIN_PW} characters.", "err")
            elif pw != pw2:
                flash("Passwords don't match.", "err")
            else:
                set_password(pw)
                os.remove(os.path.join(core.DATA, "setup-code.txt"))
                core.log("Password created", client_ip())
                start_session()
                return redirect(url_for("home"))
    return render_template("setup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if not auth().get("hash"):
        return redirect(url_for("setup"))
    if request.method == "POST":
        if locked_for():
            flash(lock_message(), "err")
        elif check_secret(check_password_hash(auth()["hash"], request.form.get("pw", "")), "password"):
            start_session()
            core.log("Logged in", client_ip())
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("home"))
        else:
            flash(lock_message() if locked_for() else "Wrong password.", "err")
    return render_template("login.html", locked=locked_for())


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def home():
    return render_template("home.html", log=core.read_log(15))


@app.route("/activity")
def activity():
    return render_template("activity.html", log=core.read_log(500))


@app.route("/password", methods=["GET", "POST"])
def password():
    if request.method == "POST":
        if locked_for():
            flash(lock_message(), "err")
        elif not check_secret(check_password_hash(auth()["hash"], request.form.get("old", "")), "current password"):
            flash("Current password is wrong.", "err")
        elif len(request.form.get("pw", "")) < MIN_PW:
            flash(f"New password must be at least {MIN_PW} characters.", "err")
        elif request.form.get("pw") != request.form.get("pw2"):
            flash("New passwords don't match.", "err")
        else:
            set_password(request.form["pw"])
            core.log("Password changed", client_ip())
            start_session()
            flash("Password changed.", "ok")
            return redirect(url_for("home"))
    return render_template("password.html")
