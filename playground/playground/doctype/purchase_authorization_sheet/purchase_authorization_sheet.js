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
	},
});

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
