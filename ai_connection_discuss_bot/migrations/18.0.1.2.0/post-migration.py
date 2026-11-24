# Copyright 2026 Cetmix Nordic Ltd
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Give the default Claude bot the new proposal tools. Its user record is
    'noupdate', so the data file does not do it on upgrade. Bots that an
    administrator created keep their own tool selection."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    bot = env.ref("ai_connection_discuss_bot.res_users_ai_bot_claude", False)
    tools = env.ref("ai_connection_discuss_bot.ai_tool_propose_activity", False)
    tools |= env.ref("ai_connection_discuss_bot.ai_tool_propose_note", False)
    if bot and tools:
        bot.write({"ai_bot_tool_ids": [(4, tool.id) for tool in tools]})
