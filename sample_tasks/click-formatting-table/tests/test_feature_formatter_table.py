import pytest

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
    assert fmt.getvalue() == "ColA  ColB\n"

    with pytest.raises(ValueError):
        fmt.write_table([], [])
    with pytest.raises(ValueError):
        fmt.write_table(["A", "B"], [["only-one"]])
    with pytest.raises(ValueError):
        fmt.write_table(["A"], [["1"]], col_spacing=0)
    with pytest.raises(ValueError):
        fmt.write_table(["A"], [["1"]], max_col_width=3)
