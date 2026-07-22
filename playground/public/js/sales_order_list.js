// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

// Renders the manual "Sales Status" custom field (custom_sales_status) as a
// coloured indicator pill in the Sales Order list view.
//
// ERPNext ships its own frappe.listview_settings["Sales Order"] (status
// indicator, add_fields, onload, ...). This app's list JS loads AFTER erpnext's,
// so we MERGE into the existing object rather than reassigning it - reassigning
// would wipe ERPNext's list behaviour. A list-view `formatters` entry keys off the
// fieldname and returns HTML for that column's cell.

frappe.provide("frappe.listview_settings");

(function () {
	const settings = frappe.listview_settings["Sales Order"] || {};

	// Sales Status value -> Frappe indicator colour.
	const SALES_STATUS_COLORS = {
		"Inspection Awaited": "cyan",
		"DI Awaited": "blue",
		"Payment Awaited": "purple",
		"Customer Delay": "yellow",
		"Hold": "gray",
		"Approval Issue": "orange",
		"Urgent": "red",
	};

	// Make sure the value rides along in the list query so the pill renders even if
	// the column isn't width-visible.
	settings.add_fields = (settings.add_fields || []).concat(["custom_sales_status"]);

	settings.formatters = Object.assign({}, settings.formatters, {
		custom_sales_status(value) {
			if (!value) return "";
			const color = SALES_STATUS_COLORS[value] || "gray";
			const label = frappe.utils.escape_html(value);
			return `<span class="indicator-pill ${color} filterable ellipsis" data-filter="custom_sales_status,=,${label}">${label}</span>`;
		},
	});

	frappe.listview_settings["Sales Order"] = settings;
})();
