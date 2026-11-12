# Flavour discovery: search and aggregate recipe insights

## Behaviour

Catalogue, recipe-builder autocomplete and stash quick-add share an independent search scorer. Searches recognise common manufacturer aliases and words in any order. Direct matches are returned first; only when no eligible direct results exist does conservative spelling similarity run. Suggestions are labelled and still require explicit selection. Numeric formulation tokens cannot be typo-corrected (v1, v2, product numbers). Queries are parameterised, normalised and bounded. Hidden, merged, invalid, inactive and excluded products remain filtered. Reference-only ingredients without a catalogue record remain supported in the builder.

Flavour detail pages now show sample count, median, average, observed minimum/maximum and a five-bin tabular usage breakdown. The explanation distinguishes recipe usage from single-flavour testing recommendations and explains median vs average. Fewer than five samples gets an explicit small-sample note; no data renders an empty state rather than invented percentages.

Pairings are counts of other eligible visible concentrates occurring in the same eligible current recipes, linking to those flavours. Counts describe observed combinations, not taste-quality guarantees. Each recipe counts once; repeated target ingredient lines are summed. Non-positive and over-100 percent observations are excluded, including duplicate sums above 100. The catalogue Used In count now uses current versions, across public, private and unlisted recipes rather than historical versions.

The source set includes public, private and unlisted TVW recipes, using recipes.current_version_id with recipe ownership of that version checked. Hidden, inactive or moderation-archived/removed recipes do not contribute. Only aggregates are exposed: recipe identities and formulations retain their existing access controls. The separate visible recipe list remains public-only and its count may therefore be lower than the analytics sample count. The separate Lab analytics engine and its cached private data are not used or changed. Existing imported catalogue hints remain separate and labelled; their unknown sample size is disclosed. No SD data, notes, code or assets were imported.

## Performance and deployment

Startup adds recipe_items indexes for ingredient/version and version/ingredient lookup and an index on flavours.source_flavor_ref_id. The aggregate statistics are read at request time, so edits, current-version changes and moderation changes take effect without an admin recomputation. The search scorer and text normalisation use bounded process caches; their inputs contain only public catalogue identity strings, not private recipe data.

A local synthetic benchmark with 16,000 added references returned 100 direct matches in 0.609s and 100 typo suggestions in 0.445s. These are local measurements, not a server performance guarantee.

Deploy with tvw-update. No manual data import or cleanup command is needed. Live visual acceptance follows deployment: search `Inawera Biscuit`, a misspelling such as `vanila`, check manufacturer selection and v1/v2 distinctions, open a flavour with known public/private/unlisted test recipes and verify aggregate counts while the visible recipe list stays public-only, then check an empty-sample flavour.

## Validation

158 API/search/product eligibility/discovery checks: 157 passed, with the pre-existing community-route redirect expectation failure remaining. New discovery tests cover aliases, reordered words, numeric formulation separation, labelled suggestions, direct-first behaviour, hidden/noningredient exclusions across all pickers, all-visibility current-version aggregation, duplicate lines, invalid percentages, empty states, sample labels and private recipe detail protection. Existing catalogue hint copy assertions were updated for the clearer source labels.

Python compilation, JavaScript syntax and whitespace checks pass. Guided Mix policies, steps, recovery behaviour and tolerances are unchanged.

## All-recipe source correction

At the user’s explicit direction, all recipe visibility levels now contribute to aggregates and catalogue Used In counts. This supersedes the initial public-only source policy. The current-version, moderation, numeric validation and distinct-recipe rules remain. Regression fixtures prove private/unlisted samples affect statistics/pairings without appearing in the visible recipe list.
