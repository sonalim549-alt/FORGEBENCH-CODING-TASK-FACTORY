from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from forgebench.models import TaskPackage
from forgebench.security import run_bounded_command


def _write_lf(path: Path, text: str) -> None:
    """Write UTF-8 text with strict Unix LF line endings (required for Linux Docker scripts)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(normalized)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _render_task_toml(package: TaskPackage) -> str:
    repo_slug = package.repo_name
    if package.repo_url and "github.com/" in package.repo_url:
        repo_slug = package.repo_url.split("github.com/", 1)[1].rstrip("/").removesuffix(".git")

    title_escaped = package.title.replace('"', '\\"')
    tags = [package.language, package.repo_name, package.category, "feature-development"]
    tags_toml = ", ".join(f'"{t}"' for t in tags)

    return f'''version = "1.0"

[metadata]
task_id = "{package.task_id}"
title = "{title_escaped}"
difficulty = "{package.difficulty}"
category = "feature-development"
tags = [{tags_toml}]
repository = "{repo_slug}"
repository_url = "{package.repo_url or 'https://github.com/pallets/click'}"
base_commit = "{package.base_commit}"
base_tag = "{package.base_tag}"
license = "{package.license_name}"
generator_strategy = "{package.generator_strategy}"

[verifier]
timeout_sec = 300.0

[agent]
timeout_sec = 600.0

[environment]
build_timeout_sec = 600.0
cpus = 2
memory_mb = 4096
storage_mb = 10240
'''


def _render_dockerfile(package: TaskPackage) -> str:
    repo_url = package.repo_url or "https://github.com/pallets/click.git"
    if repo_url.startswith("https://github.com/") and not repo_url.endswith(".git"):
        repo_url = repo_url.rstrip("/") + ".git"

    return f'''FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1 \\
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \\
    git \\
    patch \\
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace/repo

RUN git clone {repo_url} /workspace/repo \\
    && git checkout {package.base_commit} \\
    && pip install --upgrade pip \\
    && pip install -e . pytest
'''


def _render_solve_sh() -> str:
    return '''#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd /workspace/repo

if [[ -f "${SCRIPT_DIR}/reference.patch" ]]; then
    git apply --whitespace=nowarn "${SCRIPT_DIR}/reference.patch"
else
    echo "ERROR: reference.patch not found in ${SCRIPT_DIR}" >&2
    exit 1
fi
'''


def _render_test_sh(feature_test_filenames: list[str]) -> str:
    copy_lines = "\n".join(
        f'cp "${{TESTS_DIR}}/{Path(rel).name}" "/workspace/repo/{rel}"'
        for rel in feature_test_filenames
    )
    return f'''#!/usr/bin/env bash
set -uo pipefail

TESTS_DIR="/tests"
if [[ ! -d "${{TESTS_DIR}}" ]]; then
    TESTS_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
fi

LOG_DIR="/logs/verifier"
mkdir -p "${{LOG_DIR}}"

cd /workspace/repo
{copy_lines}

python - "${{TESTS_DIR}}" "${{LOG_DIR}}" <<'PYEOF'
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

tests_dir = Path(sys.argv[1])
log_dir = Path(sys.argv[2])

fail_to_pass = json.loads((tests_dir / "fail_to_pass.json").read_text(encoding="utf-8"))
pass_to_pass = json.loads((tests_dir / "pass_to_pass.json").read_text(encoding="utf-8"))


def run_pytest_suite(targets: list[str], xml_path: Path) -> tuple[int, int, int]:
    if not targets:
        return 0, 0, 0
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", f"--junitxml={{xml_path}}", *targets],
        capture_output=True,
        text=True,
    )
    if not xml_path.is_file():
        return proc.returncode, 0, len(targets)

    tree = ET.parse(xml_path)
    root = tree.getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    total = sum(int(s.attrib.get("tests", 0)) for s in suites)
    failures = sum(int(s.attrib.get("failures", 0)) for s in suites)
    errors = sum(int(s.attrib.get("errors", 0)) for s in suites)
    skipped = sum(int(s.attrib.get("skipped", 0)) for s in suites)
    passed = max(0, total - failures - errors - skipped)
    failed = failures + errors
    return proc.returncode, passed, failed


reg_rc, reg_passed, reg_failed = run_pytest_suite(pass_to_pass, log_dir / "regression.xml")
feat_rc, feat_passed, feat_failed = run_pytest_suite(fail_to_pass, log_dir / "feature.xml")

# Innodata Harbor v0.25.x Reward Computation:
# - If ANY regression test fails -> reward = 0.0
# - Else if ALL feature tests pass -> reward = 1.0
# - Else -> share of passing feature tests
feat_total = feat_passed + feat_failed
if reg_rc != 0 or reg_failed > 0:
    reward = 0.0
elif feat_total == 0:
    reward = 0.0
elif feat_rc == 0 and feat_failed == 0 and feat_passed == feat_total:
    reward = 1.0
else:
    reward = round(feat_passed / feat_total, 4)

(log_dir / "reward.txt").write_text(f"{{reward:.4f}}\\n", encoding="utf-8")
(log_dir / "reward_details.json").write_text(
    json.dumps(
        {{
            "reward": reward,
            "regression": {{"returncode": reg_rc, "passed": reg_passed, "failed": reg_failed}},
            "feature": {{"returncode": feat_rc, "passed": feat_passed, "failed": feat_failed, "total": feat_total}},
        }},
        indent=2,
    ),
    encoding="utf-8",
)
print(f"Harbor Verifier Reward: {{reward:.4f}} (feature={{feat_passed}}/{{feat_total}}, reg_failed={{reg_failed}})")
PYEOF
'''


def export_harbor_package(package: TaskPackage, output_root: Path) -> Path:
    """
    Export a certified TaskPackage conforming strictly to Harbor v0.25.x layout:
      <task_id>/
        task.toml
        instruction.md
        environment/Dockerfile
        solution/solve.sh
        solution/reference.patch
        tests/test.sh
        tests/fail_to_pass.json
        tests/pass_to_pass.json
        tests/test_feature_*.py
    Validation reports are stored cleanly in <output_root>/_validation_evidence/.
    """
    task_dir = output_root / package.task_id
    if task_dir.exists():
        shutil.rmtree(task_dir)
    task_dir.mkdir(parents=True, exist_ok=True)

    task_toml = _render_task_toml(package)
    dockerfile = _render_dockerfile(package)
    solve_sh = _render_solve_sh()
    test_sh = _render_test_sh(package.feature_test_files)

    _write_lf(task_dir / "task.toml", task_toml)
    _write_lf(task_dir / "instruction.md", package.problem_statement)
    _write_lf(task_dir / "environment" / "Dockerfile", dockerfile)
    _write_lf(task_dir / "solution" / "solve.sh", solve_sh)
    _write_lf(task_dir / "solution" / "reference.patch", package.reference_patch)
    _write_lf(task_dir / "tests" / "test.sh", test_sh)

    # Write individual feature test files into tests/ for Harbor verifier mounting.
    for rel_path, content in package.feature_test_sources.items():
        _write_lf(task_dir / "tests" / Path(rel_path).name, content)

    fail_to_pass = (
        package.validation_report.fail_to_pass
        if package.validation_report and package.validation_report.fail_to_pass
        else package.feature_test_files
    )
    pass_to_pass = (
        package.validation_report.pass_to_pass
        if package.validation_report and package.validation_report.pass_to_pass
        else package.regression_test_files
    )

    _write_lf(
        task_dir / "tests" / "fail_to_pass.json",
        json.dumps(fail_to_pass, indent=2) + "\n",
    )
    _write_lf(
        task_dir / "tests" / "pass_to_pass.json",
        json.dumps(pass_to_pass, indent=2) + "\n",
    )

    # Write tamper-evident validation report outside the Harbor task folder
    # so the task folder stays strictly compliant with the 5-component Harbor spec.
    if package.validation_report is not None:
        package.validation_report.artifact_checksums = {
            "task.toml": _sha256_text(task_toml),
            "instruction.md": _sha256_text(package.problem_statement),
            "environment/Dockerfile": _sha256_text(dockerfile),
            "solution/reference.patch": _sha256_text(package.reference_patch),
            "tests/test.sh": _sha256_text(test_sh),
        }
        ev_dir = output_root / "_validation_evidence"
        ev_dir.mkdir(parents=True, exist_ok=True)
        _write_lf(
            ev_dir / f"{package.task_id}.validation.json",
            json.dumps(package.validation_report.to_dict(), indent=2) + "\n",
        )

    return task_dir


def run_harbor_cli_checks(task_dir: Path, results_root: Path) -> dict[str, Any]:
    """
    Invoke Harbor v0.25.x CLI (`nop` and `oracle` passes) when Docker + Harbor are installed.
    Returns structured status if Harbor/Docker is unavailable on the local workstation.
    """
    if shutil.which("harbor") is None or shutil.which("docker") is None:
        return {
            "executed": False,
            "reason": "Harbor CLI or Docker daemon not found on local PATH; run on a Docker-enabled host.",
            "commands": {
                "nop": f"harbor run -p {task_dir} -a nop -k 1 -o {results_root / 'nop'} --no-delete -y",
                "oracle": f"harbor run -p {task_dir} -a oracle -k 1 -o {results_root / 'oracle'} --no-delete -y",
            },
        }

    nop_out = results_root / task_dir.name / "nop"
    oracle_out = results_root / task_dir.name / "oracle"
    nop_out.mkdir(parents=True, exist_ok=True)
    oracle_out.mkdir(parents=True, exist_ok=True)

    nop_proc = run_bounded_command(
        ["harbor", "run", "-p", str(task_dir), "-a", "nop", "-k", "1", "-o", str(nop_out), "--no-delete", "-y"],
        cwd=task_dir.parent,
        timeout_sec=600,
    )
    oracle_proc = run_bounded_command(
        ["harbor", "run", "-p", str(task_dir), "-a", "oracle", "-k", "1", "-o", str(oracle_out), "--no-delete", "-y"],
        cwd=task_dir.parent,
        timeout_sec=600,
    )
    return {
        "executed": True,
        "nop_exit_code": nop_proc.returncode,
        "oracle_exit_code": oracle_proc.returncode,
        "nop_stdout": nop_proc.stdout[-1500:],
        "oracle_stdout": oracle_proc.stdout[-1500:],
    }
