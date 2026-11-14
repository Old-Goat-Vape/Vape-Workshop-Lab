# Review knowledge — 14 November 2026

Reviews now provide structured, attributed observations to flavour pages and the
recipe builder. This extends the existing reference-review import; original
reviews, community votes, private tests and recipe statistics remain separate.

## Extraction and provenance

A versioned deterministic extractor stores JSON facts per source review, with a
content hash. Startup backfills already imported reviews and only re-extracts
changed text or a new extractor version. Original text is never rewritten.

Each fact retains literal source evidence, its field and context. API/UI results
also carry the reviewer, original source URL, source key and exact flavour name.
Unmatched, ambiguous and source-conflict reviews cannot inform a selected flavour.
Catalogue consolidation remains safe because findings link to the source review,
whose exact catalogue link is already maintained by the cleanup service.

Current bundled source audit: 1,557 verified reviews produce 13,142 observations.
The 33 existing source conflicts have no text and produce no findings. Field
coverage counts reviews containing a field, not independent votes:

| Field | Reviews |
| --- | ---: |
| Tested percentages | 1,409 |
| Explicit in-mix percentages | 118 |
| Explicit standalone percentages | 107 |
| Percentage guidance with unspecified context | 34 |
| Test steep times | 389 |
| Steep guidance | 147 |
| Strength observations | 205 |
| Sweetness | 907 |
| Off-notes | 833 |
| Mouthfeel / texture | 297 |
| Aroma | 576 |
| Flavour character | 711 |
| Pairing suggestions | 200 |
| Uses / roles | 414 |
| Cautions | 372 |
| Original score and scale | 1,505 |
| Testing setup | 295 |

Descriptive findings remain contextual prose; the extractor does not turn a
mention of sweetness into a numerical sweetness score, normalize incompatible
rating scales, or claim consensus. Unsupported wording stays in the full review.
Negations and qualifiers are retained. Missing values stay unknown.

## Behaviour

Flavour detail pages group findings with expandable evidence and source links.
The existing authenticated builder usage endpoint now also returns knowledge for
exact linked ingredient references; private recipe records are never returned.

Selecting an ingredient shows reviewer guidance alongside recipe averages. A
reviewer's explicit in-mix range has a deliberate button to use its low end;
tested or standalone percentages are never silently applied as recipe advice.
Saved and manually entered values remain editable and are never replaced by
incoming review findings. Stale asynchronous responses cannot leak another
flavour's guidance into the current row.

Steep planning uses the longest explicit recommended duration among ingredients
with positive percentages. The builder shows coverage and requires a click to
apply it. Unknown ingredients do not imply shake-and-vape. Test durations are
separate and never drive the suggestion. Removal or a 0% amount removes an
ingredient's contribution. Actual readiness still depends on the complete mix.

The Old Goat-only import report includes extraction coverage. Findings remain
machine-readable for subsequent intelligence features. No automatic potency
conversion, substitution scoring or new community ratings are introduced here.

## Validation and deployment

35 targeted tests passed, with the six extraction/integration tests rerun after
final narrative extraction changes. Another 36 regression tests passed. One Lab
template test fails on the missing recipes.library endpoint; the identical
failure was reproduced on unmodified main (6cb7958), so it predates this change.
JavaScript tests pass for manual values, selection races, removed rows, no recipe
averages, zero-day guidance, partial coverage and exclusion of 0% ingredients.

Deploy with tvw-update. Startup creates the new table and backfills all existing
review records, logging the extraction count. Subsequent unchanged restarts skip
re-extraction. Production catalogue-match coverage and visual layouts still need
a live check after deployment.
