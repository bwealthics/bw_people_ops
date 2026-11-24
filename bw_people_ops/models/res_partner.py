from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    secondary_owner_id = fields.Many2one(
        "res.users",
        string="Secondary Owner",
        tracking=True,
        help="Backup owner for this client. Every client must have one "
        "(founder bus-factor rule). Handover uses the 'Cliente: Handover de "
        "Dueño' activity plan.",
    )
