// Sales Order — Blanket Order lock (client-side UX)
// ==================================================
// When a Sales Order originates from a Blanket Order (any line carries a
// `blanket_order` link), the negotiated rate and the commercial terms are fixed:
// only quantity may be edited. This greys out the locked fields and shows a banner
// so users see the restriction before they hit Save.
//
// This is UX ONLY. The authoritative guard is the server-side `validate` hook in
// playground/playground/blanket_order_lock.py — this script mirrors it so people
// don't waste effort editing fields the save would reject.

frappe.provide("playground.blanket_order");

// Header fields frozen once the SO draws down a Blanket Order.
const LOCKED_HEADER_FIELDS = [
	"currency",
	"conversion_rate",
	"selling_price_list",
	"payment_terms_template",
];

// Sales Order Item columns frozen (rate + every discounting path). The rate is
// pinned server-side; these are greyed out so the pins are visible.
const LOCKED_ITEM_FIELDS = [
	"rate",
	"price_list_rate",
	"discount_percentage",
	"discount_amount",
	"margin_type",
	"margin_rate_or_amount",
];

// Payment Schedule columns that define the TERMS (frozen). Amount columns rescale
// with qty and stay editable.
const LOCKED_PAYMENT_SCHEDULE_FIELDS = [
	"payment_term",
	"due_date",
	"invoice_portion",
	"credit_days",
	"mode_of_payment",
];

playground.blanket_order.has_blanket_line = function (frm) {
	return (frm.doc.items || []).some((row) => row.blanket_order);
};

playground.blanket_order.apply_lock = function (frm) {
	const locked = playground.blanket_order.has_blanket_line(frm);

	LOCKED_HEADER_FIELDS.forEach((field) => {
		if (frm.fields_dict[field]) {
			frm.set_df_property(field, "read_only", locked ? 1 : 0);
		}
	});

	set_grid_fields_read_only(frm, "items", LOCKED_ITEM_FIELDS, locked);
	set_grid_fields_read_only(frm, "payment_schedule", LOCKED_PAYMENT_SCHEDULE_FIELDS, locked);

	if (locked) {
		frm.dashboard.clear_comment();
		frm.dashboard.add_comment(
			__(
				"This Sales Order draws down a Blanket Order — rate, discounting and payment terms are fixed. Only quantities may be edited."
			),
			"blue",
			true
		);
	}
};

function set_grid_fields_read_only(frm, table_field, fieldnames, locked) {
	const grid = frm.fields_dict[table_field] && frm.fields_dict[table_field].grid;
	if (!grid) {
		return;
	}
	fieldnames.forEach((field) => {
		grid.update_docfield_property(field, "read_only", locked ? 1 : 0);
	});
	grid.refresh();
}

// Sales Order — Pending Qty / Amount
// ==================================
// Fills the virtual "Pending Qty" / "Pending Amount" item columns (created in
// playground/playground/sales_order_custom_fields.py) from qty - delivered_qty,
// and shows the order-level pending totals as dashboard indicators. Values are
// assigned straight onto the rows (not frappe.model.set_value) so a submitted
// order is never marked dirty.

frappe.provide("playground.pending");

playground.pending.compute_row = function (row) {
	const pending_qty = Math.max(flt(row.qty) - flt(row.delivered_qty), 0);
	row.custom_pending_qty = flt(pending_qty, precision("qty", row));
	row.custom_pending_amount = flt(pending_qty * flt(row.rate), precision("amount", row));
};

playground.pending.apply = function (frm) {
	let total_qty = 0;
	let total_amount = 0;
	(frm.doc.items || []).forEach((row) => {
		playground.pending.compute_row(row);
		total_qty += row.custom_pending_qty;
		total_amount += row.custom_pending_amount;
	});
	frm.refresh_field("items");

	if (frm.doc.docstatus === 1) {
		const color = total_qty > 0 ? "orange" : "green";
		frm.dashboard.add_indicator(
			__("Pending Qty: {0}", [format_number(total_qty, null, precision("qty"))]),
			color
		);
		frm.dashboard.add_indicator(
			__("Pending Amount: {0}", [format_currency(total_amount, frm.doc.currency)]),
			color
		);
	}
};

frappe.ui.form.on("Sales Order", {
	refresh(frm) {
		playground.blanket_order.apply_lock(frm);
		playground.pending.apply(frm);
	},
	// Grid docfields are only fully wired after the child rows render, so re-apply.
	onload_post_render(frm) {
		playground.blanket_order.apply_lock(frm);
	},
});

// Keep a draft's pending columns in step as lines are edited.
frappe.ui.form.on("Sales Order Item", {
	qty(frm, cdt, cdn) {
		playground.pending.compute_row(locals[cdt][cdn]);
		frm.refresh_field("items");
	},
	rate(frm, cdt, cdn) {
		playground.pending.compute_row(locals[cdt][cdn]);
		frm.refresh_field("items");
	},
});
