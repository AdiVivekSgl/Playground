// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

const SPM_METHOD =
	"playground.playground.report.sales_pivot_matrix.sales_pivot_matrix";

// Column values whose fieldname is a matrix cell (c0, c1, ...). The row label
// and total columns are excluded from heatmap / drill-down cell handling.
function spm_is_cell(fieldname) {
	return /^c\d+$/.test(fieldname);
}

// The raw column dimension value for a cell fieldname, resolved from the report's
// own column list. Looked up (rather than read off the datatable column object)
// so it doesn't depend on custom keys surviving datatable preparation. Returns ""
// for the "(Not Set)" bucket.
function spm_col_value(fieldname) {
	const cols = (frappe.query_report && frappe.query_report.columns) || [];
	const col = cols.find((c) => c.fieldname === fieldname);
	return col && col.col_value != null ? col.col_value : "";
}

// Per-column maximum over the data rows (excluding the grand-total row), cached
// on the report so the formatter doesn't recompute it for every cell. Reset
// whenever the dataset changes (see after_datatable_render / onload refresh).
function spm_column_max(fieldname) {
	const report = frappe.query_report;
	if (!report._spm_col_max) {
		const max = {};
		(report.data || []).forEach((r) => {
			if (r.is_total) return;
			Object.keys(r).forEach((k) => {
				if (spm_is_cell(k)) max[k] = Math.max(max[k] || 0, flt(r[k]));
			});
		});
		report._spm_col_max = max;
	}
	return report._spm_col_max[fieldname] || 0;
}

// Light green heatmap tint scaled by the cell's share of its column maximum.
function spm_heat_style(value, fieldname) {
	const max = spm_column_max(fieldname);
	if (max <= 0) return "";
	const ratio = Math.max(0, Math.min(1, flt(value) / max));
	if (ratio <= 0) return "";
	// Alpha 0.06 -> 0.45 so even small non-zero values read as "warm".
	const alpha = (0.06 + ratio * 0.39).toFixed(3);
	return `background-color: rgba(43, 120, 214, ${alpha});`;
}

frappe.query_reports["Sales Pivot Matrix"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			reqd: 1,
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -12),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			reqd: 1,
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},

		// ── Pivot configuration ──────────────────────────────────────────────
		{
			fieldname: "rows_by",
			label: __("Rows By"),
			fieldtype: "Select",
			options: [
				{ value: "customer", label: __("Customer") },
				{ value: "customer_group", label: __("Customer Group") },
				{ value: "territory", label: __("Territory") },
				{ value: "sales_person", label: __("Sales Person") },
			],
			default: "customer",
			reqd: 1,
		},
		{
			fieldname: "columns_by",
			label: __("Columns By"),
			fieldtype: "Select",
			options: [
				{ value: "shipping_state", label: __("Shipping State") },
				{ value: "billing_state", label: __("Billing State") },
				{ value: "territory", label: __("Territory") },
				{ value: "customer_group", label: __("Customer Group") },
			],
			default: "shipping_state",
			reqd: 1,
		},
		{
			fieldname: "measure",
			label: __("Measure"),
			fieldtype: "Select",
			options: [
				{ value: "net", label: __("Net Sales Amount") },
				{ value: "gross", label: __("Gross Sales Amount") },
				{ value: "qty", label: __("Quantity Sold") },
				{ value: "count", label: __("Invoice Count") },
			],
			default: "net",
			reqd: 1,
		},

		// ── Scope filters ────────────────────────────────────────────────────
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "customer_group", label: __("Customer Group"), fieldtype: "Link", options: "Customer Group" },
		{ fieldname: "territory", label: __("Territory"), fieldtype: "Link", options: "Territory" },
		{ fieldname: "sales_person", label: __("Sales Person"), fieldtype: "Link", options: "Sales Person" },
		{ fieldname: "item_group", label: __("Item Group"), fieldtype: "Link", options: "Item Group" },
		{ fieldname: "item", label: __("Item"), fieldtype: "Link", options: "Item" },
		{ fieldname: "state", label: __("State (Shipping)"), fieldtype: "Data" },
		{ fieldname: "cost_center", label: __("Cost Center"), fieldtype: "Link", options: "Cost Center" },
		{ fieldname: "project", label: __("Project"), fieldtype: "Link", options: "Project" },

		// ── Presentation controls ────────────────────────────────────────────
		{ fieldname: "top_n", label: __("Top N Rows"), fieldtype: "Int" },
		{ fieldname: "min_total", label: __("Minimum Row Total"), fieldtype: "Float" },
		{
			fieldname: "hide_zero_columns",
			label: __("Hide Zero Columns"),
			fieldtype: "Check",
			default: 1,
		},
		{
			fieldname: "heatmap",
			label: __("Heatmap"),
			fieldtype: "Check",
			default: 0,
		},
	],

	// Zero -> blank, currency right-alignment (inherited from the Currency/Float
	// fieldtype), grand-total emphasis, optional heatmap, and clickable cells.
	formatter(value, row, column, data, default_formatter) {
		const is_total = data && data.is_total;

		// Blank out zero (and null) measure cells so the matrix reads cleanly;
		// the brief asks for blank rather than 0 (grand-total row included).
		if ((spm_is_cell(column.fieldname) || column.fieldname === "total") && !flt(value)) {
			return "";
		}

		let formatted = default_formatter(value, row, column, data);

		// Grand-total row: bold across the board.
		if (is_total) {
			return `<span style="font-weight:700;">${formatted}</span>`;
		}

		if (spm_is_cell(column.fieldname) && flt(value)) {
			const heat =
				cint(frappe.query_report.get_filter_value("heatmap")) && spm_is_cell(column.fieldname)
					? spm_heat_style(value, column.fieldname)
					: "";
			// A drill-down anchor carrying the raw row/column dimension values so
			// the click handler can rebuild the exact Sales Invoice filter.
			const col_value = spm_col_value(column.fieldname);
			const row_value = data._row_value == null ? "" : data._row_value;
			return `<a class="spm-cell" href="#"
				data-row="${frappe.utils.escape_html(String(row_value))}"
				data-col="${frappe.utils.escape_html(String(col_value))}"
				style="display:block;margin:-8px -12px;padding:8px 12px;text-align:right;${heat}"
				>${formatted}</a>`;
		}

		return formatted;
	},

	// Freeze the first column so row labels stay put during horizontal scroll.
	// (Option name has varied across frappe-datatable versions; setting both is
	// harmless — the unrecognised one is ignored.)
	get_datatable_options(datatable_options) {
		datatable_options.freezeColumns = 1;
		datatable_options.freezeColumn = 1;
		return datatable_options;
	},

	after_datatable_render() {
		// Dataset changed -> drop the cached per-column maxima.
		frappe.query_report._spm_col_max = null;
	},

	onload(report) {
		// Delegated drill-down: clicking a populated cell opens the underlying
		// Sales Invoices. We ask the server (reusing the report's own SQL) for the
		// exact invoice names, then route to the SI list filtered to them.
		$(report.page.wrapper).on("click", "a.spm-cell", function (e) {
			e.preventDefault();
			e.stopPropagation();
			const $a = $(this);
			// Read as raw attribute strings (not .data()) to avoid jQuery coercing
			// numeric-looking dimension values to Numbers.
			const row_value = $a.attr("data-row") || "";
			const col_value = $a.attr("data-col") || "";

			frappe.call({
				method: `${SPM_METHOD}.get_drilldown_invoices`,
				args: {
					filters: report.get_filter_values(),
					row_value: row_value,
					col_value: col_value,
				},
				freeze: true,
				freeze_message: __("Finding invoices…"),
				callback(r) {
					const names = r.message || [];
					if (!names.length) {
						frappe.msgprint(__("No Sales Invoices found for this cell."));
						return;
					}
					frappe.route_options = { name: ["in", names] };
					frappe.set_route("List", "Sales Invoice");
				},
			});
		});

		report.page.add_inner_button(
			__("Export to Excel"),
			() => frappe.query_report.export_report(),
			__("Actions")
		);
	},
};
