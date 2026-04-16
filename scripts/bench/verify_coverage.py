#!/usr/bin/env python3
"""
verify_coverage.py — Quick verification that optimizer improvements landed.
Run from project root: .venv/bin/python scripts/bench/verify_coverage.py
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)


def count_test_methods(filepath: str) -> int:
    """Count test methods in a file by parsing AST."""
    with open(filepath) as f:
        tree = ast.parse(f.read())
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            count += 1
    return count


def check_docstring_length() -> int:
    """Return the number of lines in the optimize_code docstring."""
    import tools.optimization as opt_mod
    import inspect
    doc = inspect.getdoc(opt_mod.optimize_code)
    return len(doc.split("\n")) if doc else 0


def main():
    print("=" * 60)
    print("Optimizer Improvement Verification")
    print("=" * 60)

    # 1. Count tests in new files
    print("\n[1] New test files:")
    new_test_files = [
        "tests/test_opt_rules_060.py",
        "tests/test_opt_rules_069.py",
        "tests/test_opt_rules_080.py",
        "tests/test_optimizer_integration.py",
    ]
    total_new = 0
    for f in new_test_files:
        fp = os.path.join(ROOT, f)
        if os.path.exists(fp):
            n = count_test_methods(fp)
            print(f"  {f}: {n} test methods")
            total_new += n
        else:
            print(f"  {f}: NOT FOUND")
    print(f"  Total new test methods: {total_new}")

    # 2. Docstring length
    print("\n[2] Tool docstring:")
    doc_lines = check_docstring_length()
    status = "PASS" if doc_lines < 50 else "FAIL (expected < 50)"
    print(f"  Lines: {doc_lines} [{status}]")

    # 3. Rule count
    print("\n[3] Rule registry:")
    from core.optimizer import _RULES
    print(f"  Total rules in _RULES: {len(_RULES)} (expected: 87)")

    # 4. Existing test count
    print("\n[4] Existing tests:")
    existing = count_test_methods(os.path.join(ROOT, "tests/test_optimizer.py"))
    print(f"  tests/test_optimizer.py: {existing} test methods")

    # 5. Summary
    print("\n" + "=" * 60)
    print(f"Total test methods (existing + new): {existing + total_new}")
    print(f"Target: 200+ (was {existing})")
    all_pass = total_new >= 50 and doc_lines < 50 and len(_RULES) == 87
    print(f"Overall: {'ALL CHECKS PASS' if all_pass else 'SOME CHECKS FAILED'}")
    print("=" * 60)


if __name__ == "__main__":
    main()
