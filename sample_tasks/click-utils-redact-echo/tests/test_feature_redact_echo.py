import pytest

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
