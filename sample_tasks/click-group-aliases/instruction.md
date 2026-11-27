# Feature Request: First-Class Subcommand Alias Support on `click.Group`

## Background
CLI tools frequently offer shorthand command aliases (such as `deploy` aliased as `dep` and `d`). Today Click requires users to subclass `click.Group` and manually override command lookup and help formatting.

Repository: `pallets/click` (starting commit `cf19179e8e2fd54c601378cec9e0d21bb12b6fb1`).

## Requirements
Extend `Group` in `src/click/core.py` with native subcommand alias registration, resolution, and help formatting:

### Public API on `Group`
1. **`add_alias(self, alias: str, target_name: str) -> None`**
   - Strips surrounding whitespace and validates both `alias` and `target_name` are non-empty strings (else `ValueError`).
   - Raises `ValueError` if `alias == target_name`.
   - Raises `KeyError` if `target_name` is not registered in `self.commands`.
   - Raises `ValueError` if `alias` collides with an existing command in `self.commands` or an already-registered alias.
2. **`resolve_alias(self, name: str) -> str | None`**
   - Returns the canonical target command name if `name` is a registered alias, otherwise `None`.
3. **`get_aliases(self, target_name: str) -> list[str]`**
   - Returns a sorted list of all alias names registered for `target_name`.
4. **Command Resolution (`get_command`):**
   - Look up `cmd_name` in `self.commands`; if not found, check `resolve_alias(cmd_name)` and return the target `Command` if present.
5. **Help Formatting (`format_commands`):**
   - When a subcommand has registered aliases, format its left-column label as `"<subcommand> (<alias1>, <alias2>)"` using sorted aliases.
   - Commands with no aliases must continue to render identically to existing Click behavior.

## Acceptance Criteria
- `Group.add_alias(alias, target_name)` registers an alias to an existing command while raising `ValueError` on empty strings, self-aliasing, or collision with an existing command/alias, and `KeyError` when `target_name` does not exist.
- `Group.resolve_alias(name)` returns the canonical command name or `None`; `Group.get_aliases(target_name)` returns sorted aliases for that target.
- `Group.get_command(ctx, cmd_name)` transparently resolves aliases so aliased subcommands invoke normally via CLI.
- `Group.format_commands(ctx, formatter)` appends `(<sorted aliases>)` next to aliased commands in `--help` output while leaving unaliased commands unchanged.
