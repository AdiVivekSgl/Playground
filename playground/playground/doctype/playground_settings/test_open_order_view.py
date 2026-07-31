# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Unit tests for the Open Order View maths (playground.playground.open_order_view).

These cover the pure pending/summary formulas that drive the submitted Sales
Order "Open Order View" panel - the numbers the client script renders. They are
plain ``unittest`` cases with no site/database dependency, so they run under
``bench run-tests`` and also standalone:

    bench --site <site> run-tests --app playground \\
        --module playground.playground.doctype.playground_settings.test_open_order_view

    # or, without a bench (frappe is stubbed if unavailable):
    python -m unittest playground.playground.doctype.playground_settings.test_open_order_view
"""

import sys
import types
import unittest

# open_order_view imports ``frappe`` at module load. When these tests run outside
# a bench (no frappe on the path), drop in a minimal stub so the pure-math
# functions can still be imported and exercised. Inside a bench the real frappe
# wins and this is a no-op.
if "frappe" not in sys.modules:
	try:
		import frappe  # noqa: F401
	except ImportError:
		sys.modules["frappe"] = types.ModuleType("frappe")

from playground.playground.open_order_view import (  # noqa: E402
	compute_item_pending,
	compute_order_summary,
)


class TestComputeItemPending(unittest.TestCase):
	def test_fully_delivered(self):
		r = compute_item_pending(qty=10, delivered_qty=10, rate=5)
		self.assertEqual(r["pending_qty"], 0)
		self.assertEqual(r["pending_amount"], 0)
		self.assertEqual(r["status"], "Fully Delivered")

	def test_partially_delivered(self):
		r = compute_item_pending(qty=10, delivered_qty=4, rate=5)
		self.assertEqual(r["pending_qty"], 6)
		self.assertEqual(r["pending_amount"], 30)
		self.assertEqual(r["status"], "Partially Delivered")

	def test_not_started(self):
		r = compute_item_pending(qty=10, delivered_qty=0, rate=5)
		self.assertEqual(r["pending_qty"], 10)
		self.assertEqual(r["pending_amount"], 50)
		self.assertEqual(r["status"], "Not Started")

	def test_cancelled_zero_qty_row(self):
		r = compute_item_pending(qty=0, delivered_qty=0, rate=5)
		self.assertEqual(r["pending_qty"], 0)
		self.assertEqual(r["status"], "Cancelled")

	def test_return_overdelivery_clamped(self):
		# Over-delivery / return pushes delivered past ordered -> pending clamps to 0.
		r = compute_item_pending(qty=10, delivered_qty=12, rate=5, clamp_negative=True)
		self.assertEqual(r["pending_qty"], 0)
		self.assertEqual(r["pending_amount"], 0)
		self.assertEqual(r["raw_pending_qty"], -2)
		self.assertEqual(r["status"], "Fully Delivered")

	def test_return_overdelivery_unclamped(self):
		r = compute_item_pending(qty=10, delivered_qty=12, rate=5, clamp_negative=False)
		self.assertEqual(r["pending_qty"], -2)
		self.assertEqual(r["pending_amount"], -10)

	def test_amended_rate_change(self):
		# After an amendment the rate can change; pending_amount follows the new rate.
		r = compute_item_pending(qty=10, delivered_qty=4, rate=7)
		self.assertEqual(r["pending_qty"], 6)
		self.assertEqual(r["pending_amount"], 42)

	def test_blank_values_are_safe(self):
		r = compute_item_pending(qty=None, delivered_qty="", rate=None)
		self.assertEqual(r["pending_qty"], 0)
		self.assertEqual(r["status"], "Cancelled")


class TestComputeOrderSummary(unittest.TestCase):
	def test_zero_delivery_order(self):
		items = [
			{"qty": 10, "delivered_qty": 0, "rate": 5},
			{"qty": 4, "delivered_qty": 0, "rate": 25},
		]
		s = compute_order_summary(items)
		self.assertEqual(s["order_value"], 150)
		self.assertEqual(s["delivered_value"], 0)
		self.assertEqual(s["pending_value"], 150)
		self.assertEqual(s["completion_percent"], 0)

	def test_fully_delivered_order(self):
		items = [
			{"qty": 10, "delivered_qty": 10, "rate": 5},
			{"qty": 4, "delivered_qty": 4, "rate": 25},
		]
		s = compute_order_summary(items)
		self.assertEqual(s["pending_value"], 0)
		self.assertEqual(s["completion_percent"], 100)

	def test_partial_delivery_mixed(self):
		# 50 delivered of 150 ordered -> 33.33% complete, 100 pending.
		items = [
			{"qty": 10, "delivered_qty": 10, "rate": 5},  # 50 delivered
			{"qty": 4, "delivered_qty": 0, "rate": 25},  # 0 delivered
		]
		s = compute_order_summary(items)
		self.assertEqual(s["order_value"], 150)
		self.assertEqual(s["delivered_value"], 50)
		self.assertEqual(s["pending_value"], 100)
		self.assertAlmostEqual(s["completion_percent"], 33.3333, places=3)

	def test_multiple_deliveries_accumulated(self):
		# delivered_qty on the SO item is the running total across many Delivery
		# Notes - the summary just reads that accumulated figure.
		items = [{"qty": 100, "delivered_qty": 70, "rate": 2}]  # 3 DNs -> 70 total
		s = compute_order_summary(items)
		self.assertEqual(s["delivered_value"], 140)
		self.assertEqual(s["pending_value"], 60)
		self.assertEqual(s["completion_percent"], 70)

	def test_return_clamped_does_not_inflate_pending(self):
		# One line over-delivered (return); pending value must not go negative.
		items = [
			{"qty": 10, "delivered_qty": 12, "rate": 5},  # over-delivered
			{"qty": 10, "delivered_qty": 3, "rate": 5},  # partial
		]
		s = compute_order_summary(items, clamp_negative=True)
		self.assertEqual(s["pending_value"], 35)  # only the partial line contributes

	def test_empty_order(self):
		s = compute_order_summary([])
		self.assertEqual(s["order_value"], 0)
		self.assertEqual(s["completion_percent"], 0)


if __name__ == "__main__":
	unittest.main()
