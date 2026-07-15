// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

// Adds a "Close Purchase Orders" bulk action to the Purchase Order list view's
// Actions menu. Select POs -> Actions -> Close Purchase Orders -> confirm ->
// server closes eligible ones via ERPNext's native close -> summary dialog.

frappe.listview_settings["Purchase Order"] = {
	onload(listview) {
		listview.page.add_actions_menu_item(
			__("Close Purchase Orders"),
			() => pg_bulk_close_purchase_orders(listview),
			false
		);
	},
};

function pg_bulk_close_purchase_orders(listview) {
	const selected = listview.get_checked_items() || [];
	if (!selected.length) {
		frappe.msgprint(__("Select one or more Purchase Orders first."));
		return;
	}

	// Client-side pre-filter: only Submitted (docstatus 1), not already Closed /
	// Cancelled. The server re-validates (and also skips fully received+billed).
	const eligible = selected.filter(
		(d) => cint(d.docstatus) === 1 && !["Closed", "Cancelled"].includes(d.status)
	);
	const ignored = selected.length - eligible.length;

	if (!eligible.length) {
		frappe.msgprint(
			__("None of the selected Purchase Orders are eligible — they must be Submitted and not already Closed or Cancelled.")
		);
		return;
	}

	const d = new frappe.ui.Dialog({
		title: __("Close {0} Purchase Order(s)", [eligible.length]),
		fields: [
			{
				fieldtype: "HTML",
				options:
					`<p>${__("This closes the selected submitted Purchase Orders using ERPNext's standard close. Purchase Orders that are fully received and billed are skipped.")}</p>` +
					(ignored
						? `<p class="text-muted">${__("{0} selected row(s) will be ignored (not Submitted, or already Closed/Cancelled).", [ignored])}</p>`
						: ""),
			},
			{
				fieldname: "reason",
				label: __("Closing Reason"),
				fieldtype: "Small Text",
				description: __("Optional — stored on each closed Purchase Order for an audit trail."),
			},
		],
		primary_action_label: __("Close Purchase Orders"),
		primary_action(values) {
			d.hide();
			frappe.call({
				method: "playground.playground.purchase_order_bulk.bulk_close_purchase_orders",
				args: {
					names: JSON.stringify(eligible.map((r) => r.name)),
					reason: values.reason || "",
				},
				freeze: true,
				freeze_message: __("Closing Purchase Orders…"),
				callback(r) {
					pg_show_bulk_close_summary(r.message || {});
					listview.clear_checked_items && listview.clear_checked_items();
					listview.refresh();
				},
			});
		},
	});
	d.show();
}

function pg_show_bulk_close_summary(m) {
	const closed = (m.closed || []).length;
	const skipped = m.skipped || [];
	const failed = m.failed || {};
	const failed_names = Object.keys(failed);

	let html = `<p><b>${__("Closed: {0}", [closed])}</b></p>`;

	if (skipped.length) {
		html +=
			`<p>${__("Skipped: {0}", [skipped.length])}</p><ul style="font-size:12px;margin-top:-4px;">` +
			skipped
				.map(
					(s) =>
						`<li>${frappe.utils.escape_html(s.name)} — ${frappe.utils.escape_html(s.reason)}</li>`
				)
				.join("") +
			`</ul>`;
	}

	if (failed_names.length) {
		html +=
			`<p style="color:#b71c1c;">${__("Failed: {0}", [failed_names.length])}</p><ul style="font-size:12px;margin-top:-4px;">` +
			failed_names
				.map(
					(n) =>
						`<li>${frappe.utils.escape_html(n)} — ${frappe.utils.escape_html(failed[n])}</li>`
				)
				.join("") +
			`</ul>`;
	}

	frappe.msgprint({
		title: __("Bulk Close Summary"),
		message: html,
		indicator: failed_names.length ? "red" : closed ? "green" : "orange",
	});

	if (closed) {
		frappe.show_alert({
			message: __("Closed {0} Purchase Order(s).", [closed]),
			indicator: "green",
		});
	}
}
