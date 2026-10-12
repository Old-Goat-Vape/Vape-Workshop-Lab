# Guided Mix Bench Rules

**Working design / rules reference for development and testing — not final product documentation.**

This file captures the current real-world Guided Mix bench rules for **The Vape Workshop Calculator**: physical workflow, pour decisions, nicotine policy, PG/VG behaviour, batch-size logic, logging, and known implementation cautions.

### How to use this file

1. **Before changing Guided Mix behaviour**, read this file and align with the rules below.
2. **When a rule changes during testing**, update this file in the **same commit / pass** as the code change.
3. Do not treat this as customer-facing documentation until it is explicitly promoted.

**Location:** `app/docs/GUIDED_MIX_BENCH_RULES.md`

---

## 1. Core bench philosophy

The Guided Mix Bench must behave like a real mixing assistant, not a strict calculator screen.

**Guided Mix is bench-critical.** Step transitions must be fast and reliable. Database busy errors must not appear during normal local use.

Real mixing has scale drift, thick liquids, small drops, empty bottles, accidental button presses, underpours that are safer than overpours, overpours that need recovery, and different rules for nicotine, flavours, PG, and VG.

The bench must guide the mixer through decisions and log meaningful deviations.

---

## 2. Physical bench model

The screen uses a physical left / middle / right workflow.

- **RIGHT SIDE:** ingredients still waiting to be used.
- **MIDDLE:** current active ingredient or action.
- **LEFT SIDE:** ingredients physically completed and moved away.

Important rule:

**An ingredient must only appear on the LEFT SIDE after the mixer clicks `BOTTLE MOVED`.**

Pour completion and bottle movement are separate states:

| State | Meaning | LEFT SIDE |
|-------|---------|-----------|
| `add_ingredient` / pour active | Pour in progress | No |
| `pour_completed_pending_bottle_move` | Pour accepted, bottle still on scale / in hand | **No** |
| `bottle_moved` | User confirmed `BOTTLE MOVED` on checkpoint | **Yes** |

Do not infer LEFT SIDE from `step.completed_at`, `measured_grams`, accepted under/over, recovery acknowledged, or checkpoint screen active — only from explicit `bottle_moved` on the pour step payload (set when the transition step completes). Log `bottle_moved` events in `mix_event_log`.

Do not mark an ingredient as left/used while a pour is active, an underpour decision is pending, overpour recovery is pending, or bottle movement has not been confirmed.

---

## 3. Step order

Guided Mix must always use this pour order:

1. Nicotine
2. Flavours / concentrates
3. PG
4. VG

Nicotine is first because nicotine overpour recovery is only clean before other ingredients enter the bottle.

Guided Mix sessions must be generated through:

`shared_mix_core.guided_steps.build_guided_assistant_steps()`

Do not persist raw `compute_mix_step_breakdown()` order directly, because the raw calculator breakdown may not be in guided-bench order.

---

## 4. Grams vs millilitres

The mixer pours by scale, so active pour screens use grams. The recipe and batch progress are volume based, so progress uses millilitres.

**Grams are what the scale sees. Millilitres are what the recipe/batch volume sees.**

Active pour screen should show target weight, equivalent volume, and scale reading. Batch progress should show `X.XX ml of selected batch size`.

Do not make gram/ml differences look like an error. VG-heavy ingredients weigh more than their ml volume.

---

## 5. Active pour remaining calculation

Active pour remaining must only use the current active step.

`remaining_grams = target_grams_for_current_step - current_scale_grams`

Previous measurements, accepted underpours, partial bottle amounts, batch progress, or stale payload fields must never contaminate the next ingredient’s active remaining value.

Example: Apricot accepted at `0.30 g`, Brown Sugar target is `1.04 g`, scale reads `0.00 g`. Brown Sugar remaining must be `1.04 g`, not `0.74 g`.

---

## 6. Underpour rules

Underpour is guidance, not punishment.

Being under target can mean the mixer intentionally stopped slightly under, one more drop may overshoot, the bottle ran empty, or the mixer typed the current amount and accidentally pressed the button too soon.

Do not hard-block underpours unless there is a truly safety-critical reason.

### Tiny underpour

Example: target `0.31 g`, actual `0.30 g`, under by `0.01 g`.

Behaviour:

- Show calm decision screen.
- Offer `ACCEPT THIS POUR` and `TRY A TINY DROP`.

For flavours: “Close enough for this pour. One more drop may put you over target.”

For nicotine: use stricter strength-aware copy.

### Large underpour

Example: target `3.20 g`, actual `2.20 g`, under by `1.00 g`.

Behaviour:

- Do not say “close enough.”
- Say “Still under by 1.00 g” and “This pour does not look finished yet.”
- Offer `CONTINUE POURING`, `SAVE AS PARTIAL / CHANGE BOTTLE`, and `ACCEPT UNDERPOUR`.

Accepting a large underpour must be deliberate and logged.

---

## 7. Try a tiny drop flow

When slightly under, the app must not simply say “you may add more” and leave the mixer hanging.

Flow:

1. Slightly under detected.
2. Mixer chooses `TRY A TINY DROP`.
3. Bench enters careful top-up mode.
4. Show target, current, needed, and “Add one tiny drop, then wait for the scale to settle.”
5. Mixer checks weight again.
6. Outcomes: still under = offer accept/try again; on target = complete; over target = trigger overpour handling.

Overpour logic must not be bypassed during top-up.

---

## 8. Accept underpour logging

Accepted underpours can change the final recipe.

Every meaningful accepted underpour should be logged with ingredient display name, ingredient id if available, target grams, actual grams, difference grams, target ml if available, actual ml if available, classification, action, timestamp, session id / step id, and optional mixer note if supported.

Large accepted underpour should include: “Recipe strength changed for this ingredient.”

Nicotine accepted underpour should include: “Final nicotine strength may differ from recipe target.”

End summary / history must show meaningful deviations so the mixer knows why a batch tastes different later.

---

## 9. Overpour rules

Over target is stricter than under target.

Under target = judgement call. Over target = acknowledgement and recovery.

For flavours:

- tiny overpour: amber note, acknowledge clearly
- serious overpour: recovery plan required

Do not hide overpour behind vague text like “minor drift.”

Use clear copy like “Slightly over by 0.01 g” or “Brown Sugar overpoured by 0.36 g.”

For serious overpour, show target, actual, over-by amount, and plain-language recovery guidance.

Buttons should say `REVIEW RECOVERY PLAN` or `APPLY CORRECTION`, not vague `CONTINUE`.

---

## 10. Serious flavour overpour recovery

For serious flavour/concentrate overpour:

1. Detect overpour.
2. Do not complete the pour immediately.
3. Do not move the ingredient to LEFT SIDE.
4. Show recovery card.
5. Review/apply correction.
6. Then show bottle movement step.
7. Only after `BOTTLE MOVED`, move ingredient to LEFT SIDE.

This must apply to all flavours in all recipes, not just the tested ingredient.

Exceptions: nicotine has its own recovery rules; PG/VG have more lenient carrier/final-fill rules.

---

## 11. Nicotine rules

Nicotine is special and is always first.

### Nicotine under target

Under nicotine is acceptable, but warning tone must be stricter than flavours.

Message must make clear that nicotine accuracy affects final strength, accepting under target may make final nicotine strength lower, and adding more must be done with a tiny controlled drop.

Possible buttons: `ACCEPT NICOTINE DOSE` and `TRY A TINY DROP`.

Accepted nicotine underpour must be logged.

### Nicotine over target

Nicotine over target is not handled like flavour overpour.

Because nicotine is first, the safe recovery is:

1. Stop pouring.
2. Pour nicotine back into the original nicotine bottle.
3. Put the mixing bottle back on the scale.
4. **Do not tare the scale.**
5. Continue from whatever nicotine residue remains in the mixing bottle.

Reason: some nicotine may remain in the mixing bottle. If the mixer tares after pouring back, the next pour can make the nicotine dose too strong.

Recovery mode must show: do not tare, target, current scale, and `needed = target - current`.

If still over after pour-back, tell the mixer to pour a little more back and do not tare.

---

## 12. Nicotine overpour policy: Personal vs Lab/Client

Nicotine overpour tolerance depends on mixing mode.

### Personal mode

- `<= 0.05 g` over: amber decision, allow `ACCEPT NICOTINE OVER` or `FIX NICOTINE DOSE`.
- `> 0.05 g` and `< 0.10 g` over: stronger warning, correction recommended, accept only with deliberate confirmation if allowed.
- `>= 0.10 g` over: recovery required, no accept.

### Lab/Client mode

- `<= 0.05 g` over: amber decision, accept only with deliberate confirmation if policy allows.
- `> 0.05 g` over: recovery required, accept disabled, show “Client/lab mode: nicotine over tolerance must be corrected.”

All nicotine over decisions must be logged with mode, target grams, actual grams, over-by grams, action, accept policy, and warning.

---

## 13. PG rules

PG is the **flexible carrier/adjuster** — more lenient than nicotine and flavours, stricter than VG final-fill.

### Session context (required)

All PG behaviour uses the active session values:

- `batch_size_ml`
- `target_pg_percent`
- `target_vg_percent`

Never assume `100 ml` or a fixed ratio. These are set when the user starts the mix and stored in session metadata (`create_guided_mix_session` → `result_json`).

### When PG adjusts

On a **small accepted flavour overpour** (slight over, not serious recovery), the bench may **trim the remaining open PG pour target** to protect:

- final volume close to `batch_size_ml`
- final PG/VG ratio close to `target_pg_percent` / `target_vg_percent`

Rules:

- **PG only** — never trim VG for flavour compensation.
- **Never adjust nicotine** — nicotine over/under uses nicotine-specific policy and recovery only.
- **PG adjustment is not a magic eraser** — the accepted flavour deviation must still be logged (`accepted_flavour_over`, `mix_event_log`).
- Flavour **underpours** are logged and guided separately; they do not silently change nicotine strength via PG/VG.

### PG screen copy when adjusted

When PG target changes after flavour compensation, show:

- “PG adjusted to protect final batch volume and ratio.”
- “Original PG target: X.XX g / Y.YY ml”
- “Adjusted PG target: X.XX g / Y.YY ml”
- “Target ratio: {target_pg_percent}/{target_vg_percent}”
- “Estimated final ratio: {est_pg}/{est_vg}”

Stored on the PG step payload as `pg_adjustment_note`; event logged as `pg_adjusted`.

### PG pour tolerance

PG is flexible. Small PG over/under differences should be **guidance, not panic**, but tolerance **scales with batch size** (e.g. ~`1 g` off matters more in `10 ml` than in `100 ml`). Carrier PG under/over within tolerance may complete with guidance and `accepted_carrier_*` log entries instead of blocking the pour.

---

## 14. VG rules

VG is **final-fill guidance** — the most lenient pour tier. Focus on bottle size and estimated final ratio, not gram-perfect VG weight.

### Session context (required)

Same as PG: `batch_size_ml`, `target_pg_percent`, `target_vg_percent`. VG target ml/grams scale with selected batch size (e.g. `30 ml @ 30/70` ≠ `100 ml @ 30/70`).

### VG screen copy (required)

- “Add VG to finish the bottle”
- “Target estimate: X.XX g / Y.YY ml”
- “Final bottle target: {batch_size_ml} ml”
- “Target ratio: PG/VG {target_pg_percent}/{target_vg_percent}”
- “VG is thick. Pour slowly and let bubbles settle before judging the final level.”

### VG completion behaviour

- Guide toward calculated VG target, but **do not punish** small VG over/under if final batch volume is close to `batch_size_ml`.
- Batch progress and fill bar use `batch_size_ml`, not a hardcoded volume.
- VG tolerance scales with batch size via `ingredient_pour_tolerance_grams(..., ingredient_category="vg")`.

---

## 15. Batch size and PG/VG ratio

**Never assume `100 ml`.**

Before the mix starts, the user selects batch size, PG/VG ratio, nicotine type/carrier, and fill target. The session must retain:

| Field | Purpose |
|-------|---------|
| `batch_size_ml` | Final bottle target, progress denominator, tolerance scaling |
| `target_pg_percent` | Target ratio (PG leg) |
| `target_vg_percent` | Target ratio (VG leg) |

All progress, PG trim, VG final-fill, tolerance checks, and completion summary use these values.

Examples: `10 ml @ 50/50`, `30 ml @ 50/50`, `60 ml @ 30/70`, `100 ml @ 20/80`, max-VG style mixes.

Copy must use `Final bottle target: {batch_size_ml} ml`.

### Ingredient-tiered pour tolerances

Implemented in `shared_mix_core/policy.py` (`ingredient_pour_tolerance_grams`, `ingredient_over_requires_recovery`, `normalize_ingredient_pour_category`). Strictest → most lenient:

| Tier | Ingredients | Behaviour |
|------|-------------|-----------|
| **Strictest** | Nicotine | Strength-aware under/over; personal vs lab/client over policy; pour-back recovery; **no PG/VG compensation** |
| **Strict** | Flavours / concentrates | Tight dose control; slight over may trigger PG trim; serious over → recovery |
| **Flexible** | PG | Carrier adjuster; batch-scaled gram tolerance; guidance over panic |
| **Most lenient** | VG | Final-fill; batch-scaled tolerance; focus on bottle volume and estimated ratio |

Tolerance grams scale with `batch_size_ml` (and policy vessel factors), not a single fixed gram slack for all bottle sizes.

### Final summary (`mix_summary`)

When all steps complete, `get_guided_session_bundle` exposes `mix_summary` with at minimum:

- selected **batch size** (`batch_size_ml`)
- **target ratio** (`target_pg_percent` / `target_vg_percent`)
- **estimated final volume** (`estimated_final_volume_ml`)
- **estimated final ratio** (`estimated_final_pg_percent` / `estimated_final_vg_percent`)
- **PG adjustments** (`pg_adjustments` from `mix_event_log`)
- **accepted deviations** (`accepted_deviations` — flavour over, carrier under, etc.)
- **nicotine events** (`nicotine_events` from `nicotine_recovery_log`)

UI renders this in `#mixSummaryDetail` on the completion screen.

---

## 16. UI language rules

The bench should use action-focused, plain language.

Avoid debug-style alerts, vague “minor drift,” repeated backend warnings, generic `CONTINUE` on dangerous steps, empty scale displays showing `— g` on non-weighing steps, and stale footers like “Up next: Brown Sugar” while already resolving Brown Sugar.

Use action labels like:

- `BOTTLE MOVED`
- `REVIEW RECOVERY PLAN`
- `APPLY CORRECTION`
- `ACCEPT THIS POUR`
- `TRY A TINY DROP`
- `CONTINUE POURING`
- `SAVE AS PARTIAL / CHANGE BOTTLE`
- `YES, ACCEPT & LOG`

---

## 17. Batch log / summary

The bench must remember what actually happened.

Log meaningful events in `mix_event_log` / `nicotine_recovery_log`: accepted underpour, large underpour, partial bottle/change bottle, try tiny drop, slight overpour, serious overpour, recovery plan inserted, recovery applied, **`pg_adjusted`**, **`accepted_flavour_over`**, **`accepted_carrier_under`**, VG final-fill deviation, nicotine over/under decisions, and nicotine pour-back recovery.

Final summary/history (`mix_summary`) must explain why the batch may taste or behave differently from the recipe — see §15 for required fields.

Final batch report must include optional mixer notes saved with the mix session. These notes are separate from automatic event logs and are used to record taste, steeping, mistakes, and next-time adjustments. Persist as `result_json.user_notes` with `result_json.user_notes_updated_at`.

Mixer notes saved on completed mix sessions must surface on the recipe card. Automatic mix logs belong behind Mix Logs / Batch History.

---

## 18. Current implementation caution

### PG/VG implementation (landed)

Primary files for batch-scaled PG/VG behaviour:

| File | Role |
|------|------|
| `app/services/guided_mix_session_service.py` | Session `batch_size_ml` / ratio context, PG trim (`_adjust_pg_for_flavour_deviation`), carrier classification, `mix_event_log`, `_build_guided_mix_summary`, pour completion policy wiring |
| `shared_mix_core/policy.py` | `ingredient_pour_tolerance_grams`, `ingredient_over_requires_recovery`, `normalize_ingredient_pour_category` |
| `app/templates/tvw_guided_mix_session.html` | VG final-fill copy, PG `pg_adjustment_note` display, `batch_size_ml` progress, `mix_summary` completion panel |

Automated coverage includes `tests/test_tvw_api.py` (PG/VG batch tests) and `tests/test_guided_mix_bench_rules_doc.py` (this document).

### Shared code copies

There are multiple copies of shared code:

- workspace source: `The_Vape_Workshop_Calculator/shared_mix_core/`
- release mirror: `release/TVW_Calculator/app/shared_mix_core/`

During dev, edit and test the workspace source first. Sync to release only after tested.

Do not assume release-copy edits affect the running local dev app.

### Performance and SQLite (bench-critical)

Guided step completion must keep **write transactions short**: update only session/step/event fields under lock, commit, then build the response bundle.

Dev logging (`TVW_GUIDED_MIX_PERF=1`, default on) records route timing, transaction timing, SQLite retry/busy counts, `result_json` size, event-log entry counts, mix-summary rebuilds, and response payload size.

Client session bundles omit heavy fields not needed on the bench (`result_json`, duplicate `payload_json`, `raw_event_log` in mid-session responses).

If the mixer sees a save failure after retries, the bench-friendly message is: *“The bench is saving this step. Try again in a moment. Your mix has not advanced yet.”*

Step buttons disable immediately with **Saving…** while a step POST is in flight to prevent duplicate advancement.

Startup logs include process id, database path, Flask reloader state, journal mode, and busy timeout. A warning is printed if the database path is inside OneDrive sync.

### Keep this file updated

Whenever Guided Mix bench rules change in code or QA, update **`app/docs/GUIDED_MIX_BENCH_RULES.md` in the same commit/pass** as the behaviour change. This file is the working design reference for developers and testers.

---

## 19. Testing checklist

Before trusting a guided mix change, test:

### Nicotine

- slight under
- slight over personal
- bigger over personal
- lab/client over threshold
- pour-back recovery
- no-tare recovery mode

### Flavours

- slight under
- try tiny drop
- large under
- save partial/change bottle
- slight over
- serious over
- recovery card
- left/right bench timing

### PG/VG

- selected batch size other than 100 ml
- selected PG/VG ratio
- PG adjustment
- VG final-fill
- final estimated ratio
- final summary

### State bugs

- no stale measured grams leaking into next step
- no ingredient on LEFT SIDE before BOTTLE MOVED
- no dead `— g` display on decision/recovery steps
- no stale `Up next` repeating current ingredient
