# Batch steeping and in-app reminders — 14 November 2026

## Behaviour

Each new guided/quick session snapshots steep guidance from its selected recipe
version into batch_steeping as part of the existing session transaction. The
original guidance and the batch's editable steep duration remain separate.
Recipe edits do not move an existing batch's plan. Existing session records are
backfilled once from their selected versions during startup; missing guidance
remains unknown rather than defaulting to zero.

Guided timers use actual session completion, not session creation. A reopened or
abandoned guided session cannot generate a ready reminder. Finishing it again
uses the new completion timestamp; an acknowledgement from the earlier schedule
does not suppress the new due reminder. Quick logs may just be calculations, so
the mixer must explicitly confirm that the bottle has now been mixed. Repeated
confirmation does not restart the timer.

States distinguish Not mixed yet, Not set, Shake & vape (explicit 0), Steeping
and Ready to check. Durations are editable whole days from 0 to 365, or blank.
Dates are computed in UTC from the physical mix timestamp, then displayed in the
browser's local timezone. A ready date is a planned tasting check, not a claim
that every recipe will taste mature on that date.

## Discovery and reminders

The completed guided-mix report links to Steep this batch & set reminder. Both
desktop and mobile My mixes views link to each batch's plan. My steeping shelf
is available from My mixes and the user menu. The shelf paginates at 50 bottles
and prioritizes due, unacknowledged reminders ahead of newer batches.

Reminders are opt-in, persistent alerts inside TVW when the user returns. They
are not email, SMS, push or desktop notifications. A header banner and private
shelf badge remain until acknowledged. Disabling reminders cancels the alert;
changing a plan re-arms it, while saving an unchanged plan preserves dismissal.
No scheduler, external notification service or browser permission is required.

## Private tasting records

Each batch has private tasting entries with timestamp, actual batch age, readiness
(not sure / needs longer / about right / past best), optional 0–10 score and notes.
Zero scores are preserved. These observations are stored for future intelligence
work, but are not silently published or counted as community flavour votes.
They do not automatically change the recipe or its recommendations.

All batch views/actions require ownership of the mix session, including for Old
Goat. Existing private history remains available to its mixer if recipe sharing
is later revoked; these pages expose no ingredient formula. POSTs require a
session CSRF token. Tasting text is escaped. Invalid durations, scores, unknown
readiness values and tasting before a real mix are rejected.

## Validation and deployment

43 distinct tests passed: 10 batch-steeping tests, 29 existing sharing/library/
recipe-builder/review-knowledge tests and four guided-mix/My mixes regressions.
The completed guided-mix page's batch-plan link was also rendered and checked.
Coverage includes frozen guidance, unknown versus zero, UTC boundary, quick
confirmation, restart persistence, opt-in/cancel/acknowledge, rescheduling,
reopened/abandoned sessions, due ordering, privacy, CSRF and tasting validation.
Guided late-session responsiveness, duplicate-step handling and slim mid-session
summaries continue to pass. JavaScript syntax and git whitespace checks passed.

Before this work, the deployed sharing page was checked as Santa's Little Helper:
the owner controls rendered and username search found Pauly Walnuts without
granting anyone access. Deployed FLV Toffee showed 26 sourced observations and a
14-day planning suggestion. The new steeping UI still requires a live visual
check after deployment.

Deploy with tvw-update. Startup creates the two new tables and idempotently
backfills plans; all reminders default off. No existing recipe, mix history,
private notebook, sharing permission or community rating is overwritten.
