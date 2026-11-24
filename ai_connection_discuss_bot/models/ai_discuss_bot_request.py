# Copyright 2026 Cetmix Nordic Ltd
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import io
import json
import logging
import re
import threading

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.tools import html2plaintext

from ..tools.markdown import markdown_to_html

_logger = logging.getLogger(__name__)

# Longest text taken from a single message, so one long email cannot fill
# the whole prompt.
MAX_MESSAGE_CHARS = 4000
# Attachments sent with one question, newest first.
MAX_ATTACHMENTS = 5
# Claude rejects larger images.
MAX_IMAGE_BYTES = 5 * 1024 * 1024
# Longest text extracted from one attachment.
MAX_ATTACHMENT_TEXT = 30000
IMAGE_MIMETYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")
TEXT_MIMETYPES = ("application/json", "application/xml", "application/csv")
# Main types Odoo stores in index_content when it cannot extract any text.
NO_TEXT_INDEX = {"application", "image", "video", "audio", "text"}
# Reasoning that some local models (e.g. Qwen) put into the answer.
THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
# The confirmation links the bot adds under an answer. They are left out of
# the conversation history sent to the AI.
ACTIONS_BLOCK = re.compile(
    r"<div[^>]*\bo_ai_bot_actions\b[^>]*>.*?</div>", re.DOTALL | re.IGNORECASE
)

DEFAULT_SYSTEM_PROMPT = (
    "You are {bot_name}, an AI assistant inside the Odoo ERP of {company}. "
    "Reply in the language of the latest user message. Keep answers short "
    "and practical. Format the reply in Markdown: short paragraphs, '-' "
    "lists, **bold** for key figures, and a table only for tabular data. "
    "User messages are prefixed with the author's name. "
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
    "search_records for individual records. Mention briefly which records "
    "your answer is based on, and link to a record as "
    "[its name](/mail/view?model=<model>&res_id=<id>)."
)
READ_ONLY_PROMPT = " You cannot create, change or delete anything."
WRITE_TOOLS_PROMPT = (
    " You can also propose changes with the propose_* tools. A proposal "
    "changes nothing by itself: {asker} must open the confirmation link that "
    "is added under your reply and confirm it there; a chat reply such as "
    "'yes' does not confirm it. Propose only what the user asked for. After "
    "proposing, say briefly what you proposed and that it waits for "
    "confirmation; never say it is done. You cannot change or delete "
    "existing records."
)
ATTACHMENTS_PROMPT = (
    " Files attached to messages are marked [Attachment: name]; their content "
    "follows the message when it could be sent to you."
)
RECORD_SYSTEM_PROMPT = (
    " This conversation is the chatter of the Odoo record '{record_name}' "
    "(model {model}, id {record_id}, {model_name}). Emails and internal notes "
    "of that record are included."
)
# Upper bound of AI round trips (tool calls) for one question.
MAX_TOOL_ITERATIONS = 10
# A request whose processing was interrupted this many times (e.g. killed by
# the cron time limit while a slow model was answering) is given up.
MAX_ATTEMPTS = 2


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
        help="Messages sent to the AI, as JSON. Attachments are listed by id "
        "in 'attachment_ids' and sent as files or text when the request is "
        "processed.",
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
    attempts = fields.Integer(
        readonly=True,
        help="How many times processing started. More than one means an earlier "
        "run was interrupted, e.g. by the cron time limit.",
    )
    action_ids = fields.One2many(
        comodel_name="ai.discuss.bot.action",
        inverse_name="request_id",
        string="Proposals",
        readonly=True,
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
        asked, so the bot never sees messages or files that user could not
        read.
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
        ``message``. User messages with files carry their ids in
        ``attachment_ids``; see :meth:`_prepare_messages`."""
        bot = bot.sudo()
        is_channel = record._name == "discuss.channel"
        message_types = ["comment"] if is_channel else ["comment", "email"]
        history = (
            self.env["mail.message"]
            .with_user(asker)
            .search(
                [
                    ("model", "=", record._name),
                    ("res_id", "=", record.id),
                    ("message_type", "in", message_types),
                    ("id", "<=", message.id),
                ],
                order="id desc",
                limit=max(bot.ai_bot_history_limit, 1),
            )
        )
        attachments_by_message = self._pick_attachments(history, bot, asker)
        conversation = []
        for msg in history.sorted("id"):
            text = self._message_text(msg)
            if msg.author_id == bot.partner_id:
                if text:
                    conversation.append({"role": "assistant", "content": text})
                continue
            sent, skipped = attachments_by_message.get(msg.id, ({}, []))
            lines = [text] if text else []
            lines += [f"[Attachment: {name}]" for name in sent.values()]
            lines += [f"[Attachment not sent: {name}]" for name in skipped]
            if not lines:
                continue
            author_name = msg.author_id.name or msg.email_from or _("Unknown")
            entry = {"role": "user", "content": f"{author_name}: " + "\n".join(lines)}
            if sent:
                entry["attachment_ids"] = list(sent)
            conversation.append(entry)
        # The model expects the conversation to start with the user
        while conversation and conversation[0]["role"] == "assistant":
            conversation.pop(0)
        return [
            {"role": "system", "content": self._get_system_prompt(record, bot, asker)}
        ] + conversation

    @api.model
    def _message_text(self, message):
        body = ACTIONS_BLOCK.sub("", message.body or "")
        return html2plaintext(body).strip()[:MAX_MESSAGE_CHARS]

    @api.model
    def _pick_attachments(self, history, bot, asker):
        """Choose the files to send, newest first.

        Returns ``{message id: ({attachment id: name} sent, [names] skipped)}``.
        Only attachments ``asker`` may read are considered.
        """
        result = {}
        mode = bot.ai_bot_attachment_mode or "files"
        max_bytes = max(bot.ai_bot_max_attachment_mb, 1) * 1024 * 1024
        remaining = 0 if mode == "none" else MAX_ATTACHMENTS
        Attachment = self.env["ir.attachment"].with_user(asker)
        for msg in history.sorted("id", reverse=True):
            if not msg.attachment_ids:
                continue
            # search() applies the attachment access rules of the user
            readable = Attachment.search(
                [("id", "in", msg.attachment_ids.ids)], order="id"
            )
            sent, skipped = {}, []
            for attachment in readable.sudo():
                limit = max_bytes
                if mode == "files" and attachment.mimetype in IMAGE_MIMETYPES:
                    limit = min(max_bytes, MAX_IMAGE_BYTES)
                if remaining and attachment.file_size <= limit:
                    sent[attachment.id] = attachment.name
                    remaining -= 1
                else:
                    skipped.append(attachment.name)
            result[msg.id] = (sent, skipped)
        return result

    @api.model
    def _get_system_prompt(self, record, bot, asker):
        prompt = DEFAULT_SYSTEM_PROMPT.format(
            bot_name=bot.name, company=bot.company_id.name
        )
        if bot._ai_bot_safe_tools():
            prompt += TOOLS_PROMPT.format(asker=asker.name)
            if bot._ai_bot_write_tools():
                prompt += WRITE_TOOLS_PROMPT.format(asker=asker.name)
            else:
                prompt += READ_ONLY_PROMPT
        else:
            prompt += NO_TOOLS_PROMPT
        if (bot.ai_bot_attachment_mode or "files") != "none":
            prompt += ATTACHMENTS_PROMPT
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
    # Attachments
    # ------------------------------------------------------------------

    def _prepare_messages(self, conversation):
        """Turn the stored conversation into the messages sent to the AI:
        attachment ids become OpenAI content parts (image, file or text)."""
        self.ensure_one()
        mode = self.bot_user_id.sudo().ai_bot_attachment_mode or "files"
        messages = []
        for message in conversation:
            message = dict(message)
            attachment_ids = message.pop("attachment_ids", None)
            if attachment_ids and mode != "none":
                parts = [{"type": "text", "text": message["content"]}]
                attachments = (
                    self.env["ir.attachment"].sudo().browse(attachment_ids).exists()
                )
                for attachment in attachments:
                    parts += self._attachment_parts(attachment, mode)
                message["content"] = parts
            messages.append(message)
        return messages

    @api.model
    def _attachment_parts(self, attachment, mode):
        mimetype = attachment.mimetype or ""
        raw = attachment.raw or b""
        if mode == "files" and mimetype in IMAGE_MIMETYPES:
            data = base64.b64encode(raw).decode()
            return [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mimetype};base64,{data}"},
                }
            ]
        if mode == "files" and mimetype == "application/pdf":
            data = base64.b64encode(raw).decode()
            return [
                {
                    "type": "file",
                    "file": {
                        "filename": attachment.name,
                        "file_data": f"data:application/pdf;base64,{data}",
                    },
                }
            ]
        if mimetype.startswith("image/"):
            text = _("[%(name)s: images are not sent to this AI]", name=attachment.name)
            return [{"type": "text", "text": text}]
        content = self._attachment_text(attachment, raw, mimetype)
        if not content:
            text = _(
                "[%(name)s: the content of this file type cannot be read]",
                name=attachment.name,
            )
            return [{"type": "text", "text": text}]
        if len(content) > MAX_ATTACHMENT_TEXT:
            content = content[:MAX_ATTACHMENT_TEXT] + "\n[...]"
        return [{"type": "text", "text": f"Content of {attachment.name}:\n{content}"}]

    @api.model
    def _attachment_text(self, attachment, raw, mimetype):
        if mimetype.startswith("text/") or mimetype in TEXT_MIMETYPES:
            return raw.decode("utf-8", errors="replace").strip()
        if mimetype == "application/pdf":
            text = self._pdf_text(raw)
            if text:
                return text
        # Text Odoo extracted for search (e.g. Office files with the
        # attachment_indexation module); otherwise only the main type is stored
        index = (attachment.index_content or "").strip()
        if index and index not in NO_TEXT_INDEX:
            return index
        return ""

    @api.model
    def _pdf_text(self, raw):
        try:
            from odoo.tools.pdf import PdfFileReader

            reader = PdfFileReader(io.BytesIO(raw), strict=False)
            texts = []
            for page in list(reader.pages)[:100]:
                extract = getattr(page, "extract_text", None) or page.extractText
                texts.append(extract() or "")
            return "\n\n".join(texts).strip()
        except Exception:
            _logger.info("AI Discuss Bot: no text extracted from a PDF", exc_info=True)
            return ""

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------

    @api.model
    def _cron_process_requests(self, limit=20):
        requests = self.search([("state", "=", "pending")], order="id", limit=limit)
        # No commits while tests run: the test framework forbids them (HTTP
        # tests switch the registry to test mode, other tests flag the thread)
        in_test_mode = self.env.registry.in_test_mode() or getattr(
            threading.current_thread(), "testing", False
        )
        for request in requests:
            # Counted in its own transaction, so a run killed mid-answer still
            # leaves a trace and the request is not retried forever.
            request.attempts += 1
            if not in_test_mode:
                self.env.cr.commit()  # pylint: disable=invalid-commit
            request._process()
            if not in_test_mode:
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
        if self.attempts > MAX_ATTEMPTS:
            self._fail(
                record,
                f"Processing was interrupted {self.attempts - 1} times, probably "
                "by the cron time limit (limit_time_real_cron) while the AI was "
                "answering.",
            )
            return
        try:
            with self.env.cr.savepoint():
                connection = bot.ai_bot_connection_id
                if not connection:
                    raise ValueError(f"User {bot.name} has no AI Bot Connection.")
                run_kwargs = {
                    "messages": self._prepare_messages(json.loads(self.messages_json)),
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
                content = THINK_BLOCK.sub("", content or "").strip()
                if not content:
                    raise ValueError("The AI returned an empty answer.")
        except Exception as error:
            _logger.exception("AI Discuss Bot: request %s failed", self.id)
            self._fail(record, str(error))
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
        self._post_reply(record, content, self.action_ids)

    def _fail(self, record, error):
        self.write({"state": "error", "error": error})
        self._post_reply(
            record,
            _(
                "Sorry, I could not answer right now. An administrator can "
                "see the reason in Settings > Technical > AI > "
                "AI Bot Requests."
            ),
        )

    def _post_reply(self, record, text, actions=None):
        """Post ``text`` (Markdown) as the bot, followed by the confirmation
        links of ``actions``. In a record chatter the answer is an internal
        note that notifies the user who asked, so nothing is ever emailed to
        the record's external followers."""
        self.ensure_one()
        bot = self.bot_user_id.sudo()
        record = record.sudo().with_context(
            ai_discuss_bot_skip=True,
            mail_create_nosubscribe=True,
            mail_post_autofollow=False,
        )
        body = markdown_to_html(text)
        pending = (actions or self.env["ai.discuss.bot.action"]).filtered(
            lambda action: action.state == "proposed"
        )
        if pending:
            body += self._actions_html(pending)
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
        if pending:
            pending.message_id = message
        return message

    def _actions_html(self, actions):
        self.ensure_one()
        # Translated here: inside the generator _() cannot find the language
        label = self.env._("Review and confirm")
        items = Markup("").join(
            Markup('<li>{} - <a href="{}">{}</a></li>').format(
                action.name, action._get_review_url(), label
            )
            for action in actions
        )
        title = _("Waiting for confirmation by %(user)s:", user=self.asker_user_id.name)
        return Markup(
            '<div class="o_ai_bot_actions"><p><strong>{}</strong></p><ul>{}</ul></div>'
        ).format(title, items)
