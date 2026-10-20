"""Notifications: phone (ntfy), email and webhook. Used by the health check and updates."""
import json
import re
import smtplib
import ssl
import threading
import time
import urllib.request
from email.message import EmailMessage

from flask import Blueprint, flash, redirect, render_template, request, url_for

import core

TOOL = {"id": "notify", "name": "Alerts", "icon": "🔔", "endpoint": "notify.index",
        "desc": "Get a message on your phone or by email if a share stops working"}
bp = Blueprint("notify", __name__, url_prefix="/alerts")

RE_TOPIC = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
RE_URL = re.compile(r"^https?://[^\s'\"<>]{3,300}$")
RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RE_HOST = re.compile(r"^[A-Za-z0-9.-]{3,100}$")


def settings():
    return core.load("notify.json", {"ntfy_server": "https://ntfy.sh"})


def configured(s=None):
    s = s or settings()
    return bool(s.get("ntfy_topic") or s.get("email_to") or s.get("webhook"))


def _ntfy(s, title, msg, urgent):
    url = s.get("ntfy_server", "https://ntfy.sh").rstrip("/") + "/" + s["ntfy_topic"]
    req = urllib.request.Request(url, data=msg.encode(), method="POST", headers={
        "Title": title, "Priority": "high" if urgent else "default",
        "Tags": "warning" if urgent else "white_check_mark"})
    urllib.request.urlopen(req, timeout=15).read()


def _webhook(s, title, msg):
    text = f"{title}\n{msg}"
    data = json.dumps({"text": text, "content": text, "title": title, "message": msg}).encode()
    req = urllib.request.Request(s["webhook"], data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=15).read()


def _email(s, title, msg):
    m = EmailMessage()
    m["Subject"] = title
    m["From"] = s.get("email_from") or s.get("email_user") or s["email_to"]
    m["To"] = s["email_to"]
    m.set_content(msg + "\n\n-- NAS Control Panel")
    port = int(s.get("email_port") or 587)
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(s["email_host"], port, context=ctx, timeout=20) as srv:
            if s.get("email_user"):
                srv.login(s["email_user"], s.get("email_pass", ""))
            srv.send_message(m)
    else:
        with smtplib.SMTP(s["email_host"], port, timeout=20) as srv:
            srv.starttls(context=ctx)
            if s.get("email_user"):
                srv.login(s["email_user"], s.get("email_pass", ""))
            srv.send_message(m)


def send(title, msg, urgent=False, s=None):
    """Send to every configured channel. Returns list of (channel, ok, error)."""
    s = s or settings()
    title = "NAS Panel: " + title
    out = []
    for ch, fn, ok_ in (("phone", lambda: _ntfy(s, title, msg, urgent), s.get("ntfy_topic")),
                        ("email", lambda: _email(s, title, msg), s.get("email_to") and s.get("email_host")),
                        ("webhook", lambda: _webhook(s, title, msg), s.get("webhook"))):
        if not ok_:
            continue
        try:
            fn()
            out.append((ch, True, ""))
        except Exception as e:                       # noqa: BLE001 - report any failure to the user
            out.append((ch, False, str(e)[:200]))
    core.log(f"Alert sent: {title} ({', '.join(c + (' ok' if o else ' FAILED') for c, o, _ in out) or 'no channels set'})")
    return out


def send_async(title, msg, urgent=False):
    threading.Thread(target=send, args=(title, msg, urgent), daemon=True).start()


@bp.route("/", methods=["GET", "POST"])
def index():
    s = settings()
    if request.method == "POST":
        f = request.form
        new = dict(s)
        topic = f.get("ntfy_topic", "").strip()
        server = f.get("ntfy_server", "").strip() or "https://ntfy.sh"
        hook = f.get("webhook", "").strip()
        to = f.get("email_to", "").strip()
        host = f.get("email_host", "").strip()
        errs = []
        if topic and not RE_TOPIC.match(topic):
            errs.append("Phone topic: 6-64 letters, numbers, - or _.")
        if not RE_URL.match(server):
            errs.append("ntfy server must start with https://")
        if hook and not RE_URL.match(hook):
            errs.append("Webhook must be a web address starting with https://")
        if to and not RE_EMAIL.match(to):
            errs.append("Email address looks wrong.")
        if to and not RE_HOST.match(host):
            errs.append("Email server (SMTP) is needed for email, e.g. smtp.gmail.com")
        if errs:
            for e in errs:
                flash(e, "err")
            return redirect(url_for(".index"))
        new.update(ntfy_topic=topic, ntfy_server=server, webhook=hook, email_to=to, email_host=host,
                   email_port=f.get("email_port", "587").strip() or "587",
                   email_user=f.get("email_user", "").strip(), email_from=f.get("email_from", "").strip())
        if f.get("email_pass"):
            new["email_pass"] = f["email_pass"]
        if f.get("clear_email_pass"):
            new["email_pass"] = ""
        core.save("notify.json", new)
        core.log("Alert settings changed")
        if f.get("action") == "test":
            res = send("Test message", "If you can read this, alerts from your NAS Control Panel are working.", s=new)
            if not res:
                flash("Saved. Nothing to test - fill in at least one way to be told.", "err")
            for ch, ok, err in res:
                flash(f"Test {ch}: {'sent - check it arrived' if ok else 'FAILED - ' + err}", "ok" if ok else "err")
        else:
            flash("Alert settings saved.", "ok")
        return redirect(url_for(".index"))
    return render_template("notify.html", s=s, has_pass=bool(s.get("email_pass")))
