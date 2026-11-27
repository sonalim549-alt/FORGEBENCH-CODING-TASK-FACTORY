from __future__ import annotations

from pathlib import Path

import pytest

from forgebench.mutator import generate_targeted_mutants
from forgebench.patch_utils import make_unified_diff
from forgebench.security import (
    audit_patch_security,
    build_sanitized_env,
    ensure_within_root,
)


def test_security_audit_flags_dangerous_patterns_and_traversal():
    bad_patch = (
        "diff --git a/../escape.py b/../escape.py\n"
        "--- a/../escape.py\n"
        "+++ b/../escape.py\n"
        "@@ -0,0 +1,4 @@\n"
        "+import subprocess\n"
        "+def run(cmd):\n"
        "+    return eval(cmd)\n"
        "+    subprocess.Popen(cmd, shell=True)\n"
    )
    findings = audit_patch_security(bad_patch)
    rule_ids = {f.rule_id for f in findings}
    assert "SEC000" in rule_ids
    assert "SEC001" in rule_ids
    assert "SEC002" in rule_ids


def test_ensure_within_root_blocks_escape(tmp_path: Path):
    safe = ensure_within_root(tmp_path, tmp_path / "sub" / "file.txt")
    assert str(safe).startswith(str(tmp_path.resolve()))
    with pytest.raises(ValueError):
        ensure_within_root(tmp_path, tmp_path / ".." / "outside.txt")


def test_sanitized_env_strips_provider_secrets(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value")
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-secret-value")
    monkeypatch.setenv("SAFE_APP_FLAG", "enabled")
    cleaned = build_sanitized_env()
    assert "OPENAI_API_KEY" not in cleaned
    assert "GEMINI_API_KEY" not in cleaned
    assert cleaned["SAFE_APP_FLAG"] == "enabled"


def test_targeted_mutator_only_mutates_added_lines(tmp_path: Path):
    original = "def existing(x):\n    if x == 1:\n        return True\n    return False\n"
    updated = (
        original
        + "\ndef added_rule(value):\n"
        + "    if value > 10 and value != 20:\n"
        + "        return value + 1\n"
        + "    return 0\n"
    )
    target = tmp_path / "rules.py"
    target.write_text(updated, encoding="utf-8")

    diff = make_unified_diff("rules.py", original, updated)
    mutants = generate_targeted_mutants(tmp_path, diff, max_mutants=5)
    assert len(mutants) >= 2
    assert all(m.line_number >= 6 for m in mutants)
    assert all(m.mutated_source != updated for m in mutants)
