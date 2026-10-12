# TVW Lab-only Isolation Plan (Implemented Baseline)

## Feature Flag

- `TVW_ENABLE_LAB_MODULES` added in `app.py` (default: `false`).
- When disabled:
  - Lab blueprints are not registered.
  - scheduler/monthly catch-up paths are disabled.
  - master admin imports are not loaded.

## Import Boundary

- `master_admin_console` blueprint and `master_admin_console_service` are lazily imported only when lab modules are enabled.
- maintenance task execution short-circuits when lab mode is off.

## Runtime Boundary

- TVW-first blueprints always active:
  - `routes/tvw_community.py`
  - `routes/tvw_recipes.py`
  - `routes/tvw_api.py`
- Legacy Lab blueprints are conditional and isolated behind the feature flag.

## Remaining Cleanup (next passes)

- Remove Old Goat copy from templates and static branding assets.
- Delete or archive `routes/master_admin_console.py`, `services/sales_consignment_service.py`, and related templates once no longer referenced.
- Replace legacy role terms in remaining Lab templates/routes if those modules are retained.

