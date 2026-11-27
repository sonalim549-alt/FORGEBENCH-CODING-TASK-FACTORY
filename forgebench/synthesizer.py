from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path
from typing import Any

from forgebench.click_blueprints import (
    synthesize_click_bytesize_task,
    synthesize_click_duration_task,
    synthesize_click_group_aliases_task,
    synthesize_click_redaction_task,
    synthesize_click_table_task,
)
from forgebench.models import FeatureSeam, RepoProfile, TaskPackage
from forgebench.patch_utils import combine_patches, make_unified_diff
from forgebench.providers import LLMProvider


def _module_import_path(rel_path: str) -> str:
    no_ext = rel_path[:-3] if rel_path.endswith(".py") else rel_path
    return no_ext.replace("\\", "/").replace("/", ".")


def _task_id(repo_name: str, seam: FeatureSeam) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", repo_name.lower()).strip("-")
    raw = f"{slug}-{seam.category}-{seam.seam_id.split('-')[-1]}"
    return raw[:48]


def _append_methods_to_class(source: str, class_name: str, methods_block: str) -> str:
    """Safely insert new methods at the end of a target class body using AST line bounds."""
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)

    target_node: ast.ClassDef | None = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            target_node = node
            break
    if target_node is None or target_node.end_lineno is None:
        raise ValueError(f"Class '{class_name}' not found in target module.")

    insert_at = target_node.end_lineno
    if insert_at > 0 and not lines[insert_at - 1].endswith("\n"):
        lines[insert_at - 1] += "\n"

    insertion = "\n" + methods_block.strip("\n") + "\n"
    updated = "".join(lines[:insert_at]) + insertion + "".join(lines[insert_at:])
    # Guarantee syntactically valid Python before emitting a patch.
    ast.parse(updated)
    return updated


def _ensure_imports(source: str, required_imports: list[str]) -> str:
    existing_lines = set(line.strip() for line in source.splitlines())
    missing = [imp for imp in required_imports if imp not in existing_lines]
    if not missing:
        return source

    lines = source.splitlines(keepends=True)
    insert_idx = 0
    # Preserve `from __future__ import ...` at line 0 if present.
    for i, line in enumerate(lines):
        if line.strip().startswith("from __future__ import"):
            insert_idx = i + 1
        elif i > 5:
            break

    block = "".join(f"{imp}\n" for imp in missing)
    return "".join(lines[:insert_idx]) + block + "".join(lines[insert_idx:])


def _synthesize_snapshot_task(profile: RepoProfile, seam: FeatureSeam) -> TaskPackage:
    repo_root = Path(profile.repo_path)
    rel_path = seam.target_module
    class_name = seam.target_symbol
    mod_import = _module_import_path(rel_path)

    original_src = (repo_root / rel_path).read_text(encoding="utf-8")
    updated_src = _ensure_imports(
        original_src,
        ["import hashlib", "import json"],
    )

    methods_code = '''
    def export_snapshot(self) -> dict:
        """Export a deterministic, integrity-checked snapshot of instance state."""
        state_payload = {}
        for attr_name, attr_val in sorted(self.__dict__.items()):
            if attr_name.startswith("_forge_"):
                continue
            try:
                json.dumps(attr_val, sort_keys=True, default=str)
                state_payload[attr_name] = json.loads(
                    json.dumps(attr_val, sort_keys=True, default=str)
                )
            except (TypeError, ValueError):
                continue

        canonical = json.dumps(state_payload, sort_keys=True, separators=(",", ":"))
        checksum = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return {
            "schema_version": 1,
            "class_name": self.__class__.__name__,
            "checksum": checksum,
            "state": state_payload,
        }

    def verify_snapshot(self, snapshot: dict) -> bool:
        """Verify structural validity and SHA-256 integrity of a snapshot dictionary."""
        if not isinstance(snapshot, dict):
            return False
        if snapshot.get("schema_version") != 1:
            return False
        if snapshot.get("class_name") != self.__class__.__name__:
            return False
        state_payload = snapshot.get("state")
        checksum = snapshot.get("checksum")
        if not isinstance(state_payload, dict) or not isinstance(checksum, str):
            return False
        canonical = json.dumps(state_payload, sort_keys=True, separators=(",", ":"))
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return checksum == expected

    def restore_snapshot(self, snapshot: dict) -> None:
        """Atomically restore state from a verified snapshot; raise ValueError if tampered."""
        if not self.verify_snapshot(snapshot):
            raise ValueError("Snapshot integrity or schema validation failed.")
        restored_state = snapshot["state"]
        for attr_name, attr_val in restored_state.items():
            if attr_name in self.__dict__:
                setattr(self, attr_name, json.loads(json.dumps(attr_val)))
'''
    updated_src = _append_methods_to_class(updated_src, class_name, methods_code)
    ref_patch = make_unified_diff(rel_path, original_src, updated_src)

    test_rel = f"tests/test_feature_snapshot_{class_name.lower()}.py"
    test_code = f'''import copy
import pytest

from {mod_import} import {class_name}


def test_export_snapshot_is_deterministic_and_verifiable():
    instance = {class_name}()
    snap_a = instance.export_snapshot()
    snap_b = instance.export_snapshot()

    assert snap_a["schema_version"] == 1
    assert snap_a["class_name"] == "{class_name}"
    assert isinstance(snap_a["checksum"], str) and len(snap_a["checksum"]) == 64
    assert snap_a["checksum"] == snap_b["checksum"]
    assert instance.verify_snapshot(snap_a) is True


def test_verify_snapshot_detects_tampering_and_schema_mismatch():
    instance = {class_name}()
    snap = instance.export_snapshot()

    tampered = copy.deepcopy(snap)
    tampered["state"]["__injected__"] = "corrupted"
    assert instance.verify_snapshot(tampered) is False

    wrong_version = copy.deepcopy(snap)
    wrong_version["schema_version"] = 99
    assert instance.verify_snapshot(wrong_version) is False

    wrong_class = copy.deepcopy(snap)
    wrong_class["class_name"] = "OtherService"
    assert instance.verify_snapshot(wrong_class) is False

    assert instance.verify_snapshot("not-a-dict") is False


def test_restore_snapshot_rejects_invalid_payload_without_mutating_state():
    instance = {class_name}()
    baseline = instance.export_snapshot()

    bad_snap = copy.deepcopy(baseline)
    bad_snap["checksum"] = "0" * 64
    with pytest.raises(ValueError):
        instance.restore_snapshot(bad_snap)

    assert instance.export_snapshot()["checksum"] == baseline["checksum"]
'''
    test_patch = make_unified_diff(test_rel, None, test_code)

    criteria = [
        f"`{class_name}.export_snapshot()` returns a dict with `schema_version` (`1`), `class_name`, `state`, and a deterministic 64-char SHA-256 `checksum`.",
        f"`{class_name}.verify_snapshot(snapshot)` returns `True` for intact snapshots and `False` for tampered state, wrong schema version, wrong class name, or non-dict inputs.",
        f"`{class_name}.restore_snapshot(snapshot)` raises `ValueError` on invalid snapshots without corrupting existing instance state, and restores valid snapshots cleanly.",
        "All pre-existing tests in the repository continue to pass without regression.",
    ]

    problem_md = f"""# Feature Task: Deterministic Snapshot Export & Integrity Verification for `{class_name}`

## Context
Target module: `{rel_path}` (starting repository commit `{profile.base_commit[:12]}`).

`{class_name}` maintains structured in-memory application state, but currently lacks a safe, portable way to serialize, verify, and restore state snapshots for backup, migration, or auditing workflows.

## Requirements
Implement three public methods on `{class_name}` in `{rel_path}`:

1. **`export_snapshot(self) -> dict`**
   - Produces a JSON-serializable snapshot dictionary containing:
     - `"schema_version"`: integer `1`
     - `"class_name"`: `self.__class__.__name__`
     - `"state"`: dictionary of JSON-serializable instance attributes
     - `"checksum"`: lowercase hex SHA-256 digest of the canonical sorted-key JSON encoding of `"state"`
   - Calling `export_snapshot()` twice on unchanged state must yield identical checksums.

2. **`verify_snapshot(self, snapshot: dict) -> bool`**
   - Validates that `snapshot` is a `dict`, `"schema_version"` equals `1`, `"class_name"` matches `self.__class__.__name__`, `"state"` is a `dict`, and `"checksum"` matches the recomputed SHA-256 digest.
   - Returns `False` (never raises) for malformed or tampered payloads.

3. **`restore_snapshot(self, snapshot: dict) -> None`**
   - Validates the snapshot using `verify_snapshot`.
   - Raises `ValueError` if validation fails, leaving current instance state completely unchanged.
   - When valid, restores matching attributes onto the instance via deep-copied JSON values.

## Acceptance Criteria
""" + "\n".join(f"- {c}" for c in criteria) + "\n"

    return TaskPackage(
        task_id=_task_id(profile.repo_name, seam),
        title=seam.title,
        category=seam.category,
        difficulty=seam.difficulty,
        repo_name=profile.repo_name,
        repo_url=profile.repo_url,
        base_commit=profile.base_commit,
        base_tag=profile.base_tag,
        license_name=profile.license_name,
        language=profile.language,
        framework=profile.framework,
        test_command=profile.test_command,
        target_files=[rel_path],
        problem_statement=problem_md,
        acceptance_criteria=criteria,
        reference_patch=ref_patch,
        feature_tests_patch=test_patch,
        feature_test_files=[test_rel],
        regression_test_files=profile.test_files[:6],
        feature_test_sources={test_rel: test_code},
        generator_strategy="ast-guided-blueprint",
    )


def _synthesize_audit_task(profile: RepoProfile, seam: FeatureSeam) -> TaskPackage:
    repo_root = Path(profile.repo_path)
    rel_path = seam.target_module
    class_name = seam.target_symbol
    mod_import = _module_import_path(rel_path)

    original_src = (repo_root / rel_path).read_text(encoding="utf-8")
    methods_code = '''
    def record_audit_event(
        self,
        action: str,
        actor: str = "system",
        details: dict | None = None,
        max_entries: int = 500,
    ) -> dict:
        """Append a structured audit event into a bounded in-memory trail."""
        if not isinstance(action, str) or not action.strip():
            raise ValueError("action must be a non-empty string.")
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor must be a non-empty string.")
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1.")

        log = getattr(self, "_forge_audit_log", None)
        if log is None:
            log = []
            self._forge_audit_log = log
        seq = getattr(self, "_forge_audit_seq", 0) + 1
        self._forge_audit_seq = seq

        entry = {
            "seq": seq,
            "action": action.strip(),
            "actor": actor.strip(),
            "details": dict(details) if isinstance(details, dict) else {},
        }
        log.append(entry)
        if len(log) > max_entries:
            del log[: len(log) - max_entries]
        return dict(entry)

    def query_audit_log(
        self,
        action: str | None = None,
        actor: str | None = None,
        since_seq: int = 0,
        limit: int | None = None,
    ) -> list[dict]:
        """Return defensive copies of recorded audit entries matching optional filters."""
        if limit is not None and limit < 0:
            raise ValueError("limit cannot be negative.")
        log = getattr(self, "_forge_audit_log", [])
        matched = []
        for item in log:
            if item["seq"] <= since_seq:
                continue
            if action is not None and item["action"] != action:
                continue
            if actor is not None and item["actor"] != actor:
                continue
            matched.append(
                {
                    "seq": item["seq"],
                    "action": item["action"],
                    "actor": item["actor"],
                    "details": dict(item["details"]),
                }
            )
        if limit is not None:
            return matched[-limit:] if limit > 0 else []
        return matched
'''
    updated_src = _append_methods_to_class(original_src, class_name, methods_code)
    ref_patch = make_unified_diff(rel_path, original_src, updated_src)

    test_rel = f"tests/test_feature_audit_{class_name.lower()}.py"
    test_code = f'''import pytest

from {mod_import} import {class_name}


def test_record_and_query_audit_events_with_filters():
    svc = {class_name}()
    e1 = svc.record_audit_event("create", actor="alice", details={{"sku": "A1"}})
    e2 = svc.record_audit_event("reserve", actor="bob", details={{"qty": 3}})
    e3 = svc.record_audit_event("create", actor="bob", details={{"sku": "B2"}})

    assert [e1["seq"], e2["seq"], e3["seq"]] == [1, 2, 3]

    creates = svc.query_audit_log(action="create")
    assert len(creates) == 2
    assert [c["actor"] for c in creates] == ["alice", "bob"]

    bob_after_first = svc.query_audit_log(actor="bob", since_seq=2)
    assert len(bob_after_first) == 1
    assert bob_after_first[0]["seq"] == 3


def test_audit_log_enforces_ring_buffer_and_defensive_copies():
    svc = {class_name}()
    for idx in range(5):
        svc.record_audit_event("tick", details={{"n": idx}}, max_entries=3)

    events = svc.query_audit_log()
    assert [e["seq"] for e in events] == [3, 4, 5]

    # Mutating returned copy must not affect internal log
    events[0]["details"]["n"] = 999
    fresh = svc.query_audit_log(limit=1)
    assert len(fresh) == 1 and fresh[0]["seq"] == 5
    assert svc.query_audit_log()[0]["details"]["n"] == 2
    assert svc.query_audit_log(limit=0) == []


def test_audit_log_rejects_invalid_arguments():
    svc = {class_name}()
    with pytest.raises(ValueError):
        svc.record_audit_event("   ")
    with pytest.raises(ValueError):
        svc.record_audit_event("create", actor="")
    with pytest.raises(ValueError):
        svc.record_audit_event("create", max_entries=0)
    with pytest.raises(ValueError):
        svc.query_audit_log(limit=-1)
'''
    test_patch = make_unified_diff(test_rel, None, test_code)

    criteria = [
        f"`{class_name}.record_audit_event(action, actor='system', details=None, max_entries=500)` assigns monotonically increasing `seq` numbers, trims oldest entries past `max_entries`, and validates non-empty `action`/`actor` and `max_entries >= 1`.",
        f"`{class_name}.query_audit_log(action=None, actor=None, since_seq=0, limit=None)` supports composable filters, returns defensive copies, raises `ValueError` for negative `limit`, and returns `[]` for `limit=0`.",
        "All existing repository tests pass without regression.",
    ]

    problem_md = f"""# Feature Task: Bounded Audit Trail & Filtered Query API for `{class_name}`

## Context
Target module: `{rel_path}` (starting repository commit `{profile.base_commit[:12]}`).

Production deployments of `{class_name}` require lightweight observability over state changes without risking unbounded memory growth.

## Requirements
Add two public methods to `{class_name}` in `{rel_path}`:

1. **`record_audit_event(self, action: str, actor: str = "system", details: dict | None = None, max_entries: int = 500) -> dict`**
   - Validates that `action` and `actor` are non-empty strings (after stripping) and `max_entries >= 1`; otherwise raises `ValueError`.
   - Assigns an auto-incrementing integer `"seq"` starting at `1`.
   - Stores bounded history (retaining the latest `max_entries` events) and returns a copy of the recorded event dict.

2. **`query_audit_log(self, action: str | None = None, actor: str | None = None, since_seq: int = 0, limit: int | None = None) -> list[dict]`**
   - Filters stored entries by exact `action`, exact `actor`, and `seq > since_seq`.
   - If `limit` is negative, raises `ValueError`; if `limit == 0`, returns `[]`; if `limit > 0`, returns the latest `limit` matching entries.
   - Returns defensive copies so caller mutations cannot corrupt internal state.

## Acceptance Criteria
""" + "\n".join(f"- {c}" for c in criteria) + "\n"

    return TaskPackage(
        task_id=_task_id(profile.repo_name, seam),
        title=seam.title,
        category=seam.category,
        difficulty=seam.difficulty,
        repo_name=profile.repo_name,
        repo_url=profile.repo_url,
        base_commit=profile.base_commit,
        base_tag=profile.base_tag,
        license_name=profile.license_name,
        language=profile.language,
        framework=profile.framework,
        test_command=profile.test_command,
        target_files=[rel_path],
        problem_statement=problem_md,
        acceptance_criteria=criteria,
        reference_patch=ref_patch,
        feature_tests_patch=test_patch,
        feature_test_files=[test_rel],
        regression_test_files=profile.test_files[:6],
        feature_test_sources={test_rel: test_code},
        generator_strategy="ast-guided-blueprint",
    )


def _synthesize_batch_function_task(
    profile: RepoProfile, seam: FeatureSeam
) -> TaskPackage:
    repo_root = Path(profile.repo_path)
    rel_path = seam.target_module
    fn_name = seam.context["function_name"]
    batch_name = seam.context["batch_name"]
    mod_import = _module_import_path(rel_path)

    original_src = (repo_root / rel_path).read_text(encoding="utf-8")
    addition = f'''

def {batch_name}(
    items: list,
    *,
    max_batch_size: int = 100,
    fail_fast: bool = False,
    deduplicate: bool = False,
) -> dict:
    """Process a bounded batch of inputs through {fn_name} with per-item fault isolation."""
    if not isinstance(items, (list, tuple)):
        raise TypeError("items must be a list or tuple.")
    if max_batch_size < 1:
        raise ValueError("max_batch_size must be at least 1.")
    if len(items) > max_batch_size:
        raise ValueError(
            f"Batch size {{len(items)}} exceeds max_batch_size ({{max_batch_size}})."
        )

    results = []
    cache = {{}}
    succeeded = 0
    failed = 0
    dedup_hits = 0

    for idx, item in enumerate(items):
        cache_key = None
        if deduplicate:
            try:
                hash(item)
                cache_key = item
            except TypeError:
                cache_key = None

        if deduplicate and cache_key is not None and cache_key in cache:
            dedup_hits += 1
            succeeded += 1
            results.append(
                {{"index": idx, "ok": True, "cached": True, "result": cache[cache_key]}}
            )
            continue

        try:
            output = {fn_name}(item)
        except Exception as exc:
            failed += 1
            if fail_fast:
                raise
            results.append(
                {{"index": idx, "ok": False, "cached": False, "error": str(exc)}}
            )
            continue

        succeeded += 1
        if deduplicate and cache_key is not None:
            cache[cache_key] = output
        results.append({{"index": idx, "ok": True, "cached": False, "result": output}})

    return {{
        "total": len(items),
        "succeeded": succeeded,
        "failed": failed,
        "dedup_hits": dedup_hits,
        "results": results,
    }}
'''
    updated_src = original_src.rstrip("\n") + "\n" + addition
    ast.parse(updated_src)
    ref_patch = make_unified_diff(rel_path, original_src, updated_src)

    safe_mod = Path(rel_path).stem
    test_rel = f"tests/test_feature_{batch_name}_{safe_mod}.py"
    test_code = f'''import pytest

import {mod_import} as target_mod


def test_batch_validates_container_and_size_bounds():
    with pytest.raises(TypeError):
        target_mod.{batch_name}("not-a-list")
    with pytest.raises(ValueError):
        target_mod.{batch_name}([], max_batch_size=0)
    with pytest.raises(ValueError):
        target_mod.{batch_name}([1, 2, 3], max_batch_size=2)


def test_batch_fault_isolation_and_deduplication(monkeypatch):
    calls = []

    def fake_worker(x):
        calls.append(x)
        if x == "bad":
            raise ValueError("invalid payload")
        return f"ok:{{x}}"

    monkeypatch.setattr(target_mod, "{fn_name}", fake_worker)

    report = target_mod.{batch_name}(
        ["alpha", "bad", "alpha", "beta"],
        deduplicate=True,
    )
    assert report["total"] == 4
    assert report["succeeded"] == 3
    assert report["failed"] == 1
    assert report["dedup_hits"] == 1
    assert calls == ["alpha", "bad", "beta"]
    assert report["results"][1]["ok"] is False
    assert "invalid payload" in report["results"][1]["error"]
    assert report["results"][2]["cached"] is True


def test_batch_fail_fast_propagates_first_error(monkeypatch):
    def failing_worker(x):
        if x == "boom":
            raise RuntimeError("hard failure")
        return x

    monkeypatch.setattr(target_mod, "{fn_name}", failing_worker)

    with pytest.raises(RuntimeError, match="hard failure"):
        target_mod.{batch_name}(["ok", "boom", "later"], fail_fast=True)
'''
    test_patch = make_unified_diff(test_rel, None, test_code)

    criteria = [
        f"`{batch_name}(items, *, max_batch_size=100, fail_fast=False, deduplicate=False)` validates `items` is a `list`/`tuple`, `max_batch_size >= 1`, and `len(items) <= max_batch_size`.",
        "When `fail_fast=False`, individual item exceptions are captured as `ok: False` entries with `error` text without aborting remaining items.",
        "When `fail_fast=True`, the first exception raised by the underlying function propagates immediately.",
        "When `deduplicate=True`, repeated hashable items reuse cached outputs and increment `dedup_hits`.",
    ]

    problem_md = f"""# Feature Task: Resilient Batch Processor `{batch_name}` in `{rel_path}`

## Context
Target module: `{rel_path}` (starting repository commit `{profile.base_commit[:12]}`).

`{fn_name}` currently handles one item at a time. Callers integrating `{rel_path}` into bulk workflows need a safe, bounded batch entry point with error isolation and optional deduplication.

## Requirements
Implement `{batch_name}(items: list, *, max_batch_size: int = 100, fail_fast: bool = False, deduplicate: bool = False) -> dict` in `{rel_path}`:

1. **Input & Bound Validation:**
   - Raise `TypeError` if `items` is not a `list` or `tuple`.
   - Raise `ValueError` if `max_batch_size < 1` or `len(items) > max_batch_size`.
2. **Per-Item Error Isolation vs Fail-Fast:**
   - By default (`fail_fast=False`), exceptions raised by `{fn_name}` must be caught and recorded in `results` as `{{"index": i, "ok": False, "cached": False, "error": str(exc)}}`.
   - When `fail_fast=True`, allow the exception to propagate immediately.
3. **Optional Deduplication (`deduplicate=True`):**
   - For hashable items seen earlier in the same batch, reuse the cached output without re-invoking `{fn_name}`, mark `"cached": True`, and increment `"dedup_hits"`.
4. **Structured Response:**
   - Return `{{"total", "succeeded", "failed", "dedup_hits", "results"}}`.

## Acceptance Criteria
""" + "\n".join(f"- {c}" for c in criteria) + "\n"

    return TaskPackage(
        task_id=_task_id(profile.repo_name, seam),
        title=seam.title,
        category=seam.category,
        difficulty=seam.difficulty,
        repo_name=profile.repo_name,
        repo_url=profile.repo_url,
        base_commit=profile.base_commit,
        base_tag=profile.base_tag,
        license_name=profile.license_name,
        language=profile.language,
        framework=profile.framework,
        test_command=profile.test_command,
        target_files=[rel_path],
        problem_statement=problem_md,
        acceptance_criteria=criteria,
        reference_patch=ref_patch,
        feature_tests_patch=test_patch,
        feature_test_files=[test_rel],
        regression_test_files=profile.test_files[:6],
        feature_test_sources={test_rel: test_code},
        generator_strategy="ast-guided-blueprint",
    )


def _synthesize_with_llm(
    profile: RepoProfile,
    seam: FeatureSeam,
    provider: LLMProvider,
    feedback: str = "",
) -> TaskPackage:
    repo_root = Path(profile.repo_path)
    target_src = (repo_root / seam.target_module).read_text(
        encoding="utf-8", errors="replace"
    )
    system_prompt = (
        "You are a senior software benchmark engineer building realistic feature-development "
        "evaluation tasks. Respond ONLY with a valid JSON object matching the requested schema. "
        "Never use eval/exec/shell=True or external network calls in tests."
    )
    user_prompt = (
        f"Repository: {profile.repo_name} (framework={profile.framework}, commit={profile.base_commit})\n"
        f"Target file: {seam.target_module}\n"
        f"Proposed feature seam: {seam.title} ({seam.rationale})\n"
        f"Current source of {seam.target_module}:\n```python\n{target_src[:6000]}\n```\n"
        + (f"\nPrevious validation failure to fix:\n{feedback}\n" if feedback else "")
        + """
Return JSON with keys:
- "title": concise feature title
- "problem_statement": clear markdown specification (requirements + edge cases, no solution leakage)
- "acceptance_criteria": list of 3-5 testable bullet strings
- "updated_target_source": complete updated Python source code for the target file
- "test_file_path": relative path under tests/, e.g. "tests/test_feature_custom.py"
- "test_file_source": complete pytest module testing happy path, edge cases, and error conditions
"""
    )
    data: dict[str, Any] = provider.complete_json(system_prompt, user_prompt)
    updated_src = str(data["updated_target_source"])
    ast.parse(updated_src)
    test_rel = str(data["test_file_path"]).replace("\\", "/")
    test_src = str(data["test_file_source"])
    ast.parse(test_src)

    ref_patch = make_unified_diff(seam.target_module, target_src, updated_src)
    test_patch = make_unified_diff(test_rel, None, test_src)

    return TaskPackage(
        task_id=_task_id(profile.repo_name, seam),
        title=str(data.get("title", seam.title)),
        category=seam.category,
        difficulty=seam.difficulty,
        repo_name=profile.repo_name,
        repo_url=profile.repo_url,
        base_commit=profile.base_commit,
        base_tag=profile.base_tag,
        license_name=profile.license_name,
        language=profile.language,
        framework=profile.framework,
        test_command=profile.test_command,
        target_files=[seam.target_module],
        problem_statement=str(data["problem_statement"]),
        acceptance_criteria=[str(x) for x in data.get("acceptance_criteria", [])],
        reference_patch=ref_patch,
        feature_tests_patch=test_patch,
        feature_test_files=[test_rel],
        regression_test_files=profile.test_files[:6],
        feature_test_sources={test_rel: test_src},
        generator_strategy=f"llm-{provider.name}",
    )


OFFLINE_BLUEPRINTS = {
    "param-type-duration": synthesize_click_duration_task,
    "param-type-bytesize": synthesize_click_bytesize_task,
    "formatting-table": synthesize_click_table_task,
    "group-aliases": synthesize_click_group_aliases_task,
    "output-redaction": synthesize_click_redaction_task,
    "data-portability": _synthesize_snapshot_task,
    "observability-audit": _synthesize_audit_task,
    "batch-processing": _synthesize_batch_function_task,
}


def synthesize_task(
    profile: RepoProfile,
    seam: FeatureSeam,
    provider: LLMProvider | None = None,
    feedback: str = "",
) -> TaskPackage:
    """Synthesize a candidate TaskPackage from a discovered FeatureSeam."""
    if provider is not None:
        return _synthesize_with_llm(profile, seam, provider, feedback=feedback)

    builder = OFFLINE_BLUEPRINTS.get(seam.category)
    if builder is not None:
        return builder(profile, seam)

    raise ValueError(
        f"No offline blueprint registered for seam category '{seam.category}'."
    )
