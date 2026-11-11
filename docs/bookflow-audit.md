# BookFlow — Code Audit Report
**Date:** 2026-11-11 | **Scope:** `src/bookflow/` (non-vendored) + tests  
**Toolchain:** Python 3.14, Flask 3, SQLAlchemy 2, uv, Ruff, pytest  
**Result:** ✅ 353/353 tests pass · ✅ Ruff clean (all configured rules)

---

## Summary

BookFlow is a well-structured, security-aware Flask application. The invariants documented in `AGENTS.md` are largely upheld: the library is strictly read-only, optimization is on-demand only, paths are always resolved through `book_file()`, and credentials are stored with Argon2id. The codebase scores highly on clarity, test coverage, and thread safety. The issues below are mostly **minor to moderate** improvements; none are critical vulnerabilities in the current deployment context.

| Severity | Count |
|---|---|
| 🔴 Bug / correctness | 2 |
| 🟠 Security / hardening | 4 |
| 🟡 Code quality / maintainability | 7 |
| 🟢 Informational / best practice | 5 |

---

## 🔴 Bugs / Correctness

### B-1 — Cover cache TOCTOU race (double-dict-access without lock)

**File:** [`opds/routes.py` L551–L566](file:///home/syafiq/bookflow/src/bookflow/opds/routes.py#L551-L566)

```python
with _cover_cache_lock:
    cached = _cover_cache.get(cache_key)   # (A) reads under lock

if cache_key in _cover_cache:              # (B) reads WITHOUT lock ← race
    if cached is None:
        abort(404)
    data, media_type, etag = cached        # could be stale/evicted
```

After the lock is released at `(A)`, another thread can call `.clear()` on the dict (triggered by size overflow). The check at `(B)` reads outside the lock, so `cached` and the dict can disagree. `cached` is `None` from a previous miss but the entry may have been evicted and re-populated, or vice-versa.

**Fix:** Consolidate both reads into one locked block, or rely solely on the already-captured `cached` variable:

```python
with _cover_cache_lock:
    cached = _cover_cache.get(cache_key)

if cached is not None:                   # truthy value → hit
    data, media_type, etag = cached
    ...
elif cache_key in _cover_cache:          # None sentinel → known miss
    abort(404)
# else: cache miss, extract cover
```

---

### B-2 — `Progression.progression` column type annotation is misleading

**File:** [`database/models.py` L137](file:///home/syafiq/bookflow/src/bookflow/database/models.py#L137)

```python
progression: Mapped[float | None] = mapped_column(JSON, nullable=True)
```

The column stores arbitrary JSON (the full OPDS Progression document object according to the spec), but the `Mapped` type says `float | None`. In practice the application writes and reads a `float`, but the SQLAlchemy type annotation is misleading to anyone who reads the model and would cause problems if the stored value were ever a dict or list (as the broader OPDS spec allows).

**Fix:** Change the annotation to match the actual JSON column semantics:
```python
progression: Mapped[dict | float | None] = mapped_column(JSON, nullable=True)
```
Or use `Any` / a custom TypeAlias if the value will remain a scalar float only.

---

## 🟠 Security / Hardening

### S-1 — OPDS Basic Auth has no brute-force protection

**File:** [`opds/auth.py`](file:///home/syafiq/bookflow/src/bookflow/opds/auth.py)

The admin login form (`/admin/login`) has a sliding-window `LoginRateLimiter`, but OPDS Basic Auth (`/opds/**`, `/opds/publications/**`) has none. An attacker who knows a username can enumerate passwords at the rate the Argon2 hasher allows. The auth cache (`AUTH_CACHE_TTL_SECONDS=60`) only helps when the same correct credential is repeated.

> [!WARNING]
> Self-hosted instances exposed to the internet are at risk. The fix is to add the same `LoginRateLimiter` check inside `authenticate()` in `opds/auth.py`, keyed on `(request.remote_addr, username)`.

---

### S-2 — No HTTP security response headers

There are no `after_request` hooks or middleware setting common hardening headers:

| Header | Recommended Value |
|---|---|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Content-Security-Policy` | at minimum `default-src 'self'` for admin UI |
| `Referrer-Policy` | `same-origin` |

Admin pages render server-side HTML, making these relevant. OPDS feeds are XML/JSON consumed by dedicated clients, so the concern there is lower.

---

### S-3 — `/healthz` is unauthenticated and reveals application state

**File:** [`app.py` L137–L139](file:///home/syafiq/bookflow/src/bookflow/app.py#L137-L139)

```python
@app.get("/healthz")
def healthz() -> Response:
    return jsonify(status="ok")
```

The current response is minimal (`{"status": "ok"}`), but the endpoint is accessible without credentials. If it ever returns version information, DB status, or hostname details, that becomes an information-disclosure vector. Currently fine — just be mindful when extending it.

---

### S-4 — `compose.yml` ships `OPDS_ADMIN_PASSWORD: ""`

**File:** [`compose.yml` L14](file:///home/syafiq/bookflow/compose.yml#L14)

The comment is clear ("deliberately empty"), but an operator who starts the stack without editing the file gets a 503 for every OPDS request. A better default pattern uses compose v2.4+ `${VAR:?error message}` syntax to fail loudly at startup:

```yaml
environment:
  OPDS_ADMIN_PASSWORD: ${OPDS_ADMIN_PASSWORD:?Set OPDS_ADMIN_PASSWORD before starting}
```

---

## 🟡 Code Quality / Maintainability

### Q-1 — Duplicated `PAGE_SIZE` and `FOLDER_SEGMENTS_LIMIT` constants

**Files:** [`admin/routes.py` L78–L80](file:///home/syafiq/bookflow/src/bookflow/admin/routes.py#L78-L80) and [`opds/routes.py` L55–L56](file:///home/syafiq/bookflow/src/bookflow/opds/routes.py#L55-L56)

Both modules define identical values independently. If one is changed the other won't update. These should live in a shared `bookflow/constants.py` (or in `config.py` as `Settings` fields if they should be configurable).

---

### Q-2 — `sessionmaker(bind=engine)` uses a deprecated SA 2 keyword

**File:** [`database/database.py` L46](file:///home/syafiq/bookflow/src/bookflow/database/database.py#L46)

```python
_session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
```

SQLAlchemy 2.0 deprecated the `bind` keyword on `sessionmaker` in favour of a positional first argument:

```python
_session_factory = sessionmaker(_engine, expire_on_commit=False)
```

The current form still works but generates deprecation warnings in some SA 2 minor releases.

---

### Q-3 — Unbounded in-process lock registry growth

**Files:** [`optimizer/locks.py`](file:///home/syafiq/bookflow/src/bookflow/optimizer/locks.py), [`library/scanner.py` L62–L73](file:///home/syafiq/bookflow/src/bookflow/library/scanner.py#L62-L73)

All three lock registries grow forever — a lock object is added the first time a key is seen and never removed. For a typical self-hosted library this is inconsequential (bounded by books × 2 profiles + users × books + folders). However, for a large library over a long runtime, thousands of `threading.Lock` objects accumulate. Consider `weakref.WeakValueDictionary` or add a comment documenting the expected cardinality bound.

---

### Q-4 — Missing return-type annotations on several public functions

A handful of module-level and blueprint-level functions lack `-> ReturnType`, contrary to the stated style convention of "type-hint public functions":

| Function | File |
|---|---|
| `authenticate()` | `opds/auth.py` |
| `navigation_feed()`, `acquisition_feed()`, `nav_entry()`, `author_entry()` | `opds/generator.py` |
| `send_book_response()` | `library/paths.py` |
| `_configure_sqlite_pragmas()` | `database/database.py` |

These are not bugs, but they reduce the value of the type-checking story and can confuse contributors.

---

### Q-5 — `_utcnow()` is a module-private helper that should be shared

**File:** [`library/scanner.py` L264](file:///home/syafiq/bookflow/src/bookflow/library/scanner.py#L264)

The pattern of storing naive UTC datetimes (stripping `tzinfo` after converting) is used in `scanner.py`, `progression.py`, and implicitly throughout the models. The stripping is intentional (SQLite has no native timezone support), but `_utcnow()` is only defined in `scanner.py`. `_as_naive()` / `_as_aware()` are also only in `progression.py`. Consider a shared `bookflow/utils.py` for these time utilities.

---

### Q-6 — SQLite-specific `sqlite_insert` import is undocumented

**File:** [`optimizer/service.py` L14](file:///home/syafiq/bookflow/src/bookflow/optimizer/service.py#L14)

```python
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
```

BookFlow is documented as SQLite-only, so this is not a practical concern, but the import creates a hard coupling that is not called out at the usage site. Worth a comment explaining why the dialect-specific import is intentional.

---

### Q-7 — No standalone CI test workflow (tests only run on manual publish)

**File:** [`.github/workflows/publish-docker.yml`](file:///home/syafiq/bookflow/.github/workflows/publish-docker.yml)

The only workflow is `workflow_dispatch`-only and runs lint + tests as a prerequisite to publishing. There is no event-triggered workflow that would run tests automatically on every commit or PR. Regressions on `main` are only caught when someone manually triggers a publish.

**Recommendation:** Add a separate `ci.yml`:
```yaml
on:
  push:
    branches: [main]
  pull_request:
jobs:
  test:
    # same steps as publish-docker.yml's test job
```

---

## 🟢 Informational / Best Practices

### I-1 — `ensure_admin_user()` silently suppresses all `SQLAlchemyError`

**File:** [`app.py` L57](file:///home/syafiq/bookflow/src/bookflow/app.py#L57)

```python
except SQLAlchemyError:
    pass
```

If the DB is uninitialized, `create_app` silently continues. The app starts, the admin user is not created, and the first request shows a confusing 503. At minimum log at `WARNING` level:

```python
except SQLAlchemyError as exc:
    logger.warning("Could not ensure admin user: %s", exc)
```

---

### I-2 — Global module-level `ElementTree.register_namespace` call

**File:** [`opds/generator.py` L60](file:///home/syafiq/bookflow/src/bookflow/opds/generator.py#L60)

```python
ElementTree.register_namespace("", ATOM_NS)
```

This mutates global `ElementTree` state at import time. It is harmless in production but can interfere with tests or libraries that also register namespaces. Consider moving it into a lazy initialisation block or the feed-building functions.

---

### I-3 — Per-process scratch set has cross-worker limitation under gunicorn

**File:** [`optimizer/service.py` L27–L28](file:///home/syafiq/bookflow/src/bookflow/optimizer/service.py#L27-L28)

`_scratch_running` correctly prevents the scratch-sweep from deleting in-progress files within one process. Under gunicorn multi-worker mode, each worker has its own set and cannot see another worker's running generations. The 15-minute `SCRATCH_EXPIRY_SECONDS` guard mitigates this in practice. Worth a comment explicitly documenting the limitation.

---

### I-4 — Inconsistent env-helper use for `OPDS_DATABASE_URL`

**File:** [`config.py` L118–L121](file:///home/syafiq/bookflow/src/bookflow/config.py#L118-L121)

```python
database_url = os.environ.get("OPDS_DATABASE_URL")
if not database_url:
    database_url = ...
```

All other settings use the uniform `_env()` / `_env_bool()` / `_env_int()` helpers. `OPDS_DATABASE_URL` bypasses them, using `os.environ.get()` directly. Minor inconsistency — worth factoring through `_env()` for uniformity.

---

### I-5 — Cover cache eviction is LRU-unaware (full clear on overflow)

**File:** [`opds/routes.py` L574–L577](file:///home/syafiq/bookflow/src/bookflow/opds/routes.py#L574-L577)

```python
if len(_cover_cache) >= _COVER_CACHE_MAX_ENTRIES:
    _cover_cache.clear()
```

Clearing the entire cache on overflow causes cache-miss storms when 256+ unique covers are frequently requested. Consider `functools.lru_cache` with a size limit, or an `OrderedDict`-based LRU, to evict the least-recently-used entry instead of everything.

---

## Positive Observations

The following deserve explicit praise as examples of solid engineering:

- **Path safety:** `book_file()` unconditionally resolves via `is_relative_to()` with no alternative code paths — invariant 3 is robustly enforced.
- **CSRF:** `require_csrf` is registered via `bp.before_request` on every write-capable blueprint — no per-route decoration needed and hard to miss.
- **Argon2id + timing-safe comparison:** `DatabasePasswordVerifier` uses `hmac.compare_digest` for all string comparisons and runs the dummy hash on failure to mask timing.
- **Auth cache:** Per-process credential caching with a 60-second TTL, keyed on an HMAC of the password (not the plaintext), is a thoughtful optimisation that avoids storing raw passwords in memory.
- **Atomic cache writes:** Optimization results are written to `.tmp/<uuid>` then atomically `Path.replace()`d into the cache — no half-written files served.
- **Thread safety:** Every shared mutable state (cover cache, auth cache, lock registries, rate limiter) is guarded by a `threading.Lock`.
- **Test quality:** 353 tests covering auth, CSRF, rate limiting, pagination, folder hierarchy, progression concurrency, scanner reconciliation, and optimizer cache. The `factories.py` helpers (`make_epub`, `insert_books`, `csrf_token`) are reusable and well-abstracted.
- **Scan isolation:** The scanner never imports `epubkit` or `cbz2xtc` — invariant 2 is enforced structurally, not just by convention.
- **Docker image:** Multi-stage build, non-root `bookflow` user, `PYTHONUNBUFFERED`, and lock-file frozen deps (`uv sync --frozen`).

---

## Findings Checklist

| ID | Severity | File(s) | Status | Resolution / Commit |
|---|---|---|---|---|
| B-1 | 🔴 Bug | `opds/routes.py` cover cache TOCTOU | ✅ Resolved | Single locked lookup + LRU order preservation (`2de97b0`) |
| B-2 | 🔴 Bug | `database/models.py` Progression.progression type | ✅ Resolved | Updated mapped_column annotation to `dict \| float \| None` (`8cd0724`) |
| S-1 | 🟠 Security | `opds/auth.py` no OPDS rate limiting | ✅ Resolved | Wired `LoginRateLimiter` to `authenticate()` + tests (`17a9e50`) |
| S-2 | 🟠 Security | `app.py` no security response headers | ✅ Resolved | Added `nosniff`, `DENY`, and `same-origin` response headers (`6cbbed1`) |
| S-3 | 🟠 Security | `app.py` unauthenticated `/healthz` | ✅ Resolved | Clarified minimal probe semantics; leak-free verified (`06b8658`) |
| S-4 | 🟠 Security | `compose.yml` empty default password | ✅ Resolved | Enforced non-empty variable via compose `${VAR:?...}` (`06b8658`) |
| Q-1 | 🟡 Quality | `admin/routes.py`, `opds/routes.py` duplicated constants | ✅ Resolved | Centralized in `src/bookflow/constants.py` (`746f492`) |
| Q-2 | 🟡 Quality | `database/database.py` deprecated `bind=` | ✅ Resolved | Switched to positional engine argument in `sessionmaker` (`a394b8a`) |
| Q-3 | 🟡 Quality | `optimizer/locks.py`, `library/scanner.py` unbounded lock maps | ✅ Resolved | Documented cardinality bounds and split-lock prevention (`62d64fb`) |
| Q-4 | 🟡 Quality | Multiple files missing return type annotations | ✅ Resolved | Verified 100% type annotation coverage across non-vendored code via AST |
| Q-5 | 🟡 Quality | `library/scanner.py` private `_utcnow()` not shared | ✅ Resolved | Extracted reusable datetime helpers into `src/bookflow/utils.py` (`746f492`) |
| Q-6 | 🟡 Quality | `optimizer/service.py` SQLite-specific import undocumented | ✅ Resolved | Documented SQLite single-engine design coupling (`39ce117`) |
| Q-7 | 🟡 Quality | No standalone push/PR CI workflow | ✅ Resolved | Added `.github/workflows/ci.yml` running ruff and pytest (`d79b4de`) |
| I-1 | 🟢 Info | `app.py` silent SQLAlchemy suppression in startup | ✅ Resolved | Added warning log on DB failure in `ensure_admin_user` (`136684c`) |
| I-2 | 🟢 Info | `opds/generator.py` global namespace mutation at import | ✅ Resolved | Moved `register_namespace` inside `_serialize()` (`d5667e4`) |
| I-3 | 🟢 Info | `optimizer/service.py` per-process scratch set limitation | ✅ Resolved | Documented worker boundaries and scratch expiry window (`39ce117`) |
| I-4 | 🟢 Info | `config.py` inconsistent env helper use | ✅ Resolved | Replaced direct `os.environ.get` with `_env()` (`db522f1`) |
| I-5 | 🟢 Info | `opds/routes.py` cover cache full-eviction on overflow | ✅ Resolved | Implemented `OrderedDict` LRU cache eviction (`2de97b0`) |
