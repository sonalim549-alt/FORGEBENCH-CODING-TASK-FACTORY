from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class FunctionInfo:
    name: str
    args: list[str]
    lineno: int
    is_async: bool = False
    decorators: list[str] = field(default_factory=list)
    docstring: str = ""


@dataclass(slots=True)
class ClassInfo:
    name: str
    bases: list[str]
    methods: list[str]
    lineno: int
    docstring: str = ""


@dataclass(slots=True)
class ModuleSummary:
    rel_path: str
    imports: list[str] = field(default_factory=list)
    functions: list[FunctionInfo] = field(default_factory=list)
    classes: list[ClassInfo] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)
    line_count: int = 0
    is_test: bool = False


@dataclass(slots=True)
class FeatureSeam:
    seam_id: str
    category: str
    title: str
    target_module: str
    target_symbol: str
    rationale: str
    difficulty: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RepoProfile:
    repo_name: str
    repo_path: str
    base_commit: str
    branch: str
    is_dirty: bool
    language: str
    framework: str
    test_runner: str
    test_command: list[str]
    dependencies: list[str]
    source_modules: list[ModuleSummary]
    test_files: list[str]
    seams: list[FeatureSeam]
    repo_url: str = ""
    base_tag: str = ""
    license_name: str = "BSD-3-Clause"
    src_root: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SecurityFinding:
    severity: str  # "HIGH" | "MEDIUM" | "LOW"
    rule_id: str
    file_path: str
    line_number: int
    message: str


@dataclass(slots=True)
class MutationOutcome:
    mutant_id: str
    file_path: str
    line_number: int
    operator: str
    description: str
    killed: bool
    exit_code: int


@dataclass(slots=True)
class GateResult:
    name: str
    passed: bool
    duration_ms: float
    summary: str
    command: list[str] = field(default_factory=list)
    stdout_tail: str = ""
    stderr_tail: str = ""


@dataclass(slots=True)
class ValidationReport:
    task_id: str
    repo_name: str
    base_commit: str
    certified: bool
    verified_at: str
    runtime_env: dict[str, str]
    gates: list[GateResult]
    mutations: list[MutationOutcome]
    mutation_kill_rate: float
    security_findings: list[SecurityFinding]
    nop_reward: float = 0.0
    oracle_reward: float = 0.0
    fail_to_pass: list[str] = field(default_factory=list)
    pass_to_pass: list[str] = field(default_factory=list)
    artifact_checksums: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TaskPackage:
    task_id: str
    title: str
    category: str
    difficulty: str
    repo_name: str
    base_commit: str
    language: str
    framework: str
    test_command: list[str]
    target_files: list[str]
    problem_statement: str
    acceptance_criteria: list[str]
    reference_patch: str
    feature_tests_patch: str
    feature_test_files: list[str]
    generator_strategy: str
    repo_url: str = ""
    base_tag: str = ""
    license_name: str = "BSD-3-Clause"
    regression_test_files: list[str] = field(default_factory=list)
    feature_test_sources: dict[str, str] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    validation_report: ValidationReport | None = None

    def metadata_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "forgebench.task.v1",
            "task_id": self.task_id,
            "title": self.title,
            "category": self.category,
            "difficulty": self.difficulty,
            "repository": {
                "name": self.repo_name,
                "url": self.repo_url,
                "base_commit": self.base_commit,
                "base_tag": self.base_tag,
                "license": self.license_name,
                "language": self.language,
                "framework": self.framework,
                "test_command": self.test_command,
            },
            "target_files": self.target_files,
            "feature_test_files": self.feature_test_files,
            "regression_test_files": self.regression_test_files,
            "acceptance_criteria": self.acceptance_criteria,
            "generator_strategy": self.generator_strategy,
            "created_at": self.created_at,
            "certified": bool(
                self.validation_report and self.validation_report.certified
            ),
            "nop_reward": (
                self.validation_report.nop_reward
                if self.validation_report
                else None
            ),
            "oracle_reward": (
                self.validation_report.oracle_reward
                if self.validation_report
                else None
            ),
        }
