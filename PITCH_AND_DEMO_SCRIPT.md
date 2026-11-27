# ForgeBench — Finalist Pitch & 3-Minute Walkthrough Demo Script

Use this script for both your **Hack2skill Walkthrough Video** and the **Live Finalist Presentation** (Innodata AI Hackathon — Track 1: Coding Task Factory).

---

## 3-Minute Walkthrough Demo Video Script (Screen + Voiceover)

### [0:00 – 0:30] Hook & Real-World Problem
**On screen:** Open `README.md` or Slide 2 of your deck.

**Say:**
> "Hi, I'm Sonali Moghe, and this is **ForgeBench** — my reusable Coding Task Factory for Track 1 of the Innodata AI Hackathon.
> Evaluating AI coding agents today suffers from three major issues: public benchmarks get contaminated in model training data, synthetic feature tests often pass even when the code is wrong, and generated patches silently break existing functionality.
> Instead of hand-crafting tasks, ForgeBench automates the entire pipeline from any pinned open-source Git repository straight into certified **Harbor v0.25.x** containerized task packages."

---

### [0:30 – 1:10] Repository Analysis & Substantive 5-Way Variation
**On screen:** Run in terminal:
```powershell
cd "C:\Users\DELL\OneDrive\Desktop\coding-task-factory"
python -m forgebench analyze repos\click
```

**Say:**
> "Here I've cloned **`pallets/click`** at verified release tag **`8.5.1`** — commit `cf19179e8e2f` — where all 2,350 upstream pytest tests pass.
> Notice ForgeBench analyzes the repository **purely statically using Python's AST** — zero untrusted code is executed during discovery.
> It automatically identifies **5 substantively different, non-upstream feature-development seams** across four Click subsystems:
> 1. `Duration` ParamType with compound time units and range bounds,
> 2. `ByteSize` ParamType distinguishing SI decimal (`KB`/`MB`) vs IEC binary (`KiB`/`MiB`) units,
> 3. An ANSI-aware multi-column `write_table()` method on `HelpFormatter`,
> 4. First-class subcommand aliasing on `click.Group` with conflict checks and `--help` integration, and
> 5. Secret-safe CLI output hygiene via `redact_text()` and `safe_echo()`."

---

### [1:10 – 2:00] Live 4-Gate Certification & AST Mutation Testing
**On screen:** Run the live factory pipeline (or show `sample_tasks/factory_summary.json` + run a single task live):
```powershell
python -m forgebench generate repos\click --out sample_tasks --count 5 --workers 2 --format harbor
```

**Say:**
> "Every task goes through our **4-Gate Certification Harness** in isolated scratch workspaces with credential-stripped environments:
> - **Gate 1** verifies baseline health across all 2,350 upstream Click tests.
> - **Gate 2** runs the Harbor `nop` check — proving feature tests fail cleanly on the unmodified commit with **reward = `0.0`**.
> - **Gate 3** is our strongest differentiator: **Targeted AST Mutation Testing**. ForgeBench mutates *only* the newly added lines in the reference patch — flipping comparisons, negating branches, and shifting boundaries — to prove our tests catch **incorrect implementations**, not just missing symbols. Across all 5 Click tasks, our tests kill **25 out of 25 mutants — a 100% mutation kill rate**.
> - **Gate 4** confirms the Harbor `oracle` pass with **reward = `1.0`**, zero regressions, and zero high-severity security findings."

---

### [2:00 – 2:40] Inspect Harbor v0.25.x Package & Evidence Dashboard
**On screen:** Show `sample_tasks/click-type-duration/` folder tree (`task.toml`, `instruction.md`, `environment/Dockerfile`, `solution/solve.sh`, `tests/test.sh`, `fail_to_pass.json`, `pass_to_pass.json`), then open `http://127.0.0.1:8765/`:
```powershell
python -m forgebench serve --tasks-dir sample_tasks --port 8765
```

**Say:**
> "Every certified output conforms strictly to **Harbor v0.25.x** — with Unix LF shell scripts, exact 40-character `base_commit` pinning, and Innodata's reward calculation written to `/logs/verifier/reward.txt`.
> In the local Evidence Explorer UI, reviewers can inspect each agent-only `instruction.md`, unified reference diffs, all 4 gate results, and the full mutation kill matrix."

*(If running on a Docker-enabled machine, also show `harbor run -p sample_tasks/click-type-duration -a nop ...` returning `0.0` and `-a oracle ...` returning `1.0`.)*

---

### [2:40 – 3:00] Scalability, Security & Future Scope
**On screen:** Slide 7 (Validation Results & Future Scope).

**Say:**
> "ForgeBench runs completely offline with zero external API keys for deterministic reproducibility, and supports pluggable Gemini, OpenAI, or local Ollama providers with an automated validation-feedback retry loop.
> Next, we can extend the exact same Harbor architecture with tree-sitter adapters for `zod` (TypeScript) and `jsoncpp` (C++), rootless container sandboxes, and multi-model `pass@k` difficulty calibration. Thank you!"

---

## Likely Judge Q&A (Quick Winning Answers)

1. **Q: How do you guarantee your tests catch *incorrect* AI solutions, not just missing functions?**
   - **A:** Gate 3 runs targeted AST mutation testing exclusively on lines added by `reference.patch` (branch negation, comparison flips, boolean/arithmetic swaps) and rejects any task whose feature tests fail to meet the mutation kill threshold. All 5 Click tasks achieved 100% (5/5) kill rates.
2. **Q: Is your factory hardcoded only to `pallets/click`?**
   - **A:** No. `analyzer.py`, `validator.py`, `mutator.py`, `security.py`, and `harbor.py` are repository-agnostic. We include both Click domain seams and general Python seams (tested end-to-end on a separate `inventory_service` repo in `tests/test_pipeline_e2e.py`), plus pluggable LLM synthesis (`--provider gemini|openai|ollama`) for arbitrary repos.
3. **Q: Why does `instruction.md` not mention internal helper names or test files?**
   - **A:** Because in Harbor, `instruction.md` is the only file shown to the evaluated AI agent. Keeping it strictly behavioral (public signatures, rules, error contracts, edge cases) prevents leakage and tests real software engineering ability.
4. **Q: How do you prevent untrusted repo code from compromising the host during factory runs?**
   - **A:** Discovery is 100% static AST (never imports target code), test execution strips host API/cloud env vars (`build_sanitized_env`), subprocesses run with `shell=False` and hard timeouts, unified diffs are scanned for path traversal / `eval` / `exec` / `shell=True` / secrets, and Harbor isolates final runs inside Docker containers.
