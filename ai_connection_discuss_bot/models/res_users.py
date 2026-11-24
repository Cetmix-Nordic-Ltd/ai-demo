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
# Tools that only propose a change; the change is made when the user who asked
# confirms it (see ai.discuss.bot.action).
WRITE_TOOL_FUNCTIONS = ("_ai_bot_propose_activity", "_ai_bot_propose_note")


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
        help="Odoo data tools the bot may use. They run with the access "
        "rights of the user who asked, never with the bot's own rights. The "
        "propose_* tools change nothing themselves: the user who asked must "
        "confirm each proposal.",
    )
    ai_bot_history_limit = fields.Integer(
        string="AI Bot History Limit",
        default=20,
        groups="base.group_system",
        help="How many of the latest messages of the conversation are sent "
        "to the AI with each question.",
    )

    ai_bot_attachment_mode = fields.Selection(
        selection=[
            ("files", "Send files (images and PDF as files, others as text)"),
            ("text", "Send extracted text only"),
            ("none", "Do not send attachments"),
        ],
        string="AI Bot Attachments",
        default="files",
        groups="base.group_system",
        help="How files attached to the conversation are sent to the AI. "
        "'Send files' suits Claude, which reads images and PDF files. "
        "'Send extracted text only' suits local models without image or PDF "
        "support: text is extracted from PDF and text files, images are "
        "skipped.",
    )
    ai_bot_max_attachment_mb = fields.Integer(
        string="AI Bot Max Attachment Size (MB)",
        default=10,
        groups="base.group_system",
        help="Larger files are not sent to the AI; the AI is told they were "
        "skipped. Images are limited to 5 MB in any case.",
    )

    def _ai_bot_safe_tools(self):
        """The bot's tools, limited to the safe ones whatever was stored."""
        self.ensure_one()
        return self.sudo().ai_bot_tool_ids.filtered_domain(SAFE_TOOL_DOMAIN).sudo()

    def _ai_bot_write_tools(self):
        """The bot's tools that propose changes for the user to confirm."""
        return self._ai_bot_safe_tools().filtered(
            lambda tool: tool.function_name in WRITE_TOOL_FUNCTIONS
        )

    def _compute_im_status(self):
        """Show AI bots as always online."""
        bots = self.filtered(lambda user: user.sudo().ai_bot_connection_id)
        bots.im_status = "online"
        others = self - bots
        if others:
            return super(ResUsers, others)._compute_im_status()
