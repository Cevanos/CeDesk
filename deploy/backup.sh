#!/usr/bin/env bash
# CeDesk backup: the database and the uploaded files, encrypted for an offline key.
#
# Cron (as root, or the user that runs docker):
#   30 2 * * * /path/to/CeDesk/deploy/backup.sh >> /var/log/cedesk-backup.log 2>&1
#
# Settings come from the environment, or from deploy/backup.env (see backup.env.example):
#   BACKUP_GPG_RECIPIENT  fingerprint of the PUBLIC key backups are encrypted for. Only the
#                         public key is on this server; the private key is kept offline, so
#                         whoever reads the backup folder still cannot read a backup.
#   BACKUP_DIR            where backups go (default ~/cedesk-backups). Created 0700.
#   BACKUP_KEEP_DAYS      delete backup sets older than this (default 14) ...
#   BACKUP_KEEP_MIN       ... but always keep at least this many newest sets (default 7).
#   BACKUP_FILES          1 (default) also backs up uploaded files; 0 skips them.
#   BACKUP_COMPOSE        how to call compose (default "docker compose"; on a server with an
#                         extra file: "docker compose -f docker-compose.yml -f x.yml").
#   BACKUP_ALERT_URL      optional: on failure, a JSON {"text": ...} is POSTed here (Slack-style).
#   BACKUP_REQUIRE_ENCRYPTION=1  refuse to back up without a recipient. Without it, a
#                         missing recipient still writes the backup (0600, in a 0700 folder),
#                         sends the alert and exits 3: a backup that is not encrypted is
#                         better than none, and cron's log says so every night.
#
# One backup set = cedesk-<stamp>.manifest plus the files it names (db and storage, each
# .gz.gpg). The manifest holds the SHA-256 of every file, before and after encryption;
# deploy/restore.sh checks both. Only sets this script made are ever deleted: older
# backups (cedesk-*.sql.gz from the previous script) are never touched.
set -Eeuo pipefail
shopt -s inherit_errexit
umask 077
cd "$(dirname "$0")"

conf="${BACKUP_CONFIG:-./backup.env}"
# shellcheck disable=SC1090
[ -f "$conf" ] && . "$conf"

dir="${BACKUP_DIR:-$HOME/cedesk-backups}"
keep_days="${BACKUP_KEEP_DAYS:-14}"
keep_min="${BACKUP_KEEP_MIN:-7}"
with_files="${BACKUP_FILES:-1}"
recipient="${BACKUP_GPG_RECIPIENT:-}"
read -r -a compose <<< "${BACKUP_COMPOSE:-docker compose}"
files_service="${BACKUP_FILES_SERVICE:-api}"
files_path="${BACKUP_FILES_PATH:-/data/storage}"
stamp="$(date -u +%Y%m%d-%H%M%S)"
step="start"

alert() {
    [ "$BASHPID" = "$$" ] || return 0  # once, from the script itself, not each subshell
    echo "CeDesk backup FAILED at step: $step ($(date -u +%FT%TZ))" >&2
    if [ -n "${BACKUP_ALERT_URL:-}" ]; then
        curl -fsS -m 10 -X POST -H 'Content-Type: application/json' \
            --data "{\"text\": \"CeDesk backup failed on $(hostname) at step: $step\"}" \
            "$BACKUP_ALERT_URL" >/dev/null || echo "could not send the alert" >&2
    fi
}

work=""
cleanup() { [ -n "$work" ] && rm -rf "$work"; }
trap 'alert' ERR
trap 'cleanup' EXIT

step="prepare"
mkdir -p "$dir"
chmod 700 "$dir"
work="$(mktemp -d "$dir/.work-XXXXXX")"

encrypted=1
if [ -z "$recipient" ]; then
    if [ "${BACKUP_REQUIRE_ENCRYPTION:-0}" = "1" ]; then
        step="config: BACKUP_GPG_RECIPIENT is not set (see deploy/backup.env.example)"
        false
    fi
    encrypted=0
    echo "WARNING: BACKUP_GPG_RECIPIENT is not set; this backup is NOT encrypted." >&2
else
    step="config: the public key $recipient is not in gpg's keyring"
    gpg --batch --list-keys "$recipient" >/dev/null 2>&1
fi

# Writes stdin to $work/<name>, checks it is a whole gzip stream, records its SHA-256,
# then encrypts it to $work/<name>.gpg (OpenPGP: integrity-protected) and removes the
# plain copy. The plain copy only ever sits in the 0700 work folder.
seal() {
    local name="$1"
    cat > "$work/$name"
    gzip -t "$work/$name"
    printf '%s  %s\n' "$(sha256sum < "$work/$name" | cut -d' ' -f1)" "$name" >> "$work/plain.sha256"
    if [ "$encrypted" = 1 ]; then
        gpg --batch --yes --quiet --trust-model always --recipient "$recipient" \
            --output "$work/$name.gpg" --encrypt "$work/$name"
        rm -f "$work/$name"
        echo "$name.gpg"
    else
        echo "$name"
    fi
}

step="database dump"
db_file="$("${compose[@]}" exec -T db pg_dump -U postgres -d cedesk | gzip | seal "cedesk-$stamp-db.sql.gz")"

files_file=""
if [ "$with_files" = "1" ]; then
    step="uploaded files ($files_service:$files_path)"
    files_file="$("${compose[@]}" exec -T "$files_service" tar -C "$files_path" -cf - . \
        | gzip | seal "cedesk-$stamp-files.tar.gz")"
fi

step="manifest"
manifest="$work/cedesk-$stamp.manifest"
{
    echo "# CeDesk backup $stamp (UTC). encrypted=$encrypted recipient=${recipient:-none}"
    echo "# stored: sha256 of each file as stored"
    for f in $db_file $files_file; do
        printf 'stored %s  %s\n' "$(sha256sum < "$work/$f" | cut -d' ' -f1)" "$f"
    done
    echo "# plain: sha256 of each file before encryption"
    sed 's/^/plain /' "$work/plain.sha256"
} > "$manifest"

step="move into place"
for f in $db_file $files_file; do
    mv "$work/$f" "$dir/$f"
done
mv "$manifest" "$dir/"
chmod 600 "$dir"/cedesk-"$stamp"*

step="retention"
# Sets (by manifest) older than keep_days, never the newest keep_min, never older backups.
mapfile -t sets < <(find "$dir" -maxdepth 1 -name 'cedesk-*.manifest' -printf '%T@ %p\n' \
    | sort -rn | cut -d' ' -f2-)
cutoff="$(date -d "-$keep_days days" +%s)"
for i in "${!sets[@]}"; do
    [ "$i" -lt "$keep_min" ] && continue
    m="${sets[$i]}"
    [ "$(stat -c %Y "$m")" -ge "$cutoff" ] && continue
    base="${m%.manifest}"
    rm -f "$base"-db.sql.gz* "$base"-files.tar.gz* "$m"
done

trap - ERR
echo "Backed up to $dir/cedesk-$stamp.manifest (encrypted=$encrypted, files=$with_files)"
if [ "$encrypted" = 0 ]; then
    step="written, but NOT encrypted: set BACKUP_GPG_RECIPIENT"
    alert
    exit 3
fi
