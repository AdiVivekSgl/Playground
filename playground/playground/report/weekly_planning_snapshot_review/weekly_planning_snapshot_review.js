// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

const WPSR_METHOD_PATH =
	"playground.playground.report.weekly_planning_snapshot_review.weekly_planning_snapshot_review";

// Colour blocks mirror the style already used in fg_stock_reservation_manager.js
// (background-color divs, not just text colour).
const WPSR_BUCKET_COLORS = {
	New: "#e1f5ee",
	"Qty Changed": "#faeeda",
	Closed: "#f1efe8",
};

const WPSR_STATUS_COLORS = {
	Cancelled: "#fcebeb",
	Dispatched: "#eaf3de",
	"Partially Dispatched": "#faeeda",
	"Production Completed": "#eaf3de",
	"In Production": "#e6f1fb",
	"Stopped / Closed": "#fcebeb",
	"Awaiting Production": "#f1efe8",
	"Removed from SO": "#fcebeb",
};

frappe.query_reports["Weekly Planning Snapshot Review"] = {
	filters: [
		{ fieldname: "item_code", label: __("FG Item"), fieldtype: "Link", options: "Item" },
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
		{
			fieldname: "only_suggested",
			label: __("Only Suggested Prodn > 0"),
			fieldtype: "Check",
			default: 0,
		},
		{
			fieldname: "consolidated",
			label: __("Consolidated Suggested Prodn"),
			fieldtype: "Check",
			default: 0,
			// One row per item: Item Name, Item Free Stock, total Suggested Prodn.
		},
	],

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		const f = column.fieldname || "";

		if (f === "diff_bucket" && data && WPSR_BUCKET_COLORS[data.diff_bucket]) {
			return `<div style="background-color:${WPSR_BUCKET_COLORS[data.diff_bucket]};margin:-8px -12px;padding:8px 12px;">${formatted}</div>`;
		}
		if (f === "status" && data && WPSR_STATUS_COLORS[data.status]) {
			return `<div style="background-color:${WPSR_STATUS_COLORS[data.status]};margin:-8px -12px;padding:8px 12px;">${formatted}</div>`;
		}
		if (f === "suggested_prodn" && data && flt(data.suggested_prodn) > 0) {
			return `<div style="background-color:#fff3e0;margin:-8px -12px;padding:8px 12px;font-weight:600;">${formatted}</div>`;
		}
		return formatted;
	},

	// Make Buffer editable; editing it recomputes Suggested Prodn live
	// (max(0, (Pending - Reserved) - Item Free Stock + Buffer)).
	get_datatable_options(options) {
		options.columns.forEach((c) => {
			if (c.id === "buffer") c.editable = true;
		});
		options.events = options.events || {};
		options.events.onSubmitEditing = function (cell) {
			const [row_values, cell_id, new_val] = cell;
			if (cell_id !== "buffer") return;
			let buffer = flt(new_val);
			if (buffer < 0) buffer = 0;
			const short = Math.max(0, flt(row_values.pending_qty) - flt(row_values.reserved_qty));
			const suggested = Math.max(0, short - flt(row_values.item_free_stock) + buffer);
			const report_row = (frappe.query_report.data || []).find(
				(r) => r.sales_order_item === row_values.sales_order_item
			);
			if (report_row) {
				report_row.buffer = buffer;
				report_row.suggested_prodn = suggested;
			}
			if (frappe.query_report.datatable) {
				frappe.query_report.datatable.refresh(frappe.query_report.data);
			}
		};
		return options;
	},

	onload(report) {
		report.page.add_inner_button(__("Approve & Save Snapshot"), () => {
			frappe.confirm(
				__("This freezes the current open Sales Order demand into a new, submitted Weekly Planning Snapshot — you'll compare against it next time. Continue?"),
				() => {
					frappe.call({
						method: `${WPSR_METHOD_PATH}.approve_snapshot`,
						args: { filters: JSON.stringify(frappe.query_report.get_filter_values()) },
						freeze: true,
						freeze_message: __("Saving snapshot…"),
						callback(r) {
							if (r.message) {
								frappe.show_alert({
									message: __("Snapshot {0} approved and submitted.", [r.message]),
									indicator: "green",
								});
								frappe.set_route("Form", "Weekly Planning Snapshot", r.message);
							}
						},
					});
				}
			);
		});
	},
};
