# Copyright 2026 Cetmix Nordic Ltd
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import ast
import json
import logging

from odoo import fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import html2plaintext

from odoo.addons.ai_tool.tools import aitool

_logger = logging.getLogger(__name__)

MAX_RECORDS = 50
MAX_GROUPS = 100
MAX_FIELDS = 40
MAX_TEXT = 500
MAX_X2MANY = 20
SKIPPED_TYPES = {"binary", "properties", "properties_definition", "json"}
SKIPPED_FIELDS = {
    "create_uid",
    "write_uid",
    "write_date",
    "__last_update",
    "access_token",
    "access_url",
    "access_warning",
}
SKIPPED_PREFIXES = ("message_", "activity_", "website_message", "rating_")


class AiDiscussBotTools(models.AbstractModel):
    """Read-only Odoo data tools for the AI Discuss Bot.

    The ``ai.tool`` records pointing here are executed with superuser rights
    (plain users cannot read ``ir.model``, which ``ai.tool`` needs). Every tool
    therefore switches to the user who asked the question, taken from the
    ``ai_discuss_bot_user_id`` context key, before touching any data, and
    refuses to run without it. Record rules and field groups of that user
    apply to everything a tool returns.

    Tools never raise: errors are returned as ``{"error": ...}`` so the AI can
    react to them.
    """

    _name = "ai.discuss.bot.tools"
    _description = "AI Discuss Bot Tools"

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    @aitool(
        input_schema={
            "query": {
                "type": "string",
                "description": "Words to look for in the model's name or "
                "technical name, e.g. 'sale order', 'invoice', 'partner'.",
            },
        },
        required_inputs=["query"],
        output_schema={"models": {"type": "array"}},
    )
    def _ai_bot_find_models(self, query=None, **kwargs):
        def run(env):
            words = (query or "").strip()
            if not words:
                raise UserError("Give a word to search for.")
            candidates = (
                env["ir.model"]
                .sudo()
                .search(
                    [
                        "|",
                        ("name", "ilike", words),
                        ("model", "ilike", words),
                        ("transient", "=", False),
                    ],
                    limit=60,
                )
            )
            result = []
            for model in candidates:
                if model.model not in env or env[model.model]._abstract:
                    continue
                if not env[model.model].has_access("read"):
                    continue
                result.append({"model": model.model, "name": model.name})
                if len(result) >= 20:
                    break
            return {"models": result}

        return self._ai_bot_call("find_models", run, query=query)

    @aitool(
        input_schema={
            "model": {
                "type": "string",
                "description": "Technical model name, e.g. 'sale.order'.",
            },
        },
        required_inputs=["model"],
        output_schema={"fields": {"type": "array"}},
    )
    def _ai_bot_get_model_fields(self, model=None, **kwargs):
        def run(env):
            Model = self._ai_bot_model(env, model)
            result = []
            for name, info in self._ai_bot_fields_get(Model).items():
                field = {"name": name, "label": info["string"], "type": info["type"]}
                if info.get("relation"):
                    field["relation"] = info["relation"]
                if info.get("selection"):
                    field["values"] = [value for value, _label in info["selection"]]
                if not info.get("store"):
                    field["searchable"] = False
                result.append(field)
            return {"model": model, "fields": result[:300]}

        return self._ai_bot_call("get_model_fields", run, model=model)

    @aitool(
        input_schema={
            "model": {
                "type": "string",
                "description": "Technical model name, e.g. 'res.partner'.",
            },
            "domain": {
                "type": "string",
                "description": "Odoo search domain as JSON, e.g. "
                '[["state", "=", "sale"], ["date_order", ">=", "2026-01-01"]]. '
                "Use [] for all records.",
            },
            "fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Field names to return. Leave empty for a "
                "default set of fields.",
            },
            "limit": {
                "type": "integer",
                "description": f"Maximum number of records, at most {MAX_RECORDS}.",
            },
            "order": {
                "type": "string",
                "description": "Sort order, e.g. 'date_order desc'.",
            },
        },
        required_inputs=["model"],
        output_schema={"count": {"type": "integer"}, "records": {"type": "array"}},
    )
    def _ai_bot_search_records(
        self, model=None, domain=None, fields=None, limit=None, order=None, **kwargs
    ):
        def run(env):
            Model = self._ai_bot_model(env, model)
            parsed_domain = self._ai_bot_parse_domain(domain)
            size = min(max(int(limit or 20), 1), MAX_RECORDS)
            records = Model.search(parsed_domain, limit=size, order=order or None)
            field_infos = self._ai_bot_pick_fields(Model, fields)
            return {
                "model": model,
                "count": Model.search_count(parsed_domain),
                "returned": len(records),
                "records": [
                    self._ai_bot_record_values(record, field_infos)
                    for record in records
                ],
            }

        return self._ai_bot_call(
            "search_records",
            run,
            model=model,
            domain=domain,
            fields=fields,
            limit=limit,
            order=order,
        )

    @aitool(
        input_schema={
            "model": {
                "type": "string",
                "description": "Technical model name, e.g. 'sale.order'.",
            },
            "domain": {
                "type": "string",
                "description": "Odoo search domain as JSON. Use [] for all records.",
            },
            "groupby": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Fields to group by. Dates may have a "
                "granularity: 'date_order:month', ':quarter', ':year', ':day'.",
            },
            "aggregates": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Aggregates as 'field:function' with function "
                "sum, avg, min, max or count_distinct, e.g. 'amount_total:sum'. "
                "'__count' counts records. Default ['__count'].",
            },
        },
        required_inputs=["model"],
        output_schema={"groups": {"type": "array"}},
    )
    def _ai_bot_aggregate_records(
        self, model=None, domain=None, groupby=None, aggregates=None, **kwargs
    ):
        def run(env):
            Model = self._ai_bot_model(env, model)
            parsed_domain = self._ai_bot_parse_domain(domain)
            group_specs = list(groupby or [])
            aggregate_specs = list(aggregates or ["__count"])
            rows = Model._read_group(
                parsed_domain, group_specs, aggregate_specs, limit=MAX_GROUPS
            )
            groups = []
            for row in rows:
                values = {}
                for spec, value in zip(group_specs + aggregate_specs, row, strict=True):
                    values[spec] = self._ai_bot_plain(value)
                groups.append(values)
            return {"model": model, "groups": groups}

        return self._ai_bot_call(
            "aggregate_records",
            run,
            model=model,
            domain=domain,
            groupby=groupby,
            aggregates=aggregates,
        )

    @aitool(
        input_schema={},
        output_schema={"model": {"type": "string"}, "values": {"type": "object"}},
    )
    def _ai_bot_get_current_record(self, record=None, **kwargs):
        def run(env):
            if not record or record._name == "discuss.channel":
                raise UserError(
                    "This conversation is a chat, not the chatter of a business "
                    "record. Use search_records instead."
                )
            current = record.with_env(env).exists()
            if not current:
                raise UserError("The record no longer exists.")
            current.check_access("read")
            return {
                "model": current._name,
                "id": current.id,
                "values": self._ai_bot_record_values(
                    current, self._ai_bot_pick_fields(current, None)
                ),
            }

        return self._ai_bot_call("get_current_record", run)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ai_bot_user_env(self):
        user_id = self.env.context.get("ai_discuss_bot_user_id")
        if not user_id:
            raise AccessError(
                "AI bot tools run only on behalf of the user who asked the question."
            )
        return self.env(user=user_id, su=False)

    def _ai_bot_call(self, name, run, **arguments):
        try:
            env = self._ai_bot_user_env()
            with self.env.cr.savepoint():
                result = run(env)
        except Exception as error:  # returned to the AI, never raised
            result = {"error": f"{type(error).__name__}: {error}"}
        self._ai_bot_log(name, arguments, result)
        return result

    def _ai_bot_log(self, name, arguments, result):
        request_id = self.env.context.get("ai_discuss_bot_request_id")
        arguments = {key: value for key, value in arguments.items() if value}
        outcome = "error: " + result["error"] if "error" in result else "ok"
        line = f"{name}({json.dumps(arguments, ensure_ascii=False, default=str)})"
        line += f" -> {outcome}"
        _logger.info("AI Discuss Bot tool call (request %s): %s", request_id, line)
        if request_id:
            request = self.env["ai.discuss.bot.request"].sudo().browse(request_id)
            request.tool_log = (request.tool_log or "") + line + "\n"

    def _ai_bot_model(self, env, model):
        if not isinstance(model, str) or model not in env:
            raise UserError(
                f"Unknown model '{model}'. Use find_models to look up the "
                "technical name."
            )
        Model = env[model]
        if Model._abstract or Model._transient:
            raise UserError(f"'{model}' does not store records.")
        Model.check_access("read")
        return Model

    def _ai_bot_parse_domain(self, domain):
        if domain in (None, "", []):
            return []
        if isinstance(domain, str):
            try:
                domain = json.loads(domain)
            except ValueError:
                domain = ast.literal_eval(domain)
        if not isinstance(domain, list):
            raise UserError("The domain must be a list, e.g. [['name', 'ilike', 'x']].")
        return [tuple(term) if isinstance(term, list) else term for term in domain]

    def _ai_bot_fields_get(self, Model):
        """Fields the current user may read, without technical noise."""
        infos = Model.fields_get(
            attributes=["string", "type", "relation", "selection", "store"]
        )
        return {
            name: info
            for name, info in infos.items()
            if info["type"] not in SKIPPED_TYPES
            and name not in SKIPPED_FIELDS
            and not name.startswith(SKIPPED_PREFIXES)
        }

    def _ai_bot_pick_fields(self, Model, requested):
        available = self._ai_bot_fields_get(Model)
        if requested:
            unknown = [name for name in requested if name not in available]
            if unknown:
                raise UserError(
                    f"Unknown or unreadable fields on {Model._name}: "
                    f"{', '.join(unknown)}. Use get_model_fields."
                )
            return {name: available[name] for name in requested}
        picked = {
            name: info
            for name, info in available.items()
            if info.get("store") and info["type"] not in ("one2many", "html")
        }
        return dict(list(picked.items())[:MAX_FIELDS])

    def _ai_bot_record_values(self, record, field_infos):
        values = {"id": record.id, "display_name": record.display_name}
        for name, info in field_infos.items():
            values[name] = self._ai_bot_value(record, name, info)
        return values

    def _ai_bot_value(self, record, name, info):
        value = record[name]
        field_type = info["type"]
        if field_type == "many2one":
            return value.display_name or None
        if field_type in ("one2many", "many2many"):
            names = value[:MAX_X2MANY].mapped("display_name")
            if len(value) > MAX_X2MANY:
                names.append(f"... {len(value) - MAX_X2MANY} more")
            return names
        if field_type == "selection":
            return dict(info.get("selection") or []).get(value, value) or None
        if field_type == "html":
            value = html2plaintext(value or "")
        return self._ai_bot_plain(value)

    def _ai_bot_plain(self, value):
        """Make ``value`` JSON serialisable and short."""
        if isinstance(value, models.BaseModel):
            if not value:
                return None
            return (
                value.display_name if len(value) == 1 else value.mapped("display_name")
            )
        if value is False or value is None:
            return None
        if isinstance(value, bool | int | float):
            return value
        if hasattr(value, "hour"):
            return fields.Datetime.to_string(value)
        if hasattr(value, "isoformat"):
            return fields.Date.to_string(value)
        text = str(value)
        return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "..."
