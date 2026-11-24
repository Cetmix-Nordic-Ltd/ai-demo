# Copyright 2026 Cetmix Nordic Ltd
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import date

from odoo import Command
from odoo.tests import freeze_time, new_test_user, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestCashFlowTools(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.company_data["company"]
        cls.bank = cls.company_data["default_journal_bank"].default_account_id
        cls.receivable = cls.company_data["default_account_receivable"]
        cls.payable = cls.company_data["default_account_payable"]
        cls.Tools = cls.env["ai.discuss.bot.tools"].with_context(
            ai_discuss_bot_user_id=cls.env.user.id
        )

    def _entry(self, day, lines):
        move = self.env["account.move"].create(
            {
                "move_type": "entry",
                "date": day,
                "journal_id": self.company_data["default_journal_misc"].id,
                "line_ids": [
                    Command.create(
                        {
                            "account_id": account.id,
                            "balance": balance,
                            "partner_id": self.partner_a.id,
                            "name": "test",
                        }
                    )
                    for account, balance in lines
                ],
            }
        )
        move.action_post()
        return move

    def _invoice(self, move_type, invoice_date, amount):
        return self.init_invoice(
            move_type, invoice_date=invoice_date, amounts=[amount], post=True
        )

    def _pay(self, invoice, day):
        return (
            self.env["account.payment.register"]
            .with_context(active_model="account.move", active_ids=invoice.ids)
            .create({"payment_date": day})
            ._create_payments()
        )

    @freeze_time("2026-11-10")
    def test_forecast(self):
        self._entry(
            date(2026, 11, 1),
            [
                (self.bank, 10000.0),
                (self.company_data["default_account_revenue"], -10000.0),
            ],
        )
        invoice = self._invoice("out_invoice", date(2026, 11, 20), 1000.0)
        bill = self._invoice("in_invoice", date(2026, 10, 1), 300.0)
        self.env["mis.cash_flow.forecast_line"].create(
            {
                "date": date(2026, 12, 1),
                "account_id": self.payable.id,
                "name": "Rent",
                "balance": -500.0,
                "company_id": self.company.id,
            }
        )
        result = self.Tools._ai_bot_cash_flow_forecast(granularity="week", periods=4)
        self.assertNotIn("error", result)
        self.assertEqual(result["opening_cash"], 10000.0)
        weeks = result["periods"]
        self.assertEqual([w["start"] for w in weeks][0], "2026-11-09")
        self.assertEqual(weeks[0]["out"], {"vendor_bills": -bill.amount_total})
        self.assertEqual(weeks[1]["in"], {"customer_invoices": invoice.amount_total})
        self.assertEqual(weeks[3]["out"], {"forecast_manual": -500.0})
        self.assertEqual(
            weeks[3]["closing_balance"],
            10000.0 - bill.amount_total + invoice.amount_total - 500.0,
        )
        self.assertEqual(
            result["overdue_included_in_first_period"],
            {"vendor_bills": -bill.amount_total},
        )
        self.assertEqual(result["lowest_balance"]["period_start"], "2026-11-09")

    @freeze_time("2026-11-10")
    def test_forecast_nets_categories(self):
        invoice = self._invoice("out_invoice", date(2026, 11, 20), 1000.0)
        refund = self._invoice("out_refund", date(2026, 11, 20), 200.0)
        result = self.Tools._ai_bot_cash_flow_forecast(granularity="week", periods=2)
        week = result["periods"][1]
        self.assertEqual(
            week["in"],
            {"customer_invoices": invoice.amount_total - refund.amount_total},
        )
        self.assertFalse(week["out"])

    @freeze_time("2026-11-10")
    def test_forecast_realistic(self):
        paid = self._invoice("out_invoice", date(2026, 9, 1), 100.0)
        self._pay(paid, date(2026, 9, 11))
        self.assertIn(paid.payment_state, ("paid", "in_payment"))
        invoice = self._invoice("out_invoice", date(2026, 11, 20), 1000.0)
        result = self.Tools._ai_bot_cash_flow_forecast(
            granularity="week", periods=4, realistic=True
        )
        self.assertEqual(result["average_payment_delay_days"], 10.0)
        weeks = result["periods"]
        self.assertFalse(weeks[1]["in"])
        self.assertEqual(weeks[3]["in"], {"customer_invoices": invoice.amount_total})

    @freeze_time("2026-11-10")
    def test_forecast_input(self):
        result = self.Tools._ai_bot_cash_flow_forecast(granularity="day")
        self.assertIn("error", result)
        result = self.Tools._ai_bot_cash_flow_forecast(granularity="month", periods=99)
        self.assertEqual(len(result["periods"]), 24)
        self.assertEqual(result["periods"][0]["start"], "2026-11-01")

    @freeze_time("2026-11-10")
    def test_history(self):
        tax_account = (
            self.company_data["default_tax_sale"]
            .invoice_repartition_line_ids.filtered(
                lambda r: r.repartition_type == "tax"
            )
            .account_id
        )
        self.assertTrue(tax_account)
        self._entry(
            date(2026, 10, 5), [(self.bank, 1000.0), (self.receivable, -1000.0)]
        )
        self._entry(date(2026, 10, 6), [(self.bank, -400.0), (self.payable, 400.0)])
        self._entry(date(2026, 10, 7), [(self.bank, -200.0), (tax_account, 200.0)])
        other_bank = self.bank.copy({"code": "101999"})
        self._entry(date(2026, 10, 8), [(self.bank, -50.0), (other_bank, 50.0)])
        # A payment through the outstanding receipts account, matched with
        # the bank line later: followed back to the customer
        invoice = self._invoice("out_invoice", date(2026, 10, 1), 300.0)
        payment = self._pay(invoice, date(2026, 11, 2))
        outstanding = payment.move_id.line_ids.filtered(
            lambda line: line.account_id.reconcile
            and line.account_id.account_type not in ("asset_receivable",)
        )
        self.assertTrue(outstanding)
        bank_move = self._entry(
            date(2026, 11, 3),
            [
                (self.bank, invoice.amount_total),
                (outstanding.account_id, -invoice.amount_total),
            ],
        )
        (
            outstanding
            | bank_move.line_ids.filtered(
                lambda line: line.account_id == outstanding.account_id
            )
        ).reconcile()
        result = self.Tools._ai_bot_cash_flow_history(months=2)
        self.assertNotIn("error", result)
        october, november = result["months"]
        self.assertEqual(october["month"], "2026-10")
        self.assertEqual(october["in"], {"customers": 1000.0})
        self.assertEqual(october["out"], {"suppliers": -400.0, "taxes": -200.0})
        self.assertEqual(october["internal_transfers_net"], 0.0)
        self.assertEqual(november["in"], {"customers": invoice.amount_total})
        self.assertEqual(october["opening_cash"], 0.0)
        self.assertEqual(november["closing_cash"], 400.0 + invoice.amount_total)

    def test_rights_of_the_asker(self):
        user = new_test_user(
            self.env,
            login="cash_flow_plain",
            notification_type="inbox",
            company_id=self.company.id,
            company_ids=[Command.set(self.company.ids)],
        )
        tools = self.env["ai.discuss.bot.tools"].with_context(
            ai_discuss_bot_user_id=user.id
        )
        self.assertIn("error", tools._ai_bot_cash_flow_history(months=1))
        self.assertIn("error", tools._ai_bot_cash_flow_forecast())

    def test_tools_on_bot(self):
        bot = self.env.ref("ai_connection_discuss_bot.res_users_ai_bot_claude")
        names = bot.ai_bot_tool_ids.mapped("name")
        self.assertIn("cash_flow_forecast", names)
        self.assertIn("cash_flow_history", names)
