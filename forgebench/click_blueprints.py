from __future__ import annotations

import ast
from pathlib import Path

from forgebench.models import FeatureSeam, RepoProfile, TaskPackage
from forgebench.patch_utils import combine_patches, make_unified_diff


def _insert_init_exports(repo_root: Path, export_lines: list[str]) -> str:
    """Safely append public re-exports into src/click/__init__.py right after open_file."""
    init_rel = "src/click/__init__.py"
    init_path = repo_root / init_rel
    if not init_path.is_file():
        return ""

    original = init_path.read_text(encoding="utf-8")
    anchor = "from .utils import open_file as open_file"
    if anchor not in original:
        return ""

    additions = "\n".join(line for line in export_lines if line not in original)
    if not additions:
        return ""

    updated = original.replace(anchor, f"{anchor}\n{additions}", 1)
    ast.parse(updated)
    return make_unified_diff(init_rel, original, updated)


def _append_to_class_ast(source: str, class_name: str, method_block: str) -> str:
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    target = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            target = node
            break
    if target is None or target.end_lineno is None:
        raise ValueError(f"Class '{class_name}' not found.")

    insert_at = target.end_lineno
    if insert_at > 0 and not lines[insert_at - 1].endswith("\n"):
        lines[insert_at - 1] += "\n"
    updated = (
        "".join(lines[:insert_at])
        + "\n"
        + method_block.strip("\n")
        + "\n"
        + "".join(lines[insert_at:])
    )
    ast.parse(updated)
    return updated


def _pick_click_regression_files(profile: RepoProfile, primary: list[str]) -> list[str]:
    existing = set(profile.test_files)
    chosen = [f for f in primary if f in existing]
    if not chosen:
        chosen = [f for f in profile.test_files if f.startswith("tests/test_")][:6]
    return chosen


def synthesize_click_duration_task(profile: RepoProfile, seam: FeatureSeam) -> TaskPackage:
    repo_root = Path(profile.repo_path)
    rel_path = seam.target_module
    original = (repo_root / rel_path).read_text(encoding="utf-8")

    # Add re and timedelta imports cleanly if not present
    updated = original
    if "import re\n" not in updated:
        updated = updated.replace("import os\n", "import os\nimport re\n", 1)
    if "from datetime import timedelta" not in updated:
        updated = updated.replace(
            "from datetime import datetime\n",
            "from datetime import datetime\nfrom datetime import timedelta\n",
            1,
        )

    duration_code = '''

_DURATION_TOKEN_RE = re.compile(r"(\\d+(?:\\.\\d+)?)\\s*(ms|s|m|h|d|w)", re.IGNORECASE)
_DURATION_UNIT_SECONDS: dict[str, float] = {
    "ms": 0.001,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
    "w": 604800.0,
}


class Duration(ParamType[timedelta]):
    """Parse human-readable duration strings (e.g. ``'30s'``, ``'1h30m'``, ``'500ms'``)
    into a :class:`datetime.timedelta` (or float seconds when ``as_seconds=True``).
    """

    name = "duration"

    def __init__(
        self,
        *,
        min_seconds: float | None = None,
        max_seconds: float | None = None,
        allow_zero: bool = True,
        as_seconds: bool = False,
    ) -> None:
        if min_seconds is not None and min_seconds < 0:
            raise ValueError("min_seconds cannot be negative.")
        if max_seconds is not None and max_seconds < 0:
            raise ValueError("max_seconds cannot be negative.")
        if (
            min_seconds is not None
            and max_seconds is not None
            and min_seconds > max_seconds
        ):
            raise ValueError("min_seconds cannot exceed max_seconds.")

        self.min_seconds = min_seconds
        self.max_seconds = max_seconds
        self.allow_zero = allow_zero
        self.as_seconds = as_seconds

    def to_info_dict(self) -> dict[str, t.Any]:
        info_dict = super().to_info_dict()
        info_dict.update(
            min_seconds=self.min_seconds,
            max_seconds=self.max_seconds,
            allow_zero=self.allow_zero,
            as_seconds=self.as_seconds,
        )
        return info_dict

    def _parse_seconds(
        self,
        value: t.Any,
        param: Parameter | None,
        ctx: Context | None,
    ) -> float:
        if isinstance(value, bool):
            self.fail(f"{value!r} is not a valid duration.", param, ctx)
        if isinstance(value, timedelta):
            return value.total_seconds()
        if isinstance(value, (int, float)):
            return float(value)
        if not isinstance(value, str):
            self.fail(f"{value!r} is not a valid duration.", param, ctx)

        cleaned = value.strip()
        if not cleaned or cleaned.startswith("-"):
            self.fail(f"{value!r} is not a valid duration.", param, ctx)

        pos = 0
        total_seconds = 0.0
        matched_any = False
        while pos < len(cleaned):
            while pos < len(cleaned) and cleaned[pos].isspace():
                pos += 1
            if pos >= len(cleaned):
                break
            match = _DURATION_TOKEN_RE.match(cleaned, pos)
            if match is None:
                self.fail(f"{value!r} is not a valid duration.", param, ctx)
            amount = float(match.group(1))
            unit = match.group(2).lower()
            total_seconds += amount * _DURATION_UNIT_SECONDS[unit]
            matched_any = True
            pos = match.end()

        if not matched_any:
            self.fail(f"{value!r} is not a valid duration.", param, ctx)
        return total_seconds

    def convert(
        self,
        value: t.Any,
        param: Parameter | None,
        ctx: Context | None,
    ) -> t.Any:
        seconds = self._parse_seconds(value, param, ctx)

        if seconds < 0:
            self.fail("Duration cannot be negative.", param, ctx)
        if not self.allow_zero and seconds == 0:
            self.fail("Duration must be greater than zero.", param, ctx)
        if self.min_seconds is not None and seconds < self.min_seconds:
            self.fail(
                f"Duration {seconds:g}s is shorter than minimum {self.min_seconds:g}s.",
                param,
                ctx,
            )
        if self.max_seconds is not None and seconds > self.max_seconds:
            self.fail(
                f"Duration {seconds:g}s exceeds maximum {self.max_seconds:g}s.",
                param,
                ctx,
            )

        if self.as_seconds:
            return seconds
        return timedelta(seconds=seconds)
'''
    updated = updated.rstrip("\n") + "\n" + duration_code
    ast.parse(updated)

    main_diff = make_unified_diff(rel_path, original, updated)
    init_diff = _insert_init_exports(repo_root, ["from .types import Duration as Duration"])
    ref_patch = combine_patches([main_diff, init_diff])

    test_rel = "tests/test_feature_duration_type.py"
    test_code = '''from datetime import timedelta

import pytest

import click
from click.testing import CliRunner


def test_duration_parses_single_compound_and_decimal_units():
    dt = click.Duration()
    assert dt.convert("500ms", None, None) == timedelta(milliseconds=500)
    assert dt.convert("45s", None, None) == timedelta(seconds=45)
    assert dt.convert("1.5h", None, None) == timedelta(minutes=90)
    assert dt.convert("1h30m15s", None, None) == timedelta(hours=1, minutes=30, seconds=15)
    assert dt.convert("2d 4h", None, None) == timedelta(days=2, hours=4)
    assert dt.convert("1w", None, None) == timedelta(weeks=1)
    assert dt.convert(timedelta(seconds=12), None, None) == timedelta(seconds=12)
    assert dt.convert(30, None, None) == timedelta(seconds=30)


def test_duration_as_seconds_and_bounds_enforcement():
    bounded = click.Duration(min_seconds=10, max_seconds=120, allow_zero=False, as_seconds=True)
    assert bounded.convert("1m", None, None) == 60.0
    assert bounded.to_info_dict()["min_seconds"] == 10
    assert bounded.to_info_dict()["max_seconds"] == 120
    assert bounded.to_info_dict()["allow_zero"] is False
    assert bounded.to_info_dict()["as_seconds"] is True

    with pytest.raises(click.BadParameter):
        bounded.convert("5s", None, None)
    with pytest.raises(click.BadParameter):
        bounded.convert("3m", None, None)

    no_zero = click.Duration(allow_zero=False)
    with pytest.raises(click.BadParameter):
        no_zero.convert("0s", None, None)


def test_duration_rejects_invalid_constructor_and_inputs():
    with pytest.raises(ValueError):
        click.Duration(min_seconds=-1)
    with pytest.raises(ValueError):
        click.Duration(max_seconds=-5)
    with pytest.raises(ValueError):
        click.Duration(min_seconds=60, max_seconds=30)

    dt = click.Duration()
    for bad in ("", "   ", "-10s", "10x", "1h bogus", True, timedelta(seconds=-2), object()):
        with pytest.raises(click.BadParameter):
            dt.convert(bad, None, None)


def test_duration_cli_integration():
    @click.command()
    @click.option("--timeout", type=click.Duration(min_seconds=1, max_seconds=3600))
    def run(timeout: timedelta | None) -> None:
        click.echo(f"seconds={int(timeout.total_seconds()) if timeout else 0}")

    runner = CliRunner()
    ok = runner.invoke(run, ["--timeout", "2m30s"])
    assert ok.exit_code == 0
    assert "seconds=150" in ok.output

    bad = runner.invoke(run, ["--timeout", "2h"])
    assert bad.exit_code != 0
'''
    test_patch = make_unified_diff(test_rel, None, test_code)

    criteria = [
        "`click.Duration` inherits from `click.ParamType` with `name = 'duration'` and is exported from `click`.",
        "Parses single and compound duration strings (`ms`, `s`, `m`, `h`, `d`, `w`, case-insensitive, optional spaces, integer or decimal magnitudes), `datetime.timedelta`, and numeric seconds.",
        "Returns `datetime.timedelta` by default or `float` seconds when `as_seconds=True`.",
        "Validates constructor bounds (`min_seconds >= 0`, `max_seconds >= 0`, `min_seconds <= max_seconds` -> `ValueError`) and enforces `allow_zero`, `min_seconds`, `max_seconds`, and invalid/negative/bool inputs via `click.BadParameter`.",
        "`to_info_dict()` exposes `min_seconds`, `max_seconds`, `allow_zero`, and `as_seconds`.",
    ]

    instruction = f"""# Feature Request: Add `Duration` Parameter Type to Click

## Background
CLI tools built with Click frequently accept human-readable time intervals (for example, `--timeout 2m30s`, `--ttl 12h`, or `--retry-delay 500ms`). Click currently provides `DateTime`, `IntRange`, and `FloatRange`, but does not include a built-in parameter type for durations.

Repository: `pallets/click` (starting commit `{profile.base_commit}`).

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
""" + "\n".join(f"- {c}" for c in criteria) + "\n"

    reg_files = _pick_click_regression_files(
        profile,
        [
            "tests/test_basic.py",
            "tests/test_options.py",
            "tests/test_info_dict.py",
            "tests/test_imports.py",
        ],
    )

    return TaskPackage(
        task_id="click-type-duration",
        title=seam.title,
        category=seam.category,
        difficulty=seam.difficulty,
        repo_name=profile.repo_name,
        repo_url=profile.repo_url,
        base_commit=profile.base_commit,
        base_tag=profile.base_tag,
        license_name=profile.license_name,
        language=profile.language,
        framework=profile.framework,
        test_command=profile.test_command,
        target_files=[rel_path, "src/click/__init__.py"],
        problem_statement=instruction,
        acceptance_criteria=criteria,
        reference_patch=ref_patch,
        feature_tests_patch=test_patch,
        feature_test_files=[test_rel],
        regression_test_files=reg_files,
        feature_test_sources={test_rel: test_code},
        generator_strategy="forgebench-ast-guided",
    )


def synthesize_click_bytesize_task(profile: RepoProfile, seam: FeatureSeam) -> TaskPackage:
    repo_root = Path(profile.repo_path)
    rel_path = seam.target_module
    original = (repo_root / rel_path).read_text(encoding="utf-8")

    updated = original
    if "import re\n" not in updated:
        updated = updated.replace("import os\n", "import os\nimport re\n", 1)

    bytesize_code = '''

_BYTESIZE_RE = re.compile(r"^\\s*(\\d+(?:\\.\\d+)?)\\s*([A-Za-z]*)\\s*$")
_BYTESIZE_UNITS_EXACT: dict[str, int] = {
    "": 1,
    "B": 1,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "TB": 1000**4,
    "KiB": 1024,
    "MiB": 1024**2,
    "GiB": 1024**3,
    "TiB": 1024**4,
}
_BYTESIZE_UNITS_CI: dict[str, int] = {
    k.lower(): v for k, v in _BYTESIZE_UNITS_EXACT.items()
}


class ByteSize(ParamType[int]):
    """Parse human-readable byte sizes distinguishing SI decimal (``KB``, ``MB``)
    and IEC binary (``KiB``, ``MiB``) units into an integer byte count.
    """

    name = "bytesize"

    def __init__(
        self,
        *,
        min_bytes: int | None = None,
        max_bytes: int | None = None,
        case_sensitive: bool = False,
    ) -> None:
        if min_bytes is not None and min_bytes < 0:
            raise ValueError("min_bytes cannot be negative.")
        if max_bytes is not None and max_bytes < 0:
            raise ValueError("max_bytes cannot be negative.")
        if min_bytes is not None and max_bytes is not None and min_bytes > max_bytes:
            raise ValueError("min_bytes cannot exceed max_bytes.")

        self.min_bytes = min_bytes
        self.max_bytes = max_bytes
        self.case_sensitive = case_sensitive

    def to_info_dict(self) -> dict[str, t.Any]:
        info_dict = super().to_info_dict()
        info_dict.update(
            min_bytes=self.min_bytes,
            max_bytes=self.max_bytes,
            case_sensitive=self.case_sensitive,
        )
        return info_dict

    def convert(
        self,
        value: t.Any,
        param: Parameter | None,
        ctx: Context | None,
    ) -> int:
        if isinstance(value, bool):
            self.fail(f"{value!r} is not a valid byte size.", param, ctx)

        if isinstance(value, int):
            total = value
        elif isinstance(value, str):
            match = _BYTESIZE_RE.match(value)
            if match is None:
                self.fail(f"{value!r} is not a valid byte size.", param, ctx)
            magnitude = float(match.group(1))
            raw_unit = match.group(2)
            unit_table = (
                _BYTESIZE_UNITS_EXACT if self.case_sensitive else _BYTESIZE_UNITS_CI
            )
            lookup_key = raw_unit if self.case_sensitive else raw_unit.lower()
            if lookup_key not in unit_table:
                self.fail(f"Unknown byte size unit {raw_unit!r}.", param, ctx)
            total = int(round(magnitude * unit_table[lookup_key]))
        else:
            self.fail(f"{value!r} is not a valid byte size.", param, ctx)

        if total < 0:
            self.fail("Byte size cannot be negative.", param, ctx)
        if self.min_bytes is not None and total < self.min_bytes:
            self.fail(
                f"Byte size {total} is smaller than minimum {self.min_bytes}.",
                param,
                ctx,
            )
        if self.max_bytes is not None and total > self.max_bytes:
            self.fail(
                f"Byte size {total} exceeds maximum {self.max_bytes}.",
                param,
                ctx,
            )
        return total
'''
    updated = updated.rstrip("\n") + "\n" + bytesize_code
    ast.parse(updated)

    main_diff = make_unified_diff(rel_path, original, updated)
    init_diff = _insert_init_exports(repo_root, ["from .types import ByteSize as ByteSize"])
    ref_patch = combine_patches([main_diff, init_diff])

    test_rel = "tests/test_feature_bytesize_type.py"
    test_code = '''import pytest

import click
from click.testing import CliRunner


def test_bytesize_distinguishes_si_and_iec_units():
    bs = click.ByteSize()
    assert bs.convert("512", None, None) == 512
    assert bs.convert("512B", None, None) == 512
    assert bs.convert("1KB", None, None) == 1000
    assert bs.convert("1KiB", None, None) == 1024
    assert bs.convert("2MB", None, None) == 2_000_000
    assert bs.convert("2MiB", None, None) == 2 * 1024 * 1024
    assert bs.convert("1.5 GiB", None, None) == int(1.5 * 1024**3)
    assert bs.convert(4096, None, None) == 4096


def test_bytesize_case_sensitivity_and_bounds():
    strict = click.ByteSize(min_bytes=1024, max_bytes=10 * 1024 * 1024, case_sensitive=True)
    assert strict.convert("4KiB", None, None) == 4096
    assert strict.to_info_dict()["case_sensitive"] is True
    assert strict.to_info_dict()["min_bytes"] == 1024
    assert strict.to_info_dict()["max_bytes"] == 10 * 1024 * 1024

    with pytest.raises(click.BadParameter):
        strict.convert("4kib", None, None)
    with pytest.raises(click.BadParameter):
        strict.convert("512B", None, None)
    with pytest.raises(click.BadParameter):
        strict.convert("20MiB", None, None)


def test_bytesize_rejects_invalid_constructor_and_inputs():
    with pytest.raises(ValueError):
        click.ByteSize(min_bytes=-1)
    with pytest.raises(ValueError):
        click.ByteSize(max_bytes=-10)
    with pytest.raises(ValueError):
        click.ByteSize(min_bytes=100, max_bytes=50)

    bs = click.ByteSize()
    for bad in ("", "-10MB", "10XB", "abc", True, -5, 1.5):
        with pytest.raises(click.BadParameter):
            bs.convert(bad, None, None)


def test_bytesize_cli_integration():
    @click.command()
    @click.option("--limit", type=click.ByteSize(min_bytes=1000, max_bytes=10_000_000))
    def upload(limit: int | None) -> None:
        click.echo(f"bytes={limit}")

    runner = CliRunner()
    res = runner.invoke(upload, ["--limit", "2MiB"])
    assert res.exit_code == 0
    assert f"bytes={2 * 1024 * 1024}" in res.output

    too_big = runner.invoke(upload, ["--limit", "50MB"])
    assert too_big.exit_code != 0
'''
    test_patch = make_unified_diff(test_rel, None, test_code)

    criteria = [
        "`click.ByteSize` inherits from `click.ParamType` with `name = 'bytesize'` and is exported from `click`.",
        "Accurately distinguishes SI decimal units (`B`, `KB`, `MB`, `GB`, `TB` — base 1000) from IEC binary units (`KiB`, `MiB`, `GiB`, `TiB` — base 1024), returning an `int` byte count.",
        "Supports integer passthrough, optional whitespace, fractional magnitudes, and `case_sensitive` matching (default `False`).",
        "Enforces constructor invariants (`ValueError`) and runtime range/type/unit validation (`click.BadParameter`), while rejecting `bool` and negative values.",
        "`to_info_dict()` exposes `min_bytes`, `max_bytes`, and `case_sensitive`.",
    ]

    instruction = f"""# Feature Request: Add `ByteSize` Parameter Type (SI & IEC Units) to Click

## Background
Developers building CLIs with Click regularly define size limits such as `--max-upload 10MiB`, `--cache-size 500MB`, or `--chunk 64KiB`. Correctly distinguishing SI decimal units (base 1000) from IEC binary units (base 1024) is error-prone when re-implemented ad hoc.

Repository: `pallets/click` (starting commit `{profile.base_commit}`).

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
""" + "\n".join(f"- {c}" for c in criteria) + "\n"

    reg_files = _pick_click_regression_files(
        profile,
        [
            "tests/test_basic.py",
            "tests/test_options.py",
            "tests/test_info_dict.py",
            "tests/test_imports.py",
        ],
    )

    return TaskPackage(
        task_id="click-type-bytesize",
        title=seam.title,
        category=seam.category,
        difficulty=seam.difficulty,
        repo_name=profile.repo_name,
        repo_url=profile.repo_url,
        base_commit=profile.base_commit,
        base_tag=profile.base_tag,
        license_name=profile.license_name,
        language=profile.language,
        framework=profile.framework,
        test_command=profile.test_command,
        target_files=[rel_path, "src/click/__init__.py"],
        problem_statement=instruction,
        acceptance_criteria=criteria,
        reference_patch=ref_patch,
        feature_tests_patch=test_patch,
        feature_test_files=[test_rel],
        regression_test_files=reg_files,
        feature_test_sources={test_rel: test_code},
        generator_strategy="forgebench-ast-guided",
    )


def synthesize_click_table_task(profile: RepoProfile, seam: FeatureSeam) -> TaskPackage:
    repo_root = Path(profile.repo_path)
    rel_path = seam.target_module
    original = (repo_root / rel_path).read_text(encoding="utf-8")

    method_block = '''
    def write_table(
        self,
        headers: cabc.Sequence[str],
        rows: cabc.Iterable[cabc.Sequence[str]],
        *,
        col_spacing: int = 2,
        max_col_width: int | None = None,
        border: bool = False,
    ) -> None:
        """Write an ANSI-aware multi-column table into the help formatter buffer."""
        header_cells = [str(h) for h in headers]
        if not header_cells:
            raise ValueError("headers must contain at least one column.")
        if col_spacing < 1:
            raise ValueError("col_spacing must be at least 1.")
        if max_col_width is not None and max_col_width < 4:
            raise ValueError("max_col_width must be at least 4 when specified.")

        col_count = len(header_cells)
        materialized_rows: list[list[str]] = []
        for row in rows:
            cells = [str(cell) for cell in row]
            if len(cells) != col_count:
                raise ValueError(
                    f"Expected {col_count} columns per row, got {len(cells)}."
                )
            materialized_rows.append(cells)

        def _clip(text: str) -> str:
            if max_col_width is not None and term_len(text) > max_col_width:
                return text[: max_col_width - 3] + "..."
            return text

        clipped_headers = [_clip(h) for h in header_cells]
        clipped_rows = [[_clip(c) for c in row] for row in materialized_rows]

        widths = [term_len(h) for h in clipped_headers]
        for row in clipped_rows:
            for idx, cell in enumerate(row):
                widths[idx] = max(widths[idx], term_len(cell))

        indent_prefix = " " * self.current_indent
        spacer = " " * col_spacing

        def _format_line(cells: list[str]) -> str:
            parts: list[str] = []
            last_idx = len(cells) - 1
            for idx, cell in enumerate(cells):
                if idx == last_idx:
                    parts.append(cell)
                else:
                    pad = widths[idx] - term_len(cell)
                    parts.append(cell + (" " * pad))
            return indent_prefix + spacer.join(parts) + "\\n"

        self.write(_format_line(clipped_headers))
        if border:
            sep_cells = ["-" * w for w in widths]
            self.write(_format_line(sep_cells))
        for row in clipped_rows:
            self.write(_format_line(row))
'''
    updated = _append_to_class_ast(original, "HelpFormatter", method_block)
    ref_patch = make_unified_diff(rel_path, original, updated)

    test_rel = "tests/test_feature_formatter_table.py"
    test_code = '''import pytest

import click
from click.formatting import HelpFormatter


def test_write_table_alignment_border_and_indentation():
    fmt = HelpFormatter()
    with fmt.indentation():
        fmt.write_table(
            ["Name", "Role", "Status"],
            [
                ["api", "gateway", "ready"],
                ["worker-long", "queue", "ok"],
            ],
            col_spacing=2,
            border=True,
        )

    lines = fmt.getvalue().splitlines()
    assert len(lines) == 4
    assert lines[0] == "  Name         Role     Status"
    assert lines[1] == "  -----------  -------  ------"
    assert lines[2] == "  api          gateway  ready"
    assert lines[3] == "  worker-long  queue    ok"


def test_write_table_ansi_width_and_truncation():
    fmt = HelpFormatter()
    styled_ok = click.style("OK", fg="green")
    fmt.write_table(
        ["Service", "State", "Notes"],
        [
            ["auth", styled_ok, "very-long-diagnostic-message"],
            ["billing", "DOWN", "short"],
        ],
        max_col_width=10,
        border=False,
    )
    lines = fmt.getvalue().splitlines()
    assert len(lines) == 3
    assert "very-lo..." in lines[1]
    # ANSI escape sequences must not misalign column 3 relative to plain rows
    assert lines[1].index("very-lo...") - (len(styled_ok) - len("OK")) == lines[2].index("short")


def test_write_table_empty_rows_and_validation_errors():
    fmt = HelpFormatter()
    fmt.write_table(["ColA", "ColB"], [])
    assert fmt.getvalue() == "ColA  ColB\\n"

    with pytest.raises(ValueError):
        fmt.write_table([], [])
    with pytest.raises(ValueError):
        fmt.write_table(["A", "B"], [["only-one"]])
    with pytest.raises(ValueError):
        fmt.write_table(["A"], [["1"]], col_spacing=0)
    with pytest.raises(ValueError):
        fmt.write_table(["A"], [["1"]], max_col_width=3)
'''
    test_patch = make_unified_diff(test_rel, None, test_code)

    criteria = [
        "`HelpFormatter.write_table(headers, rows, *, col_spacing=2, max_col_width=None, border=False)` formats aligned multi-column tables respecting `self.current_indent`.",
        "Column widths are computed using visible character widths (`term_len`), keeping ANSI-styled cells properly aligned.",
        "When `border=True`, inserts a dashed separator row (`'-' * col_width`) directly below the header.",
        "When `max_col_width` is set (`>= 4`), truncates cells wider than `max_col_width` with `'...'`.",
        "Raises `ValueError` on empty headers, mismatched row column counts, `col_spacing < 1`, or `max_col_width < 4`, and emits no trailing spaces after the last column.",
    ]

    instruction = f"""# Feature Request: Add ANSI-Aware Multi-Column `write_table()` to `HelpFormatter`

## Background
`click.HelpFormatter` currently supports 2-column definition lists via `write_dl()`, which covers options and commands, but CLI authors rendering structured multi-column tables (status summaries, environment tables, command matrices) have to hand-roll padding and ANSI width math.

Repository: `pallets/click` (starting commit `{profile.base_commit}`).

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
""" + "\n".join(f"- {c}" for c in criteria) + "\n"

    reg_files = _pick_click_regression_files(
        profile,
        [
            "tests/test_formatting.py",
            "tests/test_basic.py",
            "tests/test_commands.py",
        ],
    )

    return TaskPackage(
        task_id="click-formatting-table",
        title=seam.title,
        category=seam.category,
        difficulty=seam.difficulty,
        repo_name=profile.repo_name,
        repo_url=profile.repo_url,
        base_commit=profile.base_commit,
        base_tag=profile.base_tag,
        license_name=profile.license_name,
        language=profile.language,
        framework=profile.framework,
        test_command=profile.test_command,
        target_files=[rel_path],
        problem_statement=instruction,
        acceptance_criteria=criteria,
        reference_patch=ref_patch,
        feature_tests_patch=test_patch,
        feature_test_files=[test_rel],
        regression_test_files=reg_files,
        feature_test_sources={test_rel: test_code},
        generator_strategy="forgebench-ast-guided",
    )


def synthesize_click_group_aliases_task(profile: RepoProfile, seam: FeatureSeam) -> TaskPackage:
    repo_root = Path(profile.repo_path)
    rel_path = seam.target_module
    original = (repo_root / rel_path).read_text(encoding="utf-8")

    old_get = '''    def get_command(self, ctx: Context, cmd_name: str) -> Command | None:
        """Given a context and a command name, this returns a :class:`Command`
        object if it exists or returns ``None``.
        """
        return self.commands.get(cmd_name)'''

    new_get = '''    def add_alias(self, alias: str, target_name: str) -> None:
        """Register an alias pointing to an existing subcommand name."""
        if not isinstance(alias, str) or not alias.strip():
            raise ValueError("Alias must be a non-empty string.")
        if not isinstance(target_name, str) or not target_name.strip():
            raise ValueError("Target command name must be a non-empty string.")
        alias_clean = alias.strip()
        target_clean = target_name.strip()
        if alias_clean == target_clean:
            raise ValueError("Alias cannot be identical to its target command name.")
        if target_clean not in self.commands:
            raise KeyError(f"Unknown target command: {target_clean!r}")
        aliases: dict[str, str] = getattr(self, "_aliases", {})
        if alias_clean in self.commands or alias_clean in aliases:
            raise ValueError(f"Alias {alias_clean!r} conflicts with an existing command or alias.")
        aliases[alias_clean] = target_clean
        self._aliases = aliases

    def resolve_alias(self, name: str) -> str | None:
        """Return the canonical command name for a registered alias, or ``None``."""
        aliases: dict[str, str] = getattr(self, "_aliases", {})
        return aliases.get(name)

    def get_aliases(self, target_name: str) -> list[str]:
        """Return sorted alias names registered for ``target_name``."""
        aliases: dict[str, str] = getattr(self, "_aliases", {})
        return sorted(alias for alias, target in aliases.items() if target == target_name)

    def get_command(self, ctx: Context, cmd_name: str) -> Command | None:
        """Given a context and a command name, this returns a :class:`Command`
        object if it exists or returns ``None``.
        """
        cmd = self.commands.get(cmd_name)
        if cmd is not None:
            return cmd
        canonical = self.resolve_alias(cmd_name)
        if canonical is not None:
            return self.commands.get(canonical)
        return None'''

    old_fmt = '''            rows = []
            for subcommand, cmd in commands:
                help = cmd.get_short_help_str(limit)
                rows.append((subcommand, help))'''

    new_fmt = '''            rows = []
            for subcommand, cmd in commands:
                cmd_aliases = self.get_aliases(subcommand)
                label = (
                    f"{subcommand} ({', '.join(cmd_aliases)})"
                    if cmd_aliases
                    else subcommand
                )
                help = cmd.get_short_help_str(limit)
                rows.append((label, help))'''

    if old_get not in original or old_fmt not in original:
        raise ValueError("Expected Group.get_command / format_commands anchors not found in core.py.")

    updated = original.replace(old_get, new_get, 1).replace(old_fmt, new_fmt, 1)
    ast.parse(updated)
    ref_patch = make_unified_diff(rel_path, original, updated)

    test_rel = "tests/test_feature_group_aliases.py"
    test_code = '''import pytest

import click
from click.testing import CliRunner


def test_group_alias_resolution_and_cli_invocation():
    @click.group()
    def cli() -> None:
        pass

    @cli.command()
    @click.argument("env")
    def deploy(env: str) -> None:
        """Deploy the service."""
        click.echo(f"deploying:{env}")

    @cli.command()
    def status() -> None:
        """Show service status."""
        click.echo("status:ok")

    cli.add_alias("dep", "deploy")
    cli.add_alias("d", "deploy")

    assert cli.resolve_alias("dep") == "deploy"
    assert cli.resolve_alias("unknown") is None
    assert cli.get_aliases("deploy") == ["d", "dep"]
    assert cli.get_aliases("status") == []

    runner = CliRunner()
    res_alias = runner.invoke(cli, ["dep", "prod"])
    assert res_alias.exit_code == 0
    assert "deploying:prod" in res_alias.output

    res_short = runner.invoke(cli, ["d", "staging"])
    assert res_short.exit_code == 0
    assert "deploying:staging" in res_short.output


def test_group_help_displays_aliases_without_altering_unaliased_commands():
    @click.group()
    def cli() -> None:
        pass

    @cli.command()
    def deploy() -> None:
        """Deploy application."""

    @cli.command()
    def status() -> None:
        """Check status."""

    cli.add_alias("dep", "deploy")
    cli.add_alias("d", "deploy")

    runner = CliRunner()
    res = runner.invoke(cli, ["--help"])
    assert res.exit_code == 0
    assert "deploy (d, dep)" in res.output
    assert "status (" not in res.output


def test_group_alias_conflict_and_validation_guards():
    @click.group()
    def cli() -> None:
        pass

    @cli.command()
    def deploy() -> None:
        pass

    @cli.command()
    def status() -> None:
        pass

    with pytest.raises(ValueError):
        cli.add_alias("   ", "deploy")
    with pytest.raises(ValueError):
        cli.add_alias("d", "")
    with pytest.raises(ValueError):
        cli.add_alias("deploy", "deploy")
    with pytest.raises(KeyError):
        cli.add_alias("ghost", "nonexistent")
    with pytest.raises(ValueError):
        cli.add_alias("status", "deploy")

    cli.add_alias("d", "deploy")
    with pytest.raises(ValueError):
        cli.add_alias("d", "status")
'''
    test_patch = make_unified_diff(test_rel, None, test_code)

    criteria = [
        "`Group.add_alias(alias, target_name)` registers an alias to an existing command while raising `ValueError` on empty strings, self-aliasing, or collision with an existing command/alias, and `KeyError` when `target_name` does not exist.",
        "`Group.resolve_alias(name)` returns the canonical command name or `None`; `Group.get_aliases(target_name)` returns sorted aliases for that target.",
        "`Group.get_command(ctx, cmd_name)` transparently resolves aliases so aliased subcommands invoke normally via CLI.",
        "`Group.format_commands(ctx, formatter)` appends `(<sorted aliases>)` next to aliased commands in `--help` output while leaving unaliased commands unchanged.",
    ]

    instruction = f"""# Feature Request: First-Class Subcommand Alias Support on `click.Group`

## Background
CLI tools frequently offer shorthand command aliases (such as `deploy` aliased as `dep` and `d`). Today Click requires users to subclass `click.Group` and manually override command lookup and help formatting.

Repository: `pallets/click` (starting commit `{profile.base_commit}`).

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
""" + "\n".join(f"- {c}" for c in criteria) + "\n"

    reg_files = _pick_click_regression_files(
        profile,
        [
            "tests/test_commands.py",
            "tests/test_command_decorators.py",
            "tests/test_formatting.py",
            "tests/test_basic.py",
        ],
    )

    return TaskPackage(
        task_id="click-group-aliases",
        title=seam.title,
        category=seam.category,
        difficulty=seam.difficulty,
        repo_name=profile.repo_name,
        repo_url=profile.repo_url,
        base_commit=profile.base_commit,
        base_tag=profile.base_tag,
        license_name=profile.license_name,
        language=profile.language,
        framework=profile.framework,
        test_command=profile.test_command,
        target_files=[rel_path],
        problem_statement=instruction,
        acceptance_criteria=criteria,
        reference_patch=ref_patch,
        feature_tests_patch=test_patch,
        feature_test_files=[test_rel],
        regression_test_files=reg_files,
        feature_test_sources={test_rel: test_code},
        generator_strategy="forgebench-ast-guided",
    )


def synthesize_click_redaction_task(profile: RepoProfile, seam: FeatureSeam) -> TaskPackage:
    repo_root = Path(profile.repo_path)
    rel_path = seam.target_module
    original = (repo_root / rel_path).read_text(encoding="utf-8")

    redaction_code = '''

def redact_text(
    text: str,
    secrets: cabc.Iterable[str | None],
    *,
    replacement: str = "[REDACTED]",
    min_secret_length: int = 1,
    case_sensitive: bool = True,
) -> str:
    """Replace sensitive tokens in ``text`` with ``replacement`` using longest-first matching."""
    if min_secret_length < 1:
        raise ValueError("min_secret_length must be at least 1.")

    candidates = sorted(
        {
            item
            for item in secrets
            if isinstance(item, str) and len(item) >= min_secret_length
        },
        key=len,
        reverse=True,
    )
    if not candidates or not text:
        return text

    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = re.compile("|".join(re.escape(secret) for secret in candidates), flags)
    return pattern.sub(lambda _match: replacement, text)


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
) -> None:
    """Echo ``message`` after redacting any supplied secret values."""
    if message is not None and secrets is not None:
        text = make_str(message)
        message = redact_text(
            text,
            secrets,
            replacement=replacement,
            min_secret_length=min_secret_length,
            case_sensitive=case_sensitive,
        )
    echo(message=message, file=file, nl=nl, err=err, color=color)
'''
    anchor = "def __getattr__(name: str) -> object:"
    if anchor in original:
        updated = original.replace(anchor, redaction_code.lstrip("\n") + "\n\n" + anchor, 1)
    else:
        updated = original.rstrip("\n") + "\n" + redaction_code
    ast.parse(updated)

    main_diff = make_unified_diff(rel_path, original, updated)
    init_diff = _insert_init_exports(
        repo_root,
        [
            "from .utils import redact_text as redact_text",
            "from .utils import safe_echo as safe_echo",
        ],
    )
    ref_patch = combine_patches([main_diff, init_diff])

    test_rel = "tests/test_feature_redact_echo.py"
    test_code = '''import pytest

import click
from click.testing import CliRunner


def test_redact_text_longest_first_and_regex_safe():
    # Shorter prefix must not leave trailing characters of longer secret exposed
    masked = click.redact_text(
        "auth=tok_prod_998877 and tok_prod and meta=a+b$c*1",
        ["tok_prod", "tok_prod_998877", "a+b$c*1", None],
    )
    assert "998877" not in masked
    assert "tok_prod" not in masked
    assert "a+b$c*1" not in masked
    assert masked == "auth=[REDACTED] and [REDACTED] and meta=[REDACTED]"


def test_redact_text_case_folding_and_min_length():
    text = "Bearer SECRET_KEY_42 and id=ab"
    assert (
        click.redact_text(
            text,
            ["secret_key_42", "ab"],
            replacement="***",
            min_secret_length=4,
            case_sensitive=False,
        )
        == "Bearer *** and id=ab"
    )
    assert click.redact_text(text, ["secret_key_42"], case_sensitive=True) == text
    assert click.redact_text("", ["secret"]) == ""
    assert click.redact_text("plain", []) == "plain"

    with pytest.raises(ValueError):
        click.redact_text("hello", ["h"], min_secret_length=0)


def test_safe_echo_cli_stdout_and_none_handling():
    @click.command()
    @click.option("--token", required=True)
    def login(token: str) -> None:
        click.safe_echo(f"Using credential {token} for deploy", secrets=[token])
        click.safe_echo(None, secrets=[token])

    runner = CliRunner()
    res = runner.invoke(login, ["--token", "ghp_liveSecret98765"])
    assert res.exit_code == 0
    assert "ghp_liveSecret98765" not in res.output
    assert "Using credential [REDACTED] for deploy" in res.output
'''
    test_patch = make_unified_diff(test_rel, None, test_code)

    criteria = [
        "`click.redact_text(text, secrets, *, replacement='[REDACTED]', min_secret_length=1, case_sensitive=True)` replaces secrets longest-first and safely escapes regex metacharacters.",
        "Ignores `None` entries and secrets shorter than `min_secret_length`; raises `ValueError` when `min_secret_length < 1`.",
        "Supports `case_sensitive=False` matching without mangling replacement strings containing backslashes.",
        "`click.safe_echo(...)` redacts non-None messages when `secrets` is provided and delegates stream/newline/color options to `click.echo()`.",
        "Both `redact_text` and `safe_echo` are exported from `click`.",
    ]

    instruction = f"""# Feature Request: Secret-Safe Output Helpers (`redact_text` and `safe_echo`)

## Background
CLI tools running in CI/CD pipelines frequently print command summaries or diagnostic messages that risk accidentally leaking API tokens, bearer credentials, or passwords into build logs.

Repository: `pallets/click` (starting commit `{profile.base_commit}`).

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
""" + "\n".join(f"- {c}" for c in criteria) + "\n"

    reg_files = _pick_click_regression_files(
        profile,
        [
            "tests/test_basic.py",
            "tests/test_imports.py",
            "tests/test_stream_lifecycle.py",
        ],
    )

    return TaskPackage(
        task_id="click-utils-redact-echo",
        title=seam.title,
        category=seam.category,
        difficulty=seam.difficulty,
        repo_name=profile.repo_name,
        repo_url=profile.repo_url,
        base_commit=profile.base_commit,
        base_tag=profile.base_tag,
        license_name=profile.license_name,
        language=profile.language,
        framework=profile.framework,
        test_command=profile.test_command,
        target_files=[rel_path, "src/click/__init__.py"],
        problem_statement=instruction,
        acceptance_criteria=criteria,
        reference_patch=ref_patch,
        feature_tests_patch=test_patch,
        feature_test_files=[test_rel],
        regression_test_files=reg_files,
        feature_test_sources={test_rel: test_code},
        generator_strategy="forgebench-ast-guided",
    )
