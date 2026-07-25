// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

frappe.ui.form.on("Purchase Authorization Sheet", {
	refresh(frm) {
		// Populate the item table from the uploaded workbook's
		// "Approved for Purchase" sheet.
		if (!frm.is_new() && frm.doc.upload_excel) {
			frm.add_custom_button(__("Populate from Excel"), () => {
				frappe.confirm(
					__("Replace the item table with the rows from the 'Approved for Purchase' sheet?"),
					() => {
						frappe.call({
							method: "playground.playground.doctype.purchase_authorization_sheet.purchase_authorization_sheet.populate_from_excel",
							args: { docname: frm.doc.name },
							freeze: true,
							freeze_message: __("Reading Excel…"),
							callback(r) {
								const m = r.message || {};
								frappe.show_alert({
									message: __("Added {0} item(s){1}.", [
										m.added || 0,
										m.skipped && m.skipped.length ? __(", {0} skipped (unknown item)", [m.skipped.length]) : "",
									]),
									indicator: "green",
								});
								if (m.skipped && m.skipped.length) {
									frappe.msgprint({
										title: __("Skipped items (not found)"),
										message: m.skipped.map(frappe.utils.escape_html).join("<br>"),
										indicator: "orange",
									});
								}
								frm.reload_doc();
							},
						});
					}
				);
			});
		}

		// Line-wise approval helpers (the Approve checkbox is editable directly in
		// the grid too, incl. after submit).
		if ((frm.doc.items || []).length) {
			frm.add_custom_button(__("Approve All"), () => pas_set_all(frm, 1), __("Approvals"));
			frm.add_custom_button(__("Clear Approvals"), () => pas_set_all(frm, 0), __("Approvals"));
		}

		// Raise draft Purchase Orders (one per vendor) from the approved lines that
		// haven't been ordered yet. Only meaningful once the sheet is submitted.
		if (frm.doc.docstatus === 1 && (frm.doc.items || []).some((r) => r.approve && !r.purchase_order)) {
			frm.add_custom_button(__("Create Purchase Orders"), () => pas_create_pos(frm));
		}
	},
});

function pas_create_pos(frm) {
	frappe.confirm(
		__("Create draft Purchase Orders (one per vendor) from the approved, not-yet-ordered lines?"),
		() => {
			frappe.call({
				method: "playground.playground.doctype.purchase_authorization_sheet.purchase_authorization_sheet.create_purchase_orders",
				args: { docname: frm.doc.name },
				freeze: true,
				freeze_message: __("Creating Purchase Orders…"),
				callback(r) {
					const m = r.message || {};
					const created = m.created || [];
					const skipped = m.skipped || [];
					frappe.show_alert({
						message: created.length
							? __("Created {0} Purchase Order(s).", [created.length])
							: __("No Purchase Orders created."),
						indicator: created.length ? "green" : "orange",
					});
					if (created.length) {
						frappe.msgprint({
							title: __("Purchase Orders created"),
							message: created
								.map((n) => `<a href="/app/purchase-order/${encodeURIComponent(n)}">${frappe.utils.escape_html(n)}</a>`)
								.join("<br>"),
							indicator: "green",
						});
					}
					if (skipped.length) {
						frappe.msgprint({
							title: __("Lines not ordered"),
							message: skipped
								.map((s) => `${frappe.utils.escape_html(s.item)} — ${frappe.utils.escape_html(s.reason)}`)
								.join("<br>"),
							indicator: "orange",
						});
					}
					frm.reload_doc();
				},
			});
		}
	);
}

function pas_set_all(frm, val) {
	(frm.doc.items || []).forEach((row) => frappe.model.set_value(row.doctype, row.name, "approve", val ? 1 : 0));
	frm.dirty();
	frm.save(frm.doc.docstatus === 1 ? "Update" : undefined).then(() => {
		frappe.show_alert({
			message: val ? __("All lines approved.") : __("All approvals cleared."),
			indicator: val ? "green" : "blue",
		});
	});
}
