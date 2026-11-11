# Flavour catalogue cleanup — 11 November 2026

Guided Mix is frozen for human testing. This change concerns catalogue discovery, identity and imports.

## Live findings

The public catalogue contained 17,611 rows, including Ceramic Tweezers, Gemini Hybrid Empty Pod,
NexMesh Extreme, UB MAX/UB Pro Coils and Cotton Bacon V2. Searching `27 fish` returned three entries:

| Catalogue ID | Name | Manufacturer |
| --- | --- | --- |
| 15866 | 27 Fish | CAP |
| 2957 | 27 Fish (Sl) | CAP (SL) |
| 862 | 27 Fish Concentrate SL | CAP |

Capella identifies [27 Fish as SilverLine](https://capellaflavors.com/products/27-fish).
These naming variants therefore have one identity: **27 Fish — CAP (SL)**.
No live records were merged or deleted during investigation. Linked ingredient IDs, prices and
dependencies must be checked on the server before choosing the surviving catalogue ID.

## Resulting behaviour

- Flavour discovery, its manufacturer filter, builder choices and stash quick-add exclude stored
  hardware/base/nicotine categories and nicotine-tagged references. Public catalogue and stash
  choices also exclude hidden, invalid, inactive-reference, moderated and merged records.
- Catalogue pagination counts use the same visibility rules as its rows.
- A stale catalogue `Flavor` label cannot override a linked hardware/base/nicotine reference.
- Manual additions, Lab-to-TVW sync, BLCK, VC flavour enrichment and VC recipe reference creation
  recognise naming aliases rather than create another flavour. Priced references are preferred.
- Manual inventory edits that would create an existing flavour identity return a conflict message
  without changing the item or its stock quantity.
- CAP product lines survive a missing series marker in the product name. Known 27 Fish/27 Bears
  SilverLine aliases are explicit exceptions; other unmarked series are not guessed.
- Versions, manufacturers, concentrations and product lines stay separate. Clyrolinx no longer
  discards bracketed version markers when matching names.
- BLCK category detection uses product signals rather than substring matches such as `can` in
  Candy or `cap` in Capella. Specific observed hardware names are recognised by category inference.
- Admin duplicate review recognises identity aliases and excludes already merged sources.
- Merging catalogue aliases that share one ingredient ID cannot delete recipe ingredients.

## Reviewed data cleanup

The new command has a read-only default. Use the **actual database path from server startup logs**;
do not assume the repository's sample database is the live database. Pause the app and import jobs
while applying cleanup, so a fresh backup and the reviewed plan describe the same maintenance window.

```bash
python app/scripts/cleanup_flavour_catalogue.py --db /actual/path/tvw_calculator.db > catalogue-preview.json
```

The JSON contains `duplicates` and `category_corrections`, with complete before snapshots, issues
and fingerprints. Review proposed categories and identities. Suggestions are not automatic approvals.
For a selected category correction, replace the example ID/user with IDs from the preview/server:

```bash
python app/scripts/cleanup_flavour_catalogue.py --db /actual/path/tvw_calculator.db --apply-plan catalogue-preview.json --category-id 235 --actor-id 6 --backup /safe/path/before-category-cleanup.db
```

Generate a fresh preview after category corrections. Then consolidate a reviewed duplicate group
by **its preview target ID** (the priced linked reference is preferred), not an assumed live ID:

```bash
python app/scripts/cleanup_flavour_catalogue.py --db /actual/path/tvw_calculator.db --apply-plan catalogue-preview.json --target-id TARGET_FROM_PREVIEW --actor-id 6 --backup /safe/path/before-duplicate-cleanup.db
```

Repeat `--target-id` or `--category-id` to select multiple groups/corrections of the same type.
An existing backup is never overwritten. Applying requires an existing Master/Old Goat actor.
Changed fingerprints, conflicting ingredient links, references shared outside a group, and
unfinished guided mixes block affected operations. A failure rolls back the complete selected apply.
There is no fuzzy merge or apply-everything switch.

Consolidation sums duplicate recipe percentages and member stash quantities; retains favourites,
individual Lab stock lots, their costs/notes and quantities; moves notes/reviews/import matches;
preserves spelling aliases and old public URL redirects; and records a full audit snapshot.
Source catalogue/reference rows and all original prices/supplier metadata remain archived.
Historical mix measurements and session snapshots are not rewritten.

Use this reviewed command for cross-catalogue consolidation. The older generic library repair and
manual merge tools are not substitutes: their scope and preservation rules differ.

## Deployment boundary

Code changes alone do not consolidate existing live duplicates or correct legacy category values.
Deploy, run/review the preview against the server database, apply selected corrections with backups,
then retest `27 fish`, hardware searches, recipe links and stash totals. Ambiguous entries stay for review.
Direct third-party SQL writes are outside the supported import matching paths.

## Validation

- Catalogue, flavour search, scraper parsers and scraper-run checks: 41 tests, 39 passed,
  two existing skips. This includes 14 catalogue regressions covering alias reuse, separate
  formulations, category boundaries, edit conflicts, reviewed category correction, backup creation,
  refusal to overwrite backups, transaction rollback after a partially attempted merge, stale
  previews and unfinished-mix protection.
- Full `TVWApiTests`: 136 tests, 135 passed; the previously observed
  `test_community_route_renders_in_required_discovery_states` still expects 200 where login returns
  302. This is not a green full-suite claim.
- No Guided Mix implementation or template files were changed. Live post-deployment catalogue
  cleanup and browser verification remain outstanding.
