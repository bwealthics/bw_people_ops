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
