// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

frappe.ui.form.on("Expense Provision", {
	refresh(frm) {
		if (frm.doc.docstatus === 1 && !["Settled", "Reversed", "Cancelled"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Refresh Status"), () => {
				frappe.call({
					method: "playground.playground.doctype.expense_provision.expense_provision.refresh_status",
					args: { provision_name: frm.doc.name },
					freeze: true,
					callback: () => frm.reload_doc(),
				});
			});
		}

		// Status indicator colour, matching the settlement lifecycle.
		const colour = {
			Draft: "grey", Open: "orange", "Partially Settled": "yellow",
			Settled: "green", Reversed: "blue", Cancelled: "red",
		}[frm.doc.status];
		if (colour) frm.page.set_indicator(__(frm.doc.status), colour);
	},

	company(frm) {
		// Scope account / cost center pickers to the selected company + right root type.
		for (const [field, root] of [["expense_account", "Expense"], ["provision_account", "Liability"]]) {
			frm.set_query(field, () => ({
				filters: { company: frm.doc.company, is_group: 0, root_type: root },
			}));
		}
		frm.set_query("cost_center", () => ({ filters: { company: frm.doc.company, is_group: 0 } }));
	},
});
