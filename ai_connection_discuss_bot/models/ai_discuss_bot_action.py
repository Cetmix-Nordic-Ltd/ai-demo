# Copyright 2026 Cetmix Nordic Ltd
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json
import logging
import secrets
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import consteq, plaintext2html

from ..tools.markdown import markdown_to_html

_logger = logging.getLogger(__name__)

# A proposal must be confirmed within this time.
PROPOSAL_LIFETIME = timedelta(hours=24)


class AiDiscussBotAction(models.Model):
    """A change the AI proposed. Nothing happens until the user who asked
    confirms it on the review page (controllers/main.py); it is then made
    with that user's own access rights."""

    _name = "ai.discuss.bot.action"
    _description = "AI Discuss Bot Proposal"
    _order = "id desc"

    name = fields.Char(string="Proposal", required=True, readonly=True)
    description = fields.Text(readonly=True)
    request_id = fields.Many2one(
        comodel_name="ai.discuss.bot.request",
        required=True,
        ondelete="cascade",
        readonly=True,
        index=True,
    )
    bot_user_id = fields.Many2one(related="request_id.bot_user_id", string="Bot")
    asker_user_id = fields.Many2one(
        comodel_name="res.users",
        string="To be confirmed by",
        required=True,
        readonly=True,
    )
    action_type = fields.Selection(
        selection=[
            ("activity", "Schedule activity"),
            ("note", "Log note"),
        ],
        required=True,
        readonly=True,
    )
    res_model = fields.Char(string="Model", required=True, readonly=True)
    res_id = fields.Many2oneReference(
        string="Record ID", model_field="res_model", required=True, readonly=True
    )
    values_json = fields.Text(string="Values", required=True, readonly=True)
    state = fields.Selection(
        selection=[
            ("proposed", "Waiting for confirmation"),
            ("done", "Done"),
            ("cancelled", "Cancelled"),
            ("expired", "Expired"),
            ("failed", "Failed"),
        ],
        default="proposed",
        required=True,
        readonly=True,
        index=True,
    )
    token = fields.Char(
        readonly=True,
        copy=False,
        groups="base.group_system",
        default=lambda self: secrets.token_urlsafe(24),
    )
    decided_date = fields.Datetime(string="Decided on", readonly=True)
    error = fields.Text(readonly=True)
    message_id = fields.Many2one(
        "mail.message",
        string="Bot Reply",
        readonly=True,
        ondelete="set null",
        index="btree_not_null",
        help="The bot's answer that shows this proposal; Discuss shows the "
        "confirmation buttons on it.",
    )

    # ------------------------------------------------------------------
    # Proposing (called by ai.discuss.bot.tools)
    # ------------------------------------------------------------------

    @api.model
    def _propose(self, request, action_type, record, values, name, description):
        return self.sudo().create(
            {
                "request_id": request.id,
                "asker_user_id": request.asker_user_id.id,
                "action_type": action_type,
                "res_model": record._name,
                "res_id": record.id,
                "values_json": json.dumps(values, ensure_ascii=False),
                "name": name,
                "description": description,
            }
        )

    def _get_review_url(self):
        self.ensure_one()
        token = self.sudo().token
        return f"{self.get_base_url()}/ai_discuss_bot/action/{self.id}?token={token}"

    # ------------------------------------------------------------------
    # Review page
    # ------------------------------------------------------------------

    @api.model
    def _get_for_review(self, action_id, token, user):
        """The proposal ``action_id`` if ``token`` matches and ``user`` is the
        one who must confirm it, else an empty recordset."""
        action = self.sudo().browse(int(action_id)).exists()
        if (
            not action
            or not token
            or not consteq(action.token or "", token)
            or action.asker_user_id != user
        ):
            return self.browse()
        if action.state == "proposed" and action._is_expired():
            action.write({"state": "expired", "decided_date": fields.Datetime.now()})
        return action

    @api.model
    def _get_for_user(self, action_id, user):
        """The proposal ``action_id`` if ``user`` is the one who must decide
        it, else an empty recordset. For the buttons in Discuss: the JSON
        request carries the user's session, no token is needed."""
        action = self.sudo().browse(int(action_id)).exists()
        if not action or action.asker_user_id != user:
            return self.browse()
        if action.state == "proposed" and action._is_expired():
            action.write({"state": "expired", "decided_date": fields.Datetime.now()})
        return action

    def _to_discuss(self, user):
        """Data of the proposals for the buttons in Discuss."""
        states = dict(self._fields["state"]._description_selection(self.env))
        result = []
        for action in self.sudo():
            if action.state == "proposed" and action._is_expired():
                action.write(
                    {"state": "expired", "decided_date": fields.Datetime.now()}
                )
            result.append(
                {
                    "id": action.id,
                    "name": action.name,
                    "state": action.state,
                    "state_label": states.get(action.state, action.state),
                    "error": action.error or "",
                    "asker_name": action.asker_user_id.name,
                    "can_decide": action.state == "proposed"
                    and action.asker_user_id == user,
                }
            )
        return result

    def _is_expired(self):
        self.ensure_one()
        return fields.Datetime.now() > self.create_date + PROPOSAL_LIFETIME

    def _get_record(self):
        """The target record, with the confirming user's own access rights."""
        self.ensure_one()
        return (
            self.env[self.res_model]
            .with_user(self.asker_user_id)
            .browse(self.res_id)
            .exists()
        )

    def _get_record_url(self):
        self.ensure_one()
        return f"/mail/view?model={self.res_model}&res_id={self.res_id}"

    def _get_conversation_url(self):
        self.ensure_one()
        request = self.request_id
        if request.res_model == "discuss.channel":
            return f"/odoo/discuss?active_id=discuss.channel_{request.res_id}"
        return f"/mail/view?model={request.res_model}&res_id={request.res_id}"

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------

    def _confirm(self):
        self.ensure_one()
        if self.state != "proposed":
            raise UserError(_("This proposal is no longer waiting for confirmation."))
        if self._is_expired():
            self.write({"state": "expired", "decided_date": fields.Datetime.now()})
            return
        try:
            with self.env.cr.savepoint():
                record = self._get_record()
                if not record:
                    raise UserError(_("The record no longer exists."))
                record = record.with_context(
                    ai_discuss_bot_skip=True, mail_post_autofollow=False
                )
                getattr(self, f"_execute_{self.action_type}")(
                    record, json.loads(self.values_json)
                )
        except Exception as error:
            _logger.info("AI Discuss Bot: proposal %s failed: %s", self.id, error)
            self.write(
                {
                    "state": "failed",
                    "error": str(error),
                    "decided_date": fields.Datetime.now(),
                }
            )
            self._notify_outcome(_("Could not do it: %(proposal)s (%(error)s)"))
            return
        self.write({"state": "done", "decided_date": fields.Datetime.now()})
        self._notify_outcome(_("Done: %(proposal)s"))

    def _cancel(self):
        self.ensure_one()
        if self.state != "proposed":
            raise UserError(_("This proposal is no longer waiting for confirmation."))
        self.write({"state": "cancelled", "decided_date": fields.Datetime.now()})
        self._notify_outcome(_("Cancelled: %(proposal)s"))

    def _notify_outcome(self, template):
        """Tell the conversation what happened, so the AI knows next time."""
        self.ensure_one()
        request = self.request_id
        thread = self.env[request.res_model].sudo().browse(request.res_id).exists()
        if not thread:
            return
        text = template % {"proposal": self.name, "error": self.error or ""}
        request._post_reply(thread, text)

    def _confirmed_by_html(self):
        return Markup("<p><em>{}</em></p>").format(
            _(
                "Drafted by %(bot)s, confirmed by %(user)s.",
                bot=self.bot_user_id.name,
                user=self.asker_user_id.name,
            )
        )

    def _execute_activity(self, record, values):
        note = values.get("note")
        record.activity_schedule(
            date_deadline=fields.Date.to_date(values["date_deadline"]),
            summary=values.get("summary") or "",
            note=plaintext2html(note) if note else "",
            user_id=values["user_id"],
            activity_type_id=values["activity_type_id"],
        )

    def _execute_note(self, record, values):
        record.message_post(
            body=markdown_to_html(values["body"]) + self._confirmed_by_html(),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )
