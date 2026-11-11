{
    "name": "Today",
    "version": "17.0.1.0.0",
    "category": "Services/Property",
    "summary": "What needs your attention today across Property, Billing and Maintenance, and the first-time setup guide.",
    "author": "Ayub",
    "license": "LGPL-3",
    "depends": ["pm_property", "pm_billing", "pm_maintenance"],
    "data": [
        "security/ir.model.access.csv",
        "views/pm_today_views.xml",
        "views/pm_setup_views.xml",
        "views/menus.xml",
    ],
    "application": True,
    "installable": True,
}
