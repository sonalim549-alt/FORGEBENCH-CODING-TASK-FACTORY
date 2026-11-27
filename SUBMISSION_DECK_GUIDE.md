# Innodata AI Hackathon — Submission Deck Content Guide

Copy-paste ready content mapped directly to the official Hack2skill / Innodata Google Slides template.

---

## Slide 1 — Title Slide
- **Team Name:** *(Enter your team name, e.g., Team ForgeBench / Solo — Sonali Moghe)*
- **Problem Statement:** Track 1 — Coding Task Factory (Automated Feature-Development Task Generation with Harbor v0.25.x)
- **Team Leader Name:** Sonali Moghe

---

## Slide 2 — Brief About the Idea
**ForgeBench** is a reusable, automated **Coding Task Factory** that ingests any pinned open-source Git repository (demonstrated end-to-end on **`pallets/click` @ `8.5.1`**, commit `cf19179e8e2f`) and continuously mints **Harbor v0.25.x containerized feature-development task packages**.

Every generated task includes a clean agent-facing specification (`instruction.md`), an isolated `Dockerfile`, an oracle reference patch (`solution/solve.sh`), and SWE-bench-style verification tests (`tests/test.sh`, `fail_to_pass.json`, `pass_to_pass.json`) — automatically certified through a **4-Gate Validation Harness** with **targeted AST mutation testing**.

---

## Slide 3 — Opportunity & USP (How Is It Different?)
- **Problem Solved:** Manual coding benchmarks are slow, costly, and rapidly contaminated by LLM pre-training. Naive synthetic generators emit vague specs, weak tests that pass on broken code, or patches that break upstream regressions.
- **Our USP (4-Gate Certification + AST Mutation Testing):**
  1. **Zero-Execution AST Seam Discovery:** Statically discovers 5 substantively distinct, non-upstream feature opportunities across different subsystems (not cosmetic rewording).
  2. **Proves Both Missing *and* Incorrect Detection:** Unlike basic Fail-to-Pass pipelines, ForgeBench runs **targeted AST mutation testing** strictly on newly added reference lines — achieving a **100% (25/25) mutation kill rate** across all 5 `pallets/click` tasks.
  3. **Exact Harbor v0.25.x Reward & Regression Integrity:** Verifies `nop_reward = 0.0`, `oracle_reward = 1.0`, and **0 regressions across 2,350 upstream Click tests**.
  4. **Works 100% Offline + Pluggable LLM Mode:** Runs reproducibly with zero API keys out-of-the-box, while seamlessly supporting Gemini / OpenAI / local Ollama with a validation-feedback retry loop.

---

## Slide 4 — Key Features Offered
- **Automated 5-Way Substantive Variation on `pallets/click` (`8.5.1`):**
  1. `click-type-duration` — Compound/decimal `Duration` `ParamType` with bounds (`types.py`)
  2. `click-type-bytesize` — SI decimal vs IEC binary `ByteSize` `ParamType` (`types.py`)
  3. `click-formatting-table` — ANSI-aware multi-column `HelpFormatter.write_table()` (`formatting.py`)
  4. `click-group-aliases` — First-class `Group` command aliasing + `--help` integration (`core.py`)
  5. `click-utils-redact-echo` — Secret-safe `redact_text()` & `safe_echo()` CLI output hygiene (`utils.py`)
- **4-Gate Automated Certification Harness:** Baseline Health → Harbor `nop` (`0.0`) → Targeted AST Mutation Kill Check → Harbor `oracle` (`1.0`) + Full Regression + Static Security Audit.
- **Native Harbor v0.25.x Exporter:** Strict LF shell scripts, `task.toml`, `environment/Dockerfile`, `solution/solve.sh`, `tests/test.sh`, `fail_to_pass.json`, `pass_to_pass.json`, plus SHA-256 artifact checksums.
- **Hardened CLI & Local Evidence Explorer Dashboard:** Parallel worker pool (`--workers N`) and a `127.0.0.1`-bound web UI to inspect diffs, rewards, and mutation matrices live.

---

## Slide 5 — Process Flow & Architecture Diagram
Use this clean flow in your slide diagram:

```text
[Local Git Clone: pallets/click @ cf19179e8e2f]
                     │
                     ▼
        [1. Static AST Analyzer]
   (Commit SHA, framework, 5 distinct seams)
                     │
                     ▼
   [2. Hybrid Feature Synthesizer]
  (Offline AST Blueprints / Optional LLM + Retry)
                     │
                     ▼
   [3. 4-Gate Certification Harness (Parallel)]
    ├── Gate 1: Baseline Suite (2,350 tests PASS)
    ├── Gate 2: Harbor nop Check (reward = 0.0)
    ├── Gate 3: AST Mutation Testing (100% Killed)
    └── Gate 4: Harbor oracle (1.0) + Security Audit
                     │
                     ▼
     [4. Harbor v0.25.x Task Packages]
  (task.toml, instruction.md, Dockerfile, solve.sh, test.sh)
                     │
                     ▼
   [Harbor Docker Runner + Local Evidence Explorer UI]
```

---

## Slide 6 — Technologies & Security Architecture
- **Core Stack:** Python 3.10+ (zero mandatory third-party core dependencies), `ast`, `difflib`, `pytest` (JUnit XML), Git unified diffs, **Harbor v0.25.x**, Docker.
- **Target Foundation:** `pallets/click` (`8.5.1`, commit `cf19179e8e2fd54c601378cec9e0d21bb12b6fb1`, BSD-3-Clause).
- **Optional LLM Bridges:** Gemini API, OpenAI-compatible endpoints, or local private Ollama.
- **Security Controls:** Static-only analysis (no untrusted imports), isolated scratch verification, host credential env stripping, static patch audit (blocks `eval`, `exec`, `shell=True`, path traversal, hardcoded secrets, network calls), and CSP-hardened localhost dashboard.

---

## Slide 7 — Validation Evidence & Demo Results
- **Live Run on `pallets/click` @ `cf19179e8e2f`:**
  - **5 / 5 Harbor v0.25.x tasks CERTIFIED** in **72.86 seconds** (`0` rejected, `0` errors).
  - **100% (`25/25`) AST Mutation Kill Rate** proving tests catch incorrect implementations.
  - **0 Regressions** across all **2,350 upstream `pallets/click` tests**.
  - **9 / 9 ForgeBench self-tests passing** (including Harbor compliance & LF script checks).
- **Future Scope:** Multi-language adapters (`zod` TypeScript + `jsoncpp` C++), rootless Docker/gVisor per-gate isolation, multi-model `pass@k` difficulty calibration, and CI-triggered contamination-free benchmark refreshes.
