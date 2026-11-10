#!/bin/sh
# Runs once, after 01-roles.sql, on an empty data directory. On a server, the role
# passwords come from the environment (CEDESK_OWNER_PASSWORD, CEDESK_APP_PASSWORD) instead
# of the dev ones in 01-roles.sql; the dev test database is dropped. Locally, with the
# variables unset, nothing changes.
set -eu
if [ -n "${CEDESK_OWNER_PASSWORD:-}" ] && [ -n "${CEDESK_APP_PASSWORD:-}" ]; then
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    -v owner_pw="$CEDESK_OWNER_PASSWORD" -v app_pw="$CEDESK_APP_PASSWORD" <<'SQL'
ALTER ROLE cedesk_owner PASSWORD :'owner_pw';
ALTER ROLE cedesk_app PASSWORD :'app_pw';
DROP DATABASE IF EXISTS cedesk_test;
SQL
  echo "CeDesk role passwords set from the environment."
fi
