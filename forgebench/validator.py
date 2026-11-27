from __future__ import annotations

import os
import platform
import shutil
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from forgebench.models import (
    GateResult,
    MutationOutcome,
    RepoProfile,
    TaskPackage,
    ValidationReport,
)
from forgebench.mutator import generate_targeted_mutants
from forgebench.patch_utils import apply_unified_patch
from forgebench.security import (
    audit_patch_security,
    build_sanitized_env,
    run_bounded_command,
)


def _tail(text: str, max_chars: int = 1200) -> str:
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return "...(truncated)...\n" + cleaned[-max_chars:]


def _repo_env(repo_dir: Path, src_root: str) -> dict[str, str]:
    """Ensure isolated scratch repository code shadows any globally installed package."""
    import_root = (repo_dir / src_root) if src_root else repo_dir
    existing_pp = os.environ.get("PYTHONPATH", "")
    new_pp = (
        f"{import_root}{os.pathsep}{existing_pp}"
        if existing_pp
        else str(import_root)
    )
    return build_sanitized_env({"PYTHONPATH": new_pp})


def _prepare_isolated_copy(source_repo: Path, scratch_root: Path, name: str) -> Path:
    """Create an isolated copy of the repository at HEAD without mutating source."""
    dest = scratch_root / name
    if dest.exists():
        shutil.rmtree(dest)

    def _ignore(_src: str, names: list[str]) -> set[str]:
        skip = {
            "__pycache__",
            ".pytest_cache",
            ".venv",
            "venv",
            "output_tasks",
            "sample_tasks",
            "repos",
        }
        return {n for n in names if n in skip}

    shutil.copytree(source_repo, dest, ignore=_ignore)
    if (dest / ".git").exists():
        run_bounded_command(["git", "checkout", "--", "."], cwd=dest, timeout_sec=20)
    return dest


def _feature_test_command(profile: RepoProfile, feature_test_files: list[str]) -> list[str]:
    if profile.test_runner == "pytest":
        return [*profile.test_command, *feature_test_files]
    return profile.test_command


def _collect_pytest_node_ids(
    repo_dir: Path,
    profile: RepoProfile,
    targets: list[str],
    env: dict[str, str],
) -> list[str]:
    if profile.test_runner != "pytest" or not targets:
        return list(targets)
    proc = run_bounded_command(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *targets],
        cwd=repo_dir,
        timeout_sec=45,
        env=env,
    )
    if proc.returncode != 0:
        return list(targets)
    node_ids = [
        line.strip()
        for line in proc.stdout.splitlines()
        if "::" in line and not line.strip().startswith(("=", "-"))
    ]
    return node_ids or list(targets)


def _parse_junit_counts(xml_path: Path) -> tuple[int, int]:
    """Return (passed_count, failed_or_error_count) from pytest JUnit XML."""
    if not xml_path.is_file():
        return 0, 1
    tree = ET.parse(xml_path)
    root = tree.getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    total = sum(int(s.attrib.get("tests", 0)) for s in suites)
    failures = sum(int(s.attrib.get("failures", 0)) for s in suites)
    errors = sum(int(s.attrib.get("errors", 0)) for s in suites)
    skipped = sum(int(s.attrib.get("skipped", 0)) for s in suites)
    passed = max(0, total - failures - errors - skipped)
    return passed, failures + errors


def _compute_harbor_reward(
    reg_rc: int, feat_passed: int, feat_failed: int, feat_rc: int
) -> float:
    """Exact Innodata Harbor v0.25.x reward formula."""
    if reg_rc != 0:
        return 0.0
    total = feat_passed + feat_failed
    if total == 0:
        return 0.0
    if feat_rc == 0 and feat_failed == 0 and feat_passed == total:
        return 1.0
    return round(feat_passed / total, 4)


def validate_task_package(
    profile: RepoProfile,
    package: TaskPackage,
    *,
    timeout_sec: int = 120,
    max_mutants: int = 5,
    min_mutation_kill_rate: float = 0.70,
) -> ValidationReport:
    """
    Execute the 4-Gate Certification Pipeline in isolated scratch workspaces:
      Gate 1: Baseline repository health check
      Gate 2: Harbor nop / Missing-feature detection (expected reward == 0.0)
      Gate 3: Incorrect-behavior detection (Targeted AST mutation testing)
      Gate 4: Harbor oracle (expected reward == 1.0) + full regression + security audit
    """
    source_repo = Path(profile.repo_path)
    gates: list[GateResult] = []
    mutations: list[MutationOutcome] = []
    nop_reward = 1.0
    oracle_reward = 0.0
    fail_to_pass_ids: list[str] = list(package.feature_test_files)
    pass_to_pass_ids: list[str] = list(package.regression_test_files)

    security_findings = audit_patch_security(
        package.reference_patch, label="reference.patch"
    ) + audit_patch_security(package.feature_tests_patch, label="feature_tests.patch")

    with tempfile.TemporaryDirectory(prefix="forgebench_val_") as tmp_dir:
        scratch = Path(tmp_dir)

        # ------------------------------------------------------------------
        # Gate 1: Baseline Health Check
        # ------------------------------------------------------------------
        g1_dir = _prepare_isolated_copy(source_repo, scratch, "gate1_baseline")
        g1_env = _repo_env(g1_dir, profile.src_root)
        t0 = time.perf_counter()
        g1_proc = run_bounded_command(
            profile.test_command, cwd=g1_dir, timeout_sec=timeout_sec, env=g1_env
        )
        g1_ms = (time.perf_counter() - t0) * 1000
        g1_passed = g1_proc.returncode == 0
        gates.append(
            GateResult(
                name="gate1_baseline_health",
                passed=g1_passed,
                duration_ms=round(g1_ms, 2),
                summary=(
                    "Baseline repository test suite passed cleanly."
                    if g1_passed
                    else f"Baseline repository tests failed (exit {g1_proc.returncode})."
                ),
                command=profile.test_command,
                stdout_tail=_tail(g1_proc.stdout),
                stderr_tail=_tail(g1_proc.stderr),
            )
        )

        # ------------------------------------------------------------------
        # Gate 2: Harbor nop / Fail-to-Pass (Feature tests MUST fail cleanly -> reward 0.0)
        # ------------------------------------------------------------------
        g2_dir = _prepare_isolated_copy(source_repo, scratch, "gate2_nop")
        g2_env = _repo_env(g2_dir, profile.src_root)
        t0 = time.perf_counter()
        applied_ok, apply_msg = apply_unified_patch(
            g2_dir, package.feature_tests_patch
        )
        f_cmd = _feature_test_command(profile, package.feature_test_files)
        nop_xml = scratch / "nop_feature.xml"
        nop_cmd = (
            [*f_cmd, f"--junitxml={nop_xml}"]
            if profile.test_runner == "pytest"
            else f_cmd
        )
        if not applied_ok:
            g2_ms = (time.perf_counter() - t0) * 1000
            nop_reward = 0.0
            gates.append(
                GateResult(
                    name="gate2_missing_feature_detection",
                    passed=False,
                    duration_ms=round(g2_ms, 2),
                    summary=f"Failed to apply feature_tests.patch on base commit: {apply_msg}",
                    command=nop_cmd,
                )
            )
        else:
            g2_proc = run_bounded_command(
                nop_cmd, cwd=g2_dir, timeout_sec=timeout_sec, env=g2_env
            )
            g2_ms = (time.perf_counter() - t0) * 1000
            if profile.test_runner == "pytest":
                nop_passed, nop_failed = _parse_junit_counts(nop_xml)
                nop_reward = _compute_harbor_reward(
                    0 if g1_passed else 1, nop_passed, nop_failed, g2_proc.returncode
                )
            else:
                nop_reward = 0.0 if g2_proc.returncode != 0 else 1.0

            g2_passed = g2_proc.returncode != 0 and nop_reward == 0.0
            gates.append(
                GateResult(
                    name="gate2_missing_feature_detection",
                    passed=g2_passed,
                    duration_ms=round(g2_ms, 2),
                    summary=(
                        f"Harbor nop verified: feature tests failed on base commit (nop_reward={nop_reward:.1f})."
                        if g2_passed
                        else f"REJECTED: nop check produced unexpected reward={nop_reward:.4f} (exit={g2_proc.returncode})."
                    ),
                    command=f_cmd,
                    stdout_tail=_tail(g2_proc.stdout),
                    stderr_tail=_tail(g2_proc.stderr),
                )
            )

        # ------------------------------------------------------------------
        # Gate 4 + Gate 3 (Oracle + Regression + Targeted AST Mutation Check)
        # ------------------------------------------------------------------
        g4_dir = _prepare_isolated_copy(source_repo, scratch, "gate4_oracle")
        g4_env = _repo_env(g4_dir, profile.src_root)
        t0 = time.perf_counter()
        ref_ok, ref_msg = apply_unified_patch(g4_dir, package.reference_patch)
        tst_ok, tst_msg = (
            apply_unified_patch(g4_dir, package.feature_tests_patch)
            if ref_ok
            else (False, "Skipped due to reference patch failure.")
        )

        high_sec = [f for f in security_findings if f.severity == "HIGH"]

        if not ref_ok or not tst_ok:
            g4_ms = (time.perf_counter() - t0) * 1000
            gates.append(
                GateResult(
                    name="gate3_incorrect_behavior_mutation",
                    passed=False,
                    duration_ms=0.0,
                    summary="Skipped: reference/test patch failed to apply cleanly.",
                )
            )
            gates.append(
                GateResult(
                    name="gate4_reference_regression_security",
                    passed=False,
                    duration_ms=round(g4_ms, 2),
                    summary=f"Patch application failed (ref={ref_msg}; test={tst_msg}).",
                )
            )
        else:
            oracle_xml = scratch / "oracle_feature.xml"
            oracle_f_cmd = (
                [*f_cmd, f"--junitxml={oracle_xml}"]
                if profile.test_runner == "pytest"
                else f_cmd
            )
            feat_proc = run_bounded_command(
                oracle_f_cmd, cwd=g4_dir, timeout_sec=timeout_sec, env=g4_env
            )
            reg_proc = run_bounded_command(
                profile.test_command, cwd=g4_dir, timeout_sec=timeout_sec, env=g4_env
            )
            g4_ms = (time.perf_counter() - t0) * 1000

            if profile.test_runner == "pytest":
                f_passed, f_failed = _parse_junit_counts(oracle_xml)
                oracle_reward = _compute_harbor_reward(
                    reg_proc.returncode, f_passed, f_failed, feat_proc.returncode
                )
                fail_to_pass_ids = _collect_pytest_node_ids(
                    g4_dir, profile, package.feature_test_files, g4_env
                )
                if package.regression_test_files:
                    pass_to_pass_ids = list(package.regression_test_files)
            else:
                oracle_reward = (
                    1.0
                    if feat_proc.returncode == 0 and reg_proc.returncode == 0
                    else 0.0
                )

            # --------------------------------------------------------------
            # Gate 3: Targeted AST Mutation Testing (Incorrect Behavior Check)
            # --------------------------------------------------------------
            m_t0 = time.perf_counter()
            if feat_proc.returncode == 0:
                candidates = generate_targeted_mutants(
                    g4_dir, package.reference_patch, max_mutants=max_mutants
                )
                for cand in candidates:
                    target_file = g4_dir / cand.rel_path
                    clean_source = target_file.read_text(encoding="utf-8")
                    try:
                        target_file.write_text(cand.mutated_source, encoding="utf-8")
                        m_proc = run_bounded_command(
                            f_cmd, cwd=g4_dir, timeout_sec=timeout_sec, env=g4_env
                        )
                        mutations.append(
                            MutationOutcome(
                                mutant_id=cand.mutant_id,
                                file_path=cand.rel_path,
                                line_number=cand.line_number,
                                operator=cand.operator,
                                description=cand.description,
                                killed=m_proc.returncode != 0,
                                exit_code=m_proc.returncode,
                            )
                        )
                    finally:
                        target_file.write_text(clean_source, encoding="utf-8")

            m_ms = (time.perf_counter() - m_t0) * 1000
            killed_count = sum(1 for m in mutations if m.killed)
            kill_rate = (killed_count / len(mutations)) if mutations else 0.0
            g3_passed = (
                feat_proc.returncode == 0
                and len(mutations) > 0
                and kill_rate >= min_mutation_kill_rate
            )
            gates.append(
                GateResult(
                    name="gate3_incorrect_behavior_mutation",
                    passed=g3_passed,
                    duration_ms=round(m_ms, 2),
                    summary=(
                        f"Killed {killed_count}/{len(mutations)} targeted AST mutants "
                        f"({kill_rate * 100:.0f}% >= {min_mutation_kill_rate * 100:.0f}% threshold)."
                        if mutations
                        else "No mutants could be evaluated because feature tests did not pass."
                    ),
                    command=f_cmd,
                )
            )

            g4_passed = (
                oracle_reward == 1.0
                and feat_proc.returncode == 0
                and reg_proc.returncode == 0
                and len(high_sec) == 0
            )
            failure_reasons: list[str] = []
            if feat_proc.returncode != 0:
                failure_reasons.append(f"feature tests failed (exit {feat_proc.returncode})")
            if reg_proc.returncode != 0:
                failure_reasons.append(f"regression suite failed (exit {reg_proc.returncode})")
            if high_sec:
                failure_reasons.append(f"{len(high_sec)} HIGH security findings")

            gates.append(
                GateResult(
                    name="gate4_reference_regression_security",
                    passed=g4_passed,
                    duration_ms=round(g4_ms, 2),
                    summary=(
                        f"Harbor oracle verified (oracle_reward={oracle_reward:.1f}): all feature & regression tests passed with 0 HIGH security issues."
                        if g4_passed
                        else "Gate 4 failed: " + "; ".join(failure_reasons)
                    ),
                    command=profile.test_command,
                    stdout_tail=_tail(reg_proc.stdout),
                    stderr_tail=_tail(reg_proc.stderr),
                )
            )

    killed_total = sum(1 for m in mutations if m.killed)
    final_kill_rate = round((killed_total / len(mutations)) if mutations else 0.0, 4)
    certified = all(g.passed for g in gates)

    report = ValidationReport(
        task_id=package.task_id,
        repo_name=profile.repo_name,
        base_commit=profile.base_commit,
        certified=certified,
        verified_at=datetime.now(timezone.utc).isoformat(),
        runtime_env={
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "test_runner": profile.test_runner,
        },
        gates=gates,
        mutations=mutations,
        mutation_kill_rate=final_kill_rate,
        security_findings=security_findings,
        nop_reward=nop_reward,
        oracle_reward=oracle_reward,
        fail_to_pass=fail_to_pass_ids,
        pass_to_pass=pass_to_pass_ids,
    )
    package.validation_report = report
    return report
