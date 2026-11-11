# BookFlow — Codebase Improvement Plan

> **Deep-dive audit:** Best practices, dead code elimination, refactoring, and performance optimizations.  
> **Target version:** Python 3.14+ | Flask 3.1+ | SQLAlchemy 2.0+ | SQLite (WAL mode)

---

## 1. Executive Summary & Audit Scope

BookFlow's codebase is compact, disciplined, and strictly adheres to core architectural invariants:
- Read-only library filesystem.
- On-demand EPUB optimization (isolated to `optimize_book()`).
- Content-addressed, disposable rendition cache.
- Multi-user authentication and per-user progression tracking stored in SQLite.
- Vendored third-party code (`optimizer/epubkit/`) isolated from core application logic.

This deep dive examined:
1. **Dead code & orphaned artifacts**: Unused attributes, redundant logic, orphaned helpers, and unexecuted branches.
2. **Code smells & refactoring**: Duplication, magic string keys, repeated object allocations, and UI inconsistencies.
3. **Database & query efficiency**: Redundant sequential queries, in-memory deduplication, and multi-user metric accuracy.
4. **Performance & scalability**: CPU bottlenecks from Argon2id hashing on rapid OPDS requests and heavy archive extraction during HTTP 304 Not Modified checks.
5. **Security & robustness**: Memory bounds in rate limiting and reverse-proxy protocol handling.
6. **Modern Python 3.14 idioms**: Pathlib consistency, dataclass slots, and ISO 8601 parsing.

---

## 2. Findings & Action Items

### Category 1: Dead Code & Orphaned Logic

#### 1.1 Unused `self._admin_hash` in `DatabasePasswordVerifier`
- **Location:** [`src/bookflow/auth/service.py:53`](file:///home/syafiq/code/bookflow/src/bookflow/auth/service.py#L53)
- **Finding:** In `DatabasePasswordVerifier.__init__`, `self._admin_hash = self._hasher.hash(admin_password) if admin_password else ""` computes an Argon2id hash every time the application or verifier initializes (including during tests). However, `self._admin_hash` is never read anywhere in the codebase: database lookups check `user.password_hash`, and unmigrated database fallback uses constant-time `hmac.compare_digest(self._admin_password.encode(), password.encode())`.
- **Impact:** Wasted CPU cycles on startup running Argon2id on `admin_password` for an attribute that is dead code.
- **Action:** Remove `self._admin_hash`.

#### 1.2 Orphaned Test Helpers in `tests/factories.py`
- **Location:** [`tests/factories.py:171-198`](file:///home/syafiq/code/bookflow/tests/factories.py#L171-L198) and [`tests/test_progression.py:440-471`](file:///home/syafiq/code/bookflow/tests/test_progression.py#L440-L471)
- **Finding:** `create_user` and `basic_auth_headers` were added as standardized factory helpers in `tests/factories.py`. However, `tests/test_progression.py` inline-imports `PasswordHasher`, manually creates users via raw session calls, and hand-rolls Base64 basic auth headers.
- **Impact:** Dead code in test helpers and duplicated test boilerplate.
- **Action:** Refactor `tests/test_progression.py` to use `create_user` and `basic_auth_headers` from `factories.py`.

#### 1.3 Misnamed / Inverted Assertion in `test_user_cannot_delete_last_admin`
- **Location:** [`tests/test_users.py:176-199`](file:///home/syafiq/code/bookflow/tests/test_users.py#L176-L199)
- **Finding:** The test `test_user_cannot_delete_last_admin` actually creates a second admin (`admin2`), deletes `admin2`, and asserts `admin2` was deleted successfully. It tests deleting a secondary admin when multiple admins exist, but does *not* test the `admin_count <= 1` rejection guard.
- **Impact:** Misleading test name; the safety branch in `admin/routes.py` (`Cannot delete the last remaining administrator`) remains untested when triggered by a non-self delete attempt.
- **Action:** Rename the existing test to `test_user_can_delete_secondary_admin` and add a new test verifying that attempting to delete the last admin when only one exists returns a redirect with the flash error.

#### 1.4 Legacy OS Calls vs Pathlib
- **Location:** [`src/bookflow/optimizer/service.py:251`](file:///home/syafiq/code/bookflow/src/bookflow/optimizer/service.py#L251), [`tests/test_scanner.py:214, 218`](file:///home/syafiq/code/bookflow/tests/test_scanner.py#L214)
- **Finding:** `os.replace(tmp_file, cache_file)` is used where both are `Path` objects. `os.chmod(...)` is used in `tests/test_scanner.py` where `locked.chmod(...)` is available.
- **Action:** Replace with `tmp_file.replace(cache_file)` and `locked.chmod(...)`.

---

### Category 2: Refactoring & Code Hygiene

#### 2.1 Duplicated Progression Assignment in `opds/progression.py`
- **Location:** [`src/bookflow/opds/progression.py:96-101`](file:///home/syafiq/code/bookflow/src/bookflow/opds/progression.py#L96-L101) and [`src/bookflow/opds/progression.py:119-124`](file:///home/syafiq/code/bookflow/src/bookflow/opds/progression.py#L119-L124)
- **Finding:** When `IntegrityError` occurs due to a concurrent write, the fallback block duplicates the exact 6 attribute assignments:
  ```python
  row.progression = document.progression
  row.modified = _as_naive(document.modified)
  row.device_id = document.device_id
  row.device_name = document.device_name
  row.title = document.title
  row.references = document.references
  ```
- **Action:** Extract into a private helper `_apply_progression_document(row: Progression, document: _Document) -> None`.

#### 2.2 Hardcoded Session Keys vs Centralized Constants
- **Location:** [`src/bookflow/app.py:59`](file:///home/syafiq/code/bookflow/src/bookflow/app.py#L59), [`src/bookflow/admin/routes.py:301, 408`](file:///home/syafiq/code/bookflow/src/bookflow/admin/routes.py#L301)
- **Finding:** `auth/service.py` defines constants `ADMIN_SESSION_KEY`, `USER_ID_SESSION_KEY`, `USERNAME_SESSION_KEY`. However, `app.py` checks `session.get("admin")` and `admin/routes.py` checks `session.get("user_id")`.
- **Action:** Use `ADMIN_SESSION_KEY` and `USER_ID_SESSION_KEY` uniformly across all modules.

#### 2.3 Repeated `PasswordHasher()` Allocation in Admin Views
- **Location:** [`src/bookflow/admin/routes.py:371, 396`](file:///home/syafiq/code/bookflow/src/bookflow/admin/routes.py#L371)
- **Finding:** Each call to `user_create` and `user_change_password` instantiates a new `hasher = PasswordHasher()`.
- **Action:** Expose a hash helper on `DatabasePasswordVerifier` or use a module-level/shared `PasswordHasher` instance.

#### 2.4 Inconsistent Display Name in Admin Library Browser
- **Location:** [`src/bookflow/admin/routes.py:213`](file:///home/syafiq/code/bookflow/src/bookflow/admin/routes.py#L213)
- **Finding:** In `library_tree`, directory segments are displayed as their single-folder name (`name`), but books display `row.relative_path`. In nested directories (e.g. `/admin/library/1?path=sub/deep`), the file list renders `sub/deep/hidden.epub` instead of `hidden.epub`.
- **Action:** Render `row.relative_path.rsplit('/', 1)[-1]` for the displayed file name, matching the FTP-style browsing experience.

#### 2.5 Dataclass Memory Optimization (`slots=True`)
- **Location:** High-frequency dataclasses in [`src/bookflow/opds/generator.py`](file:///home/syafiq/code/bookflow/src/bookflow/opds/generator.py) (`Link`), [`src/bookflow/auth/service.py`](file:///home/syafiq/code/bookflow/src/bookflow/auth/service.py) (`AuthenticatedUser`), [`src/bookflow/library/metadata.py`](file:///home/syafiq/code/bookflow/src/bookflow/library/metadata.py) (`BookMetadata`), [`src/bookflow/health/service.py`](file:///home/syafiq/code/bookflow/src/bookflow/health/service.py) (`HealthCheck`).
- **Action:** Add `slots=True` to immutable dataclasses to reduce Python memory overhead and speed up attribute access.

---

### Category 3: Database & Query Performance

#### 3.1 Dashboard Query Consolidation (`_query_stats`)
- **Location:** [`src/bookflow/admin/routes.py:564-602`](file:///home/syafiq/code/bookflow/src/bookflow/admin/routes.py#L564-L602)
- **Finding:** The dashboard executes 7 sequential queries in `_query_stats`:
  1. `select(func.count(Book.id))`
  2. `select(func.count(LibraryFolder.id))`
  3. `select(func.coalesce(func.sum(Book.file_size), 0))`
  4. `select(func.count(LibraryFolder.id)).where(...)`
  5. `select(func.max(LibraryFolder.last_scan_at))`
  6. `select(LibraryFolder)...order_by(last_scan_at.desc()).limit(1)`
  7. `select(OptimizedBook.profile, ...)`
- **Optimization:**
  - Queries 1 & 3 can be combined: `select(func.count(Book.id), func.coalesce(func.sum(Book.file_size), 0))`.
  - Queries 5 & 6 are redundant: querying `select(LibraryFolder.last_scan_at, LibraryFolder.last_scan_duration).where(...).order_by(...).limit(1)` yields both `last_scan` and `last_scan_duration` in a single query without fetching the full model.

#### 3.2 Folder List Query Consolidation (`list_folders`)
- **Location:** [`src/bookflow/library/service.py:105-126`](file:///home/syafiq/code/bookflow/src/bookflow/library/service.py#L105-L126)
- **Finding:** `list_folders` first fetches all `LibraryFolder` models, then performs a second query grouping books by `folder_id`, and merges them in Python.
- **Optimization:** A single query with `outerjoin(Book, Book.folder_id == LibraryFolder.id)` and `group_by(LibraryFolder.id)` returns folder details and counts simultaneously.

#### 3.3 Accuracy and Memory in Health Progression Statistics
- **Location:** [`src/bookflow/health/service.py:190-200`](file:///home/syafiq/code/bookflow/src/bookflow/health/service.py#L190-L200)
- **Findings:**
  1. **Multi-user counting error:** `progression["books"]` uses `func.count(Progression.id)`. In multi-user mode, if 3 users read the same book, it counts 3 instead of 1. It must be `func.count(func.distinct(Progression.book_id))`.
  2. **In-memory deduplication:** `devices` loads `Progression.device_name` across *all* rows and dedupes them in Python memory. It should use `select(Progression.device_name).where(Progression.device_name.is_not(None)).distinct()`.

---

### Category 4: Performance & Caching

#### 4.1 Argon2id Verification Caching for Rapid OPDS Requests
- **Location:** [`src/bookflow/opds/auth.py:37`](file:///home/syafiq/code/bookflow/src/bookflow/opds/auth.py#L37), [`src/bookflow/auth/service.py:67`](file:///home/syafiq/code/bookflow/src/bookflow/auth/service.py#L67)
- **Problem:** OPDS clients (e.g. KOReader) frequently send bursts of 20–50 HTTP requests in seconds when opening a feed, syncing reading progression, and loading book covers. Every request carries HTTP Basic Auth, causing Argon2id verification to run repeatedly for the identical `(username, password)`. At ~60ms per verification, 50 requests can monopolize the CPU for 3+ seconds.
- **Solution:** Add a bounded in-memory verification cache with a short TTL (e.g., 60 seconds) in `DatabasePasswordVerifier`. Cache key: `(username, hmac_sha256(password))`. If a valid entry exists within the TTL window, return the cached `AuthenticatedUser` immediately. Invalidate the cache when a user password is changed or a user is deleted.

#### 4.2 Fast-Path `304 Not Modified` for EPUB Covers
- **Location:** [`src/bookflow/opds/routes.py:521-546`](file:///home/syafiq/code/bookflow/src/bookflow/opds/routes.py#L521-L546)
- **Problem:** Currently, `/opds/cover/<book_id>` opens the EPUB zip container, parses `container.xml` and OPF XML, unzips the cover image, computes SHA256 of the uncompressed image, and *then* checks `If-None-Match`. Even when a client has a cached cover, BookFlow still performs disk I/O, zip decompression, and XML parsing.
- **Solution:** Construct the ETag using the indexed book's `file_modified_at` and `file_size` (e.g. `f'"{book.id}-{int(book.file_modified_at.timestamp())}-{book.file_size}"'`). The server can query the database, check `request.if_none_match`, and return `304 Not Modified` immediately *without opening the zip file or extracting anything*.

---

### Category 5: Security & Robustness

#### 5.1 Bounded Memory for `LoginRateLimiter`
- **Location:** [`src/bookflow/auth/service.py:138-186`](file:///home/syafiq/code/bookflow/src/bookflow/auth/service.py#L138-L186)
- **Problem:** `LoginRateLimiter._failures` stores failure timestamps keyed by `(remote_addr, username)`. Stale keys are only pruned when the *exact same* key is tested again. An automated credential-stuffing attacker rotating through random usernames or IPs will cause `_failures` to grow monotonically until the process restarts.
- **Solution:** Add a maximum key limit (e.g., 5,000 keys) and periodically sweep expired keys if the dictionary exceeds the threshold.

#### 5.2 Reverse Proxy Protocol Forwarding
- **Location:** [`src/bookflow/app.py:110-112`](file:///home/syafiq/code/bookflow/src/bookflow/app.py#L110-L112)
- **Observation:** `ProxyFix(app.wsgi_app, x_for=settings.trusted_proxy_hops)` only sets `x_for`. When running behind a reverse proxy terminating HTTPS (such as Caddy or Nginx), external OPDS URLs generated with `_external=True` (like authentication document links) can incorrectly use `http://` instead of `https://`.
- **Solution:** Pass `x_proto=settings.trusted_proxy_hops` to `ProxyFix` so forwarded protocol headers (`X-Forwarded-Proto`) are respected when proxy hops are configured.

---

### Category 6: Modernization & Test Suite Polish

#### 6.1 Progression Test Cleanups
- **Location:** [`tests/test_progression.py:94`](file:///home/syafiq/code/bookflow/tests/test_progression.py#L94)
- **Action:** Replace `datetime.fromisoformat(value.replace("Z", "+00:00"))` with standard `datetime.fromisoformat(value)` (supported natively since Python 3.11).

#### 6.2 Test Coverage Additions
- Test cover fast-path 304 behavior.
- Test rate limiter memory bounded cleanup.
- Test distinct progression count in health statistics.

---

## 3. Implementation Plan (Phased Roadmap)

| Phase | Focus | Files Touched | Risk |
|---|---|---|---|
| **Phase 1** | **Dead Code & Quick Fixes** | `src/bookflow/auth/service.py`<br>`src/bookflow/optimizer/service.py`<br>`tests/factories.py`<br>`tests/test_progression.py`<br>`tests/test_users.py`<br>`tests/test_scanner.py` | Very Low |
| **Phase 2** | **Refactoring & Code Hygiene** | `src/bookflow/opds/progression.py`<br>`src/bookflow/admin/routes.py`<br>`src/bookflow/app.py`<br>`src/bookflow/library/browse.py` | Low |
| **Phase 3** | **Database Query Consolidation** | `src/bookflow/admin/routes.py`<br>`src/bookflow/library/service.py`<br>`src/bookflow/health/service.py` | Low |
| **Phase 4** | **Performance Optimizations** | `src/bookflow/auth/service.py`<br>`src/bookflow/opds/routes.py`<br>`src/bookflow/app.py` | Medium |
| **Phase 5** | **Robustness & Verification** | `src/bookflow/auth/service.py`<br>`tests/` | Low |

---

## 4. Verification Strategy

1. **Linting & Code Formatting:**
   ```bash
   uv run ruff check .
   ```
2. **Complete Test Suite (Regression Guard):**
   ```bash
   uv run pytest -q
   ```
3. **Behavioral Checks:**
   - Verify all 323+ tests pass.
   - Verify OPDS Basic Auth speedup on repetitive requests.
   - Verify `304 Not Modified` on cover requests without zip extraction.
   - Verify admin last-account protection edge cases.
