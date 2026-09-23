# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Shared names and defaults for the ERP Backup module.

Kept in one place so the pipeline, verifier, download endpoint and tests all agree
on directory names, DocType names, statuses and the default export list.
"""

# --- DocType names ---------------------------------------------------------
BACKUP_DOCTYPE = "ERP Backup"
SETTINGS_DOCTYPE = "ERP Backup Settings"
EXPORT_ENTRY_DOCTYPE = "ERP Backup Export Entry"

# --- Roles -----------------------------------------------------------------
BACKUP_MANAGER_ROLE = "Backup Manager"
# Any one of these roles may trigger and download backups (spec 8, 15).
ALLOWED_ROLES = ["System Manager", BACKUP_MANAGER_ROLE]

# --- Archive layout (spec 4) ----------------------------------------------
DIR_DATABASE = "DATABASE"
DIR_FILES = "FILES"
DIR_DATA = "DATA"
DIR_METADATA = "METADATA"
MANIFEST_NAME = "BACKUP_MANIFEST.json"
DB_DUMP_NAME = "database.sql.gz"

# --- ERP Backup.status values (spec 5) ------------------------------------
STATUS_QUEUED = "Queued"
STATUS_RUNNING = "Running"
STATUS_COMPLETED = "Completed"
STATUS_FAILED = "Failed"
STATUS_VERIFIED = "Verified"

# --- verification_status values -------------------------------------------
VERIFY_PENDING = "Pending"
VERIFY_PASSED = "Passed"
VERIFY_FAILED = "Failed"

# --- backup_type values ---------------------------------------------------
TYPE_MONTHLY = "Monthly"
TYPE_MANUAL = "Manual"
TYPE_YEAR_END = "Year-End"

# --- pipeline stage labels (written to ERP Backup.current_stage) ----------
STAGE_START = "Starting"
STAGE_DATABASE = "Database Backup"
STAGE_FILES = "File Backup"
STAGE_DATA = "Data Export"
STAGE_METADATA = "Metadata"
STAGE_MANIFEST = "Manifest"
STAGE_ARCHIVE = "Archive"
STAGE_VERIFY = "Verification"
STAGE_RETENTION = "Retention"
STAGE_NOTIFY = "Notification"

# --- misc ------------------------------------------------------------------
CHUNK_SIZE = 1024 * 1024  # 1 MiB streaming chunk for hashing / archiving
# Background-job timeout for a full backup (seconds). Large ERPs + files can be
# slow; scheduled and manual runs both use this.
BACKUP_TIMEOUT = 6 * 60 * 60

# Default human-readable export list (spec 1C). Seeded once into
# ERP Backup Settings; administrators edit it from there. Parents and their child
# tables are listed separately — child tables carry parent/parenttype/parentfield
# columns in the DB, so exporting each on its own retains the parent reference.
DEFAULT_EXPORT_DOCTYPES = [
	"Customer",
	"Supplier",
	"Item",
	"Item Group",
	"Warehouse",
	"BOM",
	"BOM Item",
	"Opportunity",
	"Quotation",
	"Quotation Item",
	"Sales Order",
	"Sales Order Item",
	"Purchase Order",
	"Purchase Order Item",
	"Material Request",
	"Delivery Note",
	"Delivery Note Item",
	"Sales Invoice",
	"Sales Invoice Item",
	"Purchase Receipt",
	"Purchase Receipt Item",
	"Purchase Invoice",
	"Purchase Invoice Item",
	"Payment Entry",
	"Payment Entry Reference",
	"Stock Entry",
	"Stock Entry Detail",
	"Work Order",
	"Job Card",
	"Project",
]

# Substrings that mark a metadata/dict key as secret — scrubbed from every
# METADATA export and the manifest so no credential ever lands in the
# human-readable output (spec 2, 15).
SECRET_KEY_HINTS = (
	"password",
	"passwd",
	"secret",
	"api_key",
	"api_secret",
	"apikey",
	"access_key",
	"encryption_key",
	"token",
	"private_key",
	"credentials",
	"salt",
)
