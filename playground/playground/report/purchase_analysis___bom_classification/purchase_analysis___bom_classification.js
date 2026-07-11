// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

frappe.query_reports["Purchase Analysis - BOM Classification"] = {
	filters: [
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date" },
		{ fieldname: "supplier", label: __("Supplier"), fieldtype: "Link", options: "Supplier" },
		{ fieldname: "item_code", label: __("Item Code"), fieldtype: "Link", options: "Item" },
		{ fieldname: "item_group", label: __("Item Group"), fieldtype: "Link", options: "Item Group" },
		{
			fieldname: "category",
			label: __("Category"),
			fieldtype: "Select",
			options: ["", "Direct", "Indirect", "Capital"].join("\n"),
		},
	],

	// Colour the Category cell so Direct / Indirect / Capital are scannable.
	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (column.fieldname === "category") {
			const colors = { Direct: "#e1f5ee", Indirect: "#fff3e0", Capital: "#ede7f6" };
			const bg = colors[value];
			if (bg) {
				return `<div style="background-color:${bg};margin:-8px -12px;padding:8px 12px;font-weight:600;">${formatted}</div>`;
			}
		}
		return formatted;
	},
};
