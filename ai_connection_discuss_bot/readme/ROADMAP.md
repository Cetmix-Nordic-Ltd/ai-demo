- Two proposal types exist (activity, note); further types could create
  for example project tasks or quotation drafts.
- `ai_connection` does not add the result of a tool that raises an
  exception to the conversation, which breaks the next AI call. The
  tools of this module therefore never raise and return errors as
  results instead.
- `ai_tool` needs read access to `ir.model`, which regular users do not
  have, so tools are executed as superuser (fixed for 19.0 in OCA/ai
  PR \#125).
