# ForgeBench — Harbor v0.25.x Coding Task Factory

**ForgeBench** is an automated, reusable **Coding Task Factory** built for **Innodata AI Hackathon — Track 1 (Coding Task Factory)**.

Given a local clone of a real open-source Git repository pinned at a verified baseline release commit (demonstrated end-to-end on **`pallets/click` tag `8.5.1`**, commit `cf19179e8e2fd54c601378cec9e0d21bb12b6fb1`), ForgeBench statically analyzes the codebase via Python AST, identifies **5 substantively distinct feature-development seams**, synthesizes complete containerized **Harbor v0.25.x** task packages, and certifies every task through a **4-Gate Validation Harness** including **Harbor `nop`/`oracle` reward verification** and **targeted AST mutation testing**.

---

## Certified Sample Outputs (`pallets/click` @ `8.5.1`)

All 5 distinct Harbor v0.25.x task packages are included in [`sample_tasks/`](sample_tasks/) and verified against commit `cf19179e8e2fd54c601378cec9e0d21bb12b6fb1` (2,350 upstream `pytest` regression tests passing):

| Task ID | Subsystem | Substantive Feature & Rules | `nop` Reward | `oracle` Reward | AST Mutation Kill Rate |
| :--- | :--- | :--- | :---: | :---: | :---: |
| `click-type-duration` | `src/click/types.py` | `Duration` `ParamType` parsing compound/decimal time units (`ms`/`s`/`m`/`h`/`d`/`w`), `timedelta` vs `as_seconds`, `allow_zero`, range bounds | `0.0` | `1.0` | **100% (5/5)** |
| `click-type-bytesize` | `src/click/types.py` | `ByteSize` `ParamType` distinguishing SI decimal (`KB`–`TB`, base 1000) vs IEC binary (`KiB`–`TiB`, base 1024), case sensitivity, bounds | `0.0` | `1.0` | **100% (5/5)** |
| `click-formatting-table` | `src/click/formatting.py` | ANSI-aware multi-column `HelpFormatter.write_table()` with `term_len` alignment, borders, indentation, and `max_col_width` truncation | `0.0` | `1.0` | **100% (5/5)** |
| `click-group-aliases` | `src/click/core.py` | Native `Group` subcommand alias registry (`add_alias`, `resolve_alias`, `get_aliases`), transparent dispatch, collision guards, and `--help` display | `0.0` | `1.0` | **100% (5/5)** |
| `click-utils-redact-echo` | `src/click/utils.py` | Secret-safe output hygiene (`redact_text`, `safe_echo`) with longest-first substitution, regex escaping, case-folding, and `min_secret_length` | `0.0` | `1.0` | **100% (5/5)** |

---

## Harbor v0.25.x Task Package Architecture

Every generated folder inside `sample_tasks/<task_id>/` strictly conforms to Harbor v0.25.x (with Unix LF line endings on all Dockerfiles and shell scripts):

```text
sample_tasks/<task_id>/
├── task.toml                   # Repo metadata, 40-char base_commit, license, verifier/agent/env limits
├── instruction.md              # Standalone requirement spec (ONLY file visible to evaluated AI agent)
├── environment/
│   └── Dockerfile              # Clones pallets/click @ exact base_commit & runs `pip install -e . pytest`
├── solution/
│   ├── solve.sh                # Harbor oracle script: applies reference.patch cleanly via `git apply`
│   └── reference.patch         # Unified diff of the reference feature implementation
└── tests/
    ├── test.sh                 # Harbor verifier: runs regression + feature tests, writes /logs/verifier/reward.txt
    ├── fail_to_pass.json       # SWE-bench-style list of new feature test node IDs
    ├── pass_to_pass.json       # SWE-bench-style list of existing regression test targets
    └── test_feature_*.py       # New feature verification tests
```

Validation reports and SHA-256 artifact checksums are saved cleanly in `sample_tasks/_validation_evidence/` and `sample_tasks/factory_summary.json`.

---

## Quick Start & Reproducible Execution

### Prerequisites
- Python **3.10+**
- `git` and `pytest` (`pip install pytest`)
- For containerized Harbor validation: Docker running + `pip install "harbor==0.25.*"`

### 1. Clone `pallets/click` at the Verified Baseline Release Commit
```bash
git clone https://github.com/pallets/click.git repos/click
cd repos/click
git checkout cf19179e8e2fd54c601378cec9e0d21bb12b6fb1
cd ../..
```

### 2. Inspect Repository & Discovered Feature Seams
```bash
python -m forgebench analyze repos/click
```

### 3. Run the Coding Task Factory (Generates & 4-Gate Certifies 5 Harbor Tasks)
```bash
python -m forgebench generate repos/click \
  --out sample_tasks \
  --count 5 \
  --workers 2 \
  --format harbor
```

Add `--harbor-verify` on any Docker + Harbor v0.25.x machine to automatically execute Harbor's `nop` and `oracle` Docker runs alongside ForgeBench's 4-Gate harness.

### 4. Run Harbor v0.25.x Validation Directly (Docker Required)
```bash
# Baseline (nop) check — Expected reward: 0.0
harbor run -p sample_tasks/click-type-duration -a nop -k 1 -o harbor_runs/nop --no-delete -y

# Oracle check — Expected reward: 1.0
harbor run -p sample_tasks/click-type-duration -a oracle -k 1 -o harbor_runs/oracle --no-delete -y
```

### 5. Launch the Local Evidence Explorer UI
```bash
python -m forgebench serve --tasks-dir sample_tasks --port 8765
```
Open `http://127.0.0.1:8765/` to inspect all 5 Harbor tasks, problem statements, patches, 4-gate logs, `nop`/`oracle` rewards, and AST mutation kill matrices.

### 6. Optional LLM Synthesis Mode (Gemini / OpenAI / Local Ollama)
ForgeBench runs **100% offline by default** (`--provider offline`) with zero API keys required. To run LLM-driven feature synthesis guarded by the same 4-Gate Certification Harness and feedback-retry loop:
```bash
set GEMINI_API_KEY=your_api_key
python -m forgebench generate repos/click --provider gemini --out llm_tasks --count 5
```

### 7. Run ForgeBench's Own Automated Test Suite
```bash
python -m pytest -v
```

---

## Security & Scalability Design

- **Static-Only Discovery:** Codebase inspection relies strictly on `ast.parse`; target code is never imported or executed during analysis.
- **Isolated Verification:** Every validation gate runs in an isolated scratch copy with `PYTHONPATH` pinned to the scratch `src/` tree; the source repository is never modified.
- **Credential Stripping:** `build_sanitized_env()` strips host API/cloud keys (`OPENAI_*`, `GEMINI_*`, `AWS_*`, `GITHUB_TOKEN`, etc.) before running repository tests.
- **Static Patch Security Audit:** Flags path traversal (`..` / absolute paths), `eval`/`exec`, `shell=True`, `os.system`, unsafe `pickle`, hardcoded secrets, and outbound network calls before certification.
- **Bounded Parallelism:** `ThreadPoolExecutor` (`--workers N`) certifies multiple task seams concurrently with strict subprocess timeouts (`shell=False`).
- **Hardened Local Dashboard:** Binds strictly to `127.0.0.1`, enforces path containment (`ensure_within_root`), and serves `CSP`, `X-Frame-Options: DENY`, and `nosniff` headers.

---

## Documentation
- [ARCHITECTURE.md](ARCHITECTURE.md) — Detailed technical explanation, design decisions, variation strategy, model/tool declarations, and future scope.
- [SUBMISSION_DECK_GUIDE.md](SUBMISSION_DECK_GUIDE.md) — Slide-by-slide content mapped directly to the Innodata AI Hackathon submission deck template.
