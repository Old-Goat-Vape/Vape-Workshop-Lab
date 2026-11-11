# Live member audit fixes — 6 November 2026

Prepared from the Santa's Little Helper desktop test. These changes are not deployed.

## Resulting behaviour

- Blank recipe codes stay NULL when edited, matching creation and avoiding a UNIQUE collision with a legacy blank code.
- Impossible mix requests fail before recipes/sessions are written: non-finite inputs, non-positive bottle/base values, negative ratios, nicotine strength above base strength, and fixed flavour/nicotine volume above the bottle size. JSON endpoints retain explicit zeroes. When only the PG/VG ratio is unreachable, remaining base fills the chosen bottle volume and the ratio warning reports the achievable composition.
- Small flavour overs within the batch-scaled gram allowance follow PG-only trim handling even on a tiny target. The 10 ml / 0.21 g Biscuit target with a 0.23 g reading now trims PG rather than entering serious recovery.
- Serious overpour requires a persisted review and then explicit **Accept changed batch**. The review states that remaining targets stay unchanged and that no automatic correction has been applied. Acceptance logs the changed dose; strict policies do not allow it. This is acceptance of a changed batch, not automatic recipe rebalancing.
- Dynamic events count once when inserted. Corrected underpour attempts do not remain in accepted-underpour coaching. Final notes distinguish flavour pours from carrier pours.
- Final review is named **Final batch check**. Completed session counters and state update before returning from rendering.
- Stash quick-add searches the full canonical catalogue on demand, returns bounded results, distinguishes IDs, excludes hidden/invalid/merged rows, and prevents stale asynchronous results replacing a newer query.
- What Can I Make includes public recipes plus the viewer's active private recipes; another member's private recipes remain excluded.
- Author links render a paginated recipe profile, with the existing library visibility gate and no account secrets or contact information.
- Successful status changes refresh the visible badges. Stash save/remove updates its summary quantity. Stash hints support 10 ml and the current recipe's custom bottle size.
- Builder search decodes imported HTML entities for display. Recipe live calculations discard stale responses and disable Save while a new calculation is pending.

## Validation

The 9 new live-audit tests fail on unchanged main (14 assertions/subtests fail) and pass on the fixed branch. Targeted guided tests cover small-bottle PG trim, recovery review before acceptance, deferred bottle movement and corrected underpour coaching.

A 235-test run across the API, shared mixing core, guided workflow, recipe builder/search, production jobs and costing had 215 passes and 20 failures. The same 20 test names fail on unchanged main (223 tests there); no new failure names remain. They concern existing admin/Lab/account gate expectations. This does not constitute a green full-suite sign-off.

Six affected pages render through Flask test clients; 12 rendered inline JavaScript blocks and the builder JavaScript pass `node --check`. Changed Python modules compile and `git diff --check` passes. The remote browser cannot connect to the isolated local preview, so interactive/visual retesting of these changes is pending deployment.

## Deployment and live acceptance

1. Review and merge the prepared PR through the normal repository process.
2. On Pauly's server, back up the production database using the normal deployment backup procedure and update the application from the merged revision. Restart the existing application service using its established deployment instructions. Do not copy the isolated test database.
3. As Santa's Little Helper, retest recipe 114 notes-only editing; verify a new version and 10 ml defaults.
4. Check invalid Quick Mix and guided-start requests do not create sessions. Check zero nicotine and zero PG remain zero.
5. Re-run the small Biscuit overpour and serious-overpour review/accept paths; verify PG adjustment, one overpour count, bottle movement and saved final notes.
6. Search Vanilla Swirl in My Stash, add it, then verify the private recipe appears in What Can I Make. Check author profiles and completed-session reload.

No complete-app sign-off: imported catalogue hardware/aliases/product variants need a separate data review. No records are deleted or bulk-merged by this patch. Initial session loss remains unconfirmed. Mobile, live multi-account privacy, admin/Lab and remaining edge cases still require testing.

## Master-access follow-up — 7 November 2026

The live Master pass covered the admin console, users, flavour intelligence, Lab dashboard, production jobs, empty sales/decanting pages, earnings and moderation of our own QA feedback. No real production, sales, payments, user role changes, scraper runs or bulk classification changes were performed.

Additional fixes:

- Cancelled production batches and cancelled jobs reject guided progression and undo before any step writes. Their mixing screens display cancellation and their sessions disappear from the Continue mix list. Production context now includes job status.
- Master user-editor options come from the canonical role hierarchy, including member, moderator and admin, so the current role remains selected.
- Single-flavour classification no longer crashes on the misspelled brand-alias constant. Preview remains read-only. The UI handles HTML responses, expired-session redirects and network failures without leaving “Previewing…” indefinitely.
- Sidebar active-link code selects actual navigation anchors and safely parses relative or malformed URLs.
- Manual stock addition and reference reclassification use the canonical Foreman threshold rather than unreachable legacy rank 6. Member access remains denied. Existing merge/archive/edit policy has not been widened.
- Owner-only pricing/costing shortcuts are shown only to the account allowed by their existing gate.

New isolated regression checks: 4 Python tests fail on the previous branch revision (4 failures and one error across subtests), and pass with these changes. The combined Master/member audit run passes all 13 tests. All 10 production service tests also pass with Lab modules enabled at startup. Node regressions cover successful classification preview, HTML error responses, redirects, network failure and sidebar URLs. Python compilation and whitespace checks pass. These are focused results; the earlier 20 baseline failures remain documented above.

Live QA job #2, “SLH QA - Production Workflow - DO NOT PRODUCE”, was left cancelled, with batch #1 cancelled and zero completed production, stock usage, earnings or payments. Guided session #62 was used to reproduce the cancellation defect: Ready to pour advanced despite cancellation; no weight was entered. Recipe #114 / version #116 was attached as 2 × 10 ml. Bottle/label overrides were R5/R4. QA feedback #1 was hidden and then restored active. An attempted zero-stock manual inventory item was rejected by the old rank gate, so it was not created live.

After deployment, retest the cancelled session #62 and job #2: no progression or undo should be accepted, and Continue mix must not offer that session. Verify the user role editor without changing another person's role; preview INW Biscuit reference #626; confirm errors clear rather than spin; check Master manual inventory addition with an explicitly disposable test record. Catalogue classification accuracy and duplicate/priced variants still require a separate review. Mobile, reset/login and live cross-account checks remain outstanding. No complete-app sign-off or live deployment is claimed.

## Nicotine pour-back correction — 7 November 2026

User-confirmed rule: nicotine is always first. On overpour, return nicotine before adding any other ingredient, retain the original tare, weigh the residue and continue to the original target. Batch enlargement/splitting is a separate proposed flavour-recovery feature and is not implemented by this change.

The existing server already implemented nicotine-first ordering, pour-back guidance and residue subtraction, but small overages could still be accepted. The client reused the removed overpour weight after pour-back, treated zero readings as absent through truthy fallbacks, and preserved stale scale input on the same step. Recovery also permitted accepting a lower dose and counted one episode multiple times.

Changes:

- Every nicotine reading above the displayed target requires correction in both Personal and Lab/Client modes. Retired accept-over requests and legacy pending acceptance states cannot bypass correction.
- Confirm pour-back before entering the residue reading. Clear the removed measurement; current/needed remain unknown until a fresh reading is entered. Keep the original target and original tare.
- Preserve a genuine zero residue across state updates and reloads. Show “Do not tare”, current total and remaining amount. Do not add the full target on top of residue.
- Keep the nicotine step open while below or above target during correction; only reaching the displayed target completes it. Ordinary underpour handling outside correction remains separate. Subsequent ingredient progression remains locked.
- Do not resize the batch or alter carrier targets. Count a recovery episode once. Bench rules and former accept-over tests now reflect the confirmed workflow.

Validation: 22 focused nicotine/order checks pass; new three-test regression set fails on the prior revision and passes here. The broader API/doc run passes 138/139 tests; the community-route login failure also reproduces on the prior revision. Node recovery UI regressions cover unknown reading, zero replacing a positive reading, reload state and legacy acceptance screens; the existing Master UI regressions still pass. The nicotine recovery page renders and all three executable inline scripts pass syntax checks. Python compilation and whitespace checks pass.

Live test limitation: secure sign-in visibly reached Home as Santa's Little Helper (Master), but opening the private QA recipe immediately redirected to login; a root-page check also showed login. No live recovery/session/pour changes were made in this attempt, and none of this patch has been deployed. After deployment, retest a labelled simulated nicotine overpour with pour-back, zero/nonzero residue, reload, repeated overage and final original-target weight. Do not treat local test results as a live sign-off.

## Post-deployment live acceptance — 10 November 2026

PR #1 was merged as `0a7ba97f85e529327ba4be8ddb4d02ac014c20d6`; user then updated the server. Fresh private QA session #73 verified deployed changes through the browser:

- Nicotine first; 0.35 g against 0.31 g requires pour-back without an accept-over action.
- After pour-back, scale input clears and current/needed are unknown until measured. Original target and no-tare instructions remain.
- Residue 0.08 g gives 0.23 g needed; zero gives 0.31 g needed and survives reload. A repeated 0.34 g reading remains blocked with pour-back guidance. Reaching 0.31 g completes nicotine and offers bottle movement.
- Biscuit 0.23 g against 0.21 g follows slight-over handling. Subsequent PG target is 2.26 g / 2.18 ml, reduced from 2.28 g / 2.20 ml.
- Vanilla Swirl 0.60 g against 0.31 g pauses. Review displays the no-automatic-correction explanation and does not advance. Review survives reload. Explicit Accept changed batch then advances to bottle movement.

All pours were simulated. Session #73 remains unfinished at PG; no completed production was recorded. Full flavour rebalancing and multi-bottle correction remain unimplemented.

Remaining issue found: the PG adjustment note displayed Estimated final ratio 100/0 because its estimate included completed PG-carried pours but excluded future VG. Follow-up code projects the finished mix using completed readings plus remaining targets, including the newly trimmed PG target and current flavour reading. Completed-batch summaries retain their completed-pour basis. Zero measured amounts no longer fall back to planned amounts. This follow-up needs its own deployment; the live tests above cover PR #1 only.

Follow-up validation: the live 100/0 preview is reproduced by a new API regression on unchanged deployed code; it reports 30/70 after the fix. That test plus the three nicotine pour-back regressions pass, and all 12 final-report-specific tests pass. Changed Python compiles and whitespace checks pass. No live correction or updated deployment is claimed for this follow-up.


## Recipe-preserving recovery implementation — 11 November 2026

The ratio-preview follow-up is merged in PR #2 (`cd1c9735d6822fd4ece772345cdded8784f9fd35`).
The new recovery workflow replaces the previously missing proportional rescue:

- Review the minimum balanced batch or exactly two original-size bottles. The plan
  uses immutable formula snapshots and actual ingredient weights, combines repeated
  ingredient IDs, and restores original targets after an earlier PG trim.
- Confirm capacity/headroom and ingredients. Correct nicotine and all flavours,
  cap/move stock left, mix concentrated liquid, split by measured liquid mass, then
  select and finish PG/VG in each labelled bottle. Refuse impossible two-bottle or
  insufficient concentrate-stage capacity before changing the session.
- Persist every task/partial reading, guard revisions against duplicate requests,
  block ordinary mixing controls, and retain original pours and recovery additions
  in history. Final estimates include actual strength, ratio and per-bottle volume.
- Mixed-liquid nicotine top-ups never use the nicotine-only stock pour-back flow.
  A further physical overpour pauses. Before splitting, replan from all added liquid;
  after transfer starts, retain separate bottle records and require a separate
  correction/additional capacity. Confirmed typing mistakes have an auditable edit.
- Blank input no longer becomes zero. Positive 0.01/0.02 g requirements stay present;
  a positive dose that rounds to 0.00 g rejects guided start with a larger-batch
  explanation. Older incomplete formula captures reject rescue rather than guessing.

Validation: 55 focused Python recovery/nicotine/ratio/report/precision/doc tests pass,
plus a separate legacy-incomplete-formula regression. A broader 202-test run passes
201; the sole failure is the previously reproduced community-route login expectation
(302 versus 200), outside this mixing change. All four JavaScript regression programs
pass. The rendered guided-mix page's three executable inline scripts pass Node syntax
checks. Python compilation and whitespace checks pass.

Deployment/live limitation: these new two-bottle and precision changes have not yet
been exercised on Pauly's server. Local API and JavaScript checks are not live visual
acceptance. Browser access to the local preview was unavailable; no bypass was used.
After pulling main and restarting, run a fresh private simulated 30 ml batch with a
third-flavour overpour, select two 30 ml bottles, reload during a partial top-up,
complete the split and both carriers, and inspect both bottle summaries. Also repeat
nicotine-only pour-back without taring, zero residue, and the 30/70 PG-preview check.
The original saved recipe must remain unchanged.
