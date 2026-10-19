#!/bin/sh
# NAS Control Panel auto-updater (runs from the same image with entrypoint /updater.sh).
# Once a day at UPDATE_TIME it pulls the latest image and restarts the panel only if it changed.
# Works out the compose project from its own container labels - no extra settings needed.
#   Update now:  docker exec nas-panel-updater /updater.sh now
T=${UPDATE_TIME:-04:00}
SVC=${PANEL_SERVICE:-nas-panel}
SELF_SVC=${UPDATER_SERVICE:-updater}
log(){ echo "$(date '+%Y-%m-%d %H:%M:%S')  $*"; }

me=$(hostname)
docker inspect "$me" >/dev/null 2>&1 || me=nas-panel-updater
lbl(){ docker inspect -f "{{index .Config.Labels \"com.docker.compose.$1\"}}" "$me" 2>/dev/null; }
PROJECT=$(lbl project); WD=$(lbl project.working_dir); CF=$(lbl project.config_files)

compose(){
  set -- -p "$PROJECT" --project-directory "$WD" $(echo "$CF" | tr ',' '\n' | sed 's/^/-f /') "$@"
  docker compose "$@"
}

check(){
  if [ -z "$PROJECT" ]; then log "ERROR: start the updater with docker compose (it reads its project from compose labels)."; return 1; fi
  for f in $(echo "$CF" | tr ',' ' '); do
    [ -r "$f" ] || { log "ERROR: can't read $f - mount the folder holding compose.yaml at the SAME path in the updater's volumes."; return 1; }
  done
}

update(){
  check || return 1
  log "Checking for a new image..."
  old=$(docker inspect -f '{{.Image}}' "$(compose ps -q "$SVC" | head -1)" 2>/dev/null)
  if ! compose pull -q "$SVC" "$SELF_SVC"; then log "Pull failed (no internet / registry down?) - will try again next time."; return 1; fi
  compose up -d --no-deps "$SVC" >/dev/null 2>&1
  new=$(docker inspect -f '{{.Image}}' "$(compose ps -q "$SVC" | head -1)" 2>/dev/null)
  if [ "$old" = "$new" ]; then log "Already up to date."; return 0; fi
  log "Panel updated: ${old#sha256:} -> ${new#sha256:}" | cut -c1-120
  # Restart this updater on the new image from a short-lived helper (it can't recreate itself).
  img=$(docker inspect -f '{{.Config.Image}}' "$me")
  docker run -d --rm -v /var/run/docker.sock:/var/run/docker.sock -v "$WD:$WD:ro" -w "$WD" \
    --entrypoint sh "$img" -c "sleep 5; docker compose -p '$PROJECT' --project-directory '$WD' $(echo "$CF" | tr ',' '\n' | sed "s/^/-f '/;s/\$/'/" | tr '\n' ' ') up -d --no-deps $SELF_SVC" >/dev/null
  docker image prune -f --filter "label=org.opencontainers.image.title=NAS Control Panel" >/dev/null 2>&1
}

[ "$1" = "now" ] && { update; exit $?; }
check
log "Auto-update for '$SVC' (project $PROJECT) scheduled daily at $T"
while true; do
  [ "$(date +%H:%M)" = "$T" ] && { update; sleep 61; }
  sleep 30
done
