# Stateful Jev Agent Implementation Plan

Goal: MCP-only iterative agent with persisted multi-turn conversations and explicit durable memories.
Architecture: Jev selects action/tool from compact state; DeepSeek supplies validated arguments and final prose. SQLite isolates user/session records. Existing runtime remains available as legacy.
Stack: Python 3.12, existing MCP SDK, jsonschema, SQLite, urllib.
Spec: approved conversation scope (real weather, multiple tools, continuous decisions, multi-turn and memory).

- [x] Add SQLite store: session task state, bounded recent turns, user-scoped explicit memory. Reset session separately from forgetting preferences.
- [x] Add weather MCP: location search and dated forecast through fixed Open-Meteo URLs; timeout, bounded response, coordinate/date validation, no fake fallback.
- [x] Add utility MCP calculation/date tools and default MCP-only config (9 tools).
- [x] Implement Jev decision loop with explicit finish/clarify/call/unsupported decisions; argument construction via DeepSeek; schema checks, duplicate suppression, maximum steps, persisted failed/clarifying task, trace and reported usage.
- [x] Integrate CLI commands /new, /reset, /remember, /memory, /forget. Explicit memory only; no automatic extraction of private facts.
- [x] Document commands, data flow, limits and lack of measured token savings.

Review focus: failed provider must preserve task; missing params must not execute; user/session isolation; stale observations must remain dated; duplicate calls and loop budgets must terminate.
Validation: code inspection only unless user requests tests; no paid API calls using previously pasted keys.

Review: fixed timezone name shadowing, shared Jev tool context, newest-first bounded history selection, and malformed provider response handling. No tests or live model calls executed. Parameter caching and automatic long-term memory extraction deferred; explicit /remember implemented.
