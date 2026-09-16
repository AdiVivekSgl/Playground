// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

frappe.query_reports["Purchase Orders Pending Delivery"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company", default: frappe.defaults.get_user_default("Company") },
		{ fieldname: "supplier", label: __("Supplier"), fieldtype: "Link", options: "Supplier" },
		{ fieldname: "item_group", label: __("Item Group"), fieldtype: "Link", options: "Item Group" },
		{
			fieldname: "base_date",
			label: __("Base Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{ fieldname: "near_days", label: __("Near Bucket (days)"), fieldtype: "Int", default: 10 },
		{ fieldname: "mid_days", label: __("Mid Bucket (days)"), fieldtype: "Int", default: 30 },
	],

	// Flag lines whose Scheduled Date is already past (overdue, still undelivered).
	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (column.fieldname === "schedule_date" && value) {
			const today = frappe.datetime.get_today();
			if (value < today) {
				return `<span style="color:#c0392b;font-weight:600;">${formatted}</span>`;
			}
		}
		return formatted;
	},
};
