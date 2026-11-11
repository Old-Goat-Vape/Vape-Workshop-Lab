# Pauly feedback: completion and community help

## Changes

The completed guided bench hid its three panels but retained the desktop grid's minimum height (up to 720px). The report appeared below an empty workspace. Completion now hides that container, shows the report, and moves focus/scroll to it once. Dashboard, Recipe, Mix Again and View Batch Report links are visible above the report. Reloading a completed session follows the same path; repeated updates do not steal focus from mixer notes.

The completion heading identifies the target batch, e.g. `10 ml batch complete`. The report labels `9.99 ml` as the estimated volume from recorded pours. Actual measurements, calculations, recovery targets and mix tolerances are unchanged.

Selecting Help Needed in the builder or owner status controls publishes the recipe. Builder copy explains this and updates the visibility selection immediately. A dedicated dashboard section lists up to six recent eligible public help requests and links to all help requests. Public moderation rules apply even for administrators in this section. Existing private Help recipes are not mass-published; selecting Help or saving with Help publishes them.

Comments, tasting notes and suggestions are inline below the recipe ingredients, rather than hidden in a modal. The form accepts a suggestion without an invented score. Empty posts and invalid scores are rejected; moderation and muted-account protections remain. Status changes now require recipe ownership or administrator access, closing an existing API permission gap.

## Validation

- 165 regression checks across recipe creation, API, final batch report and the new feedback regressions: 163 passed; two pre-existing failures remain (Old Goat private detail and community route redirect expectations).
- Six focused regressions pass, including real execution of completion/rendering JavaScript, target 10 ml vs measured 9.99 ml, public Help discovery, non-owner status denial, moderation filtering, and posting an unrated suggestion.
- Rendered recipe/detail and builder inline scripts pass Node syntax checks. Python compilation and git diff whitespace checks pass.
- Live visual and end-to-end verification requires deploying this commit with `tvw-update`.

## Deployment

Run `tvw-update`. No manual database migration or catalogue cleanup command is needed. After deployment, verify a completed test session at desktop/mobile widths, the dashboard Help section, a new Help recipe selected while Private was chosen, and a second user's comment on that recipe.

The remaining 40 catalogue proposals are deliberately deferred.
