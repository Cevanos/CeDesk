# Running CeDesk on a server

One server with Docker. Caddy answers on 80/443 and passes requests to the API; the
database and the API are not reachable from outside.

## First time

1. Server: Docker with the compose plugin, and a firewall that allows only SSH, 80 and 443:

   ```sh
   ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw enable
   ```

   (Docker opens published ports itself, past ufw. Only Caddy publishes any here.)
2. Get the code: `git clone git@github.com:Cogninest-AI/CDESK.git && cd CDESK/deploy`
3. `cp env.example .env`, then fill in `.env`: four secrets from `openssl rand -hex 32`,
   `CEDESK_SITE` (a domain pointing at the server for HTTPS, or `:80`) and
   `CEDESK_CORS_ORIGINS` (the frontend's addresses).
4. Start: `docker compose up -d --build`. It creates the database, runs the migrations,
   then starts the API and Caddy. Check: `curl http://<server>/readyz` gives `{"status":"ok",...}`.
5. Demo workspace (Ritesh as Admin, the agents and tickets from the mock):

   ```sh
   docker compose exec api uv run --no-sync cedesk seed-demo
   ```

   It asks for one password for all demo accounts and prints the sign-in emails
   (workspace `acme-store`).
6. Backups: add `deploy/backup.sh` to cron (the line is at the top of the script).

## Updating

```sh
cd CDESK && git pull && cd deploy && docker compose up -d --build
```

Migrations run before the new API starts. Uploaded files (`files` volume), the database
(`pgdata`) and the HTTPS certificates (`caddy_data`) survive updates.

When an update brings new defaults (saved replies, rules and macros), add them to a
workspace made before it. Safe to run again: anything already there is kept, a default an
Admin deleted never comes back, and a default rule or macro whose team or ticket type is
missing is left out until it exists:

```sh
docker compose exec api uv run --no-sync cedesk seed-workflow --workspace acme-store
```

## What the API refuses

With `CEDESK_ENV` other than `dev`, the API does not start with the dev secret key or the
dev database password, and `cedesk migrate` refuses the dev owner password.

## For the frontend team

- API docs to try every route: `https://<server>/docs`; the file: `docs/api/openapi.json`;
  changes: `docs/api/CHANGELOG.md`.
- Sign-in and password change allow 20 attempts per 5 minutes per address, then 429 with
  `Retry-After`.
