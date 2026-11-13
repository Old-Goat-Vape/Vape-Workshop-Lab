# Additional Old Goat reference reviews — 13 November 2026

Source: https://drive.google.com/drive/folders/1lXAQE4lIJaoVBQnpgDFI_aj_9ieJRq4X

The author requested this import. Signed-in Drive listing verified 276 root
documents, eight DIYFS documents and 74 FA documents: 358 Word documents total.
The initial connector listing was limited to 100 children. The complete root
manifest was collected from the Drive UI before reading files. All 358 files
were fetched successfully, including retried transient failures.

The separate old-goat-additional-2026-11-13.json bundle adds 355 reviews:

| Manufacturer / line | Reviews |
| --- | ---: |
| Flavour Art | 124 |
| Capella | 116 |
| Flavorah | 87 |
| Flavor Express | 9 |
| DIY Flavour Shack | 8 |
| Capella Silver Line | 5 |
| Flavour Art Pazzo | 5 |
| The Flavor Apprentice | 1 |

One exact prose copy was omitted. Cream by Flavorah (FLV).docx actually contains
a Smoothie Base review identical to another Smoothie Base document. Its ID and
retained source are recorded in the duplicate manifest. Identity comes from the
review heading, not its filename.

Fruit Circles.docx and Funnel Cake.docx returned empty text. Both original DOCX
files were downloaded and their word/document.xml inspected: neither contained
any text nodes. They are recorded as exclusions rather than invented reviews.

All prose, original scores, testing details and source links remain attributed
to Old Goat. SHA-256 checksums cover unchanged readable text. Different wording,
versions and product lines are retained. Deduplication considers source ID or
exact prose for the same author and identity, allowing whitespace/Unicode
composition differences only.

There are now 413 packaged Old Goat reviews across two bundles. With 1,177
SessionDrummer records, there are 1,590 reference records, including the 33
previously flagged SessionDrummer source conflicts. These are reference reviews,
not community votes. Catalogue matching remains exact; unmatched and ambiguous
entries stay in the Old Goat import report. Production match counts require
checking after deployment and cannot be inferred from the test database.

Validation: 43 tests passed across review imports, purchase purposes, tasting
records and catalogue identity. Coverage includes unchanged text checksums,
complete batch accounting, preserved lines/versions, copy detection, retained
revisions, independent bundles and duplicate-free restarts.

Deploy with the usual tvw-update. Startup imports the new bundle once. Existing
reviews, tasting records, recipes and stash are not replaced.
