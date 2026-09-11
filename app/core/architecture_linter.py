"""Enterprise Architecture Compliance Testing & Inward Dependency Enforcement Engine.

Statically audits clean layer boundaries, the Inward Dependency Rule, transport layer
isolation, protocol dependency inversion, strict DAG cycle prevention, and schema autonomy
using Python AST analysis without runtime module execution side-effects.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True, frozen=True)
class ArchitectureViolation:
    """Represents a discrete clean architecture boundary breach or rule violation."""

    rule_id: str
    rule_name: str
    file_path: str
    line_number: int
    message: str


@dataclass(slots=True)
class ModuleImport:
    """Represents an import relationship between two modules."""

    source_module: str
    target_module: str
    target_symbol: str | None
    line_number: int
    is_type_checking: bool


class ArchitectureLinter:
    """Clean Architecture Linter and Directed Module Graph Analyzer.

    Parses all Python AST trees across the application package to construct a directed
    module dependency graph G = (V, E) and enforce the 5 Canonical Architectural Invariants:
      1. Inward Boundary: Routers never import ORM database models.
      2. Transport Isolation: Services never import FastAPI transport objects.
      3. Dependency Inversion: Services inject repository protocols, not concrete implementations.
      4. Strict DAG: Zero circular dependency cycles across all modules via DFS O(V + E).
      5. Schema Autonomy: Routers and Services never define Pydantic DTO or ORM models inline.
    """

    FORBIDDEN_TRANSPORT_SYMBOLS: frozenset[str] = frozenset(
        {
            "Request",
            "Response",
            "APIRouter",
            "status",
            "HTTPException",
        }
    )

    CONCRETE_REPO_PREFIXES: tuple[str, ...] = (
        "SqlAlchemy",
        "InMemory",
    )

    def __init__(self, root_dir: Path | str = "app") -> None:
        self.root_path: Path = Path(root_dir).resolve()
        self.modules: set[str] = set()
        self.file_map: dict[str, Path] = {}
        self.module_by_file: dict[str, str] = {}
        self.ast_cache: dict[str, ast.AST] = {}
        self.imports: list[ModuleImport] = []
        self.graph: dict[str, set[str]] = {}
        self._is_indexed: bool = False

    def index(self) -> None:
        """Scan root directory, index all modules, parse ASTs, and extract import dependencies."""
        if self._is_indexed:
            return

        self.modules.clear()
        self.file_map.clear()
        self.module_by_file.clear()
        self.ast_cache.clear()
        self.imports.clear()
        self.graph.clear()

        # Phase 1: Discover all python source files and map module names
        for py_file in sorted(self.root_path.rglob("*.py")):
            if "__pycache__" in py_file.parts or ".pytest_cache" in py_file.parts:
                continue

            rel = py_file.relative_to(self.root_path.parent)
            if py_file.name == "__init__.py":
                mod_name = str(rel.parent).replace(os.sep, "/").replace("/", ".")
            else:
                mod_name = str(rel.with_suffix("")).replace(os.sep, "/").replace("/", ".")

            self.modules.add(mod_name)
            self.file_map[mod_name] = py_file
            norm_path = str(py_file.resolve()).replace("\\", "/")
            self.module_by_file[norm_path] = mod_name
            self.graph[mod_name] = set()

        # Phase 2: Parse ASTs and extract imports
        for mod_name, file_path in self.file_map.items():
            try:
                content = file_path.read_text(encoding="utf-8")
                tree = ast.parse(content, filename=str(file_path))
                self.ast_cache[mod_name] = tree
                self._extract_imports(mod_name, tree)
            except Exception:  # noqa: S112
                continue

        self._is_indexed = True

    def _extract_imports(self, source_mod: str, tree: ast.AST) -> None:
        """Walk AST and collect imports with TYPE_CHECKING boundary awareness."""
        type_checking_ranges: list[tuple[int, int]] = []

        # Find all `if TYPE_CHECKING:` statement line spans
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                test = node.test
                is_tc = False
                if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
                    is_tc = True
                elif isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
                    is_tc = True

                if is_tc:
                    end_lineno = getattr(node, "end_lineno", node.lineno)
                    type_checking_ranges.append((node.lineno, end_lineno))

        def in_type_checking(lineno: int) -> bool:
            return any(start <= lineno <= end for start, end in type_checking_ranges)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                is_tc = in_type_checking(node.lineno)
                for alias in node.names:
                    target_mod = self._resolve_module_target(source_mod, alias.name, level=0)
                    self.imports.append(
                        ModuleImport(
                            source_module=source_mod,
                            target_module=alias.name,
                            target_symbol=None,
                            line_number=node.lineno,
                            is_type_checking=is_tc,
                        )
                    )
                    if target_mod and target_mod != source_mod and not is_tc:
                        self.graph[source_mod].add(target_mod)

            elif isinstance(node, ast.ImportFrom):
                is_tc = in_type_checking(node.lineno)
                module_name = node.module or ""
                resolved = self._resolve_module_target(source_mod, module_name, level=node.level)
                for alias in node.names:
                    self.imports.append(
                        ModuleImport(
                            source_module=source_mod,
                            target_module=resolved or module_name,
                            target_symbol=alias.name,
                            line_number=node.lineno,
                            is_type_checking=is_tc,
                        )
                    )
                if resolved and resolved != source_mod and not is_tc:
                    self.graph[source_mod].add(resolved)

    def _resolve_module_target(self, source_mod: str, import_name: str, level: int) -> str | None:
        """Resolve absolute and relative module imports against known app modules."""
        if level == 0:
            candidate = import_name
        else:
            parts = source_mod.split(".")
            prefix = parts[:-level] if level < len(parts) else []
            candidate = ".".join(prefix + ([import_name] if import_name else []))

        # Check exact module or longest matching parent package
        cur = candidate
        while cur:
            if cur in self.modules:
                return cur
            if "." in cur:
                cur = cur.rsplit(".", 1)[0]
            else:
                break
        return None

    def detect_cycles(self) -> list[list[str]]:
        """Detect all circular dependency cycles using Depth-First Search (DFS) in O(V + E) time.

        Uses three-color state mapping:
          0 = UNVISITED (white)
          1 = VISITING / In Call Stack (gray)
          2 = VISITED / Fully Explored (black)
        """
        self.index()
        visited: dict[str, int] = {m: 0 for m in self.modules}
        cycles: list[list[str]] = []

        def dfs(node: str, path: list[str]) -> None:
            visited[node] = 1
            path.append(node)

            for neighbor in sorted(self.graph.get(node, set())):
                state = visited.get(neighbor, 0)
                if state == 1:
                    # Cycle detected: back-edge found to ancestor on current DFS path
                    cycle_start = path.index(neighbor)
                    cycle_path = path[cycle_start:] + [neighbor]
                    cycles.append(cycle_path)
                elif state == 0:
                    dfs(neighbor, path)

            path.pop()
            visited[node] = 2

        for mod in sorted(self.modules):
            if visited[mod] == 0:
                dfs(mod, [])

        return cycles

    def check_rule_1_inward_boundary(self) -> list[ArchitectureViolation]:
        """Rule 1: Routers never import ORM database models (Strict ORM Shielding)."""
        self.index()
        violations: list[ArchitectureViolation] = []

        for imp in self.imports:
            if imp.source_module.startswith("app.routers.") or imp.source_module == "app.routers":
                # Check target module or imported symbol
                target = imp.target_module
                if target.startswith("app.models.") or target == "app.models":
                    file_path = str(self.file_map.get(imp.source_module, imp.source_module))
                    symbol_str = f" ({imp.target_symbol})" if imp.target_symbol else ""
                    violations.append(
                        ArchitectureViolation(
                            rule_id="RULE-1-INWARD-BOUNDARY",
                            rule_name="Routers Never Import Models",
                            file_path=file_path,
                            line_number=imp.line_number,
                            message=(
                                f"Inward Boundary Violation: Router '{imp.source_module}' imports "
                                f"database model '{target}'{symbol_str}. Routers must operate strictly on schemas."
                            ),
                        )
                    )
        return violations

    def check_rule_2_transport_isolation(self) -> list[ArchitectureViolation]:
        """Rule 2: Services never import FastAPI transport objects (Request, Response, APIRouter, status)."""
        self.index()
        violations: list[ArchitectureViolation] = []

        for imp in self.imports:
            if imp.source_module.startswith("app.services.") or imp.source_module == "app.services":
                is_fastapi = imp.target_module.startswith("fastapi")
                if is_fastapi and imp.target_symbol in self.FORBIDDEN_TRANSPORT_SYMBOLS:
                    file_path = str(self.file_map.get(imp.source_module, imp.source_module))
                    violations.append(
                        ArchitectureViolation(
                            rule_id="RULE-2-TRANSPORT-ISOLATION",
                            rule_name="Services Never Import Transport Layer",
                            file_path=file_path,
                            line_number=imp.line_number,
                            message=(
                                f"Transport Isolation Violation: Service '{imp.source_module}' imports "
                                f"transport object 'fastapi.{imp.target_symbol}'. Services must remain protocol-agnostic "
                                "and raise domain exceptions."
                            ),
                        )
                    )
        return violations

    def check_rule_3_dependency_inversion(self) -> list[ArchitectureViolation]:
        """Rule 3: Services inject repository protocols, never concrete repository implementations."""
        self.index()
        violations: list[ArchitectureViolation] = []

        for mod_name, tree in self.ast_cache.items():
            if not (mod_name.startswith("app.services.") or mod_name == "app.services"):
                continue

            file_path = str(self.file_map.get(mod_name, mod_name))

            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue

                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                        for arg in item.args.args:
                            arg_name = arg.arg.lower()
                            if "repo" in arg_name or "repository" in arg_name:
                                ann_str = ast.unparse(arg.annotation) if arg.annotation else ""
                                for prefix in self.CONCRETE_REPO_PREFIXES:
                                    if prefix in ann_str:
                                        violations.append(
                                            ArchitectureViolation(
                                                rule_id="RULE-3-DEPENDENCY-INVERSION",
                                                rule_name="Services Inject Protocols Not Concrete Repos",
                                                file_path=file_path,
                                                line_number=item.lineno,
                                                message=(
                                                    f"Dependency Inversion Violation: Class '{node.name}' "
                                                    f"injects concrete repository type '{ann_str}' for argument '{arg.arg}'. "
                                                    "Must inject a Protocol interface."
                                                ),
                                            )
                                        )
        return violations

    def check_rule_4_strict_dag(self) -> list[ArchitectureViolation]:
        """Rule 4: Circular Dependency Elimination (Module dependency graph must be a strict DAG)."""
        cycles = self.detect_cycles()
        violations: list[ArchitectureViolation] = []

        for cycle in cycles:
            cycle_str = " -> ".join(cycle)
            first_mod = cycle[0]
            file_path = str(self.file_map.get(first_mod, first_mod))
            violations.append(
                ArchitectureViolation(
                    rule_id="RULE-4-STRICT-DAG",
                    rule_name="Circular Dependency Elimination",
                    file_path=file_path,
                    line_number=1,
                    message=f"Circular Dependency Cycle Detected: {cycle_str}",
                )
            )
        return violations

    def check_rule_5_schema_autonomy(self) -> list[ArchitectureViolation]:
        """Rule 5: Schema Autonomy (Routers and Services never define Pydantic / ORM models inline)."""
        self.index()
        violations: list[ArchitectureViolation] = []

        target_prefixes = ("app.routers.", "app.services.")

        for mod_name, tree in self.ast_cache.items():
            if not any(mod_name.startswith(p) for p in target_prefixes):
                continue

            file_path = str(self.file_map.get(mod_name, mod_name))

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    base_names = []
                    for b in node.bases:
                        if isinstance(b, ast.Name):
                            base_names.append(b.id)
                        elif isinstance(b, ast.Attribute):
                            base_names.append(b.attr)

                    if any(base in ("BaseModel", "Base", "DeclarativeBase") for base in base_names):
                        violations.append(
                            ArchitectureViolation(
                                rule_id="RULE-5-SCHEMA-AUTONOMY",
                                rule_name="Schemas Must Reside in app/schemas/",
                                file_path=file_path,
                                line_number=node.lineno,
                                message=(
                                    f"Schema Autonomy Violation: Class '{node.name}' defines a schema or model "
                                    f"in '{mod_name}'. DTO schemas belong strictly in 'app/schemas/'."
                                ),
                            )
                        )
        return violations

    def check_all(self) -> list[ArchitectureViolation]:
        """Run all 5 Architecture Compliance Rules and return consolidated violations."""
        self.index()
        violations: list[ArchitectureViolation] = []
        violations.extend(self.check_rule_1_inward_boundary())
        violations.extend(self.check_rule_2_transport_isolation())
        violations.extend(self.check_rule_3_dependency_inversion())
        violations.extend(self.check_rule_4_strict_dag())
        violations.extend(self.check_rule_5_schema_autonomy())
        return violations

    def audit_code_snippet(
        self, source_code: str, file_path: str = "app/routers/mock_router.py"
    ) -> list[ArchitectureViolation]:
        """Audit an in-memory code snippet for testing and negative-control verification."""
        violations: list[ArchitectureViolation] = []
        tree = ast.parse(source_code, filename=file_path)
        is_router = "routers" in file_path
        is_service = "services" in file_path

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if is_router and ("models" in alias.name or "app.models" in alias.name):
                        violations.append(
                            ArchitectureViolation(
                                rule_id="RULE-1-INWARD-BOUNDARY",
                                rule_name="Routers Never Import Models",
                                file_path=file_path,
                                line_number=node.lineno,
                                message=f"Router imports forbidden model '{alias.name}'.",
                            )
                        )
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if is_router and ("models" in mod or "app.models" in mod):
                    violations.append(
                        ArchitectureViolation(
                            rule_id="RULE-1-INWARD-BOUNDARY",
                            rule_name="Routers Never Import Models",
                            file_path=file_path,
                            line_number=node.lineno,
                            message=f"Router imports forbidden model from '{mod}'.",
                        )
                    )
                if is_service and "fastapi" in mod:
                    for alias in node.names:
                        if alias.name in self.FORBIDDEN_TRANSPORT_SYMBOLS:
                            violations.append(
                                ArchitectureViolation(
                                    rule_id="RULE-2-TRANSPORT-ISOLATION",
                                    rule_name="Services Never Import Transport Layer",
                                    file_path=file_path,
                                    line_number=node.lineno,
                                    message=f"Service imports forbidden transport symbol '{alias.name}'.",
                                )
                            )
            elif isinstance(node, ast.ClassDef):
                base_names = [b.id for b in node.bases if isinstance(b, ast.Name)]
                if (is_router or is_service) and any(b in ("BaseModel", "Base") for b in base_names):
                    violations.append(
                        ArchitectureViolation(
                            rule_id="RULE-5-SCHEMA-AUTONOMY",
                            rule_name="Schemas Must Reside in app/schemas/",
                            file_path=file_path,
                            line_number=node.lineno,
                            message=f"Model/schema '{node.name}' declared in '{file_path}'.",
                        )
                    )

        return violations
