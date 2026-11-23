# Copyright 2026 Cetmix Nordic Ltd
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json
from unittest.mock import patch

from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestDiscussBot(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.connection = cls.env["ai.connection"].create(
            {
                "name": "Test gateway",
                "kind": "openai",
                "url": "http://litellm.invalid:4000",
                "model": "claude-sonnet",
            }
        )
        cls.bot = cls.env.ref("ai_connection_discuss_bot.user_ai_bot_claude")
        cls.bot.ai_bot_connection_id = cls.connection
        cls.user = new_test_user(cls.env, login="bot_tester", name="Bot Tester")
        cls.other = new_test_user(cls.env, login="bot_other", name="Other User")
        cls.Request = cls.env["ai.discuss.bot.request"]

    def _patch_run(self, **kwargs):
        if "side_effect" not in kwargs:
            kwargs.setdefault("return_value", ("Hei!", 12, 3, 1))
        return patch.object(
            type(self.env["ai.connection"]), "_run", autospec=True, **kwargs
        )

    def _bot_messages(self, record):
        return self.env["mail.message"].search(
            [
                ("model", "=", record._name),
                ("res_id", "=", record.id),
                ("author_id", "=", self.bot.partner_id.id),
            ]
        )

    def test_direct_chat_is_answered(self):
        chat = (
            self.env["discuss.channel"]
            .with_user(self.user)
            .channel_get([self.bot.partner_id.id])
        )
        chat.with_user(self.user).message_post(
            body="Mikä on Suomen pääkaupunki?", message_type="comment"
        )
        request = self.Request.search([("res_id", "=", chat.id)])
        self.assertEqual(len(request), 1)
        self.assertEqual(request.state, "pending")
        conversation = json.loads(request.messages_json)
        self.assertEqual(conversation[0]["role"], "system")
        self.assertEqual(conversation[-1]["role"], "user")
        self.assertIn(
            "Bot Tester: Mikä on Suomen pääkaupunki?", conversation[-1]["content"]
        )

        with self._patch_run() as run:
            self.Request._cron_process_requests()
        run.assert_called_once()
        self.assertEqual(request.state, "done")
        self.assertEqual(request.prompt_tokens, 12)
        answers = self._bot_messages(chat)
        self.assertEqual(len(answers), 1)
        self.assertIn("Hei!", answers.body)
        # The bot's own answer must not queue a new request
        self.assertEqual(self.Request.search_count([("res_id", "=", chat.id)]), 1)

    def test_history_keeps_roles(self):
        chat = (
            self.env["discuss.channel"]
            .with_user(self.user)
            .channel_get([self.bot.partner_id.id])
        )
        chat.with_user(self.user).message_post(
            body="Ensimmäinen", message_type="comment"
        )
        with self._patch_run(return_value=("Vastaus 1", 1, 1, 1)):
            self.Request._cron_process_requests()
        chat.with_user(self.user).message_post(body="Toinen", message_type="comment")
        request = self.Request.search(
            [("res_id", "=", chat.id), ("state", "=", "pending")]
        )
        roles = [m["role"] for m in json.loads(request.messages_json)]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])

    def test_group_needs_mention(self):
        group = (
            self.env["discuss.channel"]
            .with_user(self.user)
            .create_group([self.other.partner_id.id, self.bot.partner_id.id])
        )
        group.with_user(self.user).message_post(
            body="Hei kaikki", message_type="comment"
        )
        self.assertFalse(self.Request.search([("res_id", "=", group.id)]))
        group.with_user(self.user).message_post(
            body="@Claude tiivistä",
            message_type="comment",
            partner_ids=self.bot.partner_id.ids,
        )
        self.assertEqual(self.Request.search_count([("res_id", "=", group.id)]), 1)

    def test_record_chatter_mention_answers_with_note(self):
        partner = self.env["res.partner"].create({"name": "Asiakas Oy"})
        partner.message_post(body="Asiakas kysyi toimitusajasta", message_type="email")
        partner.with_user(self.user).message_post(
            body="@Claude mitä asiakas kysyi?",
            message_type="comment",
            subtype_xmlid="mail.mt_note",
            partner_ids=self.bot.partner_id.ids,
        )
        request = self.Request.search(
            [("res_model", "=", "res.partner"), ("res_id", "=", partner.id)]
        )
        self.assertEqual(len(request), 1)
        conversation = json.loads(request.messages_json)
        self.assertIn("Asiakas Oy", conversation[0]["content"])
        self.assertIn("toimitusajasta", json.dumps(conversation, ensure_ascii=False))

        with self._patch_run(return_value=("Toimitusajasta.", 5, 2, 1)):
            self.Request._cron_process_requests()
        answer = self._bot_messages(partner)
        self.assertEqual(len(answer), 1)
        self.assertEqual(answer.subtype_id, self.env.ref("mail.mt_note"))
        self.assertIn(self.user.partner_id, answer.partner_ids)

    def test_chatter_without_mention_is_ignored(self):
        partner = self.env["res.partner"].create({"name": "Asiakas Oy"})
        partner.with_user(self.user).message_post(
            body="Muistiinpano", message_type="comment"
        )
        self.assertFalse(self.Request.search([("res_model", "=", "res.partner")]))

    def test_error_is_reported(self):
        chat = (
            self.env["discuss.channel"]
            .with_user(self.user)
            .channel_get([self.bot.partner_id.id])
        )
        chat.with_user(self.user).message_post(body="Hei", message_type="comment")
        with self._patch_run(side_effect=RuntimeError("gateway down")):
            self.Request._cron_process_requests()
        request = self.Request.search([("res_id", "=", chat.id)])
        self.assertEqual(request.state, "error")
        self.assertIn("gateway down", request.error)
        self.assertEqual(len(self._bot_messages(chat)), 1)

    def test_bot_without_connection_is_ignored(self):
        self.bot.ai_bot_connection_id = False
        chat = (
            self.env["discuss.channel"]
            .with_user(self.user)
            .channel_get([self.bot.partner_id.id])
        )
        chat.with_user(self.user).message_post(body="Hei", message_type="comment")
        self.assertFalse(self.Request.search([("res_id", "=", chat.id)]))
