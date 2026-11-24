# Copyright 2026 Cetmix Nordic Ltd
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from werkzeug.exceptions import NotFound

from odoo import http
from odoo.http import request


class AiDiscussBotController(http.Controller):
    """Review page of a proposal made by the AI. Only the user who asked the
    question can open it (the link also carries a secret token); the decision
    is sent with a POST form, so link previews and mail scanners that open
    the link cannot confirm anything."""

    def _get_action(self, action_id, token):
        action = request.env["ai.discuss.bot.action"]._get_for_review(
            action_id, token, request.env.user
        )
        if not action:
            raise NotFound()
        return action

    @http.route(
        "/ai_discuss_bot/action/<int:action_id>",
        type="http",
        auth="user",
        methods=["GET"],
    )
    def action_review(self, action_id, token=None, **kwargs):
        action = self._get_action(action_id, token)
        return request.render(
            "ai_connection_discuss_bot.ai_discuss_bot_action_review_page",
            {"action": action, "token": token},
        )

    @http.route(
        "/ai_discuss_bot/action/<int:action_id>/decide",
        type="http",
        auth="user",
        methods=["POST"],
    )
    def action_decide(self, action_id, token=None, decision=None, **kwargs):
        action = self._get_action(action_id, token)
        if action.state == "proposed":
            if decision == "confirm":
                action._confirm()
            elif decision == "cancel":
                action._cancel()
        return request.render(
            "ai_connection_discuss_bot.ai_discuss_bot_action_review_page",
            {"action": action, "token": token},
        )
