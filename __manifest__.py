{
    "name": "BW People Ops",
    "summary": "Activation gate on contracts, access grant registry, secondary client owner",
    "version": "18.0.2.0.0",
    "category": "Human Resources",
    "author": "BWEALTHICS LLC",
    "website": "https://www.bwealthics.com",
    "license": "LGPL-3",
    "depends": ["hr_contract_sign"],
    "data": [
        "security/ir.model.access.csv",
        "views/bw_access_grant_views.xml",
        "views/res_partner_views.xml",
        "views/hr_views.xml",
        "report/bw_contract_report.xml",
    ],
    "installable": True,
    "application": False,
}
