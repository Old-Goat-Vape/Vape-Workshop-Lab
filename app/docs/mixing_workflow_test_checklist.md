# Mixing Workflow Manual Test Checklist

Use this checklist after mixing workflow changes. Focus on quick regression confidence.

## 1) MTL Long Fill

- **Setup/Input:** Start mix with `Mix Mode=Long Fill`, `Style=MTL`, `Container=500ml`.
- **Expected:** Shows long-fill batch math with whole units only: **33 units, 495ml used, 5ml headroom**.
- **Expected:** Shows retail context for MTL long fill (30ml retail bottle, 15ml lab fill per bottle).
- **Expected:** Mix composition behaves as PG + flavor base for lab mix (no VG step in long-fill flow).

## 2) DTL Long Fill

- **Setup/Input:** Start mix with `Mix Mode=Long Fill`, `Style=DTL`, `Container=500ml`.
- **Expected:** Shows long-fill batch math with whole units only: **16 units, 480ml used, 20ml headroom**.
- **Expected:** Shows retail context for DTL long fill (120ml retail bottle, 30ml lab fill per bottle).
- **Expected:** Long-fill flow remains decant/base style behavior (no full-mix VG finish behavior).

## 3) MTL Full Mix

- **Setup/Input:** Start mix with `Mix Mode=Full Mix`, `Style=MTL`, `Container=30ml` and test nicotine inputs (e.g. target `0mg` then non-zero).
- **Expected:** Full bottle mix workflow (no long-fill unit/headroom presentation).
- **Expected:** Nicotine values are included in setup summary for full mix (target/base/carrier + nic amount).
- **Expected:** Mixing steps include nicotine base step, then flavor/base steps as applicable.

## 4) DTL Full Mix

- **Setup/Input:** Start mix with `Mix Mode=Full Mix`, `Style=DTL`, `Container=120ml`, test PG-based and VG-based nicotine carrier.
- **Expected:** Full bottle workflow (not decant unit workflow).
- **Expected:** Nicotine summary reflects selected carrier and values.
- **Expected:** Mix steps render and progress normally through tare + ingredient flow.

## 5) Nicotine Field UI Toggle (recipe_library + recipe_view)

- **Setup/Input:** On both pages, switch `Mix Mode` between `Long Fill` and `Full Mix`.
- **Expected:** For `Long Fill`, nicotine fields are hidden and non-interactive.
- **Expected:** For `Full Mix`, nicotine fields are shown and editable.
- **Expected:** Initial page state is correct on load (default long fill hides fields).

## 6) Validation Edge Cases

- **Setup/Input:** Long fill with container below one unit threshold (e.g. MTL <15ml, DTL <30ml).
- **Expected:** Clear validation error; batch does not start.
- **Setup/Input:** Full mix with target nicotine greater than base strength.
- **Expected:** Clear validation error; batch does not start.
- **Setup/Input:** Full mix values that force negative PG/VG remaining space.
- **Expected:** Clear validation error; batch does not start.

## 7) Overpour/Reconcile Continuity

- **Setup/Input:** Start a mix, overpour an ingredient above threshold to trigger reconcile.
- **Expected:** Reconcile flow still works (no crash), recalculates targets, and resumes step sequence.
- **Expected:** Tare gate + next ingredient progression remains intact after reconcile.

## 8) Finalize/Logging Smoke Test

- **Setup/Input:** Complete one successful batch end-to-end and finalize.
- **Expected:** Finalize succeeds, redirects to history/log view, and batch appears in production logs.
- **Expected:** Safety confirmation, batch metadata, and ingredient totals remain recorded as before.

