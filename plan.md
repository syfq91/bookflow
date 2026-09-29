# BookFlow — Implementation Plan

## 1. Project Overview

Build a small, self-hosted OPDS server in Python.

The server provides:

- A read-only ebook library sourced from existing filesystem folders.
- OPDS catalog access.
- OPDS Progression 1.0 support.
- A simple single-user admin web UI.
- Filesystem scanning and metadata indexing.
- On-demand EPUB optimization for device-specific OPDS feeds.
- X3 and X4 optimized EPUB feeds using epubkit.
- Library statistics and health monitoring.

The application must never modify the original library files.

The filesystem is the source of truth for ebook files. SQLite is an index/cache of the filesystem.

## 2. Technology Stack

Use:

- Python 3.13+
- uv for project/dependency management
- Flask
- Jinja2
- SQLAlchemy
- Alembic
- SQLite
- argon2-cffi
- pytest
- Ruff
- Gunicorn for production
- epubkit for on-demand EPUB optimization

Repository:

- <https://github.com/b1rdmania/epubkit>
- <https://github.com/opds-community/drafts/blob/main/opds-progression-1.0.md>

Do not introduce FastAPI.

The application itself is a Flask application. Do not run epubkit as a separate HTTP service.

## 3. Core Design Principles

### 3.1 Filesystem is authoritative

Configured library directories contain the actual ebook files.

SQLite contains an index of those files.

Example:

```text
/media/books/
├── fiction/
│   ├── dune.epub
│   └── foundation.epub
└── non-fiction/
    └── example.pdf
```

SQLite records metadata and relative paths.

The application must not:

- upload books
- delete books
- rename books
- move books
- modify books
- modify EPUB contents in-place
- modify files in configured library directories

### 3.2 Admin UI is not a file manager

The admin UI only manages:

- configured library folders
- scanning
- statistics
- health
- authentication/settings necessary for the server

There must be no book upload/edit/delete UI.

### 3.3 Optimized EPUBs are separate cache files

X3/X4 optimization must never modify the source EPUB.

```text
/media/books/dune.epub

data/cache/optimized/x4/<book-id>.epub
data/cache/optimized/x3/<book-id>.epub
```

### 3.4 Optimization is strictly on demand

Do not optimize:

- during library scanning
- during application startup
- on a scheduled task
- in a background worker
- when a folder is added

Only an actual request to an X3/X4 OPDS acquisition endpoint may invoke epubkit.

## 4. Project Structure

Use a src layout.

```text
bookflow/
├── pyproject.toml
├── uv.lock
├── README.md
├── PLAN.md
├── .gitignore
├── .env.example
│
├── src/
│   └── bookflow/
│       ├── __init__.py
│       ├── app.py
│       ├── config.py
│       │
│       ├── auth/
│       │   ├── __init__.py
│       │   ├── routes.py
│       │   ├── service.py
│       │   └── decorators.py
│       │
│       ├── admin/
│       │   ├── __init__.py
│       │   └── routes.py
│       │
│       ├── opds/
│       │   ├── __init__.py
│       │   ├── routes.py
│       │   ├── generator.py
│       │   ├── auth.py
│       │   └── progression.py
│       │
│       ├── library/
│       │   ├── __init__.py
│       │   ├── models.py
│       │   ├── scanner.py
│       │   ├── metadata.py
│       │   └── service.py
│       │
│       ├── optimizer/
│       │   ├── __init__.py
│       │   ├── service.py
│       │   ├── epubkit.py
│       │   └── locks.py
│       │
│       ├── database/
│       │   ├── __init__.py
│       │   ├── database.py
│       │   └── models.py
│       │
│       ├── health/
│       │   ├── __init__.py
│       │   └── service.py
│       │
│       ├── templates/
│       │   ├── layout.html
│       │   ├── login.html
│       │   ├── dashboard.html
│       │   ├── folders.html
│       │   ├── add_folder.html
│       │   └── health.html
│       │
│       └── static/
│           └── style.css
│
├── migrations/
│
└── tests/
    ├── test_auth.py
    ├── test_library.py
    ├── test_scanner.py
    ├── test_opds.py
    ├── test_progression.py
    ├── test_optimizer.py
    └── test_health.py
```

## 5. Python Project Setup

Initialize with uv.

Expected commands:

```bash
uv init
uv add flask sqlalchemy alembic argon2-cffi
uv add --dev pytest pytest-cov ruff
```

Add the dependencies required by epubkit.

The project must commit:

- `pyproject.toml`
- `uv.lock`

Do not use `requirements.txt` as the primary dependency definition.

Run the application through uv during development:

```bash
uv run flask --app bookflow.app run
```

Tests:

```bash
uv run pytest
```

Lint:

```bash
uv run ruff check .
```

## 6. Configuration

Configuration should come from environment variables and sensible defaults.

Example:

```bash
OPDS_HOST=0.0.0.0
OPDS_PORT=8000

OPDS_DATA_DIR=./data

OPDS_SESSION_SECRET=

OPDS_ADMIN_USERNAME=admin
OPDS_ADMIN_PASSWORD=

OPDS_MAX_UPLOAD_SIZE=
```

There is no book upload functionality, so `MAX_UPLOAD_SIZE` is not required for library files.

The application should still have reasonable limits for request bodies.

Do not store plaintext passwords in SQLite.

The initial admin password should be supplied during setup and hashed with Argon2id.

## 7. Authentication

There is exactly one administrative user.

Do not implement multi-user accounts.

### Admin authentication

Use session-based authentication.

Routes:

| Method | Path             |
| ------ | ---------------- |
| GET    | `/admin/login`   |
| POST   | `/admin/login`   |
| POST   | `/admin/logout`  |

Admin pages require authentication.

Use:

- Argon2id password hashing.
- Secure session cookies.
- HttpOnly.
- SameSite=Lax or stricter where practical.
- CSRF protection for state-changing admin requests.
- Login rate limiting.

Never log:

- passwords
- session cookies
- authentication headers

### OPDS authentication

Use HTTP Basic Authentication for `/opds/*`.

The same single username/password is used.

Use HTTPS in production when authentication crosses an untrusted network.

## 8. Database

Use SQLite with SQLAlchemy.

Use Alembic for migrations.

### 8.1 library_folders

Fields:

- `id`
- `path`
- `name`
- `enabled`
- `created_at`
- `updated_at`
- `last_scan_at`
- `last_scan_duration`
- `last_scan_status`
- `last_scan_error`

`path` is the filesystem root.

Example:

```text
/media/books
```

The path must be normalized and validated.

### 8.2 books

Fields:

- `id`
- `folder_id`
- `relative_path`
- `title`
- `authors`
- `publisher`
- `language`
- `isbn`
- `description`
- `series`
- `series_index`
- `file_format`
- `file_size`
- `file_modified_at`
- `created_at`
- `updated_at`

The physical file is identified by:

```text
library_folder.path + book.relative_path
```

Do not store the absolute file path for every book.

### 8.3 progressions

Fields:

- `id`
- `book_id`
- `progression`
- `modified`
- `device_id`
- `device_name`
- `title`
- `references`
- `created_at`
- `updated_at`

A book has one latest progression because this is a single-user system.

### 8.4 optimized_books

Fields:

- `id`
- `book_id`
- `profile`
- `source_mtime`
- `source_size`
- `optimized_path`
- `optimized_size`
- `created_at`
- `updated_at`

Profile currently supports:

- `x3`
- `x4`

Add a unique constraint on `book_id + profile`.

## 9. Library Folder Management

The admin UI must allow:

- Add folder
- Remove folder
- Scan folder

Adding a folder only registers it.

It must not modify the directory.

Before registering a folder:

- Path must exist.
- Path must be a directory.
- Application must be able to read it.
- Path must be normalized.
- Avoid allowing arbitrary unsafe filesystem paths where deployment policy requires restrictions.

Example:

```text
/media/books
```

The UI should show:

```text
/media/books
842 books
21.3 GB
Last scan: 2026-09-29 08:21
Status: OK
```

## 10. Library Scanner

Implement a scanner service.

The scanner recursively walks every configured folder.

Initially support:

- `.epub`
- `.pdf`
- `.cbz`
- `.cbr`
- `.mobi`
- `.azw3`

Keep supported extensions configurable.

The scanner must:

- Find supported files.
- Calculate relative paths.
- Read file metadata.
- Extract ebook metadata where possible.
- Add new files.
- Update changed files.
- Detect removed files.
- Record scan statistics.
- Record errors without aborting the entire scan.

> **Important**
>
> Scanning must never run epubkit.
>
> Scanning only indexes the source library.

## 11. Missing Folder Behavior

If a configured folder becomes unavailable:

```text
/media/books
```

must not cause its books to be immediately deleted from SQLite.

Instead:

```text
last_scan_status = error
last_scan_error = ...
```

Keep the existing index.

When the folder becomes available again, the next successful scan reconciles the index.

This prevents a temporary unmounted disk from wiping the OPDS catalog.

## 12. Metadata Extraction

For EPUB, extract where available:

- title
- author
- language
- publisher
- identifier/ISBN
- description
- series information
- cover information

For PDF:

- Use embedded PDF metadata where available.

For unsupported/missing metadata, use sensible filename-based fallback.

The source files must not be modified.

## 13. OPDS

Implement OPDS catalog generation.

Initial structure:

```text
/opds
/opds/books
/opds/books/<id>
/opds/download/<id>
/opds/cover/<id>

/opds/recent
/opds/authors
/opds/authors/<id>
/opds/search?q=<query>
```

The root catalog should expose:

- All Books
- Recent
- Authors
- Search

Use valid Atom/OPDS XML.

Correct MIME types must be returned.

Acquisition links must point to the appropriate download endpoint.

## 14. OPDS X3/X4 Feeds

Provide separate device-specific OPDS catalogs.

```text
/opds/x3
/opds/x4
```

Each feed should expose the same logical library but use a different acquisition path.

### X4

```text
/opds/x4
/opds/x4/books/<id>
/opds/x4/download/<id>
```

### X3

```text
/opds/x3
/opds/x3/books/<id>
/opds/x3/download/<id>
```

The OPDS metadata can remain the same as the source publication.

Only the acquisition output changes.

## 15. On-Demand EPUB Optimization

Integrate:

<https://github.com/b1rdmania/epubkit>

Use its reusable processing functionality directly.

Do not run the standalone epubkit web application.

Do not create a second HTTP service.

> **Critical requirement**
>
> Optimization happens only when a client requests an optimized acquisition URL.

Examples:

```text
GET /opds/x4/download/123
GET /opds/x3/download/123
```

Never optimize during:

- scanning
- startup
- folder registration
- scheduled jobs
- background jobs

## 16. Optimization Cache

Use:

```text
data/cache/optimized/
├── x3/
└── x4/
```

Example:

```text
data/cache/optimized/x4/123.epub
data/cache/optimized/x3/123.epub
```

When an optimized request arrives:

1. Find source book.
2. Check cache.
3. Check whether cache corresponds to current source metadata.
4. If valid, stream cache.
5. If invalid/missing, optimize on demand.
6. Write to a temporary file.
7. Atomically move the result into the cache.
8. Update `optimized_books`.
9. Stream the optimized file.

Never write the optimized file into the original library directory.

## 17. Optimization Cache Validation

Use:

- `source_mtime`
- `source_size`

to determine whether the cached result was produced from the current source.

If either changes, treat the cache as stale.

The scanner should update `file_modified_at` and `file_size`.

The optimizer may use those values when deciding cache validity.

## 18. Concurrent Optimization

Prevent duplicate optimization for the same `book_id + profile`.

Example problem:

```text
Client A ──┐
           ├── /opds/x4/download/123
Client B ──┘
```

Both must not independently run epubkit.

Implement a per-book/profile lock.

Expected behavior:

```text
Client A
   │
   ▼
lock(123, x4)
   │
   ▼
epubkit
   │
   ▼
cache
   │
   ▼
unlock

Client B
   │
   ▼
waits for lock
   │
   ▼
reads newly created cache
```

## 19. Safe Optimizer Output

Never write directly to the final cache path.

Use a temporary file:

```text
data/cache/optimized/x4/.tmp/<random>.epub
```

Only after successful optimization:

```text
temporary file
      ↓
atomic rename
      ↓
123.epub
```

If optimization fails:

- Delete temporary output.
- Keep the original EPUB untouched.
- Return an appropriate HTTP error.
- Log the failure without sensitive data.

## 20. OPDS Progression 1.0

Implement the OPDS Progression 1.0 draft:

<https://github.com/opds-community/drafts/blob/main/opds-progression-1.0.md>

Support:

```text
GET /opds/publications/<id>/progression
PUT /opds/publications/<id>/progression
```

Use:

```text
application/opds-progression+json
```

Advertise progression in OPDS entries using:

```xml
<link
    rel="http://opds-spec.org/progression"
    href="/opds/publications/123/progression"
    type="application/opds-progression+json"/>
```

The exact behavior must follow the current draft.

Do not invent a custom progression format.

## 21. Progression Semantics

A progression payload includes:

- `modified`
- `device`
- `progression`

and may include:

- `title`
- `references`

Store the latest accepted progression.

When receiving an update:

1. Validate the media type.
2. Parse JSON.
3. Validate required fields.
4. Validate progression range.
5. Validate timestamps.
6. Compare against stored progression.
7. Store the newer progression.
8. Return the resulting progression.

Handle stale updates according to the draft, including the appropriate `409 Conflict` behavior.

## 22. Progression and X3/X4

Progression belongs to the logical book, not to the optimized cache.

These should all refer to the same progression:

```text
/opds/download/123
/opds/x3/download/123
/opds/x4/download/123
```

Do not create separate reading positions for X3 and X4.

## 23. Authentication Discovery

For unauthenticated OPDS requests, implement the authentication behavior required by the OPDS Progression specification.

When required, return the appropriate OPDS Authentication Document rather than a generic HTML login page.

Admin authentication and OPDS authentication are separate mechanisms:

| Path       | Mechanism         |
| ---------- | ----------------- |
| `/admin/*` | session cookie    |
| `/opds/*`  | HTTP Basic Auth   |

## 24. Admin Dashboard

The dashboard should show:

- Books
- Folders
- Total library size
- Last scan
- Scan duration
- Scan errors
- X3 optimized cache size
- X4 optimized cache size
- Database status
- Filesystem status
- OPDS status
- Optimizer status

Example:

```text
BookFlow

Books                 1,284
Folders                   4
Library size           38.4 GB

Last scan
  Status              OK
  Duration            14.2 sec
  Errors                    0

Optimization cache
  X3                    5.9 GB
  X4                    6.4 GB

Health
  Database                OK
  Library                 OK
  OPDS                    OK
  epubkit                 OK
```

## 25. Library Folder UI

The folder page should show:

```text
Library Folders

/media/books
842 books
21.3 GB
Last scan: 08:21
Status: OK

[Scan] [Remove]

/media/comics
442 books
17.1 GB
Last scan: 08:21
Status: OK

[Scan] [Remove]

[Add Folder]
```

Adding a folder:

```text
Folder path:
[ /media/books________________ ]

[Cancel] [Add Folder]
```

Do not provide:

- upload
- delete book
- rename
- move
- edit metadata
- edit cover

## 26. Health Checks

Implement health checks for:

### Database

- SQLite opens successfully.
- Basic query succeeds.

### Library folders

For each folder:

- Exists.
- Is a directory.
- Is readable.

### Cache

Check:

- Cache directory exists.
- Cache is writable.

### Optimizer

- Verify the epubkit integration is available.

### OPDS

- Verify application routing is functioning.

Health page should show per-component status.

## 27. Statistics

Calculate:

- total books
- total files
- books by format
- books per folder
- total source size
- total X3 cache size
- total X4 cache size
- books with progression
- known devices
- last successful scan
- scan duration
- scan errors

Do not calculate expensive statistics on every request if they require a full filesystem walk.

Use the database/index and cached scan statistics.

## 28. Cache Management

Optional admin action:

**Clear Optimization Cache**

This may delete:

```text
data/cache/optimized/x3/*
data/cache/optimized/x4/*
```

It must never delete or modify source library files.

After clearing cache:

```text
optimized_books
```

must be cleaned accordingly.

The next X3/X4 download regenerates the EPUB on demand.

## 29. Security Requirements

Implement:

- Argon2id password hashing.
- Secure session cookies.
- CSRF protection.
- Login rate limiting.
- HTTP Basic Auth for OPDS.
- Path traversal protection.
- Strict library-root path handling.
- No arbitrary filesystem file serving.
- No user-controlled output paths.
- No writes to library folders.
- Atomic optimized-file creation.
- Safe temporary files.
- No secrets in logs.
- No passwords in source code.

A book download must only be possible for a book that exists in the indexed database.

Never construct arbitrary filesystem paths directly from URL parameters.

## 30. Error Handling

Use appropriate HTTP errors.

Examples:

- `401 Unauthorized`
- `403 Forbidden`
- `404 Not Found`
- `409 Conflict`
- `500 Internal Server Error`

For OPDS/XML endpoints, return appropriate XML/OPDS responses.

For progression endpoints, return the appropriate JSON error format from the progression specification.

Admin UI should show human-readable HTML errors.

## 31. Testing

Use pytest.

### Authentication

Test:

- valid login
- invalid username
- invalid password
- protected admin route
- logout
- session invalidation
- OPDS Basic Auth
- unauthenticated OPDS request

### Scanner

Test:

- discover EPUB
- discover PDF
- nested directories
- new file
- changed file
- removed file
- inaccessible folder
- invalid metadata
- unsupported extension

### OPDS

Test:

- root feed
- book feed
- recent feed
- author feed
- search
- original acquisition
- cover
- XML content type
- authentication

### Progression

Test:

- GET nonexistent progression
- GET existing progression
- PUT valid progression
- invalid payload
- invalid progression value
- stale progression
- device information
- content type
- discovery link

### Optimizer

Test:

- X3 optimization
- X4 optimization
- cache miss
- cache hit
- stale cache
- source modification
- optimizer failure
- temporary file cleanup
- concurrent requests
- source EPUB remains unchanged

### Security

Test:

- path traversal
- arbitrary file access
- unsafe folder path
- unauthorized downloads
- CSRF
- authentication bypass

## 32. Development Milestones

### Milestone 1 — Project skeleton

Implement:

- uv project
- Flask application
- configuration
- SQLite
- SQLAlchemy
- Alembic
- basic test setup
- Ruff

Acceptance: `uv run pytest` works.

### Milestone 2 — Authentication and admin UI

Implement:

- login
- logout
- session authentication
- dashboard
- basic CSS

Acceptance: unauthenticated users cannot access `/admin/*`.

### Milestone 3 — Library folders and scanner

Implement:

- add folder
- remove folder
- scan folder
- scanner
- database index
- metadata extraction
- scan statistics

Acceptance: given `/media/books/dune.epub`, the scanner creates a corresponding SQLite book record.

### Milestone 4 — Basic OPDS

Implement:

- root catalog
- all books
- recent books
- authors
- search
- metadata
- original downloads

Acceptance: an OPDS client can browse and download books.

### Milestone 5 — OPDS Progression

Implement:

- progression database
- GET
- PUT
- discovery links
- authentication document
- conflict handling

Acceptance: a compatible client can read and update progression.

### Milestone 6 — epubkit integration

Implement:

- optimizer abstraction
- epubkit integration
- X3 profile
- X4 profile
- optimized cache
- cache validation
- locking
- atomic output

Acceptance: `/opds/x3/download/<id>` returns an X3 optimized EPUB and `/opds/x4/download/<id>` returns an X4 optimized EPUB.

### Milestone 7 — X3/X4 OPDS catalogs

Implement:

```text
/opds/x3
/opds/x3/books/<id>
/opds/x3/download/<id>

/opds/x4
/opds/x4/books/<id>
/opds/x4/download/<id>
```

Acceptance: a client can add either feed as a separate OPDS library and acquire the appropriate optimized EPUB.

### Milestone 8 — Health and statistics

Implement:

- health page
- scanner status
- cache statistics
- database status
- optimizer status
- library statistics

Acceptance: admin dashboard provides enough information to diagnose common problems.

## 33. Explicit Non-Goals

Do not implement these unless explicitly requested later:

- Multiple users.
- User registration.
- Roles/permissions.
- Book upload.
- Book deletion through UI.
- Book renaming.
- Book moving.
- Metadata editing.
- Cover editing.
- Cloud storage.
- Calibre integration.
- Elasticsearch.
- Redis.
- Celery.
- Kubernetes.
- Microservices.
- Separate epubkit HTTP server.
- Background EPUB optimization.
- Scheduled EPUB optimization.
- Pre-generating X3/X4 files.
- Separate progression per device.
- Separate progression per optimization profile.

Keep the application small.

## 34. Deployment

Support a normal Python deployment first.

Production command:

```bash
uv run gunicorn \
  --bind 0.0.0.0:8000 \
  bookflow.app:app
```

The library directory should be mounted read-only whenever possible.

Example conceptual Docker deployment:

```text
/app/data       → read/write
/media/books    → read-only
```

This is strongly recommended.

The application should work correctly if the library filesystem is mounted read-only.

## 35. Operational Principle

The final architecture should follow this model:

```text
                   READ ONLY
                ┌──────────────┐
                │ Library      │
                │ filesystem   │
                └──────┬───────┘
                       │
                       │ scan
                       ▼
                ┌──────────────┐
                │ SQLite index │
                └──────┬───────┘
                       │
             ┌─────────┼─────────┐
             │         │         │
             ▼         ▼         ▼
           OPDS      OPDS X3   OPDS X4
             │         │         │
             │         │         │
          original   on-demand  on-demand
             │       epubkit    epubkit
             │         │         │
             ▼         ▼         ▼
          source     cache      cache
           EPUB       EPUB       EPUB
```

The server is therefore fundamentally a:

**read-only filesystem → searchable index → OPDS server**

with optional:

**on-demand EPUB transformation**

and:

**single-user reading progression storage.**

The admin UI is only an operational/control interface, not a library file manager.
