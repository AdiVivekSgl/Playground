// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

const CCPB_METHOD =
	"playground.playground.report.ftpl___customer_commercial_profile_builder.ftpl___customer_commercial_profile_builder";

// The Customer Master fields the one-click apply can write, mapped to the report
// columns that carry their suggested value / confidence / current value. Only
// these are ever written back (Company / Sales Person are advisory-only). Kept in
// sync with APPLYABLE_FIELDS in the .py.
const CCPB_FIELD_META = {
	default_price_list: {
		label: __("Price List"),
		sugg: "suggested_price_list",
		conf: "price_list_confidence",
		current: "current_price_list",
	},
	payment_terms: {
		label: __("Payment Terms"),
		sugg: "suggested_payment_terms",
		conf: "payment_terms_confidence",
		current: "current_payment_terms",
	},
	default_currency: {
		label: __("Currency"),
		sugg: "suggested_currency",
		conf: null, // no per-value confidence column for currency
		current: "current_currency",
	},
	territory: {
		label: __("Territory"),
		sugg: "suggested_territory",
		conf: null,
		current: "current_territory",
	},
	tax_category: {
		label: __("Tax Category"),
		sugg: "suggested_tax_category",
		conf: null,
		current: "current_tax_category",
	},
};

// Confidence colour bands (matches the legend and the .py report_summary).
function ccpb_confidence_colour(pct) {
	if (pct >= 95) return "#1b8a2f"; // green
	if (pct >= 80) return "#e08600"; // yellow / amber
	return "#b71c1c"; // red
}

// The report rows currently ticked in the DataTable, as data objects. Mirrors
// the FGSRM helper: getCheckedRows lives on datatable.rowmanager, not on the
// datatable object itself.
function ccpb_checked_rows() {
	const dt = frappe.query_report.datatable;
	const data = frappe.query_report.data || [];
	if (!dt || !dt.rowmanager || !dt.rowmanager.getCheckedRows) return [];
	return (dt.rowmanager.getCheckedRows() || []).map((i) => data[i]).filter(Boolean);
}

// Build the {customer, fields:{...}} update payload from a set of rows.
//
//   field_keys : which CCPB_FIELD_META keys to consider.
//   overwrite  : when false, only BLANK current values are proposed (matches the
//                server-side safety, so Preview shows exactly what would happen).
//   threshold  : when set, a field is only proposed if its confidence column is
//                >= threshold (fields without a confidence column are exempt).
function ccpb_build_updates(rows, field_keys, { overwrite = false, threshold = null } = {}) {
	const updates = [];
	for (const row of rows) {
		const fields = {};
		for (const key of field_keys) {
			const meta = CCPB_FIELD_META[key];
			const value = row[meta.sugg];
			if (!value) continue; // no suggestion to apply
			if (!overwrite && row[meta.current]) continue; // never overwrite unless told to
			if (threshold != null && meta.conf && flt(row[meta.conf]) < threshold) continue;
			fields[key] = value;
		}
		if (Object.keys(fields).length) {
			updates.push({ customer: row.customer, fields });
		}
	}
	return updates;
}

// POST the updates and report the outcome. Shared by every apply entry point.
function ccpb_apply(updates) {
	if (!updates.length) {
		frappe.msgprint(
			__("Nothing to apply — the selected rows already have these values, or fall below the confidence threshold. Tick <b>Overwrite Existing Values</b> to replace values that are already set.")
		);
		return;
	}
	const overwrite = cint(frappe.query_report.get_filter_value("overwrite_existing"));
	frappe.confirm(
		__("Apply commercial defaults to {0} customer(s)?{1}", [
			updates.length,
			overwrite ? __(" <b>Existing values will be overwritten.</b>") : "",
		]),
		() => {
			frappe.call({
				method: `${CCPB_METHOD}.apply_customer_defaults`,
				args: { updates: JSON.stringify(updates), overwrite },
				freeze: true,
				freeze_message: __("Applying customer defaults…"),
				callback(r) {
					const m = r.message || {};
					frappe.show_alert({
						message: __("Applied {0} · skipped {1} · failed {2}.", [
							m.applied || 0,
							m.skipped || 0,
							m.failed || 0,
						]),
						indicator: m.failed ? "orange" : "green",
					});
					if (m.failed) ccpb_show_apply_details(m.details || []);
					frappe.query_report.refresh();
				},
			});
		}
	);
}

// After an apply with failures, list what happened per customer so the user can
// act on the ones that didn't take (e.g. an invalid link target).
function ccpb_show_apply_details(details) {
	const rows_html = details
		.map((d) => {
			const detail =
				d.result === "applied"
					? Object.entries(d.fields || {})
							.map(([k, v]) => `${frappe.utils.escape_html(k)} → ${frappe.utils.escape_html(v)}`)
							.join("<br>")
					: frappe.utils.escape_html(d.reason || "");
			const tone = { applied: "#1b8a2f", skipped: "#e08600", failed: "#b71c1c" }[d.result] || "";
			return `<tr>
				<td>${frappe.utils.escape_html(d.customer)}</td>
				<td style="color:${tone};font-weight:600;">${frappe.utils.escape_html(d.result)}</td>
				<td>${detail}</td>
			</tr>`;
		})
		.join("");
	frappe.msgprint({
		title: __("Apply Results"),
		indicator: "orange",
		message: `<table class="table table-bordered" style="font-size:12px;">
			<thead><tr><th>${__("Customer")}</th><th>${__("Result")}</th><th>${__("Detail")}</th></tr></thead>
			<tbody>${rows_html}</tbody></table>`,
	});
}

// Preview (client-side only): show exactly what an apply of `field_keys` over the
// given rows would change, honouring the Overwrite toggle.
function ccpb_preview(rows, field_keys) {
	const overwrite = cint(frappe.query_report.get_filter_value("overwrite_existing"));
	const updates = ccpb_build_updates(rows, field_keys, { overwrite: !!overwrite });
	if (!updates.length) {
		frappe.msgprint(__("No changes to preview for the current selection and options."));
		return;
	}
	const by_row = updates
		.flatMap((u) => {
			const row = rows.find((r) => r.customer === u.customer) || {};
			return Object.entries(u.fields).map(([field, value]) => {
				const meta = CCPB_FIELD_META[field];
				const from = row[meta.current] || `<i style="color:#8d99a6;">${__("blank")}</i>`;
				return `<tr>
					<td>${frappe.utils.escape_html(u.customer)}</td>
					<td>${meta.label}</td>
					<td>${from}</td>
					<td style="font-weight:600;">${frappe.utils.escape_html(value)}</td>
				</tr>`;
			});
		})
		.join("");
	frappe.msgprint({
		title: __("Preview Changes ({0} customer(s))", [updates.length]),
		message: `<table class="table table-bordered" style="font-size:12px;">
			<thead><tr>
				<th>${__("Customer")}</th><th>${__("Field")}</th>
				<th>${__("From")}</th><th>${__("To")}</th>
			</tr></thead>
			<tbody>${by_row}</tbody></table>`,
		wide: true,
	});
}

// Field-picker dialog used by "Apply Selected": choose which defaults to write to
// the ticked rows (Price List + Payment Terms pre-checked, per the brief).
function ccpb_apply_selected() {
	const rows = ccpb_checked_rows();
	if (!rows.length) {
		frappe.msgprint(__("Tick one or more customer rows first (use the row checkboxes)."));
		return;
	}
	const d = new frappe.ui.Dialog({
		title: __("Apply to {0} selected customer(s)", [rows.length]),
		fields: [
			{
				fieldtype: "HTML",
				options: `<p>${__("Choose which inferred defaults to write. Values already set on a Customer are skipped unless <b>Overwrite Existing Values</b> is ticked in the filters.")}</p>`,
			},
			{ fieldname: "default_price_list", label: __("Price List"), fieldtype: "Check", default: 1 },
			{ fieldname: "payment_terms", label: __("Payment Terms"), fieldtype: "Check", default: 1 },
			{ fieldname: "default_currency", label: __("Currency"), fieldtype: "Check", default: 0 },
			{ fieldname: "territory", label: __("Territory"), fieldtype: "Check", default: 0 },
			{ fieldname: "tax_category", label: __("Tax Category"), fieldtype: "Check", default: 0 },
		],
		primary_action_label: __("Apply"),
		primary_action(values) {
			const keys = Object.keys(CCPB_FIELD_META).filter((k) => values[k]);
			if (!keys.length) {
				frappe.msgprint(__("Pick at least one field to apply."));
				return;
			}
			d.hide();
			const overwrite = cint(frappe.query_report.get_filter_value("overwrite_existing"));
			ccpb_apply(ccpb_build_updates(rows, keys, { overwrite: !!overwrite }));
		},
	});
	// A secondary button to preview (client-side only) without writing anything.
	d.set_secondary_action_label(__("Preview"));
	d.set_secondary_action(() => {
		const values = d.get_values();
		const keys = Object.keys(CCPB_FIELD_META).filter((k) => values[k]);
		if (!keys.length) {
			frappe.msgprint(__("Pick at least one field to preview."));
			return;
		}
		ccpb_preview(rows, keys);
	});
	d.show();
}

frappe.query_reports["FTPL - Customer Commercial Profile Builder"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			reqd: 1,
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			reqd: 1,
			// Five years of history by default.
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -60),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			reqd: 1,
			default: frappe.datetime.get_today(),
		},
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "territory", label: __("Territory"), fieldtype: "Link", options: "Territory" },
		{ fieldname: "sales_person", label: __("Sales Person"), fieldtype: "Link", options: "Sales Person" },
		{
			fieldname: "minimum_orders",
			label: __("Minimum Orders"),
			fieldtype: "Int",
			default: 3,
			// Customers with fewer submitted orders than this in the window are
			// hidden — too little history to infer a reliable default.
		},
		{
			fieldname: "confidence_threshold",
			label: __("Confidence Threshold"),
			fieldtype: "Percent",
			default: 80,
			// Drives colour coding, "Apply All Above Confidence Threshold", and the
			// Low Confidence recommendation status.
		},
		{
			fieldname: "include_disabled",
			label: __("Include Disabled Customers"),
			fieldtype: "Check",
			default: 0,
		},
		{
			fieldname: "show_only_missing",
			label: __("Show Only Missing Defaults"),
			fieldtype: "Check",
			default: 1,
			// On: only customers missing at least one fillable key default appear.
		},
		{
			fieldname: "overwrite_existing",
			label: __("Overwrite Existing Values"),
			fieldtype: "Check",
			default: 0,
			// SAFETY: off means apply only fills BLANK Customer fields. On means an
			// apply will replace values that are already set — used by every apply
			// action on this report (read server-side too).
		},
	],

	// Colour the confidence cells by band, and tint the Recommendation Status so
	// the worklist reads at a glance. Suggested cells that differ from the current
	// value get a subtle highlight so the change stands out.
	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		const f = column.fieldname;

		if ((f === "price_list_confidence" || f === "payment_terms_confidence") && (value || value === 0)) {
			return `<span style="color:${ccpb_confidence_colour(flt(value))};font-weight:600;">${formatted}</span>`;
		}

		if (f === "recommendation_status") {
			// Keyed on the English status text execute() emits (see
			// _recommendation_status in the .py); tinted for at-a-glance triage.
			const colours = {
				Missing: "#b71c1c",
				"Needs Review": "#e08600",
				"Mixed Usage": "#8e44ad",
				"Low Confidence": "#b71c1c",
				Consistent: "#1b8a2f",
			};
			const colour = colours[value];
			if (colour) return `<span style="color:${colour};font-weight:600;">${formatted}</span>`;
			return formatted;
		}

		// Highlight a suggested value that differs from what the Customer has now
		// (an actionable change). Blank current value → it's a fill, tint green.
		const diff_map = {
			suggested_price_list: "current_price_list",
			suggested_payment_terms: "current_payment_terms",
			suggested_currency: "current_currency",
			suggested_territory: "current_territory",
			suggested_tax_category: "current_tax_category",
		};
		if (diff_map[f] && value && data) {
			const current = data[diff_map[f]];
			if (!current) {
				return `<div style="background-color:#e1f5ee;margin:-8px -12px;padding:8px 12px;">${formatted}</div>`;
			}
			if (current !== value) {
				return `<div style="background-color:#fff3e0;margin:-8px -12px;padding:8px 12px;">${formatted}</div>`;
			}
		}

		if (f === "payment_diff" && (value || value === 0)) {
			// Paid slower than agreed (positive) is the risk direction → red.
			const colour = flt(value) > 0 ? "#b71c1c" : "#1b8a2f";
			return `<span style="color:${colour};">${formatted}</span>`;
		}
		return formatted;
	},

	// Row checkboxes so customers can be selected for a bulk apply.
	get_datatable_options(datatable_options) {
		datatable_options.checkboxColumn = true;
		return datatable_options;
	},

	onload(report) {
		// ── Apply actions ───────────────────────────────────────────────────
		report.page.add_inner_button(
			__("Apply Selected"),
			ccpb_apply_selected,
			__("Apply")
		);

		report.page.add_inner_button(
			__("Apply All Above Confidence Threshold"),
			() => {
				const threshold = flt(frappe.query_report.get_filter_value("confidence_threshold"));
				const overwrite = cint(frappe.query_report.get_filter_value("overwrite_existing"));
				// Every row on screen; price list + payment terms only (the two
				// dimensions that carry a confidence score to threshold against).
				const rows = frappe.query_report.data || [];
				const updates = ccpb_build_updates(rows, ["default_price_list", "payment_terms"], {
					overwrite: !!overwrite,
					threshold,
				});
				ccpb_apply(updates);
			},
			__("Apply")
		);

		report.page.add_inner_button(
			__("Preview Changes"),
			() => {
				// Preview the ticked rows if any, else the whole view; Price List +
				// Payment Terms (the primary Apply Both pair).
				const checked = ccpb_checked_rows();
				const rows = checked.length ? checked : frappe.query_report.data || [];
				if (!rows.length) {
					frappe.msgprint(__("Run the report first — there are no rows to preview."));
					return;
				}
				ccpb_preview(rows, ["default_price_list", "payment_terms"]);
			},
			__("Apply")
		);

		// ── Export ──────────────────────────────────────────────────────────
		// Parity with the brief's toolbar; opens Frappe's own export dialog
		// (Excel / CSV) for the current view.
		report.page.add_inner_button(
			__("Export to Excel"),
			() => frappe.query_report.export_report(),
			__("Actions")
		);

		// ── Selection helpers ───────────────────────────────────────────────
		report.page.add_inner_button(
			__("Select All"),
			() => {
				const dt = frappe.query_report.datatable;
				if (dt) dt.rowmanager.checkAll(true);
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
	},
};
