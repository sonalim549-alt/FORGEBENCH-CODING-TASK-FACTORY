from __future__ import annotations

import ast
import copy
from dataclasses import dataclass
from pathlib import Path

from forgebench.patch_utils import extract_added_lines_by_file


@dataclass(slots=True)
class MutantCandidate:
    mutant_id: str
    rel_path: str
    line_number: int
    operator: str
    description: str
    mutated_source: str


_COMPARE_FLIPS: dict[type[ast.cmpop], tuple[type[ast.cmpop], str]] = {
    ast.Eq: (ast.NotEq, "Flip == to !="),
    ast.NotEq: (ast.Eq, "Flip != to =="),
    ast.Lt: (ast.GtE, "Flip < to >="),
    ast.LtE: (ast.Gt, "Flip <= to >"),
    ast.Gt: (ast.LtE, "Flip > to <="),
    ast.GtE: (ast.Lt, "Flip >= to <"),
    ast.In: (ast.NotIn, "Flip 'in' to 'not in'"),
    ast.NotIn: (ast.In, "Flip 'not in' to 'in'"),
}

_BINOP_FLIPS: dict[type[ast.operator], tuple[type[ast.operator], str]] = {
    ast.Add: (ast.Sub, "Replace + with -"),
    ast.Sub: (ast.Add, "Replace - with +"),
}


def _collect_targets(tree: ast.AST, target_lines: set[int]) -> list[tuple[int, str, str]]:
    """Find candidate AST nodes on lines introduced by the reference patch."""
    hits: list[tuple[int, str, str]] = []
    seen_keys: set[tuple[int, str]] = set()

    for idx, node in enumerate(ast.walk(tree)):
        lineno = getattr(node, "lineno", None)
        if lineno is None or lineno not in target_lines:
            continue

        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            op_type = type(node.ops[0])
            if op_type in _COMPARE_FLIPS and (lineno, "compare") not in seen_keys:
                seen_keys.add((lineno, "compare"))
                hits.append((idx, "compare", _COMPARE_FLIPS[op_type][1]))

        elif isinstance(node, ast.BoolOp) and (lineno, "boolop") not in seen_keys:
            desc = (
                "Replace 'and' with 'or'"
                if isinstance(node.op, ast.And)
                else "Replace 'or' with 'and'"
            )
            seen_keys.add((lineno, "boolop"))
            hits.append((idx, "boolop", desc))

        elif isinstance(node, ast.If) and (lineno, "if_negate") not in seen_keys:
            seen_keys.add((lineno, "if_negate"))
            hits.append((idx, "if_negate", "Negate branch condition in if-statement"))

        elif isinstance(node, ast.BinOp) and type(node.op) in _BINOP_FLIPS:
            if (lineno, "binop") not in seen_keys:
                seen_keys.add((lineno, "binop"))
                hits.append((idx, "binop", _BINOP_FLIPS[type(node.op)][1]))

        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, bool)
            and (lineno, "bool_const") not in seen_keys
        ):
            seen_keys.add((lineno, "bool_const"))
            hits.append((idx, "bool_const", f"Flip boolean constant {node.value} -> {not node.value}"))

        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, int)
            and not isinstance(node.value, bool)
            and node.value in {200, 201, 400, 404, 0, 1}
            and (lineno, "int_const") not in seen_keys
        ):
            seen_keys.add((lineno, "int_const"))
            hits.append((idx, "int_const", f"Perturb integer constant {node.value} -> {node.value + 1}"))

    return hits


def _apply_single_mutation(
    tree: ast.AST, target_node_index: int, kind: str
) -> tuple[ast.AST, int] | None:
    cloned = copy.deepcopy(tree)
    nodes = list(ast.walk(cloned))
    if target_node_index >= len(nodes):
        return None
    node = nodes[target_node_index]
    lineno = getattr(node, "lineno", 0)

    if kind == "compare" and isinstance(node, ast.Compare) and len(node.ops) == 1:
        replacement_cls = _COMPARE_FLIPS[type(node.ops[0])][0]
        node.ops[0] = replacement_cls()
    elif kind == "boolop" and isinstance(node, ast.BoolOp):
        node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
    elif kind == "if_negate" and isinstance(node, ast.If):
        node.test = ast.UnaryOp(op=ast.Not(), operand=node.test)
    elif kind == "binop" and isinstance(node, ast.BinOp):
        node.op = _BINOP_FLIPS[type(node.op)][0]()
    elif kind == "bool_const" and isinstance(node, ast.Constant) and isinstance(node.value, bool):
        node.value = not node.value
    elif kind == "int_const" and isinstance(node, ast.Constant) and isinstance(node.value, int):
        node.value = node.value + 1
    else:
        return None

    ast.fix_missing_locations(cloned)
    return cloned, lineno


def generate_targeted_mutants(
    repo_dir: Path,
    reference_patch: str,
    max_mutants: int = 6,
) -> list[MutantCandidate]:
    """
    Create single-fault AST mutants strictly on lines added by reference_patch.
    Used in Gate 3 to verify that feature tests detect incorrect behavior.
    """
    added_map = extract_added_lines_by_file(reference_patch)
    mutants: list[MutantCandidate] = []
    counter = 1

    # Prioritize semantic logic mutations (if_negate, compare, boolop) over constants.
    priority_order = {
        "if_negate": 0,
        "compare": 1,
        "boolop": 2,
        "binop": 3,
        "int_const": 4,
        "bool_const": 5,
    }

    for rel_path, target_lines in added_map.items():
        if not rel_path.endswith(".py") or not target_lines:
            continue
        file_path = repo_dir / rel_path
        if not file_path.is_file():
            continue

        source = file_path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(source, filename=rel_path)
        except SyntaxError:
            continue

        targets = _collect_targets(tree, target_lines)
        targets.sort(key=lambda item: priority_order.get(item[1], 99))

        for node_idx, kind, desc in targets:
            if len(mutants) >= max_mutants:
                return mutants
            res = _apply_single_mutation(tree, node_idx, kind)
            if res is None:
                continue
            mutated_tree, lineno = res
            try:
                mutated_src = ast.unparse(mutated_tree) + "\n"
            except Exception:
                continue
            if mutated_src == source:
                continue
            mutants.append(
                MutantCandidate(
                    mutant_id=f"M{counter:02d}",
                    rel_path=rel_path,
                    line_number=lineno,
                    operator=kind,
                    description=desc,
                    mutated_source=mutated_src,
                )
            )
            counter += 1

    return mutants
