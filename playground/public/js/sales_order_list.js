// ERPNext's own Sales Order list view computes the "Overdue" badge purely
// client-side from per_delivered + delivery_date (frappe.listview_settings
// get_indicator), entirely independent of the stored `status` field - so it
// fires regardless of what status actually is, and our custom statuses
// ("Ready for Dispatch" / "Inspected", set server-side by
// sales_order_status.py) never show up even though they're correctly
// persisted.
//
// A one-shot wrap-at-load-time patch isn't reliable here: this site has other
// installed apps (besides erpnext) that may ALSO touch Sales Order's list
// view settings, and ERPNext's own script does a FULL reassignment of
// frappe.listview_settings['Sales Order'] (not a merge) - whichever script
// runs last simply discards anyone else's captured closure. Loaded via
// app_include_js (a hook that genuinely accumulates across every installed
// app - unlike doctype_list_js, which is a per-doctype scalar another app's
// hook registration could silently collide with / override), and the patch
// re-applies itself every time the Sales Order list route is entered, so it
// always has the last word regardless of what any other app's script did.
(function () {
	function patch_sales_order_indicator() {
		const settings = frappe.listview_settings["Sales Order"];
		if (!settings) return;
		// Already wrapped and nobody's replaced it since - nothing to do. The
		// marker lives on the function itself (not a flag on `settings`), so
		// this correctly detects a genuine external override even after our
		// own patch has been applied and re-applied many times across visits.
		if (settings.get_indicator && settings.get_indicator.__is_playground_patch) return;

		const original_get_indicator = settings.get_indicator;
		const patched = function (doc) {
			if (doc.status === "Inspected") {
				return [__("Inspected"), "blue", "status,=,Inspected"];
			}
			if (doc.status === "Ready for Dispatch") {
				return [__("Ready for Dispatch"), "green", "status,=,Ready for Dispatch"];
			}
			if (original_get_indicator) {
				return original_get_indicator(doc);
			}
		};
		patched.__is_playground_patch = true;
		settings.get_indicator = patched;
	}

	patch_sales_order_indicator();

	// Re-check on every visit to the Sales Order list, in case another app's
	// script (or ERPNext's own) reassigned frappe.listview_settings['Sales
	// Order'] wholesale since we last patched it.
	frappe.router.on("change", () => {
		const route = frappe.get_route();
		if (route[0] === "List" && route[1] === "Sales Order") {
			patch_sales_order_indicator();
		}
	});
})();
