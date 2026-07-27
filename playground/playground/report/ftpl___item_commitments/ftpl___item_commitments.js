// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

frappe.query_reports["FTPL - Item Commitments"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			reqd: 1,
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "item_code",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
			reqd: 1,
		},
		{
			fieldname: "warehouse",
			label: __("Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
			// Optional. Blank = every warehouse; a parent warehouse includes its
			// children (resolved server-side via get_child_warehouses).
			get_query() {
				const company = frappe.query_report.get_filter_value("company");
				return { filters: company ? { company } : {} };
			},
		},
		{
			fieldname: "include_draft",
			label: __("Include Draft Documents"),
			fieldtype: "Check",
			default: 0,
			// Off: submitted (docstatus 1) only. On: also include Draft documents.
		},
		{
			fieldname: "include_closed",
			label: __("Include Closed Documents"),
			fieldtype: "Check",
			default: 0,
			// Off: hides Completed / Closed / Stopped / Delivered documents.
		},
		{
			fieldname: "include_production_plans",
			label: __("Include Production Plans"),
			fieldtype: "Check",
			default: 0,
			// Off by default - Production Plan demand is speculative planning.
		},
		{
			fieldname: "show_summary_only",
			label: __("Show Summary Only"),
			fieldtype: "Check",
			default: 0,
			// On: render only Section 1 (Inventory Position + reconciliation +
			// warnings), hiding the per-document Commitment Explorer grid.
		},
	],

	// Colour every row by Direction so the flow of stock is readable at a glance:
	//   Incoming     -> green   (supply arriving)
	//   Outgoing     -> red     (demand / consumption)
	//   Reservation  -> orange  (firm holds)
	//   Planning     -> blue    (speculative future demand)
	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		const colours = {
			Incoming: "#1b8a2f",
			Outgoing: "#b71c1c",
			Reservation: "#e08600",
			Planning: "#1565c0",
		};
		const colour = data && colours[data.direction];
		if (!colour) return formatted;

		// Tint the Direction cell strongly; give the rest of the row a lighter
		// version of the same hue so the whole line reads as one commitment.
		if (column.fieldname === "direction") {
			return `<span style="color:${colour};font-weight:600;">${formatted}</span>`;
		}
		if (column.fieldname === "running_available" && (value || value === 0)) {
			const runColour = value < 0 ? "#b71c1c" : "#1b8a2f";
			return `<span style="color:${runColour};font-weight:600;">${formatted}</span>`;
		}
		return `<span style="color:${colour};">${formatted}</span>`;
	},
};
