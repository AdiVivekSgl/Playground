// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

frappe.query_reports["Outstanding Expense Provisions"] = {
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
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -12),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "expense_account",
			label: __("Expense Account"),
			fieldtype: "Link",
			options: "Account",
			get_query: () => ({ filters: { is_group: 0, root_type: "Expense" } }),
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: "\nOpen\nReversed\nCancelled",
		},
		{
			fieldname: "open_only",
			label: __("Open Only"),
			fieldtype: "Check",
			default: 1,
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		// Highlight the still-accrued (Open) amount.
		if (column.fieldname === "open_amount" && data && flt(data.open_amount) > 0.01) {
			value = `<span style="color:var(--text-on-orange,#b45309);font-weight:600">${value}</span>`;
		}
		return value;
	},
};
