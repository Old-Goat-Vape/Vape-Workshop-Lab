# Optional first-mix walkthrough

`/first-mix` is available to signed-in users from the dashboard invitation and account menu. Five sections explain PG/VG and draw styles versus nicotine forms, exact flavours and stash quantities, calculator inputs, final carrier accounting, the guided bench and private tasting records. It is never a prerequisite to creating or mixing a recipe. Without JavaScript all sections remain readable.

The enhanced view remembers only step/completion in a user-scoped browser key (`tvw-first-mix:v1:<user_id>`). It does not assert that a user has mixed, populate a stash, submit a recipe or start a guided mix. Restart only resets guide progress. Sources and actual app labels are visible in the guide.

The practice link opens `/recipes/new?practice=1`: a private WIP draft at 10 ml, 0 mg/ml and 30/70 PG/VG, with no preselected flavour percentages. Opening it writes no recipe. Targets remain editable; the guide tells users to check them before mixing. Practice uses `new-practice`, separate from the normal `new` browser draft. Save-success cleanup and validation-error rendering retain the practice slot. Ordinary recipe defaults are unchanged. A tested save preserves 10 ml, zero nicotine and private visibility.

The carrier arithmetic example is conditional, nicotine-free and explicitly not a recommended flavour percentage. It accounts for 1 ml PG from an example concentrate + 2 ml plain PG + 7 ml VG = 10 ml at 30/70 PG/VG. Gram targets depend on density. The guide does not prescribe a nicotine strength or new recovery rules; it states nicotine is first when present and directs errors to the actual bench instructions. Unsupported mixed nicotine carriers are not approximated. No health benefit or device suitability guarantee is claimed.

Sources checked 13 November 2026:
- https://support.innokin.com/hc/en-us/articles/4403072416275-How-to-choose-E-liquid
- https://support.innokin.com/hc/en-us/articles/4403080416019-Vape-Styles-MTL-DTL-RDL
- https://www.fda.gov/consumers/consumer-updates/how-properly-store-e-liquids-and-prevent-accidental-exposure-e-liquids-children

Validation: 28 Python tests passed (guide, builder persistence, recipe story and Lab regressions), plus the Node guide controller test and syntax check. Covers authenticated access, read-only guide, practice defaults/zero persistence, separate draft key, navigation/URL state, restore/reset and unavailable storage. Fixed the existing builder test-login helper to clear the prior simulated account's session/password signature when switching users; no production authentication changes. Live visual/keyboard/mobile QA follows deployment.
