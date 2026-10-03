# BookFlow — Reference

Configuration, HTTP endpoints, and development/production details.
[README.md](README.md) covers getting started and everyday use;
[ARCHITECTURE.md](ARCHITECTURE.md) explains how the code is organized.

## Configuration

All settings come from environment variables (defaults in parentheses):

| Variable                    | Default     | Description                                        |
| --------------------------- | ----------- | -------------------------------------------------- |
| `OPDS_DATA_DIR`             | `./data`    | Data directory (SQLite + cache)                    |
| `OPDS_DATABASE_URL`         | derived     | Override the SQLAlchemy database URL               |
| `OPDS_SESSION_SECRET`       | *(generated)* | Session secret; when unset, random and persisted to `data/.session_secret` |
| `OPDS_ADMIN_USERNAME`       | `admin`     | Admin username                                     |
| `OPDS_ADMIN_PASSWORD`       | *(empty)*   | Admin password                                     |
| `OPDS_SESSION_COOKIE_SECURE` | `false`    | Set the session cookie `Secure` flag (behind HTTPS) |
| `OPDS_SCAN_EXTENSIONS`      | `.epub,.pdf,.cbz,.cbr,.mobi,.azw3` | Comma-separated extensions the scanner indexes |
| `OPDS_BROWSE_ROOT`          | `/`         | Root the admin folder browser (`Browse…` on Add Folder) is clamped to; typed paths are unaffected |
| `OPDS_TRUSTED_PROXY_HOPS`   | `0`         | Reverse proxies in front of BookFlow; `> 0` takes the client address from `X-Forwarded-For` (see below) |
| `OPDS_LOG_LEVEL`            | `INFO`      | Root logger level: `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` (unusable values fall back to `INFO`) |

`.env.example` documents the same variables for a bare-metal install.

**Logging.** `create_app` applies `OPDS_LOG_LEVEL` to the root logger and
installs a stderr handler when the process has none, so scan summaries,
cache-clear notices and metadata debug lines reach the output (gunicorn
collects them in the worker error log). `WARNING` silences the INFO
events; `DEBUG` adds the per-file metadata messages.

## Admin UI

Set `OPDS_ADMIN_PASSWORD` and open `/admin/login` (the site root `/`
redirects to `/admin/`). Admin routes require a session; the OPDS routes
use HTTP Basic Auth with the same credentials. Without
`OPDS_ADMIN_PASSWORD` login attempts and the OPDS catalog return `503`
(the login form itself still renders). A wrong username or password
returns `401` with the login form and an inline error — `429` (rate
limit) and `503` (unset password) are the only other failure codes.

**Login rate limiting.** Five failed logins for the same client address
and username inside ten minutes answer `429` until the window slides past
them; a successful login clears the count. Counters are in memory, so
they reset on restart and are **per process** — with `GUNICORN_WORKERS=2`
each worker keeps its own, so the effective threshold is per worker. The
key is `request.remote_addr`. Behind a reverse proxy every client shares
the proxy's address, which would let one attacker lock everyone out: set
`OPDS_TRUSTED_PROXY_HOPS` to the number of proxies in front of BookFlow
(`1` for a single nginx/Caddy) so the address is taken from the last
`X-Forwarded-For` entry that proxy added. Leave it at the default `0`
when BookFlow is reachable directly — clients can forge that header, and
only enable it when every direct path to the app is blocked by the
proxy.

The dashboard shows books, folders, library size, last scan, optimization
cache sizes and health badges. `/admin/health` runs per-component checks
(database, library folders, cache, epubkit, OPDS) and lists library
statistics: books by format, books per folder, progression and known
devices, cache contents, and scanner status with the last successful scan.

**Library** (`/admin/folders`) is the single entry point to the
collection: it lists the registered folders, and each folder name links
into a plain FTP-style browser over the index — click down through folder
levels, then click a file to download the original
(`/admin/books/<id>/download`). Levels with more than 50 files paginate
via `?page=N`. Nothing is ever written to the library.
The old `/admin/library` URL redirects here.

### Library folders

Register an absolute folder path under **Library → Add Folder**. Type the
path or use **Browse…** to navigate the server's folders and select one
(browsing starts at `/`, or at `OPDS_BROWSE_ROOT` when set). BookFlow only
reads from it: nothing is uploaded, renamed or modified. Registering a
folder indexes it immediately, and **Scan** re-indexes it:

- new and changed files are (re-)indexed with metadata extracted from EPUB/PDF
- deleted files are dropped from the index
- if a folder becomes unavailable, its index is kept and the scan is marked
  `error` until it returns
- concurrent scans of the same folder are rejected with `409`

## OPDS catalog

Point any OPDS client at `/opds` using the admin credentials (HTTP Basic).
All catalog responses are Atom/OPDS XML and so are their errors, for every
status — including `405` from a wrong method and `500` from a server
fault. The progression endpoints under `/opds/publications` answer with
RFC 7807 problem documents instead (see below).
A `401` returns the OPDS Authentication Document
(`application/opds-authentication+json`), which is also served publicly at
`/opds/authentication`.

Authentication runs before anything else under the catalog prefixes
(`/opds`, `/opdsx3`, `/opdsx4`), so a request that fails routing — an
unknown path (`404`) or a wrong method (`405`) — is `401` with the
Authentication Document when the credentials are missing or wrong, and
only then answers with its own error document. The same applies to
`/opds/publications`. Nothing else under those prefixes is public.

| Endpoint              | Contents                                              |
| --------------------- | ----------------------------------------------------- |
| `/opds`               | Root navigation feed: the folder view — one entry per registered folder, plus Search and the X3/X4 catalog links |
| `/opds/books?page=N`  | All books, A→Z, 50 per page                           |
| `/opds/recent?page=N` | Books newest first                                    |
| `/opds/authors`       | Authors grouped by name, with book counts             |
| `/opds/authors/<name>`| That author's books                                   |
| `/opds/folders`       | Folder hierarchy: one subsection link per registered folder |
| `/opds/folders/<id>?path=…&page=N` | One directory level: subsection links for its subfolders plus the books directly inside it; `path` walks down (`?path=Series/Volume 1`), books paginate at 50 |
| `/opds/search?q=…`    | Search across title, authors, description, series, publisher, ISBN |
| `/opds/books/<id>`    | Feed for a single book                                |
| `/opds/download/<id>` | The book file itself (attachment)                     |
| `/opds/cover/<id>`    | Cover image extracted from the EPUB on demand (`404` when absent) |
| `/opdsx3`            | X3 catalog root: the folder view with X3-optimized acquisitions, plus Search |
| `/opdsx3/books?page=N`, `/opdsx3/recent?page=N` | Flat feeds of the X3 catalog, EPUBs via the X3 download |
| `/opdsx3/authors`, `/opdsx3/authors/<name>`, `/opdsx3/search?q=…` | Author index, author feed and search for the X3 catalog |
| `/opdsx3/books/<id>` | Single-book feed in the X3 catalog                    |
| `/opdsx3/download/<id>` | EPUB optimized for the Xteink X3 (on demand, cached) |
| `/opdsx3/folders`, `/opdsx3/folders/<id>?path=…` | The folder hierarchy with X3-optimized EPUB acquisitions |
| `/opdsx4`            | X4 catalog root: same as `/opdsx3`                  |
| `/opdsx4/books`, `/opdsx4/recent`, `/opdsx4/authors`, `/opdsx4/search`, `/opdsx4/folders`, `/opdsx4/folders/<id>?path=…` | The X3 URLs above with X4 acquisitions |
| `/opdsx4/books/<id>` | Single-book feed in the X4 catalog                    |
| `/opdsx4/download/<id>` | EPUB optimized for the Xteink X4 (on demand, cached) |
| `/opds/publications/<id>/progression` | Reading position per OPDS Progression 1.0: `GET` reads, `PUT` updates (`application/opds-progression+json`); conflicts are `409` problem details — full client docs below |

The roots (`/opds`, `/opdsx3`, `/opdsx4`) all open straight into the folder
view, so a client pointed at any of them browses the same tree. The flat
feeds (`/opds/books`, `/opds/recent`, `/opds/authors`, `/opds/folders` and
their X3/X4 mirrors) still exist but are no longer linked from the roots —
reach them by URL or through search. The X3/X4 catalogs contain the full
library and mirror the original catalog's structure section for section;
only EPUB entries differ — they use the device's optimized download, while
other formats fall back to the original file so no acquisition link is ever
broken.

Folder feeds mirror the library's directory tree from the index — they read
`books.relative_path` only and never touch the filesystem, so an unknown
`path` simply returns an empty feed. At most 500 subfolders are listed per
level (deeper trees are still reachable through `path` directly).

Optimization runs only when an X3/X4 download is requested — never during
scans or startup — using a vendored copy of the
[epubkit](https://github.com/b1rdmania/epubkit) pipeline. Results are cached
under `data/cache/optimized/{x3,x4}/` and rebuilt automatically when the
source file changes. The original library files are never modified. The
dashboard's **Clear cache** action empties the cache and its index rows;
the next X3/X4 download regenerates the EPUB on demand. A scan or a
folder removal also drops the cached renditions of the books that left
the index, and clearing never disturbs a download that is being optimized
at that moment.

### Reading progression (OPDS Progression 1.0)

Reading positions sync through OPDS Progression 1.0 at
`/opds/publications/<id>/progression`. There is exactly **one position per
logical book**, shared by the original file and that book's X3/X4 downloads;
the optimization profile never affects it.

**Discovery.** Every acquisition entry links to its own position with
`rel="http://opds-spec.org/progression"` and
`type="application/opds-progression+json"` — follow that link instead of
building the URL from the book id.

**Authentication.** The same HTTP Basic credentials as the catalog; a `401`
body is the OPDS Authentication Document (`application/opds-authentication+json`),
also served at `/opds/authentication`. While `OPDS_ADMIN_PASSWORD` is unset
both methods answer `503`.

**Request document** (`Content-Type: application/opds-progression+json`):

| Field | Required | Notes |
| ----- | -------- | ----- |
| `modified` | yes | ISO 8601 timestamp of when the client saved this position; a value without an offset is read as UTC |
| `progression` | yes | number in `0.0`–`1.0`, the fraction read through the publication |
| `device.id` | yes | non-empty string naming the device — a `urn:uuid:…` is a good choice |
| `device.name` | yes | non-empty display name |
| `title` | no | current position label, e.g. the chapter title |
| `references` | no | array of strings, e.g. the content document being read |

```json
{
  "modified": "2026-01-27T11:00:00Z",
  "progression": 0.0174,
  "device": {"id": "urn:uuid:019c0047-cc8d-7ec4-a3c3-938ccadc020a", "name": "Reader"},
  "title": "Chapter 1 - A New Dawn",
  "references": ["chapter1.html"]
}
```

**GET** → `200` with the stored document, or an **empty body** with the same
media type when the book has no position yet (treat it as "nothing saved").
A book that is not in the index is `404`.

**PUT** responses — the body of a `200`/`201` is the stored document:

| Status | Meaning |
| ------ | ------- |
| `201` | position created |
| `200` | position updated, or the same `modified` replayed (idempotent) |
| `400` | wrong media type, invalid JSON, or a field missing / out of range |
| `404` | no such book |
| `409` | this `modified` is **older** than the stored one — the newer position wins |

Errors are RFC 7807 documents (`application/problem+json`) carrying `type`
and `title`, with `type` one of
`https://registry.opds.io/error#progression-invalid-payload` (400),
`https://registry.opds.io/error#progression-date` (409) or `about:blank`
(404 and anything else, such as `405` from a wrong method or `500`).

**Conflict handling for clients.** Send `modified` from your own clock and
keep the `modified` returned by the server for the next write. An
out-of-order update from a second device gets `409` instead of clobbering
the newest position — on `409`, GET the current document and retry with the
newer timestamp. Timestamps are echoed with `Z` and microsecond precision,
so replaying a document unchanged stays `200`.

## Development

Run the development server:

```bash
uv run flask --app bookflow.app run --port 8000
```

Then open `http://127.0.0.1:8000/`, which redirects to the admin dashboard
(`/admin/`); the OPDS catalog is at `/opds`. Flask's own default is 5000,
hence the explicit `--port` — there is no bind-address/port setting, the
container always listens on `0.0.0.0:8000` (gunicorn).

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
uv run gunicorn --bind 0.0.0.0:8000 bookflow.wsgi:app
```

Mount library directories read-only; only the data directory needs write
access. The cache directory is disposable: clearing it costs only
regeneration time on the next device download.

## Docker

A prebuilt image is published to GHCR:

```bash
docker pull ghcr.io/syfq91/bookflow:latest
docker compose up -d
```

The image is published by the manually-triggered **Publish Docker image to
GHCR** workflow (`.github/workflows/publish-docker.yml`, run it from the
Actions tab): it gates on `ruff check` + the full test suite, then pushes
`ghcr.io/syfq91/bookflow:latest` and a `sha-<commit>` tag using the
workflow's `GITHUB_TOKEN`.

To build from source instead (the `Dockerfile` at the repo root):

```bash
docker compose up -d --build
```

Configuration lives in `compose.yml` itself — no `.env` step, no
`${...}` interpolation. `OPDS_ADMIN_PASSWORD` ships **empty**: no default
credential is shipped, so an unedited deployment cannot be logged into.
Before the first start set it there (otherwise OPDS returns `503` and
admin login is locked) and point the library bind at your books:

```yaml
environment:
  OPDS_ADMIN_PASSWORD: your-secret
volumes:
  - bookflow-data:/app/data
  - /srv/books:/library:ro
```

Every other setting is commented out in `compose.yml` and falls back
to the app defaults: the session secret is generated into the data volume on
first start, the scan extensions and worker count use their defaults.

- The library is bind-mounted **read-only** at `/library` (default host path
  `./library`). Register `/library` — the *container* path — under **Library**
  after first login; host paths are not visible inside the container.
- SQLite and the optimization cache live in the `bookflow-data` named
  volume; migrations run automatically at container start.
- The published port is `127.0.0.1:8000`; change the `ports:` entry to
  `0.0.0.0:8000:8000` to expose it on your LAN, or change the host port.
- `/healthz` drives the compose healthcheck.
