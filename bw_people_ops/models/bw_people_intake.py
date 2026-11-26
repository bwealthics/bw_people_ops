# Copyright 2026 BWEALTHICS LLC
# Part of bw_people_ops. License LGPL-3.
"""Formulario de autoservicio del trabajador (CONTRACT.md, Versión 2).

El token, el código y la sesión existen en claro una sola vez: en la
respuesta del método (o en el correo que los entrega, que se borra tras
enviarse). Aquí solo se guarda su hash. Los métodos públicos no lanzan
excepciones con datos: devuelven {"ok": True, ...} o
{"ok": False, "code": ...}. Solo un llamador sin permiso de escritura sobre
el modelo recibe AccessError.
"""

import hashlib
import hmac
import html
import logging
import math
import re
import secrets
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

TOKEN_TTL = timedelta(days=7)
OTP_TTL = timedelta(minutes=10)
OTP_MAX_ATTEMPTS = 5
OTP_MAX_TOTAL_FAILURES = 20
OTP_LOCKOUT = timedelta(minutes=30)
OTP_COOLDOWN = timedelta(seconds=60)
OTP_MAX_PER_HOUR = 3
SESSION_IDLE = timedelta(minutes=30)
SESSION_ABSOLUTE = timedelta(hours=4)
OPEN_STATES = ("invited", "in_progress")
SESSION_STATES = OPEN_STATES + ("submitted",)
PEPPER_PARAM = "bw_people_ops.intake_pepper"
BASE_URL_PARAM = "bw_people_ops.portal_base_url"
DEFAULT_BASE_URL = "https://bw-provision-portal.vercel.app"
ODOO_LANG = {"es": "es_ES", "en": "en_US"}
E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")
STATE_CODE_RE = re.compile(r"^[A-Z]{2,3}$")

# (clave, tipo, obligatorio, etiqueta es, etiqueta en). Lista cerrada: el
# trabajador nunca escribe SSN, TIN ni datos bancarios; otra clave se rechaza.
FIELDS = [
    ("legal_name", "text", True, "Nombre legal completo", "Full legal name"),
    ("private_street", "text", True, "Dirección", "Street address"),
    ("private_street2", "text", False, "Dirección (línea 2)",
     "Address line 2"),
    ("private_city", "text", True, "Ciudad", "City"),
    ("private_state_code", "state", False, "Estado (código)",
     "State (code)"),
    ("private_zip", "text", False, "Código postal", "ZIP / postal code"),
    ("nationality_code", "select", True, "Nacionalidad", "Nationality"),
    ("birthday", "date", True, "Fecha de nacimiento", "Date of birth"),
    # La clave sigue siendo `mobile_phone` (contrato del portal), pero se
    # guarda en `private_phone`: en Odoo 18 `hr.employee.mobile_phone` es el
    # móvil de trabajo (su inverso escribe en work_contact_id).
    ("mobile_phone", "phone", True, "Teléfono móvil", "Mobile phone"),
    ("emergency_contact", "text", False, "Contacto de emergencia",
     "Emergency contact"),
    ("emergency_phone", "phone", False, "Teléfono de emergencia",
     "Emergency phone"),
]
FIELD_KEYS = [f[0] for f in FIELDS]
HELP = {
    "private_state_code": ("2 o 3 letras, por ejemplo TX",
                           "2 or 3 letters, e.g. TX"),
    "birthday": ("AAAA-MM-DD", "YYYY-MM-DD"),
    "mobile_phone": ("Con código de país, por ejemplo +13465550100",
                     "With country code, e.g. +13465550100"),
    "emergency_phone": ("Con código de país", "With country code"),
}
MESSAGES = {
    "required": ("Obligatorio", "Required"),
    "unknown": ("Campo no permitido", "Field not allowed"),
    "text": ("Texto inválido", "Invalid text"),
    "phone": ("Teléfono inválido, use +código de país y número",
              "Invalid phone, use +country code and number"),
    "date": ("Fecha inválida", "Invalid date"),
    "age": ("La edad debe estar entre 16 y 100 años",
            "Age must be between 16 and 100"),
    "country": ("País inválido", "Invalid country"),
    "state": ("Estado inválido para el país de residencia",
              "Invalid state for the country of residence"),
    "consent": ("Debe aceptar para continuar",
                "You must accept to continue"),
}
CONSENT = (
    "Declaro que la información que entrego es verdadera y completa. "
    "Autorizo a %s a usar estos datos personales solo para gestionar mi "
    "relación de trabajo y cumplir sus obligaciones legales y fiscales.",
    "I declare that the information I provide is true and complete. I "
    "authorize %s to use this personal data only to manage my work "
    "relationship and to meet its legal and tax obligations.",
)
OTP_MAIL = (
    ("Tu código de verificación",
     "<p>Tu código es <b>%s</b>.</p>"
     "<p>Vence en 10 minutos. No lo compartas con nadie.</p>"),
    ("Your verification code",
     "<p>Your code is <b>%s</b>.</p>"
     "<p>It expires in 10 minutes. Do not share it with anyone.</p>"),
)
INVITE_MAIL = (
    ("%(company)s: completa tus datos",
     "<p>Hola %(name)s:</p><p>%(company)s te pide completar tus datos "
     "personales en este enlace:</p><p><a href=\"%(link)s\">%(link)s</a></p>"
     "<p>Vence en 7 días. Es personal: no lo compartas.</p>"),
    ("%(company)s: complete your details",
     "<p>Hi %(name)s,</p><p>%(company)s asks you to complete your personal "
     "details at this link:</p><p><a href=\"%(link)s\">%(link)s</a></p>"
     "<p>It expires in 7 days. It is personal: do not share it.</p>"),
)


def _sha256(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _iso(value):
    return "%sZ" % value.isoformat() if value else None


def _fail(code, **extra):
    return dict(extra, ok=False, code=code)


def _retry_in(until, now):
    return max(math.ceil((until - now).total_seconds()), 1)


def _mask_email(email):
    local, _sep, domain = (email or "").partition("@")
    return "%s***@%s" % (local[:1], domain) if domain else ""


def _is_id(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _lang_idx(employee):
    return 1 if employee.bw_doc_lang == "en" else 0


class BwPeopleIntake(models.Model):
    _name = "bw.people.intake"
    _description = "Formulario del trabajador"
    _order = "id desc"
    _rec_name = "employee_id"

    employee_id = fields.Many2one(
        "hr.employee", required=True, ondelete="cascade", index=True,
        readonly=True,
    )
    company_id = fields.Many2one(related="employee_id.company_id")
    state = fields.Selection(
        [
            ("invited", "Invitado"),
            ("in_progress", "En curso"),
            ("submitted", "Enviado"),
            ("cancelled", "Cancelado"),
            ("expired", "Vencido"),
        ],
        default="invited", required=True, readonly=True,
    )
    channel = fields.Selection(
        [("email", "Correo"), ("whatsapp", "WhatsApp")], readonly=True
    )
    sent_at = fields.Datetime(string="Enviado el", readonly=True)
    token_expires_at = fields.Datetime(string="Vence el", readonly=True)
    submitted_at = fields.Datetime(string="Completado el", readonly=True)
    consent_at = fields.Datetime(string="Consentimiento", readonly=True)
    # Secretos (solo hashes) y estado interno: fuera de toda vista y de la
    # lectura por RPC de RR. HH.; el código los usa con sudo.
    token_hash = fields.Char(index=True, groups="base.group_system")
    token_prefix = fields.Char(groups="base.group_system")
    otp_hash = fields.Char(groups="base.group_system")
    otp_expires_at = fields.Datetime(groups="base.group_system")
    otp_attempts = fields.Integer(groups="base.group_system")
    otp_total_failures = fields.Integer(
        groups="base.group_system",
        help="Fallos acumulados de esta invitación; a los 20 se cancela.",
    )
    otp_locked_until = fields.Datetime(groups="base.group_system")
    otp_last_sent_at = fields.Datetime(groups="base.group_system")
    otp_window_start = fields.Datetime(groups="base.group_system")
    otp_send_count = fields.Integer(groups="base.group_system")
    sid_hash = fields.Char(index=True, groups="base.group_system")
    sid_last_seen = fields.Datetime(groups="base.group_system")
    sid_expires_at = fields.Datetime(groups="base.group_system")
    form_data = fields.Json(
        groups="base.group_system",
        help="Respuestas parciales antes de enviar; se borran al enviar.",
    )

    _sql_constraints = [
        ("employee_uniq", "UNIQUE(employee_id)",
         "Un solo formulario por empleado."),
    ]

    # ------------------------------------------------------------ helpers

    def _intake_now(self):
        # Un solo punto de reloj: los tests lo parchean.
        return fields.Datetime.now()

    @api.model
    def _intake_guard(self):
        """Solo quien puede escribir el modelo (RR. HH. o el usuario de n8n)
        llama a estos métodos; después todo corre con sudo."""
        self.check_access("write")
        return self.sudo()

    @api.model
    def _installed_lang(self, code):
        """Odoo 18 raises "Invalid language code" when the context carries a
        language that is not installed: fall back to English."""
        installed = self.env["res.lang"].sudo().search_count(
            [("code", "=", code)], limit=1
        )
        return code if installed else "en_US"

    @api.model
    def _visible_employee_ids(self, employee_ids):
        """Ids que el usuario que llama puede ver con SUS reglas de acceso
        (multiempresa incluida), antes de pasar a sudo."""
        Employee = self.env["hr.employee"].with_context(active_test=False)
        return set(Employee.search([("id", "in", list(employee_ids))]).ids)

    @api.model
    def _pepper(self):
        # La migración 18.0.2.1.0 la crea; esto cubre la instalación nueva.
        params = self.env["ir.config_parameter"].sudo()
        pepper = params.get_param(PEPPER_PARAM)
        if not pepper:
            pepper = secrets.token_hex(32)
            params.set_param(PEPPER_PARAM, pepper)
        return pepper

    def _otp_hmac(self, code):
        message = "%s:%s" % (self.id, code)
        return hmac.new(
            self._pepper().encode(), message.encode(), hashlib.sha256
        ).hexdigest()

    def _secret_reset(self):
        return {
            "otp_hash": False, "otp_expires_at": False, "otp_attempts": 0,
            "sid_hash": False, "sid_last_seen": False,
            "sid_expires_at": False,
        }

    def _revoke(self, state):
        self.write(dict(
            self._secret_reset(), state=state, token_hash=False,
            token_prefix=False, form_data=False,
        ))

    def _intake_dict(self, now):
        state = self.state
        if state in OPEN_STATES and self.token_expires_at <= now:
            state = "expired"
        return {
            "state": state,
            "channel": self.channel or None,
            "sent_at": _iso(self.sent_at),
            "expires_at": _iso(self.token_expires_at),
            "submitted_at": _iso(self.submitted_at),
        }

    @api.model
    def _by_token(self, token, now):
        """Desconocido, vencido, cancelado o rotado: todos vacío."""
        if not isinstance(token, str) or not 20 <= len(token) <= 100:
            return self.browse()
        digest = _sha256(token)
        rec = self.search([("token_hash", "=", digest)], limit=1)
        if (
            not rec
            or not hmac.compare_digest(rec.token_hash, digest)
            or rec.state not in OPEN_STATES
            or rec.token_expires_at <= now
        ):
            return self.browse()
        return rec

    @api.model
    def _by_sid(self, sid, now):
        """La sesión sigue viva tras enviar, para mostrar la confirmación."""
        if not isinstance(sid, str) or not 20 <= len(sid) <= 100:
            return self.browse()
        digest = _sha256(sid)
        rec = self.search([("sid_hash", "=", digest)], limit=1)
        if (
            not rec
            or not hmac.compare_digest(rec.sid_hash, digest)
            or rec.state not in SESSION_STATES
            or (rec.state in OPEN_STATES and rec.token_expires_at <= now)
            or rec.sid_expires_at <= now
            or rec.sid_last_seen + SESSION_IDLE <= now
        ):
            return self.browse()
        rec.sid_last_seen = now
        return rec

    @api.model
    def _send_mail(self, employee, subject, body):
        """Envía ya, dentro de la transacción, a `private_email` y nada más:
        sin modelo ni res_id (no va al chatter). Después borra el correo sea
        cual sea su estado, para que ni el enlace ni el código queden en
        mail.mail o mail.message. Devuelve True si salió.

        ponytail: si la transacción se reintenta, el reintento manda otro
        correo con otro secreto; el primero ya no sirve."""
        vals = {
            "subject": subject,
            "body_html": body,
            "email_to": employee.private_email,
            "auto_delete": True,
        }
        if employee.company_id.email_formatted:
            vals["email_from"] = employee.company_id.email_formatted
        mail = self.env["mail.mail"].sudo().create(vals)
        try:
            mail.send(raise_exception=False)
        except Exception as exc:  # noqa: BLE001 - se informa con mail_failed
            # Sin el detalle: podría citar el cuerpo.
            _logger.warning(
                "Intake mail to employee %s failed: %s",
                employee.id, type(exc).__name__,
            )
        mail = mail.exists()
        ok = not mail or mail.state == "sent"
        mail.unlink()
        return ok

    # ------------------------------------------------------ admin (n8n)

    @api.model
    def intake_open(self, employee_id, channel, force=False):
        Intake = self._intake_guard()
        now = Intake._intake_now()
        if not _is_id(employee_id):
            return _fail("validation", errors={"employee_id": "int"})
        if employee_id not in self._visible_employee_ids([employee_id]):
            return _fail("invalid")
        employee = self.env["hr.employee"].sudo().browse(employee_id)
        if channel not in ("email", "whatsapp"):
            return _fail("validation", errors={"channel": "email|whatsapp"})
        if not employee.private_email:
            return _fail("validation", errors={"private_email": "required"})
        rec = Intake.search([("employee_id", "=", employee.id)], limit=1)
        if rec.state == "submitted" and force is not True:
            return _fail("conflict")
        token = secrets.token_urlsafe(32)
        link_path = "/i#t=%s" % token
        # El correo sale antes de guardar el token: si falla, no queda un
        # token nuevo utilizable ni se toca la invitación anterior.
        if channel == "email":
            base = self.env["ir.config_parameter"].sudo().get_param(
                BASE_URL_PARAM, DEFAULT_BASE_URL
            ).rstrip("/")
            subject, body = INVITE_MAIL[_lang_idx(employee)]
            values = {
                "company": html.escape(employee.company_id.name or ""),
                "name": html.escape((employee.name or "").split(" ")[0]),
                "link": html.escape(base + link_path),
            }
            if not Intake._send_mail(
                employee, subject % values, body % values
            ):
                return _fail("mail_failed")
        vals = dict(
            Intake._secret_reset(),
            state="invited",
            channel=channel,
            token_hash=_sha256(token),
            token_prefix=token[:6],
            token_expires_at=now + TOKEN_TTL,
            sent_at=now,
            submitted_at=False,
            consent_at=False,
            otp_locked_until=False,
            otp_total_failures=0,
        )
        if rec:
            rec.write(vals)
        else:
            rec = Intake.create(dict(vals, employee_id=employee.id))
        return {
            "ok": True,
            "token": token,
            "expires_at": _iso(rec.token_expires_at),
            "link_path": link_path,
            "contact_hint": _mask_email(employee.private_email),
        }

    @api.model
    def intake_cancel(self, employee_id):
        Intake = self._intake_guard()
        now = Intake._intake_now()
        if not _is_id(employee_id):
            return _fail("validation", errors={"employee_id": "int"})
        if employee_id not in self._visible_employee_ids([employee_id]):
            return _fail("invalid")
        rec = Intake.search([("employee_id", "=", employee_id)], limit=1)
        if not rec:
            return _fail("invalid")
        if rec.state == "submitted":
            return _fail("conflict")
        rec._revoke("cancelled")
        return {"ok": True, "intake": rec._intake_dict(now)}

    @api.model
    def intake_state(self, employee_ids):
        Intake = self._intake_guard()
        now = Intake._intake_now()
        if not isinstance(employee_ids, list) or not all(
            _is_id(i) for i in employee_ids
        ):
            return _fail("validation", errors={"employee_ids": "list[int]"})
        visible = self._visible_employee_ids(employee_ids)
        recs = Intake.search([("employee_id", "in", list(visible))])
        by_employee = {r.employee_id.id: r._intake_dict(now) for r in recs}
        none = {"state": "none", "channel": None, "sent_at": None,
                "expires_at": None, "submitted_at": None}
        return {
            "ok": True,
            "intakes": {
                str(i): by_employee.get(i, none) for i in employee_ids
            },
        }

    def action_cancel(self):
        for rec in self:
            rec.intake_cancel(rec.employee_id.id)

    # ----------------------------------------------- worker (via portal)

    @api.model
    def w_resolve(self, token):
        Intake = self._intake_guard()
        rec = Intake._by_token(token, Intake._intake_now())
        if not rec:
            return _fail("invalid")
        employee = rec.employee_id
        return {
            "ok": True,
            "state": rec.state,
            "first_name": (employee.name or "").split(" ")[0],
            "contact_hint": _mask_email(employee.private_email),
            "lang": employee.bw_doc_lang or "es",
            "company": employee.company_id.name,
        }

    @api.model
    def w_otp_request(self, token):
        Intake = self._intake_guard()
        now = Intake._intake_now()
        rec = Intake._by_token(token, now)
        if not rec:
            return _fail("invalid")
        if rec.otp_locked_until and rec.otp_locked_until > now:
            return _fail(
                "otp_locked", retry_in=_retry_in(rec.otp_locked_until, now)
            )
        if rec.otp_last_sent_at and rec.otp_last_sent_at + OTP_COOLDOWN > now:
            return _fail(
                "rate_limited",
                retry_in=_retry_in(rec.otp_last_sent_at + OTP_COOLDOWN, now),
            )
        window_open = (
            rec.otp_window_start
            and rec.otp_window_start + timedelta(hours=1) > now
        )
        sent = rec.otp_send_count if window_open else 0
        if sent >= OTP_MAX_PER_HOUR:
            return _fail(
                "rate_limited",
                retry_in=_retry_in(
                    rec.otp_window_start + timedelta(hours=1), now
                ),
            )
        code = "%06d" % secrets.randbelow(10**6)
        subject, body = OTP_MAIL[_lang_idx(rec.employee_id)]
        # Un correo fallido no consume el enfriamiento ni la cuota.
        if not Intake._send_mail(rec.employee_id, subject, body % code):
            return _fail("mail_failed")
        # otp_attempts no se reinicia aquí: pedir otro código no debe
        # saltarse el bloqueo de 5 intentos.
        rec.write({
            "otp_hash": rec._otp_hmac(code),
            "otp_expires_at": now + OTP_TTL,
            "otp_last_sent_at": now,
            "otp_window_start": rec.otp_window_start if window_open else now,
            "otp_send_count": sent + 1,
        })
        return {
            "ok": True, "sent": True,
            "resend_in": int(OTP_COOLDOWN.total_seconds()),
        }

    @api.model
    def w_otp_verify(self, token, code):
        Intake = self._intake_guard()
        now = Intake._intake_now()
        rec = Intake._by_token(token, now)
        if not rec:
            return _fail("invalid")
        if rec.otp_locked_until and rec.otp_locked_until > now:
            return _fail(
                "otp_locked", retry_in=_retry_in(rec.otp_locked_until, now)
            )
        valid = (
            rec.otp_hash
            and rec.otp_expires_at > now
            and hmac.compare_digest(
                rec._otp_hmac(str(code or "")), rec.otp_hash
            )
        )
        if not valid:
            total = rec.otp_total_failures + 1
            if total >= OTP_MAX_TOTAL_FAILURES:
                # Demasiados intentos en una misma invitación: se anula;
                # RR. HH. tiene que abrir otra.
                rec._revoke("cancelled")
                return _fail("invalid")
            attempts = rec.otp_attempts + 1
            if attempts >= OTP_MAX_ATTEMPTS:
                rec.write({
                    "otp_attempts": 0, "otp_hash": False,
                    "otp_total_failures": total,
                    "otp_locked_until": now + OTP_LOCKOUT,
                })
                return _fail(
                    "otp_locked",
                    retry_in=int(OTP_LOCKOUT.total_seconds()),
                )
            rec.write({"otp_attempts": attempts, "otp_total_failures": total})
            return _fail("otp_wrong")
        sid = secrets.token_urlsafe(32)
        rec.write({
            # Un código vale una sola vez; una sesión nueva reemplaza la
            # anterior.
            "otp_hash": False,
            "otp_attempts": 0,
            "sid_hash": _sha256(sid),
            "sid_last_seen": now,
            "sid_expires_at": now + SESSION_ABSOLUTE,
            "state": "in_progress",
        })
        return {
            "ok": True,
            "sid": sid,
            "expires_at": _iso(min(now + SESSION_IDLE, rec.sid_expires_at)),
        }

    # Formulario

    def _country(self):
        return self.employee_id.private_country_id

    def _employee_values(self):
        employee = self.employee_id
        return {
            "legal_name": employee.legal_name or employee.name,
            "private_street": employee.private_street,
            "private_street2": employee.private_street2,
            "private_city": employee.private_city,
            "private_state_code": employee.private_state_id.code,
            "private_zip": employee.private_zip,
            "nationality_code": employee.country_id.code,
            "birthday": (
                employee.birthday.isoformat() if employee.birthday else False
            ),
            "mobile_phone": employee.private_phone,
            "emergency_contact": employee.emergency_contact,
            "emergency_phone": employee.emergency_phone,
        }

    def _values(self):
        values = self._employee_values()
        values.update(self.form_data or {})
        return values

    def _state(self, code):
        # Sin país de residencia el dominio no encuentra nada: error.
        return self.env["res.country.state"].search(
            [("country_id", "=", self._country().id), ("code", "=", code)],
            limit=1,
        )

    def _clean(self, answers, now):
        """(limpios, errores). Un valor vacío es False; lo obligatorio se
        exige solo al enviar."""
        idx = _lang_idx(self.employee_id)
        types = {f[0]: f[1] for f in FIELDS}
        clean, errors = {}, {}
        for key, raw in answers.items():
            ftype = types.get(key)
            if not ftype:
                errors[key] = MESSAGES["unknown"][idx]
                continue
            if raw is None or raw is False or raw == "":
                clean[key] = False
                continue
            if not isinstance(raw, (str, int)) or isinstance(raw, bool):
                errors[key] = MESSAGES["text"][idx]
                continue
            value = " ".join(str(raw).split())
            if not value or len(value) > 200:
                errors[key] = MESSAGES["text"][idx]
            elif ftype == "phone":
                value = re.sub(r"[\s().\-]", "", value)
                if E164_RE.match(value):
                    clean[key] = value
                else:
                    errors[key] = MESSAGES["phone"][idx]
            elif ftype == "date":
                try:
                    birthday = fields.Date.to_date(value)
                except ValueError:
                    birthday = None
                today = now.date()
                if not birthday:
                    errors[key] = MESSAGES["date"][idx]
                elif not (
                    today - relativedelta(years=100)
                    <= birthday
                    <= today - relativedelta(years=16)
                ):
                    errors[key] = MESSAGES["age"][idx]
                else:
                    clean[key] = birthday.isoformat()
            elif ftype == "select":
                value = value.upper()
                if self.env["res.country"].search_count(
                    [("code", "=", value)], limit=1
                ):
                    clean[key] = value
                else:
                    errors[key] = MESSAGES["country"][idx]
            elif ftype == "state":
                value = value.upper()
                if STATE_CODE_RE.match(value) and self._state(value):
                    clean[key] = value
                else:
                    errors[key] = MESSAGES["state"][idx]
            else:
                clean[key] = value
        return clean, errors

    @api.model
    def w_form(self, sid):
        Intake = self._intake_guard()
        now = Intake._intake_now()
        rec = Intake._by_sid(sid, now)
        if not rec:
            return _fail("session_expired")
        idx = _lang_idx(rec.employee_id)
        lang = ("es", "en")[idx]
        readonly = rec.state == "submitted"
        # Si es_ES no está instalado, Odoo devuelve los nombres de país en
        # inglés (el valor fuente); el código ISO no cambia.
        odoo_lang = Intake._installed_lang(ODOO_LANG[lang])
        countries = self.env["res.country"].with_context(
            lang=odoo_lang
        ).search([])
        values = rec._values()
        result = []
        for key, ftype, required, label_es, label_en in FIELDS:
            item = {
                "key": key,
                "label": (label_es, label_en)[idx],
                "help": HELP[key][idx] if key in HELP else "",
                "type": ftype,
                "required": required,
                "readonly": readonly,
                "value": values.get(key) or None,
            }
            if ftype == "select":
                item["options"] = [
                    {"value": c.code, "label": c.name} for c in countries
                ]
            result.append(item)
        country = rec._country().with_context(lang=odoo_lang)
        return {
            "ok": True,
            "lang": lang,
            "state": rec.state,
            "fields": result,
            "residence_country": {
                "code": country.code or None, "name": country.name or None,
            },
            "consent_text": CONSENT[idx] % rec.employee_id.company_id.name,
        }

    @api.model
    def w_save(self, sid, answers):
        Intake = self._intake_guard()
        now = Intake._intake_now()
        rec = Intake._by_sid(sid, now)
        if not rec:
            return _fail("session_expired")
        if rec.state == "submitted":
            return _fail("conflict")
        if not isinstance(answers, dict):
            return _fail("validation", errors={})
        unknown = [k for k in answers if k not in FIELD_KEYS]
        if unknown:
            message = MESSAGES["unknown"][_lang_idx(rec.employee_id)]
            return _fail("validation", errors=dict.fromkeys(unknown, message))
        clean, errors = rec._clean(answers, now)
        rec.form_data = dict(rec.form_data or {}, **clean)
        result = {"ok": True, "saved": sorted(clean)}
        if errors:
            result["errors"] = errors
        return result

    @api.model
    def w_submit(self, sid, consent):
        Intake = self._intake_guard()
        now = Intake._intake_now()
        rec = Intake._by_sid(sid, now)
        if not rec:
            return _fail("session_expired")
        if rec.state == "submitted":
            # Idempotente: un doble clic o un reintento del portal.
            return {"ok": True, "state": "submitted"}
        idx = _lang_idx(rec.employee_id)
        # Se revalida todo, también lo que venía de la ficha.
        clean, errors = rec._clean(
            {k: v for k, v in rec._values().items() if v}, now
        )
        for key, _type, required, _es, _en in FIELDS:
            if required and not clean.get(key) and key not in errors:
                errors[key] = MESSAGES["required"][idx]
        if consent is not True:
            errors["consent"] = MESSAGES["consent"][idx]
        if errors:
            return _fail("validation", errors=errors)
        state_code = clean.get("private_state_code")
        rec.employee_id.write({
            "legal_name": clean["legal_name"],
            "private_street": clean["private_street"],
            "private_street2": clean.get("private_street2") or False,
            "private_city": clean["private_city"],
            "private_state_id": (
                rec._state(state_code).id if state_code else False
            ),
            "private_zip": clean.get("private_zip") or False,
            "country_id": self.env["res.country"].search(
                [("code", "=", clean["nationality_code"])], limit=1
            ).id,
            "birthday": clean["birthday"],
            "private_phone": clean["mobile_phone"],
            "emergency_contact": clean.get("emergency_contact") or False,
            "emergency_phone": clean.get("emergency_phone") or False,
        })
        # La sesión sigue viva (hasta sus límites) para la confirmación.
        rec.write({
            "state": "submitted",
            "submitted_at": now,
            "consent_at": now,
            "form_data": False,
            "otp_hash": False,
        })
        return {"ok": True, "state": "submitted"}

    # ------------------------------------------------------------- cron

    @api.model
    def _cron_expire(self):
        now = self._intake_now()
        Intake = self.sudo()
        Intake.search([
            ("state", "in", OPEN_STATES), ("token_expires_at", "<=", now),
        ])._revoke("expired")
        Intake.search(
            [("state", "in", ("expired", "cancelled"))]
        ).filtered("form_data").write({"form_data": False})
