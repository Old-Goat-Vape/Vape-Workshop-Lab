# TVW Rollout Sequence and Test Gates

## Milestone 1: Product Boundary

- done: TVW app identity defaults in `app.py`.
- done: Lab runtime isolation flag (`TVW_ENABLE_LAB_MODULES=false` default).
- gate:
  - app boots without loading master admin modules.
  - `/` and `/community` resolve.

## Milestone 2: Reusable Core

- done: pure calculator in `core/calculator.py`.
- done: TVW adapter service in `services/tvw_mix_service.py`.
- gate:
  - quick mix endpoint computes without touching legacy `mixing_engine`.
  - guided session persists step rows.

## Milestone 3: Community Schema

- done: V1 community tables in `migrations.py`:
  - profiles, feedback, flavour notes/reviews, stash, mix sessions.
- gate:
  - startup migrations create all new tables idempotently.
  - inserts succeed for feedback/reviews/stash/guided session.

## Milestone 4: Community Route Surface

- done: initial route contracts and endpoint shells.
- gate:
  - all listed V1 routes resolve without Lab feature flag.
  - login/signup routes land in TVW flow.

## Milestone 5: Hardening

- next:
  - add route-level tests for each API contract.
  - add schema constraints/checks and pagination for list endpoints.
  - migrate placeholder JSON pages to mobile-focused templates.

