from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class BwAccessGrant(models.Model):
    _name = "bw.access.grant"
    _description = "Access Grant (metadata only, secrets live in 1Password)"
    _inherit = ["mail.thread"]
    _order = "expiry_date, id"

    EXPIRY_REQUIRED_PROFILES = ("contractor", "temporal", "cliente")

    name = fields.Char(compute="_compute_name", store=True)
    employee_id = fields.Many2one("hr.employee", string="Worker", index=True)
    partner_id = fields.Many2one(
        "res.partner",
        string="External Contact",
        index=True,
        help="For client portal users or externals without an employee record.",
    )
    system = fields.Char(
        required=True,
        tracking=True,
        help='System or tool, e.g. "Odoo", "Google Workspace", "GitHub".',
    )
    profile = fields.Selection(
        [
            ("empleado", "Empleado"),
            ("contractor", "Contractor"),
            ("manager", "Manager"),
            ("ejecutivo", "Ejecutivo"),
            ("cliente", "Cliente"),
            ("temporal", "Temporal"),
        ],
        required=True,
        tracking=True,
    )
    date_start = fields.Date(default=fields.Date.context_today, required=True)
    expiry_date = fields.Date(
        tracking=True,
        help="Mandatory for Contractor, Temporal and Cliente profiles. "
        "Ejecutivo is reviewed quarterly via the digest instead.",
    )
    is_exception = fields.Boolean(
        string="Exception",
        tracking=True,
        help="Access that deviates from the profile matrix. Must state a reason.",
    )
    exception_reason = fields.Char()
    vault_ref = fields.Char(
        string="1Password Item",
        help="Item name / vault reference in 1Password. NEVER store the secret itself.",
    )
    notes = fields.Text()
    active = fields.Boolean(default=True)

    @api.depends("employee_id.name", "partner_id.name", "system")
    def _compute_name(self):
        for grant in self:
            who = grant.employee_id.name or grant.partner_id.name or "?"
            grant.name = "%s @ %s" % (who, grant.system or "?")

    @api.constrains("employee_id", "partner_id")
    def _check_person(self):
        for grant in self:
            if not grant.employee_id and not grant.partner_id:
                raise ValidationError(
                    _("An access grant needs a worker or an external contact.")
                )

    @api.constrains("profile", "expiry_date")
    def _check_expiry(self):
        for grant in self:
            if grant.profile in self.EXPIRY_REQUIRED_PROFILES and not grant.expiry_date:
                raise ValidationError(
                    _("Profile '%s' requires an expiry date.") % grant.profile
                )

    @api.constrains("is_exception", "exception_reason")
    def _check_exception(self):
        for grant in self:
            if grant.is_exception and not grant.exception_reason:
                raise ValidationError(_("Exceptions must state a reason."))
