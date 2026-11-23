# Copyright 2026 Cetmix Nordic Ltd
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models

# Tools a bot may use. They are executed with superuser rights by ai_tool, so
# only tools that switch to the asking user themselves (ai.discuss.bot.tools)
# and the harmless current date tool are allowed. ai_tool's post_message, for
# example, would post as superuser and is therefore excluded.
SAFE_TOOL_MODEL = "ai.discuss.bot.tools"
SAFE_EXTRA_TOOL_FUNCTIONS = ("_ai_get_date",)
SAFE_TOOL_DOMAIN = [
    "|",
    ("model_id.model", "=", SAFE_TOOL_MODEL),
    ("function_name", "in", SAFE_EXTRA_TOOL_FUNCTIONS),
]


class ResUsers(models.Model):
    _inherit = "res.users"

    ai_bot_connection_id = fields.Many2one(
        comodel_name="ai.connection",
        string="AI Bot Connection",
        groups="base.group_system",
        help="When set, this user answers as an AI bot: in direct chats, "
        "in channels and groups when mentioned, and in record chatter "
        "when mentioned.",
    )
    ai_bot_instructions = fields.Text(
        string="AI Bot Instructions",
        groups="base.group_system",
        help="Extra instructions added to the bot's system prompt, "
        "e.g. the company's tone of voice or what the bot must not do.",
    )
    ai_bot_tool_ids = fields.Many2many(
        comodel_name="ai.tool",
        relation="res_users_ai_bot_tool_rel",
        column1="user_id",
        column2="tool_id",
        string="AI Bot Tools",
        domain=SAFE_TOOL_DOMAIN,
        groups="base.group_system",
        help="Read-only Odoo data tools the bot may use. They run with the "
        "access rights of the user who asked, never with the bot's own rights.",
    )
    ai_bot_history_limit = fields.Integer(
        string="AI Bot History Limit",
        default=20,
        groups="base.group_system",
        help="How many of the latest messages of the conversation are sent "
        "to the AI with each question.",
    )

    def _ai_bot_safe_tools(self):
        """The bot's tools, limited to the safe ones whatever was stored."""
        self.ensure_one()
        return self.sudo().ai_bot_tool_ids.filtered_domain(SAFE_TOOL_DOMAIN).sudo()

    def _compute_im_status(self):
        """Show AI bots as always online."""
        bots = self.filtered(lambda user: user.sudo().ai_bot_connection_id)
        bots.im_status = "online"
        others = self - bots
        if others:
            return super(ResUsers, others)._compute_im_status()
