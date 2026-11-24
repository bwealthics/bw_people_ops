import io
import logging
import re

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import file_open
from odoo.tools.pdf import PdfFileReader

from . import irs_forms
from .hr_employee import CONTRACTOR_TYPES

_logger = logging.getLogger(__name__)

# ponytail: keyword classification of sign requests; enough while the template
# set is small, revisit if templates multiply. Word boundaries keep short
# tokens ("nda", "ica") from matching inside Spanish words like "enmienda" or
# "identificacion". An NDA never counts as the agreement (see _bw_gate_missing).
NDA_PATTERNS = (r"\bnda\b", r"non.disclosure", r"confidencialidad", r"confidentiality")
AGREEMENT_PATTERNS = (r"\bagreement\b", r"\bcontract\b", r"\bcontrato\b", r"\bacuerdo\b", r"\bica\b")

# Sign items on the contract's last page (fractions of the page, origin
# top-left), matching the fixed 25mm/8mm boxes of report/bw_contract_report.xml.
# Measured on the PDF rendered by the production server with the module's own
# paper format (paperformat_bw_document) on 2026-11-25. Re-measure if that
# format or the signature block changes.
CONTRACT_SIGN_BOXES = {
    "signature": {"posY": 0.135, "width": 0.187, "height": 0.070},
    "date": {"posY": 0.2115, "width": 0.187, "height": 0.019},
}
CONTRACT_SIGN_X = {"company": 0.294, "worker": 0.713}
# References of the tax-form requests this module creates (see bw_tax_form).
TAX_FORM_PREFIXES = ("W-4", "W-9", "W-8BEN")
SIGN_ITEM_TYPES = {
    "signature": "sign.sign_item_type_signature",
    "date": "sign.sign_item_type_date",
    "tin": "sign.sign_item_type_text",
    "us_tin": "sign.sign_item_type_text",
}


class HrContract(models.Model):
    _inherit = "hr.contract"

    bw_doc_kind = fields.Selection(
        [("employment", "Employment Agreement"), ("contractor", "Contractor Agreement")],
        string="Document",
        compute="_compute_bw_doc_kind",
        help="Follows the employee type: Contractor/Freelancer get the "
        "independent contractor agreement.",
    )
    bw_flsa = fields.Selection(
        [("exempt", "Exempt"), ("non_exempt", "Non-Exempt")],
        string="FLSA Classification",
    )
    bw_modality = fields.Selection(
        [("remote", "Remote"), ("hybrid", "Hybrid"), ("onsite", "On-site")],
        string="Modality",
    )
    bw_schedule = fields.Char(string="Schedule")
    bw_scope = fields.Text(
        string="Duties / Services",
        help="Responsibilities (employment) or scope of services (contractor).",
    )
    bw_compensation_clause = fields.Text(
        string="Compensation / Fees",
        help="Full sentence as it must read in the document.",
    )
    bw_bonus_clause = fields.Text(string="Bonus / Commission")
    bw_benefits_clause = fields.Text(string="Benefits")
    bw_invoicing_clause = fields.Text(string="Invoicing and Payment")
    bw_notice_days = fields.Integer(string="Termination Notice (days)", default=15)

    @api.depends("employee_id.employee_type")
    def _compute_bw_doc_kind(self):
        for contract in self:
            contractor = contract.employee_id.employee_type in CONTRACTOR_TYPES
            contract.bw_doc_kind = "contractor" if contractor else "employment"

    def _bw_sign_request(self, name, pdf, items, signers):
        """Create a one-off sign template + request and link it to the
        contract's Signatures tab, so the activation gate sees it."""
        attachment = self.env["ir.attachment"].create(
            {"name": name + ".pdf", "raw": pdf, "mimetype": "application/pdf"}
        )
        template = self.env["sign.template"].create(
            {
                "name": name,
                "attachment_id": attachment.id,
                "sign_item_ids": [Command.create(item) for item in items],
            }
        )
        # sign.request.create() emails the signers.
        request = self.env["sign.request"].create(
            {
                "template_id": template.id,
                "reference": name,
                "subject": name,
                "request_item_ids": [
                    Command.create({"partner_id": partner.id, "role_id": role.id})
                    for role, partner in signers
                ],
            }
        )
        self.sign_request_ids = [Command.link(request.id)]
        # One-off template: keep it out of the reusable templates list.
        template.active = False
        return request

    def _bw_sign_item(self, kind, role, page, box, required=True):
        return dict(
            box,
            type_id=self.env.ref(SIGN_ITEM_TYPES[kind]).id,
            responsible_id=role.id,
            page=page,
            required=required,
        )

    def _bw_tax_form_values(self):
        employee = self.employee_id
        city_line = ", ".join(
            filter(
                None,
                [
                    employee.private_city,
                    " ".join(
                        filter(
                            None,
                            [employee.private_state_id.code, employee.private_zip],
                        )
                    ),
                ],
            )
        )
        name = employee.legal_name or employee.name
        return {
            "name": name,
            "print_name": name,
            "capacity": True,
            "address": " ".join(
                filter(None, [employee.private_street, employee.private_street2])
            ),
            "city_state_zip": city_line,
            "classification_individual": True,
            "citizenship_country": employee.country_id.name,
            "country": employee.private_country_id.name,
            "birth_date": employee.birthday
            and employee.birthday.strftime("%m-%d-%Y"),
        }

    def action_bw_send_documents(self):
        """Render the NDA and the contract in the worker's language, prefill
        the tax form and send them all to Sign. Nothing but signature, date
        and the tax id is left for the signer."""
        self.ensure_one()
        employee = self.employee_id
        worker = employee.work_contact_id
        company_signer = self.hr_responsible_id.partner_id
        missing = [
            label
            for label, value in (
                (_("worker email (work contact)"), worker.email),
                (_("HR responsible with email"), company_signer.email),
                (_("start date"), self.date_start),
                (_("compensation clause"), self.bw_compensation_clause),
                (_("duties / services"), self.bw_scope),
                (_("document language"), employee.bw_doc_lang),
            )
            if not value
        ]
        form = employee.bw_tax_form
        if form in irs_forms.FIELD_MAP:
            # A tax form with blank identity lines is worse than no form.
            needed = [
                (_("private street"), employee.private_street),
                (_("private city"), employee.private_city),
                (_("private country"), employee.private_country_id),
            ]
            if form == "w8ben":
                needed += [
                    (_("nationality"), employee.country_id),
                    (_("date of birth"), employee.birthday),
                ]
            missing += [label for label, value in needed if not value]
        if missing:
            raise UserError(
                _("Cannot send the documents. Missing: %s", ", ".join(missing))
            )
        # "Agreement" / "NDA" in the name is what the activation gate matches.
        title = (
            "Independent Contractor Agreement"
            if self.bw_doc_kind == "contractor"
            else "Employment Agreement"
        )
        documents = [(title, "bw_people_ops.action_report_bw_contract")]
        if not self._bw_gate_docs()[0]:
            # No signed NDA on the contract yet: send it with the agreement.
            documents.insert(0, ("NDA", "bw_people_ops.action_report_bw_nda"))
        names = ["%s - %s" % (doc_title, employee.name) for doc_title, _r in documents]
        if self.sign_request_ids.filtered(
            lambda r: r.state != "canceled" and r.reference in names
        ):
            # Each send emails the signers: never do it twice by accident.
            raise UserError(
                _(
                    "These documents were already sent for this contract. "
                    "Cancel them in the Signatures tab before sending again."
                )
            )
        worker_role = self.env.ref("sign.sign_item_role_employee")
        company_role = self.env.ref("hr_contract_sign.sign_item_role_job_responsible")
        both = [(company_role, company_signer), (worker_role, worker)]

        # Build every PDF first: nothing is emailed if any of them fails.
        requests = []
        for name, (_title, report) in zip(names, documents):
            pdf = self.env["ir.actions.report"]._render_qweb_pdf(report, self.ids)[0]
            last_page = len(PdfFileReader(io.BytesIO(pdf), strict=False).pages)
            items = [
                self._bw_sign_item(
                    kind, role, last_page, dict(box, posX=CONTRACT_SIGN_X[side])
                )
                for side, role in (("company", company_role), ("worker", worker_role))
                for kind, box in CONTRACT_SIGN_BOXES.items()
            ]
            requests.append((name, pdf, items, both))
        if form in irs_forms.FIELD_MAP:
            with file_open(
                "bw_people_ops/static/src/irs/f%s.pdf" % form, "rb"
            ) as blank:
                filled = irs_forms.fill(
                    form, blank.read(), self._bw_tax_form_values()
                )
            labels = dict(employee._fields["bw_tax_form"].selection)
            requests.append(
                (
                    "%s - %s" % (labels[form], employee.name),
                    filled,
                    [
                        self._bw_sign_item(
                            kind,
                            worker_role,
                            box["page"],
                            {k: v for k, v in box.items() if k != "page"},
                            # W-9: the SSN is mandatory. W-8BEN: not every
                            # country issues a tax id, so both stay optional.
                            required=kind in ("signature", "date") or form == "w9",
                        )
                        for kind, box in irs_forms.SIGN_BOXES[form].items()
                    ],
                    [(worker_role, worker)],
                )
            )
        for request in requests:
            self._bw_sign_request(*request)
        if form not in irs_forms.FIELD_MAP:
            # W-4 and I-9 need the worker's own elections and an in-person
            # document check: not automatable as "sign only".
            self.message_post(
                body=_(
                    "Tax form not sent automatically (%s). Handle W-4 / I-9 "
                    "or set the tax form on the employee by hand.",
                    form or _("not set"),
                )
            )
        return True

    def _bw_gate_label(self, request):
        """Text the gate classifies a sign request by. The worker's name is
        removed and tax forms are ignored, so "W-9 - Ana Contract" or a
        worker called "Ica" can never count as the signed agreement."""
        reference = request.reference or ""
        if reference.startswith(TAX_FORM_PREFIXES):
            return ""
        raw = (request.template_id.name or "") + " " + reference
        for name in {self.employee_id.name, self.employee_id.legal_name}:
            if name:
                raw = raw.replace(name, " ")
        # Normalize _-. to spaces so PDF filenames like
        # "Plantilla_Contrato_ICA.pdf" still hit the word boundaries.
        return re.sub(r"[_\-.]+", " ", raw.lower())

    def _bw_gate_docs(self):
        """Signed NDAs and signed agreements on this contract."""
        self.ensure_one()
        signed = self.sign_request_ids.filtered(lambda r: r.state == "signed")
        ndas = signed.filtered(
            lambda r: any(re.search(p, self._bw_gate_label(r)) for p in NDA_PATTERNS)
        )
        agreements = (signed - ndas).filtered(
            lambda r: any(
                re.search(p, self._bw_gate_label(r)) for p in AGREEMENT_PATTERNS
            )
        )
        return ndas, agreements

    def _bw_gate_missing(self):
        """Return the list of documents the activation gate still requires on
        this contract (empty list = gate passes).

        Gate rule: the NDA and the main agreement (employment contract or ICA)
        must be fully signed on the contract itself (hr_contract_sign's
        Signatures tab). Tax forms and the official ID are checklist items and
        neither block nor satisfy the gate.
        """
        self.ensure_one()
        ndas, agreements = self._bw_gate_docs()
        missing = []
        if not ndas:
            missing.append(_("NDA"))
        if not agreements:
            missing.append(_("employment agreement / ICA"))
        return missing

    def _bw_skip_gate(self):
        # The context comes from the client (any RPC call can set it), so the
        # override only counts for the superuser or a system administrator.
        return self.env.context.get("bw_skip_gate") and (
            self.env.is_superuser() or self.env.user.has_group("base.group_system")
        )

    def _bw_raise_gate(self, contract):
        raise UserError(
            _(
                "Activation gate: '%(contract)s' cannot move to Running until "
                "the NDA and the employment agreement/ICA are fully signed on "
                "the contract (Signatures tab). Missing: %(missing)s",
                contract=contract.name,
                missing=", ".join(contract._bw_gate_missing()),
            )
        )

    def write(self, vals):
        if vals.get("state") == "open" and not self._bw_skip_gate():
            failing = self.filtered(lambda c: c._bw_gate_missing())
            if failing and self.env.context.get("from_cron"):
                # Never abort hr_contract's nightly update_state cron: skip the
                # contracts that fail the gate, activate the rest, log a trail.
                _logger.warning(
                    "bw_people_ops activation gate: cron auto-activation "
                    "skipped for %s (missing signed NDA/agreement)",
                    ", ".join(failing.mapped("name")),
                )
                passing = self - failing
                result = True
                if passing:
                    result = super(HrContract, passing).write(vals)
                rest_vals = {k: v for k, v in vals.items() if k != "state"}
                if rest_vals:
                    super(HrContract, failing).write(rest_vals)
                return result
            if failing:
                self._bw_raise_gate(failing[0])
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        contracts = super().create(vals_list)
        if not self._bw_skip_gate():
            # Covers CSV/XLSX imports, XML-RPC creates and duplications that
            # land directly in Running without going through write().
            failing = contracts.filtered(
                lambda c: c.state == "open" and c._bw_gate_missing()
            )
            if failing:
                self._bw_raise_gate(failing[0])
        return contracts
