---
name: BookFlow
version: "alpha"
description: A small, self-hosted OPDS server pointing at read-only folders of ebooks with on-demand device-optimized EPUBs & XTC comics (Xteink X3/X4) and reading-position sync.
colors:
  bg: "#f5f6f8"
  surface: "#ffffff"
  border: "#dcdfe4"
  text: "#1c2128"
  muted: "#6b7280"
  primary: "#2f6feb"
  primary-hover: "#2456bb"
  error: "#b42318"
  error-hover-bg: "#fdeceb"
  ok: "#18794e"
  warn: "#a16207"
  badge-ok-bg: "#e6f4ec"
  badge-error-bg: "#fdeceb"
  badge-warn-bg: "#fdf3e1"
  badge-muted-bg: "#f1f3f5"
  flash-info-bg: "#eef3fe"
  flash-info-border: "#cdddfd"
  flash-ok-border: "#bfe3d0"
  flash-error-border: "#f7c9c5"
typography:
  display:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
    fontSize: "1.6rem"
    fontWeight: 600
    lineHeight: 1.2
  h1:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
    fontSize: "1.4rem"
    fontWeight: 700
    lineHeight: 1.3
  h2:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
    fontSize: "1.05rem"
    fontWeight: 600
    lineHeight: 1.4
  h3:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
    fontSize: "1rem"
    fontWeight: 600
    lineHeight: 1.4
  body:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
    fontSize: "0.95rem"
    fontWeight: 400
    lineHeight: 1.5
  caption:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
    fontSize: "0.85rem"
    fontWeight: 500
    letterSpacing: "0.04em"
  code:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "0.9rem"
    fontWeight: 400
    lineHeight: 1.4
rounded:
  none: "0px"
  sm: "6px"
  md: "8px"
  full: "999px"
spacing:
  xs: "0.15rem"
  sm: "0.35rem"
  md: "0.5rem"
  base: "0.75rem"
  lg: "1rem"
  xl: "1.25rem"
  2xl: "1.5rem"
  3xl: "3rem"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.surface}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "0.5rem 1rem"
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.error}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "0.35rem 0.85rem"
  button-secondary-hover:
    backgroundColor: "{colors.error-hover-bg}"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: "1rem 1.15rem"
  card-stat:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: "1rem 1.15rem"
  badge-ok:
    backgroundColor: "{colors.badge-ok-bg}"
    textColor: "{colors.ok}"
    rounded: "{rounded.full}"
    padding: "0.1rem 0.5rem"
  badge-error:
    backgroundColor: "{colors.badge-error-bg}"
    textColor: "{colors.error}"
    rounded: "{rounded.full}"
    padding: "0.1rem 0.5rem"
  badge-warn:
    backgroundColor: "{colors.badge-warn-bg}"
    textColor: "{colors.warn}"
    rounded: "{rounded.full}"
    padding: "0.1rem 0.5rem"
  badge-muted:
    backgroundColor: "{colors.badge-muted-bg}"
    textColor: "{colors.muted}"
    rounded: "{rounded.full}"
    padding: "0.1rem 0.5rem"
  input-text:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "0.5rem 0.6rem"
---

# BookFlow Design System

A formal design specification for BookFlow conforming to the `google-labs-code/design.md` schema standard. It establishes the normative tokens, structural mechanics, visual atmosphere, and operational rules of BookFlow.

## Overview

BookFlow is a small, self-hosted OPDS server. It points at folders of ebooks on disk, indexes them into SQLite without modifying the source files, and serves them over OPDS to standard ebook readers with on-demand hardware optimization for e-ink devices (Xteink X3/X4) and universal reading progression sync.

### Dominating Architectural Shape
BookFlow's interface and design hierarchy reflect its underlying four-tier pipeline:

```text
                 READ ONLY
              ┌──────────────┐
              │ Library      │
              │ filesystem   │
              └──────┬───────┘
                     │ scan (write only to SQLite)
                     ▼
              ┌──────────────┐
              │ SQLite index │
              └──────┬───────┘
        ┌────────────┼────────────┐
        ▼            ▼            ▼
      OPDS        OPDS X3      OPDS X4
        │            │            │
     original   on-demand     on-demand
        │     epubkit/cbz2xtc epubkit/cbz2xtc
        ▼            ▼            ▼
     source       cache        cache
   EPUB / CBZ   EPUB / XTC   EPUB / XTC
        │            │            │
        └────────────┼────────────┘
                     ▼
        OPDS Progression 1.0 (Sync)
```

1. **Authoritative Ingestion Surface:** The physical library filesystem is mounted read-only (`/library`). The server indexes `.epub`, `.pdf`, `.cbz`, `.cbr`, `.mobi`, and `.azw3` files into SQLite. Files are never modified, renamed, moved, or deleted on disk.
2. **FTP-Style Collection Browser:** Direct directory navigation mirroring folder structures on disk, paginated at 50 items (`PAGE_SIZE = 50`) and limited to 500 subfolders per directory level (`FOLDER_SEGMENTS_LIMIT = 500`).
3. **On-Demand Device Optimization:** Device-specific profiles (`x3`, `x4`) serving optimized EPUBs via vendored `epubkit` and converting CBZ comics to native XTC (`application/vnd.xteink.xtc`) via `cbz2xtc`, cached under `data/cache/optimized/{x3,x4}/<book_id>.{epub,xtc}`.
4. **Universal Progression Sync:** OPDS Progression 1.0 sync (`/opds/publications/<id>/progression`) maintaining exactly one progress position per `(user, book)` regardless of which download variant (original, X3, or X4) the client read.

### Atmosphere & Philosophy
* **Utilitarian & Operational:** High information density, immediate legibility, zero decorative clutter. Designed for reliable server management and reading device interoperability.
* **Flat Rigor:** High-contrast neutral boundaries replace heavy drop shadows. Surfaces communicate hierarchy through crisp 1px borders and status-tinted badges.

## Colors

All colors are grounded in `src/bookflow/static/style.css` and server templates. The palette uses a cool, neutral slate canvas with a single functional blue accent and high-contrast semantic indicators.

### Base Canvas & Surfaces
* **`bg` (`#f5f6f8`):** Cool fog page canvas backing all application views.
* **`surface` (`#ffffff`):** Pure white container surface for cards, navigation header, and input fields.
* **`border` (`#dcdfe4`):** Slate hairline stroke for cards, table cell dividers, and resting input borders.
* **`text` (`#1c2128`):** Off-black charcoal for primary text, headlines, and metric integers.
* **`muted` (`#6b7280`):** Cool ash gray for uppercase field labels, filesystem paths, file sizes, and secondary breadcrumbs.

### Interactive Signals
* **`primary` (`#2f6feb`):** Functional signal blue for primary action triggers (Scan, Add Folder, Log in, Save Changes) and active links.
* **`primary-hover` (`#2456bb`):** Deepened blue for hovered buttons and focused interactive elements.

### Status & Feedback Tokens
* **`ok` (`#18794e`):** Forest emerald green indicating healthy components, enabled device profiles, and successful scan status.
* **`badge-ok-bg` (`#e6f4ec`):** Mint tint background for positive status chips and format indicators.
* **`error` (`#b42318`):** Crimson red for scan failure errors, destructive triggers (Remove Folder, Clear Cache), and rate limit alerts.
* **`badge-error-bg` (`#fdeceb`):** Pale crimson tint background for error badges and danger button hover states.
* **`warn` (`#a16207`):** Amber sunlight text for warning indicators and transitional system alerts.
* **`badge-warn-bg` (`#fdf3e1`):** Amber tint background for warning chips.
* **`badge-muted-bg` (`#f1f3f5`):** Soft pebble tint background for neutral or disabled profile badges.
* **`flash-info-bg` (`#eef3fe`):** Pale ice blue for administrative flash notification messages (`#cdddfd` border).

## Typography

Typographic hierarchy enforces rapid scanning through distinct role assignments between system sans-serif and monospace fonts.

### Font Families
* **Primary Sans Stack (`typography.body.fontFamily`):** `system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif`
  Used for wordmarks, page headers, body text, form labels, and buttons.
* **Monospace Stack (`typography.code.fontFamily`):** `ui-monospace, SFMono-Regular, Menlo, monospace`
  Used strictly for filesystem paths (`.path`), folder breadcrumbs (`.browse-crumb`), device identifiers (`device.id`), timestamps, and configuration keys.

### Scale Hierarchy
* **Display Metric (`typography.display`):** `1.6rem` (25.6px), font-weight `600`, line-height `1.2`. Used exclusively for numerical values in dashboard stat cards.
* **Page Heading (`typography.h1`):** `1.4rem` (22.4px), font-weight `700`, line-height `1.3`, bottom margin `1rem`.
* **Section Heading (`typography.h2`):** `1.05rem` (16.8px), font-weight `600`, line-height `1.4`, bottom margin `0.5rem`.
* **Subsection Heading (`typography.h3`):** `1rem` (16px), font-weight `600`, line-height `1.4`, bottom border `1px solid var(--border)`, padding-bottom `0.25rem`.
* **Body Text (`typography.body`):** `0.95rem` (15.2px), font-weight `400`, line-height `1.5`.
* **Data & Field Labels (`typography.caption`):** `0.85rem` (13.6px), font-weight `500`, uppercase, `letter-spacing: 0.04em`, color `var(--muted)`.
* **Code & File Paths (`typography.code`):** `0.9rem` (14.4px), font-weight `400`, monospace, word-break `break-all`.

## Layout & Spacing

Layout geometry is bounded and centered, avoiding wide-open sprawl in favor of compact, easily readable operational tables and grids.

### Container Geometry
* **Default Page Viewport (`main`):** `max-width: 60rem` (960px), centered with `margin: 0 auto; padding: 1.5rem 1.25rem 3rem;`.
* **Focused Authentication Card (`.card.narrow`):** `max-width: 22rem` (352px), centered with `margin: 3rem auto;`.

### Grid Systems
* **Metric Stat Grid (`.grid`):** Auto-fitting multi-column grid:
  `display: grid; grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr)); gap: 1rem; margin-bottom: 1rem;`
* **Two-Column Specification List (`dl.detail`):**
  `display: grid; grid-template-columns: 8rem 1fr; gap: 0.35rem 1rem; margin: 0;`
  Used for component health lists, scanner timing, cache allocations, and profile specifications.

### Spacing Scale
* `spacing.xs` (`0.15rem` / 2.4px): Gap between stat label and numeric value.
* `spacing.sm` (`0.35rem` / 5.6px): Vertical row gap in definition lists.
* `spacing.md` (`0.5rem` / 8px): Gap between buttons in action clusters; form label bottom margin.
* `spacing.base` (`0.75rem` / 12px): Site header vertical padding; table cell padding.
* `spacing.lg` (`1rem` / 16px): Standard card internal padding; grid gap.
* `spacing.xl` (`1.25rem` / 20px): Site header horizontal padding.
* `spacing.2xl` (`1.5rem` / 24px): Main container top padding; card vertical margin in optimizer.
* `spacing.3xl` (`3rem` / 48px): Bottom page padding; vertical margin on narrow login card.

## Elevation & Depth

BookFlow employs a **zero-depth flat aesthetic**. Layering and separation are achieved through perimeter hairline borders, surface contrast, and whitespace rather than drop shadows.

* **Cards & Panels:** Rendered with `background: var(--surface)` and `border: 1px solid var(--border)` with `box-shadow: none`.
* **Site Header:** Anchored to the top of the viewport with a solid white surface and `border-bottom: 1px solid var(--border)`.
* **Modal Dialogs:** Browser-native confirmation prompts (`onsubmit="return confirm(...)"`) are used for destructive actions (e.g., removing folders, purging caches) to preserve zero runtime overhead.
* **Focus States:** Focused inputs and active triggers produce a crisp `outline: 2px solid var(--accent); outline-offset: 1px; border-color: var(--accent);`. No glowing ambient drop shadows.

## Shapes

Geometry in BookFlow is clean, compact, and geometric:

* **Cards & Containers (`rounded.md`):** `border-radius: 8px`. Applied to `.card`, grouping related content into clear visual modules.
* **Form Inputs & Action Buttons (`rounded.sm`):** `border-radius: 6px`. Applied to buttons, text inputs, password fields, and flash notification containers.
* **Status Badges & Chips (`rounded.full`):** `border-radius: 999px`. Pill-shaped indicators for system statuses (`OK`, `error`, `Enabled`, `Disabled`) and file format chips (`EPUB`, `PDF`, `CBZ`).
* **Table Dividers & Separation Lines (`rounded.none`):** `border-radius: 0px`. Straight 1px hairline borders for data table rows and section headers.

## Components

Concrete component styles and behavior rules:

### 1. Header Navigation (`.site-header`)
* Brand label on left (`font-size: 1.05rem; font-weight: 700; color: var(--text); text-decoration: none;`).
* Navigation items on right with `1rem` gap: Dashboard, Library, Users, Optimizer, Health.
* Inline logout form using a borderless button (`.link-button`) matching navigation typography.

### 2. Metric Stat Cards (`.card.stat`)
* Contained within a `.grid` element.
* Upper label in uppercase muted text (`0.85rem`, `letter-spacing: 0.04em`).
* Lower value in bold off-black integer (`1.6rem`, weight `600`).

### 3. Data Table & Browse Lists (`table.data-table`, `.browse-list`)
* Full-width display with collapsed borders.
* Table headers uppercase, muted (`0.85rem`), weight `500`.
* File browser rows (`.browse-row`) display file or directory name on left, followed by format chip (`.badge.badge-ok`) and human-readable file size on right.

### 4. Action Buttons
* **Primary (`button[type="submit"]`, `a.button`):** Background `var(--accent)`, white text, `padding: 0.5rem 1rem; border-radius: 6px;`. Hover: `var(--accent-dark)`.
* **Secondary / Danger (`button.secondary`):** White background, border `1px solid var(--border)`, crimson text (`var(--error)`). Hover: background `#fdeceb`, border-color `var(--error)`.

### 5. Pill Status Badges (`.badge`)
* Dimensions: `padding: 0.1rem 0.5rem; border-radius: 999px; font-size: 0.85rem; display: inline-block;`.
* `.badge-ok`: Emerald text on mint background.
* `.badge-error`: Crimson text on pale crimson background.
* `.badge-warn`: Dark amber text on sunlight amber background.
* `.badge-muted`: Slate text on soft pebble background.

### 6. Device Optimizer Cards (`/admin/optimizer`)
* Each hardware profile (`x3`, `x4`) occupies a dedicated card with an enabled/disabled badge.
* Displays cached rendition count and size with an inline **Purge Cache** trigger.
* Structured into sections: Display Resolution (`target_width`, `target_height`), Comic Options (Floyd-Steinberg dithering, spread splitting/rotation), and EPUB Options (image quality 1–100, e-ink grayscale, contrast boost, font stripping, stylesheet cleanup, text normalization).

## Do's and Don'ts

### Do's
* **Do** treat the library filesystem as immutable and read-only. Every write belongs to SQLite or `data/cache/`.
* **Do** preserve the four-stage architectural pipeline: Ingestion → Directory Catalog → On-Demand Optimization → Progression Sync.
* **Do** paginate file and catalog feeds at 50 items (`PAGE_SIZE = 50`).
* **Do** format file paths and directory breadcrumbs with `ui-monospace` and provide `word-break: break-all`.
* **Do** use exact real labels: "Books", "Folders", "Library size", "Last scan", "Optimization cache", "Health".
* **Do** enforce a single reading progression record per `(user, book)`, synchronized across all download formats.
* **Do** use native HTTP problem documents and Atom/OPDS XML feeds for catalog endpoints.

### Don'ts
* **Don't** invent decorative marketing sections, fake SaaS metrics, or engagement charts.
* **Don't** use purple, pink, or neon glowing drop shadows.
* **Don't** provide UI controls for uploading books, deleting books, renaming files, or editing metadata.
* **Don't** run optimization during scans, background intervals, or application startup; optimization is strictly on-demand.
* **Don't** use multi-level ambient drop shadows; adhere to the 1px border flat system.
* **Don't** hardcode absolute URLs; construct catalog and download endpoints dynamically from the active request context.
