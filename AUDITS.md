# AUDITS.md

Open findings from a codebase audit (2026-09-30, refreshed 2026-10-03):
dead code, refactor candidates and best-practice gaps. Section 1 is
cleared (2026-09-30); twelve items in §2/§3 and two in §4 were cleared
2026-10-02, three more in §3 on 2026-10-03, then the §3 logging finding,
the `library_statistics` shape and all of §5 the same day. Twelve
findings are still open (2 in §2, 5 in §3, 3 in §4, 2 in §6) and are
planned as steps 7–12 under "Suggested order of attack". Tick items off
as they land; delete entries once resolved.

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
- [x] **`library_statistics()` is 116 lines** — **resolved (2026-10-03)**:
      the shape remedy, not the split: it returns `HealthStats`, with
      `FormatStats` / `FolderStats` / `CacheProfile` / `ProgressionStats` /
      `ScannerStats` TypedDicts in `health/service.py`, so what the
      templates read is written down and the empty fallback in
      `admin/routes.py` has the same declared shape. The nine queries stay
      in one function.
- [ ] **Template copy-paste**
      `dashboard.html:74-82` ≡ `health.html:14-22` (badge loop);
      `browse_folders.html:18-27` ≡ `library.html:7-19` (breadcrumbs);
      `browse_folders.html:34-63` ≈ `library.html:32-67` (row blocks).
      Extract `_checks.html` / `_breadcrumbs.html` includes or macros.
- [ ] **Magic session key in template**
      `templates/layout.html:16` hardcodes `session.get('admin')` while
      the code uses `ADMIN_SESSION_KEY` (`auth/service.py:16`). Register
      an `is_admin` Jinja global next to `csrf_token` (`app.py:76`).

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
- [x] **No logging configuration → `logger.info` is silently dropped**
      — **resolved (2026-10-03)**: `Settings.log_level` reads
      `OPDS_LOG_LEVEL` (default `INFO`; unusable values fall back) and
      `app.py:_configure_logging()` runs from `create_app` — root level
      always applied, a stderr handler added only when the process has
      none, so pytest's capture handlers and gunicorn's log setup are left
      alone. Scan summaries and cache-clear notices now reach output.
      `migrations/env.py` passes `disable_existing_loggers=False` to
      alembic's `fileConfig`, which otherwise silenced every BookFlow
      logger for the rest of the process whenever a migration ran
      in-process. Tests: env default/override/unusable values, root level
      after `create_app`, INFO records reaching handlers, the scan report
      logged at INFO. Docs: `REFERENCE.md`, `.env.example`,
      `ARCHITECTURE.md` (§4 steps, §5 table).
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
      `src/bookflow/auth/routes.py:73-81` — 429/503 are used correctly
      elsewhere, so 200 makes success indistinguishable from failure for
      scripts and monitoring. Return 401 (or 422).
- [x] **Rate limiter keyed on `request.remote_addr` with no proxy
      awareness** — **resolved (2026-10-03)**: `OPDS_TRUSTED_PROXY_HOPS`
      (`Settings.trusted_proxy_hops`, default `0`) wraps `app.wsgi_app`
      in werkzeug's `ProxyFix(x_for=…)` only when set, so behind one
      trusted proxy the limiter keys on the client address that proxy
      appended to `X-Forwarded-For`. `REFERENCE.md` documents that the
      limiter is per-process/per-IP (5 failures / 10 min, per worker,
      reset on restart) and that the setting must stay off when BookFlow
      is reachable directly — clients can forge the header.
      `.env.example` documents the variable. Tests: the header is ignored
      by default; with `hops=1` one forwarded client is blocked while
      another is not.
- [x] **`OPDS_ADMIN_PASSWORD: change-me` ships in `docker-compose.yml:14`**
      — **resolved (2026-10-03)**: `compose.yml` ships the key as `""`
      with a comment stating why no default is shipped, so an unedited
      `docker compose up` runs with login locked and OPDS at `503`
      instead of a guessable credential. README/REFERENCE/.env.example
      already say to set it before first start; the stale
      `docker-compose.yml` references in those docs were corrected to
      `compose.yml`.
- [x] **Cache hygiene vs invariant 4** — **resolved (2026-10-03)**:
      `prune_orphaned_cache()` in `optimizer/service.py` unlinks
      `<book_id>.epub` files whose `optimized_books` row is gone (a
      row-less file is a miss), each under its `profile_lock`; the admin
      layer runs it after every scan (`_scan_and_respond`) and after
      `remove_folder` (`folder_delete`) — the two places books leave the
      index — so files no longer accumulate and clear's file/row counts
      agree. `clear_optimized_cache()` no longer `rmtree`s `.tmp`:
      `_generate()` claims its scratch file in `_scratch_running`,
      `_sweep_scratch()` spares claimed entries (stale ones are swept) and
      the `.tmp` directory itself is kept, so a concurrent download's
      `os.replace()` cannot fail; `<book_id>.epub` files are unlinked
      under the profile lock too. Tests: scan prune keeps a sibling's
      cache, folder-delete prune, clear spares an in-flight generation,
      clear sweeps stale scratch.
- [ ] **Optimizer failure logs omit the book id**
      `src/bookflow/optimizer/service.py:227-233, 242` — `"optimization
      failed for %s profile"` names only the profile. Add `book_id`/file
      name; use `logger.exception` at `:242` now that step 6 makes the
      record reach output.
- [ ] **Module-level `app = create_app()` at import time**
      `src/bookflow/app.py:115` reads env, creates `data/`, persists the
      session secret and initializes the engine on import (already a
      documented gotcha — `tests/conftest.py` has to `mkdtemp` at import
      time). Consider a `bookflow/wsgi.py` factory target so importing
      `bookflow.app` is side-effect free.
- [ ] **Progression writes are not serialized per book**
      `src/bookflow/opds/progression.py:73-91` read→compare→write has no
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
      `tests/test_scanner.py:202-217` relies on `chmod 0o000`; root
      (typical CI/Docker) can still read the dir, so the assertion
      flips. Guard with `pytest.mark.skipif(os.geteuid() == 0, ...)` and
      skip the chmod entirely on that path.
- [ ] **Fragile negative assertion** `tests/test_health.py:135`
      `assert b"never" not in resp.data` passes as long as the literal
      never appears anywhere on the page; assert on the rendered
      `last_success` (folder name + timestamp) instead.
- [ ] **Temp dir leaked per session** `tests/conftest.py:14`
      `tempfile.mkdtemp(prefix="bookflow-test-")` is created at import
      time and never removed.

## 5. Type hints / docstrings (lower priority)

- [x] Helper-layer gaps — **resolved (2026-10-03)**: `author_entry`
      returns `ElementTree.Element`; `authenticate()` takes
      `Authorization | None`; the OPDS catalog's ten untyped views and
      `_book_feed` return `Response`, `_title_order` returns
      `ColumnElement[str]` and `_search_condition`
      `ColumnElement[bool]`; `progression`'s `_parse_document`,
      `_parse_timestamp` and `_parse_progression` take `object` (they
      validate the JSON) and `_to_document` returns
      `dict[str, object]`. Also closed the same gap in
      `scanner._reconcile(session: Session, …)` and
      `database._enable_sqlite_foreign_keys(...)`; an AST sweep now finds
      no unannotated function outside the vendored epubkit.
- [x] Route handler return types — **resolved (2026-10-03)**: every view
      in `admin/routes.py`, `auth/routes.py`, `opds/routes.py`,
      `opds/progression.py` and `app.py` declares what it returns —
      `Response`, `str`, or the `str | Response | tuple[str, int]`
      unions the render+status handlers actually produce; the
      abort-only unknown-path views are `Never`.
- [x] Structural typing — **resolved (2026-10-03)**: `HealthStats` (with
      its section TypedDicts) in `health/service.py`, `DashboardStats`
      in `admin/routes.py`, `FolderData` in `library/service.py` replace
      the bare `dict[str, object]` returns; `_stats_or_default()` takes
      `Callable[[], Mapping[str, object]]` so both shapes feed it.
      Runtime values are still plain dicts, so templates and the
      equality assertions are unchanged.

## 6. Minor / cosmetic

- [x] **Unused `noqa` directives (`RUF100`)** — resolved: the four dead
      `# noqa: E402` in `tests/conftest.py` were removed (ruff does not
      flag the `os.environ.setdefault(...)`-before-import pattern), and
      `health/service.py:206`'s `# noqa: F401` is in use, so it stays.
- [ ] Non-default ruff rules flag 62 findings across `ARG, SIM, C4, RUF059`
      (54 `ARG001` + 2 `ARG005` + 2 `SIM117` + 1 `SIM102` + 1 `SIM300`
      + 1 `C416` + 1 `RUF059`, mostly test fixture params — verify
      before acting).
- [ ] `health/service.py:265` imports vendored `process_epub` for a
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
  `TODO`/`FIXME`, no unreachable branches; test suite clean (296 tests).
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
5. ~~§3 cache hygiene + security items~~ **done (2026-10-03)** —
   `prune_orphaned_cache()` runs after every scan and folder removal,
   cache clear spares in-flight scratch files and unlinks under the
   profile locks; `compose.yml` ships an empty `OPDS_ADMIN_PASSWORD` (no
   guessable default); `OPDS_TRUSTED_PROXY_HOPS` + `ProxyFix` make the
   login limiter proxy-aware, documented in `REFERENCE.md`.
6. ~~§3 logging, §5 typing~~ **done (2026-10-03)** — `OPDS_LOG_LEVEL`
   + root-logger configuration in `create_app`, alembic's `fileConfig`
   no longer disables existing loggers, every helper and route handler
   annotated, `HealthStats` / `DashboardStats` / `FolderData`
   TypedDicts; 8 new tests.

Steps 1–6 are done. What remains — 2 findings in §2, 5 in §3, 3 in §4
and 2 in §6 — is planned below: correctness first, then the import
redesign the test cleanup depends on, then refactors and cosmetics.
Each step keeps the standing verify gate (`ruff check .` + full
`pytest`) before its commit.

7. **§3 HTTP correctness** — two API-visible fixes. `login_post` answers
   `401` for a wrong password instead of `200` (429 and 503 keep their
   codes); update `test_auth.py` and any doc that states the status. For
   routing failures, make `create_app`'s app-level fallback run
   `authenticate()` (skipping `opds.authentication`) *before* building
   the error document, so `POST /opds/books` is `401` with the
   Authentication Document without credentials and `405` with the XML
   document once authenticated — a routed request already behaves that
   way. Tests in `test_opds.py` for both halves; if the decision goes the
   other way, write "routing errors are public" into `REFERENCE.md`
   instead.
8. **§3 optimizer + progression hardening** — put the `book_id` and file
   name into both optimizer failure messages
   (`optimizer/service.py:227-233, 242`) and switch the second to
   `logger.exception` so the traceback is kept. Add a per-book lock
   (next to `profile_lock` in `optimizer/locks.py`) around progression's
   read→compare→write so concurrent PUTs answer `201`/`409` instead of a
   SQLite busy-timeout `500`. Tests: the log line names the book; two
   simultaneous PUTs never 500.
9. **§3 import-time side effects** — move `app = create_app()` out of
   `bookflow/app.py` into a new `bookflow/wsgi.py` so importing
   `bookflow.app` reads no env and touches no disk; point the Dockerfile
   `CMD`, `REFERENCE.md`, `ARCHITECTURE.md` (§4, §12) and `AGENTS.md` at
   `bookflow.wsgi:app` and refresh the AGENTS "known gotcha".
   Prerequisite for step 10.
10. **§4 test robustness** — three fixes: a `skipif(os.geteuid() == 0)`
    mark on the unreadable-subdirectory test; replace
    `assert b"never" not in resp.data` with an assertion on the rendered
    `last_success`; and drop `tests/conftest.py:14`'s import-time
    `mkdtemp` for a fixture-owned data dir removed at session end — safe
    only after step 9 made the module import side-effect free.
11. **§2 templates** — register an `is_admin` Jinja global next to
    `csrf_token` and use it in `layout.html:16`; extract the shared
    chunks into `_checks.html` (dashboard/health badge loop),
    `_breadcrumbs.html` (browse/library crumbs) and a row-block macro for
    the browse/library folder + book rows. Keep every CSS class and the
    rendered markup comparable, and the existing template tests green.
12. **§6 cosmetics** — comment the `process_epub` health import as an
    importability check only (invariant 2), then re-run
    `ruff check --select ARG,SIM,C4,RUF059` (62 findings today), fix the
    real ones (`C416`, `SIM*`, `RUF059`, non-fixture `ARG`), and decide
    on evidence whether those rule sets join `[tool.ruff.lint] select` —
    pytest fixture params may argue for leaving `ARG` off or scoping it
    with `per-file-ignores` instead of churning the tests.
