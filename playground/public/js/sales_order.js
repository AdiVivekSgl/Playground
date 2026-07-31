// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

// Open Order View for submitted Sales Orders
// ==========================================
//
// Turns a *submitted* Sales Order into an "open orders" board: instead of the
// original ordered figures, it surfaces what is still owed to the customer -
// pending qty, pending value and a fulfilment percentage - so Sales, Planning,
// Production and Dispatch can read the remaining demand at a glance.
//
// This is a PRESENTATION-LAYER enhancement only. It never touches the stored
// document: the standard ERPNext Items grid is left completely intact as the
// system of record (so the original qty/amount stay one click away, and Edit /
// Amend / Return behave exactly as stock ERPNext does). Everything below is
// rendered from values already on the in-memory doc - no custom fields, no
// database queries, no server round-trips.
//
// The pending maths mirrors playground/playground/open_order_view.py, which is
// the documented single source of truth and carries the unit tests. Keep the two
// in sync if the formulas ever change.
//
// Behaviour is gated by Playground Settings, shipped to the client through the
// desk bootinfo (see boot_open_order_settings) so we read it with zero round-trips.

frappe.provide("playground.open_order_view");

(function () {
	const DEFAULT_SETTINGS = {
		enable_pending_view: 1,
		show_original_qty: 0,
		show_original_amount: 0,
		highlight_completed_rows: 1,
		clamp_negative_pending: 1,
	};

	// Row status -> subtle background / accent, kept in step with open_order_view.py.
	const STATUS_STYLE = {
		"Fully Delivered": { bg: "#e8f5e9", fg: "#2e7d32", label: __("Fully Delivered") },
		"Partially Delivered": { bg: "#fff3e0", fg: "#e65100", label: __("Partially Delivered") },
		"Not Started": { bg: "#e3f2fd", fg: "#1565c0", label: __("Not Started") },
		Cancelled: { bg: "#eceff1", fg: "#607d8b", label: __("Cancelled") },
	};

	function get_settings() {
		const boot = (frappe.boot && frappe.boot.playground_open_order_settings) || {};
		return Object.assign({}, DEFAULT_SETTINGS, boot);
	}

	function row_status(qty, delivered) {
		if (qty <= 0) return "Cancelled";
		if (delivered >= qty) return "Fully Delivered";
		if (delivered <= 0) return "Not Started";
		return "Partially Delivered";
	}

	// Single source of truth (mirrors compute_item_pending in open_order_view.py).
	function compute_item_pending(qty, delivered, rate, clamp) {
		qty = flt(qty);
		delivered = flt(delivered);
		rate = flt(rate);
		const raw = qty - delivered;
		const pending_qty = clamp ? Math.max(raw, 0) : raw;
		return {
			pending_qty: pending_qty,
			pending_amount: pending_qty * rate,
			status: row_status(qty, delivered),
		};
	}

	function compute_summary(items, clamp) {
		let order_value = 0,
			delivered_value = 0,
			pending_value = 0;
		(items || []).forEach((row) => {
			const qty = flt(row.qty),
				rate = flt(row.rate),
				delivered = flt(row.delivered_qty);
			order_value += qty * rate;
			delivered_value += delivered * rate;
			pending_value += compute_item_pending(qty, delivered, rate, clamp).pending_amount;
		});
		const completion = order_value ? (delivered_value / order_value) * 100 : 0;
		return { order_value, delivered_value, pending_value, completion_percent: completion };
	}

	const esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));

	function fmt_currency(v, currency) {
		return format_currency(flt(v), currency);
	}
	function fmt_qty(v) {
		return format_number(flt(v), null, 2);
	}

	function build_summary_html(frm, settings) {
		const currency = frm.doc.currency;
		const s = compute_summary(frm.doc.items, settings.clamp_negative_pending);
		const pct = Math.round(s.completion_percent);
		const bar_class =
			pct >= 100 ? "progress-bar-success" : pct > 0 ? "progress-bar-warning" : "progress-bar-info";

		const tile = (label, value, accent) => `
			<div style="flex:1 1 140px;min-width:120px;padding:8px 12px;">
				<div style="font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--text-muted);">${label}</div>
				<div style="font-size:16px;font-weight:600;color:${accent || "var(--text-color)"};">${value}</div>
			</div>`;

		return `
			<div class="oov-summary" style="border:1px solid var(--border-color);border-radius:var(--border-radius-md);padding:6px 4px 12px;margin-bottom:12px;background:var(--card-bg);">
				<div style="display:flex;flex-wrap:wrap;align-items:center;gap:4px;">
					${tile(__("Order Value"), fmt_currency(s.order_value, currency))}
					${tile(__("Delivered Value"), fmt_currency(s.delivered_value, currency), "#2e7d32")}
					${tile(__("Pending Value"), fmt_currency(s.pending_value, currency), "#e65100")}
					${tile(__("Completion"), `${pct}% ${__("Fulfilled")}`)}
				</div>
				<div style="padding:0 12px;">
					<div class="progress" style="height:10px;margin:4px 0 0;">
						<div class="progress-bar ${bar_class}" role="progressbar"
							style="width:${Math.max(0, Math.min(100, pct))}%;" aria-valuenow="${pct}"
							aria-valuemin="0" aria-valuemax="100"></div>
					</div>
				</div>
			</div>`;
	}

	function build_table_html(frm, settings) {
		const currency = frm.doc.currency;
		const show_oqty = cint(settings.show_original_qty);
		const show_oamt = cint(settings.show_original_amount);
		const highlight = cint(settings.highlight_completed_rows);

		const head = [
			`<th style="width:32px;">#</th>`,
			`<th>${__("Item")}</th>`,
			show_oqty ? `<th class="text-right">${__("Ordered Qty")}</th>` : "",
			`<th class="text-right">${__("Delivered Qty")}</th>`,
			`<th class="text-right">${__("Pending Qty")}</th>`,
			`<th>${__("UOM")}</th>`,
			show_oamt ? `<th class="text-right">${__("Ordered Amount")}</th>` : "",
			`<th class="text-right">${__("Pending Amount")}</th>`,
			`<th>${__("Status")}</th>`,
		].join("");

		const rows = (frm.doc.items || [])
			.map((row, i) => {
				const p = compute_item_pending(
					row.qty,
					row.delivered_qty,
					row.rate,
					settings.clamp_negative_pending
				);
				const st = STATUS_STYLE[p.status] || STATUS_STYLE["Not Started"];
				const bg = highlight ? `background:${st.bg};` : "";
				const item_label = `<strong>${esc(row.item_code)}</strong>${
					row.item_name && row.item_name !== row.item_code
						? `<div class="text-muted small">${esc(row.item_name)}</div>`
						: ""
				}`;
				const pill = `<span style="background:${st.bg};color:${st.fg};padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;white-space:nowrap;">${st.label}</span>`;

				return `<tr style="${bg}">
					<td class="text-muted">${row.idx || i + 1}</td>
					<td>${item_label}</td>
					${show_oqty ? `<td class="text-right">${fmt_qty(row.qty)}</td>` : ""}
					<td class="text-right">${fmt_qty(row.delivered_qty)}</td>
					<td class="text-right" style="font-weight:600;">${fmt_qty(p.pending_qty)}</td>
					<td>${esc(row.uom || row.stock_uom || "")}</td>
					${show_oamt ? `<td class="text-right">${fmt_currency(flt(row.qty) * flt(row.rate), currency)}</td>` : ""}
					<td class="text-right" style="font-weight:600;">${fmt_currency(p.pending_amount, currency)}</td>
					<td>${pill}</td>
				</tr>`;
			})
			.join("");

		return `
			<div class="oov-table" style="margin-bottom:16px;">
				<div style="font-weight:600;margin:0 0 6px 2px;">${__("Open Order View")} <span class="text-muted small">(${__("remaining customer demand")})</span></div>
				<div style="overflow-x:auto;">
					<table class="table table-bordered" style="margin:0;font-size:13px;">
						<thead><tr>${head}</tr></thead>
						<tbody>${rows}</tbody>
					</table>
				</div>
			</div>`;
	}

	function render(frm) {
		// Always clear our prior render first so repeated refreshes never stack.
		if (frm.__oov_wrapper) {
			frm.__oov_wrapper.remove();
			frm.__oov_wrapper = null;
		}

		const settings = get_settings();

		// Draft / cancelled Sales Orders behave exactly like stock ERPNext, and the
		// whole feature is behind the master switch.
		if (frm.doc.docstatus !== 1 || !cint(settings.enable_pending_view)) return;
		if (!frm.doc.items || !frm.doc.items.length) return;

		const $wrapper = $(
			`<div class="playground-open-order-view">
				${build_summary_html(frm, settings)}
				${build_table_html(frm, settings)}
			</div>`
		);

		// Anchor directly above the standard Items grid, which stays untouched as
		// the system of record. Fall back to the dashboard if the layout changes in
		// a future ERPNext version so we never hard-fail a form load.
		const items_field = frm.get_field("items");
		if (items_field && items_field.$wrapper && items_field.$wrapper.length) {
			$wrapper.insertBefore(items_field.$wrapper);
		} else if (frm.dashboard) {
			frm.dashboard.add_section($wrapper);
		}
		frm.__oov_wrapper = $wrapper;
	}

	// Expose for debugging / reuse.
	Object.assign(playground.open_order_view, {
		compute_item_pending,
		compute_summary,
		render,
	});

	frappe.ui.form.on("Sales Order", {
		refresh: render,
	});
})();
