# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Pure pre-check rules: evaluate_pair, lock_blockers, validate_pair_rows.

	bench --site <site> run-tests --module playground.playground.item_consolidation.tests.test_checks

No data is read - every input is built here.
"""

import unittest

import frappe

from playground.playground.item_consolidation import checks


def item(name, **kw):
	base = dict(
		name=name,
		item_name=name,
		disabled=0,
		stock_uom="Nos",
		is_stock_item=1,
		has_serial_no=0,
		has_batch_no=0,
		has_variants=0,
		valuation_method="FIFO",
		default_bom=None,
	)
	base.update(kw)
	return frappe._dict(base)


def ctx(old=None, keep=None, **kw):
	base = dict(
		old=old if old is not None else item("OLD"),
		keep=keep if keep is not None else item("KEEP"),
		stock=[],
		other_company_stock=[],
		open_documents=[],
		boms=[],
		old_produced_by_boms=[],
		pending_reposts=[],
		active_reservations=[],
		lock_dates=[],
		closed_accounting_period=None,
	)
	base.update(kw)
	return frappe._dict(base)


def stock(qty, value=100.0, warehouse="Stores - FT"):
	return frappe._dict(warehouse=warehouse, actual_qty=qty, stock_value=value, valuation_rate=0)


class EvaluatePairTest(unittest.TestCase):
	def run_eval(self, c, disable=True, date="2026-10-12"):
		return checks.evaluate_pair("OLD", "KEEP", c, disable_old_items=disable, posting_date=date)

	def test_clean_pair_has_no_blockers(self):
		blockers, warnings = self.run_eval(ctx(stock=[stock(5)]))
		self.assertEqual(blockers, [])
		self.assertEqual(warnings, [])

	def test_missing_item_stops_further_checks(self):
		blockers, _ = self.run_eval(ctx(old=False))
		self.assertEqual(len(blockers), 1)

	def test_uom_and_stock_flag_mismatch_block(self):
		blockers, _ = self.run_eval(ctx(keep=item("KEEP", stock_uom="Kg", is_stock_item=0)))
		self.assertEqual(len(blockers), 2)

	def test_disabled_keep_and_template_block(self):
		blockers, _ = self.run_eval(ctx(keep=item("KEEP", disabled=1), old=item("OLD", has_variants=1)))
		self.assertEqual(len(blockers), 2)

	def test_negative_stock_blocks(self):
		blockers, _ = self.run_eval(ctx(stock=[stock(-1)]))
		self.assertEqual(len(blockers), 1)

	def test_batch_item_with_stock_blocks_but_without_stock_only_warns(self):
		batched = item("OLD", has_batch_no=1)
		blockers, _ = self.run_eval(ctx(old=batched, stock=[stock(3)]))
		self.assertEqual(len(blockers), 1)
		blockers, warnings = self.run_eval(ctx(old=batched))
		self.assertEqual(blockers, [])
		self.assertEqual(len(warnings), 1)

	def test_pending_repost_and_reservation_block(self):
		blockers, _ = self.run_eval(ctx(pending_reposts=["REPOST-1"], active_reservations=["SRE-1"]))
		self.assertEqual(len(blockers), 2)

	def test_open_documents_block_only_when_disabling(self):
		docs = [{"doctype": "Purchase Order", "names": ["PO-1"]}]
		blockers, _ = self.run_eval(ctx(open_documents=docs), disable=True)
		self.assertEqual(len(blockers), 1)
		self.assertIn("PO-1", blockers[0])
		blockers, warnings = self.run_eval(ctx(open_documents=docs), disable=False)
		self.assertEqual(blockers, [])
		self.assertEqual(len(warnings), 1)

	def test_period_lock_and_closed_accounting_period_block(self):
		c = ctx(lock_dates=[("Period Closing Voucher", "2026-10-31")], closed_accounting_period="AP-1")
		blockers, _ = self.run_eval(c)
		self.assertEqual(len(blockers), 2)

	def test_informational_warnings(self):
		c = ctx(
			keep=item("KEEP", valuation_method="Moving Average"),
			other_company_stock=["Other Co"],
			old_produced_by_boms=["BOM-OLD-001"],
		)
		blockers, warnings = self.run_eval(c)
		self.assertEqual(blockers, [])
		# valuation method, other company, produced-by BOM, keep has no default BOM
		self.assertEqual(len(warnings), 4)


class LockBlockersTest(unittest.TestCase):
	def test_only_locks_on_or_after_posting_date_block(self):
		locks = [("PCV", "2026-03-31"), ("Stock Frozen", "2026-10-12"), ("Unset", None)]
		self.assertEqual(len(checks.lock_blockers("2026-10-12", locks)), 1)
		self.assertEqual(checks.lock_blockers("2026-10-13", locks), [])


class ValidatePairRowsTest(unittest.TestCase):
	def rows(self, *pairs):
		return [frappe._dict(idx=i, old_item=o, keep_item=k) for i, (o, k) in enumerate(pairs, 1)]

	def test_empty_table(self):
		self.assertEqual(len(checks.validate_pair_rows([])), 1)

	def test_valid_many_to_one(self):
		self.assertEqual(checks.validate_pair_rows(self.rows(("A", "K"), ("B", "K"))), [])

	def test_self_duplicate_and_chain(self):
		self.assertEqual(len(checks.validate_pair_rows(self.rows(("A", "A")))), 2)  # self + chain
		self.assertEqual(len(checks.validate_pair_rows(self.rows(("A", "K"), ("A", "J")))), 1)
		self.assertEqual(len(checks.validate_pair_rows(self.rows(("A", "B"), ("B", "K")))), 1)
