# Walkthrough: Optimizer Configuration in Admin Dashboard

We have added a dedicated **Optimizer** management panel in the BookFlow admin dashboard, enabling administrators to tune on-demand e-ink rendering settings, screen resolutions, and device catalog visibility.

---

## Changes Made

### 1. Database Schema & Models
- **ORM Model** ([`src/bookflow/database/models.py`](file:///home/syafiq/bookflow/src/bookflow/database/models.py#L183-L235)): Created `DeviceProfile` with columns for target display dimensions (`target_width`, `target_height`), comic options (`cbz_dither`, `cbz_split_spreads`, `cbz_rotate_spreads`), EPUB options (`epub_grayscale`, `epub_contrast_boost`, `epub_quality`, `epub_remove_fonts`, `epub_remove_unused_css`, `epub_text_cleanup`), and catalog `enabled` toggle.
- **Alembic Migration** ([`migrations/versions/0005_device_profiles.py`](file:///home/syafiq/bookflow/migrations/versions/0005_device_profiles.py)): Created table and seeded default profiles for `x3` (528×792) and `x4` (480×800) with production defaults.

### 2. Dynamic Optimizer Service & Safe Cache Purging
- **Configuration Dispatch** ([`src/bookflow/optimizer/service.py`](file:///home/syafiq/bookflow/src/bookflow/optimizer/service.py#L36-L130)):
  - Defined `DeviceProfileConfig` and `get_device_profile_config()` with automatic fallback to defaults if database rows are absent.
  - Updated `_generate()` to inject dynamic `ProcessingOptions` into `epubkit` and `ConvertOptions` into `cbz2xtc`.
  - Added `clear_profile_cache(profile)` to safely purge cache files and database records for a single profile.
  - Added guard in `optimize_book()` checking `cfg.enabled`.

### 3. Admin Web Routes & User Interface
- **Navigation** ([`src/bookflow/templates/layout.html`](file:///home/syafiq/bookflow/src/bookflow/templates/layout.html#L20)): Added "Optimizer" tab to the admin site header.
- **Settings View** ([`src/bookflow/templates/optimizer.html`](file:///home/syafiq/bookflow/src/bookflow/templates/optimizer.html)): Created card-based configuration interface showing:
  - Profile status badge (`Enabled` / `Disabled`).
  - Cache statistics (number of cached books and total size) with an inline **Purge Cache** button.
  - Toggle for catalog availability (`/opds<profile>`).
  - Target screen width & height inputs.
  - Comic conversion checkboxes (dithering, spread splitting, rotation).
  - EPUB optimization options (image quality 1–100, grayscale, contrast boost, font stripping, CSS cleanup, text cleanup).
- **Admin Endpoints** ([`src/bookflow/admin/routes.py`](file:///home/syafiq/bookflow/src/bookflow/admin/routes.py#L262-L380)):
  - `GET /admin/optimizer`: Renders current profiles and cache metrics.
  - `POST /admin/optimizer/<profile>`: Validates input bounds, saves updates to SQLite, and automatically purges the profile's cache so new settings take effect immediately.
  - `POST /admin/optimizer/<profile>/clear-cache`: Clears cached renditions for that specific profile on demand.

### 4. OPDS Catalog Availability
- **Root Feed & Route Guards** ([`src/bookflow/opds/routes.py`](file:///home/syafiq/bookflow/src/bookflow/opds/routes.py#L48-L62)):
  - In `_catalog_root()`, subsections for device catalogs (e.g. `X3 Catalog`, `X4 Catalog`) are dynamically omitted if the profile is disabled.
  - Added `_check_profile_enabled()` in `before_request` to return HTTP 404 for any requests targeting disabled device profiles (`/opdsx3/*` or `/opdsx4/*`).

---

## Commits

| Hash | Summary |
|---|---|
| `99994ed` | Add `DeviceProfile` model and Alembic migration `0005` |
| `3bdfcdc` | Integrate dynamic `DeviceProfile` config and cache clearing into optimizer service |
| `c7cb8d5` | Add Optimizer configuration routes, views, and navigation in admin dashboard |
| `e4faeb6` | Honor enabled state for OPDS device catalogs and feeds |

---

## Verification Results

- **Linter:** `uv run ruff check .` passed with 0 errors.
- **Automated Tests:** `uv run pytest -q` passed with **366 passed** in 70.6s (10 new tests added covering database schema, migration downgrade/upgrade, admin form submissions, cache invalidation, input bounds clamping, and OPDS visibility toggles).
