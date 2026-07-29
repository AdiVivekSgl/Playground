# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
FTPL - Customer Commercial Profile Builder
==========================================

Infers the default commercial settings a Customer Master *should* carry by
mining its historical Sales Orders (with Sales Invoice / Payment Entry as a
secondary source), then lets an administrator push those inferred defaults back
onto the Customer with one click.

Many customers were created without a Default Selling Price List, Payment Terms
Template, Default Currency, Sales Person, Territory or Tax Category - instead
those values evolved organically across years of transactions. This report
reconstructs the *most commonly used* value per dimension per customer, scores
how consistent that usage was (a confidence %), and surfaces the gap between the
Customer Master and its own transaction history.

Output (a single Script Report `execute` return: columns, data, message, chart,
report_summary):

  * columns + data  -> one row per Customer: current vs suggested value for each
                       commercial dimension, confidence %, distinct-values-used,
                       payment behaviour, and an overall Recommendation Status.
  * report_summary  -> headline cards (customers analysed, how many are missing
                       defaults, average confidence, ...).
  * message (HTML)  -> a short legend explaining the confidence colours and the
                       Recommendation Status values.

Bulk apply (whitelisted `apply_customer_defaults`) writes the chosen suggestions
back onto the Customer, and by default NEVER overwrites a value the Customer
already has - only blank fields are filled - unless the caller passes
overwrite=1 (the report's "Overwrite Existing Values" toolbar toggle).

SQL strategy (no N+1)
---------------------
Every aggregate is a single grouped statement over the whole filtered set,
never one-query-per-customer:

  * one summary query      GROUP BY customer  -> counts, sums, min/max dates,
                                                 latest shipping/billing address.
  * one mode query per     GROUP BY customer, <field>  -> reduced in Python to
    commercial dimension    the modal value + its share (confidence) + distinct.
  * one payment query      GROUP BY customer  -> avg actual payment days vs avg
                                                 configured term days.
  * one Customer read + one Sales Team read (current values), both `name IN (...)`.

The modal value and confidence are computed in Python from the per-value counts
(a handful of rows per customer), which keeps the SQL portable and the mode
logic obvious.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

# The commercial dimensions inferred from Sales Order header fields. Maps the
# report's short key -> (Sales Order column, Customer Master column). The
# Customer column is None for dimensions we only *display* a suggestion for and
# never write back through the one-click apply (Company, Sales Person - the
# latter lives in a child table, not a scalar field).
SO_FIELD = "so_field"
CUSTOMER_FIELD = "customer_field"

DIMENSIONS = {
	"price_list": {SO_FIELD: "selling_price_list", CUSTOMER_FIELD: "default_price_list"},
	"payment_terms": {SO_FIELD: "payment_terms_template", CUSTOMER_FIELD: "payment_terms"},
	"currency": {SO_FIELD: "currency", CUSTOMER_FIELD: "default_currency"},
	"territory": {SO_FIELD: "territory", CUSTOMER_FIELD: "territory"},
	"tax_category": {SO_FIELD: "tax_category", CUSTOMER_FIELD: "tax_category"},
	"company": {SO_FIELD: "company", CUSTOMER_FIELD: None},
}

# The Customer Master fields the one-click apply is allowed to write, mapped to a
# friendly label used in messages (wrapped in _() at the point of use, not here -
# translating at module import happens before any request/user context exists).
# Kept deliberately narrow: only settings that are safe, scalar defaults. Sales
# Person / Company are advisory-only above.
APPLYABLE_FIELDS = {
	"default_price_list": "Default Price List",
	"payment_terms": "Payment Terms Template",
	"default_currency": "Default Currency",
	"territory": "Territory",
	"tax_category": "Tax Category",
}

# The five defaults whose absence marks a customer as "missing defaults" (drives
# the Show Only Missing Defaults filter and the Recommendation Status).
KEY_CUSTOMER_FIELDS = [
	"default_price_list",
	"payment_terms",
	"default_currency",
	"territory",
	"tax_category",
]


def execute(filters=None):
	return CustomerCommercialProfile(filters).run()


class CustomerCommercialProfile:
	def __init__(self, filters=None):
		self.filters = frappe._dict(filters or {})
		self.company = self.filters.get("company")
		self.min_orders = cint(self.filters.get("minimum_orders")) or 1
		self.threshold = flt(self.filters.get("confidence_threshold")) or 0.0

		# Shared SQL params for every Sales Order aggregate. Conditions are built
		# with an alias so the same WHERE can front different table aliases.
		self.params = {
			"company": self.company,
			"from_date": self.filters.get("from_date"),
			"to_date": self.filters.get("to_date"),
		}
		if self.filters.get("customer"):
			self.params["customer"] = self.filters.get("customer")
		if self.filters.get("territory"):
			self.params["territory"] = self.filters.get("territory")
		if self.filters.get("sales_person"):
			self.params["sales_person"] = self.filters.get("sales_person")

	# ------------------------------------------------------------------ #
	# Orchestration
	# ------------------------------------------------------------------ #

	def run(self):
		if not self.company:
			frappe.msgprint(_("Please select a Company."), indicator="orange", alert=True)
			return self.get_columns(), []

		# 1. Per-customer transactional summary (also the master list of which
		#    customers clear the Minimum Orders bar and therefore appear at all).
		self.summary = self.get_summary()
		self.customers = list(self.summary.keys())
		if not self.customers:
			frappe.msgprint(
				_("No submitted Sales Orders match these filters."), indicator="orange", alert=True
			)
			return self.get_columns(), []

		# 2. Modal value + confidence for each commercial dimension.
		self.modes = {key: self.get_mode_map(key) for key in DIMENSIONS}
		self.modes["sales_person"] = self.get_sales_person_mode_map()

		# 3. Current Customer Master values + current (primary) sales person.
		self.current = self.get_current_customer_values()
		self.current_sales_person = self.get_current_sales_person()

		# 4. Payment behaviour (secondary source).
		self.payment = self.get_payment_behaviour()

		rows = self.build_rows()
		message = self.build_message()
		report_summary = self.build_report_summary(rows)
		return self.get_columns(), rows, message, None, report_summary

	# ------------------------------------------------------------------ #
	# WHERE builder (shared across every Sales Order aggregate)
	# ------------------------------------------------------------------ #

	def _so_conditions(self, alias="so"):
		"""Common Sales Order WHERE fragment for `alias`. Only submitted orders in
		the date window for the selected company, plus the optional Customer /
		Territory / Sales Person narrowing. Returns a SQL string; params live on
		self.params (shared, alias-independent)."""
		conds = [
			f"{alias}.docstatus = 1",
			f"{alias}.company = %(company)s",
			f"{alias}.transaction_date BETWEEN %(from_date)s AND %(to_date)s",
		]
		if "customer" in self.params:
			conds.append(f"{alias}.customer = %(customer)s")
		if "territory" in self.params:
			conds.append(f"{alias}.territory = %(territory)s")
		if "sales_person" in self.params:
			# Sales Person lives in the Sales Team child table; narrow to orders
			# that carry the person on their team.
			# Alias st_f (not st) so this never collides with an outer `tabSales
			# Team st` in the sales-person mode query.
			conds.append(
				f"""EXISTS (
					SELECT 1 FROM `tabSales Team` st_f
					WHERE st_f.parent = {alias}.name AND st_f.parenttype = 'Sales Order'
						AND st_f.sales_person = %(sales_person)s
				)"""
			)
		return " AND ".join(conds)

	# ------------------------------------------------------------------ #
	# 1. Per-customer summary
	# ------------------------------------------------------------------ #

	def get_summary(self):
		"""COUNT / SUM / AVG / MIN / MAX per customer, plus the latest shipping and
		billing address. Latest address uses SUBSTRING_INDEX(GROUP_CONCAT(... ORDER
		BY transaction_date DESC), 1) - the newest value sorts first, so even if
		GROUP_CONCAT truncates at group_concat_max_len the first (latest) element is
		always intact. Minimum Orders is enforced here via HAVING."""
		rows = frappe.db.sql(
			f"""
			SELECT
				so.customer AS customer,
				COUNT(*) AS orders,
				MIN(so.transaction_date) AS first_order,
				MAX(so.transaction_date) AS last_order,
				SUM(so.base_grand_total) AS total_sales,
				AVG(so.base_grand_total) AS avg_order_value,
				SUBSTRING_INDEX(
					GROUP_CONCAT(so.shipping_address_name ORDER BY so.transaction_date DESC SEPARATOR '\n'),
					'\n', 1
				) AS latest_shipping_address,
				SUBSTRING_INDEX(
					GROUP_CONCAT(so.customer_address ORDER BY so.transaction_date DESC SEPARATOR '\n'),
					'\n', 1
				) AS latest_billing_address
			FROM `tabSales Order` so
			WHERE {self._so_conditions('so')}
			GROUP BY so.customer
			HAVING orders >= %(min_orders)s
			""",
			{**self.params, "min_orders": self.min_orders},
			as_dict=True,
		)
		return {r.customer: r for r in rows}

	# ------------------------------------------------------------------ #
	# 2. Modal value + confidence per dimension
	# ------------------------------------------------------------------ #

	def get_mode_map(self, key):
		"""For a header-level Sales Order dimension, return
		{customer: _dict(value, count, total, distinct)} where `value` is the most
		frequently used non-blank value and confidence = count / total. `total` is
		the number of orders that carried *any* value for this dimension (the
		denominator in the brief's example: 28 / 31)."""
		field = DIMENSIONS[key][SO_FIELD]
		# `field` is a fixed constant from DIMENSIONS, never user input, so it is
		# safe to inline; every runtime value is still bound as a param.
		rows = frappe.db.sql(
			f"""
			SELECT so.customer AS customer, so.{field} AS val, COUNT(*) AS c
			FROM `tabSales Order` so
			WHERE {self._so_conditions('so')}
				AND so.{field} IS NOT NULL AND so.{field} != ''
			GROUP BY so.customer, so.{field}
			""",
			self.params,
			as_dict=True,
		)
		return self._reduce_to_mode(rows)

	def get_sales_person_mode_map(self):
		"""Same idea as get_mode_map but sourced from the Sales Team child table
		(a Sales Order can name several sales persons; each Team row counts once)."""
		rows = frappe.db.sql(
			f"""
			SELECT so.customer AS customer, st.sales_person AS val, COUNT(*) AS c
			FROM `tabSales Team` st
			INNER JOIN `tabSales Order` so
				ON so.name = st.parent AND st.parenttype = 'Sales Order'
			WHERE {self._so_conditions('so')}
				AND st.sales_person IS NOT NULL AND st.sales_person != ''
			GROUP BY so.customer, st.sales_person
			""",
			self.params,
			as_dict=True,
		)
		return self._reduce_to_mode(rows)

	@staticmethod
	def _reduce_to_mode(rows):
		"""Collapse per-(customer, value) counts into the modal value per customer.
		Ties break on the alphabetically-first value so the result is stable run to
		run rather than dependent on row order."""
		by_customer = {}
		for r in rows:
			by_customer.setdefault(r.customer, []).append((r.val, cint(r.c)))

		result = {}
		for customer, pairs in by_customer.items():
			total = sum(c for _v, c in pairs)
			# Highest count wins; tie-break on the value for determinism.
			value, count = max(pairs, key=lambda p: (p[1], _neg_str(p[0])))
			result[customer] = frappe._dict(
				{
					"value": value,
					"count": count,
					"total": total,
					"distinct": len(pairs),
					"confidence": (count / total * 100.0) if total else 0.0,
				}
			)
		return result

	# ------------------------------------------------------------------ #
	# 3. Current Customer Master values
	# ------------------------------------------------------------------ #

	def get_current_customer_values(self):
		rows = frappe.db.sql(
			"""
			SELECT
				name, customer_name, customer_group, territory, disabled,
				default_price_list, payment_terms, default_currency, tax_category
			FROM `tabCustomer`
			WHERE name IN %(customers)s
			""",
			{"customers": tuple(self.customers)},
			as_dict=True,
		)
		return {r.name: r for r in rows}

	def get_current_sales_person(self):
		"""The Customer's own default sales person = the top-allocated row of its
		Sales Team child table (Customers can carry a Sales Team just like orders)."""
		rows = frappe.db.sql(
			"""
			SELECT parent AS customer, sales_person, allocated_percentage
			FROM `tabSales Team`
			WHERE parenttype = 'Customer' AND parent IN %(customers)s
			ORDER BY allocated_percentage DESC
			""",
			{"customers": tuple(self.customers)},
			as_dict=True,
		)
		# First row per customer wins (already ordered by allocation desc).
		out = {}
		for r in rows:
			out.setdefault(r.customer, r.sales_person)
		return out

	# ------------------------------------------------------------------ #
	# 4. Payment behaviour (secondary source)
	# ------------------------------------------------------------------ #

	def get_payment_behaviour(self):
		"""Average *actual* days to payment vs average *configured* term days, per
		customer, from submitted Sales Invoices matched to submitted Payment Entries
		through Payment Entry Reference.

		  actual  = DATEDIFF(payment posting_date, invoice posting_date)
		  term    = DATEDIFF(invoice due_date,     invoice posting_date)

		A single grouped join (no per-invoice lookups). Restricted to the customers
		already in scope so the join stays bounded."""
		rows = frappe.db.sql(
			"""
			SELECT
				si.customer AS customer,
				AVG(DATEDIFF(pe.posting_date, si.posting_date)) AS avg_payment_days,
				AVG(DATEDIFF(si.due_date, si.posting_date)) AS avg_term_days,
				COUNT(DISTINCT si.name) AS paid_invoices
			FROM `tabSales Invoice` si
			INNER JOIN `tabPayment Entry Reference` per
				ON per.reference_doctype = 'Sales Invoice'
				AND per.reference_name = si.name
				AND per.docstatus = 1
			INNER JOIN `tabPayment Entry` pe
				ON pe.name = per.parent AND pe.docstatus = 1
			WHERE si.docstatus = 1
				AND si.company = %(company)s
				AND si.customer IN %(customers)s
				AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
			GROUP BY si.customer
			""",
			{
				"company": self.company,
				"customers": tuple(self.customers),
				"from_date": self.params["from_date"],
				"to_date": self.params["to_date"],
			},
			as_dict=True,
		)
		return {r.customer: r for r in rows}

	# ------------------------------------------------------------------ #
	# Row assembly
	# ------------------------------------------------------------------ #

	def build_rows(self):
		show_only_missing = cint(self.filters.get("show_only_missing", 1))
		include_disabled = cint(self.filters.get("include_disabled"))

		rows = []
		for customer in self.customers:
			cust = self.current.get(customer) or frappe._dict()

			if cint(cust.get("disabled")) and not include_disabled:
				continue

			row = self._build_row(customer, cust)

			if show_only_missing and not row["_missing_count"]:
				continue

			rows.append(row)

		# Biggest opportunities first: most orders (most historical evidence) on top.
		rows.sort(key=lambda r: flt(r.get("orders")), reverse=True)
		for r in rows:
			r.pop("_missing_count", None)
		return rows

	def _build_row(self, customer, cust):
		summ = self.summary.get(customer) or frappe._dict()

		def sugg(key):
			return self.modes.get(key, {}).get(customer) or frappe._dict(
				{"value": None, "confidence": 0.0, "distinct": 0}
			)

		price = sugg("price_list")
		terms = sugg("payment_terms")
		currency = sugg("currency")
		territory = sugg("territory")
		tax = sugg("tax_category")
		company = sugg("company")
		sperson = sugg("sales_person")

		pay = self.payment.get(customer) or frappe._dict()
		avg_pay = flt(pay.get("avg_payment_days")) if pay.get("avg_payment_days") is not None else None
		avg_term = flt(pay.get("avg_term_days")) if pay.get("avg_term_days") is not None else None
		pay_diff = (avg_pay - avg_term) if (avg_pay is not None and avg_term is not None) else None

		# Which key defaults are blank on the Master AND have a suggestion to fill.
		missing = [
			f
			for f in KEY_CUSTOMER_FIELDS
			if not cust.get(f) and self._suggestion_for_field(customer, f)
		]

		row = {
			"customer": customer,
			"customer_name": cust.get("customer_name"),
			"customer_group": cust.get("customer_group"),
			"status": _("Disabled") if cint(cust.get("disabled")) else _("Enabled"),
			# Sales summary
			"orders": cint(summ.get("orders")),
			"first_order": summ.get("first_order"),
			"last_order": summ.get("last_order"),
			"total_sales": flt(summ.get("total_sales")),
			"avg_order_value": flt(summ.get("avg_order_value")),
			# Price list
			"current_price_list": cust.get("default_price_list"),
			"suggested_price_list": price.value,
			"price_list_confidence": flt(price.confidence),
			"distinct_price_lists": cint(price.distinct),
			# Payment terms
			"current_payment_terms": cust.get("payment_terms"),
			"suggested_payment_terms": terms.value,
			"payment_terms_confidence": flt(terms.confidence),
			"distinct_payment_terms": cint(terms.distinct),
			"avg_payment_days": avg_pay,
			"avg_term_days": avg_term,
			"payment_diff": pay_diff,
			# Currency
			"current_currency": cust.get("default_currency"),
			"suggested_currency": currency.value,
			# Territory
			"current_territory": cust.get("territory"),
			"suggested_territory": territory.value,
			# Sales person (advisory)
			"current_sales_person": self.current_sales_person.get(customer),
			"suggested_sales_person": sperson.value,
			# Tax category
			"current_tax_category": cust.get("tax_category"),
			"suggested_tax_category": tax.value,
			# Company (advisory)
			"suggested_company": company.value,
			# Addresses
			"latest_shipping_address": summ.get("latest_shipping_address"),
			"latest_billing_address": summ.get("latest_billing_address"),
			"_missing_count": len(missing),
		}
		row["recommendation_status"] = self._recommendation_status(row, missing)
		return row

	def _suggestion_for_field(self, customer, customer_field):
		"""The modal value that would be written to `customer_field`, if any."""
		for key, spec in DIMENSIONS.items():
			if spec[CUSTOMER_FIELD] == customer_field:
				return (self.modes.get(key, {}).get(customer) or frappe._dict()).get("value")
		return None

	def _recommendation_status(self, row, missing):
		"""One overall verdict per customer, in priority order:

		  Missing        -> at least one key default is blank and fillable.
		  Needs Review   -> a key default is set but disagrees with history.
		  Mixed Usage    -> history itself is scattered (>= 3 distinct values on a
		                    primary dimension) - infer with care.
		  Low Confidence -> the modal value is below the Confidence Threshold.
		  Consistent     -> Master matches its own history with high confidence.
		"""
		if missing:
			return _("Missing")

		# Master set but disagrees with the modal historical value.
		disagreements = []
		for key, spec in DIMENSIONS.items():
			cfield = spec[CUSTOMER_FIELD]
			if not cfield:
				continue
			current = row.get(_current_col(cfield))
			suggested = (self.modes.get(key, {}).get(row["customer"]) or frappe._dict()).get("value")
			if current and suggested and current != suggested:
				disagreements.append(key)
		if disagreements:
			return _("Needs Review")

		if row["distinct_price_lists"] >= 3 or row["distinct_payment_terms"] >= 3:
			return _("Mixed Usage")

		primary_conf = min(
			c for c in (row["price_list_confidence"], row["payment_terms_confidence"]) if c
		) if (row["price_list_confidence"] or row["payment_terms_confidence"]) else 0.0
		if primary_conf and primary_conf < self.threshold:
			return _("Low Confidence")

		return _("Consistent")

	# ------------------------------------------------------------------ #
	# report_summary cards
	# ------------------------------------------------------------------ #

	def build_report_summary(self, rows):
		total = len(rows)
		missing = sum(1 for r in rows if r.get("recommendation_status") == _("Missing"))
		needs_review = sum(1 for r in rows if r.get("recommendation_status") == _("Needs Review"))
		confs = [r["price_list_confidence"] for r in rows if r.get("price_list_confidence")]
		avg_conf = (sum(confs) / len(confs)) if confs else 0.0

		return [
			{"label": _("Customers Analysed"), "value": total, "datatype": "Int"},
			{
				"label": _("Missing Defaults"),
				"value": missing,
				"datatype": "Int",
				"indicator": "Red" if missing else "Green",
			},
			{
				"label": _("Needs Review"),
				"value": needs_review,
				"datatype": "Int",
				"indicator": "Orange" if needs_review else "Green",
			},
			{
				"label": _("Avg Price List Confidence"),
				"value": flt(avg_conf, 1),
				"datatype": "Percent",
				"indicator": "Green" if avg_conf >= 95 else ("Orange" if avg_conf >= self.threshold else "Red"),
			},
		]

	# ------------------------------------------------------------------ #
	# Legend message
	# ------------------------------------------------------------------ #

	def build_message(self):
		return f"""
			<div style="margin-bottom:10px;font-size:12px;color:var(--text-muted,#8d99a6);">
				<span style="font-weight:600;color:var(--text-color,#1f272e);">{_("Confidence")}:</span>
				<span style="color:#1b8a2f;font-weight:600;">&#9679; {_("Green &ge; 95%")}</span>&nbsp;&nbsp;
				<span style="color:#e08600;font-weight:600;">&#9679; {_("Yellow 80&ndash;95%")}</span>&nbsp;&nbsp;
				<span style="color:#b71c1c;font-weight:600;">&#9679; {_("Red &lt; 80%")}</span>
				&nbsp;&middot;&nbsp;
				{_("Suggestions are the most frequently used historical value. Apply never overwrites an existing Customer value unless you tick Overwrite Existing Values.")}
			</div>
		"""

	# ------------------------------------------------------------------ #
	# Columns
	# ------------------------------------------------------------------ #

	def get_columns(self):
		return [
			# Customer information
			{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 150},
			{"label": _("Customer Name"), "fieldname": "customer_name", "fieldtype": "Data", "width": 180},
			{"label": _("Customer Group"), "fieldname": "customer_group", "fieldtype": "Link", "options": "Customer Group", "width": 130},
			{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 80},
			{"label": _("Recommendation"), "fieldname": "recommendation_status", "fieldtype": "Data", "width": 130},
			# Sales summary
			{"label": _("Orders"), "fieldname": "orders", "fieldtype": "Int", "width": 75},
			{"label": _("First Order"), "fieldname": "first_order", "fieldtype": "Date", "width": 100},
			{"label": _("Last Order"), "fieldname": "last_order", "fieldtype": "Date", "width": 100},
			{"label": _("Total Sales"), "fieldname": "total_sales", "fieldtype": "Currency", "width": 130},
			{"label": _("Avg Order Value"), "fieldname": "avg_order_value", "fieldtype": "Currency", "width": 130},
			# Price list
			{"label": _("Current Price List"), "fieldname": "current_price_list", "fieldtype": "Link", "options": "Price List", "width": 150},
			{"label": _("Suggested Price List"), "fieldname": "suggested_price_list", "fieldtype": "Link", "options": "Price List", "width": 150},
			{"label": _("Price List Conf %"), "fieldname": "price_list_confidence", "fieldtype": "Percent", "width": 120},
			{"label": _("Distinct Price Lists"), "fieldname": "distinct_price_lists", "fieldtype": "Int", "width": 90},
			# Payment terms
			{"label": _("Current Payment Terms"), "fieldname": "current_payment_terms", "fieldtype": "Link", "options": "Payment Terms Template", "width": 160},
			{"label": _("Suggested Payment Terms"), "fieldname": "suggested_payment_terms", "fieldtype": "Link", "options": "Payment Terms Template", "width": 160},
			{"label": _("Payment Terms Conf %"), "fieldname": "payment_terms_confidence", "fieldtype": "Percent", "width": 130},
			{"label": _("Distinct Templates"), "fieldname": "distinct_payment_terms", "fieldtype": "Int", "width": 90},
			{"label": _("Avg Payment Days"), "fieldname": "avg_payment_days", "fieldtype": "Float", "width": 110, "precision": 1},
			{"label": _("Avg Term Days"), "fieldname": "avg_term_days", "fieldtype": "Float", "width": 100, "precision": 1},
			{"label": _("Payment Diff"), "fieldname": "payment_diff", "fieldtype": "Float", "width": 100, "precision": 1},
			# Currency
			{"label": _("Current Currency"), "fieldname": "current_currency", "fieldtype": "Link", "options": "Currency", "width": 110},
			{"label": _("Suggested Currency"), "fieldname": "suggested_currency", "fieldtype": "Link", "options": "Currency", "width": 120},
			# Territory
			{"label": _("Current Territory"), "fieldname": "current_territory", "fieldtype": "Link", "options": "Territory", "width": 130},
			{"label": _("Suggested Territory"), "fieldname": "suggested_territory", "fieldtype": "Link", "options": "Territory", "width": 130},
			# Sales person (advisory)
			{"label": _("Current Sales Person"), "fieldname": "current_sales_person", "fieldtype": "Link", "options": "Sales Person", "width": 140},
			{"label": _("Suggested Sales Person"), "fieldname": "suggested_sales_person", "fieldtype": "Link", "options": "Sales Person", "width": 150},
			# Tax category
			{"label": _("Current Tax Category"), "fieldname": "current_tax_category", "fieldtype": "Link", "options": "Tax Category", "width": 140},
			{"label": _("Suggested Tax Category"), "fieldname": "suggested_tax_category", "fieldtype": "Link", "options": "Tax Category", "width": 150},
			# Advisory / addresses
			{"label": _("Suggested Company"), "fieldname": "suggested_company", "fieldtype": "Link", "options": "Company", "width": 140},
			{"label": _("Latest Shipping Address"), "fieldname": "latest_shipping_address", "fieldtype": "Link", "options": "Address", "width": 170},
			{"label": _("Latest Billing Address"), "fieldname": "latest_billing_address", "fieldtype": "Link", "options": "Address", "width": 170},
		]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _neg_str(value):
	"""Sort key that makes `max()` break ties on the alphabetically-first value:
	compares the negated code points so a smaller string ranks higher."""
	return tuple(-ord(ch) for ch in (value or ""))


def _current_col(customer_field):
	"""Map a Customer field to this report's 'current_*' column name."""
	return {
		"default_price_list": "current_price_list",
		"payment_terms": "current_payment_terms",
		"default_currency": "current_currency",
		"territory": "current_territory",
		"tax_category": "current_tax_category",
	}[customer_field]


# --------------------------------------------------------------------------- #
# Bulk apply (whitelisted)
# --------------------------------------------------------------------------- #

@frappe.whitelist()
def apply_customer_defaults(updates, overwrite=0):
	"""Write inferred defaults back onto Customer Masters.

	`updates` is a JSON list of {"customer": <name>, "fields": {<customer_field>:
	<value>, ...}} where <customer_field> is one of APPLYABLE_FIELDS.

	Safety (the whole point of the "Safety" section of the brief):
	  * A field is only written when it is currently BLANK on the Customer, unless
	    overwrite=1 (the "Overwrite Existing Values" toggle) is passed.
	  * Only APPLYABLE_FIELDS may be written - Company / Sales Person are never
	    touched here.
	  * Each Customer is saved through the ORM (doc.save) so link targets and the
	    Customer's own validations are enforced; a failure on one customer is
	    collected and reported, it does not abort the rest.

	Returns a summary dict {applied, skipped, failed, details:[...]} for the client
	to surface.
	"""
	updates = frappe.parse_json(updates) or []
	overwrite = cint(overwrite)

	if not frappe.has_permission("Customer", "write"):
		frappe.throw(_("You are not permitted to edit Customers."), frappe.PermissionError)

	applied = skipped = failed = 0
	details = []

	for u in updates:
		customer = u.get("customer")
		fields = u.get("fields") or {}
		if not customer or not fields:
			continue

		if not frappe.has_permission("Customer", "write", doc=customer):
			failed += 1
			details.append({"customer": customer, "result": "failed", "reason": _("No write permission.")})
			continue

		current = frappe.db.get_value(
			"Customer", customer, list(APPLYABLE_FIELDS.keys()), as_dict=True
		) or frappe._dict()

		to_set = {}
		row_skips = []
		for field, value in fields.items():
			if field not in APPLYABLE_FIELDS:
				continue
			if value in (None, ""):
				continue
			if current.get(field) and not overwrite:
				row_skips.append(_(APPLYABLE_FIELDS[field]))
				continue
			to_set[field] = value

		if not to_set:
			skipped += 1
			details.append(
				{
					"customer": customer,
					"result": "skipped",
					"reason": _("Already set: {0}").format(", ".join(row_skips))
					if row_skips
					else _("Nothing to apply."),
				}
			)
			continue

		try:
			doc = frappe.get_doc("Customer", customer)
			for field, value in to_set.items():
				doc.set(field, value)
			doc.save()
			applied += 1
			details.append(
				{
					"customer": customer,
					"result": "applied",
					"fields": {_(APPLYABLE_FIELDS[f]): v for f, v in to_set.items()},
				}
			)
		except Exception as e:
			failed += 1
			details.append({"customer": customer, "result": "failed", "reason": str(e)})
			frappe.log_error(
				title="Customer Commercial Profile apply failed",
				message=f"{customer}: {frappe.get_traceback()}",
			)

	return {"applied": applied, "skipped": skipped, "failed": failed, "details": details}
