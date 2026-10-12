# Mixing Guide V2 Launch QA

This checklist is launch-focused and limited to correctness/flow for guided mixing in:

- The Vape Workshop Calculator (TVW)
- Old Goat Lab (Lab)

**Rules reference:** Guided Mix bench behaviour is governed by [`GUIDED_MIX_BENCH_RULES.md`](GUIDED_MIX_BENCH_RULES.md).

No feature expansion, only validation of critical branches.

## Automated Coverage

Run these from project roots.

### TVW automated tests

```powershell
Set-Location "c:\Users\Wynand Vermeulen\OneDrive - Nemtek (Pty) Ltd\Desktop\The_Vape_Workshop_Calculator\app"
python -m unittest ^
  tests.test_tvw_api.TVWApiTests.test_guided_add_ingredient_normal_pour_progresses ^
  tests.test_tvw_api.TVWApiTests.test_guided_add_ingredient_requires_measured_reading ^
  tests.test_tvw_api.TVWApiTests.test_guided_overpour_promotes_recovery_step_and_completes_original_step ^
  tests.test_tvw_api.TVWApiTests.test_reconcile_bigger_vessel_returns_success_and_delta_queue ^
  tests.test_tvw_api.TVWApiTests.test_reconcile_delta_only_skips_already_satisfied_step ^
  tests.test_tvw_api.TVWApiTests.test_tvw_reconcile_continue_with_drift_when_no_bigger_container
```

### Lab automated test

```powershell
Set-Location "c:\Users\Wynand Vermeulen\OneDrive - Nemtek (Pty) Ltd\Desktop\Old_Goat_Lab\app"
python -m unittest test_mixing_guide_v2.MixingGuideV2LabTests.test_lab_reconcile_no_bigger_container_requires_scrap
```

## Branch-to-Test Mapping

- `normal pour`: `test_guided_add_ingredient_normal_pour_progresses`
- `underpour block`: `test_guided_add_ingredient_requires_measured_reading`
- `overpour recovery promotion`: `test_guided_overpour_promotes_recovery_step_and_completes_original_step`
- `bigger vessel reconcile`: `test_reconcile_bigger_vessel_returns_success_and_delta_queue`
- `delta-only restart`: `test_reconcile_delta_only_skips_already_satisfied_step`
- `TVW continue-with-drift`: `test_tvw_reconcile_continue_with_drift_when_no_bigger_container`
- `Lab scrap-required branch`: `test_lab_reconcile_no_bigger_container_requires_scrap`

## Browser QA Checklist (Step-by-step)

Use this exact order. Record pass/fail and screenshot each step card state.

1. **Start guided session**
   - Open a recipe in TVW and start Guided Mix.
   - Confirm one clear hero step is visible and step trail is secondary.

2. **Tare step visual + gate**
   - Confirm tare step has distinct styling from ingredient step.
   - Click tare confirm and ensure progression occurs.

3. **Normal pour**
   - On ingredient step, enter exact target grams.
   - Confirm progression to next step.

4. **Underpour block**
   - Restart a session, enter clearly below target.
   - Confirm block message appears and same ingredient remains active.
   - Confirm it asks for more to reach target.

5. **Overpour recovery promotion**
   - Restart, overpour beyond threshold.
   - Confirm normal progression stops.
   - Confirm active step transitions to recovery/reconcile decision.
   - Confirm original ingredient is not re-requested.

6. **Bigger vessel reconcile**
   - In recovery, choose larger container path.
   - Enter exact ml manually.
   - Confirm reconcile succeeds and queue restarts from step 1 in reconcile mode.

7. **Delta-only restart / auto-skip**
   - After reconcile, confirm already satisfied ingredient shows skipped/completed behavior.
   - Confirm only missing deltas are requested.

8. **TVW no larger vessel**
   - Trigger overpour again and choose no bigger vessel.
   - Confirm continue-with-drift warning is explicit.
   - Confirm flow continues with recalculated targets.

9. **Lab no larger vessel**
   - Repeat in Lab mix flow.
   - Choose no bigger vessel.
   - Confirm scrap-required screen appears.
   - Confirm Manager/Master/Old Goat credentials are required.
   - Confirm successful authorization redirects to history and batch is marked scrapped.

10. **Completion sign-off**
    - Complete a full run and ensure completion card renders with expected action buttons.

## Launch Gate

Do not launch unless all seven critical branches pass automated checks and browser checklist steps 1-10 pass on:

- Windows dev machine
- Linux deployment target

