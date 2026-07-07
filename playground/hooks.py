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
	}
]

# Layers "Ready for Dispatch" / "Inspected" on top of Sales Order's own status
# after core's own set_status() has run - see sales_order_status.py.
doc_events = {
	"Sales Order": {
		"on_update": "playground.playground.sales_order_status.set_custom_status",
	}
}
