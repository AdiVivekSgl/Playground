# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Incoming FG Valuation from Mfg: rate resolution, FG re-valuation, variance
and the GL reclass.

	bench --site <site> run-tests --module playground.playground.fg_valuation.tests.test_valuation

No data is read - Item/Company values and stock balances are mocked; rows are
built as if ERPNext's standard calculate_rate_and_amount had just run.
"""

import unittest
from unittest.mock import patch

import frappe

from playground.playground.fg_valuation import valuation as v

MOD = "playground.playground.fg_valuation.valuation"


def fg_row(actual_rate, qty=100, additional_cost=0.0, **kw):
	"""FG row with ERPNext's standard figures: valuation_rate = actual rate."""
	basic_amount = actual_rate * qty - additional_cost
	base = dict(
		idx=1,
		item_code="FG-1",
		is_finished_item=1,
		t_warehouse="FG Store - FT",
		s_warehouse=None,
		transfer_qty=qty,
		set_basic_rate_manually=0,
		additional_cost=additional_cost,
		basic_rate=basic_amount / qty,
		basic_amount=basic_amount,
		valuation_rate=actual_rate,
		amount=actual_rate * qty,
		expense_account="Stock Adjustment - FT",
		cost_center="Main - FT",
	)
	base.update(kw)
	return frappe._dict(base)


def rm_row(amount):
	return frappe._dict(
		idx=2, item_code="RM-1", is_finished_item=0, s_warehouse="Stores - FT", t_warehouse=None,
		transfer_qty=10, valuation_rate=amount / 10, amount=amount,
	)


def entry(*rows, docstatus=0, action=None, purpose="Manufacture"):
	se = frappe._dict(
		purpose=purpose, company="Frontec", posting_date="2026-10-24", posting_time="10:00:00",
		docstatus=docstatus, items=list(rows),
	)
	if action:
		se._action = action
	return se


def item_values(method, rate=0.0):
	values = {
		("Item", v.ITEM_METHOD): method,
		("Item", v.ITEM_FIXED_RATE): rate,
		("Company", v.COMPANY_VARIANCE_ACCOUNT): "Mfg Valuation Variance - FT",
		("Account", "company"): "Frontec",
	}

	def get_cached_value(doctype, name, field):
		return values.get((doctype, field))

	return get_cached_value


class ApplyTestBase(unittest.TestCase):
	def run_apply(self, se, method, rate=0.0, current=0.0):
		with patch(f"{MOD}.frappe.get_cached_value", side_effect=item_values(method, rate)), patch(
			f"{MOD}.current_valuation_rate", return_value=current
		), patch(f"{MOD}._perpetual_inventory", return_value=True), patch(f"{MOD}._warn") as msg:
			changed = v.apply_fg_valuation(se)
		self.msgprint = msg
		return changed


class FixedRateTest(ApplyTestBase):
	def test_favourable_when_cost_below_fixed(self):
		d = fg_row(920)
		se = entry(rm_row(92000), d)
		self.assertTrue(self.run_apply(se, v.FIXED, rate=1000))
		self.assertEqual(d.valuation_rate, 1000)
		self.assertEqual(d.amount, 100000)
		self.assertEqual(d[v.ROW_ACTUAL_AMOUNT], 92000)
		self.assertEqual(d[v.ROW_ACTUAL_RATE], 920)
		self.assertEqual(d[v.ROW_INCOMING_RATE], 1000)
		self.assertEqual(d[v.ROW_VARIANCE], 8000)
		self.assertEqual(d[v.ROW_VARIANCE_TYPE], v.FAVOURABLE)
		self.assertEqual(d[v.ROW_METHOD], v.FIXED)
		self.assertEqual(se[v.SE_VARIANCE], 8000)
		self.assertEqual(se[v.SE_VARIANCE_TYPE], v.FAVOURABLE)

	def test_unfavourable_when_cost_above_fixed(self):
		d = fg_row(1080)
		se = entry(d)
		self.run_apply(se, v.FIXED, rate=1000)
		self.assertEqual(d.amount, 100000)
		self.assertEqual(d[v.ROW_VARIANCE], 8000)
		self.assertEqual(d[v.ROW_VARIANCE_TYPE], v.UNFAVOURABLE)

	def test_missing_fixed_rate_blocks(self):
		with self.assertRaises(frappe.ValidationError):
			self.run_apply(entry(fg_row(920)), v.FIXED, rate=0)

	def test_additional_cost_is_inside_the_rate(self):
		# 90,000 materials + 2,000 operating cost = 920/unit actual.
		d = fg_row(920, additional_cost=2000)
		self.run_apply(entry(d), v.FIXED, rate=1000)
		self.assertEqual(d.valuation_rate, 1000)
		self.assertEqual(d.basic_rate, 980)
		self.assertEqual(d.basic_amount, 98000)
		self.assertEqual(d.amount, 100000)
		self.assertEqual(d[v.ROW_VARIANCE], 8000)

	def test_additional_cost_above_rate_blocks(self):
		with self.assertRaises(frappe.ValidationError):
			self.run_apply(entry(fg_row(920, additional_cost=150000)), v.FIXED, rate=1000)


class CurrentRateTest(ApplyTestBase):
	def test_enters_at_current_rate(self):
		d = fg_row(920)
		self.run_apply(entry(d), v.CURRENT, current=1080)
		self.assertEqual(d.valuation_rate, 1080)
		self.assertEqual(d.amount, 108000)
		self.assertEqual(d[v.ROW_VARIANCE], 16000)
		self.assertEqual(d[v.ROW_VARIANCE_TYPE], v.FAVOURABLE)
		self.assertEqual(d[v.ROW_METHOD], v.CURRENT)

	def test_no_current_rate_falls_back_to_std_with_warning(self):
		d = fg_row(920)
		changed = self.run_apply(entry(d), v.CURRENT, current=0)
		self.assertFalse(changed)
		self.assertEqual(d.valuation_rate, 920)
		self.assertEqual(d[v.ROW_METHOD], v.FALLBACK)
		self.assertEqual(d[v.ROW_VARIANCE], 0)
		self.msgprint.assert_called_once()


class StdAndScopeTest(ApplyTestBase):
	def test_std_leaves_row_untouched(self):
		d = fg_row(920)
		self.assertFalse(self.run_apply(entry(d), v.STD))
		self.assertEqual(d.valuation_rate, 920)
		self.assertEqual(d.amount, 92000)
		self.assertIsNone(d.get(v.ROW_METHOD))

	def test_unconfigured_item_is_std(self):
		d = fg_row(920)
		self.assertFalse(self.run_apply(entry(d), None))
		self.assertEqual(d.valuation_rate, 920)

	def test_only_manufacture(self):
		d = fg_row(920)
		self.assertFalse(self.run_apply(entry(d, purpose="Repack"), v.FIXED, rate=1000))
		self.assertEqual(d.valuation_rate, 920)

	def test_manual_basic_rate_wins(self):
		d = fg_row(920, set_basic_rate_manually=1)
		self.assertFalse(self.run_apply(entry(d), v.FIXED, rate=1000))
		self.assertEqual(d.valuation_rate, 920)


class RepostTest(ApplyTestBase):
	def snapshot(self, d, method, rate):
		d[v.ROW_METHOD] = method
		d[v.ROW_INCOMING_RATE] = rate

	def test_repost_reuses_snapshot_not_item(self):
		# Item rate has since changed to 1,200; the submitted entry keeps 1,000
		# and only the actual cost (now 950) moves.
		d = fg_row(950)
		self.snapshot(d, v.FIXED, 1000)
		self.run_apply(entry(d, docstatus=1), v.FIXED, rate=1200)
		self.assertEqual(d.valuation_rate, 1000)
		self.assertEqual(d[v.ROW_ACTUAL_AMOUNT], 95000)
		self.assertEqual(d[v.ROW_VARIANCE], 5000)
		self.assertEqual(d[v.ROW_VARIANCE_TYPE], v.FAVOURABLE)

	def test_submit_reads_item(self):
		d = fg_row(950)
		self.snapshot(d, v.FIXED, 1000)
		self.run_apply(entry(d, docstatus=1, action="submit"), v.FIXED, rate=1200)
		self.assertEqual(d.valuation_rate, 1200)

	def test_old_entry_without_snapshot_stays_std(self):
		d = fg_row(950)
		self.assertFalse(self.run_apply(entry(d, docstatus=1), v.FIXED, rate=1000))
		self.assertEqual(d.valuation_rate, 950)


class VarianceAccountTest(ApplyTestBase):
	def test_missing_account_blocks(self):
		values = item_values(v.FIXED, 1000)

		def no_account(doctype, name, field):
			return None if field == v.COMPANY_VARIANCE_ACCOUNT else values(doctype, name, field)

		with patch(f"{MOD}.frappe.get_cached_value", side_effect=no_account), patch(
			f"{MOD}._perpetual_inventory", return_value=True
		):
			with self.assertRaises(frappe.ValidationError):
				v.apply_fg_valuation(entry(fg_row(920)))


class ReclassTest(unittest.TestCase):
	def gl(self, d):
		se = entry(d)
		se.get_gl_dict = lambda args, item=None: frappe._dict(args)
		with patch(f"{MOD}.frappe.get_cached_value", side_effect=item_values(v.FIXED)):
			return v.reclass_entries(se)

	def row(self, vtype):
		d = fg_row(1000)
		d[v.ROW_METHOD] = v.FIXED
		d[v.ROW_VARIANCE] = 8000
		d[v.ROW_VARIANCE_TYPE] = vtype
		return d

	def test_favourable_credits_variance(self):
		dr, cr = self.gl(self.row(v.FAVOURABLE))
		self.assertEqual((dr.account, dr.debit), ("Stock Adjustment - FT", 8000))
		self.assertEqual((cr.account, cr.credit), ("Mfg Valuation Variance - FT", 8000))

	def test_unfavourable_debits_variance(self):
		dr, cr = self.gl(self.row(v.UNFAVOURABLE))
		self.assertEqual((dr.account, dr.debit), ("Mfg Valuation Variance - FT", 8000))
		self.assertEqual((cr.account, cr.credit), ("Stock Adjustment - FT", 8000))

	def test_pair_balances(self):
		rows = self.gl(self.row(v.FAVOURABLE))
		self.assertEqual(sum(r.debit for r in rows), sum(r.credit for r in rows))

	def test_no_variance_no_entries(self):
		self.assertEqual(self.gl(fg_row(1000)), [])


class ItemValidationTest(unittest.TestCase):
	def test_fixed_needs_positive_rate(self):
		doc = frappe._dict({v.ITEM_METHOD: v.FIXED, v.ITEM_FIXED_RATE: 0})
		with self.assertRaises(frappe.ValidationError):
			v.validate_item(doc)

	def test_other_methods_need_no_rate(self):
		v.validate_item(frappe._dict({v.ITEM_METHOD: v.CURRENT}))
		v.validate_item(frappe._dict({v.ITEM_METHOD: v.FIXED, v.ITEM_FIXED_RATE: 1000}))


class VarianceTest(unittest.TestCase):
	def test_sign(self):
		self.assertEqual(v.variance(92000, 100000), (8000, v.FAVOURABLE))
		self.assertEqual(v.variance(108000, 100000), (8000, v.UNFAVOURABLE))
		self.assertEqual(v.variance(100000, 100000.001), (0.0, None))
