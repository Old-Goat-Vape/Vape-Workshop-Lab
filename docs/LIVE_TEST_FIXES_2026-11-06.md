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
