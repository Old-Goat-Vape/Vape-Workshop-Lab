# VC private recipe importer repair

## Confirmed problems

- The OldGoat profile loads its recipe table through POST controllers/userPageControl.php with getUsersRecipes=true, userID, start and length. The prior importer only searched static profile-page links.
- Public lists use GET controllers/publicRecipesPageControl.php with page/search. The prior discovery pass spent its budget on static sidebars, fallback endpoints, sitemaps and guessed ID ranges before detail parsing.
- Existing resume cursors were not applied to the real public controller; the cursor advanced one regardless of what discovery/detail work had finished.
- Recipe table parsing combined the ingredient name and numeric cells, polluting manufacturer/name identity with the percentage.
- Private library sync left current_version_id unset and actor ownership unassigned, relied on visibility defaults, and deleted the latest reference version on each refresh. Missing pointers excluded these imports from current-version analytics.
- Benchmark exclusions discarded POD/single-flavour recipes before library persistence, preventing recovery of those personal recipes.

## Result

One real source-list page per run, bounded detail requests, no ID guessing in the active path. OldGoat profile uses the verified server-side recipe table protocol. Resume repeats a partially processed page and skips fully imported recipes even when flavour identity review is pending; complete pages advance, end-of-feed wraps to the first page. Unexpected controller responses preserve the cursor and report an error. HTTP 403/429/503 pauses detail processing rather than continuing requests. Source access restrictions are not bypassed: recipe detail pages must be available and contain the actual ingredient table.

Imported recipes are explicitly private/reference, owned by the importing operator (or the Old Goat account for scheduled work). Source author and source link are retained and visible in the private recipe page. Current-version pointers are populated so the all-recipe aggregate analytics can use them. Existing VCREF metadata is repaired on the next scraper run without changing saved versions/items.

Identical imports reuse the saved version. Changed imports append a new version and preserve existing versions, pours, and mix-session links. A reference record is only skipped as completed when it links to a valid current library version with the expected number of ingredients. Explicit targeted imports can refresh an already-imported recipe. Benchmark eligibility is still calculated separately: adapted/POD/single-flavour recipes can be stored privately without being treated as clean DTL benchmarks.

The Lab visibility/authentication gates were not broadened. Ordinary members cannot open the scraper console or trigger its endpoint. No imported recipe is published to the community.

## Verification

- Public primary sources inspected: https://vceliquidrecipes.com/recipes, https://vceliquidrecipes.com/user/2078, https://vceliquidrecipes.com/recipe/44389 and the two controllers called by those pages.
- Live source smoke test fetched Yogi the Pink Bear (44389) into an isolated local database: status success, one stored reference, one private library recipe, valid current_version_id, no reported import error. This was not a production-server import.
- 26 scraper/parser/run-state tests: 24 passed, two existing skips. Covers profile protocol, real public pagination, response errors, parsing identities, private ownership/current version, idempotency, append-only updates with existing mix links, metadata repair, source access/invalid recipe rejection, a full import pipeline, partial-run resume and pending-review completion, plus Lab role protection.
- Broader API/recipe-create/import run: 156 tests, 154 passed; two previously known redirect expectation failures remain.
- Python compilation and whitespace checks pass.

## Deploy and verify

Run tvw-update. In the Lab scraper console select OldGoat (VC user 2078). Reset that profile cursor once if it was produced by the old static-page scraper, then use Resume for following batches. Verify source author/URL and ingredient percentages on a private imported recipe and confirm normal-user recipe discovery does not expose it. A separate public-source backfill can then run in bounded resumable batches. Production-server acceptance remains to be done after deployment.
