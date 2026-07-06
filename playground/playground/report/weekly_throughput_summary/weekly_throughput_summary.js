// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

// Most recent Friday on/before today, as a "YYYY-MM-DD" string - the default
// window start, so the report opens showing "since last Friday" by default.
function wts_last_friday() {
	const d = frappe.datetime.str_to_obj(frappe.datetime.get_today());
	const days_since_friday = (d.getDay() + 2) % 7; // getDay(): Sun=0 ... Fri=5
	d.setDate(d.getDate() - days_since_friday);
	return frappe.datetime.obj_to_str(d);
}

frappe.query_reports["Weekly Throughput Summary"] = {
	filters: [
		{
			fieldname: "section",
			label: __("Section"),
			fieldtype: "Select",
			options: ["Sales Orders Booked", "SO Lines Dispatched"].join("\n"),
			default: "Sales Orders Booked",
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: wts_last_friday(),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "item_code", label: __("FG Item"), fieldtype: "Link", options: "Item" },
	],
};
