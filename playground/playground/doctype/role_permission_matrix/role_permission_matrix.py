from io import BytesIO

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.permissions import add_permission, update_permission_property

# (child fieldname -> Custom DocPerm permission type)
PERM_FIELDS = [
	("col_read", "read"),
	("col_write", "write"),
	("col_create", "create"),
	("col_submit", "submit"),
	("col_delete", "delete"),
	("col_amend", "amend"),
	("col_print", "print"),
	("col_cancel", "cancel"),
]

# perms that imply "read" — if any of these are set, read must be too
_NON_READ_FIELDS = [f for f, _p in PERM_FIELDS if f != "col_read"]


class RolePermissionMatrix(Document):
	def validate(self):
		if not self.created_date:
			self.created_date = frappe.utils.today()

		errors = self.collect_validation_errors()
		if errors:
			frappe.throw("<br>".join(errors), title=_("Matrix Validation Failed"))

	def collect_validation_errors(self):
		"""Return a list of human-readable problems with the matrix rows.

		Used both by validate() (which turns them into a throw) and by the
		'Validate Matrix' button (which lists them without blocking)."""
		errors = []
		seen = set()

		for row in self.permissions_table:
			label = _("Row {0}").format(row.idx)

			if not row.role or not row.doctype_name:
				errors.append(_("{0}: Role and DocType are both required.").format(label))
				continue

			role = frappe.utils.escape_html(row.role)
			dt = frappe.utils.escape_html(row.doctype_name)

			if not frappe.db.exists("Role", row.role):
				errors.append(_("{0}: Role '{1}' does not exist. Create it in Setup &gt; Role first.").format(label, role))
			if not frappe.db.exists("DocType", row.doctype_name):
				errors.append(_("{0}: DocType '{1}' does not exist (names are case-sensitive).").format(label, dt))

			key = (row.role, row.doctype_name)
			if key in seen:
				errors.append(_("{0}: duplicate entry — Role '{1}' + DocType '{2}' appears more than once.").format(label, role, dt))
			seen.add(key)

			if not row.col_read and any(row.get(f) for f in _NON_READ_FIELDS):
				errors.append(_("{0}: 'Read' must be enabled when any other permission is granted ({1} / {2}).").format(label, role, dt))

		return errors

	def apply_matrix(self):
		"""Create/update Custom DocPerm records and Role Profiles from the matrix.

		Uses frappe.permissions.add_permission / update_permission_property so that
		existing standard permissions for a DocType are copied into Custom DocPerm
		before we edit them (via setup_custom_perms) — we never silently drop
		permissions that aren't in the matrix."""
		# Re-validate the persisted state before touching live permissions.
		errors = self.collect_validation_errors()
		if errors:
			frappe.throw("<br>".join(errors), title=_("Matrix Validation Failed"))

		affected = set()
		rows_written = 0

		for row in self.permissions_table:
			if not row.role or not row.doctype_name:
				continue
			dt = row.doctype_name

			# Ensure a permlevel-0 Custom DocPerm exists for this role.
			add_permission(dt, row.role, 0)

			# validate=False: skip the per-call framework re-validation (which can
			# trip on transient states, e.g. write set before read); we clear the
			# cache once at the end instead.
			for field, ptype in PERM_FIELDS:
				update_permission_property(dt, row.role, 0, ptype, 1 if row.get(field) else 0, validate=False)

			affected.add(dt)
			rows_written += 1

		for dt in affected:
			frappe.clear_cache(doctype=dt)

		profiles = self._sync_role_profiles()

		self.db_set("status", "Applied", update_modified=False)
		self.db_set("applied_on", frappe.utils.now(), update_modified=False)
		self.db_set("applied_by", frappe.session.user, update_modified=False)

		return {
			"success": True,
			"rows": rows_written,
			"doctypes": sorted(affected),
			"profiles": profiles,
		}

	def _sync_role_profiles(self):
		"""Group roles by their role_profile value and add each role to its
		Role Profile. Roles already present in a profile (added elsewhere) are
		left untouched — we only add, never strip."""
		grouped = {}
		for row in self.permissions_table:
			if row.role_profile and row.role:
				grouped.setdefault(row.role_profile, set()).add(row.role)

		count = 0
		for profile_name, roles in grouped.items():
			if frappe.db.exists("Role Profile", profile_name):
				profile = frappe.get_doc("Role Profile", profile_name)
			else:
				profile = frappe.new_doc("Role Profile")
				profile.role_profile = profile_name

			existing = {r.role for r in profile.roles}
			added = False
			for role in roles:
				if role not in existing:
					profile.append("roles", {"role": role})
					added = True

			if added or not profile.get("name"):
				profile.save(ignore_permissions=True)
			count += 1

		return count


@frappe.whitelist()
def apply_permissions(docname):
	"""Called by the 'Apply Permissions' button."""
	doc = frappe.get_doc("Role Permission Matrix", docname)
	doc.check_permission("write")
	return doc.apply_matrix()


@frappe.whitelist()
def validate_matrix(docname):
	"""Called by the 'Validate Matrix' button — lists all problems at once."""
	doc = frappe.get_doc("Role Permission Matrix", docname)
	doc.check_permission("read")
	errors = doc.collect_validation_errors()
	return {"valid": not errors, "errors": errors}


# Excel column headers, in order. Shared by export and import so a downloaded
# file round-trips cleanly back into the matrix.
EXCEL_HEADERS = ["Role", "DocType", "Read", "Write", "Create", "Submit", "Delete", "Amend", "Print", "Cancel", "Role Profile"]


@frappe.whitelist()
def export_live_permissions():
	"""Download the live custom permissions (permlevel 0 Custom DocPerm records)
	as an .xlsx in the matrix layout. This is the actual current state in ERPNext
	— edit the file and re-import it. Standard (uncustomised) permissions are not
	included, since this tool only manages custom permissions.

	Sets a binary file response, so call it via open_url_post / a direct link,
	not frappe.call."""
	frappe.only_for("System Manager")

	from frappe.utils.xlsxutils import build_xlsx_response

	# Raw query so the SQL-reserved permission columns (read/create/delete/print)
	# are backticked correctly and come back under clean dict keys.
	perms = frappe.db.sql(
		"""
		select parent, role, `read`, `write`, `create`, `submit`, `delete`, `amend`, `print`, `cancel`
		from `tabCustom DocPerm`
		where permlevel = 0
		order by parent asc, role asc
		""",
		as_dict=True,
	)

	data = [EXCEL_HEADERS]
	for p in perms:
		data.append([
			p["role"], p["parent"],
			p["read"], p["write"], p["create"], p["submit"], p["delete"], p["amend"], p["print"], p["cancel"],
			"",  # Role Profile is a grouping input, not a per-permission attribute — left blank
		])

	build_xlsx_response(data, "Role_Permission_Matrix_Live")


@frappe.whitelist()
def import_from_excel(docname):
	"""Read the attached .xlsx and replace the matrix rows with its contents.
	The document is saved (and therefore validated) — fix any flagged rows in the
	sheet and re-import, or edit them in the grid. Nothing is applied to ERPNext
	until 'Apply Permissions' is clicked."""
	doc = frappe.get_doc("Role Permission Matrix", docname)
	doc.check_permission("write")
	if not doc.upload_excel:
		frappe.throw(_("Attach an Excel file in 'Upload Excel' first."))

	rows = _read_matrix_sheet(doc.upload_excel)
	if not rows:
		frappe.throw(_("No permission rows found in the uploaded file."))

	doc.set("permissions_table", [])
	for r in rows:
		doc.append("permissions_table", r)
	doc.save()

	return {"rows": len(doc.permissions_table)}


def _read_matrix_sheet(file_url):
	"""[row-dict, ...] from the first worksheet of the attached workbook. Columns
	are located by header label (case-insensitive) so column order and any extra
	columns don't matter."""
	import openpyxl
	from frappe.utils.file_manager import get_file

	_name, content = get_file(file_url)
	wb = openpyxl.load_workbook(BytesIO(content), data_only=True, read_only=True)
	ws = wb.active

	header = {}
	rows = []
	for i, row in enumerate(ws.iter_rows(values_only=True)):
		if i == 0:
			header = {
				str(v).strip().lower(): idx
				for idx, v in enumerate(row or [])
				if v is not None and str(v).strip()
			}
			continue
		if not row:
			continue

		def cell(*names):
			for n in names:
				idx = header.get(n)
				if idx is not None and idx < len(row):
					return row[idx]
			return None

		role = _text(cell("role"))
		doctype_name = _text(cell("doctype", "doctype name", "doctype_name"))
		if not role and not doctype_name:
			continue

		rows.append({
			"role": role or None,
			"doctype_name": doctype_name or None,
			"col_read": _as_check(cell("read")),
			"col_write": _as_check(cell("write")),
			"col_create": _as_check(cell("create")),
			"col_submit": _as_check(cell("submit")),
			"col_delete": _as_check(cell("delete")),
			"col_amend": _as_check(cell("amend")),
			"col_print": _as_check(cell("print")),
			"col_cancel": _as_check(cell("cancel")),
			"role_profile": _text(cell("role profile", "role_profile")) or None,
		})
	return rows


def _text(value):
	return str(value).strip() if value is not None else ""


def _as_check(value):
	"""Interpret a spreadsheet cell as a 1/0 checkbox. Accepts 1/0, TRUE/FALSE,
	yes/no, y/n, x — anything else is treated as unchecked."""
	if value is None:
		return 0
	if isinstance(value, (int, float)):
		return 1 if value else 0
	return 1 if str(value).strip().lower() in ("1", "true", "yes", "y", "x", "checked") else 0
