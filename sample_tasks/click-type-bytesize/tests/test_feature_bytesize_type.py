import pytest

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
