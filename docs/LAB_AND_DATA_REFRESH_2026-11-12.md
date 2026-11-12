# Lab navigation, data review and TVW dashboard refresh

## 1. Return to TVW

Every Lab page now has a prominent **Back to TVW Calculator** link to `/community`, including the Lab dashboard. The header wraps on small screens. Existing Lab role gates remain in place; ordinary users are not given a Lab entry point.

## 2. Data review

### Capella custard identity

Capella's own catalogue lists the original **Vanilla Custard** separately from **Vanilla Custard V2**:

- https://capellaflavors.com/products/vanilla-custard
- https://capellaflavors.com/products/vanilla-custard-v2

The original is commonly labelled V1 by retailers (e.g. https://www.flvrs.com/eg/products/flavoring/32/vanilla-custard-30ml/2003/16162). The product-specific canonical identity now equates CAP Vanilla Custard, CAP Vanilla Custard V1 and CAP Vanilla Custard (V1). This is deliberately NOT a general unversioned = V1 rule. V2, SilverLine and other manufacturers remain separate.

Recipe autocomplete displays one verified identity and prefers an existing priced reference among duplicate candidates. Usage suggestions and detail-page statistics include equivalent reference IDs, with duplicate ingredient lines summed and each current recipe counted once. No stock holdings, prices, reference records or recipe versions are rewritten or deleted. Physical catalogue duplicates can still be reviewed through the existing backed-up cleanup tooling; this update does not apply a mass merge.

### The twelve VC failures

All twelve public source pages were inspected on 12 November:

| Source IDs | Finding | Change |
| --- | --- | --- |
| 18362, 44917 | Valid formulas; prose describes a restricted lung device | Access restrictions are read from actual notices/headings, not the word “restricted” in recipe prose |
| 45961 | Valid formula with Black Pepper at 0% | Ignore zero-percent ingredient rows |
| 2796, 2970, 3120, 41002, 41882, 42543, 42680, 43871, 43914 | Flavour-testing/review posts with “No Flavours” in the ingredient table | Record a skipped/no-formula outcome rather than inventing ingredients from percentages in the prose |

The recipe parser no longer falls back to unrelated tables or review text when the actual formula is empty. No-formula posts are excluded from recipe-derived averages. The queue/report shows a separate **No formula** count. Existing failed queue items need **Retry failed recipes** after deployment; successful items are retained.

An isolated live-source smoke test imported all three real formulas successfully with no errors. Source 42543 was correctly returned as an empty-formula post with no library entry. Production queue outcomes have not yet been retried under this update.

## 3. Simpler Lab tools

The Lab dashboard uses plain labels for catalogue items, low stock and today's mixed volume, with direct shortcuts to imports, prices, recipe data and people. The admin navigation is now **Imports & prices / Recipe data / People / Activity / Restock**. The familiar action URLs remain unchanged.

BLCK and Clyrolinx price updates are visible everyday actions. VC import progress remains visible. Scheduler internals and technical run history are collapsed; duplicate buttons and the misleading cached “Never run” list are removed. Matching tools are grouped under **Fix flavour matches**, and broader maintenance actions stay in a collapsed section. No permissions or guided mixing behaviour changed.

## 4. Dashboard imagery

The TVW Start Mixing card now uses an original generated workbench photograph, compressed as a 128 KB WebP with explicit dimensions and mobile sizing. Existing primary actions, resume-mix state, help requests and discovery remain intact.

Asset: `app/static/img/dashboard/mixing-bench.webp` (1536×1024). Generated with the built-in image tool from this prompt: “Premium editorial photograph for an adult DIY e-liquid recipe calculator: precision digital scale with empty mixing bottle, unbranded amber concentrate bottles and recipe notebook on a charcoal workbench; warm amber side light and subtle cyan edge light; real glass and metal textures; no people, pouring, smoke, devices, readable text, logos or watermark.” Original generated output was converted to WebP for the repository.

## Validation and deployment

- Lab-enabled tests cover navigation and role restrictions, collapsed diagnostics, both dashboard mix states, curated identity boundaries, price/history preservation, same-recipe alias sums, parser access notices, zero-percent rows and no-formula skip outcomes.
- Existing catalogue, recipe usage, import persistence and queue recovery tests pass.
- Node tests cover editable suggestions and import progress/error rendering.
- Local templates render successfully. The cloud browser cannot reach the local preview server, so final desktop/mobile visual acceptance remains a production check after `tvw-update`.
- `VC_IMPORT_WORKER_ENABLED=0` can disable the background worker for isolated tests; production default remains enabled.
