// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

const FGSRM_METHOD_PATH =
	"playground.playground.report.fg_stock_reservation_manager.fg_stock_reservation_manager";

// Playground's own 3-sheet workbook (FGSRM view / FG Requirement / RM Component
// Shortage) for a created Production Plan. Separate from frontec's MR Hierarchy
// Excel, which is left untouched and still available from the plan form.
const FGSRM_MR_EXCEL_METHOD =
	"playground.playground.production_plan_mr_excel.download_fgsrm_mr_excel";

// Per-user manual requirements (FGSRM Manual Requirement) - free-form or
// cherry-picked from open Blanket Orders / Quotations. See fgsrm_manual_requirement.py.
const FGSRM_MR_METHOD_PATH = "playground.playground.fgsrm_manual_requirement";

// Trigger a file download without navigating away. An <a> click (rather than
// window.open) avoids the pop-up blocker that can swallow a window.open fired
// from an async callback; the attachment Content-Disposition means the browser
// downloads instead of opening a tab. Passes the current FGSRM filters so the
// workbook's "FGSRM" sheet reflects the same view that produced the plan.
function fgsrm_download_mr_excel(pp_name, filters_json) {
	let url = `/api/method/${FGSRM_MR_EXCEL_METHOD}?name=${encodeURIComponent(pp_name)}`;
	if (filters_json) url += `&filters=${encodeURIComponent(filters_json)}`;
	const a = document.createElement("a");
	a.href = url;
	a.target = "_blank";
	a.rel = "noopener";
	document.body.appendChild(a);
	a.click();
	a.remove();
}

// The report rows currently ticked in the DataTable (as data objects).
// NOTE: getCheckedRows lives on datatable.rowmanager, not on the datatable
// object itself — calling dt.getCheckedRows() silently returns undefined,
// which meant this always reported "nothing selected" even with rows ticked.
function fgsrm_checked_rows() {
	const dt = frappe.query_report.datatable;
	const data = frappe.query_report.data || [];
	if (!dt || !dt.rowmanager || !dt.rowmanager.getCheckedRows) return [];
	return (dt.rowmanager.getCheckedRows() || []).map((i) => data[i]).filter(Boolean);
}

// Calls create_reservations and, if the server reports any `blocked` items
// (ERPNext's native reservation rejected a request our own cap allowed - see
// create_reservations' docstring for why "Only Displayed SOs" basis can do
// this), shows a dialog listing the OTHER reservations holding that item so
// the user can cancel one and retry, instead of a bare failure.
function fgsrm_call_create_reservations(rows, filters_json) {
	frappe.call({
		method: `${FGSRM_METHOD_PATH}.create_reservations`,
		args: { rows: JSON.stringify(rows), filters: filters_json },
		freeze: true,
		freeze_message: __("Creating reservations…"),
		callback(r) {
			const m = r.message || {};
			frappe.show_alert({
				message: __("Reserved {0} line(s){1}.", [
					m.created || 0,
					m.capped ? __(", {0} capped at free stock", [m.capped]) : "",
				]),
				indicator: "green",
			});
			frappe.query_report.refresh();
			if (m.blocked && Object.keys(m.blocked).length) {
				fgsrm_show_blocked_dialog(m.blocked, rows, filters_json);
			}
		},
	});
}

function fgsrm_show_blocked_dialog(blocked, retry_rows, filters_json) {
	const item_codes = Object.keys(blocked);
	if (!item_codes.length) return;

	const rows_html = item_codes
		.flatMap((item_code) =>
			(blocked[item_code] || []).map(
				(res) => `
				<tr>
					<td><input type="checkbox" data-sre="${res.name}" /></td>
					<td>${frappe.utils.escape_html(item_code)}</td>
					<td>${frappe.utils.escape_html(res.voucher_no || "")}</td>
					<td style="text-align:right">${res.reserved_qty}</td>
				</tr>`
			)
		)
		.join("");

	const html = `
		<p>${__("Some lines couldn't be reserved — ERPNext found stock already committed to these other reservations. Tick any to cancel, then retry.")}</p>
		<table class="table table-bordered" style="font-size:12px;">
			<thead>
				<tr><th></th><th>${__("Item")}</th><th>${__("Sales Order")}</th><th style="text-align:right">${__("Reserved Qty")}</th></tr>
			</thead>
			<tbody>${rows_html}</tbody>
		</table>`;

	const dialog = new frappe.ui.Dialog({
		title: __("Reservation Blocked — Existing Reservations"),
		size: "large",
		fields: [{ fieldtype: "HTML", options: html }],
		primary_action_label: __("Cancel Selected & Retry"),
		primary_action: () => {
			const checked = Array.from(
				dialog.$wrapper[0].querySelectorAll("input[data-sre]:checked")
			).map((el) => el.getAttribute("data-sre"));

			if (!checked.length) {
				frappe.msgprint(__("Tick at least one reservation to cancel."));
				return;
			}

			frappe.call({
				method: `${FGSRM_METHOD_PATH}.cancel_reservations`,
				args: { sre_names: JSON.stringify(checked) },
				freeze: true,
				freeze_message: __("Cancelling…"),
				callback() {
					dialog.hide();
					fgsrm_call_create_reservations(retry_rows, filters_json);
				},
			});
		},
	});
	dialog.show();
}

// ── Manual Requirements ──────────────────────────────────────────────────────
// Add finished-goods demand by hand: free-form (item + qty) or cherry-picked
// line-by-line from an open Blanket Order / Quotation. Persisted per-user in the
// FGSRM Manual Requirement doctype (fgsrm_manual_requirement.py); appended at the
// bottom of the report until removed / cleared. Demand-only - stock is never
// reserved against a Blanket Order / Quotation - so these rows carry no
// reservation actions.

// Step 1: choose the source, then branch to free-form or the document picker.
function fgsrm_add_requirement() {
	frappe.prompt(
		[
			{
				fieldname: "mode",
				label: __("Add From"),
				fieldtype: "Select",
				options: ["Free-form", "Blanket Order", "Quotation"].join("\n"),
				default: "Free-form",
				reqd: 1,
			},
		],
		({ mode }) => (mode === "Free-form" ? fgsrm_add_freeform() : fgsrm_add_from_source(mode)),
		__("Add Manual Requirement"),
		__("Next")
	);
}

function fgsrm_add_freeform() {
	frappe.prompt(
		[
			{ fieldname: "item_code", label: __("Item"), fieldtype: "Link", options: "Item", reqd: 1 },
			{ fieldname: "qty", label: __("Qty"), fieldtype: "Float", reqd: 1 },
			{ fieldname: "customer", label: __("Customer (optional)"), fieldtype: "Link", options: "Customer" },
			{ fieldname: "remarks", label: __("Remarks (optional)"), fieldtype: "Small Text" },
		],
		(v) => {
			frappe.call({
				method: `${FGSRM_MR_METHOD_PATH}.add_manual_requirement`,
				args: {
					item_code: v.item_code,
					qty: v.qty,
					customer: v.customer || null,
					remarks: v.remarks || null,
				},
				freeze: true,
				freeze_message: __("Adding requirement…"),
				callback() {
					frappe.show_alert({ message: __("Requirement added."), indicator: "green" });
					frappe.query_report.refresh();
				},
			});
		},
		__("Free-form Requirement"),
		__("Add")
	);
}

// Step 2 (source path): pick the document, fetch its open lines, then let the
// user tick lines and adjust qty (defaulted to the open qty) before adding.
function fgsrm_add_from_source(source_type) {
	frappe.prompt(
		[{ fieldname: "source_document", label: source_type, fieldtype: "Link", options: source_type, reqd: 1 }],
		({ source_document }) => {
			frappe.call({
				method: `${FGSRM_MR_METHOD_PATH}.get_open_source_lines`,
				args: { source_type, source_document },
				freeze: true,
				freeze_message: __("Fetching open lines…"),
				callback(r) {
					const lines = r.message || [];
					if (!lines.length) {
						frappe.msgprint(__("No open lines on {0} {1}.", [source_type, source_document]));
						return;
					}
					fgsrm_pick_source_lines(source_type, source_document, lines);
				},
			});
		},
		__("Pick {0}", [source_type]),
		__("Next")
	);
}

function fgsrm_pick_source_lines(source_type, source_document, lines) {
	const rows_html = lines
		.map(
			(ln, i) => `
			<tr>
				<td><input type="checkbox" data-i="${i}" checked /></td>
				<td>${frappe.utils.escape_html(ln.item_code)}</td>
				<td>${frappe.utils.escape_html(ln.item_name || "")}</td>
				<td style="text-align:right">${ln.open_qty}</td>
				<td><input type="number" class="form-control input-xs" data-qty="${i}" value="${ln.open_qty}" min="0" step="any" style="text-align:right;width:100px;" /></td>
			</tr>`
		)
		.join("");

	const html = `
		<p>${__("Tick the lines to add and adjust the qty (defaults to the open quantity).")}</p>
		<table class="table table-bordered" style="font-size:12px;">
			<thead><tr>
				<th></th><th>${__("Item")}</th><th>${__("Item Name")}</th>
				<th style="text-align:right">${__("Open Qty")}</th><th style="text-align:right">${__("Add Qty")}</th>
			</tr></thead>
			<tbody>${rows_html}</tbody>
		</table>`;

	const dialog = new frappe.ui.Dialog({
		title: __("{0}: {1}", [source_type, source_document]),
		size: "large",
		fields: [{ fieldtype: "HTML", options: html }],
		primary_action_label: __("Add Selected"),
		primary_action() {
			const wrap = dialog.$wrapper[0];
			const picks = [];
			wrap.querySelectorAll('input[type="checkbox"][data-i]:checked').forEach((cb) => {
				const i = cb.getAttribute("data-i");
				const qtyEl = wrap.querySelector(`input[data-qty="${i}"]`);
				const qty = flt(qtyEl && qtyEl.value);
				if (qty > 0) {
					picks.push({ source_item: lines[i].source_item, item_code: lines[i].item_code, qty });
				}
			});
			if (!picks.length) {
				frappe.msgprint(__("Tick at least one line with a qty greater than zero."));
				return;
			}
			frappe.call({
				method: `${FGSRM_MR_METHOD_PATH}.add_source_requirements`,
				args: { source_type, source_document, lines: JSON.stringify(picks) },
				freeze: true,
				freeze_message: __("Adding requirements…"),
				callback(r) {
					const m = r.message || {};
					dialog.hide();
					frappe.show_alert({ message: __("Added {0} requirement(s).", [m.added || 0]), indicator: "green" });
					frappe.query_report.refresh();
				},
			});
		},
	});
	dialog.show();
}

// ─────────────────────────────────────────────────────────────────────────────
// Dashboard tab
//
// Self-contained on purpose. Everything the Dashboard needs - fetch, render and
// the tab chrome - lives in this one object and talks to the server through a
// SINGLE whitelisted endpoint that returns a finished payload. It reads report
// state only through frappe.query_report.get_filter_values() and never touches
// execute()'s data, so the table view (reservations, Create Prodn Plan) cannot
// regress. To lift this into a standalone Dashboard page later, swap mount()'s
// host element and get_filters() for that page's own - nothing else changes.
// ─────────────────────────────────────────────────────────────────────────────
const FGSRM_DASHBOARD_METHOD = "playground.playground.fgsrm_dashboard.get_dashboard_metrics";

// Accent per indicator, used for the card's left rule and the list badges.
// Colour only ever carries emphasis here - every card is also labelled - so this
// stays readable in both themes and without colour vision.
const FGSRM_DASH_ACCENT = {
	green: "#199e70",
	orange: "#eb8c34",
	red: "#e03e52",
	blue: "#2a78d6",
	grey: "#8d99a6",
};

const FGSRM_DASH = {
	active: false,
	// Sequence number of the newest in-flight metrics request (see refresh).
	token: 0,

	// ── formatting helpers ────────────────────────────────────────────────
	money(v, currency) {
		try {
			return format_currency(flt(v), currency);
		} catch (e) {
			return flt(v).toFixed(2);
		}
	},

	qty(v) {
		const n = flt(v);
		try {
			return format_number(n, null, Number.isInteger(n) ? 0 : 2);
		} catch (e) {
			return String(n);
		}
	},

	esc(v) {
		return frappe.utils.escape_html(v == null ? "" : String(v));
	},

	// ── mount / tabs ──────────────────────────────────────────────────────
	// Idempotent: safe to call on every run. Frappe re-renders the datatable in
	// place on refresh, so the injected nodes survive, but re-asserting costs
	// nothing and covers any future teardown.
	mount(report) {
		const $main = $(report.page.main);
		this.inject_styles();

		let $tabs = $main.find(".fgsrm-tabs");
		let $dash = $main.find(".fgsrm-dash");

		if (!$tabs.length) {
			$tabs = $(`
				<div class="fgsrm-tabs">
					<button class="fgsrm-tab active" data-tab="table">${__("Table")}</button>
					<button class="fgsrm-tab" data-tab="dashboard">${__("Dashboard")}</button>
				</div>
			`);
			$dash = $('<div class="fgsrm-dash" style="display:none;"></div>');
			$main.prepend($dash);
			$main.prepend($tabs);

			$tabs.on("click", ".fgsrm-tab", (e) => {
				this.activate($(e.currentTarget).data("tab"));
			});

			this.bind_actions($dash);
		}

		// Settle directly above the report body, i.e. BELOW the filter row (the
		// filters drive both tabs, so they belong above the tab strip).
		// .report-wrapper doesn't exist yet at onload, so the first mount leaves
		// the tabs at the top of the page and the first run slides them into
		// place. Self-correcting and idempotent - safe to call on every run.
		const $anchor = $main.find(".report-wrapper").first();
		if ($anchor.length && $dash.next()[0] !== $anchor[0]) {
			$tabs.insertBefore($anchor);
			$dash.insertBefore($anchor);
		}
	},

	activate(tab) {
		const $main = $(frappe.query_report.page.main);
		const dashboard = tab === "dashboard";
		this.active = dashboard;

		$main.find(".fgsrm-tab").removeClass("active");
		$main.find(`.fgsrm-tab[data-tab="${tab}"]`).addClass("active");

		if (dashboard) {
			this.hide_report_ui($main);
		} else {
			this.show_report_ui($main);
		}
		$main.find(".fgsrm-dash").toggle(dashboard);

		if (dashboard) {
			this.refresh();
		} else {
			// The DataTable sizes its columns at render time. If the report ran
			// while it was hidden, its widths can be stale - a resize event makes
			// it re-measure WITHOUT re-rendering, so in-progress Reserve Qty edits
			// and row selections survive the tab switch.
			window.dispatchEvent(new Event("resize"));
		}
	},

	// The report-owned children the tabs swap between. .page-form is EXCLUDED
	// deliberately - that's the filter row, which drives both tabs and must stay
	// on screen; hiding it would strand the user on a dashboard they can't
	// re-filter.
	report_children($main) {
		return $main.children().not(".fgsrm-tabs").not(".fgsrm-dash").not(".page-form");
	},

	// Hide the report's own UI (summary, chart, message, datatable) without
	// naming their selectors, so this survives Frappe renaming them.
	//
	// Frappe keeps .report-summary / .chart-wrapper / the message div hidden
	// unless they have content, so we can't just show() everything on the way
	// back - that would reveal empty containers the report meant to keep hidden.
	// Instead we remember each element's report-owned visibility. Any element
	// seen VISIBLE is recorded as such (that's the report's intent, including
	// when a re-run newly populates one); an already-hidden element keeps its
	// first recorded state, so our own hiding never gets mistaken for the
	// report's.
	hide_report_ui($main) {
		this.report_children($main)
			.each(function () {
				const $el = $(this);
				if ($el.css("display") !== "none") {
					$el.data("fgsrmOwnDisplay", "shown");
				} else if ($el.data("fgsrmOwnDisplay") === undefined) {
					$el.data("fgsrmOwnDisplay", "hidden");
				}
				$el.hide();
			});
	},

	show_report_ui($main) {
		this.report_children($main)
			.each(function () {
				const $el = $(this);
				const own = $el.data("fgsrmOwnDisplay");
				$el.removeData("fgsrmOwnDisplay");
				// Unrecorded means it appeared while we were on the dashboard —
				// show it, since the report only creates what it intends to use.
				if (own !== "hidden") $el.show();
			});
	},

	// ── data ──────────────────────────────────────────────────────────────
	refresh() {
		if (!this.active) return;
		const $dash = $(frappe.query_report.page.main).find(".fgsrm-dash");
		if (!$dash.length) return;

		// Changing a filter can start a second fetch before the first returns.
		// Only the newest request is allowed to paint, so a slow earlier response
		// can't land on top of fresher numbers.
		const token = (this.token = (this.token || 0) + 1);

		$dash.html(`<div class="fgsrm-dash-msg">${__("Loading dashboard…")}</div>`);

		frappe.call({
			method: FGSRM_DASHBOARD_METHOD,
			args: { filters: JSON.stringify(frappe.query_report.get_filter_values()) },
			callback: (r) => {
				if (token !== this.token || !this.active) return;
				if (!r.message) {
					$dash.html(`<div class="fgsrm-dash-msg">${__("No data for these filters.")}</div>`);
					return;
				}
				$dash.html(this.render(r.message));
			},
			error: () => {
				if (token !== this.token || !this.active) return;
				$dash.html(`<div class="fgsrm-dash-msg">${__("Could not load the dashboard.")}</div>`);
			},
		});
	},

	// ── click-through ─────────────────────────────────────────────────────
	// A number should never be a dead end: cards and list rows push the user back
	// into the table, pre-filtered to exactly the set they just clicked.
	bind_actions($dash) {
		$dash.on("click", "[data-view-mode]", (e) => {
			frappe.query_report.set_filter_value("view_mode", $(e.currentTarget).data("view-mode") || "");
			this.activate("table");
		});

		$dash.on("click", "[data-filter-item]", (e) => {
			frappe.query_report.set_filter_value("item_code", $(e.currentTarget).data("filter-item"));
			this.activate("table");
		});

		$dash.on("click", "[data-filter-so]", (e) => {
			frappe.query_report.set_filter_value("sales_order", $(e.currentTarget).data("filter-so"));
			this.activate("table");
		});

		// The SO number itself opens the document, rather than filtering.
		$dash.on("click", ".fgsrm-so-link", (e) => {
			e.stopPropagation();
			frappe.set_route("Form", "Sales Order", $(e.currentTarget).data("so"));
		});
	},

	// ── render ────────────────────────────────────────────────────────────
	render(d) {
		return [
			this.render_cards(d),
			this.render_ageing(d),
			`<div class="fgsrm-dash-lists">
				${this.render_blocking(d)}
				${this.render_overdue(d)}
			</div>`,
			`<div class="fgsrm-dash-foot">${__("As on {0} · Overdue measured against Date Basis: {1}", [
				this.esc(d.as_on),
				this.esc(d.date_basis),
			])}</div>`,
		].join("");
	},

	render_cards(d) {
		const cards = (d.cards || [])
			.map((c) => {
				const accent = FGSRM_DASH_ACCENT[c.indicator] || FGSRM_DASH_ACCENT.grey;
				const clickable = c.action && c.action.type === "view";
				const attrs = clickable
					? `data-view-mode="${this.esc(c.action.view_mode)}" role="button" tabindex="0"`
					: "";

				const meta = [];
				if (c.qty != null) meta.push(`${this.qty(c.qty)} ${__("qty")}`);
				if (c.count != null) meta.push(__("{0} SO", [c.count]));
				const secondary = c.secondary
					? `<div class="fgsrm-card-sec">${this.esc(c.secondary.label)}: ${this.money(
							c.secondary.value,
							d.currency
					  )}</div>`
					: "";

				return `
					<div class="fgsrm-card${clickable ? " is-clickable" : ""}"
						style="border-left-color:${accent};"
						title="${this.esc(c.hint)}" ${attrs}>
						<div class="fgsrm-card-label">${this.esc(c.label)}</div>
						<div class="fgsrm-card-value">${this.money(c.value, d.currency)}</div>
						<div class="fgsrm-card-meta">${meta.join(" · ")}</div>
						${secondary}
					</div>`;
			})
			.join("");

		return `<div class="fgsrm-cards">${cards}</div>`;
	},

	render_ageing(d) {
		const buckets = d.ageing || [];
		const total = buckets.reduce((sum, b) => sum + flt(b.value), 0);
		if (!total) return "";

		const segments = buckets
			.filter((b) => flt(b.value) > 0)
			.map((b) => {
				const pct = (flt(b.value) / total) * 100;
				const accent = FGSRM_DASH_ACCENT[b.indicator] || FGSRM_DASH_ACCENT.grey;
				return `<div class="fgsrm-seg" style="width:${pct}%;background:${accent};"
					title="${this.esc(b.label)}: ${this.money(b.value, d.currency)} (${pct.toFixed(1)}%)"></div>`;
			})
			.join("");

		const legend = buckets
			.map((b) => {
				const accent = FGSRM_DASH_ACCENT[b.indicator] || FGSRM_DASH_ACCENT.grey;
				return `<span class="fgsrm-legend-item">
					<span class="fgsrm-dot" style="background:${accent};"></span>
					${this.esc(b.label)} — <strong>${this.money(b.value, d.currency)}</strong>
					<span class="fgsrm-muted">(${b.count})</span>
				</span>`;
			})
			.join("");

		return `
			<div class="fgsrm-panel">
				<div class="fgsrm-panel-title">${__("Order Book by Dispatch Window")}</div>
				<div class="fgsrm-bar">${segments}</div>
				<div class="fgsrm-legend">${legend}</div>
			</div>`;
	},

	render_blocking(d) {
		const rows = d.blocking_items || [];
		const total = (d.counts || {}).blocking_items_total || 0;

		const body = rows.length
			? rows
					.map(
						(r) => `
				<tr data-filter-item="${this.esc(r.item_code)}">
					<td>
						<div class="fgsrm-strong">${this.esc(r.item_name || r.item_code)}</div>
						<div class="fgsrm-muted">${this.esc(r.item_code)}</div>
					</td>
					<td class="fgsrm-center"><span class="fgsrm-badge fgsrm-badge-red">${r.blocked_sos}</span></td>
					<td class="fgsrm-right">${this.qty(r.short_qty)}</td>
					<td class="fgsrm-right">${this.qty(r.free_stock)}</td>
					<td class="fgsrm-right fgsrm-strong">${this.qty(r.to_produce)}</td>
					<td class="fgsrm-right">${this.money(r.value_at_risk, d.currency)}</td>
				</tr>`
					)
					.join("")
			: `<tr><td colspan="6" class="fgsrm-empty">${__("Nothing is blocking dispatch — no item needs production.")}</td></tr>`;

		return `
			<div class="fgsrm-panel">
				<div class="fgsrm-panel-title">
					${__("Top Blocking Items")}
					<span class="fgsrm-muted">${__("holding up dispatch · showing {0} of {1}", [rows.length, total])}</span>
				</div>
				<table class="fgsrm-table">
					<thead>
						<tr>
							<th>${__("Item")}</th>
							<th class="fgsrm-center">${__("SOs Blocked")}</th>
							<th class="fgsrm-right">${__("Short Qty")}</th>
							<th class="fgsrm-right">${__("Free Stock")}</th>
							<th class="fgsrm-right">${__("To Produce")}</th>
							<th class="fgsrm-right">${__("Value at Risk")}</th>
						</tr>
					</thead>
					<tbody>${body}</tbody>
				</table>
			</div>`;
	},

	render_overdue(d) {
		const rows = d.overdue_sos || [];
		const total = (d.counts || {}).overdue_sos_total || 0;

		const bucket_label = {
			ready: [__("Ready"), "green"],
			cover: [__("Reserve now"), "orange"],
			produce: [__("Needs production"), "red"],
		};

		const body = rows.length
			? rows
					.map((r) => {
						const [label, tone] = bucket_label[r.bucket] || [r.bucket, "grey"];
						return `
				<tr data-filter-so="${this.esc(r.sales_order)}">
					<td>
						<div class="fgsrm-strong fgsrm-so-link" data-so="${this.esc(r.sales_order)}">${this.esc(r.sales_order)}</div>
						<div class="fgsrm-muted">${this.esc(r.customer_name)}</div>
					</td>
					<td class="fgsrm-center"><span class="fgsrm-badge fgsrm-badge-red">${r.days_overdue}</span></td>
					<td><span class="fgsrm-badge fgsrm-badge-${tone}">${this.esc(label)}</span></td>
					<td>${this.esc(r.material_status || "")}</td>
					<td class="fgsrm-right">${this.qty(r.pending_qty)}</td>
					<td class="fgsrm-right fgsrm-strong">${this.money(r.pending_value, d.currency)}</td>
				</tr>`;
					})
					.join("")
			: `<tr><td colspan="6" class="fgsrm-empty">${__("Nothing overdue on this Date Basis.")}</td></tr>`;

		return `
			<div class="fgsrm-panel">
				<div class="fgsrm-panel-title">
					${__("Overdue Sales Orders")}
					<span class="fgsrm-muted">${__("showing {0} of {1}", [rows.length, total])}</span>
				</div>
				<table class="fgsrm-table">
					<thead>
						<tr>
							<th>${__("Sales Order")}</th>
							<th class="fgsrm-center">${__("Days")}</th>
							<th>${__("State")}</th>
							<th>${__("Material Status")}</th>
							<th class="fgsrm-right">${__("Pending Qty")}</th>
							<th class="fgsrm-right">${__("Pending Value")}</th>
						</tr>
					</thead>
					<tbody>${body}</tbody>
				</table>
			</div>`;
	},

	// Injected once. Everything is driven by Frappe's CSS variables so the
	// dashboard follows the active (light or dark) theme rather than assuming one.
	inject_styles() {
		if (document.getElementById("fgsrm-dash-styles")) return;
		const style = document.createElement("style");
		style.id = "fgsrm-dash-styles";
		style.textContent = `
			.fgsrm-tabs { display:flex; gap:4px; margin:0 0 12px; border-bottom:1px solid var(--border-color,#d1d8dd); }
			.fgsrm-tab { background:none; border:none; border-bottom:2px solid transparent; padding:8px 16px;
				font-size:13px; font-weight:600; color:var(--text-muted,#8d99a6); cursor:pointer; }
			.fgsrm-tab:hover { color:var(--text-color,#1f272e); }
			.fgsrm-tab.active { color:var(--text-color,#1f272e); border-bottom-color:var(--primary,#2a78d6); }
			.fgsrm-dash-msg { padding:32px; text-align:center; color:var(--text-muted,#8d99a6); font-size:13px; }
			.fgsrm-cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:12px; margin-bottom:16px; }
			.fgsrm-card { background:var(--card-bg,var(--fg-color,#fff)); border:1px solid var(--border-color,#d1d8dd);
				border-left-width:3px; border-radius:6px; padding:12px 14px; }
			.fgsrm-card.is-clickable { cursor:pointer; }
			.fgsrm-card.is-clickable:hover { border-color:var(--primary,#2a78d6); }
			.fgsrm-card-label { font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:.3px;
				color:var(--text-muted,#8d99a6); margin-bottom:6px; }
			.fgsrm-card-value { font-size:19px; font-weight:700; color:var(--heading-color,var(--text-color,#1f272e));
				line-height:1.25; overflow-wrap:anywhere; }
			.fgsrm-card-meta, .fgsrm-card-sec { font-size:11px; color:var(--text-muted,#8d99a6); margin-top:4px; }
			.fgsrm-panel { background:var(--card-bg,var(--fg-color,#fff)); border:1px solid var(--border-color,#d1d8dd);
				border-radius:6px; padding:12px 14px; margin-bottom:16px; }
			.fgsrm-panel-title { font-size:13px; font-weight:600; color:var(--heading-color,var(--text-color,#1f272e));
				margin-bottom:10px; display:flex; gap:8px; align-items:baseline; flex-wrap:wrap; }
			.fgsrm-bar { display:flex; height:10px; border-radius:5px; overflow:hidden; background:var(--control-bg,#f4f5f6); }
			.fgsrm-seg { height:100%; }
			.fgsrm-legend { display:flex; flex-wrap:wrap; gap:14px; margin-top:10px; font-size:12px; }
			.fgsrm-legend-item { display:inline-flex; align-items:center; gap:5px; }
			.fgsrm-dot { width:9px; height:9px; border-radius:50%; display:inline-block; }
			.fgsrm-dash-lists { display:grid; grid-template-columns:repeat(auto-fit,minmax(430px,1fr)); gap:16px; }
			.fgsrm-table { width:100%; border-collapse:collapse; font-size:12px; }
			.fgsrm-table th { text-align:left; font-weight:600; color:var(--text-muted,#8d99a6);
				border-bottom:1px solid var(--border-color,#d1d8dd); padding:6px 8px; white-space:nowrap; }
			.fgsrm-table td { padding:7px 8px; border-bottom:1px solid var(--border-color,#ebeef0);
				color:var(--text-color,#1f272e); vertical-align:top; }
			.fgsrm-table tbody tr[data-filter-item], .fgsrm-table tbody tr[data-filter-so] { cursor:pointer; }
			.fgsrm-table tbody tr:hover { background:var(--control-bg,#f4f5f6); }
			.fgsrm-right { text-align:right; } .fgsrm-center { text-align:center; }
			.fgsrm-strong { font-weight:600; }
			.fgsrm-so-link { cursor:pointer; color:var(--primary,#2a78d6); }
			.fgsrm-so-link:hover { text-decoration:underline; }
			.fgsrm-muted { color:var(--text-muted,#8d99a6); font-weight:400; font-size:11px; }
			.fgsrm-empty { text-align:center; color:var(--text-muted,#8d99a6); padding:18px; }
			.fgsrm-badge { display:inline-block; padding:1px 7px; border-radius:9px; font-size:11px; font-weight:600; }
			.fgsrm-badge-red { background:#fde2e7; color:#a01c33; }
			.fgsrm-badge-orange { background:#fff3e0; color:#8a5200; }
			.fgsrm-badge-green { background:#e1f5ee; color:#0f6b4d; }
			.fgsrm-badge-grey { background:#eceff1; color:#4a5560; }
			.fgsrm-dash-foot { font-size:11px; color:var(--text-muted,#8d99a6); padding-bottom:12px; }
		`;
		document.head.appendChild(style);
	},
};

frappe.query_reports["FG Stock Reservation Manager"] = {
	filters: [
		{ fieldname: "item_code", label: __("FG Item"), fieldtype: "Link", options: "Item" },
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "sales_order", label: __("Sales Order"), fieldtype: "Link", options: "Sales Order" },
		{
			fieldname: "date_basis",
			label: __("Date Basis"),
			fieldtype: "Select",
			options: ["Document Creation Date", "Delivery Date", "Custom Updated Delivery Date"].join("\n"),
			default: "Custom Updated Delivery Date",
		},
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date" },
		{
			fieldname: "only_unreserved",
			label: __("Only lines with unreserved pending"),
			fieldtype: "Check",
			default: 0,
		},
		{
			fieldname: "unreserved_basis",
			label: __("Unreserved Stock Basis"),
			fieldtype: "Select",
			options: ["All Reservations", "Only Displayed SOs"].join("\n"),
			default: "Only Displayed SOs",
			// Free stock for Reservable Qty: net out every reservation (truly
			// free — correct for reserving), or only reservations tied to the
			// shown SOs (to reconcile with the PRR on that basis).
		},
		{
			// Carries the active view ("" / ready_to_dispatch / possible_to_complete)
			// set by the view buttons below. Hidden — not meant to be typed in.
			fieldname: "view_mode",
			label: __("View"),
			fieldtype: "Data",
			hidden: 1,
			default: "",
		},
		{
			fieldname: "group_by_so",
			label: __("Group by Sales Order"),
			fieldtype: "Check",
			default: 0,
			// Collapses the report server-side to one summary row per Sales Order:
			// Pending Qty, Reserved Qty and Suggested Prodn are totalled across the
			// SO's items; SO / Customer / Dispatch Priority Date carry through; all
			// item-level columns are blank. A read-only summary — the per-line
			// Create/Cancel/Reserve actions don't apply to a collapsed row.
		},
	],

	// Tint the Reserved column; emphasise Reservable / Reserve Qty.
	formatter(value, row, column, data, default_formatter) {
		let formatted = default_formatter(value, row, column, data);
		const f = column.fieldname || "";
		const grouping = cint(frappe.query_report.get_filter_value("group_by_so"));

		if (grouping && data && data.so_group_first === false && (f === "sales_order" || f === "customer" || f === "so_date")) {
			return "";
		}

		if (grouping && f === "sales_order" && data && data.so_group_first) {
			formatted = `<div style="border-top:1px solid var(--border-color, #d1d8dd);margin-top:-1px;padding-top:7px;">${formatted}</div>`;
		}

		// Manual (speculative) requirement row: tint Source + Item Name indigo and
		// italicise so it reads as "not Sales Order demand", keep Suggested Prodn's
		// amber emphasis, and leave every reservation-oriented column plain (a
		// manual row has no reservations, so the pink Reserved / green Reservable
		// tints would just be a misleading "0"). Short-circuits the branches below.
		if (data && data.is_manual) {
			if (f === "source" || f === "item_name") {
				return `<div style="background-color:#ede7f6;margin:-8px -12px;padding:8px 12px;font-style:italic;">${formatted}</div>`;
			}
			if (f === "suggested_prodn" && flt(data.suggested_prodn) > 0) {
				return `<div style="background-color:#fff3e0;margin:-8px -12px;padding:8px 12px;font-weight:600;">${formatted}</div>`;
			}
			return formatted;
		}

		if (f === "reserved_qty") {
			return `<div style="background-color:#fde2e7;margin:-8px -12px;padding:8px 12px;">${formatted}</div>`;
		}
		if (f === "item_free_stock" && data && flt(data.item_free_stock) < 0) {
			return `<span style="color:red;">${formatted}</span>`;
		}
		if (f === "reservable_now") {
			return `<div style="background-color:#e1f5ee;margin:-8px -12px;padding:8px 12px;">${formatted}</div>`;
		}
		// Suggested Prodn: tint amber and bold when there's a real shortfall to make.
		if (f === "suggested_prodn") {
			if (data && flt(data.suggested_prodn) > 0) {
				return `<div style="background-color:#fff3e0;margin:-8px -12px;padding:8px 12px;font-weight:600;">${formatted}</div>`;
			}
			return formatted;
		}
		if (f === "reserve_qty") {
			return `<span style="font-weight:600;">${formatted}</span>`;
		}
		// Colour-code Material Status so the six states are scannable at a glance.
		if (f === "material_status") {
			const colors = {
				"Ready to Dispatch": "#e1f5ee", // green — good to go
				Inspected: "#e8f5e9", // light green — cleared inspection
				"Possible to Push": "#fff3e0", // amber — actionable
				"Needs Attention": "#fde2e7", // red — problem
				Reprioritize: "#ede7f6", // purple — far-out, revisit
				"Planning Pending": "#eceff1", // grey — awaiting planning
			};
			const bg = colors[value];
			if (bg) {
				return `<div style="background-color:${bg};margin:-8px -12px;padding:8px 12px;font-weight:600;">${formatted}</div>`;
			}
			return formatted;
		}
		return formatted;
	},

	// Keep the tab chrome present after every run, and - when the Dashboard tab
	// is the one on screen - re-pull its metrics so a filter change updates the
	// cards, not just the table.
	after_datatable_render() {
		FGSRM_DASH.mount(frappe.query_report);
		if (FGSRM_DASH.active) {
			// Re-assert visibility: the datatable Frappe just rendered would
			// otherwise reappear underneath the dashboard.
			FGSRM_DASH.activate("dashboard");
		}
	},

	// Make Reserve Qty editable (capped at Reservable Now); make Dispatch
	// Priority Date editable only when Date Basis = "Custom Updated Delivery
	// Date" (editing Document Creation Date / Delivery Date wouldn't make
	// sense - those are factual record-keeping dates, not a priority lever).
	// Also add row checkboxes so lines can be selected for cancellation.
	get_datatable_options(datatable_options) {
		datatable_options.checkboxColumn = true;

		const date_editable =
			frappe.query_report.get_filter_value("date_basis") === "Custom Updated Delivery Date";

		datatable_options.columns.forEach((column) => {
			if (column.id === "reserve_qty") column.editable = true;
			if (column.id === "so_date") column.editable = date_editable;
		});

		datatable_options.events = datatable_options.events || {};
		datatable_options.events.onSubmitEditing = function (cell) {
			const [row_values, cell_id, new_val] = cell;

			if (cell_id === "reserve_qty") {
				let qty = flt(new_val);
				const cap = flt(row_values.reservable_now);
				if (qty < 0) qty = 0;
				if (qty > cap) {
					frappe.show_alert({
						message: __("Reserve Qty capped at Reservable Now ({0}).", [cap]),
						indicator: "orange",
					});
					qty = cap;
				}
				const report_row = (frappe.query_report.data || []).find(
					(r) => r.sales_order_item === row_values.sales_order_item
				);
				if (report_row) report_row.reserve_qty = qty;
				if (frappe.query_report.datatable) {
					frappe.query_report.datatable.refresh(frappe.query_report.data);
				}
				return;
			}

			if (cell_id === "so_date") {
				if (!date_editable) return;
				const new_date = new_val;

				// A date is a header-level Sales Order field (one per SO, not
				// per line) - apply the edit to every row sharing this SO,
				// mirroring the existing so_group_first grouping logic.
				(frappe.query_report.data || []).forEach((r) => {
					if (r.sales_order === row_values.sales_order) r.so_date = new_date;
				});
				if (frappe.query_report.datatable) {
					frappe.query_report.datatable.refresh(frappe.query_report.data);
				}

				frappe.call({
					method: `${FGSRM_METHOD_PATH}.update_dispatch_priority_date`,
					args: { sales_order: row_values.sales_order, new_date: new_date },
					callback() {
						frappe.show_alert({
							message: __("Dispatch Priority Date saved for {0}.", [row_values.sales_order]),
							indicator: "green",
						});
					},
				});
			}
		};

		return datatable_options;
	},

	onload(report) {
		// ── Table / Dashboard tabs (see FGSRM_DASH above) ───────────────────
		// Mounted here so the tabs exist before the first run; the report opens
		// on Table, exactly as it did before this was added.
		FGSRM_DASH.mount(report);

		// ── Run the JIT Production Planning Report for this same filtered view ──
		report.page.add_inner_button(
			__("Run JIT Production Planning Report"),
			() => {
				const values = frappe.query_report.get_filter_values();
				frappe.set_route("query-report", "JIT Production Planning Report").then(() => {
					// JIT Production Planning Report shares this report's filter
					// fieldnames (item_code, customer, sales_order, date_basis,
					// from_date, to_date, only_unreserved, unreserved_basis), so its
					// Order Qty / Available match what's on screen here. It also has
					// its own extra filters (company, raw_material_warehouse,
					// include_subassembly_raw_materials) which are left at their
					// defaults since this view has no equivalents.
					frappe.query_report.set_filter_value(values);
				});
			},
			__("Reports")
		);

		// ── Freeze this filtered view's open-SO demand into a new DRAFT Weekly
		// Planning Snapshot (items pre-populated server-side). Review it - adjust
		// Committed Prodn line-wise - then Submit on the form to approve. ──
		report.page.add_inner_button(
			__("Create Weekly Snapshot (Draft)"),
			() => {
				frappe.confirm(
					__("This freezes the current open Sales Order demand (for this filtered view) into a new DRAFT Weekly Planning Snapshot. Review it (adjust Committed Prodn), then Submit to approve. Continue?"),
					() => {
						frappe.call({
							method: "playground.playground.report.weekly_planning_snapshot_review.weekly_planning_snapshot_review.approve_snapshot",
							args: { filters: JSON.stringify(frappe.query_report.get_filter_values()) },
							freeze: true,
							freeze_message: __("Creating draft snapshot…"),
							callback(r) {
								if (r.message) {
									frappe.show_alert({
										message: __("Draft snapshot {0} created — review and submit to approve.", [r.message]),
										indicator: "blue",
									});
									frappe.set_route("Form", "Weekly Planning Snapshot", r.message);
								}
							},
						});
					}
				);
			},
			__("Reports")
		);

		// ── Create a draft Production Plan from the itemwise Suggested Prodn ──
		report.page.add_inner_button(
			__("Create Prodn Plan"),
			() => {
				frappe.confirm(
					__("Create a draft Production Plan from the itemwise Suggested Prodn for the current filters? It will build the full nested plan chain and raw materials, then download the Production Plan workbook — no need to open the plan."),
					() => {
						frappe.call({
							method: `${FGSRM_METHOD_PATH}.create_production_plan_from_suggested_prodn`,
							args: { filters: JSON.stringify(frappe.query_report.get_filter_values()) },
							freeze: true,
							freeze_message: __("Creating Production Plan…"),
							callback(r) {
								const m = r.message;
								if (!m || !m.name) return;
								if (m.handed_off) {
									// Success: chain + raw materials are built, so the
									// MR Hierarchy workbook is meaningful — download it
									// straight away and stay on the report.
									frappe.show_alert({
										message: __("Production Plan {0}: {1} item(s), {2} raw material line(s), full chain built. Downloading Production Plan workbook…", [
											m.name,
											m.items,
											m.raw_materials,
										]),
										indicator: "green",
									});
									fgsrm_download_mr_excel(m.name, JSON.stringify(frappe.query_report.get_filter_values()));
								} else {
									// Chain didn't build (frontec hand-off unavailable
									// or errored) — the workbook would be incomplete, so
									// send the user to the draft to finish it manually.
									frappe.show_alert({
										message: __("Draft Production Plan {0} created with {1} item(s). Open it and click “Create Full Chain”, then download the Production Plan workbook.", [
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
			},
			__("Reports")
		);

		// ── Views: narrow to SOs where every line meets a condition ─────────
		report.page.add_inner_button(
			__("Ready to Dispatch"),
			() => frappe.query_report.set_filter_value("view_mode", "ready_to_dispatch"),
			__("Views")
		);
		report.page.add_inner_button(
			__("Possible to Complete"),
			() => frappe.query_report.set_filter_value("view_mode", "possible_to_complete"),
			__("Views")
		);
		report.page.add_inner_button(
			__("Show All"),
			() => frappe.query_report.set_filter_value("view_mode", ""),
			__("Views")
		);

		// ── Manual Requirements: add FG demand by hand (free-form or cherry-picked
		// from an open Blanket Order / Quotation); persists per-user at the bottom
		// of the report until removed / cleared. ───────────────────────────────
		report.page.add_inner_button(__("Add Requirement"), fgsrm_add_requirement, __("Manual Requirements"));

		report.page.add_inner_button(
			__("Remove Selected"),
			() => {
				const names = fgsrm_checked_rows()
					.filter((r) => r.is_manual && r.manual_name)
					.map((r) => r.manual_name);
				if (!names.length) {
					frappe.msgprint(__("Tick one or more manual requirement rows (Source = Manual / Blanket Order / Quotation) to remove."));
					return;
				}
				frappe.confirm(__("Remove {0} manual requirement(s)?", [names.length]), () => {
					frappe.call({
						method: `${FGSRM_MR_METHOD_PATH}.remove_manual_requirements`,
						args: { names: JSON.stringify(names) },
						freeze: true,
						freeze_message: __("Removing…"),
						callback(r) {
							const m = r.message || {};
							frappe.show_alert({ message: __("Removed {0} requirement(s).", [m.removed || 0]), indicator: "blue" });
							frappe.query_report.refresh();
						},
					});
				});
			},
			__("Manual Requirements")
		);

		report.page.add_inner_button(
			__("Clear My Requirements"),
			() => {
				frappe.confirm(__("Clear ALL your manual requirements? This cannot be undone."), () => {
					frappe.call({
						method: `${FGSRM_MR_METHOD_PATH}.clear_manual_requirements`,
						freeze: true,
						freeze_message: __("Clearing…"),
						callback(r) {
							const m = r.message || {};
							frappe.show_alert({ message: __("Cleared {0} requirement(s).", [m.removed || 0]), indicator: "blue" });
							frappe.query_report.refresh();
						},
					});
				});
			},
			__("Manual Requirements")
		);

		// ── Bulk selection by SO / Item (drives both Create and Cancel) ──────
		report.page.add_inner_button(
			__("Select by SO / Item"),
			() => {
				frappe.prompt(
					[
						{ fieldname: "sales_order", label: __("Sales Order"), fieldtype: "Link", options: "Sales Order" },
						{ fieldname: "item_code", label: __("FG Item"), fieldtype: "Link", options: "Item" },
					],
					(values) => {
						if (!values.sales_order && !values.item_code) {
							frappe.msgprint(__("Pick a Sales Order and/or an Item to select by."));
							return;
						}
						const dt = frappe.query_report.datatable;
						const data = frappe.query_report.data || [];
						if (!dt) return;
						dt.rowmanager.checkAll(false);
						let n = 0;
						data.forEach((r, i) => {
							const so_ok = !values.sales_order || r.sales_order === values.sales_order;
							const item_ok = !values.item_code || r.item_code === values.item_code;
							if (so_ok && item_ok) {
								dt.rowmanager.checkRow(i);
								n++;
							}
						});
						frappe.show_alert({ message: __("Selected {0} line(s).", [n]), indicator: "blue" });
					},
					__("Select lines by SO / Item"),
					__("Select")
				);
			},
			__("Selection")
		);

		report.page.add_inner_button(
			__("Clear Selection"),
			() => {
				const dt = frappe.query_report.datatable;
				if (dt) dt.rowmanager.checkAll(false);
			},
			__("Selection")
		);

		report.page.add_inner_button(__("Create Reservations"), () => {
			// If any rows are ticked, act only on them (SO/item-wise bulk create);
			// otherwise fall back to every line that has a Reserve Qty.
			const checked = fgsrm_checked_rows();
			const source = checked.length ? checked : frappe.query_report.data || [];
			const rows = source
				.filter((r) => flt(r.reserve_qty) > 0)
				.map((r) => ({
					sales_order: r.sales_order,
					sales_order_item: r.sales_order_item,
					item_code: r.item_code,
					qty: flt(r.reserve_qty),
				}));

			if (!rows.length) {
				frappe.msgprint(__("Tick the lines to reserve (or enter a Reserve Qty), then try again."));
				return;
			}

			const filters_json = JSON.stringify(frappe.query_report.get_filter_values());

			frappe.confirm(
				__("Create stock reservations for {0} line(s)? Quantities are capped at free stock under the current Unreserved Stock Basis.", [rows.length]),
				() => fgsrm_call_create_reservations(rows, filters_json)
			);
		});

		report.page.add_inner_button(__("Cancel Reservations"), () => {
			const sre_names = fgsrm_checked_rows()
				.filter((r) => r.existing_sre)
				.map((r) => r.existing_sre);

			if (!sre_names.length) {
				frappe.msgprint(__("Tick one or more rows that have an existing reservation to cancel (use Select by SO / Item for bulk)."));
				return;
			}

			frappe.confirm(
				__("Cancel the reservations on {0} selected line(s)? This releases the stock.", [sre_names.length]),
				() => {
					frappe.call({
						method: `${FGSRM_METHOD_PATH}.cancel_reservations`,
						args: { sre_names: JSON.stringify(sre_names) },
						freeze: true,
						freeze_message: __("Cancelling reservations…"),
						callback(r) {
							const m = r.message || {};
							frappe.show_alert({
								message: __("Cancelled {0} reservation(s).", [m.cancelled || 0]),
								indicator: "blue",
							});
							frappe.query_report.refresh();
						},
					});
				}
			);
		});
	},
};
