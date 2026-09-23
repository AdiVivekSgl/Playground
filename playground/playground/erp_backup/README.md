# ERP Monthly Backup & Disaster Recovery

A self-contained backup product for this Frappe/ERPNext app. Every month (or on
demand) it produces **one verified, downloadable archive** that serves two
independent recovery layers:

| Layer | Contents | Use |
|---|---|---|
| **1 — ERP restoration** | `DATABASE/database.sql.gz` + `FILES/*.tar` + `METADATA/` | Rebuild the whole site with `bench restore`. **Authoritative.** |
| **2 — Data portability** | `DATA/<Doctype>.csv` | Open business data in any spreadsheet even if ERPNext can't be stood up. |

> The CSV exports are a convenience. The **database dump is the authoritative
> restore source** — the CSVs never replace it.

## Archive layout

```
ERP_BACKUP_2026-09-30.tar.gz
└── ERP_BACKUP_2026-09-30/
    ├── DATABASE/
    │   └── database.sql.gz
    ├── FILES/
    │   ├── <site>-<ts>-files.tar          (public files)
    │   └── <site>-<ts>-private-files.tar  (private files)
    ├── DATA/
    │   ├── Customer.csv
    │   ├── Sales_Order.csv
    │   ├── Sales_Order_Item.csv           (child tables exported separately)
    │   └── … (configurable list)
    ├── METADATA/
    │   ├── doctypes.json      custom_fields.json   workflows.json
    │   ├── property_setters.json  print_formats.json  reports.json
    │   └── environment.json   (versions, installed apps, site, server info)
    └── BACKUP_MANIFEST.json
```

## How it works

The pipeline (`erp_backup/pipeline.py`) runs as a **background job** and executes,
in order (any failure ⇒ the backup is marked **Failed**, never silently "done"):

1. **Database** — `frappe.utils.backups.new_backup()` (native Frappe dump; we do
   not reinvent `mysqldump`).
2. **Files** — the public/private file tars from the same native backup, staged
   as-is (they are exactly what `bench restore --with-files` consumes).
3. **Data export** — each enabled DocType streamed to `DATA/*.csv`.
4. **Metadata** — customisations + environment to `METADATA/*.json` (secrets
   scrubbed).
5. **Manifest** — `BACKUP_MANIFEST.json`.
6. **Archive** — streamed `tar.gz` (never built in RAM).
7. **Checksum** — streamed SHA-256.
8. **Verify** — re-open the archive; confirm structure, a non-empty DB dump, the
   manifest, and a matching checksum.
9. **Verified** — set only if verification passes.
10. **Retention** — prune old backups per policy.
11. **Notify** — success email + in-app notification (failure email on any error).

## Install / upgrade

```bash
# In your bench:
bench get-app playground <repo-url>        # or update an existing checkout
bench --site <your-site> install-app playground   # first install only
bench --site <your-site> migrate           # creates DocTypes + Backup Manager role,
                                            # seeds the default export list
bench build --app playground
bench --site <your-site> clear-cache
```

`after_migrate` creates the **Backup Manager** role and seeds the default export
list into **ERP Backup Settings** the first time (it never overwrites your edits).

## Configure

Open **ERP Backup Settings** (System Manager). It ships **disabled**.

- **Enable Scheduled Backups** — tick to start the monthly schedule.
- **Day of Month / Hour** — when it runs (default 1st, 02:00 server time).
- **Keep N Monthly Backups** — rolling window (default 12).
- **Keep Year-End Backups Forever** + **Year-End Month** — a scheduled backup in
  that month is tagged `Year-End` and never pruned.
- **Storage Backend / Local Storage Path** — V1 is `Local`; blank path ⇒
  `<site>/private/backups/erp_monthly` (private, never web-served).
- **Notification Recipients** — comma/newline separated; blank ⇒ all System
  Managers.
- **Exported DocTypes** — the human-readable CSV list (configurable).

## Create a backup manually

**ERP Backup** list → **Create Backup Now** (or the button on any ERP Backup form).
Pick a type, optional description, and *Keep Forever*. It runs in the background;
the record advances `Queued → Running → Completed → Verified` (the form
auto-refreshes). Manual backups use the **same pipeline** as scheduled ones.

## Download a backup

From an **ERP Backup** record (or the **ERP Backup Status** report) click
**Download**. Downloads are served **only** through a permission-gated, streaming
endpoint (`/api/method/playground.playground.erp_backup.api.download_backup`) —
never through `/files` or any guessable public URL. Only **System Manager** /
**Backup Manager** may download.

## Retention

- Newest **N** monthly backups are kept; older ones have their archive **and**
  record deleted.
- **Year-End** backups and any marked **Keep Forever** are preserved permanently.
- Only successful (Completed/Verified) backups count toward the window — a Failed
  backup never displaces a good one.

## Verify a backup

Each backup is auto-verified at creation (see the **Verification** section on the
record: structure, non-empty DB dump, manifest, checksum). To check an archive by
hand:

```bash
tar tzf ERP_BACKUP_2026-09-30.tar.gz | head       # lists members
sha256sum ERP_BACKUP_2026-09-30.tar.gz            # compare to the record's Checksum
```

## Restore the ERP from a backup

**Layer 1 (full restore) — do this on a fresh/staging site first.**

```bash
tar xzf ERP_BACKUP_2026-09-30.tar.gz
cd ERP_BACKUP_2026-09-30
bench --site <target-site> restore DATABASE/database.sql.gz \
    --with-public-files  FILES/<site>-<ts>-files.tar \
    --with-private-files FILES/<site>-<ts>-private-files.tar
bench --site <target-site> migrate
```

**Layer 2 (data only):** open the `DATA/*.csv` files in any spreadsheet — child
tables carry `parent`/`parenttype`/`parentfield` columns so lines can be
re-associated with their parent document.

## Security

- Backups are the entire ERP — treat archives as extremely sensitive.
- Creation and download are restricted to System Manager / Backup Manager.
- Archives live in the site's **private** area; they are never exposed publicly and
  can't be reached by guessing a filename.
- Passwords, API keys and encryption keys are scrubbed from METADATA and the
  manifest; error logs are truncated.

## Testing

Plain `unittest` (mocked; no live site needed), matching the app convention:

```bash
bench --site <your-site> run-tests --module \
  playground.playground.erp_backup.tests.test_pipeline
# or, from the app directory, standalone verification/archive tests:
python -m unittest playground.playground.erp_backup.tests.test_verify
```

Covered: backup-creation stages, every stage-failure ⇒ `Failed`, security
(unauthorised cannot trigger/download), retention (window + year-end + keep-forever),
verification (corrupt / missing DB / missing manifest / checksum mismatch), manifest
fields + secret-scrubbing, manual-backup enqueue, CSV naming.

## Extension points (designed, not built in V1)

- **Cloud storage** — add an `S3Storage` (etc.) subclass in `storage.py` and a
  branch in `get_backend()`. The pipeline and download endpoint use only the
  `StorageBackend` interface, so nothing else changes.
- **Restore testing** — `verify.py` returns a structured, extensible check list.
  A future feature can restore an archive into a **disposable/staging** site and
  append deeper checks (DocTypes load, Customers/Items/Orders accessible, …). No
  dangerous automatic production restore is included.
