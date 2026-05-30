# Bug: slash command results are double-delivered (slash_commands + user_messages)

## Symptom
Every slash command (e.g. `/swap-shell 3`) delivers its return value to the agent
**twice** — once on the `slash_commands` queue and once on `user_messages` with the
**same text**. The agent's `handle()` sees the identical content on both channels in
the same turn, so it has to detect and drain the duplicate every time.

Observed mid-task:
```text
'user_messages': ['self.shell is now **ShellTools3** ... Do a one-line smoke test...']
'slash_commands': [SlashCommandResult(command='swap-shell', args='3',
                   value='self.shell is now **ShellTools3** ...', text='...same...')]
```

## Root cause
`packages/nemo-oo-agents-cli/src/nemo_oo_agents_cli/tui/session.py`, ~lines 712-724:

```python
if result.slash_result is not None:
    text = str(result.slash_result)
    if text:
        self._app.emit_block(text + "\n")
    slash_ch = getattr(self.agent, "_slash_commands_in", None)
    if slash_ch is not None:
        slash_ch.put(result.slash_result)      # (1) -> slash_commands queue
    self._app.submit_message(text)             # (2) -> user_messages queue (SAME text)
```

The result is intentionally posted to `slash_commands` (so the agent can read
`.value`/the structured object) **and** submitted as a user message (to trigger the
agent turn). But submitting the text as a user message means the same content arrives
on both channels — the comment even says "also submit as a user message to trigger
the agent turn." Triggering the turn and delivering the payload have been conflated.

## Impact
- Every slash-command turn carries redundant content; the agent must dedupe.
- Wastes context (the text appears twice).
- Ambiguous contract: is the canonical payload the `SlashCommandResult` or the user
  message string? They're the same now, but a slash command whose `.value` differs
  from its `.text` would deliver inconsistent copies.

## Fix options
1. **Trigger without re-delivering payload (preferred).** Post the
   `SlashCommandResult` to `slash_commands` only, and trigger the agent turn with a
   signal that carries no duplicate text (e.g. submit an empty/sentinel turn-trigger,
   or have the dispatcher wake on the `slash_commands` queue directly rather than via
   `user_messages`). The agent reads the payload from `slash_commands`.
2. **Deliver on exactly one channel.** If slash results should look like user
   messages, drop the `slash_ch.put(...)` and deliver only via `submit_message` (lose
   `.value` access). If they should be structured, deliver only via `slash_ch` and make
   the dispatcher wake on it.

Option 1 keeps the structured `.value` access and the turn trigger while eliminating
the duplicate. The dispatcher already races both queues (`wait_for_any`), so waking on
`slash_commands` alone is viable.

## Repro
Run any slash command in the TUI and inspect the next `handle()` notification — the
same text appears under both `user_messages` and `slash_commands`.
