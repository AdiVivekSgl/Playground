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
fixtures = [
	{
		"doctype": "Custom Field",
		"filters": [
			["dt", "in", ["BOM Item", "BOM"]],
			["fieldname", "in", ["node", "explosion_level"]],
		],
	}
]

# Route the client-facing "Get Items for Material Request" / "Transfer
# Materials" button (a frappe.call to the core dotted path) through our
# wrapper, which drops Planned Qty = 0 rows before the core function runs.
# This hook is resolved fresh at call time, so it's immune to the app-import
# load-order fragility that the module-level monkey-patch in
# production_plan_overrides.py is subject to. The monkey-patch is still needed
# for the OTHER call site — download_raw_materials() calls
# get_items_for_material_requests() internally as a plain Python reference,
# which this hook does not intercept.
override_whitelisted_methods = {
	"erpnext.manufacturing.doctype.production_plan.production_plan.get_items_for_material_requests": "playground.production_plan_overrides.get_items_for_material_requests"
}
