// ERPNext's own Sales Order list view computes the "Overdue" badge purely
// client-side from per_delivered + delivery_date (frappe.listview_settings
// get_indicator), entirely independent of the stored `status` field. That
// means it fires regardless of what status actually is, so our custom
// statuses ("Ready for Dispatch" / "Inspected", set server-side by
// sales_order_status.py) get correctly written to the DB but never show up
// in the list view - "Overdue" (or any other standard badge) just paints
// over them. Wrap get_indicator so ours are checked FIRST, falling back to
// ERPNext's own logic for every other status unchanged.
(function () {
	frappe.listview_settings["Sales Order"] = frappe.listview_settings["Sales Order"] || {};
	const original_get_indicator = frappe.listview_settings["Sales Order"].get_indicator;

	frappe.listview_settings["Sales Order"].get_indicator = function (doc) {
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
})();
