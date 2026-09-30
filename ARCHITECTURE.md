# BookFlow — Architecture

How BookFlow is put together: layered design, module responsibilities,
request flows, and the invariants every change must preserve.

- Getting started and usage: [README.md](README.md)
- Configuration, endpoints, development: [REFERENCE.md](REFERENCE.md)

## 1. Overview

BookFlow turns existing filesystem folders into a self-hosted OPDS catalog
with on-demand device optimization and single-user reading progression.

```text
                 READ ONLY
              ┌──────────────┐
              │ Library      │
              │ filesystem   │
              └──────┬───────┘
                     │ scan (write only to SQLite)
                     ▼
              ┌──────────────┐
              │ SQLite index │
              └──────┬───────┘
        ┌────────────┼────────────┐
        ▼            ▼            ▼
      OPDS        OPDS X3      OPDS X4
        │            │            │
     original   on-demand     on-demand
        │        epubkit       epubkit
        ▼            ▼            ▼
     source       cache        cache
      EPUB         EPUB         EPUB
```

Three rules define the system:

1. **The filesystem is authoritative.** SQLite is only an index; books are
   stored as `(folder.path, relative_path)`, never absolute paths, and the
   library is never written to, renamed, or deleted.
2. **Optimization is strictly on-demand.** Only a request to
   `/opds/x3/download/<id>` or `/opds/x4/download/<id>` may invoke the
   epubkit pipeline — never scanning, startup, or background jobs.
3. **Single user, single surface.** One admin (session cookie for `/admin`,
   HTTP Basic for `/opds`), one progression per book regardless of which
   download URL the reader used.

## 2. Technology

| Concern      | Choice                                              |
| ------------ | --------------------------------------------------- |
| Web          | Flask 3 (application factory, blueprints)           |
| Templates    | Jinja2 (server-rendered admin UI)                   |
| Persistence  | SQLAlchemy 2 ORM + SQLite, Alembic migrations       |
| Passwords    | argon2-cffi (Argon2id, in-memory hash of the env password) |
| EPUB parse   | stdlib `zipfile` + `xml.etree`                      |
| PDF parse    | pypdf                                               |
| Optimization | vendored epubkit pipeline (Pillow, lxml, cssutils)  |
| XML feeds    | stdlib `xml.etree.ElementTree`                      |
| Prod server  | gunicorn                                            |
| Quality      | pytest, ruff (`E,F,I,UP,B`, line length 88)         |

No FastAPI, no Redis, no Celery, no second HTTP service.

## 3. Project layout

```text
bookflow/
├── pyproject.toml / uv.lock      deps, ruff + pytest config
├── alembic.ini / migrations/     schema migrations (0001_initial_schema)
├── README.md / ARCHITECTURE.md / REFERENCE.md / AGENTS.md
├── .env.example                  documented OPDS_* variables
│
├── src/bookflow/
│   ├── app.py                    application factory + / → /admin/ + /healthz
│   ├── config.py                 frozen Settings dataclass (env → values)
│   │
│   ├── database/
│   │   ├── database.py           engine, session_scope, PRAGMAs, reset
│   │   └── models.py             all four ORM tables
│   │
│   ├── auth/                     admin session authentication
│   │   ├── service.py            PasswordVerifier, LoginRateLimiter, CSRF
│   │   ├── routes.py             /admin/login, /admin/logout
│   │   └── decorators.py         @login_required
│   │
│   ├── admin/                    operational UI (no file management)
│   │   └── routes.py             dashboard, folders, library browser,
│   │                             scan, download, cache clear, health
│   │
│   ├── library/                  filesystem → index
│   │   ├── service.py            register/list/remove folders
│   │   ├── browse.py             clamped directory listing for the picker
│   │   ├── paths.py              book_file(): the single source of book paths
│   │   ├── scanner.py            walk, reconcile, scan statistics
│   │   └── metadata.py           EPUB/PDF metadata + on-demand cover
│   │
│   ├── opds/                     catalog surface
│   │   ├── routes.py             feeds, downloads, covers, auth document
│   │   ├── generator.py          Atom/OPDS XML builders
│   │   ├── auth.py               HTTP Basic + OPDS Authentication Document
│   │   └── progression.py        OPDS Progression 1.0 GET/PUT
│   │
│   ├── optimizer/                device-specific EPUB cache
│   │   ├── service.py            optimize_book(), clear_optimized_cache()
│   │   ├── locks.py              per (book_id, profile) threading locks
│   │   └── epubkit/              vendored pipeline (see NOTICE)
│   │
│   ├── health/                   diagnostics
│   │   └── service.py            component checks + library statistics
│   │
│   ├── templates/                layout, login, dashboard, folders,
│   │                             add_folder, browse_folders, library,
│   │                             health
│   └── static/style.css
│
└── tests/                        239 tests (see §11)
```

Deliberate deviations from the originally sketched layout: every ORM model
lives in `database/models.py` (there is no `library/models.py`), and the
epubkit integration is a package `optimizer/epubkit/` rather than a single
`epubkit.py` module.

## 4. Application bootstrap

`src/bookflow/app.py` — factory pattern, one function:

1. `Settings.from_env()` (or the settings passed in by tests).
2. Flask config: secret key (`OPDS_SESSION_SECRET`, otherwise a random one
   generated once and persisted to `data/.session_secret` so restarts and
   gunicorn workers keep sessions valid), `HttpOnly` + `SameSite=Lax`
   cookies, `Secure` from `OPDS_SESSION_COOKIE_SECURE`, `MAX_CONTENT_LENGTH`
   = 1 MB.
3. `settings.ensure_directories()` — create `data/` and the x3/x4 cache dirs.
4. `init_engine(settings)` — process-wide SQLAlchemy engine.
5. Extensions (kept on `app.extensions`, not globals):
   - `password_verifier` — Argon2id hash of the configured password,
     built once at startup, used by both admin login and OPDS Basic Auth.
   - `login_rate_limiter` — in-memory failure counter per username.
6. Jinja global `csrf_token` for form templates.
7. Register four blueprints: `auth`, `admin`, `opds`, `progression`.
8. `GET /` → `302` to `/admin/` (convenience redirect for the browser).
9. `GET /healthz` → `{"status": "ok"}` (public liveness probe).

A module-level `app = create_app()` exists for `gunicorn bookflow.app:app`.
Tests call `create_app(test_settings)` directly after `reset_engine()`.

## 5. Configuration

`config.py` defines a frozen `Settings` dataclass; every field maps to one
environment variable with a default (see `.env.example`):

| Variable | Default | Notes |
| -------- | ------- | ----- |
| `OPDS_HOST` / `OPDS_PORT` | `0.0.0.0` / `8000` | bind address |
| `OPDS_DATA_DIR` | `./data` | SQLite + cache root (only writable dir) |
| `OPDS_DATABASE_URL` | derived from data dir | override for tests/production |
| `OPDS_SESSION_SECRET` | *(auto-generated)* | unset → random secret persisted to `data/.session_secret` (0600), shared by all workers |
| `OPDS_ADMIN_USERNAME` / `OPDS_ADMIN_PASSWORD` | `admin` / *(empty)* | empty password → OPDS returns `503` |
| `OPDS_SESSION_COOKIE_SECURE` | `false` | enable behind HTTPS |
| `OPDS_SCAN_EXTENSIONS` | `.epub,.pdf,.cbz,.cbr,.mobi,.azw3` | scanner scope |
| `OPDS_BROWSE_ROOT` | `/` | root the admin folder browser is clamped to (typed paths unaffected) |

Derived paths: `cache_dir = data/cache/optimized`,
`x3_cache_dir`/`x4_cache_dir` beneath it. No password is ever stored —
the env value is Argon2id-hashed in memory at startup.

## 6. Data layer

`database/database.py` owns a **process-wide engine**:

- `init_engine(settings)` builds it; `reset_engine()` disposes it (tests).
- `session_scope()` is the only way code opens a session: commit on success,
  rollback on exception, always close. Call sites are short and sync.
- SQLite specifics: `check_same_thread=False` (Flask threads) and a
  `connect` listener that runs `PRAGMA foreign_keys=ON` so the schema's
  `ON DELETE CASCADE` actually applies.
- Schema changes go through Alembic (`migrations/versions/`); models are
  verified against migrations in `tests/test_database.py`.

### Schema (migration `0001_initial_schema`)

```text
library_folders                     books
├── id (PK)                         ├── id (PK)
├── path (unique, 4096)             ├── folder_id (FK → library_folders, CASCADE)
├── name, enabled                   ├── relative_path (unique per folder)
├── created_at, updated_at          ├── title, authors, publisher, language,
└── last_scan_at/duration/…           isbn, description, series, series_index
                                    ├── file_format, file_size, file_modified_at
progressions (1:1 with books)       └── created_at, updated_at
├── id (PK), book_id (UNIQUE, FK)
├── progression (float 0..1)        optimized_books
├── modified (datetime)             ├── id (PK)
├── device_id, device_name          ├── book_id (FK, CASCADE)
├── title, references (JSON)        ├── profile ('x3' | 'x4')
└── created_at, updated_at          ├── source_mtime, source_size   ← validity
                                    ├── optimized_path, optimized_size
                                    └── UNIQUE (book_id, profile)
```

`books.folder_id + relative_path` is unique; absolute paths are never
stored. `optimized_books` rows are the index of cache files — the cache
directory itself is treated as rebuildable.

## 7. Module responsibilities

### 7.1 `auth/` — admin session authentication

- `PasswordVerifier(username, password)` — hashes the configured password
  with Argon2id at construction; `verify()` compares a submitted password.
  No plaintext is persisted or logged.
- `LoginRateLimiter` — in-memory failure counts per username with a time
  window; repeated failures are rejected until the window expires or a
  successful login resets the counter.
- CSRF: `ensure_csrf_token()` mints a session token (also exposed to Jinja
  as `csrf_token()`); `validate_csrf()` compares it to the submitted form
  value. `safe_next_target()` restricts post-login redirects.
- `@login_required` guards every admin view; unauthenticated requests
  redirect to `/admin/login` with a validated `?next=`.
- `auth/routes.py`: `GET/POST /admin/login`, `POST /admin/logout`.

### 7.2 `library/` — filesystem → index

- `service.add_folder()` validates a path (absolute, exists, directory,
  readable, normalized via `resolve()`, not nested inside another registered
  folder) and inserts a `library_folders` row. `remove_folder()` deletes only
  the row — files on disk are untouched.
- `browse.browse_directory(raw_path, root)` lists one directory for the
  admin picker: read-only, clamped to `OPDS_BROWSE_ROOT` with
  `resolve()` + `is_relative_to()`, directories only, capped at 500
  entries. Selection just prefills the add-folder form; registration
  still goes through `add_folder()`.
- `paths.book_file(book_id)` turns a book row into an on-disk path:
  root containment via `resolve()` + `is_relative_to()`, `404` on
  anything outside the root. Shared by the OPDS and admin blueprints.
- `scanner.scan_folder(folder_id, extensions)`:
  - a per-folder `threading.Lock` makes concurrent scans of the same folder
    fail fast with `ScanInProgress` → HTTP `409`;
  - recursively walks the root (symlinks skipped), filters by extension,
    and reads `size` + `mtime` for change detection;
  - `_reconcile()` upserts new/changed books and drops deleted ones;
  - **missing/unreadable root**: keeps the existing index and records
    `last_scan_status='error'` + `last_scan_error` so an unmounted disk can
    never wipe the catalog;
  - per-file errors are recorded without aborting the scan; produces a
    `ScanResult` (added/updated/removed/unchanged/errors/duration).
  - Never imports the optimizer.
- `metadata.extract_metadata()` — dispatch on suffix:
  - EPUB: locate OPF via the container, parse with ElementTree: title,
    creators, language, publisher, ISBN (normalized), description, series +
    index; filename fallback when anything is missing or the file is
    corrupt.
  - PDF: embedded `pypdf` document info, same fallback.
  - `extract_cover()` resolves the EPUB cover item to raw bytes — used
    on demand by the OPDS cover endpoint, not stored in the index.

### 7.3 `opds/` — the catalog

- `routes.py` — all catalog endpoints (table in §8). Shared helpers:
  - `before_request` applies HTTP Basic to everything under `/opds` except
    the public `/opds/authentication` document;
  - book paths come from `library.paths.book_file()` (§7.2) — the **only**
    function that turns a book row into a path, so URL input can never
    escape the library;
  - blueprint-scoped `@bp.errorhandler(404/403/500)` render XML errors —
    scoped to the blueprint so admin pages keep Flask's HTML error pages;
  - pagination: 50 entries per page with a `rel="next"` link.
- `generator.py` — pure builders over ElementTree: `navigation_feed()`,
  `acquisition_feed()`, `book_entry()`. Each entry carries acquisition,
  thumbnail/image, and progression links. With a device `profile`,
  `_acquisition()` points EPUBs at `/opds/{x3,x4}/download/<id>` and lets
  other formats fall back to the original download so no link is broken.
- `auth.py` — `authenticate()` verifies the Basic header against the shared
  `PasswordVerifier`; `unauthorized()` returns the OPDS Authentication
  Document (`application/opds-authentication+json`) as the body **and** a
  `Link: rel="http://opds-spec.org/auth/document"` header, plus
  `WWW-Authenticate: Basic`.
- `progression.py` — OPDS Progression 1.0:
  - `GET` returns the stored document, or an empty valid payload when none
    exists;
  - `PUT` validates media type → JSON → required fields (`modified`,
    `device`, `progression`) → range `0..1` → timestamps (naive values are
    interpreted as UTC; serialization is microsecond-preserving so a
    replayed PUT stays equal), then applies *older-than-stored* → `409`,
    *equal* → `200` replay, *newer* → store and return `201`/`200`;
  - errors are RFC 7807 `application/problem+json` with registry types
    (`…#progression-invalid-payload`, `…#progression-date`, `about:blank`
    for 404);
  - one `progressions` row per book — progression belongs to the logical
    book, never to a cache profile.

### 7.4 `optimizer/` — on-demand device EPUBs

- `service.optimize_book(book_id, profile, source)` — the whole §16 flow:
  1. `source.stat()` → `source_mtime` (ms) + `source_size`;
  2. acquire `profile_lock(book_id, profile)` (module-level `threading.Lock`
     registry keyed by the pair — concurrent clients for the same book and
     profile serialize; different profiles/books run in parallel);
  3. `_cache_is_valid()` — cache file exists **and** the `optimized_books`
     row matches current mtime/size and the recorded `optimized_size`
     equals the file on disk (a missing file simply reports invalid);
  4. on miss: `_generate()` runs `process_epub(source → tmp)` into
     `cache/<profile>/.tmp/<uuid>.epub`, verifies success + non-empty
     output, then `os.replace()` atomically moves it to
     `<book_id>.epub`; failures delete the temp file and raise
     `OptimizationError` (→ HTTP `500` XML), never touching the source;
  5. `_record()` upserts the `optimized_books` row.
- `service.clear_optimized_cache()` — §28 admin action: empties both cache
  directories (including `.tmp`) and deletes all index rows; returns
  `(files, rows)`. Sources are never touched; the next download regenerates.
- `epubkit/` — vendored copy of
  [b1rdmania/epubkit](https://github.com/b1rdmania/epubkit) (MIT, see
  `NOTICE`): `epub_processor.process_epub(input, output, options, …)` with
  `ProcessingOptions(device="x3"|"x4")` profiles. FastAPI/web app and tests
  from upstream were deliberately not vendored; the package is excluded
  from ruff rules in `pyproject.toml`.

### 7.5 `health/` — diagnostics

- `run_checks(settings)` → five `HealthCheck(component, status, detail)`
  results: **Database** (real table query — catches missing migrations),
  **Library** (each registered folder exists/is a directory/is readable),
  **Cache** (dir exists + writable), **epubkit** (pipeline importable),
  **OPDS** (catalog routes registered). Status is `ok | warn | error`.
- `library_statistics()` — database-only aggregates (explicitly no
  filesystem walks): totals, by format, per folder, cache rows/sizes per
  profile, progression + known devices, last successful scan, scan errors.

### 7.6 `admin/` — operational UI

- Blueprint-wide `before_request` rejects any `POST/PUT/PATCH/DELETE`
  without a valid CSRF token (`400`) *before* views run.
- Views: dashboard (`_dashboard_stats()` + health badges, degrades to a
  readable message when migrations are missing), folder CRUD + scan,
  the FTP-style library browser (folder cards on `/admin/folders` link to
  per-folder directory levels from the index, files link to an
  original-file download), `POST /admin/cache/clear` (flash + redirect),
  `/admin/health`.
- There is deliberately **no** upload, delete, rename, move, or metadata
  editing — the UI is a control surface, not a file manager.

## 8. HTTP surface

### Admin & auth

| Method | Path | Notes |
| ------ | ---- | ----- |
| GET | `/` | `302` redirect to `/admin/` |
| GET | `/healthz` | public liveness, JSON |
| GET/POST | `/admin/login` | CSRF on POST, rate limited |
| POST | `/admin/logout` | CSRF |
| GET | `/admin/` | dashboard |
| GET | `/admin/folders`, `/admin/folders/new` | library root list + add form (query `path` prefills) |
| GET | `/admin/folders/browse` | server-side folder browser, clamped to `OPDS_BROWSE_ROOT` |
| POST | `/admin/folders` | register + immediate scan |
| POST | `/admin/folders/<id>/scan` | re-scan (`409` if running) |
| POST | `/admin/folders/<id>/delete` | drops index rows only |
| POST | `/admin/cache/clear` | §28 optimization-cache reset |
| GET | `/admin/library` | `302` → `/admin/folders` (pre-merge URL) |
| GET | `/admin/library/<id>` | one directory level (`?path=` relative, from the index) |
| GET | `/admin/books/<id>/download` | original file, `404` outside root |
| GET | `/admin/health` | component checks + statistics |

All `/admin/*` (except login) require the session cookie.

### OPDS (HTTP Basic on every route except the auth document)

| Path | Contents |
| ---- | -------- |
| `/opds` | root navigation: All Books, Recent, Authors, Folders, Search, X3/X4 Catalogs |
| `/opds/books`, `/opds/recent`, `/opds/search?q=` | acquisition feeds, 50/page |
| `/opds/authors`, `/opds/authors/<name>` | grouped navigation + author feed |
| `/opds/folders`, `/opds/folders/<id>?path=` | folder hierarchy: registered folders → one directory level (subfolders + books), derived from `relative_path`, never from the filesystem |
| `/opds/books/<id>`, `/opds/download/<id>`, `/opds/cover/<id>` | single book, original file, extracted cover |
| `/opds/x3`, `/opds/x3/books/<id>`, `/opds/x3/download/<id>`, `/opds/x3/folders`, `/opds/x3/folders/<id>` | X3 catalog (same for `x4`) |
| `/opds/authentication` | public OPDS Authentication Document |
| `/opds/publications/<id>/progression` | `GET`/`PUT`, `application/opds-progression+json` |

Unknown `/opds/*` paths return XML `404` (catch-all rule), never HTML.

## 9. Representative request flows

### Catalog browse

```text
GET /opds/books  →  before_request: Basic auth (401 → auth document)
                 →  session_scope: page query (ilike-escaped search, LIMIT/OFFSET)
                 →  acquisition_feed(): ElementTree → bytes
                 →  Response(application/atom+xml;profile=opds-catalog;kind=acquisition)
```

### Optimized download (the only path into epubkit)

```text
GET /opds/x4/download/12
  → auth → book_file(12)             (DB lookup + root-resolve guard)
  → suffix must be .epub             (else 404)
  → optimize_book(12, "x4", src)
       lock(12, x4)
       cache valid?  ── yes ──► send_file(cache/x4/12.epub)
       no → process_epub → .tmp/<uuid>.epub → os.replace → upsert row
       unlock
  → send_file(..., as_attachment)
```

### Progression update

```text
PUT /opds/publications/12/progression  (application/opds-progression+json)
  → auth → parse/validate (type, fields, range, timestamps)
  → compare with stored.modified:  older → 409 problem+json
                                   equal → 200 (idempotent replay)
                                   newer → upsert → 201 (create) / 200 (update)
```

### Admin scan

```text
POST /admin/folders/<id>/scan (session + CSRF)
  → folder lock: busy → flash + 409
  → walk + metadata extract + reconcile (all writes inside session_scope)
  → scan statistics recorded on the folder row → redirect + flash
```

## 10. Concurrency, errors, security

**Concurrency.** One sync worker model (Flask dev server / gunicorn sync
workers); SQLAlchemy sessions are per-`session_scope` and short-lived.
Two in-process `threading.Lock` registries protect shared work:
per-folder scan locks (`library/scanner.py`) and per-`(book_id, profile)`
optimization locks (`optimizer/locks.py`); a waiting optimizer re-checks
the cache after acquiring the lock. SQLite is configured for multiple
threads (`check_same_thread=False` + WAL-free default journal).

**Error handling by surface.**

| Surface | Style |
| ------- | ----- |
| `/opds/*` | blueprint XML handlers (`application/xml`, 404/403/500) |
| progression | RFC 7807 `application/problem+json` (400/404/409) |
| `/admin/*` | Flask's default HTML error pages; forms show inline/flash messages |
| auth failures | `401` + auth document (OPDS) or rendered login (admin) |

**Security invariants** (enforced in code, tested in `test_optimizer.py` /
`test_opds.py` / `test_auth.py`):

- one admin credential, Argon2id-hashed in memory, never logged or stored;
- session cookie: HttpOnly, SameSite=Lax, `Secure` opt-in; CSRF on every
  state-changing admin request; login rate limiting;
- downloads only for books that exist in the index, and only inside their
  folder root (`resolve()` + `is_relative_to`);
- output paths are derived from `book_id` + profile — never from raw URL
  input; temp files are random UUIDs inside the cache dir;
- the library tree is read-only: scans and optimization only ever write to
  SQLite and `data/cache/`;
- 1 MB request body cap.

## 11. Testing

`tests/` (239 tests, `uv run pytest`):

- `conftest.py` — temp `Settings` (fresh data dir + SQLite per test) and a
  bare app fixture; every suite that needs migrations shadows these with an
  `app` fixture that runs `alembic upgrade head`, then `reset_engine()`
  on teardown.
- `factories.py` — `make_epub()` (valid container/OPF, optional cover),
  `make_pdf()`, `csrf_token(client)`.
- Suite ↔ subsystem: `test_auth`, `test_library`, `test_scanner`,
  `test_opds`, `test_progression`, `test_optimizer`, `test_health`,
  `test_device_catalogs`, plus `test_app`/`test_config`/`test_database`.
- Recurring patterns: ElementTree helpers to parse feeds and group links by
  `rel`; session helpers (`_login`, Basic `_headers`, `_get`/`_put`);
  direct ORM seeding for rows that don't need real files; monkeypatched
  counting wrappers to prove cache hits skip epubkit.

## 12. Development & deployment

```bash
uv sync                                # install (incl. gunicorn)
uv run flask --app bookflow.app run --port 8000   # dev server
uv run alembic upgrade head            # migrations
uv run ruff check .                    # lint
uv run pytest -q                       # tests
uv run gunicorn --bind 0.0.0.0:8000 bookflow.app:app   # production
```

Mount library directories **read-only**; only `data/` (SQLite + cache)
needs write access. The cache directory is disposable: clearing it (UI
button or `rm -rf`) costs only regeneration time on the next device
download.

For containers, `Dockerfile` + `docker-compose.yml` run the same stack,
either from the published `ghcr.io/syfq91/bookflow:latest` image or a
local two-stage build (uv sync → slim runtime, non-root user): a read-only
library bind mount at `/library` (`./library` by default), a named volume
for `/app/data`, automatic `alembic upgrade head` at start, and a
`/healthz` healthcheck. Settings are literals in `docker-compose.yml` —
`OPDS_ADMIN_PASSWORD` is the one to set, everything else is commented out
and falls back to the app default; `.env` is not read for interpolation.
