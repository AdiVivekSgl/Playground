# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Idempotent provisioning, wired into ``after_migrate`` (spec 15, 6).

Runs on every ``bench migrate``:
  * creates the ``Backup Manager`` role if missing;
  * ensures the ``ERP Backup Settings`` singleton exists;
  * seeds the default human-readable export list ONCE (only when empty), so an
    administrator's later edits are never clobbered.

Follows the app's existing create-if-missing convention
(``production_user.setup_production_user``, ``label_printing.setup_label_printing``).
"""

import frappe

from playground.playground.erp_backup import constants


def setup_erp_backup():
	_create_backup_manager_role()
	_seed_settings()


def _create_backup_manager_role():
	if frappe.db.exists("Role", constants.BACKUP_MANAGER_ROLE):
		return
	role = frappe.new_doc("Role")
	role.role_name = constants.BACKUP_MANAGER_ROLE
	role.desk_access = 1
	role.insert(ignore_permissions=True)


def _seed_settings():
	if not frappe.db.exists("DocType", constants.SETTINGS_DOCTYPE):
		# DocType not migrated yet (first run ordering) — nothing to seed.
		return
	settings = frappe.get_single(constants.SETTINGS_DOCTYPE)

	# Seed the export list only when the administrator has not defined one.
	if not settings.get("export_doctypes"):
		for doctype in constants.DEFAULT_EXPORT_DOCTYPES:
			settings.append("export_doctypes", {"document_type": doctype, "enabled": 1})
		settings.save(ignore_permissions=True)
		frappe.db.commit()
