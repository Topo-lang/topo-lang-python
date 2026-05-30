#!/usr/bin/env python3
"""Python call site extractor for topo-check L2 containment analysis.

Pyright does not implement textDocument/semanticTokens/full, so the generic
LSP-based L2 path used by C++/Rust/Java cannot run for Python. This script is
the Python-specific L2 extractor: it uses the ast stdlib module to parse each
source file and emit every function/method call with its position, enclosing
caller, and resolved dotted callee.

Protocol:
    argv   -- topo_extract_python.py <file1.py> <file2.py> ...
              File paths are passed as CLI args (not via stdin) so the caller
              does not have to juggle bidirectional pipe lifetime with
              PipedProcess. An empty arg list is valid and yields an empty
              callSites array.
    stdout -> {"callSites": [
                  {"file": "/abs/path/a.py",
                   "line": 12,        # 1-based
                   "col": 4,          # 0-based
                   "callee": "os.system",
                   "caller": "run_command"},
                  ...
              ],
              "fileErrors": [
                  {"file": "/abs/path/bad.py",
                   "kind": "syntax-error",    # or "read-error"
                   "line": 5,                 # 0 when not applicable
                   "message": "invalid syntax"}
              ]}
    stderr -- parse / read errors, prefixed with "[topo-extract-python]" as a
              human-readable companion to the structured ``fileErrors``
              array. The script keeps going on per-file errors and still
              emits a valid JSON response for the files that parsed
              successfully. The C++ analyzer relies on ``fileErrors`` to
              surface per-file coverage loss as distinct warnings so a
              SyntaxError in one file doesn't drop silently out of the
              containment verdict.
    exit   -- 0 on any successful dispatch (even if some files failed to
              parse), non-zero only on fatal internal errors.

Caller naming matches PythonSymbolExtractor:
    def foo(): ...            -> "foo"
    class C:\n  def bar(self):  -> "C.bar"

Callee resolution uses static import tracking:
    import os                      -> os.system(...)  yields  "os.system"
    import os.path as op           -> op.join(...)     yields  "os.path.join"
    from os import system          -> system(...)      yields  "os.system"
    from os import system as sh    -> sh(...)          yields  "os.system"
Wildcard imports (`from os import *`) are untrackable; bare names remain bare.
Type-dependent attribute calls (e.g. `client.delete(...)` where client is a
user-defined instance) fall back to the attribute chain text (`client.delete`)
— the catalog does not try to match such patterns, which is fine for the
containment use case where only module-qualified stdlib calls need to be
caught.
"""

from __future__ import annotations

import ast
import json
import sys


# Dunder attributes whose mere access is dangerous (class punning, introspection
# escape).  These mirror the L1 regex `\.__(class|bases|mro|dict|init_subclass)__\b`
# and the unsafe-catalog entries under "metaprogramming / attribute punning".
DANGEROUS_DUNDER_ATTRS = {
    "__class__",
    "__bases__",
    "__mro__",
    "__dict__",
    "__init_subclass__",
}


def resolve_callee(node: ast.AST, imports: dict) -> str:
    """Walk a callee expression and return its dotted representation."""
    if isinstance(node, ast.Name):
        return imports.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        base = resolve_callee(node.value, imports)
        return f"{base}.{node.attr}"
    if isinstance(node, ast.Call):
        return resolve_callee(node.func, imports)
    if isinstance(node, ast.Subscript):
        return resolve_callee(node.value, imports) + "[]"
    return "<unknown>"


class CallSiteVisitor(ast.NodeVisitor):
    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self.call_sites: list = []
        # Scope stack entries: (name, is_class)
        self.scope_stack: list = []
        # Map of locally-visible name -> fully-qualified dotted target
        self.imports: dict = {}
        # Modules pulled in via `from X import *` — bare calls in this file
        # may resolve to any of these modules, so we emit supplementary call
        # sites (`X.name`) alongside the bare form.
        self.wildcard_modules: list = []

    def current_caller(self) -> str:
        """Match PythonSymbolExtractor::buildQualifiedName output format."""
        class_name = ""
        func_name = ""
        for name, is_class in reversed(self.scope_stack):
            if not is_class and not func_name:
                func_name = name
            elif is_class and not class_name:
                class_name = name
        if not func_name:
            return "<module>"
        if class_name:
            return f"{class_name}.{func_name}"
        return func_name

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            full = alias.name
            if alias.asname:
                self.imports[alias.asname] = full
            else:
                # `import os.path` exposes the top-level package name only
                top = full.split(".", 1)[0]
                self.imports[top] = top
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                # Wildcard: record the source module so visit_Call can emit
                # supplementary `<module>.<name>` candidates for bare calls.
                if module and module not in self.wildcard_modules:
                    self.wildcard_modules.append(module)
                continue
            local = alias.asname if alias.asname else alias.name
            qualified = f"{module}.{alias.name}" if module else alias.name
            self.imports[local] = qualified
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope_stack.append((node.name, False))
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.scope_stack.append((node.name, False))
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope_stack.append((node.name, True))
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # Dangerous dunder access (e.g. `obj.__class__ = Evil`) is treated
        # as a synthetic call site so it flows through the catalog
        # classification pipeline.  This mirrors the L1 regex pattern and
        # keeps the L2 ast path symmetric in coverage for metaprogramming
        # escapes that never appear as ast.Call nodes.
        if node.attr in DANGEROUS_DUNDER_ATTRS:
            self.call_sites.append(
                {
                    "file": self.file_path,
                    "line": node.lineno,
                    "col": node.col_offset,
                    "callee": node.attr,
                    "caller": self.current_caller(),
                }
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        callee = resolve_callee(node.func, self.imports)
        caller = self.current_caller()
        self.call_sites.append(
            {
                "file": self.file_path,
                "line": node.lineno,
                "col": node.col_offset,
                "callee": callee,
                "caller": caller,
            }
        )
        # Wildcard-import rescue: when a bare Name is called and the file
        # has any `from X import *` imports, emit supplementary call sites
        # under each wildcard module.  The downstream classifier keeps only
        # the form that matches a catalog entry, so over-emission here is
        # safe — it costs some duplicate entries but recovers coverage on
        # `from os import *; system(...)` patterns that pure bare-name
        # resolution would miss.
        if (
            isinstance(node.func, ast.Name)
            and node.func.id not in self.imports
            and self.wildcard_modules
        ):
            bare = node.func.id
            for module in self.wildcard_modules:
                self.call_sites.append(
                    {
                        "file": self.file_path,
                        "line": node.lineno,
                        "col": node.col_offset,
                        "callee": f"{module}.{bare}",
                        "caller": caller,
                    }
                )
        self.generic_visit(node)


def process_file(path: str, file_errors: list) -> list:
    """Parse one .py file and return its call sites.

    On any read or parse failure: record a structured entry in
    ``file_errors`` (which the caller forwards on stdout as the
    ``fileErrors`` field) and return ``[]`` so the script keeps going
    for the remaining files. The human-readable stderr companion is
    preserved so existing log scrapers do not regress.
    """
    try:
        with open(path, "rb") as f:
            source = f.read()
    except OSError as e:
        print(f"[topo-extract-python] {path}: {e}", file=sys.stderr)
        file_errors.append({
            "file": path,
            "kind": "read-error",
            "line": 0,
            "message": str(e),
        })
        return []
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        print(
            f"[topo-extract-python] {path}: SyntaxError at line "
            f"{e.lineno}: {e.msg}",
            file=sys.stderr,
        )
        file_errors.append({
            "file": path,
            "kind": "syntax-error",
            "line": int(e.lineno) if e.lineno else 0,
            "message": e.msg or "SyntaxError",
        })
        return []

    visitor = CallSiteVisitor(path)
    visitor.visit(tree)
    return visitor.call_sites


def main() -> int:
    all_sites: list = []
    file_errors: list = []
    for f in sys.argv[1:]:
        all_sites.extend(process_file(f, file_errors))
    json.dump({"callSites": all_sites, "fileErrors": file_errors},
              sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
