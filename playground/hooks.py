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
