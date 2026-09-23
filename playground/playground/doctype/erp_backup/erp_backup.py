# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ERPBackup(Document):
	def on_trash(self):
		"""Delete the archive from storage when the record is deleted.

		Keeps disk usage honest: removing a backup record removes its file too. A
		'Keep Forever' backup is protected from *automatic* retention pruning, but a
		System Manager deleting it by hand is a deliberate act, so we honour it.
		"""
		if not self.archive_path:
			return
		try:
			import os

			from playground.playground.erp_backup import storage

			storage.get_backend().delete(os.path.basename(self.archive_path))
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"ERP Backup: deleting archive for {self.name}")
