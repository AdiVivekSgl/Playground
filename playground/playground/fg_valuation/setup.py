# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Idempotent provisioning for Incoming FG Valuation from Mfg, wired into
``after_migrate``.

- Item: the valuation method Select + the Fixed FG Valuation Rate (shown only
  for "Fixed / Manual").
- Company: the Manufacturing Valuation Variance account.
- Stock Entry Detail / Stock Entry: read-only audit fields recording the method,
  actual manufacturing cost, incoming FG rate and resulting variance.

Wrapped defensively like the app's other setup hooks: a failure here must never
abort migrate (on Frappe Cloud a failed migrate rolls the whole DB back); it
re-runs on the next migrate.
"""

import frappe

from playground.playground.fg_valuation.valuation import (
	COMPANY_VARIANCE_ACCOUNT,
	FIXED,
	ITEM_FIXED_RATE,
	ITEM_METHOD,
	METHODS,
	ROW_ACTUAL_AMOUNT,
	ROW_ACTUAL_RATE,
	ROW_INCOMING_RATE,
	ROW_METHOD,
	ROW_VARIANCE,
	ROW_VARIANCE_TYPE,
	SE_VARIANCE,
	SE_VARIANCE_TYPE,
	STD,
)

CURRENCY = "Company:company:default_currency"

ITEM_FIELDS = [
	{
		"fieldname": ITEM_METHOD,
		"fieldtype": "Select",
		"label": "Incoming FG Valuation from Mfg",
		"options": "\n".join(METHODS),
		"default": STD,
		"insert_after": "valuation_rate",
		"description": "How this item is valued when produced by a Manufacture Stock Entry. "
		"Any difference from the actual material cost is posted to the Company's "
		"Manufacturing Valuation Variance account.",
	},
	{
		"fieldname": ITEM_FIXED_RATE,
		"fieldtype": "Currency",
		"label": "Fixed FG Valuation Rate",
		"insert_after": ITEM_METHOD,
		"depends_on": f'eval:doc.{ITEM_METHOD}=="{FIXED}"',
		"mandatory_depends_on": f'eval:doc.{ITEM_METHOD}=="{FIXED}"',
		"description": "Valuation rate per stock UOM the manufactured FG enters inventory at.",
	},
]

COMPANY_FIELDS = [
	{
		"fieldname": COMPANY_VARIANCE_ACCOUNT,
		"fieldtype": "Link",
		"options": "Account",
		"label": "Manufacturing Valuation Variance Account",
		"insert_after": "stock_adjustment_account",
		"description": "Receives the difference between actual manufacturing cost and the FG "
		"valuation for items set to 'Same as Current Inventory Valuation Rate' or 'Fixed / Manual'.",
	},
]

_ROW_SHOWN = f"eval:doc.{ROW_METHOD}"

STOCK_ENTRY_DETAIL_FIELDS = [
	{
		"fieldname": "custom_fg_valuation_section",
		"fieldtype": "Section Break",
		"label": "FG Valuation",
		"collapsible": 1,
		"depends_on": _ROW_SHOWN,
		"insert_after": "amount",
	},
	{
		"fieldname": ROW_METHOD,
		"fieldtype": "Data",
		"label": "FG Valuation Method",
		"read_only": 1,
		"no_copy": 1,
		"insert_after": "custom_fg_valuation_section",
	},
	{
		"fieldname": ROW_ACTUAL_RATE,
		"fieldtype": "Currency",
		"label": "Actual Mfg Cost Rate",
		"options": CURRENCY,
		"read_only": 1,
		"no_copy": 1,
		"insert_after": ROW_METHOD,
	},
	{
		"fieldname": ROW_ACTUAL_AMOUNT,
		"fieldtype": "Currency",
		"label": "Actual Mfg Cost Amount",
		"options": CURRENCY,
		"read_only": 1,
		"no_copy": 1,
		"insert_after": ROW_ACTUAL_RATE,
	},
	{
		"fieldname": "custom_fg_valuation_col",
		"fieldtype": "Column Break",
		"insert_after": ROW_ACTUAL_AMOUNT,
	},
	{
		"fieldname": ROW_INCOMING_RATE,
		"fieldtype": "Currency",
		"label": "Incoming FG Valuation Rate",
		"options": CURRENCY,
		"read_only": 1,
		"no_copy": 1,
		"insert_after": "custom_fg_valuation_col",
	},
	{
		"fieldname": ROW_VARIANCE,
		"fieldtype": "Currency",
		"label": "Mfg Valuation Variance",
		"options": CURRENCY,
		"read_only": 1,
		"no_copy": 1,
		"insert_after": ROW_INCOMING_RATE,
	},
	{
		"fieldname": ROW_VARIANCE_TYPE,
		"fieldtype": "Data",
		"label": "Variance Type",
		"read_only": 1,
		"no_copy": 1,
		"insert_after": ROW_VARIANCE,
	},
]

STOCK_ENTRY_FIELDS = [
	{
		"fieldname": SE_VARIANCE,
		"fieldtype": "Currency",
		"label": "Mfg Valuation Variance",
		"options": CURRENCY,
		"read_only": 1,
		"no_copy": 1,
		"depends_on": f"eval:doc.{SE_VARIANCE}",
		"insert_after": "value_difference",
	},
	{
		"fieldname": SE_VARIANCE_TYPE,
		"fieldtype": "Data",
		"label": "Mfg Variance Type",
		"read_only": 1,
		"no_copy": 1,
		"depends_on": f"eval:doc.{SE_VARIANCE}",
		"insert_after": SE_VARIANCE,
	},
]


def setup_fg_valuation():
	"""after_migrate hook - create/refresh the FG valuation custom fields."""
	try:
		from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

		create_custom_fields(
			{
				"Item": ITEM_FIELDS,
				"Company": COMPANY_FIELDS,
				"Stock Entry Detail": STOCK_ENTRY_DETAIL_FIELDS,
				"Stock Entry": STOCK_ENTRY_FIELDS,
			},
			ignore_validate=True,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "setup_fg_valuation failed")
