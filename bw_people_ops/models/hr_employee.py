# Copyright 2026 BWEALTHICS LLC
# Part of bw_people_ops. License LGPL-3.
from odoo import api, fields, models

CONTRACTOR_TYPES = ("contractor", "freelance")


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    bw_doc_lang = fields.Selection(
        [("es", "Español"), ("en", "English")],
        string="Document Language",
        default="es",
        groups="hr.group_hr_user",
        help="Contracts are generated in this single language.",
    )
    bw_tax_form = fields.Selection(
        [("w4", "W-4 + I-9"), ("w9", "W-9"), ("w8ben", "W-8BEN")],
        string="Tax Form",
        compute="_compute_bw_tax_form",
        store=True,
        readonly=False,
        groups="hr.group_hr_user",
        help="US employee: W-4 and I-9. US contractor: W-9. Contractor "
        "abroad: W-8BEN. Left empty when the data is not enough to tell "
        "(e.g. a foreign person working inside the US): set it by hand. "
        "Recomputed when the employee type or private country changes.",
    )

    @api.depends("employee_type", "private_country_id")
    def _compute_bw_tax_form(self):
        for employee in self:
            country = employee.private_country_id.code
            contractor = employee.employee_type in CONTRACTOR_TYPES
            if not country:
                employee.bw_tax_form = False
            elif contractor:
                employee.bw_tax_form = "w9" if country == "US" else "w8ben"
            else:
                employee.bw_tax_form = "w4" if country == "US" else False
