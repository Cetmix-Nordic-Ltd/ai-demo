The module depends on `ai_tool` and `ai_connection_openai` from the OCA
`ai` repository. `ai_connection_openai` needs the Python package
`openai`.

The answers are produced by a scheduled action, so Odoo must run with at
least one cron thread.

A slow AI (for example a local model on CPU) can take minutes per
answer. In multi-worker mode Odoo stops a scheduled action after
`limit_time_real_cron` seconds (or `limit_time_real` when that is not
set, 120 s by default). Raise it, for example to 900, if answers are
interrupted. A request interrupted three times is marked as failed and
the person who asked is told.
