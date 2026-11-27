# Feature Request: Secret-Safe Output Helpers (`redact_text` and `safe_echo`)

## Background
CLI tools running in CI/CD pipelines frequently print command summaries or diagnostic messages that risk accidentally leaking API tokens, bearer credentials, or passwords into build logs.

Repository: `pallets/click` (starting commit `cf19179e8e2fd54c601378cec9e0d21bb12b6fb1`).

## Requirements
Add `redact_text` and `safe_echo` to `src/click/utils.py` and re-export both from `src/click/__init__.py`.

### Public API
```python
def redact_text(
    text: str,
    secrets: cabc.Iterable[str | None],
    *,
    replacement: str = "[REDACTED]",
    min_secret_length: int = 1,
    case_sensitive: bool = True,
) -> str: ...

def safe_echo(
    message: t.Any = None,
    *,
    secrets: cabc.Iterable[str | None] | None = None,
    replacement: str = "[REDACTED]",
    min_secret_length: int = 1,
    case_sensitive: bool = True,
    file: t.IO[t.Any] | None = None,
    nl: bool = True,
    err: bool = False,
    color: bool | None = None,
) -> None: ...
```

### Behavioral Rules
1. **Validation & Filtering:**
   - Raise `ValueError` if `min_secret_length < 1`.
   - Ignore `None` values and strings with `len(secret) < min_secret_length`.
   - If `text` is empty or no valid secrets remain, return `text` unchanged.
2. **Longest-First & Regex-Safe Substitution:**
   - Match candidate secrets in descending order of length so a shorter prefix secret cannot partially mask a longer secret and leave its suffix exposed.
   - Treat secrets as literal substrings (escape regex metacharacters like `+`, `$`, `*`) and insert `replacement` literally.
   - Respect `case_sensitive` (`True` by default, `re.IGNORECASE` when `False`).
3. **`safe_echo` Integration:**
   - When both `message` and `secrets` are non-`None`, convert `message` with `make_str`, redact it with `redact_text`, and pass the result to `echo(...)` with all standard `echo` keyword arguments (`file`, `nl`, `err`, `color`).
   - When `message is None`, delegate cleanly to `echo(message=None, ...)` without failing.

## Acceptance Criteria
- `click.redact_text(text, secrets, *, replacement='[REDACTED]', min_secret_length=1, case_sensitive=True)` replaces secrets longest-first and safely escapes regex metacharacters.
- Ignores `None` entries and secrets shorter than `min_secret_length`; raises `ValueError` when `min_secret_length < 1`.
- Supports `case_sensitive=False` matching without mangling replacement strings containing backslashes.
- `click.safe_echo(...)` redacts non-None messages when `secrets` is provided and delegates stream/newline/color options to `click.echo()`.
- Both `redact_text` and `safe_echo` are exported from `click`.
