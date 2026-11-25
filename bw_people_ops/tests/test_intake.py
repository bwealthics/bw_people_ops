# Copyright 2026 BWEALTHICS LLC
# Part of bw_people_ops. License LGPL-3.
import hashlib
import importlib.util
import os
import re
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.modules.module import get_module_path
from odoo.tests import TransactionCase, tagged

ADDRESS = {
    "legal_name": "Ana Maria Perez",
    "private_street": "100 Main St",
    "private_city": "Katy",
    "private_state_code": "tx",
    "private_zip": "77494",
    "nationality_code": "ve",
    "birthday": "1990-05-01",
    "mobile_phone": "+1 (346) 555-0100",
    "emergency_contact": "Luis Perez",
    "emergency_phone": "+584241234567",
}


@tagged("post_install", "-at_install")
class TestIntake(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.email = "hr@example.com"
        cls.employee = cls.env["hr.employee"].create(
            {
                "name": "Ana Perez",
                "private_email": "ana@example.com",
                "private_country_id": cls.env.ref("base.us").id,
                "bw_doc_lang": "es",
            }
        )
        cls.Intake = cls.env["bw.people.intake"]

    def setUp(self):
        super().setUp()
        self.now = datetime(2026, 11, 25, 12, 0, 0)
        self.sent = []
        self.mail_fails = False

        def fake_send(mails, *args, **kwargs):
            # Captura lo que saldría y simula éxito o fallo del servidor.
            for mail in mails:
                self.sent.append({
                    "to": mail.email_to,
                    "subject": mail.subject,
                    "body": mail.body_html,
                    "model": mail.model,
                    "partners": mail.recipient_ids,
                    "auto_delete": mail.auto_delete,
                })
            mails.write({"state": "exception" if self.mail_fails else "sent"})

        for target, attr, value in (
            (self.Intake, "_intake_now", lambda rec: self.now),
            (self.env["mail.mail"], "send", fake_send),
        ):
            patcher = patch.object(type(target), attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    # helpers

    def tick(self, **delta):
        self.now += timedelta(**delta)

    def last_code(self):
        return re.search(r"<b>(\d{6})</b>", self.sent[-1]["body"]).group(1)

    def open_session(self, channel="email"):
        token = self.Intake.intake_open(self.employee.id, channel)["token"]
        self.assertTrue(self.Intake.w_otp_request(token)["ok"])
        verify = self.Intake.w_otp_verify(token, self.last_code())
        self.assertTrue(verify["ok"])
        return token, verify["sid"]

    def record(self):
        return self.Intake.search([("employee_id", "=", self.employee.id)])

    def leftover_mails(self):
        return self.env["mail.mail"].search(
            [("email_to", "ilike", "example.com")]
        )

    # tests

    def test_happy_path_es(self):
        opened = self.Intake.intake_open(self.employee.id, "email")
        token = opened["token"]
        self.assertEqual(opened["link_path"], "/i#t=%s" % token)
        self.assertEqual(opened["contact_hint"], "a***@example.com")
        self.assertEqual(len(self.sent), 1)
        self.assertIn("/i#t=%s" % token, self.sent[0]["body"])
        self.assertIn("completa tus datos", self.sent[0]["subject"])
        resolved = self.Intake.w_resolve(token)
        self.assertEqual(
            (resolved["first_name"], resolved["lang"], resolved["state"]),
            ("Ana", "es", "invited"),
        )
        sent = self.Intake.w_otp_request(token)
        self.assertEqual((sent["sent"], sent["resend_in"]), (True, 60))
        self.assertEqual(self.sent[-1]["subject"], "Tu código de verificación")
        sid = self.Intake.w_otp_verify(token, self.last_code())["sid"]
        form = self.Intake.w_form(sid)
        self.assertEqual(form["lang"], "es")
        self.assertEqual(form["state"], "in_progress")
        self.assertEqual(form["fields"][0]["label"], "Nombre legal completo")
        self.assertFalse(form["fields"][0]["readonly"])
        self.assertEqual(form["residence_country"]["code"], "US")
        self.assertIn("Declaro", form["consent_text"])
        self.assertTrue(self.Intake.w_save(sid, ADDRESS)["ok"])
        self.assertEqual(
            self.Intake.w_submit(sid, True), {"ok": True, "state": "submitted"}
        )
        employee = self.employee
        self.assertEqual(employee.legal_name, "Ana Maria Perez")
        self.assertEqual(
            (employee.private_state_id.code,
             employee.private_state_id.country_id.code),
            ("TX", "US"),
        )
        self.assertEqual(employee.country_id, self.env.ref("base.ve"))
        self.assertEqual(str(employee.birthday), "1990-05-01")
        # El móvil del trabajador es personal, no el móvil de trabajo.
        self.assertEqual(employee.private_phone, "+13465550100")
        self.assertFalse(employee.mobile_phone)
        self.assertEqual(employee.emergency_phone, "+584241234567")
        self.assertEqual(employee.private_country_id, self.env.ref("base.us"))
        intake = self.record().sudo()
        self.assertEqual(intake.state, "submitted")
        self.assertEqual(intake.consent_at, self.now)
        self.assertFalse(intake.form_data)
        # Correos: solo al correo personal, sin chatter, y ninguno queda.
        self.assertEqual({m["to"] for m in self.sent}, {"ana@example.com"})
        self.assertFalse(any(m["partners"] or m["model"] for m in self.sent))
        self.assertTrue(all(m["auto_delete"] for m in self.sent))
        self.assertFalse(self.leftover_mails())

    def test_happy_path_en_whatsapp(self):
        self.employee.bw_doc_lang = "en"
        token = self.Intake.intake_open(self.employee.id, "whatsapp")["token"]
        self.assertFalse(self.sent, "WhatsApp: the caller delivers")
        self.assertEqual(self.Intake.w_resolve(token)["lang"], "en")
        self.Intake.w_otp_request(token)
        self.assertEqual(self.sent[-1]["subject"], "Your verification code")
        self.assertIn("10 minutes", self.sent[-1]["body"])
        sid = self.Intake.w_otp_verify(token, self.last_code())["sid"]
        form = self.Intake.w_form(sid)
        self.assertEqual(form["fields"][0]["label"], "Full legal name")
        self.assertIn("true and complete", form["consent_text"])
        self.Intake.w_save(sid, ADDRESS)
        self.assertTrue(self.Intake.w_submit(sid, True)["ok"])

    def test_submit_is_idempotent_and_form_readonly(self):
        _token, sid = self.open_session()
        self.Intake.w_save(sid, ADDRESS)
        self.Intake.w_submit(sid, True)
        self.assertEqual(
            self.Intake.w_submit(sid, True), {"ok": True, "state": "submitted"}
        )
        form = self.Intake.w_form(sid)
        self.assertEqual(form["state"], "submitted")
        self.assertTrue(all(f["readonly"] for f in form["fields"]))
        values = {f["key"]: f["value"] for f in form["fields"]}
        self.assertEqual(values["mobile_phone"], "+13465550100")
        self.assertEqual(
            self.Intake.w_save(sid, {"private_city": "X"})["code"], "conflict"
        )

    def test_reopen_submitted_needs_force(self):
        _token, sid = self.open_session()
        self.Intake.w_save(sid, ADDRESS)
        self.Intake.w_submit(sid, True)
        self.assertEqual(
            self.Intake.intake_open(self.employee.id, "whatsapp")["code"],
            "conflict",
        )
        reopened = self.Intake.intake_open(
            self.employee.id, "whatsapp", force=True
        )
        self.assertTrue(reopened["ok"])
        intake = self.record()
        self.assertEqual(intake.state, "invited")
        self.assertFalse(intake.submitted_at or intake.consent_at)
        self.assertEqual(self.Intake.w_form(sid)["code"], "session_expired")

    def test_invalid_tokens_indistinguishable(self):
        invalid = {"ok": False, "code": "invalid"}
        old = self.Intake.intake_open(self.employee.id, "whatsapp")["token"]
        new = self.Intake.intake_open(self.employee.id, "whatsapp")["token"]
        results = [
            self.Intake.w_resolve("x" * 43),
            self.Intake.w_resolve(None),
            self.Intake.w_resolve(old),
            self.Intake.w_otp_request(old),
            self.Intake.w_otp_verify(old, "000000"),
        ]
        self.tick(days=7)
        results.append(self.Intake.w_resolve(new))
        self.tick(days=-7)
        self.Intake.intake_cancel(self.employee.id)
        results.append(self.Intake.w_resolve(new))
        for result in results:
            self.assertEqual(result, invalid)

    def test_otp_wrong_locks(self):
        token = self.Intake.intake_open(self.employee.id, "email")["token"]
        self.Intake.w_otp_request(token)
        code = self.last_code()
        wrong = "%06d" % ((int(code) + 1) % 10**6)
        for _i in range(4):
            self.assertEqual(
                self.Intake.w_otp_verify(token, wrong)["code"], "otp_wrong"
            )
        locked = self.Intake.w_otp_verify(token, wrong)
        self.assertEqual(
            (locked["code"], locked["retry_in"]), ("otp_locked", 1800)
        )
        self.assertEqual(
            self.Intake.w_otp_verify(token, code)["code"], "otp_locked"
        )
        self.assertEqual(
            self.Intake.w_otp_request(token)["code"], "otp_locked"
        )
        self.tick(minutes=30)
        self.assertTrue(self.Intake.w_otp_request(token)["ok"])
        self.assertTrue(
            self.Intake.w_otp_verify(token, self.last_code())["ok"]
        )

    def test_new_code_does_not_reset_attempts(self):
        token = self.Intake.intake_open(self.employee.id, "whatsapp")["token"]
        self.Intake.w_otp_request(token)
        for _i in range(4):
            self.Intake.w_otp_verify(token, "bad")
        self.tick(seconds=61)
        self.Intake.w_otp_request(token)
        self.assertEqual(
            self.Intake.w_otp_verify(token, "bad")["code"], "otp_locked"
        )

    def test_twenty_failures_cancel_invitation(self):
        token = self.Intake.intake_open(self.employee.id, "whatsapp")["token"]
        for failure in range(1, 20):
            result = self.Intake.w_otp_verify(token, "bad")
            expected = "otp_locked" if failure % 5 == 0 else "otp_wrong"
            self.assertEqual(result["code"], expected)
            if expected == "otp_locked":
                self.tick(minutes=30)
        self.assertEqual(
            self.Intake.w_otp_verify(token, "bad")["code"], "invalid"
        )
        self.assertEqual(self.record().state, "cancelled")
        self.assertEqual(self.Intake.w_resolve(token)["code"], "invalid")

    def test_otp_cooldown_and_hourly_limit(self):
        token = self.Intake.intake_open(self.employee.id, "whatsapp")["token"]
        self.assertTrue(self.Intake.w_otp_request(token)["ok"])
        limited = self.Intake.w_otp_request(token)
        self.assertEqual(
            (limited["code"], limited["retry_in"]), ("rate_limited", 60)
        )
        self.tick(seconds=61)
        self.assertTrue(self.Intake.w_otp_request(token)["ok"])
        self.tick(seconds=61)
        self.assertTrue(self.Intake.w_otp_request(token)["ok"])
        self.tick(seconds=61)
        limited = self.Intake.w_otp_request(token)
        self.assertEqual(limited["code"], "rate_limited")
        self.assertEqual(limited["retry_in"], 3600 - 3 * 61)
        self.tick(hours=1)
        self.assertTrue(self.Intake.w_otp_request(token)["ok"])

    def test_mail_failure(self):
        self.mail_fails = True
        result = self.Intake.intake_open(self.employee.id, "email")
        self.assertEqual(result, {"ok": False, "code": "mail_failed"})
        self.assertFalse(self.record(), "No usable token was stored")
        self.assertFalse(self.leftover_mails())
        token = self.Intake.intake_open(self.employee.id, "whatsapp")["token"]
        self.assertEqual(
            self.Intake.w_otp_request(token)["code"], "mail_failed"
        )
        self.assertFalse(self.leftover_mails())
        # El fallo no consumió el enfriamiento ni la cuota horaria.
        self.mail_fails = False
        for _i in range(3):
            self.assertTrue(self.Intake.w_otp_request(token)["ok"])
            self.tick(seconds=61)

    def test_otp_single_use_and_expiry(self):
        token, _sid = self.open_session()
        reused = self.Intake.w_otp_verify(token, self.last_code())
        self.assertEqual(reused["code"], "otp_wrong")
        self.tick(seconds=61)
        self.Intake.w_otp_request(token)
        self.tick(minutes=10)
        expired = self.Intake.w_otp_verify(token, self.last_code())
        self.assertEqual(expired["code"], "otp_wrong")

    def test_session_idle_and_absolute(self):
        _token, sid = self.open_session()
        self.tick(minutes=29)
        self.assertTrue(self.Intake.w_form(sid)["ok"])
        self.tick(minutes=30)
        self.assertEqual(self.Intake.w_form(sid)["code"], "session_expired")
        token, sid = self.open_session()
        for _i in range(11):
            self.tick(minutes=20)
            self.assertTrue(self.Intake.w_form(sid)["ok"])
        self.tick(minutes=20)  # 4 h desde la verificación
        self.assertEqual(self.Intake.w_form(sid)["code"], "session_expired")
        self.assertEqual(
            self.Intake.w_save(sid, {})["code"], "session_expired"
        )
        self.assertTrue(self.Intake.w_resolve(token)["ok"])

    def test_partial_save_then_resume(self):
        self.employee.private_phone = "+13465550199"
        token, sid = self.open_session()
        saved = self.Intake.w_save(
            sid, {"private_city": "  Katy ", "emergency_phone": "12345"}
        )
        self.assertEqual(saved["saved"], ["private_city"])
        self.assertIn("emergency_phone", saved["errors"])
        self.tick(hours=1)
        self.Intake.w_otp_request(token)
        sid = self.Intake.w_otp_verify(token, self.last_code())["sid"]
        form = self.Intake.w_form(sid)
        values = {f["key"]: f["value"] for f in form["fields"]}
        self.assertEqual(values["private_city"], "Katy")
        self.assertEqual(values["legal_name"], "Ana Perez")
        self.assertEqual(values["mobile_phone"], "+13465550199")
        self.assertIsNone(values["emergency_phone"])
        self.assertFalse(
            self.employee.private_city, "Not written before submit"
        )

    def test_submit_validation(self):
        _token, sid = self.open_session()
        result = self.Intake.w_submit(sid, False)
        self.assertEqual(result["code"], "validation")
        for key in ("consent", "private_street", "private_city",
                    "nationality_code", "birthday", "mobile_phone"):
            self.assertIn(key, result["errors"])
        self.assertNotIn("legal_name", result["errors"])
        self.Intake.w_save(sid, dict(ADDRESS, birthday="2015-01-01"))
        result = self.Intake.w_submit(sid, True)
        self.assertEqual(list(result["errors"]), ["birthday"])
        bad = self.Intake.w_save(
            sid,
            {"birthday": "1900-01-01", "nationality_code": "XX",
             "private_state_code": "ZZ"},
        )
        self.assertEqual(
            sorted(bad["errors"]),
            ["birthday", "nationality_code", "private_state_code"],
        )
        self.assertEqual(self.record().state, "in_progress")

    def test_state_code_without_residence_country(self):
        self.employee.private_country_id = False
        _token, sid = self.open_session()
        result = self.Intake.w_save(sid, {"private_state_code": "TX"})
        self.assertEqual(list(result["errors"]), ["private_state_code"])
        self.assertEqual(result["saved"], [])

    def test_unknown_keys_rejected(self):
        _token, sid = self.open_session()
        result = self.Intake.w_save(
            sid, {"private_city": "Katy", "ssnid": "123-45-6789"}
        )
        self.assertEqual(result["code"], "validation")
        self.assertEqual(list(result["errors"]), ["ssnid"])
        self.assertFalse(self.record().sudo().form_data)
        self.assertEqual(
            self.Intake.w_save(sid, ["private_city"])["code"], "validation"
        )

    def test_argument_types(self):
        for bad in ("5", True, None, 5.0):
            self.assertEqual(
                self.Intake.intake_open(bad, "email")["code"], "validation"
            )
            self.assertEqual(
                self.Intake.intake_cancel(bad)["code"], "validation"
            )
        for bad in (5, ["a"], [True], None, (1,)):
            self.assertEqual(
                self.Intake.intake_state(bad)["code"], "validation"
            )

    def test_cancel_revokes(self):
        token, sid = self.open_session()
        self.Intake.w_save(sid, {"private_city": "Katy"})
        self.record().action_cancel()
        intake = self.record().sudo()
        self.assertEqual(intake.state, "cancelled")
        self.assertFalse(intake.form_data)
        self.assertEqual(self.Intake.w_resolve(token)["code"], "invalid")
        self.assertEqual(self.Intake.w_form(sid)["code"], "session_expired")
        state = self.Intake.intake_state([self.employee.id, 0])["intakes"]
        self.assertEqual(state[str(self.employee.id)]["state"], "cancelled")
        self.assertEqual(state["0"]["state"], "none")

    def test_nothing_plaintext_persisted(self):
        token, sid = self.open_session()
        code = self.last_code()
        self.Intake.w_save(sid, {"private_city": "Katy"})
        intake = self.record().sudo()
        self.assertEqual(
            intake.token_hash, hashlib.sha256(token.encode()).hexdigest()
        )
        stored = str(intake.read()[0]) + str(
            self.employee.sudo().read(["private_city", "notes"])[0]
        )
        params = str(self.env["ir.config_parameter"].sudo().search([]).read())
        for secret in (token, sid):
            self.assertNotIn(secret, stored)
            self.assertNotIn(secret, params)
        self.assertNotIn(code, str(intake.read()[0]))
        self.assertFalse(self.leftover_mails())
        messages = self.env["mail.message"].search(
            ["|", ("body", "ilike", code), ("subject", "ilike", "código")]
        )
        self.assertFalse(messages)

    def test_cron_expires_and_wipes(self):
        token, sid = self.open_session()
        self.Intake.w_save(sid, {"private_city": "Katy"})
        self.tick(days=7)
        self.Intake._cron_expire()
        intake = self.record().sudo()
        self.assertEqual(intake.state, "expired")
        self.assertFalse(intake.form_data or intake.token_hash)
        self.assertEqual(self.Intake.w_resolve(token)["code"], "invalid")

    def test_pepper_migration(self):
        params = self.env["ir.config_parameter"].sudo()
        params.search([("key", "=", "bw_people_ops.intake_pepper")]).unlink()
        path = os.path.join(
            get_module_path("bw_people_ops"),
            "migrations", "18.0.2.1.0", "post-migration.py",
        )
        spec = importlib.util.spec_from_file_location("bw_mig_2_1_0", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration.migrate(self.env.cr, "18.0.2.0.3")
        pepper = params.get_param("bw_people_ops.intake_pepper")
        self.assertEqual(len(pepper), 64)
        migration.migrate(self.env.cr, "18.0.2.0.3")
        self.assertEqual(
            params.get_param("bw_people_ops.intake_pepper"), pepper
        )

    def test_open_requires_access_and_private_email(self):
        user = self.env["res.users"].create(
            {
                "name": "Internal",
                "login": "internal@example.com",
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
            }
        )
        with self.assertRaises(AccessError):
            self.Intake.with_user(user).w_resolve("x" * 43)
        self.employee.private_email = False
        result = self.Intake.intake_open(self.employee.id, "email")
        self.assertEqual(result["code"], "validation")
        self.assertEqual(
            self.Intake.intake_open(self.employee.id, "sms")["code"],
            "validation",
        )
