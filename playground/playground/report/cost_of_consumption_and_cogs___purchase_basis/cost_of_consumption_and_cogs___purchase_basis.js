// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

frappe.query_reports["Cost of Consumption and COGS - Purchase Basis"] = {
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
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{ fieldname: "warehouse", label: __("Warehouse"), fieldtype: "Link", options: "Warehouse" },
		{ fieldname: "item_group", label: __("Item Group"), fieldtype: "Link", options: "Item Group" },
	],

	// Colour the Bucket cell (RM vs WIP+FG) and bold the subtotal rows.
	formatter(value, row, column, data, default_formatter) {
		let formatted = default_formatter(value, row, column, data);

		if (column.fieldname === "bucket" && value) {
			const colors = { RM: "#e1f5ee", "WIP+FG": "#fff3e0" };
			const bg = colors[value];
			if (bg) {
				formatted = `<div style="background-color:${bg};margin:-8px -12px;padding:8px 12px;font-weight:600;">${formatted}</div>`;
			}
		}

		if (data && data.is_subtotal) {
			formatted = `<span style="font-weight:700;">${formatted}</span>`;
		}

		return formatted;
	},
};
