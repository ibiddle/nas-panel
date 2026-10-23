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
TOOL_MODULES = ["tools.shares", "tools.health", "tools.notify", "tools.mounts", "tools.apps"]

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
    print("=" * 50, flush=True)
    print(f"  NAS PANEL SETUP CODE:  {setup_code()}", flush=True)
    print("  Open http://<your-NAS-IP>:8090 and enter this code", flush=True)
    print("=" * 50, flush=True)


def _announce_version():
    """Log (and alert) once when the panel starts on a new version."""
    v = core.version_info()["version"]
    last = core.load("version.json", {}).get("version")
    if last != v:
        core.save("version.json", {"version": v, "since": time.strftime("%Y-%m-%d %H:%M")})
        if last:
            core.log(f"Panel updated: v{last} -> v{v}")
            from tools import notify
            notify.send_async(f"Updated to v{v}", f"NAS Control Panel was updated from v{last} to v{v}.")
        else:
            core.log(f"Panel started (v{v})")


_announce_version()
from tools import health as _health   # noqa: E402
_health.start()


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
PUBLIC = {"login", "setup", "reset", "static"}


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
    from tools import health
    return {"TOOLS": TOOLS, "csrf": session.get("csrf", ""), "now": datetime.datetime.now(),
            "VER": core.version_info(), "PROBLEMS": health.problems() if session.get("ok") else []}


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


RESET_LIFE = 30 * 60     # reset code works for 30 minutes
RESET_GAP = 2 * 60       # a new code at most every 2 minutes


@app.route("/reset", methods=["GET", "POST"])
def reset():
    """Forgotten password: a one-time code shown in the container log (and sent to Alerts, if set up).

    Only someone who can open the NAS's Docker app (or receives the alerts) can see the code.
    """
    if not auth().get("hash"):
        return redirect(url_for("setup"))
    st = core.load("reset.json", {})
    now = time.time()
    if request.method == "POST":
        if not hmac.compare_digest(request.form.get("csrf", ""), session.get("rcsrf", "-")):
            abort(400)
        if locked_for():
            flash(lock_message(), "err")
        elif request.form.get("action") == "send":
            if now - st.get("made", 0) < RESET_GAP:
                flash("A code was made less than 2 minutes ago - use that one (check the log again).", "err")
            else:
                code = secrets.token_hex(4).upper()
                core.save("reset.json", {"hash": generate_password_hash(code), "made": now})
                print("=" * 50, flush=True)
                print(f"  NAS PANEL PASSWORD RESET CODE:  {code}", flush=True)
                print("  Works for 30 minutes. Ignore if you didn't ask for it.", flush=True)
                print("=" * 50, flush=True)
                core.log("Password reset code requested", client_ip())
                from tools import notify
                if notify.configured():
                    notify.send_async("Password reset code",
                                      f"Your NAS Control Panel reset code is {code} (works for 30 minutes). "
                                      "If you didn't ask for this, ignore it - your password hasn't changed.",
                                      urgent=True)
                flash("Reset code made. Find it in the nas-panel log (steps below)"
                      + (", or on your phone/email." if notify.configured() else "."), "ok")
            return redirect(url_for("reset", step="code"))
        else:
            code = request.form.get("code", "").strip().upper()
            pw, pw2 = request.form.get("pw", ""), request.form.get("pw2", "")
            valid = bool(st.get("hash")) and now - st.get("made", 0) < RESET_LIFE
            if not valid:
                flash("No valid reset code - get a new one.", "err")
            elif not check_secret(check_password_hash(st["hash"], code), "reset code"):
                flash(lock_message() if locked_for() else "Wrong reset code.", "err")
            elif len(pw) < MIN_PW:
                flash(f"Password must be at least {MIN_PW} characters.", "err")
            elif pw != pw2:
                flash("Passwords don't match.", "err")
            else:
                set_password(pw)
                os.remove(os.path.join(core.DATA, "reset.json"))
                core.log("Password RESET with a reset code", client_ip())
                from tools import notify
                notify.send_async("Password was reset", "The NAS Control Panel password was just reset. "
                                  "If this wasn't you, reset it again and check who can use your NAS's Docker app.",
                                  urgent=True)
                start_session()
                flash("Password changed - you're logged in.", "ok")
                return redirect(url_for("home"))
            return redirect(url_for("reset", step="code"))
    session["rcsrf"] = session.get("rcsrf") or secrets.token_hex(16)
    from tools import notify
    return render_template("reset.html", rcsrf=session["rcsrf"], locked=locked_for(),
                           step=request.args.get("step", ""), alerts=notify.configured())


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
