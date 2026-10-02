# AUDITS.md

Open findings from a codebase audit (2026-09-30, refreshed 2026-10-02):
dead code, refactor candidates and best-practice gaps. Research only —
nothing here has been fixed yet (section 1 resolved 2026-09-30; three
items in §2/§3 resolved 2026-10-02).
Tick items off as they land; delete entries once resolved.

Scope: `src/bookflow/**`, `tests/**`, docs and deploy files. Vendored
`src/bookflow/optimizer/epubkit/` was excluded. Every finding was verified
by reading the cited code, not just grepping.

---

## 1. Dead code / inert configuration

All seven findings **resolved (2026-09-30)** — nothing here is open:

- `Settings.host` / `Settings.port` and the `OPDS_HOST` / `OPDS_PORT` env
  vars removed; nothing ever bound them. The bind address is set by the
  entrypoint instead: `--port` for `flask run`, `--bind 0.0.0.0:8000` in
  the Dockerfile, `ports:` in `docker-compose.yml`.
- `get_engine_instance()` deleted (zero callers).
- `LibraryFolder.enabled` and `OptimizedBook.optimized_path` dropped from
  the models, the writes and the tests, plus migration `0002` (both
  directions verified).
- Dead `@bp.errorhandler(403)` deleted — no `abort(403)` exists in `src/`.
- `pytest-cov` removed from the dev group; `uv.lock` re-synced.
- Docs: `.env.example` now documents `OPDS_DATABASE_URL` and points at
  `REFERENCE.md` (not `README.md`) for the setting table;
  `docker-compose.yml` lists the commented `OPDS_BROWSE_ROOT`.

Checked and **not** dead (do not "clean up"): all route handlers (some
reached via dynamic `url_for`), all templates and `static/style.css`
(including `.flash-ok`/`.flash-error`, generated as
`flash flash-{{ category }}`), `MAX_CONTENT_LENGTH`, and the deps
`cssutils`/`lxml`/`pillow` (epubkit runtime path) and `gunicorn`
(Dockerfile CMD).

## 2. Refactoring — duplication

- [x] **Five OPDS feed handlers repeat the same pagination/response block**
      — **resolved (2026-10-02)**: `_book_page()` (count, `updated`
      stamp, one page of rows) + `_acquisition_response()` (self/next
      links, `acquisition_feed`, `Response`) in `opds/routes.py` now
      build every acquisition feed; `_books_feed`, `_recent_feed`,
      `_author_feed`, `_search_feed` and `_folder_level` are thin
      wrappers, and `_page_of_books` is gone. `_catalog_root` /
      `_folders_index` / `_authors_feed` keep their navigation feeds but
      already shared `_registered_folders()` + `_folder_items()`.
- [ ] **Path validation duplicated three times**
      `src/bookflow/library/service.py:40-52` ≡
      `src/bookflow/library/browse.py:52-66` (byte-identical sequence and
      user-facing messages), with a third variant in
      `src/bookflow/library/scanner.py:207-214`. Extract e.g.
      `resolve_readable_dir(value) -> tuple[Path | None, str | None]`
      into `library/paths.py`.
- [ ] **CSRF enforced two different ways**
      Blueprint hook `src/bookflow/admin/routes.py:46-50` vs inline
      `if not validate_csrf(): abort(400, ...)` at
      `src/bookflow/auth/routes.py:35-36` and `:87-88` (different
      blueprints is the root cause). Share one `require_csrf` decorator
      or register the same hook on `auth`.
- [ ] **HTTP-Basic `before_request` duplicated**
      `src/bookflow/opds/routes.py:54-59` ≡
      `src/bookflow/opds/progression.py:43-46`. Move to a shared
      `require_basic_auth(skip=...)` in `opds/auth.py`.
- [ ] **File-download response duplicated**
      `src/bookflow/admin/routes.py:202-212` ≡
      `src/bookflow/opds/routes.py:623-632` (identical `send_file(...)`
      body). One `send_book_response(target)` helper.
- [ ] **Scan trigger + flash logic duplicated in `admin`**
      `src/bookflow/admin/routes.py:109-125` vs `:275-288`. Make
      `_run_scan()` return the flash payload; both callers flash it.
- [ ] **Two `OperationalError` fallbacks with a duplicated string**
      `src/bookflow/admin/routes.py:309-318` and `:320-334` return
      *different shapes* for the same failure (health returns only
      `{"db_error"}`; dashboard returns a full default). `health.html:25`
      is only safe because of its guard. One `_stats_or_default()` helper
      returning the complete shape.
- [ ] **`get_folder()` loads the whole folder list for one row**
      `src/bookflow/library/service.py:88-91` builds
      `{f["id"]: f for f in list_folders()}`; called from
      `admin/routes.py:112,131,153`. Use `session.get(LibraryFolder, id)`
      for the single-row case.
- [ ] **`library_statistics()` is 116 lines**
      `src/bookflow/health/service.py:43-158` — nine queries, six dict
      sections, one function; return shape is `dict[str, object]`
      enforced only by templates. Split per section (natural break points
      at `:93` and `:119`) or return a TypedDict/dataclass.
- [ ] **Template copy-paste**
      `dashboard.html:74-82` ≡ `health.html:14-22` (badge loop);
      `browse_folders.html:18-27` ≡ `library.html:7-19` (breadcrumbs);
      `browse_folders.html:34-63` ≈ `library.html:32-67` (row blocks).
      Extract `_checks.html` / `_breadcrumbs.html` includes or macros.
- [ ] **Magic session key in template**
      `templates/layout.html:16` hardcodes `session.get('admin')` while
      the code uses `ADMIN_SESSION_KEY` (`auth/service.py:16`). Register
      an `is_admin` Jinja global next to `csrf_token` (`app.py:38`).

## 3. Best practice / correctness

- [ ] **Scanner holds a DB session across the whole filesystem walk**
      `src/bookflow/library/scanner.py:92-114` — `session_scope()`
      wraps `_unavailable_reason()`, `_walk()` and `_reconcile()` (which
      parses zip/PDF metadata per changed file). Violates "keep sessions
      short; never hold one across I/O" and keeps a pooled connection
      checked out for the whole scan. Do the FS work first, then open the
      session only for the diff/apply and `last_scan_*` updates.
- [ ] **`library_tree` loads an entire subtree with no `LIMIT`**
      `src/bookflow/admin/routes.py:161-181` — prefix predicate only;
      Python filters to direct children afterwards. Opening the root of a
      large folder hydrates every descendant ORM row. Push the
      "no further `/`" predicate into SQL and add pagination (mirror the
      OPDS `_page()`/`PAGE_SIZE`).
- [ ] **No logging configuration → `logger.info` is silently dropped**
      Loggers at `library/scanner.py:23`, `library/metadata.py:20`,
      `optimizer/service.py:19`; no `basicConfig`/`dictConfig` anywhere
      and no `log_level` setting, so the root logger stays at WARNING.
      Scan results and cache-clear events never reach output. Add
      `OPDS_LOG_LEVEL` to `Settings` and configure logging in
      `create_app`.
- [x] **Incomplete error coverage on `/opds/*`**
      — **resolved (2026-10-02)**: both blueprints register
      `@bp.errorhandler(HTTPException)` (`opds/routes.py`
      `error_document`, `opds/progression.py` `problem_document`), which
      covers 400/405/414/416/500 *and* unhandled exceptions (they arrive
      as `InternalServerError`); progression keeps the registry types for
      400/404/409. A failed URL match never reaches a blueprint, so
      `create_app` also registers an app-level handler that answers
      `request.path` under `/opds/publications` with the problem document
      and under `/opds` with the XML one, returning every other request
      unchanged (admin stays HTML). `progression.py` gained a
      `GET /<path:unknown>` rule so unknown ids are a problem document
      instead of the catalog's XML 404. Tests: wrong-method + unhandled
      500 in `test_opds.py`, wrong-method + non-numeric id in
      `test_progression.py`, admin-405-stays-HTML in `test_app.py`.
      Docs: `REFERENCE.md` (OPDS + progression error paragraphs) and
      `ARCHITECTURE.md` (§7.3, error-by-surface table) updated.
- [x] **`root_feed` swallows `OperationalError` while child feeds 500**
      — **resolved (2026-10-02)**: the `try/except OperationalError`
      around `max(updated_at)` in `_catalog_root` is gone. It could not
      have helped anyway: `_registered_folders()` opens a session first
      and would raise before it. A broken database now fails the root
      feed the same way it fails every child.
- [ ] **Routing failures skip the OPDS Basic-auth hook**
      A failed URL match leaves `request.url_rule` unset, so
      `request.blueprints` is empty and the blueprints'
      `@bp.before_request` never runs: `POST /opds/books` now answers
      `405` **without** credentials while `GET /opds/books` answers
      `401`. Pre-existing (the body was HTML before); found while fixing
      error coverage. Either call `authenticate()` from the app-level
      fallback before returning the document (skip
      `/opds/authentication`) or accept that routing errors are public.
- [ ] **Login failure returns HTTP 200**
      `src/bookflow/auth/routes.py:75-82` — 429/503/400 are used
      correctly elsewhere, so 200 makes success indistinguishable from
      failure for scripts and monitoring. Return 401 (or 422).
- [ ] **Rate limiter keyed on `request.remote_addr` with no proxy
      awareness** `src/bookflow/auth/routes.py:43`; no `ProxyFix`
      anywhere. Behind a documented reverse-proxy deployment every client
      shares one IP, so one attacker failing logins locks *everyone* out
      of `/admin/login` for 10 minutes (self-DoS). Document that the
      limiter is per-process/per-IP and add an opt-in trusted-proxy
      setting.
- [ ] **`OPDS_ADMIN_PASSWORD: change-me` ships in `docker-compose.yml:14`**
      Now that `ghcr.io/syfq91/bookflow:latest` is published, an
      unedited `docker compose up` exposes UI + OPDS catalog with a
      guessable credential. Prefer leaving it unset (the app already
      locks login/returns 503) or requiring it from the environment.
- [ ] **Cache hygiene vs invariant 4**
      - Deleting books/folders removes `optimized_books` rows
        (`library/scanner.py:156-158`,
        `library/service.py:80-85`) but never the `.epub` files, so
        files accumulate (correctness holds — a row-less file is a miss —
        but `clear_optimized_cache()`'s file/row counts drift). Unlink
        `<book_id>.epub` on delete under `profile_lock`, or reconcile on
        clear.
      - `clear_optimized_cache()` (`optimizer/service.py:64-69`)
        rmtree's profile dirs including `.tmp/` (`service.py:114`) while
        `_generate()` may be writing there, without taking any
        `profile_lock` — the later `os.replace()` (`service.py:132`)
        then fails and the concurrent download 500s. Skip `.tmp` (and
        sweep it separately) or gate on the locks.
- [ ] **Optimizer failure logs omit the book id**
      `src/bookflow/optimizer/service.py:124-128, 138` — `"optimization
      failed for %s profile"` names only the profile. Add `book_id`/file
      name; consider `logger.exception` at `:136`.
- [ ] **Module-level `app = create_app()` at import time**
      `src/bookflow/app.py:56` reads env, creates `data/`, persists the
      session secret and initializes the engine on import (already a
      documented gotcha — `tests/conftest.py` has to `mkdtemp` at import
      time). Consider a `bookflow/wsgi.py` factory target so importing
      `bookflow.app` is side-effect free.
- [ ] **Progression writes are not serialized per book**
      `src/bookflow/opds/progression.py:75-92` read→compare→write has no
      lock, so two devices PUTting simultaneously can hit a busy-timeout
      500 instead of a clean 409. Mirror the per-book locks in
      `optimizer/locks.py`.

## 4. Tests

- [ ] **Scaffolding duplicated across ~400 lines**
      `def alembic_config` copied **11×**, the shadowed `app` fixture
      **9×** (in test files), `client`/`root` ~10×, `_login()` 5×
      (`test_admin_books.py:57`, `test_auth.py:60`, `test_health.py:85`,
      `test_library.py:75`, `test_optimizer.py:125`), plus five
      differently-shaped seed helpers (`test_opds.py:129 _insert`,
      `test_opds_folders.py:118 _insert`,
      `test_device_catalogs.py:125 _insert_books`,
      `test_optimizer.py:99 _insert_book`, `test_progression.py:101
      _book_id`). Drift is already visible: `test_opds.py:109
      _entry_links()` returns a list, while
      `test_device_catalogs.py:103` and `test_opds_folders.py:94`
      return dicts. Move `alembic_config`, a migrated `app` fixture,
      `login_admin`, feed parsing and seeding into `tests/conftest.py` /
      `tests/factories.py`, and update the AGENTS.md fixture bullet in the
      same commit.
- [ ] **23 legacy `session.query()` calls** — AGENTS.md requires
      `select()`. Sites: `test_scanner.py:59,64,186,194,195`,
      `test_admin_books.py:248,269`,
      `test_library.py:100,175,176,353,390,402,426,454,465,466,617`,
      `test_optimizer.py:96`, `test_opds.py:126`,
      `test_database.py:60,77,97`. Mechanical swap to
      `session.scalars(select(...))` / `select(func.count(...))`.
- [ ] **`test_unreadable_subdirectory_reports_partial` fails as root**
      `tests/test_scanner.py:213-229` relies on `chmod 0o000`; root (typical
      CI/Docker) can still read the dir, so the assertion flips. Guard
      with `pytest.mark.skipif(os.geteuid() == 0, ...)`.
- [ ] **Fragile negative assertion** `tests/test_health.py:181`
      `assert b"never" not in resp.data` passes as long as the literal
      never appears anywhere on the page; assert on the rendered
      `last_success` instead.
- [ ] **Temp dir leaked per session** `tests/conftest.py:11`
      `tempfile.mkdtemp(prefix="bookflow-test-")` is created at import
      time and never removed.

## 5. Type hints / docstrings (lower priority)

- [ ] Helper-layer gaps: `opds/generator.py:185 author_entry` (no return
      type), `opds/auth.py:19 authenticate` (untyped `authorization`),
      `opds/routes.py:482,487,492,496,515,527,546,551,559,654,764`
      (no return types), `opds/progression.py:143,174,189,198` (untyped
      params; `dict` → `dict[str, object]`).
- [ ] ~35 route handlers across `admin/routes.py`, `opds/routes.py`,
      `auth/routes.py`, `opds/progression.py`, `app.py` lack
      `-> Response | str | tuple[...]`.
- [ ] Structural typing: `list[dict[str, object]]`
      (`library/service.py:94`) and `dict[str, object]`
      (`health/service.py:43`, `admin/routes.py:309,320`) — consumers are
      Jinja templates, so typos surface as template errors. Consider a
      frozen dataclass / `TypedDict`.

## 6. Minor / cosmetic

- [ ] Four unused `noqa` directives (`RUF100`): `health/service.py:206`,
      `tests/conftest.py:13,14,15`.
- [ ] Non-default ruff rules flag 55 findings across `ARG, SIM, C4, RUF059`
      (49 `ARG` + 4 `SIM` + 1 `C4` + 1 `RUF059`, mostly test fixture
      params — verify before acting).
- [ ] `health/service.py:206` imports vendored `process_epub` for a
      health check (no execution) — harmless, but add a comment so it is
      not mistaken for a violation of invariant 2.

---

## Verified clean — do not "fix"

- All seven AGENTS.md invariants hold: library is read-only; epubkit is
  called only from `optimize_book()` (`optimizer/service.py:118`); all
  paths go through `library/paths.py:18` (`resolve()` +
  `is_relative_to`); cache/index agree (row-less file ⇒ miss);
  progression is one row per book with centralized naive↔UTC helpers;
  single credential, no secrets in logs; vendored epubkit untouched.
- `os.environ` is read only in `config.py`.
- `ruff check .` passes; `F401`/`F811` clean; no commented-out code, no
  `TODO`/`FIXME`, no unreachable branches; test suite clean (273 tests).
- No queries in Jinja templates; no N+1 (`list_folders`,
  `library_statistics`, `_query_stats` are aggregate; `get_folder` and
  `paths.py:24` are fixed 2-query lookups).
- Every template and static asset is referenced; `flash` categories are
  generated dynamically.
- Test mocking is confined to the epubkit boundary, env vars and one
  `MAX_ENTRIES` constant — no over-mocking.

## Suggested order of attack

1. ~~§2 OPDS feed helper + §3 error-handler set~~ **done (2026-10-02)** —
   `_book_page`/`_acquisition_response`, `HTTPException` handlers on both
   OPDS blueprints, an app-level fallback for routing failures, docs
   refreshed, 5 new tests.
2. **§3 scanner session split + `library_tree` limit** — correctness-
   adjacent, user-visible performance.
3. **§4 test scaffolding + `session.query` migration** — biggest raw
   line-count win; update AGENTS.md in the same commit.
4. **§2 mechanical dedup** (paths, CSRF, basic auth, download, scan
   flash, stats fallback, `get_folder`) — safe as small separate commits.
5. **§3 cache hygiene + security items** (`change-me` password, proxy-
   aware limiter).
6. **§3 logging, §5 typing** — good first-issue batch.
