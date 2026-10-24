# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Incoming FG Valuation from Mfg
==============================

Item-level choice of how a Finished Good produced by a Manufacture Stock Entry
is valued:

- Std as per BOM / Material Consumption - ERPNext's standard behaviour
  (materials consumed - scrap + additional costs). The default; nothing changes.
- Same as Current Inventory Valuation Rate - the FG enters at its existing
  valuation rate in the target warehouse as of the posting date/time. With no
  stock / no rate it falls back to Std (and warns).
- Fixed / Manual - the FG enters at the Item's Fixed FG Valuation Rate.

The rate is ALL-IN: the FG row's valuation_rate equals the chosen rate exactly;
additional / operating costs are part of the actual cost and flow into the
variance. The difference between actual cost and FG value is the Manufacturing
Valuation Variance:

    variance = actual cost - FG value
      > 0  Unfavourable (cost above the FG valuation)  Dr Variance / Cr Stock Adj.
      < 0  Favourable   (cost below the FG valuation)  Dr Stock Adj. / Cr Variance

How it stays consistent (see overrides/stock_entry.py):
- apply_fg_valuation() runs INSIDE StockEntry.calculate_rate_and_amount(), after
  ERPNext's own rate/additional-cost calculation and before the SLE is built, so
  the FG SLE incoming rate (= row valuation_rate) is the chosen rate. No post-hoc
  overwrite of stock values.
- With the FG valued off actual cost, ERPNext's standard GL leaves the
  difference on the FG row's expense account (Stock Adjustment). reclass_entries()
  adds a pair to the voucher's own GL that moves it to the variance account, so
  it survives GL repost and is reversed on cancel.
- The method and rate are snapshotted on the row when the entry is saved or
  submitted. Later recalculations of a submitted entry (repost item valuation,
  after a back-dated RM transaction) reuse the snapshot: the FG keeps its rate and
  only the actual cost / variance move. Submitted entries made before this
  feature have no snapshot and stay standard.
"""

import frappe
from frappe import _
from frappe.utils import flt

STD = "Std as per BOM / Material Consumption"
CURRENT = "Same as Current Inventory Valuation Rate"
FIXED = "Fixed / Manual"
METHODS = (STD, CURRENT, FIXED)
# Recorded on the row when "Current" had no rate to use.
FALLBACK = "Std (fallback - no current valuation rate)"

FAVOURABLE = "Favourable"
UNFAVOURABLE = "Unfavourable"

ITEM_METHOD = "custom_incoming_fg_valuation_from_mfg"
ITEM_FIXED_RATE = "custom_fixed_fg_valuation_rate"
COMPANY_VARIANCE_ACCOUNT = "custom_mfg_valuation_variance_account"

ROW_METHOD = "custom_fg_valuation_method"
ROW_ACTUAL_RATE = "custom_actual_mfg_cost_rate"
ROW_ACTUAL_AMOUNT = "custom_actual_mfg_cost_amount"
ROW_INCOMING_RATE = "custom_fg_incoming_valuation_rate"
ROW_VARIANCE = "custom_mfg_valuation_variance"
ROW_VARIANCE_TYPE = "custom_mfg_variance_type"
ROW_AUDIT_FIELDS = (
	ROW_METHOD,
	ROW_ACTUAL_RATE,
	ROW_ACTUAL_AMOUNT,
	ROW_INCOMING_RATE,
	ROW_VARIANCE,
	ROW_VARIANCE_TYPE,
)

SE_VARIANCE = "custom_mfg_valuation_variance"
SE_VARIANCE_TYPE = "custom_mfg_variance_type"

# Below this a variance is rounding noise, not something to post.
_TOLERANCE = 0.005


# --- Item validation ------------------------------------------------------ #
def validate_item(doc, method=None):
	"""Item.validate doc_event: Fixed / Manual needs a positive rate."""
	if doc.get(ITEM_METHOD) == FIXED and flt(doc.get(ITEM_FIXED_RATE)) <= 0:
		frappe.throw(
			_("Fixed FG Valuation Rate must be greater than zero when Incoming FG Valuation from Mfg is '{0}'.").format(
				FIXED
			)
		)


# --- rate resolution ------------------------------------------------------ #
def variance(actual_amount, fg_amount):
	"""(absolute variance, type) for actual cost vs FG value; (0, None) if equal."""
	diff = flt(actual_amount) - flt(fg_amount)
	if abs(diff) < _TOLERANCE:
		return 0.0, None
	return abs(diff), UNFAVOURABLE if diff > 0 else FAVOURABLE


def current_valuation_rate(item_code, warehouse, posting_date, posting_time):
	"""FG valuation rate in `warehouse` as of the posting date/time, or 0 when
	there is no stock to take a meaningful rate from."""
	from erpnext.stock.utils import get_stock_balance

	qty, rate = get_stock_balance(item_code, warehouse, posting_date, posting_time, with_valuation_rate=True)
	if flt(qty) <= 0 or flt(rate) <= 0:
		return 0.0
	return flt(rate)


def is_live(se):
	"""True while the entry is being saved/submitted by a user - the method and
	rate are (re)read from the Item. False for recalculations of an already
	submitted entry (repost item valuation), which reuse the row snapshot."""
	return se.docstatus == 0 or getattr(se, "_action", None) == "submit"


def resolve_target_rate(se, d, live):
	"""(method recorded on the row, target FG rate or None for standard)."""
	if not live:
		method = d.get(ROW_METHOD)
		if method in (CURRENT, FIXED) and flt(d.get(ROW_INCOMING_RATE)) > 0:
			return method, flt(d.get(ROW_INCOMING_RATE))
		return method, None

	method = frappe.get_cached_value("Item", d.item_code, ITEM_METHOD) or STD
	if method == FIXED:
		rate = flt(frappe.get_cached_value("Item", d.item_code, ITEM_FIXED_RATE))
		if rate <= 0:
			frappe.throw(
				_("Row #{0}: Item {1} is valued '{2}' but has no Fixed FG Valuation Rate. Set a rate greater than zero on the Item.").format(
					d.idx, d.item_code, FIXED
				)
			)
		return FIXED, rate
	if method == CURRENT:
		rate = current_valuation_rate(d.item_code, d.t_warehouse, se.posting_date, se.posting_time)
		if rate <= 0:
			_warn(
				_("Row #{0}: Item {1} has no current valuation rate in {2}; it is valued as per material consumption instead.").format(
					d.idx, d.item_code, d.t_warehouse
				)
			)
			return FALLBACK, None
		return CURRENT, rate
	return None, None


def _warn(message):
	frappe.msgprint(message, indicator="orange", alert=True)


def applies_to(se, d):
	return (
		se.purpose == "Manufacture"
		and d.get("is_finished_item")
		and d.get("t_warehouse")
		and not d.get("set_basic_rate_manually")
		and flt(d.get("transfer_qty")) > 0
	)


# --- valuation ------------------------------------------------------------ #
def apply_fg_valuation(se):
	"""Re-value configured FG rows after ERPNext's standard rate calculation.

	Expects ERPNext to have just set basic_rate / additional_cost / valuation_rate /
	amount on every row. Returns True if any row changed, so the caller can
	recompute the entry totals."""
	live = is_live(se)
	changed = False
	total = 0.0  # signed: actual - FG value

	for d in se.get("items"):
		if not applies_to(se, d):
			if live:
				_clear(d)
			continue

		method, target = resolve_target_rate(se, d, live)
		if not method:
			_clear(d)
			continue

		actual_rate = flt(d.valuation_rate)
		actual_amount = flt(d.amount)
		if target is not None:
			_set_fg_rate(d, target)
			changed = True

		amount, vtype = variance(actual_amount, d.amount)
		setattr(d, ROW_METHOD, method)
		setattr(d, ROW_ACTUAL_RATE, actual_rate)
		setattr(d, ROW_ACTUAL_AMOUNT, actual_amount)
		setattr(d, ROW_INCOMING_RATE, flt(d.valuation_rate))
		setattr(d, ROW_VARIANCE, amount)
		setattr(d, ROW_VARIANCE_TYPE, vtype)
		total += amount if vtype == UNFAVOURABLE else -amount

	amount, vtype = variance(total, 0)
	setattr(se, SE_VARIANCE, amount)
	setattr(se, SE_VARIANCE_TYPE, vtype)

	if live and amount:
		validate_variance_account(se)
	return changed


def _set_fg_rate(d, target):
	"""All-in: valuation_rate = target; basic_rate absorbs the additional cost."""
	qty = flt(d.transfer_qty)
	basic_rate = flt(target) - flt(d.additional_cost) / qty
	if basic_rate < 0:
		frappe.throw(
			_("Row #{0}: additional costs of {1} per unit exceed the FG valuation rate of {2} for Item {3}.").format(
				d.idx, flt(d.additional_cost) / qty, flt(target), d.item_code
			)
		)
	d.basic_rate = flt(basic_rate, _prec(d, "basic_rate"))
	d.basic_amount = flt(qty * d.basic_rate, _prec(d, "basic_amount"))
	d.amount = flt(d.basic_amount + flt(d.additional_cost), _prec(d, "amount"))
	d.valuation_rate = flt(target, _prec(d, "valuation_rate"))


def _clear(d):
	for f in ROW_AUDIT_FIELDS:
		if d.get(f):
			setattr(d, f, None)


def _prec(d, fieldname):
	fn = getattr(d, "precision", None)
	return fn(fieldname) if callable(fn) else None


def validate_variance_account(se):
	if not _perpetual_inventory(se.company):
		return
	account = frappe.get_cached_value("Company", se.company, COMPANY_VARIANCE_ACCOUNT)
	if not account:
		frappe.throw(
			_("Set a Manufacturing Valuation Variance Account on Company {0} before submitting this Manufacture entry.").format(
				se.company
			)
		)
	if frappe.get_cached_value("Account", account, "company") != se.company:
		frappe.throw(
			_("The Manufacturing Valuation Variance Account {0} does not belong to Company {1}.").format(
				account, se.company
			)
		)


def _perpetual_inventory(company):
	import erpnext

	return erpnext.is_perpetual_inventory_enabled(company)


# --- GL ------------------------------------------------------------------- #
def reclass_entries(se):
	"""GL pairs moving each FG row's variance from its expense account (where the
	standard posting leaves it) to the Manufacturing Valuation Variance account."""
	if se.purpose != "Manufacture":
		return []
	account = frappe.get_cached_value("Company", se.company, COMPANY_VARIANCE_ACCOUNT)
	if not account:
		# Validation blocks submit without it; guard defensively (e.g. a repost
		# path) rather than posting half a reclassification.
		return []

	extra = []
	for d in se.get("items"):
		amount = flt(d.get(ROW_VARIANCE))
		vtype = d.get(ROW_VARIANCE_TYPE)
		if amount < _TOLERANCE or vtype not in (FAVOURABLE, UNFAVOURABLE) or not d.get("expense_account"):
			continue
		# Unfavourable: the FG was credited to Stock Adjustment for less than the
		# cost debited there - Dr Variance / Cr Stock Adjustment. Favourable: the
		# reverse.
		dr, cr = (account, d.expense_account) if vtype == UNFAVOURABLE else (d.expense_account, account)
		remarks = _("{0} manufacturing valuation variance for Item {1} ({2}).").format(
			_(vtype), d.item_code, d.get(ROW_METHOD)
		)
		for acc, against, debit, credit in ((dr, cr, amount, 0.0), (cr, dr, 0.0, amount)):
			extra.append(
				se.get_gl_dict(
					{
						"account": acc,
						"against": against,
						"cost_center": d.get("cost_center"),
						"project": d.get("project") or se.get("project"),
						"remarks": remarks,
						"debit": debit,
						"debit_in_account_currency": debit,
						"credit": credit,
						"credit_in_account_currency": credit,
					},
					item=d,
				)
			)
	return extra
