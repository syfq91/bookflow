# BookFlow

A small, self-hosted OPDS server: point it at folders of ebooks, then read
them from any OPDS client — with device-optimized EPUBs & XTC comics (Xteink X3/X4) and
reading-position sync.

- [ARCHITECTURE.md](ARCHITECTURE.md) — how the code is organized
- [REFERENCE.md](REFERENCE.md) — configuration, endpoints, development

## Quick start 
<strong>Docker</strong>

1. Edit `compose.yml`: set `OPDS_ADMIN_PASSWORD` and point the
   library bind at your books.
2. Start it:

   ```bash
   docker pull ghcr.io/syfq91/bookflow:latest
   docker compose up -d
   ```

3. Open <http://127.0.0.1:8000/> and sign in at `/admin/login`.

The library is mounted **read-only** at `/library`; register `/library`
(the container path — host paths are not visible inside the container)
after first login. SQLite and the cache live in a named volume and
migrations run automatically at start.

<details>
<summary><strong>Bare metal</strong></summary>

Requires [uv](https://docs.astral.sh/uv/); Python 3.14+ is managed by uv.

```bash
uv sync
uv run alembic upgrade head
uv run flask --app bookflow.app run --port 8000
```

Open <http://127.0.0.1:8000/> — it redirects to the admin dashboard.

</details>

## Using BookFlow

**Sign in.** The site root `/` redirects to `/admin/`. The same
credentials work for the OPDS catalog (HTTP Basic). Until
`OPDS_ADMIN_PASSWORD` is set, login attempts and the catalog return `503`.

**Add your library** — **Library → Add Folder**. Type an absolute path or
use **Browse…** to pick one. BookFlow only reads from it: registering
indexes the folder immediately, **Scan** re-indexes it, and deleted files
drop out of the index. Nothing is ever uploaded, renamed or modified.

**Browse and download** — **Library** lists the registered folders; each
folder name opens an FTP-style browser over the index. Click down through
folder levels, then click a file to download the original.

**Read from a client** — point any OPDS client at the root feed:

```
http://127.0.0.1:8000/opds
```

(that's the host/port you opened the admin UI on, plus `/opds`; Docker and
bare metal both default to `8000`). Authenticate with the admin
credentials. The root feed opens straight into the folder tree (mirroring
the directory layout) and links the flat feeds (`/opds/books`,
`/opds/recent`, authors) as entries; every feed advertises search. The X3/X4
device catalogs (`/opdsx3`, `/opdsx4`) open the same folder tree — their
EPUB and CBZ downloads are optimized on demand (EPUBs formatted for device;
CBZs converted to native XTC comics) and cached; other formats fall back
to the original file. Reading positions sync via OPDS Progression
(`/opds/publications/<id>/progression`) — the
[endpoint table](REFERENCE.md#opds-catalog) and a
[progression guide for client authors](REFERENCE.md#reading-progression-opds-progression-10)
(document schema, status codes, conflict rules) are in REFERENCE.md.

**Check status** — the dashboard shows books, folders, library size, last
scan, cache sizes and health badges; `/admin/health` runs per-component
checks and lists library statistics.

Endpoint tables, the full configuration reference, and development,
production and Docker details live in [REFERENCE.md](REFERENCE.md).
