# accbot development

Read `docs/design.md` before changing framework contracts. Keep its checklist and reference index current. Document current behavior, not development conversations or implementation history.

- Adapters belong to framework source. Chat participation, context, memory, prompt/persona selection, tool selection and business storage belong to plugins.
- Keep the default AI plugins in `plugins/ai/` and test fixtures in `tests/fixtures/`.
- ModelItem has typed sender/time/message/conversation/quote fields. Serialization merges adjacent ordinary user items, preserving system, assistant, tool and provider boundaries. Plugins own history stability. Raw JSON message arrays bypass rendering and prompt/persona selection.
- Never auto-activate missing dependencies. Validate the complete selected graph before activation.
- Unload removes registrations; accepted background tools finish naturally. Preserve activation identity across timers, queued callbacks and public API handles.
- Source reload uses isolated module generations, including plugin-local submodules. Shared third-party modules are not reloaded.
- Persona and system prompt cards belong to framework catalogs. Callers select IDs through `model.generate`; model cards never select a system prompt. Expand only the selected template's placeholders, with no automatic append.
- Plugin supplements use `prompts/system.md`; tool supplements use `@Tool(prompt_file="name.md")` or `@async_tool(...)` and `prompts/tools/`. Snapshot per activation; inject only active plugin supplements and tools passed to that call.
- Framework settings exclude plugin-internal parameters. Never expose stored secret values through read APIs or commit runtime data and credentials.
- Plugin pages use `frontend/index.html` with one root div and optional `index.js`/`style.css`. Plugin-owned `settings.py` handles configuration even while inactive. Preserve generation checks and component cleanup across reload/unload.
- Keep the management UI in `src/accbot/frontend/`, framework settings in `.accbot/config/` and secrets in `.accbot/secrets/`.
- Local UI access needs no token. Model and OneBot wizards manage secret references automatically; saved OneBot configurations remain inactive until explicitly enabled.
- Use `[机器人名称]` for example bot names. Runtime account identity comes from the adapter connection.
- Tests must run offline without model credentials or a QQ account. Do not commit local evaluation outputs, screenshots, caches or build artifacts.
- Do not add a license without an explicit maintainer choice.

Validation: `python3 -m pytest`, `ruff check .`, `ruff format --check .`.
