# Developer Guide — Distributed URL Shortener

> **Audience:** developers who want to understand *exactly* what happens in this codebase — request by request, module by module.
> **Companion docs:** [README](../README.md) · [Implementation plan](../P1_URL_Shortener_Implementation_Guide.md)

This guide follows **execution paths**, not the directory tree. You first see what happens when a request
arrives, and only then zoom into each module that participates. Every claim maps to a file and a symbol
you can open in your editor.

## Contents

1. [The system in one page](#1-the-system-in-one-page)
2. [Codebase map & reading order](#2-codebase-map--reading-order)
3. [Boot sequence](#3-boot-sequence-from-docker-compose-up-to-the-first-request)
4. [Request flow #1: creating a short URL](#4-request-flow-1-creating-a-short-url-write-path)
5. [Request flow #2: following a short URL](#5-request-flow-2-following-a-short-url-read-path)
6. [Request flow #3: analytics, health, metrics](#6-request-flow-3-analytics-health-metrics)
7. [Module reference](#7-module-reference-file-by-file)
8. [Cross-cutting concepts](#8-cross-cutting-concepts)
9. [Infrastructure deep dives](#9-infrastructure-deep-dives)
10. [Life of a short code](#10-life-of-a-short-code-end-to-end-timeline)
11. [Testing guide](#11-testing-guide)
12. [Operations playbook](#12-operations-playbook)
13. [Troubleshooting](#13-troubleshooting)
14. [Configuration reference](#14-configuration-reference)
15. [Known limitations & guided exercises](#15-known-limitations--guided-exercises)
16. [Glossary](#16-glossary)
- [Appendix A — Plan → code mapping](#appendix-a--plan--code-mapping)
- [Appendix B — Deviations from the plan](#appendix-b--deviations-from-the-plan-and-why)

## Reading paths

| Your goal | Suggested path |
|---|---|
| Get productive: run it, poke it, break it | 1 → 3 → 12 (Operations) |
| Understand the whole system deeply | 1 → 3 → 4 → 5 → 8 → 9 |
| Modify / extend a feature | 2 (module map) → 7 (module reference) → 15 (exercises) |
| Debug something that's broken | 12 (Operations) → 13 (Troubleshooting) |
| Prepare for interviews | 4, 5 (flows) → 8 (concepts) → 15 (trade-offs & follow-ups) |

---

## 1. The system in one page

This is a URL shortener with three moving parts of real interest: a **write-optimized path**
(PostgreSQL primary), a **read-optimized path** (Redis cache-aside in front of a streaming
read replica), and **click analytics** written on every redirect.

### 1.1 Components and responsibilities

| Component | Technology | Responsibility | Primary files |
|---|---|---|---|
| Reverse proxy | nginx | Route `/api/*`, `/{code}`, `/metrics`, `/health` to backend; everything else to the SPA; inject `X-Forwarded-*` headers | `nginx/nginx.conf`, `frontend/nginx.conf` |
| API | FastAPI + uvicorn (Python 3.12, async) | HTTP contract, validation, orchestration | `backend/app/api/`, `backend/app/main.py` |
| Shortening logic | pure Python | ID → Base62 code conversion | `backend/app/services/shortener.py` |
| Analytics logic | SQLAlchemy async | Click events + atomic counters | `backend/app/services/analytics.py` |
| Cache | Redis 7 | Hot URL → target URL (cache-aside, TTL) | `backend/app/cache.py` |
| Write DB | PostgreSQL 16 (primary) | URLs, click events, counters; WAL producer | `backend/app/models.py` + `scripts/init-primary.sh` |
| Read DB | PostgreSQL 16 (replica) | Serves redirect lookups; streaming hot standby | `docker-compose.yml` (`db-replica`) |
| UI | React 18 + Vite + Tailwind | Shorten form, analytics dashboard | `frontend/src/` |
| Observability | Prometheus client + Instrumentator | Custom counters/histogram + auto HTTP metrics | `backend/app/metrics.py` |
| Orchestration | Docker Compose / Helm (k3s) | Local stack / Kubernetes stack | `docker-compose*.yml`, `helm/` |

### 1.2 Runtime topology (Docker Compose)

```mermaid
flowchart LR
    U["User / curl / Browser"] -->|":80"| NG["nginx :80"]

    subgraph net["Docker Compose network"]
        NG -->|"API, short codes, /metrics, /health"| BE["backend :8000<br/>FastAPI + uvicorn"]
        NG -->|"everything else"| FE["frontend :3000<br/>React build + nginx"]
        FE -->|":3000 direct / k8s: API, short codes"| BE
        PR["Prometheus :9090"] -->|"scrape /metrics"| BE
        BE -->|"writes, click analytics"| PG[("db :5432<br/>PostgreSQL primary")]
        BE -->|"reads"| RP[("db-replica :5432<br/>hot standby")]
        BE -->|"cache-aside"| RD[("redis :6379")]
        PG -.->|"WAL streaming<br/>slot replica_slot_1"| RP
    end
```

### 1.3 Code topology (import graph)

The import graph is deliberately acyclic and layered:

```mermaid
flowchart TD
    main["app/main.py<br/>(composition root)"] --> routes["app/api/routes.py"]
    main --> metrics["app/metrics.py"]
    main --> cache["app/cache.py"]
    main --> database["app/database.py"]

    routes --> deps["app/api/dependencies.py"]
    routes --> schemas["app/schemas.py"]
    routes --> services["app/services/*"]
    routes --> models["app/models.py"]
    routes --> config["app/config.py"]
    routes --> metrics

    deps --> database
    deps --> cache
    services --> models
    models --> database
    database --> config
    cache --> config
    alembic["alembic/env.py"] --> models
    alembic --> database
```

**Layer rules** (worth preserving as the project grows):

1. `api/` may import `services/`, `schemas`, `dependencies`, `models` and `config` — never the reverse.
   The redirect/stats handlers query models directly; keep multi-step business rules in `services/`.
2. `services/` contain business rules; they may import `models` but not FastAPI routing objects.
3. `models/`, `database/`, `cache` are infrastructure — no HTTP concepts allowed.
4. `config.py` is imported everywhere, imports nothing from the app.
5. `main.py` is the only composition root; it wires everything together.

---

## 2. Codebase map & reading order

### 2.1 File map

| File | What lives there | Read it when you… |
|---|---|---|
| `backend/app/config.py` | `Settings` (env-driven), `settings` singleton | add a new config knob |
| `backend/app/database.py` | `Base`, `write_engine`, `read_engine`, `WriteSession`, `ReadSession`, `get_write_db`, `get_read_db` | touch connections/sessions |
| `backend/app/models.py` | `URL`, `ClickEvent` ORM models | change the schema |
| `backend/app/schemas.py` | `ShortenRequest/Response`, `ClickEventOut`, `StatsResponse` | change the HTTP contract |
| `backend/app/cache.py` | `RedisCache` (pool + client factory), `cache` singleton | change cache behavior |
| `backend/app/metrics.py` | `URLS_CREATED`, `REDIRECTS_TOTAL`, `REDIRECT_LATENCY`, `setup_metrics` | add a metric |
| `backend/app/services/shortener.py` | `encode_base62`, `decode_base62`, `create_short_url` | change code generation |
| `backend/app/services/analytics.py` | `record_click`, header helpers | change click tracking |
| `backend/app/api/dependencies.py` | `get_redis`, re-exported DB dependencies | add a DI provider |
| `backend/app/api/routes.py` | the three HTTP endpoints | change the API |
| `backend/app/main.py` | `app`, `lifespan`, `/health`, route registration order | change app wiring |
| `backend/alembic/` | migrations (`versions/`), async `env.py` | change the schema |
| `backend/tests/` | fixtures + 18 tests | test behavior |

### 2.2 Recommended reading order (bottom-up)

`config.py` → `database.py` → `models.py` → `schemas.py` → `services/shortener.py` →
`services/analytics.py` → `cache.py` → `api/dependencies.py` → `api/routes.py` → `main.py` → `tests/`.

At each step ask: *"who imports me, and what do I assume about them?"* The answer is in section 1.3.

---

## 3. Boot sequence: from `docker compose up` to the first request

Understanding startup order prevents 90% of "why is this broken locally" questions.
Everything below happens *before* the first user request.

### 3.1 Compose startup order

Compose respects `depends_on` conditions (see `docker-compose.yml`):

```
db            starts, runs initdb + init script, becomes "healthy" (pg_isready)
   │
db-replica    waits for db healthy → pg_basebackup → starts as hot standby
   │
redis         starts (healthcheck: redis-cli ping)
   │
backend       waits for db healthy + redis healthy → alembic upgrade head → uvicorn
   │
frontend      starts after the backend container (its nginx resolves backend at boot)
   │
nginx, prometheus start (nginx waits for backend + frontend containers)
```

Two things worth noticing:

- `depends_on` is about **container start order**, not readiness of the app inside. The backend is
  tolerant of the replica not being up yet because reads only happen after requests arrive — and by
  then the replica has normally finished its base backup.
- The `db-replica` health is deliberately not required by `backend` (`condition: service_started`).
  Requiring "healthy" would need a replica healthcheck; the backend's read pool reconnects lazily anyway.

### 3.2 PostgreSQL primary bootstrap

On the **first** `docker compose up` (empty named volume `pg_primary_data`):

1. The official postgres entrypoint runs `initdb`, creating `$PGDATA` (`/var/lib/postgresql/data`).
2. It executes everything in `/docker-entrypoint-initdb.d/` — here, `scripts/init-primary.sh`:
   - creates the replication role: `CREATE USER replicator WITH REPLICATION ENCRYPTED PASSWORD 'repl_password'`
   - creates a **physical replication slot**: `SELECT pg_create_physical_replication_slot('replica_slot_1')`
   - appends `host replication replicator 0.0.0.0/0 md5` to `pg_hba.conf` (allows the replica
     container to connect over TCP)
3. Postgres starts with the flags from the compose `command:`:
   - `wal_level=replica` — enough WAL detail for streaming replication
   - `max_wal_senders=3` — max concurrent replication connections
   - `max_replication_slots=3` — max slots
   - `hot_standby=on` — allows read-only queries while in recovery; this matters on the replica (which sets it too) and is harmless on the primary

> ⚠️ **Gotcha:** init scripts only run when `$PGDATA` is empty. Editing `init-primary.sh` later does
> **nothing** to an existing volume. To re-run it: `docker compose down -v && docker compose up -d db`.
> `make reset` does this for the whole stack.

### 3.3 Replica bootstrap (`db-replica`)

The replica overrides the image entrypoint (`docker-compose.yml`, service `db-replica`) because the
default entrypoint would run `initdb` — we don't want two independent databases, we want a *copy*.
The script does, in order:

1. **Wait** for the primary to accept TCP connections (`pg_isready -h db`). TCP (not the local socket)
   is used deliberately: the temporary server that postgres runs while executing init scripts does not
   listen on TCP, so this also waits until the init script has created the replicator role and slot.
2. If `$PGDATA` has no `PG_VERSION` (fresh volume), run:

   ```bash
   pg_basebackup -h db -U replicator -D /var/lib/postgresql/data \
                 -Fp -Xs -P -R -S replica_slot_1
   ```

   | Flag | Meaning |
   |---|---|
   | `-Fp` | plain-file backup (as opposed to tar) |
   | `-Xs` | stream WAL *during* the backup (the backup is self-consistent) |
   | `-P` | progress output |
   | `-R` | write recovery config: `standby.signal` + `primary_conninfo` into `postgresql.auto.conf` |
   | `-S replica_slot_1` | use the physical slot; with `-R` the slot name is recorded as `primary_slot_name` |

3. The script retries the whole backup up to 10 times (each attempt wipes the directory first). This
   covers the race where the replicator role/slot is not yet visible.
4. A belt-and-braces `grep` appends `primary_slot_name = 'replica_slot_1'` if the auto-generated
   config lacks it (behavior of `-S` + `-R` has varied across PostgreSQL versions).
5. `chown -R postgres:postgres` + `chmod 0700` the data dir — `pg_basebackup` ran as root, and
   PostgreSQL **refuses to start** on a data directory owned by root or with permissive mode.
6. `exec su-exec postgres postgres -c hot_standby=on` — drop from root to the `postgres` user, then
   start the standby. From here on, the replica replays WAL from the primary continuously.

**Why a replication slot?** Without one, the primary may recycle WAL segments the replica hasn't
consumed yet (e.g., replica down for maintenance), after which the replica can never catch up and
needs a full re-backup. A slot makes the primary *retain* WAL until the replica confirms it.
The trade-off: an abandoned slot grows WAL without bound — monitor it in production.

**Verify it worked:**

```bash
# on the primary: one row, state=streaming
docker exec pg-primary psql -U postgres -d urlshortener \
  -c "SELECT client_addr, state, sync_state FROM pg_stat_replication;"

# slot is active
docker exec pg-primary psql -U postgres -d urlshortener \
  -c "SELECT slot_name, active FROM pg_replication_slots;"

# on the replica: recovery mode + the slot name
docker exec pg-replica psql -U postgres \
  -c "SELECT pg_is_in_recovery();" -c "SHOW primary_slot_name;"

# replication lag (should be ~0 in steady state)
docker exec pg-replica psql -U postgres -d urlshortener \
  -c "SELECT now() - pg_last_xact_replay_timestamp() AS lag;"
```

### 3.4 Backend container: migrate, then import, then serve

The backend's compose `command` is:

```bash
sh -c "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"
```

**Phase 1 — migrations.** Alembic loads `backend/alembic.ini`, which points at `backend/alembic/`.
`alembic/env.py`:

1. imports `app.config.settings` and injects `settings.DATABASE_URL` as the migration URL —
   so migrations always run against the **primary**;
2. imports `app.models` so `Base.metadata` knows every table;
3. creates a short-lived **async** engine (`asyncpg`) and runs the synchronous migration API inside
   `connection.run_sync(...)` — the standard recipe for using Alembic with SQLAlchemy's asyncio extension;
4. applies `versions/0001_initial_schema.py` (creating `urls`, `click_events` and their indexes) and
   records the revision in the `alembic_version` table. Re-runs are no-ops.

**Phase 2 — import-time side effects of `app.main`.** Importing `app.main` executes, top to bottom:

| Step | Effect |
|---|---|
| `from app.config import settings` | `Settings()` reads env vars (and `.env` if present) **once**; `settings` is a process-wide singleton |
| `from app.database import ...` | two async engines created — no connections yet; pools connect lazily on first use |
| `from app.cache import cache` | `RedisCache()` builds a `ConnectionPool` from `REDIS_URL` — still no socket |
| `from app.metrics import ...` | `Counter`/`Histogram` objects register themselves in the global `prometheus_client` registry |
| `app = FastAPI(...)` | app object + lifespan handler |
| `@app.get("/health")` | `/health` route registered **first** |
| `setup_metrics(app)` | instrumentation middleware + `/metrics` route registered **second** |
| `app.include_router(router)` | the three API routes registered **last** |

> 🔑 **This order is load-bearing.** FastAPI matches routes in registration order.
> `GET /{short_code}` is a catch-all for any single path segment. If it were registered before
> `/health` or `/metrics`, then `GET /health` would be interpreted as *short code "health"*
> (a 404 from the redirect handler) and Prometheus scrapes of `/metrics` would break.

**Phase 3 — serve.** Uvicorn runs the ASGI lifecycle: `lifespan` startup (currently a no-op `yield`)
→ listen on `:8000`. On shutdown, `lifespan` closes the Redis pool and disposes both DB engines.

**Why startup does not connect to anything:** lazy pools + no warm-up query mean the process starts
even if Postgres is briefly slow, and crash-loops are avoided. Connections are established on the
first real request and reused afterwards.

### 3.5 nginx, frontend, Prometheus

- `nginx/nginx.conf` mounts as `/etc/nginx/conf.d/default.conf`. Upstream names (`backend`,
  `frontend`) resolve via Docker's embedded DNS to container IPs.
- The frontend image is a static build copied into an `nginx:alpine` listening on `:3000`
  (`frontend/nginx.conf`); besides the SPA fallback (`try_files ... /index.html`) it proxies
  `/api`, `/health`, `/metrics` and short codes to `backend:8000`.
- Prometheus reads `prometheus/prometheus.yml` and scrapes `backend:8000/metrics` every 15 s.

---

## 4. Request flow #1: creating a short URL (write path)

`POST /api/v1/shorten` — the only write endpoint.

```mermaid
sequenceDiagram
    autonumber
    participant C as Client (curl/Browser)
    participant N as nginx
    participant A as FastAPI (uvicorn)
    participant P as PostgreSQL primary
    participant R as Redis

    C->>N: POST /api/v1/shorten {"url": ..., "redirect_type": 302}
    N->>A: proxy_pass http://backend (Host, X-Real-IP, X-Forwarded-For, X-Forwarded-Proto)
    A->>A: validate ShortenRequest (Pydantic)
    A->>P: INSERT urls (placeholder code) RETURNING id
    A->>A: short_code = encode_base62(id)
    A->>P: UPDATE urls SET short_code=...  COMMIT
    A->>R: SETEX url:{code} = original_url (3600s)
    A->>R: SETEX url:{code}:type = "302" (3600s)
    A-->>N: 201 {short_url, short_code, original_url}
    N-->>C: 201
```

### 4.1 Step-by-step walkthrough

**Step 1 — routing and proxy headers.** nginx matches `location /api/` (prefix match) and proxies
to the backend, *appending* the real client address to `X-Forwarded-For`. (Header semantics are in section 9.1.)

**Step 2 — dependency resolution.** Before the handler body runs, FastAPI:
- validates the JSON body into `ShortenRequest` (`app/schemas.py`):
  - `url` — required, ≤ 2048 chars, scheme must be `http`/`https` with a host
    (custom `field_validator`, not `HttpUrl` — see section 7.4 for why);
  - `redirect_type` — `Literal[301, 302]`, default `302`. Anything else → **422** automatically.
- resolves `get_write_db` → a fresh `AsyncSession` from the **write** engine;
- resolves `get_redis` → a `Redis` client sharing the process-wide connection pool.

Handler signature (`app/api/routes.py`):

```python
@router.post("/api/v1/shorten", response_model=ShortenResponse, status_code=201)
async def shorten_url(body, db=Depends(get_write_db), redis=Depends(get_redis))
```

**Step 3 — create the row (`services/shortener.py::create_short_url`).** This single function
encapsulates the "counter → Base62" strategy:

```python
url = URL(original_url=..., redirect_type=..., short_code=secrets.token_hex(4))
db.add(url)
await db.flush()                          # INSERT ... RETURNING id  → url.id is now set
url.short_code = encode_base62(url.id)    # pure function, no I/O
await db.commit()                         # UPDATE ... ; COMMIT
await db.refresh(url)                     # reload server defaults (created_at)
```

What the database sees (illustrative SQL):

```sql
BEGIN;
INSERT INTO urls (short_code, original_url, redirect_type, click_count, is_active)
VALUES ('9f3ac21d', $1, $2, 0, true) RETURNING urls.id;   -- id = 42 (BIGSERIAL)
UPDATE urls SET short_code = 'G' WHERE urls.id = 42;
COMMIT;
SELECT ... FROM urls WHERE urls.id = 42;                  -- refresh()
```

Why this exact sequence? See the deep dive in section 4.2.

**Step 4 — metrics.** `URLS_CREATED.inc()` — a Prometheus counter named
`urlshortener_urls_created_total`.

**Step 5 — cache warm.** Two `SETEX` calls (not a pipeline here; only two commands) write
`url:{code}` → original URL and `url:{code}:type` → `"302"`, each with `CACHE_TTL_SECONDS` (3600 s).
Warming the cache matters for **read-your-writes over replication**: the client can click the short
link immediately; the redirect then does not depend on the replica having caught up yet (section 8.3).

**Step 6 — response.** `ShortenResponse` is serialized: `short_url` is built as
`{BASE_URL}/{code}` — `BASE_URL` is `http://localhost` in Compose because this is the address the
*user's browser* must reach, not the container-internal address. Then **201 Created**.

### 4.2 Deep dive: why "placeholder → flush → update"?

Three decisions are hiding in those seven lines:

1. **Why not hash the URL?** A hash (MD5 truncated, base62'ed) requires collision detection — every
   insert would need a lookup, and collisions are birthday-paradox-likely as the table grows.
   A **counter** is collision-free by construction: each counter value appears once.
2. **Why the `flush()` dance?** The short code is derived from the auto-increment `id`, which the
   database only assigns on insert. `flush()` sends the `INSERT` and returns the generated id
   *inside the same transaction*, so the placeholder never becomes visible to other transactions
   (MVCC hides uncommitted rows).
3. **Why a random placeholder instead of `""`?** The unique index on `short_code` means two
   *concurrent* inserts using the same placeholder (e.g. `""`) would block on each other at the
   index entry, and the second one would fail after the first commits. A per-insert random value
   (`secrets.token_hex(4)`) guarantees the temporary rows never collide. It is overwritten before
   commit, so users never see it.

**Trade-off to remember:** sequential ids produce *guessable* codes (`1`, `2`, `3`, …). Mitigations
range from a random offset to fully random codes with a uniqueness retry — see section 15.

### 4.3 Error paths

| Condition | Result |
|---|---|
| Missing/invalid JSON body, bad scheme, `redirect_type` not 301/302, URL > 2048 chars | `422 Unprocessable Entity` (FastAPI validation, no handler code runs) |
| DB unreachable / pool exhausted | `500` — the insert or commit raises; no custom handling |
| Duplicate code (practically impossible unless schema drifts) | unique-violation `500` |

---

## 5. Request flow #2: following a short URL (read path)

`GET /{short_code}` — the hot path; this is where cache-aside, read replicas and analytics meet.

```mermaid
sequenceDiagram
    autonumber
    participant C as Client (Browser)
    participant N as nginx
    participant A as FastAPI
    participant R as Redis
    participant E as PostgreSQL replica
    participant P as PostgreSQL primary

    C->>N: GET /{code}
    N->>A: proxy_pass http://backend (regex location)
    A->>R: GET url:{code}
    alt cache HIT
        R-->>A: original_url
        A->>R: GET url:{code}:type
        R-->>A: "302"
    else cache MISS
        R-->>A: nil
        A->>E: SELECT ... WHERE short_code=$1 AND is_active IS true
        E-->>A: row
        A->>R: pipeline: SET url:{code}, SET url:{code}:type (EX 3600)
    end
    A->>P: SELECT id FROM urls WHERE short_code=$1
    A->>P: INSERT click_events + UPDATE urls SET click_count = click_count + 1
    A->>A: REDIRECTS_TOTAL.inc(...), REDIRECT_LATENCY.observe(...)
    A-->>N: 301/302 Location: original_url
    N-->>C: 301/302
```

### 5.1 Step-by-step walkthrough

**Step 1 — nginx.** The quoted regex location `"^/([a-zA-Z0-9]{1,10})$"` matches single-segment
alphanumeric paths (`/1`, `/aB3xZ`). See section 9.1 for precedence and the quoting gotcha.

**Step 2 — route + dependencies.** FastAPI resolves one **read** session (replica), one **write**
session (primary — for analytics), and the Redis client. Sessions connect lazily: on a cache hit the
read session never opens a connection, and the write session opens one only for `record_click`.

**Step 3 — cache lookup.**

```python
cached_url = await redis.get(f"url:{short_code}")
cache_hit = cached_url is not None
```

- **Hit:** `original_url` comes from RAM (a single Redis round trip); the redirect type comes from
  `url:{code}:type` with `settings.DEFAULT_REDIRECT_STATUS` as fallback.
- **Miss:**
  ```python
  result = await db.execute(
      select(URL).where(URL.short_code == short_code, URL.is_active.is_(True))
  )
  ```
  runs on the **replica**. Not found → `404 {"detail": "Short URL not found"}` — note the click is
  *not* recorded for 404s. Found → both cache keys are written in one pipeline (single round trip).

**Step 4 — record the click (`services/analytics.py::record_click`).** This is a *write*, so it goes
to the primary:

1. `SELECT` the URL again on the primary. Why not reuse the id from the replica lookup?
   Because on a cache hit we have no row at all, and on a miss the replica may lag — the primary is
   the only source guaranteed to have the row the client can see.
2. Insert a `click_events` row:
   - `ip_address` — first entry of `X-Forwarded-For` (set by nginx), else the TCP peer address;
   - `user_agent` / `referrer` — truncated to the column widths (512 / 2048) defensively;
3. Bump the counter **atomically**, in the same transaction:
   ```python
   update(URL).where(URL.id == url.id).values(click_count=URL.click_count + 1)
   ```
   (`click_count + 1` is evaluated *by Postgres*; a read-modify-write in Python would lose updates
   under concurrency.)
4. One `COMMIT` persists both statements.

> **Latency note:** despite the "fire-and-forget" comment in the code, `record_click` is `await`ed
> inline — the redirect waits for the analytics write. It is a deliberate simplicity trade-off;
> Section 15 shows how to move it to a background task or queue.

**Step 5 — metrics.**
`REDIRECTS_TOTAL.labels(status_code="302", cache_hit="true").inc()` and
`REDIRECT_LATENCY.observe(elapsed)` where `elapsed` covers cache + DB + analytics for this request.

> **Nuance:** because `POST /shorten` warms the cache, the *first* redirect is normally already a
> cache hit. `cache_hit` therefore describes the *redirect path*, not "has this URL been visited before".
> Unit tests exercise the miss path by deleting the keys first (`tests/test_redirect.py`).

**Step 6 — response.** `RedirectResponse(url=original_url, status_code=redirect_type)` →
`Location:` header + 301/302. What the browser does with it is section 5.2.

### 5.2 301 vs 302: the analytics trade-off

| | 301 Moved Permanently | 302 Found (default) |
|---|---|---|
| Browser caching | Aggressive (often cached "forever") | Normally not cached |
| Requests reaching the server | Only the first one per client | Every click |
| Click analytics | **Lossy** | Complete |
| Latency for repeat clicks | ~0 (no server hit) | Server + cache round trip |

The service defaults to **302** precisely so analytics stay complete, and exposes `redirect_type`
per URL for users who care more about speed than measurement. This mirrors the classic
SDI Chapter 8 discussion, implemented as a database column — not a server-wide flag.

### 5.3 Deep dive: Base62 encoding

`encode_base62(n)` is textbook number-base conversion (the same loop as decimal → binary, base 62):

```
n = 125:  125 % 62 = 1 → '1'      n = 2
            2 % 62 = 2 → '2'      n = 0      →  digits collected "1","2" → reversed → "21"
```

- Alphabet: `0-9a-zA-Z` (62 chars) — all URL-safe, unlike Base64's `+` and `/`.
- 62⁷ − 1 = 3,521,614,606,207 (~3.5 × 10¹²): even at 1,000 creations/second, 7-char codes last
  **~110 years**. Codes shorter than 7 chars appear naturally while the counter is small
  (`1` → `"1"`), which is why short codes here are *at most* 7 characters, not exactly 7
  (`SHORT_CODE_LENGTH` is currently informational; see section 15).
- `decode_base62` (used by tests and available for tooling) is the inverse loop:
  `num = num * 62 + index_of(char)`.

The counter itself is the database's `BIGSERIAL`: one global monotonic sequence per table. This is
the simplest correct design for a **single-primary** system and the reason creation is not fully
horizontally scalable (a known trade-off; alternatives in section 15).

---

## 6. Request flow #3: analytics, health, metrics

### 6.1 `GET /api/v1/stats/{short_code}`

1. nginx `/api/` prefix → backend.
2. Three path segments, so it does **not** collide with the single-segment `/{short_code}` catch-all.
3. Reads from the **replica**: fetch the `URL` row, then the 100 most recent `click_events`
   (`ORDER BY clicked_at DESC LIMIT 100`).
4. 404 if the code is unknown.
5. Response model `StatsResponse` serializes ORM objects (`ClickEventOut` has
   `model_config = ConfigDict(from_attributes=True)`), so no manual dict building is needed.

**Consistency notes worth internalizing:**
- `total_clicks` and `recent_clicks` are read as two separate queries on the replica — a click that
  lands between the two queries is visible in one but not the other (no snapshot transaction).
- Because everything comes from the replica, freshly recorded clicks can be invisible for a few
  milliseconds of replication lag.

### 6.2 `GET /health`

Registered directly on the app (`main.py`) *before* the router. It returns a constant
`{"status": "ok"}`: it is a **liveness** probe — "the event loop is accepting requests".
It intentionally does *not* check Postgres or Redis; Kubernetes uses the same endpoint for
readiness today, which is a documented simplification (section 15, exercise: add `/ready`).

### 6.3 `GET /metrics`

`setup_metrics(app)` installs `prometheus_fastapi_instrumentator`, which:

1. adds a middleware that records **automatic** metrics for every request
   (`http_requests_total{handler,method,status}`, request/response sizes, and a request-duration
   histogram);
2. registers the `/metrics` route, which serializes the `prometheus_client` registry in text
   exposition format.

Custom metrics (`app/metrics.py`):

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `urlshortener_urls_created_total` | Counter | — | successful `POST /shorten` calls |
| `urlshortener_redirects_total` | Counter | `status_code` (301/302), `cache_hit` (true/false) | redirects served, sliceable by behavior |
| `urlshortener_redirect_duration_seconds` | Histogram | — | end-to-end handler latency, buckets 5 ms … 1 s |

The `*_created` series you see next to each counter are Unix timestamps emitted automatically by the
Python client, not something the app code writes.

> `/metrics` is unauthenticated here — fine inside a private network, a leak in public deployments
> (see section 9.3).

---

## 7. Module reference (file by file)

Read this section like a tour through `backend/app/`; every file appears in dependency order.

### 7.1 `config.py` — configuration

```python
class Settings(BaseSettings):
    APP_NAME: str = "URL Shortener"
    BASE_URL: str = "http://localhost:8000"
    SHORT_CODE_LENGTH: int = 7
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@db:5432/urlshortener"
    DATABASE_READ_URL: str = "postgresql+asyncpg://postgres:postgres@db-replica:5432/urlshortener"
    REDIS_URL: str = "redis://redis:6379/0"
    CACHE_TTL_SECONDS: int = 3600
    DEFAULT_REDIRECT_STATUS: int = 302
    class Config: env_file = ".env"
```

- **Precedence:** real environment variables > `.env` file > defaults in code. Compose passes env
  vars explicitly (see the `backend:` service), so a `.env` file is optional.
- `settings = Settings()` runs at **import time** — this is why tests set `os.environ` *before*
  importing `app.*` (`tests/conftest.py`).
- Every default is container-oriented (`db`, `db-replica`, `redis` hostnames); running the backend
  directly on your host requires the Docker Compose's published ports (`localhost:5432`, etc.).
- Note `SHORT_CODE_LENGTH` and `DEFAULT_REDIRECT_STATUS` usage: the latter is used as the redirect
  fallback on cache hits; the former is currently **not** used to pad codes.

### 7.2 `database.py` — engines, sessions, dependencies

```python
write_engine = create_async_engine(settings.DATABASE_URL,       pool_size=10, max_overflow=20)
read_engine  = create_async_engine(settings.DATABASE_READ_URL,  pool_size=20, max_overflow=30)

WriteSession = async_sessionmaker(write_engine, class_=AsyncSession, expire_on_commit=False)
ReadSession  = async_sessionmaker(read_engine,  class_=AsyncSession, expire_on_commit=False)

async def get_write_db(): async with WriteSession() as session: yield session
async def get_read_db():  async with ReadSession()  as session: yield session
```

- **Two engines** encode the primary/replica split at the type level: you cannot accidentally read
  from the replica in a write path (you'd have to import the wrong dependency).
- **Pool sizing:** the read pool (20) is double the write pool (10) because the workload is ~100:1
  read-heavy. Each engine may additionally open up to `max_overflow` connections temporarily
  (30 / 50). One uvicorn worker per container, so worst-case connections per backend container =
  30 + 50 = 80 — plus Postgres reserves. Watch `max_connections` (default 100) as you scale
  replicas; that math is a classic production incident.
- **`expire_on_commit=False`:** after `commit()`, accessing loaded attributes does not trigger a
  lazy refresh (which would need a round trip in async code).
- **Generator dependencies:** `yield session` then the `async with` closes the session after the
  response — sessions are never shared across requests.

### 7.3 `models.py` — the schema as Python classes

`URL` (`urls`):

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGINT` PK, autoincrement | backed by a `BIGSERIAL` sequence; is the Base62 source |
| `short_code` | `VARCHAR(10)`, unique, indexed | unique index `ix_urls_short_code` |
| `original_url` | `VARCHAR(2048)` | matches the request validator's max length |
| `created_at` | `TIMESTAMPTZ`, `server_default=now()` | set by the *database*; loaded via `refresh()` |
| `expires_at` | `TIMESTAMPTZ`, nullable | accepted by the schema but **not enforced anywhere yet** (section 15) |
| `redirect_type` | `INTEGER`, default 302 | 301 or 302 |
| `click_count` | `BIGINT`, default 0 | denormalized counter for cheap stats reads |
| `is_active` | `BOOLEAN`, default true | redirect query filters on it |

Extra index: `idx_short_code_active (short_code, is_active)` — a composite index matching the
redirect query's `WHERE` clause. (The unique index alone would already locate the row; the composite
index demonstrates the pattern and pays off if the planner prefers an index-only path. Profile with
`EXPLAIN ANALYZE` before adding more.)

`ClickEvent` (`click_events`):

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGINT` PK | |
| `url_id` | `BIGINT` FK → `urls.id`, indexed | `ix_click_events_url_id` for per-URL aggregations |
| `clicked_at` | `TIMESTAMPTZ`, `server_default=now()` | |
| `ip_address` | `VARCHAR(45)` | wide enough for IPv6 |
| `user_agent` / `referrer` | `VARCHAR(512)` / `VARCHAR(2048)` | truncated defensively in analytics |
| `country` | `VARCHAR(2)` | reserved for a future GeoIP enrichment |

**Defaults placement:** `redirect_type`, `click_count`, `is_active` are Python-side `Column(default=…)`
(applied on INSERT by SQLAlchemy), while `created_at`/`clicked_at` are server-side. Mixing is normal;
know that Python-side defaults are invisible to raw SQL inserts.

### 7.4 `schemas.py` — the HTTP contract

- `ShortenRequest.url` is a **validated `str`**, not Pydantic's `HttpUrl`. Reason: Pydantic v2
  normalizes `HttpUrl` (e.g. `https://example.com` → `https://example.com/`), and the API contract
  (and its tests) promise to echo back exactly what the user submitted.
- `redirect_type: Literal[301, 302]` — validation happens before your handler runs.
- `ClickEventOut` sets `from_attributes=True`, enabling `StatsResponse(..., recent_clicks=<ORM rows>)`.
- `StatsResponse.total_clicks` is a plain int filled from `url.click_count`.

### 7.5 `cache.py` — Redis cache-aside client

```python
class RedisCache:
    def __init__(self):
        self.pool = redis.ConnectionPool.from_url(settings.REDIS_URL,
                                                  max_connections=20, decode_responses=True)
    async def get_client(self) -> redis.Redis:
        return redis.Redis(connection_pool=self.pool)
    async def close(self):
        await self.pool.disconnect()

cache = RedisCache()
```

- One process-wide pool (≤ 20 connections); every request gets a cheap `Redis` façade object
  sharing that pool (connections are checked out per command and returned).
- `decode_responses=True` means `GET` returns `str`, not `bytes` — relevant because the redirect
  type is stored as text (`"302"`) and parsed with `int()`.
- `close()` is called from the app lifespan on shutdown. Tests call it per test — see section 11.3 for why.
- Redis itself runs with `--maxmemory 256mb --maxmemory-policy allkeys-lru` (compose): when memory
  fills, the *least recently used* entries evict first. This is safe here because Redis is a cache,
  never a source of truth — losing a key just causes a DB round trip.

### 7.6 `metrics.py` — instrumentation

Three custom metric objects (registered at import) + `setup_metrics(app)` which instruments the app
and exposes `/metrics`. See section 6.3 for semantics. Adding a metric = declare it here, import it where
you record it.

### 7.7 `services/shortener.py` — code generation

Covered in section 4.2 and section 5.3. API surface:

- `encode_base62(num) -> str` — pure, total (handles 0), O(log₆₂ n).
- `decode_base62(s) -> int` — inverse, O(len); currently used by tests/tooling.
- `create_short_url(db, original_url, redirect_type) -> URL` — one transaction: insert with a random
  placeholder, flush to obtain the id, assign the Base62 code, commit, refresh.

The deferred `from app.models import URL` inside the function avoids a circular import at module
load (`models` → `database` → `config`), while `services` sits above `models` in the layering.

### 7.8 `services/analytics.py` — click recording

- `_client_ip(request)` — trust order: first `X-Forwarded-For` entry (nginx), else socket peer.
- `_truncate(value, limit)` — defensive width limiting to match column sizes.
- `record_click(db, short_code, request)` — select on primary, insert event, atomic counter update,
  single commit. Covered in section 5.1 step 4.

Known subtlety: on a cache hit, `record_click` performs *its own* `SELECT` — one extra round trip
per click. It could be optimized (value cached alongside the URL), but keeping the primary as the
authoritative lookup is the safer default.

### 7.9 `api/dependencies.py` — the DI surface

```python
from app.database import get_read_db, get_write_db   # re-exported
async def get_redis() -> Redis: return await cache.get_client()
```

A thin indirection with two benefits: routes depend on `app.api.dependencies` (not on
`database`/`cache` directly), and tests can override any provider with
`app.dependency_overrides[get_redis] = fake` without touching production code.

### 7.10 `api/routes.py` — the endpoints

All three handlers are here; behavior is documented in sections 4-6. Things to notice while reading:

- `Depends(...)` defaults are resolved per request; the same route can depend on both a read and a
  write session (the redirect does).
- Metrics are recorded *after* the work, *before* returning — so the latency histogram includes
  analytics I/O.
- 404s `raise HTTPException`; serialization/validation errors are handled by FastAPI.

### 7.11 `main.py` — composition root

Creates the app, registers `/health`, calls `setup_metrics`, then `include_router` (order = section 3.4),
and defines the shutdown `lifespan`. If you add a startup action (e.g. a readiness check or
cache warm-up), it goes in `lifespan` *before* `yield`.

### 7.12 `alembic/` — migrations

- `alembic.ini` — static config; the DB URL is injected at runtime, so no credentials in the file.
- `env.py` — imports `settings` + models, converts the async URL into a runnable migration context.
- `versions/0001_initial_schema.py` — creates both tables and the three indexes; the exact ordering
  of `create_table` calls respects the FK from `click_events` → `urls`.
- `script.py.mako` — template for new revisions (`make revision NAME="..."`).

`alembic upgrade head` is idempotent and safe to run in every deploy; `alembic downgrade -1` exists
in the revision but is not used by automation. If models and migrations drift, `alembic revision
--autogenerate` shows the diff — run it after changing `models.py`.

### 7.13 `tests/` — executable specification

Covered in depth in section 11. Coverage map: Base62 math, request/response contract, redirect statuses,
cache warm/miss, analytics, 404 paths, validation errors.

---

## 8. Cross-cutting concepts

These mental models explain *why* the code is shaped the way it is. Each one cites where it lives.

### 8.1 Async I/O model

One `uvicorn` worker runs **one event loop**; all endpoints are `async def`. Every I/O operation is
awaited through an async driver:

| I/O | Driver | Where |
|---|---|---|
| Postgres queries | `asyncpg` via SQLAlchemy async | `database.py`, `services/` |
| Redis commands | `redis.asyncio` (hiredis parser) | `cache.py`, `routes.py` |
| Client HTTP | ASGI (`http`/`asgi`) | uvicorn |

Consequences for your own code:

- Never call blocking I/O (`requests`, `time.sleep`, heavy file IO) inside a handler — it stalls
  *all* concurrent requests on that worker.
- Pure CPU work is fine if tiny. Base62 encoding is O(log n) over ≤ 11 digits — perfect inline.
  If you ever add `bcrypt`-style work, offload it (`await run_in_threadpool(...)` or a worker).
- To use multiple cores, scale with more *containers* (`backend.replicas`) rather than threads;
  uvicorn's `--workers` would also work but multiplies connection pools (section 7.2).

### 8.2 Dependency injection and session lifecycle

`Depends(get_write_db)` is not a helper call — FastAPI builds a per-request dependency graph *before*
calling your handler and tears it down *after* the response is sent:

```
request → resolve body (Pydantic) → resolve deps (sessions, redis) → handler → response → dep teardown
```

- `get_*_db` are async generators: `async with Session() as s: yield s` — the `async with` guarantees
  the session is closed even when the handler raises (404 paths included).
- Sessions and Redis connections are **lazy**: a session with no executed query never opens a
  connection, which is why cache-hit redirects don't consume a replica connection.
- For tests, any dependency can be replaced: `app.dependency_overrides[get_redis] = fake`. That is
  the only reason `api/dependencies.py` exists as an indirection.

### 8.3 Read/write splitting and replication lag

The architecture rule: **writes and anything that must be truthful go to the primary; high-volume
reads go to the replica.** The unavoidable consequence is a **lag window** (typically sub-millisecond
to a few ms locally, but unbounded if the replica stalls) where the replica hasn't seen a primary
write yet.

Where lag could bite, and what the code does about it:

| Scenario | Exposure | Mitigation in code |
|---|---|---|
| User creates a URL and immediately clicks it | `GET` could read the replica before the row replicates (404) | `POST /shorten` **warms the cache**; the redirect hits Redis, not the replica |
| Click is recorded, then stats are fetched | stats read from replica may miss the last clicks | acceptable by design; stats are "eventually consistent" analytics |
| Cache key expires/evicted, then redirect arrives during lag | replica read could 404 for a moment | short real-world exposure; a single retry against the primary would eliminate it (exercise) |
| `record_click` must find the URL | if it read the replica it could silently drop clicks | `record_click` deliberately re-selects on the **primary** |

Interview-grade alternatives worth knowing: sticky primary reads for a session, LSN-based
"wait for replica to catch up", or session pinning in the proxy. This project chooses caching as the
read-your-writes bridge — simple and effective for a URL shortener.

### 8.4 Cache-aside: lifecycle, consistency, stampede

```mermaid
flowchart LR
    W["POST /shorten"] -->|"SET url:CODE and url:CODE:type"| C[(Redis)]
    R["GET /{code}"] -->|"GET url:CODE"| C
    C -->|hit| R
    C -->|miss| D[("PostgreSQL replica")] -->|"SET (pipeline)"| C
```

**Keys**

| Key | Value | Written by | Read by |
|---|---|---|---|
| `url:{code}` | original URL | create (warm) + redirect (miss) | redirect |
| `url:{code}:type` | `"301"`/`"302"` | create + redirect (miss) | redirect |
| `url:{code}:clicks` *(planned, not implemented)* | click counter | — | — |

**Lifecycle:** every entry has `CACHE_TTL_SECONDS` (3600 s). After expiry the next reader repopulates
it from the replica (cache-aside). Redis may also evict keys earlier under memory pressure
(`allkeys-lru`).

**Consistency model — the fine print:**

- There are no update/delete endpoints, so the classic "stale after write" problem is mostly out of
  scope. The one mutable field is `is_active`: flipping it in the database does **not** invalidate
  cache entries — the URL keeps redirecting until TTL expiry or eviction. Fixing this properly means
  invalidating on deactivate (`DEL url:{code}*`) or moving the active check into the cached value.
- Cache writes are *write-through on read*: values are only written by the creator or a reader that
  already has the authoritative value.

**Cache stampede:** when a hot key expires (or after a Redis restart), N concurrent requests all miss
and all query the replica — the "thundering herd". At current scale the replica absorbs it; at
larger scale the standard fix is **singleflight** (a short Redis `SET lock:{key} NX EX 5` so one
request fetches while others wait — the implementation plan includes a reference version). This is
the first exercise in section 15.

### 8.5 Observability

Three complementary signals:

1. **Structured logs** — uvicorn's access log + SQLAlchemy errors. Nothing custom yet
   (exercise: request IDs via middleware).
2. **Metrics** — `/metrics` for trends and SLOs. Useful PromQL:
   ```promql
   # redirect throughput by cache behavior
   sum by (cache_hit) (rate(urlshortener_redirects_total[5m]))

   # cache hit ratio (should be high in steady state)
   sum(rate(urlshortener_redirects_total{cache_hit="true"}[5m]))
     / sum(rate(urlshortener_redirects_total[5m]))

   # p95 redirect latency
   histogram_quantile(0.95, sum(rate(urlshortener_redirect_duration_seconds_bucket[5m])) by (le))
   ```
3. **Health** — `/health` (process-only, see section 6.2).

What's missing for production (deliberate learning gaps): tracing, request IDs, alerting rules,
`/ready` dependency checks, and metrics authentication.

### 8.6 Error-handling contract

The API uses FastAPI's built-in error shape rather than a custom envelope:

| Status | Body | Emitted by |
|---|---|---|
| 404 | `{"detail": "Short URL not found"}` / `{"detail": "Not Found"}` | handler / router |
| 422 | `{"detail": [ {loc, msg, type}, ... ]}` | Pydantic validation |
| 500 | `{"detail": "Internal Server Error"}` | unhandled exception |
| 301/302 | *(no JSON body; `Location` header)* | redirect handler |

Clients should treat any non-2xx/3xx as an error. If you want a uniform envelope
(`{"error": {"code", "message"}}`), add an exception handler in `main.py`; the error-path tests
assert only status codes, so they'd survive the change.

---

## 9. Infrastructure deep dives

### 9.1 nginx — routing precedence and proxy semantics

`nginx/nginx.conf` has four route kinds; nginx evaluates them in a strict order:
**exact (`=`) → longest prefix with `^~` → regex (`~`, `~*`, first match in file order) →
longest plain prefix.**

| Request | Matches | Upstream |
|---|---|---|
| `/api/v1/shorten` | `location /api/` (prefix; regex cannot match multiple segments) | backend |
| `/1`, `/aB3xZ` (≤ 10 alnum, one segment) | `location ~ "^/([a-zA-Z0-9]{1,10})$"` (regex) | backend |
| `/health`, `/metrics` | regex (both are single alnum segments!) — same upstream as their prefix blocks | backend |
| `/assets/index-abc.js`, `/` | `location /` | frontend |

Details that matter:

- **Quoting regexes:** the regex location *must* be quoted (`"..."`) because it contains `{`/`}` —
  unquoted, nginx's config parser splits the token and startup fails with
  `pcre2_compile() failed: missing closing parenthesis`. This was a real bug found during bring-up.
- `proxy_pass http://backend;` (no URI part) passes the **original request URI** through unchanged.
- Headers injected: `Host`, `X-Real-IP`, `X-Forwarded-For` (`$proxy_add_x_forwarded_for` — appends
  the TCP peer to any client-supplied list), `X-Forwarded-Proto`.
  ⚠️ Because the client can *send its own* `X-Forwarded-For`, the leftmost IP used in analytics is
  spoofable. Real deployments terminate at a trusted proxy and configure `set_real_ip_from`.
- nginx resolves `backend`/`frontend` at startup via Docker DNS; that's why `depends_on` lists them.

### 9.2 Docker Compose topology

| Service | Image / build | Host port | Why it's exposed |
|---|---|---|---|
| `db` | `postgres:16-alpine` | 5432 | psql/GUI tooling; real prod would not expose |
| `db-replica` | `postgres:16-alpine` | — | internal only |
| `redis` | `redis:7-alpine` | 6379 | `redis-cli` inspection |
| `backend` | `./backend` (multi-stage) | 8000 | `/docs`, direct debugging |
| `frontend` | `./frontend` (multi-stage) | 3000 | self-contained edge: serves the SPA + proxies `/api`, short codes |
| `nginx` | `nginx:alpine` + mounted conf | 80 | the front door |
| `prometheus` | `prom/prometheus` | 9090 | explore metrics |

- **Networking:** all services share a bridge network and address each other by *service name*
  (`db`, `db-replica`, `redis`). Inside containers the hostnames `localhost` would be wrong — except
  for `BASE_URL`, which is intentionally `http://localhost` because it's rendered for the user's
  *browser*, not used for container-to-container calls.
- **Volumes:** `pg_primary_data`, `pg_replica_data` persist databases across restarts.
  `docker compose down` keeps them; `down -v` deletes them (which is also how you re-run init
  scripts). Redis is intentionally ephemeral.
- **Config files mounted read-only:** nginx conf, prometheus conf, init-primary script.
- `docker-compose.prod.yml` mirrors the stack with: prebuilt GHCR images, `restart: unless-stopped`,
  no source mounts, and no host ports for backend/frontend/redis. Run with
  `docker compose -f docker-compose.prod.yml up -d`.
- The `version: "3.9"` key is obsolete in modern Compose and produces a harmless warning; it is kept
  because earlier Compose versions expected it.

### 9.3 Prometheus

- Config: `prometheus/prometheus.yml` — 15 s scrape of `backend:8000/metrics` (Docker DNS).
- The app exposes auto-instrumented HTTP metrics plus the three custom ones (section 6.3).
- No Grafana/Prometheus in the Helm chart: the k8s cluster is expected to bring its own monitoring
  (exercise: ServiceMonitor with kube-prometheus-stack).
- `/metrics` has no auth: keep it on a private network or protect the route.

### 9.4 Frontend — build, components, data flow

**Build:** `frontend/Dockerfile` is a two-stage build: `node:20-alpine` runs
`npm install && npm run build` (Vite + PostCSS/Tailwind compile `src/index.css` into a hashed CSS
file); the static `dist/` is copied into `nginx:alpine`, which serves it on `:3000` with SPA fallback.

**Two API paths, one contract:**

- *Dev (`npm run dev`)*: Vite dev server on `:3000` proxies `/api` → `http://localhost:8000`
  (`vite.config.js`).
- *Production edge*: the top-level nginx serves the SPA and proxies `/api` to the backend — the
  browser always calls same-origin `/api/v1/...`, so no CORS configuration is needed anywhere.
- *Container / Kubernetes*: the frontend image's **own** nginx also proxies `/api`, `/health`,
  `/metrics` and single-segment short codes to `backend:8000`. That makes `http://localhost:3000`
  a fully functional entry point by itself, and closes the Kubernetes Ingress gap (section 9.6) where the
  Ingress sends everything except `/api` to the frontend Service.

**Component/data flow:**

```
main.jsx → App.jsx
             ├── ShortenForm.jsx       state: url, redirectType, shortUrl, error
             │     POST /api/v1/shorten (createShortUrl() from api.js)
             │     renders the returned short_url as a link
             └── AnalyticsDashboard.jsx
                   input: short code → getStats() from api.js → GET /api/v1/stats/{code}
                   renders total clicks, created_at, and the recent clicks table
```

**Error rendering:** both components format failures through `getErrorMessage(err, fallback)` in
`api.js`, which flattens FastAPI's 422 `detail` arrays into a readable string. `ShortenForm` and
`AnalyticsDashboard` share the same client (`createShortUrl` / `getStats`), so the HTTP contract
lives in exactly one module.

### 9.5 CI/CD pipeline

```mermaid
flowchart LR
    subgraph PR / push
        L["lint<br/>ruff check backend/<br/>mypy backend/app/"] --> T["test<br/>pytest backend/tests/<br/>with postgres+redis services"]
        T --> B["build-and-push (main only)<br/>backend + frontend → GHCR"]
    end
```

- **lint** installs only `ruff` + `mypy`, so mypy analyzes the code with third-party imports
  unresolved (`--ignore-missing-imports` makes them `Any`). Ruff reads the repo-root
  `pyproject.toml` (rule selection + `B008` ignored for FastAPI's `Depends` defaults).
- **test** starts `postgres:16-alpine` (database `test_urlshortener`) and `redis:7-alpine` as
  service containers and runs pytest with `DATABASE_URL` / `DATABASE_READ_URL` / `REDIS_URL` env
  overrides — exactly what `tests/conftest.py` expects (it `setdefault`s only when env is absent).
- **build-and-push** runs only on `main`, logs into GHCR with `GITHUB_TOKEN`
  (`permissions: packages: write`) and pushes two images:
  `ghcr.io/<owner>/<repo>/backend:latest` and `.../frontend:latest` — the tags the Helm values expect.
- Reproduce locally: `ruff check backend/ && mypy backend/app/ --ignore-missing-imports` and
  `make test`.

### 9.6 Kubernetes / Helm

Object graph produced by the chart:

```mermaid
flowchart LR
    I["Ingress<br/>url-shortener.local"] -->|"/api"| BS["Service backend"]
    I -->|"/ (everything else)"| FS["Service frontend"]
    BS --> BD["Deployment backend (N replicas)"]
    FS --> FD["Deployment frontend"]
    BS -.->|"DATABASE_URL"| PS["Service postgres-primary"]
    BS -.->|"DATABASE_READ_URL"| PRS["Service postgres-replica"]
    PS --> PSS["StatefulSet postgres-primary<br/>PVC 5Gi"]
    PRS --> RSS["StatefulSet postgres-replica<br/>PVC 5Gi"]
    BD -.->|"REDIS_URL"| RS["Service redis"]
    RS --> RDep["Deployment redis"]
    subgraph config["Configuration"]
        CM["ConfigMap backend-config<br/>(BASE_URL, REDIS_URL, TTLs)"]
        SEC["Secret db-secret<br/>(passwords + full DB URLs)"]
        INIT["ConfigMap postgres-init<br/>(replication role + slot)"]
    end
    BD --> CM
    BD --> SEC
    PSS --> INIT
```

| `values.yaml` key | Used by | Notes |
|---|---|---|
| `namespace` | every resource | also used by `createNamespace` flag |
| `backend.image.*`, `backend.replicas`, `backend.port`, `backend.resources` | backend Deployment/Service | resources templated with `toYaml` |
| `backend.env.*` | `configmap.yaml` | non-secret env; secret URLs are composed in `secret.yaml` |
| `frontend.image.*`, `frontend.replicas`, `frontend.port` | frontend Deployment/Service | |
| `postgres.password` | `secret.yaml` + primary env | dev value; `--set` it in real use |
| `postgres.primary.storage` / `postgres.replica.storage` | `volumeClaimTemplates` | resized after the fact only via new PVCs |
| `redis.maxmemory` | redis args | cache-only pod, no PVC |
| `ingress.enabled`, `ingress.host` | Ingress | Traefik annotation for the `web` entrypoint |

**Why StatefulSets for Postgres and Deployments for everything else?** Databases need stable
identity and their own storage (`volumeClaimTemplates` → per-pod PVC). Backend/frontend/redis are
stateless (Redis is explicitly a cache): any pod restart is safe, so plain Deployments with rolling
updates are appropriate.

**Bootstrap behavior on k3s (identical logic to Compose, k8s flavored):**

- Primary: init ConfigMap mounts at `/docker-entrypoint-initdb.d`; only runs on a fresh PVC.
- Replica: its container command waits for `postgres-primary`, runs `pg_basebackup` against the
  Service, fixes ownership/permissions, then starts as a hot standby.
- Backend: each pod runs `alembic upgrade head` before uvicorn (compose parity).
  ⚠️ With `replicas: 2`, a rolling deploy can start two migrations concurrently; Alembic does not
  lock. In practice one wins and the other crash-loops into a clean retry, but the production-grade
  fix is a `pre-upgrade` Helm Hook Job or a Postgres advisory lock inside `env.py`.
- Probes: backend/frontend use HTTP probes (`/health`, `/`); Postgres uses `pg_isready`.
  Readiness is intentionally shallow (section 6.2).

**Short codes on k8s:** the Ingress routes `/api` straight to the backend; everything else (including
`/{code}`) goes to the frontend Service. The frontend image's nginx therefore proxies single-segment
alphanumeric paths (plus `/health` and `/metrics`) to `backend:8000` — the same routing the compose
edge performs. No CRDs or extra Ingress rules are needed.

**Namespace ownership:** `templates/namespace.yaml` is gated by `createNamespace` (default `false`).
If you `kubectl create namespace url-shortener` first (as the README does), Helm must not try to
adopt it — Helm 3 fails hard when a chart resource already exists without release ownership labels.
Either create it yourself *or* `--set createNamespace=true`, never both.

**Deploy / upgrade / rollback cheat sheet:**

```bash
kubectl create namespace url-shortener
helm install url-shortener ./helm/url-shortener -n url-shortener
helm upgrade url-shortener ./helm/url-shortener -n url-shortener --set backend.image.tag=v1.0.1
helm rollback url-shortener 1 -n url-shortener
kubectl get pods,svc -n url-shortener
kubectl logs -n url-shortener deployment/backend
kubectl get pvc -n url-shortener          # StatefulSet storage
```

---

## 10. Life of a short code (end-to-end timeline)

Follow code `G` (created as id 42) from cradle to cache expiry. Times are illustrative.

| t | Event | Where the truth lives |
|---|---|---|
| t0 | `POST /shorten` → id 42 → code `g`, committed on primary | primary: `urls` row; replica catches up ~ms later |
| t0 | Cache warmed: `url:g`, `url:g:type` (EX 3600) | Redis |
| t0+10 s | Click #1 → Redis hit → redirect 302 → click event + counter on primary | primary `click_events`/`urls`; replica catches up |
| t0+10 s | Stats call → served from replica (may miss click #1 for a few ms) | replica |
| t0+1 h | TTL expires (or LRU eviction under memory pressure) | Redis entry gone |
| t0+1 h | Click #N → cache miss → `SELECT` on replica → repopulate → redirect | replica → Redis |
| t + X | `is_active=false` set in DB | **cached entries still redirect until TTL/eviction** (known limitation) |
| any | Backend restarts | caches survive (Redis is a separate container); candidate stampede on hot keys |
| any | Replica stalls/goes down | primary retains WAL via `replica_slot_1`; replica resumes streaming |
| any | `docker compose down -v` | everything gone: volumes deleted, Redis empty, next `up` re-bootstraps |

Reading this table top-to-bottom is the best way to internalize the consistency model of the system.

---

## 11. Testing guide

### 11.1 Running the tests

Two supported ways:

```bash
# A) host venv (requires postgres + redis reachable at localhost per conftest defaults)
make test

# B) inside the already-built image (what this project's verification used)
docker compose run --rm -u root \
  -v "$PWD/backend/tests:/app/tests" -v "$PWD/backend/pytest.ini:/app/pytest.ini" \
  -e DATABASE_URL="postgresql+asyncpg://postgres:postgres@db:5432/test_urlshortener" \
  -e DATABASE_READ_URL="postgresql+asyncpg://postgres:postgres@db:5432/test_urlshortener" \
  -e REDIS_URL="redis://redis:6379/1" \
  backend sh -c "pip install -q pytest pytest-asyncio && pytest tests -v"
```

The test database `test_urlshortener` must exist (the README shows the one-liner).

### 11.2 Coverage map — what each test actually proves

| Test file | Proves |
|---|---|
| `test_shorten.py::TestBase62` | encoding math: zero, boundaries (61→`Z`, 62→`10`), round-trips, ≤ 7 chars for large ids |
| `test_shorten.py::TestShortenEndpoint` | 201 + payload shape; 302 redirect; 404 for unknown codes; 422 for invalid URL and invalid redirect type |
| `test_redirect.py` | 302 default & 301 opt-in with exact `Location`; cache warmed on create; cache repopulated after a simulated miss (keys deleted); unknown code 404; **inactive URL 404** (cache cleared first) |
| `test_analytics.py` | two clicks → `total_clicks == 2`, two `recent_clicks`; header capture (`user-agent`, `referer`, first `X-Forwarded-For` hop); stats 404 |

### 11.3 Fixture architecture (and the async test pitfalls it solves)

`tests/conftest.py`, in execution order:

1. **Path + env setup before imports.** `sys.path` gets `backend/` so `app` resolves regardless of
   pytest's rootdir; `os.environ.setdefault(...)` fills DB/Redis URLs only if the environment didn't
   (CI sets them explicitly). This must precede `import app.*` because `Settings` and the engines are
   created at import time.
2. **`reset_state` (autouse, per test).** Creates tables if missing
   (`Base.metadata.create_all`), truncates both tables with
   `TRUNCATE ... RESTART IDENTITY CASCADE` (so ids — and therefore expected codes — restart at 1),
   and flushes the Redis DB.
3. **`client`.** `httpx.AsyncClient` with `ASGITransport(app=app)`: requests are dispatched
   *in-process* through the ASGI interface. No sockets, no uvicorn, and (important) the lifespan
   handler does **not** run — cleanup is therefore the fixture's job.
4. **Teardown disposes engines and closes the Redis pool.** This is the subtle part: pytest-asyncio
   gives each test a fresh event loop by default, and `asyncpg`/redis connections are *bound to the
   loop that created them*. Reusing a pooled connection across loops raises
   `attached to a different loop`-class errors. Disposing forces fresh connections per test.

`pytest.ini` sets `asyncio_mode = auto` (with function-scoped default loops), and tests additionally
carry explicit `@pytest.mark.asyncio` / `pytest_asyncio.fixture` markers, so they also pass under
strict mode.

**A significant nuance:** the test schema comes from `Base.metadata.create_all`, **not** from
Alembic. A migration that diverges from the models would pass tests and fail in Docker. The Compose
stack runs `alembic upgrade head` at boot (a de-facto integration check); the rigorous fix is a
test fixture or CI step that applies migrations to a scratch database and compares schemas.

### 11.4 Adding a new test

```python
import pytest

@pytest.mark.asyncio
async def test_my_new_behavior(client):
    res = await client.post("/api/v1/shorten", json={"url": "https://example.com"})
    code = res.json()["short_code"]

    # ...exercise the behavior through HTTP...

    assert True
```

Rules of thumb: talk to the app only through `client` (black box), reset state is automatic, and
assert on status codes + payloads (not internal objects) so refactors don't break the suite.

---

## 12. Operations playbook

### 12.1 Everyday commands

| Task | Command |
|---|---|
| Start/rebuild everything | `make up` (or `docker compose up -d --build`) |
| Stop / wipe | `make down` / `make reset` (deletes volumes!) |
| Tail logs | `make logs` / `docker compose logs -f backend` |
| psql on primary / replica | `make psql` / `make psql-replica` |
| redis-cli | `make redis-cli` |
| Apply migrations | `make migrate` |
| Create a migration | `make revision NAME="add columns"` |
| Test / lint | `make test` / `make lint` |
| Helm install/upgrade | `make helm-install` |

### 12.2 Watch the system work (the best learning exercise)

```bash
# 1) follow application logs (watch the 302s and SQL errors)
docker compose logs -f backend

# 2) watch every Redis command live — create a URL and see the SETEX calls;
#    click a few times and see GETs. This is cache-aside, live.
docker compose exec redis redis-cli monitor

# 3) cache contents and TTL
docker compose exec redis redis-cli --scan --pattern 'url:*'
docker compose exec redis redis-cli TTL url:1

# 4) click data as it lands
docker exec pg-primary psql -U postgres -d urlshortener \
  -c "SELECT short_code, click_count FROM urls ORDER BY id;"
docker exec pg-primary psql -U postgres -d urlshortener \
  -c "SELECT id, url_id, ip_address, clicked_at FROM click_events ORDER BY id DESC LIMIT 5;"

# 5) verify replication lag
docker exec pg-replica psql -U postgres -d urlshortener \
  -c "SELECT now() - pg_last_xact_replay_timestamp() AS lag;"
```

### 12.3 Verification checklist (from the implementation plan, updated)

| # | Item | How to verify | Expected |
|---|---|---|---|
| 1 | Shorten returns 201 | `curl -si -X POST localhost/api/v1/shorten -H 'Content-Type: application/json' -d '{"url":"https://example.com"}'` | `201` + JSON with `short_url` |
| 2 | Redirect with correct status | `curl -si localhost/<code>` | `302` (+ `Location`); `redirect_type:301` → `301` |
| 3 | Click recorded | query `click_events` (above) | one row per redirect |
| 4 | Stats endpoint | `curl -s localhost/api/v1/stats/<code>` | `total_clicks`, `recent_clicks` |
| 5 | Cache populated | `redis-cli GET url:<code>` after create | original URL |
| 6 | Cache hit path | `redis-cli monitor` while clicking; or `urlshortener_redirects_total{cache_hit="true"}` | GETs without DB SELECTs |
| 7 | Replica streaming | `SELECT state FROM pg_stat_replication;` on primary | `streaming` |
| 8 | Metrics exposed | `curl -s localhost/metrics \| grep urlshortener` | counters + histogram |
| 9 | Health | `curl -s localhost/health` | `{"status":"ok"}` |
| 10 | Full stack up | `docker compose ps` | all services Up / healthy |
| 11 | CI commands pass | `ruff check backend/ && mypy backend/app/ --ignore-missing-imports` and tests | green |
| 12 | Helm chart valid | `helm lint helm/url-shortener && helm template url-shortener helm/url-shortener -n url-shortener` | 0 failures, 14 manifests |
| 13 | Frontend port 3000 works standalone | `curl -si -X POST localhost:3000/api/v1/shorten ...` and `curl -si localhost:3000/<code>` | `201` / `302` (not `405` / SPA HTML) |

---

## 13. Troubleshooting

| Symptom | Likely cause | Fix / check |
|---|---|---|
| `pg-replica` exits with `data directory ... has invalid permissions` | data dir owned/mode wrong after root-run basebackup | the entrypoint now does `chown` + `chmod 0700`; if you modified it, replicate that |
| Replica starts but never streams | `primary_slot_name` missing, wrong `pg_hba` rule, or slot absent | `grep primary_slot_name postgresql.auto.conf`; `SELECT * FROM pg_replication_slots;` on primary |
| WAL filling the primary's disk | an *inactive* replication slot holds WAL | check `pg_replication_slots.active`; drop unused slots |
| Reads return empty/404 right after create | replica lag, or a read went to the replica on a cold cache | check lag query; cache warm should eliminate it |
| `ERR Connection refused` from backend to Redis | wrong `REDIS_URL` (e.g. `localhost` inside a container) | use service names (`redis`), see compose env |
| nginx container crash-loops: `pcre2_compile() failed` | unquoted `{}` regex | quote the regex location |
| `alembic ... DuplicateTable` at boot | two instances ran migrations concurrently | let one finish/restart; use a Job/advisory lock in production |
| `500`s / timeouts from backend under load | DB pool exhausted | watch the `max_overflow` math (section 7.2); raise `max_connections` or reduce pool sizes |
| Browser shows the SPA, or a `405` on shorten, at `:3000` | a frontend image without the proxy rules is running (built before `frontend/nginx.conf` gained them) | rebuild: `docker compose build frontend && docker compose up -d --no-deps frontend` |
| Short codes 404 on Kubernetes | frontend can't resolve the `backend` Service at startup | confirm the `backend` Service exists; check `kubectl logs deploy/frontend` for `host not found in upstream` |
| Tests fail locally: `attached to a different loop` | pools reused across pytest event loops | teardown must dispose engines / close cache (conftest does) |
| Tests fail: `test_urlshortener does not exist` | test DB missing | `docker compose exec db psql -U postgres -c "CREATE DATABASE test_urlshortener;"` |
| `docker compose up` warning: `version is obsolete` | cosmetic | ignore, or delete the `version:` key |
| Helm install: `Namespace ... exists and cannot be imported` | namespace created manually while chart also defines it | keep `createNamespace=false` (default) **or** don't pre-create |

---

## 14. Configuration reference

All backend settings come from `app/config.py`; precedence: **env var > `.env` > code default**.

| Variable | Default | Consumed by | Purpose |
|---|---|---|---|
| `APP_NAME` | `URL Shortener` | FastAPI title | docs UI |
| `BASE_URL` | `http://localhost:8000` | `POST /shorten` response | the public base for `short_url` (browser-facing!) |
| `SHORT_CODE_LENGTH` | `7` | nothing yet | intended fixed code length (see section 15) |
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@db:5432/urlshortener` | write engine, Alembic | primary DSN |
| `DATABASE_READ_URL` | `...@db-replica:5432/...` | read engine | replica DSN |
| `REDIS_URL` | `redis://redis:6379/0` | `RedisCache` | cache DSN |
| `CACHE_TTL_SECONDS` | `3600` | create + redirect | cache entry lifetime |
| `DEFAULT_REDIRECT_STATUS` | `302` | redirect fallback | used when `:type` key is missing |

Compose-specific: `POSTGRES_PASSWORD` (default `postgres`) is interpolated into DB and backend env;
`docker-compose.prod.yml` also reads `BACKEND_IMAGE` / `FRONTEND_IMAGE`. Helm equivalents live in
`values.yaml` (section 9.6).

---

## 15. Known limitations & guided exercises

Everything below is a *deliberate* simplification. Each row names the exact hook point — work them
in order and you'll have touched every layer of the stack.

| # | Limitation | Where to hook | Suggested approach |
|---|---|---|---|
| 1 | Sequential codes are guessable (`/1`, `/2`, …), enabling enumeration | `services/shortener.py` | add a random offset to the id before encoding, or generate random 7-char codes with a uniqueness retry |
| 2 | `SHORT_CODE_LENGTH` is unused; codes aren't padded | `services/shortener.py` | left-pad to 7 (`zfill`-with-`0`) or enforce in the random-code strategy |
| 3 | `expires_at` is stored but never enforced | `api/routes.py` redirect query | add `expires_at IS NULL OR expires_at > now()`; add a cleanup job for expired rows |
| 4 | Deactivating a URL doesn't invalidate cached redirects (TTL up to 1 h) | wherever you add a deactivate endpoint | `DEL url:{code}` / `DEL url:{code}:type` on write |
| 5 | Cache stampede: N concurrent misses → N DB queries | `api/routes.py` + a lock key | singleflight with `SET lock:{key} NX EX 5` (implementation plan Layer 3 has reference code) |
| 6 | Analytics are awaited inline; they add redirect latency | `api/routes.py` | `BackgroundTasks`, a queue (Redis Stream/SQS), or batch inserts |
| 7 | `click_count` row is a hot spot under extreme click rates | `services/analytics.py` | sharded counters, append-only events + periodic aggregation |
| 8 | `/health` doesn't check dependencies; used as readiness | `main.py` | add `/ready` doing `SELECT 1` + `PING`, wire it to k8s readiness |
| 9 | Replica-lag 404 window on cold cache | `api/routes.py` | fall back to primary once when the replica 404s, or wait-on-LSN |
| 10 | No request IDs / tracing / structured logs | new middleware in `main.py` | propagate `X-Request-ID`, log JSON, plug OpenTelemetry |
| 11 | `/metrics` is unauthenticated | nginx / ingress | restrict by network policy or add basic auth |
| 12 | No URL de-duplication (same URL → many codes) | `api/routes.py` create flow | hash the URL, look up first, optionally unique index |
| 13 | `country` never populated | `services/analytics.py` | GeoIP lookup (e.g. MaxMind) on the XFF-derived IP |
| 14 | No rate limiting / abuse protection | nginx (`limit_req`) or middleware | token bucket per IP; protect `/shorten` first |
| 15 | No custom aliases (`/my-brand`) | new endpoint + schema | reserve-alias path with its own uniqueness rules |
| 16 | Edge routing is duplicated in two nginx configs (compose edge + frontend image) | `nginx/nginx.conf` + `frontend/nginx.conf` | keep them in sync, or generate both from one template |
| 17 | Migrations race across backend replicas in k8s | `helm/.../backend.yaml`, `alembic/env.py` | Helm hook Job or advisory lock |
| 18 | Tests use `create_all`, not migrations | `tests/conftest.py` / CI | apply `alembic upgrade head` against a scratch DB and diff schemas |
| 19 | In-memory pools are per-process; scaling out multiplies connections | `database.py` + deploy config | math in section 7.2; consider PgBouncer at scale |
| 20 | No Redis HA/persistence (cache-only) | `docker-compose.yml`, chart | acceptable here; Sentinel/Cluster if you ever store non-derivable data |

**Suggested learning order:** 3 → 4 → 5 → 6 → 1 → 8 → 10 → 16. Each reaches a different layer
(service, route, infra) and gives immediate observable feedback with the tools in section 12.

---

## 16. Glossary

| Term | Meaning in this project |
|---|---|
| **ASGI** | Async Server Gateway Interface — the contract between uvicorn and FastAPI |
| **Cache-aside** | App code manages the cache explicitly (read-through on miss, write on create) |
| **Cache stampede / thundering herd** | Many concurrent misses for the same expired key |
| **Connection pool** | Reusable set of DB/Redis connections per process (`pool_size`, `max_overflow`) |
| **Dependency injection (FastAPI)** | `Depends(...)` providers resolved per request, e.g. sessions and Redis |
| **`expire_on_commit`** | SQLAlchemy session option; `False` means attributes stay usable after commit |
| **Hot standby** | A replica serving read-only queries while replaying WAL |
| **LSN** | Log Sequence Number — WAL position; basis for measuring replication lag |
| **pg_basebackup** | PostgreSQL tool that makes a physical copy of a running primary |
| **pg_hba.conf** | Host-based auth rules; the init script adds the replication rule |
| **Physical replication slot** | Primary-side WAL retention guarantee for a specific replica |
| **Primary/replica** | One writable Postgres; one or more read-only streaming copies |
| **Prometheus counter/histogram** | Monotonic total / bucketed distribution metric types |
| **Read-your-writes** | Guarantee that a client sees its own write; here provided via cache warming |
| **Replication lag** | Delay between a primary commit and its appearance on the replica |
| **SERIAL / BIGSERIAL** | Postgres auto-increment integer (BIGINT + sequence) — the Base62 counter source |
| **Singleflight** | Deduplicating concurrent identical fetches so only one hits the origin |
| **TTL** | Time-to-live of a cache entry (here `CACHE_TTL_SECONDS`) |
| **WAL** | Write-Ahead Log — the stream that powers replication and crash recovery |
| **StatefulSet** | Kubernetes workload with stable identity + per-pod persistent volumes |
| **Readiness / liveness probe** | K8s checks for "send me traffic" vs "restart me" |
| **PVC** | PersistentVolumeClaim — storage requested by a StatefulSet template |
| **GHCR** | GitHub Container Registry; where CI pushes images |
| **Implementation plan** | `P1_URL_Shortener_Implementation_Guide.md`, the original 14-day build spec |

---

## Appendix A — Plan → code mapping

| Plan layer | Shipped as |
|---|---|
| Layer 1 (FastAPI core) | `backend/app/**` — all files in the guide's module map |
| Layer 1 `config.py` | `app/config.py` (env-varized) |
| Layer 2 (primary/replica) | `docker-compose.yml` (`db`, `db-replica`), `scripts/init-primary.sh` |
| Layer 3 (Redis cache) | `app/cache.py`, cache logic in `routes.py`; singleflight left as exercise #5 |
| Layer 4 (nginx) | `nginx/nginx.conf` (regex quoted — see Appendix B) |
| Layer 5 (React) | `frontend/**` — `ShortenForm.jsx` (plan's UI, wired to the shared `api.js` client); `AnalyticsDashboard.jsx` implemented |
| Layer 6 (Docker) | `backend/Dockerfile`, `frontend/Dockerfile`, `docker-compose*.yml` |
| Layer 7 (CI/CD) | `.github/workflows/ci.yml` (+ frontend image push) |
| Layer 8 (Helm/k3s) | `helm/url-shortener/**` (all templates, `createNamespace` flag added) |
| Layer 9 (Testing) | `backend/tests/**` (plan's tests plus redirect/analytics coverage) |
| Verification checklist | section 12.3 |

