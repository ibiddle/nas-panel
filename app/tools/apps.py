"""Apps: read-only overview of Docker apps on the NAS."""
from flask import Blueprint, render_template

import core

TOOL = {"id": "apps", "name": "Apps", "icon": "📦", "endpoint": "apps.index",
        "desc": "What's running on the NAS (view only)"}
bp = Blueprint("apps", __name__, url_prefix="/apps")


@bp.route("/")
def index():
    rc, out = core.run(["docker", "ps", "-a", "--format",
                        "{{.Names}}|{{.Image}}|{{.State}}|{{.Status}}|{{.Ports}}"])
    rows = [dict(zip(("name", "image", "state", "status", "ports"), l.split("|", 4)))
            for l in out.splitlines() if l.count("|") >= 4]
    rows.sort(key=lambda r: (r["state"] != "running", r["name"]))
    return render_template("apps.html", rows=rows)
