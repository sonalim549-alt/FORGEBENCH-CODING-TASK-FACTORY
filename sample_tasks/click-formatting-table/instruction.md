# Feature Request: Add ANSI-Aware Multi-Column `write_table()` to `HelpFormatter`

## Background
`click.HelpFormatter` currently supports 2-column definition lists via `write_dl()`, which covers options and commands, but CLI authors rendering structured multi-column tables (status summaries, environment tables, command matrices) have to hand-roll padding and ANSI width math.

Repository: `pallets/click` (starting commit `cf19179e8e2fd54c601378cec9e0d21bb12b6fb1`).

## Requirements
Add a `write_table` method to `HelpFormatter` in `src/click/formatting.py`:

```python
def write_table(
    self,
    headers: cabc.Sequence[str],
    rows: cabc.Iterable[cabc.Sequence[str]],
    *,
    col_spacing: int = 2,
    max_col_width: int | None = None,
    border: bool = False,
) -> None: ...
```

### Behavioral Rules
1. **Validation:**
   - Raise `ValueError` if `headers` is empty.
   - Raise `ValueError` if `col_spacing < 1`.
   - Raise `ValueError` if `max_col_width is not None and max_col_width < 4`.
   - Raise `ValueError` if any row does not have the exact same number of columns as `headers`.
2. **Truncation:**
   - If `max_col_width` is provided and a header or cell's visible length (`term_len`) exceeds `max_col_width`, clip it to `text[: max_col_width - 3] + "..."`.
3. **ANSI-Aware Alignment & Indentation:**
   - Compute each column's width as the maximum `term_len` across the clipped header and all clipped rows.
   - Prefix every line with `" " * self.current_indent`.
   - Separate columns with `" " * col_spacing` and pad every column except the last column (no trailing spaces at end of line).
4. **Optional Header Border:**
   - When `border=True`, write a separator line immediately after the header consisting of `"-"` repeated for each column's computed width.
5. **Empty Rows:**
   - When `rows` is empty, still render the header (and separator if `border=True`).

## Acceptance Criteria
- `HelpFormatter.write_table(headers, rows, *, col_spacing=2, max_col_width=None, border=False)` formats aligned multi-column tables respecting `self.current_indent`.
- Column widths are computed using visible character widths (`term_len`), keeping ANSI-styled cells properly aligned.
- When `border=True`, inserts a dashed separator row (`'-' * col_width`) directly below the header.
- When `max_col_width` is set (`>= 4`), truncates cells wider than `max_col_width` with `'...'`.
- Raises `ValueError` on empty headers, mismatched row column counts, `col_spacing < 1`, or `max_col_width < 4`, and emits no trailing spaces after the last column.
