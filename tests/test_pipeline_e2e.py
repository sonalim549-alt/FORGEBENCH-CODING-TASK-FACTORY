from __future__ import annotations

import json
import shutil
import threading
import urllib.request
from pathlib import Path

from forgebench.dashboard import create_dashboard_server
from forgebench.pipeline import run_factory
from forgebench.security import run_bounded_command


def _make_git_sample_repo(dst: Path) -> Path:
    sample = Path(__file__).resolve().parent.parent / "examples" / "inventory_service"
    shutil.copytree(sample, dst)
    run_bounded_command(["git", "init"], cwd=dst)
    run_bounded_command(["git", "config", "user.email", "ci@forgebench.local"], cwd=dst)
    run_bounded_command(["git", "config", "user.name", "ForgeBench CI"], cwd=dst)
    run_bounded_command(["git", "add", "."], cwd=dst)
    run_bounded_command(["git", "commit", "-m", "initial commit"], cwd=dst)
    return dst


def test_end_to_end_factory_generates_and_certifies_three_tasks(tmp_path: Path):
    repo_dir = _make_git_sample_repo(tmp_path / "inventory_service")
    out_dir = tmp_path / "certified_out"

    res = run_factory(
        repo_path=repo_dir,
        output_dir=out_dir,
        max_tasks=3,
        workers=2,
        adapter_format="standard",
        max_mutants=4,
    )

    assert res.total_attempted == 3
    assert res.certified_count == 3
    assert res.rejected_count == 0
    assert res.errors == []

    # Verify all 4 validation gates passed and mutations were killed on every task.
    for pkg in res.packages:
        rep = pkg.validation_report
        assert rep is not None
        assert rep.certified is True
        assert len(rep.gates) == 4
        assert all(g.passed for g in rep.gates)
        assert rep.mutation_kill_rate >= 0.70

    # Verify exported files & checksums on disk.
    summary = json.loads((out_dir / "factory_summary.json").read_text(encoding="utf-8"))
    assert summary["certified_count"] == 3
    assert len(list(out_dir.glob("*/task.json"))) == 3
    assert len(list(out_dir.glob("*/validation_report.json"))) == 3

    # Verify target repository remained completely untouched.
    status = run_bounded_command(["git", "status", "--porcelain"], cwd=repo_dir)
    assert status.stdout.strip() == ""


def test_custom_task_interface_and_dashboard_api(tmp_path: Path):
    repo_dir = _make_git_sample_repo(tmp_path / "inventory_service")
    template = (
        Path(__file__).resolve().parent.parent
        / "examples"
        / "innodata_interface_template.json"
    )

    custom_out = tmp_path / "custom_out"
    res = run_factory(
        repo_path=repo_dir,
        output_dir=custom_out,
        max_tasks=1,
        workers=1,
        interface_config=template,
        max_mutants=3,
    )
    assert res.certified_count == 1
    manifests = list(custom_out.glob("*/task_manifest.json"))
    assert len(manifests) == 1
    manifest_data = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest_data["starting_commit"] == res.base_commit
    assert manifest_data["certified"] is True

    # Also verify standard export + local Evidence Dashboard HTTP API.
    std_out = tmp_path / "std_out"
    run_factory(repo_path=repo_dir, output_dir=std_out, max_tasks=1, workers=1, max_mutants=3)

    server = create_dashboard_server(std_out, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/tasks", timeout=5) as resp:
            assert resp.status == 200
            listing = json.loads(resp.read().decode("utf-8"))
            assert len(listing["tasks"]) == 1
            tid = listing["tasks"][0]["task_id"]

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/tasks/{tid}", timeout=5) as resp:
            assert resp.status == 200
            detail = json.loads(resp.read().decode("utf-8"))
            assert detail["meta"]["task_id"] == tid
            assert detail["report"]["certified"] is True
    finally:
        server.shutdown()
        server.server_close()
