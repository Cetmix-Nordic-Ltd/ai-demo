# Copyright 2026 Cetmix Nordic Ltd
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import json
from unittest.mock import patch

from odoo.tests import new_test_user, tagged

from odoo.addons.base.tests.common import BaseCommon

# 1x1 transparent PNG
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8"
    "AAAAASUVORK5CYII="
)


@tagged("post_install", "-at_install")
class TestAttachments(BaseCommon):
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
            cls.env, notification_type="inbox", login="att_tester", name="Att Tester"
        )
        cls.Request = cls.env["ai.discuss.bot.request"]

    def _post_with_files(self, files):
        chat = (
            self.env["discuss.channel"]
            .with_user(self.user)
            .channel_get([self.bot.partner_id.id])
        )
        attachments = (
            self.env["ir.attachment"]
            .sudo()
            .create(
                [
                    {
                        "name": name,
                        "raw": raw,
                        "mimetype": mimetype,
                        "res_model": "discuss.channel",
                        "res_id": chat.id,
                    }
                    for name, raw, mimetype in files
                ]
            )
        )
        chat.with_user(self.user).message_post(
            body="Mitä liitteissä on?",
            message_type="comment",
            attachment_ids=attachments.ids,
        )
        return self.Request.search(
            [("res_model", "=", "discuss.channel"), ("res_id", "=", chat.id)]
        )

    def _sent_messages(self, request):
        with patch.object(
            type(self.env["ai.connection"]),
            "_run",
            autospec=True,
            return_value=("Ok", 1, 1, 1),
        ) as run:
            self.Request._cron_process_requests()
        self.assertEqual(request.state, "done", request.error)
        return run.call_args.kwargs["messages"]

    def test_files_mode_sends_image_pdf_and_text(self):
        request = self._post_with_files(
            [
                ("kuva.png", PNG, "image/png"),
                ("lasku.pdf", b"%PDF-1.4 test", "application/pdf"),
                ("muistio.txt", b"Toimitus perjantaina", "text/plain"),
            ]
        )
        stored = json.loads(request.messages_json)[-1]
        self.assertEqual(len(stored["attachment_ids"]), 3)
        self.assertIn("[Attachment: lasku.pdf]", stored["content"])

        last = self._sent_messages(request)[-1]
        self.assertNotIn("attachment_ids", last)
        types = [part["type"] for part in last["content"]]
        self.assertEqual(types, ["text", "image_url", "file", "text"])
        self.assertTrue(
            last["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
        )
        self.assertTrue(
            last["content"][2]["file"]["file_data"].startswith(
                "data:application/pdf;base64,"
            )
        )
        self.assertIn("Toimitus perjantaina", last["content"][3]["text"])

    def test_text_mode_skips_images(self):
        self.bot.ai_bot_attachment_mode = "text"
        request = self._post_with_files(
            [
                ("kuva.png", PNG, "image/png"),
                ("muistio.txt", b"Tervehdys", "text/plain"),
            ]
        )
        last = self._sent_messages(request)[-1]
        texts = [part["text"] for part in last["content"]]
        self.assertTrue(
            any("kuva.png" in text and "not sent" in text for text in texts)
        )
        self.assertTrue(any("Tervehdys" in text for text in texts))

    def test_none_mode_and_size_limit(self):
        self.bot.ai_bot_max_attachment_mb = 1
        big = b"x" * (2 * 1024 * 1024)
        request = self._post_with_files([("iso.txt", big, "text/plain")])
        stored = json.loads(request.messages_json)[-1]
        self.assertNotIn("attachment_ids", stored)
        self.assertIn("[Attachment not sent: iso.txt]", stored["content"])

        self.bot.ai_bot_attachment_mode = "none"
        conversation = self.Request._build_conversation(
            self.env["discuss.channel"].browse(request.res_id),
            request.message_id,
            self.bot,
            self.user,
        )
        self.assertNotIn("Files attached", conversation[0]["content"])
        self.assertIn("[Attachment not sent: iso.txt]", conversation[-1]["content"])
