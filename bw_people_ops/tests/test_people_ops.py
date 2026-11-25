from types import SimpleNamespace

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase


class TestPeopleOps(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env["hr.employee"].create({"name": "Test Worker"})
        cls.contract = cls.env["hr.contract"].create(
            {
                "name": "Test Contract",
                "employee_id": cls.employee.id,
                "wage": 1.0,
                "date_start": "2026-01-01",
            }
        )

    def test_gate_blocks_without_signatures(self):
        with self.assertRaises(UserError):
            self.contract.write({"state": "open"})

    def test_gate_skip_context(self):
        self.contract.with_context(bw_skip_gate=True).write({"state": "open"})
        self.assertEqual(self.contract.state, "open")

    def test_gate_blocks_create_in_running(self):
        with self.assertRaises(UserError):
            self.env["hr.contract"].create(
                {
                    "name": "Imported Running",
                    "employee_id": self.employee.id,
                    "wage": 1.0,
                    "date_start": "2026-01-01",
                    "state": "open",
                }
            )

    def test_gate_cron_skips_failing(self):
        self.contract.with_context(from_cron=True).write({"state": "open"})
        self.assertEqual(self.contract.state, "draft")

    def test_access_grant_constraints(self):
        Grant = self.env["bw.access.grant"]
        with self.assertRaises(ValidationError):
            Grant.create({"system": "odoo", "profile": "empleado"})
        with self.assertRaises(ValidationError):
            Grant.create(
                {
                    "employee_id": self.employee.id,
                    "system": "odoo",
                    "profile": "contractor",
                }
            )
        with self.assertRaises(ValidationError):
            Grant.create(
                {
                    "employee_id": self.employee.id,
                    "system": "odoo",
                    "profile": "empleado",
                    "is_exception": True,
                }
            )
        grant = Grant.create(
            {
                "employee_id": self.employee.id,
                "system": "odoo",
                "profile": "empleado",
            }
        )
        self.assertEqual(grant.name, "Test Worker @ Odoo")

    def test_access_grant_state_and_unique(self):
        Grant = self.env["bw.access.grant"]
        vals = {
            "employee_id": self.employee.id,
            "system": "gitea",
            "profile": "empleado",
        }
        grant = Grant.create(vals)
        self.assertEqual(grant.state, "pending")
        with self.assertRaises(ValidationError):
            Grant.create(vals)
        # A revoked (archived) grant frees the slot.
        grant.active = False
        Grant.create(vals)
        # "other" may repeat.
        Grant.create(dict(vals, system="other"))
        Grant.create(dict(vals, system="other"))

    def test_tax_form_rule(self):
        us = self.env.ref("base.us")
        ve = self.env.ref("base.ve")
        employee = self.employee
        self.assertFalse(employee.bw_tax_form)
        employee.write({"employee_type": "employee", "private_country_id": us.id})
        self.assertEqual(employee.bw_tax_form, "w4")
        employee.employee_type = "contractor"
        self.assertEqual(employee.bw_tax_form, "w9")
        employee.private_country_id = ve
        self.assertEqual(employee.bw_tax_form, "w8ben")
        # Foreign "employee": not decidable, left for a human.
        employee.employee_type = "employee"
        self.assertFalse(employee.bw_tax_form)

    def test_doc_kind_follows_employee_type(self):
        self.assertEqual(self.contract.bw_doc_kind, "employment")
        self.employee.employee_type = "freelance"
        self.assertEqual(self.contract.bw_doc_kind, "contractor")

    def test_send_documents_requires_data(self):
        with self.assertRaises(UserError):
            self.contract.action_bw_send_documents()

    def test_compensation_text(self):
        contract = self.contract
        self.assertEqual(contract.bw_compensation_text(), "")
        contract.write({"bw_pay_amount": 1500, "bw_pay_unit": "month"})
        self.assertEqual(contract.bw_compensation_text(), "", "Incomplete")
        units = {
            "hour": ("por hora", "per hour"),
            "month": ("por mes", "per month"),
            "year": ("por año", "per year"),
            "fixed": ("como monto fijo", "as a fixed amount"),
        }
        frequencies = {
            "weekly": ("semanalmente", "weekly"),
            "biweekly": ("cada dos semanas", "every two weeks"),
            "semimonthly": ("dos veces al mes", "twice a month"),
            "monthly": ("mensualmente", "monthly"),
            "on_invoice": ("contra factura aprobada", "against approved invoice"),
        }
        for lang, idx, verb in (("es", 0, "pagaderos"), ("en", 1, "payable")):
            self.employee.bw_doc_lang = lang
            for unit, unit_text in units.items():
                for frequency, frequency_text in frequencies.items():
                    contract.write(
                        {"bw_pay_unit": unit, "bw_pay_frequency": frequency}
                    )
                    self.assertEqual(
                        contract.bw_compensation_text(),
                        "USD 1,500.00 %s, %s %s."
                        % (unit_text[idx], verb, frequency_text[idx]),
                    )
        contract.bw_compensation_clause = "USD 20 per hour, see annex."
        self.assertEqual(
            contract.bw_compensation_text(), "USD 20 per hour, see annex."
        )
        contract.write(
            {"bw_pay_amount": 0, "bw_pay_unit": False, "bw_pay_frequency": False}
        )
        self.assertEqual(
            contract.bw_compensation_text(), "USD 20 per hour, see annex."
        )

    def test_send_documents_compensation_either_form(self):
        def missing():
            with self.assertRaises(UserError) as error:
                self.contract.action_bw_send_documents()
            return str(error.exception)

        self.assertIn("compensation", missing())
        self.contract.bw_compensation_clause = "USD 20 per hour."
        self.assertNotIn("compensation", missing())
        self.contract.write(
            {
                "bw_compensation_clause": False,
                "bw_pay_amount": 1500,
                "bw_pay_unit": "month",
                "bw_pay_frequency": "biweekly",
            }
        )
        self.assertNotIn("compensation", missing())
        self.contract.bw_pay_amount = 0
        self.assertIn("compensation", missing())

    def test_gate_label_ignores_worker_name_and_tax_forms(self):
        def request(reference):
            return SimpleNamespace(
                reference=reference,
                template_id=SimpleNamespace(name=reference + ".pdf"),
            )

        self.employee.name = "Ica Contrato"
        label = self.contract._bw_gate_label
        self.assertEqual(label(request("W-9 - Ica Contrato")), "")
        self.assertNotIn("contrato", label(request("Carta - Ica Contrato")))
        self.assertIn(
            "agreement", label(request("Employment Agreement - Ica Contrato"))
        )
        self.assertIn("nda", label(request("NDA - Ica Contrato")))

    def test_gate_skip_context_needs_admin(self):
        user = self.env["res.users"].create(
            {
                "name": "HR Contract Manager",
                "login": "hr_manager@example.com",
                "groups_id": [
                    (4, self.env.ref("base.group_user").id),
                    (4, self.env.ref("hr_contract.group_hr_contract_manager").id),
                ],
            }
        )
        contract = self.contract.with_user(user).with_context(bw_skip_gate=True)
        with self.assertRaises(UserError):
            contract.write({"state": "open"})
