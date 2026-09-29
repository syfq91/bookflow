# BookFlow

A small, self-hosted OPDS server: read-only filesystem → searchable index → OPDS catalog,
with on-demand EPUB optimization and single-user reading progression.

See [plan.md](plan.md) for the full implementation plan and
[ARCHITECTURE.md](ARCHITECTURE.md) for how the code is organized.

## Requirements

- [uv](https://docs.astral.sh/uv/)
- Python 3.14+ (managed automatically by uv)

## Setup

```bash
uv sync
```

## Configuration

All settings come from environment variables (defaults in parentheses):

| Variable                    | Default     | Description                                        |
| --------------------------- | ----------- | -------------------------------------------------- |
| `OPDS_HOST`                 | `0.0.0.0`   | Bind address                                       |
| `OPDS_PORT`                 | `8000`      | Bind port                                          |
| `OPDS_DATA_DIR`             | `./data`    | Data directory (SQLite + cache)                    |
| `OPDS_DATABASE_URL`         | derived     | Override the SQLAlchemy database URL               |
| `OPDS_SESSION_SECRET`       | *(generated)* | Session secret; when unset, random and persisted to `data/.session_secret` |
| `OPDS_ADMIN_USERNAME`       | `admin`     | Admin username                                     |
| `OPDS_ADMIN_PASSWORD`       | *(empty)*   | Admin password                                     |
| `OPDS_SESSION_COOKIE_SECURE` | `false`    | Set the session cookie `Secure` flag (behind HTTPS) |
| `OPDS_SCAN_EXTENSIONS`      | `.epub,.pdf,.cbz,.cbr,.mobi,.azw3` | Comma-separated extensions the scanner indexes |

## Admin UI

Set `OPDS_ADMIN_PASSWORD` and open `/admin/login`. Admin routes require a
session; the OPDS routes use HTTP Basic Auth with the same credentials.
Without `OPDS_ADMIN_PASSWORD` login attempts and the OPDS catalog return
`503` (the login form itself still renders).

The dashboard shows books, folders, library size, last scan, optimization
cache sizes and health badges. `/admin/health` runs per-component checks
(database, library folders, cache, epubkit, OPDS) and lists library
statistics: books by format, books per folder, progression and known
devices, cache contents, and scanner status with the last successful scan.

## Library folders

Register an absolute folder path under **Folders → Add Folder**. BookFlow only
reads from it: nothing is uploaded, renamed or modified. Registering a folder
indexes it immediately, and **Scan** re-indexes it:

- new and changed files are (re-)indexed with metadata extracted from EPUB/PDF
- deleted files are dropped from the index
- if a folder becomes unavailable, its index is kept and the scan is marked
  `error` until it returns
- concurrent scans of the same folder are rejected with `409`

## OPDS catalog

Point any OPDS client at `/opds` using the admin credentials (HTTP Basic).
All catalog responses are Atom/OPDS XML; errors under `/opds/*` are XML too.
A `401` returns the OPDS Authentication Document
(`application/opds-authentication+json`), which is also served publicly at
`/opds/authentication`.

| Endpoint              | Contents                                              |
| --------------------- | ----------------------------------------------------- |
| `/opds`               | Root navigation feed: All Books, Recent, Authors, Search, X3/X4 Catalogs |
| `/opds/books?page=N`  | All books, A→Z, 50 per page                           |
| `/opds/recent?page=N` | Books newest first                                    |
| `/opds/authors`       | Authors grouped by name, with book counts             |
| `/opds/authors/<name>`| That author's books                                   |
| `/opds/search?q=…`    | Search across title, authors, description, series, publisher, ISBN |
| `/opds/books/<id>`    | Feed for a single book                                |
| `/opds/download/<id>` | The book file itself (attachment)                     |
| `/opds/cover/<id>`    | Cover image extracted from the EPUB on demand (`404` when absent) |
| `/opds/x3`            | Xteink X3 catalog: every book, EPUBs acquired via the X3 download |
| `/opds/x3/books/<id>` | Single-book feed in the X3 catalog                    |
| `/opds/x3/download/<id>` | EPUB optimized for the Xteink X3 (on demand, cached) |
| `/opds/x4`            | Xteink X4 catalog: every book, EPUBs acquired via the X4 download |
| `/opds/x4/books/<id>` | Single-book feed in the X4 catalog                    |
| `/opds/x4/download/<id>` | EPUB optimized for the Xteink X4 (on demand, cached) |
| `/opds/publications/<id>/progression` | Reading position per OPDS Progression 1.0: `GET` reads, `PUT` updates (`application/opds-progression+json`); conflicts are `409` problem details |

The X3/X4 catalogs contain the full library; only EPUB entries use the
device's optimized download, while other formats fall back to the original
file so no acquisition link is ever broken.

Optimization runs only when an X3/X4 download is requested — never during
scans or startup — using a vendored copy of the
[epubkit](https://github.com/b1rdmania/epubkit) pipeline. Results are cached
under `data/cache/optimized/{x3,x4}/` and rebuilt automatically when the
source file changes. The original library files are never modified. The
dashboard's **Clear cache** action empties the cache and its index rows;
the next X3/X4 download regenerates the EPUB on demand.

## Development

Run the development server:

```bash
uv run flask --app bookflow.app run
```

Apply database migrations:

```bash
uv run alembic upgrade head
```

Create a new migration:

```bash
uv run alembic revision --autogenerate -m "description"
```

Run tests:

```bash
uv run pytest
```

Lint:

```bash
uv run ruff check .
```

## Production

```bash
uv run gunicorn --bind 0.0.0.0:8000 bookflow.app:app
```

Mount library directories read-only; only the data directory needs write access.

## Docker

```bash
cp .env.example .env    # optional; at least set OPDS_ADMIN_PASSWORD to log in
# set LIBRARY_DIR=/path/to/your/library in .env
docker compose up -d --build
```

`.env` is optional — without it the server starts with its defaults: the
session secret is auto-generated into the data volume, and everything works
except OPDS/login stay locked (`503`) until you set `OPDS_ADMIN_PASSWORD`.
Every `OPDS_*` value from `.env` is passed into the container.

- The library is bind-mounted **read-only** at `/library`
  (`LIBRARY_DIR`, default `./library`). Register `/library` — the
  *container* path — under **Folders** after first login; host paths are
  not visible inside the container.
- SQLite and the optimization cache live in the `bookflow-data` named
  volume; migrations run automatically at container start.
- The server binds `127.0.0.1:8000` by default. Set `BOOKFLOW_BIND=0.0.0.0`
  to expose it on your LAN and `BOOKFLOW_PORT` to change the host port.
- `/healthz` drives the compose healthcheck.
