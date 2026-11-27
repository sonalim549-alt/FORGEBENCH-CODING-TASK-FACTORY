# Feature Request: Add `Duration` Parameter Type to Click

## Background
CLI tools built with Click frequently accept human-readable time intervals (for example, `--timeout 2m30s`, `--ttl 12h`, or `--retry-delay 500ms`). Click currently provides `DateTime`, `IntRange`, and `FloatRange`, but does not include a built-in parameter type for durations.

Repository: `pallets/click` (starting commit `cf19179e8e2fd54c601378cec9e0d21bb12b6fb1`).

## Requirements
Implement a new `Duration` parameter type in `src/click/types.py` and re-export it from `src/click/__init__.py` as `click.Duration`.

### Public API
```python
class Duration(ParamType[timedelta]):
    name = "duration"

    def __init__(
        self,
        *,
        min_seconds: float | None = None,
        max_seconds: float | None = None,
        allow_zero: bool = True,
        as_seconds: bool = False,
    ) -> None: ...
```

### Parsing & Conversion Rules
1. **Supported Units (case-insensitive):**
   - `ms` (0.001s), `s` (1s), `m` (60s), `h` (3600s), `d` (86400s), `w` (604800s).
   - Accepts integer or decimal magnitudes (e.g., `"1.5h"`, `"500ms"`).
   - Accepts compound multi-unit durations with or without whitespace between tokens (e.g., `"1h30m15s"`, `"2d 4h"`).
2. **Non-string Inputs:**
   - `datetime.timedelta` instances pass through (subject to non-negative and range checks).
   - `int` and `float` values are treated as seconds (excluding `bool`, which must fail).
3. **Output Type:**
   - Returns `datetime.timedelta` by default, or `float` total seconds when `as_seconds=True`.
4. **Validation & Errors:**
   - Constructor must raise `ValueError` if `min_seconds < 0`, `max_seconds < 0`, or `min_seconds > max_seconds`.
   - `convert()` must raise `click.BadParameter` (via `self.fail`) on empty strings, negative durations, unrecognized units, trailing garbage, `bool`/unsupported objects, zero when `allow_zero=False`, or values outside `[min_seconds, max_seconds]`.
5. **Introspection:**
   - `to_info_dict()` must include `min_seconds`, `max_seconds`, `allow_zero`, and `as_seconds`.

## Acceptance Criteria
- `click.Duration` inherits from `click.ParamType` with `name = 'duration'` and is exported from `click`.
- Parses single and compound duration strings (`ms`, `s`, `m`, `h`, `d`, `w`, case-insensitive, optional spaces, integer or decimal magnitudes), `datetime.timedelta`, and numeric seconds.
- Returns `datetime.timedelta` by default or `float` seconds when `as_seconds=True`.
- Validates constructor bounds (`min_seconds >= 0`, `max_seconds >= 0`, `min_seconds <= max_seconds` -> `ValueError`) and enforces `allow_zero`, `min_seconds`, `max_seconds`, and invalid/negative/bool inputs via `click.BadParameter`.
- `to_info_dict()` exposes `min_seconds`, `max_seconds`, `allow_zero`, and `as_seconds`.
