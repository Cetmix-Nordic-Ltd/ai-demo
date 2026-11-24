# Copyright 2026 Cetmix Nordic Ltd
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import re
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import HttpCase, new_test_user, tagged

from odoo.addons.base.tests.common import DISABLED_MAIL_CONTEXT

from .test_bot_tools import FakeClient, answer_step, tool_call_step


@tagged("post_install", "-at_install")
class TestProposals(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, **DISABLED_MAIL_CONTEXT))
        cls.connection = cls.env["ai.connection"].create(
            {
                "name": "Test gateway",
                "kind": "openai",
                "url": "http://litellm.invalid:4000",
                "model": "claude-sonnet",
            }
        )
        cls.bot = cls.env.ref("ai_connection_discuss_bot.res_users_ai_bot_claude")
        cls.bot.ai_bot_connection_id = cls.connection
        cls.user = new_test_user(
            cls.env,
            notification_type="inbox",
            login="prop_tester",
            password="prop_tester_pw_1",
            name="Prop Tester",
            groups="base.group_user,base.group_partner_manager",
        )
        cls.colleague = new_test_user(
            cls.env,
            notification_type="inbox",
            login="prop_colleague",
            password="prop_colleague_pw_1",
            name="Maija Kollega",
        )
        cls.customer = cls.env["res.partner"].create({"name": "Ehdotus Oy"})
        cls.Request = cls.env["ai.discuss.bot.request"]

    def _ask_on_customer(self, steps):
        """Mention the bot on the customer's chatter and let FakeClient play
        ``steps``. Returns the processed request."""
        self.customer.with_user(self.user).message_post(
            body="@Claude hoida tämä",
            message_type="comment",
            subtype_xmlid="mail.mt_note",
            partner_ids=self.bot.partner_id.ids,
        )
        request = self.Request.search(
            [("res_model", "=", "res.partner"), ("res_id", "=", self.customer.id)]
        )
        client = FakeClient(steps)
        Connection = type(self.env["ai.connection"])
        with patch.object(Connection, "_get_client_openai", lambda self, tools: client):
            self.Request._cron_process_requests()
        self.assertEqual(request.state, "done", request.error)
        return request

    def _bot_messages(self):
        return self.env["mail.message"].search(
            [
                ("model", "=", "res.partner"),
                ("res_id", "=", self.customer.id),
                ("author_id", "=", self.bot.partner_id.id),
            ],
            order="id",
        )

    def test_note_is_posted_only_after_confirmation(self):
        request = self._ask_on_customer(
            [
                tool_call_step("propose_note", {"body": "Asiakas soitti **tänään**."}),
                answer_step("Ehdotin muistiinpanoa, vahvista linkistä."),
            ]
        )
        self.assertIn("-> ok", request.tool_log)
        action = request.action_ids
        self.assertEqual(len(action), 1)
        self.assertEqual(action.state, "proposed")
        self.assertEqual(action.asker_user_id, self.user)
        notes_before = self.customer.message_ids
        reply = self._bot_messages()[-1]
        self.assertIn(f"/ai_discuss_bot/action/{action.id}?token=", reply.body)
        self.assertIn("o_ai_bot_actions", reply.body)

        action._confirm()
        self.assertEqual(action.state, "done", action.error)
        new_messages = self.customer.message_ids - notes_before
        note = new_messages.filtered(lambda m: m.author_id == self.user.partner_id)
        self.assertEqual(len(note), 1)
        self.assertIn("<strong>tänään</strong>", note.body)
        self.assertEqual(note.subtype_id, self.env.ref("mail.mt_note"))
        # The bot reports the outcome in the conversation
        self.assertIn("Done", self._bot_messages()[-1].body)
        # The confirmation links are not sent back to the AI
        self.assertNotIn(
            "Review and confirm",
            self.Request._message_text(reply),
        )

    def test_activity_proposal_and_cancel(self):
        request = self._ask_on_customer(
            [
                tool_call_step(
                    "propose_activity",
                    {
                        "summary": "Soita takaisin",
                        "date_deadline": "2030-01-15",
                        "assigned_to": "Maija",
                    },
                ),
                answer_step("Ehdotin tehtävää."),
            ]
        )
        action = request.action_ids
        self.assertIn("Maija Kollega", action.name)
        action._cancel()
        self.assertEqual(action.state, "cancelled")
        self.assertFalse(self.customer.activity_ids)

    def test_activity_is_created_on_confirm(self):
        request = self._ask_on_customer(
            [
                tool_call_step(
                    "propose_activity",
                    {"summary": "Soita takaisin", "date_deadline": "2030-01-15"},
                ),
                answer_step("Ehdotin tehtävää."),
            ]
        )
        request.action_ids._confirm()
        activity = self.customer.activity_ids
        self.assertEqual(len(activity), 1)
        self.assertEqual(activity.summary, "Soita takaisin")
        self.assertEqual(activity.user_id, self.user)
        self.assertEqual(str(activity.date_deadline), "2030-01-15")

    def test_invalid_proposals_return_errors(self):
        request = self._ask_on_customer(
            [
                tool_call_step(
                    "propose_activity",
                    {"summary": "X", "assigned_to": "Ei Ketään Tällaista"},
                ),
                answer_step("En voinut."),
            ]
        )
        self.assertFalse(request.action_ids)
        self.assertIn("No internal user", request.tool_log)

    def test_expired_proposal_is_not_executed(self):
        request = self._ask_on_customer(
            [
                tool_call_step("propose_note", {"body": "Vanha"}),
                answer_step("Ehdotin."),
            ]
        )
        action = request.action_ids
        self.env.cr.execute(
            "UPDATE ai_discuss_bot_action SET create_date = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(days=2), action.id),
        )
        action.invalidate_recordset(["create_date"])
        action._confirm()
        self.assertEqual(action.state, "expired")

    def test_review_page_access(self):
        request = self._ask_on_customer(
            [
                tool_call_step("propose_note", {"body": "Sivun kautta"}),
                answer_step("Ehdotin."),
            ]
        )
        action = request.action_ids
        url = f"/ai_discuss_bot/action/{action.id}?token={action.sudo().token}"

        self.authenticate("prop_colleague", "prop_colleague_pw_1")
        self.assertEqual(self.url_open(url).status_code, 404)

        self.authenticate("prop_tester", "prop_tester_pw_1")
        bad = self.url_open(f"/ai_discuss_bot/action/{action.id}?token=wrong")
        self.assertEqual(bad.status_code, 404)
        page = self.url_open(url)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Sivun kautta", page.text)
        self.assertEqual(action.state, "proposed")  # opening changes nothing

        response = self.url_open(
            f"/ai_discuss_bot/action/{action.id}/decide",
            data={
                "token": action.sudo().token,
                "decision": "confirm",
                "csrf_token": re.search(
                    r'name="csrf_token" value="([^"]+)"', page.text
                ).group(1),
            },
        )
        self.assertEqual(response.status_code, 200)
        action.invalidate_recordset(["state"])
        self.assertEqual(action.state, "done", action.error)
