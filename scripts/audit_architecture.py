"""CLI Architecture Audit & Clean Layer Compliance Tool.

Scans the codebase using Python AST static analysis to verify:
  1. Inward Dependency Rule: Routers never import ORM database models.
  2. Transport Layer Isolation: Services never import FastAPI transport symbols.
  3. Dependency Inversion: Services inject repository protocols, not concrete classes.
  4. Circular Dependency Elimination: Module import graph is strictly a DAG (0 cycles).
  5. Schema Autonomy: DTO schemas strictly reside within app/schemas/.

Exit Code:
  0: 100% Architecture Compliant (Zero violations).
  1: Architectural Boundary Violations Detected.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Safeguard UTF-8 stdout on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: S110
        pass

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.architecture_linter import ArchitectureLinter, ArchitectureViolation  # noqa: E402


def format_table_row(col1: str, col2: str, width1: int = 40, width2: int = 30) -> str:
    """Format a two-column report row with safe ASCII borders."""
    return f"| {col1:<{width1}} | {col2:<{width2}} |"


def run_audit(target_dir: str = "app", verbose: bool = False) -> int:
    """Execute the complete architecture audit and return exit code."""
    separator = "=" * 76
    sub_sep = "+" + "-" * 42 + "+" + "-" * 32 + "+"

    print(separator)
    print("  ENTERPRISE ARCHITECTURE COMPLIANCE & LAYER BOUNDARY AUDITOR")
    print(separator)
    print(f"Scanning target: {target_dir}/ ...")

    start_time = time.perf_counter()
    linter = ArchitectureLinter(root_dir=target_dir)
    linter.index()

    v_count = len(linter.modules)
    e_count = sum(len(neighbors) for neighbors in linter.graph.values())
    raw_imports = len(linter.imports)

    # Check rules individually
    rule1_violations = linter.check_rule_1_inward_boundary()
    rule2_violations = linter.check_rule_2_transport_isolation()
    rule3_violations = linter.check_rule_3_dependency_inversion()
    rule4_violations = linter.check_rule_4_strict_dag()
    rule5_violations = linter.check_rule_5_schema_autonomy()

    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    all_violations: list[ArchitectureViolation] = (
        rule1_violations + rule2_violations + rule3_violations + rule4_violations + rule5_violations
    )

    print("\n" + sub_sep)
    print(format_table_row("METRIC / TOPOLOGY PARAMETER", "VALUE / STATUS", 40, 30))
    print(sub_sep)
    print(format_table_row("Modules Audited (V)", f"{v_count} modules", 40, 30))
    print(format_table_row("Internal Import Dependencies (E)", f"{e_count} edges", 40, 30))
    print(format_table_row("Total AST Import Nodes Walked", f"{raw_imports} nodes", 40, 30))
    print(
        format_table_row(
            "Graph Cyclomatic Status",
            "Strict DAG (0 Cycles)" if not rule4_violations else f"FAILED ({len(rule4_violations)} cycles)",
            40,
            30,
        )
    )
    print(format_table_row("Audit Execution Duration", f"{elapsed_ms:.2f} ms", 40, 30))
    print(sub_sep)

    print("\nCANONICAL RULE COMPLIANCE SUMMARY:")
    rules_summary = [
        ("Rule 1: Inward Boundary (Routers -> Models Shield)", rule1_violations),
        ("Rule 2: Transport Isolation (Services -> HTTP Decoupling)", rule2_violations),
        ("Rule 3: Dependency Inversion (Services -> Protocols Only)", rule3_violations),
        ("Rule 4: Circular Dependency Elimination (Strict DAG)", rule4_violations),
        ("Rule 5: Schema Autonomy (DTOs in app/schemas/)", rule5_violations),
    ]

    for name, v_list in rules_summary:
        status_text = "[ PASS ]" if len(v_list) == 0 else f"[ FAIL: {len(v_list)} issue(s) ]"
        print(f"  {status_text:<16} {name}")

    if all_violations:
        print("\n" + "!" * 76)
        print("  ARCHITECTURAL DRIFT / BOUNDARY VIOLATIONS DETECTED:")
        print("!" * 76)
        for idx, v in enumerate(all_violations, 1):
            print(f"\n{idx}. [{v.rule_id}] {v.rule_name}")
            print(f"   Location: {v.file_path}:{v.line_number}")
            print(f"   Details:  {v.message}")
        print("\nAudit Result: FAILED (Exit Code 1)")
        return 1

    print("\n" + separator)
    print("  AUDIT PASSED: 100% CLEAN ARCHITECTURE COMPLIANCE VERIFIED")
    print("  Zero ORM leaks, strict DAG topology, and pure protocol boundaries.")
    print(separator + "\n")
    return 0


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Clean Architecture Linter & Compliance Gate")
    parser.add_argument(
        "--target",
        type=str,
        default="app",
        help="Target package directory to audit (default: 'app')",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable detailed diagnostic logging",
    )
    args = parser.parse_args()
    exit_code = run_audit(target_dir=args.target, verbose=args.verbose)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
