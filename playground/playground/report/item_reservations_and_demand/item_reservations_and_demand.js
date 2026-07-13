// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

frappe.query_reports["Item Reservations and Demand"] = {
	filters: [
		{
			fieldname: "item_code",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
			reqd: 1,
		},
	],

	// Colour Reservation Type so committed (Reserved) vs outstanding (Demand)
	// rows are distinguishable at a glance.
	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (column.fieldname === "reservation_type") {
			if (value === "Reserved") return `<span style="color:#b71c1c;font-weight:600;">${formatted}</span>`;
			if (value === "Demand") return `<span style="color:#1565c0;font-weight:600;">${formatted}</span>`;
		}
		return formatted;
	},
};
