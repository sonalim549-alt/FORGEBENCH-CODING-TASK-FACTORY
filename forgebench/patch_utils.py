from __future__ import annotations

import difflib
import re
from pathlib import Path

from forgebench.security import ensure_within_root, run_bounded_command


def make_unified_diff(
    rel_path: str,
    original_text: str | None,
    updated_text: str,
) -> str:
    """Build a standard git-compatible unified diff for a modified or newly created file."""
    posix_path = rel_path.replace("\\", "/")
    updated_lines = updated_text.splitlines(keepends=True)
    if updated_lines and not updated_lines[-1].endswith("\n"):
        updated_lines[-1] += "\n"

    if original_text is None:
        header = [
            f"diff --git a/{posix_path} b/{posix_path}\n",
            "new file mode 100644\n",
            "--- /dev/null\n",
            f"+++ b/{posix_path}\n",
            f"@@ -0,0 +1,{len(updated_lines)} @@\n",
        ]
        body = [f"+{line}" for line in updated_lines]
        return "".join(header + body)

    original_lines = original_text.splitlines(keepends=True)
    if original_lines and not original_lines[-1].endswith("\n"):
        original_lines[-1] += "\n"

    diff_lines = list(
        difflib.unified_diff(
            original_lines,
            updated_lines,
            fromfile=f"a/{posix_path}",
            tofile=f"b/{posix_path}",
            n=3,
        )
    )
    if not diff_lines:
        return ""
    return f"diff --git a/{posix_path} b/{posix_path}\n" + "".join(diff_lines)


def combine_patches(patches: list[str]) -> str:
    non_empty = [p.rstrip("\n") + "\n" for p in patches if p.strip()]
    return "".join(non_empty)


def extract_added_lines_by_file(patch_text: str) -> dict[str, set[int]]:
    """Return mapping of relative file path -> 1-based line numbers added by the patch."""
    added: dict[str, set[int]] = {}
    current_file: str | None = None
    line_no = 0

    for raw in patch_text.splitlines():
        if raw.startswith("+++ b/"):
            current_file = raw[6:].strip()
            added.setdefault(current_file, set())
            continue
        if raw.startswith("@@"):
            match = re.search(r"\+(\d+)", raw)
            line_no = int(match.group(1)) if match else 0
            continue
        if current_file is None:
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            added[current_file].add(line_no)
            line_no += 1
        elif not raw.startswith("-"):
            line_no += 1

    return added


def apply_unified_patch(repo_dir: Path, patch_text: str) -> tuple[bool, str]:
    """Apply a unified diff inside repo_dir using git apply (with path-safety validation)."""
    if not patch_text.strip():
        return False, "Empty patch text."

    # Verify all referenced paths stay strictly inside repo_dir before touching disk.
    for line in patch_text.splitlines():
        if line.startswith("+++ b/") or line.startswith("--- a/"):
            rel = line[6:].strip()
            if rel != "/dev/null":
                ensure_within_root(repo_dir, repo_dir / rel)

    patch_file = repo_dir / ".forgebench_apply.patch"
    try:
        patch_file.write_text(patch_text, encoding="utf-8", newline="\n")
        check = run_bounded_command(
            ["git", "apply", "--check", "--whitespace=nowarn", str(patch_file)],
            cwd=repo_dir,
            timeout_sec=30,
        )
        if check.returncode != 0:
            return False, (check.stderr or check.stdout).strip()

        applied = run_bounded_command(
            ["git", "apply", "--whitespace=nowarn", str(patch_file)],
            cwd=repo_dir,
            timeout_sec=30,
        )
        if applied.returncode != 0:
            return False, (applied.stderr or applied.stdout).strip()
        return True, "Applied cleanly via git apply."
    finally:
        if patch_file.exists():
            patch_file.unlink()
