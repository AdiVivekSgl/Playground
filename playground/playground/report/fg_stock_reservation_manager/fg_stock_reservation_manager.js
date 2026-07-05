// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

const FGSRM_METHOD_PATH =
	"playground.playground.report.fg_stock_reservation_manager.fg_stock_reservation_manager";

// The report rows currently ticked in the DataTable (as data objects).
function fgsrm_checked_rows() {
	const dt = frappe.query_report.datatable;
	const data = frappe.query_report.data || [];
	if (!dt || !dt.getCheckedRows) return [];
	return (dt.getCheckedRows() || []).map((i) => data[i]).filter(Boolean);
}

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
		{
			fieldname: "unreserved_basis",
			label: __("Unreserved Stock Basis"),
			fieldtype: "Select",
			options: ["All Reservations", "Only Displayed SOs"].join("\n"),
			default: "All Reservations",
			// Free stock for Reservable Qty: net out every reservation (truly
			// free — correct for reserving), or only reservations tied to the
			// shown SOs (to reconcile with the PRR on that basis).
		},
		{
			// Carries the active view ("" / ready_to_dispatch / possible_to_complete)
			// set by the view buttons below. Hidden — not meant to be typed in.
			fieldname: "view_mode",
			label: __("View"),
			fieldtype: "Data",
			hidden: 1,
			default: "",
		},
		{
			fieldname: "group_by_so",
			label: __("Group by Sales Order"),
			fieldtype: "Check",
			default: 0,
			// Rows are always ordered so a SO's lines are adjacent; this toggle
			// just blanks the repeated SO/Customer/Date text on later lines of
			// the same SO for a cleaner grouped look. The underlying row data
			// (used by Create/Cancel/Select) is never touched.
		},
	],

	// Tint the Reserved column; emphasise Reservable / Reserve Qty.
	formatter(value, row, column, data, default_formatter) {
		let formatted = default_formatter(value, row, column, data);
		const f = column.fieldname || "";
		const grouping = cint(frappe.query_report.get_filter_value("group_by_so"));

		if (grouping && data && data.so_group_first === false && (f === "sales_order" || f === "customer" || f === "so_date")) {
			return "";
		}

		if (grouping && f === "sales_order" && data && data.so_group_first) {
			formatted = `<div style="border-top:1px solid var(--border-color, #d1d8dd);margin-top:-1px;padding-top:7px;">${formatted}</div>`;
		}

		if (f === "reserved_qty") {
			return `<div style="background-color:#fde2e7;margin:-8px -12px;padding:8px 12px;">${formatted}</div>`;
		}
		if (f === "item_free_stock" && data && flt(data.item_free_stock) < 0) {
			return `<span style="color:red;">${formatted}</span>`;
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
		// ── Run the JIT Production Planning Report for this same filtered view ──
		report.page.add_inner_button(
			__("Run JIT Production Planning Report"),
			() => {
				const values = frappe.query_report.get_filter_values();
				frappe.set_route("query-report", "JIT Production Planning Report").then(() => {
					// JIT Production Planning Report shares this report's filter
					// fieldnames (item_code, customer, sales_order, date_basis,
					// from_date, to_date, only_unreserved, unreserved_basis), so its
					// Order Qty / Available match what's on screen here. It also has
					// its own extra filters (company, raw_material_warehouse,
					// include_subassembly_raw_materials) which are left at their
					// defaults since this view has no equivalents.
					frappe.query_report.set_filter_value(values);
				});
			},
			__("Reports")
		);

		// ── Views: narrow to SOs where every line meets a condition ─────────
		report.page.add_inner_button(
			__("Ready to Dispatch"),
			() => frappe.query_report.set_filter_value("view_mode", "ready_to_dispatch"),
			__("Views")
		);
		report.page.add_inner_button(
			__("Possible to Complete"),
			() => frappe.query_report.set_filter_value("view_mode", "possible_to_complete"),
			__("Views")
		);
		report.page.add_inner_button(
			__("Show All"),
			() => frappe.query_report.set_filter_value("view_mode", ""),
			__("Views")
		);

		// ── Bulk selection by SO / Item (drives both Create and Cancel) ──────
		report.page.add_inner_button(
			__("Select by SO / Item"),
			() => {
				frappe.prompt(
					[
						{ fieldname: "sales_order", label: __("Sales Order"), fieldtype: "Link", options: "Sales Order" },
						{ fieldname: "item_code", label: __("FG Item"), fieldtype: "Link", options: "Item" },
					],
					(values) => {
						if (!values.sales_order && !values.item_code) {
							frappe.msgprint(__("Pick a Sales Order and/or an Item to select by."));
							return;
						}
						const dt = frappe.query_report.datatable;
						const data = frappe.query_report.data || [];
						if (!dt) return;
						dt.rowmanager.checkAll(false);
						let n = 0;
						data.forEach((r, i) => {
							const so_ok = !values.sales_order || r.sales_order === values.sales_order;
							const item_ok = !values.item_code || r.item_code === values.item_code;
							if (so_ok && item_ok) {
								dt.rowmanager.checkRow(i);
								n++;
							}
						});
						frappe.show_alert({ message: __("Selected {0} line(s).", [n]), indicator: "blue" });
					},
					__("Select lines by SO / Item"),
					__("Select")
				);
			},
			__("Selection")
		);

		report.page.add_inner_button(
			__("Clear Selection"),
			() => {
				const dt = frappe.query_report.datatable;
				if (dt) dt.rowmanager.checkAll(false);
			},
			__("Selection")
		);

		report.page.add_inner_button(__("Create Reservations"), () => {
			// If any rows are ticked, act only on them (SO/item-wise bulk create);
			// otherwise fall back to every line that has a Reserve Qty.
			const checked = fgsrm_checked_rows();
			const source = checked.length ? checked : frappe.query_report.data || [];
			const rows = source
				.filter((r) => flt(r.reserve_qty) > 0)
				.map((r) => ({
					sales_order: r.sales_order,
					sales_order_item: r.sales_order_item,
					item_code: r.item_code,
					qty: flt(r.reserve_qty),
				}));

			if (!rows.length) {
				frappe.msgprint(__("Tick the lines to reserve (or enter a Reserve Qty), then try again."));
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
			const sre_names = fgsrm_checked_rows()
				.filter((r) => r.existing_sre)
				.map((r) => r.existing_sre);

			if (!sre_names.length) {
				frappe.msgprint(__("Tick one or more rows that have an existing reservation to cancel (use Select by SO / Item for bulk)."));
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
