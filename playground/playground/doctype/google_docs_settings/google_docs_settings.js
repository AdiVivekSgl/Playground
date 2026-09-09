// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

frappe.ui.form.on("Google Docs Settings", {
	test_connection(frm) {
		frappe.call({
			method: "playground.playground.doctype.google_docs_settings.google_docs_settings.test_connection",
			freeze: true,
			freeze_message: __("Testing Google connection…"),
			callback(r) {
				if (r.message && r.message.ok) {
					frappe.msgprint({
						title: __("Success"),
						indicator: "green",
						message: r.message.message,
					});
				}
			},
		});
	},
});
