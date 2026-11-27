from __future__ import annotations

import json
import threading
import tomllib
import urllib.request
from pathlib import Path

from forgebench.dashboard import create_dashboard_server


SAMPLE_TASKS_DIR = Path(__file__).resolve().parent.parent / "sample_tasks"
EXPECTED_CLICK_TASKS = {
    "click-type-duration",
    "click-type-bytesize",
    "click-formatting-table",
    "click-group-aliases",
    "click-utils-redact-echo",
}


def test_five_distinct_harbor_v025_packages_conform_to_spec():
    assert SAMPLE_TASKS_DIR.is_dir(), "sample_tasks/ must exist with 5 certified Harbor tasks"

    found_tasks = {
        p.parent.name for p in SAMPLE_TASKS_DIR.glob("*/task.toml")
    }
    assert EXPECTED_CLICK_TASKS.issubset(found_tasks)

    for task_id in sorted(EXPECTED_CLICK_TASKS):
        task_dir = SAMPLE_TASKS_DIR / task_id

        # Required 5 core Harbor v0.25.x components
        toml_path = task_dir / "task.toml"
        instr_path = task_dir / "instruction.md"
        dockerfile = task_dir / "environment" / "Dockerfile"
        solve_sh = task_dir / "solution" / "solve.sh"
        ref_patch = task_dir / "solution" / "reference.patch"
        test_sh = task_dir / "tests" / "test.sh"
        f2p_json = task_dir / "tests" / "fail_to_pass.json"
        p2p_json = task_dir / "tests" / "pass_to_pass.json"

        for required in (
            toml_path,
            instr_path,
            dockerfile,
            solve_sh,
            ref_patch,
            test_sh,
            f2p_json,
            p2p_json,
        ):
            assert required.is_file(), f"Missing {required}"

        # Strict LF line endings for Docker & shell scripts
        for lf_file in (dockerfile, solve_sh, test_sh):
            raw = lf_file.read_bytes()
            assert b"\r\n" not in raw, f"CRLF found in {lf_file}"

        # Validate task.toml metadata
        parsed = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        md = parsed["metadata"]
        assert md["task_id"] == task_id
        assert md["repository"] == "pallets/click"
        assert len(md["base_commit"]) == 40
        assert md["license"] == "BSD-3-Clause"
        assert md["base_commit"] in dockerfile.read_text(encoding="utf-8")

        # Validate SWE-bench style JSON test manifests
        f2p = json.loads(f2p_json.read_text(encoding="utf-8"))
        p2p = json.loads(p2p_json.read_text(encoding="utf-8"))
        assert isinstance(f2p, list) and len(f2p) >= 3
        assert isinstance(p2p, list) and len(p2p) >= 3

        # Validate companion validation evidence
        ev_file = SAMPLE_TASKS_DIR / "_validation_evidence" / f"{task_id}.validation.json"
        assert ev_file.is_file()
        ev = json.loads(ev_file.read_text(encoding="utf-8"))
        assert ev["certified"] is True
        assert ev["nop_reward"] == 0.0
        assert ev["oracle_reward"] == 1.0
        assert ev["mutation_kill_rate"] >= 0.70


def test_dashboard_serves_harbor_v025_packages():
    server = create_dashboard_server(SAMPLE_TASKS_DIR, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/tasks", timeout=5) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            ids = {t["task_id"] for t in data["tasks"]}
            assert EXPECTED_CLICK_TASKS.issubset(ids)

        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/tasks/click-group-aliases", timeout=5
        ) as resp:
            assert resp.status == 200
            detail = json.loads(resp.read().decode("utf-8"))
            assert detail["meta"]["repository"]["name"] == "pallets/click"
            assert detail["report"]["oracle_reward"] == 1.0
            assert detail["report"]["nop_reward"] == 0.0
    finally:
        server.shutdown()
        server.server_close()
