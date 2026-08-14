# OOHStory Operations Admin

Version 0.7.0 is a real, authenticated FastAPI operations backend for OOHStory. It reads the local reader API on `127.0.0.1:8091`, reads electronic-library state directly from the durable production MySQL catalog and the shared NAS path, and can accelerate bounded read models through an optional disposable Redis cache on `127.0.0.1:6380`. It inspects bounded host/systemd state and allows only explicit actions through compiled allowlists. It has no runtime dependency on the webnovel-writer HTTP API. Shared-library writes are performed only by a root-owned structured-action helper; the unprivileged web process keeps a read-only catalog account. The complete admin uses a responsive sky-blue design system, with its electronic-library feature/UI mapping documented in `docs/library-ui-parity.md`. Local TXT80 plus four authorized body sources expose independent full-rate switches capped at 100 books per minute per selected site. Its catalog views use the same dense cover shelf, chapter/readability facts, responsive layout and grouped pagination model as Webnovel Writer, and the book-total view supports bounded, explicitly confirmed cascading deletion through the canonical recoverable library workflow.

The repository now owns the electronic-library service dependency closure,
workers, operational scripts, MySQL migrations, and inactive OOHStory systemd
templates. See `docs/electronic-library-migration.md` for the exact migration
manifest and the deliberately deferred writing-prompt assets.

## Security model

- No built-in username, password, or session key. Login stays disabled and `/healthz` returns `503 degraded` until all three required values are configured.
- Passwords use stdlib `hashlib.scrypt` with a random 16-byte salt (`N=32768`, `r=8`, `p=1`). Only the encoded hash is stored.
- Sessions are HMAC-SHA256 signed, expire after a bounded TTL, and use `HttpOnly; SameSite=Strict`. The loopback-only HTTP deployment leaves `Secure` off; enable it only if a future local TLS terminator is added.
- Every mutation, including login and logout, has CSRF verification. Login attempts are limited by client address and hashed username key.
- A strict CSP, frame denial, no-sniff, no-referrer, permissions policy, trusted-host checks, `no-store, no-transform`, 64 KiB mutation limit, bounded Reader responses/timeouts, and loopback-only Reader URL validation are enabled.
- Electronic-library catalog reads are MySQL-only and transaction read-only. The OOHStory status layer never opens legacy catalog SQLite files and never writes the shared NAS tree.
- Library storage has three explicit tiers: MySQL is the durable catalog/task truth, NAS is the body/image/deconstruction truth, and the optional Redis 6380 instance stores only bounded disposable JSON read models. The operational Redis 6379/DB6 remains reserved for Streams, locks, leases, and status and is never configured for eviction.
- Unit actions accept no arbitrary names or shell fragments. Python calls argv arrays with `shell=False`; the optional root-owned helper repeats the exact allowlist check. Every accepted, rejected, successful, or failed pipeline action is written to a mode-0600 SQLite audit DB. Secrets are never written to it.
- The pipeline script editor accepts a fixed script ID rather than a path. It rejects symlinks, non-UTF-8/NUL/oversize content, stale SHA-256 values, and invalid Python syntax. A root-owned helper creates a protected backup and atomically replaces only the exact OOHStory-managed target; it never restarts a unit automatically.
- The library action helper accepts JSON on standard input and no user-selected command, SQL, path, or environment value. It permits only fixed source IDs, numeric catalog IDs, bounded task/plan IDs, fixed runner/profile IDs returned by the task-runner catalog, the two fixed sync controls, the two fixed index kinds, and a protected cover-upload token. Imports and cover batches run in isolated transient systemd units; every request is audited without source text or credentials. Enabling an OOHStory sync pipeline fails closed while its legacy `webnovel-library-*` timer or service remains enabled/running, preventing two writers from touching the shared library.

The administration service is intentionally local-only. It binds `127.0.0.1:8092`, accepts only `127.0.0.1` and `localhost` hosts, and must not be added to FRP, public Nginx, Cloudflare, or a public DNS name. Use it directly on the host or through an explicitly established SSH local-forward when needed.

## Secure setup

```bash
cd /opt/oohstory-admin
python3 -m venv .venv
.venv/bin/pip install --requirement requirements.txt
.venv/bin/pip install .
.venv/bin/oohstory-admin-password
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Put the generated values in `/etc/oohstory-admin/admin.env`, owned by `root:root` with mode `0600`. Do not pass a clear-text password on a command line or commit the environment file.

Create the stable data-path link without copying any library payload:

```bash
ln -s /srv/oohstory/library /opt/oohstory-admin/electronic-library
```

For a completely new MySQL 8 installation, initialize the empty schema from
the repository root:

```bash
sudo mysql < deploy/mysql/init.sql
umask 077
sudo mysql --batch --raw < deploy/mysql/runtime-users.sql \
  > /var/lib/oohstory-admin/generated-mysql-passwords.txt
```

`init.sql` creates the database, all 21 current schema revisions, migration
checksums, triggers, and least-privilege roles. It rejects a non-empty schema
and contains neither accounts nor passwords. `runtime-users.sql` creates only
two `127.0.0.1` accounts with MySQL-generated random passwords. Immediately
install those two values in the writer and reader password files described
below, then securely remove the root-only capture file. There are no default
credentials or seed production records in Git.

Run `python3 scripts/electronic-library/render_mysql_init_sql.py --check` before
release. When a new numbered migration is added, regenerate `init.sql` with the
same command without `--check`; the numbered migrations remain the schema
source of truth. Existing databases are upgraded with
`apply_mysql_migrations.py --admin-socket`, not with the fresh initializer.

The fresh-install account script grants the admin status account `SELECT` only
on the `oohstory_library` schema. Store its generated password in
`/etc/oohstory-admin/mysql-password` (root-owned mode `0640`, readable by the
service group). The parent `/etc/oohstory-admin` directory must be
`0710 root:oohstory-admin` so the service can traverse to that one file while
remaining unable to list the directory. Never copy the webnovel-writer
environment file.

After every source update, reinstall the project package (or use an explicitly
managed editable install) and verify both `oohstory_admin.__file__` and
`oohstory_admin.__version__`; copying source files alone does not replace a
previous wheel installed in the virtual environment.

Required environment allowlist:

- `OOHSTORY_ADMIN_USERNAME`
- `OOHSTORY_ADMIN_PASSWORD_HASH`
- `OOHSTORY_ADMIN_SESSION_SECRET` (at least 32 UTF-8 bytes)

Supported optional environment allowlist:

- `OOH_ADMIN_BASE_PATH` (default `/admin`; set empty only for isolated local tests)
- `OOHSTORY_ADMIN_COOKIE_SECURE`, `OOHSTORY_ADMIN_COOKIE_PATH`, `OOHSTORY_ADMIN_SESSION_COOKIE`
- `OOHSTORY_ADMIN_ALLOWED_HOSTS`
- `OOHSTORY_ADMIN_SESSION_TTL`, `OOHSTORY_ADMIN_LOGIN_ATTEMPTS`, `OOHSTORY_ADMIN_LOGIN_WINDOW`
- `OOHSTORY_ADMIN_READER_URL` (validated loopback HTTP only)
- `OOHSTORY_ADMIN_UPSTREAM_TIMEOUT`, `OOHSTORY_ADMIN_DATABASE`
- `OOHSTORY_LIBRARY_ROOT`, `OOHSTORY_LIBRARY_RUNTIME_DIR`, `OOHSTORY_LIBRARY_OBJECT_ROOT`
- `OOHSTORY_LIBRARY_MYSQL_HOST`, `OOHSTORY_LIBRARY_MYSQL_PORT`, `OOHSTORY_LIBRARY_MYSQL_DATABASE`, `OOHSTORY_LIBRARY_MYSQL_USER`, `OOHSTORY_LIBRARY_MYSQL_PASSWORD_FILE`
- `OOHSTORY_LIBRARY_REDIS_HOST`, `OOHSTORY_LIBRARY_REDIS_PORT`, `OOHSTORY_LIBRARY_REDIS_DB`, `OOHSTORY_LIBRARY_REDIS_PASSWORD_FILE`, `OOHSTORY_LIBRARY_REDIS_PREFIX`
- `OOHSTORY_LIBRARY_CACHE_REDIS_ENABLED` (default `0`), `OOHSTORY_LIBRARY_CACHE_REDIS_HOST`, `OOHSTORY_LIBRARY_CACHE_REDIS_PORT`, `OOHSTORY_LIBRARY_CACHE_REDIS_DB`, `OOHSTORY_LIBRARY_CACHE_REDIS_PASSWORD_FILE`, `OOHSTORY_LIBRARY_CACHE_REDIS_PREFIX`, `OOHSTORY_LIBRARY_CACHE_REDIS_CONNECT_TIMEOUT`, `OOHSTORY_LIBRARY_CACHE_REDIS_SOCKET_TIMEOUT`, `OOHSTORY_LIBRARY_CACHE_REDIS_MAX_PAYLOAD_BYTES`
- `OOHSTORY_ADMIN_SYSTEMCTL_PATH`, `OOHSTORY_ADMIN_USE_SUDO_HELPER`, `OOHSTORY_ADMIN_SYSTEMCTL_HELPER_PATH`
- `OOHSTORY_ADMIN_LIBRARY_ACTION_HELPER_PATH`, `OOHSTORY_ADMIN_LIBRARY_UPLOAD_DIR`
- `OOHSTORY_ADMIN_MAINTENANCE_HELPER_PATH` for the atomic public maintenance switch

The cookie path defaults to `/` deliberately: the authenticated UI lives at `/admin/` while its JSON API lives at `/api/admin/`.

## Optional disposable Redis cache

Fresh installs keep the hot cache disabled. To enable it, install
`deploy/redis/oohstory-library-cache.conf` as
`/etc/redis/oohstory-library-cache.conf`, install and start
`deploy/systemd/oohstory-library-cache.service`, verify it listens only on
loopback port 6380 with `maxmemory-policy=allkeys-lfu`, and then set
`OOHSTORY_LIBRARY_CACHE_REDIS_ENABLED=1` for the admin and writer processes.
Do not point these variables at Redis 6379/DB6.

The cache contains compact counts, catalog pages/cards, book and cover
metadata (including negative cover lookups), tone facets/pages, bounded plot
query pages, and deconstruction manifests/summaries. It rejects oversized or
sensitive payloads and never stores cover/image bytes, book bodies/chapters,
or full deconstruction files. Durable writes commit to MySQL or atomically
replace NAS data first, then advance a scope generation. Warming is bounded
and best-effort. Cache timeout, malformed JSON, eviction, restart, or total
loss transparently falls back to MySQL/NAS.

Because persistence and the `FLUSH*` commands are disabled, the safest full
flush is a controlled restart of only `oohstory-library-cache.service`; do not
restart the operational Redis. No rebuild command is required: generations
and values repopulate on demand, with changed hot items warmed after writes.
Cache health is exposed in the existing infrastructure payload without a
password: endpoint, ping, memory/policy, hit/miss/error counters, hit rate,
scope generations, and warm queue depth.

## Local run

```bash
export OOHSTORY_ADMIN_USERNAME='operator'
export OOHSTORY_ADMIN_PASSWORD_HASH='scrypt$...'
export OOHSTORY_ADMIN_SESSION_SECRET='a-random-value-of-at-least-32-bytes'
export OOHSTORY_ADMIN_COOKIE_SECURE=0
.venv/bin/uvicorn oohstory_admin.app:create_app --factory \
  --host 127.0.0.1 --port 8092 --proxy-headers --forwarded-allow-ips=127.0.0.1
```

Open `http://127.0.0.1:8092/admin/`. `/` redirects to the configured UI base. JSON endpoints are under `/api/admin/`; obtain the per-session CSRF token from authenticated `GET /api/admin/session` and send it as `X-CSRF-Token` on mutations.

## Service control privilege

For a dedicated `oohstory-admin` user, install `ops/oohstory-admin-systemctl`, `ops/oohstory-admin-library-action`, and `ops/oohstory-admin-maintenance` as root-owned mode `0755` under `/usr/local/libexec/`. Keep `ops/oohstory-admin-library-action-runner` root-owned mode `0755` under `/opt/oohstory-admin/ops/`. Install `ops/oohstory-admin.sudoers` as root-owned mode `0440` only after validating it with `visudo -cf`. The service example enables helper mode. The web application deliberately has no script viewer, editor, or publisher. Do not make any helper, action runner, or library environment file writable by the service user.

The mutation helper reads the writer connection only from `/etc/oohstory-admin/library.env`, owned by `root:root` mode `0600`; it is never loaded by the web process. Use the `OOHSTORY_LIBRARY_*` names from `deploy/mysql/library-infrastructure.env.example`, store database/Redis passwords in separate root-only files, and set `LIBRARY_TASK_USE_SYSTEMD=1`. Install `deploy/systemd/oohstory-library-task-worker@.service` before enabling deconstruction. The admin service itself continues to use the independent read-only account from `admin.env`.

Copy `deploy/oohstory-admin.service` to `/etc/systemd/system/`, inspect every path, run `systemd-analyze verify`, then enable it only during an approved deployment. The supplied unit binds only `127.0.0.1:8092`, uses a private state directory, filesystem/process hardening, and loopback-only network access. `NoNewPrivileges=false` is narrowly required for the `sudo` helper; the web process itself remains unprivileged.

When migrating an existing host, install (but do not enable) all shipped
`oohstory-library-*` service/timer units first. The optional root-owned
`ops/oohstory-library-scheduler-cutover` and its one-shot unit disable the four
legacy timers without stopping a running writer, wait for all current legacy
services to drain, and only then enable the OOHStory timers. On timeout or
activation failure it restores the legacy timers. Fresh installations do not
need this migration-only one-shot.

## Access boundary

Production access is `http://127.0.0.1:8092/admin/` from the host itself. The shipped environment example and systemd unit preserve this loopback-only boundary. Public reverse-proxy and FRP examples are deliberately not included.

## Modules and honest capability boundaries

- Dashboard: real reader health/home totals, bounded `/proc` memory/load/uptime/process count, `statvfs` disk totals, and status for the exact OOHStory/library unit allowlist.
- Electronic library: a first-class workspace mirroring the original library information architecture: hero and trust boundary, eight live metrics, local/authorized sync panels, source strip, tone/plot index progress, catalog search, download queue, category distribution, four cover pipelines, infrastructure, deconstruction summary, and audited task controls. The legacy `/admin/jobs` URL redirects here; JSON is available at `/api/admin/library` while `/api/admin/jobs` remains compatible.
- Books: the complete shared-library workbench with separate total/local/Fanqie/readable/tone/plot/global-deconstruction views, MySQL-side search/filter/pagination, catalog detail, public Reader chapter/metric handoff, local/Fanqie scheduled sync switches, incremental or full tone/plot index rebuilds, logical local/Fanqie moves, real-cover synchronization, AI cover redraw, protected cover upload, nine-source global search/import, and single/selected/filtered deconstruction with runner/model/reasoning selection, resume, status, and bounded logs. The plot workbench provides local-index-first evidence Q&A, adaptation planning, review-only diffs, future-writing binding, and explicit commit with source-hash verification plus recoverable chapter backups. Plot writes are confined to `/var/lib/oohstory-admin/library-project`; the browser cannot supply another project path. Project-specific tone matching is the only intentionally omitted counterpart feature. Catalog browsing remains transaction read-only; each write crosses the audited structured helper.
- Pipeline: status and confirmed start/restart/stop/enable/disable actions for reader, authorized sync, cover generation/sync, light/derived indexes, deconstruction sync, and the crawler browser. Each card reports the unit file, working directory, and actual runtime script/executable path. The web process has no script viewer, editor, publisher, or corresponding sudo grant.
- Audit: durable SQLite actor/action/target/result/timestamp log.

If the Reader, MySQL, Redis, the shared mount, or a status file is unavailable, the UI and JSON API report an honest unavailable/degraded state. They never invent completion.

## Verification

```bash
.venv/bin/pip install --requirement requirements-test.txt
PYTHONPATH=src .venv/bin/python -m pytest
PYTHONPATH=src .venv/bin/python -m compileall -q src tests
sh -n ops/oohstory-admin-systemctl
sh -n ops/oohstory-admin-library-action
python3 -m py_compile ops/oohstory-admin-library-action-runner
sudo visudo -cf ops/oohstory-admin.sudoers
printf '%s' '{"action":"capabilities"}' | sudo -u oohstory-admin \
  sudo -n /usr/local/libexec/oohstory-admin-library-action
```

Deployment is intentionally not performed by this repository setup.
