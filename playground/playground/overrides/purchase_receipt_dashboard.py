# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Purchase Receipt dashboard override - surface the "Make Stock Entry" transfer
=============================================================================

ERPNext's native *Create > Make Stock Entry* button on a submitted Purchase
Receipt builds a Material Transfer Stock Entry (in our setup: Gate Store ->
Main Store). `make_stock_entry` stamps the originating PR onto every produced
row via `Stock Entry Detail.reference_purchase_receipt` (a Link -> Purchase
Receipt), but the stock PR dashboard never declares that relationship, so the
resulting Stock Entry does not appear in the Purchase Receipt's Connections.

This override adds Stock Entry to the Connections tab. The link field lives on
the child table (Stock Entry Detail) and has a non-standard name, so it is
registered via `non_standard_fieldnames` rather than relying on the default
`<doctype>` fieldname guess (which would look for a header field `purchase_receipt`
that does not exist on Stock Entry).

Because the reference is already stored on existing Stock Entries, the link
shows up retroactively - no custom field, no change to the native button.

Wired via `override_doctype_dashboards` in hooks.py. Frappe passes the core
dashboard data in and expects the (mutated) data back.
"""

from frappe import _


def get_dashboard_data(data):
	data.setdefault("transactions", []).append(
		{"label": _("Stock"), "items": ["Stock Entry"]}
	)
	data.setdefault("non_standard_fieldnames", {})["Stock Entry"] = "reference_purchase_receipt"
	return data
