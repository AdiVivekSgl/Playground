# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Playground Settings
===================

App-wide, single-record configuration for Playground presentation features.

The only invariant worth guarding here is that clearing the caches after a save
lets the ``extend_bootinfo`` hook (playground.playground.open_order_view
.boot_open_order_settings) hand fresh values to the next desk session. All the
actual behaviour these toggles drive lives client-side in the Sales Order script.
"""

import frappe
from frappe.model.document import Document


class PlaygroundSettings(Document):
	def on_update(self):
		# The Open Order View settings ride along in the desk bootinfo, which is
		# cached. Bust the cache so a toggle change is picked up on the next page
		# load rather than the next login.
		frappe.clear_cache()
