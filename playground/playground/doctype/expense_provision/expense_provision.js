// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

frappe.ui.form.on("Expense Provision", {
	refresh(frm) {
		// Status indicator colour, matching the lifecycle.
		const colour = {
			Draft: "grey", Open: "orange", Reversed: "blue", Cancelled: "red",
		}[frm.doc.status];
		if (colour) frm.page.set_indicator(__(frm.doc.status), colour);

		// Quick links to the booked entries.
		if (frm.doc.provision_journal_entry) {
			frm.add_custom_button(__("Provision JE"), () =>
				frappe.set_route("Form", "Journal Entry", frm.doc.provision_journal_entry), __("View"));
		}
		if (frm.doc.reversal_journal_entry) {
			frm.add_custom_button(__("Reversal JE"), () =>
				frappe.set_route("Form", "Journal Entry", frm.doc.reversal_journal_entry), __("View"));
		}
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
