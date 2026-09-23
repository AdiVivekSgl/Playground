# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Stage 3: human-readable CSV exports (recovery Layer 2, spec 1C).

Each configured business DocType is written to ``DATA/<Doctype>.csv``. Child
tables are configured and exported as their own DocTypes; because a child row
carries ``parent``/``parenttype``/``parentfield``/``idx`` columns in the database,
exporting each on its own automatically retains the parent reference (spec 1C).

Rows are streamed with keyset pagination (``name > last``) and written straight to
disk a batch at a time, so even multi-million-row tables never load into memory
(spec 16). These CSVs are a portability convenience — they are NEVER a substitute
for the database dump, which remains the authoritative restore source (spec 17).
"""

import csv
import os

import frappe

from playground.playground.erp_backup import constants, utils

BATCH_SIZE = 5000


def export_business_data(staging_dir, settings=None):
	"""Export every enabled DocType from settings to ``DATA/``.

	Returns a summary dict::

	    {"dir": "<staging>/DATA", "exported": [...], "skipped": {name: reason},
	     "doctype_count": int, "row_count": int}
	"""
	settings = settings or utils.get_settings()
	data_dir = os.path.join(staging_dir, constants.DIR_DATA)
	os.makedirs(data_dir, exist_ok=True)

	exported, skipped, total_rows = [], {}, 0

	for doctype in _enabled_doctypes(settings):
		if not frappe.db.table_exists(doctype):
			# A configured DocType not installed on this site (e.g. an app removed
			# later) is skipped and noted, never fatal.
			skipped[doctype] = "table not found"
			continue
		try:
			rows = _export_one(doctype, data_dir)
			exported.append(doctype)
			total_rows += rows
		except Exception as exc:  # one bad table must not sink the whole export
			skipped[doctype] = str(exc)
			frappe.log_error(frappe.get_traceback(), f"ERP Backup: export {doctype} failed")

	return {
		"dir": data_dir,
		"exported": exported,
		"skipped": skipped,
		"doctype_count": len(exported),
		"row_count": total_rows,
	}


def _enabled_doctypes(settings):
	rows = settings.get("export_doctypes") or []
	seen, out = set(), []
	for row in rows:
		dt = (row.get("document_type") or "").strip()
		if dt and row.get("enabled") and dt not in seen:
			seen.add(dt)
			out.append(dt)
	return out


def _export_one(doctype, data_dir):
	"""Stream one DocType to CSV; return the row count written."""
	columns = frappe.db.get_table_columns(doctype)
	table = f"tab{doctype}"
	out_path = os.path.join(data_dir, _csv_name(doctype))

	written = 0
	last_name = ""
	with open(out_path, "w", newline="", encoding="utf-8") as handle:
		writer = csv.writer(handle)
		writer.writerow(columns)
		while True:
			batch = frappe.db.sql(
				# Keyset pagination on the primary key — stable and index-friendly
				# for very large tables. Column list is validated (real DB columns),
				# never user input, so identifier interpolation here is safe.
				"SELECT {cols} FROM `{table}` WHERE name > %(last)s "
				"ORDER BY name LIMIT %(limit)s".format(
					cols=", ".join(f"`{c}`" for c in columns), table=table
				),
				{"last": last_name, "limit": BATCH_SIZE},
				as_dict=True,
			)
			if not batch:
				break
			for record in batch:
				writer.writerow([_cell(record.get(col)) for col in columns])
			written += len(batch)
			last_name = batch[-1]["name"]
			if len(batch) < BATCH_SIZE:
				break
	return written


def _csv_name(doctype):
	"""'Sales Order Item' -> 'Sales_Order_Item.csv' (consistent naming, spec 1C)."""
	safe = "".join(ch if (ch.isalnum() or ch in " -_") else "_" for ch in doctype)
	return safe.strip().replace(" ", "_") + ".csv"


def _cell(value):
	if value is None:
		return ""
	return value
