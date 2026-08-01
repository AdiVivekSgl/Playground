// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

// Sales Order list-view rendering for the Playground status fields:
//   - custom_sales_status    : manual flag, shown as a coloured indicator pill.
//   - custom_material_status : computed fulfillment text, shown as a coloured pill
//     that gains a bold red border when custom_needs_attention is set (the
//     decoupled "Needs Attention" flag rides on top of ANY status text).
//
// ERPNext ships its own frappe.listview_settings["Sales Order"] (status
// indicator, add_fields, onload, ...). This app's list JS loads AFTER erpnext's,
// so we MERGE into the existing object rather than reassigning it - reassigning
// would wipe ERPNext's list behaviour. A list-view `formatters` entry keys off the
// fieldname and returns HTML for that column's cell; it receives (value, df, doc).

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

	// Material Status value -> background colour (matches the FGSRM report's
	// material_status formatter so the two surfaces read the same).
	const MATERIAL_STATUS_COLORS = {
		Reserved: "#e1f5ee",
		Available: "#e8f5e9",
		"Possible to Push": "#fff3e0",
		"Needs Attention": "#fde2e7",
		Reprioritized: "#ede7f6",
		"Planning Pending": "#eceff1",
	};

	// Make sure the values ride along in the list query so the pills render, and so
	// the material-status formatter can read the needs-attention flag.
	settings.add_fields = (settings.add_fields || []).concat([
		"custom_sales_status",
		"custom_material_status",
		"custom_needs_attention",
	]);

	settings.formatters = Object.assign({}, settings.formatters, {
		custom_sales_status(value) {
			if (!value) return "";
			const color = SALES_STATUS_COLORS[value] || "gray";
			const label = frappe.utils.escape_html(value);
			return `<span class="indicator-pill ${color} filterable ellipsis" data-filter="custom_sales_status,=,${label}">${label}</span>`;
		},
		custom_material_status(value, df, doc) {
			const attention = doc && cint(doc.custom_needs_attention);
			if (!value && !attention) return "";
			const bg = MATERIAL_STATUS_COLORS[value] || "";
			const label = frappe.utils.escape_html(value || "");
			const style =
				(bg ? `background-color:${bg};` : "") +
				(attention ? "border:2px solid #c62828;" : "") +
				"padding:2px 8px;border-radius:10px;font-weight:600;display:inline-block;";
			return `<span style="${style}">${label}</span>`;
		},
	});

	frappe.listview_settings["Sales Order"] = settings;
})();
