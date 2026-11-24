# Copyright 2026 BWEALTHICS LLC
# Part of bw_people_ops. License LGPL-3.
from odoo import SUPERUSER_ID, api

SYSTEMS = (
    "google_workspace", "odoo", "gitea", "timely", "n8n", "whatsapp",
    "claude_team", "1password", "other",
)


def migrate(cr, version):
    # `system` went from free text ("Google Workspace") to a closed selection
    # ("google_workspace"): normalize what matches, keep the old text in the
    # notes and file the rest under "other".
    cr.execute(
        """
        UPDATE bw_access_grant
           SET system = lower(replace(trim(system), ' ', '_'))
         WHERE lower(replace(trim(system), ' ', '_')) IN %s
        """,
        (SYSTEMS,),
    )
    cr.execute(
        """
        UPDATE bw_access_grant
           SET notes = concat_ws(E'\n', notes, 'System (before 2.0): ' || system),
               system = 'other'
         WHERE system NOT IN %s
        """,
        (SYSTEMS,),
    )
    env = api.Environment(cr, SUPERUSER_ID, {})
    grants = env["bw.access.grant"].with_context(active_test=False).search([])
    env.add_to_compute(grants._fields["name"], grants)
