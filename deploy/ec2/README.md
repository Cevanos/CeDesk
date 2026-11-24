# deploy/ec2: CeDesk on a shared EC2 box, released with no downtime

This is how our own box runs CeDesk. It is not the self-hosting stack
([`deploy/docker-compose.yml`](../docker-compose.yml): its own Postgres and Caddy, one
`docker compose up`); that is untouched and is what [`docs/SELF_HOSTING.md`](../../docs/SELF_HOSTING.md)
describes. Here Postgres is remote, nginx on the host does TLS for every site on the box,
and a merge to `main` releases by itself.

| File | What |
|---|---|
| `docker-compose.prod.yml` | The API and the web app as blue/green slots, the worker, ClamAV, the one-off `migrate` |
| `deploy.sh` | `migrate`, `release`, `workers`, `rollback`, `status` |
| `env_ssm.py` | Settings to and from SSM Parameter Store, one SecureString per variable |
| `create-database.sh` | The `cedesk` database and its two logins on an existing Postgres |
| `env.example`, `env.owner.example` | The names to load into Parameter Store (no secrets) |
| `nginx/cedesk-site.conf.example` | The site's server block |
| [`../../.github/workflows/cd-ec2.yml`](../../.github/workflows/cd-ec2.yml) | Merge to `main` → release |

## How a release goes

`cd-ec2` runs on the self-hosted runner on the box:

1. Resets `/home/ubuntu/CeDesk` to the pushed commit and writes `.env` and `.env.migrate` from Parameter Store.
2. Builds `cedesk-api:<sha>` and `cedesk-web:<sha>` on the box. Running containers are not touched.
3. `deploy.sh migrate`: the one-off `migrate` container, as the owner login. The live slot keeps serving, so **a migration must keep the previous release working**: add now, remove in a later release.
4. `deploy.sh release`: starts the idle slot (API, then the web app that calls *that* API), waits for `/readyz` and `/api/health`, checks the web app reaches its API, copies the build's `/_next/static` files to `/var/www/cedesk`, rewrites both upstreams in one nginx reload, smoke-tests `/readyz`, `/api/health` and `/login` through the real server block, then stops the old slot once every nginx worker from before the reload has exited. A slot that fails any check is stopped and nginx is never left on it.
5. `deploy.sh workers`: recreates the worker in place and fails the job if it is not running or has restarted. What it was doing waits in the database.
6. Keeps the last three images of each.

| Slot | API | Web app |
|---|---|---|
| blue | 8801 | 8802 |
| green | 8811 | 8812 |

The ports never change; `.deploy/active` says which slot is live. Every port is published on `127.0.0.1` only, so nginx is the single way in.

**A red run is not an outage.** The job stops at the failing step and the containers already running keep serving the previous version.

```bash
cd ~/CeDesk
bash deploy/ec2/deploy.sh status
bash deploy/ec2/deploy.sh rollback        # seconds; the API and the web app only
```

To put the worker back too, run **cd-ec2** with *image_tag* set to the previous SHA (`.deploy/previous_image_tag`). Re-run the current commit with no change: Actions → cd-ec2 → *Run workflow*.

### Why the shared static directory

A browser that loaded a page before a release asks for that build's JavaScript after it. nginx serves `/_next/static` from one directory every release adds to, so those requests still succeed. Names are content hashes, so builds never overwrite each other; files no longer listed by the last five builds are deleted.

### Why the migration login is separate

The API container must never hold the owner password (it owns the schema). So the settings come in two Parameter Store prefixes: `/cevanos/prod/cedesk/` (what the containers read) and `/cevanos/prod/cedesk-owner/` (`CEDESK_MIGRATION_DATABASE_URL` alone, written to `.env.migrate`, read only by `migrate`). The compose file also hands each container only the variables listed for it: the web app gets no secret at all. `deploy-check.yml` fails a pull request that breaks either rule. **A setting that is not listed in the compose file does nothing**; add it there and in Parameter Store.

## One-time setup

Nothing here changes what the box is serving now. Do it in this order, once.

**1. DNS.** An A record for the site (e.g. `desk.example.com`) to the box's address.

**2. The database**, on the Postgres the box already runs (or RDS). From a checkout, as a user who can `sudo -u postgres`:

```bash
export CEDESK_OWNER_PASSWORD=$(openssl rand -hex 32)
export CEDESK_APP_PASSWORD=$(openssl rand -hex 32)
bash deploy/ec2/create-database.sh
```

Keep both passwords: they go into the two connection URLs in step 3 and are printed nowhere. Then let the containers in: `pg_hba.conf` needs the Docker networks (`172.16.0.0/12`) for database `cedesk` and users `cedesk_app` and `cedesk_owner`, and a reload. Containers reach the box at `172.17.0.1`. The same script works on RDS with `CEDESK_PSQL="psql -h <endpoint> -U <admin> -d postgres"`.

**3. Parameter Store**, from your own machine with admin AWS credentials (the filled-in files never reach the server):

```bash
cp deploy/ec2/env.example prod.env && cp deploy/ec2/env.owner.example owner.env   # fill both in
uv run python deploy/ec2/env_ssm.py push --prefix /cevanos/prod/cedesk/ --env-file prod.env
uv run python deploy/ec2/env_ssm.py push --prefix /cevanos/prod/cedesk-owner/ --env-file owner.env
uv run python deploy/ec2/env_ssm.py check --prefix /cevanos/prod/cedesk/ --env-file prod.env
```

`check` prints names, never values. The box's instance role already reads everything under `/cevanos/prod/`, so these two prefixes need no IAM change. Delete `prod.env` and `owner.env` afterwards.

**4. The box.** As `ubuntu`:

```bash
mkdir -p /home/ubuntu/CeDesk
sudo mkdir -p /var/www/cedesk/_next/static && sudo chown -R ubuntu:ubuntu /var/www/cedesk
sudo mkdir -p /etc/nginx/cevanos && sudo chown ubuntu:ubuntu /etc/nginx/cevanos     # exists if the Cevanos backend is deployed here
grep -rn 'cevanos/\*.conf' /etc/nginx/ || echo 'include /etc/nginx/cevanos/*.conf;' | sudo tee /etc/nginx/conf.d/cevanos-upstreams.conf
echo 'upstream cedesk_api { server 127.0.0.1:8801; }
upstream cedesk_web { server 127.0.0.1:8802; }' > /etc/nginx/cevanos/cedesk-upstreams.conf
```

The runner is already registered to this repository; it needs to run as `ubuntu`, in the `docker` group, with `sudo -n nginx` (`-t`, `-T`, `-s reload`) allowed, which the default `ubuntu` user has. Check free memory first: `free -h` (a release needs 1.5 GB for the new slot; ClamAV another 1.5–3 GB).

**5. The site, HTTPS.** Copy `nginx/cedesk-site.conf.example` to `/etc/nginx/sites-available/cedesk`, put the real name in `server_name`, link it into `sites-enabled`, and:

```bash
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d desk.example.com        # adds the certificate and the HTTPS block
sudo nginx -t && sudo systemctl reload nginx
```

Until the first release the upstreams point at ports nothing listens on, so the site answers 502. That is expected.

**6. Repository variables** (Settings → Secrets and variables → Actions → Variables):

| Variable | Value |
|---|---|
| `CEDESK_PROD_SSM_PREFIX` | `/cevanos/prod/cedesk/` |
| `CEDESK_PROD_OWNER_SSM_PREFIX` | `/cevanos/prod/cedesk-owner/` |
| `CEDESK_PROD_HOST` | the site's name, e.g. `desk.example.com` |

**7. First release.** Actions → cd-ec2 → *Run workflow*. The first release goes to blue, creates the `cedesk` tables, and starts the worker and ClamAV (its first start downloads the virus definitions for a few minutes; files wait as "being checked" until then).

**8. First workspace.** `docker compose -p cedesk-prod --env-file .env -f deploy/ec2/docker-compose.prod.yml exec api-blue uv run --no-sync cedesk create-workspace --help` (use the live colour from `deploy.sh status`).

## The runner, public repository

The runner serves this repository only, and the workflow runs only on a push to `main` or by hand, never on a pull request. Keep it that way: a self-hosted runner that runs a fork's pull request runs a stranger's code on the box. Settings → Actions → General → *Fork pull request workflows from outside collaborators* should stay on *Require approval for all outside collaborators*. The repository's other workflows run on GitHub's own runners. The workflow also skips itself on forks.

```bash
systemctl list-units 'actions.runner.*'
sudo systemctl status actions.runner.<name>.service
journalctl -u actions.runner.<name>.service -n 100
```

## Day to day

From `~/CeDesk`, with the live colour and tag:

```bash
export IMAGE_TAG=$(cat .deploy/image_tag) LIVE=$(cat .deploy/active)
alias cdc='docker compose -p cedesk-prod --env-file .env -f deploy/ec2/docker-compose.prod.yml'
cdc ps
cdc logs -f -t --tail=100 api-$LIVE web-$LIVE worker
```

**Never restart or recreate the API or web slot by hand to ship code**: that is downtime. Release through the workflow.

**Changing a setting**: edit the parameter, then run the workflow (*Run workflow* re-releases the running commit with no downtime). `restart` does not re-read anything.

| Symptom | Look at |
|---|---|
| Run red at **migrate** | Its log: usually the database is unreachable from the container (`pg_hba.conf`, `172.17.0.1`) or the owner login is wrong |
| Run red at **release** | The slot's last 100 lines are in the log; nginx was not touched |
| Run red at **worker** | `cdc logs --tail=100 worker`: a worker that refuses to start prints `Refusing to start` and why |
| 502 from nginx | `ss -lntp \| grep -E ':(88[0-9]{2})\b'` (must read `127.0.0.1`) and the slot's logs |
| Pages load but are unstyled after a release | `/var/www/cedesk/_next/static` is missing or not readable by nginx |
| Sign-in redirects wrong | `CEDESK_APP_URL`, `CEDESK_API_URL` (SSO) and the site's name must agree |
| Everyone shares one rate limit | A CDN in front: its range is the address nginx sees; set it as the real-IP source in the server block |
| Files stay "being checked" | `cdc ps clamav` is not `healthy` yet, or set `CEDESK_SCAN_BACKEND=none` |
