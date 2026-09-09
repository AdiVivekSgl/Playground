# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Production User role profile
============================

Provisions and guards the "Production User" role profile - a tightly restricted
production-floor profile that can only:

- start / finish Work Orders (the system creates the corresponding Stock Entries),
- create + submit Purchase Receipts, and
- make the Material Transfer Stock Entry from a Purchase Receipt.

Two pieces:

1. `setup_production_user` (wired to `after_migrate` in hooks.py) idempotently
   creates the `Production User` role, the `Production User` role profile, and the
   Custom DocPerm grid below. It is create-if-missing: it never overwrites a
   permission row that already exists, so once deployed the grid is maintained
   through the Role Profile permission workbook
   (playground.playground.role_profile_permissions), not by re-running migrate.

2. `guard_stock_entry_origin` (Stock Entry `validate` hook) enforces the one rule
   DocType permissions cannot express: a Production User may only ever save a Stock
   Entry that originates from a Work Order or a Purchase Receipt - never a
   stand-alone one.

Stock Entry origin signals:
- Work Order start/finish -> Material Transfer for Manufacture / Manufacture /
  Material Consumption for Manufacture entries carry the parent `work_order` field.
- The Purchase Receipt "Make Stock Entry" transfer stamps
  `Stock Entry Detail.reference_purchase_receipt` on every row (see
  playground.playground.overrides.purchase_receipt_dashboard).

Privileged roles (System Manager, Stock Manager, Manufacturing Manager) are never
restricted, so admins and stock/manufacturing managers keep full Stock Entry access
even if they also hold the Production User role.
"""

import frappe
from frappe import _
from frappe.permissions import setup_custom_perms

RESTRICTED_ROLE = "Production User"
PROFILE = "Production User"
PRIVILEGED_ROLES = {"System Manager", "Stock Manager", "Manufacturing Manager"}

# The Custom DocPerm flag fields, in DocPerm order. Written via .set() with string
# keys because several are Python keywords / SQL-reserved words (import, print, ...).
GRANT_FIELDS = [
	"select", "read", "write", "create", "delete", "submit", "cancel",
	"amend", "report", "import", "export", "print", "email", "share",
]

# doctype -> rights granted at permlevel 0, if-owner 0. Everything not listed is 0.
# Transactional access is deliberately narrow; masters are read-only for selection.
PERMISSION_GRID = {
	"Work Order": ["read", "write"],
	"Stock Entry": ["read", "write", "create", "submit"],
	"Purchase Receipt": ["read", "write", "create", "submit"],
	"Purchase Order": ["read"],
	"Item": ["read"],
	"Warehouse": ["read"],
	"Supplier": ["read"],
	"BOM": ["read"],
	"Company": ["read"],
	"UOM": ["read"],
}


# ---------------------------------------------------------------------------
# Provisioning (after_migrate) — idempotent, create-if-missing
# ---------------------------------------------------------------------------

def setup_production_user():
	"""Create the Production User role, role profile and permission grid if they
	are not already present. Safe to run on every migrate."""
	_ensure_role()
	_ensure_role_profile()
	_apply_permission_grid()


def _ensure_role():
	if frappe.db.exists("Role", RESTRICTED_ROLE):
		return
	role = frappe.new_doc("Role")
	role.role_name = RESTRICTED_ROLE
	role.desk_access = 1
	role.insert(ignore_permissions=True)


def _ensure_role_profile():
	if frappe.db.exists("Role Profile", PROFILE):
		profile = frappe.get_doc("Role Profile", PROFILE)
		if any(r.role == RESTRICTED_ROLE for r in profile.roles):
			return
		profile.append("roles", {"role": RESTRICTED_ROLE})
		profile.save(ignore_permissions=True)
		return
	profile = frappe.new_doc("Role Profile")
	profile.role_profile = PROFILE
	profile.append("roles", {"role": RESTRICTED_ROLE})
	profile.insert(ignore_permissions=True)


def _apply_permission_grid():
	for doctype, rights in PERMISSION_GRID.items():
		if not frappe.db.exists("DocType", doctype):
			continue

		# Preserve every existing standard permission as a Custom DocPerm before we
		# add ours — otherwise the first Custom DocPerm on a DocType makes Frappe
		# ignore its standard perms wholesale, silently dropping other roles' access.
		setup_custom_perms(doctype)

		if frappe.db.exists(
			"Custom DocPerm",
			{"parent": doctype, "role": RESTRICTED_ROLE, "permlevel": 0, "if_owner": 0},
		):
			continue  # already provisioned / managed via the workbook — leave as-is

		perm = frappe.get_doc({
			"doctype": "Custom DocPerm",
			"parent": doctype, "parenttype": "DocType", "parentfield": "permissions",
			"role": RESTRICTED_ROLE, "permlevel": 0, "if_owner": 0,
		})
		for field in GRANT_FIELDS:
			perm.set(field, 1 if field in rights else 0)
		perm.save(ignore_permissions=True)
		frappe.clear_cache(doctype=doctype)


# ---------------------------------------------------------------------------
# Guard (Stock Entry validate)
# ---------------------------------------------------------------------------

def _is_restricted_user():
	roles = set(frappe.get_roles())
	return RESTRICTED_ROLE in roles and not (PRIVILEGED_ROLES & roles)


def guard_stock_entry_origin(doc, method=None):
	"""Stock Entry validate hook: a Production User may only save a Stock Entry that
	originates from a Work Order or a Purchase Receipt."""
	if not _is_restricted_user():
		return

	from_work_order = bool(doc.get("work_order"))
	from_purchase_receipt = any(
		row.get("reference_purchase_receipt") for row in (doc.get("items") or [])
	)
	if from_work_order or from_purchase_receipt:
		return

	frappe.throw(
		_(
			"As a Production User you cannot create a stand-alone Stock Entry. "
			"Stock Entries are created for you when you start or finish a Work Order, "
			"or when you make a transfer from a Purchase Receipt."
		),
		title=_("Stock Entry Not Allowed"),
	)
