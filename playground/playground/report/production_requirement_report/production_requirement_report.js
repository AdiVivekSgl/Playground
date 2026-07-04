// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

const PRR_METHOD_PATH =
	"playground.playground.report.production_requirement_report.production_requirement_report";

function prr_recompute_required_to_produce(row_values, new_buffer_qty) {
	let total_pending = 0;
	let total_reserved = 0;

	Object.keys(row_values).forEach((key) => {
		if (key.indexOf("pending_") === 0) total_pending += flt(row_values[key]);
		if (key.indexOf("reserved_") === 0) total_reserved += flt(row_values[key]);
	});

	const total_avlbl_stock = flt(row_values.total_avlbl_stock);
	const unfulfilled_demand = total_pending - total_reserved;

	return Math.max(0, unfulfilled_demand - total_avlbl_stock + flt(new_buffer_qty));
}

frappe.query_reports["Production Requirement Report"] = {
	filters: [
		{
			fieldname: "item_code",
			label: __("FG Item"),
			fieldtype: "Link",
			options: "Item",
		},
		{
			fieldname: "customer",
			label: __("Customer"),
			fieldtype: "Link",
			options: "Customer",
		},
		{
			fieldname: "unreserved_basis",
			label: __("Unreserved Stock Basis"),
			fieldtype: "Select",
			options: ["All Reservations", "Only Displayed SOs"].join("\n"),
			default: "All Reservations",
			// Controls what "Total Avlbl Unreserved Stock" nets out of the
			// stores warehouse on-hand: every reservation (truly free stock),
			// or only reservations tied to the Sales Orders shown here.
		},
	],

	// Native Script Report columns hard-code editable:false when the DataTable is built
	// (see frappe/frappe#27414), so we flip it back on here. Buffer Qty edits are handled
	// entirely client-side: they recompute Required to Produce for this session only and
	// are NEVER written back to the Item master (Item.safety_stock is only ever read, on load).
	get_datatable_options(datatable_options) {
		datatable_options.columns.forEach((column) => {
			if (column.id === "buffer_qty") {
				column.editable = true;
			}
		});

		datatable_options.events = datatable_options.events || {};
		const original_on_submit_editing = datatable_options.events.onSubmitEditing;

		datatable_options.events.onSubmitEditing = function (cell) {
			if (original_on_submit_editing) {
				original_on_submit_editing(cell);
			}

			// cell = [row_values (before edit), cell_id, new_val]
			const [row_values, cell_id, new_val] = cell;
			if (cell_id !== "buffer_qty") return;

			let buffer_qty = flt(new_val);
			if (buffer_qty < 0) {
				frappe.show_alert({ message: __("Buffer Qty cannot be negative."), indicator: "red" });
				buffer_qty = 0;
			}

			const required_to_produce = prr_recompute_required_to_produce(row_values, buffer_qty);

			// Keep the report's own in-memory data array in sync (used by the
			// Create Production Plan button, exports, etc.) - this array is never
			// sent anywhere; it just lives for the current browser session.
			const report_row = (frappe.query_report.data || []).find(
				(r) => r.item_code === row_values.item_code
			);
			if (report_row) {
				report_row.buffer_qty = buffer_qty;
				report_row.required_to_produce = required_to_produce;
			}

			if (frappe.query_report.datatable) {
				frappe.query_report.datatable.refresh(frappe.query_report.data);
			}

			frappe.show_alert({
				message: __("Buffer Qty updated for this session only — not saved to the Item master."),
				indicator: "blue",
			});
		};

		return datatable_options;
	},

	onload(report) {
		report.page.add_inner_button(__("Create Production Plan"), () => {
			const data = frappe.query_report.data || [];
			const items = data
				.filter((row) => flt(row.required_to_produce) > 0)
				.map((row) => ({ item_code: row.item_code, qty: row.required_to_produce }));

			if (!items.length) {
				frappe.msgprint(__("No rows currently have a Required to Produce quantity greater than zero."));
				return;
			}

			const item_list_html = items
				.map((i) => `<li>${frappe.utils.escape_html(i.item_code)}: ${i.qty}</li>`)
				.join("");

			frappe.confirm(
				__("This will create a draft Production Plan with {0} item(s):<ul>{1}</ul>You'll be able to review and edit it before submitting. Continue?", [
					items.length,
					item_list_html,
				]),
				() => {
					frappe.call({
						method: `${PRR_METHOD_PATH}.create_production_plan`,
						args: { items: JSON.stringify(items) },
						freeze: true,
						freeze_message: __("Creating Production Plan..."),
						callback: function (r) {
							if (r.message) {
								frappe.set_route("Form", "Production Plan", r.message);
							}
						},
					});
				}
			);
		});
	},
};
