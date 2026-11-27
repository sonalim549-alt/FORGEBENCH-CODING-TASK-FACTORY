from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forgebench.adapters import TaskInterfaceAdapter, get_adapter
from forgebench.analyzer import analyze_repository
from forgebench.harbor import run_harbor_cli_checks
from forgebench.models import FeatureSeam, RepoProfile, TaskPackage
from forgebench.providers import LLMProvider, build_provider
from forgebench.synthesizer import OFFLINE_BLUEPRINTS, synthesize_task
from forgebench.validator import validate_task_package


@dataclass(slots=True)
class FactoryRunResult:
    repo_name: str
    base_commit: str
    total_attempted: int
    certified_count: int
    rejected_count: int
    duration_sec: float
    exported_paths: list[str] = field(default_factory=list)
    packages: list[TaskPackage] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary_dict(self) -> dict[str, Any]:
        return {
            "repo_name": self.repo_name,
            "base_commit": self.base_commit,
            "total_attempted": self.total_attempted,
            "certified_count": self.certified_count,
            "rejected_count": self.rejected_count,
            "duration_sec": round(self.duration_sec, 2),
            "exported_paths": self.exported_paths,
            "errors": self.errors,
            "tasks": [
                {
                    "task_id": p.task_id,
                    "title": p.title,
                    "category": p.category,
                    "difficulty": p.difficulty,
                    "certified": bool(
                        p.validation_report and p.validation_report.certified
                    ),
                    "nop_reward": (
                        p.validation_report.nop_reward
                        if p.validation_report
                        else None
                    ),
                    "oracle_reward": (
                        p.validation_report.oracle_reward
                        if p.validation_report
                        else None
                    ),
                    "mutation_kill_rate": (
                        p.validation_report.mutation_kill_rate
                        if p.validation_report
                        else 0.0
                    ),
                }
                for p in self.packages
            ],
        }


def _process_single_seam(
    profile: RepoProfile,
    seam: FeatureSeam,
    provider: LLMProvider | None,
    max_retries: int,
    max_mutants: int,
    min_kill_rate: float,
    timeout_sec: int,
) -> TaskPackage:
    feedback = ""
    last_package: TaskPackage | None = None
    attempts = (max_retries + 1) if provider is not None else 1

    for _ in range(attempts):
        pkg = synthesize_task(profile, seam, provider=provider, feedback=feedback)
        report = validate_task_package(
            profile,
            pkg,
            timeout_sec=timeout_sec,
            max_mutants=max_mutants,
            min_mutation_kill_rate=min_kill_rate,
        )
        last_package = pkg
        if report.certified:
            return pkg
        failed_notes = [
            f"{g.name}: {g.summary}" for g in report.gates if not g.passed
        ]
        feedback = "\n".join(failed_notes)

    assert last_package is not None
    return last_package


def run_factory(
    repo_path: str | Path,
    output_dir: str | Path,
    *,
    max_tasks: int = 5,
    workers: int = 2,
    adapter_format: str = "harbor",
    interface_config: Path | None = None,
    provider_name: str = "offline",
    model_name: str | None = None,
    max_retries: int = 1,
    max_mutants: int = 5,
    min_kill_rate: float = 0.70,
    timeout_sec: int = 120,
    export_rejected: bool = False,
    harbor_verify: bool = False,
) -> FactoryRunResult:
    """Analyze a repository, synthesize realistic feature tasks, and run 4-Gate validation in parallel."""
    t0 = time.perf_counter()
    profile = analyze_repository(repo_path)
    out_root = Path(output_dir).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    adapter: TaskInterfaceAdapter = get_adapter(adapter_format, interface_config)
    provider = build_provider(provider_name, model=model_name)

    supported_offline = set(OFFLINE_BLUEPRINTS.keys())
    eligible_seams = [
        s
        for s in profile.seams
        if provider is not None or s.category in supported_offline
    ]
    selected_seams = eligible_seams[: max(1, max_tasks)]

    packages: list[TaskPackage] = []
    errors: list[str] = []
    exported_paths: list[str] = []

    bound_workers = max(1, min(workers, len(selected_seams) or 1))
    with ThreadPoolExecutor(max_workers=bound_workers) as pool:
        future_map = {
            pool.submit(
                _process_single_seam,
                profile,
                seam,
                provider,
                max_retries,
                max_mutants,
                min_kill_rate,
                timeout_sec,
            ): seam
            for seam in selected_seams
        }
        for future in as_completed(future_map):
            seam = future_map[future]
            try:
                pkg = future.result()
                packages.append(pkg)
            except Exception as exc:
                errors.append(f"{seam.seam_id}: {exc}")

    packages.sort(key=lambda p: p.task_id)

    certified = 0
    rejected = 0
    for pkg in packages:
        is_cert = bool(pkg.validation_report and pkg.validation_report.certified)
        if is_cert:
            certified += 1
            dest = adapter.export(pkg, out_root)
            exported_paths.append(str(dest))
            if harbor_verify and adapter_format == "harbor":
                cli_res = run_harbor_cli_checks(dest, out_root / "_harbor_cli_runs")
                ev_path = out_root / "_validation_evidence" / f"{pkg.task_id}.harbor_cli.json"
                ev_path.parent.mkdir(parents=True, exist_ok=True)
                ev_path.write_text(json.dumps(cli_res, indent=2), encoding="utf-8")
        else:
            rejected += 1
            if export_rejected:
                dest = adapter.export(pkg, out_root / "_rejected")
                exported_paths.append(str(dest))

    result = FactoryRunResult(
        repo_name=profile.repo_name,
        base_commit=profile.base_commit,
        total_attempted=len(selected_seams),
        certified_count=certified,
        rejected_count=rejected,
        duration_sec=time.perf_counter() - t0,
        exported_paths=exported_paths,
        packages=packages,
        errors=errors,
    )

    summary_file = out_root / "factory_summary.json"
    summary_file.write_text(
        json.dumps(result.summary_dict(), indent=2), encoding="utf-8"
    )
    return result
