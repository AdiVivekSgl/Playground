// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

const WPS_CREATE_PLAN_METHOD =
	"playground.playground.report.fg_stock_reservation_manager.fg_stock_reservation_manager.create_production_plan_from_snapshot";
const WPS_MR_EXCEL_METHOD =
	"playground.playground.production_plan_mr_excel.download_fgsrm_mr_excel";

function wps_esc(v) {
	return frappe.utils.escape_html(v == null ? "" : String(v));
}

function wps_download_mr_excel(pp_name) {
	const a = document.createElement("a");
	a.href = `/api/method/${WPS_MR_EXCEL_METHOD}?name=${encodeURIComponent(pp_name)}`;
	a.target = "_blank";
	a.rel = "noopener";
	document.body.appendChild(a);
	a.click();
	a.remove();
}

frappe.ui.form.on("Weekly Planning Snapshot", {
	refresh(frm) {
		// The native child grid is the data model only - both views are custom
		// rendered (so Hide Zero Rows filters in the render, not a flaky grid hide).
		frm.toggle_display("items", false);
		wps_bind_inputs(frm);
		wps_render(frm);

		// Create Prodn Plan: available on a draft AND after submission (builds from
		// the frozen Committed Prodn).
		if ((frm.doc.items || []).length) {
			frm.add_custom_button(__("Create Prodn Plan"), () => wps_create_prodn_plan(frm));
		}
	},

	view_mode(frm) {
		wps_render(frm);
	},

	hide_zero_rows(frm) {
		wps_render(frm);
	},
});

frappe.ui.form.on("Weekly Planning Snapshot Item", {
	committed_prodn(frm) {
		if (frm._wps_allocating) return; // our own writes re-render explicitly
		wps_render(frm);
	},
});

// ─────────────────────────────────────────────────────────────────────────
// Rendering (single HTML field hosts whichever view is active)
// ─────────────────────────────────────────────────────────────────────────
function wps_render(frm) {
	if (frm.doc.view_mode === "Consolidated") wps_render_consolidated(frm);
	else wps_render_detailed(frm);
}

function wps_render_detailed(frm) {
	const field = frm.get_field("consolidated_requirement_html");
	if (!field) return;
	const editable = frm.doc.docstatus === 0;
	const hideZero = frm.doc.hide_zero_rows;

	const rows = (frm.doc.items || []).slice().sort((a, b) => {
		if ((a.item_code || "") !== (b.item_code || "")) return (a.item_code || "").localeCompare(b.item_code || "");
		if ((a.is_buffer ? 1 : 0) !== (b.is_buffer ? 1 : 0)) return (a.is_buffer ? 1 : 0) - (b.is_buffer ? 1 : 0);
		return (a.so_date || "9999-12-31").localeCompare(b.so_date || "9999-12-31");
	});

	const body = rows
		.filter((d) => !hideZero || flt(d.committed_prodn) > 0)
		.map((d) => {
			const com = editable
				? `<input type="number" step="any" data-wps-row="${wps_esc(d.name)}" value="${flt(d.committed_prodn)}" class="form-control input-sm" style="width:100px;text-align:right;">`
				: `<b>${format_number(flt(d.committed_prodn))}</b>`;
			return (
				`<tr><td>${wps_esc(d.item_code)}</td><td>${wps_esc(d.item_name)}</td><td>${wps_esc(d.customer)}</td>` +
				`<td>${d.so_date ? frappe.datetime.str_to_user(d.so_date) : ""}</td>` +
				`<td style="text-align:right">${format_number(flt(d.pending_qty))}</td>` +
				`<td style="text-align:right">${format_number(flt(d.reserved_qty))}</td>` +
				`<td style="text-align:right">${format_number(flt(d.item_free_stock))}</td>` +
				`<td style="text-align:right">${format_number(flt(d.suggested_prodn))}</td>` +
				`<td style="text-align:right">${com}</td></tr>`
			);
		})
		.join("");
	const total = rows.reduce((s, d) => s + flt(d.committed_prodn), 0);

	field.$wrapper.html(
		`<table class="table table-bordered" style="font-size:12px;"><thead><tr>` +
			`<th>${__("Item")}</th><th>${__("Item Name")}</th><th>${__("Customer")}</th><th>${__("Dispatch Priority Date")}</th>` +
			`<th style="text-align:right">${__("Pending")}</th><th style="text-align:right">${__("Reserved")}</th>` +
			`<th style="text-align:right">${__("Item Free Stock")}</th><th style="text-align:right">${__("Suggested")}</th>` +
			`<th style="text-align:right">${__("Committed Prodn")}</th></tr></thead>` +
			`<tbody>${body}</tbody>` +
			`<tfoot><tr><th colspan="8" style="text-align:right">${__("Total Committed Prodn")}</th>` +
			`<th style="text-align:right">${format_number(total)}</th></tr></tfoot></table>`
	);
}

function wps_render_consolidated(frm) {
	const field = frm.get_field("consolidated_requirement_html");
	if (!field) return;
	const editable = frm.doc.docstatus === 0;
	const hideZero = frm.doc.hide_zero_rows;

	const by = {};
	const order = [];
	(frm.doc.items || []).forEach((d) => {
		if (!(d.item_code in by)) {
			by[d.item_code] = { name: d.item_name || "", free: flt(d.item_free_stock), sug: 0, com: 0 };
			order.push(d.item_code);
		}
		by[d.item_code].sug += flt(d.suggested_prodn);
		by[d.item_code].com += flt(d.committed_prodn);
	});

	const body = order
		.filter((ic) => !hideZero || by[ic].com > 0)
		.map((ic) => {
			const r = by[ic];
			const com = editable
				? `<input type="number" step="any" data-wps-item="${wps_esc(ic)}" value="${r.com}" class="form-control input-sm" style="width:110px;text-align:right;">`
				: `<b>${format_number(r.com)}</b>`;
			return (
				`<tr><td>${wps_esc(ic)}</td><td>${wps_esc(r.name)}</td>` +
				`<td style="text-align:right">${format_number(r.free)}</td>` +
				`<td style="text-align:right">${format_number(r.sug)}</td>` +
				`<td style="text-align:right">${com}</td></tr>`
			);
		})
		.join("");
	const total = order.reduce((s, ic) => s + by[ic].com, 0);

	field.$wrapper.html(
		`<table class="table table-bordered" style="font-size:12px;"><thead><tr>` +
			`<th>${__("Item")}</th><th>${__("Item Name")}</th><th style="text-align:right">${__("Item Free Stock")}</th>` +
			`<th style="text-align:right">${__("Total Suggested")}</th><th style="text-align:right">${__("Committed Prodn")}</th>` +
			`</tr></thead><tbody>${body}</tbody>` +
			`<tfoot><tr><th colspan="4" style="text-align:right">${__("Total Committed Prodn")}</th>` +
			`<th style="text-align:right">${format_number(total)}</th></tr></tfoot></table>` +
			(editable
				? `<p class="text-muted small">${__("Editing an item's Committed Prodn allocates it to that item's Sales Order lines by Dispatch Priority Date (earliest first); any surplus beyond the requirement becomes a 'Buffer' row.")}</p>`
				: "")
	);
}

// ─────────────────────────────────────────────────────────────────────────
// Editing (writes back into the child table - the single source of truth)
// ─────────────────────────────────────────────────────────────────────────
function wps_bind_inputs(frm) {
	const field = frm.get_field("consolidated_requirement_html");
	if (!field || field._wps_bound) return;
	field._wps_bound = true;
	field.$wrapper.on("change", "input[data-wps-item]", function () {
		wps_allocate(frm, $(this).attr("data-wps-item"), flt($(this).val()));
	});
	field.$wrapper.on("change", "input[data-wps-row]", function () {
		wps_set_line(frm, $(this).attr("data-wps-row"), flt($(this).val()));
	});
}

// Detailed edit - set one line's Committed Prodn directly.
function wps_set_line(frm, rowname, val) {
	const d = (frm.doc.items || []).find((x) => x.name === rowname);
	if (!d) return;
	frm._wps_allocating = true;
	frappe.model.set_value(d.doctype, d.name, "committed_prodn", Math.max(0, flt(val)));
	frm._wps_allocating = false;
	frm.dirty();
	wps_render(frm);
}

// Consolidated edit - allocate an item's total Committed across its SO lines by
// Dispatch Priority Date (earliest first, capped at each line's Suggested Prodn);
// any surplus becomes/updates a single "Buffer" row for the item.
function wps_allocate(frm, item_code, new_total) {
	const real = (frm.doc.items || [])
		.filter((d) => d.item_code === item_code && !d.is_buffer)
		.sort((a, b) => (a.so_date || "9999-12-31").localeCompare(b.so_date || "9999-12-31"));

	let remaining = Math.max(0, flt(new_total));
	const alloc = real.map((d) => {
		const give = Math.min(flt(d.suggested_prodn), remaining);
		remaining -= give;
		return give;
	});
	const surplus = remaining; // beyond the SO lines' requirement

	frm._wps_allocating = true;
	real.forEach((d, i) => frappe.model.set_value(d.doctype, d.name, "committed_prodn", flt(alloc[i])));

	let buffer = (frm.doc.items || []).find((d) => d.item_code === item_code && d.is_buffer);
	if (surplus > 0.0001) {
		if (buffer) {
			frappe.model.set_value(buffer.doctype, buffer.name, "committed_prodn", flt(surplus));
		} else {
			const nm = real[0] || {};
			frm.add_child("items", {
				item_code: item_code,
				item_name: nm.item_name || "",
				customer: "Buffer",
				is_buffer: 1,
				suggested_prodn: 0,
				committed_prodn: flt(surplus),
			});
		}
	} else if (buffer) {
		const idx = frm.doc.items.findIndex((d) => d.name === buffer.name);
		if (idx > -1) frm.doc.items.splice(idx, 1);
	}
	frm._wps_allocating = false;

	frm.refresh_field("items");
	frm.dirty();
	wps_render(frm);
}

// ─────────────────────────────────────────────────────────────────────────
function wps_create_prodn_plan(frm) {
	frappe.confirm(
		__("Create a draft Production Plan from this snapshot's itemwise Committed Prodn? It builds the full nested plan chain and raw materials, then downloads the Production Plan workbook. Save any edits first."),
		() => {
			frappe.call({
				method: WPS_CREATE_PLAN_METHOD,
				args: { snapshot: frm.doc.name },
				freeze: true,
				freeze_message: __("Creating Production Plan…"),
				callback(r) {
					const m = r.message;
					if (!m || !m.name) return;
					if (m.handed_off) {
						frappe.show_alert({
							message: __("Production Plan {0}: {1} item(s), {2} raw material line(s), full chain built. Downloading workbook…", [
								m.name,
								m.items,
								m.raw_materials,
							]),
							indicator: "green",
						});
						wps_download_mr_excel(m.name);
					} else {
						frappe.show_alert({
							message: __("Draft Production Plan {0} created with {1} item(s). Open it and click “Create Full Chain”, then download the workbook.", [
								m.name,
								m.items,
							]),
							indicator: "blue",
						});
						frappe.set_route("Form", "Production Plan", m.name);
					}
				},
			});
		}
	);
}
