frappe.query_reports["FTPL Dispatch Planning Dashboard"] = {
    filters: [
        {
            fieldname: "company",
            label: __("Company"),
            fieldtype: "Link",
            options: "Company",
            default: frappe.defaults.get_user_default("Company"),
        },
        {
            fieldname: "warehouse",
            label: __("Warehouse"),
            fieldtype: "Link",
            options: "Warehouse",
            default: "Stores - FTPL",
            reqd: 1,
        },
        {
            fieldname: "customer",
            label: __("Customer"),
            fieldtype: "MultiSelectList",
            options: "Customer",
            get_data: function (txt) {
                return frappe.db.get_link_options("Customer", txt);
            },
        },
        {
            fieldname: "sales_orders",
            label: __("Sales Orders"),
            fieldtype: "MultiSelectList",
            options: "Sales Order",
            get_data: function (txt) {
                return frappe.db.get_link_options("Sales Order", txt);
            },
        },
        {
            fieldname: "item",
            label: __("Item"),
            fieldtype: "MultiSelectList",
            options: "Item",
            get_data: function (txt) {
                return frappe.db.get_link_options("Item", txt);
            },
        },
        {
            fieldname: "item_group",
            label: __("Item Group"),
            fieldtype: "Link",
            options: "Item Group",
        },
        {
            fieldname: "updated_delivery_date_from",
            label: __("Updated Delivery Date From"),
            fieldtype: "Date",
        },
        {
            fieldname: "updated_delivery_date_to",
            label: __("Updated Delivery Date To"),
            fieldtype: "Date",
        },
        {
            fieldname: "show_only_shortages",
            label: __("Show Only Shortages"),
            fieldtype: "Check",
            default: 0,
        },
        {
            fieldname: "show_only_unreserved",
            label: __("Show Only Unreserved"),
            fieldtype: "Check",
            default: 0,
        },
        {
            fieldname: "show_only_overdue",
            label: __("Show Only Overdue"),
            fieldtype: "Check",
            default: 0,
        },
        {
            fieldname: "group_by",
            label: __("Group By"),
            fieldtype: "Select",
            options: "\nCustomer\nSales Order\nItem",
        },
    ],

    formatter: function (value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);

        if (!data) {
            return value;
        }

        if (column.fieldname === "dispatch_readiness") {
            const color_by_status = {
                "Ready to Dispatch": "green",
                "Partially Ready": "orange",
                "Awaiting Production": "red",
                "Production Required": "red",
            };
            const color = color_by_status[data.dispatch_readiness];
            if (color) {
                return `<span class="indicator ${color}">${value}</span>`;
            }
        }

        if (column.fieldname === "shortage" && data.shortage > 0) {
            return `<span class="indicator red">${value}</span>`;
        }

        return value;
    },

    get_datatable_options(options) {
        return Object.assign(options, {
            treeView: false,
            checkboxColumn: false,
        });
    },

    after_datatable_render(report) {
        const group_by = report.get_filter_value("group_by");
        if (group_by) {
            frappe.show_alert({
                message: __("Use the report menu grouping option to group by {0}; rows remain unaggregated.", [group_by]),
                indicator: "blue",
            });
        }
    },
};
