# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Stock Entry override - Incoming FG Valuation from Mfg
=====================================================

Values a Manufacture entry's Finished Good per the Item's "Incoming FG
Valuation from Mfg" setting (Std / Same as Current Inventory Valuation Rate /
Fixed / Manual) and posts the difference from actual cost to the Company's
Manufacturing Valuation Variance account. The logic lives in
playground.playground.fg_valuation.valuation; this class only wires it into
ERPNext's own flow:

- calculate_rate_and_amount(): ERPNext computes the standard rates first, then
  the FG rows are re-valued BEFORE the SLE is built (SLE incoming rate = row
  valuation_rate), and the entry totals are recomputed. Repost item valuation
  calls this same method on submitted entries, so a reposted FG keeps its
  snapshotted rate.
- get_gl_entries(): appends the variance reclass to the voucher's own GL, so it
  survives GL repost and is reversed on cancel.

Items left on "Std as per BOM / Material Consumption" are untouched.

VERIFY ON YOUR BENCH (ERPNext v15 - not inspectable from this repo):
- StockEntry.calculate_rate_and_amount(self, reset_outgoing_rate=True,
  raise_error_if_no_rate=True) and get_gl_entries(self, warehouse_account)
  signatures; set_total_incoming_outgoing_value() / set_total_amount() exist.
- erpnext.stock.utils.get_stock_balance(..., with_valuation_rate=True) returns
  (qty, valuation_rate).
- If another app (e.g. DT-Frontec) already overrides Stock Entry via
  override_doctype_class, merge this logic into that subclass instead - only ONE
  app may own the class.
"""

from erpnext.stock.doctype.stock_entry.stock_entry import StockEntry

from playground.playground.fg_valuation import valuation


class CustomStockEntry(StockEntry):
	def calculate_rate_and_amount(self, reset_outgoing_rate=True, raise_error_if_no_rate=True):
		super().calculate_rate_and_amount(reset_outgoing_rate, raise_error_if_no_rate)
		if valuation.apply_fg_valuation(self):
			self.set_total_incoming_outgoing_value()
			self.set_total_amount()

	def get_gl_entries(self, warehouse_account=None):
		gl_entries = super().get_gl_entries(warehouse_account)
		return gl_entries + valuation.reclass_entries(self)
