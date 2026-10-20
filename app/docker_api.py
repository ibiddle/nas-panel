"""Tiny Docker Engine API client over the Unix socket (no extra packages needed).

Used to rebuild a plain container with exactly the same settings plus/minus one mount.
"""
import http.client
import json
import socket
import time
import urllib.parse

SOCK = "/var/run/docker.sock"


class _Conn(http.client.HTTPConnection):
    def __init__(self, timeout=120):
        super().__init__("localhost", timeout=timeout)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(SOCK)


def api(method, path, body=None, timeout=120):
    c = _Conn(timeout)
    data = json.dumps(body) if body is not None else None
    c.request(method, path, data, {"Content-Type": "application/json"})
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        out = json.loads(raw) if raw else None
    except ValueError:
        out = raw.decode(errors="replace")
    return r.status, out


def q(s):
    return urllib.parse.quote(s, safe="")


def inspect(name):
    st, info = api("GET", f"/containers/{q(name)}/json")
    return info if st == 200 else None


def _err(res):
    return res.get("message", str(res)) if isinstance(res, dict) else str(res)


def clone_body(info, add=None, remove_target=None):
    """Create-request body that reproduces a container exactly, optionally adding/removing one mount.

    Anonymous volumes are carried over explicitly so no data is lost when the container is rebuilt.
    """
    cfg = json.loads(json.dumps(info["Config"]))
    hc = json.loads(json.dumps(info["HostConfig"]))
    short_id = info["Id"][:12]
    if cfg.get("Hostname") == short_id:
        cfg.pop("Hostname", None)

    covered = set()
    for b in hc.get("Binds") or []:
        parts = b.split(":")
        covered.add(parts[1] if len(parts) > 1 else parts[0])
    mounts = list(hc.get("Mounts") or [])
    covered.update(m.get("Target") for m in mounts)
    for m in info.get("Mounts") or []:
        if m.get("Type") == "volume" and m.get("Destination") not in covered:
            mounts.append({"Type": "volume", "Source": m["Name"], "Target": m["Destination"],
                           "ReadOnly": not m.get("RW", True)})
            covered.add(m["Destination"])
    if remove_target:
        mounts = [m for m in mounts if m.get("Target") != remove_target]
    if add:
        mounts.append(add)
    hc["Mounts"] = mounts

    body = cfg
    body["HostConfig"] = hc
    mode = hc.get("NetworkMode") or ""
    if mode not in ("host", "none") and not mode.startswith("container:"):
        eps = {}
        for net, v in (info.get("NetworkSettings", {}).get("Networks") or {}).items():
            e = {}
            if v.get("IPAMConfig"):
                e["IPAMConfig"] = v["IPAMConfig"]
            aliases = [a for a in (v.get("Aliases") or []) if a != short_id]
            if aliases:
                e["Aliases"] = aliases
            if v.get("Links"):
                e["Links"] = v["Links"]
            eps[net] = e
        body["NetworkingConfig"] = {"EndpointsConfig": eps}
    return body


def wait_running(name, secs=45):
    end = time.time() + secs
    while time.time() < end:
        info = inspect(name)
        if info and info["State"].get("Running") and not info["State"].get("Restarting"):
            time.sleep(3)                       # make sure it stays up
            info = inspect(name)
            if info and info["State"].get("Running") and not info["State"].get("Restarting"):
                return True
        time.sleep(2)
    return False


def recreate(name, add=None, remove_target=None):
    """Rebuild container `name` with the same settings +/- one mount. Rolls back on any failure.

    Returns (ok, message).
    """
    info = inspect(name)
    if not info:
        return False, f"Container {name} not found."
    if info["HostConfig"].get("AutoRemove"):
        return False, f"{name} deletes itself when stopped, so it can't be safely rebuilt."
    was_running = info["State"].get("Running")
    body = clone_body(info, add, remove_target)
    old = f"{name}_panel_old"

    api("DELETE", f"/containers/{q(old)}?force=1&v=0")            # leftover from a past attempt
    api("POST", f"/containers/{q(name)}/stop?t=30", timeout=60)
    st, res = api("POST", f"/containers/{q(name)}/rename?name={q(old)}")
    if st not in (200, 204):
        if was_running:
            api("POST", f"/containers/{q(name)}/start")
        return False, "Couldn't rename the old container: " + _err(res)

    def rollback(reason):
        api("DELETE", f"/containers/{q(name)}?force=1&v=0")
        api("POST", f"/containers/{q(old)}/rename?name={q(name)}")
        if was_running:
            api("POST", f"/containers/{q(name)}/start")
        return False, reason + " - Rolled back, app is as it was."

    st, res = api("POST", f"/containers/create?name={q(name)}", body)
    if st != 201:
        return rollback("Couldn't create the new container: " + _err(res))
    st, res = api("POST", f"/containers/{q(name)}/start", timeout=90)
    if st not in (204, 304):
        return rollback("The app wouldn't start with the change: " + _err(res))
    if not wait_running(name):
        st2, logs = api("GET", f"/containers/{q(name)}/logs?stdout=1&stderr=1&tail=10")
        return rollback("The app didn't stay running with the change.")
    if not was_running:
        api("POST", f"/containers/{q(name)}/stop?t=30", timeout=60)
    api("DELETE", f"/containers/{q(old)}?force=1&v=0")             # keep its volumes (now used by the new one)
    return True, "OK"
