// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

const FGSRM_METHOD_PATH =
	"playground.playground.report.fg_stock_reservation_manager.fg_stock_reservation_manager";

frappe.query_reports["FG Stock Reservation Manager"] = {
	filters: [
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
	],

	// Tint the Reserved column; emphasise Reservable / Reserve Qty.
	formatter(value, row, column, data, default_formatter) {
		let formatted = default_formatter(value, row, column, data);
		const f = column.fieldname || "";
		if (f === "reserved_qty") {
			return `<div style="background-color:#fde2e7;margin:-8px -12px;padding:8px 12px;">${formatted}</div>`;
		}
		if (f === "reservable_now") {
			return `<div style="background-color:#e1f5ee;margin:-8px -12px;padding:8px 12px;">${formatted}</div>`;
		}
		if (f === "reserve_qty") {
			return `<span style="font-weight:600;">${formatted}</span>`;
		}
		return formatted;
	},

	// Make Reserve Qty editable (capped at Reservable Now), and add row checkboxes
	// so lines can be selected for cancellation.
	get_datatable_options(datatable_options) {
		datatable_options.checkboxColumn = true;
		datatable_options.columns.forEach((column) => {
			if (column.id === "reserve_qty") column.editable = true;
		});

		datatable_options.events = datatable_options.events || {};
		datatable_options.events.onSubmitEditing = function (cell) {
			const [row_values, cell_id, new_val] = cell;
			if (cell_id !== "reserve_qty") return;

			let qty = flt(new_val);
			const cap = flt(row_values.reservable_now);
			if (qty < 0) qty = 0;
			if (qty > cap) {
				frappe.show_alert({
					message: __("Reserve Qty capped at Reservable Now ({0}).", [cap]),
					indicator: "orange",
				});
				qty = cap;
			}
			const report_row = (frappe.query_report.data || []).find(
				(r) => r.sales_order_item === row_values.sales_order_item
			);
			if (report_row) report_row.reserve_qty = qty;
			if (frappe.query_report.datatable) {
				frappe.query_report.datatable.refresh(frappe.query_report.data);
			}
		};

		return datatable_options;
	},

	onload(report) {
		report.page.add_inner_button(__("Create Reservations"), () => {
			const rows = (frappe.query_report.data || [])
				.filter((r) => flt(r.reserve_qty) > 0)
				.map((r) => ({
					sales_order: r.sales_order,
					sales_order_item: r.sales_order_item,
					item_code: r.item_code,
					qty: flt(r.reserve_qty),
				}));

			if (!rows.length) {
				frappe.msgprint(__("Enter a Reserve Qty on at least one line first."));
				return;
			}

			frappe.confirm(
				__("Create stock reservations for {0} line(s)? Quantities are capped at available free stock.", [rows.length]),
				() => {
					frappe.call({
						method: `${FGSRM_METHOD_PATH}.create_reservations`,
						args: { rows: JSON.stringify(rows) },
						freeze: true,
						freeze_message: __("Creating reservations…"),
						callback(r) {
							const m = r.message || {};
							frappe.show_alert({
								message: __("Reserved {0} line(s){1}.", [
									m.created || 0,
									m.capped ? __(", {0} capped at free stock", [m.capped]) : "",
								]),
								indicator: "green",
							});
							frappe.query_report.refresh();
						},
					});
				}
			);
		});

		report.page.add_inner_button(__("Cancel Reservations"), () => {
			const dt = frappe.query_report.datatable;
			const checked = dt ? dt.getCheckedRows() : [];
			const data = frappe.query_report.data || [];
			const sre_names = (checked || [])
				.map((i) => data[i])
				.filter((r) => r && r.existing_sre)
				.map((r) => r.existing_sre);

			if (!sre_names.length) {
				frappe.msgprint(__("Tick one or more rows that have an existing reservation to cancel."));
				return;
			}

			frappe.confirm(
				__("Cancel the reservations on {0} selected line(s)? This releases the stock.", [sre_names.length]),
				() => {
					frappe.call({
						method: `${FGSRM_METHOD_PATH}.cancel_reservations`,
						args: { sre_names: JSON.stringify(sre_names) },
						freeze: true,
						freeze_message: __("Cancelling reservations…"),
						callback(r) {
							const m = r.message || {};
							frappe.show_alert({
								message: __("Cancelled {0} reservation(s).", [m.cancelled || 0]),
								indicator: "blue",
							});
							frappe.query_report.refresh();
						},
					});
				}
			);
		});
	},
};
