# Attributed reference flavour reviews

Requested by Old Goat on 13 November 2026, who reports explicit permission from
SessionDrummer to use his flavour reviews and other relevant data in TVW.

## Collected SD sources

- Index: https://sessiondrummerreviews.com/
- 1,177 index entries across 61 manufacturer/series headings, resolving to 65
  distinct discussion URLs (some headings span more than one discussion).
- Source: public rendered SD forum posts, author verified as SessionDrummer.
- 1,144 entries have review text verified against an exact flavour header.
- 33 need source review: index/post naming disagreements or unavailable matching
  text. These retain their source link, but are not attached to a flavour page.
- Repeated omnibus/individual posts are deduplicated by source identity. The
  individual post is preferred when its heading matches. Exact heading extraction
  from a same-author omnibus is the fallback; a different flavour's text is never
  substituted because an index permalink points to it.
- Original prose is preserved as plain text, with reviewer and original permalink.
  Images/remote embeds and HTML are not imported. The compressed JSON bundle is
  UTF-8 JSON → gzip → base64; its envelope records origin, date and permission.
  There are no credentials, authentication cookies or private SD member records.

## Application behaviour

Startup imports the local bundle once, without network requests. Catalogue matching
uses manufacturer aliases plus exact canonical flavour identity. Formulations,
versions and product lines stay distinct; ambiguous and missing matches are held in
an Old Goat-only report at `/flavour-review-imports`. Matched reviews appear in a
separate Reference reviews section on flavour details. A report button can recheck
unmatched identities after catalogue additions or consolidation.

Reference reviews do not create user accounts, masquerade as TVW user posts, change
community ratings or overwrite private tasting records. Original 10-point scores
remain within the original prose. Testing percentages remain observations, not
recipe percentages; no steep recommendation is inferred or applied by this import.
Flavour consolidation preserves matched review references. Repeated imports do not
create duplicates. Existing records are retained rather than silently overwritten.

## Old Goat Drive source still blocked

The supplied `/drive/u/2/my-drive` link identifies a browser account slot, not a
review folder. The Drive connector currently accesses a different Google account;
searches for reviews/flavour/flavor found no review collection. The browser is signed
out of the Old Goat Drive account. No Old Goat review files have been imported.
A direct review-folder/file link with accessible permissions, or restored account
access, is required to finish that source. Do not report the SD collection as a
completed import of Old Goat's reviews.

## Validation and deployment

Normal `tvw-update` and restart; new tables/indexes and the one-time data import run
on startup. The report provides actual live matched/unmatched counts; collection
counts are not a claim that all reviews match the production catalogue.
Tests cover idempotency, author/source rendering and escaping, separate rating
counts, version/line boundaries, ambiguity, report access, reconciliation and bundle
integrity. Live UI checks follow deployment.
