from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from forgebench.models import SecurityFinding

# Environment variables stripped when running untrusted repository tests so
# target code cannot harvest host tokens or provider keys.
SENSITIVE_ENV_PREFIXES = (
    "OPENAI_",
    "GEMINI_",
    "GOOGLE_API",
    "ANTHROPIC_",
    "AWS_",
    "AZURE_",
    "GCP_",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "FORGEBENCH_API_KEY",
    "HF_TOKEN",
)

DANGEROUS_PATTERNS: list[tuple[str, str, re.Pattern[str], str]] = [
    (
        "HIGH",
        "SEC001",
        re.compile(r"\b(eval|exec)\s*\("),
        "Dynamic code execution (eval/exec) is disallowed in generated patches.",
    ),
    (
        "HIGH",
        "SEC002",
        re.compile(r"subprocess\.\w+\([^)]*shell\s*=\s*True"),
        "Shell injection risk: subprocess call uses shell=True.",
    ),
    (
        "HIGH",
        "SEC003",
        re.compile(r"\bos\.system\s*\("),
        "Avoid os.system(); use safe argument lists or pure Python APIs.",
    ),
    (
        "HIGH",
        "SEC004",
        re.compile(r"\bpickle\.loads?\s*\("),
        "Unsafe deserialization via pickle detected.",
    ),
    (
        "HIGH",
        "SEC005",
        re.compile(
            r"(?i)(api[_-]?key|secret[_-]?key|password|token)\s*=\s*['\"][A-Za-z0-9_\-]{16,}['\"]"
        ),
        "Potential hardcoded credential or secret detected.",
    ),
    (
        "MEDIUM",
        "SEC006",
        re.compile(r"\b(requests\.(get|post|put|delete)|urllib\.request\.urlopen)\s*\("),
        "Outbound network call detected; evaluation tests should run deterministically offline.",
    ),
]

UNSAFE_DIFF_PATH_RE = re.compile(r"^(?:\+\+\+|---)\s+[ab]/(.+)$")


def build_sanitized_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return a clean environment dictionary stripped of host API credentials."""
    cleaned: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if any(upper.startswith(prefix) for prefix in SENSITIVE_ENV_PREFIXES):
            continue
        cleaned[key] = value
    cleaned["PYTHONDONTWRITEBYTECODE"] = "1"
    cleaned["PYTHONUTF8"] = "1"
    if extra:
        cleaned.update(extra)
    return cleaned


def run_bounded_command(
    command: list[str],
    cwd: Path,
    timeout_sec: int = 90,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute a subprocess with shell=False, timeout, and sanitized environment."""
    return subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_sec,
        shell=False,
        env=env if env is not None else build_sanitized_env(),
    )


def ensure_within_root(base_dir: Path, target_path: Path) -> Path:
    """Resolve target_path and guarantee it stays inside base_dir (prevents path traversal)."""
    root = base_dir.resolve()
    resolved = target_path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"Path traversal blocked: {target_path} escapes {root}"
        ) from exc
    return resolved


def audit_patch_security(patch_text: str, label: str = "patch") -> list[SecurityFinding]:
    """Inspect a unified diff for path traversal, secrets, and unsafe constructs."""
    findings: list[SecurityFinding] = []
    current_file = label
    added_line_no = 0

    for raw_line in patch_text.splitlines():
        path_match = UNSAFE_DIFF_PATH_RE.match(raw_line)
        if path_match:
            candidate = path_match.group(1).strip()
            current_file = candidate
            parts = Path(candidate).parts
            if candidate.startswith("/") or ".." in parts or ":" in candidate:
                findings.append(
                    SecurityFinding(
                        severity="HIGH",
                        rule_id="SEC000",
                        file_path=candidate,
                        line_number=0,
                        message="Unified diff references an absolute or parent-escaping path.",
                    )
                )
            continue

        if raw_line.startswith("@@"):
            # Extract target hunk start line: @@ -a,b +c,d @@
            hunk_match = re.search(r"\+(\d+)", raw_line)
            added_line_no = int(hunk_match.group(1)) if hunk_match else 0
            continue

        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            code_line = raw_line[1:]
            for severity, rule_id, pattern, message in DANGEROUS_PATTERNS:
                if pattern.search(code_line):
                    findings.append(
                        SecurityFinding(
                            severity=severity,
                            rule_id=rule_id,
                            file_path=current_file,
                            line_number=added_line_no,
                            message=message,
                        )
                    )
            added_line_no += 1
        elif not raw_line.startswith("-"):
            added_line_no += 1

    return findings
