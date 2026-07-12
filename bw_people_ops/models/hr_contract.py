import logging
import re

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# ponytail: keyword classification of sign requests; enough while the template
# set is small, revisit if templates multiply. Word boundaries keep short
# tokens ("nda", "ica") from matching inside Spanish words like "enmienda" or
# "identificacion". An NDA never counts as the agreement (see _bw_gate_missing).
NDA_PATTERNS = (r"\bnda\b", r"non.disclosure", r"confidencialidad", r"confidentiality")
AGREEMENT_PATTERNS = (r"\bagreement\b", r"\bcontract\b", r"\bcontrato\b", r"\bacuerdo\b", r"\bica\b")


class HrContract(models.Model):
    _inherit = "hr.contract"

    def _bw_gate_missing(self):
        """Return the list of documents the activation gate still requires on
        this contract (empty list = gate passes).

        Gate rule: the NDA and the main agreement (employment contract or ICA)
        must be fully signed on the contract itself (hr_contract_sign's
        Signatures tab). Tax forms and the official ID are checklist items and
        neither block nor satisfy the gate.
        """
        self.ensure_one()
        signed = self.sign_request_ids.filtered(lambda r: r.state == "signed")

        def label(request):
            return (
                (request.template_id.name or "") + " " + (request.reference or "")
            ).lower()

        ndas = signed.filtered(
            lambda r: any(re.search(p, label(r)) for p in NDA_PATTERNS)
        )
        agreements = (signed - ndas).filtered(
            lambda r: any(re.search(p, label(r)) for p in AGREEMENT_PATTERNS)
        )
        missing = []
        if not ndas:
            missing.append(_("NDA"))
        if not agreements:
            missing.append(_("employment agreement / ICA"))
        return missing

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
        if vals.get("state") == "open" and not self.env.context.get("bw_skip_gate"):
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
        if not self.env.context.get("bw_skip_gate"):
            # Covers CSV/XLSX imports, XML-RPC creates and duplications that
            # land directly in Running without going through write().
            failing = contracts.filtered(
                lambda c: c.state == "open" and c._bw_gate_missing()
            )
            if failing:
                self._bw_raise_gate(failing[0])
        return contracts
