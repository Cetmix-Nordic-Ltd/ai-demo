# Copyright 2026 Cetmix Nordic Ltd
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

# XML ids renamed to the OCA naming convention. Renaming them before the
# data files are loaded keeps the existing records (above all the bot user
# and its partner, which hold the bot's settings and conversations) instead
# of deleting them and creating new ones.
RENAMED = {
    "partner_ai_bot_claude": "res_partner_ai_bot_claude",
    "user_ai_bot_claude": "res_users_ai_bot_claude",
    "view_users_form_ai_bot": "view_users_form",
    "ai_discuss_bot_request_list_view": "ai_discuss_bot_request_view_list",
    "ai_discuss_bot_request_form_view": "ai_discuss_bot_request_view_form",
    "ai_discuss_bot_request_search_view": "ai_discuss_bot_request_view_search",
}


def migrate(cr, version):
    for old, new in RENAMED.items():
        cr.execute(
            """
            UPDATE ir_model_data SET name = %s
            WHERE module = 'ai_connection_discuss_bot' AND name = %s
            """,
            (new, old),
        )
