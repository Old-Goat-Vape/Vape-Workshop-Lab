# Sales/Consignment Smoke Tests

## 1) Schema startup
- Start app once.
- Confirm no migration errors in console and pages load.

## 2) Client create
- Create a new client in `Sales Control -> Clients`.
- Expect client appears with zero owed/paid and zero stock held.

## 3) Decant event -> finished goods
- Create a decant event in `Sales Control -> Finished Goods` using an existing `production_batch_id`.
- Expect `finished_goods_stock.quantity_on_hand` increases and `production_batches.remaining_volume_ml` decreases.

## 4) Delivery create (consignment)
- Create a consignment delivery with at least one finished goods line.
- Expect on-hand stock decreases and at-client stock increases.
- Expect delivery appears in Deliveries list with status `open` and due based on sold value (initially zero until reconciliation).

## 5) Reconciliation
- Open delivery and submit sold/returned/damaged values.
- Expect validation rejects totals greater than qty sent.
- Expect returned qty goes back to on-hand stock, damaged does not.
- Expect remaining with client updates and status moves to partially/reconciled states.

## 6) Payment + allocations
- Record a payment and allocate to one or more deliveries.
- Expect payment listed, allocations recorded, and delivery due decreases.
- Expect delivery status moves to partially_paid/paid/closed when appropriate.

## 7) Exports/PDF
- Download:
  - outstanding CSV
  - stock-at-client CSV
  - top-selling CSV
- Open:
  - client statement PDF
  - delivery note PDF

## 8) Audit/movement log sanity
- Verify new actions insert audit entries and stock movement rows for:
  - decant
  - delivery out
  - returns/sold/damaged reconciliation
  - payment recorded

