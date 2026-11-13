# Old Goat Drive reference reviews — verified supplied-folder coverage

The author supplied the direct review-folder link on 13 November 2026:
https://drive.google.com/drive/folders/1NcEtMMfDLE0IRyA_UL83Vz4THkPpL4ac?usp=sharing

The connected Drive reader collected 50 Word documents in the root and nine in
manufacturer subfolders. A subsequent signed-in browser check verified the root
through its last item and all eight subfolders: CAP 0, FE 0, FLV 0, FW 1, IWN 1,
LB 1, SSA 2 and TFA 4. The supplied folder contains 59 documents; all were read.
Fifty-eight are flavour reviews and one is unrelated forum planning. This verifies
coverage of the supplied folder and descendants, not other locations in the Drive.
Source documents and sharing permissions were not changed.

## Packaged records

- 58 identifiable flavour reviews, credited to **Old Goat**.
- Nine manufacturers, keeping Capella Silverline separate from regular Capella:
  CAP 19, CAP (SL) 1, FA 15, FLV 14, FE 1, FW 1, INW 1, LB 1, SSA 2, TFA 3.
- One excluded document: `Banana Cream by The Flavor Apprentice.docx` contains
  forum-planning prose rather than a flavour review. Its text is not published.
- Identity comes from the review heading, never just the filename. The builder
  rejects unrecognized headings. Reformulations/version labels are preserved:
  Sweet Strawberry RF, V1, Vanilla Custard V1/V2, Vanilla Cupcake V1/V2, etc.
- Original readable text is retained unchanged with a SHA-256 checksum, the source
  document ID/link, filename and modification time. Documents/tables are rendered
  as plain text by the Drive reader; source document formatting and images remain
  available in the original Drive file, rather than embedded in TVW.
- Setup steep time and later steep advice both remain in the prose, even when they
  differ. Nothing is averaged into a prescription or copied into private tests.

## Deployment and reporting

Normal `tvw-update` and restart. The existing importer now discovers each local
review bundle independently and records its hash. Existing SD imports remain
untouched; the new Old Goat bundle imports once. Later batches can use the same
Drive-ID source keys without multiplying existing reviews.

Only unique exact catalogue identities become visible on flavour pages. Unmatched
or ambiguous identities remain in Review imports, which now shows totals per
reviewer and allows filtering exceptions by reviewer. The existing exact-match
recheck button can attach reviews after catalogue additions/cleanup. Review score
scales remain separate from community ratings and no new user account is created.

## Checks

41 targeted tests passed across Old Goat import, SD/reference reviews, purchase
purposes, flavour tasting and catalogue identity. Includes content checksums,
non-review exclusion, version/line preservation, attribution, score separation,
independent bundles, restart idempotence and reviewer filtering. Live match totals
and UI verification still require deployment.
