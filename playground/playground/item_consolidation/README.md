# Item Consolidation

Retire duplicate items **without** ERPNext's "Rename → Merge with existing".

## Why not merge

A merge rewrites `item_code` on every historical Stock Ledger Entry of the old
item. The two ledgers become one, so valuation must be reposted from the earliest
entry of either item - straight into periods locked by Period Closing Vouchers,
Closing Stock Balance, Accounting Periods or frozen dates. Those errors are ERPNext
correctly refusing to rewrite closed books; working around them leaves stock and GL
out of agreement.

## What this does instead (per pair, posted now)

1. **Pre-checks** (`checks.py`) - blockers: UOM / stock-flag mismatch, templates,
   negative stock, batch/serial stock, running reposts, stock reservations, period
   locks, and (when disabling) open documents still using the old item.
2. **Repack** (`actions.move_stock`) - one Repack Stock Entry per warehouse: old item
   out, same qty of the kept item in at the same value. No backdated repost.
3. **Masters** - Item Prices, reorder levels, supplier/customer codes, Product
   Bundles, Pricing Rules (only what the kept item lacks), and Item Alternatives
   moved to the kept item - whether the old item is the item or the alternative.
4. **BOMs** - reuses `bom_component_replace._replace_in_bom` on active/draft BOMs,
   then optionally queues "Update Cost" for all BOMs.
5. **Disable** the old item with a "Replaced by" comment. History stays on it.

## Use

Item Consolidation → add pairs → **Dry Run** → fix blockers → Submit. The job runs
on the `long` queue, each pair in its own savepoint and committed separately; failed
pairs show their error and can be re-run with **Retry Failed**. Submitted documents
cannot be cancelled - undo a pair by cancelling its Repack entries.

## Tests

    bench --site <site> run-tests --module playground.playground.item_consolidation.tests.test_checks
    bench --site <site> run-tests --module playground.playground.item_consolidation.tests.test_api
    bench --site <site> run-tests --module playground.playground.item_consolidation.tests.test_actions
