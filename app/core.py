"""Shared helpers: storage in /data, activity log, running commands safely."""
import datetime
import json
import os
import socket
import subprocess
import threading

DATA = os.environ.get("PANEL_DATA", "/data")
LOCK = threading.RLock()          # one change at a time
_self_image = None


def self_image():
    """The image this panel is running from (used for throwaway test containers)."""
    global _self_image
    if not _self_image:
        rc, out = run(["docker", "inspect", "-f", "{{.Config.Image}}", socket.gethostname()])
        _self_image = out.strip() if rc == 0 and out.strip() else os.environ.get(
            "SELF_IMAGE", "ghcr.io/ibiddle/nas-panel:latest")
    return _self_image


def _p(name):
    return os.path.join(DATA, name)


def load(name, default):
    try:
        with open(_p(name)) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(name, obj):
    write_private(_p(name), json.dumps(obj, indent=2))


def write_private(path, text):
    """Write a file readable only by the owner (mode 600), atomically."""
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def log(msg, ip=""):
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    if ip:
        line += f"   [{ip}]"
    with LOCK, open(_p("activity.log"), "a") as f:
        f.write(line + "\n")


def read_log(n=200):
    try:
        with open(_p("activity.log")) as f:
            return f.read().splitlines()[-n:][::-1]
    except FileNotFoundError:
        return []


def run(cmd, cwd=None, timeout=300):
    """Run a command given as a list (never through a shell). Returns (rc, output)."""
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "Timed out"


def _fmt_time(iso):
    """Docker/ISO UTC timestamp -> '20 Oct 2026, 13:15' in the NAS's local time."""
    try:
        iso = iso.replace("Z", "+00:00")
        if "." in iso:                                   # trim nanoseconds
            head, rest = iso.split(".", 1)
            tz = rest[rest.find("+"):] if "+" in rest else ""
            iso = head + tz
        return datetime.datetime.fromisoformat(iso).astimezone().strftime("%d %b %Y, %H:%M")
    except (ValueError, TypeError, AttributeError):
        return ""


_version = None


def version_info():
    """Version number, build date, and when this copy was installed/updated on this NAS."""
    global _version
    if _version is None:
        rc, created = run(["docker", "inspect", "-f", "{{.Created}}", socket.gethostname()])
        _version = {
            "version": os.environ.get("PANEL_VERSION", "dev"),
            "built": _fmt_time(os.environ.get("PANEL_BUILD", "")),
            "updated": _fmt_time(created.strip()) if rc == 0 else "",
        }
    return _version
