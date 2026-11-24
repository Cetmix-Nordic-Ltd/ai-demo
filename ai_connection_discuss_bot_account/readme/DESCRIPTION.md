Finance tools for the AI bot of `ai_connection_discuss_bot`. The bot
answers questions about the company's cash flow with figures computed
by Odoo; the AI only explains them.

| Tool | Answers |
|---|---|
| `cash_flow_forecast` | Opening cash, money in and out per week or month by category, overdue items, running balance and the lowest point. Optionally "realistic": open customer invoices moved by each customer's average payment delay. |
| `cash_flow_history` | Actual cash flow statement per month from the bank and cash accounts, by category: customers, suppliers, taxes, wages and other liabilities, other operating, investments, financing. Internal transfers are left out. |

The forecast uses open invoices and bills and the forecast lines of
`mis_builder_cash_flow` (`mis_builder_cash_flow_auto` and its sales,
purchase and contract modules generate them).

Like all tools of the bot, they run with the access rights of the user
who asked: a user who cannot read journal items gets no figures.
