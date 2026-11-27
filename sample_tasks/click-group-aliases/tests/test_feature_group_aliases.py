import pytest

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
