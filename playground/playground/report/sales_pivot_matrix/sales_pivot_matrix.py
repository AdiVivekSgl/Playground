# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Sales Pivot Matrix
==================

A pivot-table style view of sales built from submitted Sales Invoices. The user
picks a *row* dimension, a *column* dimension and a *measure*; the report returns
a matrix where each cell is the aggregated measure for that (row, column) pair,
with a per-row total column and a grand-total row.

Design goals (from the brief)
-----------------------------
* **Metadata driven** - every selectable dimension / measure is one entry in the
  ``ROW_DIMENSIONS`` / ``COLUMN_DIMENSIONS`` / ``MEASURES`` registries below.
  Adding a new dimension is a dictionary entry, not a rewrite of the query.
* **Dynamic columns** - the column set is whatever distinct values the *filtered*
  dataset actually contains (e.g. the states that appear), discovered in one
  ``SELECT DISTINCT`` pass.
* **One aggregation query** - the matrix is produced by a single
  ``SUM(CASE WHEN col = 'X' ...)`` / ``COUNT(DISTINCT CASE WHEN ...)`` statement.
  There is never one query per column, so the report stays fast on large data.
* **Drill-down** - :func:`get_drilldown_invoices` reuses the exact same JOIN/WHERE
  builder to return the Sales Invoice names behind any single cell, so clicking a
  cell opens precisely those invoices in the standard list view.

Money is aggregated in **company currency** (``base_*`` fields) so multi-currency
invoices roll up correctly.
"""

import frappe
from frappe import _
from frappe.utils import flt, cint


# --------------------------------------------------------------------------- #
# Sentinels / limits
# --------------------------------------------------------------------------- #

# Column value used internally for rows whose dimension value is NULL / blank
# (an invoice with no shipping state, a customer with no group, ...). Rendered as
# "(Not Set)" and always sorted last.
NONE_KEY = "__NONE__"
NONE_LABEL = _("(Not Set)")

# Hard ceiling on generated columns - a runaway column dimension (e.g. grouping
# by a near-unique field) would otherwise produce an unusable, slow matrix.
MAX_COLUMNS = 200

# Cap on invoices returned by a single drill-down (route_options `name in [...]`).
DRILLDOWN_LIMIT = 1000


# --------------------------------------------------------------------------- #
# Metadata registries - extend these to add dimensions / measures
# --------------------------------------------------------------------------- #
#
# Each dimension maps to a SQL expression plus the JOIN(s) that expression needs.
# `needs` names optional joins the builder should switch on (see JOIN_LIBRARY).

ROW_DIMENSIONS = {
	"customer": {
		"label": _("Customer"),
		"expr": "si.customer",
		"link": "Customer",
	},
	"customer_group": {
		"label": _("Customer Group"),
		"expr": "si.customer_group",
		"link": "Customer Group",
	},
	"territory": {
		"label": _("Territory"),
		"expr": "si.territory",
		"link": "Territory",
	},
	"sales_person": {
		"label": _("Sales Person"),
		"expr": "st.sales_person",
		"link": "Sales Person",
		"needs": ["sales_team"],
	},
}

COLUMN_DIMENSIONS = {
	"shipping_state": {
		"label": _("Shipping State"),
		# Sales Invoice -> Shipping Address (shipping_address_name) -> Address.state
		"expr": "addr_ship.state",
		"needs": ["shipping_address"],
	},
	"billing_state": {
		"label": _("Billing State"),
		# Sales Invoice -> Billing Address (customer_address) -> Address.state
		"expr": "addr_bill.state",
		"needs": ["billing_address"],
	},
	"territory": {
		"label": _("Territory"),
		"expr": "si.territory",
	},
	"customer_group": {
		"label": _("Customer Group"),
		"expr": "si.customer_group",
	},
}

# All monetary measures are TAX-EXCLUSIVE by design (the report shows Net Totals,
# never grand totals). `base_net_amount` is the item's taxable/net figure - the
# sum across items is exactly the Sales Invoice `base_net_total` ("Net Total"),
# i.e. after discounts and BEFORE any taxes. The tax-inclusive figures
# (`base_grand_total`) are deliberately never used.
MEASURES = {
	"net": {
		"label": _("Net Sales Amount"),
		# Net Total basis: excludes taxes, includes discounts.
		"expr": "sii.base_net_amount",
		"kind": "currency",
		"apportion": True,  # split by Sales Person allocation when relevant
	},
	"gross": {
		"label": _("Gross Sales Amount"),
		# Line amount before invoice-level additional discount, still excluding
		# taxes (in the standard exclusive-tax setup). Use "Net Sales Amount" if
		# taxes are configured as included-in-rate, where base_amount carries tax.
		"expr": "sii.base_amount",
		"kind": "currency",
		"apportion": True,
	},
	"qty": {
		"label": _("Quantity Sold"),
		"expr": "sii.qty",
		"kind": "float",
		"apportion": True,
	},
	"count": {
		"label": _("Invoice Count"),
		# Special-cased: counted with COUNT(DISTINCT si.name) so the item-level
		# JOIN never inflates the number.
		"expr": "si.name",
		"kind": "count",
		"apportion": False,
	},
}

# Optional JOIN fragments, switched on by a dimension's `needs` (or by filters).
# LEFT joins throughout so invoices missing the linked record still appear (their
# value falls into the "(Not Set)" bucket) rather than being silently dropped.
JOIN_LIBRARY = {
	"shipping_address": "LEFT JOIN `tabAddress` addr_ship ON addr_ship.name = si.shipping_address_name",
	"billing_address": "LEFT JOIN `tabAddress` addr_bill ON addr_bill.name = si.customer_address",
	"sales_team": "LEFT JOIN `tabSales Team` st ON st.parent = si.name",
}


def execute(filters=None):
	return SalesPivotMatrix(frappe._dict(filters or {})).run()


class SalesPivotMatrix:
	def __init__(self, filters):
		self.filters = filters
		self.row_key = filters.get("rows_by") or "customer"
		self.col_key = filters.get("columns_by") or "shipping_state"
		self.measure_key = filters.get("measure") or "net"

		# Validate against the registries so a stale saved filter can't inject an
		# unknown key into the SQL. Fall back to the documented defaults.
		if self.row_key not in ROW_DIMENSIONS:
			self.row_key = "customer"
		if self.col_key not in COLUMN_DIMENSIONS:
			self.col_key = "shipping_state"
		if self.measure_key not in MEASURES:
			self.measure_key = "net"

		self.row_dim = ROW_DIMENSIONS[self.row_key]
		self.col_dim = COLUMN_DIMENSIONS[self.col_key]
		self.measure = MEASURES[self.measure_key]

	# ------------------------------------------------------------------ #
	# Orchestration
	# ------------------------------------------------------------------ #

	def run(self):
		if not (self.filters.get("from_date") and self.filters.get("to_date")):
			frappe.msgprint(_("Please set both From Date and To Date."), indicator="orange", alert=True)
			return self._empty_columns(), []

		joins, where, params = self.build_query_context()

		# Pass 1 - discover the columns that actually occur in the filtered set.
		col_values = self.get_distinct_columns(joins, where, params)
		if not col_values:
			return self._empty_columns(), []

		# Pass 2 - the single aggregation query producing the whole matrix.
		rows = self.aggregate(joins, where, params, col_values)

		# Post-processing that is cleaner in Python than SQL: threshold, Top N,
		# zero-column pruning, and the grand-total row.
		rows, col_values = self.post_process(rows, col_values)

		columns = self.build_columns(col_values)
		return columns, rows

	# ------------------------------------------------------------------ #
	# JOIN / WHERE builder (shared by aggregation and drill-down)
	# ------------------------------------------------------------------ #

	def build_query_context(self, extra_needs=None):
		"""Return ``(join_sql, where_sql, params)`` honouring every filter.

		`extra_needs` lets the drill-down force a join it needs to *filter* on a
		column dimension even when that dimension isn't otherwise selected.
		"""
		f = self.filters
		params = {}

		# Collect the joins required by the chosen dimensions (+ any extras).
		needs = set(self.row_dim.get("needs", []))
		needs |= set(self.col_dim.get("needs", []))
		if extra_needs:
			needs |= set(extra_needs)

		# A Sales Person filter needs the Sales Team join only when it isn't
		# already grouping by sales person; otherwise it's an EXISTS sub-query so
		# amounts aren't multiplied across an invoice's team members.
		self.sales_team_joined = "sales_team" in needs

		conditions = ["si.docstatus = 1"]

		if f.get("company"):
			conditions.append("si.company = %(company)s")
			params["company"] = f.company

		conditions.append("si.posting_date BETWEEN %(from_date)s AND %(to_date)s")
		params["from_date"] = f.from_date
		params["to_date"] = f.to_date

		# Straightforward header / item equality filters.
		simple = {
			"customer": "si.customer",
			"customer_group": "si.customer_group",
			"territory": "si.territory",
			"project": "si.project",
			"item_group": "sii.item_group",
			"item": "sii.item_code",
			"cost_center": "sii.cost_center",
		}
		for field, column in simple.items():
			if f.get(field):
				conditions.append(f"{column} = %({field})s")
				params[field] = f.get(field)

		# Optional State pre-filter. Applied against the shipping address (the
		# report's default geography); the join is forced on when needed.
		if f.get("state"):
			if "shipping_address" not in needs:
				needs.add("shipping_address")
			conditions.append("addr_ship.state = %(state)s")
			params["state"] = f.state

		# Sales Person filter.
		if f.get("sales_person"):
			if self.sales_team_joined:
				conditions.append("st.sales_person = %(sales_person)s")
			else:
				conditions.append(
					"EXISTS (SELECT 1 FROM `tabSales Team` stf "
					"WHERE stf.parent = si.name AND stf.sales_person = %(sales_person)s)"
				)
			params["sales_person"] = f.sales_person

		# Re-evaluate after the State pre-filter may have added a join.
		self.sales_team_joined = "sales_team" in needs

		join_sql = "INNER JOIN `tabSales Invoice Item` sii ON sii.parent = si.name"
		for need in needs:
			join_sql += "\n\t\t\t" + JOIN_LIBRARY[need]

		return join_sql, " AND ".join(conditions), params

	# ------------------------------------------------------------------ #
	# Column value expression helpers
	# ------------------------------------------------------------------ #

	def _col_norm_expr(self):
		"""SQL that normalises the column dimension to its display/bucket value:
		NULL or blank collapses to the NONE_KEY sentinel."""
		return f"COALESCE(NULLIF(TRIM({self.col_dim['expr']}), ''), '{NONE_KEY}')"

	def _row_norm_expr(self):
		return f"COALESCE(NULLIF(TRIM({self.row_dim['expr']}), ''), '{NONE_KEY}')"

	# ------------------------------------------------------------------ #
	# Pass 1 - distinct columns
	# ------------------------------------------------------------------ #

	def get_distinct_columns(self, joins, where, params):
		rows = frappe.db.sql(
			f"""
			SELECT DISTINCT {self._col_norm_expr()} AS col_val
			FROM `tabSales Invoice` si
			{joins}
			WHERE {where}
			ORDER BY col_val
			""",
			params,
		)
		values = [r[0] for r in rows if r[0] is not None]

		# Keep "(Not Set)" but always push it to the far right of the matrix.
		values = [v for v in values if v != NONE_KEY]
		values.sort(key=lambda v: (v or "").lower())
		has_none = any(r[0] == NONE_KEY for r in rows)
		if has_none:
			values.append(NONE_KEY)

		if len(values) > MAX_COLUMNS:
			frappe.msgprint(
				_("The column dimension produced {0} values; showing the first {1}. "
				  "Narrow the filters or pick a coarser Columns By.").format(len(values), MAX_COLUMNS),
				indicator="orange",
				alert=True,
			)
			values = values[:MAX_COLUMNS]
		return values

	# ------------------------------------------------------------------ #
	# Pass 2 - the single aggregation query
	# ------------------------------------------------------------------ #

	def _measure_base_expr(self):
		"""The per-row measure expression, apportioned by Sales Team allocation
		when a Sales Person dimension has joined the (multi-row) Sales Team."""
		expr = self.measure["expr"]
		if self.measure.get("apportion") and self.sales_team_joined:
			# An invoice shared across N sales people contributes its
			# allocated_percentage share to each. Missing allocation -> 100%.
			return f"({expr} * COALESCE(st.allocated_percentage, 100) / 100)"
		return expr

	def _cell_expr(self, match_sql):
		"""Aggregate expression for one matrix cell, given the SQL that is true
		when a source row belongs to that cell's column."""
		if self.measure["kind"] == "count":
			return f"COUNT(DISTINCT CASE WHEN {match_sql} THEN si.name END)"
		return f"SUM(CASE WHEN {match_sql} THEN {self._measure_base_expr()} ELSE 0 END)"

	def _total_expr(self):
		if self.measure["kind"] == "count":
			return "COUNT(DISTINCT si.name)"
		return f"SUM({self._measure_base_expr()})"

	def aggregate(self, joins, where, params, col_values):
		col_norm = self._col_norm_expr()

		# One SUM(CASE ...)/COUNT(DISTINCT CASE ...) per discovered column value.
		# Column values are bound as parameters (cv0, cv1, ...) - never inlined -
		# so state names etc. can never break or inject into the SQL.
		cell_selects = []
		for idx, value in enumerate(col_values):
			pname = f"cv{idx}"
			params[pname] = value
			match_sql = f"{col_norm} = %({pname})s"
			cell_selects.append(f"{self._cell_expr(match_sql)} AS c{idx}")

		select_clause = ",\n\t\t\t\t".join(cell_selects)

		data = frappe.db.sql(
			f"""
			SELECT
				{self._row_norm_expr()} AS row_value,
				{select_clause},
				{self._total_expr()} AS total
			FROM `tabSales Invoice` si
			{joins}
			WHERE {where}
			GROUP BY row_value
			""",
			params,
			as_dict=True,
		)

		rows = []
		for d in data:
			raw = d.get("row_value")
			row = {
				"row_label": NONE_LABEL if raw == NONE_KEY else raw,
				"_row_value": None if raw == NONE_KEY else raw,
				"total": flt(d.get("total")),
				"is_total": 0,
			}
			for idx in range(len(col_values)):
				row[f"c{idx}"] = flt(d.get(f"c{idx}"))
			rows.append(row)
		return rows

	# ------------------------------------------------------------------ #
	# Post-processing: threshold, Top N, prune, grand total
	# ------------------------------------------------------------------ #

	def post_process(self, rows, col_values):
		f = self.filters

		# Minimum-sales threshold on the row total.
		min_total = flt(f.get("min_total"))
		if min_total:
			rows = [r for r in rows if flt(r["total"]) >= min_total]

		# Highest totals first - the useful default for management triage and a
		# prerequisite for a meaningful "Top N".
		rows.sort(key=lambda r: flt(r["total"]), reverse=True)

		top_n = cint(f.get("top_n"))
		if top_n and len(rows) > top_n:
			rows = rows[:top_n]

		# Optionally drop columns that are entirely zero for the surviving rows.
		keep_idx = list(range(len(col_values)))
		if cint(f.get("hide_zero_columns")):
			keep_idx = [
				i for i in range(len(col_values))
				if any(flt(r.get(f"c{i}")) for r in rows)
			]
			if keep_idx != list(range(len(col_values))):
				rows, col_values = self._reindex_columns(rows, col_values, keep_idx)

		# Grand-total row, summed from exactly what is displayed so it always ties
		# out to the visible cells. (For a Sales Person dimension an invoice can
		# span rows; apportionment keeps the money total exact, and the Invoice
		# Count total is a sum of per-person counts by design.)
		if rows:
			total_row = {"row_label": _("Grand Total"), "_row_value": None, "is_total": 1}
			for i in range(len(col_values)):
				total_row[f"c{i}"] = sum(flt(r.get(f"c{i}")) for r in rows)
			total_row["total"] = sum(flt(r["total"]) for r in rows)
			rows.append(total_row)

		return rows, col_values

	@staticmethod
	def _reindex_columns(rows, col_values, keep_idx):
		"""Compact the c0..cN fields down to only the kept columns so the emitted
		column list and the row dicts stay in lock-step."""
		new_col_values = [col_values[i] for i in keep_idx]
		new_rows = []
		for r in rows:
			nr = {k: v for k, v in r.items() if not k.startswith("c") or not k[1:].isdigit()}
			for new_i, old_i in enumerate(keep_idx):
				nr[f"c{new_i}"] = r.get(f"c{old_i}")
			new_rows.append(nr)
		return new_rows, new_col_values

	# ------------------------------------------------------------------ #
	# Columns
	# ------------------------------------------------------------------ #

	def _measure_fieldtype(self):
		return {"currency": "Currency", "float": "Float", "count": "Int"}[self.measure["kind"]]

	def build_columns(self, col_values):
		fieldtype = self._measure_fieldtype()
		width = 130 if self.measure["kind"] != "count" else 90

		columns = [
			{
				"label": self.row_dim["label"],
				"fieldname": "row_label",
				"fieldtype": "Data",
				"width": 200,
			}
		]
		for idx, value in enumerate(col_values):
			columns.append(
				{
					"label": NONE_LABEL if value == NONE_KEY else value,
					"fieldname": f"c{idx}",
					"fieldtype": fieldtype,
					"width": width,
					# Raw dimension value carried to the client for drill-down.
					# None for the "(Not Set)" bucket.
					"col_value": None if value == NONE_KEY else value,
				}
			)
		columns.append(
			{
				"label": _("Total"),
				"fieldname": "total",
				"fieldtype": fieldtype,
				"width": 150,
			}
		)
		return columns

	def _empty_columns(self):
		return [
			{"label": self.row_dim["label"], "fieldname": "row_label", "fieldtype": "Data", "width": 200},
			{"label": _("Total"), "fieldname": "total", "fieldtype": self._measure_fieldtype(), "width": 150},
		]


# --------------------------------------------------------------------------- #
# Drill-down
# --------------------------------------------------------------------------- #

@frappe.whitelist()
def get_drilldown_invoices(filters, row_value=None, col_value=None):
	"""Return the Sales Invoice names behind a single matrix cell.

	The client passes the report `filters` plus the raw row/column dimension
	values of the clicked cell. We rebuild the *same* JOIN/WHERE the matrix used,
	pin the row and column dimensions to the clicked values, and return the
	distinct invoice names so the caller can open exactly those in the SI list
	(``frappe.route_options = {name: ["in", names]}``).
	"""
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	engine = SalesPivotMatrix(frappe._dict(filters))

	# The column dimension may need a join that the current selection didn't (it
	# always does here since we filter on it), so ask for it explicitly.
	extra = engine.col_dim.get("needs", [])
	joins, where, params = engine.build_query_context(extra_needs=extra)

	conditions = [where]

	# Pin the row dimension. Empty value means the "(Not Set)" bucket.
	if row_value in (None, "", NONE_KEY):
		conditions.append(f"({engine._row_norm_expr()}) = '{NONE_KEY}'")
	else:
		conditions.append(f"({engine._row_norm_expr()}) = %(dd_row)s")
		params["dd_row"] = row_value

	# Pin the column dimension.
	if col_value in (None, "", NONE_KEY):
		conditions.append(f"({engine._col_norm_expr()}) = '{NONE_KEY}'")
	else:
		conditions.append(f"({engine._col_norm_expr()}) = %(dd_col)s")
		params["dd_col"] = col_value

	names = frappe.db.sql(
		f"""
		SELECT DISTINCT si.name
		FROM `tabSales Invoice` si
		{joins}
		WHERE {" AND ".join(conditions)}
		ORDER BY si.name
		LIMIT {DRILLDOWN_LIMIT}
		""",
		params,
	)
	return [n[0] for n in names]
