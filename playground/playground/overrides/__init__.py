# Marks playground.overrides as a Python package.
# Monkey-patches for core ERPNext live in this package (see production_plan.py).
# Intentionally non-empty: a 0-byte __init__.py can be dropped by some build/
# packaging steps, which would make this subpackage un-importable.
