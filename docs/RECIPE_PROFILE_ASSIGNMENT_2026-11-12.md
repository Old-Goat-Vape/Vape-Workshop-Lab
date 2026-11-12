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
