# Copyright 2026 Cetmix Nordic Ltd
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import BaseCase, tagged

from ..tools.markdown import markdown_to_html


@tagged("post_install", "-at_install")
class TestMarkdown(BaseCase):
    def test_inline_and_lists(self):
        html = markdown_to_html("**Myynti**: *noin* `x`\n\n- A\n  - A1\n1. eka")
        self.assertIn("<strong>Myynti</strong>", html)
        self.assertIn("<em>noin</em>", html)
        self.assertIn("<code>x</code>", html)
        self.assertIn("<ul><li>A<ul><li>A1</li></ul></li></ul>", html)
        self.assertIn("<ol><li>eka</li></ol>", html)

    def test_table(self):
        html = markdown_to_html("| A | B |\n|---|--:|\n| 1 | 2 |")
        self.assertIn("<table", html)
        self.assertIn('<td style="text-align: right">2</td>', html)

    def test_escaping_and_links(self):
        html = markdown_to_html(
            "<script>x</script> [ok](/mail/view?model=res.partner&res_id=1) "
            "[bad](javascript:alert) [other](//evil.example) snake_case_name"
        )
        self.assertNotIn("<script>", html)
        self.assertIn(
            '<a href="/mail/view?model=res.partner&amp;res_id=1">ok</a>', html
        )
        self.assertNotIn("javascript:", html.split("bad")[0])
        self.assertNotIn('href="//evil', html)
        self.assertIn("snake_case_name", html)

    def test_code_block_is_not_formatted(self):
        html = markdown_to_html("```\n**not bold** <b>\n```")
        self.assertIn("<pre><code>**not bold** &lt;b&gt;</code></pre>", html)
