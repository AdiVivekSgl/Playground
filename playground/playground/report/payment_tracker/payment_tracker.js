// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

frappe.query_reports["Payment Tracker"] = {
	filters: [
		{ fieldname: "supplier", label: __("Supplier"), fieldtype: "Link", options: "Supplier" },
		{
			fieldname: "payment_terms_template",
			label: __("Payment Terms Template"),
			fieldtype: "Link",
			options: "Payment Terms Template",
		},
		{
			fieldname: "due_in_days",
			label: __("Due In Days"),
			fieldtype: "Select",
			options: ["", "Less than 90", "More than 90"].join("\n"),
		},
		{ fieldname: "no_of_due_days", label: __("No. of Due Days (<=)"), fieldtype: "Int" },
		{
			fieldname: "show_no_due",
			label: __("Show Transactions with No Due"),
			fieldtype: "Check",
			default: 0,
			// Off: only invoices with an outstanding balance. On: also include
			// fully settled invoices (outstanding = 0).
		},
	],

	// Bold the appended Total row.
	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (data && data.name === "Total") {
			return `<span style="font-weight:700;">${formatted}</span>`;
		}
		return formatted;
	},
};
