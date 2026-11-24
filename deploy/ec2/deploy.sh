#!/usr/bin/env bash
# Blue/green release of CeDesk (the API and the web app) on the EC2 box.
#
#   deploy/ec2/deploy.sh migrate <tag>     run the migrations on the new image
#   deploy/ec2/deploy.sh release <tag>     start the idle slot on <tag>, flip nginx, stop the old slot
#   deploy/ec2/deploy.sh workers <tag>     recreate the worker on <tag> and check it stays up
#   deploy/ec2/deploy.sh rollback          start the previous slot again and flip nginx back
#   deploy/ec2/deploy.sh status            which slot is live, on which tag
#
# Run from the deploy checkout as the runner user. Both images must already exist locally
# (`docker build -t cedesk-api:<tag> .` and `docker build -t cedesk-web:<tag> frontend`).
#
# A slot is an API and the web app that calls that API, started together and released
# together. Users never see a failed release: nginx is pointed at a slot only after both
# halves answer, a slot that fails the smoke test through nginx is flipped back before
# the old one is stopped, and the old one is stopped only once every nginx worker that
# could still send it a request has exited.
set -euo pipefail

cd "$(dirname "$0")/../.."

COMPOSE_FILE=${CEDESK_COMPOSE_FILE:-deploy/ec2/docker-compose.prod.yml}
PROJECT=${CEDESK_COMPOSE_PROJECT:-cedesk-prod}
ENV_FILE=${CEDESK_ENV_FILE:-.env}
MIGRATE_ENV_FILE=${CEDESK_MIGRATE_ENV_FILE:-.env.migrate}
STATE=${CEDESK_STATE_DIR:-.deploy}
# Included by nginx at the http level, with the Cevanos upstreams (deploy/ec2/README.md).
UPSTREAMS=${CEDESK_UPSTREAMS:-/etc/nginx/cevanos/cedesk-upstreams.conf}
UPSTREAM_HOST=${CEDESK_UPSTREAM_HOST:-127.0.0.1}
# nginx serves /_next/static from here, so a page loaded before a release can still fetch
# its own build's chunks after it. Hashed names, so builds never collide.
STATIC_ROOT=${CEDESK_STATIC_ROOT:-/var/www/cedesk}
KEEP_BUILDS=${CEDESK_KEEP_BUILDS:-5}
NGINX=${CEDESK_NGINX:-sudo -n nginx}
# The smoke test resolves the public name to this box's own nginx, so it goes through TLS
# and the real server block. Empty skips it (before the site and its certificate exist).
SMOKE_HOST=${CEDESK_SMOKE_HOST-}
MIN_FREE_MB=${CEDESK_MIN_FREE_MB:-1500}
# nginx's old workers are shared with every other site on the box, and a live-update
# stream never ends by itself; past this they are left to finish on their own.
DRAIN_TIMEOUT=${CEDESK_DRAIN_TIMEOUT:-60}
# Seconds between starting one container and the next, so a start-up does not slow the
# live slot on a shared box.
START_GAP=${CEDESK_START_GAP:-3}

log() { echo "[deploy] $*"; }
die() { echo "::error::$*" >&2; exit 1; }

# The compose file needs IMAGE_TAG to parse. Only `up` uses it; for stop, start, logs and
# ps any value does, so it falls back to the live tag.
compose() {
  IMAGE_TAG=${IMAGE_TAG:-$(cat "$STATE/image_tag" 2>/dev/null || echo none)} \
    docker compose --env-file "$ENV_FILE" -p "$PROJECT" -f "$COMPOSE_FILE" "$@"
}

port() { # port <api|web> <colour>
  local base
  case $1 in
    api) base=8801 ;;
    web) base=8802 ;;
  esac
  [ "$2" = green ] && base=$((base + 10))
  echo "$base"
}

ready_path() { [ "$1" = api ] && echo /readyz || echo /api/health; }

other() { [ "$1" = green ] && echo blue || echo green; }

slot_services() { echo "api-$1 web-$1"; }

active() {
  if [ -s "$STATE/active" ]; then
    cat "$STATE/active"
  else
    echo none
  fi
}

wait_ready() { # wait_ready <api|web> <colour>
  local url
  url="http://127.0.0.1:$(port "$1" "$2")$(ready_path "$1")"
  for _ in $(seq 1 60); do
    curl -fsS --max-time 3 "$url" >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}

# The web app reaches its API by service name inside the compose network, which the
# published ports above never exercise.
wait_link() { # wait_link <colour>
  for _ in $(seq 1 15); do
    compose exec -T "web-$1" wget -qO- --timeout=3 "http://api-$1:8100/readyz" >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}

nginx_uses_upstreams() {
  local conf name
  conf=$($NGINX -T 2>/dev/null) || return 1
  for name in cedesk_api cedesk_web; do
    grep -q "upstream $name " <<<"$conf" || return 1
    grep -q "proxy_pass http://$name" <<<"$conf" || return 1
  done
}

nginx_workers() {
  if [ -n "${CEDESK_NGINX_WORKERS:-}" ]; then
    eval "$CEDESK_NGINX_WORKERS"
  else
    pgrep -f 'nginx: worker process' || true
  fi
}

wait_for_exit() { # wait_for_exit "<pids>"
  local pid alive
  for _ in $(seq 1 "$DRAIN_TIMEOUT"); do
    alive=0
    for pid in $1; do
      grep -qx "$pid" <<<"$(nginx_workers)" && alive=1 && break
    done
    [ "$alive" -eq 0 ] && return 0
    sleep 1
  done
  log "old nginx workers still open after ${DRAIN_TIMEOUT}s; going on"
}

point_nginx_at() { # point_nginx_at <colour>
  local colour=$1 tmp before
  tmp=$(mktemp)
  {
    echo "# Written by deploy/ec2/deploy.sh. The live slot is $colour."
    echo "upstream cedesk_api { server $UPSTREAM_HOST:$(port api "$colour"); }"
    echo "upstream cedesk_web { server $UPSTREAM_HOST:$(port web "$colour"); }"
  } >"$tmp"
  cp "$UPSTREAMS" "$tmp.prev" 2>/dev/null || : >"$tmp.prev"
  cat "$tmp" >"$UPSTREAMS"
  if ! $NGINX -t >/dev/null 2>&1; then
    cat "$tmp.prev" >"$UPSTREAMS"
    rm -f "$tmp" "$tmp.prev"
    die "nginx -t rejected the new upstreams; left nginx as it was"
  fi
  rm -f "$tmp" "$tmp.prev"
  # Both upstreams change in this one reload, so a page and its API never come from
  # different slots. `-s reload` returns before the old workers exit, and until they do
  # they still proxy to the old slot: it is stopped only once every worker from before
  # the reload is gone.
  before=$(nginx_workers)
  $NGINX -s reload
  wait_for_exit "$before"
  log "nginx now points at $colour"
}

# Adds the new build's static files to the shared directory and drops the files no kept
# build lists. Runs before the flip, so the new HTML never names a chunk nginx cannot find.
publish_static() { # publish_static <colour> <tag>
  local cid manifests keep
  cid=$(compose ps -q "web-$1")
  mkdir -p "$STATIC_ROOT/_next/static" "$STATE/static"
  docker cp "$cid:/app/.next/static/." "$STATIC_ROOT/_next/static/"
  docker exec "$cid" sh -c 'cd /app/.next/static && find . -type f' | sed 's#^\./##' | sort >"$STATE/static/$2.files"
  touch "$STATE/static/$2.files"

  # shellcheck disable=SC2012 # tag file names, no odd characters
  manifests=$(ls -t "$STATE/static/"*.files | head -n "$KEEP_BUILDS")
  keep=$(mktemp)
  # shellcheck disable=SC2086
  sort -u $manifests >"$keep"
  (cd "$STATIC_ROOT/_next/static" && find . -type f | sed 's#^\./##' | sort) \
    | comm -23 - "$keep" | (cd "$STATIC_ROOT/_next/static" && xargs -r rm -f --)
  find "$STATIC_ROOT/_next/static" -mindepth 1 -type d -empty -delete
  # shellcheck disable=SC2012
  ls -t "$STATE/static/"*.files | tail -n +"$((KEEP_BUILDS + 1))" | xargs -r rm -f --
  rm -f "$keep"
  log "static files: $(wc -l <"$STATE/static/$2.files") in this build, $(find "$STATIC_ROOT/_next/static" -type f | wc -l) kept from the last $KEEP_BUILDS"
}

smoke() {
  [ -n "$SMOKE_HOST" ] || return 0
  local resolve=(--resolve "$SMOKE_HOST:443:127.0.0.1") path
  # The API, the web app and the web app's own route, each through the real server block.
  for path in /readyz /api/health /login; do
    curl -fsS --max-time 10 "${resolve[@]}" -o /dev/null "https://$SMOKE_HOST$path" || return 1
  done
}

stop_slot() { # stop_slot <colour>; kept, stopped, for rollback
  # shellcheck disable=SC2046
  compose stop $(slot_services "$1")
  log "stopped $1"
}

enough_memory() {
  [ -r /proc/meminfo ] || return 0
  local free
  free=$(awk '/MemAvailable/ {print int($2 / 1024)}' /proc/meminfo)
  [ -n "$free" ] || return 0
  [ "$free" -ge "$MIN_FREE_MB" ] || die "only ${free} MB available; a release needs ${MIN_FREE_MB} MB for the new slot"
}

need_images() { # need_images <tag>
  docker image inspect "cedesk-api:$1" >/dev/null 2>&1 || die "no local image cedesk-api:$1"
  docker image inspect "cedesk-web:$1" >/dev/null 2>&1 || die "no local image cedesk-web:$1"
}

migrate() {
  local tag=$1
  need_images "$tag"
  [ -r "$ENV_FILE" ] || die "$ENV_FILE is missing"
  [ -r "$MIGRATE_ENV_FILE" ] || die "$MIGRATE_ENV_FILE is missing"
  # The old slot keeps serving while this runs and after, so a migration must keep the
  # previous release working: add now, remove in a later release.
  IMAGE_TAG=$tag compose --env-file "$MIGRATE_ENV_FILE" run --rm --no-deps -T migrate
}

release() {
  local tag=$1 live idle
  need_images "$tag"
  mkdir -p "$STATE"
  live=$(active)
  idle=$(other "$live")
  [ "$live" = none ] && idle=blue
  log "live: $live, releasing $tag to $idle"

  if [ "$live" != none ]; then
    nginx_uses_upstreams || die "nginx does not proxy through $UPSTREAMS yet -- see deploy/ec2/README.md, one-time setup"
  fi
  enough_memory

  # The API first, then the web app that calls it.
  IMAGE_TAG=$tag compose up -d --no-deps --no-build --force-recreate "api-$idle"
  sleep "$START_GAP"
  IMAGE_TAG=$tag compose up -d --no-deps --no-build --force-recreate "web-$idle"

  local half
  for half in api web; do
    if ! wait_ready "$half" "$idle"; then
      compose logs --tail=100 "$half-$idle" || true
      # shellcheck disable=SC2046
      compose stop $(slot_services "$idle")
      echo "$tag" >"$STATE/idle_failed"
      die "$half-$idle never answered $(ready_path "$half"); nginx was not touched"
    fi
    log "$half-$idle ready on $(port "$half" "$idle")"
  done
  if ! wait_link "$idle"; then
    compose logs --tail=50 "web-$idle" || true
    # shellcheck disable=SC2046
    compose stop $(slot_services "$idle")
    echo "$tag" >"$STATE/idle_failed"
    die "web-$idle cannot reach api-$idle; nginx was not touched"
  fi

  publish_static "$idle" "$tag"
  point_nginx_at "$idle"
  if ! smoke; then
    [ "$live" != none ] && point_nginx_at "$live"
    # shellcheck disable=SC2046
    compose stop $(slot_services "$idle")
    echo "$tag" >"$STATE/idle_failed"
    die "smoke test through nginx failed on $idle; flipped back to $live"
  fi

  echo "$idle" >"$STATE/active"
  rm -f "$STATE/idle_failed"
  [ -s "$STATE/image_tag" ] && cp "$STATE/image_tag" "$STATE/previous_image_tag"
  echo "$tag" >"$STATE/image_tag"
  [ "$live" != none ] && stop_slot "$live"
  log "released $tag on $idle"
}

# The worker is recreated in place: stopped, then started on the new image, so two never
# overlap (a mailbox is read by one). What it was doing waits in the database.
workers() {
  local tag=$1 scan cid restarts state
  need_images "$tag"
  scan=$(grep -E '^CEDESK_SCAN_BACKEND=' "$ENV_FILE" | tail -n 1 | sed "s/^[^=]*=//; s/^'//; s/'\$//" || true)
  if [ "${scan:-clamd}" != none ]; then
    # Started if it is not running; never recreated, so definitions are not reloaded.
    IMAGE_TAG=$tag compose up -d --no-deps --no-recreate clamav
  fi
  IMAGE_TAG=$tag compose up -d --no-deps --no-build --force-recreate worker

  sleep 15
  state=$(compose ps --format '{{.State}}' worker)
  if [ "$state" != "running" ]; then
    compose logs --tail=100 worker || true
    die "worker is '$state', not running"
  fi
  cid=$(compose ps -q worker)
  restarts=$(docker inspect --format '{{.RestartCount}}' "$cid" 2>/dev/null || echo 0)
  if [ "${restarts:-0}" -ne 0 ]; then
    compose logs --tail=100 worker || true
    die "worker has restarted ${restarts} time(s) -- it is crash-looping (the reason is in the lines above)"
  fi
  log "worker running on $tag, 0 restarts"
}

rollback() {
  local live prev half
  live=$(active)
  case $live in
    blue | green) ;;
    *) die "nothing to roll back to: live is '$live'" ;;
  esac
  prev=$(other "$live")
  # A failed release leaves its image in the idle slot, so that slot is no longer the
  # version before; release that version's tag instead.
  if [ -s "$STATE/idle_failed" ]; then
    die "the $prev slot holds the failed release $(cat "$STATE/idle_failed"); run: $0 release $(cat "$STATE/previous_image_tag" 2>/dev/null || echo '<previous tag>')"
  fi
  # shellcheck disable=SC2046
  compose start $(slot_services "$prev")
  for half in api web; do
    wait_ready "$half" "$prev" || die "$half-$prev did not come back; nginx still points at $live"
  done
  wait_link "$prev" || die "web-$prev cannot reach api-$prev; nginx still points at $live"
  point_nginx_at "$prev"
  echo "$prev" >"$STATE/active"
  if [ -s "$STATE/previous_image_tag" ]; then
    cp "$STATE/image_tag" "$STATE/rolled_back_image_tag"
    cp "$STATE/previous_image_tag" "$STATE/image_tag"
  fi
  stop_slot "$live"
  log "rolled back to $prev"
}

status() {
  echo "live slot: $(active)"
  echo "image tag: $(cat "$STATE/image_tag" 2>/dev/null || echo unknown)"
  echo "previous:  $(cat "$STATE/previous_image_tag" 2>/dev/null || echo none)"
  [ -s "$STATE/idle_failed" ] && echo "idle slot: failed release $(cat "$STATE/idle_failed"), not a rollback target"
  return 0
}

case ${1:-} in
  migrate) migrate "${2:?usage: $0 migrate <image-tag>}" ;;
  release) release "${2:?usage: $0 release <image-tag>}" ;;
  workers) workers "${2:?usage: $0 workers <image-tag>}" ;;
  rollback) rollback ;;
  status) status ;;
  *) die "usage: $0 migrate|release|workers <image-tag> | rollback | status" ;;
esac
