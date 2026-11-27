# Feature Request: Add `ByteSize` Parameter Type (SI & IEC Units) to Click

## Background
Developers building CLIs with Click regularly define size limits such as `--max-upload 10MiB`, `--cache-size 500MB`, or `--chunk 64KiB`. Correctly distinguishing SI decimal units (base 1000) from IEC binary units (base 1024) is error-prone when re-implemented ad hoc.

Repository: `pallets/click` (starting commit `cf19179e8e2fd54c601378cec9e0d21bb12b6fb1`).

## Requirements
Implement a `ByteSize` parameter type in `src/click/types.py` and re-export it from `src/click/__init__.py` as `click.ByteSize`.

### Public API
```python
class ByteSize(ParamType[int]):
    name = "bytesize"

    def __init__(
        self,
        *,
        min_bytes: int | None = None,
        max_bytes: int | None = None,
        case_sensitive: bool = False,
    ) -> None: ...
```

### Unit & Conversion Rules
1. **Supported Units:**
   - Bare number or `B`: `1`
   - SI decimal: `KB` (`1000`), `MB` (`1000**2`), `GB` (`1000**3`), `TB` (`1000**4`)
   - IEC binary: `KiB` (`1024`), `MiB` (`1024**2`), `GiB` (`1024**3`), `TiB` (`1024**4`)
2. **Formatting & Case Rules:**
   - Accepts optional whitespace around the number and between magnitude and unit (e.g., `"1.5 GiB"`).
   - Decimal magnitudes are rounded to the nearest integer byte count (`int(round(...))`).
   - When `case_sensitive=False` (default), units match case-insensitively; when `case_sensitive=True`, exact casing above is required.
3. **Validation & Errors:**
   - Constructor raises `ValueError` if `min_bytes < 0`, `max_bytes < 0`, or `min_bytes > max_bytes`.
   - `convert()` accepts non-negative `int` (not `bool`) or valid `str`; invalid units, negative values, `bool`, `float`, or out-of-range values fail via `self.fail(...)` (`click.BadParameter`).
4. **Introspection:**
   - `to_info_dict()` includes `min_bytes`, `max_bytes`, and `case_sensitive`.

## Acceptance Criteria
- `click.ByteSize` inherits from `click.ParamType` with `name = 'bytesize'` and is exported from `click`.
- Accurately distinguishes SI decimal units (`B`, `KB`, `MB`, `GB`, `TB` — base 1000) from IEC binary units (`KiB`, `MiB`, `GiB`, `TiB` — base 1024), returning an `int` byte count.
- Supports integer passthrough, optional whitespace, fractional magnitudes, and `case_sensitive` matching (default `False`).
- Enforces constructor invariants (`ValueError`) and runtime range/type/unit validation (`click.BadParameter`), while rejecting `bool` and negative values.
- `to_info_dict()` exposes `min_bytes`, `max_bytes`, and `case_sensitive`.
