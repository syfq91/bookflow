# AUDITS.md

Findings from a codebase audit (2026-09-30, refreshed 2026-10-03):
dead code, refactor candidates and best-practice gaps. Section 1 is
cleared (2026-09-30); twelve items in §2/§3 and two in §4 were cleared
2026-10-02; the §3 logging finding, the `library_statistics` shape and
all of §5 followed the same day, then the last twelve findings (2 in §2,
5 in §3, 3 in §4, 2 in §6) on 2026-10-03 as steps 7–12 under "Suggested
order of attack". **Nothing is open any more.** Tick items off as they
land; delete entries once resolved.

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
- [x] **Template copy-paste** — **resolved (2026-10-03)**: `_checks.html`
      holds the badge loop (health passes `with_detail = true` for the
      `check.detail` line, dashboard renders the badge alone),
      `_breadcrumbs.html` renders `crumbs` through `crumb_endpoint` +
      `crumb_kwargs`, and `_rows.html` exports the `browse_row(name, href)`
      macro — folder rows call it bare, book/entry rows use `{% call %}`
      for their badge/size/Select tail. CSS classes and rendered markup
      are unchanged (`browse-row`, `browse-crumb`, `aria-current`,
      `badge-*` assertions all still match).
- [x] **Magic session key in template** — **resolved (2026-10-03)**:
      `_is_admin()` (`app.py`) reads `session.get("admin")` and is
      registered next to `csrf_token` in `jinja_env.globals`;
      `layout.html` now says `{% if is_admin() %}`.

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
- [x] **Routing failures skip the OPDS Basic-auth hook** —
      **resolved (2026-10-03)**: the app-level fallback calls
      `require_basic_auth(skip="opds.authentication")` before it builds
      the document, so `POST /opds/books` is `401` with the
      Authentication Document without credentials and `405` with the XML
      document once authenticated; `/opds/authentication` stays public.
      Covers `test_wrong_method_without_credentials_returns_401` and
      `test_authentication_document_stays_public`.
      A failed URL match leaves `request.url_rule` unset, so
      `request.blueprints` is empty and the blueprints'
      `@bp.before_request` never runs: `POST /opds/books` now answers
      `405` **without** credentials while `GET /opds/books` answers
      `401`. Pre-existing (the body was HTML before); found while fixing
      error coverage. Either call `authenticate()` from the app-level
      fallback before returning the document (skip
      `/opds/authentication`) or accept that routing errors are public.
- [x] **Login failure returns HTTP 200** — **resolved (2026-10-03)**:
      `login_post` returns `401` for bad credentials (429 rate-limit and
      503 unset-password keep their codes); the three failure paths in
      `test_auth.py` now assert 401.
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
- [x] **Optimizer failure logs omit the book id** — **resolved
      (2026-10-03)**: `_generate()` takes `book_id` and both messages
      carry `book_id=<id> source=<file> profile=<…>`; the exception path
      switched to `logger.exception` so the traceback is kept. Asserted
      by `test_optimizer_failure_returns_xml_500` and
      `test_optimizer_exception_cleans_up`.
- [x] **Module-level `app = create_app()` at import time** —
      **resolved (2026-10-03)**: the module-level `app` moved to a new
      `bookflow/wsgi.py`; importing `bookflow.app` now reads no env and
      touches no disk. Dockerfile `CMD`, `REFERENCE.md`, `ARCHITECTURE.md`
      (§3, §4, §12) and `AGENTS.md` point at `bookflow.wsgi:app`, and the
      "known gotcha" was rewritten around `create_app()` being the only
      way an app is built.
- [x] **Progression writes are not serialized per book** —
      **resolved (2026-10-03)**: `progression_lock(book_id)` sits next to
      `profile_lock` in `optimizer/locks.py` (same registry guard, own
      key space) and the PUT view takes it across the whole
      read→compare→write: `with progression_lock(book_id),
      session_scope() as session`. `test_progression_put_waits_for_book_lock`
      holds the lock and proves the request blocks (fails without it),
      `test_progression_locks_are_per_book` pins the per-id identity.

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
- [x] **`test_unreadable_subdirectory_reports_partial` fails as root** —
      **resolved (2026-10-03)**: `@pytest.mark.skipif(os.geteuid() == 0,
      reason="root reads regardless of mode bits")`.
- [x] **Fragile negative assertion** `tests/test_health.py:135` —
      **resolved (2026-10-03)**: the page is now asserted positively on
      the rendered `last_scan_at` timestamp read back from the row; the
      `never` negative is gone.
- [x] **Temp dir leaked per session** `tests/conftest.py:14` —
      **resolved (2026-10-03)**: import-time `mkdtemp` replaced by the
      session-scoped autouse `_default_data_dir` fixture, which points
      `OPDS_DATA_DIR` at a scratch dir and removes it at session end —
      safe only once step 9 made the import side-effect free.

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
- [x] **Non-default ruff rules flag 62 findings across `ARG, SIM, C4,
      RUF059`** — **resolved (2026-10-03)**: `C4`, `SIM` and `RUF059`
      joined `[tool.ruff.lint] select`; the three findings that were not
      in vendored code (`SIM300` + 2× `SIM117` in `tests/test_database.py`)
      were fixed, and the two `ARG005` lambda params became `_args` /
      `_kwargs`. The three remaining findings are vendored
      `optimizer/epubkit/` code, kept out by extending its existing
      per-file-ignores (invariant 7 — no edits to vendored files).
      **`ARG` stays off**, deliberately: Flask's `<path:unknown>`
      converters require the parameter by name and pytest fixture
      parameters are part of the fixture contract, so all 54 `ARG001`
      hits are false positives. The rationale is commented in
      `pyproject.toml`.
- [x] `health/service.py:265` imports vendored `process_epub` for a
      health check (no execution) — **resolved (2026-10-03)**: the import
      is now commented as an importability probe that cites invariant 2
      (optimization runs only inside `optimize_book()`).

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
  `TODO`/`FIXME`, no unreachable branches; test suite clean (300 tests).
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

All twelve steps are done: correctness first (7–8), then the import
redesign the test cleanup depended on (9), then test robustness (10),
refactors (11) and cosmetics (12). Each step kept the standing verify
gate (`ruff check .` + full `pytest`) before its commit; the gate after
step 12 was `ruff` clean and 300 tests green.

7. ~~§3 HTTP correctness~~ **done (2026-10-03)** — `login_post` answers
   `401` for bad credentials (429/503 unchanged) and the three failure
   paths in `test_auth.py` assert it; the app-level routing fallback
   runs `require_basic_auth(skip="opds.authentication")` under the OPDS
   prefixes *before* building the document, so `POST /opds/books` is
   `401` with the Authentication Document unauthenticated and `405` with
   the XML document once authenticated. Two new `test_opds.py` tests
   cover both halves and that `/opds/authentication` stays a public
   `405`.
8. ~~§3 optimizer + progression hardening~~ **done (2026-10-03)** —
   `_generate(book_id, source, cache_file, profile)` now logs
   `book_id=<id> source=<file> profile=<…>` at both failure sites and
   the exception path uses `logger.exception` (traceback kept); both
   messages are asserted by the existing 500 tests. `progression_lock`
   (next to `profile_lock`, own key space under the same guard) wraps the
   PUT's read→compare→write, with a lock-blocked test that fails without
   it and a per-book identity test.
9. ~~§3 import-time side effects~~ **done (2026-10-03)** —
   `app = create_app()` lives in the new `bookflow/wsgi.py`; importing
   `bookflow.app` reads no env and touches no disk. Dockerfile `CMD`,
   `REFERENCE.md`, `ARCHITECTURE.md` (§3 layout, §4, §12), `AGENTS.md`
   and the known-gotcha bullet all say `bookflow.wsgi:app` now
   (`flask --app bookflow.app` still uses the factory).
10. ~~§4 test robustness~~ **done (2026-10-03)** — root now skips the
    unreadable-subdirectory test; the health page is asserted on the
    rendered `last_scan_at` timestamp instead of a `never` negative; and
    the import-time `mkdtemp` in `tests/conftest.py` became the
    session-scoped autouse `_default_data_dir` fixture, which removes its
    scratch dir at session end.
11. ~~§2 templates~~ **done (2026-10-03)** — `is_admin` is registered
    next to `csrf_token` and used by `layout.html`; `_checks.html`
    (health passes `with_detail`), `_breadcrumbs.html` (`crumbs` +
    `crumb_endpoint`/`crumb_kwargs`) and the `browse_row` macro in
    `_rows.html` (`{% call %}` carries the badge/size/Select tail) cover
    the three copy-paste pairs. CSS classes and markup are unchanged and
    the template tests stayed green.
12. ~~§6 cosmetics~~ **done (2026-10-03)** — the `process_epub` import
    carries an invariant-2 comment; `C4`/`SIM`/`RUF059` joined
    `[tool.ruff.lint] select` after fixing the three non-vendored
    findings (`SIM300`, 2× `SIM117` in `tests/test_database.py`) and the
    two `ARG005` lambdas, with the vendored `epubkit/` findings excluded
    through the existing per-file-ignores (invariant 7 — untouched).
    `ARG` stays off with a `pyproject.toml` comment: `<path:unknown>`
    needs its parameter by name and fixture params are contractual, so
    its 54 hits are false positives.
