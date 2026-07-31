# Playground

Custom API scripts for ERPNext.

## Active API

- `playground.api.create_leave_request`: whitelisted Frappe endpoint for creating leave requests from the current session user.

## Open Order View (submitted Sales Orders)

Turns a **submitted** Sales Order into an "open orders" board. Instead of the
original ordered figures, it surfaces what is still owed to the customer so
Sales, Planning, Production and Dispatch can read the remaining demand at a
glance:

- **Open Order Summary** — Order Value, Delivered Value, Pending Value and a
  `62% Fulfilled` completion bar.
- **Open Order View table** — per item: delivered qty, **pending qty**,
  **pending amount**, and a colour status (green = fully delivered, amber =
  partial, blue = not started, grey = cancelled/empty).

### Design guarantees

This is a **presentation-layer enhancement only**. It:

1. Never overwrites standard ERP fields — the standard ERPNext Items grid is left
   completely intact directly below the panel, as the system of record. The
   original qty/amount stay one click away.
2. Never alters submitted document data — nothing is written to the database.
3. Adds **no custom fields and no schema migration** — every value is computed
   transiently in the browser from data already on the in-memory document.
4. Runs **only when `docstatus == 1`** — draft Sales Orders behave exactly like
   stock ERPNext.
5. Disables cleanly if the app is removed (client asset + settings vanish with it).
6. Costs **no extra database queries and no server round-trips** — pending qty is
   `qty - delivered_qty`, computed client-side; settings arrive via the bootinfo.

### Architecture

| Piece | File | Role |
| --- | --- | --- |
| Formulas (source of truth) | `playground/playground/open_order_view.py` | Pure functions `compute_item_pending` / `compute_order_summary`; unit-tested; reusable server-side. Also the `extend_bootinfo` hook that ships settings to the client. |
| Runtime rendering | `playground/public/js/sales_order.js` | Renders the summary + table above the standard Items grid on refresh. Mirrors the Python formulas exactly. |
| Configuration | `playground/playground/doctype/playground_settings/` | `Playground Settings` single with the toggles below. |
| List badge (stretch) | `playground/public/js/sales_order_list.js` | Opt-in `% Delivered` fulfilment pill from the standard `per_delivered` field. |
| Wiring | `playground/hooks.py` | `doctype_js["Sales Order"]` + `extend_bootinfo`. |

The Python module is the single source of truth for the maths; the JS mirrors it
so the arithmetic can run client-side with zero round-trips while staying
unit-tested. Keep the two in sync if the formulas change.

### Formulas

```
pending_qty       = qty - delivered_qty        # clamped to 0 when configured
pending_amount    = pending_qty * rate
delivered_value   = Sum(delivered_qty * rate)
pending_value     = Sum(pending_qty * rate)
order_value       = Sum(qty * rate)            # ex-tax, at item rate
completion_percent = delivered_value / order_value * 100
```

### Settings — `Playground Settings` (single)

| Toggle | Default | Effect |
| --- | --- | --- |
| Enable Pending View | ON | Master switch for the Open Order View. |
| Highlight Completed Rows | ON | Colour item rows by fulfilment status. |
| Show Original Qty Column | OFF | Also show ordered qty next to pending qty. |
| Show Original Amount Column | OFF | Also show ordered amount next to pending amount. |
| Clamp Negative Pending to Zero | ON | Show over-delivery / returns as 0 pending instead of a negative figure. |

Settings ride along in the desk bootinfo, so a change takes effect on the next
page load (the single busts the cache on save).

### Edge cases handled

Fully delivered, partial, zero-delivery, multiple Delivery Notes (uses the
accumulated `delivered_qty`), returns / over-delivery (clamped), rate changes
after amendment, and cancelled/zero-qty rows. Because the panel only reads
`qty`, `delivered_qty` and `rate` and never writes anything, Manufacturing, MRP,
reservations, stock, invoicing, taxes, pricing, workflow, permissions, printing,
the REST API and reports are all untouched.

### Tests

`playground/playground/doctype/playground_settings/test_open_order_view.py`
covers fully delivered, partial, zero, multiple deliveries, returns and
amendments. Run under a bench:

```bash
bench --site <site> run-tests --app playground \
  --module playground.playground.doctype.playground_settings.test_open_order_view
```

The tests stub `frappe` when it isn't importable, so they also run standalone
with `python -m unittest` from the app root.

> **Screenshots / GIF:** capture on a live ERPNext v15 site (open a submitted
> Sales Order with partial deliveries) — they can't be generated from this repo,
> which has no running instance.
