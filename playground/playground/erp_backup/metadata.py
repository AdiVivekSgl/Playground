# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Stage 4: configuration/metadata export (spec 2).

Writes ``METADATA/*.json`` describing the site's customisations and environment so
an old archive is understandable — and a restore is reproducible — without a live
ERP. Everything is run through :func:`utils.scrub_secrets`; we never emit
``site_config.json``, passwords, API keys or encryption keys (spec 2, 15).
"""

import json
import os
import platform
import sys

import frappe

from playground.playground.erp_backup import constants, utils


def export_metadata(staging_dir):
	"""Write the METADATA JSON files. Returns ``{"dir":..., "environment":{...}}``."""
	meta_dir = os.path.join(staging_dir, constants.DIR_METADATA)
	os.makedirs(meta_dir, exist_ok=True)

	_write(meta_dir, "doctypes.json", _doctypes())
	_write(meta_dir, "custom_fields.json", _all("Custom Field"))
	_write(meta_dir, "workflows.json", _workflows())
	_write(meta_dir, "property_setters.json", _all("Property Setter"))
	_write(meta_dir, "print_formats.json", _print_formats())
	_write(meta_dir, "reports.json", _reports())

	environment = _environment()
	_write(meta_dir, "environment.json", environment)

	return {"dir": meta_dir, "environment": environment}


# --- collectors ------------------------------------------------------------

def _doctypes():
	"""All DocTypes (name + flags + module) plus full defs for custom ones."""
	summary = frappe.get_all(
		"DocType",
		fields=["name", "module", "custom", "issingle", "istable", "is_submittable"],
		order_by="name",
	)
	custom_names = [d["name"] for d in summary if d.get("custom")]
	custom_defs = []
	for name in custom_names:
		try:
			custom_defs.append(frappe.get_doc("DocType", name).as_dict(no_default_fields=True))
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"ERP Backup: metadata DocType {name}")
	return {"all": summary, "custom": utils.scrub_secrets(custom_defs)}


def _all(doctype):
	try:
		return utils.scrub_secrets(frappe.get_all(doctype, fields=["*"]))
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"ERP Backup: metadata {doctype}")
		return []


def _workflows():
	out = []
	for row in frappe.get_all("Workflow", pluck="name"):
		try:
			out.append(frappe.get_doc("Workflow", row).as_dict(no_default_fields=True))
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"ERP Backup: metadata Workflow {row}")
	return utils.scrub_secrets(out)


def _print_formats():
	# Only non-standard (custom) print formats travel with a site's data.
	return utils.scrub_secrets(
		frappe.get_all(
			"Print Format",
			filters={"standard": "No"},
			fields=["name", "doc_type", "module", "print_format_type", "disabled", "html"],
		)
	)


def _reports():
	return utils.scrub_secrets(
		frappe.get_all(
			"Report",
			filters={"is_standard": "No"},
			fields=["name", "ref_doctype", "report_type", "module", "disabled"],
		)
	)


def _environment():
	"""Frappe/ERPNext versions, installed apps, site + server info (spec 2)."""
	versions = {}
	try:
		from frappe.utils.change_log import get_versions

		versions = get_versions() or {}
	except Exception:
		frappe.log_error(frappe.get_traceback(), "ERP Backup: get_versions failed")

	installed = frappe.get_installed_apps()
	app_versions = {}
	for app in installed:
		info = versions.get(app) or {}
		app_versions[app] = {
			"version": info.get("version"),
			"branch": info.get("branch"),
		}

	env = {
		"site": frappe.local.site,
		"frappe_version": frappe.__version__,
		"erpnext_version": (versions.get("erpnext") or {}).get("version"),
		"installed_apps": installed,
		"app_versions": app_versions,
		"python_version": sys.version.split()[0],
		"platform": platform.platform(),
		"backup_module_version": _module_version(),
		"generated_on": frappe.utils.now(),
	}
	return utils.scrub_secrets(env)


def _module_version():
	try:
		import playground

		return getattr(playground, "__version__", None)
	except Exception:
		return None


def _write(directory, name, payload):
	with open(os.path.join(directory, name), "w", encoding="utf-8") as handle:
		json.dump(payload, handle, indent=2, default=str, ensure_ascii=False)
