from __future__ import annotations

import shutil
from pathlib import Path

from forgebench.analyzer import analyze_repository
from forgebench.security import run_bounded_command


def _init_git_copy(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst)
    run_bounded_command(["git", "init"], cwd=dst)
    run_bounded_command(["git", "config", "user.email", "test@forgebench.local"], cwd=dst)
    run_bounded_command(["git", "config", "user.name", "ForgeBench Test"], cwd=dst)
    run_bounded_command(["git", "add", "."], cwd=dst)
    run_bounded_command(["git", "commit", "-m", "baseline"], cwd=dst)
    return dst


def test_analyze_repository_extracts_commit_modules_and_seams(tmp_path: Path):
    sample = Path(__file__).resolve().parent.parent / "examples" / "inventory_service"
    repo_dir = _init_git_copy(sample, tmp_path / "inventory_service")

    profile = analyze_repository(repo_dir)
    assert profile.repo_name == "inventory_service"
    assert len(profile.base_commit) == 40
    assert profile.is_dirty is False
    assert profile.language == "python"
    assert len(profile.source_modules) >= 2
    assert any("test_inventory.py" in t for t in profile.test_files)

    categories = {s.category for s in profile.seams}
    assert {"data-portability", "observability-audit", "batch-processing"}.issubset(
        categories
    )
