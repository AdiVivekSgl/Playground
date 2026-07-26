# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Unified Planning Workbook (4 sheets)
====================================

A single workbook the FGSRM report and the Weekly Planning Snapshot both download
after building a Production Plan. It walks the whole planning story end to end, in
one file, in one consistent format - and its last sheet is drop-in ready for the
Purchase Authorization Sheet's "Populate from Excel".

The Production Plan form keeps its own existing download (frontec's MR Hierarchy
Excel) untouched - this workbook is the FGSRM/WPS-side artifact only.

Sheets (each shares one grammar: column A = Item Code, column B = Item Name, a
grey/bold/centred header on row 1, Float qty / Currency value, a bold TOTAL row):

  1. "FG Reservation Status"        - the full FGSRM picture per open SO line:
                                       Requirements (Pending / Short), Reservations
                                       (Reserved / Total / by customer), Availability
                                       (Item Free Stock) and Status (Material / Sales).
  2. "Production Requirement"       - the production ask. Section A is the per-SO
                                       line detail (Pending .. Suggested .. Committed);
                                       Section B is the consolidated-by-item summary
                                       whose Committed column is the plan's own root
                                       po_items (authoritative for both entry points).
  3. "Item Requirement (BOM Levels)"- the nested plan chain exploded across every BOM
                                       level (the raw-material shortage across the
                                       chain): Qty As Per BOM, live WO Qty formula,
                                       Plan to Request, stock/ordered, Short QTY.
  4. "Approved for Purchase"        - the summary buy-list: purchasable items netted
                                       once against stock + pending POs. Column A = Item,
                                       column B = Qty exactly as the PAS reader expects
                                       (`purchase_authorization_sheet._read_approved_sheet`);
                                       columns C+ are human-facing enrichment the reader
                                       ignores.

Data sources by entry point:
  - From FGSRM: `filters` (the report's filter JSON) drives sheets 1-2; the plan
    `name` drives sheets 2B/3/4. Committed at the line level mirrors Suggested
    (the plan was built from Suggested), and 2B/plan reconcile it item-wise.
  - From WPS:   `snapshot` (the Weekly Planning Snapshot name) drives sheets 1-2
    from the frozen lines incl. Committed Prodn and Buffer rows; the plan `name`
    drives sheets 2B/3/4.

Built with openpyxl (bundled with Frappe) and streamed via frappe.response.
"""

from io import BytesIO

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from playground.playground.report.fg_stock_reservation_manager.fg_stock_reservation_manager import (
    execute as fgsrm_execute,
)
from playground.playground.report.production_requirement_report.production_requirement_report import (
    get_stock_map,
)

APPROVED_SHEET = "Approved for Purchase"  # must match purchase_authorization_sheet.APPROVED_SHEET

# frontec's parent-plan link that chains the nested Production Plans (used to walk
# the plan chain for sheets 3-4).
PARENT_FIELD = "custom_parent_production_plan"
MAX_LEVELS = 5
_HEADER_FILL = "D3D3D3"


@frappe.whitelist()
def download_unified_planning_workbook(plan, filters=None, snapshot=None):
    """Stream the 4-sheet unified workbook for Production Plan `plan`.

    Exactly one of `filters` (FGSRM entry point) or `snapshot` (WPS entry point)
    is expected for sheets 1-2; sheets 2B/3/4 always come from the plan chain.
    Both are optional so the endpoint degrades gracefully (a missing source just
    yields an empty sheet 1/2 rather than an error)."""
    if not frappe.has_permission("Production Plan", "read", doc=plan):
        frappe.throw(
            _("You are not permitted to read Production Plan {0}.").format(plan),
            frappe.PermissionError,
        )
    if snapshot and not frappe.has_permission("Weekly Planning Snapshot", "read", doc=snapshot):
        frappe.throw(
            _("You are not permitted to read Weekly Planning Snapshot {0}.").format(snapshot),
            frappe.PermissionError,
        )

    filters = frappe.parse_json(filters) if filters else {}

    import openpyxl

    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # drop the default empty sheet

    lines = _planning_lines(filters, snapshot)
    committed_by_item = _plan_committed_by_item(plan)

    _build_fg_status_sheet(wb, lines)
    _build_production_requirement_sheet(wb, lines, committed_by_item)
    _build_bom_levels_sheet(wb, plan)
    _build_approved_for_purchase_sheet(wb, plan)

    stream = BytesIO()
    wb.save(stream)

    frappe.response["filename"] = "Unified_Planning_{0}.xlsx".format(str(plan).replace("/", "-"))
    frappe.response["filecontent"] = stream.getvalue()
    frappe.response["type"] = "binary"


# --------------------------------------------------------------------------- #
# Shared line model for sheets 1 & 2
# --------------------------------------------------------------------------- #

def _planning_lines(filters, snapshot):
    """Normalise the demand into one list of per-line dicts both sheet 1 and
    sheet 2 render from, so the two never drift.

    Keys: item_code, item_name, customer, sales_order, so_date, pending_qty,
    reserved_qty, item_free_stock, suggested_prodn, committed_prodn,
    valuation_rate, pending_value, short_to_complete, total_reserved_qty,
    reserved_by_customer, material_status, sales_status, source, is_buffer."""
    if snapshot:
        return _lines_from_snapshot(snapshot)
    if filters:
        return _lines_from_fgsrm(filters)
    return []


def _lines_from_snapshot(snapshot):
    """Per-line model from a Weekly Planning Snapshot's frozen items (incl. Buffer
    rows). Committed Prodn and Valuation Rate come straight off the snapshot;
    Material/Sales Status are read live from the Sales Orders (the snapshot doesn't
    freeze them)."""
    doc = frappe.get_doc("Weekly Planning Snapshot", snapshot)
    sos = sorted({d.sales_order for d in doc.items if d.sales_order})
    material_status, sales_status = _so_status_maps(sos)

    lines = []
    for d in doc.items:
        pending = flt(d.pending_qty)
        reserved = flt(d.reserved_qty)
        lines.append(
            {
                "item_code": d.item_code,
                "item_name": d.item_name,
                "customer": d.customer,
                "sales_order": d.sales_order,
                "so_date": d.so_date,
                "pending_qty": pending,
                "reserved_qty": reserved,
                "item_free_stock": flt(d.item_free_stock),
                "suggested_prodn": flt(d.suggested_prodn),
                "committed_prodn": flt(d.committed_prodn),
                "valuation_rate": flt(d.valuation_rate),
                # A snapshot line carries no selling rate, so Pending Value is left
                # to sheet 1's blank; the sheet totals qty, not value, for WPS.
                "pending_value": 0.0,
                "short_to_complete": max(0.0, pending - reserved),
                "total_reserved_qty": None,
                "reserved_by_customer": "",
                "material_status": material_status.get(d.sales_order),
                "sales_status": sales_status.get(d.sales_order),
                "source": _("Buffer") if d.is_buffer else "",
                "is_buffer": cint(d.is_buffer),
            }
        )
    return lines


def _lines_from_fgsrm(filters):
    """Per-line model from the live FGSRM report for `filters`. Committed Prodn
    mirrors Suggested Prodn (the plan is built from Suggested); Valuation Rate is
    enriched from STOCK_WAREHOUSE so sheet 2 can still value the commitment. The
    report's own TOTAL row is dropped - each sheet appends its own."""
    _columns, rows = fgsrm_execute(dict(filters or {}))
    rows = [r for r in rows if not r.get("is_total")]

    item_codes = sorted({r.get("item_code") for r in rows if r.get("item_code")})
    stock_map = get_stock_map(item_codes)

    lines = []
    for r in rows:
        suggested = flt(r.get("suggested_prodn"))
        stock = stock_map.get(r.get("item_code")) or frappe._dict()
        lines.append(
            {
                "item_code": r.get("item_code"),
                "item_name": r.get("item_name"),
                "customer": r.get("customer"),
                "sales_order": r.get("sales_order"),
                "so_date": r.get("so_date"),
                "pending_qty": flt(r.get("pending_qty")),
                "reserved_qty": flt(r.get("reserved_qty")),
                "item_free_stock": flt(r.get("item_free_stock")),
                "suggested_prodn": suggested,
                "committed_prodn": suggested,
                "valuation_rate": flt(stock.get("valuation_rate")),
                "pending_value": flt(r.get("pending_value")),
                "short_to_complete": flt(r.get("short_to_complete")),
                "total_reserved_qty": r.get("total_reserved_qty"),
                "reserved_by_customer": r.get("reserved_by_customer") or "",
                "material_status": r.get("material_status"),
                "sales_status": r.get("sales_status"),
                "source": r.get("source") or "",
                "is_buffer": 0,
            }
        )
    return lines


def _so_status_maps(sos):
    """({so: material_status}, {so: sales_status}) read live from the Sales Orders,
    each column fetched only where it exists (so this runs on a site missing one)."""
    if not sos:
        return {}, {}
    fields = ["name"]
    has_material = frappe.db.has_column("Sales Order", "custom_material_status")
    has_sales = frappe.db.has_column("Sales Order", "custom_sales_status")
    if has_material:
        fields.append("custom_material_status")
    if has_sales:
        fields.append("custom_sales_status")
    material, sales = {}, {}
    if len(fields) > 1:
        for r in frappe.get_all("Sales Order", filters={"name": ["in", sos]}, fields=fields):
            material[r.name] = r.get("custom_material_status") if has_material else None
            sales[r.name] = r.get("custom_sales_status") if has_sales else None
    return material, sales


def _line_sort_key(line):
    """Dispatch Priority Date, then Customer; Buffer rows (no date) fall to the
    bottom - matching the WPS Detailed view ordering."""
    sd = line.get("so_date")
    return (
        getdate(sd) if sd else getdate("9999-12-31"),
        1 if line.get("is_buffer") else 0,
        line.get("customer") or "",
        line.get("item_code") or "",
    )


# --------------------------------------------------------------------------- #
# Sheet 1 - FG Reservation Status
# --------------------------------------------------------------------------- #

def _build_fg_status_sheet(wb, lines):
    ws = wb.create_sheet("FG Reservation Status")
    headers = [
        "Item Code",            # A
        "Item Name",            # B
        "Customer",             # C
        "SO",                   # D
        "Dispatch Priority Date",  # E
        "Pending Qty",          # F  ── Requirements
        "Pending Value",        # G
        "Short to Complete",    # H
        "Reserved Qty",         # I  ── Reservations
        "Total Reserved Qty",   # J
        "Reserved by Customer", # K
        "Item Free Stock",      # L  ── Availability
        "Suggested Prodn",      # M  ── Production
        "Material Status",      # N  ── Status
        "Sales Status",         # O
        "Source",               # P
    ]
    _write_header(ws, headers)

    for line in sorted(lines, key=_line_sort_key):
        ws.append([
            line.get("item_code"),
            line.get("item_name"),
            line.get("customer"),
            line.get("sales_order"),
            line.get("so_date"),
            flt(line.get("pending_qty")),
            flt(line.get("pending_value")),
            flt(line.get("short_to_complete")),
            flt(line.get("reserved_qty")),
            (flt(line.get("total_reserved_qty")) if line.get("total_reserved_qty") is not None else None),
            line.get("reserved_by_customer"),
            flt(line.get("item_free_stock")),
            flt(line.get("suggested_prodn")),
            line.get("material_status"),
            line.get("sales_status"),
            line.get("source"),
        ])

    # TOTAL row - sum only the columns that legitimately add up (Item Free Stock is
    # a per-item figure repeated on every line, so it is deliberately not summed),
    # mirroring FGSRM's own _with_total.
    if lines:
        _write_total_row(
            ws,
            label_col=1,
            values={
                6: sum(flt(l.get("pending_qty")) for l in lines),
                7: sum(flt(l.get("pending_value")) for l in lines),
                9: sum(flt(l.get("reserved_qty")) for l in lines),
                13: sum(flt(l.get("suggested_prodn")) for l in lines),
            },
        )
    _autosize(ws, headers)


# --------------------------------------------------------------------------- #
# Sheet 2 - Production Requirement (Section A per-line + Section B consolidated)
# --------------------------------------------------------------------------- #

def _build_production_requirement_sheet(wb, lines, committed_by_item):
    ws = wb.create_sheet("Production Requirement")

    # ── Section A: per-SO line detail ────────────────────────────────────────
    headers_a = [
        "Item Code",        # A
        "Item Name",        # B
        "Customer",         # C
        "SO",               # D
        "Dispatch Priority Date",  # E
        "Pending Qty",      # F
        "Reserved Qty",     # G
        "Item Free Stock",  # H
        "Suggested Prodn",  # I
        "Committed Prodn",  # J
        "Valuation Rate",   # K
        "Committed Value",  # L
    ]
    _write_header(ws, headers_a)

    for line in sorted(lines, key=_line_sort_key):
        committed = flt(line.get("committed_prodn"))
        rate = flt(line.get("valuation_rate"))
        ws.append([
            line.get("item_code"),
            line.get("item_name"),
            line.get("customer"),
            line.get("sales_order"),
            line.get("so_date"),
            flt(line.get("pending_qty")),
            flt(line.get("reserved_qty")),
            flt(line.get("item_free_stock")),
            flt(line.get("suggested_prodn")),
            committed,
            rate,
            committed * rate,
        ])
    if lines:
        _write_total_row(
            ws,
            label_col=1,
            values={
                9: sum(flt(l.get("suggested_prodn")) for l in lines),
                10: sum(flt(l.get("committed_prodn")) for l in lines),
                12: sum(flt(l.get("committed_prodn")) * flt(l.get("valuation_rate")) for l in lines),
            },
        )
    _autosize(ws, headers_a)

    # ── Section B: consolidated by item ──────────────────────────────────────
    # Committed here is the plan's own root po_items (authoritative for both entry
    # points), so this summary reconciles with sheets 3-4. Item Free Stock and
    # Total Suggested are folded from the per-line model for context.
    start = ws.max_row + 2  # one blank spacer row between the two tables
    ws.cell(start, 1, _("Consolidated by Item")).font = _bold()
    header_row = start + 1
    headers_b = [
        "Item Code",        # A
        "Item Name",        # B
        "Item Free Stock",  # C
        "Total Suggested",  # D
        "Committed Prodn",  # E
        "Valuation Rate",   # F
        "Committed Value",  # G
    ]
    _write_header(ws, headers_b, row=header_row)

    by_item = {}
    order = []
    for line in lines:
        ic = line.get("item_code")
        if ic not in by_item:
            by_item[ic] = {
                "item_name": line.get("item_name"),
                # Per-item fact: take it once, don't sum it across the item's lines.
                "item_free_stock": flt(line.get("item_free_stock")),
                "suggested": 0.0,
                "valuation_rate": flt(line.get("valuation_rate")),
            }
            order.append(ic)
        by_item[ic]["suggested"] += flt(line.get("suggested_prodn"))

    # Include any plan item that carried no demand line too, so the summary is the
    # full committed set that seeded the plan.
    for ic in committed_by_item:
        if ic not in by_item:
            by_item[ic] = {"item_name": None, "item_free_stock": 0.0, "suggested": 0.0, "valuation_rate": 0.0}
            order.append(ic)

    r = header_row + 1
    tot_sug = tot_com = tot_val = 0.0
    for ic in order:
        agg = by_item[ic]
        committed = flt(committed_by_item.get(ic))
        rate = flt(agg["valuation_rate"])
        value = committed * rate
        ws.cell(r, 1, ic)
        ws.cell(r, 2, agg["item_name"])
        ws.cell(r, 3, flt(agg["item_free_stock"]))
        ws.cell(r, 4, flt(agg["suggested"]))
        ws.cell(r, 5, committed)
        ws.cell(r, 6, rate)
        ws.cell(r, 7, value)
        tot_sug += flt(agg["suggested"])
        tot_com += committed
        tot_val += value
        r += 1
    if order:
        _write_total_row(ws, label_col=1, values={4: tot_sug, 5: tot_com, 7: tot_val}, row=r)


# --------------------------------------------------------------------------- #
# Sheet 3 - Item Requirement (BOM Levels)  [nested-chain raw-material shortage]
# --------------------------------------------------------------------------- #

def _build_bom_levels_sheet(wb, plan_name):
    ws = wb.create_sheet("Item Requirement (BOM Levels)")
    headers = [
        "Item Code",            # A
        "Item Name",            # B
        "Type",                 # C
        "Explosion Lvl",        # D
        "Qty As Per BOM",       # E
        "WO Qty",               # F  (in-cell formula, below)
        "Plan to Request Qty",  # G
        "Safety Stock",         # H
        "Minimum Order Qty",    # I
        "Qty In Stock",         # J
        "Ordered Qty",          # K
        "Short QTY",            # L  (static computed: max(0, qty - in-stock - pending PO))
    ]
    _write_header(ws, headers)

    rows = _collect_mr_rows(_build_chain(plan_name))
    if not rows:
        _autosize(ws, headers)
        return

    item_codes = sorted({r["item_code"] for r in rows})
    item_names = _item_name_map(item_codes)
    po_pending = _pending_po_map(item_codes)      # K: outstanding PO qty
    min_oqty = _item_field_map(item_codes, "min_order_qty")
    safety = _item_field_map(item_codes, "safety_stock")

    r = 2  # data starts at row 2 (headers on row 1)
    for row in rows:
        item = row["item_code"]
        qty = flt(row.get("quantity"))                 # Plan to Request Qty
        bom_qty = flt(row.get("required_bom_qty"))     # Qty As Per BOM
        actual_qty = flt(row.get("actual_qty"))        # Qty In Stock
        pending_po = flt(po_pending.get(item))         # Ordered Qty
        short_qty = max(0.0, qty - actual_qty - pending_po)

        ws.cell(r, 1, item)
        ws.cell(r, 2, item_names.get(item))
        ws.cell(r, 3, row.get("material_request_type"))
        ws.cell(r, 4, row["_level"])
        ws.cell(r, 5, bom_qty)
        # F "WO Qty" - live formula per the template: =MAX(0, QtyAsPerBOM - QtyInStock) - PlanToRequest
        ws.cell(r, 6, "=MAX(0,E{r}-J{r})-G{r}".format(r=r))
        ws.cell(r, 7, qty)
        ws.cell(r, 8, flt(safety.get(item)))
        ws.cell(r, 9, flt(min_oqty.get(item)))
        ws.cell(r, 10, actual_qty)
        ws.cell(r, 11, pending_po)
        ws.cell(r, 12, short_qty)
        r += 1

    _autosize(ws, headers)


# --------------------------------------------------------------------------- #
# Sheet 4 - Approved for Purchase  [PAS upload: A = Item, B = Qty]
# --------------------------------------------------------------------------- #

def _build_approved_for_purchase_sheet(wb, plan_name):
    ws = wb.create_sheet(APPROVED_SHEET)
    headers = [
        "Item",        # A  ← PAS reads item_code from here
        "Qty",         # B  ← PAS reads the purchase qty from here
        "Item Name",   # C  ── everything from column C on is enrichment the PAS
        "UOM",         # D     reader ignores (it re-derives these from ERPNext on
        "In Stock",    # E     Populate from Excel); shown for the human approving.
        "Reserved",    # F
        "Rate",        # G
        "Value",       # H
        "Vendor",      # I
        "Lead Time",   # J
    ]
    _write_header(ws, headers)

    rows = _collect_mr_rows(_build_chain(plan_name))
    # Purchase-type lines only; net each item ONCE against stock + pending POs
    # (an item can recur across levels, so summing per-row Short QTY would
    # over-subtract its stock - aggregate Plan to Request first, then net).
    purchase_items = sorted({r["item_code"] for r in rows if r.get("material_request_type") == "Purchase"})
    if not purchase_items:
        _autosize(ws, headers)
        return

    plan_req = {}
    actual_by_item = {}
    for row in rows:
        if row.get("material_request_type") != "Purchase":
            continue
        item = row["item_code"]
        plan_req[item] = plan_req.get(item, 0.0) + flt(row.get("quantity"))
        actual_by_item[item] = flt(row.get("actual_qty"))  # per-item constant

    po_pending = _pending_po_map(purchase_items)
    info = _item_info_map(purchase_items)
    all_wh_stock = _all_warehouse_stock_map(purchase_items)
    vendors = _default_supplier_map(purchase_items)

    r = 2
    tot_qty = tot_val = 0.0
    for item in purchase_items:
        net = max(0.0, flt(plan_req.get(item)) - flt(actual_by_item.get(item)) - flt(po_pending.get(item)))
        if net <= 0:
            continue
        it = info.get(item) or frappe._dict()
        actual, reserved = all_wh_stock.get(item, (0.0, 0.0))
        rate = flt(it.get("valuation_rate"))
        value = net * rate
        ws.cell(r, 1, item)
        ws.cell(r, 2, net)
        ws.cell(r, 3, it.get("item_name"))
        ws.cell(r, 4, it.get("stock_uom"))
        ws.cell(r, 5, flt(actual))
        ws.cell(r, 6, flt(reserved))
        ws.cell(r, 7, rate)
        ws.cell(r, 8, value)
        ws.cell(r, 9, vendors.get(item))
        ws.cell(r, 10, cint(it.get("lead_time_days")))
        tot_qty += net
        tot_val += value
        r += 1

    # TOTAL row - the PAS reader skips a row whose column A is "Total", so this is
    # safe to leave in the upload.
    if r > 2:
        ws.cell(r, 1, _("Total")).font = _bold()
        c = ws.cell(r, 2, tot_qty)
        c.font = _bold()
        c = ws.cell(r, 8, tot_val)
        c.font = _bold()
    _autosize(ws, headers)


# --------------------------------------------------------------------------- #
# Data helpers
# --------------------------------------------------------------------------- #

def _plan_committed_by_item(plan_name):
    """{item_code: planned_qty} from the plan chain's ROOT po_items - the
    authoritative committed production that seeded the whole plan (the root
    Assembly Items of the nested Production Plan)."""
    root = _build_chain(plan_name)[0]
    out = {}
    for row in frappe.get_all(
        "Production Plan Item",
        filters={"parent": root},
        fields=["item_code", "planned_qty"],
        order_by="idx asc",
    ):
        out[row.item_code] = out.get(row.item_code, 0.0) + flt(row.planned_qty)
    return out


def _item_name_map(items):
    """{item_code: item_name}."""
    if not items:
        return {}
    return {
        r.name: r.item_name
        for r in frappe.get_all("Item", filters={"name": ["in", items]}, fields=["name", "item_name"])
    }


def _item_info_map(items):
    """{item_code: {item_name, stock_uom, valuation_rate, lead_time_days}} for the
    Approved for Purchase enrichment columns."""
    if not items:
        return {}
    return {
        r.name: r
        for r in frappe.get_all(
            "Item",
            filters={"name": ["in", items]},
            fields=["name", "item_name", "stock_uom", "valuation_rate", "lead_time_days"],
        )
    }


def _all_warehouse_stock_map(items):
    """{item_code: (actual_qty, reserved_qty)} summed across ALL warehouses - the
    same basis the PAS shows for In Stock / Reserved (purchase_authorization_sheet._stock)."""
    if not items:
        return {}
    rows = frappe.db.sql(
        """
        SELECT item_code, SUM(actual_qty) AS actual_qty, SUM(reserved_qty) AS reserved_qty
        FROM `tabBin` WHERE item_code IN %(items)s GROUP BY item_code
        """,
        {"items": items},
        as_dict=True,
    )
    return {r.item_code: (flt(r.actual_qty), flt(r.reserved_qty)) for r in rows}


def _default_supplier_map(items):
    """{item_code: default_supplier} from Item Default (first row per item)."""
    if not items:
        return {}
    out = {}
    for r in frappe.get_all(
        "Item Default",
        filters={"parent": ["in", items], "default_supplier": ["is", "set"]},
        fields=["parent", "default_supplier"],
    ):
        out.setdefault(r.parent, r.default_supplier)
    return out


# --------------------------------------------------------------------------- #
# Plan-chain helpers (walk the nested Production Plan for sheets 3-4)
# --------------------------------------------------------------------------- #

def _build_chain(name):
    """[root, ..., leaf] for the nested Production Plan chain (mirrors frontec's
    _build_hierarchy_chain): walk up via PARENT_FIELD to the root, then down the
    single-child chain to the leaf, capped at MAX_LEVELS against circular refs.
    Degrades to [name] where the frontec parent field isn't installed."""
    if not frappe.db.has_column("Production Plan", PARENT_FIELD):
        return [name]

    # Phase 1 - find root
    visited = set()
    current = name
    while current and len(visited) < MAX_LEVELS:
        if current in visited:
            break
        visited.add(current)
        parent = frappe.db.get_value("Production Plan", current, PARENT_FIELD)
        if not parent:
            break
        current = parent
    root = current

    # Phase 2 - walk down root -> leaf
    chain = []
    seen = set()
    current = root
    while current and len(chain) < MAX_LEVELS:
        if current in seen:
            break
        seen.add(current)
        chain.append(current)
        current = frappe.db.get_value("Production Plan", {PARENT_FIELD: current}, "name")
    return chain


def _collect_mr_rows(chain):
    """Every Material Request Plan Item across the chain, tagged with its level
    (L1..Ln)."""
    rows = []
    for idx, pp in enumerate(chain):
        level = "L{0}".format(idx + 1)
        for d in frappe.get_all(
            "Material Request Plan Item",
            filters={"parent": pp},
            fields=["item_code", "material_request_type", "quantity", "required_bom_qty", "actual_qty"],
            order_by="idx asc",
        ):
            d["_level"] = level
            rows.append(d)
    return rows


def _pending_po_map(items):
    """{item_code: outstanding PO qty} = Σ max(qty - received_qty, 0) over
    submitted, non-Closed/Cancelled Purchase Orders (frontec's po_pending)."""
    if not items:
        return {}
    rows = frappe.db.sql(
        """
        SELECT poi.item_code,
            SUM(GREATEST(poi.qty - IFNULL(poi.received_qty, 0), 0)) AS pending_qty
        FROM `tabPurchase Order Item` poi
        INNER JOIN `tabPurchase Order` po ON po.name = poi.parent
        WHERE poi.item_code IN %(codes)s
            AND po.docstatus = 1
            AND po.status NOT IN ('Closed', 'Cancelled')
        GROUP BY poi.item_code
        """,
        {"codes": items},
        as_dict=True,
    )
    return {r.item_code: flt(r.pending_qty) for r in rows}


def _item_field_map(items, field):
    """{item_code: <field>} from the Item master (used for Minimum Order Qty and
    Safety Stock)."""
    if not items:
        return {}
    rows = frappe.db.sql(
        "SELECT name, `{0}` AS val FROM `tabItem` WHERE name IN %(codes)s".format(field),
        {"codes": items},
        as_dict=True,
    )
    return {r.name: flt(r.val) for r in rows}


# --------------------------------------------------------------------------- #
# Small styling helpers
# --------------------------------------------------------------------------- #

def _write_header(ws, headers, row=1):
    """Grey, bold, centred header row."""
    from openpyxl.styles import Alignment, Font, PatternFill

    fill = PatternFill(fill_type="solid", fgColor=_HEADER_FILL)
    font = Font(bold=True)
    center = Alignment(horizontal="center")
    for col, label in enumerate(headers, start=1):
        c = ws.cell(row, col, label)
        c.font = font
        c.fill = fill
        c.alignment = center


def _autosize(ws, headers):
    from openpyxl.utils import get_column_letter

    for col, label in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(col)].width = max(12, len(str(label)) + 2)


def _bold():
    from openpyxl.styles import Font

    return Font(bold=True)


def _write_total_row(ws, label_col, values, row=None):
    """Append (or write at `row`) a bold TOTAL row: the word TOTAL in `label_col`
    and each {column_index: value} bolded. Columns not listed are left blank."""
    if row is None:
        row = ws.max_row + 1
    bold = _bold()
    lc = ws.cell(row, label_col, _("TOTAL"))
    lc.font = bold
    for col, val in values.items():
        c = ws.cell(row, col, flt(val))
        c.font = bold
    return row
