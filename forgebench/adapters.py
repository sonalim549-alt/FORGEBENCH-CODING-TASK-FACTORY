from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from forgebench.harbor import export_harbor_package
from forgebench.models import TaskPackage


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class TaskInterfaceAdapter(ABC):
    """Contract for exporting certified TaskPackages to any external benchmark schema."""

    name: str

    @abstractmethod
    def export(self, package: TaskPackage, output_root: Path) -> Path:
        """Write a task package following the target task interface and return its location."""


class StandardForgeAdapter(TaskInterfaceAdapter):
    """Self-contained directory layout with problem spec, patches, metadata, and validation evidence."""

    name = "standard"

    def export(self, package: TaskPackage, output_root: Path) -> Path:
        task_dir = output_root / package.task_id
        task_dir.mkdir(parents=True, exist_ok=True)

        spec_path = task_dir / "problem_statement.md"
        ref_path = task_dir / "reference.patch"
        test_path = task_dir / "feature_tests.patch"
        meta_path = task_dir / "task.json"
        report_path = task_dir / "validation_report.json"

        spec_path.write_text(package.problem_statement, encoding="utf-8")
        ref_path.write_text(package.reference_patch, encoding="utf-8")
        test_path.write_text(package.feature_tests_patch, encoding="utf-8")

        checksums = {
            "problem_statement.md": _sha256_text(package.problem_statement),
            "reference.patch": _sha256_text(package.reference_patch),
            "feature_tests.patch": _sha256_text(package.feature_tests_patch),
        }

        if package.validation_report is not None:
            package.validation_report.artifact_checksums = checksums
            report_path.write_text(
                json.dumps(package.validation_report.to_dict(), indent=2),
                encoding="utf-8",
            )

        meta = package.metadata_dict()
        meta["artifacts"] = {
            "problem_statement": "problem_statement.md",
            "reference_patch": "reference.patch",
            "feature_tests_patch": "feature_tests.patch",
            "validation_report": "validation_report.json",
        }
        meta["checksums_sha256"] = checksums
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return task_dir


class SWEBenchCompatAdapter(TaskInterfaceAdapter):
    """Appends certified task records to a SWE-bench / FeatureBench compatible JSONL file."""

    name = "swebench-jsonl"

    def export(self, package: TaskPackage, output_root: Path) -> Path:
        output_root.mkdir(parents=True, exist_ok=True)
        jsonl_path = output_root / "tasks.jsonl"
        record = {
            "instance_id": package.task_id,
            "repo": package.repo_name,
            "base_commit": package.base_commit,
            "language": package.language,
            "framework": package.framework,
            "problem_statement": package.problem_statement,
            "patch": package.reference_patch,
            "test_patch": package.feature_tests_patch,
            "test_cmd": " ".join(package.test_command),
            "category": package.category,
            "difficulty": package.difficulty,
            "certified": bool(
                package.validation_report and package.validation_report.certified
            ),
            "mutation_kill_rate": (
                package.validation_report.mutation_kill_rate
                if package.validation_report
                else 0.0
            ),
        }
        with jsonl_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        return jsonl_path


class CustomSchemaAdapter(TaskInterfaceAdapter):
    """
    Adapts ForgeBench packages to any organizer-supplied task interface config (JSON).

    Example interface config (`task_interface.json`):
    {
      "layout": "directory",
      "manifest_filename": "manifest.json",
      "files": {
        "problem_statement": "prompt.md",
        "reference_patch": "solution.diff",
        "feature_tests_patch": "tests.diff",
        "validation_report": "evidence.json"
      },
      "field_map": {
        "id": "task_id",
        "repo_version": "base_commit",
        "spec": "problem_statement",
        "solution_patch": "reference_patch",
        "verification_patch": "feature_tests_patch"
      },
      "required_fields": ["id", "repo_version"]
    }
    """

    name = "custom"

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.config: dict[str, Any] = json.loads(
            config_path.read_text(encoding="utf-8")
        )

    def _resolve_field(self, package: TaskPackage, source_key: str) -> Any:
        lookup: dict[str, Any] = {
            "task_id": package.task_id,
            "title": package.title,
            "category": package.category,
            "difficulty": package.difficulty,
            "repo_name": package.repo_name,
            "base_commit": package.base_commit,
            "language": package.language,
            "framework": package.framework,
            "test_command": package.test_command,
            "target_files": package.target_files,
            "feature_test_files": package.feature_test_files,
            "problem_statement": package.problem_statement,
            "acceptance_criteria": package.acceptance_criteria,
            "reference_patch": package.reference_patch,
            "feature_tests_patch": package.feature_tests_patch,
            "certified": bool(
                package.validation_report and package.validation_report.certified
            ),
            "validation_report": (
                package.validation_report.to_dict()
                if package.validation_report
                else None
            ),
        }
        if source_key not in lookup:
            raise KeyError(
                f"Unknown source key '{source_key}' in custom interface {self.config_path}"
            )
        return lookup[source_key]

    def export(self, package: TaskPackage, output_root: Path) -> Path:
        task_dir = output_root / package.task_id
        task_dir.mkdir(parents=True, exist_ok=True)

        files_cfg: dict[str, str] = self.config.get(
            "files",
            {
                "problem_statement": "problem_statement.md",
                "reference_patch": "reference.patch",
                "feature_tests_patch": "feature_tests.patch",
                "validation_report": "validation_report.json",
            },
        )

        if "problem_statement" in files_cfg:
            (task_dir / files_cfg["problem_statement"]).write_text(
                package.problem_statement, encoding="utf-8"
            )
        if "reference_patch" in files_cfg:
            (task_dir / files_cfg["reference_patch"]).write_text(
                package.reference_patch, encoding="utf-8"
            )
        if "feature_tests_patch" in files_cfg:
            (task_dir / files_cfg["feature_tests_patch"]).write_text(
                package.feature_tests_patch, encoding="utf-8"
            )
        if "validation_report" in files_cfg and package.validation_report is not None:
            (task_dir / files_cfg["validation_report"]).write_text(
                json.dumps(package.validation_report.to_dict(), indent=2),
                encoding="utf-8",
            )

        field_map: dict[str, str] = self.config.get("field_map", {})
        manifest: dict[str, Any] = {}
        for dest_key, src_key in field_map.items():
            manifest[dest_key] = self._resolve_field(package, src_key)

        for req in self.config.get("required_fields", []):
            if req not in manifest or manifest[req] in (None, ""):
                raise ValueError(
                    f"Custom task interface requires field '{req}', which is missing."
                )

        manifest_name = self.config.get("manifest_filename", "task.json")
        (task_dir / manifest_name).write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        return task_dir


from forgebench.harbor import export_harbor_package
class HarborV025Adapter(TaskInterfaceAdapter):
    """Exports task packages strictly conforming to Harbor v0.25.x standardized architecture."""

    name = "harbor"

    def export(self, package: TaskPackage, output_root: Path) -> Path:
        return export_harbor_package(package, output_root)


def get_adapter(
    format_name: str = "harbor", interface_config: Path | None = None
) -> TaskInterfaceAdapter:
    if interface_config is not None:
        return CustomSchemaAdapter(interface_config)
    if format_name == "harbor":
        return HarborV025Adapter()
    if format_name == "standard":
        return StandardForgeAdapter()
    if format_name == "swebench-jsonl":
        return SWEBenchCompatAdapter()
    raise ValueError(
        f"Unsupported adapter format '{format_name}'. Use 'harbor', 'standard', 'swebench-jsonl', or pass --interface-config."
    )
