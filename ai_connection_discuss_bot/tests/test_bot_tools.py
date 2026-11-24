# Copyright 2026 Cetmix Nordic Ltd
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json
from unittest.mock import patch

from odoo.tests import new_test_user, tagged

from odoo.addons.base.tests.common import BaseCommon


class FakeClient:
    """Plays back prepared AI answers, like OpenaiClient.handle_message."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.received = []

    def handle_message(self, messages=None, **kwargs):
        self.received.append(list(messages))
        return self.steps.pop(0)


def tool_call_step(name, arguments, call_id="call_1"):
    return {
        "message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ],
        },
        "tool_calls": [{"name": name, "arguments": arguments, "id": call_id}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def answer_step(text):
    return {
        "message": {"role": "assistant", "content": text},
        "tool_calls": [],
        "usage": {"prompt_tokens": 20, "completion_tokens": 7},
    }


@tagged("post_install", "-at_install")
class TestBotTools(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
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
            cls.env, notification_type="inbox", login="tool_tester", name="Tool Tester"
        )
        cls.customer = cls.env["res.partner"].create(
            {"name": "Tool Test Oy", "is_company": True, "city": "Tampere"}
        )
        cls.Tools = cls.env["ai.discuss.bot.tools"]

    def _as_user(self, user=None):
        return self.Tools.with_context(ai_discuss_bot_user_id=(user or self.user).id)

    # ------------------------------------------------------------------
    # Tool functions
    # ------------------------------------------------------------------

    def test_default_tools_are_safe_and_defined(self):
        tools = self.bot._ai_bot_safe_tools()
        self.assertEqual(len(tools), 8)
        self.assertEqual(len(self.bot._ai_bot_write_tools()), 2)
        for tool in tools:
            definition = tool._get_tool_definition()
            self.assertEqual(definition["inputSchema"]["type"], "object")

    def test_unsafe_tool_is_never_used(self):
        post_message = self.env.ref("ai_tool.post_message")
        self.bot.sudo().write({"ai_bot_tool_ids": [(4, post_message.id)]})
        self.assertNotIn(post_message, self.bot._ai_bot_safe_tools())

    def test_tools_refuse_without_asking_user(self):
        result = self.Tools._ai_bot_search_records(model="res.partner")
        self.assertIn("error", result)
        self.assertIn("AccessError", result["error"])

    def test_search_records(self):
        result = self._as_user()._ai_bot_search_records(
            model="res.partner",
            domain='[["name", "=", "Tool Test Oy"]]',
            fields=["name", "city", "is_company"],
        )
        self.assertNotIn("error", result, result)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["records"][0]["city"], "Tampere")
        self.assertIs(result["records"][0]["is_company"], True)
        json.dumps(result)  # must be serialisable for the AI

    def test_search_records_bad_input_returns_error(self):
        tools = self._as_user()
        self.assertIn("error", tools._ai_bot_search_records(model="no.such.model"))
        self.assertIn(
            "error",
            tools._ai_bot_search_records(model="res.partner", domain="[[broken"),
        )
        self.assertIn(
            "error",
            tools._ai_bot_search_records(model="res.partner", fields=["no_such_field"]),
        )

    def test_user_rights_apply(self):
        # A plain employee may not read system parameters
        result = self._as_user()._ai_bot_search_records(model="ir.config_parameter")
        self.assertIn("AccessError", result["error"])
        found = self._as_user()._ai_bot_find_models(query="config_parameter")
        self.assertNotIn(
            "ir.config_parameter", [model["model"] for model in found["models"]]
        )

    def test_aggregate_records(self):
        result = self._as_user()._ai_bot_aggregate_records(
            model="res.partner",
            domain='[["name", "=", "Tool Test Oy"]]',
            groupby=["is_company"],
            aggregates=["__count"],
        )
        self.assertNotIn("error", result, result)
        self.assertEqual(result["groups"], [{"is_company": True, "__count": 1}])

    def test_get_model_fields(self):
        result = self._as_user()._ai_bot_get_model_fields(model="res.partner")
        names = [field["name"] for field in result["fields"]]
        self.assertIn("city", names)
        self.assertNotIn("message_ids", names)

    def test_get_current_record(self):
        result = self._as_user()._ai_bot_get_current_record(record=self.customer)
        self.assertEqual(result["id"], self.customer.id)
        self.assertEqual(result["values"]["city"], "Tampere")
        channel = self.env["discuss.channel"].create({"name": "General test"})
        self.assertIn(
            "error", self._as_user()._ai_bot_get_current_record(record=channel)
        )

    # ------------------------------------------------------------------
    # Through the bot and ai_connection's tool loop
    # ------------------------------------------------------------------

    def test_bot_uses_tool_with_asker_rights(self):
        chat = (
            self.env["discuss.channel"]
            .with_user(self.user)
            .channel_get([self.bot.partner_id.id])
        )
        chat.with_user(self.user).message_post(
            body="Missä kaupungissa Tool Test Oy on?", message_type="comment"
        )
        request = self.env["ai.discuss.bot.request"].search(
            [("res_model", "=", "discuss.channel"), ("res_id", "=", chat.id)]
        )
        self.assertEqual(request.asker_user_id, self.user)
        self.assertIn("search_records", json.loads(request.messages_json)[0]["content"])

        client = FakeClient(
            [
                tool_call_step(
                    "search_records",
                    {
                        "model": "res.partner",
                        "domain": '[["name", "=", "Tool Test Oy"]]',
                        "fields": ["city"],
                    },
                ),
                answer_step("Tampereella."),
            ]
        )
        seen_users = []
        original = type(self.Tools)._ai_bot_user_env

        def spy_user_env(tools_self):
            env = original(tools_self)
            seen_users.append(env.uid)
            return env

        Connection = type(self.env["ai.connection"])
        with (
            patch.object(Connection, "_get_client_openai", lambda self, tools: client),
            patch.object(type(self.Tools), "_ai_bot_user_env", spy_user_env),
        ):
            self.env["ai.discuss.bot.request"]._cron_process_requests()

        self.assertEqual(request.state, "done", request.error)
        self.assertEqual(request.response, "Tampereella.")
        self.assertEqual(request.iterations, 2)
        self.assertIn("search_records", request.tool_log)
        self.assertIn("-> ok", request.tool_log)
        self.assertEqual(seen_users, [self.user.id])
        # The tool result was sent back to the AI
        tool_message = client.received[1][-1]
        self.assertEqual(tool_message["role"], "tool")
        self.assertIn("Tampere", tool_message["content"])
