# Product research and ingredient eligibility — 11 November 2026

## Decisions from supplier evidence

| Product family | Decision | Primary evidence |
| --- | --- | --- |
| Clyrolinx It's E-liquid / It's Nic Salt | Finished liquids; exclude from flavour choices and future concentrate imports, including zero-nicotine ready liquids | [Mango ice](https://clyrolinx.co.za/shop/vape-juice/its-e-liquid/mango-ice-100ml-3mg-ml-its-e-liquid/) explicitly identifies ready-to-vape juice; [salt version](https://clyrolinx.co.za/shop/vape-juice/its-nic-salt-50mg-ml-5/mango-ice-30ml-50mg-ml-5-its-nic-salt/) is also ready-to-vape |
| Clyrolinx Cappuccino 10/30/100ml | Liquid concentrate; eligible after reviewed category restoration | Public WooCommerce Store API `https://clyrolinx.co.za/wp-json/wc/store/products?search=cappuccino` returned all three products with permalinks and category links under `flavour-concentrates-additives/clyrolinx/`. Unlike the ready liquids these are not under `vape-juice/`. Retrieved 11 Nov 2026. |
| Mr Hardwicks Breakfast Batter (HW) 30ml | Blended concentrate; restore as flavour | [Supplier product](https://blck.co.za/products/breakfast-batter-hardshots-30ml) and its Hardshots concentrate collection. HW identifies the brand here. |
| Bewolk SteamCraft AK-47 / AK-47R | Coils; exclude as hardware | [Manufacturer coil listing](https://bewolk.co.za/za/categories/coils), [supplier details](https://blck.co.za/products/steamcraft-ak-47-3-0mm) |
| BLCK Erythritol Crystals | Powder additive, not hardware; exclude from this liquid ingredient picker | [Supplier product](https://blck.co.za/products/erythritol-crystals) and [powder category](https://blck.co.za/collections/additive-powders) identify a solid sold by weight. The calculator's current liquid ingredient path does not describe the prepared solution/concentration. Do not invent a liquid density, concentration or use percentage. |
| Super Aroma Fruit Hard Candy / Scandinavian Fruits | Concentrates with unresolved supplier specifications; pending verification, not an automatically enabled mixing ingredient | BLCK describes [Fruit Hard Candy](https://blck.co.za/products/fruit-hard-candy-concentrate-sa) and [Scandinavian Fruits](https://blck.co.za/products/scandinavian-fruits-concentrate-sa) as oil-soluble/not water-soluble. Other listings conflict. [Manufacturer Scandinavian Fruits SDS](https://b2b.sobucky.com/product/attachment/1fe305502edc2b8eed6b981a6cec95be/en_US/super-aromas-msds-scandinavian-fruits.pdf) establishes product identity/food-flavour use, not resolution of the exact repackaged supplier specification. |
| Super Aroma Ice Candies | Liquid concentrate; eligible after category correction | [Supplier specification](https://blck.co.za/products/ice-candies-concentrate-sa) identifies a water-soluble PG concentrate. This is product classification, not an inhalation-safety certification. |
| Clyrolinx perfumes, bulk acids, LCLC sterilizing fluid/saline | Exclude fragrances/chemicals/raw solids from liquid flavour choices; retain records for review | [Supplier departments](https://clyrolinx.co.za/shop/) separate perfumes, other chemicals, nicotine, concentrates, bottles and finished juice. No inference from food/chemical grade to inhalation suitability is made. |

## Code changes

- Product eligibility is independent of a potentially wrong legacy `Flavor` label. Exclusions apply
  to catalogue discovery/manufacturers, builder search/options, stash quick-add, and builder save
  validation. Either the public row or its linked ingredient can block a public choice.
- Hardware vendor/model evidence and specific accessory terms cover reviewed coils, devices,
  cartridges, chargers and bottle openers. Normal flavour names, liquid additives and PG/VG
  variants retain existing handling.
- Existing recipe/history reads keep their original reference lookup behaviour. This patch does
  not delete records, change stock/costs, rewrite historical mixes, or alter Guided Mix policy.
- Clyrolinx import skips finished liquids using product lines, URLs and source categories; skips
  fragrances, unsupported powders/chemicals; and uses category tokens rather than `nic` anywhere
  in text. The old substring match classified **Unicorn** bottles as nicotine.
- Nine already nicotine-tagged bottles remain protected from automatic nicotine-flag removal.
  They are excluded from flavour choices by product identity even while their legacy flags are
  awaiting explicit correction. Existing nicotine protection and unfinished-mix guards remain.
- The backed-up cleanup tool can restore researched concentrates and align remaining hardware
  references after deployment and a fresh preview. Excluded/uncertain kinds receive an explicit
  blocker instead of a guessed ingredient category. Unfinished Acetyl Pyrazine remains blocked.

## Deployment

Deploy before generating another preview. Read-only picker exclusions take effect on restart;
no mass deletion, source-row deactivation, stock changes or automatic category rewriting occurs.
Existing unsupported ingredients cannot be newly saved through the recipe builder. Their records
remain readable so an owner can identify and correct the recipe, with history preserved.

## Validation

- 57 focused product-policy, catalogue, category, search and scraper checks: 55 passed,
  two existing skips. Tests deliberately mislabel finished liquids, hardware, powders and
  fragrances as Flavor, verify their exclusion from every affected picker and builder save,
  and verify unchanged stock, prices and default history/reference reads.
- 147 API/recipe-creation checks: 145 passed; two existing login redirects fail old 200-status
  expectations (`test_community_route_renders_in_required_discovery_states` and
  `test_private_visible_to_old_goat`). The latter was also reproduced on unchanged main;
  the former is the previously recorded baseline failure. This is not a green full-suite claim.
- No Guided Mix implementation/template files changed. Live browser retesting requires deployment.
