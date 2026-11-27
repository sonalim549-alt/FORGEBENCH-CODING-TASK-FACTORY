from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from forgebench.analyzer import analyze_repository
from forgebench.dashboard import create_dashboard_server
from forgebench.models import TaskPackage
from forgebench.pipeline import run_factory
from forgebench.security import run_bounded_command
from forgebench.validator import validate_task_package


def _cmd_analyze(args: argparse.Namespace) -> int:
    profile = analyze_repository(args.repo)
    if args.json:
        print(json.dumps(profile.to_dict(), indent=2))
        return 0

    print(f"Repository : {profile.repo_name}")
    print(f"Base Commit: {profile.base_commit} (branch: {profile.branch})")
    print(f"Framework  : {profile.language} / {profile.framework}")
    print(f"Test Runner: {profile.test_runner} ({' '.join(profile.test_command)})")
    print(f"Modules    : {len(profile.source_modules)} source, {len(profile.test_files)} test")
    print(f"Seams Found: {len(profile.seams)}")
    for idx, seam in enumerate(profile.seams, start=1):
        print(f"  {idx}. [{seam.category} | {seam.difficulty}] {seam.title}")
        print(f"     Target: {seam.target_module}::{seam.target_symbol}")
    return 0


def _cmd_generate(args: argparse.Namespace) -> int:
    result = run_factory(
        repo_path=args.repo,
        output_dir=args.out,
        max_tasks=args.count,
        workers=args.workers,
        adapter_format=args.format,
        interface_config=Path(args.interface_config) if args.interface_config else None,
        provider_name=args.provider,
        model_name=args.model,
        max_mutants=args.max_mutants,
        min_kill_rate=args.min_kill_rate,
        export_rejected=args.export_rejected,
        harbor_verify=args.harbor_verify,
    )

    print(
        f"\nForgeBench Run Complete ({result.duration_sec:.2f}s) — "
        f"Repo: {result.repo_name} @ {result.base_commit[:12]}"
    )
    print(
        f"Certified: {result.certified_count}/{result.total_attempted} | "
        f"Rejected: {result.rejected_count} | Errors: {len(result.errors)}"
    )
    for pkg in result.packages:
        rep = pkg.validation_report
        status = "CERTIFIED" if rep and rep.certified else "REJECTED"
        kill = f"{rep.mutation_kill_rate * 100:.0f}%" if rep else "n/a"
        rewards = (
            f"nop={rep.nop_reward:.1f}, oracle={rep.oracle_reward:.1f}"
            if rep
            else "n/a"
        )
        print(
            f"  - [{status}] {pkg.task_id}: {pkg.title} ({rewards} | mutation kill: {kill})"
        )
        if rep:
            for g in rep.gates:
                mark = "PASS" if g.passed else "FAIL"
                print(f"      [{mark}] {g.name}: {g.summary}")
    for err in result.errors:
        print(f"  ! Error: {err}", file=sys.stderr)

    print(f"\nArtifacts written to: {Path(args.out).resolve()}")
    return 0 if result.certified_count > 0 else 1


def _cmd_validate(args: argparse.Namespace) -> int:
    task_dir = Path(args.task_dir).resolve()
    meta = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    profile = analyze_repository(args.repo)

    pkg = TaskPackage(
        task_id=meta["task_id"],
        title=meta["title"],
        category=meta["category"],
        difficulty=meta["difficulty"],
        repo_name=profile.repo_name,
        base_commit=profile.base_commit,
        language=profile.language,
        framework=profile.framework,
        test_command=profile.test_command,
        target_files=meta.get("target_files", []),
        problem_statement=(task_dir / "problem_statement.md").read_text(encoding="utf-8"),
        acceptance_criteria=meta.get("acceptance_criteria", []),
        reference_patch=(task_dir / "reference.patch").read_text(encoding="utf-8"),
        feature_tests_patch=(task_dir / "feature_tests.patch").read_text(encoding="utf-8"),
        feature_test_files=meta.get("feature_test_files", []),
        generator_strategy=meta.get("generator_strategy", "external"),
    )
    report = validate_task_package(profile, pkg)
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.certified else 1


def _cmd_demo(args: argparse.Namespace) -> int:
    project_root = Path(__file__).resolve().parent.parent
    sample_src = project_root / "examples" / "inventory_service"
    if not sample_src.is_dir():
        print(f"Sample repository not found at {sample_src}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="forgebench_demo_repo_") as tmp_dir:
        demo_repo = Path(tmp_dir) / "inventory_service"
        shutil.copytree(sample_src, demo_repo)
        run_bounded_command(["git", "init"], cwd=demo_repo)
        run_bounded_command(["git", "config", "user.email", "demo@forgebench.local"], cwd=demo_repo)
        run_bounded_command(["git", "config", "user.name", "ForgeBench Demo"], cwd=demo_repo)
        run_bounded_command(["git", "add", "."], cwd=demo_repo)
        run_bounded_command(["git", "commit", "-m", "Initial baseline commit"], cwd=demo_repo)

        gen_args = argparse.Namespace(
            repo=str(demo_repo),
            out=args.out,
            count=args.count,
            workers=2,
            format="harbor",
            interface_config=None,
            provider="offline",
            model=None,
            max_mutants=5,
            min_kill_rate=0.70,
            export_rejected=False,
            harbor_verify=False,
        )
        return _cmd_generate(gen_args)


def _cmd_serve(args: argparse.Namespace) -> int:
    tasks_dir = Path(args.tasks_dir).resolve()
    server = create_dashboard_server(tasks_dir, host="127.0.0.1", port=args.port)
    print(f"ForgeBench Evidence Explorer running at http://127.0.0.1:{args.port}/")
    print(f"Serving task packages from: {tasks_dir}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down dashboard server.")
    finally:
        server.server_close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="forgebench",
        description="ForgeBench — Reusable Coding Task Factory with 4-Gate Certification",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_an = sub.add_parser("analyze", help="Statically analyze a Git repo and discover feature seams")
    p_an.add_argument("repo", help="Path to the target Git repository")
    p_an.add_argument("--json", action="store_true", help="Emit full RepoProfile as JSON")
    p_an.set_defaults(func=_cmd_analyze)

    p_gen = sub.add_parser("generate", help="Synthesize, validate, and export coding task packages")
    p_gen.add_argument("repo", help="Path to the target Git repository")
    p_gen.add_argument("--out", default="sample_tasks", help="Directory for exported task packages")
    p_gen.add_argument("--count", type=int, default=5, help="Number of feature tasks to generate")
    p_gen.add_argument("--workers", type=int, default=2, help="Parallel validation worker count")
    p_gen.add_argument(
        "--format",
        default="harbor",
        choices=["harbor", "standard", "swebench-jsonl"],
        help="Built-in task interface format (default: harbor v0.25.x)",
    )
    p_gen.add_argument(
        "--harbor-verify",
        action="store_true",
        help="Also run Harbor CLI nop/oracle Docker passes if docker & harbor are installed",
    )
    p_gen.add_argument(
        "--interface-config",
        default=None,
        help="Path to a custom JSON task interface mapping (e.g. Innodata supplied schema)",
    )
    p_gen.add_argument(
        "--provider",
        default="offline",
        choices=["offline", "openai", "gemini", "ollama"],
        help="Synthesis strategy (offline AST engine or pluggable LLM)",
    )
    p_gen.add_argument("--model", default=None, help="Optional LLM model override")
    p_gen.add_argument("--max-mutants", type=int, default=5, help="Max AST mutants per task in Gate 3")
    p_gen.add_argument("--min-kill-rate", type=float, default=0.70, help="Minimum mutation kill ratio")
    p_gen.add_argument("--export-rejected", action="store_true", help="Also save failed candidates under _rejected/")
    p_gen.set_defaults(func=_cmd_generate)

    p_val = sub.add_parser("validate", help="Re-verify an existing task package against a repository")
    p_val.add_argument("repo", help="Path to the target Git repository")
    p_val.add_argument("--task-dir", required=True, help="Directory containing task.json and patches")
    p_val.set_defaults(func=_cmd_validate)

    p_demo = sub.add_parser("demo", help="Run an end-to-end demo on the bundled inventory_service repo")
    p_demo.add_argument("--out", default="output_tasks", help="Output directory for certified tasks")
    p_demo.add_argument("--count", type=int, default=3, help="Number of tasks to generate")
    p_demo.set_defaults(func=_cmd_demo)

    p_srv = sub.add_parser("serve", help="Launch local read-only Evidence Explorer dashboard")
    p_srv.add_argument("--tasks-dir", default="output_tasks", help="Directory with exported tasks")
    p_srv.add_argument("--port", type=int, default=8765, help="Localhost port")
    p_srv.set_defaults(func=_cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
