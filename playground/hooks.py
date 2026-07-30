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
	# Adds the "Replace Component Item" action to ERPNext's native BOM Update
	# Tool - swap one component item for another across BOMs, raw materials and
	# sub-assemblies alike (see playground/playground/bom_component_replace.py).
	"BOM Update Tool": "public/js/bom_update_tool.js",
}

# List-view customizations. Purchase Order gets the "Close Purchase Orders" bulk
# action (see playground/public/js/purchase_order_list.js).
doctype_list_js = {
	"Purchase Order": "public/js/purchase_order_list.js",
	# Renders the manual "Sales Status" custom field as coloured indicator pills.
	"Sales Order": "public/js/sales_order_list.js",
}

# Purchase Invoice controller override: Price Adjustment Debit Note GRNI
# reclassification (see playground/playground/overrides/purchase_invoice.py).
# NOTE: only one app may own a doctype class - if another custom app already
# overrides Purchase Invoice, merge that logic instead of setting this.
override_doctype_class = {
	"Purchase Invoice": "playground.playground.overrides.purchase_invoice.CustomPurchaseInvoice",
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
					"custom_needs_attention",
					"custom_sales_status",
					"custom_inspection_completed",
					"delivery_date_revision_count",
				],
			],
		],
	},
	# Non-COGS expense attribution: link a Journal Entry / Purchase Invoice to
	# Sales Invoices (and, on Journal Entry, Purchase Invoices) so expenses can be
	# tagged against specific invoices. Customer/Supplier are derived from the
	# linked invoices at report time, not stored on the expense document.
	{
		"doctype": "Custom Field",
		"filters": [
			["dt", "in", ["Journal Entry", "Purchase Invoice"]],
			[
				"fieldname",
				"in",
				[
					"custom_linked_sales_invoice",
					"custom_linked_purchase_invoice",
				],
			],
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
	# Provision Management: a Journal Entry linking an open Expense Provision
	# (custom_provision_against) settles it from the JE's own posting. Purchase
	# Invoice settlement is handled in the CustomPurchaseInvoice override above.
	"Journal Entry": {
		"validate": "playground.playground.provision_management.on_journal_entry_validate",
		"on_submit": "playground.playground.provision_management.on_journal_entry_submit",
		"on_cancel": "playground.playground.provision_management.on_journal_entry_cancel",
	},
}

scheduler_events = {
	"hourly": [
		"playground.playground.sales_order_hooks.recompute_all_open_so_material_status",
	],
	# Auto-Reverse mode: reverse any unsettled balance of last month's provisions
	# at the start of the new month (classic accrual reversal).
	"monthly": [
		"playground.playground.provision_management.auto_reverse_due_provisions",
	],
	# Refresh BOM valuations on the 1st of every month: fire an "Update Cost"
	# across all BOMs (same as the BOM Update Tool button). "0 2 1 * *" =
	# 02:00 on the 1st - off-peak so the level-wise recost doesn't fight the
	# midnight scheduler traffic.
	"cron": {
		"0 2 1 * *": [
			"playground.playground.bom_cost.update_cost_for_all_boms",
		],
	},
}

# Provision Management: create the "Provision Against" link on Purchase Invoice
# and Journal Entry so it travels with the app (idempotent - runs every migrate).
after_migrate = [
	"playground.playground.provision_management.create_provision_custom_fields",
]
