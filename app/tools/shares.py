"""Network Shares: save SMB share logins and connect them (read-only) to Docker apps.

Layout used inside each app folder (same as the old shares.sh, so both work together):
  <app>/shares/<name>.yaml   compose override adding the share as a CIFS volume
  <app>/.env                 SHARE_<NAME>_USER / SHARE_<NAME>_PASS and COMPOSE_FILE (mode 600)
"""
import glob
import os
import re
import secrets
import time

from flask import Blueprint, flash, redirect, render_template, request, url_for

import core

TOOL = {"id": "shares", "name": "Network Shares", "icon": "🔗", "endpoint": "shares.index",
        "desc": "Connect folders from other NASes / PCs to your apps (Jellyfin, Plex...)"}
bp = Blueprint("shares", __name__, url_prefix="/shares")

ROOTS = [r.rstrip("/") + "/" for r in os.environ.get("APPS_DIRS", "/volume1/docker").split(",") if r.strip()]
MOUNT_UID = os.environ.get("PUID", "1000")
MOUNT_GID = os.environ.get("PGID", "1000")
SELF_PROJECT = "nas-panel"

# ---- Validation ---------------------------------------------------------------
RE_NAME = re.compile(r"^[a-z0-9]{1,20}$")
RE_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,62}$")
RE_PATHPART = re.compile(r"^[A-Za-z0-9 _.\-()&+]{1,80}$")
RE_SUB = re.compile(r"^[A-Za-z0-9 _.\-()&+/]{0,200}$")
RE_USER = re.compile(r"^[A-Za-z0-9_.@\\\-]{1,64}$")
RE_TARGET = re.compile(r"^/[A-Za-z0-9_.\-/]{1,100}$")
VERSIONS = ["3.0", "2.1", "2.0", "1.0"]
BLOCKED = ["/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/root", "/run", "/sbin",
           "/sys", "/usr", "/var", "/tmp", "/app", "/config", "/cache", "/opt", "/home", "/srv"]

HINTS = [
    ("error(13)", "Wrong username/password, or that user has no access to the share."),
    ("error(2)", "Share or folder name not found - check spelling and capitals."),
    ("error(95)", "SMB version not supported - try 2.1 or 1.0."),
    ("error(112)", "Can't reach that device - check the IP, or try SMB version 2.1 / 1.0."),
    ("error(113)", "Can't reach that device - check the IP and that it's switched on."),
    ("Host is down", "Can't reach that device - check the IP, or try SMB version 2.1 / 1.0."),
    ("No route", "Can't reach that device - check the IP and that it's switched on."),
    ("connection refused", "That device isn't accepting SMB connections - is file sharing (SMB) turned on?"),
    ("permission denied", "Wrong username/password, or that user has no access to the share."),
    ("No such device", "The NAS can't load SMB support (cifs). Tell Claude."),
]


def ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()


def registry():
    return core.load("shares.json", {})


def device(sh):
    d = f"//{sh['host']}/{sh['share']}"
    if sh.get("sub"):
        d += "/" + sh["sub"].strip("/")
    return d


def validate(f, new=True):
    """Check a submitted share form. Returns (share dict, error)."""
    sh = {k: (f.get(k) or "").strip() for k in ("name", "host", "share", "sub", "user", "vers")}
    sh["pass"] = f.get("pass") or ""
    if new and not RE_NAME.match(sh["name"]):
        return None, "Short name: lower-case letters and numbers only (max 20)."
    if not RE_HOST.match(sh["host"]):
        return None, "IP address / host name looks wrong."
    if not RE_PATHPART.match(sh["share"]):
        return None, "Share name: letters, numbers, spaces and - _ . ( ) & + only."
    if not RE_SUB.match(sh["sub"]) or ".." in sh["sub"]:
        return None, "Folder: letters, numbers, spaces, / and - _ . ( ) & + only."
    if not RE_USER.match(sh["user"]):
        return None, "Username contains characters that aren't allowed."
    if not sh["pass"] or any(c in sh["pass"] for c in ",'\n\r"):
        return None, "Password is required and can't contain commas, ' or line breaks (change it on the other device)."
    if sh["vers"] not in VERSIONS:
        sh["vers"] = "3.0"
    return sh, None


def hint_for(out):
    low = out.lower()
    for key, msg in HINTS:
        if key.lower() in low:
            return msg
    return ""


def test_share(sh):
    """Mount the share read-only in a throwaway container. Returns (ok, message)."""
    vol = "panel_test_" + secrets.token_hex(4)
    opts = (f"username={sh['user']},password={sh['pass']},vers={sh['vers']},"
            "ro,file_mode=0444,dir_mode=0555")
    rc, out = core.run(["docker", "volume", "create", "--driver", "local", "--opt", "type=cifs",
                        "--opt", f"device={device(sh)}", "--opt", f"o={opts}", vol])
    if rc:
        return False, out
    rc, out = core.run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "ls",
                        "-v", f"{vol}:/t:ro", core.SELF_IMAGE, "/t"], timeout=90)
    core.run(["docker", "volume", "rm", "-f", vol])
    if rc:
        out = out.replace(sh["pass"], "****")
        h = hint_for(out)
        if h:
            return False, h
        m = re.search(r"failed to mount local volume: .*?: (.+?)(\n|$)", out)
        return False, "Couldn't connect: " + (m.group(1) if m else out[-250:])
    n = len([x for x in out.splitlines() if x.strip()])
    if n == 0:
        return False, "Connected, but it looks empty - check that user has read access."
    return True, f"Connected OK - {n} items found."


# ---- Apps (docker compose projects) ----------------------------------------------
def apps():
    """Compose projects inside APPS_DIRS that the panel can manage."""
    fmt = "|".join("{{.Label \"%s\"}}" % k for k in (
        "com.docker.compose.project", "com.docker.compose.project.working_dir",
        "com.docker.compose.project.config_files", "com.docker.compose.service")) + "|{{.Names}}|{{.State}}"
    rc, out = core.run(["docker", "ps", "-a", "--format", fmt])
    res = {}
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) != 6:
            continue
        proj, wd, files, svc, cname, state = parts
        if not proj or proj == SELF_PROJECT or not any(wd.startswith(r) for r in ROOTS) or not os.path.isdir(wd):
            continue
        a = res.setdefault(proj, {"project": proj, "wd": wd, "services": {}, "base": []})
        a["services"][svc] = {"container": cname, "state": state}
        for fp in files.split(","):
            if fp and "/shares/" not in fp and fp not in a["base"]:
                a["base"].append(fp)
    for a in res.values():
        if not a["base"]:
            for n in ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml"):
                if os.path.exists(os.path.join(a["wd"], n)):
                    a["base"] = [os.path.join(a["wd"], n)]
                    break
        a["attached"] = attached(a)
    return dict(sorted(res.items()))


def attached(a):
    """Shares currently connected to an app: {name: {target, service}}."""
    out = {}
    for f in sorted(glob.glob(os.path.join(a["wd"], "shares", "*.yaml"))):
        name = os.path.basename(f)[:-5]
        txt = open(f).read()
        m = re.search(r"-\s*share_%s:([^:\n]+):ro" % re.escape(name), txt)
        s = re.search(r"^services:\s*\n\s+([A-Za-z0-9_.\-]+):", txt, re.M)
        out[name] = {"target": m.group(1) if m else "?", "service": s.group(1) if s else "?"}
    return out


def share_files(a):
    return sorted(glob.glob(os.path.join(a["wd"], "shares", "*.yaml")))


def compose(a, *args, timeout=600):
    cmd = ["docker", "compose", "-p", a["project"], "--project-directory", a["wd"]]
    for f in a["base"] + share_files(a):
        cmd += ["-f", f]
    return core.run(cmd + list(args), cwd=a["wd"], timeout=timeout)


def write_env(a, name, sh=None):
    """Update <app>/.env: set or remove SHARE_<NAME>_* lines and rebuild COMPOSE_FILE."""
    p = os.path.join(a["wd"], ".env")
    lines = open(p).read().splitlines() if os.path.exists(p) else []
    up = name.upper()
    lines = [l for l in lines if not l.startswith("COMPOSE_FILE=") and not l.startswith(f"SHARE_{up}_")]
    if sh:
        lines += [f"SHARE_{up}_USER='{sh['user']}'", f"SHARE_{up}_PASS='{sh['pass']}'"]
    files = [os.path.relpath(f, a["wd"]) for f in a["base"] + share_files(a)]
    lines.append("COMPOSE_FILE=" + ":".join(files))
    core.write_private(p, "\n".join(lines) + "\n")


def write_override(a, name, sh, service, target):
    os.makedirs(os.path.join(a["wd"], "shares"), exist_ok=True)
    up = name.upper()
    txt = f"""# Added by NAS Control Panel - share '{name}' (read-only)
services:
  {service}:
    volumes:
      - share_{name}:{target}:ro
volumes:
  share_{name}:
    driver: local
    driver_opts:
      type: cifs
      device: "{device(sh)}"
      o: "username=${{SHARE_{up}_USER}},password=${{SHARE_{up}_PASS}},vers={sh['vers']},ro,uid={MOUNT_UID},gid={MOUNT_GID},file_mode=0444,dir_mode=0555"
"""
    with open(os.path.join(a["wd"], "shares", f"{name}.yaml"), "w") as f:
        f.write(txt)


def existing_mounts(container):
    rc, out = core.run(["docker", "inspect", "-f", "{{range .Mounts}}{{.Destination}}\n{{end}}", container])
    return [l.strip() for l in out.splitlines() if l.strip()] if rc == 0 else []


def target_problem(target, used):
    t = target.rstrip("/")
    if not RE_TARGET.match(t) or ".." in t or t == "":
        return "Folder inside the app must look like /media/network/films"
    for b in BLOCKED:
        if t == b or t.startswith(b + "/"):
            return f"Can't use {b} - that's a system folder inside the app."
    for u in used:
        u = u.rstrip("/")
        if t == u or t.startswith(u + "/") or u.startswith(t + "/"):
            return f"{t} clashes with an existing folder in the app ({u})."
    return None


def wait_running(a, service, secs=40):
    end = time.time() + secs
    while time.time() < end:
        rc, cid = compose(a, "ps", "-q", service, timeout=60)
        cid = cid.strip().splitlines()[-1] if cid.strip() else ""
        if cid:
            rc, st = core.run(["docker", "inspect", "-f", "{{.State.Running}}", cid])
            if st.strip() == "true":
                return cid
        time.sleep(3)
    return None


def detach_raw(a, name):
    """Remove the share from an app (files first, then restart, then drop the volume)."""
    f = os.path.join(a["wd"], "shares", f"{name}.yaml")
    if os.path.exists(f):
        os.remove(f)
    write_env(a, name, None)
    rc, out = compose(a, "up", "-d", "--no-build")
    core.run(["docker", "volume", "rm", f"{a['project']}_share_{name}"])
    return rc, out


def attach_raw(a, name, sh, service, target):
    """Connect a share to an app. Rolls back on failure. Returns (ok, message)."""
    core.run(["docker", "volume", "rm", f"{a['project']}_share_{name}"])   # drop stale copy
    write_override(a, name, sh, service, target)
    write_env(a, name, sh)
    rc, out = compose(a, "config", "-q")
    if rc == 0:
        rc, out = compose(a, "up", "-d", "--no-build")
    if rc == 0:
        cid = wait_running(a, service)
        if cid:
            rc2, ls = core.run(["docker", "exec", cid, "ls", target], timeout=60)
            n = len([x for x in ls.splitlines() if x.strip()])
            if rc2 == 0 and n > 0:
                return True, f"{n} items visible at {target}"
            out = "App started but the share folder is empty or unreadable. " + ls[-200:]
        else:
            out = "App didn't start with the share connected. " + compose(a, "logs", "--tail", "15", service)[1][-400:]
    out = out.replace(sh["pass"], "****")
    detach_raw(a, name)          # roll back
    h = hint_for(out)
    return False, (h + " " if h else "") + out[-500:] + "  - Rolled back, app is as it was."


# ---- Routes ------------------------------------------------------------------------
@bp.route("/")
def index():
    return render_template("shares.html", shares=registry(), apps=apps(), versions=VERSIONS, device=device,
                           roots=", ".join(ROOTS))


@bp.route("/add", methods=["POST"])
def add():
    sh, err = validate(request.form)
    reg = registry()
    if not err and sh["name"] in reg:
        err = f"A share called '{sh['name']}' already exists."
    if err:
        flash(err, "err")
        return redirect(url_for(".index"))
    with core.LOCK:
        ok, msg = test_share(sh)
        if not ok:
            flash("Not saved - " + msg, "err")
            return redirect(url_for(".index"))
        name = sh.pop("name")
        sh["added"] = time.strftime("%Y-%m-%d %H:%M")
        reg[name] = sh
        core.save("shares.json", reg)
        core.log(f"Share '{name}' saved ({device(sh)}, user {sh['user']})", ip())
    flash(f"Share '{name}' saved. {msg} Now connect it to an app below.", "ok")
    return redirect(url_for(".index"))


@bp.route("/test/<name>", methods=["POST"])
def test(name):
    sh = registry().get(name)
    if not sh:
        flash("Unknown share.", "err")
    else:
        ok, msg = test_share(sh)
        flash(f"'{name}': {msg}", "ok" if ok else "err")
    return redirect(url_for(".index"))


@bp.route("/edit/<name>", methods=["POST"])
def edit(name):
    reg = registry()
    if name not in reg:
        flash("Unknown share.", "err")
        return redirect(url_for(".index"))
    form = dict(request.form)
    if not form.get("pass"):
        form["pass"] = reg[name]["pass"]        # blank = keep current password
    sh, err = validate(form, new=False)
    if err:
        flash(err, "err")
        return redirect(url_for(".index"))
    sh.pop("name", None)
    with core.LOCK:
        ok, msg = test_share(sh)
        if not ok:
            flash("Not changed - " + msg, "err")
            return redirect(url_for(".index"))
        sh["added"] = reg[name].get("added", "")
        reg[name] = sh
        core.save("shares.json", reg)
        core.log(f"Share '{name}' changed ({device(sh)}, user {sh['user']})", ip())
        results = []
        for a in apps().values():
            if name in a["attached"]:
                at = a["attached"][name]
                detach_raw(a, name)
                a = apps()[a["project"]]
                ok2, m2 = attach_raw(a, name, sh, at["service"], at["target"])
                results.append(f"{a['project']}: {'OK' if ok2 else 'FAILED - ' + m2}")
                core.log(f"Re-connected '{name}' to {a['project']}: {'OK' if ok2 else 'FAILED'}", ip())
    flash(f"Share '{name}' updated. " + " | ".join(results), "ok")
    return redirect(url_for(".index"))


@bp.route("/delete/<name>", methods=["POST"])
def delete(name):
    with core.LOCK:
        reg = registry()
        used = [p for p, a in apps().items() if name in a["attached"]]
        if used:
            flash(f"Disconnect '{name}' from {', '.join(used)} first.", "err")
        elif name in reg:
            del reg[name]
            core.save("shares.json", reg)
            core.log(f"Share '{name}' deleted", ip())
            flash(f"Share '{name}' deleted (files on the other device are untouched).", "ok")
    return redirect(url_for(".index"))


@bp.route("/attach", methods=["POST"])
def attach():
    name = request.form.get("name", "")
    project = request.form.get("project", "")
    with core.LOCK:
        sh = registry().get(name)
        a = apps().get(project)
        if not sh or not a:
            flash("Pick a share and an app.", "err")
            return redirect(url_for(".index"))
        if name in a["attached"]:
            flash(f"'{name}' is already connected to {project}.", "err")
            return redirect(url_for(".index"))
        service = request.form.get("service") or next(iter(a["services"]))
        if service not in a["services"]:
            flash("Unknown service.", "err")
            return redirect(url_for(".index"))
        target = (request.form.get("target") or f"/media/network/{name}").strip().rstrip("/")
        prob = target_problem(target, existing_mounts(a["services"][service]["container"]))
        if prob:
            flash(prob, "err")
            return redirect(url_for(".index"))
        ok, msg = attach_raw(a, name, sh, service, target)
        core.log(f"Connect '{name}' to {project} at {target}: {'OK' if ok else 'FAILED'}", ip())
    if ok:
        flash(f"'{name}' connected to {project} - {msg}. Now add {target} to a library in {project}.", "ok")
    else:
        flash(f"Couldn't connect '{name}' to {project}: {msg}", "err")
    return redirect(url_for(".index"))


@bp.route("/detach", methods=["POST"])
def detach():
    name = request.form.get("name", "")
    project = request.form.get("project", "")
    with core.LOCK:
        a = apps().get(project)
        if not a or name not in a["attached"] or not RE_NAME.match(name):
            flash("Nothing to disconnect.", "err")
            return redirect(url_for(".index"))
        rc, out = detach_raw(a, name)
        core.log(f"Disconnect '{name}' from {project}: {'OK' if rc == 0 else 'FAILED'}", ip())
    flash(f"'{name}' disconnected from {project}." if rc == 0 else f"Problem restarting {project}: {out[-300:]}",
          "ok" if rc == 0 else "err")
    return redirect(url_for(".index"))
