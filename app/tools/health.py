"""Share health check: every CHECK_MINUTES make sure each connected share still works.

If one has stopped working it tries, in order: put it back if the app lost it, restart the app,
then reconnect it from scratch. Changes of state (broken / fixed / other device offline) are
sent as alerts.
"""
import os
import threading
import time

from flask import Blueprint, flash, redirect, render_template, url_for

import core
import docker_api
from tools import notify, shares

TOOL = {"id": "health", "name": "Share Health", "icon": "🩺", "endpoint": "health.index",
        "desc": "Checks your connected shares every 10 minutes and reconnects them if they drop"}
bp = Blueprint("health", __name__, url_prefix="/health")

try:
    MINUTES = max(2, int(os.environ.get("CHECK_MINUTES", "10")))
except ValueError:
    MINUTES = 10
FIX_GAP = 60 * 60          # don't rebuild the same app more than once an hour
LABEL = {"ok": "Working", "fixed": "Fixed", "offline": "Other device offline", "failed": "Not working",
         "stopped": "App stopped", "gone": "App not found", "empty": "Empty", "nolink": "Share deleted"}
BAD = {"offline", "failed", "gone", "nolink"}
_running = threading.Lock()


def state():
    return core.load("health.json", {"checks": {}, "last": ""})


def problems():
    try:
        return [c for c in state()["checks"].values() if c.get("status") in BAD]
    except (KeyError, AttributeError):
        return []


def _ls(container, target):
    rc, out = core.run(["docker", "exec", container, "ls", target], timeout=25)
    return rc == 0, len([x for x in out.splitlines() if x.strip()]), out


def _container(a, service):
    if a["kind"] == "container":
        return a["container"]
    return (a["services"].get(service) or {}).get("container")


def _check_one(key, w, reg, all_apps, prev):
    """Returns (status, message)."""
    proj, name = w["project"], w["share"]
    sh = reg.get(name)
    if not sh:
        return "nolink", f"Share '{name}' was deleted from the panel - connect another one or disconnect it."
    a = all_apps.get(proj)
    if not a:
        return "gone", f"App '{proj}' isn't on the NAS any more (removed or renamed)."
    service = w.get("service") or next(iter(a["services"]))
    cname = _container(a, service)
    if not cname or a["services"].get(service, {}).get("state") != "running":
        return "stopped", "App is stopped - not checked."
    if name in a["attached"]:
        ok, n, out = _ls(cname, a["attached"][name]["target"])
        if ok and n:
            return "ok", f"{n} items visible"

    # ---- not right: is the other device reachable? ----
    ok, msg = shares.test_share(sh)
    if not ok:
        if "empty" in msg:
            return "empty", "Connected, but the shared folder is empty (or this user can't see its files)."
        return "offline", f"Can't reach {shares.device(sh)}: {msg} Will reconnect when it's back."
    # other device is fine, so the connection in the app is broken - repair (at most once an hour)
    if time.time() - prev.get("last_fix", 0) < FIX_GAP:
        return "failed", "Still not working. Will try to repair again within the hour."
    prev["last_fix"] = time.time()
    target = w.get("target") or f"/media/network/{name}"
    if name in a["attached"]:
        core.log(f"Health: '{name}' on {proj} not readable - restarting {cname}")
        core.run(["docker", "restart", "-t", "30", cname], timeout=120)
        if docker_api.wait_running(cname, 60):
            a = shares.apps().get(proj) or a
            ok2, n, _ = _ls(_container(a, service) or cname, target)
            if ok2 and n:
                return "fixed", f"Restarted the app - working again ({n} items)."
        core.log(f"Health: restart didn't help, reconnecting '{name}' to {proj}")
        shares.detach_raw(a, name)
        a = shares.apps().get(proj) or a
    else:
        core.log(f"Health: '{name}' missing from {proj} (app updated/reinstalled?) - connecting it again")
    ok3, m3 = shares.attach_raw(a, name, sh, service, target)
    if ok3:
        return "fixed", f"Reconnected - {m3}."
    return "failed", f"Couldn't repair automatically: {m3}"


def run_checks(why="timer"):
    """One pass over every wanted connection. Safe to call from any thread."""
    if not _running.acquire(blocking=False):
        return False
    try:
        if not core.LOCK.acquire(timeout=5):          # someone is changing things right now
            return False
        try:
            st = state()
            checks = st.get("checks", {})
            w_all = shares.wanted()
            all_apps = shares.apps()
            # adopt connections made before this feature existed (or by shares.sh)
            for proj, a in all_apps.items():
                for name, at in a["attached"].items():
                    k = f"{proj}|{name}"
                    if k not in w_all:
                        w_all[k] = {"project": proj, "share": name, "service": at["service"], "target": at["target"]}
            core.save("attachments.json", w_all)
            reg = shares.registry()
            now = time.strftime("%Y-%m-%d %H:%M")
            for k in list(checks):
                if k not in w_all:
                    del checks[k]
            for k, w in w_all.items():
                prev = checks.get(k, {})
                try:
                    status, msg = _check_one(k, w, reg, all_apps, prev)
                except Exception as e:                       # noqa: BLE001 - never kill the checker
                    status, msg = "failed", f"Check error: {e}"
                old = prev.get("status", "ok")
                if status == "fixed":
                    all_apps = shares.apps()
                c = dict(prev, project=w["project"], share=w["share"], target=w.get("target", ""),
                         status=status, msg=msg, checked=now)
                if status != old:
                    c["since"] = now
                checks[k] = c
                _alert(w, old, status, msg)
            st.update(checks=checks, last=now, why=why)
            core.save("health.json", st)
        finally:
            core.LOCK.release()
    finally:
        _running.release()
    return True


def _alert(w, old, new, msg):
    where = f"'{w['share']}' on {w['project']}"
    if new == "fixed":
        core.log(f"Health: {where} repaired - {msg}")
        notify.send_async(f"Share repaired: {w['share']}", f"{where} had stopped working and was fixed automatically.\n{msg}")
    elif new in BAD and old not in BAD:
        core.log(f"Health: {where} PROBLEM - {msg}")
        notify.send_async(f"Share not working: {w['share']}", f"{where}: {msg}", urgent=True)
    elif new in ("ok", "empty") and old in BAD:
        core.log(f"Health: {where} working again")
        notify.send_async(f"Share working again: {w['share']}", f"{where} is working again.")


def _loop():
    time.sleep(90)                 # let the NAS and apps finish starting
    while True:
        try:
            run_checks()
        except Exception as e:     # noqa: BLE001
            core.log(f"Health check crashed: {e}")
        time.sleep(MINUTES * 60)


def start():
    if os.environ.get("PANEL_NO_BG"):
        return
    threading.Thread(target=_loop, name="health", daemon=True).start()


@bp.route("/")
def index():
    st = state()
    rows = sorted(st.get("checks", {}).values(), key=lambda c: (c.get("status") not in BAD, c["project"], c["share"]))
    return render_template("health.html", rows=rows, last=st.get("last", ""), minutes=MINUTES, label=LABEL,
                           bad=BAD, alerts=notify.configured(), busy=_running.locked())


@bp.route("/check", methods=["POST"])
def check():
    threading.Thread(target=run_checks, args=("manual",), daemon=True).start()
    flash("Checking now - refresh this page in a minute to see the results.", "ok")
    return redirect(url_for(".index"))


@bp.route("/forget", methods=["POST"])
def forget():
    from flask import request
    key = request.form.get("key", "")
    with core.LOCK:
        w = shares.wanted()
        st = state()
        if key in w:
            del w[key]
            core.save("attachments.json", w)
        st.get("checks", {}).pop(key, None)
        core.save("health.json", st)
    core.log(f"Health: stopped watching {key.replace('|', ' / ')}")
    flash("Stopped watching that connection.", "ok")
    return redirect(url_for(".index"))
