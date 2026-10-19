"""NAS-wide shares: shows what the NAS has mounted, tests a share, explains the NAS's own Remote Mount.

Mounting for the whole NAS needs root, so it's left to the NAS's own (safe) remote-mount
feature rather than giving this panel root access.
"""
import os

from flask import Blueprint, flash, redirect, render_template, request, url_for

from tools import shares

TOOL = {"id": "mounts", "name": "NAS Shares", "icon": "🗄️", "endpoint": "mounts.index",
        "desc": "Shares mounted on the NAS itself (NAS file manager) + a share tester"}
bp = Blueprint("mounts", __name__, url_prefix="/nas-shares")

NET_FS = {"cifs", "smb3", "nfs", "nfs4"}


def host_mounts():
    rows = []
    try:
        for line in open("/host/mounts"):
            p = line.split()
            if len(p) >= 3 and p[2] in NET_FS and "/var/lib/docker" not in p[1] and "/@docker" not in p[1]:
                rows.append({"source": p[0].replace("\\040", " "), "path": p[1].replace("\\040", " "), "type": p[2]})
    except OSError:
        pass
    return rows


@bp.route("/")
def index():
    return render_template("mounts.html", mounts=host_mounts(), nas_ui=os.environ.get("NAS_UI_URL", ""),
                           mounts_ok=os.path.exists("/host/mounts"),
                           versions=shares.VERSIONS)


@bp.route("/test", methods=["POST"])
def test():
    form = dict(request.form)
    form["name"] = "test"
    sh, err = shares.validate(form)
    if err:
        flash(err, "err")
    else:
        ok, msg = shares.test_share(sh)
        flash(f"{shares.device(sh)}: {msg}", "ok" if ok else "err")
    return redirect(url_for(".index"))
