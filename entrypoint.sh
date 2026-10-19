#!/bin/sh
# NAS Control Panel entrypoint: detects the Docker socket's group, then drops to PUID:PGID.
set -e
SOCK=/var/run/docker.sock
CMD="gunicorn -w 1 --threads 4 -b 0.0.0.0:${PANEL_PORT:-8090} --timeout 600 --access-logfile - app:app"

if [ ! -S "$SOCK" ]; then
  echo "ERROR: Docker socket not found. Add  - /var/run/docker.sock:/var/run/docker.sock  to volumes." >&2
  exit 1
fi

if [ "$(id -u)" != "0" ]; then          # started with 'user:' - run as given
  exec $CMD
fi

PUID=${PUID:-1000}; PGID=${PGID:-1000}
DGID=$(stat -c %g "$SOCK")

grp_for() { getent group | awk -F: -v g="$1" '$3==g{print $1; exit}'; }
G=$(grp_for "$PGID");  [ -n "$G" ] || { addgroup -g "$PGID" panel; G=panel; }
U=$(getent passwd | awk -F: -v u="$PUID" '$3==u{print $1; exit}')
[ -n "$U" ] || { adduser -D -H -u "$PUID" -G "$G" -s /sbin/nologin panel; U=panel; }
DG=$(grp_for "$DGID"); [ -n "$DG" ] || { addgroup -g "$DGID" dockerhost; DG=dockerhost; }
addgroup "$U" "$DG" 2>/dev/null || true

mkdir -p /data && chown "$PUID:$PGID" /data && chmod 700 /data
export HOME=/tmp
exec su-exec "$U" env HOME=/tmp $CMD
