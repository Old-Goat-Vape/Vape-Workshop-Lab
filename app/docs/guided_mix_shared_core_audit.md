# Guided Mix — `shared_mix_core` usage audit

**Bench behaviour rules:** Guided Mix bench behaviour is governed by [`GUIDED_MIX_BENCH_RULES.md`](GUIDED_MIX_BENCH_RULES.md) (working design reference — check before code changes).

Audit date: 2026-07-09. Report only; no tolerance/overpour logic changes.

## 1. Guided session step generation path

**Confirmed:** Guided Mix sessions are built through `build_guided_assistant_steps()`, not by persisting raw `compute_mix_step_breakdown()` output.

| Layer | Function | Role |
|-------|----------|------|
| `guided_mix_session_service.create_guided_mix_session` | calls `tvw_mix_service.guided_mix_steps_for_recipe_version` | sole session creation path |
| `tvw_mix_service.guided_mix_steps_for_recipe_version` | `compute_mix_step_breakdown_with_policy` (policy gate only) → `build_guided_assistant_steps` | policy warnings; **discards** raw breakdown list |
| `shared_mix_core.guided_steps.build_guided_assistant_steps` | `compute_mix_step_breakdown` → sort → `build_guided_narrative_steps` | canonical guided step list |

`compute_mix_step_breakdown_with_policy` is used only for `PolicyEvaluation` (flavor % / nic limits). Its returned step list is assigned to `_steps` and thrown away.

**No bench route bypass found** — grep shows no app code calling `compute_mix_step_breakdown()` except inside `build_guided_assistant_steps` and the policy wrapper above.

## 2. Nicotine-first pour order

**Raw breakdown order** (`shared_mix_core/calculator.py`): flavours → nicotine → PG → VG.

**Guided order** (`shared_mix_core/guided_steps.py`): `_guided_pour_order_key` sorts nicotine (0) → flavours by role (1) → PG (3) → VG (4).

All guided sessions go through `build_guided_assistant_steps`, which always applies this sort before narrative expansion (tare / transition / add_ingredient steps).

Guard test: `tests.test_shared_mix_core_guided_audit.GuidedMixSharedCoreAuditTests.test_guided_pour_order_nicotine_before_flavours_when_nicotine_ml_positive`.

## 3. Duplicate `shared_mix_core` copies

| Copy | Path | When used |
|------|------|-----------|
| **Workspace (source of truth for dev)** | `The_Vape_Workshop_Calculator/shared_mix_core/` | Discovered when running Flask/tests from `app/` |
| **Release bundle** | `release/TVW_Calculator/app/shared_mix_core/` | Shipped zip layout; used when cwd is release `app/` |
| Desktop sibling (optional) | `Desktop/shared_mix_core/` | Referenced by layout tests / build scripts only if present |

**Import resolution** (`app/core/shared_mix_path.py`):

1. If `shared_mix_core` is already importable (`PYTHONPATH` / prior insert), use it as-is.
2. Else walk ancestors from `app/` (and `cwd`) for `shared_mix_core/__init__.py`, insert parent on `sys.path`.

Dev tests assert the loaded `guided_steps.py` resolves to `../shared_mix_core/` (project root), **not** `release/...`.

**Stale-copy risk:** Editing only `release/TVW_Calculator/app/shared_mix_core/` during local dev has **no effect** on the running app. Release must be rebuilt/synced from workspace `shared_mix_core/` before shipping.

## 4. Classification & tolerance — split of responsibility

| Concern | Source of truth | Notes |
|---------|-----------------|-------|
| `slight_under_target` | `app/services/guided_mix_session_service.py` → `_classify_under_pour` | Fixed **0.02 g** display gap threshold; not from `policy.py` |
| `large_under_target` | same `_classify_under_pour` | Under > 0.02 g |
| `slight_over_target` | `_classify_slight_over_pour` | Any display over > 0.0005 g when ratio ≤ policy multiplier |
| `over_target` | `complete_guided_step` pour branch | Display over > 0.0005 g |
| `over_target_recovery_required` | same branch | `measured/display_target > policy.overpour_threshold_multiplier` (TVW: **1.08**) |
| Tolerance grams (under/over soft bands) | `shared_mix_core/policy.py` → `scaled_pour_tolerance_grams` | Vessel-scaled % from `tvw_mix_policy`; used for near-miss stats and context, **not** slight/large under labels |
| Display-rounded target comparison | `guided_mix_session_service._display_target_grams` | `round(target, 2)`; under/over_by use display values |
| ml-aware reconcile | `app/services/mixing_engine.py` → `reconcile_batch_plan` | Uses `evaluate_overpour` from `policy.py` on **batch volume** ratio; separate from per-pour bench classifications |

`policy.py` also exposes `evaluate_overpour`, `evaluate_guided_step_progression`, `overpour_requires_hard_stop` — used by mixing engine / lab flows, not for bench `slight_under_target` / `large_under_target` strings.

## 5. Stale shared-core functions that could reintroduce old behaviour

| Function | Risk if used directly for bench |
|----------|-----------------------------------|
| `compute_mix_step_breakdown()` | Wrong pour order (flavours before nicotine) |
| `compute_mix_step_breakdown_with_policy()` return value | Same wrong order if persisted without `build_guided_assistant_steps` |
| `evaluate_overpour()` alone | Batch-volume 1.08× gate; does not map to `slight_over_target` / recovery UI |
| `scaled_pour_tolerance_grams()` alone | Soft gram bands; does not define slight vs large under (0.02 g is hardcoded in service) |
| `lab_mix_policy()` | Stricter thresholds if accidentally swapped for `tvw_mix_policy()` in bench |
| Release-only copy of `guided_narrative.py` / `guided_steps.py` | Old coach copy or missing nicotine sort if release not synced |

**Outdated doc:** `app/docs/tvw_reusable_core_map.md` still says `compute_mix_step_breakdown()` is the guided step sequence — misleading; guided path is `build_guided_assistant_steps()`.
