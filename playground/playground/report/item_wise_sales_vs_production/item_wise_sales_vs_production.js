// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

frappe.query_reports["Item-wise Sales vs Production"] = {
	filters: [
		{
			// Predefined item-universe cutoff: rows = items sold on/after this date.
			// Editable, but defaults to the start of FY 2026-27 (1 Apr 2026).
			fieldname: "sold_since",
			label: __("Sold Since (Item List)"),
			fieldtype: "Date",
			reqd: 1,
			default: "2026-04-01",
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			reqd: 1,
			default: "2026-04-01",
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			reqd: 1,
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "warehouse",
			label: __("Warehouse (Valuation Basis)"),
			fieldtype: "Link",
			options: "Warehouse",
		},
		{
			fieldname: "item_group",
			label: __("Item Group"),
			fieldtype: "Link",
			options: "Item Group",
		},
		{
			fieldname: "item_code",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
		},
	],

	// Blank out zero numeric cells so the grid reads cleanly, and bold the Grand
	// Total row (same conventions as the Sales Pivot Matrix report).
	formatter(value, row, column, data, default_formatter) {
		const is_total = data && data.is_total;

		const numeric = [
			"sale_qty",
			"sale_value",
			"cogs",
			"produced_qty",
			"production_value",
			"projected_sale_value",
		];
		if (numeric.includes(column.fieldname) && !flt(value)) {
			return "";
		}

		let formatted = default_formatter(value, row, column, data);

		if (is_total) {
			return `<span style="font-weight:700;">${formatted}</span>`;
		}
		return formatted;
	},

	onload(report) {
		report.page.add_inner_button(
			__("Export to Excel"),
			() => frappe.query_report.export_report(),
			__("Actions")
		);
	},
};
