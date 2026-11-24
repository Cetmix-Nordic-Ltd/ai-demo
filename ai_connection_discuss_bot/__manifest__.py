# Copyright 2026 Cetmix Nordic Ltd
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "AI Connection Discuss Bot",
    "summary": "Chat with an AI Connection (e.g. Claude via LiteLLM) in Discuss "
    "and record chatter",
    "version": "18.0.1.2.0",
    "license": "AGPL-3",
    "author": "Cetmix Nordic Ltd",
    "website": "https://github.com/Cetmix-Nordic-Ltd/ai-demo",
    "category": "Productivity/Discuss",
    "depends": [
        "mail",
        "ai_tool",
        "ai_connection_openai",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/ai_tool_data.xml",
        "data/res_users_data.xml",
        "data/ir_cron_data.xml",
        "views/res_users_views.xml",
        "views/ai_discuss_bot_request_views.xml",
        "views/ai_discuss_bot_action_views.xml",
        "templates/ai_discuss_bot_action_template.xml",
    ],
    "demo": [
        "demo/ai_discuss_bot_request_demo.xml",
        "demo/ai_discuss_bot_action_demo.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "ai_connection_discuss_bot/static/src/**/*",
        ],
        "web.assets_tests": [
            "ai_connection_discuss_bot/static/tests/tours/**/*",
        ],
    },
    "images": ["static/description/img/discuss_chat.png"],
    "installable": True,
}
