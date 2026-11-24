# Copyright 2026 Cetmix Nordic Ltd
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from collections import defaultdict
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import fields, models

from odoo.addons.ai_connection_discuss_bot.models.ai_discuss_bot_tools import (
    AiToolInputError,
)
from odoo.addons.ai_tool.tools import aitool

LIQUIDITY_TYPES = ("asset_cash", "liability_credit_card")
OPEN_ITEM_TYPES = ("asset_receivable", "liability_payable")
MAX_PERIODS = {"week": 26, "month": 24}
MAX_HISTORY_MONTHS = 24
# Paid customer invoices looked at for the average payment delay
DELAY_HISTORY_DAYS = 365
DELAY_MAX_LINES = 5000
HISTORY_CATEGORIES = (
    "customers",
    "suppliers",
    "taxes",
    "wages_and_other_liabilities",
    "other_operating",
    "investments",
    "financing",
    "other",
)
CATEGORY_BY_ACCOUNT_TYPE = {
    "asset_receivable": "customers",
    "liability_payable": "suppliers",
    "liability_current": "wages_and_other_liabilities",
    "income": "other_operating",
    "income_other": "other_operating",
    "expense": "other_operating",
    "expense_direct_cost": "other_operating",
    "expense_depreciation": "other_operating",
    "asset_fixed": "investments",
    "asset_non_current": "investments",
    "liability_non_current": "financing",
    "equity": "financing",
    "equity_unaffected": "financing",
    "asset_cash": "internal_transfer",
    "liability_credit_card": "internal_transfer",
}


class AiDiscussBotTools(models.AbstractModel):
    _inherit = "ai.discuss.bot.tools"

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    @aitool(
        input_schema={
            "granularity": {
                "type": "string",
                "enum": ["week", "month"],
                "description": "Length of a period: 'week' (default) or 'month'.",
            },
            "periods": {
                "type": "integer",
                "description": "Number of periods from the current one: at most "
                "26 weeks or 24 months. Default 13 weeks or 12 months.",
            },
            "realistic": {
                "type": "boolean",
                "description": "Move open customer invoices by each customer's "
                "average payment delay of the past year. Default false: "
                "invoices are paid on their due date.",
            },
        },
        output_schema={"periods": {"type": "array"}},
    )
    def _ai_bot_cash_flow_forecast(
        self, granularity=None, periods=None, realistic=False, **kwargs
    ):
        def run(env):
            is_realistic = realistic in (True, 1, "true", "True")
            return self._cf_forecast(env, granularity or "week", periods, is_realistic)

        return self._ai_bot_call(
            "cash_flow_forecast",
            run,
            granularity=granularity,
            periods=periods,
            realistic=realistic,
        )

    @aitool(
        input_schema={
            "months": {
                "type": "integer",
                "description": "Number of months up to the current one, at most "
                f"{MAX_HISTORY_MONTHS}. Default 12.",
            },
        },
        output_schema={"months": {"type": "array"}},
    )
    def _ai_bot_cash_flow_history(self, months=None, **kwargs):
        def run(env):
            return self._cf_history(env, months)

        return self._ai_bot_call("cash_flow_history", run, months=months)

    # ------------------------------------------------------------------
    # Common
    # ------------------------------------------------------------------

    def _cf_round(self, company, amount):
        return company.currency_id.round(amount)

    def _cf_liquidity_domain(self, company):
        return [
            ("company_id", "=", company.id),
            ("parent_state", "=", "posted"),
            ("account_id.account_type", "in", LIQUIDITY_TYPES),
            ("account_id.hide_in_cash_flow", "=", False),
        ]

    def _cf_liquidity_balance(self, env, company, domain):
        groups = env["account.move.line"]._read_group(
            self._cf_liquidity_domain(company) + domain, aggregates=["balance:sum"]
        )
        return groups[0][0] if groups else 0.0

    # ------------------------------------------------------------------
    # Forecast
    # ------------------------------------------------------------------

    def _cf_periods(self, today, granularity, count):
        if granularity not in MAX_PERIODS:
            raise AiToolInputError("granularity must be 'week' or 'month'.")
        if granularity == "week":
            start = today - timedelta(days=today.weekday())
            step = relativedelta(weeks=1)
            default = 13
        else:
            start = today.replace(day=1)
            step = relativedelta(months=1)
            default = 12
        count = min(max(int(count or default), 1), MAX_PERIODS[granularity])
        return [
            (start + step * i, start + step * (i + 1) - timedelta(days=1))
            for i in range(count)
        ]

    def _cf_payment_delays(self, env, company, today):
        """Average days customers paid after the due date over the past
        year, per commercial partner and overall, weighted by amount."""
        lines = env["account.move.line"].search(
            [
                ("company_id", "=", company.id),
                ("parent_state", "=", "posted"),
                ("account_id.account_type", "=", "asset_receivable"),
                ("move_id.move_type", "=", "out_invoice"),
                ("reconciled", "=", True),
                ("date_maturity", ">=", today - timedelta(days=DELAY_HISTORY_DAYS)),
            ],
            order="date_maturity desc",
            limit=DELAY_MAX_LINES,
        )
        weighted = defaultdict(float)
        weights = defaultdict(float)
        for line in lines:
            paid_dates = line.matched_credit_ids.mapped("max_date")
            if not paid_dates or not line.date_maturity:
                continue
            delay = (max(paid_dates) - line.date_maturity).days
            amount = abs(line.balance)
            for key in (line.partner_id.commercial_partner_id.id, None):
                weighted[key] += delay * amount
                weights[key] += amount
        return {key: weighted[key] / weights[key] for key in weights if weights[key]}

    def _cf_forecast_items(self, env, company, today, date_to, realistic):
        """``(date, direction, category, amount, overdue)`` of everything
        expected to be paid by ``date_to``."""
        items = []
        delays = self._cf_payment_delays(env, company, today) if realistic else {}
        open_lines = env["account.move.line"].search(
            [
                ("company_id", "=", company.id),
                ("parent_state", "=", "posted"),
                ("account_id.account_type", "in", OPEN_ITEM_TYPES),
                ("account_id.hide_in_cash_flow", "=", False),
                ("reconciled", "=", False),
                ("amount_residual", "!=", 0.0),
            ]
        )
        for line in open_lines:
            due = line.date_maturity or line.date
            overdue = due < today
            customer = line.account_id.account_type == "asset_receivable"
            if realistic and customer:
                delay = delays.get(
                    line.partner_id.commercial_partner_id.id, delays.get(None, 0.0)
                )
                due = due + timedelta(days=round(delay))
            category = "customer_invoices" if customer else "vendor_bills"
            items.append((max(due, today), category, line.amount_residual, overdue))
        forecast_lines = env["mis.cash_flow.forecast_line"].search(
            [
                ("company_id", "=", company.id),
                ("date", "<=", date_to),
                ("account_id.hide_in_cash_flow", "=", False),
            ]
        )
        for line in forecast_lines:
            items.append(
                (
                    max(line.date, today),
                    f"forecast_{line.source}",
                    line.balance,
                    line.date < today,
                )
            )
        return items, delays

    def _cf_forecast(self, env, granularity, count, realistic):
        company = env.company
        today = fields.Date.context_today(self.with_env(env))
        periods = self._cf_periods(today, granularity, count)
        date_to = periods[-1][1]
        opening = self._cf_liquidity_balance(env, company, [("date", "<=", today)])
        items, delays = self._cf_forecast_items(env, company, today, date_to, realistic)
        rows = []
        overdue = defaultdict(float)
        balance = opening
        lowest = None
        for start, end in periods:
            # Net per category first: a customer credit note or unapplied
            # payment reduces the money in, it is not money out.
            by_category = defaultdict(float)
            for day, category, amount, is_overdue in items:
                if not start <= day <= end:
                    continue
                if is_overdue:
                    overdue[category] += amount
                by_category[category] += amount
            money_in = {k: v for k, v in by_category.items() if v > 0}
            money_out = {k: v for k, v in by_category.items() if v < 0}
            net = sum(by_category.values())
            balance += net
            row = {
                "start": str(start),
                "end": str(end),
                "in": {k: self._cf_round(company, v) for k, v in money_in.items()},
                "out": {k: self._cf_round(company, v) for k, v in money_out.items()},
                "net": self._cf_round(company, net),
                "closing_balance": self._cf_round(company, balance),
            }
            rows.append(row)
            if lowest is None or balance < lowest["closing_balance"]:
                lowest = {
                    "period_start": row["start"],
                    "closing_balance": row["closing_balance"],
                }
        last_update = (
            env["mis.cash_flow.forecast_line"]
            .sudo()
            .search(
                [("company_id", "=", company.id), ("source", "!=", "manual")],
                order="create_date desc",
                limit=1,
            )
            .create_date
        )
        assumptions = [
            "Open invoices and bills are paid on their due date; overdue ones "
            "in the current period.",
            "Draft invoices and bills are not included.",
        ]
        if realistic:
            assumptions[0] = (
                "Open customer invoices are paid after their due date by the "
                "customer's average payment delay of the past year (overall "
                "average for customers without history); vendor bills on their "
                "due date."
            )
        result = {
            "company": company.name,
            "currency": company.currency_id.name,
            "today": str(today),
            "granularity": granularity,
            "opening_cash": self._cf_round(company, opening),
            "overdue_included_in_first_period": {
                k: self._cf_round(company, v) for k, v in overdue.items()
            },
            "periods": rows,
            "lowest_balance": lowest,
            "forecast_lines_updated": str(last_update) if last_update else None,
            "assumptions": assumptions,
        }
        if realistic:
            result["average_payment_delay_days"] = round(delays.get(None, 0.0), 1)
        return result

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def _cf_tax_accounts(self, company):
        accounts = company.sudo()._get_cash_flow_vat_accounts()
        groups = (
            self.env["account.tax.group"]
            .sudo()
            .search([("company_id", "=", company.id)])
        )
        return (
            accounts | groups.tax_payable_account_id | groups.tax_receivable_account_id
        ).ids

    def _cf_category(self, account, tax_account_ids):
        if account.id in tax_account_ids:
            return "taxes"
        return CATEGORY_BY_ACCOUNT_TYPE.get(account.account_type, "other")

    def _cf_counterparts(self, liquidity_lines):
        """``{category: weight}`` of what the money of a move's liquidity
        lines paid for. A counterpart on a reconcilable clearing account
        (outstanding payments, suspense, transfer account) is followed to
        the entries it is matched with."""
        move = liquidity_lines.move_id
        others = move.line_ids - liquidity_lines
        result = []
        for line in others:
            account = line.account_id
            if account.reconcile and account.account_type not in OPEN_ITEM_TYPES:
                matched = (
                    line.matched_debit_ids.debit_move_id
                    | line.matched_credit_ids.credit_move_id
                )
                followed = matched.move_id.line_ids - matched
                if followed:
                    total = sum(abs(x.balance) for x in followed) or 1.0
                    for target in followed:
                        result.append(
                            (
                                target.account_id,
                                abs(line.balance) * abs(target.balance) / total,
                            )
                        )
                    continue
            result.append((account, abs(line.balance)))
        return result

    def _cf_history(self, env, months):
        company = env.company
        today = fields.Date.context_today(self.with_env(env))
        months = min(max(int(months or 12), 1), MAX_HISTORY_MONTHS)
        first = today.replace(day=1) - relativedelta(months=months - 1)
        tax_account_ids = self._cf_tax_accounts(company)
        lines = env["account.move.line"].search(
            self._cf_liquidity_domain(company) + [("date", ">=", first)]
        )
        by_month = defaultdict(lambda: defaultdict(float))
        for move, move_lines in lines.grouped("move_id").items():
            amount = sum(move_lines.mapped("balance"))
            month = f"{move.date:%Y-%m}"
            counterparts = self._cf_counterparts(move_lines)
            total = sum(weight for _account, weight in counterparts)
            if not counterparts or not total:
                by_month[month]["internal_transfer"] += amount
                continue
            for account, weight in counterparts:
                category = self._cf_category(account, tax_account_ids)
                by_month[month][category] += amount * weight / total
        balance = self._cf_liquidity_balance(env, company, [("date", "<", first)])
        rows = []
        totals = defaultdict(float)
        month = first
        while month <= today:
            key = f"{month:%Y-%m}"
            values = by_month.get(key, {})
            opening = balance
            balance += sum(values.values())
            money_in, money_out = {}, {}
            for category in HISTORY_CATEGORIES:
                amount = values.get(category, 0.0)
                totals[category] += amount
                if company.currency_id.is_zero(amount):
                    continue
                target = money_in if amount > 0 else money_out
                target[category] = self._cf_round(company, amount)
            rows.append(
                {
                    "month": key,
                    "opening_cash": self._cf_round(company, opening),
                    "in": money_in,
                    "out": money_out,
                    "net_excluding_transfers": self._cf_round(
                        company, sum(money_in.values()) + sum(money_out.values())
                    ),
                    "internal_transfers_net": self._cf_round(
                        company, values.get("internal_transfer", 0.0)
                    ),
                    "closing_cash": self._cf_round(company, balance),
                }
            )
            month += relativedelta(months=1)
        return {
            "company": company.name,
            "currency": company.currency_id.name,
            "months": rows,
            "totals": {
                category: self._cf_round(company, amount)
                for category, amount in totals.items()
                if not company.currency_id.is_zero(amount)
            },
            "notes": [
                "Posted entries on bank and cash accounts only; the current "
                "month is up to today.",
                "A payment is classified by the account it was booked against, "
                "followed through outstanding payment and suspense accounts.",
                "Unmatched bank lines on a suspense account are 'other'.",
            ],
        }
