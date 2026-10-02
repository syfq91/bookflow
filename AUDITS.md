# AUDITS.md

Open findings from a codebase audit (2026-09-30, refreshed 2026-10-02):
dead code, refactor candidates and best-practice gaps. Section 1 is
cleared (2026-09-30); twelve items in §2/§3 and two in §4 were cleared
2026-10-02. Tick items off as they land; delete entries once resolved.

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
- [x] **Path validation duplicated three times** — **resolved (2026-10-02)**:
      `resolve_readable_dir(value) -> tuple[Path | None, str | None]` in
      `library/paths.py` is now the one place that checks absolute /
      resolvable / directory / readable and produces the user-facing
      messages; `add_folder()`, `browse_directory()` and the scanner's
      `_unavailable_reason()` all call it (the last wraps the reason in
      its "Folder is unavailable" prefix).
- [x] **CSRF enforced two different ways** — **resolved (2026-10-02)**:
      `require_csrf()` in `auth/decorators.py` is the single
      write-method hook; `bp.before_request(require_csrf)` is registered
      on both the `admin` and `auth` blueprints, so the inline
      `validate_csrf()` checks in `login_post`/`logout` are gone.
- [x] **HTTP-Basic `before_request` duplicated** — **resolved
      (2026-10-02)**: `require_basic_auth(skip=...)` in `opds/auth.py` is
      the one hook; the catalog registers it with
      `partial(..., skip="opds.authentication")`, the progression
      blueprint registers it plain.
- [x] **File-download response duplicated** — **resolved (2026-10-02)**:
      `send_book_response(target, *, download_name=None, mimetype=None)`
      in `library/paths.py` next to `book_file()` streams the attachment
      (`conditional=True`, MIME type from `book_mime_type()`); it serves
      the admin original download, `/opds/download/<id>` and the X3/X4
      optimized download (which only overrides `download_name`).
- [x] **Scan trigger + flash logic duplicated in `admin`** — **resolved
      (2026-10-02)**: `_scan_and_respond(folder_id, path)` in
      `admin/routes.py` runs the scan, flashes the busy/scan message and
      returns either the folders page (`409`) or the redirect to
      `/admin/folders`; `folder_create` and `folder_scan` are now one
      call each, and the create-route busy message is the shared
      "A scan for this folder is already in progress.".
- [x] **Two `OperationalError` fallbacks with a duplicated string** —
      **resolved (2026-10-02)**: `_stats_or_default(load, empty)` in
      `admin/routes.py` is the one `try/except OperationalError`, with
      the message in `_DB_UNINITIALIZED_MESSAGE`; `_empty_health_stats()`
      and `_empty_dashboard_stats()` each supply their page's *complete*
      shape, so `health.html` no longer depends on its `db_error` guard
      to render. New test asserts both fallback shapes.
- [x] **`get_folder()` loads the whole folder list for one row** —
      **resolved (2026-10-02)**: it now does `session.get(LibraryFolder,
      folder_id)` plus one `count`/`sum` query for that row, and the dict
      shape is shared with `list_folders()` through `_folder_data()`, so
      the two can no longer drift.
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

- [x] **Scanner holds a DB session across the whole filesystem walk**
      — **resolved (2026-10-02)**: `_scan()` runs `_snapshot()` (folder
      path + how the index sees its files) first, then
      `_unavailable_reason()` / `_walk()` / `_changed_fields()` (metadata
      for new or changed files) with **no session open**, and only then
      one session for `_reconcile()` (diff/apply) plus the `last_scan_*`
      writes. `_reconcile()` takes the pre-parsed `fields`, so the write
      session never touches the disk; its fallback re-parses a row that
      vanished under the scan lock. New test
      (`test_scan_walks_and_parses_with_no_session_open`) asserts the
      walk and the metadata parse both run with zero open sessions.
- [x] **`library_tree` loads an entire subtree with no `LIMIT`**
      — **resolved (2026-10-02)**: both predicates are SQL now —
      `distinct()` + `limit()` for the child segments (mirroring
      `opds/routes.py:_folder_level`) and `instr(rest, '/') == 0` for
      direct children — selecting only the four columns the template
      shows instead of hydrating whole `Book` rows. Books paginate at
      `PAGE_SIZE` = 50 via `?page=` (`admin/routes.py:_page()` mirrors
      the OPDS helper) with prev/next links in `library.html`.
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
        (`library/scanner.py:205-207`,
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

- [x] **Scaffolding duplicated across ~400 lines** — resolved:
      `tests/conftest.py` owns the `build_app` factory (runs `alembic
      upgrade head`, `migrate=False` for empty-DB tests, resets the engine
      around each test) plus the shared `app`, `client` and `root`
      fixtures; `tests/factories.py` owns `alembic_config`,
      `login_admin`, `insert_books` and the feed helpers (`parse_feed`,
      `feed_entries`, `feed_titles`, `feed_links`, `entry_links`,
      `links_by_rel`). Per-suite `_login`, `_parse`, `_entries`,
      `_titles`, `_feed_links`, `_entry_links`, `_links_by_rel`,
      `_insert`/`_insert_book`, and the shadowed `app`/`client`/`root`/
      `alembic_config` copies are gone; what stays suite-local is genuinely
      local (`_headers`/`_get`/`_scan`, `test_progression.py`'s datetime
      `_parse`, count/title-based seed wrappers). The drift cited here
      (`_entry_links` returning a list in one suite and a dict in two) is
      resolved: `entry_links` lists links, `links_by_rel` groups them.
      AGENTS.md fixture bullet updated in the same commit.
- [x] **23 legacy `session.query()` calls** — resolved: every site is
      `session.scalars(select(...))` (plus the count queries already on
      `select(func.count(...))`); `session.query` no longer appears in any
      `.py` file outside the vendored optimizer.
- [ ] **`test_unreadable_subdirectory_reports_partial` fails as root**
      `tests/test_scanner.py:201-217` relies on `chmod 0o000`; root (typical
      CI/Docker) can still read the dir, so the assertion flips. Guard
      with `pytest.mark.skipif(os.geteuid() == 0, ...)`.
- [ ] **Fragile negative assertion** `tests/test_health.py:134`
      `assert b"never" not in resp.data` passes as long as the literal
      never appears anywhere on the page; assert on the rendered
      `last_success` instead.
- [ ] **Temp dir leaked per session** `tests/conftest.py:14`
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
      (`health/service.py:43`, `admin/routes.py:337,348`) — consumers are
      Jinja templates, so typos surface as template errors. Consider a
      frozen dataclass / `TypedDict`.

## 6. Minor / cosmetic

- [x] **Unused `noqa` directives (`RUF100`)** — resolved: the four dead
      `# noqa: E402` in `tests/conftest.py` were removed (ruff does not
      flag the `os.environ.setdefault(...)`-before-import pattern), and
      `health/service.py:206`'s `# noqa: F401` is in use, so it stays.
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
  `TODO`/`FIXME`, no unreachable branches; test suite clean (277 tests).
- No queries in Jinja templates; no N+1 (`list_folders`,
  `library_statistics`, `_query_stats` are aggregate; `get_folder` is a
  fixed two-query lookup and `book_file()` one row).
- Every template and static asset is referenced; `flash` categories are
  generated dynamically.
- Test mocking is confined to the epubkit boundary, env vars and one
  `MAX_ENTRIES` constant — no over-mocking.

## Suggested order of attack

1. ~~§2 OPDS feed helper + §3 error-handler set~~ **done (2026-10-02)** —
   `_book_page`/`_acquisition_response`, `HTTPException` handlers on both
   OPDS blueprints, an app-level fallback for routing failures, docs
   refreshed, 5 new tests.
2. ~~§3 scanner session split + `library_tree` limit~~ **done
   (2026-10-02)** — `_scan()` split into snapshot → filesystem work →
   one write session; `library_tree` predicates pushed into SQL plus
   `?page=` pagination; 2 new tests.
3. ~~§4 test scaffolding + `session.query` migration~~ **done
   (2026-10-02)** — conftest gained the `build_app` factory plus shared
   `app`/`client`/`root`; factories gained `alembic_config`,
   `login_admin`, `insert_books` and the feed helpers; per-suite
   `_login`/`_parse`/`_insert*` and the shadowed fixtures were removed;
   all 23 `session.query()` sites moved to `select()`; AGENTS.md fixture
   bullet updated in the same commit.
4. ~~§2 mechanical dedup~~ **done (2026-10-02)** — paths, CSRF and
   HTTP-Basic hooks shared in their owning modules; `send_book_response()`
   serves every original-file download; `_scan_and_respond()` is the one
   scan+flash path for both admin folder routes; `_stats_or_default()`
   answers a missing database with each page's complete shape;
   `get_folder()` reads a single row through the shared `_folder_data()`
   builder.
5. **§3 cache hygiene + security items** (`change-me` password, proxy-
   aware limiter).
6. **§3 logging, §5 typing** — good first-issue batch.
