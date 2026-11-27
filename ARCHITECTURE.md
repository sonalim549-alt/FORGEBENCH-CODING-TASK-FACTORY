# ForgeBench — Technical Explanation, Architecture & Future Scope

## 1. Design Decisions & Core Architecture

ForgeBench treats AI benchmark task creation as an automated **software certification pipeline** rather than one-off prompt generation:

1. **Zero-Execution Static Codebase Analysis (`forgebench/analyzer.py`):**
   - Extracts exact 40-character `base_commit` SHA, release tag, license, remote URL, `src/` package root, test runner, and AST module graphs without importing or running target repository code.
   - Identifies **feature extension seams** by inspecting real classes/functions and confirming the target feature does not already exist upstream.

2. **Substantive 5-Way Task Variation Strategy (`forgebench/click_blueprints.py` + `forgebench/synthesizer.py`):**
   - To satisfy Innodata's non-trivial variation requirement (*"Tasks must differ in behaviour, rules, or interactions; simple rewording or renaming does not constitute variation"*), ForgeBench generates 5 orthogonal feature tasks spanning 4 distinct subsystems of `pallets/click` (`8.5.1`, commit `cf19179e8e2fd54c601378cec9e0d21bb12b6fb1`):
     1. **Grammar & Temporal Parsing (`click-type-duration`):** Compound/decimal duration grammar, `timedelta` conversion, `allow_zero`, range invariants.
     2. **Unit-System Semantics (`click-type-bytesize`):** SI base-1000 vs IEC base-1024 unit disambiguation, case-sensitivity rules, exact integer rounding.
     3. **Terminal Layout & ANSI Width Math (`click-formatting-table`):** Multi-column `term_len` layout, indentation propagation, border rendering, cell clipping.
     4. **Subcommand Dispatch & Help Integration (`click-group-aliases`):** Stateful alias registry, conflict/self-alias/missing-target guards, transparent dispatch in `Group.get_command()`, and non-breaking `--help` formatting.
     5. **CLI Security Hygiene (`click-utils-redact-echo`):** Longest-first overlapping secret substitution, regex metacharacter escaping, case-folding, and stream delegation via `safe_echo()`.

3. **4-Gate Automated Certification + AST Mutation Testing (`forgebench/validator.py`, `forgebench/mutator.py`):**
   - **Gate 1 (`baseline_health`):** Runs all 2,350 upstream `pallets/click` tests at `base_commit` in an isolated scratch copy with `PYTHONPATH=<scratch>/src` to guarantee zero shadowing by host packages.
   - **Gate 2 (`missing_feature_detection` / Harbor `nop`):** Applies only the new feature tests on unmodified `base_commit`, parses pytest JUnit XML with the exact Innodata Harbor reward formula, and enforces `nop_reward == 0.0`.
   - **Gate 3 (`incorrect_behavior_mutation`):** Extracts exact added line numbers from `reference.patch`, mutates only newly added AST logic nodes (`if` branch negation, comparison flips, boolean/arithmetic swaps, boundary shifts), and verifies that feature tests kill `>= 70%` of mutants (**achieved 25/25 = 100% kill rate across all 5 Click tasks**).
   - **Gate 4 (`reference_regression_security` / Harbor `oracle`):** Verifies `oracle_reward == 1.0`, zero regressions across the full 2,350-test upstream suite, and zero `HIGH` findings from `forgebench/security.py`.

4. **Strict Harbor v0.25.x Packaging (`forgebench/harbor.py`):**
   - Emits the exact 5-component Harbor folder (`task.toml`, `instruction.md`, `environment/Dockerfile`, `solution/solve.sh` + `reference.patch`, `tests/test.sh` + `fail_to_pass.json` + `pass_to_pass.json` + `test_feature_*.py`).
   - Enforces Unix LF (`\n`) line endings on every shell script and Dockerfile so Windows-generated packages execute cleanly inside Linux Docker containers.
   - Implements the exact Innodata reward specification in `tests/test.sh`:
     - Any regression failure → `0.0`
     - All feature tests pass → `1.0`
     - Partial feature passes → `passed_feature / total_feature`

---

## 2. Core Assumptions & Reused Components

- **Starting Repository:** `https://github.com/pallets/click` checked out at verified release tag `8.5.1` (`cf19179e8e2fd54c601378cec9e0d21bb12b6fb1`), licensed under `BSD-3-Clause`.
- **Reused Ecosystem Standards:** Python standard library (`ast`, `difflib`, `tomllib`, `xml.etree.ElementTree`, `concurrent.futures`, `http.server`), `git apply`, `pytest` JUnit XML reporting, SWE-bench `fail_to_pass.json` / `pass_to_pass.json` conventions, and Harbor v0.25.x Docker verifier contracts.
- **Model & Tool Disclosure (Administrative Compliance):**
  - Default pipeline execution (`--provider offline`) is **100% deterministic and offline**, requiring zero external LLM calls during benchmark reproduction.
  - Optional LLM-assisted open-ended synthesis supports Gemini (`gemini-2.5-flash`), OpenAI-compatible APIs (`gpt-4o-mini`), or local Ollama (`qwen2.5-coder:7b`) via environment variables, always subject to the identical 4-Gate Certification Harness.
  - Engineering assistance during development used Antigravity (Gemini) for architecture design, AST mutator refinement, and verification test authoring.

---

## 3. Validation Summary (Verified Run on `pallets/click` @ `cf19179e8e2f`)

- **Pipeline Throughput:** 5/5 tasks synthesized, 4-gate certified, and exported in **72.86s** (2 parallel workers, running full 2,350-test Click regression suite per task).
- **Harbor Reward Matrix:** Every task verified at **`nop_reward = 0.0`** and **`oracle_reward = 1.0`**.
- **Mutation Testing Evidence:** **25/25 targeted AST mutants killed (100% kill rate)** across all 5 tasks.
- **ForgeBench Self-Test Suite:** **9/9 pytest tests passing** (`test_analyzer`, `test_harbor_compliance`, `test_pipeline_e2e`, `test_security_and_mutator`).

---

## 4. Future Scope

1. **Multi-Language Factory Adapters (`zod` / TypeScript + `jsoncpp` / C++):**
   - Plug tree-sitter AST analyzers and language-specific verifiers (`vitest` for Zod, `cmake`/`ctest` for JsonCpp) behind `RepoProfile` and `HarborV025Adapter` so one factory serves all three Innodata languages.
2. **Ephemeral Containerized Gate Execution:**
   - Replace local scratch validation with rootless Docker/gVisor per-gate execution (`--network=none`, memory/CPU cgroups) for high-throughput cluster runs.
3. **Multi-Model Difficulty Calibration (`pass@k`):**
   - Automatically benchmark newly minted Harbor tasks against baseline vs frontier coding agents and tag empirical difficulty (`easy`, `medium`, `hard`), filtering out ambiguous or trivial tasks.
4. **Git-History Hotspot & Multi-File Architectural Seams:**
   - Combine AST seam mining with commit churn and issue templates to synthesize multi-file cross-module features continuously on every new upstream release tag.
