# AGENTS.md

Instructions for AI agents (and humans) working in this repository.

BookFlow is a small self-hosted OPDS server: read-only filesystem →
SQLite index → OPDS catalogs, plus on-demand X3/X4 EPUB optimization and
single-user reading progression. The plan is complete (milestones M1–M8 +
optional items); expect maintenance, bug fixes, and small features rather
than greenfield work.

- **How it fits together:** [ARCHITECTURE.md](ARCHITECTURE.md) (start here)
- **How to run it:** [README.md](README.md)

## Commands

```bash
uv sync                                   # install deps
uv run ruff check .                       # lint (gate before every commit)
uv run pytest -q                          # full suite: 198 tests, ~2-3 min
uv run pytest -q tests/test_opds.py       # single file (seconds)
uv run flask --app bookflow.app run       # dev server on :8000
uv run alembic upgrade head               # apply migrations
uv run alembic revision --autogenerate -m "..."
uv run gunicorn --bind 0.0.0.0:8000 bookflow.app:app
docker compose up -d --build          # containerized run (Dockerfile)
```

Verify before committing: `uv run ruff check . && uv run pytest -q`.
Both must pass. Do not commit unless explicitly asked.

### Smoke-testing a live server

Tests cover behavior; for end-to-end checks run a detached instance on a
scratch data dir:

```bash
export OPDS_DATA_DIR=/tmp/bf-smoke OPDS_ADMIN_PASSWORD=smokepass \
       OPDS_DATABASE_URL="sqlite+pysqlite:////tmp/bf-smoke/live.db" \
       OPDS_PORT=8767
setsid nohup uv run flask --app bookflow.app run --host 127.0.0.1 --port 8767 \
  > /tmp/bf-smoke.log 2>&1 < /dev/null &
# kill later with the regex-bracket trick (plain pkill kills your own shell line):
pkill -f "[f]lask --app bookflow.app run"
```

- `uv run alembic upgrade head` first if the scratch DB is new.
- Login for admin pages: scrape `csrf_token` with
  `grep -o 'name="csrf_token" value="[^"]*"' | head -1` (it appears twice
  per page — forms + meta), then POST `/admin/login` with a cookie jar.
- OPDS uses Basic auth: `-u admin:<password>`.

## Code conventions

- **Toolchain:** Python ≥3.14, uv, Flask 3, SQLAlchemy 2, Alembic, SQLite.
  Ruff rules `E,F,I,UP,B`, line length 88, `target-version = "py314"`.
- **Style:** `from __future__ import annotations` at the top of every
  module; type-hint public functions; docstrings on modules/classes/public
  functions; inline comments are rare — prefer clear code over narration.
- **Config:** a frozen `Settings` dataclass (`config.py`); never read
  `os.environ` outside it. Everything is reachable as
  `current_app.config["SETTINGS"]`.
- **Database:** open sessions only via `session_scope()` (commit/rollback/
  close handled). SQLAlchemy 2 style: `Mapped[...]`/`mapped_column`,
  `select()` — no legacy `Query`. Keep sessions short; never hold one across
  I/O or renders.
- **Web:** blueprints only (auth, admin, opds, progression); register new
  ones in `create_app`. OPDS errors must be blueprint-scoped
  (`@bp.errorhandler`) so admin pages keep HTML error pages. Every
  state-changing admin route is automatically CSRF-checked by the
  blueprint's `before_request` — views just read the form.
- **Templates:** server-rendered Jinja extending `layout.html`; CSRF token
  via the global `csrf_token()`; existing badge/detail/grid CSS classes in
  `static/style.css` (add `badge-warn`-style variants there if needed).
- **Tests:** mirror existing files. Fixture chain is
  `settings` (conftest, temp dir + DB) → shadowed `app` fixture that runs
  `alembic upgrade head` after `create_app(...)` → `client`; always
  `reset_engine()` on teardown. Reuse `tests/factories.py`
  (`make_epub`, `make_pdf`, `csrf_token`) instead of hand-rolling files.
  Seed rows directly via ORM when no real file is needed.

## Invariants — never break these

1. **The library is read-only.** No code may write, rename, or delete
   anything under a registered folder. All writes go to SQLite and
   `data/cache/`.
2. **Optimization is on-demand only.** epubkit runs exclusively inside
   `optimize_book()`, reached only from `/opds/x3|/x4/download/<id>`.
   Never call it from scanning, startup, routes, or background tasks.
3. **Paths come from the DB, not the URL.** `_book_file()` in
   `opds/routes.py` is the single source of book paths and enforces
   root containment via `resolve()` + `is_relative_to`. Don't construct
   filesystem paths from user input anywhere else.
4. **Cache is disposable, index must agree.** Cache files live at
   `data/cache/optimized/{x3,x4}/<book_id>.epub`, written through
   `.tmp/<uuid>.epub` + `os.replace()`, and validated against the
   `optimized_books` row (`source_mtime`, `source_size`,
   `optimized_size`). Writes to file and row happen together; readers
   treat any mismatch as a miss.
5. **Progression is per logical book**, one row, shared across original
   and X3/X4 downloads. Timestamps: accept naive as UTC, serialize with
   `Z` and microsecond precision (replays must compare equal).
6. **One admin, one credential.** Argon2id in memory, session auth for
   `/admin`, HTTP Basic for `/opds`, OPDS 401 body is the Authentication
   Document. Never log passwords, cookies, or auth headers.
7. **Vendored epubkit is third-party code** (`optimizer/epubkit/`, MIT —
   keep `NOTICE`, keep the ruff per-file-ignores). Avoid editing it;
   upstream it separately if the pipeline itself must change.
8. **Non-goals:** no multi-user or roles, uploads, book delete/rename/move
   or metadata/cover editing, cloud storage, Calibre/Elasticsearch/Redis/
   Celery/K8s/microservices, a separate epubkit HTTP server,
   background or scheduled optimization, pre-generated X3/X4 files, and no
   separate progression per device or per optimization profile. Keep the
   application small.

## Known gotchas

- `optimizer/epubkit/` (package) shadows nothing now — do not recreate a
  sibling `optimizer/epubkit.py`; Python would silently prefer the package.
- `tests/conftest.py` sets `OPDS_DATA_DIR` at import time so the
  module-level `app = create_app()` never writes into the repo; don't
  import `bookflow.app` in tests without the fixture chain (its engine
  would leak — that's what `reset_engine()` is for).
- OPDS feeds paginate at 50; device feeds (`/opds/x3`, `/opds/x4`) list the
  whole library with EPUB acquisitions rewritten per profile and other
  formats falling back to `/opds/download/<id>` — keep that fallback when
  adding formats.
- Search uses SQL `ilike` with escaped `% _ \` — reuse the existing
  escaping helper if you touch queries.
- Flask's default error pages are intentional for `/admin`; only the OPDS
  and progression blueprints render structured errors.
- The session secret is auto-generated and persisted to
  `data/.session_secret` when `OPDS_SESSION_SECRET` is unset (see
  `resolve_session_secret()`); the old shared dev literal no longer exists.
  Never log the file's contents.

## Git

- Direct commits to `main`, pushed to `github.com:syfq91/bookflow`.
- Message style: short imperative summary, optionally scoped
  (`Milestone 6: epubkit integration`, `Add gunicorn dependency and
  .env.example`).
- Stage only intended files; run the verify command above first; never
  commit secrets (test passwords in fixtures are fine — they aren't real).
