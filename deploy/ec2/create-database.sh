#!/usr/bin/env bash
# Creates CeDesk's database and its two logins on a Postgres you already run (the box's
# own, or RDS). Safe to run again: what exists is kept and the passwords are set again.
#
#   export CEDESK_OWNER_PASSWORD=$(openssl rand -hex 32)
#   export CEDESK_APP_PASSWORD=$(openssl rand -hex 32)
#   deploy/ec2/create-database.sh
#
# Hex passwords, so they are safe inside the connection URLs that go to Parameter Store.
# Keep them: this script prints neither. Needs psql 15 or newer (\getenv), which keeps the
# passwords out of the process list.
#
# cedesk_owner owns the schema and runs the migrations. cedesk_app is what the API and
# the worker connect as: it owns nothing and cannot bypass row-level security. The same
# roles, grants and default privileges as infra/postgres/init/01-roles.sql, without its
# development passwords and its test database.
#
# CEDESK_PSQL is the psql command. The default is the local server, as its superuser;
# for RDS: CEDESK_PSQL="psql -h <endpoint> -U <admin> -d postgres".
set -euo pipefail

: "${CEDESK_OWNER_PASSWORD:?export CEDESK_OWNER_PASSWORD first}"
: "${CEDESK_APP_PASSWORD:?export CEDESK_APP_PASSWORD first}"
PSQL=${CEDESK_PSQL:-sudo -u postgres --preserve-env=CEDESK_OWNER_PASSWORD,CEDESK_APP_PASSWORD psql -d postgres}

# shellcheck disable=SC2086 # CEDESK_PSQL is a command line
$PSQL -v ON_ERROR_STOP=1 -X <<'SQL'
\getenv owner_pw CEDESK_OWNER_PASSWORD
\getenv app_pw CEDESK_APP_PASSWORD

SELECT 'CREATE ROLE cedesk_owner LOGIN NOSUPERUSER NOCREATEROLE NOBYPASSRLS'
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cedesk_owner') \gexec
SELECT 'CREATE ROLE cedesk_app LOGIN NOSUPERUSER NOCREATEROLE NOBYPASSRLS'
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cedesk_app') \gexec
ALTER ROLE cedesk_owner PASSWORD :'owner_pw';
ALTER ROLE cedesk_app PASSWORD :'app_pw';

SELECT 'CREATE DATABASE cedesk OWNER cedesk_owner'
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'cedesk') \gexec

-- Only these two logins connect to it; no other role on a shared server can.
REVOKE ALL ON DATABASE cedesk FROM PUBLIC;
GRANT CONNECT ON DATABASE cedesk TO cedesk_app;

\connect cedesk
ALTER SCHEMA public OWNER TO cedesk_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO cedesk_app;
ALTER DEFAULT PRIVILEGES FOR ROLE cedesk_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO cedesk_app;
ALTER DEFAULT PRIVILEGES FOR ROLE cedesk_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO cedesk_app;
SQL

echo "cedesk database and logins are in place."
echo "Next: let the containers reach it (pg_hba.conf: the Docker networks, database cedesk,"
echo "roles cedesk_app and cedesk_owner), then load the settings into Parameter Store."
