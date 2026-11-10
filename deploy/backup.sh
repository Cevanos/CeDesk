#!/bin/sh
# Daily database backup, kept 14 days. Cron (as the deploy user):
#   30 2 * * * /path/to/CDESK/deploy/backup.sh >> /var/log/cedesk-backup.log 2>&1
set -eu
cd "$(dirname "$0")"
dir="${BACKUP_DIR:-$HOME/cedesk-backups}"
mkdir -p "$dir"
file="$dir/cedesk-$(date +%Y%m%d-%H%M).sql.gz"
docker compose exec -T db pg_dump -U postgres -d cedesk | gzip > "$file"
find "$dir" -name 'cedesk-*.sql.gz' -mtime +14 -delete
echo "Backed up to $file"
