-- Runs once, as the container superuser, on an empty data directory.
-- cedesk_owner: owns the schema and runs migrations.
-- cedesk_app:   what the API connects as. Owns nothing, cannot bypass row-level security.
-- Passwords here are for local development only.

CREATE ROLE cedesk_owner LOGIN PASSWORD 'cedesk_owner_dev' NOSUPERUSER NOCREATEROLE NOBYPASSRLS;
CREATE ROLE cedesk_app   LOGIN PASSWORD 'cedesk_app_dev'   NOSUPERUSER NOCREATEROLE NOBYPASSRLS;
-- cedesk_reporting: the group of BI logins (`cedesk reporting-user`, migration 0080). Made
-- here so a database backup that grants to it restores on a new server.
CREATE ROLE cedesk_reporting NOLOGIN;

ALTER DATABASE cedesk OWNER TO cedesk_owner;
CREATE DATABASE cedesk_test OWNER cedesk_owner;

\connect cedesk
ALTER SCHEMA public OWNER TO cedesk_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO cedesk_app;
ALTER DEFAULT PRIVILEGES FOR ROLE cedesk_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO cedesk_app;
ALTER DEFAULT PRIVILEGES FOR ROLE cedesk_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO cedesk_app;

\connect cedesk_test
ALTER SCHEMA public OWNER TO cedesk_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO cedesk_app;
ALTER DEFAULT PRIVILEGES FOR ROLE cedesk_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO cedesk_app;
ALTER DEFAULT PRIVILEGES FOR ROLE cedesk_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO cedesk_app;
