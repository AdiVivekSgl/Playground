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
