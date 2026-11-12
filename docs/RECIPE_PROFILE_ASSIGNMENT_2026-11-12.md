# Recipe profile assignment

Lab → Lab tools → Recipe data → Assign recipe profiles & artwork.

This is an explainable local matching tool, not an external AI or a tasting assessment. It reads all active recipes' current versions, including private imports. It never sends their contents to another service. Saved non-empty profiles are excluded; uploading a photo still takes priority over profile artwork.

## Workflow

1. Open the preview. Nothing changes on GET.
2. Review clear matches, uncertain suggestions and recipes without recognised evidence. Each card includes its ingredient percentages and reasoning. Lists paginate at 50 recipes.
3. Apply clear matches together (all pages), or choose a main profile and apply one recipe. Inferred supporting profiles are retained when selecting a different main profile.
4. Repeat after recipe imports. No automatic recurring job is enabled.
5. Undo a recent batch if needed. Undo only restores assignments still matching that batch on the same current version; later owner profile edits and new versions are preserved.

## Matching

Whole-word/phrase rules use recipe name, exact profile tags, description and ingredient names/percentages. Sweeteners do not determine the finished profile. At least 80% recognised flavour coverage plus agreement with the recipe name, or a very dominant unopposed ingredient category, is needed for a clear match. Fruit families can combine. Unknown ingredient coverage and name conflicts prevent bulk application. Descriptions supply review context, never certainty. Dessert names can lead when built from cream/bakery components (e.g. strawberry cheesecake); tobacco/dessert conflicts require review. Brand names alone do not establish a profile.

The vocabulary is intentionally finite. Unsupported names are flagged rather than filled with a guess. This first pass needs sampling against the actual catalogue after deployment; “clear” is a rule result, not independently measured accuracy.

## Data and concurrency

Only `recipe_stories.profiles_json` on the current version is filled; no formula version is created. Other story fields, photos, recipe visibility, stock and personal notes are unchanged. A new indexed `recipe_profile_audit` table records batch, recipe/version, actor, previous and assigned profiles, rule version, evidence and UTC time.

Write requests require actor-bound signed previews (two-hour lifetime). The selection is recomputed under `BEGIN IMMEDIATE`; any affected recipe/input change invalidates the preview atomically. Batches commit once or roll back completely. Master/Old Goat roles only; the page exists only with Lab modules enabled.

## Verification

Run from `app`:

```sh
TVW_ENABLE_LAB_MODULES=true python -m unittest tests.test_recipe_profiles tests.test_recipe_artwork tests.test_recipe_story tests.test_lab_refresh
```

Covers dish-vs-fruit identity, component recipes, unknown ingredients, word boundaries, sweetener exclusion, ambiguous recipes, description context, private access, bulk assignment, formula/photo/story preservation, signed preview tampering, stale inputs, manual assignment and undo with later owner edits.

## Second pass (rule version 2)

The owner's follow-up authorised a wider first assignment to refresh most recipe cards. The 1,479 remaining review cards were inspected through the live Lab UI; a local, transient copy of those cards was used to estimate coverage. No private recipe export is included in Git. Revised matching supports approximately 700 further recipes in that snapshot (about 700 before six reviewed examples were assigned live). This is an estimate: saved tags, legacy notes and concurrent imports may change the exact production count. These are inferred categories, not measured tasting data.

Expanded descriptive vocabulary includes cronut, grahams, crumble, cobbler, buttermilk, gelato, macchiato, limeade and more fruit/confection names. Rules distinguish ice cream from cooling agents and peanut butter/beer nuts from generic cream/drinks. Recipe author credit suffixes do not count as flavour evidence. A coherent bakery/cream/custard family or a clearly dominant recognised category can support an unnamed recipe; mixed confections can use the broader Dessert category. Unknown-heavy blends, conflicting tags and tobacco/custard identity conflicts still need review. Descriptions remain contextual hints, never sufficient evidence by themselves.

Manufacturer references checked for ambiguous flavour names (profile only; no usage or steep recommendations imported):

- FlavourArt Soho, tobacco: https://flavourart.com/store/en/store/flavors-en/flavors/tobacco-flavors/soho-flavor/
- Flavorah Beer Nuts, roasted nuts: https://shop.flavorah.com/shop/beer-nuts-type-866
- Flavorah Kentucky Blend, tobacco: https://wholesale.flavorah.com/shop/kentucky-blend-947
- FlavourArt Polar Blast in its fresh/menthol category: https://flavourart.com/store/it/23-aromi-freschi-e-mentolati

### Deployment applies this pass once

After schema migration, Lab-enabled startup calls `backfill_once()` with the fixed migration key `recipe-profile-families-2026-11-12`. It fills only currently empty profiles that the revised rules support. Saved owner profiles and earlier assigned profiles are preserved. There is no additional Apply click needed for this one-time pass. Normal imports later still use the Lab preview/apply tool.

`recipe_profile_backfills` stores the migration marker/count/batch. The audit uses actor 0 for this system migration and rule version 2; the Lab labels that batch “Automatic second pass.” Both the marker and assignments commit in the same transaction. A failed audit/assignment rolls everything back. Later restarts neither repeat the assignment nor reverse a user undo. Formula amounts, recipe visibility, photos, notes and steep times are untouched.

Expected startup log: `[recipe-profiles] second pass assigned=N batch=... (undo available in Lab recipe data)`.

Validation: 38 focused tests passed, including the startup path on existing recipes, single execution, undo surviving restart, saved-profile preservation and atomic rollback on audit failure.
