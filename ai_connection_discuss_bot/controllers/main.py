# Copyright 2026 Cetmix Nordic Ltd
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from werkzeug.exceptions import NotFound

from odoo import http
from odoo.http import request


class AiDiscussBotController(http.Controller):
    """Decisions on proposals made by the AI, by the user who asked only.

    Discuss shows buttons on the bot's answer and sends the decision as a
    JSON request. The review page (for e-mail notifications and other
    clients) carries a secret token and takes the decision with a POST form,
    so link previews and mail scanners that open the link cannot confirm
    anything."""

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

    # ------------------------------------------------------------------
    # Buttons in Discuss (JSON requests of the logged in user)
    # ------------------------------------------------------------------

    @http.route("/ai_discuss_bot/message_actions", type="json", auth="user")
    def message_actions(self, message_id):
        """Proposals shown on a message the user may read."""
        message = request.env["mail.message"].browse(int(message_id)).exists()
        if not message:
            return []
        message.check_access("read")
        actions = (
            request.env["ai.discuss.bot.action"]
            .sudo()
            .search([("message_id", "=", message.id)], order="id")
        )
        return actions._to_discuss(request.env.user)

    @http.route("/ai_discuss_bot/action/decide_inline", type="json", auth="user")
    def action_decide_inline(self, action_id, decision):
        action = request.env["ai.discuss.bot.action"]._get_for_user(
            action_id, request.env.user
        )
        if not action:
            raise NotFound()
        if action.state == "proposed":
            if decision == "confirm":
                action._confirm()
            elif decision == "cancel":
                action._cancel()
        return action._to_discuss(request.env.user)[0]
