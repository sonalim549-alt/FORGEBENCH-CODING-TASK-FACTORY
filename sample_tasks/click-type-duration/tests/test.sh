#!/usr/bin/env bash
set -uo pipefail

TESTS_DIR="/tests"
if [[ ! -d "${TESTS_DIR}" ]]; then
    TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

LOG_DIR="/logs/verifier"
mkdir -p "${LOG_DIR}"

cd /workspace/repo
cp "${TESTS_DIR}/test_feature_duration_type.py" "/workspace/repo/tests/test_feature_duration_type.py"

python - "${TESTS_DIR}" "${LOG_DIR}" <<'PYEOF'
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
        [sys.executable, "-m", "pytest", "-q", f"--junitxml={xml_path}", *targets],
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

(log_dir / "reward.txt").write_text(f"{reward:.4f}\n", encoding="utf-8")
(log_dir / "reward_details.json").write_text(
    json.dumps(
        {
            "reward": reward,
            "regression": {"returncode": reg_rc, "passed": reg_passed, "failed": reg_failed},
            "feature": {"returncode": feat_rc, "passed": feat_passed, "failed": feat_failed, "total": feat_total},
        },
        indent=2,
    ),
    encoding="utf-8",
)
print(f"Harbor Verifier Reward: {reward:.4f} (feature={feat_passed}/{feat_total}, reg_failed={reg_failed})")
PYEOF
