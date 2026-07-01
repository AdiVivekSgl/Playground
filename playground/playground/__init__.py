__version__ = "0.0.1"

# Monkey-patch core ERPNext Production Plan to allow rows with Planned Qty = 0
# — see overrides/production_plan.py for details.
from playground.overrides import production_plan  # noqa: F401
