app_name = "playground"
app_title = "Playground"
app_publisher = "Your Name"
app_description = "Custom API scripts for ERPNext"
app_version = "0.0.1"
app_email = "you@example.com"
app_license = "MIT"

# Ships the "node" custom field on BOM Item with this app, so it travels
# with `bench migrate` / app installs rather than being a one-off manual
# customization that doesn't survive a fresh site.
doctype_js = {
	"Work Order": "public/js/work_order.js",
}

# List-view customizations. Purchase Order gets the "Close Purchase Orders" bulk
# action (see playground/public/js/purchase_order_list.js).
doctype_list_js = {
	"Purchase Order": "public/js/purchase_order_list.js",
}

# Purchase Invoice controller override: Price Adjustment Debit Note GRNI
# reclassification (see playground/playground/overrides/purchase_invoice.py).
# NOTE: only one app may own a doctype class - if another custom app already
# overrides Purchase Invoice, merge that logic instead of setting this.
override_doctype_class = {
	"Purchase Invoice": "playground.playground.overrides.purchase_invoice.CustomPurchaseInvoice",
	# TEMPORARY DIAGNOSTIC - logs the GL rows for Delivery Note DC-26-27-014 to
	# find the zero debit/credit row (see overrides/gl_entry_debug.py). REMOVE
	# this line + the file when done; no accounting logic is changed.
	"GL Entry": "playground.playground.overrides.gl_entry_debug.DiagnosticGLEntry",
}

fixtures = [
	{
		"doctype": "Custom Field",
		"filters": [
			["dt", "in", ["BOM Item", "BOM"]],
			["fieldname", "in", ["node", "explosion_level"]],
		],
	},
	# Material Status fields on Sales Order - shipped so they travel with
	# `bench migrate` rather than being one-off Customize Form changes.
	{
		"doctype": "Custom Field",
		"filters": [
			["dt", "=", "Sales Order"],
			[
				"fieldname",
				"in",
				[
					"custom_material_status",
					"custom_inspection_completed",
					"delivery_date_revision_count",
				],
			],
		],
	},
	# Non-COGS expense attribution: link a Journal Entry / Purchase Invoice to a
	# Sales Invoice (+ auto-fetched Customer) so expenses can be tagged against a
	# specific customer / invoice.
	{
		"doctype": "Custom Field",
		"filters": [
			["dt", "in", ["Journal Entry", "Purchase Invoice"]],
			["fieldname", "in", ["custom_linked_sales_invoice", "custom_expense_customer"]],
		],
	},
	# Audit trail for the Purchase Order bulk-close action.
	{
		"doctype": "Custom Field",
		"filters": [
			["dt", "=", "Purchase Order"],
			["fieldname", "=", "custom_closing_reason"],
		],
	},
	# Price Adjustment Debit Note GRNI reclassification: flag on Purchase Invoice
	# + the target account on Company.
	{
		"doctype": "Custom Field",
		"filters": [
			["name", "in", [
				"Purchase Invoice-custom_is_price_adjustment_debit_note",
				"Company-custom_purchase_rate_adjustment_account",
			]],
		],
	},
]

# First doc_events / scheduler_events in this app - drives Sales Order Material
# Status (see playground/playground/sales_order_hooks.py).
doc_events = {
	"Sales Order": {
		"validate": "playground.playground.sales_order_hooks.on_sales_order_validate",
		"on_update": "playground.playground.sales_order_hooks.on_sales_order_update",
	},
	"Stock Reservation Entry": {
		"on_submit": "playground.playground.sales_order_hooks.recompute_from_sre",
		"on_cancel": "playground.playground.sales_order_hooks.recompute_from_sre",
	},
	"Weekly Planning Snapshot": {
		"on_submit": "playground.playground.sales_order_hooks.recompute_from_snapshot",
	},
}

scheduler_events = {
	"hourly": [
		"playground.playground.sales_order_hooks.recompute_all_open_so_material_status",
	],
}
