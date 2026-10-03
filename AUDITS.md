# Codebase Audit: Dead Code and Best Practices

**Target:** BookFlow (`src/bookflow`, `tests`, `migrations`, `compose.yml`, `Dockerfile`)  
**Date:** October 3, 2026  
**Auditor:** Antigravity Agent  
**Test Suite Status:** 300 passed (100% passing)  
**Linter Status:** Ruff passing (rules `E, F, I, UP, B, C4, SIM, RUF059`)  

---

## 1. Executive Summary

BookFlow is an exceptionally well-engineered, focused, and clean application adhering closely to its core architectural invariants (read-only library filesystem, on-demand optimization, and single-user progression). The codebase exhibits high test coverage (300 comprehensive tests passing in under 3 minutes), clear separation of concerns across Flask blueprints, and zero unused CSS classes or dangling HTML templates.

However, a deep forensic analysis identified several areas requiring attention:
1. **Dead Code & Write-Only State:** Specific metadata columns in SQLite ([`Book.language`](file:///home/syafiq/code/bookflow/src/bookflow/database/models.py#L67) and [`Book.series_index`](file:///home/syafiq/code/bookflow/src/bookflow/database/models.py#L71)) are extracted during folder scans and persisted into the database, but are never exposed in OPDS feeds, search filters, or admin templates. Additionally, certain test fixtures and result fields are unused.
2. **Multi-Process Concurrency Deficits:** The project ships with Gunicorn configured for multiple workers (`GUNICORN_WORKERS:-2`), yet all concurrency controls ([`profile_lock`](file:///home/syafiq/code/bookflow/src/bookflow/optimizer/locks.py#L16), [`progression_lock`](file:///home/syafiq/code/bookflow/src/bookflow/optimizer/locks.py#L27), [`folder_lock`](file:///home/syafiq/code/bookflow/src/bookflow/library/scanner.py#L66), `_scratch_running`, and [`LoginRateLimiter`](file:///home/syafiq/code/bookflow/src/bookflow/auth/service.py#L54)) are in-memory Python threading locks. This introduces potential multi-worker race conditions, including SQLite unique constraint violations (`IntegrityError`) during simultaneous book optimizations and progression saves, and scratch file deletion during concurrent cache clears.
3. **Database Concurrency & Indexing:** SQLite is initialized without WAL (`Write-Ahead Logging`) mode or a busy timeout, making concurrent reads and writes vulnerable to `sqlite3.OperationalError: database is locked`. Furthermore, frequently queried columns ([`Book.authors`](file:///home/syafiq/code/bookflow/src/bookflow/database/models.py#L65) and [`Book.created_at`](file:///home/syafiq/code/bookflow/src/bookflow/database/models.py#L77)) lack database indexes.
4. **Adherence to Repository Standards:** Several modules and public functions deviate from the coding conventions specified in [AGENTS.md](file:///home/syafiq/code/bookflow/AGENTS.md), notably missing `from __future__ import annotations` and docstrings in package `__init__.py` files and blueprint route handlers.
5. **Memory & Resilience Vectors:** Potential memory exhaustion in the login rate limiter under distributed probing, missing URL decoding in EPUB cover paths, and unhandled `FileNotFoundError` during cache file removal.

---

## 2. Dead Code Audit

### 2.1. Write-Only Database Columns

| Column | Table | Extraction Source | Current Usage | Status |
| :--- | :--- | :--- | :--- | :--- |
| `language` | `books` | [`metadata.py:101-104`](file:///home/syafiq/code/bookflow/src/bookflow/library/metadata.py#L101-L104) | Saved via [`scanner.py:218`](file:///home/syafiq/code/bookflow/src/bookflow/library/scanner.py#L218); mapped in [`models.py:67`](file:///home/syafiq/code/bookflow/src/bookflow/database/models.py#L67). | **Dead / Write-Only.** Never queried in any route, never displayed in admin UI, not used in OPDS XML generation, and not included in catalog search. |
| `series_index` | `books` | [`metadata.py:113-118`](file:///home/syafiq/code/bookflow/src/bookflow/library/metadata.py#L113-L118) | Saved via [`scanner.py:222`](file:///home/syafiq/code/bookflow/src/bookflow/library/scanner.py#L222); mapped in [`models.py:71`](file:///home/syafiq/code/bookflow/src/bookflow/database/models.py#L71). | **Dead / Write-Only.** While `Book.series` is used in search ([`opds/routes.py:751`](file:///home/syafiq/code/bookflow/src/bookflow/opds/routes.py#L751)), `series_index` is never read, formatted, or exposed anywhere. |

*Note on other metadata:* Unlike Calibre, BookFlow deliberately maintains a minimal schema. While columns like `publisher` and `isbn` are not displayed in the UI, they are actively referenced in catalog search filters ([`_search_condition`](file:///home/syafiq/code/bookflow/src/bookflow/opds/routes.py#L744-L753)). In contrast, `language` and `series_index` incur XML parsing, memory, and database write overhead during every scan without ever being utilized.

*Recommendation:* Either expose `language` in OPDS `<entry><dcterms:language>` tags and `series_index` in book titles/summaries, or drop the unused columns in a future Alembic migration (mirroring revision [`0002_drop_unused_columns.py`](file:///home/syafiq/code/bookflow/migrations/versions/0002_drop_unused_columns.py)).

### 2.2. Unused Return Fields

- **[`FolderResult.name`](file:///home/syafiq/code/bookflow/src/bookflow/library/service.py#L28):** In [`library.service.add_folder()`](file:///home/syafiq/code/bookflow/src/bookflow/library/service.py#L46), `FolderResult(ok=True, folder_id=folder.id, path=path, name=name)` constructs and returns `name`. The caller in [`admin/routes.py:120-123`](file:///home/syafiq/code/bookflow/src/bookflow/admin/routes.py#L120-L123) only accesses `result.folder_id` and `result.path`, passing them to `_scan_and_respond()`. `FolderResult.name` is only referenced in a single test assertion.

### 2.3. Test Suite Redundancies & Dead Parameters

- **Unused `db` Fixture Parameter:** In [`tests/test_library.py`](file:///home/syafiq/code/bookflow/tests/test_library.py#L58-L167), the `db` fixture is defined as a non-autouse setup hook that applies Alembic migrations and initializes the database engine. Consequently, 12 individual test functions take `db` as a positional parameter without ever referencing it inside the test function body.
  *Recommendation:* Declare `@pytest.fixture(autouse=True)` in `test_library.py` (or apply `@pytest.mark.usefixtures("db")` at the module/class level) to eliminate 12 unused parameters.
- **Stale Coverage Artifact:** The repository root contains a 52 KB binary `.coverage` file dated September 29, 2026. Running coverage tools against this file emits `No source for code: .../src/bookflow/library/models.py`, which was refactored to `src/bookflow/database/models.py`.
  *Recommendation:* Add `.coverage` and `.coverage.*` to [`.gitignore`](file:///home/syafiq/code/bookflow/.gitignore) and delete the root `.coverage` file.

### 2.4. Wildcard Route Arguments

- In [`opds/progression.py:99`](file:///home/syafiq/code/bookflow/src/bookflow/opds/progression.py#L99) ([`unknown_publication_path`](file:///home/syafiq/code/bookflow/src/bookflow/opds/progression.py#L99)) and [`opds/routes.py:545`](file:///home/syafiq/code/bookflow/src/bookflow/opds/routes.py#L545) ([`unknown_path`](file:///home/syafiq/code/bookflow/src/bookflow/opds/routes.py#L545)):
  ```python
  @bp.get("/<path:unknown>")
  def unknown_publication_path(unknown: str) -> Never:
      abort(404)
  ```
  The argument `unknown` is required by Werkzeug converter signatures when matching `<path:unknown>`. While Vulture flags this with 100% confidence, this is expected behavior under Flask. (This is documented as the reason `ARG` is excluded from Ruff in [`pyproject.toml:41-43`](file:///home/syafiq/code/bookflow/pyproject.toml#L41-L43)).

### 2.5. Assets, Templates, and Routes Verification

- **CSS Selectors:** An automated audit of all 432 lines of [`style.css`](file:///home/syafiq/code/bookflow/src/bookflow/static/style.css) against the 11 HTML templates revealed **0 unused CSS classes**. Every style rule (including dynamic badges `.badge-ok`, `.badge-error`, `.badge-warn`, and flash messages `.flash-ok`, `.flash-error`) is actively referenced.
- **Jinja Templates & Partials:** All 11 templates (`layout.html`, `dashboard.html`, `library.html`, `folders.html`, `add_folder.html`, `browse_folders.html`, `health.html`, `login.html`, `_breadcrumbs.html`, `_checks.html`, `_rows.html`) are actively rendered or included.
- **Jinja Globals:** Both custom global helpers (`csrf_token()` and `is_admin()`) registered in [`create_app()`](file:///home/syafiq/code/bookflow/src/bookflow/app.py#L82-L83) are actively used in template rendering.

---

## 3. Best Practices & Architecture Audit

### 3.1. Concurrency: Multi-Worker vs. In-Memory Thread Locks

[Dockerfile:39](file:///home/syafiq/code/bookflow/Dockerfile#L39) runs Gunicorn with multiple workers:
```dockerfile
CMD ["sh", "-c", "alembic upgrade head && exec gunicorn --bind 0.0.0.0:8000 --workers \"${GUNICORN_WORKERS:-2}\" bookflow.wsgi:app"]
```
When running with 2 or more worker processes, in-memory state is isolated within each operating system process. This introduces several critical edge cases:

1. **Simultaneous Optimization Race on Cache Miss:**
   When two requests for the same book and profile (`/opdsx3/download/<id>`) hit Worker 1 and Worker 2 concurrently:
   - Both workers check [`_cache_is_valid()`](file:///home/syafiq/code/bookflow/src/bookflow/optimizer/service.py#L188) (both get `False`).
   - Both workers independently execute `epubkit` optimization into `.tmp/<uuid>.epub`.
   - Both workers execute `os.replace(tmp_file, cache_file)`. (Safe on POSIX).
   - Both workers execute [`_record()`](file:///home/syafiq/code/bookflow/src/bookflow/optimizer/service.py#L258):
     ```python
     with session_scope() as session:
         row = session.scalar(select(OptimizedBook).where(...))
         if row is None:
             row = OptimizedBook(book_id=book_id, profile=profile)
             session.add(row)
     ```
   - Worker 1 commits first. Worker 2 attempts to commit and crashes with:
     `sqlalchemy.exc.IntegrityError: UNIQUE constraint failed: optimized_books.book_id, optimized_books.profile`
   - *Impact:* Worker 2 returns an unhandled 500 error to the client instead of serving the freshly built file.
   - *Best Practice:* In `_record()`, wrap the insertion in a try/except for `IntegrityError` or use SQLite `ON CONFLICT DO UPDATE` (or catch the exception and update the existing row).

2. **Cache Clearance Race with Active Optimization:**
   In [`optimizer/service.py:161-177`](file:///home/syafiq/code/bookflow/src/bookflow/optimizer/service.py#L161-L177), [`_sweep_scratch()`](file:///home/syafiq/code/bookflow/src/bookflow/optimizer/service.py#L161) checks `_scratch_running`:
   ```python
   with _scratch_guard:
       if entry in _scratch_running:
           continue
   ```
   Because `_scratch_running` is a Python process-local set:
   - Worker 1 begins generating `.tmp/a1b2c3d4.epub` and adds it to Worker 1's `_scratch_running`.
   - Worker 2 receives an admin request to `/admin/cache/clear`.
   - Worker 2 sweeps `.tmp/`. Since Worker 2's `_scratch_running` is empty, Worker 2 unlinks `.tmp/a1b2c3d4.epub`.
   - Worker 1 finishes optimization and attempts `os.replace(tmp_file, cache_file)`, failing with `OptimizationError: optimizer produced no output` or `FileNotFoundError`.
   - *Best Practice:* Check file modification age in `_sweep_scratch()` (e.g. only sweep scratch files older than 15–30 minutes) rather than relying on in-process memory sets.

3. **Progression Upsert Race:**
   In [`opds/progression.py:64-80`](file:///home/syafiq/code/bookflow/src/bookflow/opds/progression.py#L64-L80), [`progression_lock(book_id)`](file:///home/syafiq/code/bookflow/src/bookflow/optimizer/locks.py#L27) only locks threads within the same worker. If two reading devices sync progression for the same new book simultaneously across different workers, both can execute `session.add(Progression(book_id=book_id))`, triggering an `IntegrityError` on `UniqueConstraint("book_id")`.

### 3.2. Database & SQLite Engine Optimization

1. **Absence of WAL (Write-Ahead Logging) Mode:**
   In [`database/database.py:29-37`](file:///home/syafiq/code/bookflow/src/bookflow/database/database.py#L29-L37), SQLite connections are configured with:
   ```python
   connect_args = {"check_same_thread": False}
   ```
   And `PRAGMA foreign_keys=ON` is enabled on connect.
   However, SQLite's default journal mode is `DELETE`. In `DELETE` mode, any write operation (library scan, cache record, progression update) acquires an exclusive write lock that completely blocks all concurrent database readers (OPDS navigation, book downloads, admin dashboard).
   - *Best Practice:* Enable WAL mode on connect:
     ```python
     cursor.execute("PRAGMA journal_mode=WAL")
     cursor.execute("PRAGMA busy_timeout=5000")  # Wait up to 5s before throwing locked error
     ```
     WAL mode allows concurrent readers while a write transaction is executing.

2. **Missing Indexes on Key Query Paths:**
   - **`books.authors`:** Queried in [`opds/routes.py:378-384`](file:///home/syafiq/code/bookflow/src/bookflow/opds/routes.py#L378-L384) with `GROUP BY Book.authors ORDER BY lower(Book.authors)` and filtered in `_author_feed` with `Book.authors == author`. Without an index on `authors`, these queries perform full table scans on every author feed load.
   - **`books.created_at`:** Queried in [`opds/routes.py:186`](file:///home/syafiq/code/bookflow/src/bookflow/opds/routes.py#L186) with `ORDER BY Book.created_at.desc(), Book.id.desc()` to populate the `/opds/recent` feed. Lacks an index.
   - **`books.title`:** Default ordering for all catalog feeds (`_title_order()`) uses `ORDER BY lower(coalesce(Book.title, Book.relative_path))`.

### 3.3. Convention Compliance with `AGENTS.md`

[AGENTS.md](file:///home/syafiq/code/bookflow/AGENTS.md) defines explicit code standards:
> *"Style: `from __future__ import annotations` at the top of every module; type-hint public functions; docstrings on modules/classes/public functions; inline comments are rare — prefer clear code over narration."*

The codebase follows type hints rigorously (0 missing type annotations), but deviates in two specific areas:

1. **Missing `from __future__ import annotations` in package `__init__.py` files:**
   - [`src/bookflow/__init__.py`](file:///home/syafiq/code/bookflow/src/bookflow/__init__.py)
   - [`src/bookflow/admin/__init__.py`](file:///home/syafiq/code/bookflow/src/bookflow/admin/__init__.py)
   - [`src/bookflow/auth/__init__.py`](file:///home/syafiq/code/bookflow/src/bookflow/auth/__init__.py)
   - [`src/bookflow/database/__init__.py`](file:///home/syafiq/code/bookflow/src/bookflow/database/__init__.py)
   - [`src/bookflow/health/__init__.py`](file:///home/syafiq/code/bookflow/src/bookflow/health/__init__.py)
   - [`src/bookflow/library/__init__.py`](file:///home/syafiq/code/bookflow/src/bookflow/library/__init__.py)
   - [`src/bookflow/opds/__init__.py`](file:///home/syafiq/code/bookflow/src/bookflow/opds/__init__.py)
   - [`src/bookflow/optimizer/__init__.py`](file:///home/syafiq/code/bookflow/src/bookflow/optimizer/__init__.py)

2. **Missing Module & Function Docstrings:**
   - 6 package init files lack module docstrings (`admin`, `auth`, `database`, `health`, `library`, `opds`).
   - Public view routes in [`admin/routes.py`](file:///home/syafiq/code/bookflow/src/bookflow/admin/routes.py) (`dashboard`, `folders`, `folder_new`, `folder_browse`, `folder_create`, `folder_scan`, `folder_delete`, `book_download`, `cache_clear`, `health`) lack function docstrings.
   - Public view routes in [`auth/routes.py`](file:///home/syafiq/code/bookflow/src/bookflow/auth/routes.py) (`login`, `login_post`, `logout`) lack docstrings.
   - Public routes in [`opds/routes.py`](file:///home/syafiq/code/bookflow/src/bookflow/opds/routes.py) (`book_feed`, `x3_book_feed`, `x4_book_feed`, `download`, `cover`, `x3_download`, `x4_download`, `unknown_path`) and [`opds/progression.py`](file:///home/syafiq/code/bookflow/src/bookflow/opds/progression.py) (`publication_progression`, `update_publication_progression`) lack docstrings.
   - Public TypedDict models in [`health/service.py`](file:///home/syafiq/code/bookflow/src/bookflow/health/service.py#L34-L72) (`FormatStats`, `FolderStats`, `CacheProfile`, `ProgressionStats`, `LastSuccess`, `ScanError`, `ScannerStats`) lack class docstrings.

### 3.4. Memory & Resilience Findings

1. **Unbounded Memory Retention in `LoginRateLimiter`:**
   In [`auth/service.py:89-98`](file:///home/syafiq/code/bookflow/src/bookflow/auth/service.py#L89-L98):
   ```python
   def _prune(self, key: tuple[str, str], now: float) -> deque[float]:
       failures = self._failures.get(key)
       if failures is None:
           failures = deque()
           self._failures[key] = failures
       cutoff = now - self._window_seconds
       while failures and failures[0] <= cutoff:
           failures.popleft()
       return failures
   ```
   When old failure timestamps pass the cutoff window, entries are popped from `failures`, leaving an empty `deque()` associated with `(ip, username)` in `self._failures`. There is no sweep or cleanup of keys with empty deques. Under automated login attempts with random usernames or rotating IPs, `self._failures` will continuously grow, leaking memory over time.
   - *Best Practice:* In `_prune` (or in a periodic cleanup), if `not failures` after pruning, remove the key from `self._failures` using `self._failures.pop(key, None)`.

2. **Missing URL Decoding in EPUB Cover Extraction:**
   In [`library/metadata.py:269-284`](file:///home/syafiq/code/bookflow/src/bookflow/library/metadata.py#L269-L284), `_zip_resolve(opf_path, href)` handles path segments and `..` traversal, but does not call `urllib.parse.unquote(clean)`.
   In valid EPUBs where the OPF manifest specifies percent-encoded hrefs (e.g. `<item href="images/cover%20art.jpg" .../>`), standard zip archives store the literal filename (`images/cover art.jpg`). Attempting `archive.read(target)` fails with `KeyError`, causing cover extraction to silently fail and return `None`.
   - *Best Practice:* Apply `urllib.parse.unquote` to `clean` before resolving zip segments.

3. **HTTP Caching Headers on Cover Endpoint:**
   [`opds/routes.py:513-525`](file:///home/syafiq/code/bookflow/src/bookflow/opds/routes.py#L513-L525) serves `/opds/cover/<int:book_id>` with:
   ```python
   headers={"Cache-Control": "public, max-age=3600"}
   ```
   The endpoint does not emit an `ETag` or `Last-Modified` header, preventing HTTP clients from revalidating with `If-None-Match` / `304 Not Modified`. Every request past the max-age forces a full EPUB extraction and file transmission.

4. **Unconditional Cover Links for Non-EPUB Formats:**
   [`opds/generator.py:136-138`](file:///home/syafiq/code/bookflow/src/bookflow/opds/generator.py#L136-L138) emits thumbnail and cover image links for every book entry in OPDS feeds:
   ```python
   cover = url_for("opds.cover", book_id=book.id)
   _add_link(entry, Link(THUMBNAIL_REL, cover))
   _add_link(entry, Link(IMAGE_REL, cover))
   ```
   However, [`cover()`](file:///home/syafiq/code/bookflow/src/bookflow/opds/routes.py#L513-L517) immediately returns 404 if `target.suffix.lower() != ".epub"`. For PDF, CBZ, CBR, or MOBI books, readers attempting to fetch cover art will encounter broken image links and 404 responses.
   - *Best Practice:* Only generate cover links if the book format supports cover extraction (or when cover extraction has verified cover availability).

5. **Potential `FileNotFoundError` in Cache Clearance:**
   In [`optimizer/service.py:80-84`](file:///home/syafiq/code/bookflow/src/bookflow/optimizer/service.py#L80-L84):
   ```python
   with profile_lock(book_id, profile):
       if entry.is_file():
           entry.unlink()
           files += 1
   ```
   Line 77 uses `entry.unlink(missing_ok=True)` and line 123 uses `entry.unlink(missing_ok=True)`. However, line 82 uses `entry.unlink()` without `missing_ok=True`. If a file is unlinked by another process between `entry.is_file()` and `entry.unlink()`, an unhandled `FileNotFoundError` is raised.

6. **Session Expiry Configuration:**
   In [`auth/routes.py:68`](file:///home/syafiq/code/bookflow/src/bookflow/auth/routes.py#L68), `session.permanent = True` is set upon successful admin login, but `PERMANENT_SESSION_LIFETIME` is not configured in `app.py`. Flask defaults to a 31-day session lifetime. For administrative access, an explicit configuration (e.g. 7 days or 24 hours) is recommended.

---

## 4. Prioritized Recommendations Roadmap

### High Priority (Stability & Concurrency)

1. **Enable SQLite WAL Mode & Busy Timeout:**
   In `database/database.py`, execute `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000` on connect. This prevents background folder scanning and cache writes from locking out concurrent OPDS readers.
2. **Defend against Multi-Worker Unique Constraint Races:**
   In `optimizer/service.py:_record()`, handle `IntegrityError` when inserting into `optimized_books` under concurrent worker execution. If a duplicate entry occurs, refresh and update the existing row rather than allowing a 500 error.
3. **Guard Sweep Scratch against Active Multi-Worker Tasks:**
   In `optimizer/service.py:_sweep_scratch()`, ignore scratch files modified within the last 15 minutes instead of relying purely on in-memory `_scratch_running`.
4. **Fix Safe Unlink in Cache Clear:**
   Pass `missing_ok=True` to `entry.unlink()` in `optimizer/service.py:82`.

### Medium Priority (Code Hygiene & Standards)

5. **Restore Convention Compliance (`AGENTS.md`):**
   Add `from __future__ import annotations` and appropriate docstrings to all package `__init__.py` files, public route functions, and TypedDicts.
6. **Fix URL Decoding in EPUB Cover Extraction:**
   Use `urllib.parse.unquote` in `_zip_resolve` in `library/metadata.py` so books with percent-encoded filenames in their OPF manifest resolve covers properly.
7. **Clean up Pruning in `LoginRateLimiter`:**
   In `auth/service.py:_prune()`, delete empty `deque` entries to prevent unbounded memory retention.
8. **Conditionally Emit Cover Links in OPDS Feeds:**
   In `opds/generator.py:book_entry`, only attach cover/thumbnail links for `.epub` files (or formats with supported cover extraction).

### Low Priority (Optimizations & Schema Cleanliness)

9. **Database Indexes for Search and Sort:**
   Add Alembic migrations creating indexes on `books.authors` and `books.created_at`.
10. **Refactor Write-Only Columns:**
    Evaluate whether to expose `Book.language` and `Book.series_index` in OPDS feeds and search, or remove them from the scanner and database schema.
11. **Autouse Test Fixture in `test_library.py`:**
    Convert the `db` fixture in `tests/test_library.py` to `autouse=True` to eliminate redundant arguments across 12 test functions.
12. **Configure Explicit Session Lifetime:**
    Set `PERMANENT_SESSION_LIFETIME = timedelta(days=7)` in `create_app()`.
