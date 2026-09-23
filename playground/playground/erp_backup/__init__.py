# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""ERP Monthly Backup & Disaster Recovery module.

A self-contained backup product for the Playground app that, on a configurable
monthly schedule (or on demand), produces a single verified, downloadable archive
serving two independent recovery layers:

    Layer 1 (ERP restoration): DATABASE/database.sql.gz + FILES/*.tar + METADATA/
    Layer 2 (data portability): DATA/<Doctype>.csv human-readable exports

See ``README.md`` for the administrator and restore documentation. The pipeline
never marks a backup ``Verified`` unless the archive passes integrity checks, and
downloads are served only through a permission-gated, streaming endpoint.
"""
