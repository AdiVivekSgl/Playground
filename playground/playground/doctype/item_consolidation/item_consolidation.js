// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

// Item Consolidation: Dry Run (draft) and Retry Failed (submitted). Server side:
// playground/playground/item_consolidation/api.py

const ICN_API = "playground.playground.item_consolidation.api";

function icn_esc(v) {
	return frappe.utils.escape_html(v == null ? "" : String(v));
}

function icn_list(items, cls) {
	if (!items.length) return "";
	return `<ul class="${cls}" style="margin:4px 0 8px 16px;">${items
		.map((t) => `<li>${icn_esc(t)}</li>`)
		.join("")}</ul>`;
}

function icn_render_preview(reports) {
	return reports
		.map((r) => {
			const ok = !r.blockers.length;
			const badge = ok
				? `<span class="indicator-pill green">${__("Ready")}</span>`
				: `<span class="indicator-pill red">${__("Blocked")}</span>`;
			const stock = r.stock.length
				? `<table class="table table-bordered" style="font-size:12px;margin:4px 0;">
					<thead><tr><th>${__("Warehouse")}</th><th class="text-right">${__("Qty")}</th>
					<th class="text-right">${__("Value")}</th></tr></thead>
					<tbody>${r.stock
						.map(
							(s) => `<tr><td>${icn_esc(s.warehouse)}</td>
							<td class="text-right">${format_number(s.qty)}</td>
							<td class="text-right">${format_currency(s.value)}</td></tr>`
						)
						.join("")}</tbody></table>`
				: `<div class="text-muted small">${__("No stock to move.")}</div>`;
			const boms = r.boms.length
				? `<div class="small">${__("BOMs to update")}: ${r.boms.map(icn_esc).join(", ")}</div>`
				: "";
			return `<div style="border-bottom:1px solid var(--border-color);padding:8px 0;">
				<div><b>${__("Row")} ${r.idx}: ${icn_esc(r.old_item)} → ${icn_esc(r.keep_item)}</b> ${badge}</div>
				${icn_list(r.blockers, "text-danger")}
				${icn_list(r.warnings, "text-warning")}
				${stock}${boms}
			</div>`;
		})
		.join("");
}

frappe.ui.form.on("Item Consolidation", {
	onload(frm) {
		if (frm.is_new() && !frm.doc.company) {
			frm.set_value("company", frappe.defaults.get_user_default("Company"));
		}
		const active = () => ({ filters: { disabled: 0, has_variants: 0 } });
		frm.set_query("old_item", "items", active);
		frm.set_query("keep_item", "items", active);
	},

	refresh(frm) {
		if (frm.doc.docstatus === 0 && !frm.is_new()) {
			frm.add_custom_button(__("Dry Run"), () => {
				frappe.call({
					method: `${ICN_API}.preview`,
					args: { name: frm.doc.name },
					freeze: true,
					freeze_message: __("Checking item pairs..."),
					callback: (r) => {
						const d = new frappe.ui.Dialog({
							title: __("Dry Run"),
							size: "large",
							fields: [{ fieldtype: "HTML", fieldname: "report" }],
						});
						d.fields_dict.report.$wrapper.html(icn_render_preview(r.message || []));
						d.show();
					},
				});
			});
		}

		if (frm.doc.docstatus === 1 && ["Failed", "Partly Failed"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Retry Failed"), () => {
				frappe.call({
					method: `${ICN_API}.retry_failed`,
					args: { name: frm.doc.name },
					callback: () => frm.reload_doc(),
				});
			});
		}

		if (["Queued", "In Progress"].includes(frm.doc.status)) {
			frm.dashboard.set_headline(
				__("Consolidation is running in the background. This form refreshes when it finishes.")
			);
		}
	},
});
