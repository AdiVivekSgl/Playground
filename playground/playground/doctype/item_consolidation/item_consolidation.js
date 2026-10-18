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
				: r.open_documents_only
				? `<span class="indicator-pill orange">${__("Open documents only - can disable anyway on Submit")}</span>`
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
			const alternatives = (r.alternatives || []).length
				? `<div class="small">${__("Item Alternatives to move")}: ${r.alternatives
						.map(icn_esc)
						.join(", ")}</div>`
				: "";
			return `<div style="border-bottom:1px solid var(--border-color);padding:8px 0;">
				<div><b>${__("Row")} ${r.idx}: ${icn_esc(r.old_item)} → ${icn_esc(r.keep_item)}</b> ${badge}</div>
				${icn_list(r.blockers, "text-danger")}
				${icn_list(r.warnings, "text-warning")}
				${stock}${boms}${alternatives}
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

	// When open documents are the ONLY blocker, offer to disable the retired items
	// anyway instead of letting the server refuse. Any other blocker falls through
	// to the server's "Resolve these first" error unchanged.
	before_submit(frm) {
		if (!frm.doc.disable_old_items || frm.doc.disable_despite_open_documents) return;

		return frappe.call({ method: `${ICN_API}.preview`, args: { name: frm.doc.name } }).then((r) => {
			const blocked = (r.message || []).filter((p) => p.blockers.length);
			if (!blocked.length || !blocked.every((p) => p.open_documents_only)) return;

			const rows = blocked
				.map(
					(p) => `<p><b>${__("Row")} ${p.idx} (${icn_esc(p.old_item)})</b></p>
						${icn_list(p.blockers, "text-muted small")}`
				)
				.join("");
			const message = `${rows}
				<p>${__(
					"Disable these items anyway? Stock, masters and BOMs move to the kept items as normal, but the documents above will fail on submit until they are amended to use the kept item."
				)}</p>`;

			return new Promise((resolve) => {
				frappe.confirm(
					message,
					() => frm.set_value("disable_despite_open_documents", 1).then(resolve),
					() => {
						frappe.validated = false;
						resolve();
					}
				);
			});
		});
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
