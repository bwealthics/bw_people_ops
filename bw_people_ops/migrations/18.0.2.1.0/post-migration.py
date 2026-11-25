# Copyright 2026 BWEALTHICS LLC
# Part of bw_people_ops. License LGPL-3.
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    # Crea la sal del HMAC del código antes de la primera petición, para que
    # dos peticiones simultáneas no generen cada una la suya. Idempotente.
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["bw.people.intake"]._pepper()
