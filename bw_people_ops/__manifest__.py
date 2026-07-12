{
    "name": "BW People Ops",
    "summary": "Activation gate on contracts, access grant registry, secondary client owner",
    "version": "18.0.1.0.0",
    "category": "Human Resources",
    "author": "BWEALTHICS LLC",
    "website": "https://www.bwealthics.com",
    "license": "LGPL-3",
    "depends": ["hr_contract_sign"],
    "data": [
        "security/ir.model.access.csv",
        "views/bw_access_grant_views.xml",
        "views/res_partner_views.xml",
    ],
    "installable": True,
    "application": False,
}
