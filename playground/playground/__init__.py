__version__ = "0.0.1"

# Monkey-patch core ERPNext Production Plan to allow rows with Planned Qty = 0
# — see overrides/production_plan.py for details.
#
# Guarded on purpose: this runs at package-import time, which is also when
# `bench migrate` imports the app to sync DocTypes. If the override module is
# ever missing or fails to import, an unguarded import here aborts migrate for
# the WHOLE site (not just this feature). We'd rather lose the patch and log
# loudly than brick every deploy/migration. `frappe.log_error` is avoided here
# because the DB may not be ready this early in import — stderr/bench logs are.
try:
    from playground.overrides import production_plan  # noqa: F401
except Exception:
    import logging

    logging.getLogger(__name__).warning(
        "playground: Production Plan qty-0 override failed to load — "
        "core get_items_for_material_requests() is UNPATCHED. "
        "Check that playground/overrides/ shipped in this build.",
        exc_info=True,
    )
