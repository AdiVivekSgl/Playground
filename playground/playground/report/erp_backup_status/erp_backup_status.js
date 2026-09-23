// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

frappe.query_reports["ERP Backup Status"] = {
	filters: [
		{
			fieldname: "status", label: __("Status"), fieldtype: "Select",
			options: ["", "Queued", "Running", "Completed", "Failed", "Verified"].join("\n"),
		},
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date" },
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (data && data._failed) {
			// Highlight failed backups prominently (spec 12).
			value = `<span style="color:var(--red-600,#c0392b);font-weight:bold;">${value}</span>`;
		} else if (column.fieldname === "verification" && value === "Passed") {
			value = `<span style="color:var(--green-600,#1f9d55);">✓ ${value}</span>`;
		}
		return value;
	},
};
