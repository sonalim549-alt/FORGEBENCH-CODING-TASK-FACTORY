from __future__ import annotations

import ast
import hashlib
import re
import sys
from pathlib import Path

from forgebench.models import (
    ClassInfo,
    FeatureSeam,
    FunctionInfo,
    ModuleSummary,
    RepoProfile,
)
from forgebench.security import run_bounded_command

IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "dist",
    "build",
    ".tox",
    ".mypy_cache",
    "output_tasks",
    "sample_tasks",
    "repos",
}

ROUTE_DECORATOR_RE = re.compile(r"\.(route|get|post|put|delete|patch)$")


def _git_output(repo_path: Path, args: list[str], default: str = "") -> str:
    try:
        proc = run_bounded_command(["git", *args], cwd=repo_path, timeout_sec=15)
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (OSError, Exception):
        pass
    return default


def _parse_dependencies(repo_path: Path) -> list[str]:
    deps: list[str] = []
    req_file = repo_path / "requirements.txt"
    if req_file.is_file():
        for line in req_file.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("-"):
                continue
            pkg = re.split(r"[=<>!~\[;\s]", stripped, maxsplit=1)[0].strip()
            if pkg:
                deps.append(pkg.lower())

    pyproject = repo_path / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r'"([A-Za-z0-9_.\-]+)(?:[=<>!~][^"]*)?"', text):
            candidate = match.group(1).lower()
            if candidate not in deps and candidate not in {"setuptools", "wheel"}:
                deps.append(candidate)

    return sorted(set(deps))


def _parse_license(repo_path: Path) -> str:
    pyproject = repo_path / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8", errors="replace")
        match = re.search(r'(?m)^\s*license\s*=\s*"([^"]+)"', text)
        if match:
            return match.group(1).strip()
    return "BSD-3-Clause"


def _detect_framework(deps: list[str], modules: list[ModuleSummary], repo_name: str) -> str:
    if repo_name.lower() == "click" or any(
        m.rel_path.endswith("click/core.py") for m in modules
    ):
        return "click-cli-toolkit"

    dep_set = set(deps)
    all_imports = {imp.split(".")[0].lower() for m in modules for imp in m.imports}
    combined = dep_set | all_imports

    if "fastapi" in combined:
        return "fastapi"
    if "flask" in combined:
        return "flask"
    if "django" in combined:
        return "django"
    if "click" in combined or "typer" in combined:
        return "cli"
    if "streamlit" in combined:
        return "streamlit"
    return "python-stdlib"


def _detect_test_runner(repo_path: Path, test_files: list[str]) -> tuple[str, list[str]]:
    python_exe = sys.executable or "python"
    if (
        (repo_path / "pytest.ini").is_file()
        or (repo_path / "conftest.py").is_file()
        or (repo_path / "tests" / "conftest.py").is_file()
        or any("pytest" in f.lower() for f in test_files)
    ):
        return "pytest", [python_exe, "-m", "pytest", "-q"]

    pyproject = repo_path / "pyproject.toml"
    if pyproject.is_file() and "pytest" in pyproject.read_text(
        encoding="utf-8", errors="replace"
    ):
        return "pytest", [python_exe, "-m", "pytest", "-q"]

    try:
        probe = run_bounded_command(
            [python_exe, "-m", "pytest", "--version"], cwd=repo_path, timeout_sec=10
        )
        if probe.returncode == 0:
            return "pytest", [python_exe, "-m", "pytest", "-q"]
    except Exception:
        pass

    return "unittest", [python_exe, "-m", "unittest", "discover", "-q"]


def _decorator_to_str(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _parse_python_module(file_path: Path, rel_path: str) -> ModuleSummary | None:
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError:
        return None

    imports: list[str] = []
    functions: list[FunctionInfo] = []
    classes: list[ClassInfo] = []
    routes: list[str] = []

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decs = [_decorator_to_str(d) for d in node.decorator_list]
            for dec in decs:
                if ROUTE_DECORATOR_RE.search(dec.split("(")[0]):
                    routes.append(f"{dec} -> {node.name}")
            functions.append(
                FunctionInfo(
                    name=node.name,
                    args=[a.arg for a in node.args.args],
                    lineno=node.lineno,
                    is_async=isinstance(node, ast.AsyncFunctionDef),
                    decorators=decs,
                    docstring=(ast.get_docstring(node) or "").splitlines()[0]
                    if ast.get_docstring(node)
                    else "",
                )
            )
        elif isinstance(node, ast.ClassDef):
            methods = [
                n.name
                for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not n.name.startswith("__")
            ]
            bases = [_decorator_to_str(b) for b in node.bases]
            classes.append(
                ClassInfo(
                    name=node.name,
                    bases=bases,
                    methods=methods,
                    lineno=node.lineno,
                    docstring=(ast.get_docstring(node) or "").splitlines()[0]
                    if ast.get_docstring(node)
                    else "",
                )
            )

    parts = Path(rel_path).parts
    is_test = (
        Path(rel_path).name.startswith("test_")
        or Path(rel_path).name.endswith("_test.py")
        or "tests" in parts
    )

    return ModuleSummary(
        rel_path=rel_path,
        imports=sorted(set(imports)),
        functions=functions,
        classes=classes,
        routes=routes,
        line_count=len(source.splitlines()),
        is_test=is_test,
    )


def _seam_hash(*parts: str) -> str:
    digest = hashlib.sha256("::".join(parts).encode("utf-8")).hexdigest()
    return digest[:10]


def _discover_seams(
    repo_path: Path, source_modules: list[ModuleSummary], framework: str
) -> list[FeatureSeam]:
    """Identify distinct, realistic feature-extension seams directly from repository AST."""
    seams: list[FeatureSeam] = []

    # ------------------------------------------------------------------
    # Domain-Aware CLI Toolkit Seams (e.g., pallets/click)
    # Discovered strictly by checking AST classes/methods so it never
    # duplicates features that already exist upstream.
    # ------------------------------------------------------------------
    for mod in source_modules:
        cls_by_name = {c.name: c for c in mod.classes}
        fn_by_name = {f.name: f for f in mod.functions}

        # Seam 1: Parameter Type System -> Duration ParamType
        if "ParamType" in cls_by_name and "Duration" not in cls_by_name:
            seams.append(
                FeatureSeam(
                    seam_id=f"seam-click-duration-{_seam_hash(mod.rel_path, 'Duration')}",
                    category="param-type-duration",
                    title="Add Duration ParamType supporting compound time units and range bounds",
                    target_module=mod.rel_path,
                    target_symbol="ParamType",
                    rationale=(
                        f"{mod.rel_path} defines Click's ParamType hierarchy without a "
                        "human-readable duration parser (e.g. '1h30m', '500ms', '2d')."
                    ),
                    difficulty="medium",
                    context={"base_class": "ParamType"},
                )
            )

        # Seam 2: Parameter Type System -> ByteSize ParamType (SI vs IEC units)
        if "ParamType" in cls_by_name and "ByteSize" not in cls_by_name:
            seams.append(
                FeatureSeam(
                    seam_id=f"seam-click-bytesize-{_seam_hash(mod.rel_path, 'ByteSize')}",
                    category="param-type-bytesize",
                    title="Add ByteSize ParamType distinguishing SI decimal and IEC binary units",
                    target_module=mod.rel_path,
                    target_symbol="ParamType",
                    rationale=(
                        f"{mod.rel_path} lacks a byte-size ParamType supporting SI (KB/MB/GB) "
                        "vs IEC (KiB/MiB/GiB) units with min/max bounds."
                    ),
                    difficulty="medium",
                    context={"base_class": "ParamType"},
                )
            )

        # Seam 3: Terminal Formatting -> ANSI-aware Multi-Column write_table
        if (
            "HelpFormatter" in cls_by_name
            and "write_dl" in cls_by_name["HelpFormatter"].methods
            and "write_table" not in cls_by_name["HelpFormatter"].methods
        ):
            seams.append(
                FeatureSeam(
                    seam_id=f"seam-click-table-{_seam_hash(mod.rel_path, 'HelpFormatter')}",
                    category="formatting-table",
                    title="Add ANSI-aware multi-column write_table() to HelpFormatter",
                    target_module=mod.rel_path,
                    target_symbol="HelpFormatter",
                    rationale=(
                        "HelpFormatter supports 2-column definition lists (write_dl) but lacks "
                        "general multi-column tabular layout with truncation and borders."
                    ),
                    difficulty="medium",
                    context={"class_name": "HelpFormatter"},
                )
            )

        # Seam 4: Command Routing -> First-class Group Command Aliases + Help Display
        if (
            "Group" in cls_by_name
            and "get_command" in cls_by_name["Group"].methods
            and "format_commands" in cls_by_name["Group"].methods
            and "add_alias" not in cls_by_name["Group"].methods
        ):
            seams.append(
                FeatureSeam(
                    seam_id=f"seam-click-aliases-{_seam_hash(mod.rel_path, 'Group')}",
                    category="group-aliases",
                    title="Add first-class command alias registration, resolution, and help display to Group",
                    target_module=mod.rel_path,
                    target_symbol="Group",
                    rationale=(
                        "Group routes subcommands via get_command() and formats them via "
                        "format_commands(), making native command aliasing with conflict checks a high-value feature."
                    ),
                    difficulty="hard",
                    context={"class_name": "Group"},
                )
            )

        # Seam 5: Output Security Hygiene -> redact_text & safe_echo
        if "echo" in fn_by_name and "redact_text" not in fn_by_name:
            seams.append(
                FeatureSeam(
                    seam_id=f"seam-click-redact-{_seam_hash(mod.rel_path, 'echo')}",
                    category="output-redaction",
                    title="Add redact_text() and safe_echo() for secret-safe CLI output hygiene",
                    target_module=mod.rel_path,
                    target_symbol="echo",
                    rationale=(
                        f"{mod.rel_path} provides echo() for terminal output but lacks built-in "
                        "secret redaction to prevent accidental credential leaks in CLI logs."
                    ),
                    difficulty="medium",
                    context={"function_name": "echo"},
                )
            )

    # If domain-specific seams already cover >= 5 distinct tasks, return them first.
    if len(seams) >= 5:
        return seams

    # ------------------------------------------------------------------
    # General Python Repository Seams (for any other Python repo)
    # ------------------------------------------------------------------
    for mod in source_modules:
        if Path(mod.rel_path).name == "__init__.py":
            continue

        for cls in mod.classes:
            if len(cls.methods) < 2:
                continue

            if any(
                m in cls.methods
                for m in ("add_item", "create", "register", "put", "reserve", "allocate")
            ):
                if "export_snapshot" not in cls.methods:
                    seams.append(
                        FeatureSeam(
                            seam_id=f"seam-snapshot-{_seam_hash(mod.rel_path, cls.name)}",
                            category="data-portability",
                            title=f"Add deterministic snapshot export & restore to {cls.name}",
                            target_module=mod.rel_path,
                            target_symbol=cls.name,
                            rationale=(
                                f"{cls.name} manages structured domain records in {mod.rel_path} "
                                "without a versioned snapshot export/import mechanism."
                            ),
                            difficulty="medium",
                            context={
                                "class_name": cls.name,
                                "existing_methods": cls.methods,
                            },
                        )
                    )

            if "record_audit_event" not in cls.methods and len(cls.methods) >= 3:
                seams.append(
                    FeatureSeam(
                        seam_id=f"seam-audit-{_seam_hash(mod.rel_path, cls.name)}",
                        category="observability-audit",
                        title=f"Add operation audit log and query filter to {cls.name}",
                        target_module=mod.rel_path,
                        target_symbol=cls.name,
                        rationale=(
                            f"{cls.name} performs state-changing operations ({', '.join(cls.methods[:4])}) "
                            "and can expose structured audit telemetry with filtering."
                        ),
                        difficulty="medium",
                        context={
                            "class_name": cls.name,
                            "existing_methods": cls.methods,
                        },
                    )
                )

        public_funcs = [
            f
            for f in mod.functions
            if not f.name.startswith("_") and len(f.args) >= 1 and f.name != "main"
        ]
        if public_funcs:
            primary_fn = public_funcs[0]
            batch_name = f"batch_{primary_fn.name}"
            existing_names = {f.name for f in mod.functions}
            if batch_name not in existing_names:
                seams.append(
                    FeatureSeam(
                        seam_id=f"seam-batch-{_seam_hash(mod.rel_path, primary_fn.name)}",
                        category="batch-processing",
                        title=f"Add resilient batch processor ({batch_name}) in {Path(mod.rel_path).name}",
                        target_module=mod.rel_path,
                        target_symbol=primary_fn.name,
                        rationale=(
                            f"{primary_fn.name}({', '.join(primary_fn.args)}) processes single inputs; "
                            "real-world pipelines need bounded batch execution with per-item error isolation."
                        ),
                        difficulty="medium",
                        context={
                            "function_name": primary_fn.name,
                            "function_args": primary_fn.args,
                            "batch_name": batch_name,
                        },
                    )
                )

    return seams


def analyze_repository(repo_path: str | Path) -> RepoProfile:
    """Statically analyze a local Git repository without executing any repo code."""
    root = Path(repo_path).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Repository path does not exist: {root}")

    base_commit = _git_output(root, ["rev-parse", "HEAD"], default="UNTRACKED")
    branch = _git_output(root, ["rev-parse", "--abbrev-ref", "HEAD"], default="detached")
    repo_url = _git_output(root, ["remote", "get-url", "origin"], default="")
    base_tag = _git_output(root, ["describe", "--tags", "--exact-match"], default="")
    status_out = _git_output(root, ["status", "--porcelain"], default="")
    is_dirty = bool(status_out.strip())

    source_modules: list[ModuleSummary] = []
    test_files: list[str] = []

    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if any(part in IGNORED_DIRS for part in rel.parts):
            continue
        rel_posix = rel.as_posix()
        summary = _parse_python_module(path, rel_posix)
        if summary is None:
            continue
        if summary.is_test:
            test_files.append(rel_posix)
        else:
            source_modules.append(summary)

    dependencies = _parse_dependencies(root)
    license_name = _parse_license(root)
    framework = _detect_framework(dependencies, source_modules, root.name)
    test_runner, test_command = _detect_test_runner(root, test_files)
    src_root = "src" if (root / "src").is_dir() else ""
    seams = _discover_seams(root, source_modules, framework)

    return RepoProfile(
        repo_name=root.name,
        repo_path=str(root),
        base_commit=base_commit,
        branch=branch,
        is_dirty=is_dirty,
        language="python",
        framework=framework,
        test_runner=test_runner,
        test_command=test_command,
        dependencies=dependencies,
        source_modules=source_modules,
        test_files=test_files,
        seams=seams,
        repo_url=repo_url,
        base_tag=base_tag,
        license_name=license_name,
        src_root=src_root,
    )
