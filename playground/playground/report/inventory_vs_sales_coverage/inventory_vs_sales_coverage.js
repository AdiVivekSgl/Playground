// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

frappe.query_reports["Inventory vs Sales Coverage"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "as_on_date",
			label: __("As On Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "days",
			label: __("Sales Window (days)"),
			fieldtype: "Int",
			default: 90,
		},
		{ fieldname: "warehouse", label: __("Warehouse"), fieldtype: "Link", options: "Warehouse" },
		{ fieldname: "item_group", label: __("Item Group"), fieldtype: "Link", options: "Item Group" },
		{
			fieldname: "reorder_days",
			label: __("Reorder Below (days cover)"),
			fieldtype: "Int",
			default: 30,
		},
		{
			fieldname: "excess_days",
			label: __("Excess Above (days cover)"),
			fieldtype: "Int",
			default: 180,
		},
	],

	// Colour the Status cell so stockouts / reorder / excess are scannable.
	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (column.fieldname === "status") {
			const colors = {
				Stockout: "#fdecea",
				Reorder: "#fff3e0",
				Excess: "#ede7f6",
				"No Recent Sales": "#eceff1",
				OK: "#e1f5ee",
			};
			const bg = colors[value];
			if (bg) {
				return `<div style="background-color:${bg};margin:-8px -12px;padding:8px 12px;font-weight:600;">${formatted}</div>`;
			}
		}
		return formatted;
	},
};
