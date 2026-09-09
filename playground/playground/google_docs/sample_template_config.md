# Sample Template configuration — Purchase Order

How to fill the **Google Document Template** record that pairs with
`sample_purchase_order_template.md`.

## Header

| Field | Value |
| ----- | ----- |
| Template Name | `Purchase Order — Standard` |
| Enabled | ✔ |
| Reference DocType | `Purchase Order` |
| Google Template URL | *(paste the template doc URL — the ID auto-fills)* |
| Google Drive Folder ID | *(destination folder id)* |
| File Naming Pattern | `{{doctype}} - {{name}} - {{supplier_name}}` |
| Creation | `Manual` |
| Update Behaviour | `Update Existing Google Doc` |
| Sharing | `Inherit Folder` |

## Field Mappings

| ERP Fieldname | Google Placeholder | Field Type | Required |
| ------------- | ------------------ | ---------- | :------: |
| `name` | `{{document_number}}` | Data | ✔ |
| `transaction_date` | `{{transaction_date}}` | Date | ✔ |
| `supplier_name` | `{{supplier_name}}` | Data | ✔ |
| `address_display` | `{{supplier_address}}` | Text Editor | |
| `grand_total` | `{{grand_total}}` | Currency | ✔ |
| `terms` | `{{terms}}` | Text Editor | |

> Placeholders may be left blank to default to `{{erp_fieldname}}`. `Field Type`
> may be left blank to auto-detect from the Purchase Order.

## Tables

| Child Table Fieldname | Table Marker | Child DocType |
| --------------------- | ------------ | ------------- |
| `items` | `{{items_table}}` | Purchase Order Item |

## Table Columns  (all tagged with Table Marker `{{items_table}}`)

| Table Marker | Child Fieldname | Column Label | Type | Width (pt) |
| ------------ | --------------- | ------------ | ---- | ---------: |
| `{{items_table}}` | `item_code` | Item Code | Data | 90 |
| `{{items_table}}` | `description` | Description | Small Text | 200 |
| `{{items_table}}` | `qty` | Qty | Float | 50 |
| `{{items_table}}` | `rate` | Rate | Currency | 70 |
| `{{items_table}}` | `amount` | Amount | Currency | 80 |

Row order in the grid = column order in the rendered table. Tick **Hidden** to
keep a column out of the output without deleting the row.
