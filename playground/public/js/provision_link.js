// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

// Provision Management: restrict the "Provision Against" link to open
// (un-reversed) Expense Provisions of the same company, on both Purchase Invoice
// and Journal Entry. Loaded on both forms via doctype_js in hooks.py.
function set_provision_against_query(frm) {
	frm.set_query("custom_provision_against", () => ({
		filters: { docstatus: 1, status: "Open", company: frm.doc.company },
	}));
}

frappe.ui.form.on("Purchase Invoice", { setup: set_provision_against_query });
frappe.ui.form.on("Journal Entry", { setup: set_provision_against_query });
