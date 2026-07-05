// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

frappe.query_reports["JIT Production Planning Report"] = {
	filters: [
		// Same filter set as the FG Stock Reservation Manager - FG demand
		// (Order Qty / Available) is pulled straight from that report using
		// these same filters, so scoping here scopes the source data too.
		{ fieldname: "item_code", label: __("FG Item"), fieldtype: "Link", options: "Item" },
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "sales_order", label: __("Sales Order"), fieldtype: "Link", options: "Sales Order" },
		{
			fieldname: "date_basis",
			label: __("Date Basis"),
			fieldtype: "Select",
			options: ["Document Creation Date", "Delivery Date", "Custom Updated Delivery Date"].join("\n"),
			default: "Custom Updated Delivery Date",
		},
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date" },
		{
			fieldname: "only_unreserved",
			label: __("Only lines with unreserved pending"),
			fieldtype: "Check",
			default: 1,
		},
		{
			fieldname: "unreserved_basis",
			label: __("Unreserved Stock Basis"),
			fieldtype: "Select",
			options: ["All Reservations", "Only Displayed SOs"].join("\n"),
			default: "All Reservations",
		},

		// Raw-material side (BOM explosion + allotment), same as the standard
		// Production Planning Report / its manual-entry twin.
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "raw_material_warehouse",
			label: __("Raw Material Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
			get_query: function () {
				return { filters: { company: frappe.query_report.get_filter_value("company") } };
			},
		},
		{
			fieldname: "include_subassembly_raw_materials",
			label: __("Include Sub-assembly Raw Materials"),
			fieldtype: "Check",
			default: 0,
		},
	],

	// Same visual language as the standard Production Planning Report: red
	// highlight the FG item when Order Qty exceeds Available, and the raw
	// material name when Required Qty exceeds Allotted Qty.
	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		if (
			column.fieldname == "production_item" &&
			data &&
			data.production_item &&
			flt(data.qty_to_manufacture) > flt(data.available_qty)
		) {
			value = `<div style="color:red">${value}</div>`;
		}

		if (
			column.fieldname == "raw_material_name" &&
			data &&
			flt(data.required_qty) > flt(data.allotted_qty)
		) {
			value = `<div style="color:red">${value}</div>`;
		}

		return value;
	},
};
