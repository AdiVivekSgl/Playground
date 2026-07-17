// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

frappe.query_reports["Price Adjustment GRNI Reclassification"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			reqd: 1,
			default: frappe.defaults.get_user_default("Company"),
		},
		{ fieldname: "supplier", label: __("Supplier"), fieldtype: "Link", options: "Supplier" },
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date" },
	],

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (column.fieldname === "status" && data && data.status === "Residual") {
			return `<span style="color:#b71c1c;font-weight:600;">${formatted}</span>`;
		}
		return formatted;
	},
};
