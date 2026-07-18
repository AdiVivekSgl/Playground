// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

const WPSR_METHOD_PATH =
	"playground.playground.report.weekly_planning_snapshot_review.weekly_planning_snapshot_review";

// Reuse FGSRM's exact Production Plan + workbook machinery, so "Create Prodn
// Plan" here behaves identically to the FGSRM report.
const WPSR_CREATE_PLAN_METHOD =
	"playground.playground.report.fg_stock_reservation_manager.fg_stock_reservation_manager.create_production_plan_from_suggested_prodn";
const WPSR_MR_EXCEL_METHOD =
	"playground.playground.production_plan_mr_excel.download_fgsrm_mr_excel";

// Download without navigating away (anchor click avoids the async-callback
// pop-up blocker; attachment response downloads rather than opening a tab).
function wpsr_download_mr_excel(pp_name, filters_json) {
	let url = `/api/method/${WPSR_MR_EXCEL_METHOD}?name=${encodeURIComponent(pp_name)}`;
	if (filters_json) url += `&filters=${encodeURIComponent(filters_json)}`;
	const a = document.createElement("a");
	a.href = url;
	a.target = "_blank";
	a.rel = "noopener";
	document.body.appendChild(a);
	a.click();
	a.remove();
}

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
		// Create a Production Plan + download the requirement workbook, same as
		// the FGSRM report (reuses FGSRM's server methods with this report's
		// filters).
		report.page.add_inner_button(
			__("Create Prodn Plan"),
			() => {
				frappe.confirm(
					__("Create a draft Production Plan from the itemwise Suggested Prodn for the current filters? It will build the full nested plan chain and raw materials, then download the Production Plan workbook — no need to open the plan."),
					() => {
						frappe.call({
							method: WPSR_CREATE_PLAN_METHOD,
							args: { filters: JSON.stringify(frappe.query_report.get_filter_values()) },
							freeze: true,
							freeze_message: __("Creating Production Plan…"),
							callback(r) {
								const m = r.message;
								if (!m || !m.name) return;
								if (m.handed_off) {
									frappe.show_alert({
										message: __("Production Plan {0}: {1} item(s), {2} raw material line(s), full chain built. Downloading Production Plan workbook…", [
											m.name,
											m.items,
											m.raw_materials,
										]),
										indicator: "green",
									});
									wpsr_download_mr_excel(m.name, JSON.stringify(frappe.query_report.get_filter_values()));
								} else {
									frappe.show_alert({
										message: __("Draft Production Plan {0} created with {1} item(s). Open it and click “Create Full Chain”, then download the Production Plan workbook.", [
											m.name,
											m.items,
										]),
										indicator: "blue",
									});
									frappe.set_route("Form", "Production Plan", m.name);
								}
							},
						});
					}
				);
			},
			__("Reports")
		);

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
