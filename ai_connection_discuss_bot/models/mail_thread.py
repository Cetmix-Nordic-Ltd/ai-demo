# Copyright 2026 Cetmix Nordic Ltd
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from odoo import models

_logger = logging.getLogger(__name__)


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    def _message_post_after_hook(self, message, msg_values):
        res = super()._message_post_after_hook(message, msg_values)
        if self.env.context.get("ai_discuss_bot_skip"):
            return res
        try:
            self.env["ai.discuss.bot.request"]._enqueue_from_message(self, message)
        except Exception:
            # The bot must never prevent a user from posting a message.
            _logger.exception(
                "AI Discuss Bot: could not queue an answer for message %s",
                message.id,
            )
        return res
