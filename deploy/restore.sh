#!/usr/bin/env bash
# Restores a CeDesk backup set made by deploy/backup.sh.
#
#   deploy/restore.sh <path/to/cedesk-STAMP.manifest> [--db NAME] [--files-to DIR] [--force]
#
# 1. Checks every stored file against the manifest (a changed or cut-off file stops here).
# 2. Decrypts with the PRIVATE key, which must be in gpg's keyring for this run only
#    (import it from where it is kept offline, restore, then delete it again:
#    `gpg --delete-secret-keys <fingerprint>`). Checks the decrypted SHA-256 and gzip.
# 3. --db NAME: loads the database into a NEW database NAME (default cedesk_restore_STAMP),
#    so the live one is untouched and the copy can be checked first. Restoring over the
#    live database `cedesk` needs --force, with the api and worker stopped first.
# 4. --files-to DIR: unpacks the uploaded files into DIR (copy them into the files volume
#    yourself, with the api stopped).
#
# BACKUP_COMPOSE as in backup.sh. Nothing is decrypted outside a 0700 temporary folder.
set -Eeuo pipefail
shopt -s inherit_errexit
umask 077

usage() { echo "usage: $0 <cedesk-STAMP.manifest> [--db NAME] [--files-to DIR] [--force]" >&2; exit 2; }
[ $# -ge 1 ] || usage
manifest="$(realpath "$1")"; shift
[ -f "$manifest" ] || { echo "no such manifest: $manifest" >&2; exit 2; }
db_name=""; files_to=""; force=0
while [ $# -gt 0 ]; do
    case "$1" in
        --db) db_name="${2:?}"; shift 2 ;;
        --files-to) files_to="${2:?}"; shift 2 ;;
        --force) force=1; shift ;;
        *) usage ;;
    esac
done
[ -z "$files_to" ] || files_to="$(realpath -m "$files_to")"
cd "$(dirname "$0")"  # after the paths above are made absolute; compose runs from here
read -r -a compose <<< "${BACKUP_COMPOSE:-docker compose}"
src="$(dirname "$manifest")"
stamp="$(basename "$manifest" .manifest)"; stamp="${stamp#cedesk-}"
[ -n "$db_name" ] || db_name="cedesk_restore_${stamp//-/_}"
[[ "$db_name" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || { echo "bad database name: $db_name" >&2; exit 2; }
if [ "$db_name" = "cedesk" ] && [ "$force" != 1 ]; then
    echo "Refusing to restore over the live database without --force (stop api and worker first)." >&2
    exit 2
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

echo "1/3 checking stored files"
grep '^stored ' "$manifest" | sed 's/^stored //' > "$work/stored.sha256"
(cd "$src" && sha256sum --quiet -c "$work/stored.sha256")

echo "2/3 decrypting and checking"
grep '^plain ' "$manifest" | sed 's/^plain //' > "$work/plain.sha256"
while read -r _ stored; do
    plain="${stored%.gpg}"
    if [ "$stored" != "$plain" ]; then
        gpg --batch --quiet --output "$work/$plain" --decrypt "$src/$stored"
    else
        cp "$src/$stored" "$work/$plain"
    fi
    gzip -t "$work/$plain"
done < "$work/stored.sha256"
(cd "$work" && sha256sum --quiet -c plain.sha256)

echo "3/3 restoring"
db_file="$(find "$work" -maxdepth 1 -name '*-db.sql.gz' | head -1)"
if [ -n "$db_file" ]; then
    if [ "$db_name" != "cedesk" ]; then
        "${compose[@]}" exec -T db psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
            -c "CREATE DATABASE $db_name"
    fi
    gunzip -c "$db_file" | "${compose[@]}" exec -T db psql -U postgres -d "$db_name" \
        -v ON_ERROR_STOP=1 -q
    echo "database restored into: $db_name"
fi
files_file="$(find "$work" -maxdepth 1 -name '*-files.tar.gz' | head -1)"
if [ -n "$files_file" ] && [ -n "$files_to" ]; then
    mkdir -p "$files_to"
    tar -C "$files_to" -xzf "$files_file"
    echo "files unpacked into: $files_to"
fi
echo "restore finished; check the copy before pointing anything at it"
