# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""`stock XYZ-123` - available qty of one exact item code, per warehouse.

Runs as the sender's mapped ERPNext user (user_context.as_user) and reads only
through permission-checked APIs (frappe.has_permission / frappe.get_list), so
the reply shows exactly what that user could see in the desk: nothing if they
lack Item/Bin read, and only warehouses their User Permissions allow.
"""

import frappe
from frappe.utils import flt

from playground.playground.whatsapp.user_context import as_user

MAX_ITEM_CODE_LENGTH = 140
# Warehouses listed in the reply; the rest are summarised as "…and N more".
MAX_WAREHOUSES_LISTED = 10
# Upper bound on Bin rows read for one item (each item has one Bin per warehouse).
MAX_BINS = 500
# Trailing punctuation people add after the code ("stock XYZ-123?").
_TRAILING_PUNCTUATION = ".,!?;:"

USAGE_REPLY = "Send *stock* followed by an item code, e.g. *stock XYZ-123*"
NOT_LINKED_REPLY = (
	"Sorry, your WhatsApp number is not linked to an ERPNext user, so I can't look up stock. "
	"Please ask your ERPNext administrator to add it under *WhatsApp User*."
)
PERMISSION_DENIED_REPLY = "Sorry, your ERPNext user doesn't have permission to view stock."
ITEM_NOT_FOUND_REPLY = "Item *{code}* not found. Please check the exact item code."
NOT_STOCK_ITEM_REPLY = "*{code}* is not a stock item, so it has no warehouse stock."


def get_stock(args, sender):
	item_code = (args or "").strip().rstrip(_TRAILING_PUNCTUATION).strip()
	if not item_code:
		return USAGE_REPLY
	if len(item_code) > MAX_ITEM_CODE_LENGTH:
		return ITEM_NOT_FOUND_REPLY.format(code=item_code[:40] + "…")
	if not sender or not sender.user:
		return NOT_LINKED_REPLY

	with as_user(sender.user) as user:
		try:
			return _stock_reply(item_code, user)
		except frappe.PermissionError:
			return PERMISSION_DENIED_REPLY


def _stock_reply(item_code, user):
	if not (frappe.has_permission("Item", "read", user=user) and frappe.has_permission("Bin", "read", user=user)):
		return PERMISSION_DENIED_REPLY

	# get_list applies the user's permissions, so an item they may not see is
	# indistinguishable from one that doesn't exist (no existence leak).
	items = frappe.get_list(
		"Item",
		filters={"name": item_code},
		fields=["name", "item_name", "stock_uom", "is_stock_item", "disabled"],
		limit_page_length=1,
	)
	if not items:
		return ITEM_NOT_FOUND_REPLY.format(code=item_code)
	item = items[0]
	if not item.is_stock_item:
		return NOT_STOCK_ITEM_REPLY.format(code=item.name)

	bins = frappe.get_list(
		"Bin",
		filters={"item_code": item.name},
		fields=["warehouse", "actual_qty", "projected_qty"],
		order_by="actual_qty desc",
		limit_page_length=MAX_BINS,
	)
	bins = [b for b in bins if flt(b.actual_qty) or flt(b.projected_qty)]
	if bins:
		# Belt and braces: only warehouses this user can read (Bin's own query
		# already applies Warehouse User Permissions).
		visible = set(
			frappe.get_list(
				"Warehouse",
				filters={"name": ["in", [b.warehouse for b in bins]]},
				pluck="name",
				limit_page_length=len(bins),
			)
		)
		bins = [b for b in bins if b.warehouse in visible]

	return format_stock_reply(item, bins)


def format_stock_reply(item, bins):
	uom = item.stock_uom or ""
	title = f"📦 *{item.name}*"
	if item.item_name and item.item_name != item.name:
		title += f" - {item.item_name}"
	if item.disabled:
		title += " (disabled)"

	if not bins:
		return f"{title}\nNo stock in any warehouse you can see."

	total = sum(flt(b.actual_qty) for b in bins)
	lines = [title, f"In stock: *{_qty(total)}* {uom}".rstrip(), ""]
	for b in bins[:MAX_WAREHOUSES_LISTED]:
		line = f"• {b.warehouse}: {_qty(b.actual_qty)}"
		if flt(b.projected_qty) != flt(b.actual_qty):
			line += f" (projected {_qty(b.projected_qty)})"
		lines.append(line)
	hidden = len(bins) - MAX_WAREHOUSES_LISTED
	if hidden > 0:
		lines.append(f"…and {hidden} more warehouse{'s' if hidden > 1 else ''}")
	return "\n".join(lines)


def _qty(value):
	"""1200 -> "1,200", 12.5 -> "12.5", 0.125 -> "0.125"."""
	text = f"{flt(value):,.3f}".rstrip("0").rstrip(".")
	return "0" if text in ("-0", "") else text
