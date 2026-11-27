from datetime import timedelta

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
