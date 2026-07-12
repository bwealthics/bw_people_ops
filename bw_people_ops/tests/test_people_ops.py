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
            Grant.create({"system": "Odoo", "profile": "empleado"})
        with self.assertRaises(ValidationError):
            Grant.create(
                {
                    "employee_id": self.employee.id,
                    "system": "Odoo",
                    "profile": "contractor",
                }
            )
        with self.assertRaises(ValidationError):
            Grant.create(
                {
                    "employee_id": self.employee.id,
                    "system": "Odoo",
                    "profile": "empleado",
                    "is_exception": True,
                }
            )
        grant = Grant.create(
            {
                "employee_id": self.employee.id,
                "system": "Odoo",
                "profile": "empleado",
            }
        )
        self.assertEqual(grant.name, "Test Worker @ Odoo")
