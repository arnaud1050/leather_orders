# Deployment

> Part of the `leather_orders` app — see the root [CLAUDE.md](../CLAUDE.md) for orientation.

Runs in Docker via gunicorn (2 workers), not Flask's dev server. Two parallel
deployments exist, sharing the same `entrypoint.sh` and app code but built/composed
separately — **local/default** (`Dockerfile` + `docker-compose.yml`, port 5000) and
**demo** (`Dockerfile.demo` + `docker-compose-demo.yml`, port 5555, container name
`demo`, meant to sit behind nginx as `demo.arnaudrouillot.com` so a client can view
work in progress). If you change one (base image version, gunicorn flags, etc.),
check whether the other needs the same change — they're kept in sync by hand, not
via a shared base file.

- **`entrypoint.sh`**: the image's `appuser` is non-root, but Docker creates a bind
  mount's host directory (`./data` or `./data-demo`) owned by `root` if it doesn't
  already exist, which `appuser` then can't write into — this bit us on first demo
  deploy (`sqlite3.OperationalError: unable to open database file`). The entrypoint
  runs as root, `chown -R appuser:appuser /app/data`, then `exec su-exec appuser
  "$@"` to actually start gunicorn as the unprivileged user. Both Dockerfiles set
  `ENTRYPOINT ["/app/entrypoint.sh"]` and install `su-exec` via `apk add`.
- **`--preload`**: gunicorn's `CMD` includes `--preload` in both Dockerfiles. Without
  it, each of the 2 workers re-imports `app.py` independently on boot, and the
  startup `db.create_all()` + `seed_if_empty()` in `app.py` raced across workers —
  both tried to insert the same seed rows, causing
  `sqlite3.IntegrityError: UNIQUE constraint failed`. `--preload` loads the app once
  in the master process before forking workers, so seeding runs exactly once.
- **`Dockerfile`** / **`Dockerfile.demo`**: alpine-based, install `requirements.txt` +
  gunicorn + `su-exec`, copy the repo to `/app`, `chmod +x /app/entrypoint.sh`. Only
  real difference between the two: bound port (5000 vs 5555, in both `EXPOSE` and the
  gunicorn `--bind` in `CMD`). If you rename the Flask instance variable in `app.py`
  (currently `app`), update the `CMD` line in both to match.
- **`docker-compose.yml`**: service/container `atelier-orders`, port `5000:5000`,
  bind-mounts `./data` → `/app/data`.
- **`docker-compose-demo.yml`**: service/container `demo`, port `5555:5555`,
  bind-mounts `./data-demo` → `/app/data` (a separate SQLite file from the local
  deployment — the two are never meant to share data), and attaches to the external
  `website_network` docker network (must already exist on the server — created by
  whatever set up nginx and the other sites on it) so nginx can reach it as
  `demo:5555` without a host port needing to be involved. Deploy with:
  `docker compose -f docker-compose-demo.yml up --build -d`.
- **`TRUST_PROXY_HEADERS=1`** (demo only) wraps the app in Werkzeug's `ProxyFix`.
  Behind nginx the request reaches gunicorn as plain http, so `request.url` reads
  `http://` — and oauthlib **refuses to parse an http authorization response at
  all**, so the Google OAuth callback fails there and only there. It's opt-in
  rather than always-on because `X-Forwarded-Proto`/`-Host` are only trustworthy
  from a proxy we control; the local deployment publishes a host port directly,
  where a client could set them itself, so it defaults to `0` there. nginx must
  actually send them (`proxy_set_header X-Forwarded-Proto $scheme;` and `Host`).
- Both compose files pass through **`SECRET_KEY`** from the host environment
  (`${SECRET_KEY:-dev-not-secure}`) — **set a real value in a local `.env` file
  before deploying anywhere reachable**; the fallback is dev-only and insecure.
  `docker-compose-demo.yml` also passes through the two bootstrap logins, and
  they are for **two different kinds of account** (see
  [admin/CLAUDE.md](../admin/CLAUDE.md)):
  **`ADMIN_EMAIL`** / **`ADMIN_PASSWORD`** (default
  `admin@example.invalid` / `changeme`) is the *studio's* own user, created by
  `seed_if_empty()` and only on a genuinely empty database.
  **`PLATFORM_ADMIN_EMAIL`** / **`PLATFORM_ADMIN_PASSWORD`** (default
  `platform@example.invalid` / `changeme`) is the *platform admin* — belongs to
  no company, sees only `/admin` — created by `ensure_platform_admin()`, which
  fires whenever no platform staff exist yet, including on an already-populated
  database being upgraded. Neither ever overwrites an account that's already
  there; changing a password afterwards is a job for `/admin`, not the
  environment. Deleting `data-demo/atelier.db` and restarting remains the blunt
  instrument. `.env` is already gitignored.
- **Two separate encryption keys**, both passed through by both compose files and
  both optional: **`COMMS_ENCRYPTION_KEY`** (OAuth tokens, `communications/`) and
  **`AI_ENCRYPTION_KEY`** (vendor API keys, `ai/`). Generate either with
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
  Left unset, each is derived from `SECRET_KEY` — which works, and means
  **rotating `SECRET_KEY` makes those stored secrets permanently unreadable**
  (accounts have to be reconnected, API keys re-entered). Both pages say so when
  running on the derived key. They're deliberately separate variables so either
  can be rotated without invalidating the other's data; the shared mechanics live
  in the root `crypto.py`.
- **A fresh deployment starts empty** — no sample clients or orders, and a blank
  invoice letterhead to fill in from /settings. The demo dataset is loaded by hand
  and only where it's wanted (the demo deployment, or a local test), by running
  `scripts/seed_sample_data.py` inside the container:

  ```bash
  docker compose -f docker-compose-demo.yml exec demo python scripts/seed_sample_data.py
  ```
- **Schema changes apply themselves on boot.** `db.create_all()` and every
  module's `run_migrations()` run at import (the `with app.app_context()` block in
  `app.py`), so redeploying a release that added a column or a table migrates that
  deployment's database as it starts — there's no separate migrate step to
  remember, and no release that's silently half-applied. Every migration in this
  codebase is a no-op once applied, so restarting repeatedly is safe.

  `scripts/migrate.py` does the same thing **deliberately**, and prints the diff —
  for applying a schema change to a running deployment ahead of swapping the
  image, or just to see in writing what a release changed. It takes a timestamped
  `.bak` copy of the SQLite file first (`--no-backup` skips it):

  ```bash
  docker compose -f docker-compose-demo.yml exec demo python scripts/migrate.py
  ```

  Both it and `seed_sample_data.py` read **`DATABASE_URL` from the environment,
  before importing `app`** — set any later and it silently acts on the real
  `data/atelier.db` instead (hard rule 13).
- **`.dockerignore`**: excludes `venv/`, `.git`, `data/`, `CLAUDE.md`, etc. from the
  build context (shared by both Dockerfiles' build contexts). Update this if you add
  other local-only folders (e.g. `.vscode/`).

No reverse proxy or TLS is configured for the local/default deployment — put it
behind something like nginx/Caddy/Cloudflare Tunnel if it needs to be reachable
outside the LAN. (The demo deployment already assumes nginx is handling that via
`website_network`.)

To rebuild after changing `requirements.txt` or app code:
`docker compose up --build` (local) or
`docker compose -f docker-compose-demo.yml up --build -d` (demo). Compose caches the
pip-install layer, so rebuilds are fast unless `requirements.txt` changed.

