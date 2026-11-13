# Private purchase reasons and shopping list

Members can save multiple reasons for each canonical flavour: an intended recipe,
private notes, a source URL and optional planned purchase in ml. Accessible from
flavour details and My Stash; My shopping list also appears in the account menu.

New reasons can be To buy or Already bought. Existing stock defaults the form to
Already bought. Buying, archiving and restoring preserve the record. Bought it
opens the existing stash quantity editor rather than guessing a purchased quantity.
Removing stock does not remove purchase reasons. Catalogue consolidation repoints
all purchase records to the surviving flavour without combining or dropping notes.

All reads and writes are owner-scoped, including for Old Goat. Recipe choices use
the existing library visibility rules and links are checked again on every read.
A recipe becoming unavailable shows a neutral placeholder rather than its name.
Source URLs allow only HTTP/HTTPS without embedded credentials; no source is fetched.
This feature does not change stock, pricing permissions or the Lab shopping tools.

Deployment: normal tvw-update and service restart. Startup migration creates the
new table and index idempotently; no separate cleanup command or data backfill.

Validation: purchase lifecycle, multiple reasons/editing, owner boundaries, anonymous
access, bad URLs/numbers, stale private recipe links, unchanged stock, and duplicate
consolidation. Regression suites include flavour tasting and catalogue identity.
Live browser checks follow deployment.
