# Copyright 2026 Cetmix Nordic Ltd
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import models


class ResPartner(models.Model):
    _inherit = "res.partner"

    def _compute_im_status(self):
        """Show the partners of AI bot users as always online."""
        bots = self.filtered(
            lambda partner: partner.sudo().user_ids.ai_bot_connection_id
        )
        bots.im_status = "online"
        others = self - bots
        if others:
            return super(ResPartner, others)._compute_im_status()
