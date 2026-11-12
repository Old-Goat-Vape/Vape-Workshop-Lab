# VC background recipe import

The Lab now imports every publicly accessible recipe exposed by VC's public recipe listing, or the listing of a specified numeric VC user ID. It does not access private VC recipes. Imported TVW recipes remain private reference entries with source/author attribution; POD, adapted and single-flavour imports are independent of benchmark eligibility.

## Use

After `tvw-update`, open Master Admin Console → Scrapers → VC Recipe Import. Click **Import all VC recipes** once. Closing the page does not stop the server worker. Progress polls every five seconds. Pause finishes the current page/recipe; Resume continues it. Retry failed recipes resets only failed queue items. Download report includes every discovered ID and its outcome. Starting again after completion performs a fresh listing sweep and reuses complete private library entries.

VC's public listing returned 2,353 recipes / 236 pages on 12 November 2026. Counts are source-supplied and may change. Profile imports use the real profile DataTable, 50 IDs/page. Public imports use the site's documented-by-frontend pagination controller, not guessed numeric IDs. A source error or repeated page pauses rather than falsely reporting completion. Empty first-page responses require review. Last-page metadata terminates discovery; an empty page is the fallback when metadata is unavailable.

## Persistence and concurrency

`vc_import_jobs`, `vc_import_job_items`, `vc_import_job_pages` are created idempotently. No extra packages or systemd changes. A daemon thread starts with the existing Lab scheduler startup and is also ensured on start/resume. Each step uses a fresh Flask app context / SQLite connection. A transactionally acquired database lease permits one consumer across Gunicorn processes. No network request holds the lease acquisition transaction open. A server restart resumes queued/running jobs after their previous lease expires (at most ten minutes). Paused jobs stay paused. The deployed Gunicorn configuration does not preload the app.

One step reads one listing page or processes one recipe. Recipe commits and queue progress are durable separately: if the process dies between them, the next step verifies the existing private current version and does not duplicate it. Per-recipe processing omits repeated whole-library maintenance/backfill; it preserves existing validation, identity resolution, append-only recipe versions, and private source attribution.

Failed items retry three times with backoff, then remain in the report. Listing/network failures back off and pause after three attempts. HTTP 403/429/503 pauses immediately for owner review; no automatic block bypass. Completed-with-errors is distinct from complete. Source pages can change during a long scan; a fresh sweep can pick up additions. There is no guarantee that inaccessible/invalid source recipes can be imported, and they are never silently counted as successful.

## Diagnostics

Page progress, job actions and failures use Flask logging rather than buffered print. Legacy VC trigger aliases queue background work (including scheduled/run-all entry points). Scraper health includes historical profile alias runs under the VC recipe source. Legacy trigger success means queued; the VC Recipe Import panel is authoritative for background completion. JSON/HTTP/session failures are visible and successful diagnostics no longer disappear after 800 ms.

## Validation

- Queue tests: page traversal, duplicate IDs, terminal totals, empty/repeated responses, bounded retries, source-rate pause, in-flight pause/resume, crash lease recovery and cross-job exclusion.
- Route tests: member denial, owner-only start/poll/control/report and immediate HTTP 202.
- Existing VC tests: source parsing, private/current-version writes, preserved old versions, idempotency and full targeted pipeline with per-recipe maintenance disabled.
- Node UI tests: non-JSON HTTP error, expired session, accurate progress/failure controls, escaped text-only error rendering.
- Live source smoke test in an isolated temporary database: first listing page discovered ten IDs, source totals 2,353 / 236 pages; one real detail import created a private reference recipe with a current version and nine IDs remained queued. Production full-import verification requires deployment.
