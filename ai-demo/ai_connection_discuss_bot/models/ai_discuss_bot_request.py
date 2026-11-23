# Copyright 2026 Cetmix Nordic Ltd
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json
import logging

from odoo import _, api, fields, models
from odoo.tools import html2plaintext, plaintext2html

_logger = logging.getLogger(__name__)

# Longest text taken from a single message, so one long email cannot fill
# the whole prompt.
MAX_MESSAGE_CHARS = 4000

DEFAULT_SYSTEM_PROMPT = (
    "You are {bot_name}, an AI assistant inside the Odoo ERP of {company}. "
    "Reply in the language of the latest user message. Keep answers short "
    "and practical. Your reply is shown as plain text: do not use Markdown "
    "headings, tables, bold text or code fences; short lists starting with "
    "'-' are fine. User messages are prefixed with the author's name. "
    "If you do not know something, say so."
)
NO_TOOLS_PROMPT = (
    " You cannot read or change Odoo data; you only see the messages of this "
    "conversation."
)
TOOLS_PROMPT = (
    " You can read Odoo data with the tools. They run with the access rights "
    "of {asker}, the user who asked, so you see only what that user may see; "
    "an access error means the user may not see that data - say so instead of "
    "retrying. Use the tools instead of guessing figures. If you are unsure of "
    "a model or field name, look it up with find_models and get_model_fields "
    "before searching. Use aggregate_records for totals and counts and "
    "search_records for individual records. You "
    "cannot create, change or delete anything. Mention briefly which records "
    "your answer is based on."
)
RECORD_SYSTEM_PROMPT = (
    " This conversation is the chatter of the Odoo record '{record_name}' "
    "(model {model}, id {record_id}, {model_name}). Emails and internal notes "
    "of that record are included."
)
# Upper bound of AI round trips (tool calls) for one question.
MAX_TOOL_ITERATIONS = 10


class AiDiscussBotRequest(models.Model):
    _name = "ai.discuss.bot.request"
    _description = "AI Discuss Bot Request"
    _order = "id desc"

    bot_user_id = fields.Many2one(
        comodel_name="res.users",
        string="Bot",
        required=True,
        ondelete="cascade",
        readonly=True,
    )
    message_id = fields.Many2one(
        comodel_name="mail.message",
        string="Question",
        ondelete="set null",
        readonly=True,
        help="The message that triggered this request.",
    )
    author_id = fields.Many2one(
        comodel_name="res.partner",
        string="Asked by",
        readonly=True,
    )
    asker_user_id = fields.Many2one(
        comodel_name="res.users",
        string="Tools run as",
        readonly=True,
        help="The user whose access rights the bot's tools use.",
    )
    res_model = fields.Char(string="Model", required=True, readonly=True)
    res_id = fields.Many2oneReference(
        string="Record ID", model_field="res_model", required=True, readonly=True
    )
    messages_json = fields.Text(
        string="Conversation",
        readonly=True,
        help="Messages sent to the AI, as JSON.",
    )
    state = fields.Selection(
        selection=[
            ("pending", "Pending"),
            ("done", "Done"),
            ("error", "Error"),
        ],
        default="pending",
        required=True,
        readonly=True,
        index=True,
    )
    response = fields.Text(readonly=True)
    error = fields.Text(readonly=True)
    prompt_tokens = fields.Integer(readonly=True)
    completion_tokens = fields.Integer(readonly=True)
    iterations = fields.Integer(
        readonly=True, help="AI round trips used: 1 + one per batch of tool calls."
    )
    tool_log = fields.Text(
        readonly=True,
        help="Tool calls the AI made, with their arguments and outcome.",
    )

    # ------------------------------------------------------------------
    # Queueing
    # ------------------------------------------------------------------

    @api.model
    def _get_bot_users(self):
        return (
            self.env["res.users"].sudo().search([("ai_bot_connection_id", "!=", False)])
        )

    @api.model
    def _enqueue_from_message(self, record, message):
        """Queue a bot answer for ``message`` posted on ``record`` if a bot is
        addressed: any message in a direct chat with the bot, or a message that
        mentions the bot in a channel, group or record chatter.

        The conversation is read here, with the access rights of the user who
        posted, so the bot never sees messages that user could not read.
        """
        if message.message_type != "comment":
            return self.browse()
        is_channel = record._name == "discuss.channel"
        # Cheap exit for the most common case: chatter message without mentions
        if not is_channel and not message.partner_ids:
            return self.browse()
        bots = self._get_bot_users()
        if not bots:
            return self.browse()
        author = message.author_id
        if not author or author in bots.partner_id:
            return self.browse()
        # This demo answers internal users only (no portal users, guests or
        # livechat visitors). The bot's tools run with this user's rights.
        if self.env.user.partner_id == author and self.env.user._is_internal():
            asker = self.env.user
        else:
            asker = author.sudo().user_ids.filtered(lambda user: user._is_internal())[
                :1
            ]
        if not asker:
            return self.browse()

        if is_channel:
            members = record.sudo().channel_member_ids.partner_id
            addressed = bots.filtered(lambda bot: bot.partner_id in members)
            if record.channel_type != "chat":
                addressed = addressed.filtered(
                    lambda bot: bot.partner_id in message.partner_ids
                )
        else:
            addressed = bots.filtered(lambda bot: bot.partner_id in message.partner_ids)

        requests = self.browse()
        for bot in addressed:
            conversation = self._build_conversation(record, message, bot, asker)
            if len(conversation) < 2:
                # Only the system prompt: nothing to answer
                continue
            requests |= self.sudo().create(
                {
                    "bot_user_id": bot.id,
                    "message_id": message.id,
                    "author_id": author.id,
                    "asker_user_id": asker.id,
                    "res_model": record._name,
                    "res_id": record.id,
                    "messages_json": json.dumps(conversation, ensure_ascii=False),
                }
            )
            if is_channel:
                self._notify_bot_typing(record, bot, True)
        if requests:
            self.env.ref(
                "ai_connection_discuss_bot.ir_cron_ai_discuss_bot_process"
            ).sudo()._trigger()
        return requests

    @api.model
    def _build_conversation(self, record, message, bot, asker):
        """Return the OpenAI-style message list for ``bot``: a system prompt and
        the latest messages of the thread, oldest first, ending with
        ``message``."""
        bot = bot.sudo()
        is_channel = record._name == "discuss.channel"
        message_types = ["comment"] if is_channel else ["comment", "email"]
        history = self.env["mail.message"].search(
            [
                ("model", "=", record._name),
                ("res_id", "=", record.id),
                ("message_type", "in", message_types),
                ("id", "<=", message.id),
            ],
            order="id desc",
            limit=max(bot.ai_bot_history_limit, 1),
        )
        conversation = []
        for msg in history.sorted("id"):
            text = html2plaintext(msg.body or "").strip()
            if not text:
                continue
            text = text[:MAX_MESSAGE_CHARS]
            if msg.author_id == bot.partner_id:
                conversation.append({"role": "assistant", "content": text})
            else:
                name = msg.author_id.name or msg.email_from or _("Unknown")
                conversation.append({"role": "user", "content": f"{name}: {text}"})
        # The model expects the conversation to start with the user
        while conversation and conversation[0]["role"] == "assistant":
            conversation.pop(0)
        return [
            {"role": "system", "content": self._get_system_prompt(record, bot, asker)}
        ] + conversation

    @api.model
    def _get_system_prompt(self, record, bot, asker):
        prompt = DEFAULT_SYSTEM_PROMPT.format(
            bot_name=bot.name, company=bot.company_id.name
        )
        if bot._ai_bot_safe_tools():
            prompt += TOOLS_PROMPT.format(asker=asker.name)
        else:
            prompt += NO_TOOLS_PROMPT
        if record._name != "discuss.channel":
            prompt += RECORD_SYSTEM_PROMPT.format(
                record_name=record.display_name,
                model=record._name,
                record_id=record.id,
                model_name=record._description,
            )
        if bot.ai_bot_instructions:
            prompt += "\n\n" + bot.ai_bot_instructions
        return prompt

    @api.model
    def _notify_bot_typing(self, channel, bot, is_typing):
        channel.sudo().channel_member_ids.filtered(
            lambda member: member.partner_id == bot.partner_id
        )._notify_typing(is_typing)

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------

    @api.model
    def _cron_process_requests(self, limit=20):
        requests = self.search([("state", "=", "pending")], order="id", limit=limit)
        for request in requests:
            request._process()
            if not self.env.registry.in_test_mode():
                # Each answer is posted on its own: a later failure must not
                # roll back answers that users already see.
                self.env.cr.commit()  # pylint: disable=invalid-commit
        if len(requests) == limit:
            self.env.ref(
                "ai_connection_discuss_bot.ir_cron_ai_discuss_bot_process"
            )._trigger()

    def _process(self):
        self.ensure_one()
        bot = self.bot_user_id.sudo()
        record = self.env[self.res_model].sudo().browse(self.res_id).exists()
        if not record:
            self.write({"state": "error", "error": "The record no longer exists."})
            return
        try:
            with self.env.cr.savepoint():
                connection = bot.ai_bot_connection_id
                if not connection:
                    raise ValueError(f"User {bot.name} has no AI Bot Connection.")
                run_kwargs = {
                    "messages": json.loads(self.messages_json),
                    "max_iterations": MAX_TOOL_ITERATIONS,
                }
                tools = bot._ai_bot_safe_tools()
                if tools and self.asker_user_id:
                    # ai_tool executes tools with superuser rights; the tools of
                    # this module switch to the asking user from this context.
                    tool_context = {
                        "ai_discuss_bot_user_id": self.asker_user_id.id,
                        "ai_discuss_bot_request_id": self.id,
                    }
                    run_kwargs["tools"] = tools.with_context(**tool_context)
                    run_kwargs["record"] = record.with_context(**tool_context)
                content, prompt_tokens, completion_tokens, iterations = connection._run(
                    **run_kwargs
                )
                content = (content or "").strip()
                if not content:
                    raise ValueError("The AI returned an empty answer.")
        except Exception as error:
            _logger.exception("AI Discuss Bot: request %s failed", self.id)
            self.write({"state": "error", "error": str(error)})
            self._post_reply(
                record,
                _(
                    "Sorry, I could not answer right now. An administrator can "
                    "see the reason in Settings > Technical > AI > "
                    "AI Bot Requests."
                ),
            )
            return
        self.write(
            {
                "state": "done",
                "response": content,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "iterations": iterations,
            }
        )
        self._post_reply(record, content)

    def _post_reply(self, record, text):
        """Post ``text`` as the bot. In a record chatter the answer is an
        internal note that notifies the user who asked, so nothing is ever
        emailed to the record's external followers."""
        self.ensure_one()
        bot = self.bot_user_id.sudo()
        record = record.sudo().with_context(
            ai_discuss_bot_skip=True,
            mail_create_nosubscribe=True,
            mail_post_autofollow=False,
        )
        body = plaintext2html(text)
        if record._name == "discuss.channel":
            message = record.message_post(
                author_id=bot.partner_id.id,
                body=body,
                message_type="comment",
                subtype_xmlid="mail.mt_comment",
            )
            self._notify_bot_typing(record, bot, False)
        else:
            message = record.message_post(
                author_id=bot.partner_id.id,
                body=body,
                message_type="comment",
                subtype_xmlid="mail.mt_note",
                partner_ids=self.author_id.ids,
            )
        return message
