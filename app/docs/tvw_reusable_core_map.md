# TVW Reusable Core Extraction Map

## Extracted Core Package

- `core/calculator.py`
  - `compute_mix()`: pure mix math for target volume, nicotine target, PG/VG ratio, flavour percentages.
  - `compute_mix_step_breakdown()`: guided step sequence derived from pure result.
  - `FlavorInput`, `MixInput`, `MixResult`: framework-agnostic contracts.

## Adapter Layer

- `services/tvw_mix_service.py`
  - Maps DB recipe-version rows to pure-core inputs.
  - Keeps Flask/session/DB concerns outside core math.
  - Provides `quick_mix_for_recipe_version()` and `guided_mix_steps_for_recipe_version()`.

## Dependency Constraints

- Allowed in `core/*`:
  - Python stdlib only.
- Not allowed in `core/*`:
  - Flask imports.
  - `db.py`, `models/*`, `routes/*`.
  - role/permission checks.
  - stock-movement side effects.

## Legacy Module Boundary (Not Core)

- `services/mixing_engine.py` remains runtime-compatible but is considered legacy orchestration.
- New TVW routes use `tvw_mix_service.py` and `core/calculator.py` as the clean path forward.

