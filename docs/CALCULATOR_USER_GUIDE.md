# The Vape Workshop Calculator — User Guide

This is a practical operator guide for Wynand, Riku and anyone helping with the calculator.

---

## 1. Workshop vs Lab

### Workshop

Use the Workshop for normal recipe work:

- browse recipes;
- create or edit recipes;
- choose a recipe version;
- start Guided Mix;
- mix a normal personal bottle/batch.

### Lab

Use the Lab for business/production work:

- ingredient/reference data;
- pricing and costing;
- scrapers;
- Production Jobs;
- job inventory;
- production pay;
- job profitability.

A normal Workshop mix and a Production Job mix are intentionally different.

---

## 2. Recipes

Open:

`/recipes`

The Recipe Library contains the canonical recipes used throughout the system.

From here you can:

- search recipes;
- open a recipe;
- create a recipe;
- start a normal mix;
- work with the current recipe/version.

Recipe versions matter. Production Jobs lock an exact recipe version so an order cannot silently change if the recipe is edited later.

---

## 3. Creating a recipe

Open:

`/recipes/new`

Enter the recipe details and flavours, then save it.

The recipe is stored in the database along with its version and ingredient rows.

Later edits create/use recipe-version history rather than silently rewriting an active Production Job.

---

## 4. Guided Mix

Guided Mix is the normal production/mixing workflow.

When a mix is started from a normal recipe page, it is a **normal/personal mix**.

That mix:

- belongs to the signed-in user;
- does not affect any Production Job;
- does not consume a Production Job's dedicated inventory;
- does not increase an order's completed bottle count;
- does not create Production Job pay.

This isolation is intentional.

---

## 5. Lab Production Jobs

Open:

`/lab/production-jobs`

Production Jobs are named manufacturing projects, for example:

- Trinidad
- Poison Rally
- event stock
- customer orders

A job contains one or more recipe requirements.

For each recipe, the job stores:

- exact recipe;
- exact recipe version;
- bottle size;
- required bottle count;
- target total ml;
- completed production;
- in-progress production;
- remaining production.

---

## 6. Mixing for a Production Job

This is the most important Production Job rule:

**A mix only belongs to a Production Job when it is launched from that job using "Mix for this job".**

Example:

1. Open **Trinidad**.
2. Open the recipe requirement for **Yogi the Pink**.
3. Click **Mix for this job**.
4. Choose the number of bottles/batch size.
5. Start Guided Mix.
6. The Guided Mix screen shows the Production Job banner.
7. Complete the mix.
8. The completed batch is posted back to Trinidad.

If you instead open Yogi from the normal Recipe Library and mix a bottle for yourself, Trinidad is not affected.

---

## 7. Signed-in operator

The logged-in account is the production operator.

There is no operator dropdown.

If Wynand is signed in, the batch is recorded as Wynand.

If Riku is signed in, the batch is recorded as Riku.

This is also what allows the Lab to track production earnings correctly.

---

## 8. Cancelling a job-linked batch

A job-linked Guided Mix can be cancelled.

A cancelled batch:

- remains in the history/audit trail;
- does not add completed bottles;
- does not post ingredient usage;
- does not generate production pay.

Do not delete or manually fake completed production to correct a cancelled batch.

---

## 9. Ingredients for a Production Job

Each Production Job has its own ingredient pool.

This is separate from Wynand's personal stash.

The Ingredients page tracks:

- **Required (ml)** — total needed for all recipes in the job;
- **Purchased (ml)** — what was bought specifically for the job;
- **Used (ml)** — what completed job-linked batches consumed;
- **Remaining (ml)** — purchased minus used;
- **Still to buy (ml)** — required minus purchased, never below zero.

Large amounts may also be shown in litres for readability, but the canonical calculation unit is ml.

Example:

- 8,420 ml = 8.42 L

For a job such as Trinidad, only stock bought for Trinidad should be recorded in Trinidad's job inventory.

---

## 10. Recording job purchases

When ingredients are bought specifically for a Production Job, record them in that job.

Purchase records can include:

- quantity;
- pack count;
- pack size;
- pack price;
- total paid;
- source;
- purchase date;
- notes.

This lets the Lab separate:

- quantity required;
- cash spent buying stock;
- ingredient value actually consumed;
- leftover job stock.

---

## 11. Production pay

Completed job-linked batches can generate production credit.

Current configured starter rates include:

- 120 ml finished bottle — R5
- 30 ml finished bottle — R3

Helper labour (for example Riku) is tracked as money owed.

Owner labour (Wynand/Old Goat) can be tracked separately without creating a debt to himself.

Open:

`/lab/production-jobs/earnings`

to view authorised production earnings.

Payments are recorded as transactions so partial payments remain auditable.

---

## 12. Costs and profit

The Production Job Costs view can combine:

- revenue;
- ingredients consumed;
- bottles;
- labels;
- helper production pay;
- referral/finder commission;
- shipping/packing;
- other expenses.

The resulting figure is the job's calculated Old Goat profit.

The Lab also keeps **cash inventory spend** separate from **consumed ingredient cost**.

Example:

If a job needs 8.4 L VG but Wynand buys two 5 L packs:

- cash spend is the full 10 L purchase;
- consumed cost is based on the 8.4 L actually used;
- the remaining 1.6 L remains job stock.

---

## 13. Costing and scraped prices

The Lab uses current/refreshed supplier records where configured.

Important canonical BLCK roles include:

- VG
- PG
- DTL/freebase nicotine
- salt nicotine

Those roles are tied to specific supplier product variants so a scraper refresh can update pack price and R/ml without silently changing to a different pack size.

If a configured supplier variant disappears or becomes unavailable, the Lab should flag the problem rather than silently substituting another size.

---

## 14. Simple Trinidad workflow

A practical order workflow is:

1. Create/open **Trinidad** in Production Jobs.
2. Add each required recipe and bottle quantity.
3. Check the Ingredients tab.
4. Buy the stock specifically required for Trinidad.
5. Record those purchases against Trinidad.
6. Wynand or Riku signs in under their own account.
7. Open Trinidad.
8. Click **Mix for this job** beside the required recipe.
9. Complete the Guided Mix.
10. Return to Trinidad and confirm production and ingredient usage were updated.
11. Repeat until all recipe requirements are complete.
12. Use Production Pay to see helper earnings.
13. Use Costs to see job profitability.

---

## 15. What not to do

Do not:

- count a normal personal mix as order production;
- manually swap the operator on a batch;
- use a newer recipe version halfway through a locked order without deliberately updating the job;
- put Production Job purchases into personal stash and expect the job tracker to know about them;
- commit the live SQLite database to GitHub.

