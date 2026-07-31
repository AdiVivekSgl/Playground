# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Open Order View
===============

Presentation-layer maths for the "Open Order View" on submitted Sales Orders.

This module is the single source of truth for the pending-demand formulas. The
runtime rendering happens client-side (see
``playground/public/js/sales_order.js``) so that no extra database queries or
server round-trips are needed - the browser already holds the whole Sales Order
document, and pending qty/amount is trivial arithmetic over values that are
already on each item row (``qty``, ``delivered_qty``, ``rate``).

The functions here mirror that arithmetic exactly so it can be unit-tested
without a running site (see
``playground/playground/doctype/playground_settings/test_open_order_view.py``)
and reused by any server-side report or API that wants the same numbers. Nothing
in this module writes to the database or mutates the Sales Order - every value it
returns is transient.

Formulas
--------
    pending_qty    = qty - delivered_qty          (clamped to 0 when configured)
    pending_amount = pending_qty * rate
    delivered_val  = sum(delivered_qty * rate)
    pending_val    = sum(pending_qty * rate)
    order_val      = sum(qty * rate)              (ex-tax, item rate)
    completion_%   = delivered_val / order_val * 100
"""

from __future__ import annotations

import frappe

# Row status buckets - kept in sync with the colour map in sales_order.js.
STATUS_CANCELLED = "Cancelled"
STATUS_FULLY_DELIVERED = "Fully Delivered"
STATUS_PARTIALLY_DELIVERED = "Partially Delivered"
STATUS_NOT_STARTED = "Not Started"

# Client-side settings keys shipped through the desk bootinfo. Defaults here are
# the fallback when the Playground Settings single has never been saved.
DEFAULT_SETTINGS = {
	"enable_pending_view": 1,
	"show_original_qty": 0,
	"show_original_amount": 0,
	"highlight_completed_rows": 1,
	"clamp_negative_pending": 1,
}


def _f(value) -> float:
	"""Coerce ``None``/blank/str to float without raising - the grid can hand us
	empty strings for freshly-added rows."""
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def compute_item_pending(qty, delivered_qty, rate, clamp_negative=True) -> dict:
	"""Pending qty/amount and status for a single Sales Order Item.

	``clamp_negative`` mirrors the "display zero unless explicitly configured
	otherwise" rule for returns / over-delivery: when on, a negative pending qty
	is shown as 0 (and pending_amount follows the clamped qty)."""
	qty = _f(qty)
	delivered_qty = _f(delivered_qty)
	rate = _f(rate)

	raw_pending = qty - delivered_qty
	pending_qty = max(raw_pending, 0.0) if clamp_negative else raw_pending
	pending_amount = pending_qty * rate

	return {
		"pending_qty": pending_qty,
		"raw_pending_qty": raw_pending,
		"pending_amount": pending_amount,
		"status": _row_status(qty, delivered_qty),
	}


def _row_status(qty: float, delivered_qty: float) -> str:
	"""Bucket a row for the colour indicators. A zero-qty row is treated as
	Cancelled/empty; delivery is judged against the ordered qty."""
	if qty <= 0:
		return STATUS_CANCELLED
	if delivered_qty >= qty:
		return STATUS_FULLY_DELIVERED
	if delivered_qty <= 0:
		return STATUS_NOT_STARTED
	return STATUS_PARTIALLY_DELIVERED


def compute_order_summary(items, clamp_negative=True) -> dict:
	"""Roll up the Open Order Summary over an iterable of item rows.

	``items`` may be Sales Order Item documents or plain dicts - anything that
	exposes ``qty``, ``delivered_qty`` and ``rate`` via attribute or key access."""
	order_value = 0.0
	delivered_value = 0.0
	pending_value = 0.0

	for row in items:
		qty = _f(_get(row, "qty"))
		delivered_qty = _f(_get(row, "delivered_qty"))
		rate = _f(_get(row, "rate"))

		order_value += qty * rate
		delivered_value += delivered_qty * rate
		pending_value += compute_item_pending(qty, delivered_qty, rate, clamp_negative)[
			"pending_amount"
		]

	completion_percent = (delivered_value / order_value * 100.0) if order_value else 0.0

	return {
		"order_value": order_value,
		"delivered_value": delivered_value,
		"pending_value": pending_value,
		"completion_percent": completion_percent,
	}


def _get(row, key):
	"""Attribute-or-key access so both Frappe docs and dicts work."""
	if isinstance(row, dict):
		return row.get(key)
	return getattr(row, key, None)


def get_settings() -> dict:
	"""Read the Playground Settings single, falling back to defaults when the
	doctype/record isn't present yet (fresh install, mid-migrate)."""
	values = dict(DEFAULT_SETTINGS)
	try:
		single = frappe.get_cached_doc("Playground Settings")
	except Exception:
		return values
	for key in DEFAULT_SETTINGS:
		val = single.get(key)
		if val is not None:
			values[key] = int(val)
	return values


def boot_open_order_settings(bootinfo):
	"""``extend_bootinfo`` hook: ship the Open Order View settings to every desk
	session so the Sales Order client script reads them from ``frappe.boot`` with
	zero per-form round-trips."""
	bootinfo["playground_open_order_settings"] = get_settings()
