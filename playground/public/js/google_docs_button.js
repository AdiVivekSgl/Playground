// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt
//
// Generic "Google Doc" form actions for any ERP DocType that has an enabled
// Google Document Template. Wire a DocType to this file in hooks.doctype_js to
// enable it — no per-doctype JS is needed (spec 9 / extensibility).
//
// Renders under a "Google Doc" button group:
//   * no doc yet  -> Create Google Doc
//   * doc exists  -> Open / Update / Create New Google Doc  (+ a status banner)

frappe.provide("playground.google_docs");

playground.google_docs.render_buttons = function (frm) {
	if (frm.is_new()) return;

	frappe.call({
		method: "playground.playground.google_docs.api.get_google_document",
		args: { doctype: frm.doctype, name: frm.docname },
		callback(r) {
			const info = r.message || {};
			if (!info.supported) return; // no template configured for this doctype

			const group = __("Google Doc");

			if (!info.document_id) {
				frm.add_custom_button(__("Create Google Doc"), () => {
					playground.google_docs.run(frm, "create_google_document", {}, __("Creating Google Doc…"));
				}, group);
				return;
			}

			// A doc already exists — show status + the three follow-up actions.
			if (info.last_updated) {
				frm.dashboard.add_comment(
					__("Google Doc: {0} · Last updated {1}", [
						info.status || __("Created"),
						frappe.datetime.str_to_user(info.last_updated),
					]),
					info.status === "Error" ? "red" : "green",
					true
				);
			}

			frm.add_custom_button(__("Open Google Doc"), () => {
				frappe.call({
					method: "playground.playground.google_docs.api.open_google_document",
					args: { doctype: frm.doctype, name: frm.docname },
					callback(res) {
						if (res.message && res.message.url) window.open(res.message.url, "_blank");
					},
				});
			}, group);

			frm.add_custom_button(__("Update Google Doc"), () => {
				playground.google_docs.run(frm, "update_google_document", {}, __("Updating Google Doc…"));
			}, group);

			frm.add_custom_button(__("Create New Google Doc"), () => {
				frappe.confirm(
					__("This creates a brand-new Google Doc and replaces the stored link. Continue?"),
					() => playground.google_docs.run(frm, "create_google_document", { force_new: 1 }, __("Creating new Google Doc…"))
				);
			}, group);
		},
	});
};

playground.google_docs.run = function (frm, method, extra_args, message) {
	frappe.call({
		method: `playground.playground.google_docs.api.${method}`,
		args: Object.assign({ doctype: frm.doctype, name: frm.docname }, extra_args || {}),
		freeze: true,
		freeze_message: message,
		callback(r) {
			const res = r.message || {};
			if (res.queued) {
				frappe.show_alert({ message: __("Queued — refresh in a moment."), indicator: "blue" });
			} else if (res.url) {
				frappe.show_alert({ message: __("Done."), indicator: "green" });
				frm.reload_doc();
			}
		},
	});
};

// Bind on the doctype this file is attached to. `cur_frm` is the form being set
// up when doctype_js loads this file, so we register a refresh handler for it.
if (typeof cur_frm !== "undefined" && cur_frm) {
	frappe.ui.form.on(cur_frm.doctype, {
		refresh(frm) {
			playground.google_docs.render_buttons(frm);
		},
	});
}
